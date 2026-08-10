# -*- coding: utf-8 -*-
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
import sqlite3

from app.auth_middleware import require_admin
from app.auth_manager import (
    get_auth_connection, get_password_hash, get_user_by_id, get_user_by_username,
    security_logger
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    role: str = Field("employee", pattern="^(employee|manager|director|admin)$")

class UserUpdate(BaseModel):
    role: Optional[str] = Field(None, pattern="^(employee|manager|director|admin)$")
    is_active: Optional[bool] = None

class PasswordReset(BaseModel):
    new_password: str = Field(..., min_length=6)

@router.get("/users")
async def list_users(current_user: dict = Depends(require_admin)):
    """Retrieve all users in the system."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, role, is_active, created_at, last_login FROM users ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/users")
async def create_user(user_data: UserCreate, current_user: dict = Depends(require_admin)):
    """Create a new user account with role assignment."""
    username = user_data.username.strip()
    if get_user_by_username(username):
        raise HTTPException(status_code=400, detail="该用户名已被占用。")
        
    hashed_pass = get_password_hash(user_data.password)
    conn = get_auth_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, hashed_pass, user_data.role)
        )
        conn.commit()
        security_logger.info(f"User '{username}' created by admin '{current_user['username']}' with role '{user_data.role}'.")
        return {"success": True, "detail": f"用户 {username} 创建成功。"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"数据库写入失败: {e}")
    finally:
        conn.close()

@router.put("/users/{user_id}")
async def update_user(user_id: int, user_data: UserUpdate, current_user: dict = Depends(require_admin)):
    """Update user role and active state."""
    target_user = get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="目标用户未找到。")
        
    if target_user["username"] == current_user["username"] and user_data.is_active is False:
        raise HTTPException(status_code=400, detail="不能禁用您自己当前登录的账号。")
        
    updates = []
    params = []
    if user_data.role is not None:
        updates.append("role = ?")
        params.append(user_data.role)
    if user_data.is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if user_data.is_active else 0)
        
    if not updates:
        return {"success": True}
        
    params.append(user_id)
    conn = get_auth_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        security_logger.info(f"User '{target_user['username']}' (ID: {user_id}) updated by admin '{current_user['username']}'. Changes: {user_data.model_dump_json(exclude_none=True)}")
        return {"success": True, "detail": "用户信息修改成功。"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"修改失败: {e}")
    finally:
        conn.close()

@router.put("/users/{user_id}/password")
async def reset_password(user_id: int, pwd_data: PasswordReset, current_user: dict = Depends(require_admin)):
    """Reset user password by administrative override."""
    target_user = get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="目标用户未找到。")
        
    hashed_pass = get_password_hash(pwd_data.new_password)
    conn = get_auth_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed_pass, user_id))
        conn.commit()
        security_logger.info(f"Password reset for user '{target_user['username']}' (ID: {user_id}) by admin '{current_user['username']}'.")
        return {"success": True, "detail": "密码重置成功。"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"密码重置失败: {e}")
    finally:
        conn.close()

@router.delete("/users/{user_id}")
async def delete_user(user_id: int, current_user: dict = Depends(require_admin)):
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
async def list_llm_providers(current_user: dict = Depends(require_admin)):
    """List all LLM provider configurations for administration."""
    manager = LLMProviderManager()
    return manager.list_providers(include_keys=False)

@router.post("/llm-providers")
async def create_llm_provider(p_data: LLMProviderCreate, current_user: dict = Depends(require_admin)):
    """Add a new LLM provider configuration node."""
    manager = LLMProviderManager()
    try:
        return manager.add_provider(p_data.model_dump())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"添加失败: {e}")

@router.put("/llm-providers/{provider_id}")
async def update_llm_provider(provider_id: str, p_data: LLMProviderUpdate, current_user: dict = Depends(require_admin)):
    """Update an existing LLM provider configuration."""
    manager = LLMProviderManager()
    try:
        return manager.update_provider(provider_id, p_data.model_dump(exclude_none=True))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"更新失败: {e}")

@router.delete("/llm-providers/{provider_id}")
async def delete_llm_provider(provider_id: str, current_user: dict = Depends(require_admin)):
    """Delete an LLM provider configuration node."""
    manager = LLMProviderManager()
    return manager.delete_provider(provider_id)

