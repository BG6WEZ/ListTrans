# -*- coding: utf-8 -*-
import pytest
from app.llm_rule_engine import RuleHitStatsTracker, init_rule_engine_db, get_db_conn

def test_rule_hit_stats_tracker_no_deadlock():
    """Verify that calling record_hit >= 10 times does not trigger a thread deadlock and flushes correctly."""
    # Ensure tables exist
    init_rule_engine_db()
    
    conn = get_db_conn()
    cursor = conn.cursor()
    # Insert a test rule to receive hit counts
    cursor.execute("DELETE FROM llm_rules WHERE rule_id = 'test_deadlock_rule'")
    cursor.execute("""
        INSERT INTO llm_rules (
            rule_id, rule_name, scope_level, rule_json, author_id, author_name, status, hit_count
        ) VALUES (
            'test_deadlock_rule', '测试死锁规则', 'universal', '{}', 1, 'tester', 'approved', 0
        )
    """)
    conn.commit()
    conn.close()
    
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
