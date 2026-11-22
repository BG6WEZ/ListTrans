# -*- coding: utf-8 -*-
"""
V0.1.5 M5 File Boundary, Resource Quota, Dependency Availability, Warning Compat,
and M3-R01 Protection Regression Tests (F18, F24, DEP-M5-01, COMPAT-M5-01, R-M3-01).
"""

from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import struct
import threading
import time
import warnings
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import openpyxl
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import admin_api, audit_manager, auth_manager, db_manager, excel_boundary, server


def _make_xlsx_bytes(sheets: Dict[str, List[List[Any]]]) -> bytes:
    wb = openpyxl.Workbook()
    first = True
    for name, rows in sheets.items():
        if first:
            ws = wb.active
            ws.title = name
            first = False
        else:
            ws = wb.create_sheet(title=name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def _make_biff8_xls_bytes(rows: List[List[Any]], sheet_name: str = "Sheet1") -> bytes:
    """Build a valid minimal OLE2 CFB BIFF8 .xls binary purely in memory (zero PII / external deps)."""

    def biff_rec(rec_id: int, payload: bytes) -> bytes:
        return struct.pack("<HH", rec_id, len(payload)) + payload

    bof_wb = biff_rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0x0DBB, 0x07CC, 0, 0x0600))
    codepage = biff_rec(0x0042, struct.pack("<H", 1200))
    sh_ascii = sheet_name.encode("ascii", errors="replace") or b"Sheet1"
    bs_name_payload = struct.pack("<BB", len(sh_ascii), 0x00) + sh_ascii
    bounds_stub = biff_rec(0x0085, struct.pack("<IHH", 0, 0, 0) + bs_name_payload)
    eof = biff_rec(0x000A, b"")

    ws_offset = len(bof_wb) + len(codepage) + len(bounds_stub) + len(eof)
    bounds_fixed = biff_rec(0x0085, struct.pack("<IHH", ws_offset, 0, 0) + bs_name_payload)
    wb_globals = bof_wb + codepage + bounds_fixed + eof

    bof_ws = biff_rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0010, 0x0DBB, 0x07CC, 0, 0x0600))
    max_r = len(rows)
    max_c = max((len(r) for r in rows), default=0)
    dims = biff_rec(0x0200, struct.pack("<IIHHH", 0, max_r, 0, max_c, 0))
    cells = bytearray()
    for r_idx, row in enumerate(rows):
        for c_idx, val in enumerate(row):
            if val is None or val == "":
                continue
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cells += biff_rec(0x0203, struct.pack("<HHHd", r_idx, c_idx, 0, float(val)))
            else:
                s = str(val)
                s_bytes = s.encode("utf-16-le")
                cells += biff_rec(0x0204, struct.pack("<HHHHB", r_idx, c_idx, 0, len(s), 0x01) + s_bytes)
    ws_stream = bof_ws + dims + bytes(cells) + eof
    workbook_stream = wb_globals + ws_stream
    if len(workbook_stream) < 4096:
        workbook_stream += b"\x00" * (4096 - len(workbook_stream))

    wb_size = len(workbook_stream)
    n_wb_secs = (wb_size + 511) // 512
    workbook_padded = workbook_stream + b"\x00" * (n_wb_secs * 512 - wb_size)
    dir_sec = n_wb_secs
    fat_sec = n_wb_secs + 1
    assert n_wb_secs + 2 <= 128

    def dir_entry(name: str, obj_type: int, child: int = -1, start_sec: int = -2, size: int = 0) -> bytes:
        nm_utf16 = (name + "\x00").encode("utf-16-le") if name else b""
        nm_field = nm_utf16[:64].ljust(64, b"\x00")
        return struct.pack(
            "<64sHBBiii16sIQQiII",
            nm_field,
            len(nm_utf16),
            obj_type,
            1,
            -1,
            -1,
            child,
            b"\x00" * 16,
            0,
            0,
            0,
            start_sec,
            size,
            0,
        )

    dir_sector = (
        dir_entry("Root Entry", 5, child=1, start_sec=-2, size=0)
        + dir_entry("Workbook", 2, start_sec=0, size=wb_size)
        + dir_entry("", 0, start_sec=0, size=0)
        + dir_entry("", 0, start_sec=0, size=0)
    )
    fat = [-1] * 128
    for s in range(n_wb_secs - 1):
        fat[s] = s + 1
    fat[n_wb_secs - 1] = -2
    fat[dir_sec] = -2
    fat[fat_sec] = -3
    fat_sector = struct.pack("<128i", *fat)
    difat = [fat_sec] + [-1] * 108
    header = struct.pack(
        "<8s16sHHHHH6sIIIIIiIiI",
        b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1",
        b"\x00" * 16,
        0x003E,
        0x0003,
        0xFFFE,
        9,
        6,
        b"\x00" * 6,
        0,
        1,
        dir_sec,
        0,
        4096,
        -2,
        0,
        -2,
        0,
    ) + struct.pack("<109i", *difat)
    return header + workbook_padded + dir_sector + fat_sector


