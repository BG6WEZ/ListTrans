# -*- coding: utf-8 -*-
"""
V016-M0 / R01 审核补充探针 CE-6b：在全新源行上干净验证 event_seq 类型语义。
- HTTP 层(Pydantic 2.14.3)：字符串数字、布尔是否被静默归一
- 契约直调层：浮点截断、布尔显式拒绝
全部写入自建隔离根，不触碰业务路径。
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

import openpyxl
import pandas as pd
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[4]
AUDIT_ROOT = Path(tempfile.mkdtemp(prefix="listtrans_audit_v016_m0_r01_seq_"))
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

from app import feedback_contract, server  # noqa: E402
from app.audit_manager import init_audit_tables  # noqa: E402
from app.auth_manager import (  # noqa: E402
    get_auth_connection, get_password_hash, get_user_by_username,
    init_auth_db, issue_token_for_user,
)
from app.db_manager import import_materials_from_excel, init_db  # noqa: E402
from app.flywheel_db import init_flywheel_tables  # noqa: E402
from app.session_manager import init_session_db  # noqa: E402

SANDBOX = PROJECT_ROOT / "tests" / "fixtures" / "sandbox_materials_v1.json"


def _xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "暖通"
    ws.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"])
    ws.append(["030101", "排风机", "风量：1000m3/h", "台", 2])
    ws.append(["030102", "止回阀", "公称直径 DN100", "台", 4])
    ws.append(["030103", "风口", "百叶风口 500x200", "个", 8])
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


init_db()
init_auth_db()
init_session_db()
init_audit_tables()
init_flywheel_tables()
materials = json.loads(SANDBOX.read_text(encoding="utf-8"))["materials"]
xlsx_path = LAYOUT["upload_dir"] / "seq_materials.xlsx"
pd.DataFrame([{
    "物料分类": m["category"], "物料编码": m["code"], "物料名称": m["name"],
    "规格/型号": m["spec"], "计量单位": "台", "常用品牌": "", "描述": "",
} for m in materials]).to_excel(xlsx_path, index=False, startrow=1)
import_materials_from_excel(str(xlsx_path), mode="replace", filename=xlsx_path.name)

conn = get_auth_connection()
uname = f"seq_u_{uuid.uuid4().hex[:6]}"
ep = f"ep-{uname}-{uuid.uuid4().hex[:8]}"
conn.execute(
    """INSERT INTO users (username, password_hash, name, employee_no, phone, role,
       is_active, token_version, must_change_password, account_epoch)
       VALUES (?, ?, ?, ?, ?, 'employee', 1, 1, 0, ?)
       ON CONFLICT(username) DO UPDATE SET is_active=1, must_change_password=0,
       account_epoch=excluded.account_epoch""",
    (uname, get_password_hash("StrongSeqPass#2026!"), uname, f"SEQ_{uuid.uuid4().hex[:4]}", uname, ep),
)
conn.commit()
conn.close()
user = get_user_by_username(uname)
headers = {"Authorization": f"Bearer {issue_token_for_user(user)}"}

client = TestClient(server.app)
up = client.post(
    "/api/upload", headers=headers,
    files={"file": ("seq_list.xlsx", _xlsx_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
)
assert up.status_code == 200, up.text
task_id = up.json()["task_id"]
st = client.get(f"/api/upload/status?task_id={task_id}", headers=headers).json()
assert st["status"] == "completed", st
rows = st["result"]
r1, r2, r3 = rows[0], rows[1], rows[2]


def _payload(row, seq, eid=None):
    return {
        "client_event_id": eid or f"seq_{uuid.uuid4().hex[:8]}",
        "document_id": row["document_id"], "sheet_id": row["sheet_id"],
        "source_row_id": row["source_row_id"],
        "recommendation_snapshot_id": row["recommendation_snapshot_id"],
        "event_seq": seq, "action_type": "CONFIRM_TOP1",
        "final_targets": [{"code": "3.50.29.1369", "name": "目标", "spec": "DN100"}],
    }


def _stored(eid):
    c = sqlite3.connect(LAYOUT["materials_db"])
    c.row_factory = sqlite3.Row
    try:
        r = c.execute("SELECT event_seq, trust_level, superseded FROM flywheel_records "
                      "WHERE client_event_id = ?", (eid,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


out = {"isolation_root": str(AUDIT_ROOT)}

# S1 HTTP 字符串数字 "5" -> 全新行 r1
eid_s1 = f"s1_{uuid.uuid4().hex[:8]}"
resp = client.post("/api/flywheel/record", headers=headers, json=_payload(r1, "5", eid_s1))
out["S1_http_string_5"] = {"status": resp.status_code,
                           "body": resp.json() if resp.status_code == 200 else resp.json(),
                           "stored": _stored(eid_s1)}

# S2 HTTP 布尔 true -> 全新行 r2（验证 Pydantic 静默归一为 1，而非契约层 422）
eid_s2 = f"s2_{uuid.uuid4().hex[:8]}"
resp = client.post("/api/flywheel/record", headers=headers, json=_payload(r2, True, eid_s2))
out["S2_http_bool_true"] = {"status": resp.status_code,
                            "detail": resp.json().get("detail"),
                            "stored": _stored(eid_s2)}

# S3 HTTP 布尔 false -> 归一为 0 -> 契约 422
eid_s3 = f"s3_{uuid.uuid4().hex[:8]}"
resp = client.post("/api/flywheel/record", headers=headers, json=_payload(r2, False, eid_s3))
out["S3_http_bool_false"] = {"status": resp.status_code, "detail": resp.json().get("detail"),
                             "stored": _stored(eid_s3)}

# S4 契约直调浮点 2.9 -> int() 截断为 2 并落库（全新行 r3）
eid_s4 = f"s4_{uuid.uuid4().hex[:8]}"
try:
    res = feedback_contract.record_flywheel_event(user, _payload(r3, 2.9, eid_s4))
    out["S4_direct_float_2_9"] = {"outcome": "ACCEPTED", "returned_event_seq": res.get("event_seq"),
                                  "stored": _stored(eid_s4)}
except feedback_contract.FeedbackContractError as exc:
    out["S4_direct_float_2_9"] = {"outcome": "REJECTED", "status": exc.status_code, "detail": exc.detail}

# S5 契约直调布尔 True -> 显式 422（与 HTTP 层行为对照）
eid_s5 = f"s5_{uuid.uuid4().hex[:8]}"
try:
    feedback_contract.record_flywheel_event(user, _payload(r3, True, eid_s5))
    out["S5_direct_bool_true"] = {"outcome": "ACCEPTED", "stored": _stored(eid_s5)}
except feedback_contract.FeedbackContractError as exc:
    out["S5_direct_bool_true"] = {"outcome": "REJECTED", "status": exc.status_code,
                                  "detail": exc.detail}

report_path = Path(__file__).with_name("counterexamples_seq_report.json")
report_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"ISOLATION_ROOT={AUDIT_ROOT}")
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
