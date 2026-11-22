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
import math
import re
import shutil
import sqlite3
import uuid
import pandas as pd
import openpyxl
import time
from contextlib import asynccontextmanager, closing, ExitStack
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Depends, Request, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StrictInt, field_validator
from typing import List, Optional, Dict, Any, Tuple
from logging.handlers import RotatingFileHandler
import json
import threading
import asyncio

from app.db_manager import (
    init_db, search_materials_keyword, query_by_code, DB_PATH, get_db_connection,
    catalog_lock, catalog_read, catalog_write, get_db_revision,
)
from app.preprocessor import extract_params, clean_text, reload_preprocessor_configs, extract_device_name, remove_construction_info
from app.query_preprocessor import extract_query_params, is_valid_query_item
from app.vector_index import index_manager
from app.matcher import match_item, reload_matcher_configs
from app.report_generator import generate_standard_report
from app.runtime_eval_assets import RuntimeAssetError
from app.runtime_jobs import (
    JobRejected, JobStopped, catalog_jobs, checkpoint, current_job, eval_jobs, export_jobs,
    http_job, match_jobs, mining_jobs, prune_terminal_tasks, rule_jobs, terminal_ttl, upload_jobs,
)
from app.runtime_retention import ManagedFileResponse, active_path, clean_runtime_files
from app.excel_boundary import (
    DEFAULT_MAX_UPLOAD_BYTES,
    DbImportLockManager,
    ExcelBoundaryError,
    ParseCancelledError,
    UploadTooLargeError,
    ensure_workbook_non_empty,
    load_validated_excel_sheets,
    remove_file_quietly,
    stream_save_upload_file,
    validate_extension,
)

# Ensure logs directory exists (test isolation: LOG_DIR redirects runtime logs)
LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Configure system logger
logger = logging.getLogger("listtrans_app")
logger.setLevel(logging.INFO)

# Avoid duplicate handlers on hot reload
if not logger.handlers:
    fh = RotatingFileHandler(
        os.path.join(LOG_DIR, "app_runtime.log"), encoding="utf-8",
        maxBytes=max(65536, int(os.getenv("LISTTRANS_LOG_MAX_BYTES", "10485760"))),
        backupCount=max(1, int(os.getenv("LISTTRANS_LOG_BACKUPS", "5"))),
    )
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
            res = await mining_jobs.run(miner.run_mining, timeout=300)
            logger.info(f"Daily flywheel mining finished: {res}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error in daily flywheel mining loop (%s): %s", type(e).__name__, e)
            await asyncio.sleep(3600)

async def _expire_runtime_tasks():
    from app.flywheel_sandbox import expire_eval_tasks

    while True:
        await asyncio.sleep(60)
        clean_expired_upload_tasks()
        expire_eval_tasks()


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
    await asyncio.to_thread(deliver_priority_feedback)
    try:
        clean_old_draft_sessions()
    except Exception as e:
        logger.error(f"Error cleaning old drafts on startup: {e}")

    init_db()
    
    logger.info("Initializing vector index mapping...")
    try:
        index_manager.check_db_timestamp_and_build(DB_PATH)
        logger.info("Vector index initialization completed.")
    except Exception as e:
        logger.error(f"Error in vector index initialization: {e}")

    # Launch daily 02:00 background mining scheduler
    mining_bg_task = asyncio.create_task(_daily_flywheel_mining_loop())

    logger.info("SQLite DB, Auth DB, Session DB checked and Vector Index ready.")
    expiry_task = asyncio.create_task(_expire_runtime_tasks())
    priority_delivery_task = asyncio.create_task(_retry_priority_feedback())
    try:
        yield
    finally:
        mining_bg_task.cancel()
        expiry_task.cancel()
        priority_delivery_task.cancel()
        await asyncio.gather(mining_bg_task, expiry_task, priority_delivery_task, return_exceptions=True)

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
from app.auth_middleware import (
    require_employee,
    require_manager,
    require_director,
    require_admin,
    get_current_user,
    get_current_user_allow_password_change,
)
from app.admin_api import router as admin_router
from app.rule_api import router as rule_router
app.include_router(admin_router)
app.include_router(rule_router)

# Authentication and Session API Endpoints
from app.auth_manager import (
    verify_password, get_user_by_username, create_access_token,
    update_user_last_login, log_login_attempt, is_user_locked_out,
    security_logger, get_auth_connection,
    authenticate_user_and_issue_token, revoke_token_jti,
    change_user_credentials_atomic, resolve_verified_user,
)
from app.session_manager import (
    save_draft_session, get_draft_session, clear_draft_session, save_named_session, list_saved_sessions,
    get_session_by_id, update_named_session, delete_session_by_id, clear_all_runtime_data
)
from app.binding_decisions import (
    STATUS_RECOMMENDED,
    STATUS_AUTO_ACCEPTED,
    STATUS_MANUAL_CONFIRMED,
    STATUS_REJECTED,
    EXPORTABLE_STATUSES,
    SOURCE_MATCHER,
    SOURCE_RULE,
    SOURCE_MANUAL,
    SOURCE_MANUAL_REJECT,
    SOURCE_MANUAL_EDIT,
    SOURCE_RESTORE,
    make_scope_key,
    row_content_fingerprint,
    binding_fingerprint,
    validate_bindings_against_row,
    validate_binding_codes,
    evaluate_hard_conflicts,
    normalize_bindings,
    record_decision,
    invalidate_decision,
    get_decision,
    get_row_decision,
    extract_saved_bindings,
    fetch_live_material_map,
    get_unit_multiplier,
)
from app.acceptance_calibration import active_policy as acceptance_policy, assess_match, confirmed_binding, load_policy
from app.match_assessment import assess_initial_match, summarize_initial_matches
from app.llm_decision import CallBudget, active_engine as active_llm_decision
from app.bom import expand as expand_bom, is_bom_name, positive as positive_quantity
from app.audit_manager import (
    init_audit_tables, log_audit_event, record_heartbeat, 
    update_processing_task, increment_stat_counter
)
from app.feedback_contract import (
    FeedbackContractError,
    annotate_items_identity,
    compute_runtime_versions,
    extract_verified_actor,
    get_row_trace,
    load_verified_source_row,
    persist_upload_document_tree,
    record_flywheel_event,
    record_rematch_snapshot,
    verify_document_ownership,
)
from app.flywheel_db import open_flywheel_tx_connection
from app.source_binding_review import (
    IDENTITY, SOURCE_PRIORITY_REVIEW, check_revision, check_source_write, commit_priority,
    confirmed_source, deliver_priority_feedback, invalidate_source, priority_eligibility,
    priority_key, receipt_for, resolve_source, source_decision, source_version, source_write,
    write_source_decision,
)


async def _retry_priority_feedback():
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(deliver_priority_feedback)


def _binding_source(materials, user, row, filename, document_id=None):
    try:
        return resolve_source(materials, user, row, filename or "", document_id=document_id)
    except FeedbackContractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@catalog_read
def _save_rematch_revision(**kwargs):
    # Share the auth write lock with confirmation so revision checks serialize with rematch.
    with source_write():
        return record_rematch_snapshot(**kwargs)


class SourceReviewFields(BaseModel):
    document_id: Optional[str] = None
    sheet_id: Optional[str] = None
    source_row_id: Optional[str] = None
    recommendation_revision: Optional[StrictInt] = Field(default=None, ge=1)
    decision_version: Optional[StrictInt] = Field(default=None, ge=0)

class LoginRequest(BaseModel):
    username: str
    password: str

class ProfileUpdateRequest(BaseModel):
    new_username: Optional[str] = Field(None, min_length=3, max_length=50, description="新登录名（如不修改可留空）")
    old_password: str = Field(..., min_length=1, description="当前密码（必填，用于安全核验）")
    new_password: Optional[str] = Field(None, min_length=8, description="新密码（选填，至少8位）")

class DraftSaveRequest(BaseModel):
    original_filename: str
    professional: str
    items_json: str
    document_id: Optional[str] = ""
    work_session_id: Optional[str] = ""

class NamedSaveRequest(BaseModel):
    session_name: str
    original_filename: str
    professional: str
    items_json: str
    document_id: Optional[str] = ""
    work_session_id: Optional[str] = ""

class UpdateSessionRequest(BaseModel):
    items_json: str
    document_id: Optional[str] = None
    work_session_id: Optional[str] = None

class HeartbeatRequest(BaseModel):
    current_page: Optional[str] = "workbench"
    state: Optional[str] = "active"

@app.post("/api/auth/heartbeat")
def api_heartbeat(req: HeartbeatRequest, request: Request, current_user: dict = Depends(get_current_user_allow_password_change)):
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
def api_login(req: LoginRequest, request: Request):
    username = req.username.strip()
    ip = request.client.host if request.client else "unknown"

    is_locked, remaining = is_user_locked_out(username, ip)
    if is_locked:
        raise HTTPException(
            status_code=429,
            detail=f"登录失败次数过多。为了账号安全，已临时锁定，请在 {remaining} 秒后再试。"
        )

    user, access_token, auth_status = authenticate_user_and_issue_token(username, req.password)
    if auth_status == "inactive":
        log_login_attempt(username, ip, success=False)
        log_audit_event(username=username, action="用户登录", details="账号已被停用", ip_address=ip, status="failed")
        security_logger.warning(f"Inactive username '{username}' tried to login from IP {ip}.")
        raise HTTPException(status_code=403, detail="该账号已被停用，请联系管理员。")

    if auth_status != "ok" or not user or not access_token:
        log_login_attempt(username, ip, success=False)
        log_audit_event(username=username, action="用户登录", details="密码验证失败或凭据并发变更", ip_address=ip, status="failed")
        security_logger.warning(f"Failed login attempt for username '{username}' from IP {ip}.")
        raise HTTPException(status_code=400, detail="用户名或密码错误。")

    log_login_attempt(username, ip, success=True)
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

    must_change = bool(user.get("must_change_password"))
    security_logger.info(
        f"User '{user['username']}' ({display_name}, {position}) logged in from IP {ip} (must_change_password={must_change})."
    )
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
        "role": user["role"],
        "must_change_password": must_change,
    }

@app.post("/api/auth/logout")
def api_logout(request: Request, current_user: dict = Depends(get_current_user_allow_password_change)):
    """User logout endpoint: persistently revokes current JWT jti across restarts and records audit event."""
    ip = request.client.host if request.client else "127.0.0.1"
    jti = current_user.get("_token_jti")
    if jti:
        revoke_token_jti(
            jti=jti,
            user_id=current_user.get("id"),
            token_version=current_user.get("_token_tv"),
            expires_at=current_user.get("_token_exp"),
            reason="logout",
        )
    try:
        log_audit_event(
            username=current_user["username"],
            action="用户登出",
            details="主动退出系统（已注销当前会话凭据）",
            ip_address=ip,
            name=current_user.get("name"),
            role=current_user.get("role"),
            user_id=current_user.get("id")
        )
    except Exception as e:
        security_logger.warning(f"Error logging logout event: {e}")
    return {"status": "ok", "message": "已成功退出，当前登录凭据已注销"}

@app.get("/api/auth/me")
def api_get_me(current_user: dict = Depends(get_current_user_allow_password_change)):
    display_name = current_user.get("name") or ("系统管理员" if current_user["username"] == "admin" else current_user["username"])
    position = current_user.get("position") or ("系统管理员" if current_user["username"] == "admin" else "员工")
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "account_epoch": str(current_user.get("account_epoch") or ""),
        "name": display_name,
        "position": position,
        "dept1": current_user.get("dept1") or "",
        "dept2": current_user.get("dept2") or "",
        "dept3": current_user.get("dept3") or "",
        "employee_no": current_user.get("employee_no") or "",
        "need_emp_no_setup": bool(current_user.get("need_emp_no_setup")),
        "must_change_password": bool(current_user.get("must_change_password")),
        "phone": current_user.get("phone") or current_user["username"],
        "role": current_user["role"]
    }

@app.put("/api/auth/profile")
def api_update_profile(
    req: ProfileUpdateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user_allow_password_change),
):
    """Self-service profile & password update endpoint; atomically bumps token_version and invalidates prior sessions."""
    user_id = current_user["id"]
    ip = request.client.host if request.client else "unknown"
    old_username = current_user["username"]

    new_pwd_stripped = req.new_password.strip() if req.new_password is not None else None
    res = change_user_credentials_atomic(
        user_id=user_id,
        old_password=req.old_password,
        new_username=req.new_username,
        new_password=new_pwd_stripped,
        current_jti=current_user.get("_token_jti"),
        current_exp=current_user.get("_token_exp"),
    )
    if not res.get("success"):
        log_audit_event(
            username=old_username,
            name=current_user.get("name"),
            role=current_user.get("role"),
            action="修改账号设置",
            details=res.get("error", "修改账号设置失败"),
            ip_address=ip,
            status="failed",
            user_id=user_id,
        )
        raise HTTPException(status_code=res.get("status_code", 400), detail=res.get("error", "修改失败。"))

    updated_user = res["user"]
    new_username = updated_user["username"]
    username_changed = bool(res.get("username_changed"))
    password_changed = bool(res.get("password_changed"))

    if not username_changed and not password_changed:
        return {"success": True, "relogin_required": False, "detail": "没有检测到任何需要更新的项。"}

    if username_changed:
        conn = get_auth_connection()
        try:
            conn.execute("UPDATE active_heartbeats SET username = ? WHERE username = ?", (new_username, old_username))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    audit_detail = f"工号 [{updated_user.get('employee_no')}] 自助更新账号: "
    changes = []
    if username_changed:
        changes.append(f"登录名由 '{old_username}' 改为 '{new_username}'")
    if password_changed:
        changes.append("密码已修改（旧凭据及全部旧会话已失效）")
    audit_detail += "; ".join(changes)

    log_audit_event(
        username=new_username,
        name=updated_user.get("name"),
        role=updated_user.get("role"),
        action="修改账号设置",
        details=audit_detail,
        ip_address=ip,
        status="success",
        user_id=user_id,
    )
    security_logger.info(audit_detail)

    return {
        "success": True,
        "relogin_required": True,
        "must_change_password": False,
        "access_token": None,
        "token_type": "bearer",
        "detail": "凭据更新成功！原临时凭据与旧会话已立即失效，请使用新凭据重新登录。",
        "username": new_username,
        "name": updated_user.get("name") or new_username,
        "position": updated_user.get("position") or "员工",
        "employee_no": updated_user.get("employee_no") or "",
        "role": updated_user["role"],
    }

def _resolve_session_document_binding(
    current_user: dict,
    document_id: Optional[str],
    work_session_id: Optional[str],
) -> Tuple[str, str, str]:
    """Validate optional document binding on session save without permitting forged ownership."""
    raw_doc = str(document_id or "").strip()
    if not raw_doc:
        return "", "", "legacy_untrusted"
    try:
        actor = extract_verified_actor(current_user)
        from app.flywheel_db import init_flywheel_tables, open_flywheel_tx_connection

        init_flywheel_tables()
        conn = open_flywheel_tx_connection()
        try:
            doc = verify_document_ownership(
                conn.cursor(),
                actor["actor_user_id"],
                actor["actor_account_epoch"],
                raw_doc,
            )
        finally:
            conn.close()
        req_ws = str(work_session_id or "").strip()
        if req_ws and req_ws != str(doc["work_session_id"] or ""):
            raise FeedbackContractError(422, "work_session_mismatch", "工作会话标识与业务文档不匹配。")
        return doc["document_id"], doc["work_session_id"], "trusted"
    except FeedbackContractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@app.post("/api/session/draft")
def api_save_draft(req: DraftSaveRequest, current_user: dict = Depends(get_current_user)):
    doc_id, ws_id, trust_level = _resolve_session_document_binding(
        current_user, req.document_id, req.work_session_id
    )
    session_id = save_draft_session(
        current_user["id"],
        req.original_filename,
        req.professional,
        _checked_session_decisions(req.items_json, current_user, req.original_filename, doc_id),
        account_epoch=str(current_user.get("account_epoch") or ""),
        document_id=doc_id,
        work_session_id=ws_id,
        trust_level=trust_level,
    )
    log_audit_event(
        username=current_user["username"],
        name=current_user.get("name"),
        role=current_user.get("role"),
        action="暂存草稿",
        details=f"保存自动草稿: {req.original_filename} ({req.professional})"
    )
    return {"success": True, "draft_id": session_id, "document_id": doc_id, "work_session_id": ws_id, "trust_level": trust_level}

@app.get("/api/session/draft")
def api_get_draft(current_user: dict = Depends(get_current_user)):
    draft = get_draft_session(current_user["id"], account_epoch=str(current_user.get("account_epoch") or ""))
    if not draft:
        return {"draft": None}
    return {
        "draft": {
            "id": draft["id"],
            "original_filename": draft["original_filename"],
            "professional": draft["professional"],
            "items_json": _checked_session_decisions(draft["items_json"], current_user, draft["original_filename"],
                                                     draft.get("document_id")),
            "document_id": draft.get("document_id") or "",
            "work_session_id": draft.get("work_session_id") or "",
            "trust_level": draft.get("trust_level") or "legacy_untrusted",
            "schema_version": draft.get("schema_version") or "v1",
            "updated_at": draft["updated_at"]
        }
    }

@app.delete("/api/session/draft")
def api_delete_draft(current_user: dict = Depends(get_current_user)):
    clear_draft_session(current_user["id"], account_epoch=str(current_user.get("account_epoch") or ""))
    return {"success": True}

@app.post("/api/session/save")
def api_save_session(req: NamedSaveRequest, current_user: dict = Depends(get_current_user)):
    doc_id, ws_id, trust_level = _resolve_session_document_binding(
        current_user, req.document_id, req.work_session_id
    )
    session_id = save_named_session(
        current_user["id"],
        req.session_name,
        req.original_filename,
        req.professional,
        _checked_session_decisions(req.items_json, current_user, req.original_filename, doc_id),
        account_epoch=str(current_user.get("account_epoch") or ""),
        document_id=doc_id,
        work_session_id=ws_id,
        trust_level=trust_level,
    )
    log_audit_event(
        username=current_user["username"],
        name=current_user.get("name"),
        role=current_user.get("role"),
        action="保存工作进度",
        details=f"保存命名进度: {req.session_name} ({req.original_filename})"
    )
    return {"success": True, "session_id": session_id, "document_id": doc_id, "work_session_id": ws_id, "trust_level": trust_level}

@app.get("/api/session/list")
def api_list_sessions(current_user: dict = Depends(get_current_user)):
    sessions = list_saved_sessions(current_user["id"], account_epoch=str(current_user.get("account_epoch") or ""))
    return {"sessions": sessions}

@app.get("/api/session/{session_id}")
def api_get_session(session_id: str, current_user: dict = Depends(get_current_user)):
    session = get_session_by_id(current_user["id"], session_id, account_epoch=str(current_user.get("account_epoch") or ""))
    if not session:
        raise HTTPException(status_code=404, detail="未找到该工作进度记录。")
    return {
        "session": {
            "id": session["id"],
            "session_name": session["session_name"],
            "original_filename": session["original_filename"],
            "professional": session["professional"],
            "items_json": _checked_session_decisions(session["items_json"], current_user, session["original_filename"],
                                                     session.get("document_id")),
            "document_id": session.get("document_id") or "",
            "work_session_id": session.get("work_session_id") or "",
            "trust_level": session.get("trust_level") or "legacy_untrusted",
            "schema_version": session.get("schema_version") or "v1",
            "updated_at": session["updated_at"]
        }
    }

@app.put("/api/session/{session_id}")
def api_update_session(session_id: str, req: UpdateSessionRequest, current_user: dict = Depends(get_current_user)):
    ep = str(current_user.get("account_epoch") or "")
    session = get_session_by_id(current_user["id"], session_id, account_epoch=ep)
    if not session:
        raise HTTPException(status_code=404, detail="未找到该工作进度记录。")
    doc_id = None
    ws_id = None
    trust_level = None
    if req.document_id is not None:
        doc_id, ws_id, trust_level = _resolve_session_document_binding(
            current_user, req.document_id, req.work_session_id
        )
    update_named_session(
        current_user["id"],
        session_id,
        _checked_session_decisions(req.items_json, current_user, session["original_filename"],
                                   doc_id if doc_id is not None else session.get("document_id")),
        account_epoch=ep,
        document_id=doc_id,
        work_session_id=ws_id,
        trust_level=trust_level,
    )
    return {"success": True}

@app.delete("/api/session/{session_id}")
def api_delete_session(session_id: str, current_user: dict = Depends(get_current_user)):
    ep = str(current_user.get("account_epoch") or "")
    session = get_session_by_id(current_user["id"], session_id, account_epoch=ep)
    if not session:
        raise HTTPException(status_code=404, detail="未找到该工作进度记录。")
    delete_session_by_id(current_user["id"], session_id, account_epoch=ep)
    return {"success": True}


class DraftAnnotateRequest(BaseModel):
    items: List[Dict[str, Any]]
    scope_filename: Optional[str] = ""
    document_id: Optional[str] = ""
    work_session_id: Optional[str] = ""


def _checked_session_decisions(items_json, current_user, filename, document_id):
    try:
        items = json.loads(items_json)
    except (ValueError, TypeError):
        return items_json
    if not isinstance(items, list):
        return items_json
    groups = [
        item for item in items if isinstance(item, dict) and (
            document_id or any(item.get(field) for field in IDENTITY) or is_bom_name(item.get("original_name"))
        )
    ]
    if not groups:
        return items_json
    _annotate_restored_items(groups, current_user, filename, document_id)
    return json.dumps(items, ensure_ascii=False)


def _restore_source_decision(item, user, scope, document_id):
    try:
        with closing(open_flywheel_tx_connection()) as materials:
            ctx = _binding_source(materials, user, item, scope, document_id)
            if not ctx:
                return False
            with closing(get_auth_connection()) as auth:
                record = source_decision(auth, ctx)
            item["decision_version"] = source_version(record)
            try:
                live = _gate_export_row(item, user, scope, document_id=document_id, materials=materials)
            except HTTPException as exc:
                reason = exc.detail.get("row", {}).get("reason") or "源行决定无法核验，请重新确认"
                status = STATUS_REJECTED if record and record["decision"] == STATUS_REJECTED else STATUS_RECOMMENDED
                source = record["source"] if record else SOURCE_RESTORE
                item.update(_annotate_decision(item, [], status, source, reason))
            else:
                status = STATUS_MANUAL_CONFIRMED if record else STATUS_AUTO_ACCEPTED
                source = record["source"] if record else SOURCE_RULE
                item.update(_annotate_decision(item, live, status, source, "已恢复服务端确认"))
                if record:
                    item.update(row_fingerprint=record["row_fingerprint"],
                                binding_fingerprint=record["binding_fingerprint"])
                item.update(bindings=live, matched_code=",".join(b["code"] for b in live))
            return True
    except HTTPException:
        item.update(dict.fromkeys(IDENTITY, ""))
        item.update(_annotate_decision(item, [], STATUS_RECOMMENDED, SOURCE_RESTORE, "源行身份无法核验，请重新确认"))
        return True


