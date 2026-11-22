# -*- coding: utf-8 -*-
"""V015-M4 R03 / V-M4-02 Redacted Verification & Migration/Rotation Rehearsal.

Strict safety & freeze rules enforced by this script:
1. Never modifies `.env`, `app/auth.db`, `app/materials.db`, configs, code, or any
   existing artifact bound in `m4_r03_vm401_audit_baseline.json` /
   `m4_r03_vm401_audit_delivery_hashes.md`.
2. Never reads, stores, or outputs any plaintext secret, password, API key, JWT,
   personal name, phone number, employee number, or remote host address.
3. Executes end-to-end V0.1.4 -> V0.1.5 schema migration and credential rotation
   across fresh child Python processes against an isolated temporary directory,
   with `LLM_ENABLED=false` and fail-closed deployment boundary checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import runpy
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc" / "V0.1.5" / "evidence"
PYTHON_EXE = Path(sys.executable).resolve()

V014_USER_COLUMNS = [
    "id",
    "username",
    "password_hash",
    "role",
    "is_active",
    "created_at",
    "last_login",
    "name",
    "dept1",
    "dept2",
    "dept3",
    "employee_no",
    "position",
    "phone",
    "need_emp_no_setup",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_delivery_manifest(path: Path) -> Dict[str, str]:
    entries: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]{64})`\s*\|$", line.strip())
        if m:
            entries[m.group(1)] = m.group(2)
    return entries


def inspect_workspace_targets(baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only metadata inspection of the 4 F11/V-M4-02 target areas."""
    protected = baseline["protected_hashes"]

    # 1. Local .env metadata (keys & file hash only; never inspect/emit values)
    env_path = ROOT / ".env"
    env_keys: List[str] = []
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = raw.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                env_keys.append(stripped.split("=", 1)[0].strip())
    env_sha = sha256_file(env_path)
    env_expected = protected[".env"]

    # 2. Local app/auth.db metadata (schema columns & role counts only; no PII/hashes)
    auth_db = ROOT / "app" / "auth.db"
    auth_sha = sha256_file(auth_db)
    conn = sqlite3.connect(auth_db.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        user_cols = [
            r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()
        ]
        role_counts = {
            r[0]: int(r[1])
            for r in conn.execute(
                "SELECT role, COUNT(*) FROM users GROUP BY role ORDER BY role"
            ).fetchall()
        }
    finally:
        conn.close()

    m4_cols = {"token_version", "account_epoch", "must_change_password"}
    is_v014_schema = (user_cols == V014_USER_COLUMNS) and not bool(
        m4_cols.intersection(user_cols)
    )

    # 3. Local app/materials.db LLM provider metadata (names/flags only; no api_key_enc read)
    mat_db = ROOT / "app" / "materials.db"
    mat_sha = sha256_file(mat_db)
    conn_m = sqlite3.connect(mat_db.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        provider_meta = [
            {"provider_id": str(r[0]), "is_active": int(r[1])}
            for r in conn_m.execute(
                "SELECT provider_id, is_active FROM llm_providers ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn_m.close()

    # 4. Deployment scripts bound to baseline
    deploy_scripts = [
        "scripts/_deploy_env.py",
        "scripts/deploy_cloud.py",
        "scripts/deploy_to_cloud_safe.py",
        "scripts/check_cloud_status.py",
        "scripts/probe_servers.py",
    ]
    deploy_hashes_match = all(
        sha256_file(ROOT / p) == protected[p] for p in deploy_scripts
    )

    return {
        "local_env": {
            "exists": env_path.is_file(),
            "sha256": env_sha,
            "matches_audit_protected_baseline": env_sha == env_expected,
            "variable_names": env_keys,
            "variable_count": len(env_keys),
            "mutated_in_workspace": False,
        },
        "local_auth_db": {
            "exists": auth_db.is_file(),
            "sha256": auth_sha,
            "tables": tables,
            "users_columns": user_cols,
            "is_frozen_v014_15col_schema": is_v014_schema,
            "role_counts": role_counts,
            "total_accounts": sum(role_counts.values()),
            "mutated_in_workspace": False,
        },
        "deploy_ssh": {
            "scripts_checked": deploy_scripts,
            "all_scripts_match_audit_baseline": deploy_hashes_match,
            "env_vars_configured_for_v015": False,
            "remote_deployment_enabled": False,
        },
        "llm_provider_config": {
            "materials_db_sha256": mat_sha,
            "provider_metadata_flags": provider_meta,
            "external_llm_calls_in_v015_verification": False,
        },
    }


def _build_isolated_layout(tmp_root: Path) -> Dict[str, Path]:
    layout = {
        "root": tmp_root,
        "materials_db": tmp_root / "db" / "materials.db",
        "auth_db": tmp_root / "db" / "auth.db",
        "config_dir": tmp_root / "config",
        "vector_cache_dir": tmp_root / "vector_cache",
        "upload_dir": tmp_root / "uploads",
        "output_dir": tmp_root / "output",
        "log_dir": tmp_root / "logs",
        "binding_feedback_file": tmp_root / "runtime" / "binding_feedback.json",
        "feedback_samples_file": tmp_root / "runtime" / "feedback_samples.json",
        "golden_detail_file": tmp_root / "runtime" / "golden_last_run.json",
    }
    for k, p in layout.items():
        if k in (
            "materials_db",
            "auth_db",
            "binding_feedback_file",
            "feedback_samples_file",
            "golden_detail_file",
        ):
            p.parent.mkdir(parents=True, exist_ok=True)
        elif k != "root":
            p.mkdir(parents=True, exist_ok=True)
    src_cfg = ROOT / "config"
    for item in src_cfg.iterdir():
        if item.is_file():
            shutil.copy2(item, layout["config_dir"] / item.name)
    return layout


def _seed_v014_synthetic_auth_db(auth_db: Path, creds: Dict[str, str]) -> None:
    """Create a synthetic 15-column V0.1.4 auth.db matching host column structure."""
    import bcrypt

    def mkhash(raw: str) -> str:
        return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = sqlite3.connect(str(auth_db))
    try:
        conn.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'employee',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_login DATETIME,
                name TEXT,
                dept1 TEXT,
                dept2 TEXT,
                dept3 TEXT,
                employee_no TEXT,
                position TEXT,
                phone TEXT,
                need_emp_no_setup INTEGER DEFAULT 0
            )
            """
        )
        rows = [
            (
                "admin",
                mkhash(creds["old_admin_pwd"]),
                "admin",
                1,
                "SynthAdmin",
                "D1",
                "D2",
                "D3",
                "SYN_ADM01",
                "Admin",
                "19900000001",
                0,
            ),
            (
                "syn_dir1",
                mkhash(creds["strong_dir_pwd"]),
                "director",
                1,
                "SynthDirector",
                "D1",
                "D2",
                "D3",
                "SYN_DIR01",
                "Director",
                "19900000002",
                0,
            ),
            (
                "syn_mgr1",
                mkhash(creds["strong_mgr_pwd"]),
                "manager",
                1,
                "SynthManager",
                "D1",
                "D2",
                "D3",
                "SYN_MGR01",
                "Manager",
                "19900000003",
                0,
            ),
            (
                "syn_emp1",
                mkhash(creds["weak_emp_pwd"]),
                "employee",
                1,
                "SynthEmployee",
                "D1",
                "D2",
                "D3",
                "SYN_EMP01",
                "Employee",
                "19900000004",
                0,
            ),
        ]
        conn.executemany(
            """
            INSERT INTO users (
                username, password_hash, role, is_active,
                name, dept1, dept2, dept3, employee_no, position, phone, need_emp_no_setup
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _run_child(
    iso: Dict[str, Path],
    code: str,
    extra_env: Dict[str, str],
    stdin_payload: Dict[str, Any] | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for key in (
        "SECRET_KEY",
        "ADMIN_PASSWORD",
        "AUTH_DEV_MODE",
        "DEPLOY_HOST",
        "DEPLOY_USER",
        "DEPLOY_PORT",
        "DEPLOY_REMOTE_DIR",
        "DEPLOY_SSH_KEY_PATH",
        "DEPLOY_SSH_PASSWORD",
        "DEPLOY_KNOWN_HOSTS",
        "DEPLOY_PROBE_HOSTS",
    ):
        env.pop(key, None)

    env.update(
        {
            "MATERIALS_DB_PATH": str(iso["materials_db"]),
            "AUTH_DB_PATH": str(iso["auth_db"]),
            "CONFIG_DIR": str(iso["config_dir"]),
            "VECTOR_CACHE_DIR": str(iso["vector_cache_dir"]),
            "UPLOAD_DIR": str(iso["upload_dir"]),
            "OUTPUT_DIR": str(iso["output_dir"]),
            "LOG_DIR": str(iso["log_dir"]),
            "BINDING_FEEDBACK_FILE": str(iso["binding_feedback_file"]),
            "FEEDBACK_SAMPLES_FILE": str(iso["feedback_samples_file"]),
            "GOLDEN_DETAIL_FILE": str(iso["golden_detail_file"]),
            "TESTING": "0",
            "APP_ENV": "production",
            "LLM_ENABLED": "false",
            "AUTH_DEV_MODE": "false",
            "DASHSCOPE_API_KEY": "",
            "PYTHONPATH": str(ROOT),
        }
    )
    env.update(extra_env)
    return subprocess.run(
        [str(PYTHON_EXE), "-c", code],
        cwd=str(iso["root"]),
        env=env,
        input=json.dumps(stdin_payload) if stdin_payload is not None else None,
        capture_output=True,
        text=True,
        timeout=90,
    )


def run_isolated_rotation_rehearsal() -> Dict[str, Any]:
    """Run synthetic V0.1.4 -> V0.1.5 migration & credential rotation across real HTTP processes."""
    tmp_root = Path(tempfile.mkdtemp(prefix="m4_r03_vm402_")).resolve()
    try:
        iso = _build_isolated_layout(tmp_root)
        # Ephemeral synthetic credentials used in memory only; never written to report
        old_secret_1 = f"syn_sec_1_{secrets.token_hex(24)}"
        new_secret_2 = f"syn_sec_2_{secrets.token_hex(24)}"
        creds = {
            "old_admin_pwd": f"SynOldAdm!{secrets.token_hex(6)}1",
            "new_admin_pwd": f"SynNewAdm!{secrets.token_hex(6)}2",
            "ignored_env_admin_pwd": f"SynEnvOnly!{secrets.token_hex(6)}3",
            "strong_dir_pwd": f"SynDir!{secrets.token_hex(6)}4",
            "strong_mgr_pwd": f"SynMgr!{secrets.token_hex(6)}5",
            "weak_emp_pwd": "123456",
            "new_emp_pwd": f"SynEmp!{secrets.token_hex(6)}6",
        }
        _seed_v014_synthetic_auth_db(iso["auth_db"], creds)

        # Phase A: V0.1.4 -> V0.1.5 idempotent migration & pre-rotation JWT issuance
        phase_a_code = """
import json, sys
from fastapi.testclient import TestClient
from app import auth_manager, server
from app.audit_manager import init_audit_tables
from app.db_manager import init_db
from app.flywheel_db import init_flywheel_tables
from app.llm_rule_engine import init_rule_engine_db
from app.session_manager import init_session_db

p = json.loads(sys.stdin.read())
init_db()
init_auth_db = auth_manager.init_auth_db
init_auth_db()
init_auth_db()  # verify idempotency on second run
init_audit_tables()
init_session_db()
init_flywheel_tables()
init_rule_engine_db()

conn = auth_manager.get_auth_connection()
rows = conn.execute("SELECT id, username, role, must_change_password, token_version FROM users ORDER BY id").fetchall()
conn.close()
by_user = {r["username"]: dict(r) for r in rows}

client = TestClient(server.app)
adm_login = client.post("/api/auth/login", json={"username": "admin", "password": p["old_admin_pwd"]})
assert adm_login.status_code == 200, adm_login.text
adm_tok_v1 = adm_login.json()["access_token"]
adm_me_v1 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {adm_tok_v1}"})

emp_login = client.post("/api/auth/login", json={"username": "syn_emp1", "password": p["weak_emp_pwd"]})
assert emp_login.status_code == 200, emp_login.text
emp_tok_v1 = emp_login.json()["access_token"]
emp_rules_v1 = client.get("/api/llm-rules", headers={"Authorization": f"Bearer {emp_tok_v1}"})

print(json.dumps({
    "migrated_user_count": len(rows),
    "admin_must_change_after_migration": by_user["admin"]["must_change_password"],
    "director_must_change_after_migration": by_user["syn_dir1"]["must_change_password"],
    "manager_must_change_after_migration": by_user["syn_mgr1"]["must_change_password"],
    "weak_emp_flagged_must_change_after_migration": by_user["syn_emp1"]["must_change_password"],
    "emp_id": by_user["syn_emp1"]["id"],
    "admin_login_v1_status": adm_login.status_code,
    "admin_me_v1_status": adm_me_v1.status_code,
    "weak_emp_login_v1_must_change": emp_login.json()["must_change_password"],
    "weak_emp_rules_blocked_status": emp_rules_v1.status_code,
    "adm_tok_v1": adm_tok_v1,
    "emp_tok_v1": emp_tok_v1,
}))
"""
        pa = _run_child(
            iso,
            phase_a_code,
            {"SECRET_KEY": old_secret_1, "ADMIN_PASSWORD": creds["ignored_env_admin_pwd"]},
            stdin_payload=creds,
        )
        assert pa.returncode == 0, f"Phase A failed: {pa.stderr}\n{pa.stdout}"
        pa_out = json.loads(pa.stdout.strip().splitlines()[-1])

        # Phase B: Rotate SECRET_KEY & ADMIN_PASSWORD env, verify persistent DB credentials,
        # reset employee, force employee password change, and rotate persistent admin password.
        phase_b_code = """
import json, sys
from fastapi.testclient import TestClient
from app import server

p = json.loads(sys.stdin.read())
c = p["creds"]
client = TestClient(server.app)

# 1. Prove ADMIN_PASSWORD env var alone does not overwrite persistent DB admin password
env_only_adm = client.post("/api/auth/login", json={"username": "admin", "password": c["ignored_env_admin_pwd"]})
old_adm_login = client.post("/api/auth/login", json={"username": "admin", "password": c["old_admin_pwd"]})
assert old_adm_login.status_code == 200
adm_tok_v2 = old_adm_login.json()["access_token"]

# 2. Prove SECRET_KEY rotation invalidates Phase A JWT on /api/auth/me
old_secret_jwt_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {p['adm_tok_v1']}"})
new_secret_jwt_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {adm_tok_v2}"})

# 3. Admin resets employee -> unique temp password issued, weak password & prior token rejected
emp_pre_reset_login = client.post("/api/auth/login", json={"username": "syn_emp1", "password": c["weak_emp_pwd"]})
emp_pre_reset_tok = emp_pre_reset_login.json()["access_token"]

reset_res = client.put(f"/api/admin/users/{p['emp_id']}/password", headers={"Authorization": f"Bearer {adm_tok_v2}"})
assert reset_res.status_code == 200, reset_res.text
temp_pwd = reset_res.json()["temp_password"]

emp_old_pwd_after_reset = client.post("/api/auth/login", json={"username": "syn_emp1", "password": c["weak_emp_pwd"]})
emp_old_tok_after_reset = client.get("/api/auth/me", headers={"Authorization": f"Bearer {emp_pre_reset_tok}"})

emp_temp_login = client.post("/api/auth/login", json={"username": "syn_emp1", "password": temp_pwd})
assert emp_temp_login.status_code == 200
emp_temp_tok = emp_temp_login.json()["access_token"]
emp_temp_rules_blocked = client.get("/api/llm-rules", headers={"Authorization": f"Bearer {emp_temp_tok}"})

emp_chg = client.put(
    "/api/auth/profile",
    headers={"Authorization": f"Bearer {emp_temp_tok}"},
    json={"old_password": temp_pwd, "new_password": c["new_emp_pwd"]},
)
assert emp_chg.status_code == 200, emp_chg.text
emp_temp_pwd_rejected = client.post("/api/auth/login", json={"username": "syn_emp1", "password": temp_pwd})
emp_temp_tok_revoked = client.get("/api/auth/me", headers={"Authorization": f"Bearer {emp_temp_tok}"})
emp_new_login = client.post("/api/auth/login", json={"username": "syn_emp1", "password": c["new_emp_pwd"]})
assert emp_new_login.status_code == 200
emp_new_tok = emp_new_login.json()["access_token"]
emp_new_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {emp_new_tok}"})
emp_new_rules = client.get("/api/llm-rules", headers={"Authorization": f"Bearer {emp_new_tok}"})

# 4. Persistent admin self-service password rotation -> old pwd 400, old token 401, new pwd 200
adm_chg = client.put(
    "/api/auth/profile",
    headers={"Authorization": f"Bearer {adm_tok_v2}"},
    json={"old_password": c["old_admin_pwd"], "new_password": c["new_admin_pwd"]},
)
assert adm_chg.status_code == 200, adm_chg.text
adm_old_pwd_rejected = client.post("/api/auth/login", json={"username": "admin", "password": c["old_admin_pwd"]})
adm_old_tok_me_rejected = client.get("/api/auth/me", headers={"Authorization": f"Bearer {adm_tok_v2}"})
adm_old_tok_rules_rejected = client.get("/api/llm-rules", headers={"Authorization": f"Bearer {adm_tok_v2}"})

adm_new_login = client.post("/api/auth/login", json={"username": "admin", "password": c["new_admin_pwd"]})
assert adm_new_login.status_code == 200
adm_tok_v3 = adm_new_login.json()["access_token"]
adm_new_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {adm_tok_v3}"})
adm_new_rules = client.get("/api/llm-rules", headers={"Authorization": f"Bearer {adm_tok_v3}"})

print(json.dumps({
    "env_only_admin_pwd_rejected_status": env_only_adm.status_code,
    "old_secret_jwt_me_status": old_secret_jwt_me.status_code,
    "new_secret_jwt_me_status": new_secret_jwt_me.status_code,
    "emp_reset_status": reset_res.status_code,
    "emp_temp_pwd_unique_generated": bool(temp_pwd and temp_pwd != c["weak_emp_pwd"]),
    "emp_old_pwd_rejected_status": emp_old_pwd_after_reset.status_code,
    "emp_old_tok_rejected_status": emp_old_tok_after_reset.status_code,
    "emp_temp_rules_blocked_status": emp_temp_rules_blocked.status_code,
    "emp_force_change_status": emp_chg.status_code,
    "emp_temp_pwd_rejected_status": emp_temp_pwd_rejected.status_code,
    "emp_temp_tok_revoked_status": emp_temp_tok_revoked.status_code,
    "emp_new_login_status": emp_new_login.status_code,
    "emp_new_must_change": emp_new_login.json()["must_change_password"],
    "emp_new_me_status": emp_new_me.status_code,
    "emp_new_rules_status": emp_new_rules.status_code,
    "admin_rotate_status": adm_chg.status_code,
    "admin_rotate_relogin_required": adm_chg.json().get("relogin_required"),
    "admin_old_pwd_rejected_status": adm_old_pwd_rejected.status_code,
    "admin_old_tok_me_rejected_status": adm_old_tok_me_rejected.status_code,
    "admin_old_tok_rules_rejected_status": adm_old_tok_rules_rejected.status_code,
    "admin_new_login_status": adm_new_login.status_code,
    "admin_new_must_change": adm_new_login.json()["must_change_password"],
    "admin_new_me_status": adm_new_me.status_code,
    "admin_new_rules_status": adm_new_rules.status_code,
    "adm_tok_v2": adm_tok_v2,
    "adm_tok_v3": adm_tok_v3,
}))
"""
        pb = _run_child(
            iso,
            phase_b_code,
            {"SECRET_KEY": new_secret_2, "ADMIN_PASSWORD": creds["ignored_env_admin_pwd"]},
            stdin_payload={
                "creds": creds,
                "adm_tok_v1": pa_out["adm_tok_v1"],
                "emp_id": pa_out["emp_id"],
            },
        )
        assert pb.returncode == 0, f"Phase B failed: {pb.stderr}\n{pb.stdout}"
        pb_out = json.loads(pb.stdout.strip().splitlines()[-1])

        # Phase C: Fresh process persistence verification, fail-closed checks (SECRET_KEY / SSH / LLM)
        phase_c_code = """
import json, sys
from fastapi.testclient import TestClient
from app import server
from app.llm_client import QwenLLMClient
from scripts._deploy_env import DeployConfigError, load_probe_targets, load_ssh_deploy_config

p = json.loads(sys.stdin.read())
c = p["creds"]
client = TestClient(server.app)

old_pwd_after_restart = client.post("/api/auth/login", json={"username": "admin", "password": c["old_admin_pwd"]})
old_tok_after_restart = client.get("/api/auth/me", headers={"Authorization": f"Bearer {p['adm_tok_v2']}"})
new_tok_after_restart = client.get("/api/auth/me", headers={"Authorization": f"Bearer {p['adm_tok_v3']}"})

ssh_failed_closed = False
probe_failed_closed = False
try:
    load_ssh_deploy_config()
except DeployConfigError:
    ssh_failed_closed = True
try:
    load_probe_targets()
except DeployConfigError:
    probe_failed_closed = True

llm = QwenLLMClient()
llm_enabled = llm.is_enabled()

print(json.dumps({
    "admin_old_pwd_after_restart_status": old_pwd_after_restart.status_code,
    "admin_old_tok_after_restart_status": old_tok_after_restart.status_code,
    "admin_new_tok_after_restart_status": new_tok_after_restart.status_code,
    "ssh_load_failed_closed": ssh_failed_closed,
    "probe_targets_failed_closed": probe_failed_closed,
    "llm_is_enabled_under_offline_flag": llm_enabled,
}))
"""
        pc = _run_child(
            iso,
            phase_c_code,
            {"SECRET_KEY": new_secret_2, "ADMIN_PASSWORD": creds["ignored_env_admin_pwd"]},
            stdin_payload={
                "creds": creds,
                "adm_tok_v2": pb_out["adm_tok_v2"],
                "adm_tok_v3": pb_out["adm_tok_v3"],
            },
        )
        assert pc.returncode == 0, f"Phase C failed: {pc.stderr}\n{pc.stdout}"
        pc_out = json.loads(pc.stdout.strip().splitlines()[-1])

        # Fail-closed check when SECRET_KEY is omitted in production mode
        p_fail_secret = _run_child(
            iso,
            "import app.auth_manager",
            {},
        )
        secret_fail_closed = p_fail_secret.returncode != 0 and (
            "FATAL SECURITY ERROR" in (p_fail_secret.stderr + p_fail_secret.stdout)
        )

        # Strip ephemeral tokens from recorded outputs
        for d in (pa_out, pb_out):
            d.pop("adm_tok_v1", None)
            d.pop("emp_tok_v1", None)
            d.pop("adm_tok_v2", None)
            d.pop("adm_tok_v3", None)

        return {
            "isolated_rehearsal_only": True,
            "workspace_db_touched": False,
            "phase_a_migration_and_pre_rotation": pa_out,
            "phase_b_rotation_verification": pb_out,
            "phase_c_restart_persistence_and_fail_closed": {
                **pc_out,
                "missing_secret_key_fail_closed": secret_fail_closed,
            },
        }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def evaluate_checks(targets: Dict[str, Any], rehearsal: Dict[str, Any], integrity: Dict[str, Any]) -> Dict[str, bool]:
    pa = rehearsal["phase_a_migration_and_pre_rotation"]
    pb = rehearsal["phase_b_rotation_verification"]
    pc = rehearsal["phase_c_restart_persistence_and_fail_closed"]
    return {
        "workspace_env_unchanged": targets["local_env"]["matches_audit_protected_baseline"],
        "workspace_auth_db_v014_frozen": targets["local_auth_db"]["is_frozen_v014_15col_schema"],
        "deploy_scripts_match_baseline": targets["deploy_ssh"]["all_scripts_match_audit_baseline"],
        "v014_to_v015_migration_idempotent_4_roles": (
            pa["migrated_user_count"] == 4
            and pa["admin_must_change_after_migration"] == 0
            and pa["director_must_change_after_migration"] == 0
            and pa["manager_must_change_after_migration"] == 0
            and pa["weak_emp_flagged_must_change_after_migration"] == 1
        ),
        "secret_key_rotation_invalidates_old_jwt": (
            pa["admin_me_v1_status"] == 200
            and pb["old_secret_jwt_me_status"] == 401
            and pb["new_secret_jwt_me_status"] == 200
            and pc["missing_secret_key_fail_closed"] is True
        ),
        "admin_password_env_does_not_mutate_existing_db": (
            pb["env_only_admin_pwd_rejected_status"] == 400
        ),
        "persistent_admin_rotation_Verified_400_401_200": (
            pb["admin_rotate_status"] == 200
            and pb["admin_rotate_relogin_required"] is True
            and pb["admin_old_pwd_rejected_status"] == 400
            and pb["admin_old_tok_me_rejected_status"] == 401
            and pb["admin_old_tok_rules_rejected_status"] == 401
            and pb["admin_new_login_status"] == 200
            and pb["admin_new_must_change"] is False
            and pb["admin_new_me_status"] == 200
            and pb["admin_new_rules_status"] == 200
            and pc["admin_old_pwd_after_restart_status"] == 400
            and pc["admin_old_tok_after_restart_status"] == 401
            and pc["admin_new_tok_after_restart_status"] == 200
        ),
        "employee_reset_and_forced_change_verified_400_401_403_200": (
            pb["emp_reset_status"] == 200
            and pb["emp_temp_pwd_unique_generated"] is True
            and pb["emp_old_pwd_rejected_status"] == 400
            and pb["emp_old_tok_rejected_status"] == 401
            and pb["emp_temp_rules_blocked_status"] == 403
            and pb["emp_force_change_status"] == 200
            and pb["emp_temp_pwd_rejected_status"] == 400
            and pb["emp_temp_tok_revoked_status"] == 401
            and pb["emp_new_login_status"] == 200
            and pb["emp_new_must_change"] is False
            and pb["emp_new_me_status"] == 200
            and pb["emp_new_rules_status"] == 200
        ),
        "ssh_deploy_fails_closed_when_unconfigured": (
            pc["ssh_load_failed_closed"] is True
            and pc["probe_targets_failed_closed"] is True
        ),
        "llm_disabled_under_offline_flag": pc["llm_is_enabled_under_offline_flag"] is False,
        "business_snapshot_and_vm401_delivery_intact": (
            integrity["business_snapshot_equal"] is True
            and integrity["protected_mismatches"] == []
            and integrity["vm401_delivery_mismatches"] == []
        ),
    }


def main() -> int:
    baseline = json.loads((EV / "m4_r03_vm401_audit_baseline.json").read_text(encoding="utf-8"))
    snapshot_before_bytes = (EV / "m4_r03_vm401_audit_snapshot_before.json").read_bytes()
    snapshot_before = json.loads(snapshot_before_bytes)
    snapshot_before_sha = hashlib.sha256(snapshot_before_bytes).hexdigest()

    targets = inspect_workspace_targets(baseline)
    rehearsal = run_isolated_rotation_rehearsal()

    # Verify host isolation snapshot and existing V-M4-01 frozen deliverables remained untouched
    collect_snapshot = runpy.run_path(str(ROOT / "tests" / "verify_isolation.py"))["collect_snapshot"]
    snapshot_after = collect_snapshot()
    line_sep = "\r\n" if b"\r\n" in snapshot_before_bytes else "\n"
    snapshot_after_sha = hashlib.sha256(
        json.dumps(snapshot_after, indent=1, sort_keys=True)
        .replace("\n", line_sep)
        .encode("utf-8")
    ).hexdigest()
    snapshot_equal = snapshot_after == snapshot_before

    vm401_manifest = parse_delivery_manifest(EV / "m4_r03_vm401_audit_delivery_hashes.md")
    # Active status/ledger docs updated in V-M4-01 supplement audit are bound in vm401_manifest
    protected_mismatches = [
        p
        for p, expected in baseline["protected_hashes"].items()
        if p not in vm401_manifest and (not (ROOT / p).is_file() or sha256_file(ROOT / p) != expected)
    ]
    vm401_mismatches = [
        p
        for p, expected in vm401_manifest.items()
        if not (ROOT / p).is_file() or sha256_file(ROOT / p) != expected
    ]

    integrity = {
        "business_snapshot_equal": snapshot_equal,
        "snapshot_before_sha256": snapshot_before_sha,
        "snapshot_after_sha256": snapshot_after_sha,
        "protected_checked": len(baseline["protected_hashes"]),
        "protected_mismatches": protected_mismatches,
        "vm401_delivery_checked": len(vm401_manifest),
        "vm401_delivery_mismatches": vm401_mismatches,
    }

    checks = evaluate_checks(targets, rehearsal, integrity)
    overall_pass = all(checks.values())

    report = {
        "gate": "V-M4-02",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "mode": "read_only_workspace_audit_and_isolated_v014_v015_rehearsal",
        "workspace_targets_audit": targets,
        "isolated_rotation_rehearsal": rehearsal,
        "integrity_verification": integrity,
        "checks": checks,
        "overall_verdict": "PASS" if overall_pass else "FAIL",
        "note": (
            "Workspace .env, app/auth.db, and app/materials.db were NOT mutated because they are "
            "frozen under the V015 audit snapshot and baseline rules. External SSH and live LLM API "
            "keys are disabled/not applicable in V0.1.5 local development pending Pre-Release Deployment Gate."
        ),
    }

    out_path = EV / "m4_r03_vm402_verify.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "overall_verdict": report["overall_verdict"],
                "checks_passed": sum(1 for v in checks.values() if v),
                "checks_total": len(checks),
                "business_snapshot_equal": snapshot_equal,
                "protected_mismatches": len(protected_mismatches),
                "vm401_delivery_mismatches": len(vm401_mismatches),
                "out": str(out_path),
            },
            ensure_ascii=True,
        )
    )
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
