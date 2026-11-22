"""Run the signed M3 workload and paired diagnostics in isolated Windows processes."""
import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from m3_r01_run import EVIDENCE, ROOT, check_assets, collect_snapshot, execute, offline_env, read_json, sha256_file, write_json

V017 = ROOT / "doc/V0.1.7/evidence"
sys.path.insert(0, str(V017))
import m0_r01_benchmark as bench

DISPATCH = EVIDENCE / "m3_r02_dispatch_baseline.json"
R01 = EVIDENCE / "m3_r01_followup"
GOVERNANCE = ROOT / "tests/fixtures/golden_governance_v1.json"
SIGNED = V017 / "指标目标与性能预算_统筹签认.md"
RUNNER = Path(__file__).resolve()
MODES = ("keyword", "vector")
ARMS = ("r01_warmup", "rank_warmup", "v019_rank_warmup")


def validate_baseline(dispatch):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != dispatch["head"]:
        raise RuntimeError("HEAD differs from the R02 dispatch")
    for key, expected_count in (("effective_baseline_sha256", 200), ("r01_retained_delivery_sha256", 45)):
        hashes = dispatch[key]
        drift = [name for name, expected in hashes.items() if sha256_file(ROOT / name) != expected]
        if len(hashes) != expected_count or drift:
            raise RuntimeError(f"{key}: scope or hash drift: {drift}")
    if sha256_file(SIGNED) != dispatch["signed_budget_sha256"]:
        raise RuntimeError("Signed budget changed")
    if sha256_file(Path(bench.__file__)) != dispatch["signed_replay_baseline_sha256"]:
        raise RuntimeError("Signed workload changed")
    audit = EVIDENCE.parent / "rounds/V020-M3_R01_审核小结.md"
    if sha256_file(audit) != dispatch["r01_audit_sha256"]:
        raise RuntimeError("R01 audit changed")
    return check_assets(dispatch, collect_snapshot())


def restore_v019(native, dispatch):
    record = dispatch["local_recovery_archive"]
    path = ROOT / record["path"]
    if sha256_file(path) != record["sha256"]:
        raise RuntimeError("V0.1.9 source archive changed")
    baseline = read_json(EVIDENCE / "m0_r01_dispatch_baseline.json")["effective_baseline_sha256"]
    source = native / "v019"
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != record["entry_count"] or len(set(names)) != len(names):
            raise RuntimeError("V0.1.9 archive entry count differs from the dispatch")
        results = {}
        for name in names:
            dst = (source / name).resolve()
            content = archive.read(name)
            digest = sha256(content).hexdigest()
            if not dst.is_relative_to(source) or digest != baseline[name]:
                raise RuntimeError("V0.1.9 archive entry fails its signed path or digest")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(content)
            results[name] = digest
    for name, digest in dispatch["effective_baseline_sha256"].items():
        if name.startswith("model_offline/bge-small-zh-v1.5/"):
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
            if sha256_file(target) != digest:
                raise RuntimeError("Archived code is using the wrong offline model bytes")
    return source, results


def diagnostic_inputs(native):
    from m0_r01_data import read_projection

    items, labels = read_projection()
    by_id = {item["id"]: item for item in items}
    keyword, vector = (read_json(R01 / f"m3_r01_perf_{mode}.json") for mode in MODES)
    keys = ("single_input_ids", "warmup_ids")
    if any(keyword[key] != vector[key] for key in keys):
        raise RuntimeError("R01 diagnostic inputs differ between modes")
    input_ids, warm_ids = (keyword[key] for key in keys)
    if len(set(input_ids)) != 200 or len(set(warm_ids)) != 5 or set(input_ids) & set(warm_ids):
        raise RuntimeError("R01 sample counts or disjoint warmup differ")
    diag = native / "train_dev_inputs.json"
    write_json(diag, {"items": [by_id[gid] for gid in input_ids], "warm": [by_id[gid] for gid in warm_ids],
                      "labels": {gid: labels[gid] for gid in input_ids}})
    return diag


