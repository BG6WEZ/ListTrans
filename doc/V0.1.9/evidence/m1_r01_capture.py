"""Capture the actual M0 ranking and Top-50 feature rows for Train and Dev."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "doc/V0.1.8/evidence"))
import m3_r01_dev_selection as replay


def stamp(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def capture(mode, output):
    if output.exists():
        raise FileExistsError(output)
    os.environ.update({
        "LISTTRANS_V019_M0_SCORING": "legacy", "LISTTRANS_V019_M1_RERANK": "off",
        "ENABLE_SERIES_RERUN": "false",
    })
    feature_rows_by_item = []
    recall_digests = []
    baseline_rows = []
    diagnostics = replay.diagnostic_rows
    governance_file = ROOT / "tests/fixtures/golden_governance_v1.json"
    governance = replay.read_json(governance_file)["samples"]
    splits = {split: sorted(gid for gid, record in governance.items() if record["split"] == split)
              for split in ("train", "dev", "test")}
    if [len(ids) for ids in splits.values()] != [842, 218, 160]:
        raise RuntimeError("Frozen split counts changed")
    source_sha256 = {p: replay.sha(ROOT / p) for p in ("app/matcher.py", "app/candidate_reranker.py")}

    def observe(items, labels):
        from app import candidate_reranker, matcher

        recall = matcher.hybrid_recall
        match = matcher.match_item

        def record_recall(*args, **kwargs):
            candidates = recall(*args, **kwargs)
            recall_digests.append(hashlib.sha256(json.dumps([c["code"] for c in candidates]).encode()).hexdigest())
            return candidates

        def record_match(*args, **kwargs):
            result = match(*args, **kwargs)
            candidates = result["diagnostics"]["candidates"]
            baseline_rows.append({
                "ranked": [[c["code"], c["hard_blocked"]] for c in candidates],
                "targets": {str(t["code"]): t["state"] for t in result["diagnostics"]["targets"]},
            })
            return result

        def record_features(query, candidates, profile, *, exact_threshold):
            rows = candidate_reranker.feature_rows(query, candidates, depth=50, exact_threshold=exact_threshold)
            group = stamp([str(query.get(k) or "").strip().casefold()
                           for k in ("professional", "name", "specification", "unit")])
            feature_rows_by_item.append({"features": rows, "query_group": group})
            return None

        if any(labels[it["id"]]["split"] not in ("train", "dev") for it in items):
            raise RuntimeError("Held-out sample in feature extraction")
        with patch.object(candidate_reranker, "rerank", record_features), \
             patch.object(matcher, "match_item", record_match), \
             patch.object(matcher, "hybrid_recall", record_recall), \
             patch.dict(os.environ, {"LISTTRANS_V019_M1_RERANK": "linear-50"}):
            evaluated, seconds = diagnostics(items, labels)
        if not len(items) == len(feature_rows_by_item) == len(baseline_rows) == len(recall_digests):
            raise RuntimeError("Frozen rank and feature rows do not align")
        for it, base, observed, recall_digest in zip(items, baseline_rows, feature_rows_by_item, recall_digests, strict=True):
            gid = it["id"]
            base.update(observed, id=gid, split=labels[gid]["split"], domain=it["professional"],
                        recall_sha256=recall_digest, pool_size=evaluated[gid]["pool_size"],
                        recall_targets=evaluated[gid]["targets"])
        return evaluated, seconds

    def save(path, report):
        if any(replay.sha(ROOT / p) != h for p, h in source_sha256.items()):
            raise RuntimeError("Feature code changed during the capture")
        digest = hashlib.sha256("".join(recall_digests).encode()).hexdigest()
        ref = replay.read_json(ROOT / "doc/V0.1.9/evidence" / f"m0_r01_{mode}_legacy_final.json")
        if digest != ref["recall_order_sha256"] or any(
            report["splits"][split]["rank"] != ref["splits"][split]["rank"] for split in ("train", "dev", "train_dev")
        ):
            raise RuntimeError("Frozen candidate recall or business ranking did not reproduce M0")
        data = {
            "version": "v019-m1-pool-v1", "mode": mode, "exit_code": 0, "source_sha256": source_sha256,
            "catalog_sha256": report["catalog_sha256"], "golden_sha256": report["golden_sha256"],
            "governance_sha256": report["governance_sha256"], "index": report["vector_index"],
            "model_metadata": report["model_metadata"], "split_id_sha256": {k: stamp(v) for k, v in splits.items()},
            "split_counts": {k: len(v) for k, v in splits.items()}, "recall_order_sha256": digest,
            "candidate_count": sum(len(row["ranked"]) for row in baseline_rows), "rows": baseline_rows,
            "timed_seconds": report["evaluation_seconds"],
        }
        with path.open("xb") as file:
            with gzip.GzipFile(filename="", mode="wb", fileobj=file, mtime=0) as stream:
                stream.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())

    spec = {"id": f"m1_{mode}_freeze", "mode": mode, "m0": "0", "m1": "0", "m2": "0",
            "top_k": 50, "fusion": "union", "cap": 0, "http": False}
    with patch.multiple(replay, diagnostic_rows=observe, save_json=save):
        replay.run_single_worker(spec, output)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("keyword", "vector"), required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    capture(args.mode, args.out)
