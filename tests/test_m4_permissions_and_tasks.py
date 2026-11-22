# -*- coding: utf-8 -*-
"""V015-M4 / F04 + F21 + F11: Rule governance state machine, task ownership, and deploy/build boundary tests."""

import io
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import auth_manager, flywheel_sandbox, server
from app.audit_manager import init_audit_tables
from app.auth_manager import (
    get_auth_connection,
    get_password_hash,
    get_user_by_username,
    init_auth_db,
    issue_token_for_user,
)
from app.db_manager import init_db
from app.flywheel_db import init_flywheel_tables
from app.llm_rule_engine import (
    LLMRuleManager,
    get_db_conn as get_rule_db_conn,
    init_rule_engine_db,
)
from app.session_manager import init_session_db
from scripts._deploy_env import DeployConfigError, load_probe_targets, load_ssh_deploy_config


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _setup_actors():
    init_db()
    init_auth_db()
    init_audit_tables()
    init_session_db()
    init_flywheel_tables()
    init_rule_engine_db()

    actors = {}
    specs = [
        ("f04_emp1", "employee", "F04EMP1"),
        ("f04_emp2", "employee", "F04EMP2"),
        ("f04_mgr1", "manager", "F04MGR1"),
        ("f04_dir1", "director", "F04DIR1"),
        ("f04_adm1", "admin", "F04ADM1"),
        ("f04_adm2", "admin", "F04ADM2"),
    ]
    conn = get_auth_connection()
    pwd_hash = get_password_hash("ActorStrong#2026!")
    for uname, role, emp_no in specs:
        conn.execute(
            """
            INSERT INTO users (
                username, password_hash, name, employee_no, phone, role,
                is_active, token_version, must_change_password, account_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 1, 0, ?)
            ON CONFLICT(username) DO UPDATE SET
                role = excluded.role,
                is_active = 1,
                must_change_password = 0
            """,
            (uname, pwd_hash, uname, emp_no, uname, role, f"ep-{uname}"),
        )
    conn.commit()
    conn.close()

    client = TestClient(server.app)
    for uname, _, _ in specs:
        user = get_user_by_username(uname)
        assert user is not None
        token = issue_token_for_user(user)
        actors[uname] = {
            "user": user,
            "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
        }
    return client, actors


def _cleanup_rule(rule_id: str):
    conn = get_rule_db_conn()
    try:
        conn.execute("DELETE FROM llm_rules WHERE rule_id = ?", (rule_id,))
        conn.execute("DELETE FROM rule_audit_logs WHERE rule_id = ?", (rule_id,))
        conn.commit()
    finally:
        conn.close()
    LLMRuleManager.clear_active_cache()


