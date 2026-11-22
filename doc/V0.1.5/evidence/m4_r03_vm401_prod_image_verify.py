"""V015-M4 / R03 / V-M4-01 full production Dockerfile build, all-layer inspection, and isolated container startup verification.

Strictly read-only with respect to frozen workspace source/tests/docs/business snapshots:
- Builds in a temporary sanitized context containing frozen Dockerfile, .dockerignore, requirements.txt,
  app/*.py, web/, 16 allowed config/*.json, plus 10 synthetic canary files.
- Runs clean online `docker build --pull=false --no-cache` (no `--network=none`, no scratch/v0.1.4 overlay).
- Inspects ALL layers in `docker image save` tar archive for zero canary/secret leakage and source SHA256 parity.
- Verifies fail-closed missing SECRET_KEY rejection, isolated /tmp startup, auth/must_change_password/rotation HTTP flows,
  and single-worker `--workers 1` execution.
"""

from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc" / "V0.1.5" / "evidence"
IMAGE_TAG = "listtrans:m4-r03-vm401-prod"

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

CANARY_BYTE_MARKERS = [v.encode("utf-8") for v in CANARIES.values()]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_request(method: str, url: str, payload: dict = None, headers: dict = None):
    hdrs = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return exc.code, parsed
    except Exception as exc:
        return 0, str(exc)


def _is_forbidden_app_relpath(app_rel: str) -> bool:
    """Check whether a path under /app/ (relative to /app/) violates production image exclusion rules."""
    low = app_rel.lower()
    name = low.rsplit("/", 1)[-1]
    if name.startswith(".env"):
        return True
    if any(
        low.endswith(ext)
        for ext in (".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3", ".backup", ".csv", ".xls", ".xlsx")
    ):
        return True
    if ".db." in name:
        return True
    if any(tok in low for tok in ("cloud_users_snapshot", "runtime_credentials", "secret", "password", "credential")):
        return True
    if low.startswith("app/feedback_") and low.endswith(".json"):
        return True
    if low == "app/binding_feedback.json":
        return True
    if low.startswith("config/") and app_rel not in ALLOWED_CONFIGS:
        return True
    if low.startswith(("doc/", "scripts/", "data/", "tests/", "logs/", "model_offline/", "app/uploads/", "app/vector_cache/")):
        return True
    return False


def _is_suspicious_global_name(norm_path: str) -> bool:
    """Detect candidate sensitive filenames anywhere in image layers (separate from /app/ strict gate)."""
    low = norm_path.lower()
    name = low.rsplit("/", 1)[-1]
    if name.startswith(".env"):
        return True
    if any(low.endswith(ext) for ext in (".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3", ".backup", ".csv", ".xls", ".xlsx")):
        return True
    if ".db." in name:
        return True
    if "cloud_users_snapshot" in low or "runtime_credentials" in low:
        return True
    return False


def _stream_contains_markers(file_obj, markers: list[bytes]) -> list[str]:
    if file_obj is None:
        return []
    hits = set()
    max_len = max(len(m) for m in markers)
    tail = b""
    while True:
        chunk = file_obj.read(1024 * 1024)
        if not chunk:
            break
        buf = tail + chunk
        for m in markers:
            if m in buf:
                hits.add(m.decode("utf-8"))
        tail = buf[-(max_len - 1) :] if len(buf) >= max_len - 1 else buf
    return sorted(hits)


