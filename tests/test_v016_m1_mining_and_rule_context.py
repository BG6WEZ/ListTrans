# -*- coding: utf-8 -*-
"""V016-M1 R01 回归与反例矩阵：F06 统一聚合/可信读者/事务回滚 + F27 RuleContext 一致性。"""
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from unittest.mock import patch

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.auth_manager import (
    get_auth_connection,
    get_password_hash,
    get_user_by_username,
    init_auth_db,
    issue_token_for_user,
)
from app.binding_decisions import evaluate_hard_conflicts
from app.db_manager import get_db_cursor, init_db
from app.feedback_contract import persist_upload_document_tree, record_flywheel_event
from app.feedback_miner import FlywheelMiner
from app.flywheel_db import FLYWHEEL_CONTRACT_VERSION, init_flywheel_tables
from app.llm_rule_engine import DynamicRuleUnavailableError, LLMRuleManager
from app.matcher import evaluate_dynamic_exclusions, match_item, score_candidate_with_detail
from app.normalizer import clean_text_standard, clean_text_standard_with_diagnostics, dynamic_rule_matches_query
from app.query_preprocessor import extract_query_params, prepare_query
from app.rule_context import RuleContext
from app.server import app

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _make_multi_sheet_xlsx_bytes(sheets_data):
    wb = openpyxl.Workbook()
    first = True
    for sheet_name, rows in sheets_data:
        ws = wb.active if first else wb.create_sheet(title=sheet_name)
        if first:
            ws.title = sheet_name
            first = False
        ws.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量", "甲供材品类"])
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def _seed_m1_users():
    init_auth_db()
    conn = get_auth_connection()
    try:
        pwd_hash = get_password_hash("M1TestPass@123")
        accounts = [
            ("m1_emp_a", "employee", "M1员工A"),
            ("m1_emp_b", "employee", "M1员工B"),
            ("m1_mgr", "manager", "M1经理"),
            ("m1_adm", "admin", "M1管理员"),
        ]
        for uname, role, display in accounts:
            conn.execute(
                """
                INSERT OR IGNORE INTO users (
                    username, password_hash, name, role, is_active, must_change_password, account_epoch
                ) VALUES (?, ?, ?, ?, 1, 0, ?)
                """,
                (uname, pwd_hash, display, role, f"ep-{uname}"),
            )
        conn.commit()
    finally:
        conn.close()
    out = {}
    for uname in ("m1_emp_a", "m1_emp_b", "m1_mgr", "m1_adm"):
        u = get_user_by_username(uname)
        tok = issue_token_for_user(u)
        out[uname] = {"user": u, "token": tok, "headers": {"Authorization": f"Bearer {tok}"}}
    return out


def _seed_doc_row(user_obj, *, raw_name: str, raw_spec: str, professional: str, material_category: str = "", suffix: str = ""):
    uid = uuid.uuid4().hex[:8]
    doc_id = f"doc_m1_{uid}_{suffix}"
    raw_rows = [{
        "row_idx": 1,
        "sheet_index": 0,
        "sheet_name": professional or "清单",
        "source_row_num": 2,
        "name": raw_name,
        "specification": raw_spec,
        "unit": "台",
        "qty": 1.0,
        "loss": 1.0,
        "category": material_category,
        "brand": "",
        "code": "M1.Q1",
        "professional": professional,
    }]
    parsed_rows = [{
        "original_row_idx": 1,
        "original_name": raw_name,
        "original_spec": raw_spec,
        "original_qty": 1.0,
        "original_unit": "台",
        "original_loss": 1.0,
        "original_category": material_category,
        "original_brand": "",
        "original_code": "M1.Q1",
        "professional": professional,
        "parsed_spec": {"name": raw_name, "spec": raw_spec, "clean_text": raw_spec, "model": "", "attrs": {}},
        "top_candidates": [],
        "bindings": [],
        "decision_status": "recommended",
    }]
    persist_upload_document_tree(
        actor_user=user_obj,
        document_id=doc_id,
        upload_task_id=f"up_m1_{uid}_{suffix}",
        work_session_id=f"ws_m1_{uid}_{suffix}",
        original_filename=f"m1_{suffix}.xlsx",
        file_sha256=f"sha_{uid}_{suffix}",
        raw_rows=raw_rows,
        parsed_rows=parsed_rows,
    )
    return parsed_rows[0]


@pytest.fixture()
def isolated_m1_db(tmp_path, monkeypatch):
    """Per-test isolated DB files while keeping conftest outer guardrails intact."""
    db_file = tmp_path / "m1_materials.db"
    auth_file = tmp_path / "m1_auth.db"
    fb_file = tmp_path / "m1_feedback_samples.json"
    monkeypatch.setenv("MATERIALS_DB_PATH", str(db_file))
    monkeypatch.setenv("AUTH_DB_PATH", str(auth_file))
    monkeypatch.setenv("FEEDBACK_SAMPLES_FILE", str(fb_file))

    import app.auth_manager as am
    import app.db_manager as dm
    import app.llm_rule_engine as lre

    monkeypatch.setattr(dm, "DB_PATH", str(db_file))
    monkeypatch.setattr(am, "AUTH_DB_PATH", str(auth_file))
    monkeypatch.setattr(lre, "DB_PATH", str(db_file))
    lre.LLMRuleManager.clear_active_cache()

    init_db()
    init_auth_db()
    init_flywheel_tables(db_path=str(db_file))
    users = _seed_m1_users()
    yield {"db_path": str(db_file), "auth_path": str(auth_file), "fb_file": fb_file, "users": users}
    lre.LLMRuleManager.clear_active_cache()


# ---------------------------------------------------------------------------
# 0. 无副作用导入检查
# ---------------------------------------------------------------------------

def test_rule_context_and_miner_import_zero_side_effects(tmp_path):
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
        [sys.executable, "-c", "import app.rule_context, app.flywheel_db, app.feedback_miner; print('ok')"],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "ok" in proc.stdout
    assert not mat_db.exists()
    assert not auth_db.exists()


# ---------------------------------------------------------------------------
# 1. 聚合正例与兼容：迁移幂等/失败回滚/历史保留 + 同名同专业&跨专业 PARAM_ADJUST
# ---------------------------------------------------------------------------