def test_f04_rule_state_machine_spoof_isolation_and_publish_gate():
    """Verify full F04 workflow: stats order, spoof resistance, ownership, 2-actor publish, edit invalidation, toggle, delete."""
    client, actors = _setup_actors()
    emp1 = actors["f04_emp1"]
    emp2 = actors["f04_emp2"]
    mgr1 = actors["f04_mgr1"]
    dir1 = actors["f04_dir1"]
    adm1 = actors["f04_adm1"]
    adm2 = actors["f04_adm2"]

    rid = "M4-F04-RULE-01"
    _cleanup_rule(rid)

    try:
        # 1. /api/llm-rules/stats must match static route (200), not /{rule_id} (404)
        stats_res = client.get("/api/llm-rules/stats", headers=emp1["headers"])
        assert stats_res.status_code == 200, stats_res.text
        assert "published_count" in stats_res.json()

        # 2. Employee saves draft with spoofed admin/published/author_id body -> server forces draft & emp1 owner
        dsl_v1 = {
            "rule_name": "M4状态机验证规则",
            "cleaning_rules": [{"action": "synonym_replace", "target": "医用脱脂无缝紫铜管", "replacement": "磷酸脱氧无缝紫铜管"}],
            "extraction_rules": [],
            "matching_rules": {"must_not_contain": ["M4_FORBIDDEN_KW"]},
        }
        save_res = client.post(
            "/api/llm-rules",
            headers=emp1["headers"],
            json={
                "rule_id": rid,
                "rule_name": "M4状态机验证规则",
                "scope_level": "discipline",
                "scope_target": "医用气体",
                "discipline": "医用气体",
                "priority": 10,
                "status": "published",
                "author_id": 99999,
                "approver_id": 88888,
                "publisher_id": 77777,
                "role": "admin",
                "parsed_dsl": dsl_v1,
            },
        )
        assert save_res.status_code == 200, save_res.text
        saved_rule = client.get(f"/api/llm-rules/{rid}", headers=emp1["headers"]).json()
        assert saved_rule["status"] == "draft"
        assert saved_rule["author_id"] == emp1["user"]["id"]
        assert saved_rule["approver_id"] is None
        assert saved_rule["publisher_id"] is None

        # 3. Cross-user employee cannot modify, submit, withdraw, or delete emp1's rule (403)
        assert client.put(
            f"/api/llm-rules/{rid}",
            headers=emp2["headers"],
            json={"description": "hijack"},
        ).status_code == 403
        assert client.post(f"/api/llm-rules/{rid}/submit", headers=emp2["headers"]).status_code == 403
        assert client.delete(f"/api/llm-rules/{rid}", headers=emp2["headers"]).status_code == 403

        # 4. Submit -> pending_approval -> direct edit returns 409 -> withdraw -> draft -> edit works
        assert client.post(f"/api/llm-rules/{rid}/submit", headers=emp1["headers"]).status_code == 200
        assert client.put(
            f"/api/llm-rules/{rid}",
            headers=emp1["headers"],
            json={"description": "edit while pending"},
        ).status_code == 409
        assert client.post(f"/api/llm-rules/{rid}/withdraw", headers=emp2["headers"]).status_code == 403
        assert client.post(f"/api/llm-rules/{rid}/withdraw", headers=emp1["headers"]).status_code == 200
        assert client.get(f"/api/llm-rules/{rid}", headers=emp1["headers"]).json()["status"] == "draft"
        assert client.put(
            f"/api/llm-rules/{rid}",
            headers=emp1["headers"],
            json={"description": "updated after withdraw", "parsed_dsl": dsl_v1},
        ).status_code == 200

        # Re-submit -> pending_approval
        assert client.post(f"/api/llm-rules/{rid}/submit", headers=emp1["headers"]).status_code == 200

        # 5. Self-review blocked; manager/director deprecate blocked (403)
        assert client.post(
            f"/api/llm-rules/{rid}/audit",
            headers=emp1["headers"],
            json={"action": "approve", "comment": "self"},
        ).status_code == 403
        assert client.post(
            f"/api/llm-rules/{rid}/audit",
            headers=mgr1["headers"],
            json={"action": "deprecate", "comment": "mgr cannot deprecate"},
        ).status_code == 403

        # Manager approves -> approved_pending_publish (NOT yet active in get_active_rules!)
        aud_res = client.post(
            f"/api/llm-rules/{rid}/audit",
            headers=mgr1["headers"],
            json={"action": "approve", "comment": "approved v1"},
        )
        assert aud_res.status_code == 200, aud_res.text
        assert aud_res.json()["status"] == "approved_pending_publish"
        assert rid not in [r["rule_id"] for r in LLMRuleManager.get_active_rules()]

        # 6. Employee/Manager/Director cannot publish (403)
        for non_adm in (emp1, mgr1, dir1):
            assert client.post(f"/api/llm-rules/{rid}/publish", headers=non_adm["headers"], json={}).status_code == 403

        # 7. Stale expected_revision or expected_digest triggers 409
        assert client.post(
            f"/api/llm-rules/{rid}/publish",
            headers=adm1["headers"],
            json={"expected_revision": 999},
        ).status_code == 409
        assert client.post(
            f"/api/llm-rules/{rid}/publish",
            headers=adm1["headers"],
            json={"expected_digest": "deadbeef" * 8},
        ).status_code == 409

        # 8. Untested publish blocked (409); after M2 sandbox eval admin publishes -> active
        assert client.post(f"/api/llm-rules/{rid}/publish", headers=adm1["headers"], json={}).status_code == 409

        import json as _json
        import pandas as _pd
        from app.db_manager import import_materials_from_excel
        sm_path = Path(__file__).parent / "fixtures" / "sandbox_materials_v1.json"
        with open(sm_path, "r", encoding="utf-8") as _f:
            _mats = _json.load(_f)["materials"]
        _xlsx = Path(os.environ["UPLOAD_DIR"]) / "m4_eval_mats.xlsx"
        _pd.DataFrame([
            {
                "物料分类": m["category"], "物料编码": m["code"],
                "物料名称": m["name"], "规格/型号": m["spec"],
                "计量单位": "台", "常用品牌": "", "描述": "",
            }
            for m in _mats
        ]).to_excel(_xlsx, index=False, startrow=1)
        import_materials_from_excel(str(_xlsx), mode="replace", filename="m4_eval_mats.xlsx")

        ev_start = client.post(f"/api/llm-rules/{rid}/eval", headers=adm1["headers"])
        assert ev_start.status_code == 200, ev_start.text
        ev_id = ev_start.json()["eval_id"]
        st_data = None
        for _ in range(90):
            time.sleep(0.5)
            st_res = client.get(f"/api/llm-rules/eval/{ev_id}", headers=adm1["headers"])
            if st_res.status_code == 200 and st_res.json().get("status") in ("completed", "failed"):
                st_data = st_res.json()
                break
        assert st_data and st_data["status"] == "completed" and st_data["safety_passed"] is True, st_data

        pub_res = client.post(
            f"/api/llm-rules/{rid}/publish",
            headers=adm1["headers"],
            json={
                "eval_id": ev_id,
                "neutral_reason": "M4状态机验证稳态通过" if st_data.get("benefit") == "neutral" else None,
            },
        )
        assert pub_res.status_code == 200, pub_res.text
        assert pub_res.json()["status"] == "published"
        assert rid in [r["rule_id"] for r in LLMRuleManager.get_active_rules()]

        # 9. Toggle disabled <-> published is admin-only
        assert client.post(f"/api/llm-rules/{rid}/toggle", headers=mgr1["headers"], json={}).status_code == 403
        assert client.post(f"/api/llm-rules/{rid}/toggle", headers=adm1["headers"], json={"reason": "pause"}).status_code == 200
        assert rid not in [r["rule_id"] for r in LLMRuleManager.get_active_rules()]
        assert client.post(f"/api/llm-rules/{rid}/toggle", headers=adm1["headers"], json={"reason": "resume"}).status_code == 200
        assert rid in [r["rule_id"] for r in LLMRuleManager.get_active_rules()]

        # Cannot delete active/published rule (409)
        assert client.delete(f"/api/llm-rules/{rid}", headers=emp1["headers"]).status_code == 409

        # 10. Post-publish edit reverts rule to draft, clears prior approval/publish fields & clears active cache
        dsl_v2 = {
            "rule_name": "M4状态机验证规则V2",
            "cleaning_rules": [],
            "extraction_rules": [],
            "matching_rules": {"must_not_contain": ["M4_V2_KW"]},
        }
        upd_res = client.put(
            f"/api/llm-rules/{rid}",
            headers=emp1["headers"],
            json={"parsed_dsl": dsl_v2},
        )
        assert upd_res.status_code == 200, upd_res.text
        after_edit = client.get(f"/api/llm-rules/{rid}", headers=emp1["headers"]).json()
        assert after_edit["status"] == "draft"
        assert after_edit["approver_id"] is None
        assert after_edit["publisher_id"] is None
        assert rid not in [r["rule_id"] for r in LLMRuleManager.get_active_rules()]

        # 11. Admin proxy edit requires explicit opt-in & transfers authorship, then blocks same-admin self-review!
        assert client.put(
            f"/api/llm-rules/{rid}",
            headers=adm1["headers"],
            json={"description": "missing opt-in"},
        ).status_code == 403
        proxy_res = client.put(
            f"/api/llm-rules/{rid}",
            headers=adm1["headers"],
            json={"description": "admin proxy edit", "admin_proxy_edit": True},
        )
        assert proxy_res.status_code == 200, proxy_res.text
        after_proxy = client.get(f"/api/llm-rules/{rid}", headers=adm1["headers"]).json()
        assert after_proxy["author_id"] == adm1["user"]["id"]

        assert client.post(f"/api/llm-rules/{rid}/submit", headers=adm1["headers"]).status_code == 200
        # Even admin cannot approve their own rule (author_id == approver_id -> 403)
        self_aud = client.post(
            f"/api/llm-rules/{rid}/audit",
            headers=adm1["headers"],
            json={"action": "approve", "comment": "self-approve attempt"},
        )
        assert self_aud.status_code == 403

        # Director approves, then another admin deprecates
        assert client.post(
            f"/api/llm-rules/{rid}/audit",
            headers=dir1["headers"],
            json={"action": "approve", "comment": "director approval"},
        ).status_code == 200
        assert client.post(
            f"/api/llm-rules/{rid}/audit",
            headers=adm2["headers"],
            json={"action": "deprecate", "comment": "retire rule"},
        ).status_code == 200
        assert client.get(f"/api/llm-rules/{rid}", headers=adm2["headers"]).json()["status"] == "deprecated"
    finally:
        _cleanup_rule(rid)

    # 12. Direct service-layer call rejects unverified/spoofed actor dicts & author deletes own draft
    rid_del = "M4-F04-DEL-02"
    _cleanup_rule(rid_del)
    try:
        spoof = LLMRuleManager.save_rule_draft(
            {"rule_id": rid_del, "rule_name": "spoof", "parsed_dsl": {"matching_rules": {"must_not_contain": ["X"]}}},
            {"id": 999999, "username": "ghost_admin", "role": "admin"},
        )
        assert spoof["success"] is False and spoof["status_code"] == 401

        r_ok = LLMRuleManager.save_rule_draft(
            {"rule_id": rid_del, "rule_name": "author draft", "parsed_dsl": {"matching_rules": {"must_not_contain": ["X"]}}},
            emp1["user"],
        )
        assert r_ok["success"] is True
        del_res = client.delete(f"/api/llm-rules/{rid_del}", headers=emp1["headers"])
        assert del_res.status_code == 200
    finally:
        _cleanup_rule(rid_del)


