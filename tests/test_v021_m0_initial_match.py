from pathlib import Path

import pytest


@pytest.fixture
def candidate():
    return {"code": "X", "name": "样例", "unit": "个", "score": 75.0}


def classify(candidate, *, score=75, value=0.65, stale=False, source="matcher",
             status="recommended", live=True, safety=False, query_unit="个"):
    from math import log

    from app.acceptance_calibration import Policy
    from app.match_assessment import assess_initial_match

    top = dict(candidate, score=score)
    match = {"top_candidates": [top], "_initial_rank_stale": stale, "_initial_match_rank": {
        "top_code": top["code"], "ranking_score": score, "margin": 10.0, "uncapped": True,
    }}
    policy = Policy((0.0,) * 8, log(value / (1 - value)), None, 10.0, 0.0, {})
    row = {"original_name": ("防爆未知风机" if safety else top["name"]), "original_unit": query_unit}
    result = {"bindings": [top], "decision_status": status}
    return assess_initial_match(row, {}, match, result, source, policy, {top["code"]: top} if live else {}, 4)


@pytest.mark.parametrize(("score", "value", "tier"), [
    (59.9, .65, "C"), (60, .65, "B"), (74.9, .65, "B"), (75, .649, "B"), (75, .65, "A"),
])
def test_inclusive_policy_boundaries(candidate, score, value, tier):
    result = classify(candidate, score=score, value=value)
    assert result["tier"] == tier
    assert result["actual_prefilled"]


def test_stale_rank_and_safety_are_independent_of_legacy_binding(candidate):
    stale = classify(candidate, stale=True)
    assert stale["tier"] == "B" and stale["priority_review_unavailable"]
    assert stale["ranking_evidence_status"] == "stale"
    safety = classify(candidate, safety=True)
    assert safety["tier"] == "C" and safety["subtype"] == "safety_missing"
    assert safety["effective_bindings"] == []
    assert any(row["field"] == "防爆" and row["safety_critical_unknown"] for row in safety["field_comparisons"])


def test_unit_mismatch_and_unverified_bindings(candidate):
    mismatch = classify(dict(candidate, unit="m"), query_unit="km")
    assert mismatch["tier"] == "D" and not mismatch["effective_bindings"]
    assert classify(candidate, live=False)["reason_codes"] == ["invalid_code"]
    for source in ("rule", "bom"):
        valid = classify(candidate, source=source, status="auto_accepted", score=0)
        assert valid["tier"] == "A" and valid["effective_bindings"]
        assert valid["ranking_score"] is None
        assert not classify(candidate, source=source)["actual_prefilled"]


def test_explicit_power_and_model_missing_from_candidate():
    from app.acceptance_calibration import _physical_evidence

    row = {"original_name": "样例设备", "original_unit": "个"}
    evidence = _physical_evidence(row, {"parsed_spec": {"attrs": {"功率": "40-50w"}, "model": "Z-A12"}},
                                  {"name": "样例设备", "unit": "个"})
    fields = {field["field"]: field for field in evidence["comparisons"]}
    for key in ("功率", "型号"):
        assert fields[key]["missing_side"] == "candidate"
        assert fields[key]["requested_unverified"]


def test_unstructured_lighting_power_range_preserves_frozen_features():
    from app.acceptance_calibration import _physical_evidence

    row = {"original_name": "医用LED气密净化灯", "original_spec": "1200*300,40-50w",
           "original_unit": "套", "professional": "强电"}
    query = {"parsed_spec": {"domain": "lighting", "attrs": {"尺寸": "1200*300"}}}
    candidate = {"name": row["original_name"], "unit": "套",
                 "specification": "1200*300*10,吸顶安装,48W", "category": "格栅灯（荧光灯盘）"}
    result = _physical_evidence(row, query, candidate)
    power = next(field for field in result["comparisons"] if field["field"] == "功率")
    assert "功率" in result["missing"]
    assert power["query_value"] == "40-50w"
    assert power["missing_side"] == "candidate" and power["requested_unverified"]


