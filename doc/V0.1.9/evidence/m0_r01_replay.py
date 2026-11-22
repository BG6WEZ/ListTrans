"""Paired V019-M0 Train/Dev replay using frozen V018 assets and production routes."""
import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "doc" / "V0.1.8" / "evidence"))
import m3_r01_dev_selection as replay


def run(mode, profile, output, *, rank_only=False):
    os.environ["LISTTRANS_V019_M0_SCORING"] = profile
    os.environ["ENABLE_SERIES_RERUN"] = "false"
    scores = Counter()
    saturation = Counter()
    recall_digests = []
    ranked = []
    uploads = {}
    singles = {}
    source_hashes = {path: replay.sha(ROOT / path) for path in ("app/matcher.py", "app/server.py")}

    def diagnostics(items, governance):
        from app import matcher

        match_item = matcher.match_item
        recall = matcher.hybrid_recall

        def observe_recall(*args, **kwargs):
            pool = recall(*args, **kwargs)
            recall_digests.append(hashlib.sha256(json.dumps([c["code"] for c in pool]).encode()).hexdigest())
            return pool

        def observe_match(*args, **kwargs):
            result = match_item(*args, **kwargs)
            candidates = result["diagnostics"]["candidates"]
            eligible = [c for c in candidates if not c["hard_blocked"]]
            scores.update(str(min(100, int(c["score"] // 10) * 10)) for c in eligible)
            saturation.update(
                eligible=len(eligible), score_100=sum(c["score"] == 100 for c in eligible),
                score_ge_98=sum(c["score"] >= 98 for c in eligible),
            )
            gap = round(eligible[0]["score"] - eligible[1]["score"], 3) if len(eligible) >= 2 else None
            targets = {t["code"] for t in result["diagnostics"]["targets"]}
            ranked.append({
                "top2_gap": gap,
                "top": [[c["code"], c["score"]] for c in eligible[:3]],
                "targets": {c["code"]: [c["score"], c["hard_blocked"]] for c in candidates if c["code"] in targets},
            })
            return result

        with patch.object(matcher, "match_item", observe_match), patch.object(matcher, "hybrid_recall", observe_recall):
            data, elapsed = replay_diagnostics(items, governance)
        if len(data) != len(ranked) or len(data) != len(recall_digests):
            raise RuntimeError("Expected one rank and recall observation per row")
        return data, elapsed

    def match(client, headers, item):
        result = replay_match(client, headers, item)
        singles[item["id"]] = {k: v for k, v in result.items() if k != "ms"}
        return result

    def upload(client, headers, items):
        rows, meta = replay_upload(client, headers, items)
        uploads.update(rows)
        return rows, meta

    replay_diagnostics = replay.diagnostic_rows
    replay_match = replay.send_match
    replay_upload = replay.send_upload
    replay_phase = replay.phase

    @contextmanager
    def trace_phase(report, key):
        print(f"{mode} {profile} {key} started", flush=True)
        with replay_phase(report, key):
            yield
        print(f"{mode} {profile} {key} finished in {report[key]['seconds']:.1f}s", flush=True)

    spec = {
        "id": f"{mode}_{profile}", "mode": mode, "m0": "0", "m1": "0", "m2": "0",
        "top_k": 50, "fusion": "union", "cap": 0, "http": not rank_only,
    }
    if output.exists():
        raise FileExistsError(output)
    # Intermediate reports from the reusable runner include business text. Only
    # publish the selected metrics and code-level decisions, with no input text.
    save = replay.save_json

    def save_redacted(path, value):
        value["profile"] = profile
        value["source_sha256"] = source_hashes
        if any(replay.sha(ROOT / path) != digest for path, digest in source_hashes.items()):
            raise RuntimeError("Source changed during measurement")
        value["exit_code"] = 0
        value["failed_rows"] = value["skipped_rows"] = 0
        ids = [it["id"] for it in replay.read_json(replay.GOLDEN)["items"]
               if it["id"] in split_ids]
        value["recall_order_sha256"] = hashlib.sha256("".join(recall_digests).encode()).hexdigest()
        value["scores"] = {"histogram_lower_bounds": dict(scores), **saturation}
        value["top2_gap"] = replay.dist_ms([r["top2_gap"] for r in ranked if r["top2_gap"] is not None])
        value["top2_exact_ties"] = sum(r["top2_gap"] == 0 for r in ranked)
        for split in value["splits"].values():
            split.pop("miss_analysis")
        for row in value["per_sample_ans"].values():
            row.pop("name")
        value["rows"] = {
            gid: {**row, "match": singles.get(gid), "upload": uploads.get(gid)}
            for gid, row in zip(ids, ranked, strict=True)
        }
        save(path, value)

    governance = replay.read_json(ROOT / "tests" / "fixtures" / "golden_governance_v1.json")["samples"]
    split_ids = {gid for gid, entry in governance.items() if entry["split"] in ("train", "dev")}
    with patch.multiple(replay, diagnostic_rows=diagnostics, send_match=match, send_upload=upload,
                        save_json=save_redacted, phase=trace_phase):
        replay.run_single_worker(spec, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("keyword", "vector"), required=True)
    parser.add_argument("--profile", choices=("legacy", "dedup", "calibrated"), required=True)
    parser.add_argument("--rank-only", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.mode, args.profile, args.out, rank_only=args.rank_only)
