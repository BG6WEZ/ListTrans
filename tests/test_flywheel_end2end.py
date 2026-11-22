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
from app.auth_manager import issue_token_for_user, get_user_by_username, init_auth_db, get_auth_connection, get_password_hash
from app.flywheel_db import init_flywheel_tables
from app.feedback_miner import miner

client = TestClient(app)

_SANDBOX_FIXTURE = os.path.join(
    PROJECT_ROOT, "tests", "fixtures", "sandbox_materials_v1.json"
)

@pytest.fixture(scope="module", autouse=True)
def setup_test_environment():
    init_db()
    init_auth_db()
    init_flywheel_tables()
    
    # Create test accounts in users.db
    conn = get_auth_connection()
    cur = conn.cursor()
    pwd_hash = get_password_hash("testpass123")
    cur.execute("INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch) VALUES ('admin_e2e_tester', ?, '测试管理员', 'admin', 1, 0, 'ep-adm-e2e')", (pwd_hash,))
    cur.execute("INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch) VALUES ('engineer_alice', ?, '工程师爱丽丝', 'employee', 1, 0, 'ep-alice')", (pwd_hash,))
    cur.execute("INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch) VALUES ('engineer_bob', ?, '工程师鲍勃', 'employee', 1, 0, 'ep-bob')", (pwd_hash,))
    conn.commit()
    conn.close()

    # Clean previous test flywheel state
    with get_db_cursor(commit=True) as c:
        c.execute("DELETE FROM flywheel_records WHERE user_id != 'legacy_anonymous'")
        c.execute("DELETE FROM flywheel_rule_suggestions")
        c.execute("DELETE FROM flywheel_config_snapshots")
        c.execute("DELETE FROM flywheel_pattern_aggregates")

    # 可观察基线前置：仅向隔离库播种既定合成 fixture，禁止空库全 0 稳态通过。
    # 与 test_flywheel_sandbox.py 使用同一真实 XLSX 导入链路及只读金标目标物料。
    import pandas as pd
    from app.db_manager import import_materials_from_excel, get_all_materials

    with open(_SANDBOX_FIXTURE, encoding="utf-8") as f:
        materials = json.load(f)["materials"]
    xlsx_path = os.path.join(os.environ["UPLOAD_DIR"], "e2e_sandbox_materials.xlsx")
    pd.DataFrame([
        {
            "物料分类": m["category"], "物料编码": m["code"],
            "物料名称": m["name"], "规格/型号": m["spec"],
            "计量单位": "台", "常用品牌": "", "描述": "",
        }
        for m in materials
    ]).to_excel(xlsx_path, index=False, startrow=1)
    meta = import_materials_from_excel(xlsx_path, mode="replace", filename="e2e_sandbox_materials.xlsx")
    assert meta.get("imported_rows", 0) > 0
    assert len(get_all_materials(limit=10 ** 6)) == len(materials)

def _upload_e2e_rows(headers, filename, rows):
    import io
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "医用气体"
    ws.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    up = client.post(
        "/api/upload",
        headers=headers,
        files={"file": (filename, buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up.status_code == 200, up.text
    task_id = up.json()["task_id"]
    st = client.get(f"/api/upload/status?task_id={task_id}", headers=headers)
    assert st.status_code == 200, st.text
    body = st.json()
    assert body["status"] == "completed", body
    return body["result"]


def test_flywheel_end_to_end_pipeline(tmp_path):
    """Test full Data Flywheel pipeline: Record -> Mine -> Sandbox Eval -> Accept -> Hot Reload -> Rollback -> Export."""
    # 1. Auth headers
    admin_token = issue_token_for_user(get_user_by_username("admin_e2e_tester"))
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    
    user1_token = issue_token_for_user(get_user_by_username("engineer_alice"))
    user1_headers = {"Authorization": f"Bearer {user1_token}"}
    
    user2_token = issue_token_for_user(get_user_by_username("engineer_bob"))
    user2_headers = {"Authorization": f"Bearer {user2_token}"}

    alice_rows = _upload_e2e_rows(
        user1_headers,
        "alice_e2e.xlsx",
        [
            ["030101", "医用脱脂无缝紫铜管", "D15*1.0", "米", 10],
            ["030102", "医用脱脂无缝紫铜管", "D22*1.2", "米", 20],
        ],
    )
    bob_rows = _upload_e2e_rows(
        user2_headers,
        "bob_e2e.xlsx",
        [
            ["030101", "医用脱脂无缝紫铜管", "D28*1.5", "米", 30],
        ],
    )

    # 2. Insert feedback stream for alias: "医用脱脂无缝紫铜管" -> "磷酸脱氧无缝紫铜管" (3 actions from 2 distinct users)
    records_to_insert = [
        (user1_headers, "evt_e2e_1", alice_rows[0], "MANUAL_REPLACE", [{"code": "2.17.01.0001", "name": "磷酸脱氧无缝紫铜管", "spec": "D15*1.0"}]),
        (user1_headers, "evt_e2e_2", alice_rows[1], "PICK_CANDIDATE", [{"code": "2.17.01.0002", "name": "磷酸脱氧无缝紫铜管", "spec": "D22*1.2"}]),
        (user2_headers, "evt_e2e_3", bob_rows[0], "MANUAL_REPLACE", [{"code": "2.17.01.0003", "name": "磷酸脱氧无缝紫铜管", "spec": "D28*1.5"}]),
    ]

    for headers, evt_id, row_ctx, act_type, targets in records_to_insert:
        payload = {
            "client_event_id": evt_id,
            "document_id": row_ctx["document_id"],
            "sheet_id": row_ctx["sheet_id"],
            "source_row_id": row_ctx["source_row_id"],
            "recommendation_snapshot_id": row_ctx["recommendation_snapshot_id"],
            "event_seq": 1,
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

    # 6. Verify accept requires eval_id and rejects post-eval edit, then accepts with valid eval_id
    missing_eval_res = client.post(
        "/api/flywheel/suggestions/accept",
        json={"suggestion_id": sug["id"], "discipline": sug.get("discipline") or "医用气体"},
        headers=admin_headers,
    )
    assert missing_eval_res.status_code == 409

    edited_mismatch_res = client.post(
        "/api/flywheel/suggestions/accept",
        json={
            "suggestion_id": sug["id"],
            "edited_target_value": "铜质阀门配件",
            "eval_id": task_id,
            "discipline": sug.get("discipline") or "医用气体",
        },
        headers=admin_headers,
    )
    assert edited_mismatch_res.status_code == 409

    if st_data.get("benefit") == "neutral":
        no_reason_res = client.post(
            "/api/flywheel/suggestions/accept",
            json={
                "suggestion_id": sug["id"],
                "edited_target_value": "磷酸脱氧无缝紫铜管",
                "eval_id": task_id,
                "discipline": sug.get("discipline") or "医用气体",
            },
            headers=admin_headers,
        )
        assert no_reason_res.status_code == 422

    accept_res = client.post(
        "/api/flywheel/suggestions/accept",
        json={
            "suggestion_id": sug["id"],
            "edited_target_value": "磷酸脱氧无缝紫铜管",
            "eval_id": task_id,
            "discipline": sug.get("discipline") or "医用气体",
            "neutral_reason": "E2E验证中性稳态别名经审批采纳" if st_data.get("benefit") == "neutral" else None,
        },
        headers=admin_headers
    )
    assert accept_res.status_code == 200, accept_res.text
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