def test_f04_legacy_rule_migration_and_disabled_re_enable_blocked(tmp_path, monkeypatch):
    """Legacy 'approved' rules without dual provenance migrate idempotently to 'disabled' and cannot be toggled active."""
    rule_db = str(tmp_path / "legacy_rules.db")
    auth_db = str(tmp_path / "legacy_rules_auth.db")
    monkeypatch.setenv("MATERIALS_DB_PATH", rule_db)
    monkeypatch.setenv("AUTH_DB_PATH", auth_db)
    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", auth_db)
    init_auth_db()

    conn = sqlite3.connect(rule_db)
    conn.executescript(
        """
        CREATE TABLE llm_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id TEXT UNIQUE NOT NULL,
            rule_name TEXT NOT NULL,
            scope_level TEXT NOT NULL,
            scope_target TEXT,
            discipline TEXT,
            rule_json TEXT NOT NULL,
            description TEXT,
            author_id INTEGER,
            author_name TEXT,
            approver_id INTEGER,
            approver_name TEXT,
            status TEXT DEFAULT 'draft',
            audit_comment TEXT,
            priority INTEGER DEFAULT 10,
            hit_count INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            approved_at DATETIME
        );
        """
    )
    conn.execute(
        "INSERT INTO llm_rules (rule_id, rule_name, scope_level, discipline, rule_json, author_id, status) VALUES (?,?,?,?,?,?,?)",
        (
            "LEGACY-APPROVED-1",
            "旧未背书规则",
            "universal",
            "全专业",
            json.dumps({"matching_rules": {"must_not_contain": ["LEGACY_BAD"]}}, ensure_ascii=False),
            1,
            "approved",
        ),
    )
    conn.commit()
    conn.close()

    init_rule_engine_db()
    init_rule_engine_db()
    LLMRuleManager.clear_active_cache()

    assert LLMRuleManager.get_active_rules() == []
    migrated = LLMRuleManager.get_rule_by_id("LEGACY-APPROVED-1")
    assert migrated is not None and migrated["status"] == "disabled"

    adm = get_user_by_username("admin")
    issue_token_for_user(adm)
    tog = LLMRuleManager.toggle_rule_status("LEGACY-APPROVED-1", adm, reason="try illegal enable")
    assert tog["success"] is False and tog["status_code"] == 409


