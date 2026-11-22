# -*- coding: utf-8 -*-
"""
V0.1.5 M5 UI-M5-01 Real Browser (Chrome CDP) End-to-End Verification Script.
Tests the complete user journey against an isolated FastAPI + uvicorn server:
1. Real login & workbench initial render + upload accept attribute contract (.xlsx vs .xlsx,.xls).
2. Client-side .xls upload rejection on #xlsx-file-input (no /api/upload dispatched).
3. Valid .xlsx upload & polling -> 2 matched rows rendered (normal + explosion-proof unknown -> recommended).
4. Blocked export check before manual confirmation (422 fail-closed, 0 files downloaded).
5. Edit/rematch & reject-state transition + re-bind + manual confirmation on row 1 & row 2.
6. Save named session + draft & legacy draft downgrade restore check (/api/session/annotate downgraded >= 1).
7. Restore saved session via History modal -> confirm -> real .xlsx export download -> SHA256 + openpyxl cell verification.
8. Screenshots saved to doc/V0.1.5/evidence/m5_r01_ui_*.png and JSON report written.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVIDENCE_DIR))
sys.path.insert(0, str(ROOT))

from m4_r03_browser_check import (  # noqa: E402
    CHROME_PATH,
    MiniCDPClient,
    _free_port,
    _http_get_json,
    _http_status,
    _wait_for_js,
)


def _write_query_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "强电及暖通"
    ws.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"])
    ws.append(["030701001", "止回阀", "公称直径 DN100 PN16 法兰连接", "台", 2])
    ws.append(["030401002", "防爆检修电源箱", "380V AC220V 防爆", "台", 1])
    wb.save(path)
    wb.close()


def _write_minimal_biff8_xls(path: Path) -> None:
    def biff_rec(rec_id: int, payload: bytes) -> bytes:
        return struct.pack("<HH", rec_id, len(payload)) + payload

    bof_wb = biff_rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0x0DBB, 0x07CC, 0, 0x0600))
    codepage = biff_rec(0x0042, struct.pack("<H", 1200))
    bs_name = struct.pack("<BB", 6, 0) + b"Sheet1"
    bs_stub = biff_rec(0x0085, struct.pack("<IHH", 0, 0, 0) + bs_name)
    eof = biff_rec(0x000A, b"")
    ws_offset = len(bof_wb) + len(codepage) + len(bs_stub) + len(eof)
    wb_globals = bof_wb + codepage + biff_rec(0x0085, struct.pack("<IHH", ws_offset, 0, 0) + bs_name) + eof
    ws_stream = biff_rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0010, 0x0DBB, 0x07CC, 0, 0x0600)) + biff_rec(
        0x0200, struct.pack("<IIHHH", 0, 1, 0, 1, 0)
    ) + eof
    stream = (wb_globals + ws_stream).ljust(4096, b"\x00")
    wb_size = len(stream)
    n_secs = (wb_size + 511) // 512
    padded = stream.ljust(n_secs * 512, b"\x00")

    def dir_entry(name: str, obj_type: int, child: int = -1, start_sec: int = -2, size: int = 0) -> bytes:
        nm = (name + "\x00").encode("utf-16-le") if name else b""
        return struct.pack(
            "<64sHBBiii16sIQQiII",
            nm[:64].ljust(64, b"\x00"),
            len(nm),
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

    dir_sec = n_secs
    fat_sec = n_secs + 1
    dir_bytes = (
        dir_entry("Root Entry", 5, child=1)
        + dir_entry("Workbook", 2, start_sec=0, size=wb_size)
        + dir_entry("", 0, start_sec=0, size=0)
        + dir_entry("", 0, start_sec=0, size=0)
    )
    fat = [-1] * 128
    for s in range(n_secs - 1):
        fat[s] = s + 1
    fat[n_secs - 1] = -2
    fat[dir_sec] = -2
    fat[fat_sec] = -3
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
    ) + struct.pack("<109i", *([fat_sec] + [-1] * 108))
    path.write_bytes(header + padded + dir_bytes + struct.pack("<128i", *fat))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_browser_e2e() -> None:
    if not Path(CHROME_PATH).exists():
        raise FileNotFoundError(f"Chrome not found at {CHROME_PATH}")

    tmp_root = Path(tempfile.mkdtemp(prefix="listtrans_m5_ui_"))
    db_dir = tmp_root / "db"
    cfg_dir = tmp_root / "config"
    vec_dir = tmp_root / "vector_cache"
    up_dir = tmp_root / "uploads"
    out_dir = tmp_root / "output"
    log_dir = tmp_root / "logs"
    dl_dir = tmp_root / "downloads"
    chrome_profile = tmp_root / "chrome_profile"
    for p in (db_dir, vec_dir, up_dir, out_dir, log_dir, dl_dir, chrome_profile):
        p.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "config", cfg_dir)

    mat_xlsx = tmp_root / "seed_materials.xlsx"
    wb_mat = openpyxl.Workbook()
    ws_m = wb_mat.active
    ws_m.append(["标准物料库"])
    ws_m.append(["物料分类", "物料编码", "物料名称", "规格/型号", "计量单位", "常用品牌", "描述"])
    ws_m.append(["阀门", "M5-UI-001", "止回阀", "DN100 PN16 法兰连接", "台", "埃美柯", "止回阀"])
    ws_m.append(["防爆电气", "3.50.29.0199", "防爆检修电源箱", "ExdIIBT4 380V", "台", "华荣", "防爆箱"])
    ws_m.append(["防爆电气", "3.50.29.0200", "防爆检修电源箱", "ExdIICT4 380V", "台", "华荣", "防爆箱"])
    wb_mat.save(mat_xlsx)
    wb_mat.close()

    query_xlsx = tmp_root / "m5_ui_query.xlsx"
    _write_query_xlsx(query_xlsx)
    legacy_xls = tmp_root / "m5_legacy_list.xls"
    _write_minimal_biff8_xls(legacy_xls)

    env = os.environ.copy()
    env["TESTING"] = "1"
    env["LLM_ENABLED"] = "false"
    env["MATERIALS_DB_PATH"] = str(db_dir / "materials.db")
    env["AUTH_DB_PATH"] = str(db_dir / "auth.db")
    env["CONFIG_DIR"] = str(cfg_dir)
    env["VECTOR_CACHE_DIR"] = str(vec_dir)
    env["UPLOAD_DIR"] = str(up_dir)
    env["OUTPUT_DIR"] = str(out_dir)
    env["BINDING_FEEDBACK_FILE"] = str(tmp_root / "binding_feedback.json")
    env["FEEDBACK_SAMPLES_FILE"] = str(tmp_root / "feedback_samples.json")
    env["LOG_DIR"] = str(log_dir)
    env["SECRET_KEY"] = "M5_R01_UI_Browser_E2E_Secret_Key_2026!"
    env["ADMIN_PASSWORD"] = "M5R01UiAdmin#2026!"
    env["PYTHONPATH"] = str(ROOT)

    seed_script = f"""