def test_m1_c01_schema_migration_rollback_and_archive_preservation(tmp_path):
    # 1) Empty DB twice -> idempotent
    empty_db = tmp_path / "empty.db"
    m_e1 = init_flywheel_tables(db_path=str(empty_db))
    m_e2 = init_flywheel_tables(db_path=str(empty_db))
    assert m_e1["contract_version"] == FLYWHEEL_CONTRACT_VERSION
    assert m_e2["migrated_columns"] == []

    # 2) Pre-M1 schema copy (archived aggregates + unscoped/scoped legacy suggestions)
    old_db = tmp_path / "old_pre_m1.db"
    conn = sqlite3.connect(old_db)
    conn.execute("""
        CREATE TABLE flywheel_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_event_id TEXT UNIQUE NOT NULL,
            session_id TEXT NOT NULL,
            row_idx INTEGER NOT NULL,
            discipline TEXT DEFAULT '通用',
            raw_name TEXT NOT NULL,
            raw_name_norm TEXT NOT NULL,
            action_type TEXT NOT NULL,
            final_targets_json TEXT NOT NULL,
            user_id TEXT NOT NULL,
            superseded INTEGER DEFAULT 0,
            is_processed INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE flywheel_rule_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_type TEXT NOT NULL,
            source_pattern TEXT NOT NULL,
            target_value TEXT NOT NULL,
            discipline TEXT DEFAULT '通用',
            support_count INTEGER DEFAULT 1,
            distinct_users INTEGER DEFAULT 1,
            consistency REAL DEFAULT 1.0,
            sample_ids_json TEXT DEFAULT '[]',
            status TEXT DEFAULT 'pending',
            applied_rule_id TEXT DEFAULT '',
            reviewer_id TEXT DEFAULT '',
            reviewed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE flywheel_pattern_aggregates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_pattern TEXT NOT NULL,
            suggestion_type TEXT NOT NULL,
            target_value TEXT NOT NULL,
            archived_support INTEGER DEFAULT 0,
            archived_distinct_users INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_pattern, suggestion_type, target_value)
        )
    """)
    conn.execute("""
        INSERT INTO flywheel_pattern_aggregates (
            source_pattern, suggestion_type, target_value, archived_support, archived_distinct_users
        ) VALUES ('历史归档风机', 'PARAM_ADJUST', '', 7, 4)
    """)
    conn.execute("""
        INSERT INTO flywheel_rule_suggestions (
            suggestion_type, source_pattern, target_value, discipline,
            support_count, distinct_users, consistency, status, applied_rule_id, reviewer_id
        ) VALUES
        ('ALIAS', '旧无上下文词', '标准词', '通用', 5, 3, 0.9, 'accepted', 'snap_old_1', 'old_admin'),
        ('ALIAS', '旧暖通词', '标准暖通词', '暖通', 4, 2, 0.85, 'rejected', '', 'old_admin')
    """)
    conn.commit()
    conn.close()

    # Injected migration failure rolls back BOTH DDL and DML atomically
    def _fail_agg(stage, _cur):
        if stage == "after_migrate_pattern_aggregates":
            raise RuntimeError("injected_agg_migration_boom")

    with pytest.raises(RuntimeError, match="injected_agg_migration_boom"):
        init_flywheel_tables(db_path=str(old_db), _migration_hook=_fail_agg)

    chk = sqlite3.connect(old_db)
    chk.row_factory = sqlite3.Row
    cols_before = {r[1] for r in chk.execute("PRAGMA table_info(flywheel_pattern_aggregates)").fetchall()}
    assert "source_origin" not in cols_before
    assert chk.execute("SELECT archived_support FROM flywheel_pattern_aggregates").fetchone()["archived_support"] == 7
    sug_cols_before = {r[1] for r in chk.execute("PRAGMA table_info(flywheel_rule_suggestions)").fetchall()}
    assert "source_origin" not in sug_cols_before
    chk.close()

    # Successful migration twice -> idempotent, preserves archive counts & legacy suggestion provenance
    res1 = init_flywheel_tables(db_path=str(old_db))
    res2 = init_flywheel_tables(db_path=str(old_db))
    assert res1["migrated_columns"]
    assert res2["migrated_columns"] == []

    chk = sqlite3.connect(old_db)
    chk.row_factory = sqlite3.Row
    agg = chk.execute("SELECT * FROM flywheel_pattern_aggregates WHERE source_pattern = '历史归档风机'").fetchone()
    assert agg is not None
    assert agg["archived_support"] == 7
    assert agg["archived_distinct_users"] == 4
    assert agg["active_support"] == 0
    assert agg["total_support"] == 7
    assert agg["discipline"] == ""  # Never fabricated!
    assert agg["source_origin"] == "legacy_archive"

    sugs = {
        r["source_pattern"]: dict(r)
        for r in chk.execute("SELECT * FROM flywheel_rule_suggestions").fetchall()
    }
    assert sugs["旧无上下文词"]["source_origin"] == "legacy_unscoped"
    assert sugs["旧无上下文词"]["status"] == "accepted"
    assert sugs["旧无上下文词"]["applied_rule_id"] == "snap_old_1"
    assert sugs["旧暖通词"]["source_origin"] == "legacy_pre_m1"
    assert sugs["旧暖通词"]["status"] == "rejected"
    chk.close()

    # 3) Legacy pattern_key schema copy also migrates cleanly without losing history
    legacy_pk_db = tmp_path / "legacy_pk.db"
    conn_pk = sqlite3.connect(legacy_pk_db)
    conn_pk.execute("""
        CREATE TABLE flywheel_pattern_aggregates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_key TEXT NOT NULL,
            discipline TEXT DEFAULT '',
            total_count INTEGER DEFAULT 0,
            correct_count INTEGER DEFAULT 0,
            manual_replace_count INTEGER DEFAULT 0,
            param_adjust_count INTEGER DEFAULT 0,
            dominant_target_code TEXT DEFAULT '',
            sample_ids_json TEXT DEFAULT '[]',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn_pk.execute("""
        INSERT INTO flywheel_pattern_aggregates (
            pattern_key, discipline, total_count, param_adjust_count, dominant_target_code, sample_ids_json
        ) VALUES ('旧模式PK', '', 4, 4, 'PK.01', '[11,12,13,14]')
    """)
    conn_pk.commit()
    conn_pk.close()

    init_flywheel_tables(db_path=str(legacy_pk_db))
    res_pk2 = init_flywheel_tables(db_path=str(legacy_pk_db))
    assert res_pk2["migrated_columns"] == []
    chk_pk = sqlite3.connect(legacy_pk_db)
    chk_pk.row_factory = sqlite3.Row
    pk_row = chk_pk.execute("SELECT * FROM flywheel_pattern_aggregates WHERE source_pattern = '旧模式PK'").fetchone()
    assert pk_row["source_origin"] == "legacy_unknown"
    assert pk_row["discipline"] == ""
    assert pk_row["archived_support"] == 4
    assert pk_row["param_adjust_count"] == 4
    assert pk_row["sample_ids_json"] == "[11,12,13,14]"
    chk_pk.close()


def test_m1_c01_c04_param_adjust_and_cross_discipline_isolation(isolated_m1_db):
    users = isolated_m1_db["users"]
    emp_a = users["m1_emp_a"]["user"]
    emp_b = users["m1_emp_b"]["user"]
    miner = FlywheelMiner()

    # Pre-seed an old unscoped '通用' suggestion AND a 'legacy_archive' aggregate to prove non-interference
    with get_db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO flywheel_rule_suggestions (
                suggestion_type, source_pattern, target_value, discipline,
                support_count, distinct_users, consistency, status, source_origin
            ) VALUES ('ALIAS', '管道阀门PA', '标准蝶阀', '通用', 9, 5, 0.95, 'accepted', 'legacy_unscoped')
        """)
        cur.execute("""
            INSERT INTO flywheel_pattern_aggregates (
                source_pattern, suggestion_type, target_value, discipline, source_origin,
                archived_support, archived_distinct_users, total_support
            ) VALUES ('管道阀门PA', 'PARAM_ADJUST', '', '', 'legacy_archive', 10, 4, 10)
        """)

    # 1. Two independent trusted PARAM_ADJUST in 暖通 + two in 给排水 (same raw_name)
    hvac_1 = _seed_doc_row(emp_a, raw_name="管道阀门PA", raw_spec="DN50", professional="暖通", suffix="h1")
    hvac_2 = _seed_doc_row(emp_b, raw_name="管道阀门PA", raw_spec="DN80", professional="暖通", suffix="h2")
    plumb_1 = _seed_doc_row(emp_a, raw_name="管道阀门PA", raw_spec="DN100", professional="给排水", suffix="p1")
    plumb_2 = _seed_doc_row(emp_b, raw_name="管道阀门PA", raw_spec="DN150", professional="给排水", suffix="p2")

    pa_ids = {}
    for key, u, row_ctx, param_val, tgt_code in (
        ("h1", emp_a, hvac_1, {"dn": 50}, "HV.01"),
        ("h2", emp_b, hvac_2, {"dn": 80}, "HV.01"),
        ("p1", emp_a, plumb_1, {"dn": 100}, "PL.01"),
        ("p2", emp_b, plumb_2, {"dn": 150}, "PL.01"),
    ):
        ev = record_flywheel_event(
            u,
            {
                "client_event_id": f"evt_pa_{key}_{uuid.uuid4().hex[:6]}",
                "document_id": row_ctx["document_id"],
                "sheet_id": row_ctx["sheet_id"],
                "source_row_id": row_ctx["source_row_id"],
                "recommendation_snapshot_id": row_ctx["recommendation_snapshot_id"],
                "event_seq": 1,
                "action_type": "PARAM_ADJUST",
                "extracted_params": param_val,
                "final_targets": [{"code": tgt_code, "name": "标准蝶阀", "spec": f"DN{param_val['dn']}"}],
            },
        )
        pa_ids[key] = ev["record_id"]

    # 2. Same source->target ALIAS in 暖通 (3 events, 2 users -> pending) and 给排水 (2 events, 2 users -> observing)
    for idx, (u, suf) in enumerate(((emp_a, "ah1"), (emp_b, "ah2"), (emp_a, "ah3"))):
        rc = _seed_doc_row(u, raw_name="管道阀门PA", raw_spec="DN50", professional="暖通", suffix=suf)
        record_flywheel_event(
            u,
            {
                "client_event_id": f"evt_al_hv_{idx}_{uuid.uuid4().hex[:6]}",
                "document_id": rc["document_id"],
                "sheet_id": rc["sheet_id"],
                "source_row_id": rc["source_row_id"],
                "recommendation_snapshot_id": rc["recommendation_snapshot_id"],
                "event_seq": 1,
                "action_type": "MANUAL_REPLACE",
                "final_targets": [{"code": "HV.01", "name": "标准蝶阀", "spec": "DN50"}],
            },
        )
    for idx, (u, suf) in enumerate(((emp_a, "ap1"), (emp_b, "ap2"))):
        rc = _seed_doc_row(u, raw_name="管道阀门PA", raw_spec="DN100", professional="给排水", suffix=suf)
        record_flywheel_event(
            u,
            {
                "client_event_id": f"evt_al_pl_{idx}_{uuid.uuid4().hex[:6]}",
                "document_id": rc["document_id"],
                "sheet_id": rc["sheet_id"],
                "source_row_id": rc["source_row_id"],
                "recommendation_snapshot_id": rc["recommendation_snapshot_id"],
                "event_seq": 1,
                "action_type": "MANUAL_REPLACE",
                "final_targets": [{"code": "PL.01", "name": "标准蝶阀", "spec": "DN100"}],
            },
        )

    res1 = miner.run_mining()
    res2 = miner.run_mining()  # Idempotent second run must not double-count or clobber archive
    assert res1["status"] == "success"
    assert res1["processed_records_count"] == 9
    assert res2["processed_records_count"] == 0

    with get_db_cursor() as cur:
        aggs = {
            (r["discipline"], r["source_origin"]): dict(r)
            for r in cur.execute(
                "SELECT * FROM flywheel_pattern_aggregates WHERE source_pattern = '管道阀门pa'"
            ).fetchall()
        }
        # Note: clean_text lowercases English 'PA' -> 'pa'
        if not aggs:
            aggs = {
                (r["discipline"], r["source_origin"]): dict(r)
                for r in cur.execute(
                    "SELECT * FROM flywheel_pattern_aggregates WHERE LOWER(source_pattern) = '管道阀门pa'"
                ).fetchall()
            }
        # Check archived row untouched (note source_pattern '管道阀门PA' stays exact case for legacy_archive)
        arch = cur.execute(
            "SELECT * FROM flywheel_pattern_aggregates WHERE source_origin = 'legacy_archive'"
        ).fetchone()
        assert arch["archived_support"] == 10
        assert arch["archived_distinct_users"] == 4
        assert arch["total_support"] == 10

        hv_agg = aggs[("暖通", "trusted_recompute")]
        pl_agg = aggs[("给排水", "trusted_recompute")]
        assert hv_agg["active_support"] == 2
        assert hv_agg["active_distinct_users"] == 2
        assert hv_agg["param_adjust_count"] == 2
        assert hv_agg["dominant_target_code"] == "HV.01"
        assert json.loads(hv_agg["sample_ids_json"]) == sorted([pa_ids["h1"], pa_ids["h2"]])

        assert pl_agg["active_support"] == 2
        assert pl_agg["active_distinct_users"] == 2
        assert pl_agg["param_adjust_count"] == 2
        assert pl_agg["dominant_target_code"] == "PL.01"
        assert json.loads(pl_agg["sample_ids_json"]) == sorted([pa_ids["p1"], pa_ids["p2"]])

        # Mark 暖通 suggestion accepted; re-run mining and verify 给排水 remains independent
        cur.execute(
            """
            UPDATE flywheel_rule_suggestions
            SET status = 'accepted'
            WHERE discipline = '暖通' AND source_origin = 'trusted_miner' AND target_value = '标准蝶阀'
            """
        )

    miner.run_mining()
    with get_db_cursor() as cur:
        sugs = cur.execute(
            """
            SELECT discipline, source_origin, support_count, distinct_users, status
            FROM flywheel_rule_suggestions
            WHERE target_value = '标准蝶阀'
            """
        ).fetchall()
        by_key = {(r["discipline"], r["source_origin"]): dict(r) for r in sugs}
        assert by_key[("通用", "legacy_unscoped")]["support_count"] == 9
        assert by_key[("通用", "legacy_unscoped")]["status"] == "accepted"
        assert by_key[("暖通", "trusted_miner")]["support_count"] == 3
        assert by_key[("暖通", "trusted_miner")]["status"] == "accepted"
        assert by_key[("给排水", "trusted_miner")]["support_count"] == 2
        assert by_key[("给排水", "trusted_miner")]["status"] == "observing"


