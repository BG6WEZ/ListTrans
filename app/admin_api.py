# -*- coding: utf-8 -*-
from app.runtime_jobs import http_job, rule_jobs
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from typing import List, Optional
import sqlite3

from app.auth_middleware import require_admin
from app.auth_manager import (
    get_auth_connection,
    get_user_by_id,
    security_logger,
    admin_create_user_atomic,
    admin_update_user_atomic,
    admin_reset_password_atomic,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: Optional[str] = Field(None, description="可选；留空则系统生成一次性随机临时密码")
    name: Optional[str] = ""
    dept1: Optional[str] = ""
    dept2: Optional[str] = ""
    dept3: Optional[str] = ""
    employee_no: str = Field(..., min_length=1, max_length=50, description="工号（必填且唯一，创建后不可修改）")
    position: Optional[str] = ""
    phone: Optional[str] = ""
    role: str = Field("employee", pattern="^(employee|manager|director|admin)$")

class UserUpdate(BaseModel):
    name: Optional[str] = None
    dept1: Optional[str] = None
    dept2: Optional[str] = None
    dept3: Optional[str] = None
    employee_no: Optional[str] = None
    position: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = Field(None, pattern="^(employee|manager|director|admin)$")
    is_active: Optional[bool] = None

class PasswordReset(BaseModel):
    new_password: Optional[str] = Field(None, description="可选；留空则系统生成一次性随机临时密码")

@router.get("/users")
def list_users(current_user: dict = Depends(require_admin)):
    """Retrieve all users in the system."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, username, name, dept1, dept2, dept3, employee_no, position, phone,
               role, is_active, must_change_password, token_version, created_at, last_login
        FROM users
        ORDER BY id ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/users")
def create_user(user_data: UserCreate, current_user: dict = Depends(require_admin)):
    """Create a new user account with unique one-time temp password and role assignment."""
    username = user_data.username.strip()
    employee_no = user_data.employee_no.strip()
    if not employee_no:
        raise HTTPException(status_code=400, detail="工号不能为空。")

    phone = (user_data.phone or "").strip() or username
    res = admin_create_user_atomic(
        username=username,
        role=user_data.role,
        password=user_data.password,
        name=user_data.name or "",
        dept1=user_data.dept1 or "",
        dept2=user_data.dept2 or "",
        dept3=user_data.dept3 or "",
        employee_no=employee_no,
        position=user_data.position or "",
        phone=phone,
    )
    if not res.get("success"):
        raise HTTPException(status_code=res.get("status_code", 400), detail=res.get("error", "创建用户失败。"))

    security_logger.info(
        f"User '{username}' ({user_data.name}, emp_no: {employee_no}) created by admin '{current_user['username']}' with role '{user_data.role}', must_change_password=1."
    )
    return {
        "success": True,
        "id": res["id"],
        "username": res["username"],
        "role": res["role"],
        "must_change_password": 1,
        "temp_password": res["temp_password"],
        "detail": f"用户 {username} ({user_data.name or ''}) 创建成功，请通过受控渠道一次性交付临时密码。",
    }

@router.post("/users/sync-excel")
@http_job(rule_jobs, timeout=120)
def sync_users_from_excel(current_user: dict = Depends(require_admin)):
    """Synchronize users from doc/人员清单.xls with per-user unique temporary passwords."""
    from app.auth_manager import import_users_from_excel
    res = import_users_from_excel()
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message", "导入失败"))
    security_logger.info(
        f"Excel user sync triggered by admin '{current_user['username']}': created={res.get('imported_count', 0)}, updated={res.get('updated_count', 0)}."
    )
    return res

@router.put("/users/{user_id}")
def update_user(user_id: int, user_data: UserUpdate, current_user: dict = Depends(require_admin)):
    """Update user role and active state; atomically increments token_version on role or is_active changes."""
    target_user = get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="目标用户未找到。")

    if target_user["username"] == current_user["username"] and user_data.is_active is False:
        raise HTTPException(status_code=400, detail="不能禁用您自己当前登录的账号。")

    updates = {}
    for field in ("name", "dept1", "dept2", "dept3", "position", "phone", "role", "is_active"):
        val = getattr(user_data, field)
        if val is not None:
            updates[field] = val

    if user_data.employee_no is not None:
        new_emp = user_data.employee_no.strip()
        existing_emp = (target_user.get("employee_no") or "").strip()
        if existing_emp and new_emp != existing_emp:
            raise HTTPException(status_code=400, detail="工号为账号全局唯一不可变标识，创建后不可修改。")
        elif not existing_emp and new_emp:
            updates["employee_no"] = new_emp

    if not updates:
        return {"success": True}

    res = admin_update_user_atomic(user_id, updates)
    if not res.get("success"):
        raise HTTPException(status_code=res.get("status_code", 400), detail=res.get("error", "修改失败。"))

    security_logger.info(
        f"User '{target_user['username']}' (ID: {user_id}) updated by admin '{current_user['username']}'. token_version_bumped={res.get('token_version_bumped')}."
    )
    return {
        "success": True,
        "token_version_bumped": res.get("token_version_bumped", False),
        "detail": "用户信息修改成功。",
    }

@router.put("/users/{user_id}/password")
def reset_password(user_id: int, pwd_data: Optional[PasswordReset] = None, current_user: dict = Depends(require_admin)):
    """Reset user password with unique temp secret, force must_change_password=1, and invalidate prior tokens."""
    requested_pwd = pwd_data.new_password if pwd_data else None
    res = admin_reset_password_atomic(user_id, requested_pwd)
    if not res.get("success"):
        raise HTTPException(status_code=res.get("status_code", 400), detail=res.get("error", "密码重置失败。"))
    security_logger.info(
        f"Password reset for user '{res['username']}' (ID: {user_id}) by admin '{current_user['username']}'; prior sessions invalidated."
    )
    return {
        "success": True,
        "username": res["username"],
        "must_change_password": 1,
        "temp_password": res["temp_password"],
        "detail": "密码已重置为一次性临时密码，该用户旧会话已立即失效且下次登录必须修改密码。",
    }

@router.delete("/users/{user_id}")
def delete_user(user_id: int, current_user: dict = Depends(require_admin)):
    """Delete a user account."""
    target_user = get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="目标用户未找到。")
        
    if target_user["username"] == current_user["username"]:
        raise HTTPException(status_code=400, detail="不能删除您自己当前登录的账号。")
        
    conn = get_auth_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        security_logger.info(f"User '{target_user['username']}' (ID: {user_id}) deleted by admin '{current_user['username']}'.")
        return {"success": True, "detail": "用户删除成功。"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"删除用户失败: {e}")
    finally:
        conn.close()


# LLM Provider Management Endpoints for Admin

from app.llm_rule_engine import LLMProviderManager

class LLMProviderCreate(BaseModel):
    provider_id: str = Field(..., min_length=2, max_length=50)
    name: str = Field(..., min_length=2)
    base_url: str = Field(..., min_length=5)
    model_name: str = Field(..., min_length=2)
    api_key_enc: str = Field(...)
    priority: int = Field(10, ge=1, le=100)
    timeout_seconds: float = Field(8.0, ge=1.0, le=60.0)
    is_active: bool = True

class LLMProviderUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    model_name: Optional[str] = None
    api_key_enc: Optional[str] = None
    priority: Optional[int] = None
    timeout_seconds: Optional[float] = None
    is_active: Optional[bool] = None

@router.get("/llm-providers")
def list_llm_providers(current_user: dict = Depends(require_admin)):
    """List all LLM provider configurations for administration."""
    manager = LLMProviderManager()
    return manager.list_providers(include_keys=False)

@router.post("/llm-providers")
def create_llm_provider(p_data: LLMProviderCreate, current_user: dict = Depends(require_admin)):
    """Add a new LLM provider configuration node."""
    manager = LLMProviderManager()
    try:
        return manager.add_provider(p_data.model_dump())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"添加失败: {e}")

@router.put("/llm-providers/{provider_id}")
def update_llm_provider(provider_id: str, p_data: LLMProviderUpdate, current_user: dict = Depends(require_admin)):
    """Update an existing LLM provider configuration."""
    manager = LLMProviderManager()
    try:
        return manager.update_provider(provider_id, p_data.model_dump(exclude_none=True))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"更新失败: {e}")

@router.delete("/llm-providers/{provider_id}")
def delete_llm_provider(provider_id: str, current_user: dict = Depends(require_admin)):
    """Delete an LLM provider configuration node."""
    manager = LLMProviderManager()
    return manager.delete_provider(provider_id)


# ==========================================
# 1. Server Health & High-Load Alert Endpoints
# ==========================================

from app.audit_manager import (
    get_server_hardware_metrics, get_online_and_active_users,
    get_alert_snapshots, check_and_capture_high_load_snapshot,
    get_aggregated_statistics, get_audit_logs_paginated, log_audit_event
)

@router.get("/health")
def get_admin_health(current_user: dict = Depends(require_admin)):
    """
    Retrieve real-time hardware health (CPU, RAM, Disk), current online users,
    and currently processing matching/cleaning rows.
    """
    hardware = get_server_hardware_metrics()
    online_info = get_online_and_active_users()
    metrics_available = bool(hardware.get("metrics_available", False))
    metrics_error = str(hardware.get("metrics_error") or "")

    return {
        "status": "healthy" if metrics_available else "degraded",
        "metrics_available": metrics_available,
        "metrics_error": metrics_error,
        "hardware": hardware,
        "online_count": online_info["online_count"],
        "active_count": online_info["active_count"],
        "processing_rows_count": online_info["processing_rows_count"],
        "active_tasks": online_info["active_tasks"],
        "is_alert": hardware["is_alert"],
        "alert_message": hardware["alert_message"]
    }

@router.get("/health/alerts")
def get_admin_health_alerts(limit: int = 50, current_user: dict = Depends(require_admin)):
    """Retrieve history of high-load alert snapshots."""
    return get_alert_snapshots(limit=limit)

@router.post("/health/alerts/simulate")
def simulate_health_alert(current_user: dict = Depends(require_admin)):
    """
    Manually capture/simulate a high-load alert snapshot for demonstration.
    """
    hardware = get_server_hardware_metrics()
    snapshot_id = check_and_capture_high_load_snapshot(
        trigger_type=f"管理员手动触发/模拟负荷警戒 (CPU {max(hardware['cpu']['percent'], 91.5)}%)",
        cpu_percent=max(hardware['cpu']['percent'], 91.5),
        memory_percent=max(hardware['memory']['percent'], 90.2),
        disk_percent=hardware['disk']['percent'],
        force=True
    )
    log_audit_event(
        username=current_user["username"],
        name=current_user.get("name"),
        role=current_user.get("role"),
        action="模拟负荷警戒快照",
        details=f"管理员手动触发高负荷警戒快照 #{snapshot_id}"
    )
    return {"success": True, "snapshot_id": snapshot_id, "message": "高负荷警戒快照已成功捕获记录！"}


# ==========================================
# 2. System Aggregate Statistics Endpoints
# ==========================================

@router.get("/statistics")
def get_admin_statistics(current_user: dict = Depends(require_admin)):
    """
    Retrieve comprehensive system metrics:
    - 总登录人次
    - 使用人次
    - 处理的总清单行数
    - 输出的行数
    - 系统总使用时长
    - 专业分布与活跃排行
    """
    return get_aggregated_statistics()


# ==========================================
# 3. Online Users & Operation Audit Log Endpoints
# ==========================================

@router.get("/online-users")
def get_admin_online_users(current_user: dict = Depends(require_admin)):
    """Retrieve list of currently online and active users."""
    return get_online_and_active_users()

@router.get("/audit-logs")
def get_admin_audit_logs(
    username: Optional[str] = None,
    action: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(require_admin)
):
    """Retrieve operation audit records with multi-dimensional filtering and pagination."""
    return get_audit_logs_paginated(
        username=username,
        action=action,
        status=status,
        search=search,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size
    )

@router.get("/audit-actions")
def get_admin_audit_actions(current_user: dict = Depends(require_admin)):
    """Retrieve all unique audit action types for frontend dropdown selection."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT action FROM audit_logs WHERE action IS NOT NULL ORDER BY action ASC")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]


