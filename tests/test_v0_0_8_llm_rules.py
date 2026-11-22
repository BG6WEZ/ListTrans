# -*- coding: utf-8 -*-
import json
import os
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
    from app.auth_manager import init_auth_db, get_auth_connection, get_password_hash, get_user_by_username, issue_token_for_user
    from app.llm_rule_engine import init_rule_engine_db

    init_auth_db()
    init_rule_engine_db()
    conn = get_auth_connection()
    pwd_hash = get_password_hash("UnitTest@12345")
    for uname, role in [("test_emp", "employee"), ("test_mgr", "manager"), ("test_adm", "admin")]:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch) VALUES (?, ?, ?, ?, 1, 0, ?)",
            (uname, pwd_hash, uname, role, f"ep-{uname}"),
        )
    conn.commit()
    conn.close()

    user = get_user_by_username("test_emp")
    manager_user = get_user_by_username("test_mgr")
    admin_user = get_user_by_username("test_adm")
    issue_token_for_user(user)
    issue_token_for_user(manager_user)
    issue_token_for_user(admin_user)

    dsl = {
        "rule_name": "单元测试紫铜管清洗规则",
        "cleaning_rules": [{"action": "synonym_replace", "target": "医用脱脂无缝紫铜管", "replacement": "磷酸脱氧无缝紫铜管"}],
        "extraction_rules": [],
        "matching_rules": {},
    }

    rule_data = {
        "rule_id": "TEST-RULE-001",
        "rule_name": "单元测试紫铜管规则",
        "scope_level": "discipline",
        "scope_target": "医用气体",
        "discipline": "医用气体",
        "priority": 5,
        "description": "单元测试说明",
        "parsed_dsl": dsl
    }

    try:
        # 1. Save draft
        res_save = LLMRuleManager.save_rule_draft(rule_data, user)
        assert res_save["success"] is True

        # 2. Submit for audit
        res_sub = LLMRuleManager.submit_rule("TEST-RULE-001", user)
        assert res_sub["success"] is True

        # 3. Manager Audit Approve -> approved_pending_publish (not yet active)
        res_audit = LLMRuleManager.audit_rule("TEST-RULE-001", "approve", "同意上线", manager_user)
        assert res_audit["success"] is True
        assert res_audit["status"] == "approved_pending_publish"
        assert "TEST-RULE-001" not in [r["rule_id"] for r in LLMRuleManager.get_active_rules()]

        # 4. Admin Publish without eval_id blocked (409), then evaluated + published -> active
        res_untested = LLMRuleManager.publish_rule("TEST-RULE-001", admin_user)
        assert res_untested["success"] is False
        assert res_untested["status_code"] == 409

        # Ensure sandbox materials are present so real M2 evaluation can run and hit targets
        import json as _json
        import pandas as _pd
        from app.db_manager import import_materials_from_excel
        sm_path = os.path.join(os.path.dirname(__file__), "fixtures", "sandbox_materials_v1.json")
        with open(sm_path, "r", encoding="utf-8") as _f:
            _mats = _json.load(_f)["materials"]
        _xlsx = os.path.join(os.environ["UPLOAD_DIR"], "llm_rule_eval_mats.xlsx")
        _pd.DataFrame([
            {
                "物料分类": m["category"], "物料编码": m["code"],
                "物料名称": m["name"], "规格/型号": m["spec"],
                "计量单位": "台", "常用品牌": "", "描述": "",
            }
            for m in _mats
        ]).to_excel(_xlsx, index=False, startrow=1)
        import_materials_from_excel(_xlsx, mode="replace", filename="llm_rule_eval_mats.xlsx")

        ev_start = LLMRuleManager.start_rule_evaluation("TEST-RULE-001", admin_user)
        assert ev_start["success"] is True, ev_start
        from app.flywheel_sandbox import get_eval_task_status
        ev_id = ev_start["eval_id"]
        for _ in range(240):
            import time as _t
            _t.sleep(0.5)
            st = get_eval_task_status(ev_id)
            if st and st["status"] in ("completed", "failed"):
                assert st["status"] == "completed" and st["safety_passed"] is True, st
                break

        assert st and st["status"] == "completed" and st["safety_passed"] is True, st

        res_pub = LLMRuleManager.publish_rule(
            "TEST-RULE-001",
            admin_user,
            eval_id=ev_id,
            neutral_reason="单元测试验证稳态规则通过" if st.get("benefit") == "neutral" else None,
        )
        assert res_pub["success"] is True, res_pub
        assert res_pub["status"] == "published"

        # 5. Check active rules
        active_rules = LLMRuleManager.get_active_rules()
        rule_ids = [r["rule_id"] for r in active_rules]
        assert "TEST-RULE-001" in rule_ids

        # 6. Check stats
        stats = LLMRuleManager.get_rule_stats()
        assert stats["published_count"] >= 1
        assert stats["approved_count"] >= 1
    finally:
        from app.llm_rule_engine import get_db_conn
        c_clean = get_db_conn()
        try:
            c_clean.execute("DELETE FROM llm_rules WHERE rule_id = 'TEST-RULE-001'")
            c_clean.execute("DELETE FROM rule_audit_logs WHERE rule_id = 'TEST-RULE-001'")
            c_clean.commit()
        finally:
            c_clean.close()
        LLMRuleManager.clear_active_cache()
