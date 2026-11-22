"""Audit a checkpointed flywheel SQLite snapshot. Output contains opaque references only."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
import sqlite3

SPLITS = ("train", "dev", "holdout")


def reference(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def utc(value):
    instant = datetime.fromisoformat(value)
    # SQLite CURRENT_TIMESTAMP is stored as naive UTC.
    return (instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


@dataclass(frozen=True)
class Provenance:
    project: str
    source: str
    content: str
    collected_at: datetime
    label_at: datetime


def assert_isolated_splits(splits):
    if set(splits) != set(SPLITS) or any(not splits[split] for split in SPLITS):
        raise ValueError("train/dev/holdout must all be populated")
    for group in splits.values():
        for row in group:
            if (not all(isinstance(key, str) and key.strip() for key in
                        (row.project, row.source, row.content)) or not row.collected_at.tzinfo
                    or not row.label_at.tzinfo or row.collected_at > row.label_at):
                raise ValueError("invalid project/source/time provenance")
    for early, late in combinations(SPLITS, 2):
        left, right = splits[early], splits[late]
        for key in ("project", "source", "content"):
            if {getattr(row, key) for row in left} & {getattr(row, key) for row in right}:
                raise ValueError(f"{key} crosses {early}/{late}")
        if max(row.label_at for row in left) >= min(row.collected_at for row in right):
            raise ValueError(f"time crosses {early}/{late}")


def chronological_split(rows, train_end, dev_end):
    if train_end >= dev_end:
        raise ValueError("time cutoffs must be ordered")
    splits = {split: [] for split in SPLITS}
    for row in rows:
        split = "train" if row.collected_at < train_end else "dev" if row.collected_at < dev_end else "holdout"
        cutoff = {"train": train_end, "dev": dev_end}.get(split)
        if cutoff and row.label_at >= cutoff:
            raise ValueError(f"label arrives at or after the {split} cutoff")
        splits[split].append(row)
    assert_isolated_splits(splits)
    return splits


def check_checkpoint(path):
    for suffix in ("-wal", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("use a checkpointed, stable SQLite snapshot")


def snapshot_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _review_sample(row, review_book):
    event = reference(row["client_event_id"])
    manual, auto = (str(row[key] or "").strip() for key in ("manual_code", "auto_code"))
    judgments = review_book.get(event, {})
    if not isinstance(judgments, dict) or any(
        not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(label, str) or not label.strip()
        for reviewer, label in judgments.items()
    ):
        raise ValueError("review book requires distinct reviewer IDs mapped to nonblank material codes")
    labels = set(judgments.values())
    findings = []
    if not row["verified_document"] or not row["verified_row"] or not row["verified_snapshot"] or not row["initial"]:
        findings.append("invalid_source_or_snapshot")
    if not manual or not row["material_exists"]:
        findings.append("unknown_manual_code")
    if (row["auto_code"] != row["verified_auto"] or row["is_correct"] not in (0, 1)
            or bool(row["is_correct"]) != bool(auto and auto == manual)):
        findings.append("inconsistent_recommendation_label")
    if len(labels) > 1 or (labels and labels != {manual}):
        findings.append("review_disagreement")
    elif len(judgments) < 2:
        findings.append("awaiting_independent_review")
    return {
        "event_ref": event, "document_ref": reference(row["document_id"]),
        "review_votes": {reference(reviewer): reference(label) for reviewer, label in sorted(judgments.items())},
        "recorded_label_ref": reference(manual), "findings": findings,
    }


def audit(path, *, provenance=None, reviews=None, cutoffs=None):
    """Review trusted current events without importing the app or opening a write connection."""
    path = Path(path).resolve(strict=True)
    check_checkpoint(path)
    digest = snapshot_digest(path)
    provenance, reviews = provenance or {}, reviews or {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        db.row_factory = sqlite3.Row
        names = ("flywheel_feedback_samples", "flywheel_records", "flywheel_documents",
                 "flywheel_source_rows", "flywheel_rule_suggestions", "flywheel_pattern_aggregates")
        counts = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in names}
        rows = db.execute("""
            SELECT f.*, d.document_id AS verified_document, d.file_sha256, d.created_at AS uploaded_at,
                   s.sheet_index, r.source_row_num, r.source_row_id AS verified_row,
                   n.snapshot_id AS verified_snapshot, i.snapshot_id AS initial,
                   COALESCE(NULLIF(n.auto_top1_code, ''), i.auto_top1_code, '') AS verified_auto,
                   (m.code IS NOT NULL) AS material_exists
            FROM flywheel_feedback_samples f
            LEFT JOIN flywheel_documents d
                ON d.document_id=f.document_id AND d.actor_user_id=f.actor_user_id
                AND d.actor_account_epoch=f.actor_account_epoch AND d.collection_boundary='server_upload'
            LEFT JOIN flywheel_sheets s ON s.sheet_id=f.sheet_id AND s.document_id=d.document_id
            LEFT JOIN flywheel_source_rows r ON r.source_row_id=f.source_row_id
                AND r.sheet_id=s.sheet_id AND r.document_id=d.document_id
            LEFT JOIN flywheel_recommendation_snapshots i ON i.snapshot_id=f.initial_snapshot_id
                AND i.source_row_id=r.source_row_id AND i.sheet_id=s.sheet_id AND i.document_id=d.document_id
                AND i.is_initial=1
            LEFT JOIN flywheel_recommendation_snapshots n ON n.snapshot_id=f.recommendation_snapshot_id
                AND n.source_row_id=r.source_row_id AND n.sheet_id=s.sheet_id AND n.document_id=d.document_id
            LEFT JOIN materials m ON m.code=f.manual_code
            WHERE f.trust_level='trusted' AND f.superseded=0 AND f.is_stale_arrival=0 AND f.event_seq>0
            ORDER BY f.id
        """).fetchall()
    check_checkpoint(path)
    if snapshot_digest(path) != digest:
        raise RuntimeError("SQLite snapshot changed during extraction")

    reviewed, by_origin, eligible = [], defaultdict(list), []
    for row in rows:
        trace = _review_sample(row, reviews)
        reviewed.append(trace)
        if row["verified_row"] and re.fullmatch(r"[0-9a-f]{64}", row["file_sha256"] or ""):
            by_origin[row["file_sha256"], row["sheet_index"], row["source_row_num"]].append((row, trace))
        else:
            trace["findings"].append("unverified_source_content")
    for copies in by_origin.values():
        if len({row["manual_code"] for row, _ in copies}) > 1:
            for _, trace in copies:
                trace["findings"].append("conflicting_labels_for_source_row")
            continue
        valid = [(row, trace) for row, trace in copies if not trace["findings"]]
        if not valid:
            continue
        row, trace = valid[0]
        for _, duplicate in valid[1:]:
            duplicate["findings"].append("duplicate_source_row")
        metadata = provenance.get(trace["document_ref"], {})
        try:
            collected = utc(metadata["collected_at"])
            available = utc(row["created_at"])
            if not collected <= utc(row["uploaded_at"]) <= available:
                raise ValueError("inconsistent source and label chronology")
            project, source = metadata["project"], metadata["source"]
            if not all(isinstance(v, str) and v.strip() and v == v.strip() for v in (project, source)):
                raise ValueError("missing source/project key")
            eligible.append(Provenance(project, source, row["file_sha256"], collected, available))
        except (KeyError, TypeError, ValueError):
            trace["findings"].append("missing_or_invalid_project_time_provenance")

    split_counts = None
    split_status = "insufficient_reviewed_feedback"
    if eligible:
        split_status = "missing_frozen_cutoffs"
        if cutoffs:
            try:
                splits = chronological_split(eligible, *cutoffs)
                split_counts = {name: len(samples) for name, samples in splits.items()}
                split_status = "pending_sample_size_approval"
            except ValueError as exc:
                split_status = str(exc)
    return {
        "snapshot_sha256": digest, "flywheel_counts": counts, "trusted_current_events": len(rows),
        "review_eligible": len(eligible), "split_status": split_status, "split_counts": split_counts,
        "online_automatic_training": False, "offline_benchmark_is_distinct": True,
        "finding_counts": dict(Counter(finding for trace in reviewed for finding in trace["findings"])),
        "trace": reviewed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--cutoffs", nargs=2, type=utc, metavar=("TRAIN_END", "DEV_END"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    inputs = [json.loads(path.read_text(encoding="utf-8")) if path else {} for path in
              (args.provenance, args.reviews)]
    report = audit(args.snapshot, provenance=inputs[0], reviews=inputs[1], cutoffs=args.cutoffs)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
