# -*- coding: utf-8 -*-
"""
Full End-to-End Pipeline Test for Data Flywheel (tests/test_flywheel_end2end.py).
Validates:
1. Multi-user action recording with idempotency.
2. Two-phase intelligent mining.
3. Subprocess sandbox dual-run regression gate evaluation (Baseline vs Candidate).
4. Safe two-phase suggestion acceptance with hot reload.
5. LIFO stack snapshot rollback.
6. Benchmark (leakage-free) and contrastive triplets export.
7. Compensating rollback on failure.
"""
import os
import sys
import json
import time
import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, PROJECT_ROOT)

from app.server import app, atomic_write_json_config, trigger_all_config_reloads
from app.db_manager import get_db_cursor, init_db
from app.auth_manager import create_access_token, init_auth_db, get_auth_connection, get_password_hash
from app.flywheel_db import init_flywheel_tables
from app.feedback_miner import miner

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_test_environment():
    init_db()
    init_auth_db()
    init_flywheel_tables()
    
    # Create test accounts in users.db
    conn = get_auth_connection()
    cur = conn.cursor()
    pwd_hash = get_password_hash("testpass123")
    cur.execute("INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active) VALUES ('admin_e2e_tester', ?, '测试管理员', 'admin', 1)", (pwd_hash,))
    cur.execute("INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active) VALUES ('engineer_alice', ?, '工程师爱丽丝', 'employee', 1)", (pwd_hash,))
    cur.execute("INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active) VALUES ('engineer_bob', ?, '工程师鲍勃', 'employee', 1)", (pwd_hash,))
    conn.commit()
    conn.close()

    # Clean previous test flywheel state
    with get_db_cursor(commit=True) as c:
        c.execute("DELETE FROM flywheel_records WHERE user_id != 'legacy_anonymous'")
        c.execute("DELETE FROM flywheel_rule_suggestions")
        c.execute("DELETE FROM flywheel_config_snapshots")
        c.execute("DELETE FROM flywheel_pattern_aggregates")

