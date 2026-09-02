# -*- coding: utf-8 -*-
import os
import sys
import json
import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, PROJECT_ROOT)

from app.server import app, atomic_write_json_config, trigger_all_config_reloads
from app.db_manager import get_db_cursor, init_db
from app.flywheel_db import init_flywheel_tables
from app.auth_manager import create_access_token, init_auth_db

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_test_env():
    init_db()
    init_auth_db()
    init_flywheel_tables()

def test_db_cursor_context_manager():
    """Verify get_db_cursor handles commit and rollback properly."""
    with get_db_cursor(commit=True) as cur:
        cur.execute("SELECT 1 as val")
        row = cur.fetchone()
        assert row["val"] == 1

def test_atomic_write_and_hot_reload(tmp_path):
    """Verify atomic write and all 6 reload functions work without error."""
    test_cfg_path = os.path.join(tmp_path, "test_cfg.json")
    test_data = {"test_key": "test_val"}
    atomic_write_json_config(test_cfg_path, test_data)
    
    with open(test_cfg_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == test_data

    # Test full reload trigger
    trigger_all_config_reloads()

import uuid

def test_flywheel_record_idempotency_and_superseded():
    """Verify POST /api/flywheel/record handles idempotency and superseded flag correctly."""
    token = create_access_token(data={"sub": "admin", "role": "admin"})
    headers = {"Authorization": f"Bearer {token}"}

    uid = uuid.uuid4().hex[:8]
    session_id = f"test_session_{uid}"
    row_idx = 101
    event_id_1 = f"test_event_{uid}_v1"
    
    payload1 = {
        "client_event_id": event_id_1,
        "session_id": session_id,
        "row_idx": row_idx,
        "discipline": "暖通",
        "raw_name": "测试排风机组AP-1",
        "raw_spec": "风量1000m3/h",
        "action_type": "MANUAL_REPLACE",
        "final_targets": [{"code": "TEST.001", "name": "标准排风机", "spec": "1000m3/h", "qty_ratio": 1.0}]
    }

    # First insert
    res1 = client.post("/api/flywheel/record", json=payload1, headers=headers)
    assert res1.status_code == 200

    # Idempotent re-insert with same event_id
    res_dup = client.post("/api/flywheel/record", json=payload1, headers=headers)
    assert res_dup.status_code == 200

    # Verify only 1 record exists
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM flywheel_records WHERE client_event_id = ?", (event_id_1,))
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["superseded"] == 0
        rec_id_1 = rows[0]["id"]

    # Second user action on the same row (new UUID)
    event_id_2 = f"test_event_{uid}_v2"
    payload2 = {
        "client_event_id": event_id_2,
        "session_id": session_id,
        "row_idx": row_idx,
        "discipline": "暖通",
        "raw_name": "测试排风机组AP-1",
        "raw_spec": "风量1000m3/h",
        "action_type": "CONFIRM_TOP1",
        "final_targets": [{"code": "TEST.002", "name": "标准排风机组B", "spec": "1000m3/h", "qty_ratio": 1.0}]
    }
    res2 = client.post("/api/flywheel/record", json=payload2, headers=headers)
    assert res2.status_code == 200

    # Verify record 1 is now superseded=1, and record 2 is superseded=0
    with get_db_cursor() as cur:
        cur.execute("SELECT id, client_event_id, superseded FROM flywheel_records WHERE session_id = ? AND row_idx = ? ORDER BY id ASC", (session_id, row_idx))
        rows = cur.fetchall()
        assert len(rows) == 2
        assert rows[0]["id"] == rec_id_1
        assert rows[0]["superseded"] == 1
        assert rows[1]["client_event_id"] == event_id_2
        assert rows[1]["superseded"] == 0