def test_f04_binding_rules_admin_only_while_row_confirm_stays_working():
    """Global binding rule routes and service functions require admin; normal employee row confirm/reject keeps working."""
    client, actors = _setup_actors()
    emp1 = actors["f04_emp1"]
    mgr1 = actors["f04_mgr1"]
    adm1 = actors["f04_adm1"]

    orig_name = "医用静音型排风机"
    bindings = [{"code": "3.50.29.1369", "name": "医用离心式排风机(箱）", "specification": "风量：1000m³/h，机外余压250pa，镀锌钢板", "unit": "台", "qty": 1.0, "brand": "", "category": "暖通", "qty_ratio": 1.0}]
    payload = {"original_name": orig_name, "original_spec": "风量：1000m3/h", "professional": "暖通", "bindings": bindings}

    try:
        # HTTP confirm & delete reject employee/manager with 403
        assert client.post("/api/binding/rules/confirm", headers=emp1["headers"], json=payload).status_code == 403
        assert client.post("/api/binding/rules/confirm", headers=mgr1["headers"], json=payload).status_code == 403
        assert client.post("/api/binding/rules/delete", headers=emp1["headers"], json={"original_name": orig_name}).status_code == 403

        # Direct service-layer bypass attempt also rejected
        with pytest.raises(PermissionError):
            server.confirm_binding_rule_service(orig_name, bindings, emp1["user"])
        with pytest.raises(PermissionError):
            server.delete_binding_rule_service(orig_name, mgr1["user"])
        with pytest.raises(PermissionError):
            server.save_auto_binding_rules([], actor_user={"id": emp1["user"]["id"], "username": "f04_emp1", "role": "admin"})

        # Admin confirm without eval_id blocked (409), then eval -> confirm -> delete succeed (200)
        assert client.post("/api/binding/rules/confirm", headers=adm1["headers"], json=payload).status_code == 409

        import json as _json
        import pandas as _pd
        from app.db_manager import import_materials_from_excel
        sm_path = Path(__file__).parent / "fixtures" / "sandbox_materials_v1.json"
        with open(sm_path, "r", encoding="utf-8") as _f:
            _mats = _json.load(_f)["materials"]
        _xlsx = Path(os.environ["UPLOAD_DIR"]) / "m4_bind_eval_mats.xlsx"
        _pd.DataFrame([
            {
                "物料分类": m["category"], "物料编码": m["code"],
                "物料名称": m["name"], "规格/型号": m["spec"],
                "计量单位": "台", "常用品牌": "", "描述": "",
            }
            for m in _mats
        ]).to_excel(_xlsx, index=False, startrow=1)
        import_materials_from_excel(str(_xlsx), mode="replace", filename="m4_bind_eval_mats.xlsx")

        ev_bind = client.post("/api/binding/rules/eval", headers=adm1["headers"], json=payload)
        assert ev_bind.status_code == 200, ev_bind.text
        ev_bind_id = ev_bind.json()["eval_id"]
        bind_st = None
        for _ in range(90):
            time.sleep(0.5)
            st_r = client.get(f"/api/binding/rules/eval/{ev_bind_id}", headers=adm1["headers"])
            if st_r.status_code == 200 and st_r.json().get("status") in ("completed", "failed"):
                bind_st = st_r.json()
                break
        assert bind_st and bind_st["status"] == "completed" and bind_st["safety_passed"] is True, bind_st

        ok_conf = client.post("/api/binding/rules/confirm", headers=adm1["headers"], json=dict(payload, eval_id=ev_bind_id))
        assert ok_conf.status_code == 200, ok_conf.text
        ok_del = client.post("/api/binding/rules/delete", headers=adm1["headers"], json={"original_name": orig_name})
        assert ok_del.status_code == 200, ok_del.text

        # Regular employee row-level reject still works
        rej = client.post(
            "/api/binding/reject",
            headers=emp1["headers"],
            json={
                "original_name": "闸阀",
                "original_spec": "DN50",
                "original_qty": 1.0,
                "bindings": bindings,
                "scope_filename": "m4-sess-1.xlsx",
            },
        )
        assert rej.status_code == 200, rej.text
    finally:
        server.delete_binding_rule_service(orig_name, adm1["user"])