# ---------------------------------------------------------------------------
# 2. 可信反例：legacy / untrusted / 伪造 trusted / superseded / 迟到样本 / 重试
# ---------------------------------------------------------------------------

def test_m1_c02_untrusted_forged_superseded_stale_and_retry_excluded(isolated_m1_db):
    users = isolated_m1_db["users"]
    emp_a = users["m1_emp_a"]["user"]
    emp_b = users["m1_emp_b"]["user"]
    miner = FlywheelMiner()

    # Valid trusted events: emp_a (2 files) + emp_b (1 file) -> exact 3 supports, 2 distinct users
    # Plus retry of same client_event_id -> still 3 supports
    r_a1 = _seed_doc_row(emp_a, raw_name="M1可信隔离阀", raw_spec="DN50", professional="暖通", suffix="t1")
    r_a2 = _seed_doc_row(emp_a, raw_name="M1可信隔离阀", raw_spec="DN50", professional="暖通", suffix="t2")
    r_b1 = _seed_doc_row(emp_b, raw_name="M1可信隔离阀", raw_spec="DN50", professional="暖通", suffix="t3")

    ev_a1_payload = {
        "client_event_id": f"evt_retry_{uuid.uuid4().hex[:6]}",
        "document_id": r_a1["document_id"],
        "sheet_id": r_a1["sheet_id"],
        "source_row_id": r_a1["source_row_id"],
        "recommendation_snapshot_id": r_a1["recommendation_snapshot_id"],
        "event_seq": 1,
        "action_type": "MANUAL_REPLACE",
        "final_targets": [{"code": "V.01", "name": "标准止回阀", "spec": "DN50"}],
    }
    res_first = record_flywheel_event(emp_a, ev_a1_payload)
    res_retry = record_flywheel_event(emp_a, ev_a1_payload)
    assert res_retry["idempotent_replay"] is True

    # On r_a2: record seq=2 FIRST, then late arrival seq=1 (marked is_stale_arrival=1, superseded=1)
    ev_a2_seq2 = record_flywheel_event(
        emp_a,
        {
            "client_event_id": f"evt_seq2_{uuid.uuid4().hex[:6]}",
            "document_id": r_a2["document_id"],
            "sheet_id": r_a2["sheet_id"],
            "source_row_id": r_a2["source_row_id"],
            "recommendation_snapshot_id": r_a2["recommendation_snapshot_id"],
            "event_seq": 2,
            "action_type": "MANUAL_REPLACE",
            "final_targets": [{"code": "V.01", "name": "标准止回阀", "spec": "DN50"}],
        },
    )
    ev_a2_stale = record_flywheel_event(
        emp_a,
        {
            "client_event_id": f"evt_stale_{uuid.uuid4().hex[:6]}",
            "document_id": r_a2["document_id"],
            "sheet_id": r_a2["sheet_id"],
            "source_row_id": r_a2["source_row_id"],
            "recommendation_snapshot_id": r_a2["recommendation_snapshot_id"],
            "event_seq": 1,
            "action_type": "MANUAL_REPLACE",
            "final_targets": [{"code": "V.BAD", "name": "迟到错误阀", "spec": "DN50"}],
        },
    )
    assert ev_a2_stale["is_stale_arrival"] == 1

    # On r_b1: record seq=1 (wrong target), then supersede with seq=2 (correct target)
    record_flywheel_event(
        emp_b,
        {
            "client_event_id": f"evt_b_old_{uuid.uuid4().hex[:6]}",
            "document_id": r_b1["document_id"],
            "sheet_id": r_b1["sheet_id"],
            "source_row_id": r_b1["source_row_id"],
            "recommendation_snapshot_id": r_b1["recommendation_snapshot_id"],
            "event_seq": 1,
            "action_type": "MANUAL_REPLACE",
            "final_targets": [{"code": "V.OLD", "name": "被替代旧阀", "spec": "DN50"}],
        },
    )
    ev_b1_new = record_flywheel_event(
        emp_b,
        {
            "client_event_id": f"evt_b_new_{uuid.uuid4().hex[:6]}",
            "document_id": r_b1["document_id"],
            "sheet_id": r_b1["sheet_id"],
            "source_row_id": r_b1["source_row_id"],
            "recommendation_snapshot_id": r_b1["recommendation_snapshot_id"],
            "event_seq": 2,
            "action_type": "MANUAL_REPLACE",
            "final_targets": [{"code": "V.01", "name": "标准止回阀", "spec": "DN50"}],
        },
    )

    # Inject poison records: legacy_untrusted + forged SQLite trusted rows lacking real doc/row/actor
    poison_ids = []
    with get_db_cursor(commit=True) as cur:
        for i in range(6):
            cur.execute(
                """
                INSERT INTO flywheel_records (
                    client_event_id, session_id, row_idx, discipline,
                    raw_name, raw_name_norm, raw_spec, action_type, final_targets_json,
                    user_id, actor_user_id, actor_account_epoch,
                    document_id, sheet_id, source_row_id, payload_digest,
                    superseded, is_processed, trust_level
                ) VALUES (?, ?, ?, '暖通', 'M1可信隔离阀', 'M1可信隔离阀', 'DN50', 'MANUAL_REPLACE', ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
                """,
                (
                    f"poison_{i}_{uuid.uuid4().hex[:6]}",
                    "default_session" if i < 3 else "forged_sess",
                    i,
                    json.dumps([{"code": "V.POISON", "name": "毒化假目标", "spec": "DN50"}]),
                    "legacy_anonymous" if i < 3 else f"fake_user_{i}",
                    None if i < 4 else 9999 + i,
                    "" if i < 4 else "fake_epoch",
                    "" if i < 4 else f"fake_doc_{i}",
                    "" if i < 4 else f"fake_sh_{i}",
                    "" if i < 4 else f"fake_row_{i}",
                    "" if i < 5 else "fake_digest",
                    "legacy_untrusted" if i < 3 else "trusted",
                ),
            )
            poison_ids.append(cur.lastrowid)

    # Same-user two-row threshold check in 强电 (2 events by emp_a only -> distinct_users=1 -> observing!)
    e_r1 = _seed_doc_row(emp_a, raw_name="单人强电盒", raw_spec="", professional="强电", suffix="e1")
    e_r2 = _seed_doc_row(emp_a, raw_name="单人强电盒", raw_spec="", professional="强电", suffix="e2")
    for idx, rc in enumerate((e_r1, e_r2)):
        record_flywheel_event(
            emp_a,
            {
                "client_event_id": f"evt_single_u_{idx}_{uuid.uuid4().hex[:6]}",
                "document_id": rc["document_id"],
                "sheet_id": rc["sheet_id"],
                "source_row_id": rc["source_row_id"],
                "recommendation_snapshot_id": rc["recommendation_snapshot_id"],
                "event_seq": 1,
                "action_type": "MANUAL_REPLACE",
                "final_targets": [{"code": "E.01", "name": "配电箱", "spec": ""}],
            },
        )

    mine_res = miner.run_mining()
    assert mine_res["status"] == "success"

    with get_db_cursor() as cur:
        sug_ok = cur.execute(
            """
            SELECT * FROM flywheel_rule_suggestions
            WHERE source_pattern = 'M1可信隔离阀' AND target_value = '标准止回阀' AND discipline = '暖通'
            """
        ).fetchone()
        assert sug_ok is not None
        # Denominator & numerator must ignore all 6 poison + 2 superseded/stale rows!
        assert sug_ok["support_count"] == 3
        assert sug_ok["distinct_users"] == 2
        assert sug_ok["consistency"] == 1.0
        assert sug_ok["status"] == "pending"
        assert json.loads(sug_ok["sample_ids_json"]) == sorted(
            [res_first["record_id"], ev_a2_seq2["record_id"], ev_b1_new["record_id"]]
        )

        # Poison, stale, and superseded targets must NEVER become suggestions
        bad_sugs = cur.execute(
            """
            SELECT * FROM flywheel_rule_suggestions
            WHERE target_value IN ('毒化假目标', '迟到错误阀', '被替代旧阀')
            """
        ).fetchall()
        assert bad_sugs == []

        # Poison records must NOT even be marked is_processed = 1
        placeholders = ",".join("?" for _ in poison_ids)
        proc_flags = [
            r["is_processed"]
            for r in cur.execute(
                f"SELECT is_processed FROM flywheel_records WHERE id IN ({placeholders})",
                poison_ids,
            ).fetchall()
        ]
        assert proc_flags == [0] * len(poison_ids)

        # Same user across 2 files/rows -> support=2, distinct_users=1, observing (never faked to 2!)
        sug_single = cur.execute(
            "SELECT * FROM flywheel_rule_suggestions WHERE source_pattern = '单人强电盒' AND discipline = '强电'"
        ).fetchone()
        assert sug_single is not None
        assert sug_single["support_count"] == 2
        assert sug_single["distinct_users"] == 1
        assert sug_single["status"] == "observing"


