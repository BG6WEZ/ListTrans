# -*- coding: utf-8 -*-
"""
V016-M0 / F05: 反馈身份、文档-工作表-源行归属、幂等/乱序/并发、真实版本追溯与旧数据迁移专项回归。
"""
import hashlib
import io
import json
import os
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import auth_manager, feedback_contract, feedback_train, flywheel_db, server, session_manager
from app.audit_manager import init_audit_tables
from app.auth_manager import (
    get_auth_connection,
    get_password_hash,
    get_user_by_username,
    init_auth_db,
    issue_token_for_user,
)
from app.db_manager import get_db_cursor, import_materials_from_excel, init_db
from app.flywheel_db import init_flywheel_tables
from app.session_manager import init_session_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "sandbox_materials_v1.json"


def _make_two_sheet_xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "暖通"
    ws1.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"])
    ws1.append(["030101", "排风机", "风量：1000m3/h", "台", 2])
    ws1.append(["030102", "止回阀", "公称直径 DN100", "台", 4])

    ws2 = wb.create_sheet("强电")
    ws2.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"])
    ws2.append(["030401", "电力电缆", "YJV 4x25", "米", 100])
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def _create_actor(username: str, role: str = "employee", epoch: str | None = None) -> dict:
    conn = get_auth_connection()
    pwd_hash = get_password_hash("StrongM0Pass#2026!")
    ep = epoch or f"ep-{username}-{uuid.uuid4().hex[:8]}"
    emp_no = f"M0_{username}_{uuid.uuid4().hex[:4]}"
    conn.execute(
        """
        INSERT INTO users (
            username, password_hash, name, employee_no, phone, role,
            is_active, token_version, must_change_password, account_epoch
        ) VALUES (?, ?, ?, ?, ?, ?, 1, 1, 0, ?)
        ON CONFLICT(username) DO UPDATE SET
            password_hash = excluded.password_hash,
            role = excluded.role,
            is_active = 1,
            must_change_password = 0,
            account_epoch = excluded.account_epoch
        """,
        (username, pwd_hash, f"姓名_{username}", emp_no, username, role, ep),
    )
    conn.commit()
    conn.close()
    user = get_user_by_username(username)
    assert user is not None
    token = issue_token_for_user(user)
    return {
        "user": user,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def _upload_workbook(client: TestClient, headers: dict, filename: str, content: bytes) -> dict:
    up = client.post(
        "/api/upload",
        headers=headers,
        files={
            "file": (
                filename,
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert up.status_code == 200, up.text
    up_json = up.json()
    task_id = up_json["task_id"]
    st = client.get(f"/api/upload/status?task_id={task_id}", headers=headers)
    assert st.status_code == 200, st.text
    st_json = st.json()
    assert st_json["status"] == "completed", st_json
    return {
        "upload": up_json,
        "status": st_json,
        "rows": st_json["result"],
    }


@pytest.fixture(scope="module")
def m0_env():
    init_db()
    init_auth_db()
    init_session_db()
    init_audit_tables()
    init_flywheel_tables()

    materials = json.loads(SANDBOX_FIXTURE.read_text(encoding="utf-8"))["materials"]
    xlsx_path = Path(os.environ["UPLOAD_DIR"]) / "m0_contract_materials.xlsx"
    pd.DataFrame(
        [
            {
                "物料分类": m["category"],
                "物料编码": m["code"],
                "物料名称": m["name"],
                "规格/型号": m["spec"],
                "计量单位": "台",
                "常用品牌": "",
                "描述": "",
            }
            for m in materials
        ]
    ).to_excel(xlsx_path, index=False, startrow=1)
    meta = import_materials_from_excel(str(xlsx_path), mode="replace", filename=xlsx_path.name)
    assert meta.get("imported_rows", 0) > 0

    client = TestClient(server.app)
    u1 = _create_actor(f"m0_u1_{uuid.uuid4().hex[:6]}", "employee")
    u2 = _create_actor(f"m0_u2_{uuid.uuid4().hex[:6]}", "employee")
    adm = _create_actor(f"m0_adm_{uuid.uuid4().hex[:6]}", "admin")
    return {"client": client, "u1": u1, "u2": u2, "adm": adm}


def test_c01_c02_cross_user_cross_upload_cross_sheet_isolation(m0_env):
    """2 users x 2 identical uploads x 2 sheets x identical row numbers never collide or supersede across objects."""
    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]
    u2 = m0_env["u2"]
    xlsx_bytes = _make_two_sheet_xlsx_bytes()

    u1_up1 = _upload_workbook(client, u1["headers"], "same_list.xlsx", xlsx_bytes)
    u1_up2 = _upload_workbook(client, u1["headers"], "same_list.xlsx", xlsx_bytes)
    u2_up1 = _upload_workbook(client, u2["headers"], "same_list.xlsx", xlsx_bytes)

    assert u1_up1["upload"]["document_id"] != u1_up2["upload"]["document_id"]
    assert u1_up1["upload"]["document_id"] != u2_up1["upload"]["document_id"]
    assert u1_up1["upload"]["work_session_id"] != u1_up2["upload"]["work_session_id"]
    assert u1_up1["upload"]["document_id"] != u1_up1["upload"]["work_session_id"]

    # Sheet 1 row 1 vs Sheet 2 row 1 (physical row 2 in both sheets!)
    u1_d1_s1_r1 = next(r for r in u1_up1["rows"] if r["sheet_name"] == "暖通" and r["source_row_num"] == 2)
    u1_d1_s2_r1 = next(r for r in u1_up1["rows"] if r["sheet_name"] == "强电" and r["source_row_num"] == 2)
    u1_d2_s1_r1 = next(r for r in u1_up2["rows"] if r["sheet_name"] == "暖通" and r["source_row_num"] == 2)
    u2_d1_s1_r1 = next(r for r in u2_up1["rows"] if r["sheet_name"] == "暖通" and r["source_row_num"] == 2)

    assert len({
        u1_d1_s1_r1["source_row_id"],
        u1_d1_s2_r1["source_row_id"],
        u1_d2_s1_r1["source_row_id"],
        u2_d1_s1_r1["source_row_id"],
    }) == 4
    assert u1_d1_s1_r1["sheet_id"] != u1_d1_s2_r1["sheet_id"]

    # Record actions on all 4 distinct objects with overlapping display/physical row_idx
    targets = [
        (u1["headers"], u1_d1_s1_r1, "3.50.29.1369", "u1_d1_s1"),
        (u1["headers"], u1_d1_s2_r1, "3.50.29.1369", "u1_d1_s2"),
        (u1["headers"], u1_d2_s1_r1, "3.50.29.1369", "u1_d2_s1"),
        (u2["headers"], u2_d1_s1_r1, "2.17.01.0001", "u2_d1_s1"),
    ]
    evt_ids = []
    fb_ids = []
    for headers, row, code, tag in targets:
        eid = f"evt_iso_{tag}_{uuid.uuid4().hex[:6]}"
        evt_ids.append(eid)
        r = client.post(
            "/api/flywheel/record",
            headers=headers,
            json={
                "client_event_id": eid,
                "document_id": row["document_id"],
                "sheet_id": row["sheet_id"],
                "source_row_id": row["source_row_id"],
                "recommendation_snapshot_id": row["recommendation_snapshot_id"],
                "event_seq": 1,
                "action_type": "MANUAL_REPLACE",
                "final_targets": [{"code": code, "name": f"物料_{tag}", "spec": "DN100"}],
            },
        )
        assert r.status_code == 200, r.text

        fb_id = f"fb_iso_{tag}_{uuid.uuid4().hex[:6]}"
        fb_ids.append(fb_id)
        rfb = client.post(
            "/api/feedback/submit",
            headers=headers,
            json={
                "client_event_id": fb_id,
                "document_id": row["document_id"],
                "sheet_id": row["sheet_id"],
                "source_row_id": row["source_row_id"],
                "event_seq": 1,
                "row_id": row["original_row_idx"],
                "raw_text": row["original_spec"],
                "auto_code": row["initial_auto_code"],
                "manual_code": code,
                "is_correct": False,
            },
        )
        assert rfb.status_code == 200, rfb.text

    with get_db_cursor() as cur:
        rows = cur.execute(
            f"SELECT * FROM flywheel_records WHERE client_event_id IN ({','.join('?' for _ in evt_ids)})",
            evt_ids,
        ).fetchall()
        assert len(rows) == 4
        assert all(r["superseded"] == 0 for r in rows)
        assert all(r["trust_level"] == "trusted" for r in rows)

        fb_rows = cur.execute(
            f"SELECT * FROM flywheel_feedback_samples WHERE client_event_id IN ({','.join('?' for _ in fb_ids)})",
            fb_ids,
        ).fetchall()
        assert len(fb_rows) == 4
        assert all(r["superseded"] == 0 for r in fb_rows)


def test_c01_c03_auth_ownership_and_anti_tampering(m0_env):
    """Unauthenticated 401, missing/forged/default_session 422, cross-user 404, tampered payload ignored/rejected."""
    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]
    u2 = m0_env["u2"]
    up = _upload_workbook(client, u1["headers"], "auth_probe.xlsx", _make_two_sheet_xlsx_bytes())
    row = up["rows"][0]
    row2 = up["rows"][1]

    # 1. Unauthenticated 401 on all feedback/trace endpoints
    assert client.post("/api/flywheel/record", json={}).status_code == 401
    assert client.post("/api/feedback/submit", json={}).status_code == 401
    assert client.get("/api/flywheel/runtime-version").status_code == 401
    assert client.get(f"/api/flywheel/documents/{row['document_id']}/rows/{row['source_row_id']}/trace").status_code == 401

    with get_db_cursor() as cur:
        before_fw = cur.execute("SELECT COUNT(*) FROM flywheel_records").fetchone()[0]
        before_fb = cur.execute("SELECT COUNT(*) FROM flywheel_feedback_samples").fetchone()[0]

    # 2. Missing / default_session / forged sheet or row / mismatched snapshot -> 422
    bad_payloads = [
        {"client_event_id": "bad1", "session_id": "default_session", "event_seq": 1, "action_type": "MANUAL_REPLACE", "final_targets": []},
        {"client_event_id": "bad2", "document_id": "default_session", "sheet_id": row["sheet_id"], "source_row_id": row["source_row_id"], "event_seq": 1, "action_type": "MANUAL_REPLACE", "final_targets": []},
        {"client_event_id": "bad3", "document_id": row["document_id"], "sheet_id": "forged_sheet", "source_row_id": row["source_row_id"], "event_seq": 1, "action_type": "MANUAL_REPLACE", "final_targets": []},
        {"client_event_id": "bad4", "document_id": row["document_id"], "sheet_id": row["sheet_id"], "source_row_id": "forged_row", "event_seq": 1, "action_type": "MANUAL_REPLACE", "final_targets": []},
        {"client_event_id": "bad5", "document_id": row["document_id"], "sheet_id": row["sheet_id"], "source_row_id": row["source_row_id"], "recommendation_snapshot_id": row2["recommendation_snapshot_id"], "event_seq": 1, "action_type": "MANUAL_REPLACE", "final_targets": []},
        {"client_event_id": "bad6", "document_id": row["document_id"], "sheet_id": row["sheet_id"], "source_row_id": row["source_row_id"], "event_seq": 1, "action_type": "MANUAL_REPLACE", "final_targets": [], "user_id": u2["user"]["id"]},
    ]
    for bp in bad_payloads:
        r = client.post("/api/flywheel/record", headers=u1["headers"], json=bp)
        assert r.status_code == 422, (bp, r.status_code, r.text)

    # Same strict validation on /api/feedback/submit
    for bp in [
        {"row_id": 1, "raw_text": "x", "auto_code": "a", "manual_code": "b", "is_correct": False},
        {"client_event_id": "badfb1", "document_id": row["document_id"], "sheet_id": "bad_sheet", "source_row_id": row["source_row_id"], "event_seq": 1, "manual_code": "b", "is_correct": False},
    ]:
        rfb = client.post("/api/feedback/submit", headers=u1["headers"], json=bp)
        assert rfb.status_code == 422, (bp, rfb.status_code, rfb.text)

    # 3. Cross-user & unissued document -> uniform 404 anti-enumeration
    for target_doc in (row["document_id"], f"doc_{uuid.uuid4().hex}"):
        r_cross = client.post(
            "/api/flywheel/record",
            headers=u2["headers"],
            json={
                "client_event_id": f"cross_{uuid.uuid4().hex[:6]}",
                "document_id": target_doc,
                "sheet_id": row["sheet_id"],
                "source_row_id": row["source_row_id"],
                "event_seq": 1,
                "action_type": "MANUAL_REPLACE",
                "final_targets": [{"code": "3.50.29.1369", "name": "x"}],
            },
        )
        assert r_cross.status_code == 404, r_cross.text
        assert client.get(
            f"/api/flywheel/documents/{target_doc}/rows/{row['source_row_id']}/trace",
            headers=u2["headers"],
        ).status_code == 404

    with get_db_cursor() as cur:
        after_fw = cur.execute("SELECT COUNT(*) FROM flywheel_records").fetchone()[0]
        after_fb = cur.execute("SELECT COUNT(*) FROM flywheel_feedback_samples").fetchone()[0]
        assert after_fw == before_fw
        assert after_fb == before_fb

    # 4. Client tampering of raw_name, discipline, auto_top1_code, algo_version, config_version is ignored by server
    tamper_evt = f"evt_tamper_{uuid.uuid4().hex[:8]}"
    r_tamper = client.post(
        "/api/flywheel/record",
        headers=u1["headers"],
        json={
            "client_event_id": tamper_evt,
            "document_id": row["document_id"],
            "sheet_id": row["sheet_id"],
            "source_row_id": row["source_row_id"],
            "event_seq": 1,
            "discipline": "伪造专业",
            "raw_name": "伪造物料名称",
            "raw_spec": "伪造规格",
            "auto_top1_code": "FORGED_CODE",
            "auto_candidates": [{"code": "FORGED_CODE", "name": "伪造", "score": 99.9}],
            "algo_version": "v1.0",
            "config_version": "snap_v1",
            "action_type": "MANUAL_REPLACE",
            "final_targets": [{"code": "3.50.29.1369", "name": "医用离心式排风机(箱）", "spec": "1000m3/h"}],
        },
    )
    assert r_tamper.status_code == 200, r_tamper.text
    body = r_tamper.json()
    assert body["algo_version"] != "v1.0"
    assert body["config_version"] != "snap_v1"

    trace = client.get(
        f"/api/flywheel/documents/{row['document_id']}/rows/{row['source_row_id']}/trace",
        headers=u1["headers"],
    )
    assert trace.status_code == 200
    eff = trace.json()["effective_record"]
    assert eff["raw_name"] == row["original_name"]
    assert eff["discipline"] == row["professional"]
    assert eff["auto_top1_code"] != "FORGED_CODE"
    assert eff["algo_version"] == row["algo_version"]
    assert eff["config_version"] == row["config_version"]
    assert eff["actor_user_id"] == u1["user"]["id"]
    assert eff["actor_account_epoch"] == u1["user"]["account_epoch"]

    # 5. Feedback record does NOT bypass M2 export gate (must still be 422 without /api/binding/submit)
    exp = client.post(
        "/api/export",
        headers=u1["headers"],
        json={
            "scope_filename": "auth_probe.xlsx",
            "items": [
                {
                    "original_row_idx": row["original_row_idx"],
                    "original_name": row["original_name"],
                    "original_spec": row["original_spec"],
                    "original_qty": row["original_qty"],
                    "original_unit": row["original_unit"],
                    "original_loss": row["original_loss"],
                    "matched_code": "3.50.29.1369",
                    "matched_name": "医用离心式排风机(箱）",
                    "matched_category": "通风",
                    "matched_spec": "1000m3/h",
                    "matched_unit": "台",
                    "matched_brand": "",
                    "professional": row["professional"],
                    "decision_status": "manual_confirmed",
                    "bindings": [
                        {
                            "code": "3.50.29.1369",
                            "name": "医用离心式排风机(箱）",
                            "specification": "1000m3/h",
                            "unit": "台",
                            "brand": "",
                            "category": "通风",
                            "qty": row["original_qty"],
                        }
                    ],
                }
            ],
        },
    )
    assert exp.status_code == 422


def test_c04_c05_rematch_revision_initial_immutability_and_version_recompute(m0_env, tmp_path):
    """Rematch creates revision>=2 without overwriting initial revision=1; config_version is back-computable and reflects config edits."""
    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]
    up = _upload_workbook(client, u1["headers"], "rematch_rev.xlsx", _make_two_sheet_xlsx_bytes())
    row = up["rows"][0]

    # Verify config_version matches independent back-computation from CONFIG_DIR + published rules
    rt = client.get("/api/flywheel/runtime-version", headers=u1["headers"]).json()
    recomputed = feedback_contract.compute_runtime_versions()
    assert rt["config_version"] == recomputed["config_version"] == row["config_version"]
    assert rt["config_digest_full"] == recomputed["config_digest_full"] == row["config_digest_full"]

    # When vector model is disabled/unloaded, model_version must explicitly report unavailable (never fake loaded)
    with patch.object(type(server.index_manager), "is_enabled", property(lambda self: False)):
        rt_disabled = client.get("/api/flywheel/runtime-version", headers=u1["headers"]).json()
        assert "unavailable:vector_disabled" in rt_disabled["model_version"]

    # Trigger /api/rematch bound to the server row
    rem = client.post(
        "/api/rematch",
        headers=u1["headers"],
        json={
            "row_id": row["original_row_idx"],
            "text": row["original_spec"],
            "professional": row["professional"],
            "category": row["original_category"],
            "brand": row["original_brand"],
            "unit": row["original_unit"],
            "document_id": row["document_id"],
            "sheet_id": row["sheet_id"],
            "source_row_id": row["source_row_id"],
            "fixed_params": {"air_volume": 1000, "power": 1.5},
        },
    )
    assert rem.status_code == 200, rem.text
    rem_json = rem.json()
    assert rem_json["recommendation_revision"] == 2
    assert rem_json["recommendation_snapshot_id"] != row["initial_snapshot_id"]

    # Record final human action referencing revision=2
    evt_id = f"evt_rematch_{uuid.uuid4().hex[:8]}"
    rec = client.post(
        "/api/flywheel/record",
        headers=u1["headers"],
        json={
            "client_event_id": evt_id,
            "document_id": row["document_id"],
            "sheet_id": row["sheet_id"],
            "source_row_id": row["source_row_id"],
            "recommendation_snapshot_id": rem_json["recommendation_snapshot_id"],
            "event_seq": 1,
            "action_type": "MANUAL_REPLACE",
            "final_targets": [{"code": "3.50.29.1369", "name": "医用离心式排风机(箱）", "spec": "1000m3/h"}],
        },
    )
    assert rec.status_code == 200, rec.text

    trace = client.get(
        f"/api/flywheel/documents/{row['document_id']}/rows/{row['source_row_id']}/trace",
        headers=u1["headers"],
    ).json()
    assert trace["initial_snapshot"]["revision"] == 1
    assert trace["initial_snapshot"]["snapshot_id"] == row["initial_snapshot_id"]
    assert len(trace["snapshots"]) == 2
    assert trace["effective_record"]["initial_snapshot_id"] == row["initial_snapshot_id"]
    assert trace["effective_record"]["recommendation_snapshot_id"] == rem_json["recommendation_snapshot_id"]
    assert trace["effective_record"]["recommendation_revision"] == 2
    assert json.loads(trace["effective_record"]["final_targets_json"])[0]["code"] == "3.50.29.1369"

    # Modifying isolated config file changes config_version deterministically
    cfg_path = Path(os.environ["CONFIG_DIR"]) / "alias_dict.json"
    orig_cfg = cfg_path.read_text(encoding="utf-8")
    try:
        data = json.loads(orig_cfg)
        data["__v016_m0_probe__"] = "测试风机"
        server.atomic_write_json_config(str(cfg_path), data)
        rt_changed = client.get("/api/flywheel/runtime-version", headers=u1["headers"]).json()
        assert rt_changed["config_version"] != rt["config_version"]
    finally:
        cfg_path.write_text(orig_cfg, encoding="utf-8")


def test_c06_c07_idempotency_conflict_out_of_order_concurrency_and_rollback(m0_env):
    """Idempotent replay, 409 conflicts, out-of-order monotonic history, concurrency, and atomic rollback."""
    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]
    up = _upload_workbook(client, u1["headers"], "idemp_ooo.xlsx", _make_two_sheet_xlsx_bytes())
    row1, row2 = up["rows"][0], up["rows"][1]

    # 1. Exact retry -> 200 replay, single DB row
    evt1 = f"evt_idemp_{uuid.uuid4().hex[:8]}"
    p1 = {
        "client_event_id": evt1,
        "document_id": row1["document_id"],
        "sheet_id": row1["sheet_id"],
        "source_row_id": row1["source_row_id"],
        "event_seq": 2,  # Out-of-order: seq=2 arrives BEFORE seq=1!
        "action_type": "CONFIRM_TOP1",
        "final_targets": [{"code": "3.50.29.1369", "name": "最新目标", "spec": "1000"}],
    }
    r1 = client.post("/api/flywheel/record", headers=u1["headers"], json=p1)
    assert r1.status_code == 200 and r1.json()["idempotent_replay"] is False
    r1_dup = client.post("/api/flywheel/record", headers=u1["headers"], json=p1)
    assert r1_dup.status_code == 200 and r1_dup.json()["idempotent_replay"] is True
    assert r1_dup.json()["record_id"] == r1.json()["record_id"]

    # 2. Same client_event_id with different content or different row -> 409
    p1_diff_content = {**p1, "action_type": "MANUAL_REPLACE"}
    assert client.post("/api/flywheel/record", headers=u1["headers"], json=p1_diff_content).status_code == 409
    p1_diff_row = {**p1, "sheet_id": row2["sheet_id"], "source_row_id": row2["source_row_id"]}
    assert client.post("/api/flywheel/record", headers=u1["headers"], json=p1_diff_row).status_code == 409

    # 3. Late seq=1 arrives AFTER seq=2 -> archived in history (superseded=1), effective final remains seq=2!
    evt_late = f"evt_late_{uuid.uuid4().hex[:8]}"
    r_late = client.post(
        "/api/flywheel/record",
        headers=u1["headers"],
        json={
            "client_event_id": evt_late,
            "document_id": row1["document_id"],
            "sheet_id": row1["sheet_id"],
            "source_row_id": row1["source_row_id"],
            "event_seq": 1,
            "action_type": "PICK_CANDIDATE",
            "final_targets": [{"code": "OLD.001", "name": "迟到旧目标", "spec": "100"}],
        },
    )
    assert r_late.status_code == 200
    assert r_late.json()["superseded"] == 1
    assert r_late.json()["is_stale_arrival"] == 1
    assert r_late.json()["effective_event_seq"] == 2

    trace = client.get(
        f"/api/flywheel/documents/{row1['document_id']}/rows/{row1['source_row_id']}/trace",
        headers=u1["headers"],
    ).json()
    assert len(trace["events"]) == 2
    assert trace["effective_record"]["client_event_id"] == evt1
    assert trace["effective_record"]["event_seq"] == 2

    # 4. Duplicate seq=2 with a different client_event_id -> 409 Conflict
    r_dup_seq = client.post(
        "/api/flywheel/record",
        headers=u1["headers"],
        json={
            "client_event_id": f"evt_dupseq_{uuid.uuid4().hex[:8]}",
            "document_id": row1["document_id"],
            "sheet_id": row1["sheet_id"],
            "source_row_id": row1["source_row_id"],
            "event_seq": 2,
            "action_type": "MANUAL_REPLACE",
            "final_targets": [{"code": "CONFLICT", "name": "并发冲突", "spec": "100"}],
        },
    )
    assert r_dup_seq.status_code == 409

    # 5. Fault injection during insert / supersede update -> zero partial writes, safe retry
    evt_fault = f"evt_fault_{uuid.uuid4().hex[:8]}"
    fault_payload = {
        "client_event_id": evt_fault,
        "document_id": row1["document_id"],
        "sheet_id": row1["sheet_id"],
        "source_row_id": row1["source_row_id"],
        "event_seq": 3,
        "action_type": "MANUAL_REPLACE",
        "final_targets": [{"code": "3.50.29.1369", "name": "容错恢复目标", "spec": "1000"}],
    }
    def _fail_after_insert(stage, _cur):
        if stage == "after_insert":
            raise RuntimeError("injected_fault_after_insert")

    with pytest.raises(RuntimeError, match="injected_fault_after_insert"):
        feedback_contract.record_flywheel_event(u1["user"], fault_payload, _tx_hook=_fail_after_insert)

    # Verify seq=2 is STILL superseded=0 and evt_fault is NOT present
    trace_mid = client.get(
        f"/api/flywheel/documents/{row1['document_id']}/rows/{row1['source_row_id']}/trace",
        headers=u1["headers"],
    ).json()
    assert len(trace_mid["events"]) == 2
    assert trace_mid["effective_record"]["client_event_id"] == evt1

    # Retrying exact same fault_payload now succeeds atomically
    r_retry = client.post("/api/flywheel/record", headers=u1["headers"], json=fault_payload)
    assert r_retry.status_code == 200
    assert r_retry.json()["effective_event_seq"] == 3

    # 6. Concurrent threads across row2 & row3 must never collide or cross-supersede
    row3 = up["rows"][2]
    results = []
    def _send(row_obj, idx):
        return feedback_contract.record_flywheel_event(
            u1["user"],
            {
                "client_event_id": f"evt_conc_{idx}_{uuid.uuid4().hex[:6]}",
                "document_id": row_obj["document_id"],
                "sheet_id": row_obj["sheet_id"],
                "source_row_id": row_obj["source_row_id"],
                "event_seq": 1,
                "action_type": "MANUAL_REPLACE",
                "final_targets": [{"code": f"C.{idx}", "name": f"并发{idx}", "spec": ""}],
            },
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_send, row2, 2)
        f2 = pool.submit(_send, row3, 3)
        results.extend([f1.result(), f2.result()])
    assert all(res["superseded"] == 0 for res in results)


