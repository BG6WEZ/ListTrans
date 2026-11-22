"""V015-M4 / R03 comprehensive security probe replaying all M4 R01 audit blockers and R03 controls.

Synthetic accounts/Excel decoder only; real SQLite, JWT, LLMRuleManager, server, and HTTP paths in isolated temp root.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.5/evidence"
TMP = Path(tempfile.mkdtemp(prefix="m4_r03_probes_"))
sys.path.insert(0, str(ROOT))

for directory in ("db", "config", "vector", "uploads", "outputs", "logs", "runtime"):
    (TMP / directory).mkdir(parents=True, exist_ok=True)
for p in (ROOT / "config").iterdir():
    if p.is_file():
        shutil.copy2(p, TMP / "config" / p.name)

os.environ.update({
    "TESTING": "1",
    "AUTH_DEV_MODE": "false",
    "LLM_ENABLED": "false",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "MATERIALS_DB_PATH": str(TMP / "db/materials.db"),
    "AUTH_DB_PATH": str(TMP / "db/auth.db"),
    "CONFIG_DIR": str(TMP / "config"),
    "VECTOR_CACHE_DIR": str(TMP / "vector"),
    "UPLOAD_DIR": str(TMP / "uploads"),
    "OUTPUT_DIR": str(TMP / "outputs"),
    "LOG_DIR": str(TMP / "logs"),
    "BINDING_FEEDBACK_FILE": str(TMP / "runtime/binding_feedback.json"),
    "FEEDBACK_SAMPLES_FILE": str(TMP / "runtime/feedback_samples.json"),
    "GOLDEN_DETAIL_FILE": str(TMP / "runtime/golden_last_run.json"),
    "SECRET_KEY": "Synthetic_R03_Probe_Signing_Key_2026_abcdef123456",
})

try:
    from fastapi.testclient import TestClient
    from app import auth_manager as am, flywheel_sandbox, server
    from app.audit_manager import init_audit_tables
    from app.db_manager import init_db
    from app.llm_rule_engine import init_rule_engine_db, LLMRuleManager
    from app.session_manager import init_session_db

    init_db()
    am.init_auth_db()
    init_audit_tables()
    init_session_db()
    init_rule_engine_db()

    client = TestClient(server.app)

    def create_active_account(name: str, role: str, pwd: str):
        created = am.admin_create_user_atomic(name, role=role, employee_no=name)
        assert created["success"], created
        changed = am.change_user_credentials_atomic(
            created["id"],
            created["temp_password"],
            new_password=pwd,
        )
        assert changed["success"], changed
        user = am.get_user_by_id(created["id"])
        tok = am.issue_token_for_user(user)
        return user, tok

    employee, employee_token = create_active_account("r03_probe_emp", "employee", "R03Emp#2026!Unique")
    manager, manager_token = create_active_account("r03_probe_mgr", "manager", "R03Mgr#2026!Unique")
    admin, admin_token = create_active_account("r03_probe_adm", "admin", "R03Adm#2026!Unique")
    admin2, admin2_token = create_active_account("r03_probe_adm2", "admin", "R03Adm2#2026!Unique")

    report = {
        "fixture": "all accounts/rows synthetic; Excel decoder substituted only; auth/SQLite/HTTP real inside temp dir",
        "probes": {},
    }
    probes = report["probes"]

    class _Sheet:
        def __init__(self, rows):
            self.rows = rows
            self.nrows = len(rows)
            self.ncols = len(rows[0])

        def cell_value(self, row, col):
            return self.rows[row][col]

    def import_rows(rows, default_password=None):
        book = SimpleNamespace(sheet_by_index=lambda _: _Sheet(rows))
        source = TMP / "synthetic_personnel.xls"
        source.write_bytes(b"synthetic decoder fixture")
        with patch.dict(sys.modules, {"xlrd": SimpleNamespace(open_workbook=lambda _: book)}):
            return am.import_users_from_excel(str(source), default_password=default_password)

    # Probe 1 (A-M4-01): Excel role change invalidates ALL prior concurrent tokens immediately
    _, m_tok_1, m_st_1 = am.authenticate_user_and_issue_token("r03_probe_mgr", "R03Mgr#2026!Unique")
    _, m_tok_2, m_st_2 = am.authenticate_user_and_issue_token("r03_probe_mgr", "R03Mgr#2026!Unique")
    assert m_st_1 == "ok" and m_st_2 == "ok" and m_tok_1 != m_tok_2
    h1 = {"Authorization": f"Bearer {m_tok_1}"}
    h2 = {"Authorization": f"Bearer {m_tok_2}"}
    assert client.get("/api/llm-rules", headers=h1).status_code == 200
    assert client.get("/api/auth/me", headers=h2).status_code == 200

    before = am.get_user_by_id(manager["id"])
    res_demote = import_rows([
        ["姓名", "手机号", "工号", "职位"],
        ["Synthetic Manager", manager["username"], manager["employee_no"], "普通员工"],
    ])
    after = am.get_user_by_id(manager["id"])
    tok1_me_after = client.get("/api/auth/me", headers=h1).status_code
    tok2_me_after = client.get("/api/auth/me", headers=h2).status_code
    tok1_rules_after = client.get("/api/llm-rules", headers=h1).status_code

    relogin = client.post("/api/auth/login", json={"username": "r03_probe_mgr", "password": "R03Mgr#2026!Unique"})
    fresh_tok = relogin.json().get("access_token")
    res_unchanged = import_rows([
        ["姓名", "部门1级", "手机号", "工号", "职位"],
        ["Synthetic Manager", "新部门", manager["username"], manager["employee_no"], "普通员工"],
    ])
    unchanged_after = am.get_user_by_id(manager["id"])
    unchanged_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {fresh_tok}"}).status_code

    probes["excel_role_change_old_token"] = {
        "import_success": res_demote["success"],
        "role_changed_count": res_demote.get("role_changed_count"),
        "role_before": before["role"],
        "role_after": after["role"],
        "token_version_before": before["token_version"],
        "token_version_after": after["token_version"],
        "old_token_1_me_after": tok1_me_after,
        "old_token_2_me_after": tok2_me_after,
        "old_token_1_rules_after": tok1_rules_after,
        "relogin_status": relogin.status_code,
        "relogin_role": relogin.json().get("role"),
        "unchanged_role_changed_count": res_unchanged.get("role_changed_count"),
        "token_version_after_unchanged": unchanged_after["token_version"],
        "unchanged_role_token_me": unchanged_me,
        "requirement_met": (
            res_demote["success"]
            and res_demote.get("role_changed_count") == 1
            and after["role"] == "employee"
            and after["token_version"] == before["token_version"] + 1
            and tok1_me_after == 401
            and tok2_me_after == 401
            and tok1_rules_after == 401
            and relogin.status_code == 200
            and relogin.json().get("role") == "employee"
            and res_unchanged.get("role_changed_count") == 0
            and unchanged_after["token_version"] == after["token_version"]
            and unchanged_me == 200
        ),
    }

    # Probe 2 (A-M4-03): Shared explicit default_password/password/new_password never yield shared credentials
    weak_xls = import_rows(
        [["姓名", "手机号", "工号", "职位"], ["Weak One", "r03_weak_i", "R03-WI", "员工"]],
        default_password="admin",
    )
    res_batch = import_rows(
        [
            ["姓名", "手机号", "工号", "职位"],
            ["Synthetic One", "r03_import_one", "R03-I1", "员工"],
            ["Synthetic Two", "r03_import_two", "R03-I2", "员工"],
        ],
        default_password="SyntheticShared#2026!",
    )
    creds = res_batch["created_credentials"]
    unique_xls = len({c["temp_password"] for c in creds})
    probes["excel_shared_explicit_initial_password"] = {
        "weak_rejected": not weak_xls["success"],
        "created": len(creds),
        "distinct_initial_passwords": unique_xls,
        "ignored_explicit_default": all(c["temp_password"] != "SyntheticShared#2026!" for c in creds),
        "requirement_met": (
            not weak_xls["success"]
            and len(creds) == 2
            and unique_xls == 2
            and all(c["temp_password"] != "SyntheticShared#2026!" for c in creds)
        ),
    }

    initial_values = []
    initial_statuses = []
    created_ids = []
    for suffix in ("one", "two"):
        resp = client.post(
            "/api/admin/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": f"r03_http_{suffix}",
                "employee_no": f"R03-HTTP-{suffix}",
                "role": "employee",
                "password": "SyntheticShared#2026!",
            },
        )
        initial_statuses.append(resp.status_code)
        initial_values.append(resp.json().get("temp_password"))
        created_ids.append(resp.json().get("id"))

    reset_values = []
    reset_statuses = []
    for uid in created_ids:
        resp = client.put(
            f"/api/admin/users/{uid}/password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"new_password": "SyntheticResetShared#2026!"},
        )
        reset_statuses.append(resp.status_code)
        reset_values.append(resp.json().get("temp_password"))

    weak_http = client.post(
        "/api/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "r03_http_weak", "employee_no": "R03-HTTP-WK", "role": "employee", "password": "hkjj@12345"},
    ).status_code

    probes["http_shared_initial_and_reset_passwords"] = {
        "weak_create_status": weak_http,
        "create_statuses": initial_statuses,
        "create_distinct_passwords": len(set(initial_values)),
        "create_ignored_caller_secret": all(v != "SyntheticShared#2026!" for v in initial_values),
        "reset_statuses": reset_statuses,
        "reset_distinct_passwords": len(set(reset_values)),
        "reset_ignored_caller_secret": all(v != "SyntheticResetShared#2026!" for v in reset_values),
        "requirement_met": (
            weak_http == 400
            and initial_statuses == [200, 200]
            and reset_statuses == [200, 200]
            and len(set(initial_values)) == 2
            and len(set(reset_values)) == 2
            and all(v != "SyntheticShared#2026!" for v in initial_values)
            and all(v != "SyntheticResetShared#2026!" for v in reset_values)
        ),
    }

    # Probe 3 (A-M4-02): Direct service layer forged/tampered/revoked/missing actors all rejected
    rule_id = "R03-SERVICE-FORGED-ACTOR"
    payload = {
        "rule_id": rule_id,
        "rule_name": "Synthetic R03 rule",
        "scope_level": "universal",
        "parsed_dsl": {"matching_rules": {"must_not_contain": ["SYNTHETIC_FORBIDDEN"]}},
    }
    assert LLMRuleManager.save_rule_draft(payload, employee)["success"]
    assert LLMRuleManager.submit_rule(rule_id, employee)["success"]
    assert LLMRuleManager.audit_rule(rule_id, "approve", "synthetic independent audit", admin)["success"]

    http_denied = client.post(
        f"/api/llm-rules/{rule_id}/publish",
        headers={"Authorization": f"Bearer {employee_token}"},
    ).status_code
    forged_actor_emp_claim = {"id": admin["id"], "username": admin["username"], "role": "employee"}
    forged_actor_adm_claim = {"id": admin["id"], "username": admin["username"], "role": "admin"}
    tampered_emp_role = dict(employee, role="admin")

    pub_forged_1 = LLMRuleManager.publish_rule(rule_id, forged_actor_emp_claim)
    pub_forged_2 = LLMRuleManager.publish_rule(rule_id, forged_actor_adm_claim)
    pub_tampered = LLMRuleManager.publish_rule(rule_id, tampered_emp_role)

    # Revoke admin2 session via logout -> admin2 dict must also be rejected
    assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {admin2_token}"}).status_code == 200
    pub_revoked = LLMRuleManager.publish_rule(rule_id, admin2)
    pub_valid = LLMRuleManager.publish_rule(rule_id, admin)

    probes["service_fabricated_existing_admin_identity"] = {
        "employee_http_publish": http_denied,
        "forged_emp_claim_result": {"success": pub_forged_1.get("success"), "code": pub_forged_1.get("status_code")},
        "forged_adm_claim_result": {"success": pub_forged_2.get("success"), "code": pub_forged_2.get("status_code")},
        "tampered_emp_role_result": {"success": pub_tampered.get("success"), "code": pub_tampered.get("status_code")},
        "revoked_admin_result": {"success": pub_revoked.get("success"), "code": pub_revoked.get("status_code")},
        "valid_admin_publish_result": {"success": pub_valid.get("success"), "status": pub_valid.get("status")},
        "requirement_met": (
            http_denied == 403
            and not pub_forged_1.get("success")
            and not pub_forged_2.get("success")
            and not pub_tampered.get("success")
            and not pub_revoked.get("success")
            and bool(pub_valid.get("success"))
            and pub_valid.get("status") == "published"
        ),
    }

    # Probe 4 (A-M4-02): save_auto_binding_rules refuses actor_user=None, forged, employee, revoked; allows valid admin
    binding_errors = {}
    for label, actor in (
        ("none_actor", None),
        ("forged_admin", forged_actor_adm_claim),
        ("employee_actor", employee),
        ("revoked_admin2", admin2),
    ):
        try:
            server.save_auto_binding_rules([{"original_name": f"R03_{label}", "bound_materials": []}], actor_user=actor)
            binding_errors[label] = "UNEXPECTED_ALLOW"
        except PermissionError:
            binding_errors[label] = "PermissionError"

    written_bad = any(
        str(x.get("original_name", "")).startswith("R03_") for x in server.load_auto_binding_rules()
    )
    server.save_auto_binding_rules([{"original_name": "R03_VALID_ADMIN", "bound_materials": []}], actor_user=admin)
    written_good = any(x.get("original_name") == "R03_VALID_ADMIN" for x in server.load_auto_binding_rules())
    server.save_auto_binding_rules([], actor_user=admin)

    probes["binding_persistence_missing_actor"] = {
        "rejections": binding_errors,
        "bad_writes_persisted": written_bad,
        "valid_admin_persisted": written_good,
        "requirement_met": (
            all(v == "PermissionError" for v in binding_errors.values())
            and not written_bad
            and written_good
        ),
    }

    # Probe 5: Flywheel task status rejects forged/revoked direct dict
    with patch("app.flywheel_sandbox.threading.Thread"):
        tid = flywheel_sandbox.start_sandbox_evaluation({"suggestion_type": "ALIAS"}, owner_user=admin)
    task_for_owner = flywheel_sandbox.get_eval_task_status_for_user(tid, admin) is not None
    task_for_forged = flywheel_sandbox.get_eval_task_status_for_user(tid, forged_actor_adm_claim) is None
    task_for_revoked = flywheel_sandbox.get_eval_task_status_for_user(tid, admin2) is None
    probes["flywheel_task_direct_service_check"] = {
        "owner_allowed": task_for_owner,
        "forged_blocked": task_for_forged,
        "revoked_blocked": task_for_revoked,
        "requirement_met": task_for_owner and task_for_forged and task_for_revoked,
    }

    # Probe 6: Cross-process persistent revocation + admin role update control
    assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {admin_token}"}).status_code == 200
    child_env = os.environ.copy()
    child_env["AUDIT_SYNTHETIC_TOKEN"] = admin_token
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,json; from app.auth_manager import verify_token_session; "
            "u,c,e=verify_token_session(os.environ['AUDIT_SYNTHETIC_TOKEN']); "
            "print(json.dumps({'code':c,'accepted':u is not None}))",
        ],
        cwd=ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    child_result = json.loads(child.stdout.strip().splitlines()[-1])
    probes["logout_revocation_real_process_restart"] = {
        "child_exit": child.returncode,
        "child_session_status": child_result["code"],
        "child_accepted": child_result["accepted"],
        "requirement_met": child.returncode == 0 and child_result["code"] == 401 and not child_result["accepted"],
    }

    # Probe 7 (A-M4-05): Excel sync preserves is_active=0 while enforcing role revocation & admin-only reactivation
    import sqlite3

    r02_replay_dir = TMP / "r02_replay"
    for sub in ("db", "logs"):
        (r02_replay_dir / sub).mkdir(parents=True, exist_ok=True)
    r02_replay_env = os.environ.copy()
    r02_replay_env.update({
        "AUTH_DB_PATH": str(r02_replay_dir / "db/auth.db"),
        "LOG_DIR": str(r02_replay_dir / "logs"),
    })
    r02_script = "\n".join([
        "import importlib.util, json, sys, types",
        "from pathlib import Path",
        "from unittest.mock import patch",
        "spec = importlib.util.spec_from_file_location('r02_am', r'" + str(ROOT / '.local-archive/m4_r03_pre_backup/app/auth_manager.py') + "')",
        "m = importlib.util.module_from_spec(spec)",
        "spec.loader.exec_module(m)",
        "m.init_auth_db()",
        "adm = m.admin_create_user_atomic('r03_replay_adm', role='admin', employee_no='R03-RA')",
        "cr = m.admin_create_user_atomic('r03_replay_u', role='employee', employee_no='R03-RP')",
        "assert cr['success'] and adm['success']",
        "ch = m.change_user_credentials_atomic(cr['id'], cr['temp_password'], new_password='ReplayEmp#2026!Unique')",
        "assert ch['success']",
        "m.admin_update_user_atomic(cr['id'], {'is_active': 0})",
        "_, _, st_before = m.authenticate_user_and_issue_token('r03_replay_u', 'ReplayEmp#2026!Unique')",
        "class _S:",
        "    def __init__(self, r):",
        "        self.r, self.nrows, self.ncols = r, len(r), len(r[0])",
        "    def cell_value(self, i, j):",
        "        return self.r[i][j]",
        "bk = types.SimpleNamespace(sheet_by_index=lambda _: _S([['姓名','手机号','工号','职位'],['Replay','r03_replay_u','R03-RP','普通员工']]))",
        "p = Path(r'" + str(r02_replay_dir / 'replay.xls') + "')",
        "p.write_bytes(b'replay')",
        "with patch.dict(sys.modules, {'xlrd': types.SimpleNamespace(open_workbook=lambda _: bk)}):",
        "    m.import_users_from_excel(str(p))",
        "u_after = m.get_user_by_id(cr['id'])",
        "_, tok_after, st_after = m.authenticate_user_and_issue_token('r03_replay_u', 'ReplayEmp#2026!Unique')",
        "print(json.dumps({'pre_sync_auth': st_before, 'after_is_active': u_after['is_active'], 'after_auth': st_after, 'issued_token': bool(tok_after)}))",
    ])
    r02_replay = subprocess.run(
        [sys.executable, "-c", r02_script],
        cwd=ROOT,
        env=r02_replay_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r02_replay.returncode == 0, r02_replay.stderr
    r02_contrast = json.loads(r02_replay.stdout.strip().splitlines()[-1])

    a5_adm, a5_adm_tok = create_active_account("r03_a5_adm", "admin", "R03A5Adm#2026!Unique")
    a5_emp, a5_emp_tok1 = create_active_account("r03_a5_emp", "employee", "R03A5Emp#2026!Unique")
    _, a5_emp_tok2, _ = am.authenticate_user_and_issue_token("r03_a5_emp", "R03A5Emp#2026!Unique")
    a5_mgr_created = am.admin_create_user_atomic("r03_a5_mgr", role="manager", employee_no="R03-A5-MGR")
    assert a5_mgr_created["success"], a5_mgr_created
    a5_mgr_temp_pwd = a5_mgr_created["temp_password"]
    a5_keep, a5_keep_tok = create_active_account("r03_a5_keep", "employee", "R03A5Keep#2026!Unique")

    adm_a5_headers = {"Authorization": f"Bearer {a5_adm_tok}"}
    emp_keep_headers = {"Authorization": f"Bearer {a5_keep_tok}"}
    dis_emp_resp = client.put(f"/api/admin/users/{a5_emp['id']}", json={"is_active": False}, headers=adm_a5_headers)
    dis_mgr_resp = client.put(f"/api/admin/users/{a5_mgr_created['id']}", json={"is_active": False}, headers=adm_a5_headers)
    assert dis_emp_resp.status_code == 200 and dis_mgr_resp.status_code == 200

    pre_sync_emp_login = client.post("/api/auth/login", json={"username": "r03_a5_emp", "password": "R03A5Emp#2026!Unique"}).status_code
    pre_sync_mgr_login = client.post("/api/auth/login", json={"username": "r03_a5_mgr", "password": a5_mgr_temp_pwd}).status_code

    with sqlite3.connect(os.environ["AUTH_DB_PATH"]) as conn:
        conn.row_factory = sqlite3.Row
        row_mgr_before = dict(conn.execute("SELECT is_active, role, token_version, must_change_password, password_hash FROM users WHERE id=?", (a5_mgr_created["id"],)).fetchone())
        row_emp_before = dict(conn.execute("SELECT is_active, role, token_version FROM users WHERE id=?", (a5_emp["id"],)).fetchone())

    a5_sync_res = import_rows([
        ["姓名", "手机号", "工号", "职位"],
        ["Disabled Emp", a5_emp["username"], a5_emp["employee_no"], "普通员工"],
        ["Disabled Mgr", "r03_a5_mgr", "R03-A5-MGR", "普通员工"],
        ["Active Keep", a5_keep["username"], a5_keep["employee_no"], "普通员工"],
    ])
    with sqlite3.connect(os.environ["AUTH_DB_PATH"]) as conn:
        conn.row_factory = sqlite3.Row
        row_mgr_after = dict(conn.execute("SELECT is_active, role, token_version, must_change_password, password_hash FROM users WHERE id=?", (a5_mgr_created["id"],)).fetchone())
        row_emp_after = dict(conn.execute("SELECT is_active, role, token_version FROM users WHERE id=?", (a5_emp["id"],)).fetchone())
        row_keep_after = dict(conn.execute("SELECT is_active, role, token_version FROM users WHERE id=?", (a5_keep["id"],)).fetchone())

    post_sync_emp_login = client.post("/api/auth/login", json={"username": "r03_a5_emp", "password": "R03A5Emp#2026!Unique"}).status_code
    post_sync_mgr_login = client.post("/api/auth/login", json={"username": "r03_a5_mgr", "password": a5_mgr_temp_pwd}).status_code
    post_sync_emp_tok1_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {a5_emp_tok1}"}).status_code
    post_sync_emp_tok2_rules = client.get("/api/llm-rules", headers={"Authorization": f"Bearer {a5_emp_tok2}"}).status_code
    post_sync_keep_me = client.get("/api/auth/me", headers=emp_keep_headers).status_code

    emp_reactivate_resp = client.put(f"/api/admin/users/{a5_emp['id']}", json={"is_active": True}, headers=emp_keep_headers).status_code
    adm_reactivate_resp = client.put(f"/api/admin/users/{a5_emp['id']}", json={"is_active": True}, headers=adm_a5_headers).status_code
    post_admin_enable_login = client.post("/api/auth/login", json={"username": "r03_a5_emp", "password": "R03A5Emp#2026!Unique"})
    post_admin_enable_tok = post_admin_enable_login.json().get("access_token") if post_admin_enable_login.status_code == 200 else ""
    post_admin_enable_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {post_admin_enable_tok}"}).status_code if post_admin_enable_tok else 0
    post_admin_enable_old_tok1 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {a5_emp_tok1}"}).status_code

    class _ExplodingSheet:
        nrows = 3
        ncols = 4
        _rows = [
            ["姓名", "手机号", "工号", "职位"],
            ["Disabled Mgr", "r03_a5_mgr", "R03-A5-MGR", "项目经理"],
            ["Boom Row", "r03_a5_boom", "R03-A5-BM", "普通员工"],
        ]

        def cell_value(self, row, col):
            if row == 2 and col == 1:
                raise RuntimeError("synthetic mid-import boom")
            return self._rows[row][col]

    boom_src = TMP / "boom.xls"
    boom_src.write_bytes(b"boom")
    boom_book = SimpleNamespace(sheet_by_index=lambda _: _ExplodingSheet())
    boom_raised = False
    with patch.dict(sys.modules, {"xlrd": SimpleNamespace(open_workbook=lambda _: boom_book)}):
        try:
            am.import_users_from_excel(str(boom_src))
        except RuntimeError:
            boom_raised = True
    with sqlite3.connect(os.environ["AUTH_DB_PATH"]) as conn:
        conn.row_factory = sqlite3.Row
        row_mgr_rollback = dict(conn.execute("SELECT is_active, role, token_version FROM users WHERE id=?", (a5_mgr_created["id"],)).fetchone())

    probes["a_m4_05_excel_reactivation"] = {
        "r02_baseline_contrast": r02_contrast,
        "pre_sync_emp_login": pre_sync_emp_login,
        "pre_sync_mgr_login": pre_sync_mgr_login,
        "sync_result": a5_sync_res,
        "disabled_emp_unchanged_role": {
            "is_active_after": row_emp_after["is_active"],
            "token_version_before": row_emp_before["token_version"],
            "token_version_after": row_emp_after["token_version"],
            "login_status": post_sync_emp_login,
            "old_tok1_me": post_sync_emp_tok1_me,
            "old_tok2_rules": post_sync_emp_tok2_rules,
        },
        "disabled_mgr_changed_role": {
            "role_before": row_mgr_before["role"],
            "role_after": row_mgr_after["role"],
            "is_active_after": row_mgr_after["is_active"],
            "token_version_before": row_mgr_before["token_version"],
            "token_version_after": row_mgr_after["token_version"],
            "must_change_password_preserved": row_mgr_before["must_change_password"] == 1 and row_mgr_after["must_change_password"] == 1,
            "password_hash_preserved": row_mgr_before["password_hash"] == row_mgr_after["password_hash"],
            "login_status": post_sync_mgr_login,
        },
        "unchanged_active_keep_me": post_sync_keep_me,
        "unchanged_active_keep_is_active": row_keep_after["is_active"],
        "employee_explicit_enable_status": emp_reactivate_resp,
        "admin_explicit_enable_status": adm_reactivate_resp,
        "admin_enable_new_login_status": post_admin_enable_login.status_code,
        "admin_enable_new_me_status": post_admin_enable_me,
        "admin_enable_old_token_me_status": post_admin_enable_old_tok1,
        "mid_import_rollback_failed_cleanly": boom_raised and row_mgr_rollback == {"is_active": 0, "role": "employee", "token_version": row_mgr_after["token_version"]},
        "requirement_met": (
            r02_contrast == {"pre_sync_auth": "inactive", "after_is_active": 1, "after_auth": "ok", "issued_token": True}
            and pre_sync_emp_login == 403
            and pre_sync_mgr_login == 403
            and a5_sync_res.get("role_changed_count") == 1
            and row_emp_after["is_active"] == 0
            and row_emp_after["token_version"] == row_emp_before["token_version"]
            and post_sync_emp_login == 403
            and post_sync_emp_tok1_me == 401
            and post_sync_emp_tok2_rules == 401
            and row_mgr_after["is_active"] == 0
            and row_mgr_after["role"] == "employee"
            and row_mgr_after["token_version"] == row_mgr_before["token_version"] + 1
            and row_mgr_before["must_change_password"] == 1
            and row_mgr_after["must_change_password"] == 1
            and row_mgr_before["password_hash"] == row_mgr_after["password_hash"]
            and post_sync_mgr_login == 403
            and post_sync_keep_me == 200
            and row_keep_after["is_active"] == 1
            and emp_reactivate_resp == 403
            and adm_reactivate_resp == 200
            and post_admin_enable_login.status_code == 200
            and post_admin_enable_me == 200
            and post_admin_enable_old_tok1 == 401
            and boom_raised
            and row_mgr_rollback == {"is_active": 0, "role": "employee", "token_version": row_mgr_after["token_version"]}
        ),
    }

    assert all(p.get("requirement_met") for p in probes.values()), json.dumps(probes, ensure_ascii=False, indent=2)

    out_path = EV / "m4_r03_probes.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"all_passed": True, "probes": {k: v["requirement_met"] for k, v in probes.items()}}, ensure_ascii=False))
finally:
    shutil.rmtree(TMP, ignore_errors=True)
