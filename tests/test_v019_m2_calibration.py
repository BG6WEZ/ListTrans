"""Independent ranking evidence and server-owned V019-M2 binding authority."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

from app import acceptance_calibration as cal, server
from app.auth_manager import get_user_by_username, init_auth_db, issue_token_for_user
from app.db_manager import get_db_connection, init_db
from app.query_preprocessor import prepare_query
from test_binding_decisions import M2Base, _ensure_user, _seed_material


POLICY = cal.Policy((0.0,) * len(cal.FEATURES), 6, 0.99, 2, 25, {})
CODE = "M2-CAL-A"
NAME = "校准专用样件"
SPEC = "QAL-412X 配电箱 400×300×200"


def material(code=CODE, **values):
    return {"code": code, "name": NAME, "specification": SPEC, "unit": "个", "category": "强电",
            "brand": "", "description": "", "parsed_params": None, "score": 96.0,
            "hard_blocked": False, "max_score": 1.0, **values}


def original(**values):
    return {"original_row_idx": 0, "original_name": NAME, "original_spec": SPEC, "original_qty": 2.0,
            "original_unit": "个", "original_category": "强电", "original_brand": "", "original_loss": 0.0,
            "original_code": "M2-test", "professional": "强电",
            "matched_code": CODE, "matched_name": NAME, "matched_spec": SPEC, "matched_unit": "个",
            "matched_category": "强电", "matched_brand": "", **values}


def rank(ordered, policy=POLICY, uncapped=True):
    return {"top_candidates": ordered[:2], "is_exact": True, "matched_code": CODE,
            "_acceptance_rank": cal.ranking_evidence(ordered, policy.equivalences, uncapped)}


def assess(row, match, policy=POLICY):
    query = prepare_query(row["original_name"], row["original_spec"], row["professional"],
                          category=row["original_category"], unit=row["original_unit"])
    return cal.assess_match(row, query, match, policy)


def test_equivalents_only_skip_unchanged_full_identity_and_consider_later_rivals():
    same = material("M2-CAL-EQ")
    policy = replace(POLICY, equivalences={same["code"]: {CODE, same["code"]}})
    candidates = [material(), same, material("OTHER", score=80, specification="QAL-913Y")]
    evidence = rank(candidates, policy)
    result = assess(original(), evidence, policy)
    assert result["outcome"] == "accepted", (result["missing_fields"], result["different_fields"], result["reason_codes"])
    assert result["equivalent_codes"] == [same["code"]]
    assert result["distinct_margin"] == 16
    assert result["ranking_score"] == 96
    assert result["confidence"] != result["ranking_score"] / 100
    different = {**same, "specification": "QAL-300X"}
    result = assess(original(), rank([candidates[0], different, candidates[2]], policy), policy)
    assert result["outcome"] == "review"
    assert result["competitor_code"] == same["code"] and result["distinct_margin"] == 0
    assert "ambiguous_distinct_candidate" in result["reason_codes"]


def test_no_answer_and_weak_evidence_are_explicit_without_legacy_exact_authority():
    assert assess(original(), rank([]))["reason_codes"] == ["no_answer"]
    low = rank([material(score=10), material("OTHER", score=1)])
    result = assess(original(), low)
    assert result["outcome"] == "rejected", result["missing_fields"]
    assert "weak_evidence" in result["reason_codes"]
    capped = rank([material(), material("OTHER", score=80)], uncapped=False)
    result = assess(original(), capped)
    assert result["outcome"] == "review"
    assert "score_cap_or_stale_rank" in result["reason_codes"]
    result = assess(original(), rank([material(hard_blocked=True), material("OTHER", score=80)]))
    assert result["outcome"] == "rejected"


@pytest.mark.parametrize(("row", "top", "missing"), [
    (original(professional="其它"), material(), "专业"),
    (original(original_spec="unknown"), material(), "核心规格"),
    (original(original_unit=""), material(), "单位倍率"),
    (original(original_name="不锈钢管", original_spec="DN100 不锈钢", professional="给排水",
              original_category="给排水", original_unit="米"),
     material(name="不锈钢管", category="给排水", specification="DN100 316L", unit="米"), "材质牌号"),
    (original(original_name="给水管", original_spec="给水管", professional="给排水",
              original_category="给排水", original_unit="米"),
     material(name="给水管", category="给排水", specification="DN100", unit="米"), "管径"),
    (original(original_name="轴流风机", original_spec="风量 3000m³/h", professional="暖通",
              original_category="暖通", original_unit="台"),
     material(name="轴流风机", category="暖通", specification="风量 3000m³/h", unit="台"), "防爆"),
    (original(original_name="风机盘管", original_spec="FP-68", professional="暖通",
              original_category="暖通", original_unit="台"),
     material(name="风机盘管", category="暖通", specification="FP-68", unit="台"), "提升泵"),
    (original(original_name="轴流风机", original_spec="风量 3000m³/h 防爆等级：未知", professional="暖通",
              original_category="暖通", original_unit="台"),
     material(name="轴流风机", category="暖通", specification="风量 3000m³/h 防爆", unit="台"), "防爆"),
])
def test_unknown_critical_field_prevents_auto_accept(row, top, missing):
    result = assess(row, rank([top, material("OTHER", score=50)]))
    assert result["outcome"] == "needs_parameters", result
    assert result["decision_status"] == "recommended"
    assert missing in result["missing_fields"], result


def test_off_invalid_or_missing_policy_never_grants_authority(monkeypatch, tmp_path):
    for setting in ("OFF", "unknown", "1", "", "off"):
        monkeypatch.setenv("LISTTRANS_V019_M2_CALIBRATION", setting)
        assert cal.active_policy() is None
    monkeypatch.delenv("LISTTRANS_V019_M2_CALIBRATION")
    assert cal.active_policy() is None
    monkeypatch.setenv("LISTTRANS_V019_M2_CALIBRATION", "on")
    assert cal.active_policy() is not None
    with monkeypatch.context() as local:
        local.setattr(cal, "ASSETS", tmp_path)
        cal.load_policy.cache_clear()
        assert cal.active_policy() is None
    cal.load_policy.cache_clear()


@pytest.fixture
def live_http(monkeypatch):
    init_db()
    init_auth_db()
    _ensure_user("calibration-fixture", "Test-fixture-2#", "employee")
    headers = {"Authorization": "Bearer " + issue_token_for_user(get_user_by_username("calibration-fixture"))}
    _seed_material(CODE, NAME, SPEC, "个", "强电")
    from app.binding_decisions import fetch_live_material_map
    fixture = fetch_live_material_map([CODE])[CODE]
    baseline = rank([{**fixture, "score": 96.0, "hard_blocked": False, "max_score": 1.0},
                     material("OTHER", score=61, specification="QAL-913Y")])
    # Test the positive HTTP authority branch with deterministic rank evidence.
    # Catalog validity, current hard conflicts, and decision persistence stay real.
    monkeypatch.setattr(server, "match_item", lambda *args, **kwargs: deepcopy(baseline))
    monkeypatch.setattr(server, "load_auto_binding_rules", lambda: [])
    monkeypatch.setattr(server, "acceptance_policy", lambda: POLICY)
    try:
        yield TestClient(server.app), headers
    finally:
        M2Base._clear_seeded_materials()


def test_matcher_export_and_restore_recompute_and_use_live_binding(live_http):
    client, headers = live_http
    scope = "calibration-positive.xlsx"
    payload = {"name": NAME, "text": SPEC, "professional": "强电", "category": "强电", "unit": "个", "qty": 2}
    response = client.post("/api/match", headers=headers, json=payload)
    assert response.status_code == 200
    accepted = response.json()
    assessment = accepted["acceptance_calibration"]
    assert accepted["decision_status"] == "auto_accepted", (assessment["missing_fields"], assessment["different_fields"])
    forged = client.post("/api/match", headers=headers, json={**payload, "parsed_spec": {"attrs": {}}}).json()
    assert forged["bindings"] == [] and forged["matched_code"] == ""
    assert "untrusted_parameter_override" in forged["acceptance_calibration"]["reason_codes"]
    missing_professional = {key: value for key, value in payload.items() if key != "professional"}
    incomplete = client.post("/api/match", headers=headers, json=missing_professional).json()
    assert "专业" in incomplete["acceptance_calibration"]["missing_fields"]
    row = original(bindings=[{**accepted["bindings"][0], "name": "客户端名称"}],
                   decision_status="recommended", is_exact=False, acceptance_calibration={"confidence": 0})

    def request(path, value, scope_filename=scope):
        return client.post(path, headers=headers, json={"items": [value], "scope_filename": scope_filename})

    resp = request("/api/export", row)
    assert resp.status_code == 200
    wb = load_workbook(BytesIO(resp.content), read_only=True)
    try:
        assert any(NAME in cells for cells in wb.active.values)
        assert not any("客户端名称" in cells for cells in wb.active.values)
    finally:
        wb.close()
    restored = request("/api/session/annotate", row).json()["items"][0]
    assert restored["decision_status"] == "auto_accepted" and restored["bindings"][0]["name"] == NAME
    assert restored["acceptance_calibration"]["confidence"] > 0.99

    forged = {**row, "is_exact": True, "decision_status": "auto_accepted",
              "acceptance_calibration": {"confidence": 1, "outcome": "accepted"}}
    for changes in ({"qty": 3.0}, {"code": "INVALID"}, {"unit": "米"}, {"qty_ratio": 0.5}):
        bad = {**forged, "bindings": [{**row["bindings"][0], **changes}]}
        assert request("/api/export", bad).status_code == 422
        result = request("/api/session/annotate", bad).json()["items"][0]
        assert result["decision_status"] == "recommended" and "acceptance_calibration" not in result
    assert request("/api/export", {**forged, "bindings": []}).status_code == 422
    unspecified = {key: value for key, value in row.items() if key != "professional"}
    assert request("/api/export", unspecified).status_code == 422
    assert request("/api/session/annotate", unspecified).json()["items"][0]["decision_status"] == "recommended"

    rejected = client.post("/api/binding/reject", headers=headers, json={**row, "scope_filename": scope})
    assert rejected.status_code == 200
    assert request("/api/export", forged).status_code == 422
    assert request("/api/session/annotate", forged).json()["items"][0]["decision_status"] == "rejected"

    # Remove the isolated material and exercise independent freshness.
    with get_db_connection() as conn:
        conn.execute("DELETE FROM materials WHERE code=?", (CODE,))
    assert request("/api/export", forged, "new.xlsx").status_code == 422
    resp = request("/api/session/annotate", forged, "new.xlsx")
    assert resp.json()["items"][0]["decision_status"] == "recommended"


def test_rule_gate_cannot_lose_correct_prior_proposal_when_calibration_abstains(live_http, monkeypatch):
    from app.flywheel_sandbox import _compare_binding_pipeline_rows
    from app.sandbox_evaluator import _evaluate_binding_pipeline

    _seed_material("WRONG", NAME, SPEC, "个", "强电")
    items = [{"id": "fixture", "name": NAME, "specification": SPEC, "professional": "强电",
              "supply_category": "强电", "unit": "个", "qty": 2.0, "target_codes": [CODE]}]
    baseline = {**original(), "matched_code": "", "top_candidates": [material()],
                "decision_status": "recommended", "decision_source": "matcher", "bindings": [],
                "acceptance_calibration": {"outcome": "review"}}
    monkeypatch.setattr(server, "batch_match_rows", lambda rows: [baseline])
    before = _evaluate_binding_pipeline(items)
    assert before[0]["bindings"] == [] and not before[0]["codes_match_gt"]
    assert before[0]["prior_matcher_proposal"]["codes_match_gt"] and not before[0]["export_eligible"]

    rule = {**original(), "matched_code": "WRONG", "decision_status": "recommended",
            "decision_source": "auto_binding_rule",
            "bindings": [{**material("WRONG"), "qty": 2.0, "qty_ratio": 1.0}]}
    monkeypatch.setattr(server, "batch_match_rows", lambda rows: [rule])
    after = _evaluate_binding_pipeline(items)
    assert after[0]["rule_applied"] and not after[0]["invalid_codes"] and not after[0]["conflicts"]
    result = _compare_binding_pipeline_rows(before, after)
    assert not result["passed"] and result["degraded_count"] == 1
    assert result["violations"] == [{"id": "fixture", "type": "binding_target_code_degraded",
                                     "base_code": CODE, "cand_code": "WRONG"}]
    correct = {**after[0], "codes_match_gt": True, "matched_code": CODE}
    retained = _compare_binding_pipeline_rows(before, [correct])
    assert retained["passed"] and retained["improved_count"] == 0
