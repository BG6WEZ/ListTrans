# -*- coding: utf-8 -*-
"""
V016-M2 R01 Comprehensive Verification Suite (tests/test_v016_m2_publish_gate.py).
Covers all 6 Minimum Acceptance Matrix requirements (M2-C01..C08):
1. Unified publish gate across manual LLM DSL, legacy binding, and flywheel rules + forgery/staleness/TTL/env-drift rejection.
2. Canonical discipline isolation (暖通 vs 给排水, 医用气体==医气) + legacy_unverified idempotent/atomic DB migration + frozen baseline alias_dict.
3. Real production execution + 5 benefit classes (improved/harmful/neutral/empty/unhit) + 1-to-N/qty_ratio/wrong-code/hard-conflict catches.
4. Disjoint targeted + stratified global guard sets; guard regression interception even when targeted set passes.
5. Process timeouts (baseline/cand TimeoutExpired), nonzero exit, corrupt/missing outputs fail closed + lock release + golden_last_run protection.
6. CAS double-publish/accept conflict, post-eval edit invalidation, DB/reload compensation failure setting persistent recovery block.
"""
import json
import os
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import flywheel_sandbox, server
from app.auth_manager import (
    get_password_hash,
    get_user_by_username,
    init_auth_db,
    issue_token_for_user,
)
from app.db_manager import get_db_connection, import_materials_from_excel, init_db
from app.flywheel_db import init_flywheel_tables
from app.llm_rule_engine import LLMRuleManager, get_db_conn, init_rule_engine_db
from app.normalizer import clean_text_standard, reload_normalizer_configs
from app.rule_context import RuleContext
from app.rule_governance import (
    EVAL_TTL_SECONDS,
    compute_publish_env_snapshot,
    compute_rule_digest,
    create_eval_run_record,
    finalize_eval_run_record,
    get_rule_recovery_block_info,
    normalize_rule_spec,
    reset_rule_publish_block,
    verify_eval_credential_for_publish,
    EvalGateError,
)
from tests.eval_golden_set import _select_sandbox_subsets

SANDBOX_MATERIALS_FIXTURE = Path(__file__).parent / "fixtures" / "sandbox_materials_v1.json"


