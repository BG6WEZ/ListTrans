"""Independent M4 R03 regression runner; never overwrites development evidence."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.5/evidence"
os.environ.update({"LLM_ENABLED": "false", "AUTH_DEV_MODE": "false", "HF_HUB_OFFLINE": "1",
                   "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"})

commands = [
    ("m4", ["-m", "pytest", "tests/test_m4_auth_lifecycle.py", "tests/test_m4_permissions_and_tasks.py", "-q", "--tb=short"]),
    ("compile", ["-W", "error", "-m", "py_compile", "app/auth_manager.py", "app/auth_middleware.py",
                 "app/admin_api.py", "app/rule_api.py", "app/llm_rule_engine.py", "app/server.py",
                 "app/flywheel_sandbox.py", "scripts/_deploy_env.py", "scripts/deploy_cloud.py",
                 "scripts/deploy_to_cloud_safe.py", "scripts/check_cloud_status.py", "scripts/probe_servers.py"]),
    ("m3", ["-m", "pytest", "tests/test_m3_hard_constraints.py", "tests/test_m3_hard_constraints_http.py", "-q", "--tb=short"]),
    ("required", ["-m", "pytest", "tests/test_m0_isolation.py", "tests/test_m0_guardrail_regression.py",
                  "tests/test_query_contract.py", "tests/test_auto_binding.py", "tests/test_binding_decisions.py", "-q", "--tb=short"]),
    ("boundaries", ["-m", "pytest", "tests/test_flywheel_sandbox.py", "-k", "compare or nonzero", "-q", "--tb=short"]),
    ("full", ["-m", "pytest", "tests/", "-q", "--tb=short"]),
    ("probes", [str(EV / "m4_r03_audit_probes.py")]),
]
summary = []
for label, args in commands:
    start = time.monotonic()
    with (EV / ("m4_r03_audit_" + label + ".log")).open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run([sys.executable] + args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
            log.write("AUDIT TIMEOUT after 300 seconds; not a pass\n")
    row = {"check": label, "command": [sys.executable] + args, "exit_code": code,
           "wall_seconds": round(time.monotonic() - start, 3)}
    summary.append(row)
    (EV / "m4_r03_audit_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(row, ensure_ascii=True), flush=True)
raise SystemExit(0 if all(r["exit_code"] == 0 for r in summary) else 1)
