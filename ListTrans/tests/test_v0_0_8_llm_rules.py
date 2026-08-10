# -*- coding: utf-8 -*-
import json
import pytest
from app.llm_rule_engine import (
    DynamicRuleInterpreter, LLMRuleManager, LLMProviderManager, RuleHitStatsTracker
)

def test_dynamic_rule_interpreter_cleaning():
    sample_text = "高效变频离心式水冷机组 1200kW"
    rules_list = [
        {
            "action": "regex_replace",
            "pattern": "高效(变频)?离心式",
            "replacement": "离心式",
            "description": "规范化离心式前缀"
        }
    ]
    cleaned, logs = DynamicRuleInterpreter.apply_cleaning_rules(sample_text, rules_list)
    assert "离心式水冷机组" in cleaned
    assert len(logs) == 1

def test_dynamic_rule_interpreter_extraction():
    sample_text = "离心式水冷机组 1200kW R134a"
    extraction_rules = [
        {
            "key": "cooling_kw",
            "pattern": r"(\d+(?:\.\d+)?)\s*(kW|千瓦)",
            "unit_convert": {"kW": 1.0}
        },
        {
            "key": "refrigerant",
            "pattern": r"(R134a|R410A)",
            "default_value": "R134a"
        }
    ]
    extracted = DynamicRuleInterpreter.apply_extraction_rules(sample_text, extraction_rules)
    assert extracted.get("cooling_kw") == 1200.0
    assert extracted.get("refrigerant") == "R134a"

def test_dynamic_rule_interpreter_matching_filters():
    matching_rules = {
        "must_contain": ["离心"],
        "must_not_contain": ["螺杆", "风冷"],
        "score_boost_keywords": {"磁悬浮": 15.0}
    }
    
    # Excluded case
    is_ex, score, reasons = DynamicRuleInterpreter.evaluate_matching_filters("离心机", "螺杆式水冷机组 500kW", matching_rules)
    assert is_ex is True
    assert score == -999.0
    
    # Boost case
    is_ex, score, reasons = DynamicRuleInterpreter.evaluate_matching_filters("离心机", "磁悬浮离心水冷机组 1000kW", matching_rules)
    assert is_ex is False
    assert score == 15.0

def test_llm_rule_manager_draft_and_audit():
    user = {"id": 1, "username": "test_emp", "role": "employee"}
    manager_user = {"id": 2, "username": "test_mgr", "role": "manager"}

    dsl = {
        "rule_name": "单元测试风机盘管规则",
        "cleaning_rules": [{"action": "synonym_replace", "target": "FCU", "replacement": "风机盘管"}],
        "extraction_rules": [],
        "matching_rules": {"must_contain": ["风机盘管"]}
    }

    rule_data = {
        "rule_id": "TEST-RULE-001",
        "rule_name": "单元测试风机盘管规则",
        "scope_level": "category",
        "scope_target": "风机盘管",
        "discipline": "暖通",
        "priority": 5,
        "description": "单元测试说明",
        "parsed_dsl": dsl
    }

    # 1. Save draft
    res_save = LLMRuleManager.save_rule_draft(rule_data, user)
    assert res_save["success"] is True

    # 2. Submit for audit
    res_sub = LLMRuleManager.submit_rule("TEST-RULE-001", user)
    assert res_sub["success"] is True

    # 3. Manager Audit Approve
    res_audit = LLMRuleManager.audit_rule("TEST-RULE-001", "approve", "同意上线", manager_user)
    assert res_audit["success"] is True

    # 4. Check active rules
    active_rules = LLMRuleManager.get_active_rules()
    rule_ids = [r["rule_id"] for r in active_rules]
    assert "TEST-RULE-001" in rule_ids

    # 5. Check stats
    stats = LLMRuleManager.get_rule_stats()
    assert stats["approved_count"] >= 1
