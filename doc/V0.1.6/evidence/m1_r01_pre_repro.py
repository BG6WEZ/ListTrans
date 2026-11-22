# -*- coding: utf-8 -*-
"""V016-M1 R01 改前隔离基线复现脚本（仅读业务配置快照，全部写入 tests.conftest 隔离临时根）。"""
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

os.environ["LLM_ENABLED"] = "false"
os.environ["TESTING"] = "1"

import tests.conftest as cf

checks = cf.run_guardrail_checks()
assert not checks["gaps"], checks

from app.db_manager import get_db_cursor, init_db
from app.flywheel_db import init_flywheel_tables
from app.feedback_miner import miner
from app.llm_rule_engine import LLMRuleManager, DynamicRuleInterpreter
from app.normalizer import clean_text_standard, dynamic_rule_matches_query
from app.query_preprocessor import extract_query_params, prepare_query

init_db()
init_flywheel_tables()

report = {"isolation_root": str(cf._ISOLATION_ROOT), "f06": {}, "f27": {}}

# 1. F06 pre-fix reproduction
uid = uuid.uuid4().hex[:6]
with get_db_cursor(commit=True) as cur:
    cur.execute(
        """
        INSERT INTO flywheel_records (
            client_event_id, session_id, row_idx, discipline,
            raw_name, raw_name_norm, raw_spec, action_type, final_targets_json,
            extracted_params, user_id, is_processed, superseded, trust_level
        ) VALUES
        (?, 's1', 1, '暖通', '复现蝶阀', '复现蝶阀', 'DN50', 'PARAM_ADJUST', '[]', '{"dn":50}', 'u1', 0, 0, 'legacy_untrusted'),
        (?, 's1', 2, '暖通', '复现蝶阀', '复现蝶阀', 'DN80', 'PARAM_ADJUST', '[]', '{"dn":80}', 'u2', 0, 0, 'legacy_untrusted')
        """,
        (f"pre_pa_1_{uid}", f"pre_pa_2_{uid}"),
    )

try:
    miner.run_mining()
    report["f06"]["param_adjust_error"] = None
except Exception as exc:
    report["f06"]["param_adjust_error"] = f"{type(exc).__name__}: {exc}"

with get_db_cursor() as cur:
    rows = cur.execute(
        "SELECT is_processed FROM flywheel_records WHERE client_event_id IN (?, ?) ORDER BY id ASC",
        (f"pre_pa_1_{uid}", f"pre_pa_2_{uid}"),
    ).fetchall()
    report["f06"]["is_processed_after_failure"] = [r["is_processed"] for r in rows]

# Legacy untrusted alias entering miner + hardcoded discipline='通用'
with get_db_cursor(commit=True) as cur:
    cur.execute("DELETE FROM flywheel_records WHERE client_event_id LIKE 'pre_%'")
    cur.execute(
        """
        INSERT INTO flywheel_records (
            client_event_id, session_id, row_idx, discipline,
            raw_name, raw_name_norm, raw_spec, action_type, final_targets_json,
            user_id, is_processed, superseded, trust_level
        ) VALUES
        (?, 'default_session', 1, '强电', '复现旧别名', '复现旧别名', '', 'MANUAL_REPLACE', '[{"code":"E1","name":"复现标准设备"}]', 'legacy_anonymous', 0, 0, 'legacy_untrusted'),
        (?, 'default_session', 2, '强电', '复现旧别名', '复现旧别名', '', 'MANUAL_REPLACE', '[{"code":"E1","name":"复现标准设备"}]', 'legacy_anonymous', 0, 0, 'legacy_untrusted')
        """,
        (f"pre_al_1_{uid}", f"pre_al_2_{uid}"),
    )

res_legacy = miner.run_mining()
with get_db_cursor() as cur:
    sug = cur.execute(
        "SELECT suggestion_type, source_pattern, target_value, discipline, support_count, status "
        "FROM flywheel_rule_suggestions WHERE source_pattern = '复现旧别名'"
    ).fetchone()
    report["f06"]["legacy_untrusted_mined"] = dict(sug) if sug else None
    cur.execute("DELETE FROM flywheel_records WHERE client_event_id LIKE 'pre_%'")
    cur.execute("DELETE FROM flywheel_rule_suggestions WHERE source_pattern = '复现旧别名'")

# 2. F27 pre-fix audit (already fixed vs remaining gaps)
prev_cache = LLMRuleManager._active_rules_cache
try:
    plumb_rule = {
        "rule_id": "pre-plumb",
        "scope_level": "discipline",
        "scope_target": "给排水",
        "parsed_dsl": {
            "cleaning_rules": [{"action": "remove_token", "token": "CLEANME"}],
            "extraction_rules": [{"key": "plumb_tag", "pattern": r"PTAG-(\d+)"}],
            "matching_rules": {"must_not_contain": ["BAD"]},
        },
    }
    cat_rule = {
        "rule_id": "pre-cat-fcu",
        "scope_level": "category",
        "scope_target": "风机盘管",
        "parsed_dsl": {
            "cleaning_rules": [{"action": "remove_token", "token": "FCUCLEAN"}],
            "extraction_rules": [{"key": "fcu_tag", "pattern": r"FTAG-(\d+)"}],
        },
    }
    LLMRuleManager._active_rules_cache = [plumb_rule]
    # 2a. Missing context leaks plumbing cleaning rule
    report["f27"]["missing_ctx_clean_leaked_plumb_rule"] = (
        "cleanme" not in clean_text_standard("Q=10 CLEANME").lower()
    )
    # 2b. Missing professional + material category='给排水' leaks into discipline rule
    pq_missing_prof = prepare_query("给水泵", "PTAG-9", "", category="给排水")
    report["f27"]["missing_prof_with_category_leaked_extraction"] = (
        "plumb_tag" in (pq_missing_prof.get("parsed_spec") or {}).get("attrs", {})
    )
    report["f27"]["missing_prof_with_category_leaked_matching"] = dynamic_rule_matches_query(
        plumb_rule, pq_missing_prof
    )
    # 2c. Category-scoped cleaning rule skipped during extract_query_params while extraction runs
    LLMRuleManager._active_rules_cache = [cat_rule]
    pq_cat = extract_query_params("风机盘管 FCUCLEAN", "FTAG-7", "暖通")
    report["f27"]["category_cleaning_skipped_when_prof_passed_as_cat"] = (
        "fcuclean" in pq_cat["name"].lower()
        and pq_cat["attrs"].get("fcu_tag") == 7.0
    )
    # 2d. Invalid regex silently swallowed in apply_cleaning_rules
    try:
        DynamicRuleInterpreter.apply_cleaning_rules("abc", [{"action": "regex_replace", "pattern": "[unclosed", "replacement": "x"}])
        report["f27"]["invalid_regex_swallowed_in_cleaning"] = True
    except Exception as exc:
        report["f27"]["invalid_regex_swallowed_in_cleaning"] = f"raised: {exc}"
finally:
    LLMRuleManager._active_rules_cache = prev_cache

out_path = ROOT / "doc/V0.1.6/evidence/m1_r01_pre_reproduction.json"
out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
