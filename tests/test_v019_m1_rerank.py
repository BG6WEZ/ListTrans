"""V019-M1 bounded permutation, feature and default-off contracts."""
import pytest

from app import candidate_reranker as reranker
from app import db_manager, matcher


def row(index, *, score=70.0, cap=1.0, blocked=False, semantic=0.0, sources=(), parsed=None):
    return {
        "code": f"M1-{index:02}", "name": "电缆", "specification": "4*25", "unit": "米",
        "category": "强电", "brand": "", "description": "", "parsed_params": parsed or {},
        "score": score, "max_score": round(cap, 4), "hard_blocked": blocked, "semantic_score": semantic,
        "recall_source": sources[0] if sources else "", "recall_sources": list(sources), "match_reasons": [],
        "_rerank_inputs": (cap, semantic, parsed or {}),
    }


@pytest.fixture
def synthetic_model(monkeypatch):
    weights = [0.0] * len(reranker.FEATURES)
    weights[reranker.FEATURES.index("semantic_score")] = 12.0
    models = {mode: (weights, 0.0) for mode in ("keyword", "vector")}
    monkeypatch.setattr(reranker, "_load_model", lambda: (models, "synthetic"))


@pytest.mark.parametrize("profile, promoted", [("linear-20", "M1-10"), ("linear-50", "M1-27"),
                                              ("blend-20", "M1-10"), ("blend-50", "M1-27")])
def test_rank_only_permutation_with_cap_threshold_and_tail(profile, promoted, synthetic_model):
    rows = [row(i) for i in range(60)]
    rows[0] = row(0, score=90)
    rows[1] = row(1, score=80, cap=0.99999, semantic=1.0)
    rows[2] = row(2, score=0.0, cap=0.0)
    rows[3] = row(3, score=0.0, blocked=True)
    rows[10] = row(10, semantic=0.5, sources=("semantic",))
    rows[27] = row(27, semantic=1.0, sources=("semantic",))
    rows[55] = row(55, semantic=1.0, sources=("semantic",))
    original = list(rows)

    report = reranker.rerank({}, rows, profile, exact_threshold=75.0)

    assert rows[:4] == original[:4]
    assert rows[4]["code"] == promoted
    assert rows[report["depth"]:] == original[report["depth"]:]
    assert report["mode"] == "vector"
    assert sorted(rows, key=lambda r: r["code"]) == original


def test_explicit_code_and_missing_model_leave_rank_unchanged(monkeypatch, synthetic_model):
    rows = [row(0), row(1, semantic=1), row(2, sources=("exact_code",))]
    original = list(rows)
    assert reranker.rerank({}, rows, "linear-20", exact_threshold=75.0) is None
    assert rows == original
    monkeypatch.setattr(reranker, "_load_model", lambda: None)
    rows = rows[:2]
    assert reranker.rerank({}, rows, "linear-20", exact_threshold=75.0) is None
    assert rows == original[:2]


def test_typed_unknown_and_negation_do_not_count_as_equal():
    query = {"parsed_spec": {"typed_attrs": {
        "防爆": {"value": False, "status": "negated"}, "管径": {"value": 20, "status": "known"},
    }}}
    candidate = row(0, parsed={"typed_attrs": {
        "防爆": {"value": 0, "status": "known"}, "管径": {"value": 20, "status": "unknown"},
        "外径": {"value": 20, "status": "known"},
    }})
    _, _, features = reranker.feature_rows(query, [candidate], depth=20, exact_threshold=75.0)[0]
    assert tuple(features[12:15]) == (0.0, 0.5, 0.5)


def test_match_item_off_invalid_and_round_trip(monkeypatch, synthetic_model):
    db_manager.init_db()
    monkeypatch.setattr(matcher, "hybrid_recall", lambda *_args: [
        row(90, score=80), row(91, score=78, semantic=1, sources=("semantic",)),
    ])
    monkeypatch.setattr(matcher, "score_candidate_with_detail",
                        lambda _q, c, _s: (c["score"], [], {"hard_blocked": False, "max_score": 1.0}))
    monkeypatch.setenv("LISTTRANS_V019_M0_SCORING", "legacy")
    monkeypatch.delenv("LISTTRANS_V019_M1_RERANK", raising=False)
    query = {"name": "电缆", "specification": "4*25", "parsed_spec": {}}
    original = matcher.match_item(query, diagnostic_targets=["M1-90"])
    assert original["matched_code"] == "M1-90"

    monkeypatch.setenv("LISTTRANS_V019_M1_RERANK", "linear-20")
    ranked = matcher.match_item(query, diagnostic_targets=["M1-90"])
    assert ranked["matched_code"] == "M1-91" and ranked["is_exact"]
    assert ranked["diagnostics"]["rerank"]["depth"] == 20
    assert all("_rerank_inputs" not in c for c in ranked["top_candidates"])

    for value in ("invalid", "off"):
        monkeypatch.setenv("LISTTRANS_V019_M1_RERANK", value)
        assert matcher.match_item(query, diagnostic_targets=["M1-90"]) == original