@catalog_read
def _annotate_restored_items(items: List[Dict[str, Any]], current_user: dict, scope: str, document_id=None):
    """V015-M2：草稿/保存进度恢复时的服务端权威状态注释。

    可导出状态来自服务端人工确认、约束规则或新鲜校准复核。
    旧草稿里的绑定、编码和历史匹配提示本身不授予导出权限。
    """
    annotated = []
    downgraded = 0
    policy = acceptance_policy()
    for item in items:
        item.pop("acceptance_calibration", None)
        if _restore_source_decision(item, current_user, scope, document_id):
            downgraded += item["decision_status"] not in EXPORTABLE_STATUSES
            annotated.append(item)
            continue
        professional = item.get("professional") or "暖通"
        scope_key = make_scope_key(scope or "", professional)
        row = {
            "original_name": item.get("original_name", ""),
            "original_spec": item.get("original_spec", ""),
            "original_qty": item.get("original_qty"),
            "original_unit": item.get("original_unit", ""),
            "original_loss": item.get("original_loss", 0),
            "original_category": item.get("original_category", ""),
            "original_brand": item.get("original_brand", ""),
            "original_code": item.get("original_code", ""),
            "professional": professional,
        }
        bom = _current_bom(item)
        if bom is not None:
            _restore_bom_item(item, row, bom, current_user["id"], scope_key)
            if item["decision_status"] not in EXPORTABLE_STATUSES:
                downgraded += 1
            annotated.append(item)
            continue
        binds = normalize_bindings(item.get("bindings") or [], item.get("original_qty"))
        binds = [b for b in binds if (b.get("code") or "").strip()]
        status = STATUS_RECOMMENDED
        source = SOURCE_RESTORE
        reason = "旧草稿恢复：待人工复核，未确认不可导出"

        if binds or policy is not None or item.get("decision_source") in (SOURCE_MANUAL_EDIT, SOURCE_MANUAL_REJECT):
            row_decision = get_row_decision(
                user_id=current_user["id"], scope_key=scope_key, row=row,
            )
            if row_decision and row_decision["decision"] == STATUS_REJECTED:
                status = STATUS_REJECTED
                source = SOURCE_MANUAL_REJECT
                reason = "已恢复服务端人工拒绝，须重新选材并确认"
                downgraded += 1
            elif row_decision and row_decision["source"] == SOURCE_MANUAL_EDIT:
                source = SOURCE_MANUAL_EDIT
                reason = row_decision["reason"]
            else:
                record = get_decision(
                    user_id=current_user["id"], scope_key=scope_key,
                    row=row, bindings=binds,
                )
                validation = (
                    validate_bindings_against_row(
                        row,
                        binds,
                        saved_bindings=extract_saved_bindings(record),
                        require_saved_unit_evidence=True,
                    )
                    if record else None
                )
                if record and validation["ok"]:
                    binds = _with_live_binding_fields(binds, validation["live_materials"])
                    item["bindings"] = binds
                    status = STATUS_MANUAL_CONFIRMED
                    source = SOURCE_MANUAL
                    reason = "已恢复服务端人工确认"
                else:
                    if record:
                        if validation["invalid_codes"]:
                            reason = "确认后物料编码已失效，草稿已降为待复核"
                        else:
                            first_conflict = (
                                validation["conflicts"][0]["reason"]
                                if validation.get("conflicts") else ""
                            )
                            reason = (
                                f"绑定目标与清单行存在硬冲突({first_conflict})，草稿已降为待复核"
                                if first_conflict
                                else "绑定目标与清单行存在硬冲突，草稿已降为待复核"
                            )
                    # 尝试带约束自动接受规则（仅约束逐项匹配 + 编码有效 + 无硬冲突）
                    rule = _select_matching_binding_rule(item, load_auto_binding_rules())
                    if rule is not None and binds:
                        raw = _raw_item_from_export(row)
                        rule_binds = _rule_expected_bindings(raw, rule)
                        dec = _evaluate_rule_decision(raw, rule, rule_binds)
                        if (dec["decision_status"] == STATUS_AUTO_ACCEPTED
                                and binding_fingerprint(binds) == binding_fingerprint(rule_binds)):
                            status = STATUS_AUTO_ACCEPTED
                            source = SOURCE_RULE
                            reason = dec["decision_reason"]
                            binds = _with_live_binding_fields(
                                rule_binds, fetch_live_material_map([b["code"] for b in rule_binds]),
                            )
                            item["bindings"] = binds
                    if (policy is not None and binds and item.get("professional") and not row_decision
                            and status == STATUS_RECOMMENDED and rule is None):
                        calibrated, live_bindings = _revalidate_matcher_binding(row, binds, policy)
                        if live_bindings:
                            item.update(calibrated, bindings=live_bindings)
                            status, source, reason = (calibrated[k] for k in (
                                "decision_status", "decision_source", "decision_reason",
                            ))
                if (policy is not None and not binds and not row_decision and item.get("professional")
                        and status == STATUS_RECOMMENDED):
                    review = _calibrate_current_row(row, policy)[0]
                    if review["acceptance_calibration"]["outcome"] in ("needs_parameters", "rejected"):
                        item.update(review)
                        status, source, reason = (review[k] for k in (
                            "decision_status", "decision_source", "decision_reason",
                        ))
                if status == STATUS_RECOMMENDED:
                    downgraded += 1
                    if (not record and
                            (item.get("is_exact") or item.get("decision_status") in EXPORTABLE_STATUSES)):
                        reason = "旧草稿绑定/确认状态已失效或无法核验，已降为待复核"
        else:
            if (item.get("matched_code") or "").strip():
                downgraded += 1
                reason = "旧草稿仅有编码无确认记录，已降为待复核"

        item["decision_status"] = status
        item["decision_source"] = source
        item["decision_reason"] = reason
        item["decision_codes"] = sorted({b["code"] for b in binds})
        item["row_fingerprint"] = row_content_fingerprint(row)
        item["binding_fingerprint"] = binding_fingerprint(binds)
        if status in EXPORTABLE_STATUSES:
            item["matched_code"] = ",".join(b["code"] for b in item["bindings"])
        # is_exact 保留为算法提示，不再代表可导出
        annotated.append(item)
    return annotated, downgraded


@app.post("/api/session/annotate", dependencies=[Depends(require_employee)])
def api_annotate_session(
    req: DraftAnnotateRequest,
    current_user: dict = Depends(get_current_user),
):
    """草稿恢复注释：返回带服务端权威决策状态与可核验文档/表/源行身份的 items 与降级数量。"""
    items, downgraded = _annotate_restored_items(req.items, current_user, req.scope_filename or "", req.document_id)
    try:
        items, legacy_untrusted_count = annotate_items_identity(current_user, items)
    except FeedbackContractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return {
        "code": 0,
        "actor_user_id": current_user["id"],
        "actor_account_epoch": str(current_user.get("account_epoch") or ""),
        "items": items,
        "initial_match_summary": summarize_initial_matches(items),
        "downgraded": downgraded,
        "legacy_untrusted_count": legacy_untrusted_count,
    }

@app.get("/api/db/portrait", dependencies=[Depends(require_employee)])
@catalog_read
def api_get_db_portrait():
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
@http_job(catalog_jobs, timeout=90)
def api_system_reset(current_user: dict = Depends(require_admin)):
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

        # 4. Clean flywheel runtime data (preserving quarantined legacy records)
        from app.flywheel_db import init_flywheel_tables
        from app.rule_governance import reset_rule_publish_block
        init_flywheel_tables()
        with get_db_cursor(commit=True) as cur:
            cur.execute("DELETE FROM flywheel_records WHERE user_id != 'legacy_anonymous' AND COALESCE(trust_level, 'trusted') != 'legacy_untrusted'")
            cur.execute("DELETE FROM flywheel_feedback_samples WHERE COALESCE(trust_level, 'trusted') != 'legacy_untrusted'")
            cur.execute("DELETE FROM flywheel_recommendation_snapshots")
            cur.execute("DELETE FROM flywheel_source_rows")
            cur.execute("DELETE FROM flywheel_sheets")
            cur.execute("DELETE FROM flywheel_documents")
            cur.execute("DELETE FROM flywheel_rule_suggestions")
            cur.execute("DELETE FROM flywheel_config_snapshots")
            cur.execute("DELETE FROM flywheel_pattern_aggregates")
            cur.execute("DELETE FROM rule_eval_runs")
        reset_rule_publish_block()
                
        security_logger.info(f"System runtime data reset executed by admin '{current_user.get('username')}'.")
        return {"success": True, "message": "系统运行数据（会话草稿、上传临时文件、反馈日志及飞轮流水）已全面清理恢复干净状态！"}
    except Exception as e:
        logger.error(f"Error executing system reset: {e}")
        raise HTTPException(status_code=500, detail=f"清理系统数据失败: {str(e)}")