def measure_diagnostic(args, case, source):
    frozen = bench.stage(args.root, args.mode, read_json(V017 / "m0_r01_before.json"))
    phases = {}
    with bench.phase(phases, "startup_prepare"):
        from fastapi.testclient import TestClient
        from app import server, llm_client
        from app.vector_index import index_manager

        if not Path(server.__file__).resolve().is_relative_to(source / "app"):
            raise RuntimeError("Diagnostic loaded a different implementation")
        if llm_client._get_llm_client().is_enabled():
            raise RuntimeError("LLM must remain OFF")
        materials, _ = bench.read_catalog()
    with TestClient(server.app) as client:
        with bench.phase(phases, "warmup"):
            index = bench.checked_index(args.mode, index_manager, materials)
            headers = bench.configure_actor()
            for item in case["warm"]:
                bench.send_match(client, headers, item)
            bench.send_upload(client, headers, case["warm"])
        rank_count = 0
        if args.arm != "r01_warmup":
            with bench.phase(phases, "rank_warmup"):
                rank, _ = bench.diagnostic_rows(case["items"], case["labels"])
            rank_count = len(rank)
        with bench.phase(phases, "single_http"):
            timings = [bench.send_match(client, headers, item)["ms"] for item in case["items"]]
    return {"model_index": index, "input_hashes": frozen, "phases": phases, "rank_rows": rank_count,
            "single_ms": timings, "upload_batches": [], "warmup_single": len(case["warm"]), "warmup_upload_rows": 5,
            "order_sha256": sha256("\n".join(item["id"] for item in case["items"]).encode()).hexdigest(),
            "split_counts": dict(Counter(label["split"] for label in case["labels"].values()))}


def measure_signed(args):
    # The signed replay retains diagnostic and response state through every measured phase.
    # Its application outputs stay in memory. Child application logs stay in the private temp root.
    with (args.root.parent / f"signed_{args.mode}_private.log").open("x", encoding="utf-8") as log:
        with redirect_stdout(log), redirect_stderr(log):
            raw = bench.run(SimpleNamespace(root=args.root, mode=args.mode, governance=GOVERNANCE, batch_size=50))
    single = [row["ms"] for row in raw["single"].values()]
    uploads = [{"rows": row["rows"], "ms": row["ms"]} for row in raw["uploads"]]
    if len(single) != 1220 or len(raw["diagnostics"]) != 1220 or len(raw["batch"]) != 1220:
        raise RuntimeError("Signed replay lost work rows")
    if [batch["rows"] for batch in uploads] != [50] * 24 + [20]:
        raise RuntimeError("Signed upload batch sequence changed")
    return {"model_index": raw["index"], "input_hashes": raw["input_hashes"], "phases": raw["phases"],
            "rank_rows": len(raw["diagnostics"]), "semantic_rows": raw["semantic_rows"], "single_ms": single,
            "upload_batches": uploads, "warmup_single": 5, "warmup_upload_rows": 5,
            "order_sha256": sha256("\n".join(raw["single"]).encode()).hexdigest(),
            "split_counts": dict(Counter(row["split"] for row in read_json(GOVERNANCE)["samples"].values()))}


def distribution(values):
    values = sorted(values)
    return {"n": len(values), "p50_ms": values[math.ceil(len(values) * .50) - 1],
            "p95_ms": values[math.ceil(len(values) * .95) - 1]}


def worker(args):
    dispatch = read_json(DISPATCH)
    if args.root.resolve().is_relative_to(ROOT):
        raise RuntimeError("Use a fresh native root outside the project")
    os.environ.update(dispatch["default_switches_frozen"])
    source = args.source.resolve()
    sys.path[:] = [str(source), *[p for p in sys.path if p and Path(p).resolve() != ROOT]]
    outbound = provider = 0

    def network_guard(event, params):
        nonlocal outbound
        try:
            bench.block_external_network(event, params)
        except RuntimeError:
            outbound += 1
            raise

    def blocked_provider(*unused, **kwargs):
        nonlocal provider
        provider += 1
        raise RuntimeError("Provider dispatch blocked by the offline replay")

    sys.addaudithook(network_guard)
    from openai.resources.chat.completions import AsyncCompletions, Completions

    start_cpu = bench.psutil.cpu_percent(interval=.5)
    with (patch.object(Completions, "create", blocked_provider),
          patch.object(AsyncCompletions, "create", blocked_provider)):
        if args.arm == "signed":
            if source != ROOT or verify_frozen(args.out.parent)["script_sha256"] != sha256_file(RUNNER):
                raise RuntimeError("Signed replay is not bound to the candidate freeze")
            claim = args.out.with_suffix(".claim.json")
            write_json(claim, {"mode": args.mode, "freeze_sha256": sha256_file(args.out.parent / "freeze.json")})
            print(f"{args.mode}: signed full replay claimed", flush=True)
            result = measure_signed(args)
        else:
            result = measure_diagnostic(args, read_json(args.inputs), source)
    single = distribution(result["single_ms"])
    observed = {"single_p50_ms": single["p50_ms"], "single_p95_ms": single["p95_ms"]}
    if args.arm == "signed":
        uploads = distribution([batch["ms"] for batch in result["upload_batches"] if batch["rows"] == 50])
        observed.update(up50_p50_ms=uploads["p50_ms"], up50_p95_ms=uploads["p95_ms"],
                        wset_mib=max(stage["process_peak_working_set_mib"] for stage in result["phases"].values()))
        if args.mode == "vector" and result["model_index"] != {"enabled": True, "dimension": 512, "rows": 26863, "faiss": False}:
            raise RuntimeError("Signed vector index did not use the frozen NumPy generation")
    caps = {key: dispatch["signed_budgets"][args.mode][key] for key in observed}
    result.update(arm=args.arm, mode=args.mode, observed=observed, caps=caps, switches=dispatch["default_switches_frozen"],
                  budget_pass={key: value <= caps[key] for key, value in observed.items()}, hardware=bench.hardware(),
                  cpu_percent_before=start_cpu, cpu_percent_after=bench.psutil.cpu_percent(interval=.5),
                  outbound_network_attempts=outbound, llm_transport_attempts=provider)
    write_json(args.out, result)
    print(args.mode, args.arm, observed, result["budget_pass"], flush=True)
    if outbound or provider:
        return 1
    return 2 if args.arm == "signed" and not all(result["budget_pass"].values()) else 0