# ---------------------------------------------------------------------------
# 3. 专业矩阵与 RuleContext 一致性：清洗 / 抽取 / 匹配 / 诊断 / 错误可见
# ---------------------------------------------------------------------------

def test_m1_c03_c06_rule_context_matrix_and_error_visibility(isolated_m1_db):
    rules = [
        {
            "rule_id": "rule-hvac",
            "scope_level": "discipline",
            "scope_target": "暖通",
            "parsed_dsl": {
                "cleaning_rules": [{"action": "remove_token", "token": "TOK_HVAC"}],
                "extraction_rules": [{"key": "hvac_tag", "pattern": r"HV-(\d+)"}],
                "matching_rules": {"must_not_contain": ["BLK_HVAC"]},
            },
        },
        {
            "rule_id": "rule-plumb",
            "scope_level": "discipline",
            "scope_target": "给排水",
            "parsed_dsl": {
                "cleaning_rules": [{"action": "remove_token", "token": "TOK_PLUMB"}],
                "extraction_rules": [{"key": "plumb_tag", "pattern": r"PL-(\d+)"}],
                "matching_rules": {"must_not_contain": ["BLK_PLUMB"]},
            },
        },
        {
            "rule_id": "rule-elec",
            "scope_level": "discipline",
            "scope_target": "强电",
            "parsed_dsl": {
                "cleaning_rules": [{"action": "remove_token", "token": "TOK_ELEC"}],
                "extraction_rules": [{"key": "elec_tag", "pattern": r"EL-(\d+)"}],
                "matching_rules": {"must_not_contain": ["BLK_ELEC"]},
            },
        },
        {
            "rule_id": "rule-medgas",
            "scope_level": "discipline",
            "scope_target": "医气",
            "parsed_dsl": {
                "cleaning_rules": [{"action": "remove_token", "token": "TOK_MED"}],
                "extraction_rules": [{"key": "med_tag", "pattern": r"MG-(\d+)"}],
                "matching_rules": {"must_not_contain": ["BLK_MED"]},
            },
        },
        {
            "rule_id": "rule-cat-fcu",
            "scope_level": "category",
            "scope_target": "风机盘管",
            "parsed_dsl": {
                "cleaning_rules": [{"action": "remove_token", "token": "TOK_FCU"}],
                "extraction_rules": [{"key": "fcu_tag", "pattern": r"FCU-(\d+)"}],
                "matching_rules": {"must_not_contain": ["BLK_FCU"]},
            },
        },
        {
            "rule_id": "rule-univ",
            "scope_level": "universal",
            "scope_target": "任意目标",
            "parsed_dsl": {
                "cleaning_rules": [{"action": "remove_token", "token": "TOK_UNIV"}],
                "extraction_rules": [{"key": "univ_tag", "pattern": r"UV-(\d+)"}],
                "matching_rules": {"must_not_contain": ["BLK_UNIV"]},
            },
        },
    ]
    LLMRuleManager._active_rules_cache = rules

    # Matrix check across 暖通 / 给排水 / 强电 / 医用气体(alias) / 缺专业(with misleading material_category='给排水')
    cases = [
        ("暖通", "", "离心排风机 TOK_HVAC TOK_PLUMB TOK_UNIV", "HV-10 PL-20 EL-30 MG-40 UV-50",
         {"hvac_tag": 10.0, "univ_tag": 50.0}, {"plumb_tag", "elec_tag", "med_tag"}, "BLK_HVAC", "BLK_PLUMB"),
        ("给排水", "", "给水泵 TOK_PLUMB TOK_HVAC TOK_UNIV", "HV-10 PL-20 EL-30 MG-40 UV-50",
         {"plumb_tag": 20.0, "univ_tag": 50.0}, {"hvac_tag", "elec_tag", "med_tag"}, "BLK_PLUMB", "BLK_HVAC"),
        ("强电", "", "配电箱 TOK_ELEC TOK_PLUMB TOK_UNIV", "HV-10 PL-20 EL-30 MG-40 UV-50",
         {"elec_tag": 30.0, "univ_tag": 50.0}, {"hvac_tag", "plumb_tag", "med_tag"}, "BLK_ELEC", "BLK_PLUMB"),
        ("医用气体", "", "氧气终端 TOK_MED TOK_HVAC TOK_UNIV", "HV-10 PL-20 EL-30 MG-40 UV-50",
         {"med_tag": 40.0, "univ_tag": 50.0}, {"hvac_tag", "plumb_tag", "elec_tag"}, "BLK_MED", "BLK_HVAC"),
        # Missing professional even when material_category='给排水' and name='给水泵' -> ONLY universal runs!
        ("", "给排水", "给水泵 TOK_PLUMB TOK_HVAC TOK_UNIV", "HV-10 PL-20 EL-30 MG-40 UV-50",
         {"univ_tag": 50.0}, {"hvac_tag", "plumb_tag", "elec_tag", "med_tag"}, "BLK_UNIV", "BLK_PLUMB"),
    ]

    for prof, mat_cat, name_in, spec_in, expect_attrs, forbid_attrs, hit_blk, safe_blk in cases:
        pq = prepare_query(name_in, spec_in, prof, category=mat_cat)
        # 1) Cleaning consistency
        assert "tok_univ" not in pq["name"].lower(), prof
        if prof == "暖通":
            assert "tok_hvac" not in pq["name"].lower()
            assert "tok_plumb" in pq["name"].lower()
        elif prof == "给排水":
            assert "tok_plumb" not in pq["name"].lower()
            assert "tok_hvac" in pq["name"].lower()
        elif prof == "强电":
            assert "tok_elec" not in pq["name"].lower()
            assert "tok_plumb" in pq["name"].lower()
        elif prof == "医用气体":
            assert "tok_med" not in pq["name"].lower()
            assert "tok_hvac" in pq["name"].lower()
        else:
            # Missing professional skips ALL discipline cleaning rules!
            assert "tok_plumb" in pq["name"].lower()
            assert "tok_hvac" in pq["name"].lower()

        # 2) Extraction consistency
        attrs = pq["parsed_spec"]["attrs"]
        for k, v in expect_attrs.items():
            assert attrs.get(k) == v, (prof, k, attrs)
        for fk in forbid_attrs:
            assert fk not in attrs, (prof, fk, attrs)

        # 3) Matching consistency (evaluate_dynamic_exclusions & score_candidate_with_detail)
        cand_hit = {"code": "C-HIT", "name": "设备", "specification": hit_blk, "category": prof or "通用", "unit": "台"}
        cand_safe = {"code": "C-SAFE", "name": "设备", "specification": safe_blk, "category": prof or "通用", "unit": "台"}
        assert evaluate_dynamic_exclusions(pq, cand_hit)
        assert evaluate_dynamic_exclusions(pq, cand_safe) == []
        s_hit, _, d_hit = score_candidate_with_detail(pq, cand_hit)
        assert s_hit == 0.0 and d_hit["hard_blocked"] is True
        s_safe, _, d_safe = score_candidate_with_detail(pq, cand_safe)
        assert d_safe["hard_blocked"] is False and s_safe > 0.0

    # Category-scoped rule consistency across cleaning, extraction, matching
    pq_fcu = prepare_query("卧式风机盘管 TOK_FCU", "FCU-77 TOK_FCU", "暖通", category="甲供材")
    assert "tok_fcu" not in pq_fcu["name"].lower()
    assert "tok_fcu" not in pq_fcu["specification"].lower()
    assert pq_fcu["parsed_spec"]["attrs"].get("fcu_tag") == 77.0

    # Missing context for category rule -> skipped with diagnostic
    cleaned_no_cat, diag_no_cat = clean_text_standard_with_diagnostics("TOK_FCU TOK_PLUMB", discipline="", category="")
    assert "tok_fcu" in cleaned_no_cat.lower()
    assert "tok_plumb" in cleaned_no_cat.lower()
    skipped_reasons = {s["rule_id"]: s["reason"] for s in diag_no_cat["skipped_rules"]}
    assert skipped_reasons["rule-plumb"] == "missing_professional_context"
    assert skipped_reasons["rule-cat-fcu"] == "missing_category_or_name_context"

    # Error visibility (M1-C06): broken regex must surface in diagnostics and fail-closed in matcher/test_rule_execution
    LLMRuleManager._active_rules_cache = [{
        "rule_id": "broken-regex",
        "scope_level": "universal",
        "scope_target": "",
        "parsed_dsl": {
            "cleaning_rules": [{"action": "regex_replace", "pattern": "[unclosed", "replacement": "x"}],
        },
    }]
    pq_err = prepare_query("测试风机", "DN100", "暖通")
    assert pq_err["parsed_spec"]["rule_diagnostics"]["degraded"] is True
    assert pq_err["parsed_spec"]["rule_diagnostics"]["errors"]
    assert evaluate_dynamic_exclusions(pq_err, {"code": "X", "name": "风机", "specification": "DN100"})
    err_score, err_reasons, err_det = score_candidate_with_detail(
        pq_err, {"code": "X", "name": "风机", "specification": "DN100", "category": "暖通", "unit": "台"}
    )
    assert err_score == 0.0 and err_det["hard_blocked"] is True
    assert any("动态规则不可用" in r for r in err_reasons)

    test_exec_err = LLMRuleManager.test_rule_execution(
        {"cleaning_rules": [{"action": "regex_replace", "pattern": "[unclosed", "replacement": "x"}]},
        "测试文本",
    )
    assert test_exec_err["success"] is False

    # Preserve V015 negation & unit conflicts alongside RuleContext
    LLMRuleManager._active_rules_cache = []
    assert (
        evaluate_hard_conflicts(
            {"original_name": "非防爆风机", "original_spec": "", "original_unit": "台", "professional": "暖通"},
            {"code": "F1", "name": "防爆风机", "specification": "防爆", "unit": "台", "category": "暖通"},
        )
        is not None
    )


