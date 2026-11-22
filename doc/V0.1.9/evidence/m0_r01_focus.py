"""Trace query extraction and candidate scores for the frozen V018 regression cases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from m0_r01_v017_reference import (
    benchmark, EVIDENCE, GOLDEN, read_json, RestoredSources, ROOT, save_json, stage_reference, V017,
)


def run(version, mode, output):
    if output.exists():
        raise FileExistsError(output)
    report = read_json(EVIDENCE / "m0_r01_comparison.json")
    focus = "decoration_hit1" if mode == "keyword" else "strong_current_hit3"
    affected = report["modes"][mode]["v017_to_v018_attribution"]["per_sample_changes"]
    ids = {gid for gid, case in affected.items() if focus in case["focus"]}
    governance = read_json(ROOT / "tests/fixtures/golden_governance_v1.json")["samples"]
    items = [row for row in read_json(GOLDEN)["items"]
             if row["id"] in ids and governance[row["id"]]["split"] in ("train", "dev")]
    if len(items) != len(ids) or not items:
        raise RuntimeError("Invalid focus cohort")
    os.environ.update({
        "LISTTRANS_V019_M0_SCORING": "legacy", "LISTTRANS_V018_M0_RECALL": "0",
        "LISTTRANS_V018_M1_RECALL": "0", "LISTTRANS_V018_M2_RECALL": "0", "ENABLE_SERIES_RERUN": "false",
    })
    with tempfile.TemporaryDirectory(prefix="v019_m0_focus_", ignore_cleanup_errors=True) as temp:
        root = Path(temp).resolve()
        if version == "v017":
            paths, _ = stage_reference(root / "source")
            sys.meta_path.insert(0, RestoredSources(paths))
        benchmark.stage(root / "runtime", mode, read_json(V017 / "m0_r01_before.json"))
        sys.addaudithook(benchmark.block_external_network)

        from fastapi.testclient import TestClient
        from app.sandbox_evaluator import evaluate
        from app.server import app
        from app.vector_index import index_manager

        with TestClient(app):
            materials, _ = benchmark.read_catalog()
            benchmark.checked_index(mode, index_manager, materials)
            # Use the same equivalent-target expansion as the full replay.
            expanded = [
                {**r, "target_codes": list(dict.fromkeys(r["target_codes"] + [
                    code for group in governance[r["id"]]["target_alternatives"].values() for code in group
                ]))}
                for r in items
            ]
            rows, _ = evaluate(expanded)
        captured = {}
        for row in rows:
            diag = row["diagnostics"]
            parsed = diag["query_parsed_spec"]
            captured[row["id"]] = {
                "domain": parsed.get("domain"), "model_present": bool(parsed.get("model")),
                "attributes": {
                    key: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    for key, value in parsed.get("attrs", {}).items()
                },
                "target_candidates": [{
                    key: target[key] for key in ("code", "full_rank", "T", "reasons", "recall_sources", "max_score")
                } for target in diag["targets"]],
                "top_candidates": [{
                    key: cand[key] for key in ("code", "score", "recall_sources", "match_reasons", "max_score")
                } for cand in diag["candidates"][:3]],
            }
        if captured.keys() != ids:
            raise RuntimeError("Focus cohort incomplete")
        save_json(output, {"version": version, "mode": mode, "exit_code": 0, "cases": captured})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", choices=("v017", "v018"), required=True)
    parser.add_argument("--mode", choices=("keyword", "vector"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.version, args.mode, args.out)