def _inspect_image_tar(tar_path: Path, expected_app_hashes: dict[str, str]) -> dict:
    with tarfile.open(tar_path, mode="r:") as outer:
        manifest_raw = outer.extractfile("manifest.json").read().decode("utf-8")
        manifest = json.loads(manifest_raw)
        layers = manifest[0]["Layers"]

        layer_reports = []
        canary_path_hits = {k: False for k in CANARIES}
        canary_content_hits = []
        app_forbidden_hits = []
        outside_app_pattern_hits = []
        app_layer_file_hashes = {}
        cumulative_app_files = set()

        for idx, layer_blob in enumerate(layers):
            member = outer.getmember(layer_blob)
            stream = outer.extractfile(member)
            file_count = 0
            app_files_in_layer = []
            layer_hits = []

            with tarfile.open(fileobj=stream, mode="r|*") as lyr:
                for item in lyr:
                    norm = item.name.lstrip("./")
                    if norm.startswith("app/") and "/.wh." in norm:
                        removed = norm.replace("/.wh.", "/")
                        cumulative_app_files.discard(removed)
                        app_layer_file_hashes.pop(removed, None)
                    elif norm == "app/.wh..wh..opq":
                        cumulative_app_files.clear()
                        app_layer_file_hashes.clear()

                    if not item.isfile():
                        continue
                    file_count += 1

                    # Check exact canary path hits (at /app/<canary> or /<canary>)
                    for cname in CANARIES:
                        if norm == f"app/{cname}" or norm == cname:
                            canary_path_hits[cname] = True

                    if norm.startswith("app/"):
                        app_rel = norm[len("app/") :]
                        cumulative_app_files.add(norm)
                        app_files_in_layer.append(norm)
                        if _is_forbidden_app_relpath(app_rel):
                            app_forbidden_hits.append({"layer_index": idx, "path": norm})
                    elif _is_suspicious_global_name(norm):
                        outside_app_pattern_hits.append({"layer_index": idx, "path": norm})

                    fobj = lyr.extractfile(item)
                    if norm in expected_app_hashes:
                        data = fobj.read() if fobj is not None else b""
                        app_layer_file_hashes[norm] = _sha256_bytes(data)
                        for m in CANARY_BYTE_MARKERS:
                            if m in data:
                                canary_content_hits.append({"layer_index": idx, "path": norm, "marker": m.decode("utf-8")})
                    else:
                        matched_markers = _stream_contains_markers(fobj, CANARY_BYTE_MARKERS)
                        for mk in matched_markers:
                            canary_content_hits.append({"layer_index": idx, "path": norm, "marker": mk})
                            layer_hits.append({"path": norm, "marker": mk})

            layer_reports.append(
                {
                    "layer_index": idx,
                    "blob": layer_blob,
                    "file_count": file_count,
                    "app_file_count": len(app_files_in_layer),
                    "app_files_sample": app_files_in_layer[:25],
                    "canary_content_hits": layer_hits,
                }
            )

    config_files_in_image = sorted(p for p in cumulative_app_files if p.startswith("app/config/"))
    expected_image_configs = sorted("app/" + cfg for cfg in ALLOWED_CONFIGS)
    hash_mismatches = [
        {"path": path, "expected_sha256": exp, "actual_sha256": app_layer_file_hashes.get(path)}
        for path, exp in sorted(expected_app_hashes.items())
        if app_layer_file_hashes.get(path) != exp
    ]

    return {
        "total_layers_scanned": len(layers),
        "layers": layer_reports,
        "canary_path_hits": canary_path_hits,
        "all_10_canaries_excluded": not any(canary_path_hits.values()),
        "canary_content_hits_across_all_layers": canary_content_hits,
        "zero_canary_content_in_all_layers": len(canary_content_hits) == 0,
        "app_forbidden_hits": app_forbidden_hits,
        "zero_forbidden_files_under_app": len(app_forbidden_hits) == 0,
        "outside_app_pattern_hits_count": len(outside_app_pattern_hits),
        "outside_app_pattern_hits_sample": outside_app_pattern_hits[:20],
        "config_files_in_image": config_files_in_image,
        "exact_16_allowed_configs_in_image": config_files_in_image == expected_image_configs,
        "expected_app_files_checked": len(expected_app_hashes),
        "app_source_sha256_mismatches": hash_mismatches,
        "all_app_source_hashes_match": len(hash_mismatches) == 0,
    }


