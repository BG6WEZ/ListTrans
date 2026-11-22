# -*- coding: utf-8 -*-
"""
V016-M0 / R01 审核独立运行态反例（审核只读探针，不修改任何业务实现或既有测试）。

CE-1 无 actor、自报 trust_level=trusted 的旧直调是否仍可写 FEEDBACK_SAMPLES_FILE
CE-2 伪造完整身份字段的旧 JSON 是否通过 _quarantine 并进入 get_feedback_suggestions
CE-3 DB COMMIT 后 JSON 同步失败 -> HTTP 500；同事件幂等重试是否仍不补文件
CE-4 /api/rematch 与 /api/match 篡改专业/文本/parsed_spec 后是否形成可信重算快照
CE-5 旧空 epoch 草稿是否被新可信草稿 SELECT+UPDATE 覆盖（隔离态丢失）
CE-6 event_seq 在 HTTP(Pydantic) 与契约直调两层的严格性（浮点/字符串/布尔/0/负数/缺失）

所有路径均在脚本自建隔离根内，不触碰业务库、账号库、配置、向量缓存、上传与输出目录。
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pandas as pd
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# 1. 隔离根与环境变量：必须先于任何 app.* 导入
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[4]
AUDIT_ROOT = Path(tempfile.mkdtemp(prefix="listtrans_audit_v016_m0_r01_ce_"))
LAYOUT = {
    "materials_db": AUDIT_ROOT / "db" / "materials.db",
    "auth_db": AUDIT_ROOT / "db" / "auth.db",
    "config_dir": AUDIT_ROOT / "config",
    "vector_cache_dir": AUDIT_ROOT / "vector_cache",
    "upload_dir": AUDIT_ROOT / "uploads",
    "output_dir": AUDIT_ROOT / "output",
    "log_dir": AUDIT_ROOT / "logs",
    "binding_feedback_file": AUDIT_ROOT / "runtime" / "binding_feedback.json",
    "feedback_samples_file": AUDIT_ROOT / "runtime" / "feedback_samples.json",
    "golden_detail_file": AUDIT_ROOT / "runtime" / "golden_last_run.json",
}
for key, path in LAYOUT.items():
    if key in ("materials_db", "auth_db", "binding_feedback_file",
               "feedback_samples_file", "golden_detail_file"):
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True)

os.environ.update({
    "MATERIALS_DB_PATH": str(LAYOUT["materials_db"]),
    "AUTH_DB_PATH": str(LAYOUT["auth_db"]),
    "CONFIG_DIR": str(LAYOUT["config_dir"]),
    "VECTOR_CACHE_DIR": str(LAYOUT["vector_cache_dir"]),
    "UPLOAD_DIR": str(LAYOUT["upload_dir"]),
    "OUTPUT_DIR": str(LAYOUT["output_dir"]),
    "LOG_DIR": str(LAYOUT["log_dir"]),
    "BINDING_FEEDBACK_FILE": str(LAYOUT["binding_feedback_file"]),
    "FEEDBACK_SAMPLES_FILE": str(LAYOUT["feedback_samples_file"]),
    "GOLDEN_DETAIL_FILE": str(LAYOUT["golden_detail_file"]),
    "LLM_ENABLED": "false",
    "AUTH_DEV_MODE": "false",
    "TESTING": "1",
})
for item in (PROJECT_ROOT / "config").iterdir():
    if item.is_file():
        shutil.copy2(item, LAYOUT["config_dir"] / item.name)
sys.path.insert(0, str(PROJECT_ROOT))

from app import feedback_contract, feedback_train, server, session_manager  # noqa: E402
from app.audit_manager import init_audit_tables  # noqa: E402
from app.auth_manager import (  # noqa: E402
    get_auth_connection,
    get_password_hash,
    get_user_by_username,
    init_auth_db,
    issue_token_for_user,
)
from app.db_manager import import_materials_from_excel, init_db  # noqa: E402
from app.flywheel_db import init_flywheel_tables  # noqa: E402

SANDBOX_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "sandbox_materials_v1.json"
report = {"isolation_root": str(AUDIT_ROOT), "cases": {}}


def _make_two_sheet_xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "暖通"
    ws1.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"])
    ws1.append(["030101", "排风机", "风量：1000m3/h", "台", 2])
    ws1.append(["030102", "止回阀", "公称直径 DN100", "台", 4])
    ws2 = wb.create_sheet("强电")
    ws2.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"])
    ws2.append(["030401", "电力电缆", "YJV 4x25", "米", 100])
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def _create_actor(username: str, role: str = "employee", epoch: str = None) -> dict:
    conn = get_auth_connection()
    ep = epoch or f"ep-{username}-{uuid.uuid4().hex[:8]}"
    conn.execute(
        """
        INSERT INTO users (
            username, password_hash, name, employee_no, phone, role,
            is_active, token_version, must_change_password, account_epoch
        ) VALUES (?, ?, ?, ?, ?, ?, 1, 1, 0, ?)
        ON CONFLICT(username) DO UPDATE SET
            password_hash = excluded.password_hash, role = excluded.role,
            is_active = 1, must_change_password = 0, account_epoch = excluded.account_epoch
        """,
        (username, get_password_hash("StrongCePass#2026!"), f"姓名_{username}",
         f"CE_{username}_{uuid.uuid4().hex[:4]}", username, role, ep),
    )
    conn.commit()
    conn.close()
    user = get_user_by_username(username)
    token = issue_token_for_user(user)
    return {"user": user, "token": token,
            "headers": {"Authorization": f"Bearer {token}"}}


def _upload_workbook(client: TestClient, headers: dict, filename: str, content: bytes) -> dict:
    up = client.post(
        "/api/upload",
        headers=headers,
        files={"file": (filename, content,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up.status_code == 200, up.text
    up_json = up.json()
    st = client.get(f"/api/upload/status?task_id={up_json['task_id']}", headers=headers)
    assert st.status_code == 200, st.text
    st_json = st.json()
    assert st_json["status"] == "completed", st_json
    return {"upload": up_json, "rows": st_json["result"]}


def _materials_sql(sql: str, params=()):
    conn = sqlite3.connect(LAYOUT["materials_db"])
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _auth_sql(sql: str, params=()):
    conn = sqlite3.connect(LAYOUT["auth_db"])
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. 基础环境：库表 + 沙箱物料 + 双用户 + 真实上传
# ---------------------------------------------------------------------------
init_db()
init_auth_db()
init_session_db = session_manager.init_session_db
init_session_db()
init_audit_tables()
init_flywheel_tables()

materials = json.loads(SANDBOX_FIXTURE.read_text(encoding="utf-8"))["materials"]
xlsx_path = LAYOUT["upload_dir"] / "ce_contract_materials.xlsx"
pd.DataFrame([
    {
        "物料分类": m["category"],
        "物料编码": m["code"],
        "物料名称": m["name"],
        "规格/型号": m["spec"],
        "计量单位": "台",
        "常用品牌": "",
        "描述": "",
    }
    for m in materials
]).to_excel(xlsx_path, index=False, startrow=1)
import_meta = import_materials_from_excel(str(xlsx_path), mode="replace", filename=xlsx_path.name)

client = TestClient(server.app)
u1 = _create_actor(f"ce_u1_{uuid.uuid4().hex[:6]}")
u2 = _create_actor(f"ce_u2_{uuid.uuid4().hex[:6]}")
u1_upload = _upload_workbook(client, u1["headers"], "ce_list.xlsx", _make_two_sheet_xlsx_bytes())
u1_rows = u1_upload["rows"]
row_a = next(r for r in u1_rows if r["sheet_name"] == "暖通" and r["source_row_num"] == 2)
row_b = next(r for r in u1_rows if r["sheet_name"] == "暖通" and r["source_row_num"] == 3)
row_c = next(r for r in u1_rows if r["sheet_name"] == "强电" and r["source_row_num"] == 2)

report["setup"] = {
    "materials_imported": import_meta.get("imported_rows"),
    "u1_user_id": u1["user"]["id"],
    "u1_epoch": u1["user"]["account_epoch"],
    "document_id": u1_upload["upload"]["document_id"],
    "rows": [{"tag": "row_a", **{k: row_a[k] for k in
             ("document_id", "sheet_id", "source_row_id", "recommendation_snapshot_id")}},
             {"tag": "row_b", **{k: row_b[k] for k in
             ("document_id", "sheet_id", "source_row_id", "recommendation_snapshot_id")}},
             {"tag": "row_c", **{k: row_c[k] for k in
             ("document_id", "sheet_id", "source_row_id", "recommendation_snapshot_id")}}],
}

# ---------------------------------------------------------------------------
# CE-1 无 actor 自报 trusted 旧直调
# ---------------------------------------------------------------------------
ce1_event = f"ce1_direct_{uuid.uuid4().hex[:8]}"
forged = {
    "trust_level": "trusted",
    "client_event_id": ce1_event,
    "actor_user_id": 999999,
    "actor_account_epoch": "ep-forged-ce1",
    "document_id": "doc_forged", "sheet_id": "sht_forged", "source_row_id": "row_forged",
    "row_id": 1, "raw_text": "CE1伪造旧直调风口", "auto_code": "A",
    "manual_code": "3.50.29.1369", "is_correct": False,
}
try:
    ce1_result = feedback_train.save_feedback_sample(forged, actor_user=None)
    ce1 = {"outcome": "WRITE_ACCEPTED", "result": ce1_result}
except Exception as exc:  # noqa: BLE001
    ce1 = {"outcome": "REJECTED", "error": f"{type(exc).__name__}: {exc}"}
fb_path = LAYOUT["feedback_samples_file"]
file_recs = json.loads(fb_path.read_text(encoding="utf-8")) if fb_path.exists() else []
ce1["file_record_count"] = len(file_recs)
ce1["forged_record_persisted_as_trusted"] = any(
    r.get("client_event_id") == ce1_event and r.get("trust_level") == "trusted"
    and r.get("actor_user_id") == 999999 for r in file_recs
)
report["cases"]["CE1_no_actor_trusted_direct_write"] = ce1

# ---------------------------------------------------------------------------
# CE-2 伪造完整身份字段的旧 JSON 进入建议统计
# ---------------------------------------------------------------------------
ce2_event = f"ce2_json_{uuid.uuid4().hex[:8]}"
forged_json = {
    "trust_level": "trusted",
    "client_event_id": ce2_event,
    "actor_user_id": 888888,
    "actor_account_epoch": "ep-forged-ce2",
    "document_id": "doc_forged2", "sheet_id": "sht_forged2", "source_row_id": "row_forged2",
    "row_id": 2, "raw_text": "CE2伪造JSON建议词", "auto_code": "B",
    "manual_code": "3.50.29.1369", "is_correct": False, "superseded": 0,
}
existing = json.loads(fb_path.read_text(encoding="utf-8")) if fb_path.exists() else []
existing.append(forged_json)
fb_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
suggestions = feedback_train.get_feedback_suggestions()
ce2 = {
    "suggestion_count": len(suggestions),
    "ce1_raw_text_in_suggestions": any("CE1伪造旧直调风口" in s.get("suggested_typo", "") for s in suggestions),
    "ce2_raw_text_in_suggestions": any("CE2伪造JSON建议词" in s.get("suggested_typo", "") for s in suggestions),
    "matching_suggestions": [s for s in suggestions if "CE" in s.get("suggested_typo", "")],
}
report["cases"]["CE2_forged_full_identity_json_into_suggestions"] = ce2

# ---------------------------------------------------------------------------
# CE-3 DB 提交后 JSON 同步失败 + 幂等重试不补文件
# ---------------------------------------------------------------------------
ce3_event = f"ce3_sync_{uuid.uuid4().hex[:8]}"
ce3_payload = {
    "client_event_id": ce3_event,
    "document_id": row_a["document_id"],
    "sheet_id": row_a["sheet_id"],
    "source_row_id": row_a["source_row_id"],
    "recommendation_snapshot_id": row_a["recommendation_snapshot_id"],
    "event_seq": 1,
    "row_id": 1,
    "raw_text": "CE3同步故障样本",
    "auto_code": "3.50.29.0194",
    "manual_code": "3.50.29.1369",
    "is_correct": False,
}
with patch("app.feedback_train.sync_trusted_feedback_file",
           side_effect=OSError("injected_ce3_sync_failure")):
    ce3_resp1 = client.post("/api/feedback/submit", headers=u1["headers"], json=ce3_payload)
ce3_db_rows = _materials_sql(
    "SELECT client_event_id, trust_level, event_seq, superseded FROM flywheel_feedback_samples "
    "WHERE client_event_id = ?", (ce3_event,))
file_ids_before_retry = {r.get("client_event_id") for r in
                         json.loads(fb_path.read_text(encoding="utf-8"))}
ce3_resp2 = client.post("/api/feedback/submit", headers=u1["headers"], json=ce3_payload)
file_ids_after_retry = {r.get("client_event_id") for r in
                        json.loads(fb_path.read_text(encoding="utf-8"))}
report["cases"]["CE3_db_commit_json_sync_failure"] = {
    "first_http_status": ce3_resp1.status_code,
    "first_http_detail": ce3_resp1.json().get("detail"),
    "db_row_committed": ce3_db_rows,
    "event_in_file_before_retry": ce3_event in file_ids_before_retry,
    "retry_http_status": ce3_resp2.status_code,
    "retry_idempotent_replay": ce3_resp2.json().get("idempotent_replay"),
    "event_in_file_after_retry": ce3_event in file_ids_after_retry,
    "divergence_self_healed": ce3_event in file_ids_after_retry,
}

# ---------------------------------------------------------------------------
# CE-4 重算/单条匹配篡改专业、文本与 parsed_spec
# ---------------------------------------------------------------------------
src_row = _materials_sql(
    "SELECT raw_name, raw_spec, professional FROM flywheel_source_rows WHERE source_row_id = ?",
    (row_a["source_row_id"],))
rev1_before = _materials_sql(
    "SELECT revision, professional FROM flywheel_recommendation_snapshots "
    "WHERE source_row_id = ? ORDER BY revision", (row_a["source_row_id"],))
ce4_rematch = client.post("/api/rematch", headers=u1["headers"], json={
    "row_id": 1,
    "text": "完全篡改文本 电力电缆 YJV 4x25",
    "fixed_params": {"model": "TAMPERED-MODEL", "air_volume": 999},
    "professional": "强电",
    "category": "", "brand": "", "unit": "",
    "document_id": row_a["document_id"],
    "sheet_id": row_a["sheet_id"],
    "source_row_id": row_a["source_row_id"],
})
ce4_snap_id = ce4_rematch.json().get("recommendation_snapshot_id") if ce4_rematch.status_code == 200 else None
ce4_snap = _materials_sql(
    "SELECT snapshot_id, revision, is_initial, snapshot_kind, professional, parsed_spec_json "
    "FROM flywheel_recommendation_snapshots WHERE snapshot_id = ?", (ce4_snap_id,)) if ce4_snap_id else []
ce4_match = client.post("/api/match", headers=u1["headers"], json={
    "text": "排风机 风量：1000m3/h",
    "name": "排风机",
    "professional": "暖通",
    "document_id": row_b["document_id"],
    "sheet_id": row_b["sheet_id"],
    "source_row_id": row_b["source_row_id"],
    "parsed_spec": {"attrs": {"CE4伪造维度": "X"}},
})
ce4_match_snap_id = ce4_match.json().get("recommendation_snapshot_id") if ce4_match.status_code == 200 else None
ce4_match_snap = _materials_sql(
    "SELECT snapshot_id, revision, snapshot_kind, professional, parsed_spec_json "
    "FROM flywheel_recommendation_snapshots WHERE snapshot_id = ?",
    (ce4_match_snap_id,)) if ce4_match_snap_id else []
rev1_after = _materials_sql(
    "SELECT revision, professional FROM flywheel_recommendation_snapshots "
    "WHERE source_row_id = ? ORDER BY revision", (row_a["source_row_id"],))
report["cases"]["CE4_rematch_context_tampering"] = {
    "source_row_before": src_row,
    "rematch_http_status": ce4_rematch.status_code,
    "rematch_snapshot": ce4_snap,
    "match_http_status": ce4_match.status_code,
    "match_snapshot": ce4_match_snap,
    "row_a_revisions_before": rev1_before,
    "row_a_revisions_after": rev1_after,
    "rev1_unchanged": rev1_before == [r for r in rev1_after if r["revision"] == 1],
}

# ---------------------------------------------------------------------------
# CE-5 旧空 epoch 草稿被新可信草稿覆盖
# ---------------------------------------------------------------------------
legacy_id = session_manager.save_draft_session(
    u1["user"]["id"], "legacy.xlsx", "暖通", "LEGACY_ITEMS_JSON",
    account_epoch="", document_id="", work_session_id="", trust_level="legacy_untrusted",
)
legacy_rows_before = _auth_sql(
    "SELECT id, trust_level, owner_account_epoch, document_id, items_json "
    "FROM work_sessions WHERE user_id = ? AND session_type = 'draft'", (u1["user"]["id"],))
ce5_resp = client.post("/api/session/draft", headers=u1["headers"], json={
    "original_filename": "new_trusted.xlsx",
    "professional": "暖通",
    "items_json": "NEW_TRUSTED_ITEMS_JSON",
    "document_id": u1_upload["upload"]["document_id"],
    "work_session_id": u1_upload["upload"]["work_session_id"],
})
drafts_after = _auth_sql(
    "SELECT id, trust_level, owner_account_epoch, document_id, items_json, updated_at "
    "FROM work_sessions WHERE user_id = ? AND session_type = 'draft'", (u1["user"]["id"],))
report["cases"]["CE5_legacy_draft_overwrite"] = {
    "legacy_draft_id": legacy_id,
    "drafts_before": legacy_rows_before,
    "trusted_save_http_status": ce5_resp.status_code,
    "trusted_save_body": ce5_resp.json(),
    "drafts_after": drafts_after,
    "legacy_row_reused_and_upgraded": any(
        d["id"] == legacy_id and d["trust_level"] == "trusted"
        and d["items_json"] == "NEW_TRUSTED_ITEMS_JSON" for d in drafts_after),
    "legacy_items_preserved": any(d["items_json"] == "LEGACY_ITEMS_JSON" for d in drafts_after),
    "draft_row_count_after": len(drafts_after),
}

# ---------------------------------------------------------------------------
# CE-6 event_seq 严格性：HTTP 层与契约直调层
# ---------------------------------------------------------------------------
def _flywheel_payload(row, seq, event_id=None):
    return {
        "client_event_id": event_id or f"ce6_{uuid.uuid4().hex[:8]}",
        "document_id": row["document_id"],
        "sheet_id": row["sheet_id"],
        "source_row_id": row["source_row_id"],
        "recommendation_snapshot_id": row["recommendation_snapshot_id"],
        "event_seq": seq,
        "action_type": "CONFIRM_TOP1",
        "final_targets": [{"code": "3.50.29.1369", "name": "CE6目标", "spec": "DN100"}],
    }

http_cases = {}
for label, seq, row in (
    ("float_fraction_1_5", 1.5, row_c),
    ("float_whole_1_0", 1.0, row_c),
    ("string_2", "2", row_c),
    ("bool_true", True, row_c),
    ("zero", 0, row_c),
    ("negative_minus_3", -3, row_c),
):
    resp = client.post("/api/flywheel/record", headers=u1["headers"],
                       json=_flywheel_payload(row, seq))
    http_cases[label] = {
        "sent_event_seq": seq,
        "status_code": resp.status_code,
        "detail": resp.json().get("detail") if resp.status_code != 200 else None,
        "idempotent_replay": resp.json().get("idempotent_replay") if resp.status_code == 200 else None,
    }
# 缺失 event_seq
resp_missing = client.post("/api/flywheel/record", headers=u1["headers"],
                           json=_flywheel_payload(row_c, None))
http_cases["missing"] = {
    "status_code": resp_missing.status_code,
    "detail": resp_missing.json().get("detail"),
}
# 契约直调：浮点 1.5 是否被 int() 截断为 1 并落库
direct_event = f"ce6direct_{uuid.uuid4().hex[:8]}"
try:
    direct_result = feedback_contract.record_flywheel_event(
        u1["user"], _flywheel_payload(row_c, 1.5, event_id=direct_event))
    direct_db = _materials_sql(
        "SELECT event_seq, trust_level FROM flywheel_records WHERE client_event_id = ?",
        (direct_event,))
    direct_outcome = {"outcome": "ACCEPTED", "result_event_seq": direct_result.get("event_seq"),
                      "db_row": direct_db}
except feedback_contract.FeedbackContractError as exc:
    direct_outcome = {"outcome": "REJECTED", "status_code": exc.status_code,
                      "code": exc.detail.get("code") if isinstance(exc.detail, dict) else exc.detail}
report["cases"]["CE6_event_seq_strictness"] = {"http": http_cases, "direct_float_1_5": direct_outcome}

# ---------------------------------------------------------------------------
# 落盘报告
# ---------------------------------------------------------------------------
report_path = Path(__file__).with_name("counterexamples_report.json")
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"ISOLATION_ROOT={AUDIT_ROOT}")
print(f"REPORT={report_path}")
for case_id, case in report["cases"].items():
    print(f"--- {case_id}")
    print(json.dumps(case, ensure_ascii=False, indent=2, default=str)[:4000])
