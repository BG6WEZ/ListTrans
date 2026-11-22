# -*- coding: utf-8 -*-
"""V015-M4 / F22: Auth lifecycle, fail-closed startup, atomic invalidation, and persistent revocation tests."""

import logging
import os
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import auth_manager, server
from app.audit_manager import init_audit_tables
from app.auth_manager import (
    admin_create_user_atomic,
    authenticate_user_and_issue_token,
    get_auth_connection,
    get_password_hash,
    get_user_by_username,
    import_users_from_excel,
    init_auth_db,
    verify_password,
)


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_prod_auth_probe(tmp_dir, tmp_db: str, extra_env: dict, code: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["TESTING"] = "0"
    env["LLM_ENABLED"] = "false"
    env["AUTH_DB_PATH"] = tmp_db
    env["PYTHONPATH"] = PROJECT_ROOT
    log_dir = tmp_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    env["LOG_DIR"] = str(log_dir)
    env.pop("SECRET_KEY", None)
    env.pop("ADMIN_PASSWORD", None)
    env.pop("AUTH_DEV_MODE", None)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_fail_closed_production_secret_and_admin_password(tmp_path):
    """Production mode (TESTING=0) must fail closed without strong SECRET_KEY and strong ADMIN_PASSWORD."""
    p1 = _run_prod_auth_probe(tmp_path, str(tmp_path / "prod_no_secret.db"), {}, "import app.auth_manager")
    assert p1.returncode != 0
    assert "SECRET_KEY" in (p1.stderr + p1.stdout)

    p2 = _run_prod_auth_probe(
        tmp_path,
        str(tmp_path / "prod_weak_secret.db"),
        {"SECRET_KEY": "changeme"},
        "import app.auth_manager",
    )
    assert p2.returncode != 0

    strong_secret = "M4_Strong_Prod_Secret_Key_9876543210_ABCDEF"
    p3 = _run_prod_auth_probe(
        tmp_path,
        str(tmp_path / "prod_no_admin_pwd.db"),
        {"SECRET_KEY": strong_secret},
        "import app.auth_manager as am; am.init_auth_db()",
    )
    assert p3.returncode != 0
    assert "ADMIN_PASSWORD" in (p3.stderr + p3.stdout)

    p4 = _run_prod_auth_probe(
        tmp_path,
        str(tmp_path / "prod_weak_admin_pwd.db"),
        {"SECRET_KEY": strong_secret, "ADMIN_PASSWORD": "hkjj@12345"},
        "import app.auth_manager as am; am.init_auth_db()",
    )
    assert p4.returncode != 0

    strong_admin_pwd = "ProdAdm#Strong2026!"
    p5 = _run_prod_auth_probe(
        tmp_path,
        str(tmp_path / "prod_ok.db"),
        {"SECRET_KEY": strong_secret, "ADMIN_PASSWORD": strong_admin_pwd},
        (
            "import app.auth_manager as am; am.init_auth_db(); "
            "u = am.get_user_by_username('admin'); "
            "assert u is not None and int(u['must_change_password']) == 0"
        ),
    )
    assert p5.returncode == 0, p5.stderr
    combined_out = p5.stdout + "\n" + p5.stderr
    assert strong_admin_pwd not in combined_out
    assert strong_secret not in combined_out


def test_testing_blocks_dev_mode_and_auto_excel_import(tmp_path, monkeypatch):
    """Under TESTING=1, AUTH_DEV_MODE is disabled and init_auth_db never imports doc/人员清单.xls."""
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    isolated_db = str(tmp_path / "no_auto_excel.db")
    monkeypatch.setenv("AUTH_DB_PATH", isolated_db)
    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", isolated_db)

    init_auth_db()
    conn = get_auth_connection()
    rows = conn.execute("SELECT username FROM users ORDER BY id").fetchall()
    conn.close()
    assert [r["username"] for r in rows] == ["admin"]

    blocked = import_users_from_excel()
    assert blocked["success"] is False

    from app.auth_middleware import get_current_user
    import asyncio
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user(credentials=None))
    assert exc.value.status_code == 401


