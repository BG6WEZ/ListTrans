# -*- coding: utf-8 -*-
import os

# Prevent OpenBLAS/PyTorch OpenMP thread thrashing on multi-threaded numpy matrix ops (P1 Fix H2)
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import logging
import shutil
import uuid
import pandas as pd
import openpyxl
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Depends, Request, BackgroundTasks, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import asyncio

from app.db_manager import init_db, search_materials_keyword, query_by_code, DB_PATH, get_db_connection
from app.preprocessor import extract_params, clean_text, reload_preprocessor_configs, extract_device_name, remove_construction_info
from app.query_preprocessor import extract_query_params, is_valid_query_item
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


from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

ENABLE_DOCS = os.getenv("ENABLE_DOCS", "false").lower() == "true"
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:18080,http://localhost:18080").split(",")

async def _daily_flywheel_mining_loop():
    """Background scheduler to trigger mining engine daily at 02:00."""
    logger.info("Flywheel daily mining scheduler registered.")
    while True:
        try:
            now = datetime.now()
            next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            sleep_secs = (next_run - now).total_seconds()
            await asyncio.sleep(sleep_secs)
            
            logger.info("Executing scheduled daily 02:00 flywheel mining run...")
            from app.feedback_miner import miner
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(executor, miner.run_mining)
            logger.info(f"Daily flywheel mining finished: {res}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in daily flywheel mining loop: {e}")
            await asyncio.sleep(3600)