# ---------------------------------------------------------------------------
# 4. 事务故障注入（4 个关键阶段）、锁释放重试、并发与替代重算
# ---------------------------------------------------------------------------

def test_m1_c05_transaction_rollback_concurrency_and_supersede_recompute(isolated_m1_db):
    users = isolated_m1_db["users"]
    emp_a = users["m1_emp_a"]["user"]
    emp_b = users["m1_emp_b"]["user"]
    miner = FlywheelMiner()

    # Seed ALIAS (2 records), BIND_MULTI (2 records), PARAM_ADJUST (2 records)
    r_al1 = _seed_doc_row(emp_a, raw_name="事务阀门TX", raw_spec="DN50", professional="暖通", suffix="al1")
    r_al2 = _seed_doc_row(emp_b, raw_name="事务阀门TX", raw_spec="DN50", professional="暖通", suffix="al2")
    r_bd1 = _seed_doc_row(emp_a, raw_name="事务组合TX", raw_spec="DN50", professional="暖通", suffix="bd1")
    r_bd2 = _seed_doc_row(emp_b, raw_name="事务组合TX", raw_spec="DN50", professional="暖通", suffix="bd2")
    r_pa1 = _seed_doc_row(emp_a, raw_name="事务微调TX", raw_spec="DN50", professional="暖通", suffix="pa1")
    r_pa2 = _seed_doc_row(emp_b, raw_name="事务微调TX", raw_spec="DN80", professional="暖通", suffix="pa2")

    for u, rc, act, tgts, ext in (
        (emp_a, r_al1, "MANUAL_REPLACE", [{"code": "TX.1", "name": "标准闸阀"}], {}),
        (emp_b, r_al2, "MANUAL_REPLACE", [{"code": "TX.1", "name": "标准闸阀"}], {}),
        (emp_a, r_bd1, "BIND_MULTI", [{"code": "B1", "name": "主阀"}, {"code": "B2", "name": "法兰"}], {}),
        (emp_b, r_bd2, "BIND_MULTI", [{"code": "B1", "name": "主阀"}, {"code": "B2", "name": "法兰"}], {}),
        (emp_a, r_pa1, "PARAM_ADJUST", [{"code": "PA.1", "name": "蝶阀"}], {"dn": 50}),
        (emp_b, r_pa2, "PARAM_ADJUST", [{"code": "PA.1", "name": "蝶阀"}], {"dn": 80}),
    ):
        record_flywheel_event(
            u,
            {
                "client_event_id": f"evt_tx_{uuid.uuid4().hex[:6]}",
                "document_id": rc["document_id"],
                "sheet_id": rc["sheet_id"],
                "source_row_id": rc["source_row_id"],
                "recommendation_snapshot_id": rc["recommendation_snapshot_id"],
                "event_seq": 1,
                "action_type": act,
                "final_targets": tgts,
                "extracted_params": ext,
            },
        )

    # Inject failures at all 4 required stages; every failure must roll back completely
    for stage in (
        "after_first_suggestion_write",
        "after_binding_suggestion_write",
        "after_param_aggregate_write",
        "before_commit",
    ):
        def _hook(cur_stage, _cur, target=stage):
            if cur_stage == target:
                raise RuntimeError(f"injected_fault_{target}")

        with pytest.raises(RuntimeError, match=f"injected_fault_{stage}"):
            miner.run_mining(_tx_hook=_hook)

        with get_db_cursor() as cur:
            assert cur.execute("SELECT COUNT(*) FROM flywheel_rule_suggestions").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM flywheel_pattern_aggregates").fetchone()[0] == 0
            assert cur.execute("SELECT COUNT(*) FROM flywheel_records WHERE is_processed = 1").fetchone()[0] == 0

    # Concurrent execution (2 threads racing on the same 6 unprocessed records)
    results = []
    errors = []

    def _worker():
        try:
            results.append(miner.run_mining())
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert not errors, errors
    assert len(results) == 2
    assert sum(r["processed_records_count"] for r in results) == 6

    with get_db_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM flywheel_records WHERE is_processed = 0").fetchone()[0] == 0
        sug_al = cur.execute(
            "SELECT * FROM flywheel_rule_suggestions WHERE source_pattern = '事务阀门TX'"
        ).fetchone()
        assert sug_al["support_count"] == 2
        assert sug_al["status"] == "observing"

        agg_pa = cur.execute(
            "SELECT * FROM flywheel_pattern_aggregates WHERE source_pattern = '事务微调TX'"
        ).fetchone()
        assert agg_pa["active_support"] == 2

    # Supersede already-processed feedback (r_al1 & r_pa1 overwritten by seq=2 CONFIRM_TOP1)
    record_flywheel_event(
        emp_a,
        {
            "client_event_id": f"evt_tx_sup_al_{uuid.uuid4().hex[:6]}",
            "document_id": r_al1["document_id"],
            "sheet_id": r_al1["sheet_id"],
            "source_row_id": r_al1["source_row_id"],
            "recommendation_snapshot_id": r_al1["recommendation_snapshot_id"],
            "event_seq": 2,
            "action_type": "CONFIRM_TOP1",
            "final_targets": [{"code": "TX.2", "name": "球阀"}],
        },
    )
    record_flywheel_event(
        emp_a,
        {
            "client_event_id": f"evt_tx_sup_pa_{uuid.uuid4().hex[:6]}",
            "document_id": r_pa1["document_id"],
            "sheet_id": r_pa1["sheet_id"],
            "source_row_id": r_pa1["source_row_id"],
            "recommendation_snapshot_id": r_pa1["recommendation_snapshot_id"],
            "event_seq": 2,
            "action_type": "CONFIRM_TOP1",
            "final_targets": [{"code": "PA.2", "name": "球阀"}],
        },
    )

    miner.run_mining()
    with get_db_cursor() as cur:
        sug_after = cur.execute(
            "SELECT * FROM flywheel_rule_suggestions WHERE source_pattern = '事务阀门TX'"
        ).fetchone()
        # Must recompute down to 1 and transition out of observing/pending (never retain false 2!)
        assert sug_after["support_count"] == 1
        assert sug_after["status"] == "superseded"

        agg_after = cur.execute(
            "SELECT * FROM flywheel_pattern_aggregates WHERE source_pattern = '事务微调TX'"
        ).fetchone()
        assert agg_after["active_support"] == 1
        assert agg_after["param_adjust_count"] == 1