def test_c01_c08_lifecycle_save_restore_restart_and_account_recreation(m0_env):
    """Sorting/draft/named save/restore/restart preserve source_row_id; deleted+recreated username cannot hijack old resources."""
    client: TestClient = m0_env["client"]
    uname = f"m0_recreate_{uuid.uuid4().hex[:6]}"
    ep1 = f"ep-orig-{uuid.uuid4().hex[:6]}"
    actor1 = _create_actor(uname, "employee", epoch=ep1)

    up = _upload_workbook(client, actor1["headers"], "lifecycle.xlsx", _make_two_sheet_xlsx_bytes())
    orig_rows = up["rows"]
    # Simulate client-side sorting (reverse order) without altering server source_row_id
    reversed_rows = list(reversed(orig_rows))

    # Save draft & named session
    r_draft = client.post(
        "/api/session/draft",
        headers=actor1["headers"],
        json={
            "original_filename": "lifecycle.xlsx",
            "professional": "暖通",
            "document_id": up["upload"]["document_id"],
            "work_session_id": up["upload"]["work_session_id"],
            "items_json": json.dumps(reversed_rows, ensure_ascii=False),
        },
    )
    assert r_draft.status_code == 200 and r_draft.json()["trust_level"] == "trusted"

    r_named = client.post(
        "/api/session/save",
        headers=actor1["headers"],
        json={
            "session_name": "生命周期测试进度",
            "original_filename": "lifecycle.xlsx",
            "professional": "暖通",
            "document_id": up["upload"]["document_id"],
            "work_session_id": up["upload"]["work_session_id"],
            "items_json": json.dumps(reversed_rows, ensure_ascii=False),
        },
    )
    assert r_named.status_code == 200
    saved_id = r_named.json()["session_id"]

    # Simulate server restart: clear in-memory upload_tasks
    with server._upload_tasks_lock:
        server.upload_tasks.clear()

    # Restore named session & annotate after restart
    got = client.get(f"/api/session/{saved_id}", headers=actor1["headers"])
    assert got.status_code == 200
    restored_items = json.loads(got.json()["session"]["items_json"])
    ann = client.post(
        "/api/session/annotate",
        headers=actor1["headers"],
        json={
            "items": restored_items,
            "scope_filename": "lifecycle.xlsx",
            "document_id": got.json()["session"]["document_id"],
            "work_session_id": got.json()["session"]["work_session_id"],
        },
    )
    assert ann.status_code == 200
    ann_data = ann.json()
    assert ann_data["legacy_untrusted_count"] == 0
    assert [it["source_row_id"] for it in ann_data["items"]] == [it["source_row_id"] for it in reversed_rows]

    # Username change does NOT break immutable actor_user_id + account_epoch ownership
    new_uname = f"{uname}_renamed"
    conn = get_auth_connection()
    conn.execute("UPDATE users SET username = ? WHERE id = ?", (new_uname, actor1["user"]["id"]))
    conn.commit()
    conn.close()
    refreshed_user = get_user_by_username(new_uname)
    refreshed_headers = {"Authorization": f"Bearer {issue_token_for_user(refreshed_user)}"}
    r_after_rename = client.post(
        "/api/flywheel/record",
        headers=refreshed_headers,
        json={
            "client_event_id": f"evt_rename_{uuid.uuid4().hex[:6]}",
            "document_id": orig_rows[0]["document_id"],
            "sheet_id": orig_rows[0]["sheet_id"],
            "source_row_id": orig_rows[0]["source_row_id"],
            "event_seq": 1,
            "action_type": "CONFIRM_TOP1",
            "final_targets": [{"code": "3.50.29.1369", "name": "改名后仍归属同一人"}],
        },
    )
    assert r_after_rename.status_code == 200

    # Delete and recreate user with same username -> old token 401, new epoch cannot read/write old document or session
    conn = get_auth_connection()
    conn.execute("DELETE FROM users WHERE id = ?", (actor1["user"]["id"],))
    conn.commit()
    conn.close()
    assert client.post(
        "/api/flywheel/record",
        headers=refreshed_headers,
        json={
            "client_event_id": f"evt_deleted_{uuid.uuid4().hex[:6]}",
            "document_id": orig_rows[0]["document_id"],
            "sheet_id": orig_rows[0]["sheet_id"],
            "source_row_id": orig_rows[0]["source_row_id"],
            "event_seq": 2,
            "action_type": "CONFIRM_TOP1",
            "final_targets": [{"code": "3.50.29.1369", "name": "已删除账号"}],
        },
    ).status_code == 401

    ep2 = f"ep-recreated-{uuid.uuid4().hex[:6]}"
    recreated = _create_actor(new_uname, "employee", epoch=ep2)
    assert client.get(f"/api/session/{saved_id}", headers=recreated["headers"]).status_code == 404
    assert client.get(
        f"/api/flywheel/documents/{orig_rows[0]['document_id']}/rows/{orig_rows[0]['source_row_id']}/trace",
        headers=recreated["headers"],
    ).status_code == 404