@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Lifespan initialization of DB, vector index, and Data Flywheel scheduler."""
    logger.info("Server started. Initializing Auth, Session, Audit, SQLite DB, Flywheel tables and vector index mapping.")
    from app.auth_manager import init_auth_db
    from app.session_manager import init_session_db, clean_old_draft_sessions
    from app.audit_manager import init_audit_tables
    from app.flywheel_db import init_flywheel_tables
    init_auth_db()
    init_session_db()
    init_audit_tables()
    init_flywheel_tables()
    try:
        clean_old_draft_sessions()
    except Exception as e:
        logger.error(f"Error cleaning old drafts on startup: {e}")

    proj_dir = os.path.dirname(os.path.dirname(__file__))
    xlsx_path = os.path.join(proj_dir, "doc", "sample", "物料明细.xlsx")
    init_db(xlsx_path)
    
    logger.info("Initializing vector index mapping...")
    try:
        index_manager.check_db_timestamp_and_build(DB_PATH)
        logger.info("Vector index initialization completed.")
    except Exception as e:
        logger.error(f"Error in vector index initialization: {e}")

    # Launch daily 02:00 background mining scheduler
    mining_bg_task = asyncio.create_task(_daily_flywheel_mining_loop())

    logger.info("SQLite DB, Auth DB, Session DB checked and Vector Index ready.")
    yield
    mining_bg_task.cancel()

app = FastAPI(
    title="武汉华康世纪洁净科技股份有限公司 · 物料清单匹配系统",
    version="0.1.4",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    lifespan=lifespan
)

# Enable CORS: strictly use ALLOWED_ORIGINS whitelist instead of wildcard regex
origins_list = [origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins_list if origins_list else ["http://127.0.0.1:18080", "http://localhost:18080"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Security and Cache-Control headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # Ensure fresh content is always loaded without aggressive browser 304 caching
    if request.url.path.endswith((".html", ".js", ".css")) or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Include Auth and Session dependencies
from app.auth_middleware import require_employee, require_manager, require_director, require_admin, get_current_user
from app.admin_api import router as admin_router
from app.rule_api import router as rule_router
app.include_router(admin_router)
app.include_router(rule_router)

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
from app.audit_manager import (
    init_audit_tables, log_audit_event, record_heartbeat, 
    update_processing_task, increment_stat_counter
)

class LoginRequest(BaseModel):
    username: str
    password: str

class ProfileUpdateRequest(BaseModel):
    new_username: Optional[str] = Field(None, min_length=3, max_length=50, description="新登录名（如不修改可留空）")
    old_password: str = Field(..., min_length=1, description="当前密码（必填，用于安全核验）")
    new_password: Optional[str] = Field(None, min_length=6, description="新密码（选填，最少6位）")

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

class HeartbeatRequest(BaseModel):
    current_page: Optional[str] = "workbench"
    state: Optional[str] = "active"

@app.post("/api/auth/heartbeat")
async def api_heartbeat(req: HeartbeatRequest, request: Request, current_user: dict = Depends(get_current_user)):
    """Heartbeat endpoint for tracking real-time online users and session duration."""
    ip = request.client.host if request.client else "127.0.0.1"
    record_heartbeat(
        username=current_user["username"],
        name=current_user.get("name"),
        role=current_user.get("role"),
        dept=current_user.get("dept1") or current_user.get("dept2") or "",
        ip_address=ip,
        current_page=req.current_page or "workbench",
        state=req.state or "active"
    )
    return {"status": "ok"}

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
        log_audit_event(username=username, action="用户登录", details="密码验证失败", ip_address=ip, status="failed")
        security_logger.warning(f"Failed login attempt for username '{username}' from IP {ip}.")
        raise HTTPException(status_code=400, detail="用户名或密码错误。")
        
    if not user["is_active"]:
        log_login_attempt(username, ip, success=False)
        log_audit_event(username=username, action="用户登录", details="账号已被停用", ip_address=ip, status="failed")
        security_logger.warning(f"Inactive username '{username}' tried to login from IP {ip}.")
        raise HTTPException(status_code=403, detail="该账号已被停用，请联系管理员。")
        
    log_login_attempt(username, ip, success=True)
    update_user_last_login(user["id"])
    
    access_token = create_access_token(data={
        "sub": user["username"],
        "uid": user["id"],
        "emp_no": user.get("employee_no") or "",
        "role": user["role"]
    })
    display_name = user.get("name") or ("系统管理员" if user["username"] == "admin" else user["username"])
    position = user.get("position") or ("系统管理员" if user["username"] == "admin" else "员工")
    
    # Record success login audit log and heartbeat
    log_audit_event(
        username=user["username"],
        action="用户登录",
        details=f"登录系统 ({position})",
        ip_address=ip,
        name=display_name,
        role=user["role"],
        user_id=user["id"]
    )
    record_heartbeat(
        username=user["username"],
        name=display_name,
        role=user["role"],
        dept=user.get("dept1") or "",
        ip_address=ip,
        current_page="workbench",
        state="active"
    )
    
    security_logger.info(f"User '{user['username']}' ({display_name}, {position}) logged in successfully from IP {ip}.")
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "username": user["username"],
        "name": display_name,
        "position": position,
        "dept1": user.get("dept1") or "",
        "dept2": user.get("dept2") or "",
        "dept3": user.get("dept3") or "",
        "employee_no": user.get("employee_no") or "",
        "phone": user.get("phone") or user["username"],
        "role": user["role"]
    }

@app.post("/api/auth/logout")
async def api_logout(request: Request, current_user: dict = Depends(get_current_user)):
    """User logout endpoint to record audit event and clear session."""
    ip = request.client.host if request.client else "127.0.0.1"
    try:
        log_audit_event(
            username=current_user["username"],
            action="用户登出",
            details="主动退出系统",
            ip_address=ip,
            name=current_user.get("name"),
            role=current_user.get("role"),
            user_id=current_user.get("id")
        )
    except Exception as e:
        security_logger.warning(f"Error logging logout event: {e}")
    return {"status": "ok", "message": "已成功退出"}

@app.get("/api/auth/me")
async def api_get_me(current_user: dict = Depends(get_current_user)):
    display_name = current_user.get("name") or ("系统管理员" if current_user["username"] == "admin" else current_user["username"])
    position = current_user.get("position") or ("系统管理员" if current_user["username"] == "admin" else "员工")
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "name": display_name,
        "position": position,
        "dept1": current_user.get("dept1") or "",
        "dept2": current_user.get("dept2") or "",
        "dept3": current_user.get("dept3") or "",
        "employee_no": current_user.get("employee_no") or "",
        "need_emp_no_setup": bool(current_user.get("need_emp_no_setup")),
        "phone": current_user.get("phone") or current_user["username"],
        "role": current_user["role"]
    }

@app.put("/api/auth/profile")
async def api_update_profile(req: ProfileUpdateRequest, request: Request, current_user: dict = Depends(get_current_user)):
    """Self-service profile and password update endpoint."""
    user_id = current_user["id"]
    ip = request.client.host if request.client else "unknown"
    
    # 1. Verify old_password
    if not verify_password(req.old_password, current_user["password_hash"]):
        log_audit_event(
            username=current_user["username"],
            name=current_user.get("name"),
            role=current_user.get("role"),
            action="修改账号设置",
            details="验证当前密码失败",
            ip_address=ip,
            status="failed",
            user_id=user_id
        )
        raise HTTPException(status_code=400, detail="当前密码输入错误，请核实后重试。")
        
    conn = get_auth_connection()
    cursor = conn.cursor()
    
    updates = []
    params = []
    old_username = current_user["username"]
    new_username = (req.new_username or "").strip()
    username_changed = False
    
    # 2. Check username change
    if new_username and new_username != old_username:
        # Cross-field unique validation: must not collide with other users' username, employee_no or phone
        cursor.execute("""
            SELECT id FROM users 
            WHERE (username = ? OR employee_no = ? OR phone = ?) AND id != ?
        """, (new_username, new_username, new_username, user_id))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail="该登录名已被占用或与已有工号/手机号冲突，请更换。")
            
        updates.append("username = ?")
        params.append(new_username)
        username_changed = True
    else:
        new_username = old_username
        
    # 3. Check password change
    password_changed = False
    if req.new_password:
        new_pwd = req.new_password.strip()
        if len(new_pwd) < 6:
            conn.close()
            raise HTTPException(status_code=400, detail="新密码长度不能少于 6 位。")
        if verify_password(new_pwd, current_user["password_hash"]):
            conn.close()
            raise HTTPException(status_code=400, detail="新密码不能与当前密码完全相同。")
            
        new_hash = get_password_hash(new_pwd)
        updates.append("password_hash = ?")
        params.append(new_hash)
        password_changed = True
        
    if not updates:
        conn.close()
        return {"success": True, "detail": "没有检测到任何需要更新的项。"}
        
    try:
        params.append(user_id)
        cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        
        # Cascade update active_heartbeats table if username changed
        if username_changed:
            cursor.execute("UPDATE active_heartbeats SET username = ? WHERE username = ?", (new_username, old_username))
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        security_logger.error(f"Failed to update profile for user {old_username}: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")
    finally:
        conn.close()
        
    # Issue a refreshed access token so the user stays authenticated seamlessly
    refreshed_token = create_access_token(data={
        "sub": new_username,
        "uid": user_id,
        "emp_no": current_user.get("employee_no") or "",
        "role": current_user["role"]
    })
    
    audit_detail = f"工号 [{current_user.get('employee_no')}] 自助更新账号: "
    changes = []
    if username_changed: changes.append(f"登录名由 '{old_username}' 改为 '{new_username}'")
    if password_changed: changes.append("密码已修改")
    audit_detail += "; ".join(changes)
    
    log_audit_event(
        username=new_username,
        name=current_user.get("name"),
        role=current_user.get("role"),
        action="修改账号设置",
        details=audit_detail,
        ip_address=ip,
        status="success",
        user_id=user_id
    )
    security_logger.info(audit_detail)
    
    return {
        "success": True,
        "detail": "账号信息更新成功！",
        "access_token": refreshed_token,
        "token_type": "bearer",
        "username": new_username,
        "name": current_user.get("name") or new_username,
        "position": current_user.get("position") or "员工",
        "employee_no": current_user.get("employee_no") or "",
        "role": current_user["role"]
    }

@app.post("/api/session/draft")
async def api_save_draft(req: DraftSaveRequest, current_user: dict = Depends(get_current_user)):
    save_draft_session(current_user["id"], req.original_filename, req.professional, req.items_json)
    log_audit_event(
        username=current_user["username"],
        name=current_user.get("name"),
        role=current_user.get("role"),
        action="暂存草稿",
        details=f"保存自动草稿: {req.original_filename} ({req.professional})"
    )
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
    log_audit_event(
        username=current_user["username"],
        name=current_user.get("name"),
        role=current_user.get("role"),
        action="保存工作进度",
        details=f"保存命名进度: {req.session_name} ({req.original_filename})"
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

@app.get("/api/db/portrait", dependencies=[Depends(require_employee)])
async def api_get_db_portrait():
    """Calculate and return live Data Portrait metrics for materials.db."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. Total items
        total_items = cursor.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
        
        if total_items == 0:
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
        for (p_str,) in rows:
            if not p_str:
                continue
            try:
                p_obj = json.loads(p_str)
                if isinstance(p_obj, dict):
                    m = p_obj.get("model")
                    if m and str(m).strip():
                        model_extracted_count += 1
                    attrs = p_obj.get("attrs")
                    if attrs and isinstance(attrs, dict) and len(attrs) >= 2:
                        structured_attr_count += 1
            except Exception:
                pass
        
        model_missing_count = max(0, total_items - model_extracted_count)
        model_extracted_rate = round((model_extracted_count / total_items) * 100, 2) if total_items > 0 else 0.0
        structured_attr_rate = round((structured_attr_count / total_items) * 100, 2) if total_items > 0 else 0.0

        # Top 5 categories
        top_cats = cursor.execute(
            "SELECT category, COUNT(*) as cnt FROM materials WHERE category IS NOT NULL AND category != '' GROUP BY category ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        top_categories = [{"category": row[0], "count": row[1], "percent": round((row[1]/total_items)*100, 1)} for row in top_cats]
        
        # 7. Top 5 Units
        top_u = cursor.execute(
            "SELECT unit, COUNT(*) as cnt FROM materials WHERE unit IS NOT NULL AND unit != '' GROUP BY unit ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        top_units = [{"unit": row[0], "count": row[1], "percent": round((row[1]/total_items)*100, 1)} for row in top_u]
        
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
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

@app.delete("/api/system/reset")
async def api_system_reset(current_user: dict = Depends(require_admin)):
    """Reset system runtime data (clear sessions, uploads, feedback log, and flywheel tables). Only administrators are allowed."""
    try:
        from app.db_manager import get_db_cursor
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
                    json.dump([], f)
            except Exception as e:
                logger.error(f"Failed to reset binding feedback file: {e}")

        # 4. Clean flywheel runtime data (P2-7)
        with get_db_cursor(commit=True) as cur:
            cur.execute("DELETE FROM flywheel_records WHERE user_id != 'legacy_anonymous'")
            cur.execute("DELETE FROM flywheel_rule_suggestions")
            cur.execute("DELETE FROM flywheel_config_snapshots")
            cur.execute("DELETE FROM flywheel_pattern_aggregates")
                
        security_logger.info(f"System runtime data reset executed by admin '{current_user.get('username')}'.")
        return {"success": True, "message": "系统运行数据（会话草稿、上传临时文件、反馈日志及飞轮流水）已全面清理恢复干净状态！"}
    except Exception as e:
        logger.error(f"Error executing system reset: {e}")
        raise HTTPException(status_code=500, detail=f"清理系统数据失败: {str(e)}")

@app.get("/api/db/materials", dependencies=[Depends(require_employee)])
async def api_get_db_materials(
    keyword: Optional[str] = None,
    category: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """Query and search master materials with pagination."""
    conn = None
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
        
        # Get list of unique categories from cache
        all_categories = get_cached_categories()
        
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
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

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

# Thread Pool for concurrent matching (4 workers optimal for CPU throughput without thread thrashing)
executor = ThreadPoolExecutor(max_workers=4)

_upload_tasks_lock = threading.Lock()
upload_tasks: Dict[str, Dict[str, Any]] = {}

def update_task_progress(task_id: str, **kwargs):
    """Thread-safe update for upload / import task progress."""
    if not task_id:
        return
    with _upload_tasks_lock:
        if task_id in upload_tasks:
            upload_tasks[task_id].update(kwargs)

def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """Thread-safe retrieval of a task copy."""
    with _upload_tasks_lock:
        task = upload_tasks.get(task_id)
        return dict(task) if task else None

def set_task_initial(task_id: str, data: Dict[str, Any]):
    """Thread-safe initialization of a task."""
    with _upload_tasks_lock:
        upload_tasks[task_id] = data

def clean_expired_upload_tasks(max_tasks: int = 50, max_age_seconds: int = 1800):
    """Clean completed/failed tasks older than max_age_seconds or prune oldest if exceeding max_tasks."""
    now = time.time()
    with _upload_tasks_lock:
        # 1. Prune expired completed/failed tasks
        expired = [
            tid for tid, task in upload_tasks.items()
            if task.get("status") in ("completed", "failed") and (now - task.get("created_time", now)) > max_age_seconds
        ]
        for tid in expired:
            upload_tasks.pop(tid, None)
            
        # 2. If still exceeds max_tasks, prune oldest completed/failed
        if len(upload_tasks) > max_tasks:
            completed_or_failed = [
                (tid, task.get("created_time", 0)) 
                for tid, task in upload_tasks.items() 
                if task.get("status") in ("completed", "failed")
            ]
            sorted_tasks = sorted(completed_or_failed, key=lambda x: x[1])
            to_prune = len(upload_tasks) - max_tasks
            for tid, _ in sorted_tasks[:to_prune]:
                upload_tasks.pop(tid, None)

_categories_cache: Optional[List[str]] = None
_categories_cache_lock = threading.Lock()

def get_cached_categories() -> List[str]:
    """Get unique materials categories with in-memory caching."""
    global _categories_cache
    with _categories_cache_lock:
        if _categories_cache is None:
            conn = get_db_connection()
            try:
                cat_rows = conn.cursor().execute("SELECT DISTINCT category FROM materials WHERE category IS NOT NULL AND category != '' ORDER BY category ASC").fetchall()
                _categories_cache = [c[0] for c in cat_rows]
            finally:
                conn.close()
        return list(_categories_cache)

def invalidate_categories_cache():
    """Invalidate categories cache when materials table changes."""
    global _categories_cache
    with _categories_cache_lock:
        _categories_cache = None

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
    fixed_params: Dict[str, Any]
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

def _parse_and_prepare_upload_excel(task_id: str, temp_path: str) -> List[Dict[str, Any]]:
    """CPU-bound Excel extraction running in executor (M1 unblocking)."""
    wb = openpyxl.load_workbook(temp_path, data_only=True)
    raw_rows = []
    
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
        return "暖通"
        
    exclude_kws = ["说明", "汇总", "商务部", "备用"]
    update_task_progress(task_id, current_step="正在读取并解析 Excel 工作表...", structuring_progress=5)
    
    global_row_counter = 1
    total_sheets = len(wb.sheetnames)
    for s_idx, sheet_name in enumerate(wb.sheetnames):
        if any(kw in sheet_name for kw in exclude_kws):
            continue
            
        sheet = wb[sheet_name]
        professional = get_professional_by_sheet(sheet_name)
        
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
            code_idx, name_idx, spec_idx, unit_idx, qty_idx = 1, 2, 3, 4, 5
            loss_idx, category_idx, brand_idx = None, None, None
            start_r = 1
        else:
            start_r = header_row + 1
            headers = [sheet.cell(header_row, c).value for c in range(1, sheet.max_column + 1)]
            code_idx = name_idx = spec_idx = unit_idx = qty_idx = loss_idx = category_idx = brand_idx = None
            
            for idx, h in enumerate(headers):
                if not h: continue
                h_str = str(h).strip()
                if "编码" in h_str and code_idx is None: code_idx = idx + 1
                elif "名称" in h_str and name_idx is None: name_idx = idx + 1
                elif ("规格" in h_str or "特征" in h_str) and spec_idx is None: spec_idx = idx + 1
                elif "单位" in h_str and unit_idx is None: unit_idx = idx + 1
                elif h_str in ["*数量", "数量", "工程量"] and qty_idx is None: qty_idx = idx + 1
                elif "损耗" in h_str and loss_idx is None: loss_idx = idx + 1
                elif h_str in ["甲供材品类", "品类", "分类"] and category_idx is None: category_idx = idx + 1
                elif "品牌" in h_str and brand_idx is None: brand_idx = idx + 1

            if code_idx is None: code_idx = 1
            if name_idx is None: name_idx = 2
            if spec_idx is None: spec_idx = 3
            if unit_idx is None: unit_idx = 4
            if qty_idx is None: qty_idx = 5
        
        total_r = sheet.max_row
        for r in range(start_r, total_r + 1):
            val_b = sheet.cell(r, name_idx).value
            val_c = sheet.cell(r, spec_idx).value
            val_b_str = str(val_b or "").strip()
            val_c_str = str(val_c or "").strip()
            raw_unit = str(sheet.cell(r, unit_idx).value or "").strip() if unit_idx else ""
            original_code = str(sheet.cell(r, code_idx).value or "").strip() if code_idx else ""

            is_valid, _ = is_valid_query_item(val_b_str, val_c_str, raw_unit, original_code, r)
            if not is_valid:
                continue

            unit_str = raw_unit or "台"
            qty_val = sheet.cell(r, qty_idx).value
            try: qty = float(qty_val) if qty_val is not None else 0.0
            except ValueError: qty = 0.0

            loss = 1.0
            if loss_idx:
                loss_val = sheet.cell(r, loss_idx).value
                try: loss = float(loss_val) if loss_val is not None else 1.0
                except ValueError: loss = 1.0

            category = str(sheet.cell(r, category_idx).value or "").strip() if category_idx else ""
            brand = str(sheet.cell(r, brand_idx).value or "").strip() if brand_idx else ""

            raw_rows.append({
                "row_idx": global_row_counter,
                "code": original_code,
                "name": val_b_str,
                "specification": val_c_str,
                "unit": unit_str,
                "qty": qty,
                "loss": loss,
                "category": category,
                "brand": brand,
                "professional": professional
            })
            global_row_counter += 1

        sheet_prog = min(95, int(5 + ((s_idx + 1) / total_sheets) * 90))
        update_task_progress(task_id, structuring_progress=sheet_prog)

    wb.close()
    return raw_rows

async def async_process_upload_task(task_id: str, temp_path: str):
    matching_timeout = 120.0
    try:
        loop = asyncio.get_running_loop()
        # M1: Offload CPU-bound openpyxl parsing to executor
        raw_rows = await loop.run_in_executor(executor, _parse_and_prepare_upload_excel, task_id, temp_path)
        
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        
        update_task_progress(
            task_id,
            structuring_progress=100,
            current_step="Excel 清单数据解析完成，开始智能特征比对..."
        )
        
        update_processing_task(task_id, filename=os.path.basename(temp_path), total_rows=len(raw_rows), status="processing", current_step="正在智能清洗与特征比对...")
        increment_stat_counter("total_processed_rows", len(raw_rows))
        
        matching_timeout = max(120.0, len(raw_rows) * 1.5)
        parsed_rows = await asyncio.wait_for(
            loop.run_in_executor(executor, batch_match_rows, raw_rows, task_id),
            timeout=matching_timeout
        )
        
        update_processing_task(task_id, filename=os.path.basename(temp_path), total_rows=0, status="completed")
        log_audit_event(username="system", action="智能比对完成", details=f"成功完成 {len(raw_rows)} 行清单特征清洗与智能匹配")
        
        update_task_progress(
            task_id,
            status="completed",
            result=parsed_rows,
            current_step="所有清单项目已智能比对匹配完成！"
        )
        
    except asyncio.TimeoutError:
        update_processing_task(task_id, filename=os.path.basename(temp_path), total_rows=0, status="failed")
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except Exception: pass
        logger.error(f"Async upload task {task_id} timed out after {matching_timeout:.1f}s.")
        update_task_progress(
            task_id,
            status="failed",
            error=f"清单处理超时（超过 {matching_timeout:.1f} 秒），已自动终止。",
            current_step=f"处理超时: 超过 {matching_timeout:.1f} 秒"
        )
    except Exception as e:
        update_processing_task(task_id, filename=os.path.basename(temp_path), total_rows=0, status="failed")
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except Exception: pass
        logger.error(f"Error during async process upload task: {e}")
        update_task_progress(
            task_id,
            status="failed",
            error=str(e),
            current_step=f"处理出错: {str(e)}"
        )

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB limit

@app.post("/api/upload")
async def upload_file(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...), current_user: dict = Depends(require_employee)):
    """
    Upload original list and start high performance batch matching background task.
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only Excel files (.xlsx, .xls) are supported.")
        
    # Check Content-Length header first if present
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="文件大小超出限制（最大允许 50MB）。")
        
    safe_filename = os.path.basename(file.filename)
    logger.info(f"Received query list Excel upload: {safe_filename} by user {current_user.get('username')}")
    file_id = str(uuid.uuid4())
    temp_path = os.path.join(UPLOAD_DIR, f"{file_id}_{safe_filename}")
    
    total_written = 0
    with open(temp_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            total_written += len(chunk)
            if total_written > MAX_UPLOAD_SIZE:
                buffer.close()
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise HTTPException(status_code=413, detail="文件大小超出限制（最大允许 50MB）。")
            buffer.write(chunk)
        
    clean_expired_upload_tasks()
    task_id = str(uuid.uuid4())
    set_task_initial(task_id, {
        "status": "processing",
        "created_time": time.time(),
        "structuring_progress": 0,
        "vectorization_progress": 0,
        "matching_progress": 0,
        "current_step": "正在初始化后台比对任务...",
        "result": None,
        "error": None
    })
    
    log_audit_event(
        username=current_user["username"],
        name=current_user.get("name"),
        role=current_user.get("role"),
        action="上传清单",
        details=f"上传待匹配清单: {file.filename}"
    )
    
    background_tasks.add_task(async_process_upload_task, task_id, temp_path)
    return JSONResponse(content={"task_id": task_id})

@app.get("/api/upload/status", dependencies=[Depends(require_employee)])
async def get_upload_status(task_id: str):
    """
    Query current upload process progress status.
    """
    task = get_task_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="指定任务不存在")
    return JSONResponse(content=task)

ENABLE_SERIES_RERUN = os.getenv("ENABLE_SERIES_RERUN", "false").lower() == "true"

def batch_match_rows(raw_rows: List[Dict[str, Any]], task_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Helper to perform batch encoding and threadpool matching in chunks of 500."""
    import time
    
    logger.debug(f"batch_match_rows started for {len(raw_rows)} rows. task_id: {task_id}")
    total_results = []
    total_items = len(raw_rows)

    # Load confirmed auto-binding rules
    logger.debug("Loading auto binding rules...")
    rules = load_auto_binding_rules()
    rules_map = {r["original_name"].strip(): r["bound_materials"] for r in rules if r.get("original_name")}
    logger.debug(f"Loaded {len(rules_map)} rules.")

    # Step 1: Pre-process all rows
    if task_id:
        update_task_progress(task_id, current_step="正在对清单特征参数进行清洗与解析...")
    
    logger.debug("Step 1: Preprocessing query parameters...")
    preprocessed_rows = []
    for idx, item in enumerate(raw_rows):
        raw_name = item["name"].strip()
        raw_spec = item["specification"].strip()
        prof = item.get("professional", "暖通")

        parsed_info = extract_query_params(raw_name, raw_spec, prof)
        real_name = parsed_info.get("name") or raw_name
        clean_spec = parsed_info.get("spec") or raw_spec

        preprocessed_rows.append({
            "real_name": real_name,
            "clean_spec": clean_spec,
            "parsed_info": parsed_info,
            "item": item
        })
        
        if task_id and idx % 5 == 0:
            update_task_progress(task_id, current_step=f"【解析特征】 {raw_name[:25]} ({idx}/{total_items})")
    logger.debug(f"Preprocessing completed for {len(preprocessed_rows)} rows.")

    # Step 2: Batch Vectorization (Encoding)
    if task_id:
        update_task_progress(task_id, current_step="正在生成文本语义特征（向量化中）...", vectorization_progress=0)

    logger.debug("Step 2: Batch encoding...")
    from app.vector_index import index_manager
    embeddings_list = [None] * total_items
    
    if index_manager.is_enabled:
        chunk_size = 500
        for idx in range(0, total_items, chunk_size):
            chunk = preprocessed_rows[idx : idx + chunk_size]
            chunk_texts = [f"{p['real_name']} {p['clean_spec']}".strip() for p in chunk]
            
            logger.debug(f"Encoding chunk {idx} to {idx + len(chunk)}...")
            # Batch encode
            chunk_embeddings = index_manager.encode_queries(chunk_texts)
            if chunk_embeddings is not None:
                for offset, emb in enumerate(chunk_embeddings):
                    embeddings_list[idx + offset] = emb
            
            if task_id:
                vect_progress = min(100, int(((idx + len(chunk)) / total_items) * 100))
                update_task_progress(task_id, vectorization_progress=vect_progress, current_step=f"【特征提取】 {chunk[0]['real_name'][:25]} ({idx + len(chunk)}/{total_items})")
    else:
        logger.debug("Vector index not enabled. Skipping vector encoding.")
        if task_id:
            update_task_progress(task_id, vectorization_progress=100)
    logger.debug("Encoding completed.")

    # Step 3: Fast Matching (Search & Score)
    if task_id:
        update_task_progress(task_id, current_step="正在与标准库进行高精匹配与重打分...", matching_progress=0)

    logger.debug("Step 3: Sequential matching...")
    # Process in chunks of 500 for matching and series rank boosting
    chunk_size = 500
    for idx in range(0, total_items, chunk_size):
        chunk = preprocessed_rows[idx : idx + chunk_size]
        chunk_embeddings = embeddings_list[idx : idx + chunk_size]
        
        # Prepare match jobs
        match_jobs = []
        for offset, p in enumerate(chunk):
            item = p["item"]
            q_vector = chunk_embeddings[offset]
            query_info = {
                "name": p["real_name"],
                "specification": p["clean_spec"],
                "category": item.get("category") or item.get("professional") or p["real_name"],
                "brand": item["brand"],
                "professional": item.get("professional", "暖通"),
                "parsed_spec": p["parsed_info"]
            }
            match_jobs.append((query_info, q_vector, item))
            
        logger.debug(f"Matching jobs chunk {idx} to {idx + len(chunk)}...")
        # First pass sequential matching (using pre-computed vectors)
        first_pass_results = []
        for offset, (q_info, q_vector, item) in enumerate(match_jobs):
            current_idx = idx + offset + 1
            if task_id:
                update_task_progress(task_id, current_step=f"【智能匹配】 {q_info['name'][:25]} ({current_idx}/{total_items})", matching_progress=min(100, int((current_idx / total_items) * 100)))
            
            logger.debug(f"  Matching job {idx + offset}: name={q_info['name']}, spec={q_info['specification']}, category={q_info['category']}, brand={q_info['brand']}")
            t_start_match = time.time()
            res = match_item(q_info, q_vector)
            t_elapsed_match = time.time() - t_start_match
            logger.debug(f"    Job {idx + offset} matched in {t_elapsed_match:.4f}s. is_exact={res['is_exact']}. Candidates={len(res['top_candidates'])}")
            first_pass_results.append(res)
        
        logger.debug(f"Determining confirmed series for chunk {idx}...")
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
        logger.debug(f"Confirmed series: {confirmed_series}")
                        
        # Second pass: re-match non-exact matches using series clustering
        logger.debug(f"Second pass matching for chunk {idx}...")
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
                if not res["is_exact"] and confirmed_series and res.get("top_candidates"):
                    if ENABLE_SERIES_RERUN:
                        # Full second-pass rematch if explicitly enabled
                        res = match_item(q_info, q_vector, confirmed_series)
                    else:
                        # Fast in-memory series rank boost without re-running full recall/scoring (default)
                        for cand in res["top_candidates"]:
                            c_spec = cand.get("specification", "")
                            c_model = extract_params(c_spec).get("model", "")
                            if c_model:
                                c_prefix = c_model.split("-")[0].strip().lower()
                                if c_prefix in confirmed_series:
                                    cand["score"] = min(100.0, cand["score"] + 5.0)
                                    if "系列优先" not in cand.get("match_reasons", []):
                                        cand.setdefault("match_reasons", []).append("系列优先")
                        res["top_candidates"].sort(key=lambda x: x["score"], reverse=True)
                    
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
        
        if task_id:
            match_progress = min(100, int(((idx + len(chunk)) / total_items) * 100))
            update_task_progress(task_id, matching_progress=match_progress, current_step=f"正在与企业标准库进行深度比对与打分... ({idx + len(chunk)}/{total_items})")
            
    logger.debug("batch_match_rows completed.")
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
        
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(executor, batch_match_rows, raw_rows)
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
        
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(executor, match_item, query_info)
    return JSONResponse(content=res)

@app.post("/api/rematch")
async def api_rematch(req: RematchRequest, current_user: dict = Depends(require_employee)):
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
    
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(executor, match_item, query_info)
    
    log_audit_event(
        username=current_user["username"],
        name=current_user.get("name"),
        role=current_user.get("role"),
        action="单条特征重算",
        details=f"重算第 {req.row_id} 行: {req.text[:30]}"
    )
    return JSONResponse(content=res)

@app.post("/api/refresh_vector", dependencies=[Depends(require_employee)])
async def api_refresh_vector():
    """Trigger manual vector index rebuild."""
    try:
        index_manager.check_db_timestamp_and_build(DB_PATH, force=True)
        return {"code": 0, "msg": "向量索引已成功刷新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

_config_file_lock = threading.Lock()

def atomic_write_json_config(filepath: str, data: Any):
    """Atomically write JSON configuration using tempfile and os.replace with verification."""
    import tempfile
    with _config_file_lock:
        dir_name = os.path.dirname(filepath)
        os.makedirs(dir_name, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="cfg_tmp_", suffix=".json")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            # Verify file is valid JSON before replace
            with open(temp_path, "r", encoding="utf-8") as f:
                json.load(f)
            os.replace(temp_path, filepath)
        except Exception:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise

def trigger_all_config_reloads():
    """Trigger reload across all configuration consumers and clear caches."""
    try:
        reload_preprocessor_configs()
    except Exception as e:
        logger.warning(f"Error in reload_preprocessor_configs: {e}")
    try:
        reload_matcher_configs()
    except Exception as e:
        logger.warning(f"Error in reload_matcher_configs: {e}")
    try:
        from app.normalizer import reload_normalizer_configs
        reload_normalizer_configs()
    except Exception as e:
        logger.warning(f"Error in reload_normalizer_configs: {e}")
    try:
        from app.domain_extractors import reload_extractor_configs
        reload_extractor_configs()
    except Exception as e:
        logger.warning(f"Error in reload_extractor_configs: {e}")
    try:
        from app.domain_router import reload_router_configs
        reload_router_configs()
    except Exception as e:
        logger.warning(f"Error in reload_router_configs: {e}")
    try:
        from app.llm_rule_engine import LLMRuleManager
        LLMRuleManager.clear_active_cache()
    except Exception as e:
        logger.warning(f"Error in LLMRuleManager.clear_active_cache: {e}")

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
        
    config_dir = os.getenv("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))
    config_path = os.path.join(config_dir, filename)
    try:
        atomic_write_json_config(config_path, req.data)
        trigger_all_config_reloads()
        return {"code": 0, "msg": "配置保存成功并实时生效"}
    except Exception as e:
        logger.error(f"Error in api_save_config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class FlywheelRecordRequest(BaseModel):
    client_event_id: str
    session_id: str
    row_idx: int
    discipline: Optional[str] = "通用"
    raw_name: str
    raw_spec: Optional[str] = ""
    raw_unit: Optional[str] = ""
    raw_category: Optional[str] = ""
    extracted_params: Optional[Dict[str, Any]] = None
    auto_top1_code: Optional[str] = ""
    auto_top1_score: Optional[float] = 0.0
    auto_top1_name: Optional[str] = ""
    auto_candidates: Optional[List[Dict[str, Any]]] = None
    algo_version: Optional[str] = "v1.0"
    config_version: Optional[str] = ""
    action_type: str
    final_targets: List[Dict[str, Any]]

@app.post("/api/flywheel/record")
async def api_flywheel_record(req: FlywheelRecordRequest, current_user: dict = Depends(require_employee)):
    """Record user match action and calibration context into Data Flywheel with idempotency."""
    from app.db_manager import get_db_cursor
    from app.preprocessor import clean_text
    try:
        user_id = str(current_user.get("username") or current_user.get("id") or "anonymous")
        user_name = str(current_user.get("name") or user_id)
        raw_name_norm = clean_text(req.raw_name)
        extracted_params_json = json.dumps(req.extracted_params or {}, ensure_ascii=False)
        auto_candidates_json = json.dumps(req.auto_candidates[:3] if req.auto_candidates else [], ensure_ascii=False)
        final_targets_json = json.dumps(req.final_targets or [], ensure_ascii=False)
        
        with get_db_cursor(commit=True) as cur:
            cur.execute("""
            INSERT OR IGNORE INTO flywheel_records (
                client_event_id, session_id, row_idx, discipline,
                raw_name, raw_name_norm, raw_spec, raw_unit, raw_category,
                extracted_params, auto_top1_code, auto_top1_score, auto_top1_name,
                auto_candidates_json, algo_version, config_version, action_type,
                final_targets_json, user_id, user_name, superseded, is_processed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
            """, (
                req.client_event_id, req.session_id, req.row_idx, req.discipline or "通用",
                req.raw_name, raw_name_norm, req.raw_spec or "", req.raw_unit or "", req.raw_category or "",
                extracted_params_json, req.auto_top1_code or "", float(req.auto_top1_score or 0.0), req.auto_top1_name or "",
                auto_candidates_json, req.algo_version or "v1.0", req.config_version or "", req.action_type,
                final_targets_json, user_id, user_name
            ))
            
            if cur.rowcount == 1:
                new_id = cur.lastrowid
                cur.execute("""
                UPDATE flywheel_records SET superseded = 1
                WHERE session_id = ? AND row_idx = ? AND id < ? AND superseded = 0
                """, (req.session_id, req.row_idx, new_id))
                
        return {"code": 0, "msg": "操作流水已成功记录"}
    except Exception as e:
        logger.error(f"Error in api_flywheel_record: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class FlywheelEvalRequest(BaseModel):
    suggestion_id: int

class FlywheelAcceptRequest(BaseModel):
    suggestion_id: int
    edited_target_value: Optional[str] = None

class FlywheelRejectRequest(BaseModel):
    suggestion_id: int
    reason: Optional[str] = ""

class FlywheelRollbackRequest(BaseModel):
    snapshot_version: Optional[str] = None

@app.get("/api/flywheel/statistics", dependencies=[Depends(require_manager)])
async def api_flywheel_statistics():
    from app.db_manager import get_db_cursor
    with get_db_cursor() as cur:
        cur.execute("SELECT COUNT(*) as total FROM flywheel_records")
        total_records = cur.fetchone()["total"]
        
        cur.execute("SELECT action_type, COUNT(*) as cnt FROM flywheel_records WHERE superseded = 0 GROUP BY action_type")
        action_counts = {r["action_type"]: r["cnt"] for r in cur.fetchall()}
        
        cur.execute("SELECT COUNT(DISTINCT user_id) as users FROM flywheel_records WHERE user_id != 'legacy_anonymous'")
        active_users = cur.fetchone()["users"]
        
        cur.execute("SELECT status, COUNT(*) as cnt FROM flywheel_rule_suggestions GROUP BY status")
        suggestion_counts = {r["status"]: r["cnt"] for r in cur.fetchall()}
        
        cur.execute("SELECT COUNT(*) as snap_cnt FROM flywheel_config_snapshots WHERE is_active = 1")
        active_snapshots = cur.fetchone()["snap_cnt"]
        
    return JSONResponse(content={
        "total_records": total_records,
        "action_counts": action_counts,
        "active_users": active_users,
        "suggestion_counts": suggestion_counts,
        "active_snapshots": active_snapshots
    })

@app.get("/api/flywheel/suggestions", dependencies=[Depends(require_manager)])
async def api_flywheel_suggestions(status: Optional[str] = None):
    from app.db_manager import get_db_cursor
    with get_db_cursor() as cur:
        if status:
            cur.execute("""
            SELECT * FROM flywheel_rule_suggestions
            WHERE status = ?
            ORDER BY support_count DESC, consistency DESC, id DESC
            """, (status,))
        else:
            cur.execute("""
            SELECT * FROM flywheel_rule_suggestions
            ORDER BY CASE status WHEN 'pending' THEN 1 WHEN 'observing' THEN 2 WHEN 'accepted' THEN 3 ELSE 4 END,
                     support_count DESC, consistency DESC
            """)
        rows = [dict(r) for r in cur.fetchall()]
    return JSONResponse(content={"suggestions": rows})

@app.post("/api/flywheel/suggestions/eval", dependencies=[Depends(require_admin)])
async def api_flywheel_eval_suggestion(req: FlywheelEvalRequest):
    from app.db_manager import get_db_cursor
    from app.flywheel_sandbox import start_sandbox_evaluation
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM flywheel_rule_suggestions WHERE id = ?", (req.suggestion_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        sug_dict = dict(row)
        
    task_id = start_sandbox_evaluation(sug_dict)
    return {"code": 0, "task_id": task_id, "msg": "沙箱评测任务已启动"}

@app.get("/api/flywheel/eval/{task_id}", dependencies=[Depends(require_admin)])
async def api_flywheel_get_eval_status(task_id: str):
    from app.flywheel_sandbox import get_eval_task_status
    status = get_eval_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Eval task not found")
    return JSONResponse(content=status)

@app.post("/api/flywheel/suggestions/accept", dependencies=[Depends(require_admin)])
async def api_flywheel_accept_suggestion(req: FlywheelAcceptRequest, current_user: dict = Depends(require_admin)):
    """Accept a suggestion with two-phase atomic write, snapshot creation, and compensating rollback."""
    from app.db_manager import get_db_cursor
    operator_id = str(current_user.get("username") or "admin")
    config_dir = os.getenv("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))
    snap_version = f"snap_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    
    # 1. Fetch suggestion details
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM flywheel_rule_suggestions WHERE id = ?", (req.suggestion_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        sug = dict(row)
        
    target_value = req.edited_target_value if req.edited_target_value is not None else sug["target_value"]
    target_type = sug["suggestion_type"]
    source_pattern = sug["source_pattern"]
    
    if target_type == "ALIAS":
        target_path = os.path.join(config_dir, "alias_dict.json")
        before_data = {}
        if os.path.exists(target_path):
            with open(target_path, "r", encoding="utf-8") as f:
                before_data = json.load(f)
        after_data = dict(before_data)
        after_data[source_pattern] = target_value
        reason = f"采纳别名 '{source_pattern}' -> '{target_value}'" + (" (经人工编辑)" if req.edited_target_value else "")
    elif target_type == "BINDING_RULE":
        target_path = os.path.join(config_dir, "auto_binding_rules.json")
        before_data = []
        if os.path.exists(target_path):
            with open(target_path, "r", encoding="utf-8") as f:
                before_data = json.load(f)
        after_data = list(before_data)
        after_data.append({
            "original_name": source_pattern,
            "bound_materials": json.loads(target_value) if isinstance(target_value, str) else target_value
        })
        reason = f"采纳多物料绑定规则 '{source_pattern}'" + (" (经人工编辑)" if req.edited_target_value else "")
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported suggestion type: {target_type}")

    # 2. Atomic write config file
    try:
        atomic_write_json_config(target_path, after_data)
    except Exception as e:
        logger.error(f"Failed to write config file {target_path}: {e}")
        raise HTTPException(status_code=500, detail=f"配置文件写入失败: {str(e)}")

    # 3. Update DB state in transaction (Snapshot + Suggestion status)
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("""
            INSERT INTO flywheel_config_snapshots (
                snapshot_version, target_type, config_before, config_after,
                suggestion_id, reason, operator_id, is_active, targeted_eval_passed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)
            """, (
                snap_version, target_type,
                json.dumps(before_data, ensure_ascii=False),
                json.dumps(after_data, ensure_ascii=False),
                req.suggestion_id,
                reason,
                operator_id
            ))
            
            cur.execute("""
            UPDATE flywheel_rule_suggestions
            SET status = 'accepted', target_value = ?, reviewer_id = ?, reviewed_at = CURRENT_TIMESTAMP,
                applied_rule_id = ?
            WHERE id = ?
            """, (target_value, operator_id, snap_version, req.suggestion_id))
            
            log_audit_event(
                username=operator_id,
                action="飞轮规则采纳",
                details=f"采纳 {target_type} 建议: {source_pattern} -> {target_value} (快照: {snap_version})"
            )
    except Exception as e:
        # Compensating rollback of the config file
        logger.error(f"DB update failed during suggestion accept, performing compensating file rollback: {e}")
        try:
            atomic_write_json_config(target_path, before_data)
        except Exception as ce:
            logger.critical(f"Compensating rollback failed for {target_path}: {ce}")
        raise HTTPException(status_code=500, detail=f"数据库更新失败，已安全复原配置文件: {str(e)}")

    # 4. Trigger in-memory cache reload outside of DB transaction
    try:
        trigger_all_config_reloads()
    except Exception as e:
        logger.error(f"Error during trigger_all_config_reloads: {e}")

    return {"code": 0, "msg": "规则已成功采纳并秒级生效", "snapshot_version": snap_version}

@app.post("/api/flywheel/suggestions/reject", dependencies=[Depends(require_admin)])
async def api_flywheel_reject_suggestion(req: FlywheelRejectRequest, current_user: dict = Depends(require_admin)):
    from app.db_manager import get_db_cursor
    operator_id = str(current_user.get("username") or "admin")
    with get_db_cursor(commit=True) as cur:
        cur.execute("""
        UPDATE flywheel_rule_suggestions
        SET status = 'rejected', reviewer_id = ?, reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (operator_id, req.suggestion_id))
        
        log_audit_event(
            username=operator_id,
            action="飞轮规则驳回",
            details=f"驳回建议 ID: {req.suggestion_id}, 原因: {req.reason or '无'}"
        )
    return {"code": 0, "msg": "建议已标记为驳回"}

@app.get("/api/flywheel/snapshots", dependencies=[Depends(require_admin)])
async def api_flywheel_get_snapshots():
    from app.db_manager import get_db_cursor
    with get_db_cursor() as cur:
        cur.execute("""
        SELECT * FROM flywheel_config_snapshots
        ORDER BY id DESC
        LIMIT 50
        """)
        rows = [dict(r) for r in cur.fetchall()]
    return JSONResponse(content={"snapshots": rows})

@app.post("/api/flywheel/rollback", dependencies=[Depends(require_admin)])
async def api_flywheel_rollback_snapshot(req: FlywheelRollbackRequest, current_user: dict = Depends(require_admin)):
    """LIFO stack-based rollback to safely restore previous configuration."""
    from app.db_manager import get_db_cursor
    operator_id = str(current_user.get("username") or "admin")
    config_dir = os.getenv("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))
    
    # 1. Fetch snapshot info
    with get_db_cursor() as cur:
        if req.snapshot_version:
            cur.execute("""
            SELECT * FROM flywheel_config_snapshots
            WHERE snapshot_version = ? AND is_active = 1
            """, (req.snapshot_version,))
        else:
            cur.execute("""
            SELECT * FROM flywheel_config_snapshots
            WHERE is_active = 1
            ORDER BY id DESC LIMIT 1
            """)
        snap = cur.fetchone()
        if not snap:
            raise HTTPException(status_code=400, detail="没有可回滚的有效快照")
        snap = dict(snap)
            
    snap_id = snap["id"]
    snap_ver = snap["snapshot_version"]
    target_type = snap["target_type"]
    config_before = json.loads(snap["config_before"])
    config_after = json.loads(snap["config_after"])
    suggestion_id = snap["suggestion_id"]
    
    if target_type == "ALIAS":
        target_path = os.path.join(config_dir, "alias_dict.json")
    elif target_type == "BINDING_RULE":
        target_path = os.path.join(config_dir, "auto_binding_rules.json")
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported target type: {target_type}")
        
    # 2. Write config file first
    try:
        atomic_write_json_config(target_path, config_before)
    except Exception as e:
        logger.error(f"Failed to write config file {target_path} during rollback: {e}")
        raise HTTPException(status_code=500, detail=f"配置文件回滚写入失败: {str(e)}")

    # 3. Update DB state in transaction
    try:
        with get_db_cursor(commit=True) as cur:
            if suggestion_id:
                cur.execute("""
                UPDATE flywheel_rule_suggestions
                SET status = 'rolled_back', reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """, (suggestion_id,))
                
            cur.execute("UPDATE flywheel_config_snapshots SET is_active = 0 WHERE id = ?", (snap_id,))
            
            log_audit_event(
                username=operator_id,
                action="飞轮规则回滚",
                details=f"回滚快照: {snap_ver} (目标: {target_type})"
            )
    except Exception as e:
        # Compensating file restore
        try:
            atomic_write_json_config(target_path, config_after)
        except Exception as ce:
            logger.critical(f"Compensating file restore failed: {ce}")
        raise HTTPException(status_code=500, detail=f"数据库回滚记录失败，已复原配置: {str(e)}")

    # 4. Trigger reload outside DB transaction
    try:
        trigger_all_config_reloads()
    except Exception as e:
        logger.error(f"Error during trigger_all_config_reloads after rollback: {e}")
        
    return {"code": 0, "msg": f"快照 {snap_ver} 已成功回滚并恢复配置"}

@app.post("/api/flywheel/miner/run", dependencies=[Depends(require_admin)])
async def api_flywheel_run_miner():
    """Trigger manual mining run."""
    from app.feedback_miner import miner
    res = miner.run_mining()
    return {"code": 0, "result": res, "msg": "数据挖掘与规则聚类完成"}

@app.get("/api/flywheel/export/benchmark", dependencies=[Depends(require_admin)])
async def api_flywheel_export_benchmark():
    """Export golden evaluation set with flywheel_auto isolated cases (excluding already matched aliases)."""
    from app.db_manager import get_db_cursor
    from app.normalizer import alias_dict
    with get_db_cursor() as cur:
        cur.execute("""
        SELECT raw_name, raw_spec, final_targets_json, action_type
        FROM flywheel_records
        WHERE action_type IN ('CONFIRM_TOP1', 'MANUAL_REPLACE') AND superseded = 0
        LIMIT 500
        """)
        rows = cur.fetchall()
        
    items = []
    for r in rows:
        targets = json.loads(r["final_targets_json"] or "[]")
        t_code = targets[0].get("code", "") if targets else ""
        t_name = targets[0].get("name", "") if targets else ""
        
        # P1-3: Prevent leakage: skip items where raw_name is already in alias_dict and action was CONFIRM_TOP1
        if r["raw_name"] in alias_dict and r["action_type"] == "CONFIRM_TOP1":
            continue
            
        items.append({
            "id": f"FA-{len(items)+1:04d}",
            "source": "flywheel_auto",
            "name": r["raw_name"],
            "raw_name": r["raw_name"],
            "specification": r["raw_spec"],
            "raw_spec": r["raw_spec"],
            "target_codes": [t_code] if t_code else [],
            "target_name": t_name,
            "label_type": "1to1",
            "professional": "通用",
            "ambiguous": False
        })
    return JSONResponse(content={"items": items, "count": len(items)})

@app.get("/api/flywheel/export/triplets", dependencies=[Depends(require_admin)])
async def api_flywheel_export_triplets():
    """Export BGE contrastive triplets (Anchor, Positive, Hard Negative)."""
    from app.db_manager import get_db_cursor
    with get_db_cursor() as cur:
        cur.execute("""
        SELECT raw_name, raw_spec, auto_top1_name, final_targets_json
        FROM flywheel_records
        WHERE action_type = 'MANUAL_REPLACE' AND auto_top1_name != '' AND superseded = 0
        LIMIT 500
        """)
        rows = cur.fetchall()
        
    triplets = []
    for r in rows:
        targets = json.loads(r["final_targets_json"] or "[]")
        t_name = targets[0].get("name", "") if targets else ""
        if t_name and r["auto_top1_name"] and t_name != r["auto_top1_name"]:
            triplets.append({
                "anchor": f"{r['raw_name']} {r['raw_spec']}".strip(),
                "positive": t_name,
                "negative": r["auto_top1_name"]
            })
    return JSONResponse(content={"triplets": triplets, "count": len(triplets)})

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
                    data = json.load(f)
                    if isinstance(data, list):
                        samples = data
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
            if not isinstance(samples, list):
                samples = []
            
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

@app.post("/api/binding/rules/confirm")
async def api_confirm_binding_rule(req: BindingRuleConfirmRequest, current_user: dict = Depends(require_employee)):
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
        log_audit_event(
            username=current_user["username"],
            name=current_user.get("name"),
            role=current_user.get("role"),
            action="确认绑定规则",
            details=f"启用物料绑定规则: {req.original_name} (关联 {len(req.bindings)} 项物料)"
        )
        return {"code": 0, "msg": "规则已成功启用并保存"}
    except Exception as e:
        logger.error(f"Error in api_confirm_binding_rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/binding/rules/delete")
async def api_delete_binding_rule(req: BindingRuleDeleteRequest, current_user: dict = Depends(require_employee)):
    """Delete a confirmed auto-binding rule."""
    try:
        rules = load_auto_binding_rules()
        rules = [r for r in rules if r.get("original_name", "").strip() != req.original_name.strip()]
        save_auto_binding_rules(rules)
        log_audit_event(
            username=current_user["username"],
            name=current_user.get("name"),
            role=current_user.get("role"),
            action="删除绑定规则",
            details=f"删除物料绑定规则: {req.original_name}"
        )
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

@app.post("/api/export")
async def export_report(req: ExportRequest, current_user: dict = Depends(require_employee)):
    """Export final verified matched items back to standard report template."""
    logger.info(f"Exporting matched items list as standard Excel report: {len(req.items)} rows by {current_user.get('username')}.")
    proj_dir = os.path.dirname(os.path.dirname(__file__))
    template_path = os.path.join(proj_dir, "doc", "sample", "标准格式.xlsx")
    try:
        output_filename = f"标准格式_{uuid.uuid4().hex[:8]}.xlsx"
        output_path = os.path.join(UPLOAD_DIR, output_filename)
        
        items_to_write = [it.dict() for it in req.items]
        generate_standard_report(items_to_write, template_path, output_path)
        
        # Increment total exported rows and log audit
        increment_stat_counter("total_exported_rows", len(req.items))
        log_audit_event(
            username=current_user["username"],
            name=current_user.get("name"),
            role=current_user.get("role"),
            action="导出报表",
            details=f"导出标准物料清单: {output_filename} (共 {len(req.items)} 行)"
        )
        
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

_db_import_lock = threading.Lock()

def async_process_db_upload_task(task_id: str, temp_path: str, mode: str, filename: str, disable_mode: str = "on_expire"):
    try:
        logger.info(f"Background task {task_id} started for db upload. Filename: {filename}, Mode: {mode}, DisableMode: {disable_mode}")
        invalidate_categories_cache()
        update_task_progress(task_id, current_step="正在解析并清洗物料数据...", structuring_progress=10)
        
        # Import to SQLite
        from app.db_manager import import_materials_from_excel, get_db_connection
        meta = import_materials_from_excel(temp_path, mode, filename, task_id=task_id, disable_mode=disable_mode)
        
        update_task_progress(task_id, structuring_progress=100, current_step="物料数据结构化完成，开始重建向量索引...", vectorization_progress=10)
        
        # Reload vector index mapping & index from sqlite
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT code, name, specification, category FROM materials")
            materials = [dict(row) for row in cursor.fetchall()]
        finally:
            try:
                conn.close()
            except Exception:
                pass
        
        update_task_progress(task_id, current_step=f"正在为 {len(materials)} 项物料生成向量特征并重建索引...", vectorization_progress=30)
        
        # Rebuild vector index
        index_manager.build_index(materials, task_id=task_id)
        index_manager.save_cache()
        invalidate_categories_cache()
        logger.info("Vector index rebuilt and saved successfully.")
        
        update_task_progress(task_id, vectorization_progress=100, matching_progress=100, status="completed", result=meta, current_step="标准物料数据库导入及向量索引重建全部完成！")
    except Exception as e:
        logger.error(f"Error during async process db upload task: {e}")
        update_task_progress(task_id, status="failed", error=str(e), current_step=f"导入出错: {str(e)}")
    finally:
        try:
            _db_import_lock.release()
        except Exception:
            pass
        # Clean up temp file
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

@app.post("/api/db/upload", dependencies=[Depends(require_director)])
async def api_db_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    mode: str = Form("replace"), # "replace" or "append"
    disable_mode: str = Form("on_expire") # "on_expire" or "immediate"
):
    """Upload and import materials to the global database, then rebuild FAISS index asynchronously."""
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only Excel files (.xlsx, .xls) are supported.")
        
    if not _db_import_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="当前已有物料库导入或索引重建任务正在运行中，请稍候再试。")
        
    safe_filename = os.path.basename(file.filename)
    temp_path = os.path.join(UPLOAD_DIR, f"db_{uuid.uuid4().hex}_{safe_filename}")
    with open(temp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
        
    task_id = str(uuid.uuid4())
    set_task_initial(task_id, {
        "status": "processing",
        "structuring_progress": 0,
        "vectorization_progress": 0,
        "matching_progress": 0,
        "current_step": "正在初始化后台物料库导入任务...",
        "result": None,
        "error": None
    })
    
    background_tasks.add_task(async_process_db_upload_task, task_id, temp_path, mode, file.filename, disable_mode)
    return JSONResponse(content={"task_id": task_id})

from pydantic import BaseModel, Field
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
            invalidate_categories_cache()
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
    port = int(os.getenv("PORT", "18080"))
    uvicorn.run(app, host=host, port=port)