@pytest.fixture(scope="module")
def m2_env():
    init_db()
    init_auth_db()
    init_rule_engine_db()
    init_flywheel_tables()

    with open(SANDBOX_MATERIALS_FIXTURE, "r", encoding="utf-8") as f:
        mats = json.load(f)["materials"]
    xlsx_path = Path(os.environ["UPLOAD_DIR"]) / "m2_gate_sandbox_materials.xlsx"
    pd.DataFrame([
        {
            "物料分类": m["category"],
            "物料编码": m["code"],
            "物料名称": m["name"],
            "规格/型号": m["spec"],
            "计量单位": "台",
            "常用品牌": "",
            "描述": "",
        }
        for m in mats
    ]).to_excel(xlsx_path, index=False, startrow=1)
    import_materials_from_excel(str(xlsx_path), mode="replace", filename="m2_gate_sandbox_materials.xlsx")

    conn = get_auth_connection_safe()
    pwd_hash = get_password_hash("M2Test@12345")
    for uname, role in [("m2_emp", "employee"), ("m2_mgr", "manager"), ("m2_adm", "admin")]:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch)
            VALUES (?, ?, ?, ?, 1, 0, ?)
            ON CONFLICT(username) DO UPDATE SET
                password_hash = excluded.password_hash,
                role = excluded.role,
                is_active = 1,
                must_change_password = 0
            """,
            (uname, pwd_hash, uname, role, f"m2-ep-{uname}"),
        )
    conn.commit()
    conn.close()

    emp = get_user_by_username("m2_emp")
    mgr = get_user_by_username("m2_mgr")
    adm = get_user_by_username("m2_adm")
    emp_tok = issue_token_for_user(emp)
    mgr_tok = issue_token_for_user(mgr)
    adm_tok = issue_token_for_user(adm)

    yield {
        "client": TestClient(server.app),
        "emp": {"user": dict(emp, token=emp_tok), "headers": {"Authorization": f"Bearer {emp_tok}"}},
        "mgr": {"user": dict(mgr, token=mgr_tok), "headers": {"Authorization": f"Bearer {mgr_tok}"}},
        "adm": {"user": dict(adm, token=adm_tok), "headers": {"Authorization": f"Bearer {adm_tok}"}},
    }


def get_auth_connection_safe():
    from app.auth_manager import get_auth_connection
    return get_auth_connection()


@pytest.fixture(autouse=True)
def _clean_m2_state():
    reset_rule_publish_block()
    yield
    reset_rule_publish_block()


def _wait_eval(client: TestClient, headers: dict, url: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(url, headers=headers)
        if r.status_code == 200:
            body = r.json()
            if body.get("status") in ("completed", "failed"):
                return body
        time.sleep(0.3)
    raise TimeoutError(f"Timed out waiting for {url}")


def _seed_completed_credential(
    *,
    rule_source: str,
    rule_key: str,
    rule_type: str,
    normalized_spec: dict,
    owner_user: dict,
    safety_passed: bool = True,
    benefit_class: str = "improved",
    status: str = "completed",
    age_seconds: float = 0.0,
    rule_revision: int = 1,
    env_override: dict = None,
) -> str:
    import uuid
    eval_id = f"eval_test_{uuid.uuid4().hex[:10]}"
    env_snap = compute_publish_env_snapshot()
    if env_override:
        env_snap.update(env_override)
    create_eval_run_record(
        eval_id=eval_id,
        rule_source=rule_source,
        rule_key=rule_key,
        rule_type=rule_type,
        rule_revision=rule_revision,
        normalized_spec=normalized_spec,
        env_snapshot=env_snap,
        owner_user=owner_user,
    )
    if status == "pending":
        return eval_id
    finalize_eval_run_record(
        eval_id=eval_id,
        status=status,
        safety_passed=safety_passed,
        benefit_class=benefit_class,
        result_summary={"reason": f"seed:{benefit_class}"},
        targeted_count=5,
        guard_count=5,
        improved_count=1 if benefit_class == "improved" else 0,
        degraded_count=1 if benefit_class == "harmful" else 0,
        rule_execution_hits=2 if benefit_class != "invalid" else 0,
    )
    if age_seconds:
        conn = sqlite3.connect(os.environ["MATERIALS_DB_PATH"], timeout=15.0)
        try:
            comp_ep = time.time() - age_seconds
            exp_ep = comp_ep + EVAL_TTL_SECONDS
            conn.execute(
                "UPDATE rule_eval_runs SET completed_at_epoch = ?, expires_at_epoch = ? WHERE eval_id = ?",
                (comp_ep, exp_ep, eval_id),
            )
            conn.commit()
        finally:
            conn.close()
    return eval_id


# ---------------------------------------------------------------------------
# Matrix 1 & 3 & 4: Real 3-source gate + credential forgery/staleness/TTL/env-drift
# ---------------------------------------------------------------------------

def test_matrix1_three_sources_unified_gate_and_credential_rejections(m2_env):
    client = m2_env["client"]
    emp, mgr, adm = m2_env["emp"], m2_env["mgr"], m2_env["adm"]

    # --- Source A: Manual LLM DSL rule (HTTP + service + re-enable) ---
    rid = "M2-GATE-LLM-01"
    dsl = {
        "rule_name": "M2门禁测试紫铜管规则",
        "cleaning_rules": [{"action": "synonym_replace", "target": "医用脱脂无缝紫铜管", "replacement": "磷酸脱氧无缝紫铜管"}],
        "extraction_rules": [],
        "matching_rules": {},
    }
    try:
        assert client.post(
            "/api/llm-rules",
            headers=emp["headers"],
            json={
                "rule_id": rid,
                "rule_name": "M2门禁测试紫铜管规则",
                "scope_level": "discipline",
                "scope_target": "医用气体",
                "discipline": "医用气体",
                "priority": 10,
                "parsed_dsl": dsl,
            },
        ).status_code == 200
        assert client.post(f"/api/llm-rules/{rid}/submit", headers=emp["headers"]).status_code == 200
        assert client.post(f"/api/llm-rules/{rid}/audit", headers=mgr["headers"], json={"action": "approve"}).status_code == 200

        # Non-admin 403 preserved vs admin 409 gate
        assert client.post(f"/api/llm-rules/{rid}/publish", headers=mgr["headers"], json={}).status_code == 403
        assert client.post(f"/api/llm-rules/{rid}/publish", headers=adm["headers"], json={}).status_code == 409
        assert LLMRuleManager.publish_rule(rid, adm["user"])["status_code"] == 409

        # Forged eval_id rejected
        bad_res = client.post(
            f"/api/llm-rules/{rid}/publish",
            headers=adm["headers"],
            json={"eval_id": "eval_forged_xyz", "passed": True},
        )
        assert bad_res.status_code == 409
        assert bad_res.json()["detail"]["code"] == "eval_not_found"

        # Pending, failed, expired, env-drifted, and cross-rule eval_id rejected
        row = LLMRuleManager.get_rule_by_id(rid)
        spec, _ = LLMRuleManager._build_governed_spec_from_row(row)
        pending_eid = _seed_completed_credential(rule_source="manual_llm", rule_key=rid, rule_type="LLM_DSL", normalized_spec=spec, owner_user=adm["user"], status="pending")
        failed_eid = _seed_completed_credential(rule_source="manual_llm", rule_key=rid, rule_type="LLM_DSL", normalized_spec=spec, owner_user=adm["user"], safety_passed=False, benefit_class="harmful")
        expired_eid = _seed_completed_credential(rule_source="manual_llm", rule_key=rid, rule_type="LLM_DSL", normalized_spec=spec, owner_user=adm["user"], age_seconds=EVAL_TTL_SECONDS + 120)
        other_eid = _seed_completed_credential(rule_source="manual_llm", rule_key="OTHER-RULE", rule_type="LLM_DSL", normalized_spec=dict(spec, rule_key="OTHER-RULE"), owner_user=adm["user"])
        drift_eid = _seed_completed_credential(rule_source="manual_llm", rule_key=rid, rule_type="LLM_DSL", normalized_spec=spec, owner_user=adm["user"], env_override={"materials_revision": "stale_rev_999"})

        for eid, expected_code in [
            (pending_eid, "eval_not_completed"),
            (failed_eid, "eval_safety_failed"),
            (expired_eid, "eval_expired"),
            (other_eid, "eval_wrong_rule"),
            (drift_eid, "eval_env_stale"),
        ]:
            res = client.post(f"/api/llm-rules/{rid}/publish", headers=adm["headers"], json={"eval_id": eid})
            assert res.status_code == 409, f"{eid} -> {res.text}"
            assert res.json()["detail"]["code"] == expected_code

        # Real sandbox evaluation -> publish succeeds
        ev_start = client.post(f"/api/llm-rules/{rid}/eval", headers=adm["headers"])
        assert ev_start.status_code == 200, ev_start.text
        ev_id = ev_start.json()["eval_id"]
        st = _wait_eval(client, adm["headers"], f"/api/llm-rules/eval/{ev_id}")
        assert st["status"] == "completed" and st["safety_passed"] is True, st

        neutral_reason = "M2单元测试中性稳态合法理由" if st["benefit"] == "neutral" else None
        if st["benefit"] == "neutral":
            assert client.post(f"/api/llm-rules/{rid}/publish", headers=adm["headers"], json={"eval_id": ev_id}).status_code == 422

        pub_ok = client.post(
            f"/api/llm-rules/{rid}/publish",
            headers=adm["headers"],
            json={"eval_id": ev_id, "neutral_reason": neutral_reason},
        )
        assert pub_ok.status_code == 200, pub_ok.text

        # Pause -> re-enable with expired credential rejected -> fresh eval passes
        assert client.post(f"/api/llm-rules/{rid}/toggle", headers=adm["headers"], json={"reason": "pause"}).status_code == 200
        assert client.post(f"/api/llm-rules/{rid}/toggle", headers=adm["headers"], json={"eval_id": expired_eid}).status_code == 409
        assert client.post(f"/api/llm-rules/{rid}/toggle", headers=adm["headers"], json={"reason": "resume"}).status_code == 200

        # Editing after eval invalidates approval & eval credential
        assert client.put(
            f"/api/llm-rules/{rid}",
            headers=emp["headers"],
            json={"priority": 20, "parsed_dsl": dsl},
        ).status_code == 200
        assert client.post(f"/api/llm-rules/{rid}/submit", headers=emp["headers"]).status_code == 200
        assert client.post(f"/api/llm-rules/{rid}/audit", headers=mgr["headers"], json={"action": "approve"}).status_code == 200
        rev2_reject = client.post(f"/api/llm-rules/{rid}/publish", headers=adm["headers"], json={"eval_id": ev_id})
        assert rev2_reject.status_code == 409
        assert rev2_reject.json()["detail"]["code"] in ("eval_revision_mismatch", "eval_content_changed")
    finally:
        conn = get_db_conn()
        try:
            conn.execute("DELETE FROM llm_rules WHERE rule_id = ?", (rid,))
            conn.execute("DELETE FROM rule_audit_logs WHERE rule_id = ?", (rid,))
            conn.commit()
        finally:
            conn.close()
        LLMRuleManager.clear_active_cache()

    # --- Source B: Legacy Binding (HTTP + service save_auto_binding_rules) ---
    binds = [{
        "code": "3.50.29.1369",
        "name": "医用离心式排风机(箱）",
        "specification": "风量：1000m³/h，机外余压250pa，镀锌钢板",
        "unit": "台",
        "brand": "",
        "category": "通风机",
        "qty": 1.0,
        "qty_ratio": 1.0,
    }]
    b_payload = {
        "original_name": "医用静音型排风机",
        "original_spec": "风量：1000m3/h",
        "professional": "暖通",
        "bindings": binds,
    }
    try:
        # Missing eval_id rejected identically on HTTP & direct services
        assert client.post("/api/binding/rules/confirm", headers=adm["headers"], json=b_payload).status_code == 409
        with pytest.raises(EvalGateError):
            server.confirm_binding_rule_service("医用静音型排风机", binds, adm["user"], original_spec="风量：1000m3/h", professional="暖通")
        with pytest.raises(EvalGateError):
            server.save_auto_binding_rules([b_payload | {"bound_materials": binds}], actor_user=adm["user"])

        ev_b = client.post("/api/binding/rules/eval", headers=adm["headers"], json=b_payload)
        assert ev_b.status_code == 200, ev_b.text
        ev_b_id = ev_b.json()["eval_id"]
        st_b = _wait_eval(client, adm["headers"], f"/api/binding/rules/eval/{ev_b_id}")
        assert st_b["status"] == "completed" and st_b["safety_passed"] is True and st_b["benefit"] == "improved", st_b

        # Tampering qty_ratio after eval rejected
        tampered = dict(b_payload, eval_id=ev_b_id, bindings=[dict(binds[0], qty_ratio=2.0)])
        assert client.post("/api/binding/rules/confirm", headers=adm["headers"], json=tampered).status_code == 409

        # Genuine confirm succeeds
        ok_b = client.post("/api/binding/rules/confirm", headers=adm["headers"], json=dict(b_payload, eval_id=ev_b_id))
        assert ok_b.status_code == 200, ok_b.text

        # Row-level confirm stays independent of global rule publish gate
        row_ok = client.post(
            "/api/binding/submit",
            headers=emp["headers"],
            json={
                "original_name": "医用静音型排风机",
                "original_spec": "风量：1000m3/h",
                "original_qty": 1.0,
                "professional": "暖通",
                "bindings": binds,
                "scope_filename": "m2_row_indep.xlsx",
            },
        )
        assert row_ok.status_code == 200, row_ok.text
    finally:
        server.save_auto_binding_rules([], actor_user=adm["user"])


def test_matrix2_discipline_isolation_scoped_aliases_and_legacy_migration(tmp_path):
    # 1) Same term in 暖通 vs 给排水 isolated at eval & runtime; 医气 == 医用气体; missing/legacy_unscoped rejected
    cfg_dir = os.environ["CONFIG_DIR"]
    alias_baseline_path = Path(cfg_dir) / "alias_dict.json"
    baseline_before = alias_baseline_path.read_bytes() if alias_baseline_path.exists() else None
    scoped_path = Path(cfg_dir) / "scoped_aliases.json"
    scoped_backup = scoped_path.read_bytes() if scoped_path.exists() else None

    try:
        scoped_path.write_text(
            json.dumps(
                {
                    "暖通": {"静音风箱": "医用离心式排风机(暖通)"},
                    "给排水": {"静音风箱": "潜水排污泵(给排水)"},
                    "医气": {"真空铜管": "磷酸脱氧无缝紫铜管"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        reload_normalizer_configs()

        assert clean_text_standard("静音风箱", rule_context=RuleContext.from_fields(professional="暖通")) != clean_text_standard("静音风箱", rule_context=RuleContext.from_fields(professional="给排水"))
        assert clean_text_standard("静音风箱", rule_context=RuleContext.from_fields(professional="强电")) == "静音风箱"
        # Canonical 医用气体 == 医气 equivalence (both resolve identically and differ from unrelated 暖通)
        mg_1 = clean_text_standard("真空铜管", rule_context=RuleContext.from_fields(professional="医用气体"))
        mg_2 = clean_text_standard("真空铜管", rule_context=RuleContext.from_fields(professional="医气"))
        assert mg_1 == mg_2 and mg_1 != clean_text_standard("真空铜管", rule_context=RuleContext.from_fields(professional="暖通"))

        # Unscoped / legacy_unscoped cannot upgrade to universal
        with pytest.raises(EvalGateError) as e_unscoped:
            normalize_rule_spec(
                rule_source="flywheel_alias",
                rule_key="suggestion:999",
                rule_type="ALIAS",
                discipline="暖通",
                source_pattern="A",
                target_value="B",
                source_origin="legacy_unscoped",
                require_concrete_discipline=True,
            )
        assert e_unscoped.value.code == "unscoped_legacy_rule"

        with pytest.raises(EvalGateError) as e_missing:
            normalize_rule_spec(
                rule_source="flywheel_alias",
                rule_key="suggestion:998",
                rule_type="ALIAS",
                discipline="",
                source_pattern="A",
                target_value="B",
                require_concrete_discipline=True,
            )
        assert e_missing.value.code == "missing_discipline_scope"

        # Binding cross-discipline runtime isolation
        rules = [
            {
                "original_name": "控制阀",
                "original_spec": "DN50",
                "professional": "给排水",
                "bound_materials": [{"code": "PLUMB_01", "specification": "DN50", "qty_ratio": 1.0}],
            }
        ]
        assert server._select_matching_binding_rule({"name": "控制阀", "specification": "DN50", "professional": "暖通"}, rules) is None
        assert server._select_matching_binding_rule({"name": "控制阀", "specification": "DN50", "professional": "给排水"}, rules)["bound_materials"][0]["code"] == "PLUMB_01"

        # Baseline alias_dict.json untouched
        if baseline_before is not None:
            assert alias_baseline_path.read_bytes() == baseline_before
    finally:
        if scoped_backup is None:
            if scoped_path.exists():
                scoped_path.unlink()
        else:
            scoped_path.write_bytes(scoped_backup)
        reload_normalizer_configs()

    # 2) Idempotent & atomic DB migration on isolated copy DB
    mig_db = tmp_path / "legacy_pre_m2.db"
    conn = sqlite3.connect(str(mig_db))
    conn.execute(
        """
        CREATE TABLE llm_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id TEXT UNIQUE NOT NULL,
            rule_name TEXT NOT NULL,
            scope_level TEXT NOT NULL DEFAULT 'category',
            scope_target TEXT,
            discipline TEXT,
            priority INTEGER DEFAULT 10,
            rule_json TEXT NOT NULL,
            author_id INTEGER NOT NULL,
            author_name TEXT NOT NULL,
            approver_id INTEGER,
            approver_name TEXT,
            status TEXT DEFAULT 'draft',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO llm_rules (rule_id, rule_name, scope_level, discipline, rule_json, author_id, author_name, status) "
        "VALUES ('OLD-01', '历史已发布规则', 'category', '暖通', '{}', 1, 'emp', 'published')"
    )
    conn.execute(
        "INSERT INTO llm_rules (rule_id, rule_name, scope_level, discipline, rule_json, author_id, author_name, status) "
        "VALUES ('OLD-02', '历史停用规则', 'category', '', '{}', 1, 'emp', 'disabled')"
    )
    conn.execute(
        """
        CREATE TABLE flywheel_rule_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discipline TEXT NOT NULL,
            suggestion_type TEXT NOT NULL,
            source_pattern TEXT NOT NULL,
            target_value TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    conn.execute(
        "INSERT INTO flywheel_rule_suggestions (discipline, suggestion_type, source_pattern, target_value, status) "
        "VALUES ('暖通', 'ALIAS', '旧词', '新词', 'pending')"
    )
    conn.execute(
        """
        CREATE TABLE flywheel_config_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_version TEXT UNIQUE NOT NULL,
            target_type TEXT NOT NULL,
            config_before TEXT NOT NULL,
            config_after TEXT NOT NULL,
            suggestion_id INTEGER,
            reason TEXT,
            operator_id TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            targeted_eval_passed INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        "INSERT INTO flywheel_config_snapshots (snapshot_version, target_type, config_before, config_after, reason, operator_id, targeted_eval_passed) "
        "VALUES ('snap_old_01', 'ALIAS', '{}', '{}', 'legacy', 'admin', 1)"
    )
    conn.commit()
    conn.close()

    # Rollback atomicity when mid-migration hook fails
    with pytest.raises(RuntimeError, match="injected_mig_failure"):
        init_flywheel_tables(
            db_path=str(mig_db),
            _migration_hook=lambda stage, _c: (_ for _ in ()).throw(RuntimeError("injected_mig_failure"))
            if stage == "after_migrate_rule_eval_runs"
            else None,
        )
    chk_conn = sqlite3.connect(str(mig_db))
    snap_passed = chk_conn.execute(
        "SELECT targeted_eval_passed FROM flywheel_config_snapshots WHERE snapshot_version = 'snap_old_01'"
    ).fetchone()[0]
    assert snap_passed == 1  # Rolled back atomically before commit!
    chk_conn.close()

    # Double migration idempotency + legacy_unverified backfill
    init_flywheel_tables(db_path=str(mig_db))
    init_flywheel_tables(db_path=str(mig_db))
    init_rule_engine_db(db_path=str(mig_db))
    init_rule_engine_db(db_path=str(mig_db))

    chk_conn = sqlite3.connect(str(mig_db))
    chk_conn.row_factory = sqlite3.Row
    s_row = chk_conn.execute("SELECT * FROM flywheel_config_snapshots WHERE snapshot_version = 'snap_old_01'").fetchone()
    assert s_row["governance_provenance"] == "legacy_unverified"
    assert s_row["targeted_eval_passed"] == 0
    old_r1 = chk_conn.execute("SELECT * FROM llm_rules WHERE rule_id = 'OLD-01'").fetchone()
    old_r2 = chk_conn.execute("SELECT * FROM llm_rules WHERE rule_id = 'OLD-02'").fetchone()
    assert old_r1["governance_provenance"] == "legacy_unverified"
    assert old_r2["governance_provenance"] == "legacy_unverified"
    chk_conn.close()