@app.get("/api/db/materials", dependencies=[Depends(require_employee)])
@catalog_read
def api_get_db_materials(
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

UPLOAD_DIR = os.getenv(
    "UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploads")
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Configuration and feedback files for auto-binding
# Test isolation: BINDING_FEEDBACK_FILE / OUTPUT_DIR redirects keep business files untouched
BINDING_FEEDBACK_FILE = os.getenv(
    "BINDING_FEEDBACK_FILE",
    os.path.join(os.path.dirname(__file__), "binding_feedback.json"),
)
_DEFAULT_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
AUTO_BINDING_RULES_FILE = os.path.join(
    os.getenv("CONFIG_DIR", _DEFAULT_CONFIG_DIR),
    "auto_binding_rules.json",
)
_INITIAL_AUTO_BINDING_RULES_FILE = AUTO_BINDING_RULES_FILE


def _get_config_dir() -> str:
    return os.getenv("CONFIG_DIR", _DEFAULT_CONFIG_DIR)


def _get_auto_binding_rules_path() -> str:
    if AUTO_BINDING_RULES_FILE != _INITIAL_AUTO_BINDING_RULES_FILE:
        return AUTO_BINDING_RULES_FILE
    cfg = os.getenv("CONFIG_DIR")
    if cfg:
        return os.path.join(cfg, "auto_binding_rules.json")
    return AUTO_BINDING_RULES_FILE


def load_auto_binding_rules() -> List[Dict[str, Any]]:
    path = _get_auto_binding_rules_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Error loading auto binding rules: {e}")
        return []


def _binding_rule_identity_tuple(rule: Dict[str, Any]) -> Tuple[str, str, str]:
    from app.rule_context import canonicalize_professional

    orig_name = str(rule.get("original_name") or "").strip()
    orig_spec = str(rule.get("original_spec") or "").strip()
    prof_raw = str(rule.get("professional") or "").strip()
    canon_prof = canonicalize_professional(prof_raw) if prof_raw else ""
    return (orig_name, canon_prof, orig_spec)


def _legacy_binding_rule_key(orig_name: str, canon_prof: str, orig_spec: str) -> str:
    return f"{canon_prof}::{orig_name.strip()}::{orig_spec.strip()}"


def _build_legacy_binding_spec_and_key(
    original_name: str,
    bindings_list: Any,
    *,
    original_spec: str = "",
    professional: str = "",
    require_concrete_discipline: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    from app.rule_governance import normalize_rule_spec

    orig_name = str(original_name or "").strip()
    orig_spec = str(original_spec or "").strip()
    spec_preview = normalize_rule_spec(
        rule_source="legacy_binding",
        rule_key=f"pending::{orig_name}",
        rule_type="BINDING_RULE",
        revision=1,
        discipline=professional,
        source_pattern=orig_name,
        target_value=bindings_list,
        original_spec=orig_spec,
        require_concrete_discipline=require_concrete_discipline,
    )
    rkey = _legacy_binding_rule_key(orig_name, spec_preview["discipline"], orig_spec)
    norm_spec = dict(spec_preview)
    norm_spec["rule_key"] = rkey
    return rkey, norm_spec


def start_binding_rule_evaluation(
    req_original_name: str,
    bindings_list: List[Dict[str, Any]],
    actor_user: Dict[str, Any],
    *,
    original_spec: str = "",
    professional: str = "",
) -> Dict[str, Any]:
    """Start sandbox evaluation for a legacy auto-binding rule before confirm/save."""
    from app.flywheel_sandbox import start_governed_evaluation
    from app.rule_governance import compute_rule_digest

    verified = resolve_verified_user(actor_user)
    if not verified:
        raise PermissionError("UNAUTHORIZED: 无效的操作账号。")
    if verified.get("must_change_password"):
        raise PermissionError("MUST_CHANGE_PASSWORD: 请先修改初始或重置密码。")
    if verified.get("role") != "admin":
        raise PermissionError("FORBIDDEN: 仅管理员（admin）有权评测全局自动绑定规则。")

    rkey, norm_spec = _build_legacy_binding_spec_and_key(
        req_original_name,
        bindings_list,
        original_spec=original_spec,
        professional=professional,
        require_concrete_discipline=True,
    )
    task_id = start_governed_evaluation(
        rule_source="legacy_binding",
        rule_key=rkey,
        rule_type="BINDING_RULE",
        normalized_spec=norm_spec,
        rule_revision=1,
        owner_user=verified,
    )
    return {
        "code": 0,
        "task_id": task_id,
        "eval_id": task_id,
        "rule_key": rkey,
        "payload_digest": compute_rule_digest(norm_spec),
        "msg": "绑定规则沙箱评测已启动",
    }


def _persist_binding_rules_with_reload(
    target_path: str,
    before_rules: List[Dict[str, Any]],
    after_rules: List[Dict[str, Any]],
    *,
    audit_user: Optional[Dict[str, Any]] = None,
    audit_action: str = "",
    audit_details: str = "",
) -> None:
    from app.rule_governance import EvalGateError, block_rule_publish_recovery

    atomic_write_json_config(target_path, after_rules)
    if audit_user and audit_action:
        try:
            log_audit_event(
                username=audit_user["username"],
                name=audit_user.get("name"),
                role=audit_user.get("role"),
                action=audit_action,
                details=audit_details,
            )
        except Exception as db_exc:
            logger.error(f"DB/audit write failed during binding rules update, compensating file: {db_exc}")
            try:
                atomic_write_json_config(target_path, before_rules)
            except Exception as comp_exc:
                logger.critical(f"Compensating rollback failed for {target_path}: {comp_exc}")
                block_rule_publish_recovery(
                    f"绑定规则落盘后审计事务失败且文件回滚失败: {comp_exc}",
                    details={"target_path": target_path, "db_error": str(db_exc)},
                )
                raise EvalGateError(
                    500,
                    "publish_recovery_blocked",
                    f"绑定规则保存事务失败且文件补偿恢复失败，已锁定后续规则发布：{comp_exc}",
                ) from comp_exc
            raise EvalGateError(
                500,
                "publish_db_failed_rolled_back",
                f"绑定规则审计记录失败，已自动回滚配置文件：{db_exc}",
            ) from db_exc

    try:
        trigger_all_config_reloads(raise_on_error=True)
    except Exception as reload_exc:
        logger.error(f"Config reload failed after binding rules write, compensating: {reload_exc}")
        try:
            atomic_write_json_config(target_path, before_rules)
            trigger_all_config_reloads(raise_on_error=True)
        except Exception as comp_exc:
            logger.critical(f"Binding rules compensation/reload failed: {comp_exc}")
            block_rule_publish_recovery(
                f"绑定规则热重载失败且补偿恢复未完成一致状态: {comp_exc}",
                details={"target_path": target_path, "reload_error": str(reload_exc)},
            )
            raise EvalGateError(
                500,
                "publish_recovery_blocked",
                f"热重载失败且补偿恢复异常，已锁定后续规则发布：{comp_exc}",
            ) from comp_exc
        raise EvalGateError(
            500,
            "publish_reload_failed_rolled_back",
            f"配置热重载失败，已回滚绑定规则配置：{reload_exc}",
        ) from reload_exc


def save_auto_binding_rules(
    rules: List[Dict[str, Any]],
    actor_user: Optional[Dict[str, Any]] = None,
    *,
    eval_id: Optional[str] = None,
    neutral_reason: Optional[str] = None,
):
    from app.rule_governance import (
        EvalGateError,
        _PUBLISH_GATE_LOCK,
        compute_rule_digest,
        verify_eval_credential_for_publish,
    )

    verified = resolve_verified_user(actor_user)
    if not verified or verified.get("must_change_password") or verified.get("role") != "admin":
        raise PermissionError("仅已认证的管理员（admin）有权修改全局自动绑定规则。")

    target_path = _get_auto_binding_rules_path()
    with _PUBLISH_GATE_LOCK:
        existing_rules = load_auto_binding_rules()
        existing_digests = {}
        for er in existing_rules:
            ident = _binding_rule_identity_tuple(er)
            try:
                _, es = _build_legacy_binding_spec_and_key(
                    ident[0],
                    er.get("bound_materials") or [],
                    original_spec=ident[2],
                    professional=ident[1],
                    require_concrete_discipline=False,
                )
                if er.get("bom") == es.get("bom"):
                    existing_digests[ident] = compute_rule_digest(es)
            except EvalGateError:
                existing_digests[ident] = json.dumps(er, sort_keys=True, ensure_ascii=False)

        new_or_changed = []
        normalized_after: List[Dict[str, Any]] = []
        for r in rules:
            ident = _binding_rule_identity_tuple(r)
            _, ns = _build_legacy_binding_spec_and_key(
                ident[0],
                r.get("bound_materials") or [],
                original_spec=ident[2],
                professional=ident[1],
                require_concrete_discipline=False,
            )
            d = compute_rule_digest(ns)
            if existing_digests.get(ident) != d:
                rkey, strict_spec = _build_legacy_binding_spec_and_key(
                    ident[0],
                    r.get("bound_materials") or [],
                    original_spec=ident[2],
                    professional=ident[1],
                    require_concrete_discipline=True,
                )
                new_or_changed.append((rkey, strict_spec, r))
                normalized_after.append({
                    "original_name": strict_spec["original_name"],
                    "original_spec": strict_spec["original_spec"],
                    "professional": strict_spec["discipline"],
                    "bound_materials": strict_spec["bound_materials"],
                    "eval_id": str(eval_id or "").strip(),
                    "rule_digest": compute_rule_digest(strict_spec),
                    **({"bom": strict_spec["bom"]} if "bom" in strict_spec else {}),
                })
            else:
                normalized_after.append({**r, "bom": ns["bom"]} if "bom" in ns else r)

        for rkey, strict_spec, _ in new_or_changed:
            verify_eval_credential_for_publish(
                eval_id=eval_id,
                rule_source="legacy_binding",
                rule_key=rkey,
                rule_revision=1,
                normalized_spec=strict_spec,
                neutral_reason=neutral_reason,
            )

        _persist_binding_rules_with_reload(target_path, existing_rules, normalized_after)


def confirm_binding_rule_service(
    req_original_name: str,
    bindings_list: List[Dict[str, Any]],
    actor_user: Dict[str, Any],
    *,
    original_spec: str = "",
    professional: str = "",
    eval_id: Optional[str] = None,
    neutral_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Service-layer global auto-binding rule confirmation enforcing admin RBAC & M2 eval gate."""
    from app.rule_governance import _PUBLISH_GATE_LOCK, compute_rule_digest, verify_eval_credential_for_publish

    verified = resolve_verified_user(actor_user)
    if not verified:
        raise PermissionError("UNAUTHORIZED: 无效的操作账号。")
    if verified.get("must_change_password"):
        raise PermissionError("MUST_CHANGE_PASSWORD: 请先修改初始或重置密码。")
    if verified.get("role") != "admin":
        raise PermissionError("FORBIDDEN: 仅管理员（admin）有权启用全局自动绑定规则。")

    rkey, norm_spec = _build_legacy_binding_spec_and_key(
        req_original_name,
        bindings_list,
        original_spec=original_spec,
        professional=professional,
        require_concrete_discipline=True,
    )
    target_path = _get_auto_binding_rules_path()
    with _PUBLISH_GATE_LOCK:
        ev = verify_eval_credential_for_publish(
            eval_id=eval_id,
            rule_source="legacy_binding",
            rule_key=rkey,
            rule_revision=1,
            normalized_spec=norm_spec,
            neutral_reason=neutral_reason,
        )
        rules = load_auto_binding_rules()
        before_rules = list(rules)
        target_ident = (norm_spec["original_name"], norm_spec["discipline"], norm_spec["original_spec"])
        after_rules = [r for r in rules if _binding_rule_identity_tuple(r) != target_ident]
        new_rule = {
            "original_name": norm_spec["original_name"],
            "original_spec": norm_spec["original_spec"],
            "professional": norm_spec["discipline"],
            "bound_materials": norm_spec["bound_materials"],
            "eval_id": ev["eval_id"],
            "rule_digest": ev["payload_digest"],
            "benefit_class": ev["benefit_class"],
            "governance_provenance": "verified",
            **({"bom": norm_spec["bom"]} if "bom" in norm_spec else {}),
        }
        if ev["benefit_class"] == "neutral" and neutral_reason:
            new_rule["neutral_reason"] = str(neutral_reason).strip()
        after_rules.append(new_rule)
        audit_detail = (
            f"启用物料绑定规则: {norm_spec['original_name']} "
            f"[{norm_spec['discipline']}] (关联 {len(norm_spec['bound_materials'])} 项物料, "
            f"eval={ev['eval_id']}, benefit={ev['benefit_class']})"
        )
        _persist_binding_rules_with_reload(
            target_path,
            before_rules,
            after_rules,
            audit_user=verified,
            audit_action="确认绑定规则",
            audit_details=audit_detail,
        )

    return {
        "code": 0,
        "msg": "规则已通过沙箱门禁并成功启用保存",
        "eval_id": ev["eval_id"],
        "benefit_class": ev["benefit_class"],
        "rule_digest": ev["payload_digest"],
        "discipline": norm_spec["discipline"],
    }


def delete_binding_rule_service(
    req_original_name: str,
    actor_user: Dict[str, Any],
    *,
    original_spec: Optional[str] = None,
    professional: Optional[str] = None,
) -> Dict[str, Any]:
    """Service-layer global auto-binding rule deletion restricted to verified admin."""
    from app.rule_context import canonicalize_professional
    from app.rule_governance import _PUBLISH_GATE_LOCK

    verified = resolve_verified_user(actor_user)
    if not verified:
        raise PermissionError("UNAUTHORIZED: 无效的操作账号。")
    if verified.get("must_change_password"):
        raise PermissionError("MUST_CHANGE_PASSWORD: 请先修改初始或重置密码。")
    if verified.get("role") != "admin":
        raise PermissionError("FORBIDDEN: 仅管理员（admin）有权删除全局自动绑定规则。")

    target_name = req_original_name.strip()
    target_spec = original_spec.strip() if original_spec is not None else None
    target_prof = canonicalize_professional(professional.strip()) if professional else None

    target_path = _get_auto_binding_rules_path()
    with _PUBLISH_GATE_LOCK:
        before_rules = load_auto_binding_rules()
        after_rules = []
        for r in before_rules:
            ident = _binding_rule_identity_tuple(r)
            if ident[0] != target_name:
                after_rules.append(r)
                continue
            if target_spec is not None and ident[2] != target_spec:
                after_rules.append(r)
                continue
            if target_prof is not None and ident[1] != target_prof:
                after_rules.append(r)
                continue
        _persist_binding_rules_with_reload(
            target_path,
            before_rules,
            after_rules,
            audit_user=verified,
            audit_action="删除绑定规则",
            audit_details=f"删除物料绑定规则: {target_name}",
        )
    return {"code": 0, "msg": "规则已删除"}


# V015-M2：绑定决策状态/来源契约辅助。is_exact 仅是 matcher 算法提示，
# 不再代表“已确认/可导出”；自动接受必须有规格/专业约束、逐项匹配且当前编码有效。
def _rule_is_constrained(rule: Dict[str, Any]) -> bool:
    """带约束规则：显式携带非空规格或专业约束，且绑定物料带规格。

    仅 original_name 相等的旧规则（名称-only）或标记为 legacy_unscoped 的规则一律不满足，只能作为推荐。
    """
    rule_spec = str(rule.get("original_spec", "") or "").strip()
    rule_prof = str(rule.get("professional", "") or "").strip()
    if rule_prof in ("legacy_unscoped", "legacy_unknown", "未知"):
        return False
    if not rule_spec and not rule_prof:
        return False
    bound = rule.get("bound_materials") or []
    if not bound:
        return False
    return all(str(b.get("specification", "") or "").strip() for b in bound)


def _row_matches_rule_constraints(item: Dict[str, Any], rule: Dict[str, Any]) -> bool:
    """逐项校验当前行是否满足规则的规格/专业约束（名称已在外层相等匹配）。"""
    from app.rule_context import DYNAMIC_UNIVERSAL_TOKENS, canonicalize_professional

    rule_spec = str(rule.get("original_spec", "") or "").strip()
    rule_prof = str(rule.get("professional", "") or "").strip()
    if len(rule.get("bound_materials") or []) > 1 and not rule_spec:
        return False
    if rule_prof:
        if rule_prof in ("legacy_unscoped", "legacy_unknown", "未知"):
            return False
        canon_rule_prof = canonicalize_professional(rule_prof)
        if canon_rule_prof.lower() in DYNAMIC_UNIVERSAL_TOKENS or canon_rule_prof in ("全部", "其它"):
            return False
        row_prof = str(item.get("professional", "") or "").strip()
        canon_row_prof = canonicalize_professional(row_prof) if row_prof else ""
        if not canon_row_prof or canon_row_prof != canon_rule_prof:
            return False
    if rule_spec:
        # 规格约束以关键 token 逐项包含匹配，DN100 与 DN15 不会互相满足
        row_spec = str(item.get("specification") or item.get("original_spec") or "").strip().lower()
        tokens = [t for t in rule_spec.lower().replace("×", "x").split() if t]
        if not tokens and rule_spec:
            tokens = [rule_spec.lower()]
        if len(rule.get("bound_materials") or []) > 1:
            row_spec = row_spec.replace("×", "x")
            # A constrained legacy multi-rule cannot use DN15 as a substring of DN150.
            if any(not re.search(r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z0-9.])", row_spec)
                   for tok in tokens):
                return False
        elif any(tok not in row_spec for tok in tokens):
            return False
    return True


def _select_matching_binding_rule(
    item: Dict[str, Any],
    rules: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Select binding rule for a row enforcing canonical discipline isolation and spec specificity."""
    from app.rule_context import canonicalize_professional

    item_name = str(item.get("original_name") or item.get("name") or "").strip()
    if not item_name or not rules or is_bom_name(item_name):
        return None
    row_prof = str(item.get("professional") or "").strip()
    canon_row_prof = canonicalize_professional(row_prof) if row_prof else ""

    same_name_rules = [r for r in rules if "bom" not in r and str(r.get("original_name") or "").strip() == item_name]
    if not same_name_rules:
        return None

    prof_compatible: List[Dict[str, Any]] = []
    for r in same_name_rules:
        r_prof = str(r.get("professional") or "").strip()
        if r_prof in ("legacy_unscoped", "legacy_unknown", "未知"):
            continue
        if r_prof:
            canon_r_prof = canonicalize_professional(r_prof)
            if canon_r_prof and canon_row_prof == canon_r_prof:
                prof_compatible.append(r)
        else:
            # Unscoped legacy on-disk fixture rule (e.g., name-only or spec-only legacy fixture)
            prof_compatible.append(r)

    if not prof_compatible:
        return None

    matching_constrained = [
        r for r in prof_compatible
        if _row_matches_rule_constraints(item, r)
    ]
    if matching_constrained:
        def _spec_score(r: Dict[str, Any]) -> Tuple[int, int]:
            has_prof = 1 if str(r.get("professional") or "").strip() else 0
            has_spec = 1 if str(r.get("original_spec") or "").strip() else 0
            return (has_prof, has_spec)

        return sorted(matching_constrained, key=_spec_score, reverse=True)[0]

    # Legacy unconstrained name-only rule falls back to 'recommended' only;
    # spec-constrained rules whose original_spec does not match this row never hijack it.
    name_only_legacy = [
        r for r in prof_compatible
        if not str(r.get("original_spec") or "").strip() and not str(r.get("professional") or "").strip()
    ]
    return name_only_legacy[0] if name_only_legacy else None


def _annotate_decision(
    item: Dict[str, Any],
    bindings: List[Dict[str, Any]],
    status: str,
    source: str,
    reason: str = "",
) -> Dict[str, Any]:
    """生成统一决策字段，供前端/保存恢复/重算/导出共用。"""
    return {
        "decision_status": status,
        "decision_source": source,
        "decision_reason": reason or "",
        "decision_codes": [str(b.get("code", "")).strip() for b in (bindings or []) if str(b.get("code", "")).strip()],
        "row_fingerprint": row_content_fingerprint(item),
        "binding_fingerprint": binding_fingerprint(bindings or []),
    }


def _build_row_item_from_raw(item: Dict[str, Any]) -> Dict[str, Any]:
    """把 batch_match_rows 的原始行映射为决策/校验使用的标准行结构。"""
    return {
        "original_name": item.get("name", ""),
        "original_spec": item.get("specification", ""),
        "original_qty": item.get("qty"),
        "original_unit": item.get("unit", ""),
        "original_loss": item.get("loss", 1.0),
        "original_category": item.get("category", ""),
        "original_brand": item.get("brand", ""),
        "professional": item.get("professional", "暖通"),
        "original_code": item.get("code", ""),
    }


def _raw_item_from_export(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": item.get("original_name", ""),
        "specification": item.get("original_spec", ""),
        "qty": item.get("original_qty"),
        "unit": item.get("original_unit", ""),
        "loss": item.get("original_loss", 0),
        "category": item.get("original_category", ""),
        "brand": item.get("original_brand", ""),
        "professional": item.get("professional", "暖通"),
        "code": item.get("original_code", ""),
    }


def _current_bom(row, rules=None):
    if not is_bom_name(row.get("original_name")):
        return None
    return expand_bom(row, load_auto_binding_rules() if rules is None else rules)


def _apply_llm_review(engine, budget, row, query, result):
    """Attach advisory output without promoting a code into a binding."""
    if engine is not active_llm_decision():
        return
    job = current_job()
    try:
        advice = engine.review(row, query, result, budget, deadline=job.deadline if job else None)
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
        logger.warning("补参证据校验失败 (%s)", type(exc).__name__)
        advice = {"decision": "fallback", "reason": "evidence_unavailable"}
    if advice is None or engine is not active_llm_decision():
        return
    result["llm_decision"] = advice
    reasons = {
        "recommend": "辅助建议已核验，须人工选择和确认",
        "no_answer": "辅助复核未找到有依据的目录答案",
        "need_manual_params": "辅助复核需要人工补齐关键参数",
    }
    if advice["decision"] in reasons:
        result.update(bindings=[], matched_code="", is_exact=False)
        result.update(_annotate_decision(row, [], STATUS_RECOMMENDED, SOURCE_MATCHER, reasons[advice["decision"]]))


def _bom_match_fields(row, bom, *, review=None, edited=False):
    status = STATUS_AUTO_ACCEPTED if bom.bindings else STATUS_REJECTED if bom.hard_conflict else STATUS_RECOMMENDED
    source, reason, bindings = SOURCE_RULE, bom.reason, bom.bindings
    if edited and bindings:
        status, reason, bindings = STATUS_RECOMMENDED, "BOM 重算后须按原始条件和完整集合重新确认", []
    if review and (review["decision"] == STATUS_REJECTED or review["source"] == SOURCE_MANUAL_EDIT):
        status, reason, bindings = review["decision"], review["reason"], []
        source = SOURCE_MANUAL_REJECT if status == STATUS_REJECTED else SOURCE_MANUAL_EDIT
    first = bindings[0] if bindings else {}
    return {
        "bindings": bindings, "is_exact": bool(bindings), "matched_code": ",".join(b["code"] for b in bindings),
        **{f"matched_{key}": first.get(field, "") for key, field in (
            ("name", "name"), ("spec", "specification"), ("unit", "unit"), ("brand", "brand"), ("category", "category"),
        )},
        **_annotate_decision(row, bindings, status, source, reason),
    }


def _restore_bom_item(item, row, bom, user_id, scope_key):
    bindings = []
    try:
        bindings = normalize_bindings(item.get("bindings") or [], row["original_qty"])
        if any(not positive_quantity(b.get("qty")) or not positive_quantity(b.get("qty_ratio")) for b in bindings):
            bindings = []
        issue = bom.mismatch(bindings)
    except (AttributeError, TypeError, ValueError, OverflowError):
        issue = "BOM 草稿的绑定格式或数量无效"
    review = get_row_decision(user_id=user_id, scope_key=scope_key, row=row)
    if review and review["decision"] == STATUS_REJECTED:
        item.update(bindings=[], matched_code="", is_exact=False)
        item.update(_annotate_decision(row, [], STATUS_REJECTED, SOURCE_MANUAL_REJECT, review["reason"]))
        return
    if not issue and review:
        record = get_decision(user_id=user_id, scope_key=scope_key, row=row, bindings=bindings)
        if record and validate_bindings_against_row(
            row, bindings, saved_bindings=extract_saved_bindings(record),
            require_saved_unit_evidence=True, component_shares=bom.shares,
        )["ok"]:
            item.update(_bom_match_fields(row, bom))
            item.update(_annotate_decision(row, bom.bindings, STATUS_MANUAL_CONFIRMED, SOURCE_MANUAL, "已恢复 BOM 人工确认"))
            return
        issue = "BOM 确认内容或权威目标单位已变化，须重新确认"
    if issue:
        status = STATUS_REJECTED if bom.hard_conflict else STATUS_RECOMMENDED
        item.update(bindings=bindings, matched_code="", is_exact=False)
        item.update(_annotate_decision(row, bindings, status, SOURCE_RESTORE, issue))
    else:
        item.update(_bom_match_fields(row, bom))


def _calibrated_decision(row, query, result, policy, *, trusted=True):
    assessed = assess_match(row, query, result, policy, trusted=trusted)
    bindings = confirmed_binding(row, result, assessed)
    labels = {
        "accepted": "关键字段、实时物料与接受置信度联合核验通过",
        "review": "接受证据不足，待人工复核",
        "needs_parameters": "关键字段待补参：" + "、".join(assessed["missing_fields"]),
        "rejected": assessed["hard_conflict"] or "无有效答案或候选证据过弱，拒绝预绑定",
    }
    decision = _annotate_decision(row, bindings, assessed["decision_status"], SOURCE_MATCHER, labels[assessed["outcome"]])
    decision["acceptance_calibration"] = {k: v for k, v in assessed.items() if k not in ("features", "decision_status")}
    return decision, bindings


@catalog_read
def _revalidate_matcher_binding(row, incoming, policy):
    """Recompute from submitted original fields, using current server assets."""
    if len(incoming) != 1:
        return None, []
    decision, live = _calibrate_current_row(row, policy)
    request_unit = str(incoming[0].get("unit") or "").strip()
    if (not live or not request_unit or get_unit_multiplier(request_unit, live[0]["unit"]) != 1.0
            or binding_fingerprint(incoming) != binding_fingerprint(live)):
        return None, []
    return decision, live


def _calibrate_current_row(row, policy):
    from app.query_preprocessor import prepare_query

    raw = _raw_item_from_export(row)
    query = prepare_query(raw["name"], raw["specification"], raw["professional"],
                          category=raw["category"], brand=raw["brand"], unit=raw["unit"])
    result = match_item(query, calibration_groups=policy.equivalences)
    return _calibrated_decision(row, query, result, policy)


def _rule_expected_bindings(item: Dict[str, Any], rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按当前行数量与规则比例重建完整预期绑定，不从草稿/导出请求继承数量。"""
    qty = float(item.get("qty") or 0.0)
    targets = rule.get("bound_materials") or []
    if not math.isfinite(qty) or any(
        not isinstance(b, dict) or (b.get("qty_ratio") is not None
                                  and (not positive_quantity(b["qty_ratio"]) or not math.isfinite(qty * b["qty_ratio"])))
        for b in targets
    ):
        return []
    if len({b["code"].strip() for b in targets if isinstance(b.get("code"), str) and b["code"].strip()}) != len(targets):
        return []
    return normalize_bindings([
        {
            "code": (b.get("code") or "").strip(),
            "name": b.get("name", ""),
            "specification": b.get("specification", ""),
            "unit": b.get("unit", ""),
            "brand": b.get("brand", ""),
            "category": b.get("category", ""),
            "qty": qty * float(b.get("qty_ratio") if b.get("qty_ratio") is not None else 1.0),
            "qty_ratio": b.get("qty_ratio"),
        }
        for b in targets
    ], qty)


def _evaluate_rule_decision(item: Dict[str, Any], rule: Dict[str, Any], bindings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """评估规则命中行的服务端决策：名称-only 旧规则/约束不匹配/编码失效/硬冲突均降推荐。"""
    row = _build_row_item_from_raw(item)
    if not bindings or len({b["code"] for b in bindings if b["code"]}) != len(bindings):
        return _annotate_decision(row, [], STATUS_RECOMMENDED, SOURCE_RULE,
                                  "旧规则的子项编码或数量比例无效，待人工复核")
    if not _rule_is_constrained(rule):
        return _annotate_decision(row, bindings, STATUS_RECOMMENDED, SOURCE_RULE,
                                  "仅名称相等的旧规则，需人工确认")
    if not _row_matches_rule_constraints(item, rule):
        return _annotate_decision(row, bindings, STATUS_RECOMMENDED, SOURCE_RULE,
                                  "当前行规格/专业不满足规则约束，需人工确认")
    # V015-M3 R02：自动规则不得在跨非1倍率单位时静默自动接受，阻断并提示按目标单位复核
    check = validate_bindings_against_row(row, bindings, allow_unit_conversion=False)
    if check["invalid_codes"]:
        return _annotate_decision(row, bindings, STATUS_RECOMMENDED, SOURCE_RULE,
                                  f"规则绑定编码已失效: {','.join(check['invalid_codes'])}")
    if check["conflicts"]:
        reasons = ";".join(f"{c['code']}:{c['reason']}" for c in check["conflicts"])
        return _annotate_decision(row, bindings, STATUS_REJECTED, SOURCE_RULE,
                                  f"硬冲突阻断: {reasons}")
    return _annotate_decision(row, bindings, STATUS_AUTO_ACCEPTED, SOURCE_RULE,
                              "约束规则逐项匹配且编码当前有效，服务端自动接受")

_upload_tasks_lock = threading.Lock()
upload_tasks: Dict[str, Dict[str, Any]] = {}

def update_task_progress(task_id: str, **kwargs):
    """Thread-safe update for upload / import task progress."""
    if not task_id:
        return
    with _upload_tasks_lock:
        if task_id in upload_tasks:
            upload_tasks[task_id].update(kwargs)
            if kwargs.get("status") in ("completed", "failed", "cancelled", "timed_out"):
                upload_tasks[task_id].setdefault("finished_at", time.time())
                prune_terminal_tasks(upload_tasks, 50, terminal_ttl())

def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """Thread-safe internal retrieval of a task copy."""
    with _upload_tasks_lock:
        prune_terminal_tasks(upload_tasks, 50, terminal_ttl())
        task = upload_tasks.get(task_id)
        return dict(task) if task else None


def get_task_status_for_user(task_id: str, user_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Strict creator-only retrieval for HTTP upload/import tasks; rejects missing/foreign/recreated owners."""
    if not isinstance(user_info, dict) or not user_info.get("id") or not user_info.get("account_epoch"):
        return None
    req_uid = int(user_info["id"])
    req_epoch = str(user_info["account_epoch"])
    with _upload_tasks_lock:
        prune_terminal_tasks(upload_tasks, 50, terminal_ttl())
        task = upload_tasks.get(task_id)
        if not task:
            return None
        owner_uid = task.get("owner_user_id")
        owner_epoch = task.get("owner_account_epoch")
        if owner_uid is None or not owner_epoch:
            return None
        if int(owner_uid) != req_uid or str(owner_epoch) != req_epoch:
            return None
        return dict(task)


def set_task_initial(task_id: str, data: Dict[str, Any], owner_user: Optional[Dict[str, Any]] = None):
    """Thread-safe initialization of a task optionally bound to owner identity."""
    payload = dict(data)
    if isinstance(owner_user, dict) and owner_user.get("id") is not None:
        payload["owner_user_id"] = int(owner_user["id"])
        payload["owner_account_epoch"] = str(owner_user.get("account_epoch") or "")
    with _upload_tasks_lock:
        upload_tasks[task_id] = payload

def clean_expired_upload_tasks(max_tasks: int = 50, max_age_seconds: Optional[int] = None):
    with _upload_tasks_lock:
        prune_terminal_tasks(upload_tasks, max_tasks, max_age_seconds or terminal_ttl())


def _reserve_upload_job(pool, timeout, task_id):
    try:
        return pool.reserve(timeout, task_id)
    except JobRejected as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "1"}) from exc


async def _finish_background_job(job, artifacts, worker, *args, **kwargs):
    try:
        await job.wait(worker, *args, **kwargs)
    finally:
        artifacts.close()
        job.release()


_categories_cache: Optional[Tuple[int, List[str]]] = None
_categories_cache_lock = threading.Lock()

@catalog_read
def get_cached_categories() -> List[str]:
    """Get unique materials categories with in-memory caching."""
    global _categories_cache
    with _categories_cache_lock:
        revision = get_db_revision()
        if _categories_cache is None or _categories_cache[0] != revision:
            conn = get_db_connection()
            try:
                cat_rows = conn.cursor().execute("SELECT DISTINCT category FROM materials WHERE category IS NOT NULL AND category != '' ORDER BY category ASC").fetchall()
                _categories_cache = (revision, [c[0] for c in cat_rows])
            finally:
                conn.close()
        return list(_categories_cache[1])

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
    qty_ratio: Optional[float] = None

    @field_validator("qty", "qty_ratio", mode="before")
    @classmethod
    def reject_boolean_quantities(cls, value):
        if isinstance(value, bool):
            raise ValueError("工程量和比例须为数值")
        return value

class MatchedItemModel(SourceReviewFields):
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
    # V015-M1 / F19：原始品类与品牌贯通导出（report_generator 第 4/11 列）。
    # Optional + 默认空串，旧前端/旧保存进度缺失这两个键时仍可通过校验。
    original_category: Optional[str] = ""
    original_brand: Optional[str] = ""
    bindings: Optional[List[BindingModel]] = None
    # V015-M2：客户端自报的决策状态/范围仅作上下文，导出端不信任，一律服务端复核。
    decision_status: Optional[str] = ""
    decision_source: Optional[str] = ""
    scope_filename: Optional[str] = ""


class ExportRequest(BaseModel):
    items: List[MatchedItemModel]
    document_id: Optional[str] = ""
    # V015-M2：导出工作范围（原始文件名），与人工确认记录的 scope_key 对齐。
    scope_filename: Optional[str] = ""

class RematchRequest(BaseModel):
    row_id: int
    text: str
    fixed_params: Dict[str, Any]
    professional: Optional[str] = "暖通"
    # V015-M1：重算保留原行品类/品牌/单位，统一 prepare_query 契约使用。
    category: Optional[str] = ""
    brand: Optional[str] = ""
    unit: Optional[str] = ""
    document_id: Optional[str] = None
    sheet_id: Optional[str] = None
    source_row_id: Optional[str] = None

class ConfigSaveRequest(BaseModel):
    config_type: str # alias, units, stop_words, weights, tolerances, boosts
    data: Any

class FeedbackSubmitRequest(BaseModel):
    row_id: int = 0
    raw_text: str = ""
    auto_code: str = ""
    manual_code: str = ""
    is_correct: bool = False
    client_event_id: Optional[str] = None
    document_id: Optional[str] = None
    sheet_id: Optional[str] = None
    source_row_id: Optional[str] = None
    event_seq: Optional[StrictInt] = None
    recommendation_snapshot_id: Optional[str] = None
    user_id: Optional[Any] = None
    actor_user_id: Optional[Any] = None

def _extract_upload_rows_from_sheets(
    sheets: List[Tuple[str, List[Tuple[Any, ...]]]],
    task_id: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
) -> List[Dict[str, Any]]:
    """Extract and validate query rows from pre-validated workbook sheets."""
    ensure_workbook_non_empty(sheets, context_label="工程清单")
    raw_rows: List[Dict[str, Any]] = []

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

    def _cell(row_vals: Tuple[Any, ...], idx_1based: Optional[int]) -> Any:
        if idx_1based is None or idx_1based <= 0 or idx_1based > len(row_vals):
            return None
        return row_vals[idx_1based - 1]

    exclude_kws = ["说明", "汇总", "商务部", "备用"]
    if task_id:
        update_task_progress(task_id, current_step="正在读取并解析 Excel 工作表...", structuring_progress=5)

    global_row_counter = 1
    total_sheets = max(len(sheets), 1)
    found_valid_header = False

    for s_idx, (sheet_name, sheet_rows) in enumerate(sheets):
        if cancel_event is not None and cancel_event.is_set():
            raise ParseCancelledError("清单解析已取消或超时")
        if any(kw in sheet_name for kw in exclude_kws):
            continue

        professional = get_professional_by_sheet(sheet_name)

        header_row = None
        for r_idx, row_vals in enumerate(sheet_rows[:10], start=1):
            row_strs = [str(x).strip() for x in row_vals if x is not None]
            if (
                any(x in ["*编码", "项目编码", "编码", "项目名称", "*名称", "名称"] for x in row_strs)
                and any("规格" in x or "特征" in x or "型号" in x for x in row_strs)
            ):
                header_row = r_idx
                break

        if not header_row:
            continue

        found_valid_header = True
        start_r = header_row + 1
        headers = sheet_rows[header_row - 1]
        code_idx = name_idx = spec_idx = unit_idx = qty_idx = loss_idx = category_idx = brand_idx = None

        for idx, h in enumerate(headers):
            if not h:
                continue
            h_str = str(h).strip()
            if "编码" in h_str and code_idx is None:
                code_idx = idx + 1
            elif "名称" in h_str and name_idx is None:
                name_idx = idx + 1
            elif ("规格" in h_str or "特征" in h_str or "型号" in h_str) and spec_idx is None:
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

        if code_idx is None:
            code_idx = 1
        if name_idx is None:
            name_idx = 2
        if spec_idx is None:
            spec_idx = 3
        if unit_idx is None:
            unit_idx = 4
        if qty_idx is None:
            qty_idx = 5

        for r, row_vals in enumerate(sheet_rows[start_r - 1 :], start=start_r):
            val_b = _cell(row_vals, name_idx)
            val_c = _cell(row_vals, spec_idx)
            val_b_str = str(val_b if val_b is not None else "").strip()
            val_c_str = str(val_c if val_c is not None else "").strip()
            raw_unit_val = _cell(row_vals, unit_idx)
            raw_unit = str(raw_unit_val if raw_unit_val is not None else "").strip() if unit_idx else ""
            raw_code_val = _cell(row_vals, code_idx)
            original_code = str(raw_code_val if raw_code_val is not None else "").strip() if code_idx else ""

            is_valid, _ = is_valid_query_item(val_b_str, val_c_str, raw_unit, original_code, r)
            if not is_valid:
                continue

            unit_str = raw_unit or "台"
            qty_val = _cell(row_vals, qty_idx)
            try:
                qty = float(qty_val) if qty_val is not None else 0.0
            except (ValueError, TypeError):
                qty = 0.0

            loss = 1.0
            if loss_idx:
                loss_val = _cell(row_vals, loss_idx)
                try:
                    loss = float(loss_val) if loss_val is not None else 1.0
                except (ValueError, TypeError):
                    loss = 1.0

            raw_cat_val = _cell(row_vals, category_idx)
            category = str(raw_cat_val if raw_cat_val is not None else "").strip() if category_idx else ""
            raw_brand_val = _cell(row_vals, brand_idx)
            brand = str(raw_brand_val if raw_brand_val is not None else "").strip() if brand_idx else ""

            raw_rows.append({
                "row_idx": global_row_counter,
                "sheet_index": s_idx,
                "sheet_name": sheet_name,
                "source_row_num": r,
                "code": original_code,
                "name": val_b_str,
                "specification": val_c_str,
                "unit": unit_str,
                "qty": qty,
                "loss": loss,
                "category": category,
                "brand": brand,
                "professional": professional,
            })
            global_row_counter += 1

        if task_id:
            sheet_prog = min(95, int(5 + ((s_idx + 1) / total_sheets) * 90))
            update_task_progress(task_id, structuring_progress=sheet_prog)

    if not found_valid_header:
        raise ExcelBoundaryError(
            "清单文件缺少有效表头列（需包含“项目名称/名称/编码”以及“规格/特征/型号”列）",
            status_code=400,
            code="missing_query_columns",
        )
    if not raw_rows:
        raise ExcelBoundaryError(
            "清单文件中未识别出有效的待匹配物料数据行",
            status_code=400,
            code="empty_query_rows",
        )
    return raw_rows


def _precheck_upload_excel(temp_path: str, original_filename: Optional[str] = None) -> List[Dict[str, Any]]:
    """Synchronous pre-validation (magic, zip bomb, budgets, columns, rows) before background task dispatch."""
    checkpoint()
    job = current_job()
    cancel = job.cancelled if job else None
    sheets = load_validated_excel_sheets(
        temp_path,
        allowed_exts=frozenset({".xlsx"}),
        original_filename=original_filename or temp_path,
        cancel_event=cancel,
        context_label="清单上传",
    )
    return _extract_upload_rows_from_sheets(sheets, task_id=None, cancel_event=cancel)


def _check_and_hash_upload(temp_path, filename):
    rows = _precheck_upload_excel(temp_path, filename)
    import hashlib
    with open(temp_path, "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checkpoint()
    return rows, digest


def _precheck_materials_upload(temp_path, filename):
    checkpoint()
    sheets = load_validated_excel_sheets(
        temp_path,
        allowed_exts=frozenset({".xlsx", ".xls"}),
        original_filename=filename or temp_path,
        max_sheets_to_read=1,
        cancel_event=current_job().cancelled,
        context_label="物料库导入",
    )
    ensure_workbook_non_empty(sheets, context_label="物料库 Excel")


def _parse_and_prepare_upload_excel(
    task_id: str,
    temp_path: str,
    cancel_event: Optional[threading.Event] = None,
) -> List[Dict[str, Any]]:
    """CPU-bound Excel extraction running in executor (M1 unblocking + M5 safety boundary)."""
    sheets = load_validated_excel_sheets(
        temp_path,
        allowed_exts=frozenset({".xlsx"}),
        original_filename=temp_path,
        cancel_event=cancel_event,
        context_label="清单上传",
    )
    return _extract_upload_rows_from_sheets(sheets, task_id=task_id, cancel_event=cancel_event)

def async_process_upload_task(
    task_id: str,
    temp_path: str,
    prechecked_rows: Optional[List[Dict[str, Any]]] = None,
):
    try:
        checkpoint()
        if prechecked_rows is not None:
            raw_rows = prechecked_rows
            update_task_progress(task_id, structuring_progress=95)
        else:
            job = current_job()
            raw_rows = _parse_and_prepare_upload_excel(task_id, temp_path, job.cancelled if job else None)

        remove_file_quietly(temp_path)

        update_task_progress(
            task_id,
            structuring_progress=100,
            current_step="Excel 清单数据解析完成，开始智能特征比对..."
        )

        update_processing_task(task_id, filename=os.path.basename(temp_path), total_rows=len(raw_rows), status="processing", current_step="正在智能清洗与特征比对...")
        increment_stat_counter("total_processed_rows", len(raw_rows))

        parsed_rows = batch_match_rows(raw_rows, task_id)
        checkpoint()

        task_meta = get_task_status(task_id) or {}
        doc_summary: Dict[str, Any] = {}
        if task_meta.get("owner_user_id") is not None and task_meta.get("owner_account_epoch"):
            owner_actor = {
                "id": task_meta["owner_user_id"],
                "account_epoch": task_meta["owner_account_epoch"],
            }
            doc_summary = persist_upload_document_tree(
                actor_user=owner_actor,
                document_id=task_meta.get("document_id") or f"doc_{uuid.uuid4().hex}",
                upload_task_id=task_id,
                work_session_id=task_meta.get("work_session_id") or f"ws_{uuid.uuid4().hex}",
                original_filename=task_meta.get("original_filename") or os.path.basename(temp_path),
                file_sha256=task_meta.get("file_sha256") or "",
                raw_rows=raw_rows,
                parsed_rows=parsed_rows,
            )

        update_processing_task(task_id, filename=os.path.basename(temp_path), total_rows=0, status="completed")
        log_audit_event(username="system", action="智能比对完成", details=f"成功完成 {len(raw_rows)} 行清单特征清洗与智能匹配")

        update_task_progress(
            task_id,
            status="completed",
            result=parsed_rows,
            initial_match_summary=summarize_initial_matches(parsed_rows),
            document_id=doc_summary.get("document_id") or task_meta.get("document_id", ""),
            work_session_id=doc_summary.get("work_session_id") or task_meta.get("work_session_id", ""),
            algo_version=doc_summary.get("algo_version", ""),
            model_version=doc_summary.get("model_version", ""),
            config_version=doc_summary.get("config_version", ""),
            current_step="所有清单项目已智能比对匹配完成！"
        )

    except JobStopped as exc:
        update_processing_task(task_id, filename=os.path.basename(temp_path), total_rows=0, status="failed")
        update_task_progress(task_id, status="failed", error=str(exc), current_step=str(exc))
    except Exception as e:
        update_processing_task(task_id, filename=os.path.basename(temp_path), total_rows=0, status="failed")
        remove_file_quietly(temp_path)
        logger.error(f"Error during async process upload task: {e}")
        update_task_progress(
            task_id,
            status="failed",
            error=str(e),
            current_step=f"处理出错: {str(e)}"
        )

    finally:
        remove_file_quietly(temp_path)

MAX_UPLOAD_SIZE = DEFAULT_MAX_UPLOAD_BYTES  # 50 MB limit

@app.post("/api/upload")
async def upload_file(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...), current_user: dict = Depends(require_employee)):
    """
    Upload original list (.xlsx only) and start high performance batch matching background task.
    """
    temp_path: Optional[str] = None
    task_id = str(uuid.uuid4())
    job = None
    artifacts = ExitStack()
    scheduled = False

    try:
        ext = validate_extension(file.filename, frozenset({".xlsx"}), context_label="清单上传")
        safe_filename = os.path.basename((file.filename or f"upload{ext}").strip())
        if not safe_filename.lower().endswith(".xlsx"):
            safe_filename = f"{safe_filename}.xlsx"
        logger.info(f"Received query list Excel upload: {safe_filename} by user {current_user.get('username')}")
        file_id = str(uuid.uuid4())
        temp_path = os.path.join(UPLOAD_DIR, f"{file_id}_{safe_filename}")

        job = _reserve_upload_job(upload_jobs, 900, task_id)
        await job.wait(artifacts.enter_context, active_path(temp_path))
        max_bytes = MAX_UPLOAD_SIZE if MAX_UPLOAD_SIZE != DEFAULT_MAX_UPLOAD_BYTES else None
        await stream_save_upload_file(request, file, temp_path, max_bytes=max_bytes)

        prechecked_rows, file_sha256 = await job.wait(_check_and_hash_upload, temp_path, file.filename)
        job.deadline = min(job.deadline, time.monotonic() + max(120.0, len(prechecked_rows) * 1.5))
        clean_expired_upload_tasks()
        document_id = f"doc_{uuid.uuid4().hex}"
        work_session_id = f"ws_{uuid.uuid4().hex}"
        set_task_initial(task_id, {
            "status": "processing",
            "created_time": time.time(),
            "document_id": document_id,
            "work_session_id": work_session_id,
            "original_filename": safe_filename,
            "file_sha256": file_sha256,
            "structuring_progress": 0,
            "vectorization_progress": 0,
            "matching_progress": 0,
            "current_step": "正在初始化后台比对任务...",
            "result": None,
            "error": None
        }, owner_user=current_user)

        log_audit_event(
            username=current_user["username"],
            name=current_user.get("name"),
            role=current_user.get("role"),
            action="上传清单",
            details=f"上传待匹配清单: {file.filename}"
        )

        # Wait: pass temp_path and rely on async task (with prechecked_rows fast-path) so tests mocking either keep working cleanly.
        background_tasks.add_task(
            _finish_background_job, job, artifacts, async_process_upload_task, task_id, temp_path,
            prechecked_rows=prechecked_rows,
        )
        scheduled = True
        return JSONResponse(content={"task_id": task_id, "document_id": document_id, "work_session_id": work_session_id})
    except ExcelBoundaryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except JobStopped as exc:
        raise HTTPException(504, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        if task_id:
            try:
                update_task_progress(
                    task_id,
                    status="failed",
                    error=f"上传任务启动失败: {exc}",
                    current_step=f"任务启动失败: {exc}",
                )
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"清单上传初始化失败: {exc}")
    finally:
        if not scheduled:
            remove_file_quietly(temp_path)
            artifacts.close()
            if job:
                job.release()

@app.get("/api/upload/status")
def get_upload_status(task_id: str, current_user: dict = Depends(require_employee)):
    """
    Query current upload/import process progress status (strictly owner-only, anti-enumeration 404).
    """
    task = get_task_status_for_user(task_id, current_user)
    if not task:
        raise HTTPException(status_code=404, detail="指定任务不存在或无权访问")
    return JSONResponse(content=task)

ENABLE_SERIES_RERUN = os.getenv("ENABLE_SERIES_RERUN", "false").lower() == "true"

def batch_match_rows(raw_rows: List[Dict[str, Any]], task_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Helper to perform batch encoding and threadpool matching in chunks of 500."""
    import time
    
    logger.debug(f"batch_match_rows started for {len(raw_rows)} rows. task_id: {task_id}")
    total_results = []
    total_items = len(raw_rows)
    policy = acceptance_policy()
    initial_policy = load_policy(require_frozen=True) if task_id else None
    first_revision = get_db_revision() if task_id else None
    rank_options = {}
    if policy is not None:
        rank_options["calibration_groups"] = policy.equivalences
    if initial_policy is not None:
        rank_options["initial_match_groups"] = initial_policy.equivalences
    task_meta = (get_task_status(task_id) or {}) if policy is not None and task_id else {}
    llm_engine = active_llm_decision()
    if llm_engine is not None:
        llm_budget = CallBudget(2)
        llm_task = (task_meta or get_task_status(task_id) or {}) if task_id else {}

    # Load confirmed auto-binding rules
    logger.debug("Loading auto binding rules...")
    rules = load_auto_binding_rules()
    logger.debug(f"Loaded {len(rules)} rules.")

    # Step 1: Pre-process all rows
    if task_id:
        update_task_progress(task_id, current_step="正在对清单特征参数进行清洗与解析...")
    
    logger.debug("Step 1: Preprocessing query parameters...")
    # V015-M1 / F01/F13：统一 prepare_query 契约。规范名称与清洗文本由
    # extract_query_params 顶层 name/spec 提供，不再 get("name") 恒回退原文；
    # 施工噪声隔离进 noise_text，编码文本只用清洗后的规范字段。
    from app.query_preprocessor import prepare_query
    preprocessed_rows = []
    for idx, item in enumerate(raw_rows):
        checkpoint()
        raw_name = item["name"].strip()
        raw_spec = item["specification"].strip()
        prof = item.get("professional", "暖通")

        query_info = prepare_query(
            raw_name, raw_spec, prof,
            category=item.get("category", ""),
            brand=item.get("brand", ""),
            unit=item.get("unit", ""),
        )
        real_name = query_info["name"]
        clean_spec = query_info["specification"]

        preprocessed_rows.append({
            "real_name": real_name,
            "clean_spec": clean_spec,
            "query_info": query_info,
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
            checkpoint()
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
        checkpoint()
        chunk = preprocessed_rows[idx : idx + chunk_size]
        chunk_embeddings = embeddings_list[idx : idx + chunk_size]
        
        # Prepare match jobs
        match_jobs = []
        for offset, p in enumerate(chunk):
            item = p["item"]
            q_vector = chunk_embeddings[offset]
            # V015-M1：统一契约产出完整 query_info（含 unit 修复 F13、
            # 原始品类/品牌贯通），同块同源，不再二次拼装。
            query_info = dict(p["query_info"])
            match_jobs.append((query_info, q_vector, item))
            
        logger.debug(f"Matching jobs chunk {idx} to {idx + len(chunk)}...")
        # First pass sequential matching (using pre-computed vectors)
        first_pass_results = []
        for offset, (q_info, q_vector, item) in enumerate(match_jobs):
            checkpoint()
            current_idx = idx + offset + 1
            if task_id:
                update_task_progress(task_id, current_step=f"【智能匹配】 {q_info['name'][:25]} ({current_idx}/{total_items})", matching_progress=min(100, int((current_idx / total_items) * 100)))
            
            logger.debug(f"  Matching job {idx + offset}: name={q_info['name']}, spec={q_info['specification']}, category={q_info['category']}, brand={q_info['brand']}")
            t_start_match = time.time()
            res = match_item(q_info, q_vector, **rank_options)
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
            checkpoint()
            res = first_pass_results[i]
            if is_bom_name(item.get("name")):
                row_for_bom = _build_row_item_from_raw(item)
                bom = _current_bom(row_for_bom, rules)
                owner = task_meta.get("owner_user_id")
                review = get_row_decision(
                    owner, make_scope_key(task_meta.get("original_filename"), row_for_bom["professional"]), row_for_bom,
                ) if owner is not None else None
                final_chunk_results.append({
                    **row_for_bom, "original_row_idx": item["row_idx"], "parsed_spec": q_info["parsed_spec"],
                    "top_candidates": res["top_candidates"], **_bom_match_fields(row_for_bom, bom, review=review),
                })
                continue
            rule_obj = _select_matching_binding_rule(item, rules)
            rule_bindings = rule_obj.get("bound_materials") if rule_obj else None
            
            if rule_bindings:
                # Apply confirmed auto-binding rule
                bindings_list = _rule_expected_bindings(item, rule_obj)
                first_b = bindings_list[0] if bindings_list else {}
                # V015-M2：规则命中不再天然可导出。名称-only 旧规则/约束不匹配/编码
                # 失效降为推荐，硬冲突拒绝；仅约束逐项匹配且编码当前有效才自动接受。
                # is_exact 保留为历史算法提示，决策以 decision_* 字段为准。
                decision = _evaluate_rule_decision(item, rule_obj, bindings_list)
                result_row = {
                    "original_row_idx": item["row_idx"],
                    "original_name": item["name"],
                    "original_spec": item["specification"],
                    "original_qty": item["qty"],
                    "original_unit": item["unit"],
                    "original_loss": item["loss"],
                    "original_category": item["category"],
                    "original_brand": item.get("brand", ""),
                    "professional": item.get("professional", "暖通"),
                    "original_code": item.get("code", ""),
                    "parsed_spec": q_info["parsed_spec"],
                    "is_exact": bool(bindings_list),
                    "matched_code": ",".join([b["code"] for b in bindings_list]),
                    "matched_name": first_b.get("name", ""),
                    "matched_category": first_b.get("category", ""),
                    "matched_spec": first_b.get("specification", ""),
                    "matched_unit": first_b.get("unit", ""),
                    "matched_brand": first_b.get("brand", ""),
                    "top_candidates": res["top_candidates"],
                    "bindings": bindings_list
                }
                result_row.update(decision)
                final_chunk_results.append(result_row)
            else:
                if not res["is_exact"] and confirmed_series and res.get("top_candidates"):
                    if ENABLE_SERIES_RERUN:
                        # Full second-pass rematch if explicitly enabled
                        initial_options = {k: v for k, v in rank_options.items() if k == "initial_match_groups"}
                        res = match_item(q_info, q_vector, confirmed_series, **initial_options)
                        first_pass_results[i] = res
                    else:
                        # Fast in-memory series rank boost without re-running full recall/scoring (default)
                        # V015-M3：blocked 候选不享受第二遍系列加分；+5 受候选不可逆 max_score
                        # 上限收口，禁止借内存 boost 复活硬排除或突破前置封顶
                        for cand in res["top_candidates"]:
                            if cand.get("hard_blocked"):
                                continue
                            c_spec = cand.get("specification", "")
                            c_model = extract_params(c_spec).get("model", "")
                            if c_model:
                                c_prefix = c_model.split("-")[0].strip().lower()
                                if c_prefix in confirmed_series:
                                    cap = cand.get("max_score")
                                    item_cap = max(0.0, float(cap) if cap is not None else 1.0) * 100.0
                                    prior_score = cand["score"]
                                    cand["score"] = min(item_cap, prior_score + 5.0)
                                    if task_id and cand["score"] != prior_score:
                                        res["_initial_rank_stale"] = True
                                    if "score_evidence" in cand:
                                        cand["score_evidence"]["components"]["batch_series_adjustment"] = (
                                            cand["score"] - prior_score
                                        ) / 100.0
                                    if "系列优先" not in cand.get("match_reasons", []):
                                        cand.setdefault("match_reasons", []).append("系列优先")
                        res["top_candidates"].sort(key=lambda x: x["score"], reverse=True)
                    if policy is not None:
                        res.pop("_acceptance_rank", None)
                    
                top_cands = res["top_candidates"]
                first_cand = top_cands[0] if top_cands else None
                top_score = float(first_cand.get("score", 0.0) or 0.0) if first_cand else 0.0

                # V015-M2：matcher 推荐候选不是可导出绑定。0 分/无答案不预绑定；
                # top1 触发专业/品牌/单位硬冲突时拒绝且不预绑定；其余仅作为推荐，
                # 必须经显式人工确认（通过新鲜编码与硬冲突核验）后方可导出。
                row_for_decision = _build_row_item_from_raw(item)
                bindings_list = []
                decision_status = STATUS_RECOMMENDED
                decision_reason = "matcher 算法推荐，需人工确认"
                calibrated = None
                if policy is not None:
                    calibrated, bindings_list = _calibrated_decision(row_for_decision, q_info, res, policy)
                elif not first_cand or top_score <= 0.0:
                    decision_status = STATUS_RECOMMENDED
                    decision_reason = "无有效候选或匹配得分为 0，不预绑定"
                else:
                    candidate_row = {
                        "code": first_cand.get("code", ""),
                        "name": first_cand.get("name", ""),
                        "specification": first_cand.get("specification", ""),
                        "unit": first_cand.get("unit", item["unit"]),
                        "brand": first_cand.get("brand", ""),
                        "category": first_cand.get("category", ""),
                        # V015-M3：补齐 description/parsed_params，使 evaluate_hard_conflicts
                        # 与 matcher 同源复核真实品牌值、DN/外径、材质枚举与否定三态
                        "description": first_cand.get("description", ""),
                        "parsed_params": first_cand.get("parsed_params", ""),
                    }
                    conflict_reason = evaluate_hard_conflicts(row_for_decision, candidate_row)
                    if conflict_reason:
                        decision_status = STATUS_REJECTED
                        decision_reason = f"top1 {candidate_row['code']} {conflict_reason}，不预绑定"
                    else:
                        # 仅推荐候选：保留在 bindings 供前端展示与人工确认，但默认不可导出
                        bindings_list.append({
                            "code": res["matched_code"] or first_cand.get("code", ""),
                            "name": first_cand.get("name", ""),
                            "specification": first_cand.get("specification", ""),
                            "unit": first_cand.get("unit", item["unit"]),
                            "brand": first_cand.get("brand", ""),
                            "category": first_cand.get("category", ""),
                            "qty": float(item["qty"]),
                            "qty_ratio": 1.0,
                        })

                result_row = {
                    "original_row_idx": item["row_idx"],
                    "original_name": item["name"],
                    "original_spec": item["specification"],
                    "original_qty": item["qty"],
                    "original_unit": item["unit"],
                    "original_loss": item["loss"],
                    "original_category": item["category"],
                    "original_brand": item.get("brand", ""),
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
                }
                result_row.update(calibrated if calibrated is not None else _annotate_decision(
                    row_for_decision, bindings_list, decision_status, SOURCE_MATCHER, decision_reason
                ))
                if calibrated is not None:
                    result_row["matched_code"] = bindings_list[0]["code"] if bindings_list else ""
                    owner = task_meta.get("owner_user_id")
                    if bindings_list and owner is not None:
                        scope = make_scope_key(task_meta.get("original_filename"), result_row["professional"])
                        review = get_row_decision(owner, scope, result_row)
                        if review:
                            status = review["decision"]
                            source = SOURCE_MANUAL_REJECT if status == STATUS_REJECTED else SOURCE_RESTORE
                            result_row.update(bindings=[], matched_code="")
                            result_row.pop("acceptance_calibration", None)
                            result_row.update(_annotate_decision(
                                result_row, [], STATUS_REJECTED if status == STATUS_REJECTED else STATUS_RECOMMENDED,
                                source, "已有人工决策，须按现有决定复核",
                            ))
                if llm_engine is not None and rule_obj is None:
                    owner = llm_task.get("owner_user_id")
                    scope = make_scope_key(llm_task.get("original_filename"), result_row["professional"])
                    if owner is None or not get_row_decision(owner, scope, result_row):
                        _apply_llm_review(llm_engine, llm_budget, row_for_decision, q_info, result_row)
                if "cross_encoder_rerank" in res:
                    result_row["cross_encoder_rerank"] = res["cross_encoder_rerank"]
                final_chunk_results.append(result_row)
            
        if task_id:
            codes = [binding.get("code") for result in final_chunk_results for binding in result["bindings"]]
            for result in final_chunk_results:
                if result["top_candidates"]:
                    codes.append(result["top_candidates"][0].get("code"))
            with catalog_lock:
                revision = get_db_revision()
                live = fetch_live_material_map(codes)
            for job, match, result in zip(match_jobs, first_pass_results, final_chunk_results, strict=True):
                checkpoint()
                query, _, raw = job
                source = "bom" if is_bom_name(raw.get("name")) else (
                    "rule" if result["decision_source"] == SOURCE_RULE else "matcher"
                )
                if revision != first_revision:
                    match["_initial_rank_stale"] = True
                result["initial_match"] = assess_initial_match(
                    result, query, match, result, source, initial_policy, live, revision,
                )
        total_results.extend(final_chunk_results)
        
        if task_id:
            match_progress = min(100, int(((idx + len(chunk)) / total_items) * 100))
            update_task_progress(task_id, matching_progress=match_progress, current_step=f"正在与企业标准库进行深度比对与打分... ({idx + len(chunk)}/{total_items})")
            
    logger.debug("batch_match_rows completed.")
    return total_results

@app.post("/api/batch_match", dependencies=[Depends(require_employee)])
@http_job(match_jobs, timeout=300)
def api_batch_match(payload: Dict[str, Any]):
    """Match a batch of lines sent as JSON."""
    rows = payload.get("rows", [])
    if not rows:
        return JSONResponse(content={"results": []})

    # Map input format to expected format
    # V015-M1：透传行内 unit/category/brand/professional（缺失时保持历史默认），
    # 规范化统一由 batch_match_rows 内的 prepare_query 契约完成。
    raw_rows = []
    for r in rows:
        name = r.get("name") or r.get("text", "")
        if is_bom_name(name):
            qty = r.get("qty")
            raw_rows.append({
                "row_idx": r.get("id", 0), "name": name, "specification": r.get("specification") or r.get("text", ""),
                "unit": r.get("unit", ""), "qty": qty if positive_quantity(qty) else 0.0, "loss": 1.0,
                "category": r.get("category", ""), "brand": r.get("brand", ""), "professional": r.get("professional") or "",
            })
            continue
        raw_rows.append({
            "row_idx": r.get("id", 0),
            "name": r.get("text", ""),
            "specification": r.get("text", ""), # Use text for both name/spec if simplified
            "unit": r.get("unit", "台"),
            "qty": float(r.get("qty", 1.0) or 1.0),
            "loss": 1.0,
            "category": r.get("category", ""),
            "brand": r.get("brand", ""),
            "professional": r.get("professional", "暖通")
        })
        
    results = batch_match_rows(raw_rows)
    return JSONResponse(content={"results": results})

@app.post("/api/match")
@http_job(match_jobs, timeout=120)
def api_match_single(payload: Dict[str, Any], current_user: dict = Depends(require_employee)):
    """Match a single query item; records a new recommendation revision when bound to a server row."""
    from app.query_preprocessor import prepare_query

    doc_id = payload.get("document_id")
    sheet_id = payload.get("sheet_id")
    source_row_id = payload.get("source_row_id")
    has_doc_ctx = any(v is not None and str(v).strip() != "" for v in (doc_id, sheet_id, source_row_id))

    if has_doc_ctx:
        try:
            ctx = load_verified_source_row(
                current_user,
                str(doc_id or ""),
                str(sheet_id or ""),
                str(source_row_id or ""),
                professional=payload.get("professional"),
                parsed_spec_override=payload.get("parsed_spec"),
                has_parsed_spec_override=("parsed_spec" in payload),
            )
        except FeedbackContractError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
        src_row = ctx["source_row"]
        professional = ctx["server_professional"]
        query_info = prepare_query(
            str(src_row.get("raw_name") or ""),
            str(src_row.get("raw_spec") or ""),
            professional,
            category=str(src_row.get("raw_category") or ""),
            brand=str(src_row.get("raw_brand") or ""),
            unit=str(src_row.get("raw_unit") or ""),
        )
        query_info["parsed_spec"] = ctx["validated_parsed_spec"]
    else:
        text = payload.get("text", "")
        name = payload.get("name", "") or extract_device_name(text)
        category = payload.get("category", "")
        brand = payload.get("brand", "")
        professional = payload.get("professional") if ("professional" in payload and payload.get("professional") is not None) else "暖通"
        unit = payload.get("unit", "")

        # V015-M1：单条/重算与上传批量共用 prepare_query 契约，保证同一输入
        # 各入口规范字段一致。客户端显式传入 parsed_spec（特征维度重算）时，
        # 以传入解析结果覆盖统一契约的默认解析，保持既有重算交互不变。
        query_info = prepare_query(
            name, text, professional,
            category=category, brand=brand, unit=unit,
        )
        if "parsed_spec" in payload:
            override = payload["parsed_spec"] or {}
            if isinstance(override, dict):
                merged_parsed = dict(query_info["parsed_spec"])
                merged_parsed.update(override)
                # 前端重算传入 {model, attrs} 子集时，保留统一契约的清洗文本
                # 与规范名称，避免编码文本退回原始全文
                merged_parsed.setdefault("clean_text", query_info["parsed_spec"].get("clean_text", ""))
                merged_parsed.setdefault("name", query_info["parsed_spec"].get("name", ""))
                merged_parsed.setdefault("spec", query_info["parsed_spec"].get("spec", ""))
                query_info["parsed_spec"] = merged_parsed

    if has_doc_ctx:
        raw = {"name": src_row.get("raw_name"), "specification": src_row.get("raw_spec"),
               "category": src_row.get("raw_category"), "brand": src_row.get("raw_brand"),
               "unit": src_row.get("raw_unit"), "qty": src_row.get("raw_qty"), "professional": professional}
    else:
        raw = {"name": name, "specification": text, "category": category, "brand": brand,
               "unit": unit, "qty": payload.get("qty"), "professional": payload.get("professional")}
    row_for_bom = _build_row_item_from_raw(raw)
    bom = _current_bom(row_for_bom)
    checkpoint()
    policy = acceptance_policy()
    if bom is not None:
        res = match_item(query_info)
        res.update(_bom_match_fields(row_for_bom, bom, edited="parsed_spec" in payload))
    elif policy is None:
        res = match_item(query_info)
    else:
        res = match_item(query_info, calibration_groups=policy.equivalences)
        if not has_doc_ctx and "qty" not in payload:
            raw["qty"] = 1.0
        decision, bindings = _calibrated_decision(
            _build_row_item_from_raw(raw), query_info, res, policy,
            trusted=not has_doc_ctx and "parsed_spec" not in payload,
        )
        res.pop("_acceptance_rank", None)
        res.update(decision, bindings=bindings, matched_code=bindings[0]["code"] if bindings else "")
    llm_engine = active_llm_decision()
    if (llm_engine is not None and bom is None and not has_doc_ctx and "parsed_spec" not in payload
            and _select_matching_binding_rule(raw, load_auto_binding_rules()) is None):
        _apply_llm_review(llm_engine, CallBudget(1), row_for_bom, query_info, res)
    checkpoint()
    if has_doc_ctx:
        try:
            snap_meta = _save_rematch_revision(
                actor_user=current_user,
                document_id=str(doc_id or ""),
                sheet_id=str(sheet_id or ""),
                source_row_id=str(source_row_id or ""),
                professional=professional,
                parsed_spec=query_info["parsed_spec"],
                match_result=res,
                snapshot_kind="match_override",
            )
            res.update(snap_meta)
        except FeedbackContractError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    else:
        res.update(compute_runtime_versions())
        res["parsed_spec"] = query_info["parsed_spec"]
    return JSONResponse(content=res)

@app.post("/api/rematch")
@http_job(match_jobs, timeout=120)
def api_rematch(req: RematchRequest, current_user: dict = Depends(require_employee)):
    """Rematch item after manual parameters override; verifies server source context and separates provenance."""
    from app.query_preprocessor import prepare_query

    fixed = req.fixed_params
    has_doc_ctx = any(
        v is not None and str(v).strip() != ""
        for v in (req.document_id, req.sheet_id, req.source_row_id)
    )
    if has_doc_ctx:
        try:
            ctx = load_verified_source_row(
                current_user,
                str(req.document_id or ""),
                str(req.sheet_id or ""),
                str(req.source_row_id or ""),
                professional=req.professional,
                raw_text=req.text,
            )
        except FeedbackContractError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
        src_row = ctx["source_row"]
        professional = ctx["server_professional"]
        auth_name = str(src_row.get("raw_name") or "") or extract_device_name(str(src_row.get("raw_spec") or ""))
        auth_spec = str(src_row.get("raw_spec") or "")
        auth_category = str(src_row.get("raw_category") or "")
        auth_brand = str(src_row.get("raw_brand") or "")
        auth_unit = str(src_row.get("raw_unit") or "")
    else:
        professional = req.professional if req.professional is not None else "暖通"
        auth_name = extract_device_name(req.text)
        auth_spec = req.text
        auth_category = req.category or ""
        auth_brand = req.brand or ""
        auth_unit = req.unit or ""

    parsed = extract_query_params(auth_name, auth_spec, professional, material_category=auth_category)

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

    # V015-M1：重算入口与上传批量共用 prepare_query 契约；手动修正参数以
    # parsed_spec 覆盖统一契约默认解析，category 不再清空（保持原行甲供材
    # 品类/专业链），名称与清洗文本由统一契约产出。
    query_info = prepare_query(
        auth_name, auth_spec, professional,
        category=auth_category,
        brand=auth_brand,
        unit=auth_unit,
    )
    query_info["parsed_spec"] = parsed

    checkpoint()
    res = match_item(query_info)
    if has_doc_ctx and is_bom_name(auth_name):
        row = _build_row_item_from_raw({
            "name": auth_name, "specification": auth_spec, "professional": professional,
            "category": auth_category, "brand": auth_brand, "unit": auth_unit, "qty": src_row.get("raw_qty"),
        })
        res.update(_bom_match_fields(row, _current_bom(row), edited=True))
    checkpoint()

    has_doc_ctx = any(
        v is not None and str(v).strip() != ""
        for v in (req.document_id, req.sheet_id, req.source_row_id)
    )
    if has_doc_ctx:
        try:
            snap_meta = _save_rematch_revision(
                actor_user=current_user,
                document_id=str(req.document_id or ""),
                sheet_id=str(req.sheet_id or ""),
                source_row_id=str(req.source_row_id or ""),
                professional=professional,
                parsed_spec=query_info["parsed_spec"],
                match_result=res,
                snapshot_kind="rematch",
            )
            res.update(snap_meta)
        except FeedbackContractError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    else:
        res.update(compute_runtime_versions())
        res["parsed_spec"] = query_info["parsed_spec"]

    log_audit_event(
        username=current_user["username"],
        name=current_user.get("name"),
        role=current_user.get("role"),
        action="单条特征重算",
        details=f"重算第 {req.row_id} 行: {req.text[:30]}"
    )
    return JSONResponse(content=res)

@app.post("/api/refresh_vector", dependencies=[Depends(require_employee)])
@http_job(catalog_jobs, timeout=900)
def api_refresh_vector():
    """Trigger manual vector index rebuild."""
    from app.db_manager import RecoveryBlockedError
    from app.vector_index import CacheRollbackError

    if is_recovery_blocked():
        info = get_recovery_block_status()
        raise HTTPException(
            status_code=409,
            detail=f"恢复失败/需人工处置，向量缓存处于写入阻断状态：{info.get('reason') or '请先核验并完成一致性恢复'}",
        )
    if _db_import_lock.locked():
        raise HTTPException(409, "当前已有物料库导入任务正在运行")
    try:
        index_manager.check_db_timestamp_and_build(DB_PATH, force=True)
        return {"code": 0, "msg": "向量索引已成功刷新"}
    except JobStopped:
        raise
    except (CacheRollbackError, RecoveryBlockedError) as e:
        raise HTTPException(status_code=409, detail=str(e))
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

def _restore_flywheel_config(filepath: str, snapshot: Any):
    """Read legacy content snapshots and M4 snapshots preserving file absence."""
    versioned = isinstance(snapshot, dict) and snapshot.get("snapshot_format") == "v016-m4-file-v1"
    content = snapshot["content"] if versioned else snapshot
    atomic_write_json_config(filepath, content)
    if versioned and not snapshot["existed"]:
        os.remove(filepath)


def trigger_all_config_reloads(raise_on_error: bool = False):
    """Trigger reload across all configuration consumers and clear caches."""
    errors = []
    steps = [
        ("reload_preprocessor_configs", reload_preprocessor_configs),
        ("reload_matcher_configs", reload_matcher_configs),
    ]
    from app.normalizer import reload_normalizer_configs
    from app.domain_extractors import reload_extractor_configs
    from app.domain_router import reload_router_configs
    from app.llm_rule_engine import LLMRuleManager

    steps.extend([
        ("reload_normalizer_configs", reload_normalizer_configs),
        ("reload_extractor_configs", reload_extractor_configs),
        ("reload_router_configs", reload_router_configs),
        ("LLMRuleManager.clear_active_cache", LLMRuleManager.clear_active_cache),
    ])
    for step_name, fn in steps:
        try:
            fn()
        except Exception as e:
            logger.warning(f"Error in {step_name}: {e}")
            errors.append(f"{step_name}: {e}")
    if raise_on_error and errors:
        raise RuntimeError("; ".join(errors))

@app.post("/api/config/save", dependencies=[Depends(require_admin)])
@http_job(rule_jobs, timeout=120)
def api_save_config(req: ConfigSaveRequest):
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
    client_event_id: Optional[str] = None
    document_id: Optional[str] = None
    sheet_id: Optional[str] = None
    source_row_id: Optional[str] = None
    work_session_id: Optional[str] = ""
    saved_session_id: Optional[str] = ""
    session_id: Optional[str] = ""
    row_idx: Optional[int] = 0
    event_seq: Optional[StrictInt] = None
    recommendation_snapshot_id: Optional[str] = None
    recommendation_revision: Optional[int] = None
    discipline: Optional[str] = ""
    professional: Optional[str] = ""
    raw_name: Optional[str] = ""
    raw_spec: Optional[str] = ""
    raw_unit: Optional[str] = ""
    raw_category: Optional[str] = ""
    parsed_spec: Optional[Dict[str, Any]] = None
    extracted_params: Optional[Dict[str, Any]] = None
    auto_top1_code: Optional[str] = ""
    auto_top1_score: Optional[float] = 0.0
    auto_top1_name: Optional[str] = ""
    auto_candidates: Optional[List[Dict[str, Any]]] = None
    algo_version: Optional[str] = ""
    model_version: Optional[str] = ""
    config_version: Optional[str] = ""
    action_type: Optional[str] = None
    final_targets: Optional[List[Dict[str, Any]]] = None
    user_id: Optional[Any] = None
    actor_user_id: Optional[Any] = None

@app.post("/api/flywheel/record")
def api_flywheel_record(req: FlywheelRecordRequest, current_user: dict = Depends(require_employee)):
    """Record verified, idempotent, revision-ordered user match action into Data Flywheel."""
    try:
        result = record_flywheel_event(current_user, req.model_dump())
        return result
    except FeedbackContractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as e:
        logger.error(f"Error in api_flywheel_record: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/flywheel/runtime-version")
def api_flywheel_runtime_version(current_user: dict = Depends(require_employee)):
    """Return actual runtime algorithm, model, and config versions."""
    return compute_runtime_versions()

@app.get("/api/flywheel/documents/{document_id}/rows/{source_row_id}/trace")
def api_flywheel_row_trace(
    document_id: str,
    source_row_id: str,
    current_user: dict = Depends(require_employee),
):
    """Inspect initial snapshot, rematch revisions, event history, and effective final state for an owned row."""
    try:
        return get_row_trace(current_user, document_id, source_row_id)
    except FeedbackContractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as e:
        logger.error(f"Error in api_flywheel_row_trace: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class FlywheelEvalRequest(BaseModel):
    suggestion_id: int
    edited_target_value: Optional[str] = None
    discipline: Optional[str] = None
    original_spec: Optional[str] = None

class FlywheelAcceptRequest(BaseModel):
    suggestion_id: int
    edited_target_value: Optional[str] = None
    eval_id: Optional[str] = None
    discipline: Optional[str] = None
    original_spec: Optional[str] = None
    neutral_reason: Optional[str] = None

class FlywheelRejectRequest(BaseModel):
    suggestion_id: int
    reason: Optional[str] = ""

class FlywheelRollbackRequest(BaseModel):
    snapshot_version: Optional[str] = None

@app.get("/api/flywheel/statistics", dependencies=[Depends(require_manager)])
def api_flywheel_statistics():
    from app.db_manager import get_db_cursor
    from app.flywheel_db import init_flywheel_tables, trusted_flywheel_where_clause
    init_flywheel_tables()
    trusted_all = trusted_flywheel_where_clause(include_effective_only=False)
    trusted_eff = trusted_flywheel_where_clause(include_effective_only=True)
    with get_db_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) as total FROM flywheel_records WHERE {trusted_all}")
        total_records = cur.fetchone()["total"]

        cur.execute(f"SELECT COUNT(*) as legacy_total FROM flywheel_records WHERE NOT ({trusted_all})")
        legacy_untrusted_records = cur.fetchone()["legacy_total"]

        cur.execute(f"SELECT action_type, COUNT(*) as cnt FROM flywheel_records WHERE {trusted_eff} GROUP BY action_type")
        action_counts = {r["action_type"]: r["cnt"] for r in cur.fetchall()}

        cur.execute(f"SELECT COUNT(DISTINCT actor_user_id || ':' || actor_account_epoch) as users FROM flywheel_records WHERE {trusted_all}")
        active_users = cur.fetchone()["users"]

        cur.execute("SELECT status, COUNT(*) as cnt FROM flywheel_rule_suggestions GROUP BY status")
        suggestion_counts = {r["status"]: r["cnt"] for r in cur.fetchall()}

        cur.execute("SELECT COUNT(*) as snap_cnt FROM flywheel_config_snapshots WHERE is_active = 1")
        active_snapshots = cur.fetchone()["snap_cnt"]

        cur.execute("SELECT COUNT(*) as agg_cnt FROM flywheel_pattern_aggregates")
        pattern_aggregates_count = cur.fetchone()["agg_cnt"]

    return JSONResponse(content={
        "total_records": total_records,
        "legacy_untrusted_records": legacy_untrusted_records,
        "action_counts": action_counts,
        "active_users": active_users,
        "suggestion_counts": suggestion_counts,
        "active_snapshots": active_snapshots,
        "pattern_aggregates_count": pattern_aggregates_count,
    })

@app.get("/api/flywheel/suggestions", dependencies=[Depends(require_manager)])
def api_flywheel_suggestions(status: Optional[str] = None, discipline: Optional[str] = None):
    from app.db_manager import get_db_cursor
    from app.flywheel_db import init_flywheel_tables
    from app.rule_context import canonicalize_professional
    init_flywheel_tables()
    where_parts = []
    params: List[Any] = []
    if status:
        where_parts.append("status = ?")
        params.append(status)
    if discipline:
        where_parts.append("discipline = ?")
        params.append(canonicalize_professional(discipline))
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    order_sql = (
        "ORDER BY support_count DESC, consistency DESC, id DESC"
        if status
        else "ORDER BY CASE status WHEN 'pending' THEN 1 WHEN 'observing' THEN 2 WHEN 'accepted' THEN 3 ELSE 4 END, support_count DESC, consistency DESC, id DESC"
    )
    with get_db_cursor() as cur:
        cur.execute(f"SELECT * FROM flywheel_rule_suggestions {where_sql} {order_sql}", params)
        rows = [dict(r) for r in cur.fetchall()]
    return JSONResponse(content={"suggestions": rows})

@app.get("/api/flywheel/aggregates", dependencies=[Depends(require_manager)])
def api_flywheel_aggregates(
    suggestion_type: Optional[str] = None,
    discipline: Optional[str] = None,
    source_origin: Optional[str] = None,
):
    from app.db_manager import get_db_cursor
    from app.flywheel_db import init_flywheel_tables
    from app.rule_context import canonicalize_professional
    init_flywheel_tables()
    where_parts = []
    params: List[Any] = []
    if suggestion_type:
        where_parts.append("suggestion_type = ?")
        params.append(suggestion_type)
    if discipline is not None and discipline != "":
        where_parts.append("discipline = ?")
        params.append(canonicalize_professional(discipline))
    if source_origin:
        where_parts.append("source_origin = ?")
        params.append(source_origin)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    with get_db_cursor() as cur:
        cur.execute(
            f"SELECT * FROM flywheel_pattern_aggregates {where_sql} ORDER BY total_support DESC, id DESC",
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
    return JSONResponse(content={"aggregates": rows})

@app.post("/api/flywheel/suggestions/eval")
@http_job(rule_jobs, timeout=120)
def api_flywheel_eval_suggestion(req: FlywheelEvalRequest, current_user: dict = Depends(require_admin)):
    from app.db_manager import get_db_cursor
    from app.flywheel_sandbox import start_governed_evaluation
    from app.rule_governance import EvalGateError, compute_rule_digest, normalize_rule_spec

    verified = resolve_verified_user(current_user)
    if not verified or verified.get("must_change_password") or verified.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员（admin）有权启动沙箱评测。")
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM flywheel_rule_suggestions WHERE id = ?", (req.suggestion_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        sug_dict = dict(row)

    sug_type = str(sug_dict.get("suggestion_type") or "ALIAS").upper()
    rule_source = "flywheel_binding" if sug_type in ("BINDING", "BINDING_RULE") else "flywheel_alias"
    target_val = req.edited_target_value if req.edited_target_value is not None else sug_dict.get("target_value")
    disc = req.discipline if req.discipline is not None else (sug_dict.get("discipline") or "")
    orig_spec = req.original_spec if req.original_spec is not None else (sug_dict.get("original_spec") or "")
    try:
        norm_spec = normalize_rule_spec(
            rule_source=rule_source,
            rule_key=f"suggestion:{req.suggestion_id}",
            rule_type=sug_type,
            revision=1,
            discipline=disc,
            source_pattern=sug_dict.get("source_pattern") or "",
            target_value=target_val if target_val is not None else "",
            original_spec=orig_spec,
            source_origin=str(sug_dict.get("source_origin") or ""),
            require_concrete_discipline=True,
        )
        task_id = start_governed_evaluation(
            rule_source=rule_source,
            rule_key=f"suggestion:{req.suggestion_id}",
            rule_type=norm_spec["rule_type"],
            normalized_spec=norm_spec,
            rule_revision=1,
            owner_user=verified,
        )
    except EvalGateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail())

    return {
        "code": 0,
        "task_id": task_id,
        "eval_id": task_id,
        "payload_digest": compute_rule_digest(norm_spec),
        "discipline": norm_spec["discipline"],
        "msg": "沙箱评测任务已启动",
    }

@app.get("/api/flywheel/eval/{task_id}")
def api_flywheel_get_eval_status(task_id: str, current_user: dict = Depends(require_admin)):
    from app.flywheel_sandbox import get_eval_task_status_for_user
    status = get_eval_task_status_for_user(task_id, current_user)
    if not status:
        raise HTTPException(status_code=404, detail="指定评测任务不存在或无权访问")
    return JSONResponse(content=status)


def accept_flywheel_suggestion_service(
    suggestion_id: int,
    actor_user: Dict[str, Any],
    *,
    edited_target_value: Optional[str] = None,
    eval_id: Optional[str] = None,
    discipline: Optional[str] = None,
    original_spec: Optional[str] = None,
    neutral_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Service-layer flywheel accept enforcing M2 gate, CAS concurrency, atomic snapshot, and recovery blocking."""
    from app.flywheel_db import open_flywheel_tx_connection
    from app.rule_governance import (
        EvalGateError,
        _PUBLISH_GATE_LOCK,
        assert_rule_publish_not_blocked,
        block_rule_publish_recovery,
        load_scoped_aliases,
        normalize_rule_spec,
        verify_eval_credential_for_publish,
    )

    verified = resolve_verified_user(actor_user)
    if not verified:
        raise PermissionError("UNAUTHORIZED: 无效的操作账号。")
    if verified.get("must_change_password"):
        raise PermissionError("MUST_CHANGE_PASSWORD: 请先修改初始或重置密码。")
    if verified.get("role") != "admin":
        raise PermissionError("FORBIDDEN: 仅管理员（admin）有权采纳飞轮全局规则。")

    assert_rule_publish_not_blocked()

    operator_id = str(verified.get("username") or "admin")
    config_dir = _get_config_dir()
    snap_version = f"snap_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    with _PUBLISH_GATE_LOCK:
        tx_conn = open_flywheel_tx_connection()
        try:
            row = tx_conn.execute(
                "SELECT * FROM flywheel_rule_suggestions WHERE id = ?",
                (suggestion_id,),
            ).fetchone()
            if not row:
                raise EvalGateError(404, "suggestion_not_found", "Suggestion not found")
            sug = dict(row)
        finally:
            tx_conn.close()

        prev_status = str(sug.get("status") or "")
        if prev_status not in ("pending", "observing", "rolled_back"):
            raise EvalGateError(
                409,
                "suggestion_status_conflict",
                f"当前建议状态为 '{prev_status}'，不可重复采纳。",
                details={"suggestion_id": suggestion_id, "status": prev_status},
            )

        target_value_raw = edited_target_value if edited_target_value is not None else sug["target_value"]
        target_type_raw = str(sug["suggestion_type"] or "ALIAS").upper()
        source_pattern = str(sug["source_pattern"] or "").strip()
        eff_disc = discipline if discipline is not None else (sug.get("discipline") or "")
        eff_spec = original_spec if original_spec is not None else (sug.get("original_spec") or "")

        if target_type_raw == "ALIAS":
            rule_source = "flywheel_alias"
        elif target_type_raw in ("BINDING", "BINDING_RULE"):
            rule_source = "flywheel_binding"
        else:
            raise EvalGateError(400, "unsupported_suggestion_type", f"Unsupported suggestion type: {target_type_raw}")

        norm_spec = normalize_rule_spec(
            rule_source=rule_source,
            rule_key=f"suggestion:{suggestion_id}",
            rule_type=target_type_raw,
            revision=1,
            discipline=eff_disc,
            source_pattern=source_pattern,
            target_value=target_value_raw if target_value_raw is not None else "",
            original_spec=eff_spec,
            source_origin=str(sug.get("source_origin") or ""),
            require_concrete_discipline=True,
        )
        ev = verify_eval_credential_for_publish(
            eval_id=eval_id,
            rule_source=rule_source,
            rule_key=f"suggestion:{suggestion_id}",
            rule_revision=1,
            normalized_spec=norm_spec,
            neutral_reason=neutral_reason,
        )
        canon_disc = norm_spec["discipline"]
        clean_neutral = str(neutral_reason or "").strip() if ev["benefit_class"] == "neutral" else ""

        if target_type_raw == "ALIAS":
            snap_target_type = "SCOPED_ALIAS"
            target_path = os.path.join(config_dir, "scoped_aliases.json")
            before_data = load_scoped_aliases(config_dir=config_dir)
            after_data = {k: dict(v) for k, v in before_data.items()}
            after_data.setdefault(canon_disc, {})[source_pattern] = norm_spec["target_value"]
            stored_target_value = norm_spec["target_value"]
            reason = (
                f"采纳专业作用域别名 [{canon_disc}] '{source_pattern}' -> '{stored_target_value}'"
                + (" (经人工编辑)" if edited_target_value is not None else "")
            )
        else:
            snap_target_type = "BINDING_RULE"
            target_path = _get_auto_binding_rules_path()
            before_data = load_auto_binding_rules()
            target_ident = (source_pattern, canon_disc, norm_spec["original_spec"])
            after_data = [r for r in before_data if _binding_rule_identity_tuple(r) != target_ident]
            new_binding_rule = {
                "original_name": source_pattern,
                "original_spec": norm_spec["original_spec"],
                "professional": canon_disc,
                "bound_materials": norm_spec["bound_materials"],
                "eval_id": ev["eval_id"],
                "rule_digest": ev["payload_digest"],
                "benefit_class": ev["benefit_class"],
                "governance_provenance": "verified",
            }
            if clean_neutral:
                new_binding_rule["neutral_reason"] = clean_neutral
            after_data.append(new_binding_rule)
            stored_target_value = json.dumps(norm_spec["bound_materials"], ensure_ascii=False)
            reason = (
                f"采纳专业作用域绑定规则 [{canon_disc}] '{source_pattern}'"
                + (" (经人工编辑)" if edited_target_value is not None else "")
            )

        before_snapshot = {"snapshot_format": "v016-m4-file-v1", "existed": os.path.isfile(target_path), "content": before_data}

        # 2. Atomic write target config file (baseline alias_dict.json is never mutated by flywheel)
        try:
            atomic_write_json_config(target_path, after_data)
        except Exception as e:
            logger.error(f"Failed to write config file {target_path}: {e}")
            raise EvalGateError(500, "config_write_failed", f"配置文件写入失败: {e}") from e

        # 3. Atomic DB CAS transaction (BEGIN IMMEDIATE) with compensating rollback + recovery guard
        tx_conn = open_flywheel_tx_connection()
        try:
            tx_conn.execute("BEGIN IMMEDIATE")
            cur_row = tx_conn.execute(
                "SELECT * FROM flywheel_rule_suggestions WHERE id = ?",
                (suggestion_id,),
            ).fetchone()
            if not cur_row or str(cur_row["status"] or "") not in ("pending", "observing", "rolled_back"):
                tx_conn.rollback()
                try:
                    _restore_flywheel_config(target_path, before_snapshot)
                except Exception as comp_exc:
                    block_rule_publish_recovery(
                        f"并发状态冲突且文件回滚失败: {comp_exc}",
                        details={"suggestion_id": suggestion_id, "target_path": target_path},
                    )
                    raise EvalGateError(
                        500,
                        "publish_recovery_blocked",
                        f"并发状态冲突且文件补偿恢复失败，已锁定后续规则发布：{comp_exc}",
                    ) from comp_exc
                raise EvalGateError(
                    409,
                    "suggestion_status_conflict",
                    f"并发状态冲突：建议 #{suggestion_id} 当前状态不可重复采纳。",
                )

            cur = tx_conn.execute(
                """
                UPDATE flywheel_rule_suggestions
                SET status = 'accepted',
                    target_value = ?,
                    discipline = ?,
                    reviewer_id = ?,
                    reviewed_at = CURRENT_TIMESTAMP,
                    applied_rule_id = ?,
                    last_eval_id = ?,
                    last_eval_digest = ?,
                    neutral_reason = ?,
                    governance_provenance = 'verified'
                WHERE id = ? AND status IN ('pending', 'observing', 'rolled_back')
                """,
                (
                    stored_target_value,
                    canon_disc,
                    operator_id,
                    snap_version,
                    ev["eval_id"],
                    ev["payload_digest"],
                    clean_neutral,
                    suggestion_id,
                ),
            )
            if cur.rowcount != 1:
                tx_conn.rollback()
                try:
                    _restore_flywheel_config(target_path, before_snapshot)
                except Exception as comp_exc:
                    block_rule_publish_recovery(
                        f"CAS 失败且文件回滚失败: {comp_exc}",
                        details={"suggestion_id": suggestion_id, "target_path": target_path},
                    )
                    raise EvalGateError(
                        500,
                        "publish_recovery_blocked",
                        f"并发冲突且文件补偿恢复失败，已锁定后续规则发布：{comp_exc}",
                    ) from comp_exc
                raise EvalGateError(409, "suggestion_status_conflict", "并发状态冲突：采纳被拒绝。")

            tx_conn.execute(
                """
                INSERT INTO flywheel_config_snapshots (
                    snapshot_version, target_type, config_before, config_after,
                    suggestion_id, reason, operator_id, is_active, targeted_eval_passed,
                    eval_id, rule_digest, benefit_class, neutral_reason, discipline,
                    governance_provenance, migration_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?, 'verified', 'v0.16-m2-v1')
                """,
                (
                    snap_version,
                    snap_target_type,
                    json.dumps(before_snapshot, ensure_ascii=False),
                    json.dumps(after_data, ensure_ascii=False),
                    suggestion_id,
                    reason,
                    operator_id,
                    ev["eval_id"],
                    ev["payload_digest"],
                    ev["benefit_class"],
                    clean_neutral,
                    canon_disc,
                ),
            )
            tx_conn.commit()
        except EvalGateError:
            raise
        except Exception as e:
            try:
                tx_conn.rollback()
            except Exception:
                pass
            logger.error(f"DB update failed during suggestion accept, compensating file rollback: {e}")
            try:
                _restore_flywheel_config(target_path, before_snapshot)
            except Exception as ce:
                logger.critical(f"Compensating rollback failed for {target_path}: {ce}")
                block_rule_publish_recovery(
                    f"采纳事务失败且配置文件回滚失败: {ce}",
                    details={"suggestion_id": suggestion_id, "target_path": target_path, "db_error": str(e)},
                )
                raise EvalGateError(
                    500,
                    "publish_recovery_blocked",
                    f"数据库事务失败且配置文件补偿恢复失败，已阻断后续规则发布：{ce}",
                ) from ce
            raise EvalGateError(
                500,
                "publish_db_failed_rolled_back",
                f"数据库更新失败，已回滚配置文件：{e}",
            ) from e
        finally:
            tx_conn.close()

        # 4. Trigger in-memory cache reload outside DB TX; compensate BOTH file + DB on failure
        try:
            trigger_all_config_reloads(raise_on_error=True)
        except Exception as reload_exc:
            logger.error(f"Config reload failed after flywheel accept, compensating file + DB: {reload_exc}")
            try:
                _restore_flywheel_config(target_path, before_snapshot)
                comp_conn = open_flywheel_tx_connection()
                try:
                    comp_conn.execute("BEGIN IMMEDIATE")
                    comp_conn.execute(
                        "UPDATE flywheel_config_snapshots SET is_active = 0 WHERE snapshot_version = ?",
                        (snap_version,),
                    )
                    comp_conn.execute(
                        """
                        UPDATE flywheel_rule_suggestions
                        SET status = ?, applied_rule_id = ?, reviewed_at = ?
                        WHERE id = ?
                        """,
                        (sug.get("status") or "pending", sug.get("applied_rule_id"), sug.get("reviewed_at"), suggestion_id),
                    )
                    comp_conn.commit()
                finally:
                    comp_conn.close()
                trigger_all_config_reloads(raise_on_error=True)
            except Exception as comp_exc:
                logger.critical(f"Compensation after reload failure failed: {comp_exc}")
                block_rule_publish_recovery(
                    f"热重载失败且文件/DB补偿恢复失败: {comp_exc}",
                    details={"suggestion_id": suggestion_id, "snapshot_version": snap_version},
                )
                raise EvalGateError(
                    500,
                    "publish_recovery_blocked",
                    f"热重载失败且补偿恢复失败，系统已锁定后续规则发布：{comp_exc}",
                ) from comp_exc
            raise EvalGateError(
                500,
                "publish_reload_failed_rolled_back",
                f"配置热重载失败，已回滚文件与数据库状态：{reload_exc}",
            ) from reload_exc

        log_audit_event(
            username=operator_id,
            action="飞轮规则采纳",
            details=f"采纳 {snap_target_type}[{canon_disc}] 建议: {source_pattern} (快照: {snap_version}, eval: {ev['eval_id']})",
        )
        return {
            "code": 0,
            "msg": "规则已通过沙箱门禁并成功采纳生效",
            "snapshot_version": snap_version,
            "eval_id": ev["eval_id"],
            "benefit_class": ev["benefit_class"],
            "discipline": canon_disc,
        }


@app.post("/api/flywheel/suggestions/accept", dependencies=[Depends(require_admin)])
@http_job(rule_jobs, timeout=120)
def api_flywheel_accept_suggestion(req: FlywheelAcceptRequest, current_user: dict = Depends(require_admin)):
    """Accept a suggestion via the unified M2 service gate."""
    from app.rule_governance import EvalGateError

    try:
        return accept_flywheel_suggestion_service(
            req.suggestion_id,
            current_user,
            edited_target_value=req.edited_target_value,
            eval_id=req.eval_id,
            discipline=req.discipline,
            original_spec=req.original_spec,
            neutral_reason=req.neutral_reason,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except EvalGateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail())

@app.post("/api/flywheel/suggestions/reject", dependencies=[Depends(require_admin)])
def api_flywheel_reject_suggestion(req: FlywheelRejectRequest, current_user: dict = Depends(require_admin)):
    from app.db_manager import get_db_cursor
    verified = resolve_verified_user(current_user)
    if not verified or verified.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员（admin）有权驳回飞轮全局规则。")
    operator_id = str(verified.get("username") or "admin")
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
def api_flywheel_get_snapshots():
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
@http_job(rule_jobs, timeout=120)
def api_flywheel_rollback_snapshot(req: FlywheelRollbackRequest, current_user: dict = Depends(require_admin)):
    """LIFO stack-based rollback restoring config, DB CAS state, and in-memory caches with recovery guard."""
    from app.flywheel_db import open_flywheel_tx_connection
    from app.rule_governance import EvalGateError, _PUBLISH_GATE_LOCK, block_rule_publish_recovery

    verified = resolve_verified_user(current_user)
    if not verified or verified.get("must_change_password") or verified.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员（admin）有权回滚飞轮全局配置快照。")
    operator_id = str(verified.get("username") or "admin")
    config_dir = _get_config_dir()

    with _PUBLISH_GATE_LOCK:
        tx_conn = open_flywheel_tx_connection()
        try:
            stack_top = tx_conn.execute(
                "SELECT * FROM flywheel_config_snapshots WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if req.snapshot_version:
                snap = tx_conn.execute(
                    "SELECT * FROM flywheel_config_snapshots WHERE snapshot_version = ? AND is_active = 1",
                    (req.snapshot_version,),
                ).fetchone()
            else:
                snap = tx_conn.execute(
                    "SELECT * FROM flywheel_config_snapshots WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if not snap:
                raise HTTPException(status_code=400, detail="没有可回滚的有效快照")
            if not stack_top or snap["id"] != stack_top["id"]:
                raise HTTPException(status_code=409, detail="只能按 LIFO 顺序回滚最新激活的栈顶快照。")
            snap = dict(snap)
        finally:
            tx_conn.close()

        snap_id = snap["id"]
        snap_ver = snap["snapshot_version"]
        target_type = snap["target_type"]
        config_before = json.loads(snap["config_before"])
        config_after = json.loads(snap["config_after"])
        suggestion_id = snap["suggestion_id"]

        if target_type == "SCOPED_ALIAS" or (
            target_type == "ALIAS" and str(snap.get("governance_provenance") or "") == "verified"
        ):
            target_path = os.path.join(config_dir, "scoped_aliases.json")
        elif target_type == "ALIAS":
            target_path = os.path.join(config_dir, "alias_dict.json")
        elif target_type == "BINDING_RULE":
            target_path = _get_auto_binding_rules_path()
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported target type: {target_type}")

        try:
            _restore_flywheel_config(target_path, config_before)
        except Exception as e:
            logger.error(f"Failed to write config file {target_path} during rollback: {e}")
            raise HTTPException(status_code=500, detail=f"配置文件回滚写入失败: {e}")

        tx_conn = open_flywheel_tx_connection()
        try:
            tx_conn.execute("BEGIN IMMEDIATE")
            if suggestion_id:
                tx_conn.execute(
                    """
                    UPDATE flywheel_rule_suggestions
                    SET status = 'rolled_back', reviewed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (suggestion_id,),
                )
            cur = tx_conn.execute(
                "UPDATE flywheel_config_snapshots SET is_active = 0 WHERE id = ? AND is_active = 1",
                (snap_id,),
            )
            if cur.rowcount != 1:
                tx_conn.rollback()
                try:
                    atomic_write_json_config(target_path, config_after)
                except Exception as comp_exc:
                    block_rule_publish_recovery(
                        f"回滚 CAS 冲突且配置还原失败: {comp_exc}",
                        details={"snapshot_version": snap_ver, "target_path": target_path},
                    )
                    raise HTTPException(status_code=500, detail=f"回滚并发冲突且配置还原失败，已锁定后续发布: {comp_exc}")
                raise HTTPException(status_code=409, detail="并发冲突：快照已被其他操作回滚。")
            tx_conn.commit()
        except HTTPException:
            raise
        except Exception as e:
            try:
                tx_conn.rollback()
            except Exception:
                pass
            try:
                atomic_write_json_config(target_path, config_after)
            except Exception as ce:
                logger.critical(f"Compensating file restore failed: {ce}")
                block_rule_publish_recovery(
                    f"回滚 DB 事务失败且配置还原失败: {ce}",
                    details={"snapshot_version": snap_ver, "target_path": target_path, "db_error": str(e)},
                )
                raise HTTPException(status_code=500, detail=f"数据库回滚失败且配置还原失败，已锁定后续发布: {ce}")
            raise HTTPException(status_code=500, detail=f"数据库回滚记录失败，已复原配置: {e}")
        finally:
            tx_conn.close()

        try:
            trigger_all_config_reloads(raise_on_error=True)
        except Exception as e:
            logger.error(f"Error during trigger_all_config_reloads after rollback: {e}")
            block_rule_publish_recovery(
                f"回滚后配置热重载失败: {e}",
                details={"snapshot_version": snap_ver, "target_path": target_path},
            )
            raise HTTPException(status_code=500, detail=f"回滚后配置热重载失败，已阻断后续规则发布: {e}")

        log_audit_event(
            username=operator_id,
            action="飞轮规则回滚",
            details=f"回滚快照: {snap_ver} (目标: {target_type})",
        )
        return {"code": 0, "msg": f"快照 {snap_ver} 已成功回滚并恢复配置"}

@app.post("/api/flywheel/miner/run", dependencies=[Depends(require_admin)])
@http_job(mining_jobs, timeout=300)
def api_flywheel_run_miner():
    """Trigger manual mining run."""
    from app.feedback_miner import miner
    try:
        res = miner.run_mining()
        return {"code": 0, "result": res, "msg": "数据挖掘与规则聚类完成"}
    except JobStopped:
        raise
    except Exception as exc:
        logger.error("Manual flywheel mining failed (%s): %s", type(exc).__name__, exc)
        raise HTTPException(status_code=500, detail=f"数据挖掘执行失败: {type(exc).__name__}")

@app.get("/api/flywheel/export/benchmark", dependencies=[Depends(require_admin)])
@http_job(export_jobs, timeout=120)
def api_flywheel_export_benchmark():
    """Export golden evaluation set with flywheel_auto isolated cases (excluding legacy/untrusted and already matched aliases)."""
    from app.db_manager import get_db_cursor
    from app.flywheel_db import init_flywheel_tables, trusted_flywheel_where_clause
    from app.normalizer import alias_dict
    from app.rule_governance import load_scoped_aliases
    from app.rule_context import canonicalize_professional
    scoped_aliases = load_scoped_aliases()
    init_flywheel_tables()
    trusted_eff = trusted_flywheel_where_clause()
    with get_db_cursor() as cur:
        cur.execute(
            f"""
        SELECT raw_name, raw_spec, final_targets_json, action_type, discipline
        FROM flywheel_records
        WHERE action_type IN ('CONFIRM_TOP1', 'MANUAL_REPLACE')
          AND {trusted_eff}
        LIMIT 500
        """
        )
        rows = cur.fetchall()

    items = []
    for r in rows:
        targets = json.loads(r["final_targets_json"] or "[]")
        t_code = targets[0].get("code", "") if targets else ""
        t_name = targets[0].get("name", "") if targets else ""

        # P1-3: Prevent leakage: skip items where raw_name is already in alias_dict and action was CONFIRM_TOP1
        scoped = scoped_aliases.get(canonicalize_professional(r["discipline"] or ""), {})
        if (r["raw_name"] in alias_dict or any(source.lower() in r["raw_name"].lower() for source in scoped)) and r["action_type"] == "CONFIRM_TOP1":
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
            "professional": r["discipline"] or "通用",
            "ambiguous": False
        })
    return JSONResponse(content={"items": items, "count": len(items)})

@app.get("/api/flywheel/export/triplets", dependencies=[Depends(require_admin)])
@http_job(export_jobs, timeout=120)
def api_flywheel_export_triplets():
    """Export BGE contrastive triplets (Anchor, Positive, Hard Negative) from trusted effective records only."""
    from app.db_manager import get_db_cursor
    from app.flywheel_db import init_flywheel_tables, trusted_flywheel_where_clause
    init_flywheel_tables()
    trusted_eff = trusted_flywheel_where_clause()
    with get_db_cursor() as cur:
        cur.execute(
            f"""
        SELECT raw_name, raw_spec, auto_top1_name, final_targets_json
        FROM flywheel_records
        WHERE action_type = 'MANUAL_REPLACE'
          AND auto_top1_name != ''
          AND {trusted_eff}
        LIMIT 500
        """
        )
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

@app.post("/api/feedback/submit")
def api_submit_feedback(req: FeedbackSubmitRequest, current_user: dict = Depends(require_employee)):
    """Submit verified, idempotent human calibration logs (no unauthenticated/unidentified bypass)."""
    from app.feedback_train import save_feedback_sample
    try:
        return save_feedback_sample(req.model_dump(), actor_user=current_user)
    except FeedbackContractError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class BindingRowRequest(SourceReviewFields):
    original_name: str
    original_spec: str
    original_qty: float
    # V015-M2：人工确认范围与来源行内容（用于服务端权威确认记录与行指纹）。
    # 缺省视为旧客户端，行内容以本次提交为准，导出端仍逐项复核指纹。
    professional: Optional[str] = "暖通"
    scope_filename: Optional[str] = ""
    original_unit: Optional[str] = ""
    original_loss: Optional[float] = 0.0
    original_category: Optional[str] = ""
    original_brand: Optional[str] = ""
    original_code: Optional[str] = ""
    original_row_idx: Optional[int] = 0

    def row(self):
        row = self.model_dump(exclude={"scope_filename", "bindings"})
        for key in ("original_spec", "original_unit", "original_category", "original_brand", "original_code"):
            row[key] = row[key] or ""
        row["original_loss"] = row["original_loss"] or 0.0
        row["original_row_idx"] = row["original_row_idx"] or 0
        return {**row, "professional": self.professional or "暖通"}


class BindingSubmitRequest(BindingRowRequest):
    bindings: List[BindingModel]


class PriorityReviewRequest(BaseModel):
    rows: List[BindingSubmitRequest] = Field(min_length=1, max_length=200)


class PriorityConfirmRow(BindingSubmitRequest):
    idempotency_key: str = Field(min_length=64, max_length=64)
    reviewed_missing: bool = False


class PriorityConfirmRequest(BaseModel):
    batch_request_id: str = Field(min_length=1, max_length=128)
    rows: List[PriorityConfirmRow] = Field(min_length=1, max_length=200)

class BindingRuleConfirmRequest(BaseModel):
    original_name: str
    bindings: List[BindingModel]
    original_spec: Optional[str] = ""
    professional: Optional[str] = ""
    eval_id: Optional[str] = None
    neutral_reason: Optional[str] = None

class BindingRuleDeleteRequest(BaseModel):
    original_name: str
    original_spec: Optional[str] = None
    professional: Optional[str] = None


def _with_live_binding_fields(bindings, live):
    fields = ("name", "specification", "unit", "brand", "category")
    canonical = []
    for binding in bindings:
        code = str(binding["code"]).strip()
        canonical.append({**binding, "code": code, **{key: live[code].get(key) or "" for key in fields}})
    return canonical


@app.post("/api/binding/priority", dependencies=[Depends(require_employee)])
@catalog_read
def api_priority_review(req: PriorityReviewRequest, current_user: dict = Depends(get_current_user)):
    results = []
    with closing(open_flywheel_tx_connection()) as materials, closing(get_auth_connection()) as auth:
        for entry in req.rows:
            row = entry.row()
            try:
                ctx = _binding_source(materials, current_user, row, entry.scope_filename)
                if not ctx:
                    raise HTTPException(422, {"error": "source_identity_required", "message": "须提供已验证源行。"})
                binds = normalize_bindings([b.model_dump() for b in entry.bindings], row["original_qty"])
                result = priority_eligibility(ctx, source_decision(auth, ctx), row, binds)
            except HTTPException as exc:
                result = {"eligible": False, "reason": exc.detail["error"]}
            results.append({"source_row_id": entry.source_row_id, **result})
    return {"code": 0, "rows": results}


@app.post("/api/binding/priority/submit", dependencies=[Depends(require_employee)])
@catalog_read
def api_submit_priority_review(req: PriorityConfirmRequest, current_user: dict = Depends(get_current_user)):
    results = []
    with source_write() as auth, closing(open_flywheel_tx_connection()) as materials:
        for entry in req.rows:
            try:
                result = _submit_priority_row(auth, materials, current_user, req.batch_request_id, entry)
            except (HTTPException, FeedbackContractError) as exc:
                result = {"ok": False, "reason": exc.detail["error"], "message": exc.detail.get("message")}
            results.append({"source_row_id": entry.source_row_id, **result})
    return {"code": 0, "rows": results, "feedback_delivery_ok": deliver_priority_feedback()}


def _submit_priority_row(auth, materials, user, batch_id, entry):
    row = entry.row()
    ctx = _binding_source(materials, user, row, entry.scope_filename)
    if not ctx:
        raise HTTPException(422, {"error": "source_identity_required", "message": "须提供已验证源行。"})
    binds = normalize_bindings([b.model_dump() for b in entry.bindings], row["original_qty"])
    record = source_decision(auth, ctx)
    previous = receipt_for(auth, ctx, entry.idempotency_key)
    if previous:
        receipt = json.loads(previous["receipt_json"])
        submitted_ctx = {**ctx, "revision": row.get("recommendation_revision")}
        if previous["payload_digest"] != priority_key(submitted_ctx, row, binds):
            return {"ok": False, "reason": "idempotency_conflict"}
        active = (
            source_version(record) == receipt["decision_version"]
            and ctx["revision"] == receipt["recommendation_revision"]
            and confirmed_source(record, ctx, row, receipt["bindings"])
            and validate_bindings_against_row(
                row, binds, saved_bindings=extract_saved_bindings(record), require_saved_unit_evidence=True,
            )["ok"]
        )
        return {"ok": bool(active), "replayed": True, "reason": "confirmed" if active else "confirmation_inactive",
                "receipt": receipt}
    check_source_write(ctx, record, row)
    eligibility = priority_eligibility(ctx, record, row, binds)
    if not eligibility["eligible"]:
        return {"ok": False, "reason": eligibility["reason"]}
    if entry.idempotency_key != eligibility["idempotency_key"]:
        return {"ok": False, "reason": "idempotency_conflict"}
    if eligibility["missing"] and not entry.reviewed_missing:
        return {"ok": False, "reason": "missing_review_required"}
    live = fetch_live_material_map([binds[0]["code"]])
    canonical = _with_live_binding_fields(binds, live)
    reason = "优先核对逐行勾选确认"
    version = write_source_decision(auth, ctx, record, row, canonical, STATUS_MANUAL_CONFIRMED,
                                    SOURCE_PRIORITY_REVIEW, reason)
    result = {
        "bindings": canonical, **version,
        "decision": {
            **_annotate_decision(row, canonical, STATUS_MANUAL_CONFIRMED, SOURCE_PRIORITY_REVIEW, reason),
            **version,
        },
    }
    receipt = commit_priority(auth, ctx, row, binds, canonical, entry.idempotency_key, batch_id, result)
    return {"ok": True, "reason": "confirmed", "receipt": receipt}


@app.post("/api/binding/invalidate", dependencies=[Depends(require_employee)])
@catalog_read
def api_invalidate_binding(req: BindingRowRequest, current_user: dict = Depends(get_current_user)):
    row = req.row()
    with source_write() as auth, closing(open_flywheel_tx_connection()) as materials:
        ctx = _binding_source(materials, current_user, row, req.scope_filename)
        if ctx:
            record = source_decision(auth, ctx)
            try:
                check_source_write(ctx, record, row, editing=True)
            except FeedbackContractError as exc:
                raise HTTPException(exc.status_code, exc.detail) from exc
            return {"code": 0, **invalidate_source(auth, ctx, record, row)}
    invalidate_decision(current_user["id"], make_scope_key(req.scope_filename, row["professional"]), row)
    return {"code": 0}


def _record_binding_review(req, current_user, row, binds, decision, source, reason):
    with source_write() as auth, closing(open_flywheel_tx_connection()) as materials:
        ctx = _binding_source(materials, current_user, row, req.scope_filename)
        if ctx:
            record = source_decision(auth, ctx)
            try:
                check_source_write(ctx, record, row)
            except FeedbackContractError as exc:
                raise HTTPException(exc.status_code, exc.detail) from exc
            return write_source_decision(auth, ctx, record, row, binds, decision, source, reason)
    return record_decision(
        current_user["id"], make_scope_key(req.scope_filename, row["professional"]),
        row, binds, decision, source, reason,
    )


@app.post("/api/binding/submit", dependencies=[Depends(require_employee)])
@catalog_read
def api_submit_binding(
    req: BindingSubmitRequest,
    current_user: dict = Depends(get_current_user),
):
    """V015-M2：显式人工确认绑定。

    服务端权威确认：重新核验目标编码当前有效性与行级硬冲突，任一项不通过
    拒绝确认；通过后写入 binding_decisions（manual_confirmed，含行/绑定双指纹），
    返回服务端决策凭据。旧反馈日志继续保留用于学习，但不再是可导出凭据。
    客户端自填 is_exact/confirmed/accepted 一律不被采信。
    """
    binds = []
    for b in req.bindings:
        if not (b.code or "").strip():
            raise HTTPException(status_code=400, detail="绑定编码不能为空")
        binds.append({
            "code": b.code.strip(),
            "name": b.name or "",
            "specification": b.specification or "",
            "unit": b.unit or "",
            "brand": b.brand or "",
            "category": b.category or "",
            "qty": float(b.qty or 0.0),
            "qty_ratio": b.qty_ratio,
        })
    if not binds:
        raise HTTPException(status_code=400, detail="至少需要一个绑定编码才能确认")

    row_item = req.row()
    binds = normalize_bindings(binds, req.original_qty)
    bom = _current_bom({**row_item, "professional": req.professional
                        if "professional" in req.model_fields_set else None})
    if bom is not None:
        issue = bom.mismatch(binds)
        if issue:
            raise HTTPException(status_code=409, detail={"error": "binding_bom_invalid", "message": issue})
    # 新鲜编码 + 行级硬冲突核验：硬排除不得靠人工/伪造状态绕过。
    validation = validate_bindings_against_row(row_item, binds, component_shares=bom.shares if bom else None)
    if validation["invalid_codes"]:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "binding_invalid_codes",
                "message": "绑定编码已失效或不存在，请重新选择当前有效物料",
                "invalid_codes": validation["invalid_codes"],
            },
        )
    if validation["conflicts"]:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "binding_hard_conflict",
                "message": "绑定目标与清单行存在硬冲突，不允许人工确认",
                "conflicts": validation["conflicts"],
            },
        )

    if any(
        not math.isfinite(b["qty"]) or not positive_quantity(b["qty_ratio"])
        or (req.original_qty > 0 and not math.isclose(
            b["qty"], req.original_qty * b["qty_ratio"], rel_tol=1e-9, abs_tol=1e-9,
        ))
        for b in binds
    ):
        raise HTTPException(status_code=409, detail="绑定数量与比例不一致")

    binds = bom.bindings if bom is not None else _with_live_binding_fields(binds, validation["live_materials"])
    record = _record_binding_review(
        req, current_user, row_item, binds, STATUS_MANUAL_CONFIRMED, SOURCE_MANUAL, "人工显式确认",
    )

    # 反馈学习日志（历史功能保留，非可导出凭据；失败不影响权威确认结果）
    try:
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
            "bindings": binds,
        })
        with open(BINDING_FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(samples, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.warning(f"binding feedback log write failed (non-fatal): {e}")

    return {
        "code": 0,
        "msg": "绑定关系已由服务端确认",
        "bindings": binds,
        "decision": {
            **_annotate_decision(row_item, binds, STATUS_MANUAL_CONFIRMED, SOURCE_MANUAL, "人工显式确认"),
            "binding_fingerprint": record["binding_fingerprint"],
        },
        "row_fingerprint": record["row_fingerprint"],
        "binding_fingerprint": record["binding_fingerprint"],
        "decision_version": record.get("decision_version"),
    }


@app.post("/api/binding/reject", dependencies=[Depends(require_employee)])
@catalog_read
def api_reject_binding(
    req: BindingSubmitRequest,
    current_user: dict = Depends(get_current_user),
):
    """V015-M2：人工拒绝当前推荐，显式置为不可导出，等待重新选材。"""
    row_item = req.row()
    binds = normalize_bindings([b.model_dump() for b in req.bindings], req.original_qty)
    record = _record_binding_review(
        req, current_user, row_item, binds, STATUS_REJECTED, SOURCE_MANUAL_REJECT, "人工拒绝推荐",
    )
    return {
        "code": 0,
        "msg": "已拒绝当前推荐",
        "decision_version": record.get("decision_version"),
        "decision": {
            **_annotate_decision(row_item, binds, STATUS_REJECTED, SOURCE_MANUAL_REJECT, "人工拒绝推荐"),
            "binding_fingerprint": record["binding_fingerprint"],
        },
    }

@app.get("/api/binding/suggestions", dependencies=[Depends(require_employee)])
def api_get_binding_suggestions():
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

@app.post("/api/binding/rules/eval")
@http_job(rule_jobs, timeout=120)
def api_eval_binding_rule(req: BindingRuleConfirmRequest, current_user: dict = Depends(require_admin)):
    """Admin-only M2 sandbox evaluation for a global auto-binding rule before confirm/publish."""
    from app.rule_governance import EvalGateError

    try:
        bindings_list = [
            {
                "code": b.code,
                "name": b.name,
                "specification": b.specification,
                "unit": b.unit,
                "brand": b.brand,
                "category": b.category,
                "qty_ratio": b.qty_ratio if b.qty_ratio is not None else 1.0,
            }
            for b in req.bindings
        ]
        return start_binding_rule_evaluation(
            req.original_name,
            bindings_list,
            current_user,
            original_spec=req.original_spec or "",
            professional=req.professional or "",
        )
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except EvalGateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail())
    except Exception as e:
        logger.error(f"Error in api_eval_binding_rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/binding/rules/eval/{eval_id}")
def api_get_binding_rule_eval_status(eval_id: str, current_user: dict = Depends(require_admin)):
    """Retrieve sandbox evaluation status for a legacy auto-binding rule."""
    from app.flywheel_sandbox import get_eval_task_status_for_user

    st = get_eval_task_status_for_user(eval_id, current_user)
    if not st:
        raise HTTPException(status_code=404, detail="指定绑定规则评测任务不存在或无权访问")
    return JSONResponse(content=st)

@app.post("/api/binding/rules/confirm")
@http_job(rule_jobs, timeout=120)
def api_confirm_binding_rule(req: BindingRuleConfirmRequest, current_user: dict = Depends(require_admin)):
    """Admin-only confirmation/activation of a global auto-binding rule with mandatory M2 eval gate."""
    from app.rule_governance import EvalGateError

    try:
        bindings_list = [
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
        return confirm_binding_rule_service(
            req.original_name,
            bindings_list,
            current_user,
            original_spec=req.original_spec or "",
            professional=req.professional or "",
            eval_id=req.eval_id,
            neutral_reason=req.neutral_reason,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except EvalGateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail())
    except Exception as e:
        logger.error(f"Error in api_confirm_binding_rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/binding/rules/delete")
def api_delete_binding_rule(req: BindingRuleDeleteRequest, current_user: dict = Depends(require_admin)):
    """Admin-only deletion of a global confirmed auto-binding rule."""
    from app.rule_governance import EvalGateError

    try:
        return delete_binding_rule_service(
            req.original_name,
            current_user,
            original_spec=req.original_spec,
            professional=req.professional,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except EvalGateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail())
    except Exception as e:
        logger.error(f"Error in api_delete_binding_rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/binding/rules", dependencies=[Depends(require_employee)])
def api_get_binding_rules():
    """Retrieve all confirmed auto-binding rules."""
    try:
        rules = load_auto_binding_rules()
        return JSONResponse(content={"rules": rules})
    except Exception as e:
        logger.error(f"Error in api_get_binding_rules: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/search", dependencies=[Depends(require_employee)])
def search_materials(keyword: str = "", limit: int = 50):
    """Search database for materials by keyword."""
    results = search_materials_keyword(keyword, limit=limit)
    return JSONResponse(content={"results": results})

# ---------------------------------------------------------------------------
# V015-M2：导出后端门禁（fail-closed）。
# 每行必须能被服务端独立推导为可导出状态：人工确认以 binding_decisions 记录
# + 行/绑定双指纹 + 新鲜编码 + 硬冲突复核为准；自动接受以当前规则 + 约束逐项
# 匹配 + 新鲜编码 + 硬冲突复核为准。客户端自报 decision_status/is_exact/
# matched_code/bindings 均不可信；任何一行不满足即整批拒绝并给出行级原因。
# ---------------------------------------------------------------------------
def _export_row_bindings(item: dict) -> List[dict]:
    """导出实际生效的绑定集合（一对多优先，单条回退），与 report_generator 一致。"""
    binds = item.get("bindings") or []
    if binds:
        return normalize_bindings(binds, item.get("original_qty"))
    code = (item.get("matched_code") or "").strip()
    if not code:
        return []
    codes = [c.strip() for c in code.split(",") if c.strip()]
    return normalize_bindings(
        [{"code": c, "qty": float(item.get("original_qty") or 0.0)} for c in codes],
        item.get("original_qty"),
    )


@catalog_read
def _gate_export_row(item: dict, current_user: dict, request_scope: str, *,
                     allow_calibrated=True, document_id=None, materials=None) -> Optional[List[Dict[str, Any]]]:
    """对单行执行导出可导出性核验；不可导出时抛 HTTPException(422) 并带行级原因。"""
    row_idx = item.get("original_row_idx")
    name = item.get("original_name", "")
    spec = item.get("original_spec", "")
    professional = item.get("professional") or "暖通"
    scope_key = make_scope_key(request_scope or "", professional)
    binds = _export_row_bindings(item)

    def deny(reason: str, **extra):
        detail = {
            "error": "export_blocked",
            "row": {
                "original_row_idx": row_idx,
                "original_name": name,
                "original_spec": spec,
                "reason": reason,
                **extra,
            },
        }
        raise HTTPException(status_code=422, detail=detail)

    try:
        if materials is None:
            with closing(open_flywheel_tx_connection()) as lookup:
                ctx = _binding_source(lookup, current_user, item, request_scope, document_id)
        else:
            ctx = _binding_source(materials, current_user, item, request_scope, document_id)
        if ctx:
            check_revision(ctx, item)
    except (HTTPException, FeedbackContractError) as exc:
        if exc.status_code == 404:
            raise
        deny(exc.detail["message"])
    if ctx:
        with closing(get_auth_connection()) as auth:
            row_decision = source_decision(auth, ctx)
    else:
        row_decision = get_row_decision(user_id=current_user["id"], scope_key=scope_key, row=item)
    if row_decision and row_decision["decision"] == STATUS_REJECTED:
        deny("人工拒绝：须重新选材并显式确认后方可导出")
    if row_decision and row_decision["source"] == SOURCE_MANUAL_EDIT:
        deny("内容已修改，请重新确认")
    if not binds:
        deny("无有效绑定：未匹配或已拒绝，禁止导出（不默选 top1）")
    bom = _current_bom(item)
    if bom is not None:
        issue = bom.mismatch(binds)
        if issue:
            deny(issue)

    # 1) 人工确认：服务端确认记录 + 行/绑定双指纹一致 + 编码当前有效 + 无硬冲突
    record = (
        row_decision if confirmed_source(row_decision, ctx, item, binds) else None
    ) if ctx else get_decision(user_id=current_user["id"], scope_key=scope_key, row=item, bindings=binds)
    if record:
        validation = validate_bindings_against_row(
            item,
            binds,
            saved_bindings=extract_saved_bindings(record),
            require_saved_unit_evidence=True,
            component_shares=bom.shares if bom else None,
        )
        if validation["invalid_codes"]:
            deny("确认后物料编码已失效", invalid_codes=validation["invalid_codes"])
        if validation["conflicts"]:
            deny("绑定目标与清单行存在硬冲突", conflicts=validation["conflicts"])
        # Quantities come from the confirmed snapshot when it is available.
        approved = extract_saved_bindings(record)
        quantities = {binding_fingerprint([b]): b for b in approved or []}
        checked = []
        for binding in binds:
            saved = quantities.get(binding_fingerprint([binding])) or {}
            checked.append({**binding, **{key: saved[key] for key in ("qty", "qty_ratio") if key in saved}})
        return _with_live_binding_fields(checked, validation["live_materials"])

    if ctx and (row_decision or row_content_fingerprint(item) != ctx["original_fingerprint"] or ctx["revision"] != 1):
        deny("当前源行或绑定已变化，请重新确认")

    if bom is not None:
        if row_decision:
            deny("BOM 绑定与服务端确认不一致，须重新确认")
        return bom.bindings

    # 2) 自动接受：仅带约束且逐项匹配的规则，且编码当前有效、无硬冲突。
    #    名称-only 旧规则不进入自动接受（DN100 不得因 DN15 同名规则被接受）。
    matched_rule = _select_matching_binding_rule(item, load_auto_binding_rules())
    if matched_rule is not None:
        raw = _raw_item_from_export(item)
        rule_binds = _rule_expected_bindings(raw, matched_rule)
        decision = _evaluate_rule_decision(raw, matched_rule, rule_binds)
        if decision["decision_status"] == STATUS_AUTO_ACCEPTED:
            if binding_fingerprint(binds) != binding_fingerprint(rule_binds):
                deny("自动接受绑定与当前规则数量/比例不一致，须重新人工确认")
            live = fetch_live_material_map([b.get("code", "") for b in binds])
            for b in binds:
                code = str(b.get("code", "") or "").strip()
                mat = live.get(code)
                live_unit = str((mat or {}).get("unit", "") or "").strip()
                req_unit = str(b.get("unit", "") or "").strip()
                if live_unit:
                    if not req_unit:
                        deny("自动接受绑定缺失目标单位，须按当前物料单位导出")
                    u_mult = get_unit_multiplier(req_unit, live_unit)
                    if u_mult is None or abs(u_mult - 1.0) > 1e-6:
                        deny("自动接受绑定目标单位与当前物料单位不一致，须重新人工确认")
            return _with_live_binding_fields(rule_binds, live)

    policy = acceptance_policy()
    if (policy is not None and allow_calibrated and not row_decision
            and matched_rule is None and item.get("bindings")):
        _, live = _revalidate_matcher_binding(item, binds, policy)
        if live:
            return live

    # 3) 其余（推荐/旧草稿/伪造状态/确认后修改致指纹失配）一律拒绝
    deny("未确认：须显式人工确认或满足带约束的自动接受规则后方可导出")


@app.post("/api/export", dependencies=[Depends(require_employee)])
@http_job(export_jobs, timeout=300)
def export_report(req: ExportRequest, current_user: dict = Depends(require_employee)):
    """Export final verified matched items back to standard report template."""
    logger.info(f"Exporting matched items list as standard Excel report: {len(req.items)} rows by {current_user.get('username')}.")
    # V015-M2：导出前逐行 fail-closed 核验，任一不合规即整批 422（不默选 top1）。
    request_scope = req.scope_filename or ""
    items_to_write = []
    with closing(open_flywheel_tx_connection()) as materials:
        for it in req.items:
            checkpoint()
            item = it.model_dump()
            if is_bom_name(item["original_name"]) and "professional" not in it.model_fields_set:
                item["professional"] = None
            live = _gate_export_row(item, current_user, request_scope, document_id=req.document_id,
                                    allow_calibrated="professional" in it.model_fields_set, materials=materials)
            if live:
                item["bindings"] = live
            items_to_write.append(item)
    try:
        output_filename = f"标准格式_{uuid.uuid4().hex[:8]}.xlsx"
        output_path = os.path.join(UPLOAD_DIR, output_filename)

        with active_path(output_path):
            generate_standard_report(items_to_write, None, output_path)
        checkpoint()

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
        return ManagedFileResponse(
            path=output_path,
            filename=output_filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except JobStopped:
        raise
    except RuntimeAssetError as exc:
        raise HTTPException(status_code=500, detail={"code": exc.code, "message": str(exc)}) from exc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating standard report: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating standard report: {str(e)}")

@app.get("/api/config/load", dependencies=[Depends(require_manager)])
def api_load_all_configs():
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
def api_get_feedback_suggestions():
    """Get alias/typo correction recommendations from user corrections."""
    from app.feedback_train import get_feedback_suggestions
    suggestions = get_feedback_suggestions()
    return JSONResponse(content={"suggestions": suggestions})

@app.get("/api/logs", dependencies=[Depends(require_director)])
def api_get_logs(limit: int = 100):
    """Retrieve the last N lines of system execution logs."""
    log_path = os.path.join(LOG_DIR, "app_runtime.log")
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
def api_db_metadata():
    """Get metadata for the current global material database."""
    from app.db_manager import get_db_metadata
    try:
        meta = get_db_metadata()
        return JSONResponse(content=meta)
    except Exception as e:
        logger.error(f"Error querying db metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def is_recovery_blocked() -> bool:
    """Return True when any layer (DB, memory, or disk cache) failed rollback and
    has not yet passed verified cross-layer consistency reconciliation."""
    import importlib

    db_manager = importlib.import_module("app.db_manager")
    vector_index = importlib.import_module("app.vector_index")

    return db_manager.is_db_write_blocked() or vector_index.is_cache_write_blocked()


def get_recovery_block_status() -> Dict[str, Any]:
    import importlib

    db_manager = importlib.import_module("app.db_manager")
    vector_index = importlib.import_module("app.vector_index")

    db_info = db_manager.get_db_recovery_block_info()
    cache_info = vector_index.get_cache_recovery_block_info()
    blocked = bool(db_info or cache_info)
    reasons = [
        info["reason"]
        for info in (db_info, cache_info)
        if isinstance(info, dict) and info.get("reason")
    ]
    return {
        "blocked": blocked,
        "reason": " | ".join(reasons) if reasons else None,
        "db_block": db_info,
        "cache_block": cache_info,
    }


class RecoveryAwareDbImportLockManager(DbImportLockManager):
    """Import lock manager that refuses new import tokens while recovery is blocked."""

    def try_acquire(self) -> Optional[str]:
        if is_recovery_blocked():
            return None
        return super().try_acquire()


def _new_db_import_lock_manager() -> RecoveryAwareDbImportLockManager:
    return RecoveryAwareDbImportLockManager(_db_import_lock)


_db_import_lock = threading.Lock()
_db_import_lock_manager = _new_db_import_lock_manager()
_DB_IMPORT_TASK_TIMEOUT_SECONDS = 600.0
# R03 A-M5-02: once the worker has won the commit gate (staging -> committing), the
# publication chain is a short, local and deterministic sequence (one EXCLUSIVE SQLite
# transaction, one in-lock memory swap, local file promotions). The supervisor waits for
# its unique terminal outcome in bounded slices instead of racing it with a late "failed"
# write; this is a settlement slice, not an extension or removal of the staging timeout.
_DB_IMPORT_COMMIT_SETTLE_SLICE_SECONDS = 0.5


class ImportCommitArbiter:
    """Single arbitration point shared by the db-import worker thread and its supervisor.

    State machine:
        staging -> cancelled                             (timeout/cancel wins before the gate)
        staging -> committing -> committed               (every layer published)
                                   -> rolled_back        (a publication layer failed and all
                                                          touched layers rolled back cleanly)
                                   -> recovery_failed    (any layer failed rollback; persistent
                                                          write block engaged until verified)

    Every transition is CAS-guarded by one Condition so the supervisor can never turn a
    real committed outcome into failed/cancelled, and a late timeout can never revoke a
    commit gate the worker has already won.
    """

    STAGING = "staging"
    CANCELLED = "cancelled"
    COMMITTING = "committing"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    RECOVERY_FAILED = "recovery_failed"
    TERMINAL_STATES = frozenset((CANCELLED, COMMITTED, ROLLED_BACK, RECOVERY_FAILED))

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._state = self.STAGING
        self._terminal = threading.Event()
        self.terminal_reason: Optional[str] = None
        self.recovery_details: Optional[Dict[str, Any]] = None

    @property
    def state(self) -> str:
        with self._cond:
            return self._state

    def request_cancel(self) -> bool:
        """Supervisor timeout/exception entry. Cancel wins only while still staging.

        Returns True when cancellation is accepted (the worker's later begin_commit()
        is refused and no live layer is touched); False once the worker has entered
        committing, in which case the supervisor must wait for the unique outcome.
        """
        with self._cond:
            if self._state == self.STAGING:
                self._state = self.CANCELLED
                self._terminal.set()
                self._cond.notify_all()
                return True
            return False

    def begin_commit(self) -> bool:
        """Worker gate, called immediately before the DB swap. Returns False if the
        supervisor already won cancellation, so the worker aborts before any live mutation."""
        with self._cond:
            if self._state == self.STAGING:
                self._state = self.COMMITTING
                self._cond.notify_all()
                return True
            return False

    def finish_commit(self) -> None:
        with self._cond:
            if self._state == self.COMMITTING:
                self._state = self.COMMITTED
                self._terminal.set()
                self._cond.notify_all()

    def fail_commit(self, reason: Optional[str] = None) -> None:
        with self._cond:
            if self._state == self.COMMITTING:
                self._state = self.ROLLED_BACK
                self.terminal_reason = reason
                self._terminal.set()
                self._cond.notify_all()

    def fail_recovery(
        self,
        reason: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._cond:
            if self._state == self.COMMITTING:
                self._state = self.RECOVERY_FAILED
                self.terminal_reason = reason
                self.recovery_details = details
                self._terminal.set()
                self._cond.notify_all()

    def wait_terminal(self, timeout: Optional[float] = None) -> str:
        self._terminal.wait(timeout=timeout)
        with self._cond:
            return self._state


def _release_db_lock_if_held(lock_token: Optional[str]) -> None:
    if lock_token is not None:
        _db_import_lock_manager.release_for_token(lock_token)
    elif _db_import_lock.locked():
        try:
            _db_import_lock.release()
        except RuntimeError:
            pass


def async_process_db_upload_task(
    task_id: str,
    temp_path: str,
    mode: str,
    filename: str,
    disable_mode: str = "on_expire",
    lock_token: Optional[str] = None,
):
    if is_recovery_blocked():
        info = get_recovery_block_status()
        msg = (
            "恢复失败/需人工处置（系统处于写入阻断状态，禁止继续导入）："
            f"{info.get('reason') or '存在未核验的恢复失败'}"
        )
        logger.error(f"Rejected db upload task {task_id} under recovery write block: {msg}")
        update_task_progress(
            task_id,
            status="failed",
            recovery_failed=True,
            recovery_status="recovery_failed_manual_required",
            write_blocked=True,
            error=msg,
            current_step="写入阻断：恢复失败/需人工处置",
        )
        _release_db_lock_if_held(lock_token)
        remove_file_quietly(temp_path)
        return

    cancel_event = threading.Event()
    worker_result: Dict[str, Any] = {}
    worker_error: Optional[BaseException] = None
    arbiter = ImportCommitArbiter()

    @catalog_write
    def _run_worker():
        nonlocal worker_result, worker_error
        # R03/R04 A-M5-01/A-M5-02/A-M5-07: both replace and append stage every artifact
        # (DB tables, vector cache) in isolated *_stage / '.stage_*' destinations and
        # publish behind one arbitration gate; any recovery failure engages a persistent
        # write block and preserves recovery materials until verified reconciliation.
        stage_cache_dir: Optional[str] = None
        swap_published = False
        memory_published = False
        cache_promoted = False
        preserve_cache_recovery = False
        previous_meta: Optional[Dict[str, Any]] = None
        mem_snapshot = None
        publication_locked = False
        try:
            logger.info(f"Background task {task_id} started for db upload. Filename: {filename}, Mode: {mode}, DisableMode: {disable_mode}")
            invalidate_categories_cache()
            if not cancel_event.is_set():
                update_task_progress(task_id, current_step="正在解析并清洗物料数据...", structuring_progress=10)

            import importlib
            _db_mod = importlib.import_module("app.db_manager")
            _vi_mod = importlib.import_module("app.vector_index")
            from app.db_manager import (
                import_materials_from_excel,
                commit_staged_materials_import,
                get_staged_materials,
                get_db_metadata,
                reverse_retained_materials_swap,
                drop_retained_materials_old_generation,
                cleanup_staged_materials_import,
            )
            from app.vector_index import get_cache_dir, CacheRollbackError

            # Step 1 (replace AND append): parse and persist into materials_stage /
            # materials_fts_stage only. In append mode the staged generation already
            # holds the whole live generation plus this batch, so a successful
            # publication never drops pre-existing materials.
            stage_handle = import_materials_from_excel(
                temp_path,
                mode,
                filename,
                task_id=task_id,
                disable_mode=disable_mode,
                cancel_event=cancel_event,
                stage_only=True,
            )
            if cancel_event.is_set():
                raise ParseCancelledError("物料库导入任务在向量索引阶段前被取消或超时")

            # Step 2: read staged rows as the vector build input (live tables untouched).
            materials = get_staged_materials()

            update_task_progress(
                task_id,
                structuring_progress=100,
                current_step="物料数据结构化完成，开始重建向量索引...",
                vectorization_progress=10,
            )
            if cancel_event.is_set():
                raise ParseCancelledError("物料库导入任务在读取暂存物料后被取消或超时")

            update_task_progress(
                task_id,
                current_step=f"正在为 {len(materials)} 项物料生成向量特征并重建索引...",
                vectorization_progress=30,
            )

            # Step 3: shadow candidate build — no live-index or disk mutation.
            candidate = index_manager.build_index_candidate(
                materials, task_id=task_id, cancel_event=cancel_event,
                db_revision=stage_handle["db_revision"],
            )

            # Step 4: write candidate cache files into an isolated '.stage_*' directory.
            stage_cache_dir = os.path.join(
                get_cache_dir(),
                f".stage_{task_id}_{os.getpid()}_{threading.get_ident()}",
            )
            index_manager.write_candidate_cache_to_dir(stage_cache_dir, candidate)

            if cancel_event.is_set():
                raise ParseCancelledError("物料库导入任务在原子交换前被取消或超时")

            # R03 A-M5-02 single arbitration gate: after this point a late timeout or
            # cancellation cannot revoke publication; the supervisor defers to this
            # arbiter's unique terminal outcome instead of racing a conflicting status.
            if not arbiter.begin_commit():
                raise ParseCancelledError("物料库导入任务在原子交换前被取消或超时")

            # Capture every pre-publication layer at the same point as the gate so a
            # failure below can be restored to the exact verified pre-state.
            catalog_lock.acquire()
            publication_locked = True
            previous_meta = get_db_metadata()
            mem_snapshot = index_manager.snapshot_published_state()

            # Step 5: DB exclusive atomic swap (DDL + FTS + metadata in one transaction).
            # The real cancel_event is retained so the pre-swap and in-transaction cancel
            # checks stay effective; the old generation is retained for coordinated reversal.
            meta = commit_staged_materials_import(
                stage_handle,
                cancel_event=cancel_event,
                retain_old_generation=True,
            )
            swap_published = True

            # Step 6: publish the in-memory index. Inside the gate no late cancellation
            # may revoke the sequence (cancel_event=None); the method's own cancel_event
            # contract is preserved for its direct callers and contract tests.
            index_manager.publish_candidate(candidate, cancel_event=None)
            memory_published = True

            # Step 7: promote the staged cache generation as one reversible unit. A
            # failed promotion self-restores the backup; a failed restore engages the
            # persistent write block and preserves both directories as recovery material.
            index_manager.commit_candidate_cache(
                stage_cache_dir, finalize=drop_retained_materials_old_generation
            )
            cache_promoted = True
            stage_cache_dir = None
            arbiter.finish_commit()

            invalidate_categories_cache()
            logger.info("Vector index rebuilt and saved successfully.")
            worker_result = meta
        except BaseException as exc:
            worker_error = exc
            if arbiter.state == ImportCommitArbiter.COMMITTING:
                # The gate had been won: unwind every layer that actually published.
                # If ANY layer fails rollback (DB, memory, or cache), upgrade to
                # RECOVERY_FAILED, engage persistent write block on DB and cache, and
                # preserve all recovery materials on disk instead of claiming rolled_back.
                import importlib
                _db_mod = importlib.import_module("app.db_manager")
                _vi_mod = importlib.import_module("app.vector_index")
                from app.db_manager import (
                    reverse_retained_materials_swap,
                    cleanup_staged_materials_import,
                )
                from app.vector_index import get_cache_dir, CacheRollbackError

                layer_results: Dict[str, str] = {
                    "db": "untouched",
                    "memory": "untouched",
                    "cache": "untouched",
                }
                recovery_errors: Dict[str, str] = {}
                preserved_materials: Dict[str, Any] = {}

                try:
                    if swap_published:
                        reverse_retained_materials_swap(previous_meta or {})
                        layer_results["db"] = "rolled_back"
                    else:
                        cleanup_staged_materials_import()
                except BaseException as rollback_exc:
                    layer_results["db"] = "rollback_failed"
                    recovery_errors["db"] = repr(rollback_exc)
                    preserved_materials["db_path"] = os.path.abspath(_db_mod.DB_PATH)
                    preserved_materials["retained_old_tables"] = [
                        "materials_old",
                        "materials_fts_old",
                    ]
                    logger.critical(
                        f"DB generation rollback failed for task {task_id}; retained recovery "
                        f"material must be inspected manually: {rollback_exc!r}"
                    )

                try:
                    if memory_published and mem_snapshot is not None:
                        index_manager.rollback_published_state(mem_snapshot)
                        layer_results["memory"] = "rolled_back"
                except BaseException as rollback_exc:
                    layer_results["memory"] = "rollback_failed"
                    recovery_errors["memory"] = repr(rollback_exc)
                    logger.critical(
                        f"In-memory index rollback failed for task {task_id}: {rollback_exc!r}"
                    )

                if isinstance(exc, CacheRollbackError):
                    layer_results["cache"] = "rollback_failed"
                    recovery_errors["cache"] = str(exc)
                    preserve_cache_recovery = True
                    logger.critical(
                        f"Cache write block engaged for task {task_id}: {exc}"
                    )
                elif cache_promoted:
                    layer_results["cache"] = "already_promoted_post_step7"
                    recovery_errors["cache"] = (
                        f"post-promotion cleanup failed after Step 7 cache switch: {exc!r}"
                    )
                    preserve_cache_recovery = True
                elif stage_cache_dir is not None and swap_published and memory_published:
                    # Step 7 failed and self-restored its backup cleanly.
                    layer_results["cache"] = "rolled_back"

                if recovery_errors:
                    preserve_cache_recovery = True
                    cache_dir_abs = os.path.abspath(get_cache_dir())
                    preserved_materials.setdefault("db_path", os.path.abspath(_db_mod.DB_PATH))
                    preserved_materials["cache_dir"] = cache_dir_abs
                    if stage_cache_dir is not None:
                        preserved_materials["stage_cache_dir"] = os.path.abspath(stage_cache_dir)
                    failed_layers = ", ".join(sorted(recovery_errors.keys()))
                    block_reason = (
                        f"恢复失败/需人工处置（已进入写入阻断，保留恢复材料）: "
                        f"failed_layers=[{failed_layers}], "
                        f"layer_results={layer_results}, "
                        f"recovery_errors={recovery_errors}, "
                        f"trigger_error={exc}"
                    )
                    block_details = {
                        "task_id": task_id,
                        "mode": mode,
                        "filename": filename,
                        "layer_results": layer_results,
                        "recovery_errors": recovery_errors,
                        "preserved_materials": preserved_materials,
                        "trigger_error": repr(exc),
                    }
                    _db_mod.block_db_writes(block_reason, details=block_details)
                    _vi_mod.block_cache_writes(
                        block_reason, details=block_details, target_dir=cache_dir_abs
                    )
                    worker_error = RuntimeError(block_reason)
                    arbiter.fail_recovery(block_reason, details=block_details)
                else:
                    arbiter.fail_commit(str(exc))
            else:
                # Staging-phase failure (or a cancel that won before the gate): no live
                # layer was touched; discard staged DB tables so no partial artifact survives.
                try:
                    cleanup_staged_materials_import()
                except Exception as cleanup_exc:
                    logger.warning(f"Cleanup of staged materials tables failed: {cleanup_exc}")
            if stage_cache_dir is not None and not preserve_cache_recovery:
                shutil.rmtree(stage_cache_dir, ignore_errors=True)
        finally:
            if publication_locked:
                catalog_lock.release()
            remove_file_quietly(temp_path)
            # A-M5-02: the owner token is released only when the worker actually exits,
            # so a timed-out but still-running thread keeps the mutex and a second import
            # stays rejected until cancellation and cleanup have fully completed.
            _release_db_lock_if_held(lock_token)

    request_job = current_job()

    def run_with_active_path():
        with active_path(temp_path):
            _run_worker()

    worker_thread = threading.Thread(
        target=run_with_active_path,
        name=f"db-upload-{task_id[:8]}",
        daemon=True,
    )

    try:
        worker_thread.start()
        deadline = time.monotonic() + float(_DB_IMPORT_TASK_TIMEOUT_SECONDS)
        while worker_thread.is_alive():
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (request_job and request_job.cancelled.is_set()):
                break
            worker_thread.join(timeout=min(0.05, remaining))

        if worker_thread.is_alive():
            # R03 A-M5-02: timeout cancellation is arbitrated, not unconditional. It
            # only wins while the worker is still staging; once the commit gate has been
            # won the supervisor must defer to the worker's unique terminal outcome.
            cancel_won = arbiter.request_cancel()
            cancel_event.set()
            logger.error(
                f"Background db upload task {task_id} timed out after {_DB_IMPORT_TASK_TIMEOUT_SECONDS:.1f}s; "
                f"cancel signalled (arbiter={arbiter.state}), import mutex stays owned until the worker thread exits."
            )
            if cancel_won:
                update_task_progress(
                    task_id,
                    status="failed",
                    error=(
                        f"物料库导入及向量重建超时（超过 {_DB_IMPORT_TASK_TIMEOUT_SECONDS:.1f} 秒），已发出取消信号；"
                        "后台线程实际退出前导入锁保持占用，线程退出后自动释放。"
                    ),
                    current_step=f"导入超时（>{_DB_IMPORT_TASK_TIMEOUT_SECONDS:.1f}s），等待后台线程退出后释放锁",
                )
                return

            # The worker is inside the irrevocable committing sequence. Wait in bounded
            # slices for its unique terminal state; never fabricate a conflicting failed
            # or cancelled status — that race was the R02 split-brain root cause.
            while worker_thread.is_alive():
                arbiter.wait_terminal(timeout=_DB_IMPORT_COMMIT_SETTLE_SLICE_SECONDS)

        if arbiter.state == ImportCommitArbiter.COMMITTED:
            # A late timeout/cancel cannot overturn a publication every layer actually
            # completed: the committed outcome is the unique terminal truth.
            update_task_progress(
                task_id,
                vectorization_progress=100,
                matching_progress=100,
                status="completed",
                result=worker_result,
                current_step="标准物料数据库导入及向量索引重建全部完成！",
            )
            return

        if arbiter.state == ImportCommitArbiter.RECOVERY_FAILED:
            details = arbiter.recovery_details or {}
            msg = arbiter.terminal_reason or str(worker_error)
            logger.critical(f"Recovery failed during db upload task {task_id}: {msg}")
            update_task_progress(
                task_id,
                status="failed",
                recovery_failed=True,
                recovery_status="recovery_failed_manual_required",
                write_blocked=True,
                layer_results=details.get("layer_results"),
                recovery_errors=details.get("recovery_errors"),
                preserved_recovery_materials=details.get("preserved_materials"),
                error=msg,
                current_step="恢复失败/需人工处置（已进入写入阻断）",
            )
            return

        if arbiter.state == ImportCommitArbiter.ROLLED_BACK:
            logger.error(f"Error during async process db upload task (rolled back): {worker_error}")
            update_task_progress(
                task_id,
                status="failed",
                recovery_failed=False,
                recovery_status="rolled_back",
                write_blocked=False,
                error=str(worker_error),
                current_step=f"导入出错并已安全回滚: {str(worker_error)}",
            )
            return

        if worker_error is not None:
            logger.error(f"Error during async process db upload task: {worker_error}")
            update_task_progress(
                task_id,
                status="failed",
                error=str(worker_error),
                current_step=f"导入出错: {str(worker_error)}",
            )
            return

        if cancel_event.is_set():
            update_task_progress(
                task_id,
                status="failed",
                error="物料库导入任务已取消。",
                current_step="导入任务已取消",
            )
            return

        update_task_progress(
            task_id,
            vectorization_progress=100,
            matching_progress=100,
            status="completed",
            result=worker_result,
            current_step="标准物料数据库导入及向量索引重建全部完成！",
        )
    except Exception as e:
        cancel_event.set()
        logger.error(f"Unexpected supervisor error in async_process_db_upload_task: {e}")
        update_task_progress(task_id, status="failed", error=str(e), current_step=f"导入出错: {str(e)}")
    finally:
        if worker_thread.is_alive():
            # A-M5-02: the timed-out worker still owns the mutex; its own finally block
            # releases the owner token once cancellation and cleanup actually finish.
            logger.info(
                f"Supervisor of db upload task {task_id} exits while the worker is still alive; "
                "lock release deferred to the worker's own exit."
            )
        else:
            _release_db_lock_if_held(lock_token)
        if not worker_thread.is_alive():
            remove_file_quietly(temp_path)


@app.post("/api/db/upload")
async def api_db_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    mode: str = Form("replace"),  # "replace" or "append"
    disable_mode: str = Form("on_expire"),  # "on_expire" or "immediate"
    current_user: dict = Depends(require_director),
):
    """Upload and import materials to the global database, then rebuild FAISS index asynchronously."""
    if mode not in ("replace", "append"):
        raise HTTPException(status_code=400, detail="无效的导入模式（仅支持 replace 或 append）")
    if disable_mode not in ("on_expire", "immediate"):
        raise HTTPException(status_code=400, detail="无效的禁用生效策略（仅支持 on_expire 或 immediate）")

    try:
        ext = validate_extension(file.filename, frozenset({".xlsx", ".xls"}), context_label="物料库导入")
    except ExcelBoundaryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    if is_recovery_blocked():
        info = get_recovery_block_status()
        raise HTTPException(
            status_code=409,
            detail=f"恢复失败/需人工处置，系统处于写入阻断状态，请先核验并完成一致性恢复：{info.get('reason') or '存在未清除的恢复阻断'}",
        )

    lock_token = _db_import_lock_manager.try_acquire()
    if not lock_token:
        if is_recovery_blocked():
            info = get_recovery_block_status()
            raise HTTPException(
                status_code=409,
                detail=f"恢复失败/需人工处置，系统处于写入阻断状态：{info.get('reason') or '存在未清除的恢复阻断'}",
            )
        raise HTTPException(status_code=409, detail="当前已有物料库导入或索引重建任务正在运行中，请稍候再试。")

    temp_path: Optional[str] = None
    task_id = str(uuid.uuid4())
    job = None
    artifacts = ExitStack()
    scheduled = False

    try:
        safe_filename = os.path.basename((file.filename or f"materials{ext}").strip())
        if not safe_filename.lower().endswith(ext):
            safe_filename = f"{safe_filename}{ext}"
        temp_path = os.path.join(UPLOAD_DIR, f"db_{uuid.uuid4().hex}_{safe_filename}")

        job = _reserve_upload_job(catalog_jobs, 900, task_id)
        await job.wait(artifacts.enter_context, active_path(temp_path))
        max_bytes = MAX_UPLOAD_SIZE if MAX_UPLOAD_SIZE != DEFAULT_MAX_UPLOAD_BYTES else None
        await stream_save_upload_file(request, file, temp_path, max_bytes=max_bytes)

        await job.wait(_precheck_materials_upload, temp_path, file.filename)
        set_task_initial(task_id, {
            "status": "processing",
            "created_time": time.time(),
            "structuring_progress": 0,
            "vectorization_progress": 0,
            "matching_progress": 0,
            "current_step": "正在初始化后台物料库导入任务...",
            "result": None,
            "error": None
        }, owner_user=current_user)

        background_tasks.add_task(
            _finish_background_job, job, artifacts, async_process_db_upload_task,
            task_id,
            temp_path,
            mode,
            file.filename,
            disable_mode,
            lock_token=lock_token,
        )
        scheduled = True
        return JSONResponse(content={"task_id": task_id})
    except ExcelBoundaryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except JobStopped as exc:
        raise HTTPException(504, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        if task_id:
            try:
                update_task_progress(
                    task_id,
                    status="failed",
                    error=f"物料库后台任务调度失败: {exc}",
                    current_step=f"任务调度失败: {exc}",
                )
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"物料库导入启动失败: {exc}")
    finally:
        if not scheduled:
            remove_file_quietly(temp_path)
            artifacts.close()
            if job:
                job.release()
            _db_import_lock_manager.release_for_token(lock_token)

from pydantic import BaseModel, Field, StrictInt
from typing import Optional, List

class ReparseRequest(BaseModel):
    category: Optional[str] = None
    codes: Optional[List[str]] = None

@app.get("/api/db/schema/stats", dependencies=[Depends(require_manager)])
def api_db_schema_stats():
    """Get structural metrics and field extraction stats for each domain."""
    from app.db_manager import get_db_schema_stats
    try:
        stats = get_db_schema_stats()
        return JSONResponse(content=stats)
    except Exception as e:
        logger.error(f"Error querying db schema stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@catalog_read
def verify_and_clear_recovery_block() -> Dict[str, Any]:
    """Verify full cross-layer consistency (DB/FTS/metadata, in-memory index, disk
    cache, zero leftover stage/old/backup artifacts, unlocked worker mutex) before
    clearing the persistent recovery write block. Never clears without verification."""
    import importlib

    db_manager = importlib.import_module("app.db_manager")
    vector_index = importlib.import_module("app.vector_index")

    errors: List[str] = []
    if _db_import_lock.locked():
        errors.append("存在正在运行的物料库导入线程（_db_import_lock 仍占用）")

    db_state = db_manager.inspect_db_recovery_state()
    if not db_state["has_live"]:
        errors.append("缺少 live 物料表 materials")
    if db_state["leftover_tables"]:
        errors.append(f"仍存在未清理的暂存或旧代数据库表: {db_state['leftover_tables']}")
    db_codes = db_state["db_codes"]
    if db_state["has_fts"] and db_state["fts_codes"] != db_codes:
        errors.append("materials 与 materials_fts 物料编码不一致")
    meta = db_state["metadata"] or {}
    if db_state["has_live"] and int(meta.get("imported_rows", -1)) != len(db_codes):
        errors.append(
            f"materials_metadata.imported_rows ({meta.get('imported_rows')}) "
            f"与实际物料数 ({len(db_codes)}) 不一致"
        )

    with index_manager._lock:
        mem_codes = sorted(m.get("code") for m in (index_manager.index_to_material or []))
        mem_count = len(index_manager.index_to_material or [])
        mem_embs = index_manager.numpy_embeddings
        mem_use_faiss = index_manager.use_faiss
        mem_faiss = index_manager.faiss_index

    if mem_codes != db_codes:
        errors.append("内存向量索引编码集合与数据库 materials 不一致")
    if mem_embs is not None and len(mem_embs) != mem_count:
        errors.append(
            f"内存向量矩阵行数 ({len(mem_embs)}) 与映射条目数 ({mem_count}) 不一致"
        )
    if mem_use_faiss and mem_faiss is not None and mem_faiss.ntotal != mem_count:
        errors.append(
            f"内存 FAISS 索引条目数 ({mem_faiss.ntotal}) 与映射条目数 ({mem_count}) 不一致"
        )

    cache_dir = vector_index.get_cache_dir()
    leftover_cache_dirs = sorted(
        name
        for name in (os.listdir(cache_dir) if os.path.isdir(cache_dir) else [])
        if (name.startswith(".stage_") or name.startswith(".backup_"))
        and os.path.isdir(os.path.join(cache_dir, name))
    )
    if leftover_cache_dirs:
        errors.append(f"向量缓存目录仍存在未清理的恢复目录: {leftover_cache_dirs}")

    mapping_path = os.path.join(cache_dir, "index_to_material.json")
    meta_path = os.path.join(cache_dir, "cache_metadata.json")
    npy_path = os.path.join(cache_dir, "materials_embeddings.npy")
    index_path = os.path.join(cache_dir, "materials.index")
    if not os.path.isfile(mapping_path):
        errors.append("缺少磁盘缓存文件 index_to_material.json")
    else:
        try:
            with open(mapping_path, "r", encoding="utf-8") as f:
                disk_mapping = json.load(f)
            disk_codes = sorted(item.get("code") for item in disk_mapping)
            if disk_codes != db_codes:
                errors.append("磁盘缓存 index_to_material.json 与数据库编码不一致")
        except Exception as exc:
            errors.append(f"读取磁盘缓存 index_to_material.json 失败: {exc!r}")

    if not os.path.isfile(meta_path):
        errors.append("缺少磁盘缓存文件 cache_metadata.json")
    else:
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                disk_meta = json.load(f)
            revision = meta.get("db_revision", 0)
            if disk_meta.get("db_revision", 0) != revision or index_manager.db_revision != revision:
                errors.append("数据库、内存与磁盘缓存 db_revision 不一致")
            if int(disk_meta.get("item_count", -1)) != len(db_codes):
                errors.append(
                    f"cache_metadata.json item_count ({disk_meta.get('item_count')}) "
                    f"与数据库物料数 ({len(db_codes)}) 不一致"
                )
        except Exception as exc:
            errors.append(f"读取 cache_metadata.json 失败: {exc!r}")

    if os.path.isfile(npy_path):
        try:
            import numpy as _np

            disk_embs = _np.load(npy_path, allow_pickle=False)
            if disk_embs.shape[0] != len(db_codes):
                errors.append(
                    f"materials_embeddings.npy 行数 ({disk_embs.shape[0]}) "
                    f"与数据库物料数 ({len(db_codes)}) 不一致"
                )
        except Exception as exc:
            errors.append(f"读取 materials_embeddings.npy 失败: {exc!r}")
    elif mem_embs is not None and len(db_codes) > 0:
        errors.append("缺少磁盘缓存文件 materials_embeddings.npy")

    if mem_use_faiss and len(db_codes) > 0:
        if not os.path.isfile(index_path):
            errors.append("启用 FAISS 时缺少磁盘缓存文件 materials.index")
        elif vector_index.HAS_FAISS:
            try:
                disk_faiss = vector_index.faiss.read_index(index_path)
                if disk_faiss.ntotal != len(db_codes):
                    errors.append(
                        f"磁盘 materials.index 条目数 ({disk_faiss.ntotal}) 与数据库物料数 ({len(db_codes)}) 不一致"
                    )
            except Exception as exc:
                errors.append(f"读取磁盘 materials.index 失败: {exc!r}")

    if errors:
        return {
            "cleared": False,
            "blocked": is_recovery_blocked(),
            "verified_count": len(db_codes),
            "errors": errors,
        }

    db_manager.clear_db_write_block_on_verified_recovery()
    vector_index.clear_cache_write_block_on_verified_recovery(cache_dir)
    return {
        "cleared": True,
        "blocked": is_recovery_blocked(),
        "verified_count": len(db_codes),
        "errors": [],
    }


@app.post("/api/admin/recovery/verify-clear", dependencies=[Depends(require_director)])
def api_admin_verify_and_clear_recovery():
    """Verify all 5 layers match before clearing recovery write block."""
    res = verify_and_clear_recovery_block()
    if not res["cleared"]:
        raise HTTPException(
            status_code=409,
            detail={"msg": "全层一致性核验未通过，继续保持写入阻断", **res},
        )
    return JSONResponse(content=res)


@app.post("/api/db/reparse")
def api_db_reparse(
    background_tasks: BackgroundTasks, req: Optional[ReparseRequest] = None,
    current_user: dict = Depends(require_director),
):
    """Schedule a full or partial catalog reparse."""
    from app.db_manager import reparse_materials_db_asymmetric, reparse_materials_db_partial

    if is_recovery_blocked():
        info = get_recovery_block_status()
        raise HTTPException(
            status_code=409,
            detail=f"恢复失败/需人工处置，系统处于写入阻断状态：{info.get('reason') or '请先核验并完成一致性恢复'}",
        )
    if _db_import_lock.locked():
        raise HTTPException(409, "当前已有物料库导入任务正在运行")
    task_id = str(uuid.uuid4())
    job = _reserve_upload_job(catalog_jobs, 900, task_id)
    category = req.category if req else None
    codes = req.codes if req else None

    def run_reparse():
        try:
            checkpoint()
            if category or codes:
                result = reparse_materials_db_partial(category=category, codes=codes)
            else:
                result = reparse_materials_db_asymmetric()
            invalidate_categories_cache()
            update_task_progress(task_id, status="completed", result=result)
        except Exception as exc:
            update_task_progress(task_id, status="failed", error=str(exc))
            logger.warning("Background catalog reparse failed (%s)", type(exc).__name__)

    try:
        set_task_initial(task_id, {"status": "processing", "created_time": time.time()}, owner_user=current_user)
        background_tasks.add_task(_finish_background_job, job, ExitStack(), run_reparse)
    except BaseException:
        job.release()
        raise
    return JSONResponse(content={"success": True, "message": "Reparse task started in background.", "task_id": task_id})


@app.post("/api/flywheel/suggestions/eval/{task_id}/cancel")
def cancel_flywheel_eval(task_id: str, current_user: dict = Depends(require_admin)):
    from app.flywheel_sandbox import cancel_eval_task

    result = cancel_eval_task(task_id, current_user)
    if result is None:
        raise HTTPException(404, "评测任务不存在。")
    if not result:
        raise HTTPException(409, "评测任务已结束。")
    return {"cancel_requested": True}


@app.post("/api/upload/cancel/{task_id}")
def cancel_upload(task_id: str, current_user: dict = Depends(require_employee)):
    if not get_task_status_for_user(task_id, current_user):
        raise HTTPException(404, "指定任务不存在或无权访问")
    if not (upload_jobs.cancel(task_id) or catalog_jobs.cancel(task_id)):
        raise HTTPException(409, "任务已结束")
    return {"cancel_requested": True}


@app.post("/api/admin/runtime/cleanup")
@http_job(export_jobs, timeout=120)
def api_clean_runtime_files(dry_run: bool = True, current_user: dict = Depends(require_admin)):
    result = clean_runtime_files(UPLOAD_DIR, LOG_DIR, dry_run=dry_run)
    log_audit_event(
        username=current_user["username"], name=current_user.get("name"), role=current_user.get("role"),
        action="运行文件保留清理", details=f"dry_run={dry_run}, counts={result}",
    )
    return result


class LLMToggleRequest(BaseModel):
    enabled: bool

@app.get("/api/llm/status", dependencies=[Depends(require_manager)])
def api_llm_status():
    """Get Tongyi Qwen LLM integration stats and circuit status."""
    from app.llm_client import llm_client
    try:
        return JSONResponse(content=llm_client.get_stats())
    except Exception as e:
        logger.error(f"Error querying LLM status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/llm/toggle", dependencies=[Depends(require_director)])
def api_llm_toggle(req: LLMToggleRequest):
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
