"""Reproduce the serial V020-M3 gates without writing to runtime assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
from verify_isolation import collect_snapshot, diff_snapshots, sha256_file

EDITABLE = ("README.md", "deploy_guide.md", "doc/V0.2.0/02_任务台账.md")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def check_baseline(dispatch, snapshot_root):
    baseline = dispatch["effective_baseline_sha256"]
    actual_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    differences = [name for name, digest in baseline.items() if sha256_file(ROOT / name) != digest]
    if actual_head != dispatch["head"] or len(baseline) != 200 or differences:
        raise RuntimeError(f"Effective baseline failed: HEAD={actual_head}, drift={differences}")
    if sha256_file(EVIDENCE / "m2_r01_coord_release.json") != dispatch["m2_coord_release_sha256"]:
        raise RuntimeError("M2 release evidence drifted")
    archive = dispatch["local_recovery_archive"]
    archive_path = ROOT / archive["path"]
    if sha256_file(archive_path) != archive["sha256"]:
        raise RuntimeError("Recovery archive digest mismatch")
    original = read_json(EVIDENCE / "m0_r01_dispatch_baseline.json")["effective_baseline_sha256"]
    with zipfile.ZipFile(archive_path) as source:
        names = source.namelist()
        if (len(names) != 72 or len(set(names)) != 72
                or any(hashlib.sha256(source.read(name)).hexdigest() != original[name] for name in names)):
            raise RuntimeError("Recovery archive entry mismatch")
    for name in EDITABLE:
        target = snapshot_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return {"head": actual_head, "effective_baseline_count": len(baseline),
            "effective_baseline_drift": differences, "archive_entries_verified": len(names),
            "editable_original_sha256": {name: sha256_file(snapshot_root / name) for name in EDITABLE},
            **check_assets(dispatch, collect_snapshot())}


def check_assets(dispatch, snapshot):
    reference = dispatch["business_snapshot_sha256"]
    count = len(reference["files"]) + sum(len(tree["files"]) for tree in reference["trees"].values())
    differences = diff_snapshots(reference, snapshot)
    rules = read_json(ROOT / "config/auto_binding_rules.json")
    if count != 35 or count != dispatch["business_snapshot_asset_count"] or differences or rules != []:
        raise RuntimeError(f"Business baseline failed: count={count}, drift={differences}")
    return {"business_assets": count, "business_differences": differences, "auto_binding_rules": rules}


def check_rules(current):
    previous = read_json(EVIDENCE / "m2_r01_rule_gate.json")
    deltas = current["historical_score_delta_counts"]
    values = [value for group in deltas.values() for value in group.values()]
    safety = current["rule_safety_unchanged"]
    constrained = current["rules"]["beneficial_constrained"]
    protected = current["protected_probes"]
    if (current["arm"] != "off" or current["exit_code"] or current["subset_rows"] != 1060
            or len(values) != 18 or any(values) or deltas != previous["historical_score_delta_counts"]
            or len(safety) != 5 or not all(safety.values()) or safety != previous["rule_safety_unchanged"]
            or not current["passed_protected_probes"] or protected != previous["protected_probes"]
            or set(protected) != {"GT-0674", "GT-0677", "GT-0678"}
            or constrained["binding_checks_count"] != 40 or constrained["binding_degraded_count"]
            or not constrained["safety_passed"] or not constrained["guard_passed"]):
        raise RuntimeError("OFF rules gate differs from the released reference")


def execute(out, label, options, env):
    cmd = [sys.executable, "-B", "-X", "utf8", *options]
    t0 = time.perf_counter()
    with (out / f"m3_r01_{label}.log").open("xb") as log:
        result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    entry = {"step": label, "command": cmd, "exit_code": result.returncode,
             "elapsed_s": round(time.perf_counter() - t0, 3)}
    with (out / "m3_r01_execution.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(json.dumps(entry, ensure_ascii=False), flush=True)
    return result.returncode


def offline_env(dispatch, native):
    env = {**os.environ, **dispatch["default_switches_frozen"], "TESTING": "1",
           "AUTH_DEV_MODE": "false", "PYTHON_DOTENV_DISABLED": "1", "PYTHONUNBUFFERED": "1",
           "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
           "DASHSCOPE_API_KEY": "", "DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""}
    env.pop("PYTEST_ADDOPTS", None)
    for key in ("TEMP", "TMP", "TMPDIR"):
        env[key] = str(native)
    return env


def followup(out, native, dispatch, env, repeat_single):
    before = out / "m3_r01_isolation_before.json"
    if execute(out, "isolation_before", ["tests/verify_isolation.py", "--out", str(before)], env):
        return 1
    for point in ("before", "after"):
        check_assets(dispatch, read_json(before)[f"business_snapshot_{point}"])
    statuses = {}
    repeat_options = ["--single-only"] if repeat_single else []
    for mode in repeat_single or ("keyword", "vector"):
        statuses[mode] = execute(out, f"perf_{mode}", [str(EVIDENCE / "m3_r01_perf.py"), "--mode", mode,
                                 "--root", str(native / mode), "--out", str(out / f"m3_r01_perf_{mode}.json"),
                                 *repeat_options], env)
    if not repeat_single:
        statuses["bom_governed"] = execute(out, "bom_governed", [str(EVIDENCE / "m1_r01_governed_replay.py"),
                                            "--out", str(out / "m3_r01_bom_governed.json")], env)
        statuses["inventory"] = execute(out, "inventory", [str(EVIDENCE / "m3_r01_inventory.py"), "--isolation", str(before),
                                         "--out", str(out / "m3_r01_inventory.json")], env)
    after = out / "m3_r01_isolation_after.json"
    statuses["isolation_after"] = execute(out, "isolation_after", ["tests/verify_isolation.py", "--out", str(after)], env)
    if not statuses["isolation_after"]:
        for point in ("before", "after"):
            check_assets(dispatch, read_json(after)[f"business_snapshot_{point}"])
    return int(any(statuses.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New evidence directory in a work copy")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--followup", action="store_true", help="Run performance, BOM and offline inventory after full regression")
    modes.add_argument("--repeat-single", nargs="+", choices=("keyword", "vector"), help="Repeat the same 200 single requests")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    native = Path(tempfile.mkdtemp(prefix="listtrans_v020_m3_")).resolve()
    if native.is_relative_to(ROOT):
        raise RuntimeError("Test root must be outside the workspace")
    print(f"Native root: {native}", flush=True)
    dispatch = read_json(EVIDENCE / "m3_r01_dispatch_baseline.json")
    env = offline_env(dispatch, native)
    if args.followup or args.repeat_single:
        return followup(out, native, dispatch, env, args.repeat_single)
    baseline = check_baseline(dispatch, native / "original")
    write_json(out / "m3_r01_preflight.json", {**baseline, "native_root": str(native),
               "dispatch_sha256": sha256_file(EVIDENCE / "m3_r01_dispatch_baseline.json"),
               "switches": dispatch["default_switches_frozen"]})
    before = out / "m3_r01_isolation_before.json"
    if execute(out, "isolation_before", ["tests/verify_isolation.py", "--out", str(before)], env):
        return 1
    for point in ("before", "after"):
        check_assets(dispatch, read_json(before)[f"business_snapshot_{point}"])

    xml = native / "pytest.xml"
    env["LISTTRANS_TEST_ROOT"] = str(native / "full")
    full_status = execute(out, "pytest_full", ["-m", "pytest", "tests", "-vv", "-ra", "-p", "no:cacheprovider",
                          "--durations=25", "--basetemp", str(native / "full" / "pytest"),
                          "--junitxml", str(xml)], env)
    if xml.exists():
        suite = ET.parse(xml).getroot().find("testsuite")
        flywheel = [case for case in suite.findall("testcase") if case.get("name") == "test_flywheel_end_to_end_pipeline"]
        public_suite = {key: value for key, value in suite.attrib.items() if key != "hostname"}
        write_json(out / "m3_r01_pytest_summary.json", {"suite": public_suite, "exit_code": full_status,
                   "flywheel": [case.attrib for case in flywheel]})
    gate = out / "m3_r01_rule_gate.json"
    rule_status = execute(out, "rule_gate", [str(EVIDENCE / "m0_r01_replay.py"), "--mode", "rules",
                          "--arm", "off", "--out", str(gate)], env)
    after = out / "m3_r01_isolation_after.json"
    isolation_status = execute(out, "isolation_after", ["tests/verify_isolation.py", "--out", str(after)], env)
    if not isolation_status:
        for point in ("before", "after"):
            check_assets(dispatch, read_json(after)[f"business_snapshot_{point}"])
    if not rule_status:
        check_rules(read_json(gate))
    return int(bool(full_status or rule_status or isolation_status))


if __name__ == "__main__":
    raise SystemExit(main())
