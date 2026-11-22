# -*- coding: utf-8 -*-
"""
Tests for Smart Mining Engine (tests/test_feedback_miner.py).
Validates:
1. Domain tokenization and abbreviation extraction.
2. Two-phase mining and multi-tier statistical gating over M0 trusted events.
3. Legacy/untrusted direct SQLite inserts are excluded from suggestions.
"""
import json
import uuid
import pytest

from app.auth_manager import (
    get_auth_connection,
    get_password_hash,
    get_user_by_username,
    init_auth_db,
)
from app.db_manager import get_db_cursor
from app.feedback_contract import persist_upload_document_tree, record_flywheel_event
from app.feedback_miner import domain_tokenize, extract_alias_candidate, get_domain_vocab, miner
from app.flywheel_db import init_flywheel_tables
from app.preprocessor import clean_text


@pytest.fixture(autouse=True)
def _flywheel_schema():
    """A-M0-05 / V016-M1: 按实际依赖初始化隔离库中的飞轮与认证 Schema。"""
    init_auth_db()
    init_flywheel_tables()
    conn = get_auth_connection()
    try:
        pwd_hash = get_password_hash("UnitTest@12345")
        for uname, display in (("fm_alice", "飞轮测试甲"), ("fm_bob", "飞轮测试乙")):
            conn.execute(
                """
                INSERT OR IGNORE INTO users (
                    username, password_hash, name, role, is_active, must_change_password, account_epoch
                ) VALUES (?, ?, ?, 'employee', 1, 0, ?)
                """,
                (uname, pwd_hash, display, f"ep-{uname}"),
            )
        conn.commit()
    finally:
        conn.close()
    yield
    with get_db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM flywheel_records WHERE client_event_id LIKE ?", ("evt_gate_%",))
        cur.execute("DELETE FROM flywheel_recommendation_snapshots WHERE document_id LIKE ?", ("doc_fm_%",))
        cur.execute("DELETE FROM flywheel_source_rows WHERE document_id LIKE ?", ("doc_fm_%",))
        cur.execute("DELETE FROM flywheel_sheets WHERE document_id LIKE ?", ("doc_fm_%",))
        cur.execute("DELETE FROM flywheel_documents WHERE document_id LIKE ?", ("doc_fm_%",))
        cur.execute("DELETE FROM flywheel_rule_suggestions WHERE source_pattern LIKE ?", ("试验阀门%",))
        cur.execute("DELETE FROM flywheel_pattern_aggregates WHERE source_pattern LIKE ?", ("试验阀门%",))


def test_tokenizer_and_abbreviation_extraction():
    """Verify domain tokenizer handles English abbreviations and engineering terminology."""
    vocab = get_domain_vocab()

    tokens1 = domain_tokenize("医用排风机PAF-10", vocab)
    assert "排风机" in tokens1
    assert "PAF-10" in tokens1

    pair1 = extract_alias_candidate("PAF-10", "排风机", vocab)
    assert pair1 is not None
    assert pair1[0] == "PAF-10"

    pair2 = extract_alias_candidate("消音器", "消声器", vocab)
    assert pair2 is not None
    assert pair2[0] == "消音器"
    assert pair2[1] == "消声器"


def test_mining_multi_tier_gating():
    """Verify support count, distinct users, and consistency gating logic over M0 trusted records."""
    uid = uuid.uuid4().hex[:6]
    raw_name = f"试验阀门{uid}"
    raw_norm = clean_text(raw_name)

    alice = get_user_by_username("fm_alice")
    bob = get_user_by_username("fm_bob")
    assert alice is not None and bob is not None

    def _seed_trusted_row(user_obj, suffix: str):
        doc_id = f"doc_fm_{uid}_{suffix}"
        raw_rows = [{
            "row_idx": 1,
            "sheet_index": 0,
            "sheet_name": "暖通",
            "source_row_num": 2,
            "name": raw_name,
            "specification": "DN50",
            "unit": "台",
            "qty": 1.0,
            "loss": 1.0,
            "category": "",
            "brand": "",
            "code": "FM.Q1",
            "professional": "暖通",
        }]
        parsed_rows = [{
            "original_row_idx": 1,
            "original_name": raw_name,
            "original_spec": "DN50",
            "original_qty": 1.0,
            "original_unit": "台",
            "original_loss": 1.0,
            "original_category": "",
            "original_brand": "",
            "original_code": "FM.Q1",
            "professional": "暖通",
            "parsed_spec": {"name": raw_name, "spec": "DN50", "clean_text": "DN50", "model": "", "attrs": {}},
            "top_candidates": [],
            "bindings": [],
            "decision_status": "recommended",
        }]
        persist_upload_document_tree(
            actor_user=user_obj,
            document_id=doc_id,
            upload_task_id=f"up_fm_{uid}_{suffix}",
            work_session_id=f"ws_fm_{uid}_{suffix}",
            original_filename=f"fm_{suffix}.xlsx",
            file_sha256=f"sha_{uid}_{suffix}",
            raw_rows=raw_rows,
            parsed_rows=parsed_rows,
        )
        return parsed_rows[0]

    row_a1 = _seed_trusted_row(alice, "a1")
    row_a2 = _seed_trusted_row(alice, "a2")
    row_b1 = _seed_trusted_row(bob, "b1")

    for idx, (u, row_ctx) in enumerate(((alice, row_a1), (bob, row_b1), (alice, row_a2))):
        record_flywheel_event(
            u,
            {
                "client_event_id": f"evt_gate_{uid}_{idx}",
                "document_id": row_ctx["document_id"],
                "sheet_id": row_ctx["sheet_id"],
                "source_row_id": row_ctx["source_row_id"],
                "recommendation_snapshot_id": row_ctx["recommendation_snapshot_id"],
                "event_seq": 1,
                "action_type": "MANUAL_REPLACE",
                "final_targets": [{"code": "FM.001", "name": "蝶阀", "spec": "DN50"}],
            },
        )

    # Legacy/untrusted bare SQLite row must never enter suggestions
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO flywheel_records (
                client_event_id, session_id, row_idx, discipline, raw_name, raw_name_norm,
                raw_spec, action_type, final_targets_json, user_id, is_processed, superseded, trust_level
            ) VALUES (?, 'default_session', 99, '暖通', ?, ?, 'DN50', 'MANUAL_REPLACE', ?, 'legacy_anonymous', 0, 0, 'legacy_untrusted')
            """,
            (
                f"evt_gate_leg_{uid}",
                raw_name,
                raw_norm,
                json.dumps([{"code": "FM.LEG", "name": "旧假蝶阀", "spec": "DN50"}]),
            ),
        )

    res = miner.run_mining()
    assert res["status"] == "success"

    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT * FROM flywheel_rule_suggestions
            WHERE source_pattern = ? AND target_value = '蝶阀' AND discipline = '暖通'
            """,
            (raw_norm,),
        )
        sug = cur.fetchone()
        assert sug is not None
        assert sug["support_count"] >= 3
        assert sug["distinct_users"] >= 2
        assert sug["consistency"] >= 0.8
        assert sug["status"] == "pending"

        cur.execute(
            """
            SELECT * FROM flywheel_rule_suggestions
            WHERE source_pattern = ? AND target_value = '旧假蝶阀'
            """,
            (raw_norm,),
        )
        assert cur.fetchone() is None
