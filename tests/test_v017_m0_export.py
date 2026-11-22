from io import BytesIO
import json
import os
from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook

from app.rule_governance import load_scoped_aliases
from tests.test_v016_m0_feedback_contract import _upload_workbook, m0_env


def test_blank_scoped_aliases_preserve_trusted_confirmation_export(m0_env):
    client = m0_env["client"]
    employee = m0_env["u1"]["headers"]
    admin = m0_env["adm"]["headers"]
    nonce = uuid4().hex[:10]
    source, other = f"排风机 {nonce}", f"止回阀 {nonce}"
    workbook = Workbook()
    hvac = workbook.active
    hvac.title = "暖通"
    electrical = workbook.create_sheet("强电")
    for sheet in (hvac, electrical):
        sheet.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"])
        sheet.append(["030101", source, "风量：1000m3/h", "台", 2])
    hvac.append(["030102", other, "公称直径 DN100", "台", 4])
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    rows = _upload_workbook(client, employee, "blank-alias.xlsx", content.getvalue())["rows"]
    assert len(rows) == 3
    for row in rows:
        response = client.post("/api/flywheel/record", headers=employee, json={
            "client_event_id": f"m0-empty-{uuid4().hex}",
            "document_id": row["document_id"],
            "sheet_id": row["sheet_id"],
            "source_row_id": row["source_row_id"],
            "event_seq": 1,
            "action_type": "CONFIRM_TOP1",
            "final_targets": [{"code": "3.50.29.1369", "name": "排风机", "spec": "1000"}],
        })
        assert response.status_code == 200, response.text

    def exported():
        response = client.get("/api/flywheel/export/benchmark", headers=admin)
        assert response.status_code == 200, response.text
        return {(row["raw_name"], row["professional"]) for row in response.json()["items"]
                if row["raw_name"] in {source, other}}

    path = Path(os.environ["CONFIG_DIR"]) / "scoped_aliases.json"
    before = path.read_bytes() if path.exists() else None
    try:
        path.write_text(json.dumps({"暖通": {"": "甲", " \t ": "乙"}}), encoding="utf-8")
        assert load_scoped_aliases() == {"暖通": {}}
        assert exported() == {(source, "暖通"), (source, "强电"), (other, "暖通")}

        path.write_text(json.dumps({"暖通": {"": "甲", " \t ": "乙", f" {source} ": "丙"}}),
                        encoding="utf-8")
        assert load_scoped_aliases() == {"暖通": {source: "丙"}}
        assert exported() == {(source, "强电"), (other, "暖通")}
    finally:
        if before is None:
            path.unlink()
        else:
            path.write_bytes(before)
