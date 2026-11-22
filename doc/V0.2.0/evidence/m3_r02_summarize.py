"""Verify M3 R02 evidence and record the performance comparison from raw timings."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from m3_r01_finalize import redact_home_paths
from m3_r02_replay import ARMS, DISPATCH, EVIDENCE, MODES, R01, ROOT, RUNNER, check_assets, distribution
from m3_r02_replay import read_json, sha256_file, validate_baseline, verify_frozen, write_json


def compare_mode(out, mode, steps, dispatch):
    reference = read_json(R01 / f"m3_r01_perf_{mode}.json")
    repeat = read_json(EVIDENCE / "m3_r01_repeat" / f"m3_r01_perf_{mode}.json")
    expected_order = sha256("\n".join(reference["single_input_ids"]).encode()).hexdigest()
    diagnostics = {}
    for arm, rank_n in zip(ARMS, (0, 200, 200), strict=True):
        doc = read_json(out / f"{mode}_{arm}.json")
        if (doc["order_sha256"] != expected_order or len(doc["single_ms"]) != 200
                or doc["rank_rows"] != rank_n or "test" in doc["split_counts"]
                or doc["hardware"] != reference["hardware"] or doc["input_hashes"] != reference["input_hashes"]
                or doc["model_index"] != reference["model_index"]
                or [doc["warmup_single"], doc["warmup_upload_rows"]] != [5, 5]
                or steps[f"{mode}_{arm}"]["exit_code"] != 0):
            raise RuntimeError(f"Diagnostic is not paired with the original inputs: {mode}/{arm}")
        if doc["switches"] != dispatch["default_switches_frozen"] or doc["outbound_network_attempts"] or doc["llm_transport_attempts"]:
            raise RuntimeError(f"Offline conditions did not hold: {mode}/{arm}")
        diagnostics[arm] = distribution(doc["single_ms"])

    signed = read_json(out / f"{mode}_signed.json")
    claim = read_json(out / f"{mode}_signed.claim.json")
    if (claim["freeze_sha256"] != sha256_file(out / "freeze.json") or claim["mode"] != mode
            or signed["split_counts"] != {"train": 842, "dev": 218, "test": 160}
            or signed["rank_rows"] != 1220 or len(signed["single_ms"]) != 1220
            or signed["semantic_rows"] != (1220 if mode == "vector" else 0)
            or [item["rows"] for item in signed["upload_batches"]] != [50] * 24 + [20]
            or [signed["warmup_single"], signed["warmup_upload_rows"]] != [5, 5]
            or signed["hardware"] != reference["hardware"] or signed["input_hashes"] != reference["input_hashes"]
            or signed["model_index"] != reference["model_index"] or signed["switches"] != dispatch["default_switches_frozen"]
            or signed["outbound_network_attempts"] or signed["llm_transport_attempts"]):
        raise RuntimeError(f"Incomplete signed workload or offline settings: {mode}")
    single = distribution(signed["single_ms"])
    upload = distribution([item["ms"] for item in signed["upload_batches"] if item["rows"] == 50])
    observed = {"single_p50_ms": single["p50_ms"], "single_p95_ms": single["p95_ms"],
                "up50_p50_ms": upload["p50_ms"], "up50_p95_ms": upload["p95_ms"],
                "wset_mib": max(stage["process_peak_working_set_mib"] for stage in signed["phases"].values())}
    caps = dispatch["signed_budgets"][mode]
    passed = {metric: value <= caps[metric] for metric, value in observed.items()}
    exit_code = 0 if all(passed.values()) else 2
    if observed != signed["observed"] or passed != signed["budget_pass"] or steps[f"{mode}_signed"]["exit_code"] != exit_code:
        raise RuntimeError(f"Signed budget comparison disagrees with raw data: {mode}")
    return {"r01_single": [distribution(report["single_ms"]) for report in (reference, repeat)],
            "r02_same_sample": diagnostics, "r02_signed": {"observed": observed, "caps": caps, "passed": passed,
                    "order_sha256": signed["order_sha256"], "elapsed_s": steps[f"{mode}_signed"]["elapsed_s"],
                    "tail20_ms": signed["upload_batches"][-1]["ms"], "exit_code": exit_code}}


def compare(out, output):
    dispatch = read_json(DISPATCH)
    validate_baseline(dispatch)
    verify_frozen(out)
    checkpoints = {}
    for bound in ("before", "after"):
        path = out / f"isolation_{bound}.json"
        result = read_json(path)
        if result["verdict"] != "PASS" or result["exit_code"] != 0:
            raise RuntimeError(f"Isolation failed before or after replay: {bound}")
        checkpoints[bound] = {key: check_assets(dispatch, result[f"business_snapshot_{key}"]) for key in ("before", "after")}
    entries = (out / "m3_r01_execution.jsonl").read_text(encoding="utf-8").splitlines()
    steps = {row["step"]: row for row in map(json.loads, entries)}
    order = ["isolation_before", *(f"{mode}_{arm}" for mode in MODES for arm in ARMS),
             *(f"{mode}_signed" for mode in MODES), "isolation_after"]
    if list(steps) != order or len(steps) != len(entries):
        raise RuntimeError("The full replay was not executed in the expected serial order")
    modes = {mode: compare_mode(out, mode, steps, dispatch) for mode in MODES}
    if len({mode["r02_signed"]["order_sha256"] for mode in modes.values()}) != 1:
        raise RuntimeError("Signed input order differed across modes")
    write_json(output, {"head": dispatch["head"], "r02_dispatch_sha256": sha256_file(DISPATCH),
                        "r01_source_and_retained_deliveries_match": True, "isolation": checkpoints, "modes": modes})


def inventory(out, output):
    copies = [path for path in out.iterdir() if path.is_file()]
    redact_home_paths(copies)
    files = [*copies, RUNNER, Path(__file__).resolve(), EVIDENCE / "m3_r02_comparison.json",
             EVIDENCE / "m3_r02_reproduce.md", EVIDENCE.parent / "02_任务台账.md",
             EVIDENCE.parent / "rounds/V020-M3_R02_开发小结.md"]
    hashes = {path.relative_to(ROOT).as_posix(): {"sha256": sha256_file(path), "bytes": path.stat().st_size}
              for path in sorted(files)}
    write_json(output, {"file_count": len(hashes), "files": hashes, "dispatch_sha256": sha256_file(DISPATCH)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--delivery", action="store_true")
    args = parser.parse_args()
    summarize = inventory if args.delivery else compare
    summarize(args.results.resolve(), args.out.resolve())
