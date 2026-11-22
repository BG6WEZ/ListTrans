# -*- coding: utf-8 -*-
import pytest
from app.llm_rule_engine import RuleHitStatsTracker, init_rule_engine_db, get_db_conn

def test_rule_hit_stats_tracker_no_deadlock():
    """Verify that calling record_hit >= 10 times does not trigger a thread deadlock and flushes correctly."""
    from app.llm_rule_engine import compute_rule_content_digest, LLMRuleManager

    # Ensure tables exist
    init_rule_engine_db()
    digest = compute_rule_content_digest("universal", "", "", 10, "{}")

    conn = get_db_conn()
    cursor = conn.cursor()
    # Insert a test rule to receive hit counts
    cursor.execute("DELETE FROM llm_rules WHERE rule_id = 'test_deadlock_rule'")
    cursor.execute(
        """
        INSERT INTO llm_rules (
            rule_id, rule_name, scope_level, scope_target, discipline, priority,
            rule_json, author_id, author_name, approver_id, approver_name,
            publisher_id, publisher_name, revision, content_digest,
            approved_revision, approved_digest, status, hit_count
        ) VALUES (
            'test_deadlock_rule', '测试死锁规则', 'universal', '', '', 10,
            '{}', 1, 'tester', 2, 'mgr',
            3, 'adm', 1, ?, 1, ?, 'published', 0
        )
        """,
        (digest, digest),
    )
    conn.commit()
    conn.close()

    try:
        tracker = RuleHitStatsTracker()

        # Record 25 hits across multiple iterations to trigger multiple flush calls
        for _ in range(25):
            tracker.record_hit("test_deadlock_rule")

        # Explicitly flush any remainder
        tracker.flush()

        # Query database to confirm hit_count was incremented
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT hit_count FROM llm_rules WHERE rule_id = 'test_deadlock_rule'")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row["hit_count"] >= 25
    finally:
        conn = get_db_conn()
        try:
            conn.execute("DELETE FROM llm_rules WHERE rule_id = 'test_deadlock_rule'")
            conn.commit()
        finally:
            conn.close()
        LLMRuleManager.clear_active_cache()
