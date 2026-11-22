"""Run the frozen default full benchmark once per mode, and retest M2 ON latency on Train/Dev."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
V017 = ROOT / "doc/V0.1.7/evidence"
sys.path[:0] = [str(ROOT), str(V017), str(ROOT / "doc/V0.1.8/evidence")]
from m0_r01_benchmark import (
    block_external_network, checked_index, configure_actor, diagnostic_rows, hardware, phase,
    send_match, send_upload, stage,
)
from m0_r01_build_governance import GOLDEN, read_catalog, read_json, save_json, sha
from m0_r01_compare import with_interval
from m3_r01_dev_selection import SIGNED_BUDGETS, dist_ms, summarize_split
from m1_r01_capture import stamp

GOVERNANCE = ROOT / "tests/fixtures/golden_governance_v1.json"
SWITCHES = {
    "LISTTRANS_V018_M0_RECALL": "0", "LISTTRANS_V018_M1_RECALL": "0", "LISTTRANS_V018_M2_RECALL": "0",
    "LISTTRANS_V019_M0_SCORING": "legacy", "LISTTRANS_V019_M1_RERANK": "off",
    "LISTTRANS_V019_M2_CALIBRATION": "off", "ENABLE_SERIES_RERUN": "false",
}
RATE_KEYS = (
    "auto_accepted_coverage", "assessed_auto_accepted_accuracy", "assessed_wrong_auto_binding_rate",
    "correct_rejection_rate", "assessed_wrong_proposed_binding_rate",
)


def verify_freeze(freeze):
    for file, digest in freeze["sha256"].items():
        if sha(ROOT / file) != digest:
            raise RuntimeError(f"Frozen file changed: {file}")


def run(mode, kind, output):
    if output.exists():
        raise FileExistsError(output)
    full = kind == "final"
    if not full and mode != "vector":
        raise ValueError("Clean M2 ON latency uses the signed BGE mode")
    claim = output.with_suffix(".claim.json")
    if full and (EVIDENCE / f"m3_r01_final_{mode}.claim.json").exists() and claim != EVIDENCE / f"m3_r01_final_{mode}.claim.json":
        raise RuntimeError(f"Frozen {mode} evaluation already claimed")
    freeze_path = EVIDENCE / "m3_r01_pre_holdout_freeze.json"
    freeze = read_json(freeze_path) if full else None
    if freeze:
        verify_freeze(freeze)
        if output != EVIDENCE / f"m3_r01_final_{mode}.json":
            raise ValueError("Use the canonical one-time result path")
    data = read_json(GOVERNANCE)
    labels = data["samples"]
    ids = {split: sorted(gid for gid, row in labels.items() if row["split"] == split)
           for split in ("train", "dev", "test")}
    if [len(rows) for rows in ids.values()] != [842, 218, 160]:
        raise RuntimeError("Unexpected split population")
    limited = {gid for split in ("train", "dev") for gid in ids[split]}
    items = [it for it in read_json(GOLDEN)["items"] if full or it["id"] in limited]
    if len(items) != (1220 if full else 1060):
        raise RuntimeError("Frozen evaluation population mismatch")
    os.environ.update(SWITCHES)
    if not full:
        os.environ["LISTTRANS_V019_M2_CALIBRATION"] = "on"
    stages = {}
    with TemporaryDirectory(prefix=f"v019_m3_{kind}_{mode}_", ignore_cleanup_errors=True) as directory:
        root = Path(directory).resolve() / "isolated"
        frozen = stage(root, mode, read_json(V017 / "m0_r01_before.json"))
        sys.addaudithook(block_external_network)
        with phase(stages, "startup"):
            from fastapi.testclient import TestClient
            from app.server import app
            from app.vector_index import index_manager
            from app.acceptance_calibration import active_policy
            material, _ = read_catalog()
        if bool(active_policy()) != (not full):
            raise RuntimeError("Acceptance profile mismatch")
        with TestClient(app) as client:
            with phase(stages, "warmup"):
                index = checked_index(mode, index_manager, material)
                headers = configure_actor()
                warm = [it for it in items if labels[it["id"]]["split"] == "train"][:5]
                for it in warm:
                    send_match(client, headers, it)
                if full:
                    send_upload(client, headers, warm)
            if full:
                with claim.open("x", encoding="utf-8") as file:
                    json.dump({"mode": mode, "freeze_sha256": sha(freeze_path),
                               "started_at": datetime.now(timezone.utc).isoformat()}, file)
                with phase(stages, "rank"):
                    diagnostic, _ = diagnostic_rows(items, labels)
                expected_semantic = len(items) if mode == "vector" else 0
                if sum(row["semantic_in_pool"] for row in diagnostic.values()) != expected_semantic:
                    raise RuntimeError("Unexpected semantic channel coverage")
            print(f"{mode}/{kind} single", flush=True)
            with phase(stages, "single"):
                singles = {it["id"]: send_match(client, headers, it) for it in items}
            uploaded, upload_ms = {}, []
            if full:
                print(f"{mode}/{kind} upload", flush=True)
                with phase(stages, "upload"):
                    for start in range(0, len(items), 50):
                        rows, meta = send_upload(client, headers, items[start:start + 50])
                        uploaded.update(rows)
                        if meta["rows"] == 50:
                            upload_ms.append(meta["ms"])
        single = dist_ms([v["ms"] for v in singles.values()])
        batch = dist_ms(upload_ms) if full else None
        peak = round(max(v["process_peak_working_set_mib"] for v in stages.values()), 2)
        signed = SIGNED_BUDGETS[mode]
        checks = {"single_p50": single["p50_ms"] <= signed["single_p50_ms"],
                  "single_p95": single["p95_ms"] <= signed["single_p95_ms"],
                  "workset": peak <= signed["wset_mib"]}
        if full:
            checks.update(upload50_p50=batch["p50_ms"] <= signed["up50_p50_ms"],
                          upload50_p95=batch["p95_ms"] <= signed["up50_p95_ms"])
        result = {
            "mode": mode, "kind": kind, "rows": len(items), "hardware": hardware(),
            "inputs": frozen, "model_and_index": index, "latency": {"single": single, "upload50": batch},
            "phases": stages, "peak_working_set_mib": peak, "budget_checks": checks,
            "split_id_sha256": {k: stamp(v) for k, v in ids.items()}, "exit_code": 0,
        }
        if full:
            verify_freeze(freeze)
            if set(uploaded) != set(diagnostic) or set(singles) != set(diagnostic):
                raise RuntimeError("Routes did not cover the frozen population")
            result["freeze_sha256"] = sha(freeze_path)
            result["splits"] = {}
            for name, subset in (("full", items), ("test", [it for it in items if it["id"] not in limited])):
                metrics = summarize_split(subset, diagnostic, singles, uploaded, labels)
                for route in ("single_decisions", "upload_decisions", "upload_answerable_decisions",
                              "upload_negative_decisions"):
                    for key in RATE_KEYS:
                        if metrics[route][key] is not None:
                            metrics[route][key] = with_interval(metrics[route][key])
                result["splits"][name] = metrics
        save_json(output, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("keyword", "vector"), required=True)
    parser.add_argument("--kind", choices=("final", "m2-on"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    options = parser.parse_args()
    run(options.mode, options.kind, options.out.resolve())
