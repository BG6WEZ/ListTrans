"""Rerun delivered integration fixtures with independent output paths and full isolation.

Browser reuses the delivered UI exercise, with output directory overridden only.
Gate reuses the delivered fixture and actual child processes, replacing two output filenames only.
No development evidence is overwritten. No Docker daemon start/build/download or remote access.
"""
from pathlib import Path
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.5/evidence"


def browser():
    tmp = Path(tempfile.mkdtemp(prefix="m4_audit_browser_outer_"))
    for name in ("vector", "runtime", "outputs", "uploads", "logs"):
        (tmp / name).mkdir()
    os.environ.update({
        "VECTOR_CACHE_DIR": str(tmp / "vector"), "GOLDEN_DETAIL_FILE": str(tmp / "runtime/golden_last_run.json"),
        "BINDING_FEEDBACK_FILE": str(tmp / "runtime/binding_feedback.json"),
        "FEEDBACK_SAMPLES_FILE": str(tmp / "runtime/feedback_samples.json"),
        "UPLOAD_DIR": str(tmp / "uploads"), "OUTPUT_DIR": str(tmp / "outputs"), "LOG_DIR": str(tmp / "logs"),
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "LLM_ENABLED": "false",
    })
    ns = runpy.run_path(str(EV / "m4_r02_browser_check.py"))
    output = EV / "m4_r02_audit_browser"
    output.mkdir(exist_ok=True)
    ns["run_browser_e2e"].__globals__["EVIDENCE_DIR"] = output
    original_remove = shutil.rmtree

    def checked_remove(path, *args, **kwargs):
        resolved = Path(path).resolve()
        if resolved.parent != Path(tempfile.gettempdir()).resolve() or not resolved.name.startswith("m4_r02_browser_e2e_"):
            raise RuntimeError("Refusing cleanup outside the browser fixture's named temporary directory")
        return original_remove(resolved, *args, **kwargs)

    shutil.rmtree = checked_remove
    try:
        ns["run_browser_e2e"]()
    finally:
        shutil.rmtree = original_remove


def gate():
    src = EV / "m4_r02_capture_evidence.py"
    code = src.read_text(encoding="utf-8-sig")
    code = code.replace('evidence/m4_r02_gate_reports.json', 'evidence/m4_r02_audit_gate_reports.json')
    code = code.replace('evidence/m4_r02_3rule_check.json', 'evidence/m4_r02_audit_gate_compare.json')
    exec(compile(code, str(src), "exec"), {"__name__": "__audit_gate__", "__file__": str(src)})


def main():
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "LLM_ENABLED": "false",
                       "AUTH_DEV_MODE": "false", "PYTHONIOENCODING": "utf-8"})
    summary = []
    for label in ("browser", "gate"):
        start = time.monotonic()
        with (EV / ("m4_r02_audit_" + label + ".log")).open("w", encoding="utf-8") as log:
            try:
                p = subprocess.run([sys.executable, __file__, label], cwd=ROOT, stdout=log,
                                   stderr=subprocess.STDOUT, timeout=300)
                code = p.returncode
            except subprocess.TimeoutExpired:
                code = 124
                log.write("Audit integration TIMEOUT; not a pass\n")
        row = {"check": label, "exit_code": code, "wall_seconds": round(time.monotonic() - start, 3)}
        summary.append(row)
        (EV / "m4_r02_audit_integration_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(row), flush=True)
    try:
        p = subprocess.run(["docker", "info", "--format", "{{json .ServerVersion}}"], cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
        (EV / "m4_r02_audit_docker.log").write_text(p.stdout + p.stderr, encoding="utf-8")
        row = {"check": "docker_engine", "exit_code": p.returncode,
               "build_or_layer_scan_executed": False, "status": "available; layer scan still unexecuted" if p.returncode == 0 else "pending; engine unavailable"}
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        row = {"check": "docker_engine", "status": "pending", "error_type": type(exc).__name__}
    summary.append(row)
    (EV / "m4_r02_audit_integration_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(row), flush=True)
    return 0 if all(row["exit_code"] == 0 for row in summary[:2]) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1:
        {"browser": browser, "gate": gate}[sys.argv[1]]()
    else:
        raise SystemExit(main())
