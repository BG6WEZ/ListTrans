"""Independent reviewer rerun; preserves all earlier development/audit evidence."""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
EV = Path(__file__).resolve().parent
PREFIX = "m3_r01_joint_audit2"
os.chdir(ROOT)
os.environ.update(PYTHONIOENCODING="utf-8", LLM_ENABLED="false", AUTH_DEV_MODE="false",
                  HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false",
                  OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                  VECLIB_MAXIMUM_THREADS="1", NUMEXPR_NUM_THREADS="1")

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

def write(name, data):
    (EV / f"{PREFIX}_{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def hashes():
    records = []
    for line in (EV / "m3_r01_delivery_hashes.md").read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        hs = re.findall(r"\b[0-9a-f]{64}\b", " ".join(cells[1:]))
        if not hs:
            continue
        path = cells[0]
        p = ROOT / path if "/" in path else EV / path
        actual = digest(p)
        records.append(dict(file=str(p.relative_to(ROOT)), expected=hs[-1], actual=actual,
                            matches=actual == hs[-1]))
    return records

def snapshot(label):
    # Read-only standard-library module; main() is deliberately not invoked here.
    helpers = runpy.run_path(str(ROOT / "tests/verify_isolation.py"))
    snap = helpers["collect_snapshot"]()
    path = EV / f"{PREFIX}_snapshot_{label}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return dict(file=path.name, sha256=digest(path), golden_paths={
        p: (ROOT / p).exists() for p in ["tests/fixtures/golden_last_run.json", "data/golden_last_run.json"]})

def run(label, args):
    start = time.perf_counter()
    p = subprocess.run([sys.executable, *args], cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=420)
    elapsed = round(time.perf_counter() - start, 3)
    log = EV / f"{PREFIX}_{label}.log"
    log.write_bytes(p.stdout + f"\nEXIT={p.returncode} WALL_SECONDS={elapsed}\n".encode())
    item = dict(command=[sys.executable, *args], exit=p.returncode, seconds=elapsed,
                log=log.name, log_sha256=digest(log))
    print(json.dumps(item), flush=True)
    return item

if __name__ == "__main__":
    summary = dict(python=sys.version, started=time.strftime("%Y-%m-%d %H:%M:%S"),
                   baseline=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                   hashes_before=hashes(), steps=[])
    summary["snapshot_before"] = snapshot("before")
    write("summary", summary)
    try:
        pre = run("verify_before", ["tests/verify_isolation.py", "--out", str(EV / f"{PREFIX}_verify_before.json")])
        summary["steps"].append(pre)
        assert pre["exit"] == 0
        jobs = [
            ("m3", ["-m", "pytest", "tests/test_m3_hard_constraints.py", "tests/test_m3_hard_constraints_http.py", "-q", "--tb=short"]),
            ("required", ["-m", "pytest", "tests/test_m0_isolation.py", "tests/test_m0_guardrail_regression.py", "tests/test_query_contract.py", "tests/test_auto_binding.py", "tests/test_binding_decisions.py", "-q", "--tb=short"]),
            ("boundaries", ["-m", "pytest", "tests/test_flywheel_sandbox.py", "-k", "compare or nonzero", "-q", "--tb=short"]),
            ("gate", [str(EV / f"{PREFIX}_gate.py")]),
            ("full", ["-m", "pytest", "tests/", "-q", "--tb=short"]),
            ("verify_after", ["tests/verify_isolation.py", "--out", str(EV / f"{PREFIX}_verify_after.json")]),
        ]
        gate_source = (EV / "m3_r01_capture_evidence.py").read_text(encoding="utf-8")
        (EV / f"{PREFIX}_gate.py").write_text(gate_source.replace("m3_r01_gate_reports.json", f"{PREFIX}_gate_reports.json"), encoding="utf-8")
        for label, args in jobs:
            summary["steps"].append(run(label, args))
            write("summary", summary)
    finally:
        summary["snapshot_after"] = snapshot("after")
        summary["hashes_after"] = hashes()
        summary["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        write("summary", summary)
        print(json.dumps({"snapshots_equal": summary["snapshot_before"] == {**summary["snapshot_after"], "file": summary["snapshot_before"]["file"]},
                          "hash_mismatches": [r for r in summary["hashes_after"] if not r["matches"]]}), flush=True)