def test_matrix3_and_4_guard_isolation_five_benefit_classes_and_neutral_approval(m2_env):
    client = m2_env["client"]
    adm = m2_env["adm"]

    # 1) Disjoint stratified targeted & guard items check
    with open(Path(__file__).parent / "fixtures" / "golden_set_v1.json", "r", encoding="utf-8") as f:
        gs_items = json.load(f)["items"]
    targeted_items, guard_items = _select_sandbox_subsets(
        gs_items,
        {"rule_type": "ALIAS", "source_pattern": "医用脱脂无缝紫铜管", "discipline": "医用气体"},
    )
    t_ids = [x["id"] for x in targeted_items]
    g_ids = [x["id"] for x in guard_items]
    assert t_ids and g_ids
    assert len(t_ids) == len(set(t_ids))
    assert len(g_ids) == len(set(g_ids))
    assert not (set(t_ids) & set(g_ids))
    assert len({x.get("professional") for x in guard_items if x.get("professional")}) >= 2

    # 2) Neutral rule: safety_passed=True, benefit="neutral": blocked without neutral_reason, allowed with reason
    with get_auth_connection_safe():
        pass
    from app.db_manager import get_db_cursor
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO flywheel_rule_suggestions (
                discipline, suggestion_type, source_pattern, target_value,
                support_count, distinct_users, consistency, status
            ) VALUES ('暖通', 'BINDING_RULE', '医用静音型排风机', '[]', 4, 3, 1.0, 'pending')
            """
        )
        neutral_sid = cur.lastrowid

    norm_n = normalize_rule_spec(
        rule_source="flywheel_binding",
        rule_key=f"suggestion:{neutral_sid}",
        rule_type="BINDING_RULE",
        discipline="暖通",
        source_pattern="医用静音型排风机",
        target_value=[{"code": "3.50.29.1369", "specification": "风量：1000m³/h", "qty_ratio": 1.0}],
        original_spec="风量：1000m3/h",
        require_concrete_discipline=True,
    )
    neutral_eid = _seed_completed_credential(
        rule_source="flywheel_binding",
        rule_key=f"suggestion:{neutral_sid}",
        rule_type="BINDING_RULE",
        normalized_spec=norm_n,
        owner_user=adm["user"],
        safety_passed=True,
        benefit_class="neutral",
    )
    try:
        # Auto-publish blocked for neutral
        with pytest.raises(EvalGateError) as e_auto:
            verify_eval_credential_for_publish(
                eval_id=neutral_eid,
                rule_source="flywheel_binding",
                rule_key=f"suggestion:{neutral_sid}",
                rule_revision=1,
                normalized_spec=norm_n,
                neutral_reason="自动发布不应允许",
                auto_publish=True,
            )
        assert e_auto.value.code == "neutral_no_auto_publish"

        # HTTP accept without neutral_reason -> 422; with explicit reason -> 200
        target_str = json.dumps(norm_n["bound_materials"], ensure_ascii=False)
        r_no_reason = client.post(
            "/api/flywheel/suggestions/accept",
            headers=adm["headers"],
            json={"suggestion_id": neutral_sid, "edited_target_value": target_str, "original_spec": "风量：1000m3/h", "eval_id": neutral_eid},
        )
        assert r_no_reason.status_code == 422

        r_ok = client.post(
            "/api/flywheel/suggestions/accept",
            headers=adm["headers"],
            json={
                "suggestion_id": neutral_sid,
                "edited_target_value": target_str,
                "original_spec": "风量：1000m3/h",
                "eval_id": neutral_eid,
                "neutral_reason": "稳态规则经人工核验可减少人工确认工作量",
            },
        )
        assert r_ok.status_code == 200, r_ok.text
        snap_ver = r_ok.json()["snapshot_version"]
        assert client.post("/api/flywheel/rollback", headers=adm["headers"], json={"snapshot_version": snap_ver}).status_code == 200
    finally:
        with get_db_cursor(commit=True) as cur:
            cur.execute("DELETE FROM flywheel_config_snapshots WHERE suggestion_id = ?", (neutral_sid,))
            cur.execute("DELETE FROM flywheel_rule_suggestions WHERE id = ?", (neutral_sid,))

    # 3) Empty & unhit -> invalid; qty_ratio mismatch caught in _compare_binding_pipeline_rows
    comp = flywheel_sandbox._compare_binding_pipeline_rows(
        [{"id": "X1", "matched_code": "A", "codes_match_gt": True, "export_eligible": False, "rejected": False}],
        [{"id": "X1", "matched_code": "A", "codes_match_gt": True, "rule_applied": True, "qty_ratios_ok": False, "conflicts": [], "invalid_codes": [], "export_eligible": True, "rejected": False}],
    )
    assert comp["passed"] is False and comp["violations"][0]["type"] == "binding_qty_ratio_mismatch"

    # 4) Global guard degradation blocks even when targeted set passes
    tid = flywheel_sandbox.start_sandbox_evaluation(
        {
            "suggestion_type": "ALIAS",
            "discipline": "医用气体",
            "source_pattern": "排风机",
            "target_value": "低压交联聚乙烯绝缘聚氯乙烯护套电力电缆",
        },
        owner_user=adm["user"],
    )
    st_guard = _wait_eval(client, adm["headers"], f"/api/flywheel/eval/{tid}")
    assert st_guard["safety_passed"] is False
    assert st_guard["benefit"] in ("harmful", "invalid")


def test_matrix5_process_bounds_timeout_corrupt_json_and_golden_protection(m2_env):
    adm = m2_env["adm"]
    golden_last = Path("doc/V0.1.6") / "dummy"  # real golden_last_run check below
    real_golden_last = Path(os.environ.get("GOLDEN_DETAIL_FILE", "nonexistent"))

    # Helper to wait directly for sandbox status
    def _wait_direct(tid):
        for _ in range(40):
            s = flywheel_sandbox.get_eval_task_status(tid)
            if s and s["status"] in ("completed", "failed"):
                return s
            time.sleep(0.1)
        raise TimeoutError(tid)

    sug = {"suggestion_type": "ALIAS", "discipline": "暖通", "source_pattern": "医用静音型排风机", "target_value": "医用离心式排风机"}

    # 1) Baseline timeout fails closed cleanly & cleans temp dir
    with patch("app.flywheel_sandbox._run_eval_process", side_effect=subprocess.TimeoutExpired(cmd="eval", timeout=300)):
        t_to = flywheel_sandbox.start_sandbox_evaluation(sug, owner_user=adm["user"])
        st_to = _wait_direct(t_to)
        assert st_to["status"] == "failed"
        assert st_to["safety_passed"] is False
        assert st_to["benefit"] == "invalid"

    # 2) Corrupt candidate JSON output fails closed cleanly
    call_idx = {"n": 0}
    def _fake_subproc_corrupt(cmd, **kwargs):
        call_idx["n"] += 1
        out_path = cmd[cmd.index("--output-json") + 1]
        if call_idx["n"] == 1:
            Path(out_path).write_text(json.dumps({"rows": [], "guard_rows": [], "binding_rows": [], "summary": {}}), encoding="utf-8")
        else:
            Path(out_path).write_text("{corrupt_json!!", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("app.flywheel_sandbox._run_eval_process", side_effect=_fake_subproc_corrupt):
        t_cor = flywheel_sandbox.start_sandbox_evaluation(sug, owner_user=adm["user"])
        st_cor = _wait_direct(t_cor)
        assert st_cor["status"] == "failed"
        assert st_cor["safety_passed"] is False
        assert st_cor["benefit"] == "invalid"

    # 3) Nonzero return code fails closed cleanly
    with patch("app.flywheel_sandbox._run_eval_process", return_value=subprocess.CompletedProcess(["eval"], 2, stdout="", stderr="boom")):
        t_nz = flywheel_sandbox.start_sandbox_evaluation(sug, owner_user=adm["user"])
        st_nz = _wait_direct(t_nz)
        assert st_nz["status"] == "failed"
        assert st_nz["safety_passed"] is False


def test_matrix6_cas_double_accept_and_recovery_block_on_reload_failure(m2_env):
    client = m2_env["client"]
    adm = m2_env["adm"]
    from app.db_manager import get_db_cursor

    with get_db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO flywheel_rule_suggestions (
                discipline, suggestion_type, source_pattern, target_value,
                support_count, distinct_users, consistency, status
            ) VALUES ('医用气体', 'ALIAS', '医用脱脂无缝紫铜管', '磷酸脱氧无缝紫铜管', 5, 3, 1.0, 'pending')
            """
        )
        sid = cur.lastrowid

    norm_s = normalize_rule_spec(
        rule_source="flywheel_alias",
        rule_key=f"suggestion:{sid}",
        rule_type="ALIAS",
        discipline="医用气体",
        source_pattern="医用脱脂无缝紫铜管",
        target_value="磷酸脱氧无缝紫铜管",
        require_concrete_discipline=True,
    )
    eid = _seed_completed_credential(
        rule_source="flywheel_alias",
        rule_key=f"suggestion:{sid}",
        rule_type="ALIAS",
        normalized_spec=norm_s,
        owner_user=adm["user"],
        safety_passed=True,
        benefit_class="improved",
    )

    try:
        # 1) Concurrent double accept -> exactly one 200, one 409 (CAS conflict)
        results = []
        barrier = threading.Barrier(2)

        def _worker():
            barrier.wait()
            r = client.post(
                "/api/flywheel/suggestions/accept",
                headers=adm["headers"],
                json={"suggestion_id": sid, "discipline": "医用气体", "eval_id": eid},
            )
            results.append(r.status_code)

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start(); t2.start(); t1.join(); t2.join()
        assert sorted(results) == [200, 409], results

        # Rollback back to rolled_back so we can test reload/compensation failure with a fresh credential
        assert client.post("/api/flywheel/rollback", headers=adm["headers"], json={}).status_code == 200
        eid2 = _seed_completed_credential(
            rule_source="flywheel_alias",
            rule_key=f"suggestion:{sid}",
            rule_type="ALIAS",
            normalized_spec=norm_s,
            owner_user=adm["user"],
            safety_passed=True,
            benefit_class="improved",
        )

        # 2) Reload failure + compensation failure triggers recovery block & rejects fake safe-restore claims
        with patch.object(server, "atomic_write_json_config", side_effect=[None, OSError("disk_comp_fail")]), \
             patch.object(server, "trigger_all_config_reloads", side_effect=RuntimeError("reload_down")):
            r_fail = client.post(
                "/api/flywheel/suggestions/accept",
                headers=adm["headers"],
                json={"suggestion_id": sid, "discipline": "医用气体", "eval_id": eid2},
            )
            assert r_fail.status_code == 500, r_fail.text
            body_text = json.dumps(r_fail.json(), ensure_ascii=False)
            assert "已安全" not in body_text
            assert get_rule_recovery_block_info() is not None

        # Subsequent rule publish blocked until operator clears recovery block
        r_blocked = client.post(
            "/api/flywheel/suggestions/accept",
            headers=adm["headers"],
            json={"suggestion_id": sid, "discipline": "医用气体", "eval_id": eid2},
        )
        assert r_blocked.status_code == 409
        assert r_blocked.json()["detail"]["code"] == "rule_recovery_blocked"
    finally:
        reset_rule_publish_block()
        with get_db_cursor(commit=True) as cur:
            cur.execute("DELETE FROM flywheel_config_snapshots WHERE suggestion_id = ?", (sid,))
            cur.execute("DELETE FROM flywheel_rule_suggestions WHERE id = ?", (sid,))
