"""Offline synthetic BuildKit probe using the delivered .dockerignore and COPY shapes.

Two scratch images inspect (1) transmitted context, (2) production COPY source patterns.
This is NOT a successful production dependency image build or production layer scan.
All artifacts contain synthetic canaries only, never live data, credentials or configuration.
"""
from pathlib import Path
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.5/evidence"
TMP = Path(tempfile.mkdtemp(prefix="m4_audit_docker_canary_"))
CANARIES = {
    ".env": "SYNTHETIC_ENV_CANARY",
    ".env.production": "SYNTHETIC_ENV_PROD_CANARY",
    "app/auth.db": "SYNTHETIC_AUTH_CANARY",
    "app/auth.db-wal": "SYNTHETIC_AUTH_WAL_CANARY",
    "app/auth.db.backup": "SYNTHETIC_R02_AUTH_BACKUP_CANARY",
    "data/audit_disabled_materials.csv": "SYNTHETIC_BUSINESS_DATA_CANARY",
    "doc/personnel.xls": "SYNTHETIC_PERSONNEL_CANARY",
    "scripts/cloud_users_snapshot.json": "SYNTHETIC_USER_SNAPSHOT_CANARY",
    "config/runtime_credentials.json": "SYNTHETIC_CREDENTIAL_CANARY",
    "config/unlisted_runtime.json": "SYNTHETIC_UNLISTED_CONFIG_CANARY",
}
for name, content in CANARIES.items():
    p = TMP / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
for name, content in {"app/main.py": "# safe synthetic source\n", "web/index.html": "safe source",
                      "config/alias_dict.json": "{}", "requirements.txt": ""}.items():
    p = TMP / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
shutil.copy2(ROOT / ".dockerignore", TMP / ".dockerignore")
allowed_configs = [line[1:] for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
                   if line.startswith("!config/")]
assert len(allowed_configs) == 16
for name in allowed_configs:
    (TMP / name).write_text("{}", encoding="utf-8")
report = {"fixture": "synthetic only; no downloads; scratch bases; no containers or remote connections",
          "production_dockerfile_sha256": hashlib.sha256((ROOT / "Dockerfile").read_bytes()).hexdigest(),
          "dockerignore_sha256": hashlib.sha256((ROOT / ".dockerignore").read_bytes()).hexdigest(),
          "production_image_build_executed": False, "probes": {}}
for kind, instructions in {
    "context": "FROM scratch\nCOPY . /context/\n",
    "copy_patterns": "FROM scratch\nCOPY requirements.txt /app/\nCOPY app/*.py /app/app/\nCOPY web/ /app/web/\nCOPY config/ /app/config/\n",
}.items():
    dockerfile = TMP / "Dockerfile"
    dockerfile.write_text(instructions, encoding="utf-8")
    tag = "listtrans:m4-r02-audit-" + kind.replace("_", "-") + "-" + str(int(time.time()))
    args = ["docker", "build", "--network=none", "--pull=false", "--no-cache", "-t", tag, str(TMP)]
    built = subprocess.run(args, capture_output=True, text=True, timeout=120)
    (EV / ("m4_r02_audit_docker_" + kind + ".log")).write_text(built.stdout + built.stderr, encoding="utf-8")
    row = {"command": args, "tag": tag, "build_exit": built.returncode}
    if built.returncode == 0:
        target = EV / ("m4_r02_audit_docker_" + kind + ".tar")
        saved = subprocess.run(["docker", "image", "save", "-o", str(target), tag], capture_output=True, text=True, timeout=60)
        row["save_exit"] = saved.returncode
        if saved.returncode == 0:
            files = set()
            with tarfile.open(target) as outer:
                names = set(outer.getnames())
                # Docker/OCI save layouts both contain uncompressed layer tar blobs.
                for member in outer.getmembers():
                    if not member.isfile():
                        continue
                    raw = outer.extractfile(member).read()
                    try:
                        with tarfile.open(fileobj=io.BytesIO(raw)) as layer:
                            files.update(m.name.lstrip("./") for m in layer if m.isfile())
                    except tarfile.TarError:
                        pass
            row["files_in_layers"] = sorted(files)
            row["sensitive_canaries_present"] = {
                name: (("context/" + name) if kind == "context" else ("app/" + name)) in files
                for name in CANARIES
            }
            row["archive_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            prefix = "context/" if kind == "context" else "app/"
            row["allowed_configs_present"] = {name: prefix + name in files for name in allowed_configs}
            row["all_16_allowed_configs_retained"] = all(row["allowed_configs_present"].values())
            row["all_canaries_excluded"] = not any(row["sensitive_canaries_present"].values())
    report["probes"][kind] = row
    (EV / "m4_r02_audit_docker_probes.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"check": kind, "exit": row["build_exit"],
                      "canaries_present": row.get("sensitive_canaries_present", {})}), flush=True)
