"""Safety and fallbacks for the default-off V021 cross-encoder experiment."""

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from app import acceptance_calibration, binding_decisions, cross_encoder_reranker as ce, db_manager, matcher


def candidate(code, score, **fields):
    return {"code": code, "score": score, "max_score": 1.0, "name": code, "specification": "",
            "category": "", "unit": "个", "description": "", **fields}


@pytest.fixture()
def ranking(monkeypatch):
    entries = [candidate("A", 82), candidate("B", 81), candidate("C", 72)]
    live = {c["code"]: c for c in entries}
    monkeypatch.setattr(binding_decisions, "fetch_live_material_map", lambda codes: live)
    monkeypatch.setattr(binding_decisions, "evaluate_hard_conflicts", lambda row, material: None)
    monkeypatch.setattr(acceptance_calibration, "_physical_evidence", lambda *a: {"comparisons": []})
    predict = Mock(return_value=[0.0, 1.0, 0.5])
    monkeypatch.setattr(ce._MODEL, "predict", predict)
    return entries, live, predict


def test_off_no_module_import_or_weights(monkeypatch):
    import builtins

    with monkeypatch.context() as enabled:
        enabled.setenv(ce.SWITCH, "off")
        reference = matcher.match_item({"parsed_spec": {}}, initial_match_groups={})
    monkeypatch.delenv(ce.SWITCH, raising=False)
    importer = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name != "app.cross_encoder_reranker"
        return importer(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    default = matcher.match_item({"parsed_spec": {}}, initial_match_groups={})
    assert default == reference
    assert "cross_encoder_rerank" not in default
    assert ce._MODEL.process is None


def test_final_evidence_uses_full_pool_and_adjusted_scores(monkeypatch, ranking):
    db_manager.init_db()
    entries, _, predict = ranking
    raw = [{**c, "brand": "", "semantic_score": 0.0} for c in entries]
    monkeypatch.setattr(matcher, "hybrid_recall", lambda *a: raw)
    monkeypatch.setattr(matcher, "score_candidate_with_detail",
                        lambda query, entry, semantic: (entry["score"], [], {}))
    monkeypatch.setenv(ce.SWITCH, "on")
    result = matcher.match_item({"name": "查询", "unit": "个", "parsed_spec": {}}, initial_match_groups={})
    assert [c["code"] for c in result["top_candidates"]] == ["B", "A", "C"]
    assert result["matched_code"] == "B"
    assert result["cross_encoder_rerank"] == {"status": "applied", "candidate_count": 3, "moved_count": 2}
    assert result["_initial_match_rank"] == {
        "top_code": "B", "ranking_score": 82, "uncapped": True,
        "competitor_code": "A", "margin": 3.0, "equivalent_codes": [],
    }
    predict.assert_called_once_with("查询 个", ["A 个", "B 个", "C 个"])


@pytest.mark.parametrize("protected", ["direct", "hard", "cap", "rounded_cap", "inactive", "missing", "zero",
                                       "safety_unknown", "non_one", "requested_unverified", "live_conflict"])
def test_protected_boundary_stops_inference(monkeypatch, ranking, protected):
    entries, live, predict = ranking
    first = entries[0]
    raw_caps = set()
    if protected == "direct":
        entries[-1]["recall_sources"] = ["exact_code"]
    elif protected == "hard":
        first["hard_blocked"] = True
    elif protected == "cap":
        first["max_score"] = 0.9
    elif protected == "rounded_cap":
        raw_caps.add(first["code"])
    elif protected == "inactive":
        live["A"] = {**first, "name": "接头（停用）"}
    elif protected == "missing":
        del live["A"]
    elif protected == "zero":
        first["score"] = 0
    elif protected == "live_conflict":
        monkeypatch.setattr(binding_decisions, "evaluate_hard_conflicts", lambda *a: "冲突")
    else:
        field = {"safety_unknown": "防爆", "non_one": "单位倍率", "requested_unverified": "功率"}[protected]
        comparison = {"field": field, "state": "different" if protected == "non_one" else "missing",
                      "requested_unverified": protected == "requested_unverified"}
        monkeypatch.setattr(acceptance_calibration, "_physical_evidence",
                            lambda *a: {"comparisons": [comparison]})
    original = [dict(entry) for entry in entries]
    assert ce.rerank({"name": "test"}, entries, raw_caps)["status"] == "skipped"
    assert entries == original
    predict.assert_not_called()


def test_pool_depth_and_boundary(monkeypatch, ranking):
    entries = [candidate(str(i), 98 - i) for i in range(25)]
    monkeypatch.setattr(binding_decisions, "fetch_live_material_map", lambda codes: {
        c["code"]: c for c in entries if c["code"] in codes
    })
    _, _, predict = ranking
    predict.side_effect = lambda query, texts: [1.0] * len(texts)
    original = entries.copy()
    assert ce.rerank({"name": "x"}, entries, set())["candidate_count"] == 20
    assert entries[20:] == original[20:]
    assert 79 <= entries[19]["score"] <= entries[0]["score"] <= 98


@pytest.mark.parametrize("failure,expected", [
    (ce.OfflineFailure("inference_timeout"), "inference_timeout"),
    (RuntimeError("do not leak internal exception"), "rerank_error"),
])
def test_failure_leaves_every_score_and_position_unchanged(ranking, failure, expected):
    entries, _, predict = ranking
    predict.side_effect = failure
    baseline = entries.copy()
    result = ce.rerank({"name": "x"}, entries, set())
    assert result == {"status": "degraded", "reason": expected}
    assert baseline == entries


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_tampered_model_via_real_worker(tmp_path, missing):
    directory = tmp_path / "unavailable"
    if not missing:
        directory.mkdir()
        for name in ce.ASSET_SHA256:
            (directory / name).write_bytes(b"tampered")
    worker = ce.OfflineWorker(directory)
    reason = "model_directory_missing" if missing else "model_hash_mismatch"
    try:
        with pytest.raises(ce.OfflineFailure, match=reason):
            worker.predict("query", ["candidate"])
        assert worker.process is None
        assert worker.failure == reason
    finally:
        worker.close()


def test_validated_asset_set_cannot_find_unpinned_tokenizer_file(tmp_path, monkeypatch):
    sample = tmp_path / "tokenizer.json"
    sample.write_bytes(b"pinned")
    monkeypatch.setattr(ce, "ASSET_SHA256", {sample.name: hashlib.sha256(b"pinned").hexdigest()})
    ce._verify_assets(tmp_path)
    (tmp_path / "added_tokens.json").touch()
    with pytest.raises(ce.OfflineFailure, match="unexpected_model_asset"):
        ce._verify_assets(tmp_path)


def test_asset_pins_match_dispatch():
    dispatch = Path(__file__).resolve().parents[1] / "doc/V0.2.1/evidence/m3_r01_dispatch_baseline.json"
    signed = json.loads(dispatch.read_bytes())["authorized_cross_encoder_asset"]
    assert ce.MODEL_REVISION == signed["pinned_revision"]
    assert ce.ASSET_SHA256 == signed["core_files_sha256"]


def test_worker_inference_poll_timeout_cleans_up(monkeypatch, tmp_path):
    worker = ce.OfflineWorker(tmp_path)
    connection = Mock()
    connection.poll.return_value = False
    worker.connection = connection
    worker.process = Mock(pid=123)
    worker.process.is_alive.side_effect = [True, False, False]
    with pytest.raises(ce.OfflineFailure, match="inference_timeout"):
        worker.predict("query", ["candidate"])
    worker_error = worker.failure
    assert worker.process is None and worker.connection is None
    assert worker_error == "inference_timeout"
    connection.close.assert_called_once()
    assert math.isclose(connection.poll.call_args.args[0], ce.INFERENCE_TIMEOUT_SECONDS, abs_tol=0.1)


def test_concurrent_request_falls_back_without_disabling_worker(tmp_path):
    worker = ce.OfflineWorker(tmp_path)
    with worker.lock, pytest.raises(ce.OfflineFailure, match="worker_busy"):
        worker.predict("query", ["candidate"])
    assert worker.failure is None
    assert worker.process is None


def test_air_gap_blocks_dns_and_socket_connect():
    probe = """
import socket, sys
from app.cross_encoder_reranker import _reject_network, OfflineFailure
sys.addaudithook(_reject_network)
with socket.socket() as sock:
    for action in (lambda: socket.getaddrinfo('localhost', 80),
                   lambda: sock.connect(('127.0.0.1', 1))):
        try:
            action()
        except OfflineFailure as failure:
            assert str(failure) == 'network_blocked'
        else:
            raise AssertionError('network access was not blocked')
"""
    subprocess.run([sys.executable, "-c", probe], cwd=Path(__file__).resolve().parents[1],
                   env=os.environ.copy(), check=True, timeout=15)
