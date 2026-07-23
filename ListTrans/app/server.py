# -*- coding: utf-8 -*-
import os
import logging
import shutil
import uuid
import pandas as pd
import openpyxl
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Depends, Request, BackgroundTasks, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor
import json

from app.db_manager import init_db, search_materials_keyword, query_by_code, DB_PATH, get_db_connection
from app.preprocessor import extract_params, clean_text, reload_preprocessor_configs, extract_device_name, remove_construction_info
from app.query_preprocessor import extract_query_params
from app.vector_index import index_manager
from app.matcher import match_item, reload_matcher_configs
from app.report_generator import generate_standard_report

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

# Configure system logger
logger = logging.getLogger("listtrans_app")
logger.setLevel(logging.INFO)

# Avoid duplicate handlers on hot reload
if not logger.handlers:
    fh = logging.FileHandler("logs/app_runtime.log", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh.setFormatter(formatter)
    logger.addHandler(fh)


from dotenv import load_dotenv
load_dotenv()

ENABLE_DOCS = os.getenv("ENABLE_DOCS", "true").lower() == "true"
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")

app = FastAPI(
    title="华康洁净物料清单匹配工具",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

# Include Auth and Session dependencies
from app.auth_middleware import require_employee, require_manager, require_director, require_admin, get_current_user
from app.admin_api import router as admin_router
app.include_router(admin_router)

# Authentication and Session API Endpoints
from app.auth_manager import (
    verify_password, get_user_by_username, create_access_token, 
    update_user_last_login, log_login_attempt, is_user_locked_out,
    security_logger
)
from app.session_manager import (
    save_draft_session, get_draft_session, clear_draft_session, save_named_session, list_saved_sessions,
    get_session_by_id, update_named_session, delete_session_by_id, clear_all_runtime_data
)

class LoginRequest(BaseModel):
    username: str
    password: str

class DraftSaveRequest(BaseModel):
    original_filename: str
    professional: str
    items_json: str

class NamedSaveRequest(BaseModel):
    session_name: str
    original_filename: str
    professional: str
    items_json: str

class UpdateSessionRequest(BaseModel):
    items_json: str

@app.post("/api/auth/login")
async def api_login(req: LoginRequest, request: Request):
    username = req.username.strip()
    ip = request.client.host if request.client else "unknown"
    
    is_locked, remaining = is_user_locked_out(username, ip)
    if is_locked:
        raise HTTPException(
            status_code=429,
            detail=f"登录失败次数过多。为了账号安全，已临时锁定，请在 {remaining} 秒后再试。"
        )
        
    user = get_user_by_username(username)
    if not user or not verify_password(req.password, user["password_hash"]):
        log_login_attempt(username, ip, success=False)
        security_logger.warning(f"Failed login attempt for username '{username}' from IP {ip}.")
        raise HTTPException(status_code=400, detail="用户名或密码错误。")
        
    if not user["is_active"]:
        log_login_attempt(username, ip, success=False)
        security_logger.warning(f"Inactive username '{username}' tried to login from IP {ip}.")
        raise HTTPException(status_code=403, detail="该账号已被停用，请联系管理员。")
        
    log_login_attempt(username, ip, success=True)
    update_user_last_login(user["id"])
    
    access_token = create_access_token(data={"sub": username, "role": user["role"]})
    security_logger.info(f"User '{username}' logged in successfully from IP {ip}.")
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "username": username,
        "role": user["role"]
    }

@app.post("/api/session/draft")
async def api_save_draft(req: DraftSaveRequest, current_user: dict = Depends(get_current_user)):
    save_draft_session(current_user["id"], req.original_filename, req.professional, req.items_json)
    return {"success": True}

@app.get("/api/session/draft")
async def api_get_draft(current_user: dict = Depends(get_current_user)):
    draft = get_draft_session(current_user["id"])
    if not draft:
        return {"draft": None}
    return {
        "draft": {
            "id": draft["id"],
            "original_filename": draft["original_filename"],
            "professional": draft["professional"],
            "items_json": draft["items_json"],
            "updated_at": draft["updated_at"]
        }
    }

@app.delete("/api/session/draft")
async def api_delete_draft(current_user: dict = Depends(get_current_user)):
    clear_draft_session(current_user["id"])
    return {"success": True}

@app.post("/api/session/save")
async def api_save_session(req: NamedSaveRequest, current_user: dict = Depends(get_current_user)):
    session_id = save_named_session(
        current_user["id"], req.session_name, req.original_filename, req.professional, req.items_json
    )
    return {"success": True, "session_id": session_id}

@app.get("/api/session/list")
async def api_list_sessions(current_user: dict = Depends(get_current_user)):
    sessions = list_saved_sessions(current_user["id"])
    return {"sessions": sessions}

@app.get("/api/session/{session_id}")
async def api_get_session(session_id: str, current_user: dict = Depends(get_current_user)):
    session = get_session_by_id(current_user["id"], session_id)
    if not session:
        raise HTTPException(status_code=404, detail="未找到该工作进度记录。")
    return {
        "session": {
            "id": session["id"],
            "session_name": session["session_name"],
            "original_filename": session["original_filename"],
            "professional": session["professional"],
            "items_json": session["items_json"],
            "updated_at": session["updated_at"]
        }
    }

@app.put("/api/session/{session_id}")
async def api_update_session(session_id: str, req: UpdateSessionRequest, current_user: dict = Depends(get_current_user)):
    session = get_session_by_id(current_user["id"], session_id)
    if not session:
        raise HTTPException(status_code=404, detail="未找到该工作进度记录。")
    update_named_session(current_user["id"], session_id, req.items_json)
    return {"success": True}

@app.delete("/api/session/{session_id}")
async def api_delete_session(session_id: str, current_user: dict = Depends(get_current_user)):
    session = get_session_by_id(current_user["id"], session_id)
    if not session:
        raise HTTPException(status_code=404, detail="未找到该工作进度记录。")
    delete_session_by_id(current_user["id"], session_id)
    return {"success": True}

@app.get("/api/db/portrait")
async def api_get_db_portrait():
    """Calculate and return live Data Portrait metrics for materials.db."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. Total items
        total_items = cursor.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
        
        if total_items == 0:
            conn.close()
            return {
                "total_items": 0,
                "valid_materials": 0,
                "disabled_count": 0,
                "category_count": 0,
                "spec_nonempty_count": 0,
                "spec_nonempty_rate": 0.0,
                "model_extracted_count": 0,
                "model_missing_count": 0,
                "model_extracted_rate": 0.0,
                "structured_attr_count": 0,
                "structured_attr_rate": 0.0,
                "top_categories": [],
                "top_units": []
            }
            
        # 2. Disabled / Invalid
        disabled_count = cursor.execute(
            "SELECT COUNT(*) FROM materials WHERE code LIKE '%禁用%' OR code LIKE '%停用%' OR code LIKE '%无效%' OR name LIKE '%禁用%' OR name LIKE '%停用%'"
        ).fetchone()[0]
        valid_materials = max(0, total_items - disabled_count)
        
        # 3. Category count
        category_count = cursor.execute("SELECT COUNT(DISTINCT category) FROM materials WHERE category IS NOT NULL AND category != ''").fetchone()[0]
        
        # 4. Raw specification non-empty count
        spec_nonempty_count = cursor.execute(
            "SELECT COUNT(*) FROM materials WHERE specification IS NOT NULL AND TRIM(specification) != ''"
        ).fetchone()[0]
        spec_nonempty_rate = round((spec_nonempty_count / total_items) * 100, 2)
        
        # 5. Extracted JSON Model / Attrs stats
        rows = cursor.execute("SELECT parsed_params FROM materials").fetchall()
        model_extracted_count = 0
        structured_attr_count = 0
        
        for r in rows:
            p_json = r[0]
            if p_json:
                try:
                    p = json.loads(p_json)
                    if p.get("model"):
                        model_extracted_count += 1
                    attrs = p.get("attrs", {})
                    if isinstance(attrs, dict) and len([k for k, v in attrs.items() if v]) >= 2:
                        structured_attr_count += 1
                except Exception:
                    pass
                    
        model_missing_count = total_items - model_extracted_count
        model_extracted_rate = round((model_extracted_count / total_items) * 100, 2)
        structured_attr_rate = round((structured_attr_count / total_items) * 100, 2)
        
        # 6. Top 5 Categories
        top_cats = cursor.execute(
            "SELECT category, COUNT(*) as cnt FROM materials WHERE category IS NOT NULL AND category != '' GROUP BY category ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        top_categories = [{"category": row[0], "count": row[1], "percent": round((row[1]/total_items)*100, 1)} for row in top_cats]
        
        # 7. Top 5 Units
        top_u = cursor.execute(
            "SELECT unit, COUNT(*) as cnt FROM materials WHERE unit IS NOT NULL AND unit != '' GROUP BY unit ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        top_units = [{"unit": row[0], "count": row[1], "percent": round((row[1]/total_items)*100, 1)} for row in top_u]
        
        conn.close()
        
        return {
            "total_items": total_items,
            "valid_materials": valid_materials,
            "disabled_count": disabled_count,
            "category_count": category_count,
            "spec_nonempty_count": spec_nonempty_count,
            "spec_nonempty_rate": spec_nonempty_rate,
            "model_extracted_count": model_extracted_count,
            "model_missing_count": model_missing_count,
            "model_extracted_rate": model_extracted_rate,
            "structured_attr_count": structured_attr_count,
            "structured_attr_rate": structured_attr_rate,
            "top_categories": top_categories,
            "top_units": top_units
        }
    except Exception as e:
        logger.error(f"Error computing data portrait: {e}")
        raise HTTPException(status_code=500, detail=f"生成物料库数字画像失败: {str(e)}")

@app.delete("/api/system/reset")
async def api_system_reset(current_user: dict = Depends(get_current_user)):
    """Reset system runtime data (clear sessions, uploads, and feedback log)."""
    try:
        # 1. Clear work_sessions database table
        clear_all_runtime_data()
        
        # 2. Clear uploaded files in UPLOAD_DIR
        if os.path.exists(UPLOAD_DIR):
            for filename in os.listdir(UPLOAD_DIR):
                filepath = os.path.join(UPLOAD_DIR, filename)
                if os.path.isfile(filepath):
                    try:
                        os.remove(filepath)
                    except Exception as e:
                        logger.error(f"Failed to delete upload file {filepath}: {e}")
                        
        # 3. Clear binding_feedback.json
        if os.path.exists(BINDING_FEEDBACK_FILE):
            try:
                with open(BINDING_FEEDBACK_FILE, "w", encoding="utf-8") as f:
                    json.dump({}, f)
            except Exception as e:
                logger.error(f"Failed to reset binding feedback file: {e}")
                
        security_logger.info(f"System runtime data reset executed by user '{current_user.get('username')}'.")
        return {"success": True, "message": "系统运行数据（会话草稿、上传临时文件及反馈日志）已全面清理恢复干净状态！"}
    except Exception as e:
        logger.error(f"Error executing system reset: {e}")
        raise HTTPException(status_code=500, detail=f"清理系统数据失败: {str(e)}")

@app.get("/api/db/materials")
async def api_get_db_materials(
    keyword: Optional[str] = None,
    category: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """Query and search master materials with pagination."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        where_clauses = []
        params = []
        
        if category and category.strip():
            where_clauses.append("category = ?")
            params.append(category.strip())
            
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            where_clauses.append("(code LIKE ? OR name LIKE ? OR specification LIKE ? OR parsed_params LIKE ?)")
            params.extend([kw, kw, kw, kw])
            
        where_str = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        
        # Count total matching
        count_sql = f"SELECT COUNT(*) FROM materials{where_str}"
        total_count = cursor.execute(count_sql, params).fetchone()[0]
        
        # Fetch paginated items
        offset = (page - 1) * page_size
        data_sql = f"SELECT code, name, specification, unit, category, brand, parsed_params FROM materials{where_str} ORDER BY rowid ASC LIMIT ? OFFSET ?"
        data_params = params + [page_size, offset]
        rows = cursor.execute(data_sql, data_params).fetchall()
        
        items = []
        for r in rows:
            parsed = {}
            if r[6]:
                try:
                    parsed = json.loads(r[6])
                except Exception:
                    pass
            items.append({
                "code": r[0] or "",
                "name": r[1] or "",
                "specification": r[2] or "",
                "unit": r[3] or "",
                "category": r[4] or "",
                "brand": r[5] or "",
                "model": parsed.get("model", ""),
                "attrs": parsed.get("attrs", {})
            })
            
        conn.close()
        
        # Get list of unique categories for frontend dropdown filter
        conn_cat = get_db_connection()
        cat_rows = conn_cat.cursor().execute("SELECT DISTINCT category FROM materials WHERE category IS NOT NULL AND category != '' ORDER BY category ASC").fetchall()
        all_categories = [c[0] for c in cat_rows]
        conn_cat.close()
        
        return {
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total_count + page_size - 1) // page_size),
            "categories": all_categories,
            "items": items
        }
    except Exception as e:
        logger.error(f"Error querying materials: {e}")
        raise HTTPException(status_code=500, detail=f"查询物料表失败: {str(e)}")

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Configuration and feedback files for auto-binding
BINDING_FEEDBACK_FILE = os.path.join(os.path.dirname(__file__), "binding_feedback.json")
AUTO_BINDING_RULES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "auto_binding_rules.json")