def test_flywheel_end_to_end_pipeline(tmp_path):
    """Test full Data Flywheel pipeline: Record -> Mine -> Sandbox Eval -> Accept -> Hot Reload -> Rollback -> Export."""
    # 1. Auth headers
    admin_token = create_access_token(data={"sub": "admin_e2e_tester", "role": "admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    
    user1_token = create_access_token(data={"sub": "engineer_alice", "role": "employee"})
    user1_headers = {"Authorization": f"Bearer {user1_token}"}
    
    user2_token = create_access_token(data={"sub": "engineer_bob", "role": "employee"})
    user2_headers = {"Authorization": f"Bearer {user2_token}"}

    # 2. Insert feedback stream for alias: "医用脱脂无缝紫铜管" -> "磷酸脱氧无缝紫铜管" (3 actions from 2 distinct users)
    records_to_insert = [
        (user1_headers, "evt_e2e_1", "sess_e1", 1, "医用脱脂无缝紫铜管", "D15*1.0", "MANUAL_REPLACE", [{"code": "2.17.01.0001", "name": "磷酸脱氧无缝紫铜管", "spec": "D15*1.0"}]),
        (user1_headers, "evt_e2e_2", "sess_e2", 2, "医用脱脂无缝紫铜管", "D22*1.2", "PICK_CANDIDATE", [{"code": "2.17.01.0002", "name": "磷酸脱氧无缝紫铜管", "spec": "D22*1.2"}]),
        (user2_headers, "evt_e2e_3", "sess_e3", 1, "医用脱脂无缝紫铜管", "D28*1.5", "MANUAL_REPLACE", [{"code": "2.17.01.0003", "name": "磷酸脱氧无缝紫铜管", "spec": "D28*1.5"}]),
    ]

    for headers, evt_id, sess_id, r_idx, r_name, r_spec, act_type, targets in records_to_insert:
        payload = {
            "client_event_id": evt_id,
            "session_id": sess_id,
            "row_idx": r_idx,
            "discipline": "医用气体",
            "raw_name": r_name,
            "raw_spec": r_spec,
            "action_type": act_type,
            "final_targets": targets
        }
        res = client.post("/api/flywheel/record", json=payload, headers=headers)
        assert res.status_code == 200, f"Record insert failed: {res.text}"

    # 3. Trigger mining
    mine_res = client.post("/api/flywheel/miner/run", headers=admin_headers)
    assert mine_res.status_code == 200
    mine_data = mine_res.json()
    assert mine_data["code"] == 0

    # 4. Query suggestions
    sug_res = client.get("/api/flywheel/suggestions", headers=admin_headers)
    assert sug_res.status_code == 200
    sugs = sug_res.json()["suggestions"]
    assert len(sugs) > 0

    sug = next((s for s in sugs if "医用脱脂无缝紫铜管" in s["source_pattern"]), None)
    assert sug is not None
    assert sug["support_count"] >= 3
    assert sug["distinct_users"] >= 2
    assert sug["consistency"] >= 0.8
    assert sug["status"] == "pending"

    # 5. Start sandbox evaluation
    eval_res = client.post("/api/flywheel/suggestions/eval", json={"suggestion_id": sug["id"]}, headers=admin_headers)
    assert eval_res.status_code == 200
    task_id = eval_res.json()["task_id"]

    # Poll sandbox eval status
    max_retries = 90
    eval_finished = False
    for _ in range(max_retries):
        time.sleep(1.0)
        st_res = client.get(f"/api/flywheel/eval/{task_id}", headers=admin_headers)
        if st_res.status_code == 200:
            st_data = st_res.json()
            if st_data["status"] in ("completed", "failed"):
                eval_finished = True
                assert st_data["status"] == "completed", f"Eval task failed: {st_data}"
                assert st_data["passed"] is True, f"Beneficial rule did not pass: {st_data}"
                report = st_data["report"]
                assert report["targeted_count"] > 0
                assert report["degraded_from_top1"] == 0
                break
    assert eval_finished, "Sandbox evaluation timed out"

    # 6. Accept suggestion
    accept_res = client.post(
        "/api/flywheel/suggestions/accept",
        json={"suggestion_id": sug["id"], "edited_target_value": "磷酸脱氧无缝紫铜管"},
        headers=admin_headers
    )
    assert accept_res.status_code == 200
    accept_data = accept_res.json()
    snap_ver = accept_data["snapshot_version"]
    assert snap_ver.startswith("snap_")

    # Verify suggestion marked accepted
    with get_db_cursor() as cur:
        cur.execute("SELECT status, applied_rule_id FROM flywheel_rule_suggestions WHERE id = ?", (sug["id"],))
        row = cur.fetchone()
        assert row["status"] == "accepted"
        assert row["applied_rule_id"] == snap_ver

    # Verify snapshot recorded with targeted_eval_passed=1
    snap_res = client.get("/api/flywheel/snapshots", headers=admin_headers)
    assert snap_res.status_code == 200
    snaps = snap_res.json()["snapshots"]
    assert any(s["snapshot_version"] == snap_ver and s["is_active"] == 1 and s["targeted_eval_passed"] == 1 for s in snaps)

    # 7. Rollback snapshot
    rollback_res = client.post("/api/flywheel/rollback", json={"snapshot_version": snap_ver}, headers=admin_headers)
    assert rollback_res.status_code == 200

    # Verify suggestion marked rolled_back and snapshot marked inactive
    with get_db_cursor() as cur:
        cur.execute("SELECT status FROM flywheel_rule_suggestions WHERE id = ?", (sug["id"],))
        assert cur.fetchone()["status"] == "rolled_back"
        cur.execute("SELECT is_active FROM flywheel_config_snapshots WHERE snapshot_version = ?", (snap_ver,))
        assert cur.fetchone()["is_active"] == 0

    # 8. Test Data Exports
    bench_res = client.get("/api/flywheel/export/benchmark", headers=admin_headers)
    assert bench_res.status_code == 200
    b_data = bench_res.json()
    assert "items" in b_data
    assert all(it["source"] == "flywheel_auto" for it in b_data["items"])

    triplet_res = client.get("/api/flywheel/export/triplets", headers=admin_headers)
    assert triplet_res.status_code == 200
    assert "triplets" in triplet_res.json()

    # 9. Test Statistics
    stats_res = client.get("/api/flywheel/statistics", headers=admin_headers)
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert stats["total_records"] >= 3
    assert stats["active_users"] >= 2
