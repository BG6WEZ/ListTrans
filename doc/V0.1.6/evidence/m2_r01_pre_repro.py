# -*- coding: utf-8 -*-
"""
V016-M2 R01 Pre-change empirical reproduction of F07 / F28 publish-gate gaps.
Runs strictly in tests/conftest.py isolated sandbox with LLM_ENABLED=false.
"""

import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

os.environ["LLM_ENABLED"] = "false"
os.environ["TESTING"] = "1"

import tests.conftest as cf

checks = cf.run_guardrail_checks()
assert not checks["gaps"], checks

from fastapi.testclient import TestClient
from app.auth_manager import (
    get_auth_connection,
    get_password_hash,
    get_user_by_username,
    init_auth_db,
    issue_token_for_user,
)
from app.db_manager import get_db_cursor, init_db
from app.flywheel_db import init_flywheel_tables
from app.llm_rule_engine import LLMRuleManager, init_rule_engine_db
from app.normalizer import clean_text_standard
from app.server import app


def main():
    init_db()
    init_auth_db()
    init_flywheel_tables()
    init_rule_engine_db()

    conn = get_auth_connection()
    pwd = get_password_hash("M2PreRepro#2026")
    for uname, role in (("m2_repro_emp", "employee"), ("m2_repro_mgr", "manager"), ("m2_repro_adm", "admin")):
        conn.execute(
            """
            INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch)
            VALUES (?, ?, ?, ?, 1, 0, ?)
            """,
            (uname, pwd, uname, role, f"ep-{uname}"),
        )
    conn.commit()
    conn.close()

    emp = get_user_by_username("m2_repro_emp")
    mgr = get_user_by_username("m2_repro_mgr")
    adm = get_user_by_username("m2_repro_adm")
    emp_hdr = {"Authorization": f"Bearer {issue_token_for_user(emp)}"}
    mgr_hdr = {"Authorization": f"Bearer {issue_token_for_user(mgr)}"}
    adm_hdr = {"Authorization": f"Bearer {issue_token_for_user(adm)}"}
    client = TestClient(app)

    repro = {}

    # 1. Flywheel suggestion accepted WITHOUT any sandbox eval, hardcoding targeted_eval_passed=1,
    #    and writing unscoped global alias across disciplines!
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO flywheel_rule_suggestions (
                suggestion_type, source_pattern, target_value, discipline,
                support_count, distinct_users, consistency, status, source_origin
            ) VALUES ('ALIAS', 'm2repro未测词', '标准对照阀门', '暖通', 5, 3, 1.0, 'pending', 'trusted_miner')
            """
        )
        sug_id = cur.execute("SELECT last_insert_rowid()").fetchone()[0]

    acc_res = client.post(
        "/api/flywheel/suggestions/accept",
        headers=adm_hdr,
        json={"suggestion_id": sug_id},
    )
    snap_row = None
    with get_db_cursor() as cur:
        r = cur.execute(
            "SELECT targeted_eval_passed, target_type, is_active FROM flywheel_config_snapshots WHERE suggestion_id = ?",
            (sug_id,),
        ).fetchone()
        snap_row = dict(r) if r else None

    cleaned_plumbing = clean_text_standard("m2repro未测词", discipline="给排水")
    repro["flywheel_untested_accept"] = {
        "status_code": acc_res.status_code,
        "snapshot": snap_row,
        "leaked_to_plumbing": "标准对照阀门" in cleaned_plumbing,
    }

    # 2. Legacy global binding rule confirmed WITHOUT any sandbox eval
    bind_res = client.post(
        "/api/binding/rules/confirm",
        headers=adm_hdr,
        json={
            "original_name": "m2repro未测绑定",
            "bindings": [{
                "code": "M2-B1",
                "name": "蝶阀",
                "specification": "DN50",
                "unit": "个",
                "brand": "",
                "category": "暖通",
                "qty": 1.0,
                "qty_ratio": 1.0,
            }],
        },
    )
    repro["binding_untested_confirm"] = {
        "status_code": bind_res.status_code,
        "body": bind_res.json(),
    }

    # 3. Manual LLM rule published WITHOUT any sandbox eval
    rid = f"M2-REPRO-{uuid.uuid4().hex[:6]}"
    client.post(
        "/api/llm-rules",
        headers=emp_hdr,
        json={
            "rule_id": rid,
            "rule_name": "未评测直发规则",
            "scope_level": "discipline",
            "scope_target": "暖通",
            "discipline": "暖通",
            "parsed_dsl": {"matching_rules": {"must_not_contain": ["M2_REPRO_BAD"]}},
        },
    )
    client.post(f"/api/llm-rules/{rid}/submit", headers=emp_hdr)
    client.post(f"/api/llm-rules/{rid}/audit", headers=mgr_hdr, json={"action": "approve", "comment": "ok"})
    pub_res = client.post(f"/api/llm-rules/{rid}/publish", headers=adm_hdr, json={})
    repro["llm_rule_untested_publish"] = {
        "status_code": pub_res.status_code,
        "body": pub_res.json(),
    }

    # 4. Sandbox worker source gaps check (no subprocess timeout, no global guard, no DB credential table)
    import inspect
    from app import flywheel_sandbox
    src = inspect.getsource(flywheel_sandbox._run_sandbox_worker)
    repro["sandbox_worker_gaps"] = {
        "has_subprocess_timeout": "timeout=" in src,
        "runs_global_guard": "golden_guard" in src or "global_guard" in src,
        "runs_binding_pipeline": "_evaluate_rule_decision" in src or "batch_match_rows" in src,
    }

    out = ROOT / "doc/V0.1.6/evidence/m2_r01_pre_reproduction.json"
    out.write_text(json.dumps(repro, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(repro, ensure_ascii=False, indent=2))
    assert not cf.run_guardrail_checks()["gaps"]


if __name__ == "__main__":
    main()
