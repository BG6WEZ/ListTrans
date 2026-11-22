# -*- coding: utf-8 -*-
import os
import sys
import pytest
from fastapi.testclient import TestClient

# Add project directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.server import app
from app.auth_manager import init_auth_db, issue_token_for_user, get_user_by_username
from app.session_manager import init_session_db
from app.audit_manager import (
    init_audit_tables, log_audit_event, record_heartbeat,
    get_online_and_active_users, get_server_hardware_metrics,
    check_and_capture_high_load_snapshot, get_alert_snapshots,
    get_aggregated_statistics, get_audit_logs_paginated,
    increment_stat_counter
)

client = TestClient(app)

@pytest.fixture(scope="module")
def admin_token():
    # A-M0-05: 按实际依赖初始化隔离账号库与审计表，不依赖业务库已有数据或其他模块副作用。
    # init_auth_db 创建 users/login_attempts 并写入合成默认 admin；init_audit_tables 创建
    # audit_logs/active_heartbeats/load_alert_snapshots/system_stats_summary；init_session_db
    # 创建 get_aggregated_statistics 依赖的 work_sessions。均使用 conftest 注入的隔离 AUTH_DB_PATH。
    init_auth_db()
    init_audit_tables()
    init_session_db()
    admin_user = get_user_by_username("admin")
    assert admin_user is not None, "init_auth_db 后必须存在合成默认 admin 账号"
    token = issue_token_for_user(admin_user)
    return token

def test_audit_manager_direct():
    # A-M0-05: log_audit_event 未传 user_id 时回查 users 表、get_aggregated_statistics
    # 依赖 login_attempts/work_sessions，按实际依赖补齐初始化（隔离 AUTH_DB_PATH 内）。
    init_auth_db()
    init_session_db()
    init_audit_tables()
    
    # 1. Test logging audit event
    log_audit_event(
        username="admin",
        action="单元测试操作",
        details="测试审计日志写入与读取",
        name="系统管理员",
        role="admin",
        ip_address="127.0.0.1"
    )
    
    logs = get_audit_logs_paginated(username="admin", page=1, page_size=10)
    assert logs["total"] >= 1
    assert any(item["action"] == "单元测试操作" for item in logs["items"])
    
    # 2. Test heartbeat
    record_heartbeat(
        username="18258426632",
        name="何仁泰",
        role="director",
        dept="成本中心",
        ip_address="192.168.1.50",
        current_page="workbench",
        state="active"
    )
    
    online_info = get_online_and_active_users()
    assert online_info["online_count"] >= 1
    assert any(u["username"] == "18258426632" for u in online_info["online_users"])
    
    # 3. Test hardware metrics
    metrics = get_server_hardware_metrics()
    assert "cpu" in metrics
    assert "memory" in metrics
    assert "disk" in metrics
    assert metrics["cpu"]["percent"] >= 0
    assert metrics["memory"]["total_gb"] > 0
    assert metrics["disk"]["total_gb"] > 0
    
    # 4. Test high-load snapshot
    snap_id = check_and_capture_high_load_snapshot(
        trigger_type="CPU 负荷达到 92.5%",
        cpu_percent=92.5,
        memory_percent=88.0,
        disk_percent= metrics["disk"]["percent"],
        force=True
    )
    assert snap_id is not None
    
    snapshots = get_alert_snapshots(limit=10)
    assert len(snapshots) >= 1
    assert snapshots[0]["trigger_type"] == "CPU 负荷达到 92.5%"
    assert snapshots[0]["online_users_count"] >= 1
    
    # 5. Test stats counter
    increment_stat_counter("total_processed_rows", 100)
    increment_stat_counter("total_exported_rows", 80)
    stats = get_aggregated_statistics()
    assert stats["total_processed_rows"] >= 100
    assert stats["total_exported_rows"] >= 80

def test_admin_api_endpoints(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Test /api/admin/health
    res = client.get("/api/admin/health", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "hardware" in data
    assert "online_count" in data
    assert "processing_rows_count" in data
    
    # Test /api/admin/health/alerts
    res = client.get("/api/admin/health/alerts", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)
    
    # Test /api/admin/health/alerts/simulate
    res = client.post("/api/admin/health/alerts/simulate", headers=headers)
    assert res.status_code == 200
    assert res.json()["success"] is True
    
    # Test /api/admin/statistics
    res = client.get("/api/admin/statistics", headers=headers)
    assert res.status_code == 200
    stats = res.json()
    assert "total_logins" in stats
    assert "total_processed_rows" in stats
    assert "total_exported_rows" in stats
    assert "formatted_duration" in stats
    
    # Test /api/admin/online-users
    res = client.get("/api/admin/online-users", headers=headers)
    assert res.status_code == 200
    assert "online_users" in res.json()
    
    # Test /api/admin/audit-logs
    res = client.get("/api/admin/audit-logs?page=1&page_size=20", headers=headers)
    assert res.status_code == 200
    logs = res.json()
    assert "items" in logs
    assert "total" in logs
    
    # Test /api/admin/audit-actions
    res = client.get("/api/admin/audit-actions", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)

def test_heartbeat_endpoint(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    res = client.post(
        "/api/auth/heartbeat",
        headers=headers,
        json={"current_page": "workbench", "state": "active"}
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
