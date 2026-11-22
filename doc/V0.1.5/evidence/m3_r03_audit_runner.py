"""Independent M3 R03 rerun, with exact backup diff and immutable evidence names."""
import ast
import difflib
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
PREFIX = "m3_r03_audit"
os.chdir(ROOT)
os.environ.update(PYTHONIOENCODING="utf-8", LLM_ENABLED="false", AUTH_DEV_MODE="false",
                  HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false",
                  OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

def write(name, value):
    (EV / f"{PREFIX}_{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def hashes():
    result = []
    for line in (EV / "m3_r03_delivery_hashes.md").read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        cells = [c.strip().strip("`") for c in line.split("|")[1:-1]]
        hs = re.findall(r"\b[0-9a-f]{64}\b", " ".join(cells[1:]))
        if not hs:
            continue
        p = ROOT / cells[0] if "/" in cells[0] else EV / cells[0]
        result.append(dict(file=p.relative_to(ROOT).as_posix(), expected=hs[-1], actual=sha(p), matches=hs[-1] == sha(p)))
    return result

def snapshot(label):
    snap = runpy.run_path(str(ROOT / "tests/verify_isolation.py"))["collect_snapshot"]()
    p = EV / f"{PREFIX}_snapshot_{label}.json"
    p.write_text(json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return dict(file=p.name, sha256=sha(p), golden_paths={
        p: (ROOT/p).exists() for p in ["tests/fixtures/golden_last_run.json", "data/golden_last_run.json"]})

def run(label, args):
    started = time.perf_counter()
    p = subprocess.run([sys.executable, *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=360)
    elapsed = round(time.perf_counter()-started, 3)
    log = EV / f"{PREFIX}_{label}.log"
    log.write_bytes(p.stdout + f"\nEXIT={p.returncode} WALL_SECONDS={elapsed}\n".encode())
    result = dict(command=[sys.executable,*args], exit=p.returncode, seconds=elapsed, log=log.name, sha256=sha(log))
    print(json.dumps(result), flush=True)
    return result

def backup_diffs():
    diffs, files = [], []
    for old in sorted((ROOT/".local-archive/m3_r03_pre_backup").rglob("*.py")):
        rel = old.relative_to(ROOT/".local-archive/m3_r03_pre_backup")
        new = ROOT/rel
        before, after = old.read_text(encoding="utf-8-sig"), new.read_text(encoding="utf-8-sig")
        item = dict(file=rel.as_posix(), backup_sha256=sha(old), current_sha256=sha(new), changed=sha(old)!=sha(new))
        if str(rel).startswith("tests"):
            def tests(text):
                tree=ast.parse(text)
                return {n.name:ast.dump(n, include_attributes=False) for n in ast.walk(tree)
                        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith("test_")}
            a,b=tests(before),tests(after)
            item.update(old_tests=len(a), new_tests=len(b), removed=sorted(a.keys()-b.keys()),
                        modified_old=sorted(k for k in a.keys()&b.keys() if a[k]!=b[k]))
        files.append(item)
        diffs.extend(difflib.unified_diff(before.splitlines(True),after.splitlines(True),
                                         fromfile="R02/"+rel.as_posix(),tofile="R03/"+rel.as_posix()))
    (EV/f"{PREFIX}_r01_to_r02.diff").write_text("".join(diffs),encoding="utf-8")
    return files

if __name__=="__main__":
    summary=dict(baseline=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
                 started=time.strftime("%Y-%m-%d %H:%M:%S"), python=sys.version,
                 hashes_before=hashes(), backups=backup_diffs(), steps=[])
    assert all(r["matches"] for r in summary["hashes_before"]), summary["hashes_before"]
    summary["snapshot_before"]=snapshot("before")
    write("summary", summary)
    gate=(EV/"m3_r03_capture_evidence.py").read_text(encoding="utf-8")
    (EV/f"{PREFIX}_gate.py").write_text(gate.replace("m3_r03_gate_reports.json",f"{PREFIX}_gate_reports.json"),encoding="utf-8")
    try:
        pre=run("verify_before",["tests/verify_isolation.py","--out",str(EV/f"{PREFIX}_verify_before.json")])
        summary["steps"].append(pre)
        assert pre["exit"]==0
        jobs=[("m3",["-m","pytest","tests/test_m3_hard_constraints.py","tests/test_m3_hard_constraints_http.py","-q","--tb=short"]),
              ("required",["-m","pytest","tests/test_m0_isolation.py","tests/test_m0_guardrail_regression.py","tests/test_query_contract.py","tests/test_auto_binding.py","tests/test_binding_decisions.py","-q","--tb=short"]),
              ("boundaries",["-m","pytest","tests/test_flywheel_sandbox.py","-k","compare or nonzero","-q","--tb=short"]),
              ("gate",[str(EV/f"{PREFIX}_gate.py")]),
              ("full",["-m","pytest","tests/","-q","--tb=short"]),
              ("verify_after",["tests/verify_isolation.py","--out",str(EV/f"{PREFIX}_verify_after.json")])]
        for label,args in jobs:
            summary["steps"].append(run(label,args))
            write("summary",summary)
    finally:
        summary.update(snapshot_after=snapshot("after"), hashes_after=hashes(), finished=time.strftime("%Y-%m-%d %H:%M:%S"))
        write("summary",summary)