def main():
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    base_inspect = json.loads(
        subprocess.check_output(["docker", "image", "inspect", "python:3.11-slim"], text=True)
    )[0]

    # Collect frozen source hashes expected inside /app in the built image
    expected_app_hashes = {"app/requirements.txt": _sha256_file(ROOT / "requirements.txt")}
    source_hashes = {
        "Dockerfile": _sha256_file(ROOT / "Dockerfile"),
        ".dockerignore": _sha256_file(ROOT / ".dockerignore"),
        "requirements.txt": expected_app_hashes["app/requirements.txt"],
    }
    for py in sorted((ROOT / "app").glob("*.py")):
        rel = f"app/{py.name}"
        h = _sha256_file(py)
        source_hashes[rel] = h
        expected_app_hashes[f"app/{rel}"] = h
    for wf in sorted(p for p in (ROOT / "web").rglob("*") if p.is_file()):
        rel = str(wf.relative_to(ROOT)).replace("\\", "/")
        h = _sha256_file(wf)
        source_hashes[rel] = h
        expected_app_hashes[f"app/{rel}"] = h
    for cfg in sorted(ALLOWED_CONFIGS):
        h = _sha256_file(ROOT / cfg)
        source_hashes[cfg] = h
        expected_app_hashes[f"app/{cfg}"] = h

    tmp_dir = Path(tempfile.mkdtemp(prefix="m4_r03_vm401_prod_"))
    container_name = f"listtrans-m4-r03-vm401-{int(time.time())}"

    try:
        prod_ctx = tmp_dir / "prod_ctx"
        prod_ctx.mkdir(parents=True)
        shutil.copy2(ROOT / "Dockerfile", prod_ctx / "Dockerfile")
        shutil.copy2(ROOT / ".dockerignore", prod_ctx / ".dockerignore")
        shutil.copy2(ROOT / "requirements.txt", prod_ctx / "requirements.txt")
        (prod_ctx / "app").mkdir()
        for py in (ROOT / "app").glob("*.py"):
            shutil.copy2(py, prod_ctx / "app" / py.name)
        shutil.copytree(ROOT / "web", prod_ctx / "web")
        (prod_ctx / "config").mkdir()
        for cfg in ALLOWED_CONFIGS:
            shutil.copy2(ROOT / cfg, prod_ctx / cfg)

        # Inject all 10 synthetic sensitive canaries into the build context
        for cname, cval in CANARIES.items():
            target = prod_ctx / cname
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(cval, encoding="utf-8")

        build_cmd = ["docker", "build", "--pull=false", "--no-cache", "-t", IMAGE_TAG, str(prod_ctx)]
        build_log_path = EV / "m4_r03_vm401_prod_image_build.log"
        t0 = time.monotonic()
        with build_log_path.open("w", encoding="utf-8") as logf:
            build_proc = subprocess.run(
                build_cmd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=900,
            )
        build_wall = round(time.monotonic() - t0, 3)
        assert build_proc.returncode == 0, f"Production docker build failed with exit={build_proc.returncode}"

        img_inspect = json.loads(
            subprocess.check_output(["docker", "image", "inspect", IMAGE_TAG], text=True)
        )[0]
        history_raw = subprocess.check_output(
            ["docker", "history", "--no-trunc", "--format", "{{json .}}", IMAGE_TAG],
            text=True,
        )
        history_entries = [json.loads(line) for line in history_raw.splitlines() if line.strip()]
        pip_freeze_out = subprocess.check_output(
            ["docker", "run", "--rm", "--entrypoint", "python", IMAGE_TAG, "-m", "pip", "freeze"],
            text=True,
            timeout=60,
        )
        pip_freeze_lines = [line.strip() for line in pip_freeze_out.splitlines() if line.strip()]

        # Save full image tar and scan ALL layers
        tar_path = tmp_dir / "prod_image.tar"
        save_proc = subprocess.run(
            ["docker", "image", "save", "-o", str(tar_path), IMAGE_TAG],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert save_proc.returncode == 0, f"docker image save failed: {save_proc.stderr}"
        layer_scan = _inspect_image_tar(tar_path, expected_app_hashes)

        # Verify no plaintext secret or canary appears in image Env or History
        env_and_hist_blob = json.dumps(
            {"env": img_inspect.get("Config", {}).get("Env", []), "history": history_entries},
            ensure_ascii=False,
        )
        secret_leak_patterns = [
            *CANARIES.values(),
            "OPENAI_API_KEY=",
            "DASHSCOPE_API_KEY=",
            "DEEPSEEK_API_KEY=",
            "SECRET_KEY=",
            "ADMIN_PASSWORD=",
        ]
        config_history_leaks = [pat for pat in secret_leak_patterns if pat in env_and_hist_blob]
        if re.search(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{12,}", env_and_hist_blob):
            config_history_leaks.append("sk-***")

        # 1) Fail-closed missing SECRET_KEY test
        fc_proc = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-e",
                "TESTING=0",
                "-e",
                "APP_ENV=production",
                "-e",
                "LLM_ENABLED=false",
                IMAGE_TAG,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        fc_combined = fc_proc.stdout + fc_proc.stderr
        fail_closed_check = {
            "exit_code": fc_proc.returncode,
            "rejected_nonzero": fc_proc.returncode != 0,
            "fatal_security_error_present": "FATAL SECURITY ERROR" in fc_combined,
            "output_tail": fc_combined[-800:],
        }

        # 2) Isolated runtime container startup, auth, must_change_password, rotation & single-worker check
        host_port = _free_port()
        synth_secret = "Synthetic_VM401_Prod_Container_Key_2026_Strong_998877"
        synth_admin_pwd = "SyntheticVM401Admin#2026!"
        synth_emp_new_pwd = "VM401EmpNew#2026!"
        synth_admin_new_pwd = "VM401AdminNew#2026!"

        run_proc = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container_name,
                "-p",
                f"127.0.0.1:{host_port}:18080",
                "-e",
                "TESTING=0",
                "-e",
                "APP_ENV=production",
                "-e",
                "LLM_ENABLED=false",
                "-e",
                "AUTH_DEV_MODE=false",
                "-e",
                "HF_HUB_OFFLINE=1",
                "-e",
                "TRANSFORMERS_OFFLINE=1",
                "-e",
                f"SECRET_KEY={synth_secret}",
                "-e",
                f"ADMIN_PASSWORD={synth_admin_pwd}",
                "-e",
                "MATERIALS_DB_PATH=/tmp/materials.db",
                "-e",
                "AUTH_DB_PATH=/tmp/auth.db",
                "-e",
                "LOG_DIR=/tmp/logs",
                "-e",
                "UPLOAD_DIR=/tmp/uploads",
                "-e",
                "OUTPUT_DIR=/tmp/outputs",
                "-e",
                "VECTOR_CACHE_DIR=/tmp/vector",
                "-e",
                "FEEDBACK_FILE=/tmp/feedback.json",
                "-e",
                "BINDING_FEEDBACK_FILE=/tmp/binding_feedback.json",
                IMAGE_TAG,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert run_proc.returncode == 0, f"Container failed to start: {run_proc.stderr}"

        base_url = f"http://127.0.0.1:{host_port}"
        try:
            deadline = time.time() + 60.0
            login_html_code = 0
            while time.time() < deadline:
                login_html_code, _ = _http_request("GET", f"{base_url}/login.html")
                if login_html_code == 200:
                    break
                time.sleep(0.5)
            assert login_html_code == 200, f"/login.html did not return 200 in time (got {login_html_code})"

            unauth_me_code, _ = _http_request("GET", f"{base_url}/api/auth/me")
            wrong_pwd_code, _ = _http_request(
                "POST",
                f"{base_url}/api/auth/login",
                payload={"username": "admin", "password": "WrongPassword#000"},
            )

            admin_login_code, admin_login_body = _http_request(
                "POST",
                f"{base_url}/api/auth/login",
                payload={"username": "admin", "password": synth_admin_pwd},
            )
            admin_tok = admin_login_body["access_token"]
            admin_hdr = {"Authorization": f"Bearer {admin_tok}"}
            admin_me_code, _ = _http_request("GET", f"{base_url}/api/auth/me", headers=admin_hdr)
            admin_rules_code, _ = _http_request("GET", f"{base_url}/api/llm-rules", headers=admin_hdr)

            emp_create_code, emp_create_body = _http_request(
                "POST",
                f"{base_url}/api/admin/users",
                payload={
                    "username": "vm401_emp",
                    "name": "镜像验证员工",
                    "employee_no": "VM401-E01",
                    "role": "employee",
                },
                headers=admin_hdr,
            )
            emp_temp_pwd = emp_create_body["temp_password"]

            emp_login_code, emp_login_body = _http_request(
                "POST",
                f"{base_url}/api/auth/login",
                payload={"username": "vm401_emp", "password": emp_temp_pwd},
            )
            emp_old_tok = emp_login_body["access_token"]
            emp_old_hdr = {"Authorization": f"Bearer {emp_old_tok}"}
            emp_me_before_code, _ = _http_request("GET", f"{base_url}/api/auth/me", headers=emp_old_hdr)
            emp_rules_blocked_code, emp_rules_blocked_body = _http_request(
                "GET", f"{base_url}/api/llm-rules", headers=emp_old_hdr
            )

            emp_profile_code, emp_profile_body = _http_request(
                "PUT",
                f"{base_url}/api/auth/profile",
                payload={"old_password": emp_temp_pwd, "new_password": synth_emp_new_pwd},
                headers=emp_old_hdr,
            )
            emp_old_tok_after_code, _ = _http_request("GET", f"{base_url}/api/auth/me", headers=emp_old_hdr)
            emp_old_pwd_login_code, _ = _http_request(
                "POST",
                f"{base_url}/api/auth/login",
                payload={"username": "vm401_emp", "password": emp_temp_pwd},
            )
            emp_new_login_code, emp_new_login_body = _http_request(
                "POST",
                f"{base_url}/api/auth/login",
                payload={"username": "vm401_emp", "password": synth_emp_new_pwd},
            )
            emp_new_hdr = {"Authorization": f"Bearer {emp_new_login_body['access_token']}"}
            emp_me_after_code, _ = _http_request("GET", f"{base_url}/api/auth/me", headers=emp_new_hdr)
            emp_rules_after_code, _ = _http_request("GET", f"{base_url}/api/llm-rules", headers=emp_new_hdr)

            # Admin rotates initial password as well
            admin_profile_code, _ = _http_request(
                "PUT",
                f"{base_url}/api/auth/profile",
                payload={"old_password": synth_admin_pwd, "new_password": synth_admin_new_pwd},
                headers=admin_hdr,
            )
            admin_old_tok_after_code, _ = _http_request("GET", f"{base_url}/api/auth/me", headers=admin_hdr)
            admin_old_pwd_login_code, _ = _http_request(
                "POST",
                f"{base_url}/api/auth/login",
                payload={"username": "admin", "password": synth_admin_pwd},
            )
            admin_new_login_code, admin_new_login_body = _http_request(
                "POST",
                f"{base_url}/api/auth/login",
                payload={"username": "admin", "password": synth_admin_new_pwd},
            )

            # Inspect running processes and filesystem cleanliness inside container
            ps_out = subprocess.check_output(
                [
                    "docker",
                    "exec",
                    container_name,
                    "python",
                    "-c",
                    "import glob, pathlib; print('\\n'.join(pathlib.Path(p).read_bytes().replace(b'\\x00', b' ').decode('utf-8', 'replace').strip() for p in sorted(glob.glob('/proc/[0-9]*/cmdline'))))",
                ],
                text=True,
                timeout=20,
            )
            uvicorn_lines = [line.strip() for line in ps_out.splitlines() if "uvicorn" in line]
            cfg_ls_out = subprocess.check_output(
                ["docker", "exec", container_name, "ls", "-1", "/app/config"],
                text=True,
                timeout=20,
            )
            container_configs = sorted(line.strip() for line in cfg_ls_out.splitlines() if line.strip())
            app_db_out = subprocess.check_output(
                ["docker", "exec", container_name, "find", "/app/app", "-maxdepth", "2", "-name", "*.db*"],
                text=True,
                timeout=20,
            )
            app_dir_dbs = [line.strip() for line in app_db_out.splitlines() if line.strip()]

            isolated_startup_check = {
                "start_exit": run_proc.returncode,
                "port": host_port,
                "login_html_status": login_html_code,
                "unauth_me_status": unauth_me_code,
                "wrong_password_login_status": wrong_pwd_code,
                "admin_initial_login_status": admin_login_code,
                "admin_initial_must_change_password": admin_login_body.get("must_change_password"),
                "admin_me_status": admin_me_code,
                "admin_llm_rules_status": admin_rules_code,
                "employee_create_status": emp_create_code,
                "employee_create_must_change_password": emp_create_body.get("must_change_password"),
                "employee_temp_password_len": len(emp_temp_pwd),
                "employee_temp_login_status": emp_login_code,
                "employee_temp_must_change_password": emp_login_body.get("must_change_password"),
                "employee_me_before_change_status": emp_me_before_code,
                "employee_llm_rules_before_change_status": emp_rules_blocked_code,
                "employee_llm_rules_before_change_detail": (
                    emp_rules_blocked_body.get("detail") if isinstance(emp_rules_blocked_body, dict) else emp_rules_blocked_body
                ),
                "employee_profile_change_status": emp_profile_code,
                "employee_profile_must_change_password": emp_profile_body.get("must_change_password"),
                "employee_old_token_after_change_status": emp_old_tok_after_code,
                "employee_old_temp_password_login_status": emp_old_pwd_login_code,
                "employee_new_login_status": emp_new_login_code,
                "employee_new_must_change_password": emp_new_login_body.get("must_change_password"),
                "employee_me_after_change_status": emp_me_after_code,
                "employee_llm_rules_after_change_status": emp_rules_after_code,
                "admin_profile_rotate_status": admin_profile_code,
                "admin_old_token_after_rotate_status": admin_old_tok_after_code,
                "admin_old_password_login_status": admin_old_pwd_login_code,
                "admin_new_password_login_status": admin_new_login_code,
                "uvicorn_cmdlines": uvicorn_lines,
                "workers_1_cmd_present": any("--workers 1" in line for line in uvicorn_lines),
                "uvicorn_process_count": len(uvicorn_lines),
                "container_config_files": container_configs,
                "exact_16_container_configs": container_configs == sorted(p.split("/", 1)[1] for p in ALLOWED_CONFIGS),
                "app_dir_db_files": app_dir_dbs,
                "zero_db_files_under_app_dir": len(app_dir_dbs) == 0,
            }
        finally:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=30)

        all_checks = {
            "build_succeeded": build_proc.returncode == 0,
            "all_10_canaries_excluded": layer_scan["all_10_canaries_excluded"],
            "zero_canary_content_in_all_layers": layer_scan["zero_canary_content_in_all_layers"],
            "zero_forbidden_files_under_app": layer_scan["zero_forbidden_files_under_app"],
            "exact_16_allowed_configs_in_image": layer_scan["exact_16_allowed_configs_in_image"],
            "all_app_source_hashes_match": layer_scan["all_app_source_hashes_match"],
            "zero_config_or_history_leaks": len(config_history_leaks) == 0,
            "fail_closed_missing_secret_key": (
                fail_closed_check["rejected_nonzero"] and fail_closed_check["fatal_security_error_present"]
            ),
            "isolated_startup_and_auth_flows_passed": (
                isolated_startup_check["login_html_status"] == 200
                and isolated_startup_check["unauth_me_status"] == 401
                and isolated_startup_check["wrong_password_login_status"] == 400
                and isolated_startup_check["admin_initial_login_status"] == 200
                and isolated_startup_check["admin_initial_must_change_password"] is False
                and isolated_startup_check["admin_me_status"] == 200
                and isolated_startup_check["admin_llm_rules_status"] == 200
                and isolated_startup_check["employee_create_status"] == 200
                and isolated_startup_check["employee_create_must_change_password"] == 1
                and isolated_startup_check["employee_temp_login_status"] == 200
                and isolated_startup_check["employee_temp_must_change_password"] is True
                and isolated_startup_check["employee_me_before_change_status"] == 200
                and isolated_startup_check["employee_llm_rules_before_change_status"] == 403
                and "MUST_CHANGE_PASSWORD" in str(isolated_startup_check["employee_llm_rules_before_change_detail"])
                and isolated_startup_check["employee_profile_change_status"] == 200
                and isolated_startup_check["employee_profile_must_change_password"] is False
                and isolated_startup_check["employee_old_token_after_change_status"] == 401
                and isolated_startup_check["employee_old_temp_password_login_status"] == 400
                and isolated_startup_check["employee_new_login_status"] == 200
                and isolated_startup_check["employee_new_must_change_password"] is False
                and isolated_startup_check["employee_me_after_change_status"] == 200
                and isolated_startup_check["employee_llm_rules_after_change_status"] == 200
                and isolated_startup_check["admin_profile_rotate_status"] == 200
                and isolated_startup_check["admin_old_token_after_rotate_status"] == 401
                and isolated_startup_check["admin_old_password_login_status"] == 400
                and isolated_startup_check["admin_new_password_login_status"] == 200
            ),
            "single_worker_verified": (
                isolated_startup_check["workers_1_cmd_present"]
                and isolated_startup_check["uvicorn_process_count"] == 1
            ),
            "container_runtime_dir_clean": (
                isolated_startup_check["exact_16_container_configs"]
                and isolated_startup_check["zero_db_files_under_app_dir"]
            ),
        }

        report = {
            "gate": "V-M4-01",
            "baseline_head": head,
            "frozen_source_binding": {
                "dockerfile_sha256": source_hashes["Dockerfile"],
                "dockerignore_sha256": source_hashes[".dockerignore"],
                "requirements_sha256": source_hashes["requirements.txt"],
                "total_source_files_bound": len(source_hashes),
                "source_hashes": source_hashes,
            },
            "base_image": {
                "tag": "python:3.11-slim",
                "id": base_inspect.get("Id"),
                "repo_digests": base_inspect.get("RepoDigests", []),
            },
            "build": {
                "command": "docker build --pull=false --no-cache -t listtrans:m4-r03-vm401-prod <TMP/prod_ctx>",
                "offline_flag_used": False,
                "runtime_overlay_substituted": False,
                "scratch_substituted": False,
                "exit_code": build_proc.returncode,
                "wall_seconds": build_wall,
                "image_tag": IMAGE_TAG,
                "image_id": img_inspect.get("Id"),
                "rootfs_layers_count": len(img_inspect.get("RootFS", {}).get("Layers", [])),
                "rootfs_layers": img_inspect.get("RootFS", {}).get("Layers", []),
                "config_cmd": img_inspect.get("Config", {}).get("Cmd"),
                "config_env": img_inspect.get("Config", {}).get("Env"),
                "exposed_ports": list((img_inspect.get("Config", {}).get("ExposedPorts") or {}).keys()),
                "pip_freeze_count": len(pip_freeze_lines),
                "pip_freeze": pip_freeze_lines,
                "build_log": "doc/V0.1.5/evidence/m4_r03_vm401_prod_image_build.log",
            },
            "all_layer_scan": layer_scan,
            "config_history_leaks": config_history_leaks,
            "fail_closed_check": fail_closed_check,
            "isolated_startup_check": isolated_startup_check,
            "checks": all_checks,
            "overall_verdict": "PASS" if all(all_checks.values()) else "FAIL",
        }

        out_json = EV / "m4_r03_vm401_prod_image_verify.json"
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"verdict": report["overall_verdict"], "checks": all_checks, "image_id": report["build"]["image_id"], "layers": report["all_layer_scan"]["total_layers_scanned"], "wall_seconds": build_wall}, ensure_ascii=False))
        assert report["overall_verdict"] == "PASS", f"V-M4-01 checks failed: {all_checks}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
