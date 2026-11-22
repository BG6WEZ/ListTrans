"""Actual production Dockerfile offline build; sanitized source-only temporary context.
Never uses the historical application image with known embedded credentials.
"""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.5/evidence"
TMP = Path(tempfile.mkdtemp(prefix="m4_r03_audit_prod_build_"))
for name in ("Dockerfile", ".dockerignore", "requirements.txt"):
    shutil.copy2(ROOT / name, TMP / name)
(TMP / "app").mkdir()
for p in (ROOT / "app").glob("*.py"):
    shutil.copy2(p, TMP / "app" / p.name)
shutil.copytree(ROOT / "web", TMP / "web")
(TMP / "config").mkdir()
for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
    if line.startswith("!config/"):
        name = line[1:]
        shutil.copy2(ROOT / name, TMP / name)
tag = "listtrans:m4-r03-audit-full-offline-" + str(int(time.time()))
command = ["docker", "build", "--network=none", "--pull=false", "--no-cache", "-t", tag, str(TMP)]
started = time.monotonic()
with (EV / "m4_r03_audit_docker_full_offline.log").open("w", encoding="utf-8") as log:
    try:
        process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        exit_code = process.returncode
    except subprocess.TimeoutExpired:
        exit_code = 124
        log.write("AUDIT build timeout; no full image validation\n")
report = {"command": command, "exit_code": exit_code, "wall_seconds": round(time.monotonic() - started, 3),
          "full_image_build_completed": exit_code == 0, "historical_image_used": False,
          "context": "current source/web/requirements and 16 allowed configurations only; no real .env, DB, data or personnel files",
          "whole_production_image_layer_validation_completed": False}
(EV / "m4_r03_audit_docker_full_offline.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report), flush=True)
