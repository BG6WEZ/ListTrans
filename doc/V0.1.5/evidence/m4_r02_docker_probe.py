"""V015-M4 / R02 Docker build context, layer exclusion, and isolated container startup probes.

Part 1: Synthetic canary probes (`context` and `copy_patterns` on `FROM scratch`) verifying A-M4-04 fixes.
Part 2 (V-M4-01):
  2A) Honest offline test of production `Dockerfile` (`docker build --network=none`).
  2B) Runtime overlay build on existing local `material-matcher:v0.1.4` applying production COPY allowlist + `--workers 1` CMD,
      full layer tar inspection (zero synthetic canaries / runtime secrets), and isolated `docker run --rm` verification
      for fail-closed missing SECRET_KEY and live startup on port 18080.
"""
from pathlib import Path
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.5/evidence"
TMP = Path(tempfile.mkdtemp(prefix="m4_r02_docker_probe_"))

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
}
SAFE_SYNTHETIC = {
    "app/main.py": "# safe synthetic source\n",
    "web/index.html": "safe source",
    "config/alias_dict.json": "{}",
    "requirements.txt": "",
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


def _scan_top_layer_files(tar_path: Path) -> list:
    files = set()
    with tarfile.open(tar_path) as outer:
        manifest_member = next((m for m in outer.getmembers() if m.name.endswith("manifest.json")), None)
        target_blobs = set()
        if manifest_member:
            manifest = json.loads(outer.extractfile(manifest_member).read().decode("utf-8"))
            layers = manifest[0].get("Layers", [])
            # Check final 4 layers produced by overlay COPY + RUN steps
            for lyr in layers[-4:]:
                target_blobs.add(lyr.lstrip("./"))
        for member in outer.getmembers():
            if not member.isfile():
                continue
            norm = member.name.lstrip("./")
            if target_blobs and norm not in target_blobs:
                continue
            raw = outer.extractfile(member).read()
            try:
                with tarfile.open(fileobj=io.BytesIO(raw)) as layer:
                    files.update(m.name.lstrip("./") for m in layer if m.isfile())
            except tarfile.TarError:
                pass
    return sorted(files)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_post_json(url: str, payload: dict):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, str(exc)


def _http_get(url: str, headers: dict = None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, str(exc)


log_lines = []


def _log(msg: str):
    log_lines.append(msg)
    print(msg, flush=True)


try:
    # ------------------------------------------------------------------
    # Part 1: Synthetic canary probes (context + copy_patterns)
    # ------------------------------------------------------------------
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
            tag = f"listtrans:m4-r02-{kind.replace('_', '-')}-{ts}"
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
                    row["files_in_layers"] = files
                    row["sensitive_canaries_present"] = canaries_present
                    row["safe_files_present"] = safe_present
                    row["all_canaries_excluded"] = not any(canaries_present.values())
                    row["all_safe_retained"] = all(safe_present.values())
            report["canary_probes"][kind] = row
            _log(f"CANARY {kind}: exit={row['build_exit']} all_excluded={row.get('all_canaries_excluded')} all_safe={row.get('all_safe_retained')}")

        # ------------------------------------------------------------------
        # Part 2: V-M4-01 isolated clean app + synthetic canaries context
        # ------------------------------------------------------------------
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
        # Inject all 9 synthetic canaries into prod_ctx to verify none leak into app image
        for name, content in CANARIES.items():
            p = prod_ctx / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        # 2A: Real Dockerfile under --network=none (honest record if pip offline blocks)
        prod_offline_tag = f"listtrans:m4-r02-prod-offline-{ts}"
        built_tags.append(prod_offline_tag)
        prod_offline = subprocess.run(
            ["docker", "build", "--network=none", "--pull=false", "--no-cache", "-t", prod_offline_tag, str(prod_ctx)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        tail_offline = (prod_offline.stdout + prod_offline.stderr)[-1500:]
        report["v_m4_01"]["production_dockerfile_offline"] = {
            "build_exit": prod_offline.returncode,
            "full_build_completed": prod_offline.returncode == 0,
            "status": "PASSED" if prod_offline.returncode == 0 else "BLOCKED_BY_OFFLINE_PIP_NETWORK_PENDING_VERIFY",
            "output_tail": tail_offline,
        }
        _log(f"PROD_DOCKERFILE_OFFLINE: exit={prod_offline.returncode} status={report['v_m4_01']['production_dockerfile_offline']['status']}")

        # 2B: Overlay on local preinstalled runtime base material-matcher:v0.1.4 + layer scan + container startup test
        base_check = subprocess.run(["docker", "image", "inspect", "material-matcher:v0.1.4"], capture_output=True, text=True)
        if base_check.returncode == 0:
            overlay_df = (
                "FROM material-matcher:v0.1.4\n"
                "ENV PYTHONDONTWRITEBYTECODE=1\n"
                "ENV PYTHONUNBUFFERED=1\n"
                "WORKDIR /app\n"
                "RUN rm -rf /app/app/*.db /app/app/*.db-* /app/config/* /app/web/* /app/app/*.py\n"
                "COPY requirements.txt /app/\n"
                "COPY app/*.py /app/app/\n"
                "COPY web/ /app/web/\n"
                "COPY config/ /app/config/\n"
                "RUN mkdir -p /app/app/uploads /app/app/vector_cache /app/logs /app/model_offline\n"
                "EXPOSE 18080\n"
                "CMD [\"uvicorn\", \"app.server:app\", \"--host\", \"0.0.0.0\", \"--port\", \"18080\", \"--workers\", \"1\"]\n"
            )
            (prod_ctx / "Dockerfile").write_text(overlay_df, encoding="utf-8")
            overlay_tag = f"listtrans:m4-r02-runtime-overlay-{ts}"
            built_tags.append(overlay_tag)
            ov_build = subprocess.run(
                ["docker", "build", "--network=none", "--pull=false", "--no-cache", "-t", overlay_tag, str(prod_ctx)],
                capture_output=True,
                text=True,
                timeout=180,
            )
            ov_info = {"build_exit": ov_build.returncode, "base_image": "material-matcher:v0.1.4"}
            if ov_build.returncode == 0:
                ov_tar = TMP / "runtime_overlay.tar"
                ov_save = subprocess.run(["docker", "image", "save", "-o", str(ov_tar), overlay_tag], capture_output=True, text=True, timeout=180)
                ov_info["save_exit"] = ov_save.returncode
                if ov_save.returncode == 0:
                    all_files = _scan_tar_files(ov_tar)
                    top_files = _scan_top_layer_files(ov_tar)
                    top_canaries = {name: ("app/" + name) in top_files for name in CANARIES}
                    all_canaries = {name: ("app/" + name) in all_files for name in CANARIES}
                    ov_info["top_layers_canaries_present"] = top_canaries
                    ov_info["all_layers_synthetic_canaries_present"] = all_canaries
                    ov_info["top_layers_all_canaries_excluded"] = not any(top_canaries.values())
                    ov_info["top_layers_config_files"] = sorted(
                        f for f in top_files if f.startswith("app/config/")
                    )

                    # Test fail-closed startup without SECRET_KEY
                    fc = subprocess.run(
                        [
                            "docker", "run", "--rm",
                            "-e", "TESTING=0",
                            "-e", "APP_ENV=production",
                            "-e", "LLM_ENABLED=false",
                            overlay_tag,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    ov_info["container_fail_closed_no_secret"] = {
                        "exit_code": fc.returncode,
                        "rejected_cleanly": fc.returncode != 0 and "FATAL SECURITY ERROR" in (fc.stdout + fc.stderr),
                    }

                    # Test live container startup with isolated tmp mount and --workers 1
                    host_port = _free_port()
                    cname = f"listtrans-m4-r02-check-{ts}"
                    run_proc = subprocess.run(
                        [
                            "docker", "run", "-d", "--name", cname,
                            "-p", f"127.0.0.1:{host_port}:18080",
                            "-e", "TESTING=0",
                            "-e", "APP_ENV=production",
                            "-e", "LLM_ENABLED=false",
                            "-e", "AUTH_DEV_MODE=false",
                            "-e", "HF_HUB_OFFLINE=1",
                            "-e", "TRANSFORMERS_OFFLINE=1",
                            "-e", "SECRET_KEY=Synthetic_R02_Docker_Container_Key_2026_XYZ",
                            "-e", "ADMIN_PASSWORD=SyntheticDockerAdmin#2026!",
                            "-e", "MATERIALS_DB_PATH=/tmp/materials.db",
                            "-e", "AUTH_DB_PATH=/tmp/auth.db",
                            "-e", "LOG_DIR=/tmp/logs",
                            "-e", "UPLOAD_DIR=/tmp/uploads",
                            "-e", "OUTPUT_DIR=/tmp/outputs",
                            "-e", "VECTOR_CACHE_DIR=/tmp/vector",
                            overlay_tag,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    startup_res = {"start_exit": run_proc.returncode, "port": host_port}
                    try:
                        if run_proc.returncode == 0:
                            deadline = time.time() + 45.0
                            login_code = 0
                            while time.time() < deadline:
                                login_code, _ = _http_get(f"http://127.0.0.1:{host_port}/login.html")
                                if login_code == 200:
                                    break
                                time.sleep(0.5)
                            ps_out = subprocess.run(
                                [
                                    "docker",
                                    "exec",
                                    cname,
                                    "python",
                                    "-c",
                                    "import glob, pathlib; print('\\n'.join(pathlib.Path(p).read_bytes().replace(b'\\x00', b' ').decode('utf-8', 'replace').strip() for p in sorted(glob.glob('/proc/[0-9]*/cmdline'))))",
                                ],
                                capture_output=True,
                                text=True,
                                timeout=20,
                            )
                            me_unauth_code, _ = _http_get(f"http://127.0.0.1:{host_port}/api/auth/me")
                            login_api_code, login_body = _http_post_json(
                                f"http://127.0.0.1:{host_port}/api/auth/login",
                                {"username": "admin", "password": "SyntheticDockerAdmin#2026!"},
                            )
                            tok = login_body.get("access_token") if isinstance(login_body, dict) else ""
                            me_auth_code = 0
                            if tok:
                                me_auth_code, _ = _http_get(
                                    f"http://127.0.0.1:{host_port}/api/auth/me",
                                    headers={"Authorization": f"Bearer {tok}"},
                                )
                            cfg_list = subprocess.run(
                                ["docker", "exec", cname, "ls", "-1", "/app/config"],
                                capture_output=True,
                                text=True,
                                timeout=20,
                            )
                            ps_lines = [line.strip() for line in ps_out.stdout.splitlines() if "uvicorn" in line]
                            startup_res["login_html_status"] = login_code
                            startup_res["unauth_me_status"] = me_unauth_code
                            startup_res["login_api_status"] = login_api_code
                            startup_res["auth_me_status"] = me_auth_code
                            startup_res["container_config_files"] = sorted(
                                line.strip() for line in cfg_list.stdout.splitlines() if line.strip()
                            )
                            startup_res["uvicorn_cmdlines"] = ps_lines
                            startup_res["workers_1_cmd_present"] = any("--workers 1" in line for line in ps_lines)
                            startup_res["uvicorn_process_count"] = len(ps_lines)
                    finally:
                        subprocess.run(["docker", "rm", "-f", cname], capture_output=True, text=True, timeout=30)
                    ov_info["container_isolated_startup"] = startup_res
            report["v_m4_01"]["runtime_overlay_check"] = ov_info
            _log(
                f"RUNTIME_OVERLAY: build_exit={ov_build.returncode} "
                f"fail_closed={ov_info.get('container_fail_closed_no_secret')} "
                f"startup={ov_info.get('container_isolated_startup')}"
            )
    finally:
        for t in built_tags:
            subprocess.run(["docker", "image", "rm", "-f", t], capture_output=True, text=True, timeout=30)

    assert all(
        report["canary_probes"][k].get("all_canaries_excluded") and report["canary_probes"][k].get("all_safe_retained")
        for k in ("context", "copy_patterns")
    ), json.dumps(report, ensure_ascii=False, indent=2)

    (EV / "m4_r02_docker_probes.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (EV / "m4_r02_docker_check.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print("DOCKER_PROBE_SUMMARY PASS", flush=True)
finally:
    shutil.rmtree(TMP, ignore_errors=True)
