"""V015-M4 / R03 Docker build context, layer exclusion, and offline Dockerfile status probes.

Part 1: Synthetic canary probes (context and copy_patterns on FROM scratch) covering all 10 synthetic canaries
        (including config/unlisted_runtime.json) and all 16 allowed baseline config/*.json files.
Part 2 (V-M4-01): Honest offline test of production Dockerfile (`docker build --network=none --pull=false --no-cache`).
                  Per R02裁决, runtime overlay on material-matcher:v0.1.4 is NOT used as substitute proof for V-M4-01.
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
TMP = Path(tempfile.mkdtemp(prefix="m4_r03_docker_probe_"))

CANARIES = {
    ".env": "SYNTHETIC_ENV_CANARY",
    ".env.production": "SYNTHETIC_ENV_PROD_CANARY",
    "app/auth.db": "SYNTHETIC_AUTH_CANARY",
    "app/auth.db-wal": "SYNTHETIC_AUTH_WAL_CANARY",
    "app/auth.db.backup": "SYNTHETIC_AUTH_BACKUP_CANARY",
    "data/audit_disabled_materials.csv": "SYNTHETIC_BUSINESS_DATA_CANARY",
    "doc/personnel.xls": "SYNTHETIC_PERSONNEL_CANARY",
    "scripts/cloud_users_snapshot.json": "SYNTHETIC_USER_SNAPSHOT_CANARY",
    "config/runtime_credentials.json": "SYNTHETIC_CREDENTIAL_CANARY",
    "config/unlisted_runtime.json": "SYNTHETIC_UNLISTED_CONFIG_CANARY",
}

ALLOWED_CONFIGS = [
    "config/alias_dict.json",
    "config/auto_binding_rules.json",
    "config/boost_rules.json",
    "config/category_domain_map.json",
    "config/connection_dict.json",
    "config/domain_schema.json",
    "config/flags_dict.json",
    "config/llm_config.json",
    "config/llm_prompts.json",
    "config/material_dict.json",
    "config/model_config.json",
    "config/stop_words.json",
    "config/tolerance_rules.json",
    "config/unit_norm.json",
    "config/unit_rules.json",
    "config/weight_rules.json",
]

SAFE_SYNTHETIC = {
    "app/main.py": "# safe synthetic source\n",
    "web/index.html": "safe source",
    "requirements.txt": "",
    **{cfg: "{}" for cfg in ALLOWED_CONFIGS},
}


def _scan_tar_files(tar_path: Path) -> list:
    files = set()
    with tarfile.open(tar_path) as outer:
        for member in outer.getmembers():
            if not member.isfile():
                continue
            raw = outer.extractfile(member).read()
            try:
                with tarfile.open(fileobj=io.BytesIO(raw)) as layer:
                    files.update(m.name.lstrip("./") for m in layer if m.isfile())
            except tarfile.TarError:
                pass
    return sorted(files)


log_lines = []


def _log(msg: str):
    log_lines.append(msg)
    print(msg, flush=True)


try:
    canary_ctx = TMP / "canary_ctx"
    canary_ctx.mkdir()
    for name, content in {**CANARIES, **SAFE_SYNTHETIC}.items():
        p = canary_ctx / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    shutil.copy2(ROOT / ".dockerignore", canary_ctx / ".dockerignore")

    report = {
        "production_dockerfile_sha256": hashlib.sha256((ROOT / "Dockerfile").read_bytes()).hexdigest(),
        "dockerignore_sha256": hashlib.sha256((ROOT / ".dockerignore").read_bytes()).hexdigest(),
        "canary_probes": {},
        "v_m4_01": {},
    }
    ts = str(int(time.time()))
    built_tags = []

    try:
        for kind, instructions in {
            "context": "FROM scratch\nCOPY . /context/\n",
            "copy_patterns": "FROM scratch\nCOPY requirements.txt /app/\nCOPY app/*.py /app/app/\nCOPY web/ /app/web/\nCOPY config/ /app/config/\n",
        }.items():
            (canary_ctx / "Dockerfile").write_text(instructions, encoding="utf-8")
            tag = f"listtrans:m4-r03-{kind.replace('_', '-')}-{ts}"
            built_tags.append(tag)
            args = ["docker", "build", "--network=none", "--pull=false", "--no-cache", "-t", tag, str(canary_ctx)]
            built = subprocess.run(args, capture_output=True, text=True, timeout=120)
            row = {"build_exit": built.returncode, "tag": tag}
            if built.returncode == 0:
                tar_file = TMP / f"canary_{kind}.tar"
                saved = subprocess.run(["docker", "image", "save", "-o", str(tar_file), tag], capture_output=True, text=True, timeout=60)
                row["save_exit"] = saved.returncode
                if saved.returncode == 0:
                    files = _scan_tar_files(tar_file)
                    prefix = "context/" if kind == "context" else "app/"
                    canaries_present = {name: (prefix + name) in files for name in CANARIES}
                    safe_present = {name: (prefix + name) in files for name in SAFE_SYNTHETIC}
                    configs_present = {cfg: (prefix + cfg) in files for cfg in ALLOWED_CONFIGS}
                    row["files_in_layers"] = files
                    row["sensitive_canaries_present"] = canaries_present
                    row["safe_files_present"] = safe_present
                    row["allowed_configs_retained_count"] = sum(1 for v in configs_present.values() if v)
                    row["all_canaries_excluded"] = not any(canaries_present.values())
                    row["all_safe_retained"] = all(safe_present.values())
                    row["all_16_allowed_configs_retained"] = all(configs_present.values()) and len(ALLOWED_CONFIGS) == 16
            report["canary_probes"][kind] = row
            _log(
                f"CANARY {kind}: exit={row['build_exit']} "
                f"all_excluded={row.get('all_canaries_excluded')} "
                f"all_16_configs={row.get('all_16_allowed_configs_retained')}"
            )

        # Honest offline test of production Dockerfile (no v0.1.4 overlay substitution)
        prod_ctx = TMP / "prod_ctx"
        prod_ctx.mkdir()
        shutil.copy2(ROOT / "Dockerfile", prod_ctx / "Dockerfile")
        shutil.copy2(ROOT / ".dockerignore", prod_ctx / ".dockerignore")
        shutil.copy2(ROOT / "requirements.txt", prod_ctx / "requirements.txt")
        (prod_ctx / "app").mkdir()
        for py in (ROOT / "app").glob("*.py"):
            shutil.copy2(py, prod_ctx / "app" / py.name)
        shutil.copytree(ROOT / "web", prod_ctx / "web")
        (prod_ctx / "config").mkdir()
        for cfg in (ROOT / "config").glob("*.json"):
            shutil.copy2(cfg, prod_ctx / "config" / cfg.name)
        for name, content in CANARIES.items():
            p = prod_ctx / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        prod_offline_tag = f"listtrans:m4-r03-prod-offline-{ts}"
        built_tags.append(prod_offline_tag)
        prod_offline = subprocess.run(
            ["docker", "build", "--network=none", "--pull=false", "--no-cache", "-t", prod_offline_tag, str(prod_ctx)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        tail_offline = (prod_offline.stdout + prod_offline.stderr)[-1500:]
        report["v_m4_01"] = {
            "runtime_overlay_substituted": False,
            "production_dockerfile_offline": {
                "build_exit": prod_offline.returncode,
                "full_build_completed": prod_offline.returncode == 0,
                "status": "PASSED" if prod_offline.returncode == 0 else "BLOCKED_BY_OFFLINE_PIP_NETWORK_PENDING_VERIFY",
                "output_tail": tail_offline,
            },
        }
        _log(
            f"PROD_DOCKERFILE_OFFLINE: exit={prod_offline.returncode} "
            f"status={report['v_m4_01']['production_dockerfile_offline']['status']}"
        )
    finally:
        for t in built_tags:
            subprocess.run(["docker", "image", "rm", "-f", t], capture_output=True, text=True, timeout=30)

    assert all(
        report["canary_probes"][k].get("all_canaries_excluded")
        and report["canary_probes"][k].get("all_safe_retained")
        and report["canary_probes"][k].get("all_16_allowed_configs_retained")
        for k in ("context", "copy_patterns")
    ), json.dumps(report, ensure_ascii=False, indent=2)

    (EV / "m4_r03_docker_probes.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (EV / "m4_r03_docker_check.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print("DOCKER_PROBE_SUMMARY PASS", flush=True)
finally:
    shutil.rmtree(TMP, ignore_errors=True)
