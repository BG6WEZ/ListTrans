# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient
from app.server import app
from app.auth_manager import create_access_token, get_auth_connection

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
    employee_token = create_access_token(data={"sub": "employee_user", "role": "employee"})
    admin_token = create_access_token(data={"sub": "admin_user", "role": "admin"})
    
    # Ensure test user accounts exist in DB so get_current_user finds them
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active) VALUES ('employee_user', 'hash', '员工用户', 'employee', 1)")
    cursor.execute("INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active) VALUES ('admin_user', 'hash', '管理员用户', 'admin', 1)")
    conn.commit()
    conn.close()

    # Employee tries to reset -> 403 Forbidden
    res = client.delete("/api/system/reset", headers={"Authorization": f"Bearer {employee_token}"})
    assert res.status_code == 403

    # Admin tries to reset -> 200 OK
    res = client.delete("/api/system/reset", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
