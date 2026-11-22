"""Isolated V021 upload and latency replay, restricted to the Train/Dev projection."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "doc/V0.2.0/evidence"))
from m0_r01_replay import (
    ROOT, V017, SIGNED_BUDGETS, block_external_network, checked_index, configure_actor,
    configure_arm, dist_ms, hardware, phase, read_catalog, read_json, read_projection,
    send_match, stage,
)
from m0_r01_benchmark import make_workbook, response_json


def upload(client, headers, items):
    workbook = make_workbook(items)
    start = time.perf_counter()
    job = response_json(client.post("/api/upload", headers=headers, files={
        "file": ("v021-replay.xlsx", workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    }))
    state = response_json(client.get("/api/upload/status", headers=headers, params={"task_id": job["task_id"]}))
    ms = (time.perf_counter() - start) * 1000
    if state["status"] != "completed":
        raise RuntimeError(f"staged upload failed: {state.get('error')}")
    rows = state["result"]
    assert len(rows) == len(items) and {row["original_code"] for row in rows} == {item["id"] for item in items}
    assert all(row["initial_snapshot_id"] for row in rows)
    summary = state["initial_match_summary"]
    assert summary["known_rows"] == len(items) == sum(summary["tiers"].values())
    assert summary["actual_prefilled"] == sum(row["initial_match"]["actual_prefilled"] for row in rows)
    return rows, ms


def run(mode, out, *, probes_only=False):
    configure_arm("off", None)
    items, labels = read_projection()
    stages, rows, latency, export_checks = {}, [], [], {}
    safety_ids = ("GT-0674", "GT-0677", "GT-0678")
    probe_ids = ("GT-0300", *safety_ids)
    batches = [items[start:start + 50] for start in range(0, len(items), 50)]
    if probes_only:
        batches = [batch for batch in batches if any(item["id"] in probe_ids for item in batch)]
    with TemporaryDirectory(prefix=f"v021_{mode}_", ignore_cleanup_errors=True) as root:
        inputs = stage(Path(root) / "isolated", mode, read_json(V017 / "m0_r01_before.json"))
        sys.addaudithook(block_external_network)
        with phase(stages, "startup"):
            from fastapi import HTTPException
            from fastapi.testclient import TestClient
            from app.auth_manager import get_user_by_username
            from app.match_assessment import summarize_initial_matches
            from app.server import _gate_export_row, app
            from app.vector_index import index_manager
            materials, _ = read_catalog()
        with TestClient(app) as client:
            with phase(stages, "warmup"):
                index = checked_index(mode, index_manager, materials)
                headers = configure_actor()
                for item in items[:5]:
                    send_match(client, headers, item)
                upload(client, headers, items[:5])
            with phase(stages, "single"):
                singles = [] if probes_only else [send_match(client, headers, item)["ms"] for item in items]
            with phase(stages, "upload"):
                for batch in batches:
                    observed, ms = upload(client, headers, batch)
                    rows.extend(observed)
                    if len(batch) == 50:
                        latency.append(ms)
                    print(f"{mode}: {len(rows)}/{len(items)}", flush=True)
            actor = get_user_by_username("benchmark")
            for row in rows:
                if row["original_code"] in safety_ids:
                    try:
                        _gate_export_row(row, actor, "v021-replay.xlsx")
                    except HTTPException as exc:
                        export_checks[row["original_code"]] = exc.status_code
                    else:
                        export_checks[row["original_code"]] = 200
    summary = summarize_initial_matches(rows)
    probes = {row["original_code"]: row["initial_match"] for row in rows if row["original_code"] in probe_ids}
    safety_pass = all(
        probes[gid]["tier"] == "C" and probes[gid]["subtype"] == "safety_missing"
        and not probes[gid]["effective_bindings"] and export_checks.get(gid) == 422 for gid in safety_ids
    )
    power_pass = any(
        entry["field"] == "功率" and entry["missing_side"] == "candidate" and entry["requested_unverified"]
        for entry in probes["GT-0300"]["field_comparisons"]
    )
    probe_report = {
        "probes": probes, "legacy_export_checks": export_checks,
        "safety_probes_pass": safety_pass, "power_evidence_pass": power_pass,
    }
    if probes_only:
        out.write_text(json.dumps({"mode": mode, "source_rows": len(rows), **probe_report},
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return int(not (safety_pass and power_pass))

    single, batch = dist_ms(singles), dist_ms(latency)
    observed = {"single_p50_ms": single["p50_ms"], "single_p95_ms": single["p95_ms"],
                "up50_p50_ms": batch["p50_ms"], "up50_p95_ms": batch["p95_ms"],
                "wset_mib": max(record["process_peak_working_set_mib"] for record in stages.values())}
    history = {"keyword": {"actual_prefilled": 510, "A": 34, "B": 205, "C": 344, "D": 477,
                           "low_score_prefill": 271, "safety_missing": 73},
               "vector": {"actual_prefilled": 694, "A": 36, "B": 440, "C": 303, "D": 281,
                          "low_score_prefill": 218, "safety_missing": 85}}[mode]
    current = {"actual_prefilled": summary["actual_prefilled"], **summary["tiers"], **summary["c_subtypes"]}
    report = {
        "mode": mode, "splits": Counter(label["split"] for label in labels.values()),
        "hardware": hardware(), "inputs": inputs, "index": index, "phases": stages,
        "latency": {"single": single, "upload50": batch}, "observed_budget": observed,
        "signed_budget": SIGNED_BUDGETS[mode],
        "budget_pass": {k: observed[k] <= limit for k, limit in SIGNED_BUDGETS[mode].items()},
        "summary": summary, "historical_r2": history,
        "r2_deltas": {key: current[key] - value for key, value in history.items()},
        "reason_counts": Counter(reason for row in rows for reason in row["initial_match"]["reason_codes"]),
        **probe_report,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return int(not (safety_pass and power_pass and all(report["budget_pass"].values())))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("keyword", "vector"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--probes-only", action="store_true")
    args = parser.parse_args()
    sys.exit(run(args.mode, args.out, probes_only=args.probes_only))
