"""Feedback training gates, hard negatives, and ranker safety."""
from dataclasses import replace
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from app import feedback_ranker as ranking
from app.flywheel_db import init_flywheel_tables
from scripts.v020_feedback_audit import Provenance, audit, chronological_split, reference, utc


def _snapshot(path):
    init_flywheel_tables(db_path=str(path))
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE materials (code TEXT PRIMARY KEY)")
        db.execute("INSERT INTO materials VALUES ('M1')")
        db.execute("""INSERT INTO flywheel_documents
          (document_id, upload_task_id, work_session_id, actor_user_id, actor_account_epoch,
           original_filename, file_sha256, contract_version, created_at)
          VALUES ('d','task','session',1,'epoch','synthetic.xlsx',?,'v', '2026-01-01')""", ("a" * 64,))
        db.execute("INSERT INTO flywheel_sheets (sheet_id,document_id,sheet_index,sheet_name) VALUES ('s','d',0,'synthetic')")
        db.execute("""INSERT INTO flywheel_source_rows
          (source_row_id,document_id,sheet_id,source_row_num,display_row_idx,raw_name,raw_name_norm)
          VALUES ('r','d','s',2,0,'synthetic','synthetic')""")
        db.execute("""INSERT INTO flywheel_recommendation_snapshots
          (snapshot_id,document_id,sheet_id,source_row_id,revision,is_initial,professional,parsed_spec_json,
           auto_top1_code,algo_version,model_version,config_version,config_digest_full)
          VALUES ('snap','d','s','r',1,1,'warm','{}','AUTO','a','m','c','hash')""")
        insert = """INSERT INTO flywheel_feedback_samples
          (client_event_id,payload_digest,actor_user_id,actor_account_epoch,document_id,sheet_id,
           source_row_id,event_seq,initial_snapshot_id,recommendation_snapshot_id,manual_code,auto_code,
           superseded,is_stale_arrival,trust_level,created_at)
          VALUES (?, 'digest', 1, 'epoch','d','s','r',?,'snap','snap','M1',?,?,?,?,'2026-01-02')"""
        db.executemany(insert, [
            ("old", 1, "AUTO", 1, 0, "trusted"), ("stale", 2, "AUTO", 0, 1, "trusted"),
            ("untrusted", 3, "AUTO", 0, 0, "legacy_untrusted"), ("live", 4, "AUTO", 0, 0, "trusted"),
        ])
        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def test_audit_real_schema_quarantines_disagreements_and_preserves_snapshot(tmp_path):
    path = tmp_path / "synthetic-only.db"
    _snapshot(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    event = reference("live")
    provenance = {reference("d"): {"project": "project-a", "source": "list-a", "collected_at": "2025-12-31"}}
    disputed = audit(path, provenance=provenance, reviews={event: {"A": "M1", "B": "M2"}})
    assert disputed["flywheel_counts"]["flywheel_feedback_samples"] == 4
    assert disputed["trusted_current_events"] == 1
    assert disputed["review_eligible"] == 0
    assert disputed["trace"][0]["event_ref"] == event
    assert len(disputed["trace"][0]["review_votes"]) == 2
    assert disputed["finding_counts"] == {"review_disagreement": 1}
    verified = audit(path, provenance=provenance, reviews={event: {"A": "M1", "B": "M1"}})
    assert verified["review_eligible"] == 1
    assert verified["split_status"] == "missing_frozen_cutoffs"
    assert not verified["online_automatic_training"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_audit_empty_and_inconsistent_feedback(tmp_path):
    path = tmp_path / "empty.db"
    _snapshot(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE flywheel_feedback_samples SET auto_code='forged' WHERE client_event_id='live'")
    assert "inconsistent_recommendation_label" in audit(path)["finding_counts"]
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM flywheel_feedback_samples")
    report = audit(path)
    assert report["trusted_current_events"] == report["review_eligible"] == 0
    assert report["split_status"] == "insufficient_reviewed_feedback"


def test_project_source_duplicate_file_and_time_leaks_rejected():
    first = Provenance("P1", "S1", "F1", utc("2026-01-01"), utc("2026-01-02"))
    second = Provenance("P2", "S2", "F2", utc("2026-02-01"), utc("2026-02-02"))
    third = Provenance("P3", "S3", "F3", utc("2026-03-01"), utc("2026-03-02"))
    cutoffs = utc("2026-02-01"), utc("2026-03-01")
    assert chronological_split([first, second, third], *cutoffs) == {
        "train": [first], "dev": [second], "holdout": [third],
    }
    for key in ("project", "source", "content"):
        with pytest.raises(ValueError, match=f"{key} crosses"):
            chronological_split([first, second, replace(third, **{key: getattr(first, key)})], *cutoffs)
    with pytest.raises(ValueError, match="label arrives"):
        chronological_split([replace(first, label_at=cutoffs[0]), second, third], *cutoffs)


def test_missing_invalid_asset_and_default_switch(monkeypatch, tmp_path):
    path = tmp_path / "weights.json"
    monkeypatch.setenv("LISTTRANS_V020_M0_RANKER_ASSET", str(path))
    for switch in (None, "", "off", "ON", "invalid"):
        if switch is None:
            monkeypatch.delenv("LISTTRANS_V020_M0_RANKER", raising=False)
        else:
            monkeypatch.setenv("LISTTRANS_V020_M0_RANKER", switch)
        assert ranking.active_ranker() is None
    monkeypatch.setenv("LISTTRANS_V020_M0_RANKER", "on")
    monkeypatch.setenv("LISTTRANS_V020_M0_RANKER_SHA256", "0" * 64)
    assert ranking.active_ranker() is None
    payload = {"version": ranking.VERSION, "fields": ranking.FIELDS, "weights": [1] + [0] * (len(ranking.FIELDS) - 1)}
    for weights in ([float("nan")] * len(ranking.FIELDS), [5] * len(ranking.FIELDS), payload["weights"]):
        raw = json.dumps({**payload, "weights": weights}).encode()
        path.write_bytes(raw)
        assert ranking.active_ranker() is None
        digest = hashlib.sha256(raw).hexdigest()
        monkeypatch.setenv("LISTTRANS_V020_M0_RANKER_SHA256", digest)
        if weights == payload["weights"]:
            assert ranking.active_ranker() == (tuple(weights), digest)
        else:
            assert ranking.active_ranker() is None


def test_rerank_keeps_hard_blocked_capped_threshold_and_protected_slots():
    def row(code, score, blocked=False, name="三通"):
        return {"code": code, "name": name, "specification": "", "score": score, "hard_blocked": blocked,
                "recall_sources": []}

    candidates = [row("a", 80), row("cap", 79), row("b", 78), row("low", 74),
                  row("fan", 73, name="防爆风机"), row("block", 0, True)]
    inputs = {r["code"]: (0.8 if r["code"] == "cap" else 1, {"attrs": {"管径": 20 if r["code"] == "a" else 15}})
              for r in candidates}
    query = {"name": "三通", "parsed_spec": {"attrs": {"管径": 15}}}
    model = ((5,) + (0,) * (len(ranking.FIELDS) - 1), "synthetic")
    scores = {r["code"]: r["score"] for r in candidates}
    assert ranking.rerank(query, candidates, inputs, model, exact_threshold=75) == 2
    assert [r["code"] for r in candidates] == ["b", "cap", "a", "low", "fan", "block"]
    assert {r["code"]: r["score"] for r in candidates} == scores
    assert ranking.rerank({**query, "name": "排风机", "specification": "防爆要求未知"},
                          candidates, inputs, model, exact_threshold=75) == 0
    candidates[0]["recall_sources"] = ["exact_code"]
    assert ranking.rerank(query, candidates, inputs, model, exact_threshold=75) == 0


def test_matcher_bypasses_experiment_for_other_profiles_and_calibration(monkeypatch):
    from app import matcher

    observed = []
    monkeypatch.setattr(ranking, "active_ranker", lambda: observed.append("load"))
    monkeypatch.setattr(matcher, "hybrid_recall", lambda *args: [])
    monkeypatch.setenv("LISTTRANS_V020_M0_RANKER", "on")
    monkeypatch.setenv("LISTTRANS_V019_M1_RERANK", "off")
    monkeypatch.setenv("LISTTRANS_V019_M0_SCORING", "legacy")
    query = {"name": "三通", "parsed_spec": {}}
    matcher.match_item(query, calibration_groups={})
    assert not observed
    monkeypatch.setenv("LISTTRANS_V019_M0_SCORING", "dedup")
    matcher.match_item(query)
    assert not observed
    monkeypatch.setenv("LISTTRANS_V019_M0_SCORING", "legacy")
    matcher.match_item(query)
    assert observed == ["load"]


@pytest.fixture
def experiment_data(monkeypatch):
    evidence = Path(__file__).resolve().parents[1] / "doc/V0.2.0/evidence/m0_r01_data.py"
    spec = importlib.util.spec_from_file_location("v020_data", evidence)
    data = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    spec.loader.exec_module(data)
    return data


def test_same_name_structured_negative_categories(experiment_data):
    left = ranking.structured_values("合成对照", "不锈钢 SUS304 非防爆 不带提升泵", {
        "model": "INV-01-A", "attrs": {"管径": 20, "外径": 27, "厚度": 2, "额定电流": 16},
    })
    right = ranking.structured_values("合成对照", "不锈钢 SUS316L 防爆 带提升泵", {
        "model": "INV-01-B", "attrs": {"管径": 25, "外径": 34, "厚度": 4, "额定电流": 20},
    })
    common = {"管径", "外径", "304_vs_316L", "型号尾缀", "防爆", "提升泵"}
    assert set(experiment_data.contrast_fields(left, right, "装饰")) == common | {"装饰/厚度"}
    assert set(experiment_data.contrast_fields(left, right, "强电")) == common | {"强电/额定电流"}


@pytest.mark.parametrize("name,domain,positive,negative,expected", [
    ("石膏板", "装饰", "1200×2400×12mm", "1200×2400×9mm", "装饰/厚度"),
    ("风机盘管", "暖通", "FP-20-A 不带提升泵", "FP-20-B 带提升泵", "提升泵"),
])
def test_train_pair_builder_on_synthetic_catalog(
    experiment_data, name, domain, positive, negative, expected,
):
    item = {"id": "synthetic-train", "name": name, "specification": positive, "professional": domain,
            "supply_category": name, "unit": "个", "label_type": "1to1", "target_codes": ["ok"],
            "ambiguous": False, "qc_suspect": False, "query_source": "raw"}
    labels = {item["id"]: {"split": "train", "status": "answerable", "target_alternatives": {"ok": ["ok"]}}}
    catalog = [
        {"code": code, "name": name, "specification": spec, "category": name}
        for code, spec in (("ok", positive), ("conflict", negative))
    ]
    pairs = experiment_data.build_pairs([item], labels, catalog)
    assert len(pairs) == 1 and pairs[0]["negative"] == "conflict"
    assert expected in pairs[0]["contrasts"]
    labels[item["id"]]["split"] = "dev"
    with pytest.raises(ValueError, match="only Train"):
        experiment_data.build_pairs([item], labels, catalog)


def test_current_feedback_audit_rejects_live_wal(tmp_path):
    path = tmp_path / "checkpoint-required.db"
    _snapshot(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("UPDATE flywheel_feedback_samples SET auto_code='changed'")
        db.commit()
        with pytest.raises(ValueError, match="checkpointed"):
            audit(path)


def test_projection_never_deserializes_heldout_payload(experiment_data, monkeypatch, tmp_path):
    governance_items = [
        '    "GT-1": {\n      "split": "train",\n      "status": "answerable"\n    }',
        '    "GT-2": {\n      "split": "test",\n      <invalid heldout label>\n    }',
        '    "GT-3": {\n      "split": "dev",\n      "status": "answerable"\n    }',
    ]
    gold_items = [
        '  {\n   "id": "GT-1",\n   "name": "train"\n  }',
        '  {\n   "id": "GT-2",\n   <invalid heldout text>\n  }',
        '  {\n   "id": "GT-3",\n   "name": "dev"\n  }',
    ]
    governance = tmp_path / "synthetic-governance.json"
    golden = tmp_path / "synthetic-golden.json"
    governance.write_text('{\n  "samples": {\n' + ",\n".join(governance_items) + '\n  }\n}', encoding="utf-8")
    golden.write_text('{\n "items": [\n' + ",\n".join(gold_items) + '\n ]\n}', encoding="utf-8")
    monkeypatch.setattr(experiment_data, "GOVERNANCE", governance)
    monkeypatch.setattr(experiment_data, "GOLDEN", golden)
    monkeypatch.setattr(experiment_data, "DATA_COUNTS", {"train": 1, "dev": 1})
    items, labels = experiment_data.read_projection()
    assert [item["id"] for item in items] == list(labels) == ["GT-1", "GT-3"]