def isolation(out, env, dispatch, bound):
    path = out / f"isolation_{bound}.json"
    if execute(out, f"isolation_{bound}", ["tests/verify_isolation.py", "--out", str(path)], env):
        raise RuntimeError(f"Isolation probe {bound} failed")
    snapshots = read_json(path)
    for point in ("before", "after"):
        check_assets(dispatch, snapshots[f"business_snapshot_{point}"])


def freeze(out, dispatch):
    targets = [RUNNER, DISPATCH, SIGNED, Path(bench.__file__), V017 / "m0_r01_before.json",
               V017 / "m0_r01_build_governance.py", GOVERNANCE, bench.GOLDEN, EVIDENCE / "m3_r01_run.py",
               ROOT / "doc/V0.2.0/rounds/V020-M3_R02_统筹下发.md"]
    settings = {"mode_order": list(MODES), "batch_rows": 50, "warmup_single": 5, "warmup_upload_rows": 5,
                "rank_rows": 1220, "single_rows": 1220, "full_uploads": 24, "tail_rows": 20,
                "nearest_rank": True, "default_switches": dispatch["default_switches_frozen"]}
    write_json(out / "freeze.json", {"head": dispatch["head"], "script_sha256": sha256_file(RUNNER),
               "file_sha256": {p.relative_to(ROOT).as_posix(): sha256_file(p) for p in targets},
               "settings": settings, "frozen_at": datetime.now(timezone(timedelta(hours=8))).isoformat()})


def verify_frozen(out):
    lock = read_json(out / "freeze.json")
    if any(sha256_file(ROOT / name) != expected for name, expected in lock["file_sha256"].items()):
        raise RuntimeError("Frozen scripts or inputs changed")
    return lock


def launch(args):
    dispatch = read_json(DISPATCH)
    baseline = validate_baseline(dispatch)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    native = Path(tempfile.mkdtemp(prefix="listtrans_v020_m3_r02_")).resolve()
    if native.is_relative_to(ROOT):
        raise RuntimeError("Use a native temp directory outside the workspace")
    env = offline_env(dispatch, native)
    for key in ("SECRET_KEY", "ADMIN_PASSWORD", "ADMIN_USERNAME"):
        env.pop(key, None)
    env["PIP_NO_INDEX"] = "1"
    inputs = diagnostic_inputs(native)
    archived, sources = restore_v019(native, dispatch)
    write_json(out / "preflight.json", {"head": dispatch["head"], "baseline_count": 200, "r01_deliveries": 45,
               "diagnostic_input_sha256": sha256_file(inputs), "restored_archive_sha256": sources, **baseline})
    isolation(out, env, dispatch, "before")

    def invoke(mode, arm):
        label = f"{mode}_{arm}"
        source = archived if arm == "v019_rank_warmup" else ROOT
        return execute(out, label, [str(RUNNER), "--worker", "--mode", mode, "--arm", arm,
                       "--root", str(native / label), "--source", str(source), "--inputs", str(inputs),
                       "--out", str(out / f"{label}.json")], env)

    try:
        for mode in MODES:
            for arm in ARMS:
                if invoke(mode, arm):
                    raise RuntimeError(f"Diagnostic failed: {mode}/{arm}")
        validate_baseline(dispatch)
        freeze(out, dispatch)
        codes = {mode: invoke(mode, "signed") for mode in MODES}
        verify_frozen(out)
    finally:
        isolation(out, env, dispatch, "after")
        validate_baseline(dispatch)
    return 1 if any(code not in (0, 2) for code in codes.values()) else 2 if any(codes.values()) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=MODES, help=argparse.SUPPRESS)
    parser.add_argument("--arm", choices=(*ARMS, "signed"), help=argparse.SUPPRESS)
    for option in ("--root", "--source", "--inputs"):
        parser.add_argument(option, type=Path, help=argparse.SUPPRESS)
    parsed = parser.parse_args()
    raise SystemExit(worker(parsed) if parsed.worker else launch(parsed))
