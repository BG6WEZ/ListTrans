"""Independent audit probes. Synthetic accounts/Excel reader; real SQLite and HTTP auth.

All paths are set before application imports. No live credentials or account DB used.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.5/evidence"
TMP = Path(tempfile.mkdtemp(prefix="m4_r02_audit_probes_"))
sys.path.insert(0, str(ROOT))
for directory in ("db", "config", "vector", "uploads", "outputs", "logs", "runtime"):
    (TMP / directory).mkdir()
for p in (ROOT / "config").iterdir():
    if p.is_file():
        shutil.copy2(p, TMP / "config" / p.name)
os.environ.update({
    "TESTING": "1", "AUTH_DEV_MODE": "false", "LLM_ENABLED": "false",
    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    "MATERIALS_DB_PATH": str(TMP / "db/materials.db"),
    "AUTH_DB_PATH": str(TMP / "db/auth.db"), "CONFIG_DIR": str(TMP / "config"),
    "VECTOR_CACHE_DIR": str(TMP / "vector"), "UPLOAD_DIR": str(TMP / "uploads"),
    "OUTPUT_DIR": str(TMP / "outputs"), "LOG_DIR": str(TMP / "logs"),
    "BINDING_FEEDBACK_FILE": str(TMP / "runtime/binding_feedback.json"),
    "FEEDBACK_SAMPLES_FILE": str(TMP / "runtime/feedback_samples.json"),
    "GOLDEN_DETAIL_FILE": str(TMP / "runtime/golden_last_run.json"),
    "SECRET_KEY": "Synthetic_Audit_Signing_Key_Only_2026_abcdef",
})

from fastapi.testclient import TestClient
from app import auth_manager as am, server
from app.audit_manager import init_audit_tables
from app.db_manager import init_db
from app.llm_rule_engine import init_rule_engine_db, LLMRuleManager
from app.session_manager import init_session_db

init_db()
am.init_auth_db()
init_audit_tables()
init_session_db()
init_rule_engine_db()


def account(name, role):
    created = am.admin_create_user_atomic(name, role=role, employee_no=name)
    assert created["success"]
    changed = am.change_user_credentials_atomic(created["id"], created["temp_password"],
                                               new_password="AuditUser#2026!Unique")
    assert changed["success"]
    user = am.get_user_by_id(created["id"])
    return user, am.issue_token_for_user(user)


client = TestClient(server.app)
employee, employee_token = account("audit_employee", "employee")
manager, manager_token = account("audit_manager", "manager")
admin, admin_token = account("audit_admin", "admin")
report = {"fixture": "all accounts/rows synthetic; Excel decoder substituted only; auth/SQLite/HTTP real",
          "probes": {}}
probes = report["probes"]

# A: old token should not survive ANY role-changing account update path.
class Sheet:
    def __init__(self, rows):
        self.rows = rows
        self.nrows = len(rows)
        self.ncols = len(rows[0])

    def cell_value(self, row, col):
        return self.rows[row][col]


def import_rows(rows, default_password=None):
    book = SimpleNamespace(sheet_by_index=lambda _: Sheet(rows))
    source = TMP / "synthetic_personnel.xls"
    source.write_bytes(b"synthetic decoder fixture; never a real personnel file")
    with patch.dict(sys.modules, {"xlrd": SimpleNamespace(open_workbook=lambda _: book)}):
        return am.import_users_from_excel(str(source), default_password=default_password)


before = am.get_user_by_id(manager["id"])
headers = {"Authorization": "Bearer " + manager_token}
status_before = client.get("/api/llm-rules", headers=headers).status_code
res = import_rows([["姓名", "手机号", "工号", "职位"],
                   ["Synthetic Manager", manager["username"], manager["employee_no"], "员工"]])
after = am.get_user_by_id(manager["id"])
status_after = client.get("/api/llm-rules", headers=headers).status_code
probes["excel_role_change_old_token"] = {
    "import_success": res["success"], "updated": res["updated_count"],
    "role_before": before["role"], "role_after": after["role"],
    "token_version_before": before["token_version"], "token_version_after": after["token_version"],
    "http_before": status_before, "http_after_old_token": status_after,
    "expected_after": 401, "requirement_met": status_after == 401,
}

# B: the retained import default_password parameter still enables a shared initial secret.
res = import_rows([["姓名", "手机号", "工号", "职位"],
                   ["Synthetic One", "audit_import_one", "AUDIT-I1", "员工"],
                   ["Synthetic Two", "audit_import_two", "AUDIT-I2", "员工"]],
                  default_password="SyntheticShared#2026!")
credentials = res["created_credentials"]
unique = len({c["temp_password"] for c in credentials})
probes["excel_shared_explicit_initial_password"] = {
    "created": len(credentials), "distinct_initial_passwords": unique,
    "requirement_met": unique == len(credentials),
}

# The same issue is reachable through legitimate admin HTTP creation/reset APIs.
initial_values = []
initial_statuses = []
created_ids = []
for suffix in ("one", "two"):
    response = client.post("/api/admin/users", headers={"Authorization": "Bearer " + admin_token},
        json={"username": "audit_http_" + suffix, "employee_no": "AUDIT-HTTP-" + suffix,
              "role": "employee", "password": "SyntheticShared#2026!"})
    initial_statuses.append(response.status_code)
    initial_values.append(response.json().get("temp_password"))
    created_ids.append(response.json().get("id"))
reset_values = []
reset_statuses = []
for uid in created_ids:
    response = client.put("/api/admin/users/" + str(uid) + "/password",
        headers={"Authorization": "Bearer " + admin_token}, json={"new_password": "SyntheticResetShared#2026!"})
    reset_statuses.append(response.status_code)
    reset_values.append(response.json().get("temp_password"))
probes["http_shared_initial_and_reset_passwords"] = {
    "create_statuses": initial_statuses, "create_distinct_passwords": len(set(initial_values)),
    "reset_statuses": reset_statuses, "reset_distinct_passwords": len(set(reset_values)),
    "requirement_met": len(set(initial_values)) == 2 and len(set(reset_values)) == 2,
}

# C: service identity is fabricated by specifying another existing DB account, without a token.
rule_id = "AUDIT-SERVICE-FORGED-ACTOR"
payload = {"rule_id": rule_id, "rule_name": "Synthetic rule", "scope_level": "universal",
           "parsed_dsl": {"matching_rules": {"must_not_contain": ["SYNTHETIC_FORBIDDEN"]}}}
created = LLMRuleManager.save_rule_draft(payload, employee)
assert created["success"]
assert LLMRuleManager.submit_rule(rule_id, employee)["success"]
assert LLMRuleManager.audit_rule(rule_id, "approve", "synthetic independent audit", admin)["success"]
http_denied = client.post("/api/llm-rules/" + rule_id + "/publish",
                          headers={"Authorization": "Bearer " + employee_token}).status_code
forged_actor = {"id": admin["id"], "username": admin["username"], "role": "employee"}
published = LLMRuleManager.publish_rule(rule_id, forged_actor)
probes["service_fabricated_existing_admin_identity"] = {
    "employee_http_publish": http_denied,
    "service_authenticated_token_supplied": False,
    "service_publish_success": published.get("success"),
    "persisted_status": LLMRuleManager.get_rule_by_id(rule_id)["status"],
    "requirement_met": not published.get("success"),
    "limit": "Service boundary reproduction; HTTP request-body identity impersonation is NOT established",
}

# D: the persistence service's actor is optional and skips authorization altogether when absent.
save_denied = False
try:
    server.save_auto_binding_rules([{"original_name": "AUDIT-UNAUTHENTICATED-SAVE", "bound_materials": []}])
except PermissionError:
    save_denied = True
saved = any(x.get("original_name") == "AUDIT-UNAUTHENTICATED-SAVE" for x in server.load_auto_binding_rules())
probes["binding_persistence_missing_actor"] = {
    "actor_supplied": False, "permission_denied": save_denied, "global_file_written": saved, "requirement_met": save_denied and not saved,
}

# E: a normal authenticated employee request is denied while valid admin publish is allowed.
probes["normal_http_authorization_control"] = {
    "employee_publish_denied": http_denied == 403,
    "employee_binding_confirm": client.post("/api/binding/rules/confirm",
        headers={"Authorization": "Bearer " + employee_token},
        json={"original_name": "AUDIT", "bindings": []}).status_code,
    "admin_current_token": client.get("/api/auth/me", headers={"Authorization": "Bearer " + admin_token}).status_code,
}

# H: no active-state column/request must not reactivate an administratively disabled account.
import ast
previous_source = (ROOT / ".local-archive/m4_r02_pre_backup/app/auth_manager.py").read_text(encoding="utf-8-sig")
previous_node = next(n for n in ast.parse(previous_source).body if isinstance(n, ast.FunctionDef) and n.name == "import_users_from_excel")
previous_globals = dict(am.__dict__)
exec(compile(ast.Module(body=[previous_node], type_ignores=[]), "<R01-import-only-replay>", "exec"), previous_globals)
previous_import = previous_globals["import_users_from_excel"]
state_cases = {}
for label in ("r01_function", "r02_current"):
    disabled, old_token = account("audit_disabled_" + label, "employee")
    deactivated = client.put("/api/admin/users/" + str(disabled["id"]),
        headers={"Authorization": "Bearer " + admin_token}, json={"is_active": False})
    assert deactivated.status_code == 200
    before_login = client.post("/api/auth/login", json={"username": disabled["username"], "password": "AuditUser#2026!Unique"}).status_code
    rows = [["姓名", "手机号", "工号", "职位"], ["Synthetic Disabled", disabled["username"], disabled["employee_no"], "员工"]]
    sheet = Sheet(rows)
    book = SimpleNamespace(sheet_by_index=lambda _: sheet)
    path = TMP / (label + "_synthetic.xls")
    path.write_bytes(b"synthetic-only")
    importer = previous_import if label == "r01_function" else am.import_users_from_excel
    with patch.dict(sys.modules, {"xlrd": SimpleNamespace(open_workbook=lambda _: book)}):
        imported = importer(str(path))
    after_row = am.get_user_by_id(disabled["id"])
    after_login = client.post("/api/auth/login", json={"username": disabled["username"], "password": "AuditUser#2026!Unique"}).status_code
    state_cases[label] = {"deactivate_http": deactivated.status_code, "before_login_http": before_login,
        "import_success": imported["success"], "after_is_active": after_row["is_active"],
        "after_login_http": after_login, "explicit_activation_field_supplied": False}
probes["excel_reactivation_regression"] = {"comparison": state_cases,
    "requirement_met": state_cases["r02_current"]["after_is_active"] == 0 and state_cases["r02_current"]["after_login_http"] == 403}

# F: persistent revocation is checked in a genuinely new Python process, not only a new TestClient.
logout = client.post("/api/auth/logout", headers={"Authorization": "Bearer " + admin_token})
child_env = os.environ.copy()
child_env["AUDIT_SYNTHETIC_TOKEN"] = admin_token
child = subprocess.run(
    [sys.executable, "-c", "import os,json; from app.auth_manager import verify_token_session; "
     "u,c,e=verify_token_session(os.environ['AUDIT_SYNTHETIC_TOKEN']); print(json.dumps({'code':c,'accepted':u is not None}))"],
    cwd=ROOT, env=child_env, capture_output=True, text=True, timeout=30,
)
child_result = json.loads(child.stdout.strip().splitlines()[-1])
probes["logout_revocation_real_process_restart"] = {
    "logout_http": logout.status_code, "child_exit": child.returncode,
    "child_session_status": child_result["code"], "child_accepted": child_result["accepted"],
    "requirement_met": logout.status_code == 200 and child_result["code"] == 401,
}

# G: the dedicated admin role-change path correctly invalidates the old token (Excel path contrast).
control, control_token = account("audit_role_control", "manager")
changed = am.admin_update_user_atomic(control["id"], {"role": "employee"})
control_status = client.get("/api/llm-rules", headers={"Authorization": "Bearer " + control_token}).status_code
probes["admin_role_change_token_control"] = {
    "update_success": changed["success"], "old_token_http": control_status,
    "requirement_met": changed["success"] and control_status == 401,
}

output = EV / "m4_r02_audit_probes.json"
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=True))
