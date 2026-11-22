"""Replay V020-M0 OFF/ON and rule gates on the frozen Train/Dev projection."""
import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from m0_r01_data import GOVERNANCE, ROOT, V017, read_projection

V018 = ROOT / "doc/V0.1.8/evidence"
V019 = ROOT / "doc/V0.1.9/evidence"
sys.path.extend([str(V018), str(V019)])
from m0_r01_benchmark import (
    block_external_network, checked_index, configure_actor, diagnostic_rows, hardware, phase,
    send_match, send_upload, stage,
)
from m0_r01_build_governance import GOLDEN, read_catalog, read_json, save_json, sha
from m3_r01_dev_selection import SIGNED_BUDGETS, dist_ms, extract_per_sample_compact, summarize_split

SWITCHES = {
    "LISTTRANS_V018_M0_RECALL": "0", "LISTTRANS_V018_M1_RECALL": "0", "LISTTRANS_V018_M2_RECALL": "0",
    "LISTTRANS_V019_M0_SCORING": "legacy", "LISTTRANS_V019_M1_RERANK": "off",
    "LISTTRANS_V019_M2_CALIBRATION": "off", "ENABLE_SERIES_RERUN": "false",
}


def configure_arm(arm, asset):
    os.environ.update(SWITCHES)
    os.environ["LISTTRANS_V020_M0_RANKER"] = arm
    if arm == "on":
        if asset is None or not asset.is_file():
            raise ValueError("ON requires the frozen V020-M0 model")
        os.environ["LISTTRANS_V020_M0_RANKER_ASSET"] = str(asset.resolve())
        os.environ["LISTTRANS_V020_M0_RANKER_SHA256"] = sha(asset)


def benchmark(mode, arm, asset, output):
    configure_arm(arm, asset)
    items, labels = read_projection()
    subsets = {name: [item for item in items if labels[item["id"]]["split"] == name] for name in ("train", "dev")}
    stages, uploaded, upload_ms = {}, {}, []
    with TemporaryDirectory(prefix=f"listtrans_v020_{mode}_{arm}_", ignore_cleanup_errors=True) as directory:
        frozen = stage(Path(directory) / "isolated", mode, read_json(V017 / "m0_r01_before.json"))
        sys.addaudithook(block_external_network)
        with phase(stages, "startup"):
            from fastapi.testclient import TestClient
            from app.feedback_ranker import active_ranker
            from app.server import app
            from app.vector_index import index_manager
            materials, _ = read_catalog()
        if bool(active_ranker()) != (arm == "on"):
            raise RuntimeError("V020-M0 arm is not active as requested")
        with TestClient(app) as client:
            with phase(stages, "warmup"):
                index = checked_index(mode, index_manager, materials)
                headers = configure_actor()
                warm = subsets["train"][:5]
                for item in warm:
                    send_match(client, headers, item)
                send_upload(client, headers, warm)
            with phase(stages, "rank"):
                diagnostic, _ = diagnostic_rows(items, labels)
            expected_semantic = len(items) if mode == "vector" else 0
            if sum(row["semantic_in_pool"] for row in diagnostic.values()) != expected_semantic:
                raise RuntimeError("semantic channel coverage differs from the locked mode")
            print(f"{mode} {arm}: single route", flush=True)
            with phase(stages, "single"):
                singles = {item["id"]: send_match(client, headers, item) for item in items}
            print(f"{mode} {arm}: 50-row upload route", flush=True)
            with phase(stages, "upload"):
                for start in range(0, len(items), 50):
                    rows, meta = send_upload(client, headers, items[start:start + 50])
                    uploaded.update(rows)
                    if meta["rows"] == 50:
                        upload_ms.append(meta["ms"])
    if set(diagnostic) != set(uploaded) or set(diagnostic) != set(singles):
        raise RuntimeError("rank, single, and upload records differ in population")
    peak = round(max(stage_result["process_peak_working_set_mib"] for stage_result in stages.values()), 2)
    single, batch = dist_ms([row["ms"] for row in singles.values()]), dist_ms(upload_ms)
    observed = {"single_p50_ms": single["p50_ms"], "single_p95_ms": single["p95_ms"],
                "up50_p50_ms": batch["p50_ms"], "up50_p95_ms": batch["p95_ms"], "wset_mib": peak}
    budget = SIGNED_BUDGETS[mode]
    save_json(output, {
        "mode": mode, "arm": arm, "exit_code": 0, "hardware": hardware(), "input_hashes": frozen,
        "asset_sha256": sha(asset) if asset else None, "model_index": index, "phases": stages,
        "latency": {"single": single, "upload50": batch}, "budget": budget,
        "budget_pass": {key: observed[key] <= limit for key, limit in budget.items()},
        "peak_working_set_mib": peak, "semantic_count": expected_semantic,
        "metrics": {split: summarize_split(rows, diagnostic, singles, uploaded, labels)
                    for split, rows in {**subsets, "train_dev": items}.items()},
        "paired_rank": {gid: {key: row[key] for key in ("split", "domain", "rank", "recall_pos")}
                        for gid, row in extract_per_sample_compact(items, diagnostic, labels).items()},
        "upload_decisions": {gid: {key: row[key] for key in ("decision_status", "bindings", "top_code")}
                             for gid, row in uploaded.items()},
    })


def rule_gate(arm, asset, output):
    configure_arm(arm, asset)
    import m0_r01_candidate_gate as historical

    items, labels = read_projection()
    with TemporaryDirectory(prefix="listtrans_v020_gate_projection_") as directory:
        projected = Path(directory) / "train_dev.json"
        save_json(projected, {"meta": {"version": "v1.0-train-dev", "total": len(items)}, "items": items})
        raw_output = Path(directory) / "raw.json"

        def projection_read(path):
            return {"samples": labels} if path == GOVERNANCE else read_json(path)

        with patch.object(historical, "GOLDEN", projected), patch.object(historical, "read_json", projection_read):
            exit_code = historical.run("legacy", raw_output)
        result = read_json(raw_output)
    differences = {ref: {rule: len(rows) for rule, rows in rules.items()}
                   for ref, rules in result["comparisons"].items()}
    if arm == "off" and (sum(map(len, differences.values())) != 18
                         or any(n for rules in differences.values() for n in rules.values())):
        exit_code = 1
    binding_checks = result["rules"]["beneficial_constrained"]["binding_checks_count"]
    binding_degraded = result["rules"]["beneficial_constrained"]["binding_degraded_count"]
    if not binding_checks or binding_degraded:
        exit_code = 1
    rules = {key: {name: datum for name, datum in value.items() if name != "score_comparisons"}
             for key, value in result["rules"].items()}
    save_json(output, {
        "arm": arm, "exit_code": exit_code, "subset_rows": result["subset_rows"], "rules": rules,
        "historical_score_delta_counts": differences, "protected_probes": result["probes"],
        "rule_safety_unchanged": result["rule_safety_unchanged"],
        "passed_protected_probes": result["probes_ok"], "projected_asset_sha256": result["subset_sha256"],
        "ranker_asset_sha256": sha(asset) if asset else None, "source_sha256": result["source_sha256"],
    })
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("off", "on"), required=True)
    parser.add_argument("--mode", choices=("keyword", "vector", "rules"), required=True)
    parser.add_argument("--asset", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    if args.mode == "rules":
        sys.exit(rule_gate(args.arm, args.asset, args.out))
    benchmark(args.mode, args.arm, args.asset, args.out)
