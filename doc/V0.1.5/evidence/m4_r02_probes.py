"""V015-M4 / R02 comprehensive security probe replaying all M4 R01 audit blockers and R02 controls.

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
TMP = Path(tempfile.mkdtemp(prefix="m4_r02_probes_"))
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
    "SECRET_KEY": "Synthetic_R02_Probe_Signing_Key_2026_abcdef123456",
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

    employee, employee_token = create_active_account("r02_probe_emp", "employee", "R02Emp#2026!Unique")
    manager, manager_token = create_active_account("r02_probe_mgr", "manager", "R02Mgr#2026!Unique")
    admin, admin_token = create_active_account("r02_probe_adm", "admin", "R02Adm#2026!Unique")
    admin2, admin2_token = create_active_account("r02_probe_adm2", "admin", "R02Adm2#2026!Unique")

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
    _, m_tok_1, m_st_1 = am.authenticate_user_and_issue_token("r02_probe_mgr", "R02Mgr#2026!Unique")
    _, m_tok_2, m_st_2 = am.authenticate_user_and_issue_token("r02_probe_mgr", "R02Mgr#2026!Unique")
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

    relogin = client.post("/api/auth/login", json={"username": "r02_probe_mgr", "password": "R02Mgr#2026!Unique"})
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
        [["姓名", "手机号", "工号", "职位"], ["Weak One", "r02_weak_i", "R02-WI", "员工"]],
        default_password="admin",
    )
    res_batch = import_rows(
        [
            ["姓名", "手机号", "工号", "职位"],
            ["Synthetic One", "r02_import_one", "R02-I1", "员工"],
            ["Synthetic Two", "r02_import_two", "R02-I2", "员工"],
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
                "username": f"r02_http_{suffix}",
                "employee_no": f"R02-HTTP-{suffix}",
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
        json={"username": "r02_http_weak", "employee_no": "R02-HTTP-WK", "role": "employee", "password": "hkjj@12345"},
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
    rule_id = "R02-SERVICE-FORGED-ACTOR"
    payload = {
        "rule_id": rule_id,
        "rule_name": "Synthetic R02 rule",
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
            server.save_auto_binding_rules([{"original_name": f"R02_{label}", "bound_materials": []}], actor_user=actor)
            binding_errors[label] = "UNEXPECTED_ALLOW"
        except PermissionError:
            binding_errors[label] = "PermissionError"

    written_bad = any(
        str(x.get("original_name", "")).startswith("R02_") for x in server.load_auto_binding_rules()
    )
    server.save_auto_binding_rules([{"original_name": "R02_VALID_ADMIN", "bound_materials": []}], actor_user=admin)
    written_good = any(x.get("original_name") == "R02_VALID_ADMIN" for x in server.load_auto_binding_rules())
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

    assert all(p.get("requirement_met") for p in probes.values()), json.dumps(probes, ensure_ascii=False, indent=2)

    out_path = EV / "m4_r02_probes.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"all_passed": True, "probes": {k: v["requirement_met"] for k, v in probes.items()}}, ensure_ascii=False))
finally:
    shutil.rmtree(TMP, ignore_errors=True)
