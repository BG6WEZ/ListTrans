# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient
from app.server import app
from app.auth_manager import init_auth_db, issue_token_for_user, get_auth_connection, get_user_by_username
from app.session_manager import init_session_db
from app.flywheel_db import init_flywheel_tables

client = TestClient(app)

def test_unauthenticated_request_rejected_401():
    """Verify that requests without Authorization header are rejected with 401."""
    # 1. Reset endpoint without token -> 401
    res = client.delete("/api/system/reset")
    assert res.status_code == 401

    # 2. Session list without token -> 401
    res = client.get("/api/session/list")
    assert res.status_code == 401

    # 3. Admin users list without token -> 401
    res = client.get("/api/admin/users")
    assert res.status_code == 401

    # 4. Data portrait without token -> 401
    res = client.get("/api/db/portrait")
    assert res.status_code == 401

    # 5. Master materials list without token -> 401
    res = client.get("/api/db/materials")
    assert res.status_code == 401

def test_rbac_system_reset_permission():
    """Verify that only admin role can execute system reset."""
    # A-M0-05: 按实际依赖初始化隔离账号库/会话表/飞轮表。
    # init_auth_db 创建 users/login_attempts；init_session_db 创建 api_system_reset
    # 会 DELETE 的 work_sessions；init_flywheel_tables 创建其会 DELETE 的 4 张飞轮表。
    # 随后用合成测试账号替代业务库已有数据，保证空库可重复执行。
    init_auth_db()
    init_session_db()
    init_flywheel_tables()
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch) VALUES ('employee_user', 'hash', '员工用户', 'employee', 1, 0, 'ep-emp-1')")
    cursor.execute("INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch) VALUES ('admin_user', 'hash', '管理员用户', 'admin', 1, 0, 'ep-adm-1')")
    conn.commit()
    conn.close()

    employee_token = issue_token_for_user(get_user_by_username("employee_user"))
    admin_token = issue_token_for_user(get_user_by_username("admin_user"))

    # Employee tries to reset -> 403 Forbidden
    res = client.delete("/api/system/reset", headers={"Authorization": f"Bearer {employee_token}"})
    assert res.status_code == 403

    # Admin tries to reset -> 200 OK
    res = client.delete("/api/system/reset", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