def test_full_ranking_finds_first_non_equivalent_after_top_ten():
    from app.acceptance_calibration import ranking_evidence

    candidates = [{"code": str(i), "score": 100 - i, "name": "同规格"} for i in range(11)]
    equivalents = {str(i): {"0"} for i in range(1, 10)}
    assert ranking_evidence(candidates[:10], equivalents, True)["margin"] is None
    evidence = ranking_evidence(candidates, equivalents, True)
    assert evidence["competitor_code"] == "10" and evidence["margin"] == 10


def test_batch_series_score_change_marks_initial_rank_stale(monkeypatch):
    from app import server
    from app.vector_index import index_manager

    monkeypatch.setattr(type(index_manager), "is_enabled", property(lambda _self: False))
    monkeypatch.setattr(server, "ENABLE_SERIES_RERUN", False)
    monkeypatch.setattr(server, "load_auto_binding_rules", lambda: [])
    monkeypatch.setattr(server, "acceptance_policy", lambda: None)
    monkeypatch.setattr(server, "extract_params", lambda _text: {"model": "YX-01"})
    first = {"code": "A", "name": "电缆桥架", "specification": "YX-01", "unit": "个", "score": 98.0}
    second = dict(first, code="B", score=72.0)
    monkeypatch.setattr(server, "fetch_live_material_map", lambda _: {"A": first, "B": second})
    matches = iter([
        {"is_exact": True, "matched_code": "A", "top_candidates": [first]},
        {"is_exact": False, "matched_code": None, "top_candidates": [second], "_initial_match_rank": {
            "top_code": "B", "ranking_score": 72.0, "margin": 10, "uncapped": True,
        }},
    ])
    monkeypatch.setattr(server, "match_item", lambda *args, **kwargs: next(matches))
    rows = server.batch_match_rows([
        {"row_idx": i, "name": "电缆桥架", "specification": "YX-01", "professional": "强电",
         "category": "桥架", "qty": 1, "unit": "个", "loss": 0}
        for i in (1, 2)
    ], task_id="isolated-test")
    result = rows[1]["initial_match"]
    assert result["ranking_score"] == 77.0
    assert result["ranking_evidence_status"] == "stale" and result["distinct_margin"] is None
    assert result["tier"] == "B" and result["priority_review_unavailable"]
    assert rows[1]["bindings"] == result["effective_bindings"]


def test_invalid_frozen_assets_fail_closed(monkeypatch, tmp_path):
    from app import acceptance_calibration as calibration

    original = calibration.ASSETS
    for filename in ("v019_m2_policy.json", "v019_m2_equivalences.json"):
        (tmp_path / filename).write_bytes((original / filename).read_bytes())
    calibration.load_policy.cache_clear()
    monkeypatch.setattr(calibration, "ASSETS", tmp_path)
    try:
        assert calibration.load_policy(require_frozen=True)
        calibration.load_policy.cache_clear()
        policy = tmp_path / "v019_m2_policy.json"
        policy.write_bytes(policy.read_bytes() + b"\n")
        assert calibration.load_policy(require_frozen=True) is None
        calibration.load_policy.cache_clear()
        policy.unlink()
        assert calibration.load_policy(require_frozen=True) is None
    finally:
        calibration.load_policy.cache_clear()


def test_summary_deduplicates_source_rows_and_preserves_unknown_count(candidate):
    from app.match_assessment import summarize_initial_matches

    row = {"document_id": "d", "sheet_id": "s", "source_row_id": "1",
           "professional": "暖通", "initial_match": classify(candidate)}
    expected = summarize_initial_matches([row, row, dict(row, source_row_id="2", initial_match=None)])
    assert expected["total_rows"] == 2
    assert expected["missing_rows"] == 1 and expected["prefill_rate"] is None
    assert expected["by_professional"]["暖通"]["tiers"]["A"] == 1
    assert summarize_initial_matches([])["status"] == "empty"
    assert summarize_initial_matches([{}])["status"] == "unrecorded"


