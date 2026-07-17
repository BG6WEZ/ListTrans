# -*- coding: utf-8 -*-
import os
import logging
import shutil
import uuid
import pandas as pd
import openpyxl
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
import json

from app.db_manager import init_db, search_materials_keyword, query_by_code, DB_PATH
from app.preprocessor import extract_params, clean_text, reload_preprocessor_configs, extract_device_name
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


app = FastAPI(title="华康洁净物料清单匹配工具")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Thread Pool for concurrent matching
executor = ThreadPoolExecutor(max_workers=10)

@app.on_event("startup")
async def startup_event():
    """Startup initialization of DB and vector index."""
    logger.info("Server started. Initializing SQLite DB and vector index mapping.")
    proj_dir = os.path.dirname(os.path.dirname(__file__))
    xlsx_path = os.path.join(proj_dir, "doc", "sample", "物料明细.xlsx")
    
    # Always call init_db to check schema and self-heal if necessary
    init_db(xlsx_path)
    
    # Verify/build vector index
    index_manager.check_db_timestamp_and_build(DB_PATH)
    logger.info("SQLite DB checked and vector index verified.")

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

@app.post("/api/upload")
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
                
                # Default mappings
                code_idx = 1
                name_idx = 2
                spec_idx = 3
                unit_idx = 4
                qty_idx = 5
                loss_idx = None
                category_idx = None
                brand_idx = None
                
                for idx, h in enumerate(headers):
                    if not h:
                        continue
                    h_str = str(h).strip()
                    if "编码" in h_str:
                        code_idx = idx + 1
                    elif "名称" in h_str:
                        name_idx = idx + 1
                    elif "规格" in h_str or "特征" in h_str:
                        spec_idx = idx + 1
                    elif "单位" in h_str:
                        unit_idx = idx + 1
                    elif h_str in ["*数量", "数量", "工程量"]:
                        qty_idx = idx + 1
                    elif "损耗" in h_str:
                        loss_idx = idx + 1
                    elif h_str in ["甲供材品类", "品类", "分类"]:
                        category_idx = idx + 1
                    elif "品牌" in h_str:
                        brand_idx = idx + 1
            
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
                    
                raw_rows.append({
                    "row_idx": global_row_counter,
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
    
    # Process in chunks of 500
    chunk_size = 500
    for idx in range(0, len(raw_rows), chunk_size):
        chunk = raw_rows[idx : idx + chunk_size]
        
        # 1. Batch vector encoding (main thread, avoiding PyTorch thread thrashing)
        query_vectors = None
        if index_manager.is_enabled:
            texts_to_encode = [f"{item['name']} {item['specification']}".strip() for item in chunk]
            query_vectors = index_manager.encode_queries(texts_to_encode)
            
        # 2. Extract parameters and prepare match items
        match_jobs = []
        for i, item in enumerate(chunk):
            query_info = {
                "name": item["name"],
                "specification": item["specification"],
                "category": item["category"],
                "brand": item["brand"],
                "professional": item.get("professional", "暖通")
            }
            # Pre-parse specs with professional category hint
            query_info["parsed_spec"] = extract_params(item["specification"], item["professional"])
            
            q_vector = query_vectors[i] if query_vectors is not None else None
            match_jobs.append((query_info, q_vector, item))
            
        # 3. First pass: Submit matching to ThreadPool to find exact matches for series clustering
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
            # If not exact match, rerun matching with confirmed series info to adjust ranks
            if not res["is_exact"] and confirmed_series:
                res = match_item(q_info, q_vector, confirmed_series)
                
            final_chunk_results.append({
                "original_row_idx": item["row_idx"],
                "original_name": item["name"],
                "original_spec": item["specification"],
                "original_qty": item["qty"],
                "original_unit": item["unit"],
                "original_loss": item["loss"],
                "original_category": item["category"],
                "professional": item.get("professional", "暖通"),
                "parsed_spec": q_info["parsed_spec"],
                "is_exact": res["is_exact"],
                "matched_code": res["matched_code"] or "",
                "matched_name": res["top_candidates"][0]["name"] if res["top_candidates"] else "",
                "matched_category": res["top_candidates"][0]["category"] if res["top_candidates"] else "",
                "matched_spec": res["top_candidates"][0]["specification"] if res["top_candidates"] else "",
                "matched_unit": res["top_candidates"][0]["unit"] if res["top_candidates"] else "",
                "matched_brand": res["top_candidates"][0]["brand"] if res["top_candidates"] else "",
                "top_candidates": res["top_candidates"]
            })
            
        total_results.extend(final_chunk_results)
        
    return total_results

@app.post("/api/batch_match")
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

@app.post("/api/match")
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

@app.post("/api/rematch")
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

@app.post("/api/refresh_vector")
async def api_refresh_vector():
    """Trigger manual vector index rebuild."""
    try:
        index_manager.check_db_timestamp_and_build(DB_PATH, force=True)
        return {"code": 0, "msg": "向量索引已成功刷新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/save")
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

@app.post("/api/feedback/submit")
async def api_submit_feedback(req: FeedbackSubmitRequest):
    """Submit human calibration logs."""
    from app.feedback_train import save_feedback_sample
    try:
        save_feedback_sample(req.dict())
        return {"code": 0, "msg": "反馈提交成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/search")
async def search_materials(keyword: str = "", limit: int = 50):
    """Search database for materials by keyword."""
    results = search_materials_keyword(keyword, limit=limit)
    return JSONResponse(content={"results": results})

@app.post("/api/export")
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

@app.get("/api/config/load")
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

@app.get("/api/feedback/suggestions")
async def api_get_feedback_suggestions():
    """Get alias/typo correction recommendations from user corrections."""
    from app.feedback_train import get_feedback_suggestions
    suggestions = get_feedback_suggestions()
    return JSONResponse(content={"suggestions": suggestions})

@app.get("/api/logs")
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

@app.get("/api/db/metadata")
async def api_db_metadata():
    """Get metadata for the current global material database."""
    from app.db_manager import get_db_metadata
    try:
        meta = get_db_metadata()
        return JSONResponse(content=meta)
    except Exception as e:
        logger.error(f"Error querying db metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/db/upload")
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

# Mount web static directory
web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
os.makedirs(web_dir, exist_ok=True)
app.mount("/", StaticFiles(directory=web_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