# ---------------------------------------------------------------------------
# 5. 真实 HTTP 链路：认证上传 -> 事件记录 -> 真实挖掘 -> 建议/聚合/统计/权限/错误状态
# ---------------------------------------------------------------------------

def test_m1_http_end_to_end_pipeline_permissions_and_errors(isolated_m1_db):
    users = isolated_m1_db["users"]
    emp_a = users["m1_emp_a"]
    emp_b = users["m1_emp_b"]
    mgr = users["m1_mgr"]
    adm = users["m1_adm"]
    client = TestClient(app)

    xlsx_bytes = _make_multi_sheet_xlsx_bytes([
        ("医用气体", [
            ["M1-01", "HTTP医气专用阀", "DN25", "个", 5, "医用管阀"],
            ["M1-02", "HTTP微调铜管", "φ15*1.0", "米", 10, "铜管"],
        ]),
    ])

    def _upload_and_wait(hdrs, fname):
        up = client.post(
            "/api/upload",
            headers=hdrs,
            files={"file": (fname, xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert up.status_code == 200, up.text
        tid = up.json()["task_id"]
        for _ in range(60):
            st = client.get(f"/api/upload/status?task_id={tid}", headers=hdrs)
            assert st.status_code == 200, st.text
            body = st.json()
            if body["status"] == "completed":
                return body["result"]
            assert body["status"] != "failed", body
            time.sleep(0.1)
        raise AssertionError("upload timed out")

    rows_a1 = _upload_and_wait(emp_a["headers"], "m1_http_a1.xlsx")
    rows_a2 = _upload_and_wait(emp_a["headers"], "m1_http_a2.xlsx")
    rows_b1 = _upload_and_wait(emp_b["headers"], "m1_http_b1.xlsx")

    # 3 MANUAL_REPLACE on row 0 (医气), 2 PARAM_ADJUST on row 1 (医气)
    for hdrs, rc, seq in (
        (emp_a["headers"], rows_a1[0], 1),
        (emp_a["headers"], rows_a2[0], 1),
        (emp_b["headers"], rows_b1[0], 1),
    ):
        resp = client.post(
            "/api/flywheel/record",
            headers=hdrs,
            json={
                "client_event_id": f"http_al_{uuid.uuid4().hex[:8]}",
                "document_id": rc["document_id"],
                "sheet_id": rc["sheet_id"],
                "source_row_id": rc["source_row_id"],
                "recommendation_snapshot_id": rc["recommendation_snapshot_id"],
                "event_seq": seq,
                "action_type": "MANUAL_REPLACE",
                "final_targets": [{"code": "MG.V1", "name": "医用气体截止阀", "spec": "DN25"}],
            },
        )
        assert resp.status_code == 200, resp.text

    for hdrs, rc, dn_val in (
        (emp_a["headers"], rows_a1[1], 15),
        (emp_b["headers"], rows_b1[1], 15),
    ):
        resp = client.post(
            "/api/flywheel/record",
            headers=hdrs,
            json={
                "client_event_id": f"http_pa_{uuid.uuid4().hex[:8]}",
                "document_id": rc["document_id"],
                "sheet_id": rc["sheet_id"],
                "source_row_id": rc["source_row_id"],
                "recommendation_snapshot_id": rc["recommendation_snapshot_id"],
                "event_seq": 1,
                "action_type": "PARAM_ADJUST",
                "extracted_params": {"dn": dn_val},
                "final_targets": [{"code": "MG.P1", "name": "脱脂无缝紫铜管", "spec": "φ15*1.0"}],
            },
        )
        assert resp.status_code == 200, resp.text

    # Auth permissions: unauth -> 401, employee -> 403 on miner/run and aggregates
    assert client.post("/api/flywheel/miner/run").status_code == 401
    assert client.post("/api/flywheel/miner/run", headers=emp_a["headers"]).status_code == 403
    assert client.get("/api/flywheel/aggregates").status_code == 401
    assert client.get("/api/flywheel/aggregates", headers=emp_a["headers"]).status_code == 403

    # Admin triggers real mining over HTTP
    mine_resp = client.post("/api/flywheel/miner/run", headers=adm["headers"])
    assert mine_resp.status_code == 200, mine_resp.text
    assert mine_resp.json()["result"]["status"] == "success"
    assert mine_resp.json()["result"]["processed_records_count"] == 5

    # Read suggestions & aggregates as manager
    sug_resp = client.get("/api/flywheel/suggestions?discipline=医用气体", headers=mgr["headers"])
    assert sug_resp.status_code == 200, sug_resp.text
    sugs = sug_resp.json()["suggestions"]
    med_sug = next((s for s in sugs if s["source_pattern"] == "HTTP医气专用阀"), None)
    assert med_sug is not None
    assert med_sug["discipline"] == "医气"
    assert med_sug["support_count"] == 3
    assert med_sug["distinct_users"] == 2
    assert med_sug["status"] == "pending"

    agg_resp = client.get("/api/flywheel/aggregates?discipline=医气", headers=mgr["headers"])
    assert agg_resp.status_code == 200, agg_resp.text
    aggs = agg_resp.json()["aggregates"]
    med_agg = next((a for a in aggs if a["source_pattern"] == "HTTP微调铜管"), None)
    assert med_agg is not None
    assert med_agg["active_support"] == 2
    assert med_agg["active_distinct_users"] == 2
    assert med_agg["param_adjust_count"] == 2

    stats_resp = client.get("/api/flywheel/statistics", headers=mgr["headers"])
    assert stats_resp.status_code == 200
    assert stats_resp.json()["total_records"] >= 5
    assert stats_resp.json()["pattern_aggregates_count"] >= 1

    # Explicit 500 when mining fails over HTTP (never fake 200)
    with patch("app.feedback_miner.miner.run_mining", side_effect=RuntimeError("http_mining_boom")):
        fail_resp = client.post("/api/flywheel/miner/run", headers=adm["headers"])
        assert fail_resp.status_code == 500

    # HTTP /api/llm-rules/test with RuleContext scoping and error visibility
    scope_skip = client.post(
        "/api/llm-rules/test",
        headers=emp_a["headers"],
        json={
            "parsed_dsl": {"cleaning_rules": [{"action": "remove_token", "token": "DELME"}]},
            "sample_text": "设备 DELME",
            "scope_level": "discipline",
            "scope_target": "给排水",
            "professional": "暖通",
        },
    )
    assert scope_skip.status_code == 200
    assert scope_skip.json()["in_scope"] is False
    assert scope_skip.json()["scope_reason"] == "discipline_mismatch"
    assert scope_skip.json()["cleaned_text"] == "设备 DELME"

    bad_regex_http = client.post(
        "/api/llm-rules/test",
        headers=emp_a["headers"],
        json={
            "parsed_dsl": {"cleaning_rules": [{"action": "regex_replace", "pattern": "[broken", "replacement": ""}]},
            "sample_text": "设备",
        },
    )
    assert bad_regex_http.status_code == 200
    assert bad_regex_http.json()["success"] is False