def test_f21_task_ownership_across_upload_db_and_flywheel_eval():
    """Tasks on /api/upload, /api/db/upload, and /api/flywheel/eval strictly belong to creator user+epoch (uniform 404 otherwise)."""
    client, actors = _setup_actors()
    emp1 = actors["f04_emp1"]
    emp2 = actors["f04_emp2"]
    adm1 = actors["f04_adm1"]
    adm2 = actors["f04_adm2"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["序号", "项目名称", "规格型号", "单位", "数量"])
    ws.append([1, "镀锌钢管", "DN25", "m", 10])
    buf = io.BytesIO()
    wb.save(buf)
    xlsx_bytes = buf.getvalue()

    with patch("app.server.async_process_upload_task", lambda *a, **kw: None):
        up1 = client.post(
            "/api/upload",
            headers=emp1["headers"],
            files={"file": ("emp1_boq.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert up1.status_code == 200, up1.text
    up_task_id = up1.json()["task_id"]

    # Creator reads 200; other employee, admin, and nonexistent task all read 404
    assert client.get(f"/api/upload/status?task_id={up_task_id}", headers=emp1["headers"]).status_code == 200
    assert client.get(f"/api/upload/status?task_id={up_task_id}", headers=emp2["headers"]).status_code == 404
    assert client.get(f"/api/upload/status?task_id={up_task_id}", headers=adm1["headers"]).status_code == 404
    assert client.get("/api/upload/status?task_id=nonexistent-task-id", headers=emp1["headers"]).status_code == 404

    # /api/db/upload: admin1 creator gets 200, admin2 and emp1 get 404
    with patch("app.server.async_process_db_upload_task", lambda *a, **kw: None):
        try:
            db_up = client.post(
                "/api/db/upload",
                headers=adm1["headers"],
                data={"mode": "append"},
                files={"file": ("db_import.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
            assert db_up.status_code == 200, db_up.text
            db_task_id = db_up.json()["task_id"]
            assert client.get(f"/api/upload/status?task_id={db_task_id}", headers=adm1["headers"]).status_code == 200
            assert client.get(f"/api/upload/status?task_id={db_task_id}", headers=adm2["headers"]).status_code == 404
            assert client.get(f"/api/upload/status?task_id={db_task_id}", headers=emp1["headers"]).status_code == 404
        finally:
            if server._db_import_lock.locked():
                server._db_import_lock.release()

    # Flywheel sandbox eval ownership
    from app.db_manager import get_db_cursor

    with get_db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO flywheel_rule_suggestions (
                discipline, suggestion_type, source_pattern, target_value,
                support_count, distinct_users, consistency, status
            ) VALUES ('暖通', 'ALIAS', 'M4_ALIAS_SRC', 'M4_ALIAS_TGT', 3, 2, 0.9, 'pending')
            """
        )
        sug_id = cur.lastrowid

    dispatched = threading.Semaphore(0)
    with patch("app.flywheel_sandbox._run_sandbox_worker", side_effect=lambda *a, **kw: dispatched.release()):
        ev_emp = client.post("/api/flywheel/suggestions/eval", headers=emp1["headers"], json={"suggestion_id": sug_id})
        assert ev_emp.status_code == 403

        ev_adm1 = client.post("/api/flywheel/suggestions/eval", headers=adm1["headers"], json={"suggestion_id": sug_id})
        assert ev_adm1.status_code == 200, ev_adm1.text
        eval_task_id = ev_adm1.json()["task_id"]

        assert client.get(f"/api/flywheel/eval/{eval_task_id}", headers=adm1["headers"]).status_code == 200
        assert client.get(f"/api/flywheel/eval/{eval_task_id}", headers=adm2["headers"]).status_code == 404
        assert client.get(f"/api/flywheel/eval/{eval_task_id}", headers=emp1["headers"]).status_code == 403
        assert client.get("/api/flywheel/eval/missing-eval-id", headers=adm1["headers"]).status_code == 404

        orphan_task_id = flywheel_sandbox.start_sandbox_evaluation(
            {"suggestion_type": "ALIAS", "source_pattern": "测试排风机", "target_value": "排风机", "discipline": "暖通"},
            owner_user=None,
        )
        assert client.get(f"/api/flywheel/eval/{orphan_task_id}", headers=adm1["headers"]).status_code == 404
        for task_id in (eval_task_id, orphan_task_id):
            if flywheel_sandbox.get_eval_task_status(task_id)["status"] == "pending":
                assert dispatched.acquire(timeout=5)

    # Recreating same user id + username with new account_epoch must invalidate previous task visibility
    emp1_uid = emp1["user"]["id"]
    conn = get_auth_connection()
    conn.execute("UPDATE users SET account_epoch = 'recreated-new-epoch' WHERE id = ?", (emp1_uid,))
    conn.commit()
    conn.close()
    reloaded_emp1 = get_user_by_username("f04_emp1")
    new_tok = issue_token_for_user(reloaded_emp1)
    assert client.get(
        f"/api/upload/status?task_id={up_task_id}",
        headers={"Authorization": f"Bearer {new_tok}"},
    ).status_code == 404


def test_f11_deploy_fail_closed_zero_hardcoded_secrets_and_docker_boundary(tmp_path, monkeypatch):
    """Verify F11 fail-closed deploy env loader, zero leaked credential literals, and Docker allowlist/ignore rules."""
    for var in (
        "DEPLOY_HOST",
        "DEPLOY_PORT",
        "DEPLOY_USER",
        "DEPLOY_REMOTE_DIR",
        "DEPLOY_SSH_KEY_PATH",
        "DEPLOY_SSH_PASSWORD",
        "DEPLOY_PROBE_HOSTS",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(DeployConfigError):
        load_ssh_deploy_config(require_remote_dir=True)
    with pytest.raises(DeployConfigError):
        load_probe_targets()

    key_file = tmp_path / "id_ed25519"
    key_file.write_text("dummy-private-key-path-marker", encoding="utf-8")
    monkeypatch.setenv("DEPLOY_HOST", "203.0.113.10")
    monkeypatch.setenv("DEPLOY_USER", "deploy")
    monkeypatch.setenv("DEPLOY_REMOTE_DIR", "/srv/listtrans")
    monkeypatch.setenv("DEPLOY_SSH_KEY_PATH", str(key_file))
    monkeypatch.setenv("DEPLOY_PROBE_HOSTS", "203.0.113.10,203.0.113.11")

    cfg = load_ssh_deploy_config(require_remote_dir=True)
    assert cfg.host == "203.0.113.10"
    assert cfg.key_path == str(key_file)
    assert load_probe_targets() == ["203.0.113.10", "203.0.113.11"]

    # Zero hardcoded passwords / weak secrets across deployment scripts & templates
    forbidden_tokens = [
        "hkjj@12345",
        "ListTrans_2026_Secure_Key",
        "Admin123",
    ]
    script_targets = [
        PROJECT_ROOT / "scripts" / "_deploy_env.py",
        PROJECT_ROOT / "scripts" / "deploy_cloud.py",
        PROJECT_ROOT / "scripts" / "deploy_to_cloud_safe.py",
        PROJECT_ROOT / "scripts" / "check_cloud_status.py",
        PROJECT_ROOT / "scripts" / "probe_servers.py",
        PROJECT_ROOT / ".env.example",
    ]
    for path in script_targets:
        text = path.read_text(encoding="utf-8")
        for secret_tok in forbidden_tokens:
            assert secret_tok not in text, f"Forbidden secret token '{secret_tok}' found in {path}"
        assert "AutoAddPolicy" not in text, f"Insecure AutoAddPolicy found in {path}"

    # .env.example must keep placeholders rather than non-empty secret values
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "<REPLACE_" in env_example

    # Dockerfile must NOT use broad `COPY . /app/` and must keep single-worker uvicorn
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^\s*COPY\s+\.\s+", dockerfile, flags=re.MULTILINE)
    assert "COPY app/*.py" in dockerfile
    assert "COPY web/" in dockerfile
    assert "COPY config/" in dockerfile
    assert "--workers" in dockerfile and "1" in dockerfile

    # .dockerignore patterns must cover env secrets, DBs + WAL/SHM/journal, scripts, doc, keys, tests
    dockerignore_lines = [
        ln.strip()
        for ln in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    for required_ignore in (
        ".env",
        ".env.*",
        "*.db",
        "*.db-wal",
        "*.db-shm",
        "*.db-journal",
        "*.db.*",
        "app/*.db",
        "app/*.db-*",
        "app/*.db.*",
        "data/",
        "*.csv",
        "*.backup",
        "*backup*",
        "*credential*",
        "config/*",
        "!config/alias_dict.json",
        "!config/auto_binding_rules.json",
        "!config/model_config.json",
        "scripts/",
        "doc/",
        "*.pem",
        "*.key",
        "tests/",
        "golden_last_run/",
    ):
        assert required_ignore in dockerignore_lines, f"Missing '{required_ignore}' in .dockerignore"


def test_r02_a_m4_02_service_layer_rejects_unauthenticated_forged_or_revoked_actors():
    """A-M4-02: Service helpers require verified unrevoked sessions and block actor_user=None or forged dicts."""
    client, actors = _setup_actors()
    emp1 = actors["f04_emp1"]
    mgr1 = actors["f04_mgr1"]
    adm1 = actors["f04_adm1"]
    adm2 = actors["f04_adm2"]

    rid = "R02-A-M4-02-SVC"
    _cleanup_rule(rid)
    rules_backup = None
    if Path(server.AUTO_BINDING_RULES_FILE).exists():
        rules_backup = Path(server.AUTO_BINDING_RULES_FILE).read_text(encoding="utf-8")

    try:
        # Prepare approved_pending_publish rule cleanly
        assert LLMRuleManager.save_rule_draft(
            {
                "rule_id": rid,
                "rule_name": "R02服务权限边界",
                "scope_level": "discipline",
                "scope_target": "医用气体",
                "discipline": "医用气体",
                "parsed_dsl": {
                    "cleaning_rules": [{"action": "synonym_replace", "target": "医用脱脂无缝紫铜管", "replacement": "磷酸脱氧无缝紫铜管"}],
                    "matching_rules": {"must_not_contain": ["BADX"]},
                },
            },
            emp1["user"],
        )["success"] is True
        assert LLMRuleManager.submit_rule(rid, emp1["user"])["success"] is True
        assert LLMRuleManager.audit_rule(rid, "approve", "mgr ok", mgr1["user"])["success"] is True

        # 1. Forged dict pointing to real active admin id/username WITHOUT signed token must fail (401)
        forged_admin_no_tok = {"id": adm1["user"]["id"], "username": adm1["user"]["username"], "role": "employee"}
        forged_admin_role = {"id": adm1["user"]["id"], "username": adm1["user"]["username"], "role": "admin"}
        pub_forged_1 = LLMRuleManager.publish_rule(rid, forged_admin_no_tok)
        pub_forged_2 = LLMRuleManager.publish_rule(rid, forged_admin_role)
        assert pub_forged_1["success"] is False and pub_forged_1["status_code"] == 401
        assert pub_forged_2["success"] is False and pub_forged_2["status_code"] == 401
        assert LLMRuleManager.get_rule_by_id(rid)["status"] == "approved_pending_publish"

        # 2. Tampering role claim on an authenticated employee dict also rejected (401)
        tampered_emp = dict(emp1["user"], role="admin")
        assert LLMRuleManager.publish_rule(rid, tampered_emp)["success"] is False

        # 3. Logged-out (revoked jti) admin dict rejected immediately across rule & binding & sandbox services
        logout_adm2 = client.post("/api/auth/logout", headers=adm2["headers"])
        assert logout_adm2.status_code == 200
        pub_revoked = LLMRuleManager.publish_rule(rid, adm2["user"])
        assert pub_revoked["success"] is False and pub_revoked["status_code"] == 401
        with pytest.raises(PermissionError):
            server.save_auto_binding_rules([{"original_name": "R02_REVOKED", "bound_materials": []}], actor_user=adm2["user"])

        # 4. Calling save_auto_binding_rules without actor_user (None) or with forged/employee dict raises PermissionError
        for bad_actor in (None, forged_admin_no_tok, forged_admin_role, emp1["user"], mgr1["user"]):
            with pytest.raises(PermissionError):
                server.save_auto_binding_rules([{"original_name": "R02_BYPASS", "bound_materials": []}], actor_user=bad_actor)
        assert not any(r.get("original_name") == "R02_BYPASS" for r in server.load_auto_binding_rules())

        # 5. Genuine unrevoked admin dict succeeds on publish (with M2 eval) & save_auto_binding_rules([])
        import json as _json
        import pandas as _pd
        from app.db_manager import import_materials_from_excel
        sm_path = Path(__file__).parent / "fixtures" / "sandbox_materials_v1.json"
        with open(sm_path, "r", encoding="utf-8") as _f:
            _mats = _json.load(_f)["materials"]
        _xlsx = Path(os.environ["UPLOAD_DIR"]) / "r02_eval_mats.xlsx"
        _pd.DataFrame([
            {
                "物料分类": m["category"], "物料编码": m["code"],
                "物料名称": m["name"], "规格/型号": m["spec"],
                "计量单位": "台", "常用品牌": "", "描述": "",
            }
            for m in _mats
        ]).to_excel(_xlsx, index=False, startrow=1)
        import_materials_from_excel(str(_xlsx), mode="replace", filename="r02_eval_mats.xlsx")

        ev_s = LLMRuleManager.start_rule_evaluation(rid, adm1["user"])
        assert ev_s["success"] is True, ev_s
        ev_s_id = ev_s["eval_id"]
        st_s = None
        for _ in range(90):
            time.sleep(0.5)
            cur_st = flywheel_sandbox.get_eval_task_status(ev_s_id)
            if cur_st and cur_st.get("status") in ("completed", "failed"):
                st_s = cur_st
                break
        assert st_s and st_s["status"] == "completed" and st_s["safety_passed"] is True, st_s
        pub_ok = LLMRuleManager.publish_rule(
            rid,
            adm1["user"],
            eval_id=ev_s_id,
            neutral_reason="R02边界正例稳态发布" if st_s.get("benefit") == "neutral" else None,
        )
        assert pub_ok["success"] is True and pub_ok["status"] == "published", pub_ok
        server.save_auto_binding_rules([], actor_user=adm1["user"])

        # 6. Direct flywheel task query rejects forged actor without session token
        dispatched = threading.Event()
        with patch("app.flywheel_sandbox._run_sandbox_worker", side_effect=lambda *a, **kw: dispatched.set()):
            tid = flywheel_sandbox.start_sandbox_evaluation(
                {"suggestion_type": "ALIAS", "source_pattern": "测试排风机", "target_value": "排风机", "discipline": "暖通"},
                owner_user=adm1["user"],
            )
            assert dispatched.wait(timeout=5)
        assert flywheel_sandbox.get_eval_task_status_for_user(tid, adm1["user"]) is not None
        assert flywheel_sandbox.get_eval_task_status_for_user(tid, forged_admin_role) is None
        assert flywheel_sandbox.get_eval_task_status_for_user(tid, adm2["user"]) is None
    finally:
        _cleanup_rule(rid)
        if rules_backup is not None:
            Path(server.AUTO_BINDING_RULES_FILE).write_text(rules_backup, encoding="utf-8")