def test_migration_is_nullable_idempotent_and_preserves_legacy_data(tmp_path):
    import sqlite3

    from app.flywheel_db import init_flywheel_tables

    path = str(tmp_path / "test.db")
    init_flywheel_tables(db_path=path)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE flywheel_recommendation_snapshots DROP COLUMN initial_match_json")
        conn.execute("""
            INSERT INTO flywheel_recommendation_snapshots
            (snapshot_id, document_id, sheet_id, source_row_id, revision, professional, parsed_spec_json,
             initial_bindings_json, algo_version, model_version, config_version, config_digest_full)
            VALUES ('snap', 'doc', 'sheet', 'row', 1, '暖通', '{}', '[1,2]', '', '', '', '')
        """)
    init_flywheel_tables(db_path=path)
    init_flywheel_tables(db_path=path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT initial_bindings_json, initial_match_json FROM "
                            "flywheel_recommendation_snapshots").fetchone() == ("[1,2]", None)


def test_upload_rollback_retry_and_authoritative_restore(monkeypatch, tmp_path, candidate):
    from copy import deepcopy
    import json
    import sqlite3

    from app import feedback_contract, flywheel_db

    path = str(tmp_path / "test.db")
    actor = {"actor_user_id": 1, "actor_account_epoch": "test-account"}
    monkeypatch.setattr(feedback_contract, "extract_verified_actor", lambda user: user)
    flywheel_db.init_flywheel_tables(db_path=path)
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TRIGGER fail_second_snapshot BEFORE INSERT ON flywheel_recommendation_snapshots
            WHEN (SELECT COUNT(*) FROM flywheel_recommendation_snapshots) > 0
            BEGIN SELECT RAISE(ABORT, 'isolated failure'); END
        """)
    raw = [{"name": "样例", "row_idx": i} for i in (1, 2)]
    rows = [{"professional": "暖通", "bindings": [candidate], "initial_match": classify(candidate)},
            {"professional": "暖通", "bindings": []}]
    versions = {key: "isolation" for key in (
        "algo_version", "model_version", "config_version", "config_digest_full",
    )}
    params = (actor, "doc", "task", "session", "test.xlsx", "file-digest", raw, rows)
    with pytest.raises(sqlite3.IntegrityError, match="isolated failure"):
        feedback_contract.persist_upload_document_tree(*params, runtime_versions=versions, db_path=path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM flywheel_documents").fetchone() == (0,)
        conn.execute("DROP TRIGGER fail_second_snapshot")
    feedback_contract.persist_upload_document_tree(*params, runtime_versions=versions, db_path=path)
    with sqlite3.connect(path) as conn:
        bindings, assessment = conn.execute(
            "SELECT initial_bindings_json, initial_match_json FROM flywheel_recommendation_snapshots "
            "WHERE source_row_id=?", (rows[0]["source_row_id"],),
        ).fetchone()
    assert json.loads(bindings) == [candidate]
    assert json.loads(assessment) == rows[0]["initial_match"]
    restored = deepcopy(rows)
    restored[0]["initial_match"] = {"tier": "forged"}
    restored[1]["initial_match"] = rows[0]["initial_match"]
    verified, legacy = feedback_contract.annotate_items_identity(actor, restored, db_path=path)
    assert legacy == 0
    assert verified[0]["initial_match"] == rows[0]["initial_match"]
    assert verified[1]["initial_match"] is None
    foreign = dict(actor, actor_account_epoch="foreign")
    rejected, legacy = feedback_contract.annotate_items_identity(foreign, deepcopy(rows), db_path=path)
    assert legacy == 2 and all(item["initial_match"] is None for item in rejected)


def test_docker_assets_have_exact_allowlist():
    root = Path(__file__).resolve().parents[1]
    directory = "app/calibration_assets/"
    assets = [directory + filename for filename in ("v019_m2_policy.json", "v019_m2_equivalences.json")]
    context = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert {line for line in context if line.startswith("!" + directory)} == {
        "!" + value for value in (directory, *assets)
    }
    lines = (root / "Dockerfile").read_text(encoding="utf-8").splitlines()
    assert [line.split()[1:-1] for line in lines if line.startswith("COPY " + directory)] == [assets]
