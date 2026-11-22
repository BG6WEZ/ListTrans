import os
from typing import List, Dict, Any, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.auth_manager import verify_token_session, is_testing_env

AUTH_DEV_MODE = os.getenv("AUTH_DEV_MODE", "false").lower() == "true"
APP_ENV = os.getenv("APP_ENV", "production").lower()

# Security assertion: Never allow AUTH_DEV_MODE in production or testing environments
if AUTH_DEV_MODE:
    if is_testing_env() or APP_ENV not in ("development", "dev", "local"):
        raise RuntimeError(
            "FATAL SECURITY VIOLATION: AUTH_DEV_MODE is enabled outside isolated local development. "
            "To prevent unauthorized admin access, the application refused to start."
        )

# Security scheme for Bearer token authorization
security_scheme = HTTPBearer(auto_error=False)


async def _authenticate_bearer_token(
    credentials: Optional[HTTPAuthorizationCredentials],
) -> Dict[str, Any]:
    if not credentials:
        if AUTH_DEV_MODE and not is_testing_env():
            return {
                "id": 1,
                "username": "admin",
                "role": "admin",
                "is_active": 1,
                "must_change_password": 0,
                "token_version": 1,
                "account_epoch": "dev-local",
            }
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录系统以获取访问权限。",
        )

    user, code, err_detail = verify_token_session(credentials.credentials)
    if user is None:
        raise HTTPException(status_code=code, detail=err_detail)
    return user


async def get_current_user_allow_password_change(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> Dict[str, Any]:
    """Authenticated user dependency for identity/profile/logout endpoints that remain open before forced password change."""
    return await _authenticate_bearer_token(credentials)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> Dict[str, Any]:
    """Dependency to retrieve and validate authenticated active user; blocks business APIs until forced password change completes."""
    user = await _authenticate_bearer_token(credentials)
    if user.get("must_change_password"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MUST_CHANGE_PASSWORD: 首次登录或密码重置后必须先修改密码。",
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