def load_auto_binding_rules() -> List[Dict[str, Any]]:
    if not os.path.exists(AUTO_BINDING_RULES_FILE):
        return []
    try:
        with open(AUTO_BINDING_RULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading auto binding rules: {e}")
        return []

def save_auto_binding_rules(rules: List[Dict[str, Any]]):
    try:
        with open(AUTO_BINDING_RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving auto binding rules: {e}")

# Thread Pool for concurrent matching
executor = ThreadPoolExecutor(max_workers=10)

@app.on_event("startup")
async def startup_event():
    """Startup initialization of DB and vector index."""
    logger.info("Server started. Initializing Auth, Session, SQLite DB and vector index mapping.")
    
    # Initialize Auth and Session databases
    from app.auth_manager import init_auth_db
    from app.session_manager import init_session_db, clean_old_draft_sessions
    init_auth_db()
    init_session_db()
    try:
        clean_old_draft_sessions()
    except Exception as e:
        logger.error(f"Error cleaning old drafts on startup: {e}")

    proj_dir = os.path.dirname(os.path.dirname(__file__))
    xlsx_path = os.path.join(proj_dir, "doc", "sample", "物料明细.xlsx")
    
    # Always call init_db to check schema and self-heal if necessary
    init_db(xlsx_path)
    
    # Verify/build vector index asynchronously in background thread to avoid blocking server startup
    import threading
    def build_index_async():
        logger.info("Starting background vector index verification/build...")
        try:
            index_manager.check_db_timestamp_and_build(DB_PATH)
            logger.info("Background vector index verification/build completed.")
        except Exception as e:
            logger.error(f"Error in background vector index build: {e}")

    threading.Thread(target=build_index_async, daemon=True).start()
    logger.info("SQLite DB, Auth DB, Session DB checked. Vector index build started in background.")

class BindingModel(BaseModel):
    code: str
    name: str
    specification: str
    unit: str
    brand: str
    category: str
    qty: float
    qty_ratio: Optional[float] = 1.0

class MatchedItemModel(BaseModel):
    original_row_idx: int
    original_name: str
    original_spec: str
    original_qty: float
    original_unit: str
    original_loss: float
    matched_code: str
    matched_name: str
    matched_category: str
    matched_spec: str
    matched_unit: str
    matched_brand: str
    professional: Optional[str] = "暖通"
    original_code: Optional[str] = ""
    bindings: Optional[List[BindingModel]] = None


class ExportRequest(BaseModel):
    items: List[MatchedItemModel]

class RematchRequest(BaseModel):
    row_id: int
    text: str
    fixed_params: Dict[str, Any] # {air_volume, power, dn, model}
    professional: Optional[str] = "暖通"

class ConfigSaveRequest(BaseModel):
    config_type: str # alias, units, stop_words, weights, tolerances, boosts
    data: Any

class FeedbackSubmitRequest(BaseModel):
    row_id: int
    raw_text: str
    auto_code: str
    manual_code: str
    is_correct: bool

@app.post("/api/upload", dependencies=[Depends(require_employee)])
async def upload_file(file: UploadFile = File(...)):
    """
    Upload original list and perform high performance batch matching.
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only Excel files (.xlsx, .xls) are supported.")
        
    logger.info(f"Received query list Excel upload: {file.filename}")
    file_id = str(uuid.uuid4())
    temp_path = os.path.join(UPLOAD_DIR, f"{file_id}_{file.filename}")
    
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        wb = openpyxl.load_workbook(temp_path, data_only=True)
        raw_rows = []
        
        # Helper to classify professional by sheet name
        def get_professional_by_sheet(sheet_name: str) -> str:
            name_lower = sheet_name.lower()
            if any(kw in name_lower for kw in ["装饰", "地材", "卷材", "橡胶", "pvc", "门", "手术室", "无影灯", "打胶", "自动门"]):
                return "装饰"
            if any(kw in name_lower for kw in ["强电", "动力", "照明", "变配电", "配电"]):
                return "强电"
            if any(kw in name_lower for kw in ["弱电", "智能化", "自控", "监控", "消防", "网络", "通信"]):
                return "弱电"
            if any(kw in name_lower for kw in ["给排水", "给水", "排水", "消火栓", "喷淋"]):
                return "给排水"
            if any(kw in name_lower for kw in ["暖通", "多联机", "空调", "通风"]):
                return "暖通"
            if any(kw in name_lower for kw in ["医气", "气体", "氧气", "吸引"]):
                return "医气"
            if any(kw in name_lower for kw in ["纯水", "水处理"]):
                return "纯水"
            return "暖通" # Fallback
            
        exclude_kws = ["说明", "汇总", "商务部", "备用"]
        
        global_row_counter = 1
        for sheet_name in wb.sheetnames:
            # Check sheet exclusion
            if any(kw in sheet_name for kw in exclude_kws):
                continue
                
            sheet = wb[sheet_name]
            professional = get_professional_by_sheet(sheet_name)
            
            # Find the header row in first 10 rows
            header_row = None
            for r in range(1, 11):
                if r > sheet.max_row:
                    break
                row_vals = [sheet.cell(r, c).value for c in range(1, sheet.max_column + 1)]
                row_strs = [str(x).strip() for x in row_vals if x is not None]
                if any(x in ["*编码", "项目编码", "编码", "项目名称", "*名称", "名称"] for x in row_strs) and any("规格" in x or "特征" in x for x in row_strs):
                    header_row = r
                    break
            
            if not header_row:
                # Fallback if no matching header row found: default column positions
                code_idx = 1
                name_idx = 2
                spec_idx = 3
                unit_idx = 4
                qty_idx = 5
                loss_idx = None
                category_idx = None
                brand_idx = None
                start_r = 1
            else:
                start_r = header_row + 1
                headers = [sheet.cell(header_row, c).value for c in range(1, sheet.max_column + 1)]
                
                # Default mappings (use first match to prevent duplicate trailing columns from overwriting indices)
                code_idx = None
                name_idx = None
                spec_idx = None
                unit_idx = None
                qty_idx = None
                loss_idx = None
                category_idx = None
                brand_idx = None
                
                for idx, h in enumerate(headers):
                    if not h:
                        continue
                    h_str = str(h).strip()
                    if "编码" in h_str and code_idx is None:
                        code_idx = idx + 1
                    elif "名称" in h_str and name_idx is None:
                        name_idx = idx + 1
                    elif ("规格" in h_str or "特征" in h_str) and spec_idx is None:
                        spec_idx = idx + 1
                    elif "单位" in h_str and unit_idx is None:
                        unit_idx = idx + 1
                    elif h_str in ["*数量", "数量", "工程量"] and qty_idx is None:
                        qty_idx = idx + 1
                    elif "损耗" in h_str and loss_idx is None:
                        loss_idx = idx + 1
                    elif h_str in ["甲供材品类", "品类", "分类"] and category_idx is None:
                        category_idx = idx + 1
                    elif "品牌" in h_str and brand_idx is None:
                        brand_idx = idx + 1

                # Set fallbacks if not found
                if code_idx is None: code_idx = 1
                if name_idx is None: name_idx = 2
                if spec_idx is None: spec_idx = 3
                if unit_idx is None: unit_idx = 4
                if qty_idx is None: qty_idx = 5
            
            # Extract data rows
            for r in range(start_r, sheet.max_row + 1):
                val_b = sheet.cell(r, name_idx).value  # 名称
                val_c = sheet.cell(r, spec_idx).value  # 规格
                
                # Skip headers, empty rows, total summary, subheadings
                if not val_b or str(val_b).strip() == "" or str(val_b).strip() == "*名称" or str(val_b).strip() == "合计" or str(val_b).strip() == "项目名称":
                    continue
                if str(val_b).strip().endswith("部分") or str(val_b).strip().startswith("机组设备"):
                    continue
                if val_c is None or str(val_c).strip() == "" or str(val_c).strip() == "规格型号/特征":
                    continue
                    
                qty_val = sheet.cell(r, qty_idx).value
                try:
                    qty = float(qty_val) if qty_val is not None else 0.0
                except ValueError:
                    qty = 0.0
                    
                loss = 1.0
                if loss_idx:
                    loss_val = sheet.cell(r, loss_idx).value
                    try:
                        loss = float(loss_val) if loss_val is not None else 1.0
                    except ValueError:
                        loss = 1.0
                
                category = ""
                if category_idx:
                    category = str(sheet.cell(r, category_idx).value or "").strip()
                    if category in ["", "/"]:
                        continue
                    
                brand = ""
                if brand_idx:
                    brand = str(sheet.cell(r, brand_idx).value or "").strip()
                    
                original_code = ""
                if code_idx:
                    original_code = str(sheet.cell(r, code_idx).value or "").strip()
                    
                raw_rows.append({
                    "row_idx": global_row_counter,
                    "code": original_code,
                    "name": str(val_b).strip(),
                    "specification": str(val_c).strip(),
                    "unit": str(sheet.cell(r, unit_idx).value or "台").strip(),
                    "qty": qty,
                    "loss": loss,
                    "category": category,
                    "brand": brand,
                    "professional": professional
                })
                global_row_counter += 1
                
        wb.close()
        os.remove(temp_path)
        
        # Batch match the collected rows
        parsed_rows = batch_match_rows(raw_rows)
        return JSONResponse(content={"items": parsed_rows})
        
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=f"Error parsing original list: {str(e)}")

def batch_match_rows(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Helper to perform batch encoding and threadpool matching in chunks of 500."""
    total_results = []
    
    # Load confirmed auto-binding rules
    rules = load_auto_binding_rules()
    rules_map = {r["original_name"].strip(): r["bound_materials"] for r in rules if r.get("original_name")}
    
    # Process in chunks of 500
    chunk_size = 500
    for idx in range(0, len(raw_rows), chunk_size):
        chunk = raw_rows[idx : idx + chunk_size]
        
        # 1. Pre-process chunk to extract refined names and clean specifications (removing construction noise)
        preprocessed_chunk = []
        for item in chunk:
            raw_name = item["name"].strip()
            real_name = raw_name
            if len(raw_name) <= 3 or any(kw in raw_name for kw in ["柜", "线", "管", "阀", "箱", "板", "支架", "按钮"]):
                real_name = extract_device_name(item["specification"], raw_name)
            
            clean_spec = remove_construction_info(item["specification"])
            preprocessed_chunk.append({
                "real_name": real_name,
                "clean_spec": clean_spec,
                "item": item
            })
            
        # 2. Batch vector encoding (main thread, avoiding PyTorch thread thrashing)
        query_vectors = None
        if index_manager.is_enabled:
            texts_to_encode = [f"{p['real_name']} {p['clean_spec']}".strip() for p in preprocessed_chunk]
            query_vectors = index_manager.encode_queries(texts_to_encode)
            
        # 3. Prepare match jobs for execution
        match_jobs = []
        for i, p in enumerate(preprocessed_chunk):
            item = p["item"]
            query_info = {
                "name": p["real_name"],
                "specification": p["clean_spec"],
                "category": item.get("category") or item.get("professional") or p["real_name"],
                "brand": item["brand"],
                "professional": item.get("professional", "暖通")
            }
            # Pre-parse specs with professional category hint
            query_info["parsed_spec"] = extract_query_params(item.get("name", ""), item["specification"], query_info["professional"])
            
            q_vector = query_vectors[i] if query_vectors is not None else None
            match_jobs.append((query_info, q_vector, item))
            
        # 4. Submit matching to ThreadPool to find exact matches for series clustering
        futures = []
        for q_info, q_vector, item in match_jobs:
            futures.append(executor.submit(match_item, q_info, q_vector))
            
        # Collect first-pass results
        first_pass_results = [f.result() for f in futures]
        
        # Determine confirmed series from exact matches (Score >= 85)
        confirmed_series = []
        for res in first_pass_results:
            if res["is_exact"] and res["top_candidates"]:
                top = res["top_candidates"][0]
                model = extract_params(top.get("specification", "")).get("model", "")
                if model:
                    prefix = model.split("-")[0].strip().lower()
                    if prefix not in confirmed_series:
                        confirmed_series.append(prefix)
                        
        # 4. Second pass: re-match non-exact matches using series clustering (fast, pure memory sorting)
        final_chunk_results = []
        for i, (q_info, q_vector, item) in enumerate(match_jobs):
            res = first_pass_results[i]
            
            item_name_clean = item["name"].strip()
            rule_bindings = rules_map.get(item_name_clean)
            
            if rule_bindings:
                # Apply confirmed auto-binding rule
                bindings_list = []
                for rb in rule_bindings:
                    ratio = rb.get("qty_ratio", 1.0)
                    bindings_list.append({
                        "code": rb.get("code", ""),
                        "name": rb.get("name", ""),
                        "specification": rb.get("specification", ""),
                        "unit": rb.get("unit", ""),
                        "brand": rb.get("brand", ""),
                        "category": rb.get("category", ""),
                        "qty": float(item["qty"]) * ratio
                    })
                first_b = bindings_list[0] if bindings_list else {}
                final_chunk_results.append({
                    "original_row_idx": item["row_idx"],
                    "original_name": item["name"],
                    "original_spec": item["specification"],
                    "original_qty": item["qty"],
                    "original_unit": item["unit"],
                    "original_loss": item["loss"],
                    "original_category": item["category"],
                    "professional": item.get("professional", "暖通"),
                    "original_code": item.get("code", ""),
                    "parsed_spec": q_info["parsed_spec"],
                    "is_exact": True, # Rule match counts as exact
                    "matched_code": ",".join([b["code"] for b in bindings_list]),
                    "matched_name": first_b.get("name", ""),
                    "matched_category": first_b.get("category", ""),
                    "matched_spec": first_b.get("specification", ""),
                    "matched_unit": first_b.get("unit", ""),
                    "matched_brand": first_b.get("brand", ""),
                    "top_candidates": res["top_candidates"],
                    "bindings": bindings_list
                })
            else:
                # If not exact match, rerun matching with confirmed series info to adjust ranks
                if not res["is_exact"] and confirmed_series:
                    res = match_item(q_info, q_vector, confirmed_series)
                    
                top_cands = res["top_candidates"]
                first_cand = top_cands[0] if top_cands else None
                
                bindings_list = []
                if first_cand:
                    bindings_list.append({
                        "code": res["matched_code"] or first_cand.get("code", ""),
                        "name": first_cand.get("name", ""),
                        "specification": first_cand.get("specification", ""),
                        "unit": first_cand.get("unit", item["unit"]),
                        "brand": first_cand.get("brand", ""),
                        "category": first_cand.get("category", ""),
                        "qty": float(item["qty"])
                    })
                    
                final_chunk_results.append({
                    "original_row_idx": item["row_idx"],
                    "original_name": item["name"],
                    "original_spec": item["specification"],
                    "original_qty": item["qty"],
                    "original_unit": item["unit"],
                    "original_loss": item["loss"],
                    "original_category": item["category"],
                    "professional": item.get("professional", "暖通"),
                    "original_code": item.get("code", ""),
                    "parsed_spec": q_info["parsed_spec"],
                    "is_exact": res["is_exact"],
                    "matched_code": res["matched_code"] or "",
                    "matched_name": first_cand.get("name", "") if first_cand else "",
                    "matched_category": first_cand.get("category", "") if first_cand else "",
                    "matched_spec": first_cand.get("specification", "") if first_cand else "",
                    "matched_unit": first_cand.get("unit", "") if first_cand else "",
                    "matched_brand": first_cand.get("brand", "") if first_cand else "",
                    "top_candidates": top_cands,
                    "bindings": bindings_list
                })
            
        total_results.extend(final_chunk_results)
        
    return total_results

@app.post("/api/batch_match", dependencies=[Depends(require_employee)])
async def api_batch_match(payload: Dict[str, Any]):
    """Match a batch of lines sent as JSON."""
    rows = payload.get("rows", [])
    if not rows:
        return JSONResponse(content={"results": []})
        
    # Map input format to expected format
    raw_rows = []
    for r in rows:
        raw_rows.append({
            "row_idx": r.get("id", 0),
            "name": r.get("text", ""),
            "specification": r.get("text", ""), # Use text for both name/spec if simplified
            "unit": "台",
            "qty": 1.0,
            "loss": 1.0,
            "category": r.get("category", ""),
            "brand": ""
        })
        
    results = batch_match_rows(raw_rows)
    return JSONResponse(content={"results": results})

@app.post("/api/match", dependencies=[Depends(require_employee)])
async def api_match_single(payload: Dict[str, Any]):
    """Match a single query item."""
    text = payload.get("text", "")
    name = payload.get("name", "") or extract_device_name(text)
    category = payload.get("category", "")
    brand = payload.get("brand", "")
    professional = payload.get("professional", "暖通")
    
    query_info = {
        "name": name,
        "specification": text,
        "category": category,
        "brand": brand,
        "professional": professional
    }
    if "parsed_spec" in payload:
        query_info["parsed_spec"] = payload["parsed_spec"]
        
    res = match_item(query_info)
    return JSONResponse(content=res)

@app.post("/api/rematch", dependencies=[Depends(require_employee)])
async def api_rematch(req: RematchRequest):
    """Rematch item after manual parameters override."""
    fixed = req.fixed_params
    professional = req.professional or "暖通"
    parsed = extract_params(req.text, professional)
    
    # Overwrite extracted params with user manually entered attributes based on professional category
    if professional == "强电":
        if "air_volume" in fixed and fixed["air_volume"] is not None:
            parsed["attrs"]["芯数"] = str(int(fixed["air_volume"]))
        if "power" in fixed and fixed["power"] is not None:
            parsed["attrs"]["功率"] = fixed["power"]
        if "dn" in fixed and fixed["dn"] is not None:
            parsed["attrs"]["截面积"] = str(fixed["dn"])
    elif professional in ["给排水", "纯水", "医气"]:
        if "dn" in fixed and fixed["dn"] is not None:
            parsed["attrs"]["管径"] = fixed["dn"]
        if "power" in fixed and fixed["power"] is not None:
            parsed["attrs"]["压力"] = fixed["power"]
    else:
        # HVAC / default
        if "air_volume" in fixed and fixed["air_volume"] is not None:
            parsed["attrs"]["风量"] = fixed["air_volume"]
        if "power" in fixed and fixed["power"] is not None:
            parsed["attrs"]["功率"] = fixed["power"]
        if "dn" in fixed and fixed["dn"] is not None:
            parsed["attrs"]["管径"] = fixed["dn"]
            
    if "model" in fixed and fixed["model"]:
        parsed["model"] = fixed["model"]
        
    query_info = {
        "name": extract_device_name(req.text),
        "specification": req.text,
        "category": "",
        "professional": professional,
        "parsed_spec": parsed
    }
    
    res = match_item(query_info)
    return JSONResponse(content=res)

@app.post("/api/refresh_vector", dependencies=[Depends(require_employee)])
async def api_refresh_vector():
    """Trigger manual vector index rebuild."""
    try:
        index_manager.check_db_timestamp_and_build(DB_PATH, force=True)
        return {"code": 0, "msg": "向量索引已成功刷新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/save", dependencies=[Depends(require_admin)])
async def api_save_config(req: ConfigSaveRequest):
    """Save user configuration panels variables to config json files."""
    filename_map = {
        "alias": "alias_dict.json",
        "units": "unit_rules.json",
        "stop_words": "stop_words.json",
        "weights": "weight_rules.json",
        "tolerances": "tolerance_rules.json",
        "boosts": "boost_rules.json"
    }
    
    filename = filename_map.get(req.config_type)
    if not filename:
        raise HTTPException(status_code=400, detail="Invalid config type")
        
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", filename)
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(req.data, f, ensure_ascii=False, indent=4)
        # Trigger runtime reload
        reload_preprocessor_configs()
        reload_matcher_configs()
        return {"code": 0, "msg": "配置保存成功并实时生效"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/feedback/submit", dependencies=[Depends(require_employee)])
async def api_submit_feedback(req: FeedbackSubmitRequest):
    """Submit human calibration logs."""
    from app.feedback_train import save_feedback_sample
    try:
        save_feedback_sample(req.dict())
        return {"code": 0, "msg": "反馈提交成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class BindingSubmitRequest(BaseModel):
    original_name: str
    original_spec: str
    original_qty: float
    bindings: List[BindingModel]

class BindingRuleConfirmRequest(BaseModel):
    original_name: str
    bindings: List[BindingModel]

class BindingRuleDeleteRequest(BaseModel):
    original_name: str

@app.post("/api/binding/submit", dependencies=[Depends(require_employee)])
async def api_submit_binding(req: BindingSubmitRequest):
    """Submit a list item's confirmed bindings to the feedback log for learning."""
    try:
        binds = []
        original_qty = req.original_qty if req.original_qty > 0 else 1.0
        for b in req.bindings:
            binds.append({
                "code": b.code,
                "name": b.name,
                "specification": b.specification,
                "unit": b.unit,
                "brand": b.brand,
                "category": b.category,
                "qty": b.qty,
                "qty_ratio": float(b.qty) / float(original_qty)
            })
        
        # Save to binding_feedback.json
        samples = []
        if os.path.exists(BINDING_FEEDBACK_FILE):
            try:
                with open(BINDING_FEEDBACK_FILE, "r", encoding="utf-8") as f:
                    samples = json.load(f)
            except Exception:
                pass
        
        samples.append({
            "original_name": req.original_name,
            "original_spec": req.original_spec,
            "bindings": binds
        })
        
        with open(BINDING_FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(samples, f, ensure_ascii=False, indent=4)
            
        return {"code": 0, "msg": "绑定关系已提交记录"}
    except Exception as e:
        logger.error(f"Error in api_submit_binding: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/binding/suggestions", dependencies=[Depends(require_employee)])
async def api_get_binding_suggestions():
    """Analyze binding feedback logs and suggest auto-binding rules."""
    try:
        if not os.path.exists(BINDING_FEEDBACK_FILE):
            return JSONResponse(content={"suggestions": []})
            
        with open(BINDING_FEEDBACK_FILE, "r", encoding="utf-8") as f:
            samples = json.load(f)
            
        groups = {} # original_name -> { serialized_bindings -> count }
        bindings_map = {} # serialized_bindings -> bindings_list
        
        for s in samples:
            name = s.get("original_name", "").strip()
            binds = s.get("bindings", [])
            if not name or not binds:
                continue
                
            # Normalize by sorting on material code
            sorted_binds = sorted(binds, key=lambda x: x.get("code", ""))
            
            cleaned_binds = []
            for b in sorted_binds:
                cleaned_binds.append({
                    "code": b.get("code", ""),
                    "name": b.get("name", ""),
                    "specification": b.get("specification", ""),
                    "unit": b.get("unit", ""),
                    "brand": b.get("brand", ""),
                    "category": b.get("category", ""),
                    "qty_ratio": b.get("qty_ratio", 1.0)
                })
                
            serialized = json.dumps(cleaned_binds, ensure_ascii=False)
            bindings_map[serialized] = cleaned_binds
            
            if name not in groups:
                groups[name] = {}
            groups[name][serialized] = groups[name].get(serialized, 0) + 1
            
        suggestions = []
        for name, spec_groups in groups.items():
            for serialized, count in spec_groups.items():
                suggestions.append({
                    "original_name": name,
                    "bindings": bindings_map[serialized],
                    "confidence_count": count
                })
                
        suggestions.sort(key=lambda x: x["confidence_count"], reverse=True)
        return JSONResponse(content={"suggestions": suggestions})
    except Exception as e:
        logger.error(f"Error in api_get_binding_suggestions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/binding/rules/confirm", dependencies=[Depends(require_employee)])
async def api_confirm_binding_rule(req: BindingRuleConfirmRequest):
    """Confirm/Enable a learned auto-binding rule."""
    try:
        rules = load_auto_binding_rules()
        
        new_rule = {
            "original_name": req.original_name.strip(),
            "bound_materials": [
                {
                    "code": b.code,
                    "name": b.name,
                    "specification": b.specification,
                    "unit": b.unit,
                    "brand": b.brand,
                    "category": b.category,
                    "qty_ratio": b.qty_ratio if b.qty_ratio is not None else 1.0
                }
                for b in req.bindings
            ]
        }
        
        rules = [r for r in rules if r.get("original_name", "").strip() != req.original_name.strip()]
        rules.append(new_rule)
        
        save_auto_binding_rules(rules)
        return {"code": 0, "msg": "规则已成功启用并保存"}
    except Exception as e:
        logger.error(f"Error in api_confirm_binding_rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/binding/rules/delete", dependencies=[Depends(require_employee)])
async def api_delete_binding_rule(req: BindingRuleDeleteRequest):
    """Delete a confirmed auto-binding rule."""
    try:
        rules = load_auto_binding_rules()
        rules = [r for r in rules if r.get("original_name", "").strip() != req.original_name.strip()]
        save_auto_binding_rules(rules)
        return {"code": 0, "msg": "规则已删除"}
    except Exception as e:
        logger.error(f"Error in api_delete_binding_rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/binding/rules", dependencies=[Depends(require_employee)])
async def api_get_binding_rules():
    """Retrieve all confirmed auto-binding rules."""
    try:
        rules = load_auto_binding_rules()
        return JSONResponse(content={"rules": rules})
    except Exception as e:
        logger.error(f"Error in api_get_binding_rules: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/search", dependencies=[Depends(require_employee)])
async def search_materials(keyword: str = "", limit: int = 50):
    """Search database for materials by keyword."""
    results = search_materials_keyword(keyword, limit=limit)
    return JSONResponse(content={"results": results})

@app.post("/api/export", dependencies=[Depends(require_employee)])
async def export_report(req: ExportRequest):
    """Export final verified matched items back to standard report template."""
    logger.info(f"Exporting matched items list as standard Excel report: {len(req.items)} rows.")
    proj_dir = os.path.dirname(os.path.dirname(__file__))
    template_path = os.path.join(proj_dir, "doc", "sample", "标准格式.xlsx")
    try:
        output_filename = f"标准格式_{uuid.uuid4().hex[:8]}.xlsx"
        output_path = os.path.join(UPLOAD_DIR, output_filename)
        
        items_to_write = [it.dict() for it in req.items]
        generate_standard_report(items_to_write, template_path, output_path)
        
        logger.info(f"Standard Excel report generated successfully: {output_filename}")
        return FileResponse(
            path=output_path,
            filename=output_filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        logger.error(f"Error generating standard report: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating standard report: {str(e)}")

@app.get("/api/config/load", dependencies=[Depends(require_manager)])
async def api_load_all_configs():
    """Load and return all runtime configurations for UI edit board."""
    from app.preprocessor import alias_dict, unit_rules, stop_words
    from app.matcher import weight_rules, tolerance_rules, boost_rules
    return JSONResponse(content={
        "alias": alias_dict,
        "units": unit_rules,
        "stop_words": stop_words,
        "weights": weight_rules,
        "tolerances": tolerance_rules,
        "boosts": boost_rules
    })

@app.get("/api/feedback/suggestions", dependencies=[Depends(require_employee)])
async def api_get_feedback_suggestions():
    """Get alias/typo correction recommendations from user corrections."""
    from app.feedback_train import get_feedback_suggestions
    suggestions = get_feedback_suggestions()
    return JSONResponse(content={"suggestions": suggestions})

@app.get("/api/logs", dependencies=[Depends(require_director)])
async def api_get_logs(limit: int = 100):
    """Retrieve the last N lines of system execution logs."""
    log_path = "logs/app_runtime.log"
    if not os.path.exists(log_path):
        return JSONResponse(content={"logs": []})
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # Return last N lines stripped
        last_lines = [line.strip() for line in lines[-limit:]]
        return JSONResponse(content={"logs": last_lines})
    except Exception as e:
        return JSONResponse(content={"logs": [f"Error reading log file: {str(e)}"]})

@app.get("/api/db/metadata", dependencies=[Depends(require_manager)])
async def api_db_metadata():
    """Get metadata for the current global material database."""
    from app.db_manager import get_db_metadata
    try:
        meta = get_db_metadata()
        return JSONResponse(content=meta)
    except Exception as e:
        logger.error(f"Error querying db metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/db/upload", dependencies=[Depends(require_director)])
async def api_db_upload(
    file: UploadFile = File(...),
    mode: str = Form("replace") # "replace" or "append"
):
    """Upload and import materials to the global database, then rebuild FAISS index."""
    temp_path = os.path.join(UPLOAD_DIR, f"db_{uuid.uuid4().hex}_{file.filename}")
    try:
        # Save temp file
        with open(temp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
            
        logger.info(f"Received material Excel import. Filename: {file.filename}, Mode: {mode}")
        
        # Import to SQLite
        from app.db_manager import import_materials_from_excel, get_db_connection
        meta = import_materials_from_excel(temp_path, mode, file.filename)
        
        # Reload vector index mapping & index from sqlite
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT code, name, specification, category FROM materials")
        materials = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        logger.info(f"Rebuilding vector index for {len(materials)} materials...")
        index_manager.build_index(materials)
        index_manager.save_cache()
        logger.info("Vector index rebuilt and saved successfully.")
        
        # Clean up temp file
        if os.path.exists(temp_path):
            os.remove(temp_path)
            
        return JSONResponse(content={"success": True, "metadata": meta})
    except Exception as e:
        logger.error(f"Error importing material database: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=f"Database import failed: {str(e)}")

from pydantic import BaseModel
from typing import Optional, List

class ReparseRequest(BaseModel):
    category: Optional[str] = None
    codes: Optional[List[str]] = None

@app.get("/api/db/schema/stats", dependencies=[Depends(require_manager)])
async def api_db_schema_stats():
    """Get structural metrics and field extraction stats for each domain."""
    from app.db_manager import get_db_schema_stats
    try:
        stats = get_db_schema_stats()
        return JSONResponse(content=stats)
    except Exception as e:
        logger.error(f"Error querying db schema stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/db/reparse", dependencies=[Depends(require_director)])
async def api_db_reparse(background_tasks: BackgroundTasks, req: Optional[ReparseRequest] = None):
    """
    Asynchronously reparse materials in the database with the new schema definitions.
    If req specifies category or codes, performs partial incremental reparse in place.
    Otherwise, performs double-buffer atomic swap full reparse.
    """
    from app.db_manager import reparse_materials_db_asymmetric, reparse_materials_db_partial
    
    category = req.category if req else None
    codes = req.codes if req else None
    
    def run_reparse_and_log():
        logger.info("Starting background database reparse...")
        try:
            if category or codes:
                res = reparse_materials_db_partial(category=category, codes=codes)
            else:
                res = reparse_materials_db_asymmetric()
            logger.info(f"Database reparse completed. Details: {res}")
            
            # Hot reload FAISS memory cache in current running server process
            logger.info("Hot reloading vector index in running process...")
            index_manager.load_cache()
            logger.info("Hot reload completed successfully.")
        except Exception as e:
            logger.error(f"Error during background database reparse: {e}")

    background_tasks.add_task(run_reparse_and_log)
    return JSONResponse(content={"success": True, "message": "Reparse task started in background."})


class LLMToggleRequest(BaseModel):
    enabled: bool

@app.get("/api/llm/status", dependencies=[Depends(require_manager)])
async def api_llm_status():
    """Get Tongyi Qwen LLM integration stats and circuit status."""
    from app.llm_client import llm_client
    try:
        return JSONResponse(content=llm_client.get_stats())
    except Exception as e:
        logger.error(f"Error querying LLM status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/llm/toggle", dependencies=[Depends(require_director)])
async def api_llm_toggle(req: LLMToggleRequest):
    """Enable or disable Tongyi Qwen LLM integration dynamically."""
    from app.llm_client import llm_client
    try:
        os.environ["LLM_ENABLED"] = "true" if req.enabled else "false"
        logger.info(f"LLM integration has been dynamically {'enabled' if req.enabled else 'disabled'}.")
        return JSONResponse(content={"success": True, "llm_enabled": llm_client.is_enabled()})
    except Exception as e:
        logger.error(f"Error toggling LLM integration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Mount web static directory
web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
os.makedirs(web_dir, exist_ok=True)
app.mount("/", StaticFiles(directory=web_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
