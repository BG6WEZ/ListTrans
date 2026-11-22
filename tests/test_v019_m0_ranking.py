# -*- coding: utf-8 -*-
"""V019 M0 ranking evidence, caps and rollback contract."""
import json

import pytest

from app import matcher
from app.binding_decisions import evaluate_hard_conflicts
from app.query_preprocessor import prepare_query
from app.typed_fields import build_typed_attrs


def material(code, name, spec, category, attrs, *, model="", unit="个", brand=""):
    return {
        "code": code, "name": name, "specification": spec, "category": category,
        "unit": unit, "brand": brand, "description": "", "semantic_score": 0.0,
        "parsed_params": json.dumps({
            "model": model, "attrs": attrs, "typed_attrs": build_typed_attrs(attrs),
        }, ensure_ascii=False),
    }


@pytest.fixture(params=["dedup", "calibrated"])
def experimental(monkeypatch, request):
    monkeypatch.setenv("LISTTRANS_V019_M0_SCORING", request.param)
    from app.llm_rule_engine import LLMRuleManager
    monkeypatch.setattr(LLMRuleManager, "get_active_rules", lambda: [])
    return request.param


def test_model_and_diameter_each_count_once(experimental):
    attrs = {"型号": "Q41F", "管径": "DN25", "dn_val": 25, "材质": "304"}
    query = prepare_query("不锈钢球阀", "Q41F DN25 不锈钢304", "给排水")
    query.update(attrs=attrs, parsed_spec={"model": "Q41F", "attrs": attrs})
    good = material("a", "不锈钢球阀", "Q41F DN25 不锈钢304", "给排水", attrs, model="Q41F")
    bad = material("b", good["name"], "Q41F DN50 不锈钢304", "给排水",
                   {**attrs, "管径": "DN50", "dn_val": 50}, model="Q41F")
    good_score, good_reasons, good_detail = matcher.score_candidate_with_detail(query, good, 1.0)
    bad_score, _, bad_detail = matcher.score_candidate_with_detail(query, bad, 1.0)

    evidence = good_detail["score_evidence"]
    assert 15 < good_score < 100
    assert "型号完全匹配" in good_reasons
    assert set(evidence["attribute_groups"]["管径"]) == {"管径", "dn_val"}
    assert "型号" not in evidence["attribute_groups"]
    assert evidence["candidate_fields"]["管径"]["origin"] == "candidate_parse"
    assert bad_score <= 15
    assert bad_detail["max_score"] == 0.15
    assert any("冲突" in r for r in bad_detail["score_evidence"]["reasons"])


def test_grade_generic_dn_od_and_unknown_remain_distinct(experimental):
    query = prepare_query("不锈钢管", "DN25 不锈钢304", "给排水", unit="米")
    known = material("a", "不锈钢管", "DN25 不锈钢316L", "给排水",
                     {"管径": "DN25", "材质": "316L"}, unit="米")
    generic = material("b", "不锈钢管", "De25 不锈钢", "给排水",
                       {"外径": 25, "材质": "不锈钢", "壁厚": None}, unit="米")
    score, reasons, detail = matcher.score_candidate_with_detail(query, known, 1.0)
    _, _, generic_detail = matcher.score_candidate_with_detail(query, generic)
    assert score <= 15 and detail["max_score"] == 0.15
    assert any("牌号冲突" in reason for reason in reasons)
    assert evaluate_hard_conflicts(query, known)
    assert not evaluate_hard_conflicts(query, generic)
    ev = generic_detail["score_evidence"]
    assert ev["candidate_fields"]["壁厚"]["status"] == "unknown"
    assert "外径" in ev["candidate_fields"] and "管径" not in ev["candidate_fields"]
    assert not any("牌号冲突" in reason or "口径冲突" in reason for reason in ev["reasons"])


def test_all_initial_conflicts_explained_without_reviving_candidate(experimental):
    query = prepare_query("风机", "AV-01", "暖通", unit="台", brand="甲牌")
    candidate = material("a", "电缆", "AV-01", "强电", {}, unit="米", brand="乙牌")
    score, reasons, detail = matcher.score_candidate_with_detail(query, candidate, 1.0)
    assert score == detail["max_score"] == 0
    assert detail["hard_blocked"]
    assert set(reasons) == {"专业冲突阻断", "品牌冲突阻断", "单位不可换算阻断"}
    evidence = detail["score_evidence"]
    assert evidence["gates"][0]["reasons"] == reasons
    assert evidence["metadata"]["candidate"]["unit"] == "米"