def _make_plain_zip_bytes(entries: Dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for k, v in entries.items():
            zf.writestr(k, v)
    return buf.getvalue()


def _create_isolated_user(username: str, role: str, emp_no: str) -> Dict[str, Any]:
    conn = auth_manager.get_auth_connection()
    try:
        pw_hash = auth_manager.get_password_hash("M5Boundary#Strong2026!")
        epoch = secrets.token_hex(12)
        conn.execute(
            """
            INSERT OR REPLACE INTO users (
                username, password_hash, name, dept1, dept2, dept3,
                employee_no, position, phone, role, is_active, need_emp_no_setup,
                token_version, must_change_password, account_epoch
            ) VALUES (?, ?, ?, '工程部', '', '', ?, ?, ?, ?, 1, 0, 1, 0, ?)
            """,
            (username, pw_hash, f"Test_{role}", emp_no, role, username, role, epoch),
        )
        conn.commit()
    finally:
        conn.close()
    user = auth_manager.get_user_by_username(username)
    assert user is not None
    token = auth_manager.issue_token_for_user(user)
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "m5_iso"
    upload_dir = root / "uploads"
    output_dir = root / "output"
    log_dir = root / "logs"
    vec_dir = root / "vector_cache"
    for p in (upload_dir, output_dir, log_dir, vec_dir):
        p.mkdir(parents=True, exist_ok=True)

    auth_db = str(root / "auth.db")
    mat_db = str(root / "materials.db")
    fb_file = str(root / "binding_feedback.json")
    fs_file = str(root / "feedback_samples.json")

    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("AUTH_DB_PATH", auth_db)
    monkeypatch.setenv("MATERIALS_DB_PATH", mat_db)
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("LOG_DIR", str(log_dir))
    monkeypatch.setenv("VECTOR_CACHE_DIR", str(vec_dir))
    monkeypatch.setenv("BINDING_FEEDBACK_FILE", fb_file)
    monkeypatch.setenv("FEEDBACK_SAMPLES_FILE", fs_file)

    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", auth_db)
    monkeypatch.setattr(db_manager, "DB_PATH", mat_db)
    monkeypatch.setattr(server, "DB_PATH", mat_db)
    monkeypatch.setattr(server, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(server, "BINDING_FEEDBACK_FILE", fb_file)

    import app.feedback_train as ft

    monkeypatch.setattr(ft, "FEEDBACK_FILE", fs_file)

    auth_manager.init_auth_db()
    audit_manager.init_audit_tables()
    from app import session_manager

    session_manager.init_session_db()
    db_manager.init_db()

    # Clear lock state defensively before each test
    if server._db_import_lock.locked():
        try:
            server._db_import_lock.release()
        except RuntimeError:
            pass
    server._db_import_lock_manager = excel_boundary.DbImportLockManager(server._db_import_lock)

    emp = _create_isolated_user("13800001101", "employee", "EMP-M5-01")
    director = _create_isolated_user("13800001102", "director", "DIR-M5-01")
    admin = _create_isolated_user("13800001103", "admin", "ADM-M5-01")

    client = TestClient(server.app)
    yield {
        "client": client,
        "emp": emp,
        "director": director,
        "admin": admin,
        "upload_dir": upload_dir,
        "output_dir": output_dir,
        "root": root,
    }
    if server._db_import_lock.locked():
        try:
            server._db_import_lock.release()
        except RuntimeError:
            pass


def _valid_query_xlsx() -> bytes:
    return _make_xlsx_bytes(
        {
            "暖通清单": [
                ["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"],
                ["030701001", "镀锌钢板风管", "矩形风管 1000x500 δ=1.0", "m2", 120],
            ]
        }
    )


def _valid_materials_rows() -> List[List[Any]]:
    return [
        ["标准物料库导入模板"],
        ["物料分类", "物料编码", "物料名称", "规格/型号", "计量单位", "常用品牌", "描述"],
        ["风管", "M5-MAT-001", "镀锌钢板风管", "1000x500 δ=1.0", "m2", "自有", "矩形风管"],
        ["阀门", "M5-MAT-002", "防爆电动调节阀", "DN100 PN16 防爆", "台", None, None],
    ]


# ---------------------------------------------------------------------------
# F18: Format Capability & Engine Boundary Tests
# ---------------------------------------------------------------------------


def test_f18_list_upload_strict_xlsx_and_precheck(isolated_env, monkeypatch):
    client: TestClient = isolated_env["client"]
    headers = isolated_env["emp"]["headers"]
    upload_dir: Path = isolated_env["upload_dir"]

    # Stub async background processing to avoid heavy vector init on precheck tests
    calls: List[str] = []
    monkeypatch.setattr(server, "async_process_upload_task", lambda *a, **k: calls.append("called"))

    # 1. Accept lowercase .xlsx and uppercase .XLSX
    for fname in ("list.xlsx", "LIST.XLSX"):
        resp = client.post(
            "/api/upload",
            headers=headers,
            files={"file": (fname, _valid_query_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 200, resp.text
        assert "task_id" in resp.json()
    assert len(calls) == 2

    # 2. Reject .xls / .XLS / .csv / no extension BEFORE scheduling background task
    before_calls = len(calls)
    valid_xls = _make_biff8_xls_bytes([["项目名称", "规格型号"], ["风机", "100W"]])
    bad_uploads = [
        ("old_list.xls", valid_xls),
        ("OLD_LIST.XLS", valid_xls),
        ("list.csv", b"name,spec\nfan,100w\n"),
        ("noext", _valid_query_xlsx()),
        # Disguised extensions with .xlsx suffix:
        ("fake_csv_as_xlsx.xlsx", b"a,b,c\n1,2,3\n"),
        ("xls_renamed.xlsx", valid_xls),
        ("plain_zip.xlsx", _make_plain_zip_bytes({"hello.txt": b"world"})),
        ("corrupt.xlsx", b"PK\x03\x04corrupted_garbage_bytes_here"),
        (
            "encrypted.xlsx",
            _make_plain_zip_bytes(
                {
                    "EncryptionInfo": b"encrypted",
                    "[Content_Types].xml": b"<Types/>",
                    "xl/workbook.xml": b"<workbook/>",
                }
            ),
        ),
        ("empty_0bytes.xlsx", b""),
        ("empty_sheet.xlsx", _make_xlsx_bytes({"暖通": [[None, None], [None, None]]})),
        (
            "wrong_cols.xlsx",
            _make_xlsx_bytes({"Sheet1": [["姓名", "手机", "邮箱"], ["张三", "13900001111", "a@b.c"]]}),
        ),
        (
            "header_only.xlsx",
            _make_xlsx_bytes({"暖通": [["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"]]}),
        ),
    ]
    for fname, payload in bad_uploads:
        before_files = set(os.listdir(upload_dir))
        r = client.post("/api/upload", headers=headers, files={"file": (fname, payload, "application/octet-stream")})
        assert r.status_code == 400, f"{fname} should return 400, got {r.status_code}: {r.text}"
        assert set(os.listdir(upload_dir)) == before_files, f"Residual temp file left for {fname}"

    assert len(calls) == before_calls


def test_runtime_cleanup_admin_only_and_dry_run(isolated_env):
    client = isolated_env["client"]
    path = isolated_env["upload_dir"] / "标准格式_9876abcd.xlsx"
    path.write_bytes(b"synthetic exported file")
    old = time.time() - 200000
    os.utime(path, (old, old))
    url = "/api/admin/runtime/cleanup"
    assert client.post(url, headers=isolated_env["emp"]["headers"]).status_code == 403
    admin = isolated_env["admin"]["headers"]
    dry = client.post(url, headers=admin)
    assert dry.status_code == 200 and dry.json()["eligible"] == 1 and path.exists()
    applied = client.post(url + "?dry_run=false", headers=admin)
    assert applied.status_code == 200 and applied.json()["removed"] == 1 and not path.exists()


def test_f18_db_import_accepts_xlsx_and_real_xls_and_rejects_bad(isolated_env, monkeypatch, tmp_path: Path):
    client: TestClient = isolated_env["client"]
    headers = isolated_env["director"]["headers"]

    # Skip heavy FAISS encoding inside background vector step while testing real DB SQLite import
    monkeypatch.setattr(server.index_manager, "build_index", lambda *a, **k: None)
    monkeypatch.setattr(server.index_manager, "save_cache", lambda *a, **k: None)

    rows = _valid_materials_rows()
    xlsx_bytes = _make_xlsx_bytes({"物料表": rows})
    xls_bytes = _make_biff8_xls_bytes(rows, sheet_name="Materials")

    # 1. Both .XLSX and .XLS succeed end-to-end
    r1 = client.post(
        "/api/db/upload",
        headers=headers,
        data={"mode": "replace", "disable_mode": "on_expire"},
        files={"file": ("MAT.XLSX", xlsx_bytes, "application/octet-stream")},
    )
    assert r1.status_code == 200, r1.text
    tid1 = r1.json()["task_id"]
    st1 = client.get(f"/api/upload/status?task_id={tid1}", headers=headers).json()
    assert st1["status"] == "completed"
    assert db_manager.query_by_code("M5-MAT-001") is not None

    r2 = client.post(
        "/api/db/upload",
        headers=headers,
        data={"mode": "replace", "disable_mode": "on_expire"},
        files={"file": ("MAT.XLS", xls_bytes, "application/vnd.ms-excel")},
    )
    assert r2.status_code == 200, r2.text
    tid2 = r2.json()["task_id"]
    st2 = client.get(f"/api/upload/status?task_id={tid2}", headers=headers).json()
    assert st2["status"] == "completed", st2
    mat2 = db_manager.query_by_code("M5-MAT-002")
    assert mat2 is not None and mat2["name"] == "防爆电动调节阀"

    # 2. Disguised/corrupted/empty .xlsx and .xls rejected explicitly at HTTP precheck or terminal fail
    disguised_bad = [
        ("fake.xls", xlsx_bytes),  # xlsx renamed to .xls
        ("fake.xlsx", xls_bytes),  # xls renamed to .xlsx
        ("garbage.xls", b"not an ole2 file"),
        ("empty.xls", _make_biff8_xls_bytes([])),
        ("empty.xlsx", _make_xlsx_bytes({"Sheet1": [[None, None]]})),
        ("bad.txt", b"hello"),
    ]
    for fname, body in disguised_bad:
        resp = client.post(
            "/api/db/upload",
            headers=headers,
            data={"mode": "replace", "disable_mode": "on_expire"},
            files={"file": (fname, body, "application/octet-stream")},
        )
        assert resp.status_code == 400, f"{fname} expected 400 got {resp.status_code}: {resp.text}"
        assert not server._db_import_lock.locked()

    # 3. Missing required material columns or empty material rows fail explicitly in import_materials_from_excel
    missing_cols_path = tmp_path / "missing_cols.xls"
    missing_cols_path.write_bytes(_make_biff8_xls_bytes([["标题"], ["编号", "名称"], ["1", "A"]]))
    with pytest.raises(excel_boundary.ExcelBoundaryError):
        db_manager.import_materials_from_excel(str(missing_cols_path), mode="replace", filename="missing_cols.xls")

    empty_code_path = tmp_path / "empty_codes.xlsx"
    empty_code_path.write_bytes(
        _make_xlsx_bytes({"Sheet1": [["标题"], ["物料编码", "物料名称"], [None, "缺编码行"]]})
    )
    with pytest.raises(excel_boundary.ExcelBoundaryError):
        db_manager.import_materials_from_excel(str(empty_code_path), mode="replace", filename="empty_codes.xlsx")


def test_f18_personnel_import_supports_xlsx_and_real_xls_and_rejects_bad(isolated_env, tmp_path: Path):
    client: TestClient = isolated_env["client"]
    adm_headers = isolated_env["admin"]["headers"]

    rows = [
        ["姓名", "1级部门", "工号", "职位", "手机"],
        ["王测试", "工程中心", "EMP-XLS-1", "暖通工程师", "13911112222"],
    ]
    xls_path = tmp_path / "users.xls"
    xls_path.write_bytes(_make_biff8_xls_bytes(rows))
    res_xls = auth_manager.import_users_from_excel(str(xls_path))
    assert res_xls["success"] is True, res_xls
    assert res_xls["imported_count"] == 1

    xlsx_path = tmp_path / "users.xlsx"
    xlsx_path.write_bytes(
        _make_xlsx_bytes({"人员": [["姓名", "1级部门", "工号", "职位", "手机"], ["李测试", "工程中心", "EMP-XLSX-1", "部门经理", "13933334444"]]})
    )
    res_xlsx = auth_manager.import_users_from_excel(str(xlsx_path))
    assert res_xlsx["success"] is True, res_xlsx
    assert res_xlsx["imported_count"] == 1
    assert auth_manager.get_user_by_username("13933334444")["role"] == "manager"

    # Empty data rows rejected without silent success
    empty_xlsx = tmp_path / "empty_users.xlsx"
    empty_xlsx.write_bytes(_make_xlsx_bytes({"人员": [["姓名", "手机"], ["", ""]]}))
    res_empty = auth_manager.import_users_from_excel(str(empty_xlsx))
    assert res_empty["success"] is False

    # Disguised txt->xls rejected
    bad_xls = tmp_path / "bad_users.xls"
    bad_xls.write_bytes(b"plain text disguised as xls")
    res_bad = auth_manager.import_users_from_excel(str(bad_xls))
    assert res_bad["success"] is False

    # HTTP /api/admin/users/sync-excel maps failure to explicit 400
    r_sync = client.post("/api/admin/users/sync-excel", headers=adm_headers)
    assert r_sync.status_code == 400


def test_f18_frontend_html_js_contract():
    root = Path(__file__).resolve().parent.parent
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    js = (root / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="xlsx-file-input" accept=".xlsx"' in html
    assert 'id="db-upload-input" style="display: none;" accept=".xlsx,.xls"' in html
    assert "选择 <code>.xlsx</code> 文件" in html
    assert "endsWith('.xlsx')" in js
    assert "endsWith('.xls')" in js


# ---------------------------------------------------------------------------
# F24: Quotas, Zip Bomb, Reset Dimensions, and Lock Release Matrix
# ---------------------------------------------------------------------------


def test_f24_streaming_byte_cap_and_content_length(isolated_env, monkeypatch):
    client: TestClient = isolated_env["client"]
    headers = isolated_env["emp"]["headers"]
    upload_dir: Path = isolated_env["upload_dir"]

    monkeypatch.setattr(server, "MAX_UPLOAD_SIZE", 512)
    # 1. Spoofed small Content-Length header cannot bypass streaming byte limit -> 413
    payload = _valid_query_xlsx()  # ~5 KB > 512 bytes
    assert len(payload) > 512
    before_files = set(os.listdir(upload_dir))
    r = client.post(
        "/api/upload",
        headers={**headers, "Content-Length": "100"},
        files={"file": ("big.xlsx", payload, "application/octet-stream")},
    )
    assert r.status_code == 413
    assert set(os.listdir(upload_dir)) == before_files

    # 2. Direct stream_save_upload_file invalid Content-Length -> 400; declared > limit -> 413
    class _Req:
        def __init__(self, cl, ct="application/octet-stream"):
            self.headers = {"content-length": cl, "content-type": ct}

    class _UF:
        async def read(self, n):
            return b""

        async def close(self):
            pass

    import asyncio

    with pytest.raises(excel_boundary.ExcelBoundaryError) as exc_bad_cl:
        asyncio.run(excel_boundary.stream_save_upload_file(_Req("abc"), _UF(), str(upload_dir / "x.xlsx")))
    assert exc_bad_cl.value.status_code == 400

    with pytest.raises(excel_boundary.UploadTooLargeError) as exc_big_cl:
        asyncio.run(
            excel_boundary.stream_save_upload_file(
                _Req(str(excel_boundary.DEFAULT_MAX_UPLOAD_BYTES + 1)),
                _UF(),
                str(upload_dir / "y.xlsx"),
            )
        )
    assert exc_big_cl.value.status_code == 413


def test_f24_zip_bomb_and_workbook_budgets_with_reset_dimensions(tmp_path: Path, monkeypatch):
    zip_budget = excel_boundary.ExcelParseBudget(
        max_upload_bytes=1024 * 1024,
        stream_chunk_bytes=65536,
        max_zip_entries=10,
        max_zip_uncompressed_bytes=4096,
    )
    small_budget = excel_boundary.ExcelParseBudget(
        max_upload_bytes=1024 * 1024,
        stream_chunk_bytes=65536,
        max_zip_entries=64,
        max_zip_uncompressed_bytes=256 * 1024,
        max_sheets=3,
        max_rows_per_sheet=15,
        max_cols_per_sheet=8,
        max_total_cells=40,
        parse_timeout_seconds=5.0,
    )

    # 1. Forged / real decompressed zip bomb > 4096 bytes uncompressed
    bomb_bytes = _make_plain_zip_bytes(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
            "xl/junk.bin": b"A" * 10000,
        }
    )
    assert len(bomb_bytes) < 1000  # small compressed payload!
    bomb_path = tmp_path / "bomb.xlsx"
    bomb_path.write_bytes(bomb_bytes)
    with pytest.raises(excel_boundary.ExcelBoundaryError, match="解压"):
        excel_boundary.validate_excel_container(str(bomb_path), frozenset({".xlsx"}), budget=zip_budget)

    # 2. Too many zip entries
    many_entries = {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"}
    for i in range(12):
        many_entries[f"xl/f{i}.txt"] = b"x"
    many_path = tmp_path / "entries.xlsx"
    many_path.write_bytes(_make_plain_zip_bytes(many_entries))
    with pytest.raises(excel_boundary.ExcelBoundaryError, match="条目数"):
        excel_boundary.validate_excel_container(str(many_path), frozenset({".xlsx"}), budget=zip_budget)

    # 3. Spoofed <dimension ref="A1:A1"/> hiding column 12 (> max_cols=8) bypassed via reset_dimensions()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "H1"
    ws.cell(1, 12, "SPOOFED_WIDE_COL")
    wide_path = tmp_path / "spoof_dim.xlsx"
    wb.save(wide_path)
    wb.close()
    # Rewrite sheet1.xml dimension tag to lie as A1:A1
    with zipfile.ZipFile(wide_path, "r") as zin:
        items = {n: zin.read(n) for n in zin.namelist()}
    items["xl/worksheets/sheet1.xml"] = re_sub_dim(items["xl/worksheets/sheet1.xml"])
    with zipfile.ZipFile(wide_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for n, b in items.items():
            zout.writestr(n, b)

    with pytest.raises(excel_boundary.ExcelBoundaryError, match="列数"):
        excel_boundary.load_validated_excel_sheets(str(wide_path), frozenset({".xlsx"}), budget=small_budget)

    # 4. Too many sheets / rows / total cells / timeout
    four_sheets = tmp_path / "four_sheets.xlsx"
    four_sheets.write_bytes(_make_xlsx_bytes({"S1": [["a"]], "S2": [["b"]], "S3": [["c"]], "S4": [["d"]]}))
    with pytest.raises(excel_boundary.ExcelBoundaryError, match="工作表数量"):
        excel_boundary.load_validated_excel_sheets(str(four_sheets), frozenset({".xlsx"}), budget=small_budget)

    too_many_rows = tmp_path / "rows.xlsx"
    too_many_rows.write_bytes(_make_xlsx_bytes({"S1": [[f"r{i}"] for i in range(18)]}))
    with pytest.raises(excel_boundary.ExcelBoundaryError, match="行数"):
        excel_boundary.load_validated_excel_sheets(str(too_many_rows), frozenset({".xlsx"}), budget=small_budget)

    too_many_cells = tmp_path / "cells.xlsx"
    too_many_cells.write_bytes(_make_xlsx_bytes({"S1": [[1, 2, 3, 4, 5, 6, 7, 8] for _ in range(7)]}))
    with pytest.raises(excel_boundary.ExcelBoundaryError, match="单元格总数"):
        excel_boundary.load_validated_excel_sheets(str(too_many_cells), frozenset({".xlsx"}), budget=small_budget)

    # Timeout check using zero monotonic budget
    zero_time_budget = excel_boundary.ExcelParseBudget(parse_timeout_seconds=-0.01)
    ok_xlsx = tmp_path / "ok.xlsx"
    ok_xlsx.write_bytes(_valid_query_xlsx())
    with pytest.raises(excel_boundary.ParseTimeoutError):
        excel_boundary.load_validated_excel_sheets(str(ok_xlsx), frozenset({".xlsx"}), budget=zero_time_budget)


def re_sub_dim(xml_bytes: bytes) -> bytes:
    import re

    text = xml_bytes.decode("utf-8")
    return re.sub(r'<dimension ref="[^"]+"/>', '<dimension ref="A1:A1"/>', text).encode("utf-8")


def test_f24_db_upload_lock_concurrency_all_5_failure_modes_and_recovery(isolated_env, monkeypatch):
    client: TestClient = isolated_env["client"]
    headers = isolated_env["director"]["headers"]
    upload_dir: Path = isolated_env["upload_dir"]

    monkeypatch.setattr(server.index_manager, "build_index", lambda *a, **k: None)
    monkeypatch.setattr(server.index_manager, "save_cache", lambda *a, **k: None)

    mat_xlsx = _make_xlsx_bytes({"物料": _valid_materials_rows()})

    # Concurrency 409 check while lock is held
    held_token = server._db_import_lock_manager.try_acquire()
    assert held_token is not None
    r_409 = client.post(
        "/api/db/upload",
        headers=headers,
        files={"file": ("m.xlsx", mat_xlsx, "application/octet-stream")},
    )
    assert r_409.status_code == 409
    assert server._db_import_lock_manager.release_for_token(held_token) is True
    assert server._db_import_lock_manager.release_for_token(held_token) is False  # single-release guarantee

    # Failure Mode 1: streaming save failure
    async def _fail_save(*a, **k):
        raise RuntimeError("disk_write_boom")

    with monkeypatch.context() as mp:
        mp.setattr(server, "stream_save_upload_file", _fail_save)
        r_f1 = client.post(
            "/api/db/upload",
            headers=headers,
            files={"file": ("m.xlsx", mat_xlsx, "application/octet-stream")},
        )
        assert r_f1.status_code == 500
    assert not server._db_import_lock.locked()
    assert os.listdir(upload_dir) == []

    # Failure Mode 2: sync precheck failure (corrupt xlsx)
    r_f2 = client.post(
        "/api/db/upload",
        headers=headers,
        files={"file": ("corrupt.xlsx", b"PK\x03\x04bad", "application/octet-stream")},
    )
    assert r_f2.status_code == 400
    assert not server._db_import_lock.locked()
    assert os.listdir(upload_dir) == []

    # Failure Mode 3: BackgroundTasks.add_task exception marks task failed & releases lock once
    from fastapi import BackgroundTasks

    with monkeypatch.context() as mp:
        mp.setattr(BackgroundTasks, "add_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sched_fail")))
        r_f3 = client.post(
            "/api/db/upload",
            headers=headers,
            files={"file": ("m.xlsx", mat_xlsx, "application/octet-stream")},
        )
        assert r_f3.status_code == 500
    assert not server._db_import_lock.locked()
    assert os.listdir(upload_dir) == []

    # Failure Mode 4: import_materials_from_excel / build_index exception -> queryable failed + unlocked
    with monkeypatch.context() as mp:
        mp.setattr(
            db_manager,
            "import_materials_from_excel",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("import_exploded")),
        )
        r_f4 = client.post(
            "/api/db/upload",
            headers=headers,
            files={"file": ("m.xlsx", mat_xlsx, "application/octet-stream")},
        )
        assert r_f4.status_code == 200
        st4 = client.get(f"/api/upload/status?task_id={r_f4.json()['task_id']}", headers=headers).json()
        assert st4["status"] == "failed" and "import_exploded" in st4["error"]
    assert not server._db_import_lock.locked()
    assert os.listdir(upload_dir) == []

    # Failure Mode 5: timeout triggers cancel_event, sets status=failed, unlocks, prevents late overwrite
    late_write_attempted: List[bool] = []
    started = threading.Event()

    def _slow_import(*a, cancel_event=None, **k):
        started.set()
        time.sleep(0.35)
        if cancel_event is not None and cancel_event.is_set():
            raise excel_boundary.ParseCancelledError("cancelled")
        late_write_attempted.append(True)
        return {}

    with monkeypatch.context() as mp:
        mp.setattr(server, "_DB_IMPORT_TASK_TIMEOUT_SECONDS", 0.08)
        mp.setattr(db_manager, "import_materials_from_excel", _slow_import)
        r_f5 = client.post(
            "/api/db/upload",
            headers=headers,
            files={"file": ("m.xlsx", mat_xlsx, "application/octet-stream")},
        )
        assert r_f5.status_code == 200
        assert started.is_set()
        st5 = client.get(f"/api/upload/status?task_id={r_f5.json()['task_id']}", headers=headers).json()
        assert st5["status"] == "failed" and "超时" in st5["error"]
        # A-M5-02 (R02 ruling-authorized correction of the old unsafe expectation): the
        # timed-out worker thread is still alive here, so the write mutex must stay owned
        # instead of being released before the worker actually exits or isolates itself.
        assert server._db_import_lock.locked()
        time.sleep(0.4)
        # Slow thread exited cleanly without late overwrite and released the owner token
        # exactly once; task status remains failed.
        assert not server._db_import_lock.locked()
        assert late_write_attempted == []
        assert client.get(f"/api/upload/status?task_id={r_f5.json()['task_id']}", headers=headers).json()["status"] == "failed"

    # Next normal import recovers immediately and succeeds!
    r_ok = client.post(
        "/api/db/upload",
        headers=headers,
        files={"file": ("recover.xlsx", mat_xlsx, "application/octet-stream")},
    )
    assert r_ok.status_code == 200
    st_ok = client.get(f"/api/upload/status?task_id={r_ok.json()['task_id']}", headers=headers).json()
    assert st_ok["status"] == "completed"
    assert not server._db_import_lock.locked()
    assert db_manager.query_by_code("M5-MAT-001") is not None


# ---------------------------------------------------------------------------
# DEP-M5-01: psutil Production Dependency & Admin Health Contract
# ---------------------------------------------------------------------------


def test_dep_m5_01_psutil_and_admin_health_metrics(isolated_env, monkeypatch):
    import psutil

    assert psutil is not None
    client: TestClient = isolated_env["client"]
    adm_headers = isolated_env["admin"]["headers"]

    m = audit_manager.get_server_hardware_metrics()
    assert m["metrics_available"] is True
    assert m["metrics_source"] == "psutil"
    assert m["memory"]["total_gb"] > 0
    assert m["disk"]["total_gb"] > 0
    assert m["process"]["memory_mb"] > 0

    r = client.get("/api/admin/health", headers=adm_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "healthy"
    assert body["metrics_available"] is True
    assert body["metrics_error"] == ""
    assert body["hardware"]["memory"]["total_gb"] > 0

    # Explicit degraded signal when psutil unavailable & Windows fallback lacks memory/process stats
    monkeypatch.setattr(audit_manager, "psutil", None)
    if os.name == "nt":
        r_deg = client.get("/api/admin/health", headers=adm_headers)
        assert r_deg.status_code == 200
        b_deg = r_deg.json()
        assert b_deg["status"] == "degraded"
        assert b_deg["metrics_available"] is False
        assert b_deg["metrics_error"] != ""


# ---------------------------------------------------------------------------
# COMPAT-M5-01: Warnings Cleanup Under -W error + Data Parity
# ---------------------------------------------------------------------------


def test_compat_m5_01_zero_target_warnings(isolated_env, tmp_path: Path):
    client: TestClient = isolated_env["client"]
    headers = isolated_env["emp"]["headers"]
    from pydantic.warnings import PydanticDeprecatedSince20

    pandas4_warn = getattr(pd.errors, "Pandas4Warning", FutureWarning)

    # Seed 1 material for export check
    xlsx_path = tmp_path / "compat_mat.xlsx"
    xlsx_path.write_bytes(
        _make_xlsx_bytes(
            {
                "Sheet1": [
                    ["标准库"],
                    ["物料分类", "物料编码", "物料名称", "规格/型号", "计量单位", "常用品牌", "描述"],
                    ["阀门", "0010", "止回阀", "DN100", "台", None, None],
                ]
            }
        )
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        warnings.simplefilter("error", DeprecationWarning)
        warnings.simplefilter("error", PydanticDeprecatedSince20)
        warnings.simplefilter("error", pandas4_warn)

        meta = db_manager.import_materials_from_excel(str(xlsx_path), mode="replace", filename="compat_mat.xlsx")
        assert meta["imported_rows"] == 1
        mat = db_manager.query_by_code("0010")
        assert mat is not None and mat["brand"] == ""

        # Vector embedding dimension warning-free call if model loaded
        if server.index_manager.model is not None:
            server.index_manager._initialize_model()

        # Pydantic v2 .model_dump() endpoints (V016-M0: feedback/submit requires verified upload row identity)
        query_bytes = _make_xlsx_bytes(
            {
                "暖通": [
                    ["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"],
                    ["030101", "止回阀", "DN100", "台", 2],
                ]
            }
        )
        r_up = client.post(
            "/api/upload",
            headers=headers,
            files={"file": ("compat.xlsx", query_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert r_up.status_code == 200, r_up.text
        st_up = client.get(f"/api/upload/status?task_id={r_up.json()['task_id']}", headers=headers).json()
        assert st_up["status"] == "completed", st_up
        row_ctx = st_up["result"][0]
        r_fb = client.post(
            "/api/feedback/submit",
            headers=headers,
            json={
                "client_event_id": "compat_fb_evt_1",
                "document_id": row_ctx["document_id"],
                "sheet_id": row_ctx["sheet_id"],
                "source_row_id": row_ctx["source_row_id"],
                "event_seq": 1,
                "row_id": 1,
                "raw_text": "止回阀 DN100",
                "auto_code": "0010",
                "manual_code": "0010",
                "is_correct": True,
            },
        )
        assert r_fb.status_code == 200

        # Confirm item cleanly and export
        r_confirm = client.post(
            "/api/binding/submit",
            headers=headers,
            json={
                "original_name": "止回阀",
                "original_spec": "DN100",
                "original_qty": 2,
                "original_unit": "台",
                "original_loss": 1.0,
                "original_row_idx": 1,
                "scope_filename": "compat.xlsx",
                "bindings": [
                    {
                        "code": "0010",
                        "name": "止回阀",
                        "specification": "DN100",
                        "unit": "台",
                        "brand": "",
                        "category": "阀门",
                        "qty": 2,
                    }
                ],
            },
        )
        assert r_confirm.status_code == 200, r_confirm.text

        r_exp = client.post(
            "/api/export",
            headers=headers,
            json={
                "scope_filename": "compat.xlsx",
                "items": [
                    {
                        "original_row_idx": 1,
                        "original_name": "止回阀",
                        "original_spec": "DN100",
                        "original_qty": 2,
                        "original_unit": "台",
                        "original_loss": 1.0,
                        "matched_code": "0010",
                        "matched_name": "止回阀",
                        "matched_category": "阀门",
                        "matched_spec": "DN100",
                        "matched_unit": "台",
                        "matched_brand": "",
                        "bindings": [
                            {
                                "code": "0010",
                                "name": "止回阀",
                                "specification": "DN100",
                                "unit": "台",
                                "brand": "",
                                "category": "阀门",
                                "qty": 2,
                            }
                        ],
                        "decision_status": "manual_confirmed",
                        "decision_source": "manual",
                        "scope_filename": "compat.xlsx",
                    }
                ],
            },
        )
        assert r_exp.status_code == 200, r_exp.text


# ---------------------------------------------------------------------------
# R-M3-01: Explosion-Proof Recommended-Only & Export Bypass Protection
# ---------------------------------------------------------------------------


def test_r_m3_01_explosion_proof_recommended_and_fail_closed_export(isolated_env, tmp_path: Path):
    client: TestClient = isolated_env["client"]
    headers = isolated_env["emp"]["headers"]

    # Seed realistic explosion-proof pair with different防爆等级 (ExdIIBT4 vs ExdIICT4)
    mat_path = tmp_path / "exp_proof_mats.xlsx"
    mat_path.write_bytes(
        _make_xlsx_bytes(
            {
                "物料": [
                    ["标准库"],
                    ["物料分类", "物料编码", "物料名称", "规格/型号", "计量单位", "常用品牌", "描述"],
                    ["防爆电气", "3.50.29.0199", "防爆检修电源箱", "ExdIIBT4 380V", "台", "华荣", "防爆箱"],
                    ["防爆电气", "3.50.29.0200", "防爆检修电源箱", "ExdIICT4 380V", "台", "华荣", "防爆箱"],
                ]
            }
        )
    )
    db_manager.import_materials_from_excel(str(mat_path), mode="replace", filename="exp_proof_mats.xlsx")
    server.index_manager.is_enabled  # noop attribute access without forcing heavy rebuild if absent

    # Match query lacking 防爆等级 -> must NOT auto_select
    res = server.batch_match_rows(
        [
            {
                "row_idx": 1,
                "code": "",
                "name": "防爆检修电源箱",
                "specification": "380V 防爆",
                "unit": "台",
                "qty": 1,
                "loss": 1.0,
                "category": "",
                "brand": "",
                "professional": "强电",
            }
        ]
    )[0]
    assert res["decision_status"] == "recommended"
    assert res["decision_status"] != "auto_selected"

    # Attempt export with forged auto_selected status -> fail-closed 422
    exp_binding = [
        {
            "code": "3.50.29.0199",
            "name": "防爆检修电源箱",
            "specification": "ExdIIBT4 380V",
            "unit": "台",
            "brand": "华荣",
            "category": "防爆电气",
            "qty": 1,
        }
    ]
    item_payload = {
        "original_row_idx": 1,
        "original_name": "防爆检修电源箱",
        "original_spec": "380V 防爆",
        "original_qty": 1,
        "original_unit": "台",
        "original_loss": 1.0,
        "professional": "强电",
        "matched_code": "3.50.29.0199",
        "matched_name": "防爆检修电源箱",
        "matched_category": "防爆电气",
        "matched_spec": "ExdIIBT4 380V",
        "matched_unit": "台",
        "matched_brand": "华荣",
        "bindings": exp_binding,
        "decision_status": "auto_selected",
        "decision_source": "rule_bypass",
        "scope_filename": "exp.xlsx",
    }
    r_422_forged = client.post(
        "/api/export",
        headers=headers,
        json={"scope_filename": "exp.xlsx", "items": [item_payload]},
    )
    assert r_422_forged.status_code == 422

    # Legacy draft save & restore annotate downgrades is_exact/auto_selected to recommended and blocks export 422
    legacy_row = {**item_payload, "is_exact": True, "bindings": []}
    r_save = client.post(
        "/api/session/draft",
        headers=headers,
        json={
            "original_filename": "exp.xlsx",
            "professional": "强电",
            "items_json": json.dumps([legacy_row], ensure_ascii=False),
        },
    )
    assert r_save.status_code == 200
    r_load = client.get("/api/session/draft", headers=headers)
    assert r_load.status_code == 200 and r_load.json()["draft"] is not None
    r_ann = client.post(
        "/api/session/annotate",
        headers=headers,
        json={"scope_filename": "exp.xlsx", "items": json.loads(r_load.json()["draft"]["items_json"])},
    )
    assert r_ann.status_code == 200
    ann_body = r_ann.json()
    assert ann_body.get("downgraded", 0) >= 1
    assert ann_body["items"][0]["decision_status"] == "recommended"

    # Genuine manual confirmation persists authoritative binding and unlocks 200 export
    r_bind = client.post(
        "/api/binding/submit",
        headers=headers,
        json={
            "original_name": "防爆检修电源箱",
            "original_spec": "380V 防爆",
            "original_qty": 1,
            "original_unit": "台",
            "original_loss": 1.0,
            "original_row_idx": 1,
            "professional": "强电",
            "scope_filename": "exp.xlsx",
            "bindings": exp_binding,
        },
    )
    assert r_bind.status_code == 200

    r_ok = client.post(
        "/api/export",
        headers=headers,
        json={
            "scope_filename": "exp.xlsx",
            "items": [
                {
                    **item_payload,
                    "decision_status": "manual_confirmed",
                    "decision_source": "manual",
                }
            ],
        },
    )
    assert r_ok.status_code == 200


# ---------------------------------------------------------------------------
# V015-M5 R02 / A-M5-03: unified file-size budget on the shared reader,
# cancellation/deadline coverage in ZIP extraction and workbook open.
# ---------------------------------------------------------------------------


def test_r02_am5_03_locked_default_budget_constants():
    """R02 must not silently lower any R01 locked budget constant."""
    assert excel_boundary.DEFAULT_MAX_UPLOAD_BYTES == 50 * 1024 * 1024
    assert excel_boundary.DEFAULT_STREAM_CHUNK_BYTES == 1024 * 1024
    assert excel_boundary.DEFAULT_MAX_ZIP_ENTRIES == 4096
    assert excel_boundary.DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES == 200 * 1024 * 1024
    assert excel_boundary.DEFAULT_MAX_SHEETS == 32
    assert excel_boundary.DEFAULT_MAX_ROWS_PER_SHEET == 100_000
    assert excel_boundary.DEFAULT_MAX_COLS_PER_SHEET == 256
    assert excel_boundary.DEFAULT_MAX_TOTAL_CELLS == 5_000_000
    assert excel_boundary.DEFAULT_PARSE_TIMEOUT_SECONDS == 60.0


def test_r02_am5_03_shared_entry_rejects_oversized_xlsx_and_xls(tmp_path: Path):
    """1-byte budget must reject real workbooks through the shared reader (audit reverse case)."""
    rows = [["物料编码", "物料名称"], ["R02-X1", "测试阀门"]]
    xlsx_path = tmp_path / "oversize.xlsx"
    xlsx_path.write_bytes(_make_xlsx_bytes({"S1": rows}))
    xls_path = tmp_path / "oversize.xls"
    xls_path.write_bytes(_make_biff8_xls_bytes(rows))
    assert xlsx_path.stat().st_size > 1 and xls_path.stat().st_size > 1

    tight_budget = excel_boundary.ExcelParseBudget(max_upload_bytes=1)
    for path, ext in ((xlsx_path, ".xlsx"), (xls_path, ".xls")):
        with pytest.raises(excel_boundary.UploadTooLargeError) as exc:
            excel_boundary.load_validated_excel_sheets(
                str(path),
                allowed_exts=frozenset({".xlsx", ".xls"}),
                budget=tight_budget,
                context_label="R02 大小预算",
            )
        assert exc.value.status_code == 413

    # Same real files parse successfully under the default budget: no regression of the 50 MiB gate.
    xlsx_sheets = excel_boundary.load_validated_excel_sheets(str(xlsx_path), allowed_exts=frozenset({".xlsx", ".xls"}))
    assert len(xlsx_sheets[0][1]) == 2
    xls_sheets = excel_boundary.load_validated_excel_sheets(str(xls_path), allowed_exts=frozenset({".xlsx", ".xls"}))
    assert len(xls_sheets[0][1]) == 2


def test_r02_am5_03_personnel_path_denies_oversized_via_default_budget(isolated_env, tmp_path: Path):
    """Personnel import calls the shared reader directly; the default-budget override must close that bypass."""
    rows = [
        ["姓名", "1级部门", "工号", "职位", "手机"],
        ["赵超限", "工程中心", "EMP-R02-1", "工程师", "13900002233"],
    ]
    xlsx_path = tmp_path / "users_big.xlsx"
    xlsx_path.write_bytes(_make_xlsx_bytes({"人员": rows}))
    xls_path = tmp_path / "users_big.xls"
    xls_path.write_bytes(_make_biff8_xls_bytes(rows))

    tight = excel_boundary.ExcelParseBudget(max_upload_bytes=1)
    # Dedicated MonkeyPatch so undo() reverts only the budget override, not the isolated_env patches.
    budget_patch = pytest.MonkeyPatch()
    budget_patch.setattr(excel_boundary, "get_default_budget", lambda: tight)

    res_xlsx = auth_manager.import_users_from_excel(str(xlsx_path))
    assert res_xlsx["success"] is False
    res_xls = auth_manager.import_users_from_excel(str(xls_path))
    assert res_xls["success"] is False
    # Rejected before any account write.
    assert auth_manager.get_user_by_username("13900002233") is None

    budget_patch.undo()
    res_ok = auth_manager.import_users_from_excel(str(xlsx_path))
    assert res_ok["success"] is True and res_ok["imported_count"] == 1


def test_r02_am5_03_pre_cancelled_shared_entry_xlsx_and_xls(tmp_path: Path):
    rows = [["物料编码", "物料名称"], ["R02-C1", "测试管道"]]
    xlsx_path = tmp_path / "pre_cancel.xlsx"
    xlsx_path.write_bytes(_make_xlsx_bytes({"S1": rows}))
    xls_path = tmp_path / "pre_cancel.xls"
    xls_path.write_bytes(_make_biff8_xls_bytes(rows))

    cancel = threading.Event()
    cancel.set()
    for path in (xlsx_path, xls_path):
        with pytest.raises(excel_boundary.ParseCancelledError):
            excel_boundary.load_validated_excel_sheets(
                str(path),
                allowed_exts=frozenset({".xlsx", ".xls"}),
                cancel_event=cancel,
            )


def test_r02_am5_03_zip_extraction_loop_observes_deadline_and_cancel(tmp_path: Path, monkeypatch):
    xlsx_path = tmp_path / "loop.xlsx"
    xlsx_path.write_bytes(
        _make_xlsx_bytes({"S1": [["物料编码", "物料名称"], ["R02-Z1", "测试风机"], ["R02-Z2", "测试水泵"]]})
    )

    # Deadline: negative monotonic budget must trip inside the streaming ZIP extraction loop.
    deadline_budget = excel_boundary.ExcelParseBudget(parse_timeout_seconds=-0.01)
    with pytest.raises(excel_boundary.ParseTimeoutError):
        excel_boundary.validate_excel_container(
            str(xlsx_path), frozenset({".xlsx"}), budget=deadline_budget
        )

    # Cancellation armed on the first real decompression read must abort the extraction loop.
    cancel = threading.Event()
    real_zef_read = zipfile.ZipExtFile.read

    def _arm_on_read(self, n=-1):
        cancel.set()
        return real_zef_read(self, n)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", _arm_on_read)
    with pytest.raises(excel_boundary.ParseCancelledError):
        excel_boundary.validate_excel_container(
            str(xlsx_path),
            frozenset({".xlsx"}),
            budget=excel_boundary.get_default_budget(),
            cancel_event=cancel,
        )


def test_r02_am5_03_workbook_open_rechecks_cancel_and_deadline(tmp_path: Path, monkeypatch):
    rows = [["物料编码", "物料名称"], ["R02-W1", "测试桥架"]]
    xlsx_path = tmp_path / "recheck.xlsx"
    xlsx_path.write_bytes(_make_xlsx_bytes({"S1": rows}))
    xls_path = tmp_path / "recheck.xls"
    xls_path.write_bytes(_make_biff8_xls_bytes(rows))

    # xlsx: cancel armed while load_workbook returns; post-open recheck must refuse to publish rows.
    cancel_x = threading.Event()
    real_load_workbook = openpyxl.load_workbook

    def _arm_xlsx(*a, **k):
        wb = real_load_workbook(*a, **k)
        cancel_x.set()
        return wb

    monkeypatch.setattr(openpyxl, "load_workbook", _arm_xlsx)
    with pytest.raises(excel_boundary.ParseCancelledError):
        excel_boundary.load_validated_excel_sheets(
            str(xlsx_path), allowed_exts=frozenset({".xlsx"}), cancel_event=cancel_x
        )
    monkeypatch.undo()

    # xls: cancel armed while xlrd.open_workbook returns; post-open recheck must refuse as well.
    import xlrd

    cancel_l = threading.Event()
    real_xlrd_open = xlrd.open_workbook

    def _arm_xls(*a, **k):
        book = real_xlrd_open(*a, **k)
        cancel_l.set()
        return book

    monkeypatch.setattr(xlrd, "open_workbook", _arm_xls)
    with pytest.raises(excel_boundary.ParseCancelledError):
        excel_boundary.load_validated_excel_sheets(
            str(xls_path), allowed_exts=frozenset({".xlsx", ".xls"}), cancel_event=cancel_l
        )


# ---------------------------------------------------------------------------
# R02 A-M5-01: replace import staging + atomic swap; failure keeps the full
# pre-state (materials / FTS / metadata / in-memory vector index / disk cache),
# success publishes one unified new state. All data is synthetic and isolated.
# ---------------------------------------------------------------------------


def _r02_seed_old_material():
    """Seed one synthetic OLD row into materials, materials_fts and metadata."""
    conn = db_manager.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM materials")
        cur.execute("DELETE FROM materials_fts")
        cur.execute(
            "INSERT INTO materials(code, category, name, specification, unit, brand, description, "
            "search_tokens, parsed_params, parsed_schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 2)",
            ("R02-OLD-1", "阀门", "旧阀门", "DN15", "台", "旧牌", "前态物料", "old valve", "{}"),
        )
        cur.execute(
            "INSERT INTO materials_fts(code, category, name, specification, brand, description) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("R02-OLD-1", "阀门", "旧阀门", "DN15", "旧牌", "前态物料"),
        )
        conn.commit()
    finally:
        conn.close()
    db_manager.update_db_metadata(1, 1, 0, "r02_old_seed.xlsx")


def _r02_new_materials_xlsx() -> bytes:
    return _make_xlsx_bytes(
        {
            "物料表": [
                ["标准物料库导入模板"],
                ["物料分类", "物料编码", "物料名称", "规格/型号", "计量单位", "常用品牌", "描述"],
                ["风管", "R02-NEW-1", "新风管", "1000x500", "m2", "新牌", "后态物料甲"],
                ["阀门", "R02-NEW-2", "新调节阀", "DN100 PN16", "台", None, None],
            ]
        }
    )


def _r02_material_codes() -> List[str]:
    conn = db_manager.get_db_connection()
    try:
        return [r[0] for r in conn.execute("SELECT code FROM materials ORDER BY code")]
    finally:
        conn.close()


def _r02_fts_codes() -> List[str]:
    conn = db_manager.get_db_connection()
    try:
        return [r[0] for r in conn.execute("SELECT code FROM materials_fts ORDER BY code")]
    finally:
        conn.close()


def _r02_stage_residue() -> List[str]:
    conn = db_manager.get_db_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%r02_stage%' OR name LIKE '%_stage%'"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _r02_poll_final_status(client: TestClient, task_id: str, headers: Dict[str, str]) -> Dict[str, Any]:
    last: Dict[str, Any] = {}
    for _ in range(200):
        last = client.get(f"/api/upload/status?task_id={task_id}", headers=headers).json()
        if last.get("status") in ("completed", "failed"):
            return last
        time.sleep(0.05)
    return last


def test_r02_am5_01_cancel_during_fts_keeps_old_db_fts_metadata(isolated_env, tmp_path: Path, monkeypatch):
    from app import preprocessor

    _r02_seed_old_material()
    xlsx_path = tmp_path / "cancel_fts.xlsx"
    xlsx_path.write_bytes(_r02_new_materials_xlsx())

    real_tokenize = preprocessor.fts_tokenize
    cancel = threading.Event()

    def _arm_cancel(text):
        cancel.set()
        return real_tokenize(text)

    monkeypatch.setattr(preprocessor, "fts_tokenize", _arm_cancel)
    with pytest.raises(excel_boundary.ParseCancelledError):
        db_manager.import_materials_from_excel(
            str(xlsx_path), mode="replace", filename=xlsx_path.name, cancel_event=cancel
        )

    # Pre-state fully verifiable: old table/FTS row survives, new codes never published.
    assert _r02_material_codes() == ["R02-OLD-1"]
    assert _r02_fts_codes() == ["R02-OLD-1"]
    meta = db_manager.get_db_metadata()
    assert meta["filename"] == "r02_old_seed.xlsx" and meta["total_rows"] == 1
    assert _r02_stage_residue() == []


def test_r02_am5_01_swap_failure_rolls_back_and_cleans_stage(isolated_env, tmp_path: Path, monkeypatch):
    _r02_seed_old_material()
    xlsx_path = tmp_path / "swap_boom.xlsx"
    xlsx_path.write_bytes(_r02_new_materials_xlsx())

    real_connect = db_manager.sqlite3.connect

    class _BoomCursor:
        def __init__(self, real_cursor):
            self._real = real_cursor

        def __getattr__(self, item):
            return getattr(self._real, item)

        def execute(self, sql, params=()):
            if "RENAME TO materials" in sql:
                raise RuntimeError("SYNTHETIC_SWAP_BOOM")
            return self._real.execute(sql, params)

    class _ExclusiveConnProxy:
        # sqlite3.Connection is a C type whose instance attributes are read-only, so
        # cursor interception is injected through a proxy instead of assigning .cursor.
        def __init__(self, real_conn):
            self._real = real_conn
            self._real_cursor = real_conn.cursor

        def cursor(self, *a, **k):
            return _BoomCursor(self._real_cursor(*a, **k))

        def __getattr__(self, item):
            return getattr(self._real, item)

    def _connect_wrapper(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        if kwargs.get("isolation_level") == "EXCLUSIVE":
            return _ExclusiveConnProxy(conn)
        return conn

    monkeypatch.setattr(db_manager.sqlite3, "connect", _connect_wrapper)
    with pytest.raises(RuntimeError, match="SYNTHETIC_SWAP_BOOM"):
        db_manager.import_materials_from_excel(str(xlsx_path), mode="replace", filename=xlsx_path.name)

    # The exclusive transaction rolled back: destructive DDL never became visible.
    assert _r02_material_codes() == ["R02-OLD-1"]
    assert _r02_fts_codes() == ["R02-OLD-1"]
    assert db_manager.get_db_metadata()["filename"] == "r02_old_seed.xlsx"
    assert _r02_stage_residue() == []


def test_r02_am5_01_replace_success_unifies_tables_fts_metadata(isolated_env, tmp_path: Path):
    _r02_seed_old_material()
    xlsx_path = tmp_path / "replace_ok.xlsx"
    xlsx_path.write_bytes(_r02_new_materials_xlsx())

    meta = db_manager.import_materials_from_excel(str(xlsx_path), mode="replace", filename=xlsx_path.name)

    assert meta["filename"] == xlsx_path.name and meta["total_rows"] == 2 and meta["imported_rows"] == 2
    assert _r02_material_codes() == ["R02-NEW-1", "R02-NEW-2"]
    assert _r02_fts_codes() == ["R02-NEW-1", "R02-NEW-2"]
    assert _r02_stage_residue() == []
    # Indexes keep their production names after the staged rename.
    conn = db_manager.get_db_connection()
    try:
        idx_names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='materials'"
            )
        }
    finally:
        conn.close()
    assert {"idx_materials_code", "idx_materials_category"}.issubset(idx_names)


def test_r02_am5_01_append_failure_rolls_back_without_losing_rows(isolated_env, tmp_path: Path, monkeypatch):
    from app import preprocessor

    _r02_seed_old_material()
    xlsx_path = tmp_path / "append_boom.xlsx"
    xlsx_path.write_bytes(_r02_new_materials_xlsx())

    def _boom_tokenize(text):
        raise RuntimeError("SYNTHETIC_FTS_BOOM")

    monkeypatch.setattr(preprocessor, "fts_tokenize", _boom_tokenize)
    with pytest.raises(RuntimeError, match="SYNTHETIC_FTS_BOOM"):
        db_manager.import_materials_from_excel(str(xlsx_path), mode="append", filename=xlsx_path.name)

    assert _r02_material_codes() == ["R02-OLD-1"]
    assert _r02_fts_codes() == ["R02-OLD-1"]


def _r02_seed_vector_pre_state(isolated_env, monkeypatch):
    """Seed in-memory index and disk cache with the OLD synthetic material."""
    import numpy as np

    old_mapping = [{"code": "R02-OLD-1", "name": "旧阀门", "specification": "DN15", "category": "阀门"}]
    from app.vector_index import IndexCandidate
    manager = server.index_manager
    monkeypatch.setattr(manager, "_snapshot", IndexCandidate(
        old_mapping, np.zeros((1, 512), dtype=np.float32), None, False,
        manager.model_name_or_path, 512, db_manager.get_db_revision(),
    ))
    vec_dir = isolated_env["root"] / "vector_cache"
    mapping_file = vec_dir / "index_to_material.json"
    mapping_file.write_text(json.dumps(old_mapping, ensure_ascii=False), encoding="utf-8")
    (vec_dir / "cache_metadata.json").write_text(
        json.dumps({"model_name": "r02-old", "dimension": 512, "item_count": 1}), encoding="utf-8"
    )
    return mapping_file


def test_r02_am5_01_server_index_failure_keeps_full_pre_state(isolated_env, monkeypatch):
    import hashlib

    client: TestClient = isolated_env["client"]
    headers = isolated_env["director"]["headers"]
    upload_dir: Path = isolated_env["upload_dir"]
    _r02_seed_old_material()
    mapping_file = _r02_seed_vector_pre_state(isolated_env, monkeypatch)
    cache_hash_before = hashlib.sha256(mapping_file.read_bytes()).hexdigest()

    def _boom_candidate(*a, **k):
        raise RuntimeError("SYNTHETIC_INDEX_FAILURE")

    monkeypatch.setattr(server.index_manager, "build_index_candidate", _boom_candidate)

    resp = client.post(
        "/api/db/upload",
        headers=headers,
        files={"file": ("idx_fail.xlsx", _r02_new_materials_xlsx(), "application/octet-stream")},
    )
    assert resp.status_code == 200
    state = _r02_poll_final_status(client, resp.json()["task_id"], headers)
    assert state["status"] == "failed" and "SYNTHETIC_INDEX_FAILURE" in state["error"]

    # Every layer stays at its verified pre-state.
    assert _r02_material_codes() == ["R02-OLD-1"]
    assert _r02_fts_codes() == ["R02-OLD-1"]
    assert db_manager.get_db_metadata()["filename"] == "r02_old_seed.xlsx"
    assert server.index_manager.index_to_material[0]["code"] == "R02-OLD-1"
    assert hashlib.sha256(mapping_file.read_bytes()).hexdigest() == cache_hash_before
    assert _r02_stage_residue() == []
    assert not list((isolated_env["root"] / "vector_cache").glob(".stage_*"))
    assert os.listdir(upload_dir) == []


def test_r02_am5_01_server_cache_stage_failure_keeps_full_pre_state(isolated_env, monkeypatch):
    import hashlib

    client: TestClient = isolated_env["client"]
    headers = isolated_env["director"]["headers"]
    upload_dir: Path = isolated_env["upload_dir"]
    _r02_seed_old_material()
    mapping_file = _r02_seed_vector_pre_state(isolated_env, monkeypatch)
    cache_hash_before = hashlib.sha256(mapping_file.read_bytes()).hexdigest()

    def _boom_cache_write(*a, **k):
        raise RuntimeError("SYNTHETIC_CACHE_STAGE_FAILURE")

    monkeypatch.setattr(server.index_manager, "write_candidate_cache_to_dir", _boom_cache_write)

    resp = client.post(
        "/api/db/upload",
        headers=headers,
        files={"file": ("cache_fail.xlsx", _r02_new_materials_xlsx(), "application/octet-stream")},
    )
    assert resp.status_code == 200
    state = _r02_poll_final_status(client, resp.json()["task_id"], headers)
    assert state["status"] == "failed" and "SYNTHETIC_CACHE_STAGE_FAILURE" in state["error"]

    assert _r02_material_codes() == ["R02-OLD-1"]
    assert _r02_fts_codes() == ["R02-OLD-1"]
    assert db_manager.get_db_metadata()["filename"] == "r02_old_seed.xlsx"
    assert server.index_manager.index_to_material[0]["code"] == "R02-OLD-1"
    assert hashlib.sha256(mapping_file.read_bytes()).hexdigest() == cache_hash_before
    assert _r02_stage_residue() == []
    assert not list((isolated_env["root"] / "vector_cache").glob(".stage_*"))
    assert os.listdir(upload_dir) == []


def test_r02_am5_01_server_success_unifies_db_fts_memory_cache(isolated_env, monkeypatch):
    client: TestClient = isolated_env["client"]
    headers = isolated_env["director"]["headers"]
    _r02_seed_old_material()
    _r02_seed_vector_pre_state(isolated_env, monkeypatch)
    if not server.index_manager.is_enabled:
        pytest.skip("Offline embedding model unavailable in this environment")

    resp = client.post(
        "/api/db/upload",
        headers=headers,
        files={"file": ("unified_ok.xlsx", _r02_new_materials_xlsx(), "application/octet-stream")},
    )
    assert resp.status_code == 200
    state = _r02_poll_final_status(client, resp.json()["task_id"], headers)
    assert state["status"] == "completed", state

    new_codes = {"R02-NEW-1", "R02-NEW-2"}
    assert set(_r02_material_codes()) == new_codes
    assert set(_r02_fts_codes()) == new_codes
    assert db_manager.get_db_metadata()["filename"] == "unified_ok.xlsx"
    assert {m["code"] for m in server.index_manager.index_to_material} == new_codes

    vec_dir = isolated_env["root"] / "vector_cache"
    mapping_file = vec_dir / "index_to_material.json"
    npy_file = vec_dir / "materials_embeddings.npy"
    assert mapping_file.exists() and npy_file.exists()
    cached_mapping = json.loads(mapping_file.read_text(encoding="utf-8"))
    assert {m["code"] for m in cached_mapping} == new_codes
    assert _r02_stage_residue() == []
    assert not list(vec_dir.glob(".stage_*"))


def test_r02_am5_01_vector_candidate_publish_contract_and_cancel(isolated_env, monkeypatch):
    import numpy as np

    from app import vector_index

    mgr = vector_index.VectorIndexManager()
    mgr.model = object()
    mgr.embedding_dim = 2
    old_mapping = [
        {"code": "R02-OLD-1", "name": "旧阀门", "specification": "DN15", "category": "阀门"}
    ]
    from app.vector_index import IndexCandidate
    mgr.publish_candidate(IndexCandidate(
        old_mapping, np.array([[0.0, 1.0]], dtype=np.float32), None, False,
        mgr.model_name_or_path, 2,
    ))
    mgr.encode_queries = lambda texts, **kw: np.zeros((len(texts), 2), dtype=np.float32)
    mgr.save_cache = lambda: None

    with monkeypatch.context() as mp:
        mp.setattr(vector_index, "HAS_FAISS", False)
        candidate = mgr.build_index_candidate(
            [{"code": "R02-NEW-1", "name": "新风管", "specification": "1000x500", "category": "风管"}]
        )
        assert candidate is not None
        # Candidate construction never mutates the live index.
        assert mgr.index_to_material[0]["code"] == "R02-OLD-1"

        mgr.publish_candidate(candidate)
        assert mgr.index_to_material[0]["code"] == "R02-NEW-1"
        assert mgr.numpy_embeddings.shape == (1, 2)

        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(excel_boundary.ParseCancelledError):
            mgr.publish_candidate(candidate, cancel_event=cancelled)
        # In-lock cancellation backstop: live index remains the published one.
        assert mgr.index_to_material[0]["code"] == "R02-NEW-1"


# ---------------------------------------------------------------------------
# R02 A-M5-02: deterministic mutex-lifecycle and swap-cancellation arbitration
# ---------------------------------------------------------------------------


def test_r02_am5_02_alive_worker_holds_mutex_second_import_409_then_recovers(isolated_env, monkeypatch):
    # Ruling scenario 1: while the timed-out worker thread is still alive the write
    # mutex stays owned, so a second import is rejected with 409 and cannot release
    # the owner's token. A new import succeeds only after the worker actually exits.
    client: TestClient = isolated_env["client"]
    headers = isolated_env["director"]["headers"]
    upload_dir: Path = isolated_env["upload_dir"]
    _r02_seed_old_material()

    real_import = db_manager.import_materials_from_excel
    started = threading.Event()
    gate = threading.Event()

    def _parking_import(*args, cancel_event=None, **kwargs):
        started.set()
        # Park the worker after mutex acquisition; the supervisor times out while we wait.
        gate.wait(timeout=10.0)
        if cancel_event is not None and cancel_event.is_set():
            raise excel_boundary.ParseCancelledError("parked worker cancelled after supervisor timeout")
        return real_import(*args, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(server, "_DB_IMPORT_TASK_TIMEOUT_SECONDS", 0.1)
        mp.setattr(db_manager, "import_materials_from_excel", _parking_import)

        r1 = client.post(
            "/api/db/upload",
            headers=headers,
            files={"file": ("parked.xlsx", _r02_new_materials_xlsx(), "application/octet-stream")},
        )
        assert r1.status_code == 200
        assert started.is_set()
        task1_id = r1.json()["task_id"]
        st1 = client.get(f"/api/upload/status?task_id={task1_id}", headers=headers).json()
        assert st1["status"] == "failed" and "超时" in st1["error"]

        # The parked worker is still alive: the mutex remains owned by its owner token.
        assert server._db_import_lock.locked()

        # A second import is deterministically rejected (409 before streaming) and the
        # rejected request's None-token cleanup cannot release the owner's mutex.
        r2 = client.post(
            "/api/db/upload",
            headers=headers,
            files={"file": ("rejected.xlsx", _r02_new_materials_xlsx(), "application/octet-stream")},
        )
        assert r2.status_code == 409
        assert server._db_import_lock.locked()

        # Unpark the worker: the supervisor's cancellation makes it abort without
        # publishing anything; its own finally block releases the owner token once.
        gate.set()
        for _ in range(250):
            if not server._db_import_lock.locked():
                break
            time.sleep(0.02)
        assert not server._db_import_lock.locked()

    # The timed-out task never overwrote the verified pre-state and its temp file is gone.
    assert client.get(f"/api/upload/status?task_id={task1_id}", headers=headers).json()["status"] == "failed"
    assert _r02_material_codes() == ["R02-OLD-1"]
    assert _r02_fts_codes() == ["R02-OLD-1"]
    assert db_manager.get_db_metadata()["filename"] == "r02_old_seed.xlsx"
    assert _r02_stage_residue() == []
    assert os.listdir(upload_dir) == []

    # After the worker has exited, a fresh import is accepted and completes end-to-end.
    r3 = client.post(
        "/api/db/upload",
        headers=headers,
        files={"file": ("recover.xlsx", _r02_new_materials_xlsx(), "application/octet-stream")},
    )
    assert r3.status_code == 200
    st3 = _r02_poll_final_status(client, r3.json()["task_id"], headers)
    assert st3["status"] == "completed", st3
    assert set(_r02_material_codes()) == {"R02-NEW-1", "R02-NEW-2"}
    assert not server._db_import_lock.locked()


def test_r02_am5_02_cancel_arbitration_before_and_inside_swap(isolated_env, tmp_path: Path):
    # Ruling scenario 2 (deterministic, no timing): cancellation is honored both at the
    # pre-swap public gate and inside the BEGIN EXCLUSIVE transaction before any
    # destructive DDL. Every abort rolls back / cleans up with the live pre-state intact.
    _r02_seed_old_material()
    xlsx_path = tmp_path / "cancel_swap.xlsx"
    xlsx_path.write_bytes(_r02_new_materials_xlsx())

    # Phase A: pre-swap arbitration in the public commit entry point.
    handle = db_manager.import_materials_from_excel(
        str(xlsx_path), mode="replace", filename=xlsx_path.name, stage_only=True
    )
    assert handle["stage_only"] is True
    assert _r02_stage_residue()  # stage tables exist before arbitration

    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(excel_boundary.ParseCancelledError, match="原子交换前"):
        db_manager.commit_staged_materials_import(handle, cancel_event=cancelled)

    assert _r02_material_codes() == ["R02-OLD-1"]
    assert _r02_fts_codes() == ["R02-OLD-1"]
    assert db_manager.get_db_metadata()["filename"] == "r02_old_seed.xlsx"
    assert _r02_stage_residue() == []

    # Phase B: in-transaction arbitration after BEGIN EXCLUSIVE, before the first DDL.
    handle_b = db_manager.import_materials_from_excel(
        str(xlsx_path), mode="replace", filename=xlsx_path.name, stage_only=True
    )
    assert handle_b["stage_only"] is True
    with pytest.raises(excel_boundary.ParseCancelledError, match="事务内"):
        db_manager._swap_materials_tables_exclusive(handle_b, cancel_event=cancelled)

    assert _r02_material_codes() == ["R02-OLD-1"]
    assert _r02_fts_codes() == ["R02-OLD-1"]
    assert db_manager.get_db_metadata()["filename"] == "r02_old_seed.xlsx"
    assert _r02_stage_residue() == []


def test_r02_am5_02_server_cancel_before_swap_keeps_full_pre_state(isolated_env, monkeypatch):
    # End-to-end counterpart of audit probe 'cancel_before_swap_still_publishes_index':
    # arm the worker's own cancel_event exactly at the swap boundary. The staged import
    # must abort before publication and every layer must remain at its verified pre-state.
    import hashlib

    client: TestClient = isolated_env["client"]
    headers = isolated_env["director"]["headers"]
    upload_dir: Path = isolated_env["upload_dir"]
    _r02_seed_old_material()
    mapping_file = _r02_seed_vector_pre_state(isolated_env, monkeypatch)
    cache_hash_before = hashlib.sha256(mapping_file.read_bytes()).hexdigest()

    real_commit = db_manager.commit_staged_materials_import

    def _arm_cancel_then_commit(handle, cancel_event=None, **kwargs):
        if cancel_event is not None:
            cancel_event.set()
        return real_commit(handle, cancel_event=cancel_event, **kwargs)

    monkeypatch.setattr(db_manager, "commit_staged_materials_import", _arm_cancel_then_commit)

    resp = client.post(
        "/api/db/upload",
        headers=headers,
        files={"file": ("cancel_at_swap.xlsx", _r02_new_materials_xlsx(), "application/octet-stream")},
    )
    assert resp.status_code == 200
    state = _r02_poll_final_status(client, resp.json()["task_id"], headers)
    assert state["status"] == "failed" and "取消" in state["error"]

    assert _r02_material_codes() == ["R02-OLD-1"]
    assert _r02_fts_codes() == ["R02-OLD-1"]
    assert db_manager.get_db_metadata()["filename"] == "r02_old_seed.xlsx"
    assert server.index_manager.index_to_material[0]["code"] == "R02-OLD-1"
    assert hashlib.sha256(mapping_file.read_bytes()).hexdigest() == cache_hash_before
    assert _r02_stage_residue() == []
    assert not list((isolated_env["root"] / "vector_cache").glob(".stage_*"))
    assert os.listdir(upload_dir) == []