def test_c08_c09_legacy_quarantine_idempotent_migration_and_atomic_rollback(tmp_path):
    """Pre-V0.1.6 legacy DB migration is atomic (rollback on failure), repeatable, and isolates legacy/untrusted records."""
    legacy_db = tmp_path / "legacy_materials.db"
    conn = sqlite3.connect(legacy_db)
    conn.execute("""
        CREATE TABLE flywheel_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_event_id TEXT UNIQUE NOT NULL,
            session_id TEXT NOT NULL,
            row_idx INTEGER NOT NULL,
            discipline TEXT DEFAULT '通用',
            raw_name TEXT NOT NULL,
            raw_name_norm TEXT NOT NULL,
            raw_spec TEXT DEFAULT '',
            raw_unit TEXT DEFAULT '',
            raw_category TEXT DEFAULT '',
            extracted_params TEXT DEFAULT '{}',
            auto_top1_code TEXT DEFAULT '',
            auto_top1_score REAL DEFAULT 0.0,
            auto_top1_name TEXT DEFAULT '',
            auto_candidates_json TEXT DEFAULT '[]',
            algo_version TEXT DEFAULT 'v1.0',
            config_version TEXT DEFAULT '',
            action_type TEXT NOT NULL,
            final_targets_json TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT DEFAULT '',
            superseded INTEGER DEFAULT 0,
            is_processed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        """
        INSERT INTO flywheel_records (
            client_event_id, session_id, row_idx, raw_name, raw_name_norm,
            action_type, final_targets_json, user_id
        ) VALUES
        ('leg_1', 'default_session', 1, '旧排风机', '旧排风机', 'MANUAL_REPLACE', '[{"code":"L1","name":"旧目标"}]', 'legacy_anonymous'),
        ('leg_2', 'client_sess', 1, '', '', 'CONFIRM_TOP1', '[]', 'zhangsan')
        """
    )
    conn.commit()
    conn.close()

    # 1. Mid-migration fault must roll back ALL DDL + DML changes (zero partial columns/tables)
    def _fail_mid_migration(stage, _cur):
        if stage == "after_quarantine_legacy":
            raise RuntimeError("injected_migration_failure")

    with pytest.raises(RuntimeError, match="injected_migration_failure"):
        init_flywheel_tables(db_path=str(legacy_db), _migration_hook=_fail_mid_migration)

    chk_conn = sqlite3.connect(legacy_db)
    cols_after_fail = [r[1] for r in chk_conn.execute("PRAGMA table_info(flywheel_records)").fetchall()]
    tables_after_fail = {r[0] for r in chk_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    chk_conn.close()
    assert "document_id" not in cols_after_fail
    assert "flywheel_documents" not in tables_after_fail

    # 2. Run migration twice -> idempotent, legacy rows preserved & quarantined as legacy_untrusted
    m1 = init_flywheel_tables(db_path=str(legacy_db))
    m2 = init_flywheel_tables(db_path=str(legacy_db))
    assert m1["contract_version"] == flywheel_db.FLYWHEEL_CONTRACT_VERSION
    assert m2["migrated_columns"] == []

    chk_conn = sqlite3.connect(legacy_db)
    chk_conn.row_factory = sqlite3.Row
    leg_rows = chk_conn.execute("SELECT * FROM flywheel_records ORDER BY id ASC").fetchall()
    eff_rows = chk_conn.execute("SELECT * FROM flywheel_effective_records").fetchall()
    chk_conn.close()
    assert len(leg_rows) == 2
    assert all(r["trust_level"] == "legacy_untrusted" for r in leg_rows)
    assert all((r["document_id"] or "") == "" for r in leg_rows)
    assert len(eff_rows) == 0

    # 3. Empty DB migration twice -> idempotent
    empty_db = tmp_path / "empty_materials.db"
    init_flywheel_tables(db_path=str(empty_db))
    res_empty_2 = init_flywheel_tables(db_path=str(empty_db))
    assert res_empty_2["migrated_columns"] == []

    # 4. Legacy feedback_samples.json quarantine & legacy draft annotate isolation
    fb_file = Path(os.environ["FEEDBACK_SAMPLES_FILE"])
    prev_fb = fb_file.read_text(encoding="utf-8") if fb_file.exists() else None
    try:
        fb_file.write_text(
            json.dumps([{"row_id": 1, "raw_text": "旧风口", "auto_code": "A", "manual_code": "3.50.29.1369", "is_correct": False}], ensure_ascii=False),
            encoding="utf-8",
        )
        # Ensure legacy JSON record is NOT returned by get_feedback_suggestions
        with get_db_cursor(commit=True) as cur:
            cur.execute("DELETE FROM flywheel_feedback_samples")
        sugs = feedback_train.get_feedback_suggestions()
        assert all(s.get("suggested_typo") != "旧风口" for s in sugs)
    finally:
        if prev_fb is not None:
            fb_file.write_text(prev_fb, encoding="utf-8")
        elif fb_file.exists():
            fb_file.unlink()


def test_c08_legacy_draft_quarantine_and_exports_exclude_untrusted(m0_env):
    """Legacy drafts are quarantined on annotate without upgrading to server snapshots; statistics/exports exclude legacy_untrusted."""
    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]
    u2 = m0_env["u2"]
    adm = m0_env["adm"]

    # Forged foreign document_id on draft save -> 404 without overwriting existing draft
    up = _upload_workbook(client, u1["headers"], "owner_doc.xlsx", _make_two_sheet_xlsx_bytes())
    assert client.post(
        "/api/session/draft",
        headers=u2["headers"],
        json={
            "original_filename": "hijack.xlsx",
            "professional": "暖通",
            "document_id": up["upload"]["document_id"],
            "work_session_id": up["upload"]["work_session_id"],
            "items_json": "[]",
        },
    ).status_code == 404

    # Legacy draft without document_id
    legacy_items = [
        {
            "original_row_idx": 1,
            "original_name": "旧排风机",
            "original_spec": "1000m3/h",
            "original_qty": 1,
            "original_unit": "台",
            "original_loss": 1.0,
            "professional": "暖通",
            "matched_code": "3.50.29.1369",
            "is_exact": True,
            "bindings": [],
        }
    ]
    r_leg = client.post(
        "/api/session/draft",
        headers=u2["headers"],
        json={
            "original_filename": "legacy.xlsx",
            "professional": "暖通",
            "items_json": json.dumps(legacy_items, ensure_ascii=False),
        },
    )
    assert r_leg.status_code == 200 and r_leg.json()["trust_level"] == "legacy_untrusted"

    with get_db_cursor() as cur:
        docs_before = cur.execute("SELECT COUNT(*) FROM flywheel_documents").fetchone()[0]
        snaps_before = cur.execute("SELECT COUNT(*) FROM flywheel_recommendation_snapshots").fetchone()[0]

    ann = client.post(
        "/api/session/annotate",
        headers=u2["headers"],
        json={"items": legacy_items, "scope_filename": "legacy.xlsx"},
    )
    assert ann.status_code == 200
    ann_json = ann.json()
    assert ann_json["legacy_untrusted_count"] == 1
    assert ann_json["items"][0]["trust_level"] == "legacy_untrusted"
    assert ann_json["items"][0]["feedback_eligible"] is False
    assert ann_json["items"][0]["document_id"] == ""

    with get_db_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM flywheel_documents").fetchone()[0] == docs_before
        assert cur.execute("SELECT COUNT(*) FROM flywheel_recommendation_snapshots").fetchone()[0] == snaps_before

        # Insert an untrusted legacy record directly and ensure statistics & exports exclude it
        marker_name = f"LEGACY_UNTRUSTED_MAT_{uuid.uuid4().hex[:6]}"
        cur.execute(
            """
            INSERT INTO flywheel_records (
                client_event_id, session_id, row_idx, discipline,
                raw_name, raw_name_norm, raw_spec, action_type, final_targets_json,
                user_id, auto_top1_name, superseded, trust_level, legacy_reason
            ) VALUES (?, 'default_session', 1, '暖通', ?, ?, 'DN100', 'MANUAL_REPLACE', ?, 'legacy_anonymous', '旧推荐', 0, 'legacy_untrusted', 'test_legacy')
            """,
            (
                f"evt_leg_exp_{uuid.uuid4().hex[:6]}",
                marker_name,
                marker_name,
                json.dumps([{"code": "L.001", "name": "旧标准物料"}], ensure_ascii=False),
            ),
        )
    conn_commit_check = client.get("/api/flywheel/export/benchmark", headers=adm["headers"])
    assert conn_commit_check.status_code == 200
    assert all(it["raw_name"] != marker_name for it in conn_commit_check.json()["items"])

    trip = client.get("/api/flywheel/export/triplets", headers=adm["headers"])
    assert trip.status_code == 200
    assert all(marker_name not in t["anchor"] for t in trip.json()["triplets"])

    stats = client.get("/api/flywheel/statistics", headers=adm["headers"])
    assert stats.status_code == 200
    assert stats.json()["legacy_untrusted_records"] >= 1


def test_feedback_contract_import_has_zero_side_effects(tmp_path):
    """Importing app.feedback_contract alone creates zero DB files or writes."""
    import subprocess
    import sys

    sub_root = tmp_path / "clean_import"
    sub_root.mkdir()
    mat_db = sub_root / "materials.db"
    auth_db = sub_root / "auth.db"
    env = {
        **os.environ,
        "MATERIALS_DB_PATH": str(mat_db),
        "AUTH_DB_PATH": str(auth_db),
        "TESTING": "1",
        "LLM_ENABLED": "false",
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import app.feedback_contract; print('ok')"],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "ok" in proc.stdout
    assert not mat_db.exists()
    assert not auth_db.exists()


def test_r02_audit_01_02_03_feedback_security_and_self_heal(m0_env):
    """AUDIT-01/02/03: reject no-actor write, ignore forged JSON suggestions, heal replay JSON divergence."""
    from app import feedback_train
    from app.db_manager import get_db_cursor

    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]
    adm = m0_env["adm"]

    up = _upload_workbook(client, u1["headers"], "r02_fb_sec.xlsx", _make_two_sheet_xlsx_bytes())
    row = up["rows"][0]
    fb_file = Path(os.environ["FEEDBACK_SAMPLES_FILE"])

    # AUDIT-01 (CE1): direct save_feedback_sample without actor_user MUST fail unconditionally
    forged_sample = {
        "client_event_id": f"ce1_{uuid.uuid4().hex[:8]}",
        "actor_user_id": u1["user"]["id"],
        "actor_account_epoch": u1["user"]["account_epoch"],
        "document_id": row["document_id"],
        "sheet_id": row["sheet_id"],
        "source_row_id": row["source_row_id"],
        "event_seq": 1,
        "raw_text": "CE1伪造直写样本",
        "auto_code": "A",
        "manual_code": "3.50.29.1369",
        "is_correct": False,
        "trust_level": "trusted",
    }
    with pytest.raises(ValueError):
        feedback_train.save_feedback_sample(forged_sample)

    # AUDIT-02 (CE2): forged JSON entry with full fake identity + trust_level='trusted' must NOT enter suggestions
    existing_json = json.loads(fb_file.read_text(encoding="utf-8")) if fb_file.exists() else []
    forged_json = dict(forged_sample, client_event_id=f"ce2_{uuid.uuid4().hex[:8]}", raw_text="CE2伪造JSON建议词")
    existing_json.append(forged_json)
    fb_file.write_text(json.dumps(existing_json, ensure_ascii=False, indent=2), encoding="utf-8")

    sugs_local = feedback_train.get_feedback_suggestions()
    assert all("CE2伪造JSON建议词" not in s.get("suggested_typo", "") for s in sugs_local)
    quarantined_forged = feedback_train._quarantine_legacy_samples([forged_json])
    assert quarantined_forged[0]["trust_level"] == "legacy_untrusted"
    sugs_http = client.get("/api/flywheel/statistics", headers=adm["headers"])
    assert sugs_http.status_code == 200
    assert all(
        "CE2伪造JSON建议词" not in s.get("suggested_typo", "")
        for s in client.get("/api/feedback/suggestions", headers=adm["headers"]).json().get("suggestions", [])
    )

    # AUDIT-03 (CE3): DB commit + JSON write failure heals automatically on idempotent retry
    ce3_evt = f"ce3_{uuid.uuid4().hex[:8]}"
    ce3_payload = {
        "client_event_id": ce3_evt,
        "document_id": row["document_id"],
        "sheet_id": row["sheet_id"],
        "source_row_id": row["source_row_id"],
        "recommendation_snapshot_id": row["recommendation_snapshot_id"],
        "event_seq": 1,
        "row_id": row["original_row_idx"],
        "raw_text": row["original_spec"],
        "auto_code": row["matched_code"],
        "manual_code": "3.50.29.1369",
        "is_correct": False,
    }
    with patch("app.feedback_train.sync_trusted_feedback_file", side_effect=OSError("disk_full")):
        r1 = client.post("/api/feedback/submit", headers=u1["headers"], json=ce3_payload)
    assert r1.status_code == 500
    with get_db_cursor() as cur:
        assert cur.execute(
            "SELECT COUNT(*) FROM flywheel_feedback_samples WHERE client_event_id = ?", (ce3_evt,)
        ).fetchone()[0] == 1
    before_retry_ids = {
        item.get("client_event_id")
        for item in json.loads(fb_file.read_text(encoding="utf-8"))
        if isinstance(item, dict)
    }
    assert ce3_evt not in before_retry_ids

    r2 = client.post("/api/feedback/submit", headers=u1["headers"], json=ce3_payload)
    assert r2.status_code == 200, r2.text
    assert r2.json()["idempotent_replay"] is True
    after_retry_ids = {
        item.get("client_event_id")
        for item in json.loads(fb_file.read_text(encoding="utf-8"))
        if isinstance(item, dict)
    }
    assert ce3_evt in after_retry_ids


def test_r02_audit_04_rematch_and_match_override_tamper_rejected(m0_env):
    """AUDIT-04 (CE4): tamper on professional/text/parsed_spec rejected 422; manual param tagged client_param_override."""
    from app.db_manager import get_db_cursor

    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]

    up = _upload_workbook(client, u1["headers"], "r02_rematch.xlsx", _make_two_sheet_xlsx_bytes())
    row = up["rows"][0]

    # Tampered professional on /api/rematch -> 422
    bad_prof = client.post(
        "/api/rematch",
        headers=u1["headers"],
        json={
            "row_id": row["original_row_idx"],
            "text": row["original_spec"],
            "fixed_params": {"air_volume": 999},
            "professional": "强电",
            "document_id": row["document_id"],
            "sheet_id": row["sheet_id"],
            "source_row_id": row["source_row_id"],
        },
    )
    assert bad_prof.status_code == 422

    # Tampered raw text on /api/rematch -> 422
    bad_text = client.post(
        "/api/rematch",
        headers=u1["headers"],
        json={
            "row_id": row["original_row_idx"],
            "text": "完全篡改文本 电力电缆 YJV 4x25",
            "fixed_params": {"air_volume": 999},
            "professional": row["professional"],
            "document_id": row["document_id"],
            "sheet_id": row["sheet_id"],
            "source_row_id": row["source_row_id"],
        },
    )
    assert bad_text.status_code == 422

    # Forged parsed_spec on /api/match -> 422
    bad_match = client.post(
        "/api/match",
        headers=u1["headers"],
        json={
            "text": row["original_spec"],
            "name": row["original_name"],
            "professional": row["professional"],
            "document_id": row["document_id"],
            "sheet_id": row["sheet_id"],
            "source_row_id": row["source_row_id"],
            "parsed_spec": {"attrs": {"CE4伪造维度": "X"}},
        },
    )
    assert bad_match.status_code == 422

    # Verify only rev=1 exists after rejected attempts
    with get_db_cursor() as cur:
        snaps = cur.execute(
            "SELECT revision FROM flywheel_recommendation_snapshots WHERE source_row_id = ?",
            (row["source_row_id"],),
        ).fetchall()
    assert [r["revision"] for r in snaps] == [1]

    # Legitimate /api/match toggling subset of server-parsed attrs -> 200 & match_override
    server_attrs = dict((row.get("parsed_spec") or {}).get("attrs") or {})
    subset_attrs = dict(list(server_attrs.items())[:1])
    ok_match = client.post(
        "/api/match",
        headers=u1["headers"],
        json={
            "text": row["original_spec"],
            "name": row["original_name"],
            "professional": row["professional"],
            "document_id": row["document_id"],
            "sheet_id": row["sheet_id"],
            "source_row_id": row["source_row_id"],
            "parsed_spec": {"model": "", "attrs": subset_attrs},
        },
    )
    assert ok_match.status_code == 200, ok_match.text
    assert ok_match.json()["snapshot_kind"] == "match_override"

    # Legitimate /api/rematch with manual client param -> 200, tagged client_param_override
    ok_rematch = client.post(
        "/api/rematch",
        headers=u1["headers"],
        json={
            "row_id": row["original_row_idx"],
            "text": row["original_spec"],
            "fixed_params": {"model": "MANUAL-X", "air_volume": 1200},
            "professional": row["professional"],
            "document_id": row["document_id"],
            "sheet_id": row["sheet_id"],
            "source_row_id": row["source_row_id"],
        },
    )
    assert ok_rematch.status_code == 200, ok_rematch.text
    assert ok_rematch.json()["snapshot_kind"] == "rematch_client_param"
    with get_db_cursor() as cur:
        snap3 = cur.execute(
            "SELECT professional, snapshot_kind, parsed_spec_json FROM flywheel_recommendation_snapshots WHERE snapshot_id = ?",
            (ok_rematch.json()["recommendation_snapshot_id"],),
        ).fetchone()
    assert snap3["professional"] == row["professional"]
    assert snap3["snapshot_kind"] == "rematch_client_param"
    assert json.loads(snap3["parsed_spec_json"]).get("_provenance") == "client_param_override"


def test_r02_audit_05_legacy_draft_preserved_not_overwritten(m0_env):
    """AUDIT-05 (CE5): saving a trusted draft inserts a new row and preserves any empty-epoch legacy draft."""
    from app import auth_manager, session_manager

    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]
    up = _upload_workbook(client, u1["headers"], "r02_draft.xlsx", _make_two_sheet_xlsx_bytes())

    legacy_id = session_manager.save_draft_session(
        u1["user"]["id"],
        "legacy.xlsx",
        "暖通",
        "LEGACY_ITEMS_JSON",
        account_epoch="",
        document_id="",
        work_session_id="",
        trust_level="legacy_untrusted",
    )
    resp = client.post(
        "/api/session/draft",
        headers=u1["headers"],
        json={
            "original_filename": "new_trusted.xlsx",
            "professional": "暖通",
            "items_json": "NEW_TRUSTED_ITEMS_JSON",
            "document_id": up["upload"]["document_id"],
            "work_session_id": up["upload"]["work_session_id"],
        },
    )
    assert resp.status_code == 200, resp.text
    new_draft_id = resp.json()["draft_id"]
    assert new_draft_id != legacy_id

    conn = auth_manager.get_auth_connection()
    rows = conn.execute(
        "SELECT id, trust_level, owner_account_epoch, document_id, items_json FROM work_sessions WHERE user_id = ? AND session_type = 'draft'",
        (u1["user"]["id"],),
    ).fetchall()
    conn.close()

    by_id = {r["id"]: dict(r) for r in rows}
    assert by_id[legacy_id]["trust_level"] == "legacy_untrusted"
    assert by_id[legacy_id]["items_json"] == "LEGACY_ITEMS_JSON"
    assert (by_id[legacy_id]["owner_account_epoch"] or "") == ""
    assert by_id[new_draft_id]["trust_level"] == "trusted"
    assert by_id[new_draft_id]["items_json"] == "NEW_TRUSTED_ITEMS_JSON"

    got = client.get("/api/session/draft", headers=u1["headers"])
    assert got.status_code == 200
    assert got.json()["draft"]["id"] == new_draft_id
    assert got.json()["draft"]["trust_level"] == "trusted"


def test_r02_audit_06_event_seq_strict_positive_int_parity(m0_env):
    """AUDIT-06 (CE6/S1-S5): HTTP and direct contract calls reject bool/float/str/<=0 event_seq identically with 422."""
    from app import feedback_contract

    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]
    up = _upload_workbook(client, u1["headers"], "r02_seq.xlsx", _make_two_sheet_xlsx_bytes())
    row = up["rows"][0]

    invalid_seqs = [None, True, False, 1.0, 1.5, "2", 0, -3]
    for bad_seq in invalid_seqs:
        fw_payload = {
            "client_event_id": f"seq_fw_{uuid.uuid4().hex[:8]}",
            "document_id": row["document_id"],
            "sheet_id": row["sheet_id"],
            "source_row_id": row["source_row_id"],
            "recommendation_snapshot_id": row["recommendation_snapshot_id"],
            "event_seq": bad_seq,
            "action_type": "CONFIRM_TOP1",
            "final_targets": [{"code": "3.50.29.1369", "name": "风机", "spec": "DN100"}],
        }
        r_fw = client.post("/api/flywheel/record", headers=u1["headers"], json=fw_payload)
        assert r_fw.status_code == 422, f"/api/flywheel/record accepted {bad_seq!r}"
        with pytest.raises(feedback_contract.FeedbackContractError) as exc_fw:
            feedback_contract.record_flywheel_event(u1["user"], fw_payload)
        assert exc_fw.value.status_code == 422

        fb_payload = {
            "client_event_id": f"seq_fb_{uuid.uuid4().hex[:8]}",
            "document_id": row["document_id"],
            "sheet_id": row["sheet_id"],
            "source_row_id": row["source_row_id"],
            "recommendation_snapshot_id": row["recommendation_snapshot_id"],
            "event_seq": bad_seq,
            "row_id": 1,
            "raw_text": row["original_spec"],
            "auto_code": row["matched_code"],
            "manual_code": "3.50.29.1369",
            "is_correct": False,
        }
        r_fb = client.post("/api/feedback/submit", headers=u1["headers"], json=fb_payload)
        assert r_fb.status_code == 422, f"/api/feedback/submit accepted {bad_seq!r}"
        with pytest.raises(feedback_contract.FeedbackContractError) as exc_fb:
            feedback_contract.record_feedback_sample_event(u1["user"], fb_payload)
        assert exc_fb.value.status_code == 422


def _run_web_app_scenario(client: TestClient, scenario_js: str, fetch_handler):
    """Execute real web/app.js in Node vm wired via NDJSON IPC to real FastAPI TestClient."""
    import shutil
    import subprocess
    import tempfile

    node_bin = shutil.which("node")
    assert node_bin, "Node.js is required for C-M0-08 frontend+HTTP+SQLite integration tests"

    harness_js = r"""
const fs = require('fs');
const vm = require('vm');
const readline = require('readline');

const appJsSrc = fs.readFileSync(process.argv[2], 'utf8');
const scenarioSrc = fs.readFileSync(process.argv[3], 'utf8');

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
const waiters = [];
const buffered = [];
rl.on('line', (line) => {
    if (!line.trim()) return;
    const msg = JSON.parse(line);
    if (waiters.length > 0) {
        waiters.shift()(msg);
    } else {
        buffered.push(msg);
    }
});

function nextIpcReply() {
    if (buffered.length > 0) return Promise.resolve(buffered.shift());
    return new Promise((resolve) => waiters.push(resolve));
}

async function ipcFetch(url, options = {}) {
    const reqId = Math.random().toString(36).slice(2);
    let bodyObj = options.body;
    if (typeof bodyObj === 'string') {
        try { bodyObj = JSON.parse(bodyObj); } catch (_) {}
    }
    process.stdout.write(JSON.stringify({
        type: 'FETCH',
        reqId,
        url: String(url),
        method: String(options.method || 'GET').toUpperCase(),
        headers: options.headers || {},
        body: bodyObj,
    }) + '\n');
    const resp = await nextIpcReply();
    if (resp.networkError) {
        throw new Error(resp.networkError);
    }
    const respText = JSON.stringify(resp.json === undefined ? {} : resp.json);
    return {
        ok: resp.status >= 200 && resp.status < 300,
        status: resp.status,
        json: async () => JSON.parse(respText),
        text: async () => respText,
        clone: () => ({
            json: async () => JSON.parse(respText),
            text: async () => respText,
        }),
    };
}

function makeMemoryStorage(initial = {}) {
    const store = Object.assign({}, initial);
    return {
        getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: (k) => { delete store[k]; },
        clear: () => { Object.keys(store).forEach(k => delete store[k]); },
        _dump: () => Object.assign({}, store),
    };
}

function makeFakeElement(id) {
    const listeners = {};
    return {
        id,
        style: {},
        innerHTML: '',
        innerText: '',
        title: '',
        disabled: false,
        dataset: {},
        children: [],
        parentNode: null,
        addEventListener: (ev, fn) => { listeners[ev] = listeners[ev] || []; listeners[ev].push(fn); },
        click: () => { (listeners.click || []).forEach(fn => fn()); },
        querySelector: () => null,
        querySelectorAll: () => [],
        remove: function() {
            if (this.parentNode) {
                this.parentNode.children = this.parentNode.children.filter(c => c !== this);
                if (domById[this.id] === this) delete domById[this.id];
            }
        },
        insertBefore: function(node, refNode) {
            node.parentNode = this;
            if (node.id) domById[node.id] = node;
            const idx = this.children.indexOf(refNode);
            if (idx >= 0) this.children.splice(idx, 0, node);
            else this.children.push(node);
            return node;
        },
        appendChild: function(node) {
            node.parentNode = this;
            if (node.id) domById[node.id] = node;
            this.children.push(node);
            return node;
        },
        _listeners: listeners,
    };
}

const domById = {};
const rootContainer = makeFakeElement('root-parent');
const wbEl = makeFakeElement('workbench');
rootContainer.appendChild(wbEl);
domById['workbench'] = wbEl;

const fakeDocument = {
    documentElement: { setAttribute: () => {}, getAttribute: () => 'dark' },
    getElementById: (id) => domById[id] || null,
    createElement: (tag) => makeFakeElement(''),
    querySelectorAll: () => [],
    addEventListener: () => {},
};

function bootContext(initialStorage) {
    Object.keys(domById).forEach(k => { if (k !== 'workbench') delete domById[k]; });
    rootContainer.children = [wbEl];
    const storage = makeMemoryStorage(initialStorage || {});
    const alerts = [];
    const ctx = {
        console,
        setTimeout,
        clearTimeout,
        setInterval: () => 0,
        clearInterval,
        Promise,
        JSON,
        Math,
        Number,
        String,
        Boolean,
        Array,
        Object,
        Set,
        Date,
        Error,
        TypeError,
        crypto: require('crypto').webcrypto || require('crypto'),
        localStorage: storage,
        document: fakeDocument,
        Headers: class Headers { constructor() { this.map = {}; } set(k, v) { this.map[k] = v; } },
        alert: (m) => alerts.push(String(m)),
        confirm: () => true,
        fetch: ipcFetch,
        window: {
            location: { protocol: 'http:', href: 'http://127.0.0.1:18080/index.html', origin: 'http://127.0.0.1:18080' },
            addEventListener: () => {},
            fetch: ipcFetch,
            __M0_FEEDBACK_RETRY_CONFIG: { maxAutoRetries: 1, retryDelayMs: 5 },
        },
        _alerts: alerts,
        _domById: domById,
    };
    ctx.globalThis = ctx;
    vm.createContext(ctx);
    vm.runInContext(appJsSrc, ctx, { filename: 'web/app.js' });
    return ctx;
}

(async () => {
    try {
        const fn = new Function('bootContext', 'domById', `return (async () => { ${scenarioSrc} })();`);
        const result = await fn(bootContext, domById);
        process.stdout.write(JSON.stringify({ type: 'DONE', result }) + '\n');
        process.exit(0);
    } catch (err) {
        process.stdout.write(JSON.stringify({ type: 'ERROR', error: String(err && err.stack || err) }) + '\n');
        process.exit(1);
    }
})();
"""
    with tempfile.TemporaryDirectory(prefix="m0_r03_node_") as tmp_dir:
        tmp_p = Path(tmp_dir)
        harness_path = tmp_p / "harness.js"
        scenario_path = tmp_p / "scenario.js"
        harness_path.write_text(harness_js, encoding="utf-8")
        scenario_path.write_text(scenario_js, encoding="utf-8")
        proc = subprocess.Popen(
            [node_bin, str(harness_path), str(PROJECT_ROOT / "web" / "app.js"), str(scenario_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        requests_seen = []
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                msg = json.loads(line)
                if msg["type"] == "FETCH":
                    requests_seen.append(msg)
                    reply = fetch_handler(msg, len(requests_seen))
                    proc.stdin.write(json.dumps(reply, ensure_ascii=False) + "\n")
                    proc.stdin.flush()
                elif msg["type"] == "DONE":
                    proc.wait(timeout=10)
                    return msg["result"], requests_seen
                elif msg["type"] == "ERROR":
                    proc.wait(timeout=10)
                    raise AssertionError(f"Node scenario failed: {msg['error']}")
            stderr_txt = proc.stderr.read()
            rc = proc.wait(timeout=10)
            raise AssertionError(f"Node exited unexpectedly rc={rc}, stderr={stderr_txt}")
        finally:
            if proc.poll() is None:
                proc.kill()


def test_r03_cm008_frontend_retry_same_event_and_json_self_heal(m0_env):
    """C-M0-08: pre-submit drop, post-commit lost response, and HTTP 500 JSON failure retry same event_id/seq/payload and self-heal."""
    from app.db_manager import get_db_cursor

    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]
    up = _upload_workbook(client, u1["headers"], "r03_retry.xlsx", _make_two_sheet_xlsx_bytes())
    row_pre = up["rows"][0]
    row_lost = up["rows"][1]
    row_500 = up["rows"][2]
    fb_file = Path(os.environ["FEEDBACK_SAMPLES_FILE"])

    scenario = """
    const initStore = {
        token: %(tok)s,
        user_id: String(%(uid)s),
        account_epoch: %(ep)s,
        actor_token_ref: %(tok)s
    };
    const ctx = bootContext(initStore);
    ctx.window.__M0_FEEDBACK_RETRY_CONFIG = { maxAutoRetries: 1, retryDelayMs: 5 };

    // Case A: network failure BEFORE submission -> bounded auto-retry uses exact same client_event_id/seq/payload -> 200
    const rowPre = %(row_pre)s;
    const rowLost = %(row_lost)s;
    const row500 = %(row_500)s;
    ctx.window.__m0SetWorkbenchItems([rowPre, rowLost, row500], rowPre.document_id, rowPre.work_session_id);
    const resPre = await ctx.reportFlywheelAction(rowPre, 'CONFIRM_TOP1', [{code: '3.50.29.1369', name: '风机', spec: 'DN100'}]);

    // Case B: response lost AFTER server DB commit -> bounded auto-retry gets idempotent_replay=true
    const resLost = await ctx.reportFlywheelAction(rowLost, 'CONFIRM_TOP1', [{code: '3.50.29.1369', name: '阀', spec: 'DN100'}]);

    // Case C: persistent HTTP 500 during outage (DB commit OK, JSON write fails on both auto attempts) -> pending banner + manual retry -> 200 idempotent_replay + JSON self-heal
    const res500First = await ctx.submitFeedbackSample(row500, row500.matched_code, '3.50.29.1369', false);
    const bannerBeforeRetry = domById['feedback-delivery-banner'] ? domById['feedback-delivery-banner'].innerHTML : '';
    const activeBeforeRetry = ctx.getActivePendingFeedbackEvents().map(e => ({id: e.event_id, seq: e.event_seq, status: e.status}));
    const retryOutcomes = await ctx.retryPendingFeedbackEvents();
    const bannerAfterRetry = domById['feedback-delivery-banner'] ? domById['feedback-delivery-banner'].innerHTML : '';
    const activeAfterRetry = ctx.getActivePendingFeedbackEvents();

    return {
        resPre,
        resLost,
        res500First,
        bannerBeforeRetry,
        activeBeforeRetry,
        retryOutcomes,
        bannerAfterRetry,
        activeAfterRetry,
        deliveryAttr: row500._last_feedback_delivery,
    };
    """ % {
        "tok": json.dumps(u1["token"]),
        "uid": int(u1["user"]["id"]),
        "ep": json.dumps(u1["user"]["account_epoch"]),
        "row_pre": json.dumps(row_pre, ensure_ascii=False),
        "row_lost": json.dumps(row_lost, ensure_ascii=False),
        "row_500": json.dumps(row_500, ensure_ascii=False),
    }

    counts = {"fw_pre": 0, "fw_lost": 0, "fb_500": 0}

    def handler(msg, _idx):
        url = msg["url"]
        body = msg.get("body")
        headers = {"Authorization": f"Bearer {u1['token']}"}
        if "/api/auth/me" in url:
            r = client.get("/api/auth/me", headers=headers)
            return {"status": r.status_code, "json": r.json()}
        if "/api/flywheel/record" in url:
            src_id = body["source_row_id"]
            if src_id == row_pre["source_row_id"]:
                counts["fw_pre"] += 1
                if counts["fw_pre"] == 1:
                    # Injection point: pre-submit network failure before hitting FastAPI server
                    return {"networkError": "injected_pre_submit_network_drop"}
                r = client.post("/api/flywheel/record", headers=headers, json=body)
                return {"status": r.status_code, "json": r.json()}
            if src_id == row_lost["source_row_id"]:
                counts["fw_lost"] += 1
                r = client.post("/api/flywheel/record", headers=headers, json=body)
                assert r.status_code == 200
                if counts["fw_lost"] == 1:
                    # Injection point: DB committed on server, response lost in transit
                    return {"networkError": "injected_post_commit_response_lost"}
                return {"status": r.status_code, "json": r.json()}
        if "/api/feedback/submit" in url:
            counts["fb_500"] += 1
            if counts["fb_500"] <= 2:
                # Injection point: server DB commit succeeds, JSON file sync throws OSError -> 500 across initial + 1 bounded auto-retry
                with patch("app.feedback_train.sync_trusted_feedback_file", side_effect=OSError("injected_disk_full")):
                    r = client.post("/api/feedback/submit", headers=headers, json=body)
                return {"status": r.status_code, "json": r.json()}
            # Manual user retry succeeds and heals JSON file
            r = client.post("/api/feedback/submit", headers=headers, json=body)
            return {"status": r.status_code, "json": r.json()}
        raise AssertionError(f"Unexpected URL {url}")

    result, seen = _run_web_app_scenario(client, scenario, handler)

    # Pre-submit network drop: 2 requests with IDENTICAL client_event_id & seq=1 -> 1 DB row
    fw_pre_reqs = [r["body"] for r in seen if "/api/flywheel/record" in r["url"] and r["body"]["source_row_id"] == row_pre["source_row_id"]]
    assert len(fw_pre_reqs) == 2
    assert fw_pre_reqs[0] == fw_pre_reqs[1]
    assert result["resPre"]["ok"] is True

    # Post-commit lost response: 2 requests with IDENTICAL client_event_id & seq=1 -> 2nd is idempotent_replay=True, 1 DB row
    fw_lost_reqs = [r["body"] for r in seen if "/api/flywheel/record" in r["url"] and r["body"]["source_row_id"] == row_lost["source_row_id"]]
    assert len(fw_lost_reqs) == 2
    assert fw_lost_reqs[0] == fw_lost_reqs[1]
    assert result["resLost"]["ok"] is True
    assert result["resLost"]["idempotent_replay"] is True

    # Persistent HTTP 500 -> visible banner + retry button -> manual retry sends IDENTICAL client_event_id & seq=1 -> 200 replay + JSON self-heal
    assert result["res500First"]["ok"] is False
    assert result["res500First"]["retryable"] is True
    assert result["res500First"]["status"] == 500
    assert "投递失败待重试" in result["bannerBeforeRetry"]
    assert "retry-pending-feedback-btn" in result["bannerBeforeRetry"]
    assert len(result["activeBeforeRetry"]) == 1
    assert result["retryOutcomes"][0]["ok"] is True
    assert result["retryOutcomes"][0]["idempotent_replay"] is True
    assert result["bannerAfterRetry"] == ""
    assert result["activeAfterRetry"] == []

    fb_reqs = [r["body"] for r in seen if "/api/feedback/submit" in r["url"]]
    assert len(fb_reqs) == 3
    assert fb_reqs[0] == fb_reqs[1] == fb_reqs[2]
    evt_id_500 = fb_reqs[0]["client_event_id"]

    with get_db_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM flywheel_records WHERE source_row_id = ?", (row_pre["source_row_id"],)).fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM flywheel_records WHERE source_row_id = ?", (row_lost["source_row_id"],)).fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM flywheel_feedback_samples WHERE client_event_id = ?", (evt_id_500,)).fetchone()[0] == 1
    file_ids = {x.get("client_event_id") for x in json.loads(fb_file.read_text(encoding="utf-8")) if isinstance(x, dict)}
    assert evt_id_500 in file_ids


def test_r03_cm008_non_retryable_409_422_dual_actions_and_late_retry(m0_env):
    """C-M0-08: 409/422 stop auto-retry & keep confirmation state; dual actions use distinct events; late retry archives stale without overwriting newer seq."""
    from app.db_manager import get_db_cursor

    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]
    up = _upload_workbook(client, u1["headers"], "r03_409_422.xlsx", _make_two_sheet_xlsx_bytes())
    row_dual = up["rows"][0]
    row_409 = up["rows"][1]
    row_422 = up["rows"][2]

    # Seed row_409 seq=1 server-side so client event_seq=1 triggers real 409 event_seq_conflict
    pre = client.post(
        "/api/flywheel/record",
        headers=u1["headers"],
        json={
            "client_event_id": f"seed_409_{uuid.uuid4().hex[:8]}",
            "document_id": row_409["document_id"],
            "sheet_id": row_409["sheet_id"],
            "source_row_id": row_409["source_row_id"],
            "recommendation_snapshot_id": row_409["recommendation_snapshot_id"],
            "event_seq": 1,
            "action_type": "CONFIRM_TOP1",
            "final_targets": [{"code": "3.50.29.1369", "name": "止回阀", "spec": "DN100"}],
        },
    )
    assert pre.status_code == 200

    scenario = """
    const ctx = bootContext({
        token: %(tok)s,
        user_id: String(%(uid)s),
        account_epoch: %(ep)s,
        actor_token_ref: %(tok)s
    });
    ctx.window.__M0_FEEDBACK_RETRY_CONFIG = { maxAutoRetries: 1, retryDelayMs: 5 };

    const row409 = %(row_409)s;
    const row422 = %(row_422)s;
    const rowDual = %(row_dual)s;
    const badRow422 = Object.assign({}, row422, {source_row_id: 'missing_nonexistent_row'});
    ctx.window.__m0SetWorkbenchItems([rowDual, row409, badRow422], rowDual.document_id, rowDual.work_session_id);

    // Simulate non-retryable 409 conflict & 422 invalid on rows while keeping confirmation state separate
    row409.decision_status = 'manual_confirmed';
    const res409 = await ctx.reportFlywheelAction(row409, 'CONFIRM_TOP1', [{code: '3.50.29.1369', name: '止回阀', spec: 'DN100'}]);
    badRow422.decision_status = 'manual_confirmed';
    const res422 = await ctx.reportFlywheelAction(badRow422, 'CONFIRM_TOP1', [{code: '3.50.29.1369', name: '电缆', spec: 'YJV'}]);
    const retryBlockedCheck = await ctx.retryPendingFeedbackEvents();

    // Consecutive dual semantic actions on rowDual: Action 1 fails 500 (seq=1), Action 2 succeeds (new UUID, seq=2), then late retry of Action 1 (seq=1)
    const dual1 = await ctx.reportFlywheelAction(rowDual, 'MANUAL_REPLACE', [{code: '3.50.29.1369', name: '方案一', spec: 'S1'}]);
    const dual2 = await ctx.reportFlywheelAction(rowDual, 'MANUAL_REPLACE', [{code: '2.17.01.0001', name: '方案二', spec: 'S2'}]);
    const pendingBeforeLate = ctx.getActivePendingFeedbackEvents().filter(e => e.source_row_id === rowDual.source_row_id).map(e => ({id: e.event_id, seq: e.event_seq, status: e.status}));
    const lateRetry = await ctx.retryPendingFeedbackEvents();

    return {
        res409,
        row409DecisionAfter: row409.decision_status,
        res422,
        row422DecisionAfter: badRow422.decision_status,
        retryBlockedCheck,
        bannerText: domById['feedback-delivery-banner'] ? domById['feedback-delivery-banner'].innerHTML : '',
        dual1,
        dual2,
        pendingBeforeLate,
        lateRetry,
    };
    """ % {
        "tok": json.dumps(u1["token"]),
        "uid": int(u1["user"]["id"]),
        "ep": json.dumps(u1["user"]["account_epoch"]),
        "row_409": json.dumps(row_409, ensure_ascii=False),
        "row_422": json.dumps(row_422, ensure_ascii=False),
        "row_dual": json.dumps(row_dual, ensure_ascii=False),
    }

    dual1_attempts = {"n": 0}

    def handler(msg, _idx):
        body = msg.get("body")
        headers = {"Authorization": f"Bearer {u1['token']}"}
        if "/api/auth/me" in msg["url"]:
            r = client.get("/api/auth/me", headers=headers)
            return {"status": r.status_code, "json": r.json()}
        if body["source_row_id"] == row_dual["source_row_id"] and body["event_seq"] == 1:
            dual1_attempts["n"] += 1
            if dual1_attempts["n"] <= 2:
                # Injection point: Action 1 encounters 500 during its initial + 1 auto-retry attempts
                return {"status": 500, "json": {"detail": "injected_dual1_outage"}}
        r = client.post("/api/flywheel/record", headers=headers, json=body)
        return {"status": r.status_code, "json": r.json()}

    result, seen = _run_web_app_scenario(client, scenario, handler)

    # 409 and 422 must execute ONCE (no auto-retry) and never be re-sent by retryPendingFeedbackEvents
    r409_reqs = [r for r in seen if r["body"]["source_row_id"] == row_409["source_row_id"]]
    r422_reqs = [r for r in seen if r["body"]["source_row_id"] == "missing_nonexistent_row"]
    assert len(r409_reqs) == 1
    assert len(r422_reqs) == 1
    assert result["res409"]["status"] == 409 and result["res409"]["retryable"] is False
    assert result["res422"]["status"] == 422 and result["res422"]["retryable"] is False
    assert result["row409DecisionAfter"] == "manual_confirmed"
    assert result["row422DecisionAfter"] == "manual_confirmed"
    assert "已停止自动重试" in result["bannerText"]

    # Dual semantic actions: Action 1 != Action 2 (distinct UUID & seq 1 vs 2); late retry returns seq=1 archived stale without replacing seq=2
    assert result["dual1"]["ok"] is False and result["dual1"]["event_seq"] == 1
    assert result["dual2"]["ok"] is True and result["dual2"]["event_seq"] == 2
    assert result["dual1"]["event_id"] != result["dual2"]["event_id"]
    assert len(result["pendingBeforeLate"]) == 1
    assert result["pendingBeforeLate"][0]["seq"] == 1
    assert len(result["lateRetry"]) == 1
    assert result["lateRetry"][0]["ok"] is True
    assert result["lateRetry"][0]["event_id"] == result["dual1"]["event_id"]
    assert bool(result["lateRetry"][0]["data"]["is_stale_arrival"]) is True

    with get_db_cursor() as cur:
        rows = cur.execute(
            "SELECT client_event_id, event_seq, superseded, is_stale_arrival FROM flywheel_records WHERE source_row_id = ? ORDER BY event_seq ASC",
            (row_dual["source_row_id"],),
        ).fetchall()
    assert [(r["event_seq"], r["superseded"], r["is_stale_arrival"]) for r in rows] == [(1, 1, 1), (2, 0, 0)]


def test_r03_cm008_pending_persistence_reload_and_cross_account_isolation(m0_env):
    """C-M0-08: pending event survives reload for same actor+doc, advances new seq cleanly, and never leaks to other doc/legacy/user."""
    client: TestClient = m0_env["client"]
    u1 = m0_env["u1"]
    u2 = m0_env["u2"]
    up1 = _upload_workbook(client, u1["headers"], "r03_alice_1.xlsx", _make_two_sheet_xlsx_bytes())
    up1_b = _upload_workbook(client, u1["headers"], "r03_alice_2.xlsx", _make_two_sheet_xlsx_bytes())
    up2 = _upload_workbook(client, u2["headers"], "r03_bob_1.xlsx", _make_two_sheet_xlsx_bytes())
    row1 = up1["rows"][0]

    scenario = """
    // Session 1 (Alice): trigger a failing flywheel action -> persisted in localStorage
    const aliceStore = {
        token: %(tok1)s,
        user_id: String(%(uid1)s),
        account_epoch: %(ep1)s,
        actor_token_ref: %(tok1)s
    };
    const ctx1 = bootContext(aliceStore);
    ctx1.window.__M0_FEEDBACK_RETRY_CONFIG = { maxAutoRetries: 0, retryDelayMs: 0 };
    const row1 = %(row1)s;
    ctx1.window.__m0SetWorkbenchItems([row1], row1.document_id, row1.work_session_id);
    const fail1 = await ctx1.reportFlywheelAction(row1, 'MANUAL_REPLACE', [{code: '3.50.29.1369', name: '待重发', spec: 'S1'}]);
    const savedStorage = ctx1.localStorage._dump();

    // Session 2 (Alice reloads F5 & restores SAME doc1 via /api/session/annotate): pending stays visible & _event_seq >= 1
    const ctxReloadSameDoc = bootContext(savedStorage);
    ctxReloadSameDoc.window.__m0SetWorkbenchItems([Object.assign({}, row1)], row1.document_id, row1.work_session_id);
    await ctxReloadSameDoc.annotateRestoredItems();
    const activeSameDoc = ctxReloadSameDoc.getActivePendingFeedbackEvents().map(e => ({id: e.event_id, seq: e.event_seq}));
    const seqAfterAnnotate = ctxReloadSameDoc.window.annotateRestoredItems ? (function() { return activeSameDoc.reduce((m, e) => Math.max(m, e.seq), 0); })() : 0;
    const retrySameDoc = await ctxReloadSameDoc.retryPendingFeedbackEvents();

    // Session 3 (Alice restores DIFFERENT doc2 while doc1 pending exists in savedStorage): zero pending leaked to doc2
    const rowOtherDoc = %(row_other_doc)s;
    const ctxOtherDoc = bootContext(savedStorage);
    ctxOtherDoc.window.__m0SetWorkbenchItems([rowOtherDoc], rowOtherDoc.document_id, rowOtherDoc.work_session_id);
    await ctxOtherDoc.annotateRestoredItems();
    const activeOtherDoc = ctxOtherDoc.getActivePendingFeedbackEvents();

    // Session 4 (Bob logs in with same localStorage containing Alice's pending): identity mismatch purges Alice's pending
    const bobStorage = Object.assign({}, savedStorage, { token: %(tok2)s });
    const ctxBob = bootContext(bobStorage);
    const rowBob = %(row_bob)s;
    ctxBob.window.__m0SetWorkbenchItems([rowBob], rowBob.document_id, rowBob.work_session_id);
    await ctxBob.annotateRestoredItems();
    const activeBob = ctxBob.getActivePendingFeedbackEvents();
    const retryBob = await ctxBob.retryPendingFeedbackEvents();

    return {
        fail1,
        activeSameDoc,
        seqAfterAnnotate,
        retrySameDoc,
        activeOtherDoc,
        activeBob,
        retryBob,
    };
    """ % {
        "tok1": json.dumps(u1["token"]),
        "uid1": int(u1["user"]["id"]),
        "ep1": json.dumps(u1["user"]["account_epoch"]),
        "tok2": json.dumps(u2["token"]),
        "row1": json.dumps(row1, ensure_ascii=False),
        "row_other_doc": json.dumps(up1_b["rows"][0], ensure_ascii=False),
        "row_bob": json.dumps(up2["rows"][0], ensure_ascii=False),
    }

    fail_first = {"done": False}

    def handler(msg, _idx):
        url = msg["url"]
        auth = msg["headers"].get("Authorization", "")
        if "/api/auth/me" in url:
            r = client.get("/api/auth/me", headers={"Authorization": auth})
            return {"status": r.status_code, "json": r.json()}
        if "/api/session/annotate" in url:
            r = client.post("/api/session/annotate", headers={"Authorization": auth.replace("Bearer ", "Bearer ") if auth.startswith("Bearer ") else f"Bearer {u1['token']}"}, json=msg["body"])
            return {"status": r.status_code, "json": r.json()}
        if "/api/flywheel/record" in url:
            if not fail_first["done"]:
                fail_first["done"] = True
                # Injection point: initial attempt fails 503
                return {"status": 503, "json": {"detail": "injected_503_before_reload"}}
            r = client.post("/api/flywheel/record", headers={"Authorization": auth}, json=msg["body"])
            return {"status": r.status_code, "json": r.json()}
        raise AssertionError(f"Unexpected URL {url}")

    result, seen = _run_web_app_scenario(client, scenario, handler)

    assert result["fail1"]["ok"] is False and result["fail1"]["event_seq"] == 1
    assert len(result["activeSameDoc"]) == 1
    assert result["activeSameDoc"][0]["id"] == result["fail1"]["event_id"]
    assert result["seqAfterAnnotate"] >= 1
    assert len(result["retrySameDoc"]) == 1 and result["retrySameDoc"][0]["ok"] is True
    assert result["retrySameDoc"][0]["event_id"] == result["fail1"]["event_id"]
    assert result["activeOtherDoc"] == []
    assert result["activeBob"] == []
    assert result["retryBob"] == []
    # Confirm Bob never dispatched Alice's client_event_id over HTTP
    bob_dispatched = [
        r for r in seen
        if "/api/flywheel/record" in r["url"] and r["headers"].get("Authorization") == f"Bearer {u2['token']}"
    ]
    assert bob_dispatched == []