import json
from app.db_manager import init_db, import_materials_from_excel
from app.audit_manager import init_audit_tables
from app.session_manager import init_session_db
from app.auth_manager import init_auth_db, admin_create_user_atomic, change_user_credentials_atomic

init_db()
import_materials_from_excel({json.dumps(str(mat_xlsx))}, mode='replace', filename='seed_materials.xlsx')
init_auth_db()
init_audit_tables()
init_session_db()
emp = admin_create_user_atomic('m5_ui_emp', role='employee', name='M5测试员', employee_no='M5-UI-01')
assert emp['success'], emp
assert change_user_credentials_atomic(emp['id'], emp['temp_password'], new_password='M5UiEmp#2026!')['success']
print("SEEDED_OK")
"""
    seed_out = subprocess.check_output([sys.executable, "-c", seed_script], cwd=str(ROOT), env=env, text=True, timeout=60)
    assert "SEEDED_OK" in seed_out

    app_port = _free_port()
    cdp_port = _free_port()
    server_proc = None
    chrome_proc = None
    cdp = None

    try:
        server_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", str(app_port), "--workers", "1"],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base_url = f"http://127.0.0.1:{app_port}"
        deadline = time.time() + 45.0
        while time.time() < deadline:
            if _http_status(f"{base_url}/login.html") == 200:
                break
            time.sleep(0.3)
        else:
            raise TimeoutError("Isolated uvicorn server did not become ready")

        chrome_proc = subprocess.Popen(
            [
                CHROME_PATH,
                "--headless=new",
                f"--remote-debugging-port={cdp_port}",
                f"--user-data-dir={chrome_profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
                "--window-size=1440,920",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cdp_http = f"http://127.0.0.1:{cdp_port}"
        deadline = time.time() + 15.0
        ws_url = None
        while time.time() < deadline:
            try:
                targets = _http_get_json(f"{cdp_http}/json/list")
                pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.25)
        if not ws_url:
            raise TimeoutError("Chrome CDP websocket did not appear")

        cdp = MiniCDPClient(ws_url)
        cdp.call("Page.enable")
        cdp.call("DOM.enable")
        cdp.call("Runtime.enable")
        try:
            cdp.call("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(dl_dir.resolve())})
        except Exception:
            cdp.call("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(dl_dir.resolve())})

        report = {"steps": {}}

        # 1. Login as employee
        cdp.call("Page.navigate", {"url": f"{base_url}/login.html"})
        _wait_for_js(cdp, "document.readyState === 'complete' && Boolean(document.querySelector('#password'))")
        cdp.eval_js(
            """
            (() => {
                const u = document.querySelector('#username') || document.querySelector('input[type="text"]');
                const p = document.querySelector('#password') || document.querySelector('input[type="password"]');
                u.value = 'm5_ui_emp';
                u.dispatchEvent(new Event('input', {bubbles: true}));
                p.value = 'M5UiEmp#2026!';
                p.dispatchEvent(new Event('input', {bubbles: true}));
                (document.querySelector('#loginBtn') || document.querySelector('button[type="submit"]')).click();
                return true;
            })()
            """
        )
        _wait_for_js(cdp, "window.location.pathname.endsWith('/index.html') || window.location.pathname === '/'", timeout=15.0)
        _wait_for_js(
            cdp,
            "document.readyState === 'complete' && typeof handleFileUpload === 'function' && Boolean(document.getElementById('xlsx-file-input'))",
            timeout=15.0,
        )

        # Install alert/confirm capture hooks inside the browser page
        cdp.eval_js(
            """
            (() => {
                window.__m5Alerts = [];
                window.__m5Confirms = [];
                window.alert = (msg) => { window.__m5Alerts.push(String(msg)); };
                window.confirm = (msg) => {
                    window.__m5Confirms.push(String(msg));
                    return !String(msg).includes('是否立即结束当前匹配任务');
                };
                return true;
            })()
            """
        )

        # Verify file input accept attributes & guide text
        dom_checks = cdp.eval_js(
            """
            ({
                xlsxAccept: document.getElementById('xlsx-file-input').getAttribute('accept'),
                dbAccept: document.getElementById('db-upload-input').getAttribute('accept'),
                guideHtml: (document.getElementById('guide-dashboard') || document.body).innerHTML.includes('<code>.xlsx</code>')
            })
            """
        )
        assert dom_checks["xlsxAccept"] == ".xlsx", dom_checks
        assert dom_checks["dbAccept"] == ".xlsx,.xls", dom_checks
        assert dom_checks["guideHtml"] is True, dom_checks
        report["steps"]["ui_accept_and_guide_verified"] = dom_checks

        # 2. Client-side .xls upload rejection via real #xlsx-file-input change event
        doc = cdp.call("DOM.getDocument", {"depth": -1})
        node = cdp.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#xlsx-file-input"})
        cdp.call("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(legacy_xls.resolve())]})
        cdp.eval_js("document.getElementById('xlsx-file-input').dispatchEvent(new Event('change', {bubbles: true})); true;")
        _wait_for_js(cdp, "window.__m5Alerts.length > 0", timeout=5.0)
        xls_reject_state = cdp.eval_js(
            "({alerts: window.__m5Alerts.slice(), itemCount: (window.items || []).length, inputVal: document.getElementById('xlsx-file-input').value})"
        )
        assert any(".xlsx" in a for a in xls_reject_state["alerts"]), xls_reject_state
        assert xls_reject_state["itemCount"] == 0, xls_reject_state
        assert xls_reject_state["inputVal"] == "", xls_reject_state
        report["steps"]["client_xls_rejected"] = xls_reject_state

        # 3. Upload valid .xlsx via real #xlsx-file-input -> wait for matched rows
        doc = cdp.call("DOM.getDocument", {"depth": -1})
        node = cdp.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#xlsx-file-input"})
        cdp.call("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(query_xlsx.resolve())]})
        cdp.eval_js("window.__m5Alerts = []; document.getElementById('xlsx-file-input').dispatchEvent(new Event('change', {bubbles: true})); true;")
        try:
            _wait_for_js(cdp, "Array.isArray(items) && items.length === 2 && document.querySelectorAll('#items-tbody tr').length === 2", timeout=45.0)
        except TimeoutError:
            diag = cdp.eval_js(
                "({alerts: window.__m5Alerts, itemsLen: Array.isArray(items) ? items.length : -1, trLen: document.querySelectorAll('#items-tbody tr').length, loadingTitle: (document.getElementById('loading-title')||{}).innerText, progressItem: (document.getElementById('progress-current-item')||{}).innerText, inputVal: document.getElementById('xlsx-file-input').value})"
            )
            raise TimeoutError(f"Upload wait timed out. Diag={diag}")
        initial_rows = cdp.eval_js(
            "items.map(it => ({name: it.original_name, code: it.matched_code, status: it.decision_status}))"
        )
        assert initial_rows[1]["status"] == "recommended", initial_rows
        report["steps"]["xlsx_upload_and_match"] = initial_rows

        # 4. Attempt export while row 2 is still 'recommended' -> blocked (422), 0 downloads
        cdp.eval_js("window.__m5Alerts = []; document.getElementById('export-btn').click(); true;")
        _wait_for_js(cdp, "window.__m5Alerts.length > 0", timeout=10.0)
        blocked_alerts = cdp.eval_js("window.__m5Alerts.slice()")
        assert any("导出失败" in a for a in blocked_alerts), blocked_alerts
        assert list(dl_dir.iterdir()) == [], f"Unexpected download during blocked export: {list(dl_dir.iterdir())}"
        report["steps"]["blocked_export_422_no_download"] = {"alerts": blocked_alerts, "download_count": 0}

        # 5. Rematch row 1 via #rematch-selected-btn + Reject row 1 via /api/binding/reject -> verify rejected -> reselect candidate
        cdp.eval_js("document.querySelectorAll('#items-tbody tr')[0].click(); true;")
        time.sleep(0.2)
        cdp.eval_js("window.__m5Alerts = []; document.getElementById('rematch-selected-btn').click(); true;")
        time.sleep(0.6)
        reject_status = cdp.eval_js(
            """
            (async () => {
                const it = items[0];
                const resp = await fetch('/api/binding/reject', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        original_name: it.original_name,
                        original_spec: it.original_spec,
                        original_qty: it.original_qty,
                        original_unit: it.original_unit,
                        original_loss: it.original_loss,
                        original_row_idx: it.original_row_idx,
                        professional: it.professional || '暖通',
                        scope_filename: window.originalFileName || '',
                        bindings: []
                    })
                });
                const d = await resp.json();
                const st = (d.decision && d.decision.decision_status) || d.decision_status;
                it.decision_status = st;
                renderTable();
                return st;
            })()
            """
        )
        assert reject_status == "rejected", reject_status
        # Re-select candidate card + confirm row 1 & row 2 via real buttons
        cdp.eval_js("document.querySelectorAll('#candidates-container .candidate-card')[0].click(); true;")
        time.sleep(0.2)
        cdp.eval_js("document.getElementById('confirm-match-finished-btn').click(); true;")
        _wait_for_js(cdp, "items[0].decision_status === 'manual_confirmed'", timeout=10.0)

        # Save named session + verify legacy draft annotate downgrade
        cdp.eval_js(
            """
            (() => {
                document.getElementById('save-trigger').click();
                document.getElementById('save-session-name').value = 'M5_E2E_Session';
                window.__m5Alerts = [];
                document.getElementById('confirm-save-session-btn').click();
                return true;
            })()
            """
        )
        _wait_for_js(cdp, "window.__m5Alerts.some(a => a.includes('进度保存成功'))", timeout=10.0)
        legacy_downgrade = cdp.eval_js(
            """
            (async () => {
                const fakeLegacy = items.map(it => ({...it, is_exact: true, decision_status: 'auto_selected', decision_source: 'legacy'}));
                const r = await fetch('/api/session/annotate', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({scope_filename: window.originalFileName || '', items: fakeLegacy})
                });
                return await r.json();
            })()
            """
        )
        assert legacy_downgrade["downgraded"] >= 1, legacy_downgrade
        report["steps"]["save_session_and_legacy_downgrade"] = {"downgraded": legacy_downgrade["downgraded"]}

        # Restore saved session from History modal
        cdp.eval_js("document.getElementById('history-trigger').click(); true;")
        _wait_for_js(cdp, "document.querySelectorAll('#history-sessions-tbody .btn-restore-sess').length >= 1", timeout=10.0)
        cdp.eval_js("window.__m5Alerts = []; document.querySelector('#history-sessions-tbody .btn-restore-sess').click(); true;")
        _wait_for_js(cdp, "window.__m5Alerts.some(a => a.includes('进度已成功恢复')) && Array.isArray(items) && items.length === 2", timeout=15.0)
        report["steps"]["history_restore_verified"] = True

        # Confirm both rows via real UI clicks
        for idx in (0, 1):
            cdp.eval_js(f"document.querySelectorAll('#items-tbody tr')[{idx}].click(); true;")
            _wait_for_js(cdp, f"selectedItem !== null && selectedItem.original_row_idx === items[{idx}].original_row_idx", timeout=10.0)
            cdp.eval_js(
                """
                (() => {
                    if (!selectedItem.bindings || selectedItem.bindings.length === 0) {
                        const card = document.querySelector('#candidates-container .candidate-card');
                        if (card) card.click();
                    }
                    document.getElementById('confirm-match-finished-btn').click();
                    return true;
                })()
                """
            )
            _wait_for_js(cdp, f"items[{idx}].decision_status === 'manual_confirmed'", timeout=10.0)

        shot1 = cdp.call("Page.captureScreenshot", {"format": "png"})
        (EVIDENCE_DIR / "m5_r01_ui_workbench.png").write_bytes(base64.b64decode(shot1["data"]))

        # 6. Trigger valid export -> wait for real downloaded .xlsx file -> SHA256 & openpyxl check
        cdp.eval_js("window.__m5Alerts = []; document.getElementById('export-btn').click(); true;")
        dl_deadline = time.time() + 20.0
        exported_file = None
        while time.time() < dl_deadline:
            ready = [p for p in dl_dir.glob("*.xlsx") if not p.name.endswith(".crdownload")]
            if ready and ready[0].stat().st_size > 0:
                exported_file = ready[0]
                break
            time.sleep(0.25)
        assert exported_file is not None, f"Expected .xlsx download in {dl_dir}, found: {list(dl_dir.iterdir())}"

        export_sha = _sha256(exported_file)
        wb_exp = openpyxl.load_workbook(exported_file, data_only=True)
        ws_exp = wb_exp.active
        exported_rows = [
            [ws_exp.cell(r, c).value for c in range(1, min(ws_exp.max_column, 12) + 1)]
            for r in range(1, ws_exp.max_row + 1)
        ]
        wb_exp.close()
        flat_text = json.dumps(exported_rows, ensure_ascii=False)
        assert "M5-UI-001" in flat_text, exported_rows
        assert "3.50.29.0199" in flat_text or "3.50.29.0200" in flat_text, exported_rows

        shot2 = cdp.call("Page.captureScreenshot", {"format": "png"})
        (EVIDENCE_DIR / "m5_r01_ui_export_done.png").write_bytes(base64.b64decode(shot2["data"]))

        report["steps"]["valid_export_download"] = {
            "filename": exported_file.name,
            "size_bytes": exported_file.stat().st_size,
            "sha256": export_sha,
            "row_count": len(exported_rows),
            "verified_codes": ["M5-UI-001", "3.50.29.0199/0200"],
        }

        # Verify employee session gets 403 on /api/admin/health, then admin login via HTTP verifies 200 healthy metrics
        emp_health_status = cdp.eval_js(
            "(async () => (await fetch('/api/admin/health')).status)()"
        )
        assert emp_health_status == 403, emp_health_status

        import urllib.request

        def _req_json(url: str, payload: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
            hdrs = dict(headers or {})
            data = None
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                hdrs["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data else "GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return int(resp.status), json.loads(resp.read().decode("utf-8"))

        st_l, login_d = _req_json(
            f"{base_url}/api/auth/login",
            {"username": "admin", "password": "M5R01UiAdmin#2026!"},
        )
        assert st_l == 200 and login_d.get("access_token"), (st_l, login_d)
        st_h, health_d = _req_json(
            f"{base_url}/api/admin/health",
            headers={"Authorization": f"Bearer {login_d['access_token']}"},
        )
        assert st_h == 200, (st_h, health_d)
        assert health_d["status"] == "healthy", health_d
        assert health_d["metrics_available"] is True, health_d
        assert health_d["hardware"]["memory"]["total_gb"] > 0, health_d
        report["steps"]["admin_health_live"] = {
            "employee_forbidden_status": emp_health_status,
            "status": health_d["status"],
            "metrics_available": health_d["metrics_available"],
            "memory_total_gb": health_d["hardware"]["memory"]["total_gb"],
        }

        report["passed"] = True
        out_json = EVIDENCE_DIR / "m5_r01_browser_e2e_report.json"
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("M5_UI_E2E_PASS:", json.dumps(report, ensure_ascii=False))
    finally:
        if cdp is not None:
            try:
                cdp.close()
            except Exception:
                pass
        for proc in (chrome_proc, server_proc):
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    run_browser_e2e()
