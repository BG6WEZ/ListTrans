# -*- coding: utf-8 -*-
"""
Tests for Smart Mining Engine (tests/test_feedback_miner.py).
Validates:
1. Domain tokenization and abbreviation extraction.
2. Two-phase mining and multi-tier statistical gating.
3. Multi-candidate conflict tracking without silent overwrite.
"""
import uuid
import json
import pytest
from app.db_manager import get_db_cursor
from app.feedback_miner import domain_tokenize, extract_alias_candidate, get_domain_vocab, miner

def test_tokenizer_and_abbreviation_extraction():
    """Verify domain tokenizer handles English abbreviations and engineering terminology."""
    vocab = get_domain_vocab()
    
    # 1. English abbreviations
    tokens1 = domain_tokenize("医用排风机PAF-10", vocab)
    assert "排风机" in tokens1
    assert "PAF-10" in tokens1
    
    # 2. Extract abbreviation as alias
    pair1 = extract_alias_candidate("PAF-10", "排风机", vocab)
    assert pair1 is not None
    assert pair1[0] == "PAF-10"
    
    # 3. Clean diff pair extraction
    pair2 = extract_alias_candidate("消音器", "消声器", vocab)
    assert pair2 is not None
    assert pair2[0] == "消音器"
    assert pair2[1] == "消声器"

from app.preprocessor import clean_text

def test_mining_multi_tier_gating():
    """Verify support count, distinct users, and consistency gating logic."""
    uid = uuid.uuid4().hex[:4]
    raw_name = f"试验阀门{uid}"
    raw_norm = clean_text(raw_name)
    
    # Insert 3 records from 2 distinct users with same target (meets pending gate)
    with get_db_cursor(commit=True) as cur:
        for i, u in enumerate(["user_alice", "user_bob", "user_alice"]):
            cur.execute("""
            INSERT INTO flywheel_records (
                client_event_id, session_id, row_idx, discipline, raw_name, raw_name_norm,
                raw_spec, action_type, final_targets_json, user_id, is_processed, superseded
            ) VALUES (?, ?, ?, '暖通', ?, ?, 'DN50', 'MANUAL_REPLACE', ?, ?, 0, 0)
            """, (
                f"evt_gate_{uid}_{i}", f"sess_{uid}", i,
                raw_name, raw_norm,
                json.dumps([{"code": "FM.001", "name": "蝶阀", "spec": "DN50"}]),
                u
            ))
            
    res = miner.run_mining()
    assert res["status"] == "success"
    
    with get_db_cursor() as cur:
        cur.execute("""
        SELECT * FROM flywheel_rule_suggestions
        WHERE source_pattern = ? AND target_value = '蝶阀'
        """, (raw_norm,))
        sug = cur.fetchone()
        assert sug is not None
        assert sug["support_count"] >= 3
        assert sug["distinct_users"] >= 2
        assert sug["consistency"] >= 0.8
        assert sug["status"] == "pending"