def test_blank_values_do_not_contribute_evidence(experimental):
    attrs = {"材质": ""}
    query = prepare_query("板材", "", "装饰")
    query.update(attrs=attrs, parsed_spec={"attrs": attrs})
    candidate = material("a", "板材", "", "装饰", attrs)
    _, _, detail = matcher.score_candidate_with_detail(query, candidate)
    evidence = detail["score_evidence"]
    assert evidence["query_fields"]["材质"]["status"] == "unknown"
    assert evidence["candidate_fields"]["材质"]["status"] == "unknown"
    assert evidence["components"]["attributes"] == 0


def test_legacy_round_trip_and_explicit_code_are_stable(monkeypatch, experimental):
    query = prepare_query("不锈钢球阀", "Q41F DN25", "给排水")
    candidate = material("a", "不锈钢球阀", "Q41F DN25", "给排水",
                         {"管径": "DN25", "dn_val": 25}, model="Q41F")
    monkeypatch.delenv("LISTTRANS_V019_M0_SCORING", raising=False)
    legacy = matcher.score_candidate_with_detail(query, candidate, 1.0)
    monkeypatch.setenv("LISTTRANS_V019_M0_SCORING", experimental)
    assert matcher.score_candidate_with_detail(query, candidate, 1.0)[0] < legacy[0]
    monkeypatch.setenv("LISTTRANS_V019_M0_SCORING", "legacy")
    assert matcher.score_candidate_with_detail(query, candidate, 1.0) == legacy
    monkeypatch.setenv("LISTTRANS_V019_M0_SCORING", "unknown")
    assert matcher.score_candidate_with_detail(query, candidate, 1.0) == legacy

    exact = {**candidate, "recall_sources": ["exact_code"], "semantic_score": 1.0}
    from app import db_manager
    db_manager.init_db()
    monkeypatch.setattr(matcher, "hybrid_recall", lambda *_args: [dict(exact)])
    baseline = matcher.match_item(query)
    monkeypatch.setenv("LISTTRANS_V019_M0_SCORING", experimental)
    assert matcher.match_item(query) == baseline


def test_deviation_cap_survives_dynamic_and_batch_series_boost(experimental, monkeypatch):
    from app import db_manager
    from app.llm_rule_engine import LLMRuleManager

    db_manager.init_db()
    query = prepare_query("电力电缆", "YJV-4*25", "强电")
    candidate = material("a", "电力电缆", "YJV-3*25", "强电",
                         {"芯数": "3", "截面积": "25"}, model="YJV-3", unit="米")
    monkeypatch.setattr(LLMRuleManager, "get_active_rules", lambda: [{
        "rule_id": "test-boost",
        "scope_level": "universal",
        "scope_target": "全部",
        "parsed_dsl": {"matching_rules": {"score_boost_keywords": {"YJV": 100.0}}},
    }])
    monkeypatch.setattr(matcher, "hybrid_recall", lambda *_args: [{**candidate, "semantic_score": 1.0}])
    top = matcher.match_item(query, batch_confirmed_series=["yjv"])["top_candidates"][0]
    assert top["score"] <= 30.0 and top["max_score"] == 0.3
    assert "芯数冲突拦截" in top["score_evidence"]["reasons"]
    assert any(g["stage"] == "dynamic_matching" for g in top["score_evidence"]["gates"])


def test_upload_series_cannot_restore_zero_cap(monkeypatch):
    from app import server
    from app.vector_index import index_manager

    monkeypatch.setattr(type(index_manager), "is_enabled", property(lambda _self: False))
    monkeypatch.setattr(server, "ENABLE_SERIES_RERUN", False)
    monkeypatch.setattr(server, "load_auto_binding_rules", lambda: [])
    monkeypatch.setattr(server, "extract_params", lambda _text: {"model": "YR-01"})
    first = material("a", "风机", "YR-01", "暖通", {}, unit="台", model="YR-01")
    second = material("b", "风机", "YR-02", "暖通", {}, unit="台", model="YR-02")
    responses = iter([
        {"is_exact": True, "matched_code": "a", "top_candidates": [{**first, "score": 92}]},
        {"is_exact": False, "matched_code": None, "top_candidates": [
            {**second, "score": 0, "max_score": 0, "hard_blocked": False},
        ]},
    ])
    monkeypatch.setattr(server, "match_item", lambda *_args: next(responses))
    rows = server.batch_match_rows([
        {"row_idx": i, "name": "风机", "specification": spec, "professional": "暖通",
         "category": "", "qty": 1, "unit": "台", "loss": 0}
        for i, spec in enumerate(("YR-01", "YR-02"))
    ])
    assert rows[1]["top_candidates"][0]["score"] == 0
    assert rows[1]["decision_status"] == "recommended"
    assert rows[1]["bindings"] == []