def test_legacy_auth_db_idempotent_and_transactional_migration(tmp_path, monkeypatch):
    """Legacy users schema migrates idempotently, preserves real hashes, flags weak defaults, and rolls back on failure."""
    legacy_db = str(tmp_path / "legacy_auth.db")
    conn = sqlite3.connect(legacy_db)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            dept1 TEXT,
            dept2 TEXT,
            dept3 TEXT,
            employee_no TEXT UNIQUE,
            position TEXT,
            phone TEXT,
            role TEXT NOT NULL DEFAULT 'employee',
            is_active INTEGER NOT NULL DEFAULT 1,
            need_emp_no_setup INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_login DATETIME
        );
        """
    )
    real_hash = get_password_hash("RealStrongPwd#7788")
    weak_emp_hash = get_password_hash("123456")
    weak_admin_hash = get_password_hash("hkjj@12345")
    conn.execute(
        "INSERT INTO users (username, password_hash, name, employee_no, role, is_active) VALUES (?,?,?,?,?,1)",
        ("admin", weak_admin_hash, "旧管理员", "ADMIN_001", "admin"),
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, name, employee_no, role, is_active) VALUES (?,?,?,?,?,1)",
        ("13800000001", weak_emp_hash, "弱密码员工", "E001", "employee"),
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, name, employee_no, role, is_active) VALUES (?,?,?,?,?,1)",
        ("13800000002", real_hash, "正常密码员工", "E002", "employee"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("AUTH_DB_PATH", legacy_db)
    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", legacy_db)
    init_auth_db()
    init_auth_db()

    adm = get_user_by_username("admin")
    weak_emp = get_user_by_username("13800000001")
    real_emp = get_user_by_username("13800000002")
    assert adm["must_change_password"] == 1
    assert weak_emp["must_change_password"] == 1
    assert real_emp["must_change_password"] == 0
    assert verify_password("RealStrongPwd#7788", real_emp["password_hash"])
    assert len({adm["account_epoch"], weak_emp["account_epoch"], real_emp["account_epoch"]}) == 3
    assert all(u["token_version"] == 1 for u in (adm, weak_emp, real_emp))

    # Transactional rollback check when migration raises midway
    broken_db = str(tmp_path / "broken_migration.db")
    conn_b = sqlite3.connect(broken_db)
    conn_b.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'employee', is_active INTEGER NOT NULL DEFAULT 1)"
    )
    conn_b.execute(
        "INSERT INTO users (username, password_hash, role, is_active) VALUES ('u_keep', ?, 'employee', 1)",
        (real_hash,),
    )
    conn_b.commit()
    conn_b.close()

    monkeypatch.setenv("AUTH_DB_PATH", broken_db)
    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", broken_db)
    with patch("app.auth_manager._is_known_weak_default_hash", side_effect=RuntimeError("injected migration failure")):
        with pytest.raises(RuntimeError, match="injected migration failure"):
            init_auth_db()

    conn_check = sqlite3.connect(broken_db)
    cols = {r[1] for r in conn_check.execute("PRAGMA table_info(users)").fetchall()}
    conn_check.close()
    assert "account_epoch" not in cols
    assert "token_version" not in cols


def test_temp_passwords_must_change_and_token_invalidation_http(tmp_path, monkeypatch, caplog):
    """Cover unique temp passwords, forced-change 403 gating, re-login, role/disable/recreate epoch invalidation, and persistent logout."""
    test_db = str(tmp_path / "m4_http_lifecycle.db")
    monkeypatch.setenv("AUTH_DB_PATH", test_db)
    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", test_db)
    init_auth_db()
    init_audit_tables()

    conn = get_auth_connection()
    adm_pwd = "AdmRoot#2026Auth!"
    conn.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
        (get_password_hash(adm_pwd),),
    )
    conn.commit()
    conn.close()

    client = TestClient(server.app)
    login_adm = client.post("/api/auth/login", json={"username": "admin", "password": adm_pwd})
    assert login_adm.status_code == 200, login_adm.text
    adm_token = login_adm.json()["access_token"]
    adm_headers = {"Authorization": f"Bearer {adm_token}"}

    # Reject weak passwords on create
    bad_create = client.post(
        "/api/admin/users",
        headers=adm_headers,
        json={"username": "weak_user", "password": "hkjj@12345", "employee_no": "M4-WEAK", "role": "employee"},
    )
    assert bad_create.status_code == 400

    # Create 2 users omitting password -> unique one-time temp passwords + no secret in logs
    with caplog.at_level(logging.INFO, logger="security"):
        c1 = client.post(
            "/api/admin/users",
            headers=adm_headers,
            json={"username": "m4_emp1", "name": "员工一", "employee_no": "M4-E01", "role": "employee"},
        )
        c2 = client.post(
            "/api/admin/users",
            headers=adm_headers,
            json={"username": "m4_emp2", "name": "员工二", "employee_no": "M4-E02", "role": "employee"},
        )
    assert c1.status_code == 200, c1.text
    assert c2.status_code == 200, c2.text
    temp1 = c1.json()["temp_password"]
    temp2 = c2.json()["temp_password"]
    assert temp1 and temp2 and temp1 != temp2
    assert c1.json()["must_change_password"] == 1

    log_blob = "\n".join(r.getMessage() for r in caplog.records)
    assert temp1 not in log_blob
    assert temp2 not in log_blob

    users_list = client.get("/api/admin/users", headers=adm_headers).json()
    serialized_list = str(users_list)
    assert temp1 not in serialized_list and "password_hash" not in serialized_list

    # Synthetic Excel sync creates unique temp passwords too
    class _FakeSheet:
        nrows = 3
        ncols = 3
        _rows = [
            ["姓名", "手机号", "工号"],
            ["表员工A", "13900001111", "XLS-01"],
            ["表员工B", "13900002222", "XLS-02"],
        ]

        def cell_value(self, r, c):
            return self._rows[r][c]

    fake_book = SimpleNamespace(sheet_by_index=lambda _idx: _FakeSheet())
    xls_stub = tmp_path / "mock_personnel.xls"
    xls_stub.write_bytes(b"stub")
    monkeypatch.setitem(sys.modules, "xlrd", SimpleNamespace(open_workbook=lambda _p: fake_book))
    xls_res = import_users_from_excel(excel_path=str(xls_stub))
    assert xls_res["success"] is True and xls_res["imported_count"] == 2
    creds = xls_res["created_credentials"]
    assert creds[0]["temp_password"] != creds[1]["temp_password"]
    assert creds[0]["temp_password"] not in "\n".join(r.getMessage() for r in caplog.records)

    # First login with temp password enforces must_change_password=True and blocks business routes
    l1 = client.post("/api/auth/login", json={"username": "m4_emp1", "password": temp1})
    assert l1.status_code == 200 and l1.json()["must_change_password"] is True
    t1 = l1.json()["access_token"]
    h1 = {"Authorization": f"Bearer {t1}"}

    for method, path, kw in (
        ("get", "/api/llm-rules", {}),
        ("get", "/api/session/list", {}),
        ("post", "/api/flywheel/record", {"json": {}}),
    ):
        r_blk = getattr(client, method)(path, headers=h1, **kw)
        assert r_blk.status_code == 403, (path, r_blk.text)
        assert "MUST_CHANGE_PASSWORD" in r_blk.json()["detail"]

    # Allowed auth endpoints remain accessible
    assert client.get("/api/auth/me", headers=h1).status_code == 200
    assert client.post("/api/auth/heartbeat", headers=h1, json={"current_page": "workbench"}).status_code == 200

    # Same password or weak password rejected
    assert client.put(
        "/api/auth/profile",
        headers=h1,
        json={"old_password": temp1, "new_password": temp1},
    ).status_code == 400
    assert client.put(
        "/api/auth/profile",
        headers=h1,
        json={"old_password": temp1, "new_password": "123456"},
    ).status_code == 422
    assert client.put(
        "/api/auth/profile",
        headers=h1,
        json={"old_password": temp1, "new_password": "hkjj@12345"},
    ).status_code == 400

    # Valid password change forces relogin and invalidates old temp password + old token
    new_pwd1 = "Emp1Strong#2026!"
    prof = client.put(
        "/api/auth/profile",
        headers=h1,
        json={"old_password": temp1, "new_password": new_pwd1},
    )
    assert prof.status_code == 200, prof.text
    assert prof.json()["relogin_required"] is True
    assert prof.json()["access_token"] is None
    assert client.post("/api/auth/login", json={"username": "m4_emp1", "password": temp1}).status_code == 400
    assert client.get("/api/auth/me", headers=h1).status_code == 401

    l1_new = client.post("/api/auth/login", json={"username": "m4_emp1", "password": new_pwd1})
    assert l1_new.status_code == 200 and l1_new.json()["must_change_password"] is False
    t1_new = l1_new.json()["access_token"]
    h1_new = {"Authorization": f"Bearer {t1_new}"}
    assert client.get("/api/llm-rules", headers=h1_new).status_code == 200

    # Username change requires valid old_password and invalidates old token immediately
    u1_id = c1.json()["id"]
    bad_rename = client.put(
        "/api/auth/profile",
        headers=h1_new,
        json={"old_password": "WrongPwd#123", "new_username": "m4_emp1_renamed"},
    )
    assert bad_rename.status_code == 400
    rename_res = client.put(
        "/api/auth/profile",
        headers=h1_new,
        json={"old_password": new_pwd1, "new_username": "m4_emp1_renamed"},
    )
    assert rename_res.status_code == 200 and rename_res.json()["relogin_required"] is True
    assert client.get("/api/auth/me", headers=h1_new).status_code == 401

    l1_ren = client.post("/api/auth/login", json={"username": "m4_emp1_renamed", "password": new_pwd1})
    assert l1_ren.status_code == 200
    h1_ren = {"Authorization": f"Bearer {l1_ren.json()['access_token']}"}

    # Admin role promotion bumps token_version -> active token invalidated
    role_upd = client.put(f"/api/admin/users/{u1_id}", headers=adm_headers, json={"role": "manager"})
    assert role_upd.status_code == 200
    assert client.get("/api/auth/me", headers=h1_ren).status_code == 401

    # Re-login, then admin reset password invalidates active token & restores must_change_password
    l1_mgr = client.post("/api/auth/login", json={"username": "m4_emp1_renamed", "password": new_pwd1})
    assert l1_mgr.status_code == 200
    h1_mgr = {"Authorization": f"Bearer {l1_mgr.json()['access_token']}"}

    bad_reset = client.put(
        f"/api/admin/users/{u1_id}/password",
        headers=adm_headers,
        json={"new_password": "password"},
    )
    assert bad_reset.status_code == 400
    rst = client.put(f"/api/admin/users/{u1_id}/password", headers=adm_headers, json={})
    assert rst.status_code == 200
    rst_pwd = rst.json()["temp_password"]
    assert rst_pwd and rst_pwd != new_pwd1
    assert client.get("/api/auth/me", headers=h1_mgr).status_code == 401

    # Admin disable invalidates active token & blocks login
    l1_rst = client.post("/api/auth/login", json={"username": "m4_emp1_renamed", "password": rst_pwd})
    assert l1_rst.status_code == 200
    h1_rst = {"Authorization": f"Bearer {l1_rst.json()['access_token']}"}
    dis = client.put(f"/api/admin/users/{u1_id}", headers=adm_headers, json={"is_active": False})
    assert dis.status_code == 200
    assert client.get("/api/auth/me", headers=h1_rst).status_code == 401
    assert client.post("/api/auth/login", json={"username": "m4_emp1_renamed", "password": rst_pwd}).status_code == 403

    # Recreating deleted account with identical id + username yields new account_epoch -> old token rejected
    l2 = client.post("/api/auth/login", json={"username": "m4_emp2", "password": temp2})
    t2_old = l2.json()["access_token"]
    h2_old = {"Authorization": f"Bearer {t2_old}"}
    u2_id = c2.json()["id"]
    conn2 = get_auth_connection()
    conn2.execute("DELETE FROM users WHERE id = ?", (u2_id,))
    conn2.execute(
        """
        INSERT INTO users (id, username, password_hash, name, employee_no, role, is_active, token_version, must_change_password, account_epoch)
        VALUES (?, 'm4_emp2', ?, '同名新账号', 'M4-E02-NEW', 'employee', 1, 1, 0, 'new_recreated_epoch_99')
        """,
        (u2_id, get_password_hash("Emp2New#2026!")),
    )
    conn2.commit()
    conn2.close()
    assert client.get("/api/auth/me", headers=h2_old).status_code == 401

    # Logout persists jti revocation across app/TestClient restarts
    assert client.post("/api/auth/logout", headers=adm_headers).status_code == 200
    restarted_client = TestClient(server.app)
    assert restarted_client.get("/api/auth/me", headers=adm_headers).status_code == 401

    # Corrupted/unreadable auth DB fails closed (503) rather than silent fallback
    fresh_login = restarted_client.post("/api/auth/login", json={"username": "admin", "password": adm_pwd})
    fresh_hdr = {"Authorization": f"Bearer {fresh_login.json()['access_token']}"}
    with patch("app.auth_manager.get_auth_connection", side_effect=sqlite3.OperationalError("db locked/corrupt")):
        fail_me = restarted_client.get("/api/auth/me", headers=fresh_hdr)
        assert fail_me.status_code == 503


def test_concurrent_login_vs_password_change_race(tmp_path, monkeypatch):
    """If credentials change between password verification and snapshot re-check, login refuses to mint a token."""
    race_db = str(tmp_path / "race_auth.db")
    monkeypatch.setenv("AUTH_DB_PATH", race_db)
    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", race_db)
    init_auth_db()
    old_pwd = "RaceInit#2026!"
    new_pwd = "RaceNext#2026!"
    created = admin_create_user_atomic("race_user", role="employee", password=old_pwd, employee_no="RACE-1")
    assert created["success"] is True
    old_pwd = created["temp_password"]
    uid = created["id"]

    entered_verify = threading.Event()
    allow_finish_verify = threading.Event()
    orig_verify = auth_manager.verify_password
    lock = threading.Lock()
    blocked_once = False

    def blocking_verify(plain, hashed):
        nonlocal blocked_once
        res = orig_verify(plain, hashed)
        should_block = False
        with lock:
            if not blocked_once:
                blocked_once = True
                should_block = True
        if should_block:
            entered_verify.set()
            allow_finish_verify.wait(timeout=5)
        return res

    outcome = {}

    def login_worker():
        with patch("app.auth_manager.verify_password", side_effect=blocking_verify):
            outcome["res"] = authenticate_user_and_issue_token("race_user", old_pwd)

    t = threading.Thread(target=login_worker)
    t.start()
    assert entered_verify.wait(timeout=5)

    chg = auth_manager.change_user_credentials_atomic(uid, old_password=old_pwd, new_password=new_pwd)
    assert chg["success"] is True
    allow_finish_verify.set()
    t.join(timeout=5)

    user_res, tok_res, status_res = outcome["res"]
    assert tok_res is None
    assert status_res == "credential_changed_concurrently"
    assert user_res is not None


def test_r02_a_m4_01_excel_sync_role_change_revokes_all_old_tokens(tmp_path, monkeypatch):
    """A-M4-01: Excel sync role changes must atomically bump token_version and revoke all prior concurrent tokens."""
    db_file = str(tmp_path / "r02_excel_role.db")
    monkeypatch.setenv("AUTH_DB_PATH", db_file)
    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", db_file)
    monkeypatch.setenv("ADMIN_PASSWORD", "BootstrapAdmin#2026!")
    init_auth_db()
    init_audit_tables()

    class _Sheet:
        def __init__(self, rows):
            self._rows = rows
            self.nrows = len(rows)
            self.ncols = len(rows[0])

        def cell_value(self, r, c):
            return self._rows[r][c]

    xls_stub = tmp_path / "r02_sync.xls"
    xls_stub.write_bytes(b"synthetic")

    def run_sync(rows, default_password=None):
        book = SimpleNamespace(sheet_by_index=lambda _i: _Sheet(rows))
        with patch.dict(sys.modules, {"xlrd": SimpleNamespace(open_workbook=lambda _p: book)}):
            return import_users_from_excel(excel_path=str(xls_stub), default_password=default_password)

    # 1. Create manager via Excel sync, activate with self-chosen password, mint two concurrent tokens
    res1 = run_sync([
        ["姓名", "部门1级", "部门2级", "工号", "职位", "手机号"],
        ["王同期", "供应链", "采购部", "R02-SYNC-01", "采购经理", "13900007777"],
    ])
    assert res1["success"] is True and res1["imported_count"] == 1
    temp_pwd = res1["created_credentials"][0]["temp_password"]
    u_created = get_user_by_username("13900007777")
    assert u_created is not None and u_created["role"] == "manager"

    user_pwd = "WangStrong#2026!"
    chg = auth_manager.change_user_credentials_atomic(u_created["id"], old_password=temp_pwd, new_password=user_pwd)
    assert chg["success"] is True

    _, tok_old_1, st1 = authenticate_user_and_issue_token("13900007777", user_pwd)
    _, tok_old_2, st2 = authenticate_user_and_issue_token("13900007777", user_pwd)
    assert st1 == "ok" and st2 == "ok" and tok_old_1 and tok_old_2 and tok_old_1 != tok_old_2

    client = TestClient(server.app)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok_old_1}"}).status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok_old_2}"}).status_code == 200
    tv_before = get_user_by_username("13900007777")["token_version"]

    # 2. Excel sync changes position from manager -> employee: token_version bumps atomically, BOTH old tokens 401
    res2 = run_sync([
        ["姓名", "部门1级", "部门2级", "工号", "职位", "手机号"],
        ["王同期", "供应链", "采购部", "R02-SYNC-01", "采购专员", "13900007777"],
    ])
    assert res2["success"] is True and res2["updated_count"] == 1 and res2["role_changed_count"] == 1
    u_after_demote = get_user_by_username("13900007777")
    assert u_after_demote["role"] == "employee"
    assert u_after_demote["token_version"] == tv_before + 1
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok_old_1}"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok_old_2}"}).status_code == 401

    # 3. Fresh login obtains new role token and works
    login_new = client.post("/api/auth/login", json={"username": "13900007777", "password": user_pwd})
    assert login_new.status_code == 200
    assert login_new.json()["role"] == "employee"
    tok_new = login_new.json()["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok_new}"}).status_code == 200

    # 4. Excel sync without role change (only dept2 updates) preserves token_version and active token
    tv_mid = get_user_by_username("13900007777")["token_version"]
    res3 = run_sync([
        ["姓名", "部门1级", "部门2级", "工号", "职位", "手机号"],
        ["王同期", "供应链", "商务部", "R02-SYNC-01", "采购专员", "13900007777"],
    ])
    assert res3["success"] is True and res3["updated_count"] == 1 and res3["role_changed_count"] == 0
    u_after_profile = get_user_by_username("13900007777")
    assert u_after_profile["dept2"] == "商务部"
    assert u_after_profile["token_version"] == tv_mid
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok_new}"}).status_code == 200

    # 5. Admin-assigned role changed back via Excel sync also invalidates concurrent admin tokens immediately
    auth_manager.admin_update_user_atomic(u_after_profile["id"], {"role": "admin"})
    _, adm_tok_1, _ = authenticate_user_and_issue_token("13900007777", user_pwd)
    _, adm_tok_2, _ = authenticate_user_and_issue_token("13900007777", user_pwd)
    assert client.get("/api/admin/users", headers={"Authorization": f"Bearer {adm_tok_1}"}).status_code == 200
    res4 = run_sync([
        ["姓名", "部门1级", "部门2级", "工号", "职位", "手机号"],
        ["王同期", "供应链", "商务部", "R02-SYNC-01", "采购专员", "13900007777"],
    ])
    assert res4["role_changed_count"] == 1
    assert client.get("/api/admin/users", headers={"Authorization": f"Bearer {adm_tok_1}"}).status_code == 401
    assert client.get("/api/admin/users", headers={"Authorization": f"Bearer {adm_tok_2}"}).status_code == 401


def test_r02_a_m4_03_unique_temp_passwords_across_create_reset_and_excel_import(tmp_path, monkeypatch):
    """A-M4-03: Explicit caller password/new_password/default_password never produce shared temporary credentials."""
    db_file = str(tmp_path / "r02_unique_pwds.db")
    monkeypatch.setenv("AUTH_DB_PATH", db_file)
    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", db_file)
    monkeypatch.setenv("ADMIN_PASSWORD", "BootstrapAdmin#2026!")
    init_auth_db()
    init_audit_tables()

    client = TestClient(server.app)
    adm_login = client.post("/api/auth/login", json={"username": "admin", "password": "BootstrapAdmin#2026!"})
    assert adm_login.status_code == 200
    adm_hdr = {"Authorization": f"Bearer {adm_login.json()['access_token']}"}

    # Weak explicit password still rejected with 400
    weak_c = client.post(
        "/api/admin/users",
        headers=adm_hdr,
        json={"username": "r02_weak", "employee_no": "R02-WK", "role": "employee", "password": "hkjj@12345"},
    )
    assert weak_c.status_code == 400

    # Shared strong password in POST /api/admin/users is ignored; each user gets distinct server temp_password
    shared_req = "SharedExplicit#2026!"
    c1 = client.post(
        "/api/admin/users",
        headers=adm_hdr,
        json={"username": "r02_u1", "employee_no": "R02-U1", "role": "employee", "password": shared_req},
    )
    c2 = client.post(
        "/api/admin/users",
        headers=adm_hdr,
        json={"username": "r02_u2", "employee_no": "R02-U2", "role": "employee", "password": shared_req},
    )
    assert c1.status_code == 200 and c2.status_code == 200
    tp1 = c1.json()["temp_password"]
    tp2 = c2.json()["temp_password"]
    assert tp1 != tp2 and tp1 != shared_req and tp2 != shared_req
    assert client.post("/api/auth/login", json={"username": "r02_u1", "password": shared_req}).status_code == 400

    # Shared strong new_password in PUT /api/admin/users/{id}/password yields distinct per-account temp secrets
    shared_reset = "SharedReset#2026!"
    r1 = client.put(f"/api/admin/users/{c1.json()['id']}/password", headers=adm_hdr, json={"new_password": shared_reset})
    r2 = client.put(f"/api/admin/users/{c2.json()['id']}/password", headers=adm_hdr, json={"new_password": shared_reset})
    assert r1.status_code == 200 and r2.status_code == 200
    rp1 = r1.json()["temp_password"]
    rp2 = r2.json()["temp_password"]
    assert rp1 != rp2 and rp1 != shared_reset and rp2 != shared_reset
    assert rp1 != tp1 and rp2 != tp2

    # User logs in with issued reset password, changes password, and logs in cleanly
    l1 = client.post("/api/auth/login", json={"username": "r02_u1", "password": rp1})
    assert l1.status_code == 200 and l1.json()["must_change_password"] is True
    h1 = {"Authorization": f"Bearer {l1.json()['access_token']}"}
    assert client.put(
        "/api/auth/profile",
        headers=h1,
        json={"old_password": rp1, "new_password": "U1FinalOwn#2026!"},
    ).status_code == 200
    l1_final = client.post("/api/auth/login", json={"username": "r02_u1", "password": "U1FinalOwn#2026!"})
    assert l1_final.status_code == 200 and l1_final.json()["must_change_password"] is False

    # Shared strong default_password in Excel import: weak rejected, strong ignored per-account unique
    class _Sheet:
        def __init__(self, rows):
            self._rows = rows
            self.nrows = len(rows)
            self.ncols = len(rows[0])

        def cell_value(self, r, c):
            return self._rows[r][c]

    xls_stub = tmp_path / "r02_batch.xls"
    xls_stub.write_bytes(b"synthetic")
    book = SimpleNamespace(
        sheet_by_index=lambda _i: _Sheet([
            ["姓名", "手机号", "工号", "职位"],
            ["表一", "13900008881", "R02-B1", "员工"],
            ["表二", "13900008882", "R02-B2", "员工"],
        ])
    )
    with patch.dict(sys.modules, {"xlrd": SimpleNamespace(open_workbook=lambda _p: book)}):
        weak_xls = import_users_from_excel(excel_path=str(xls_stub), default_password="admin")
        assert weak_xls["success"] is False
        ok_xls = import_users_from_excel(excel_path=str(xls_stub), default_password="SharedExcel#2026!")
    assert ok_xls["success"] is True and ok_xls["imported_count"] == 2
    assert ok_xls["default_password_ignored"] is True
    creds = ok_xls["created_credentials"]
    assert creds[0]["temp_password"] != creds[1]["temp_password"]
    assert creds[0]["temp_password"] != "SharedExcel#2026!" and creds[1]["temp_password"] != "SharedExcel#2026!"


def test_r03_a_m4_05_excel_sync_preserves_disabled_state_and_admin_explicit_reactivation(tmp_path, monkeypatch):
    """A-M4-05: Regular Excel personnel sync must never re-enable administratively disabled accounts."""
    db_file = str(tmp_path / "r03_disabled_sync.db")
    monkeypatch.setenv("AUTH_DB_PATH", db_file)
    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", db_file)
    monkeypatch.setenv("ADMIN_PASSWORD", "BootstrapAdmin#2026!")
    init_auth_db()
    init_audit_tables()

    client = TestClient(server.app)
    adm_login = client.post("/api/auth/login", json={"username": "admin", "password": "BootstrapAdmin#2026!"})
    assert adm_login.status_code == 200
    adm_tok = adm_login.json()["access_token"]
    adm_hdr = {"Authorization": f"Bearer {adm_tok}"}

    # Helper to create synthetic users, complete initial password setup, and capture concurrent tokens
    users = {}
    for uname, emp_no, role, pwd in [
        ("13911110001", "R03-DIS-EMP", "employee", "R03EmpOwn#2026!"),
        ("13911110002", "R03-DIS-MGR", "manager", "R03MgrOwn#2026!"),
        ("13911110003", "R03-ACT-MCP", "employee", "R03McpTmp#2026!"),
        ("13911110004", "R03-ACT-OK", "employee", "R03ActOk#2026!"),
    ]:
        created = admin_create_user_atomic(uname, role=role, employee_no=emp_no, phone=uname)
        assert created["success"] is True
        if uname != "13911110003":
            chg = auth_manager.change_user_credentials_atomic(
                created["id"], old_password=created["temp_password"], new_password=pwd
            )
            assert chg["success"] is True
            _, tok1, st1 = authenticate_user_and_issue_token(uname, pwd)
            _, tok2, st2 = authenticate_user_and_issue_token(uname, pwd)
            assert st1 == "ok" and st2 == "ok"
            effective_pwd = pwd
        else:
            effective_pwd = created["temp_password"]
            tok1, tok2 = None, None
        row = get_user_by_username(uname)
        users[uname] = {
            "id": row["id"],
            "password": effective_pwd,
            "password_hash": row["password_hash"],
            "tok1": tok1,
            "tok2": tok2,
        }

    # 1. Admin disables both synthetic employee and manager via HTTP PUT /api/admin/users/{id}
    for uname in ("13911110001", "13911110002"):
        uid = users[uname]["id"]
        resp = client.put(f"/api/admin/users/{uid}", headers=adm_hdr, json={"is_active": False})
        assert resp.status_code == 200
        assert get_user_by_username(uname)["is_active"] == 0
        # Old tokens are revoked immediately (401), login returns 403
        for tok in (users[uname]["tok1"], users[uname]["tok2"]):
            assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
            assert client.get("/api/llm-rules", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": uname, "password": users[uname]["password"]}).status_code == 403

    emp_tv_before_sync = get_user_by_username("13911110001")["token_version"]
    mgr_tv_before_sync = get_user_by_username("13911110002")["token_version"]
    act_tv_before_sync = get_user_by_username("13911110004")["token_version"]

    class _Sheet:
        def __init__(self, rows):
            self._rows = rows
            self.nrows = len(rows)
            self.ncols = len(rows[0])

        def cell_value(self, r, c):
            return self._rows[r][c]

    xls_stub = tmp_path / "r03_sync.xls"
    xls_stub.write_bytes(b"synthetic-r03-sync")

    def run_sync(rows):
        book = SimpleNamespace(sheet_by_index=lambda _i: _Sheet(rows))
        with patch.dict(sys.modules, {"xlrd": SimpleNamespace(open_workbook=lambda _p: book)}):
            return import_users_from_excel(excel_path=str(xls_stub))

    # 2. Regular Excel sync (no active-state column) updates profiles & demotes disabled manager -> employee
    sync_res = run_sync([
        ["姓名", "部门1级", "部门2级", "工号", "职位", "手机号"],
        ["停用员工", "供应链", "新部门E", "R03-DIS-EMP", "采购专员", "13911110001"],
        ["停用经理", "供应链", "新部门M", "R03-DIS-MGR", "采购专员", "13911110002"],
        ["待改密用户", "供应链", "新部门P", "R03-ACT-MCP", "员工", "13911110003"],
        ["正常员工", "供应链", "新部门A", "R03-ACT-OK", "员工", "13911110004"],
    ])
    assert sync_res["success"] is True and sync_res["updated_count"] == 4 and sync_res["role_changed_count"] == 1

    # Disabled accounts MUST remain is_active == 0 and login == 403, with all 4 old tokens still 401
    emp_after = get_user_by_username("13911110001")
    mgr_after = get_user_by_username("13911110002")
    assert emp_after["is_active"] == 0 and emp_after["dept2"] == "新部门E"
    assert emp_after["token_version"] == emp_tv_before_sync
    assert mgr_after["is_active"] == 0 and mgr_after["role"] == "employee" and mgr_after["dept2"] == "新部门M"
    assert mgr_after["token_version"] == mgr_tv_before_sync + 1
    for uname in ("13911110001", "13911110002"):
        assert client.post("/api/auth/login", json={"username": uname, "password": users[uname]["password"]}).status_code == 403
        for tok in (users[uname]["tok1"], users[uname]["tok2"]):
            assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401

    # Active must_change_password=1 & password_hash preserved; unchanged active user's session remains 200
    mcp_after = get_user_by_username("13911110003")
    act_after = get_user_by_username("13911110004")
    assert mcp_after["must_change_password"] == 1
    assert mcp_after["password_hash"] == users["13911110003"]["password_hash"]
    assert act_after["token_version"] == act_tv_before_sync
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {users['13911110004']['tok1']}"}).status_code == 200

    # 3. Unauthorized employee cannot explicitly reactivate disabled account (403)
    emp_enable = client.put(
        f"/api/admin/users/{emp_after['id']}",
        headers={"Authorization": f"Bearer {users['13911110004']['tok1']}"},
        json={"is_active": True},
    )
    assert emp_enable.status_code == 403
    assert get_user_by_username("13911110001")["is_active"] == 0

    # 4. Authorized admin explicitly enables deactivated account: token_version bumps, old tokens stay 401, fresh login 200
    adm_enable = client.put(
        f"/api/admin/users/{emp_after['id']}",
        headers=adm_hdr,
        json={"is_active": True},
    )
    assert adm_enable.status_code == 200
    emp_enabled = get_user_by_username("13911110001")
    assert emp_enabled["is_active"] == 1 and emp_enabled["token_version"] == emp_tv_before_sync + 1
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {users['13911110001']['tok1']}"}).status_code == 401
    relogin = client.post("/api/auth/login", json={"username": "13911110001", "password": users["13911110001"]["password"]})
    assert relogin.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {relogin.json()['access_token']}"}).status_code == 200

    # 5. Mid-import exception rolls back partial row updates cleanly without partial state/version drift
    class _BrokenSheet:
        def __init__(self):
            self._rows = [
                ["姓名", "部门1级", "部门2级", "工号", "职位", "手机号"],
                ["回滚检验", "供应链", "应该回滚部门", "R03-DIS-MGR", "总监", "13911110002"],
                ["触发异常", "供应链", "X", "R03-ERR", "员工", "13999990000"],
            ]
            self.nrows = len(self._rows)
            self.ncols = len(self._rows[0])

        def cell_value(self, r, c):
            if r == 2:
                raise RuntimeError("simulated excel read failure mid-sync")
            return self._rows[r][c]

    mgr_snapshot = get_user_by_username("13911110002")
    with patch.dict(sys.modules, {"xlrd": SimpleNamespace(open_workbook=lambda _p: SimpleNamespace(sheet_by_index=lambda _i: _BrokenSheet()))}):
        with pytest.raises(RuntimeError, match="simulated excel read failure"):
            import_users_from_excel(excel_path=str(xls_stub))
    mgr_post_fail = get_user_by_username("13911110002")
    assert mgr_post_fail["role"] == mgr_snapshot["role"]
    assert mgr_post_fail["dept2"] == mgr_snapshot["dept2"]
    assert mgr_post_fail["is_active"] == mgr_snapshot["is_active"]
    assert mgr_post_fail["token_version"] == mgr_snapshot["token_version"]


