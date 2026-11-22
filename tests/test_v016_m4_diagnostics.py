"""M4 diagnostic contracts without changing production ranking or golden labels."""
import copy
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import db_manager, matcher, sandbox_evaluator, vector_index


@pytest.fixture
def diagnostic_catalog(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE materials(code TEXT, unit TEXT, brand TEXT, description TEXT, parsed_params TEXT)")
    codes = ["NOTREC", "BLOCK", "LOW", "HIT"] + [f"C{i}" for i in range(12)]
    conn.executemany("INSERT INTO materials VALUES (?, '台', '', '', '')", [(c,) for c in codes])
    monkeypatch.setattr(db_manager, "get_thread_db_connection", lambda: conn)
    monkeypatch.setattr(vector_index, "index_manager", SimpleNamespace(is_enabled=False))

    def recall(query, vector=None):
        case = query["name"]
        result = [{"code": f"C{i}", "score": 60 - i} for i in range(12)]
        if case in ("BLOCK", "LOW", "HIT"):
            result.append({"code": case, "score": {"BLOCK": 0, "LOW": 1, "HIT": 99}[case]})
        for c in result:
            c.update(name=c["code"], specification="", category="暖通", unit="台", brand="", description="",
                     semantic_score=0.0, recall_source="fts", recall_sources=["fts", "semantic"])
        return result

    monkeypatch.setattr(matcher, "hybrid_recall", recall)
    monkeypatch.setattr(matcher, "score_candidate_with_detail", lambda q, c, s: (
        c["score"], ["防爆冲突", "动态排除", "完整第三条原因"] if c["code"] == "BLOCK" else ["候选"],
        {"hard_blocked": c["code"] == "BLOCK", "max_score": 0 if c["code"] == "BLOCK" else 1}))
    monkeypatch.setattr(sandbox_evaluator, "_active_material_codes", lambda: set(codes))
    yield
    conn.close()


def item(code):
    return {"id": code, "name": code, "specification": "", "professional": "暖通", "target_codes": [code],
            "label_type": "1to1", "ambiguous": False}


def test_five_stages_recall_denominator_and_full_reasons(diagnostic_catalog):
    cases = ["MISSING", "NOTREC", "BLOCK", "LOW", "HIT"]
    rows, _ = sandbox_evaluator.evaluate([item(c) for c in cases])
    traces = {r["id"]: r["diagnostics"]["targets"][0] for r in rows}
    assert [traces[c]["state"] for c in cases] == [
        "not_in_catalog", "not_recalled", "hard_blocked", "ranked_gt10", "top10_hit"]
    assert traces["BLOCK"]["reasons"] == ["防爆冲突", "动态排除", "完整第三条原因"]
    assert traces["BLOCK"]["max_score"] == 0
    assert traces["LOW"]["full_rank"] == 13 and traces["LOW"]["top10"] is False
    assert traces["HIT"]["recall_sources"] == ["fts", "semantic"]
    m = sandbox_evaluator.metrics(rows)
    assert m["candidate_recall"] == 60 and m["recall@10"] == 20 and m["miss@10"] == 80
    assert m["candidate_recall_numerator"] == 3 and m["candidate_recall_denominator"] == 5
    assert all(m[s + "_count"] == 1 for s in ("not_in_catalog", "not_recalled", "hard_blocked", "ranked_gt10", "top10_hit"))
    assert m["channel_contributions"] == {"fts": 3, "semantic": 3}


def test_real_skips_and_evaluation_failures(diagnostic_catalog, monkeypatch):
    old = matcher.match_item
    def fail(query, *args, **kwargs):
        if query["name"] == "FAIL":
            raise RuntimeError("controlled_guard_failure")
        return old(query, *args, **kwargs)
    monkeypatch.setattr(matcher, "match_item", fail)
    rows, _ = sandbox_evaluator.evaluate([item("HIT"), item("FAIL"), item("EMPTY") | {"target_codes": []}, {"id": "BAD"}])
    m = sandbox_evaluator.metrics(rows)
    assert m["failed_samples_count"] == 1 and m["skipped_samples_count"] == 2
    assert m["candidate_recall"] == 50 and m["diagnostic_failed_targets_count"] == 1
    _, _, selection = sandbox_evaluator._select_sandbox_subsets(
        [item("HIT"), item("LOW") | {"ambiguous": True}, item("EMPTY") | {"target_codes": []}],
        {"rule_type": "ALIAS", "source_pattern": "HIT", "discipline": "暖通"}, with_stats=True)
    assert selection["skipped_samples_count"] == 2
    assert {s["reason"] for s in selection["skipped_samples"]} == {"no_valid_target_codes", "filtered_or_not_selected"}


def test_recall_preserves_all_channels_and_first_source(monkeypatch):
    def search(*args, **kwargs):
        return [{"code": "A", "name": "机组", "specification": "AB-1", "category": "暖通"}]
    monkeypatch.setattr(matcher, "search_materials_keyword", search)
    monkeypatch.setattr(vector_index, "index_manager", SimpleNamespace(
        is_enabled=True, has_current_index=lambda revision: True, encode_single_query=lambda q: [1],
        search=lambda *a, **k: [({"code": "A"}, .8)]))
    query = {"name": "机组", "specification": "AB-1", "professional": "暖通", "parsed_spec": {"model": "AB-1", "attrs": {}}}
    cand = matcher.hybrid_recall(query)[0]
    assert cand["recall_source"] == "model_match"
    assert {"model_match", "fts", "semantic"} <= set(cand["recall_sources"])
    assert cand["semantic_score"] == .8


def test_diagnostic_option_keeps_legacy_outputs_and_score_signature(diagnostic_catalog):
    query = {"name": "HIT", "specification": "", "professional": "暖通", "parsed_spec": {"model": "", "attrs": {}}}
    plain = matcher.match_item(copy.deepcopy(query))
    detailed = matcher.match_item(copy.deepcopy(query), diagnostic_targets=["HIT"])
    assert "diagnostics" not in plain
    assert {key: detailed[key] for key in plain} == plain
    # Existing score_candidate remains the two-value wrapper.
    score, reasons = matcher.score_candidate(query, {"code": "HIT", "score": 99})
    assert score == 99 and reasons == ["候选"]


def test_invalid_optional_fields_and_binding_failure_attribution(monkeypatch):
    assert sandbox_evaluator._sample_skip_reason(item("X") | {"supply_category": 123}) == "invalid_sample_fields"
    assert sandbox_evaluator._sample_skip_reason(item("X") | {"qty": "invalid"}) == "invalid_sample_fields"
    def binding(items, is_guard=False):
        if len(items) > 1 or items[0]["id"] == "FAIL":
            raise ValueError("binding_sample_failure")
        return [{"id": items[0]["id"], "is_guard": is_guard, "decision_status": "recommended"}]
    monkeypatch.setattr(sandbox_evaluator, "_evaluate_binding_pipeline", binding)
    rows = sandbox_evaluator._binding_evaluation_with_errors([item("HIT"), item("FAIL")], is_guard=True)
    assert rows[0]["decision_status"] == "recommended"
    assert rows[1]["id"] == "FAIL" and rows[1]["is_guard"] and "binding_sample_failure" in rows[1]["error"]
