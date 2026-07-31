# -*- coding: utf-8 -*-
from typing import List, Dict, Any, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.auth_manager import verify_access_token, get_user_by_username

# Security scheme for Bearer token authorization
security_scheme = HTTPBearer(auto_error=False)

async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme)) -> Dict[str, Any]:
    """Dependency to retrieve and validate the authenticated user from JWT token."""
    if not credentials:
        # Fallback default user for local single-user workspace execution
        return {"username": "admin", "role": "admin", "is_active": True}
        
    token = credentials.credentials
    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录状态已过期或无效，请重新登录。"
        )
        
    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 格式错误：缺少主体信息。"
        )
        
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="该账号已不存在。"
        )
        
    if not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该账号已被停用，请联系管理员。"
        )
        
    return user

class RequireRole:
    """Dependency to enforce role constraints on specific API endpoints."""
    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        role = current_user.get("role", "employee")
        if role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="您的权限不足以执行此操作。"
            )
        return current_user

# Pre-defined dependency instances for roles
require_employee = RequireRole(["employee", "manager", "director", "admin"])
require_manager = RequireRole(["manager", "director", "admin"])
require_director = RequireRole(["director", "admin"])
require_admin = RequireRole(["admin"])
