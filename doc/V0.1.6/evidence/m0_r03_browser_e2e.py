# -*- coding: utf-8 -*-
"""
V0.1.6 M0 R03 真实 Chrome CDP 端到端交互验证（跨独立认证上下文 + 失败重试 + 刷新恢复 + SQLite 落库核验）。

覆盖要求：
1. 原 R01/R02 7 项核心门禁（Alice 上传、候选反馈、芯片重算 revision>=2 且不覆写 rev1、确认、命名保存与刷新历史恢复、Bob 同文件独立上下文、SQLite 跨用户隔离）。
2. C-M0-08 Chrome 真实 DOM 故障与恢复门禁：
   - 注入 HTTP 500（标明传输拦截注入点）-> 真实 DOM 展示 #feedback-delivery-banner 与 #retry-pending-feedback-btn，留存重试截图；
   - 点击 #retry-pending-feedback-btn -> 沿用同一 client_event_id、event_seq 与 payload 重试并 200 清零；
   - 再触发故障并保存草稿、真实整页重载接受草稿恢复 -> 待投递事件与重试横幅跨刷新保留 -> 点击重试成功；
   - Bob 独立上下文与 SQLite 核验无重复事件、无跨用户待投递泄漏。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = Path(__file__).resolve().parent
V015_EVIDENCE_DIR = ROOT / "doc" / "V0.1.5" / "evidence"
sys.path.insert(0, str(V015_EVIDENCE_DIR))
sys.path.insert(0, str(ROOT))

from m4_r03_browser_check import (  # noqa: E402
    CHROME_PATH,
    MiniCDPClient,
    _free_port,
    _http_get_json,
    _http_status,
    _wait_for_js,
)

REPORT_OUT = EVIDENCE_DIR / "m0_r03_browser_e2e_report.json"
ALICE_SHOT = EVIDENCE_DIR / "m0_r03_ui_alice_restored.png"
ALICE_ERR_SHOT = EVIDENCE_DIR / "m0_r03_ui_alice_pending_error.png"
BOB_SHOT = EVIDENCE_DIR / "m0_r03_ui_bob_workbench.png"

INSTALL_CAPTURE_HOOK_JS = """
(() => {
    if (window.__m0HookInstalled) return true;
    window.__m0HookInstalled = true;
    window.__m0Requests = [];
    window.__m0Alerts = [];
    window.__m0Confirms = [];
    window.__m0AcceptDraftOnReload = window.__m0AcceptDraftOnReload || false;
    window.__m0InjectHttpErrorForPath = window.__m0InjectHttpErrorForPath || null;
    window.__m0InjectedHttpCount = 0;
    window.alert = (msg) => { window.__m0Alerts.push(String(msg)); };
    window.confirm = (msg) => {
        window.__m0Confirms.push(String(msg));
        if (String(msg).includes('未保存的编辑草稿')) {
            return Boolean(window.__m0AcceptDraftOnReload || localStorage.getItem('__m0AcceptDraftOnReload') === '1');
        }
        return true;
    };
    const origFetch = window.fetch.bind(window);
    window.fetch = async function(...args) {
        const input = args[0];
        const url = String((input && input.url) || input || '');
        const init = args[1] || {};
        const method = String(init.method || (input && input.method) || 'GET').toUpperCase();
        const parsedPath = new URL(url, window.location.origin).pathname;
        let reqBody = null;
        if (typeof init.body === 'string') {
            try { reqBody = JSON.parse(init.body); } catch (e) { reqBody = init.body; }
        }
        const inj = window.__m0InjectHttpErrorForPath;
        if (inj && parsedPath === inj.pathname && inj.remaining > 0) {
            inj.remaining -= 1;
            window.__m0InjectedHttpCount += 1;
            const status = Number(inj.status || 500);
            const errBody = inj.json || { detail: 'injected_chrome_transport_error' };
            window.__m0Requests.push({
                url: url,
                pathname: parsedPath,
                method: method,
                status: status,
                injected: true,
                reqBody: reqBody,
                respJson: errBody
            });
            return new Response(JSON.stringify(errBody), {
                status: status,
                headers: { 'Content-Type': 'application/json' }
            });
        }
        const resp = await origFetch(...args);
        let respJson = null;
        try {
            const clone = resp.clone();
            const txt = await clone.text();
            if (txt && (txt.startsWith('{') || txt.startsWith('['))) {
                respJson = JSON.parse(txt);
            }
        } catch (e) {}
        window.__m0Requests.push({
            url: url,
            pathname: parsedPath,
            method: method,
            status: resp.status,
            injected: false,
            reqBody: reqBody,
            respJson: respJson
        });
        return resp;
    };
    return true;
})()
"""


def _write_two_sheet_query_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "暖通"
    ws1.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"])
    ws1.append(["030701001", "止回阀", "公称直径 DN100 PN16 法兰连接", "台", 2])
    ws2 = wb.create_sheet("强电")
    ws2.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"])
    ws2.append(["030401002", "防爆检修电源箱", "380V 防爆", "台", 1])
    wb.save(path)
    wb.close()


class M0CDPClient(MiniCDPClient):
    """MiniCDPClient subclass that handles native beforeunload/confirm dialogs."""

    def __init__(self, ws_url: str):
        super().__init__(ws_url)
        self.accept_draft_confirm = False

    def call(self, method: str, params: dict = None):
        self._msg_id += 1
        cid = self._msg_id
        self._send_text(json.dumps({"id": cid, "method": method, "params": params or {}}))
        while True:
            raw = self._recv_frame()
            msg = json.loads(raw)
            if msg.get("method") == "Page.javascriptDialogOpening":
                dlg_params = msg.get("params") or {}
                dlg_type = dlg_params.get("type", "")
                dlg_msg = dlg_params.get("message", "")
                if dlg_type == "beforeunload":
                    accept = True
                elif "未保存的编辑草稿" in dlg_msg:
                    accept = self.accept_draft_confirm
                else:
                    accept = True
                self._msg_id += 1
                self._send_text(json.dumps({
                    "id": self._msg_id,
                    "method": "Page.handleJavaScriptDialog",
                    "params": {"accept": accept},
                }))
                continue
            if msg.get("id") == cid:
                if "error" in msg:
                    raise RuntimeError(f"CDP error for {method}: {msg['error']}")
                return msg.get("result", {})


def _launch_chrome_session(cdp_port: int, profile_dir: Path):
    chrome_proc = subprocess.Popen(
        [
            CHROME_PATH,
            "--headless=new",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={profile_dir}",
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
        chrome_proc.terminate()
        raise TimeoutError("Chrome CDP websocket 未出现")

    cdp = M0CDPClient(ws_url)
    cdp.call("Page.enable")
    cdp.call("DOM.enable")
    cdp.call("Runtime.enable")
    cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": INSTALL_CAPTURE_HOOK_JS})
    return chrome_proc, cdp


def _login_user(cdp: MiniCDPClient, base_url: str, username: str, password: str) -> None:
    cdp.call("Page.navigate", {"url": f"{base_url}/login.html"})
    _wait_for_js(cdp, "document.readyState === 'complete' && Boolean(document.querySelector('#password'))")
    cdp.eval_js(
        f"""
        (() => {{
            const u = document.querySelector('#username') || document.querySelector('input[type="text"]');
            const p = document.querySelector('#password') || document.querySelector('input[type="password"]');
            u.value = {json.dumps(username)};
            u.dispatchEvent(new Event('input', {{bubbles: true}}));
            p.value = {json.dumps(password)};
            p.dispatchEvent(new Event('input', {{bubbles: true}}));
            (document.querySelector('#loginBtn') || document.querySelector('button[type="submit"]')).click();
            return true;
        }})()
        """
    )
    _wait_for_js(cdp, "window.location.pathname.endsWith('/index.html') || window.location.pathname === '/'", timeout=15.0)
    _wait_for_js(
        cdp,
        "document.readyState === 'complete' && typeof handleFileUpload === 'function' && Boolean(document.getElementById('xlsx-file-input'))",
        timeout=15.0,
    )
    assert cdp.eval_js(INSTALL_CAPTURE_HOOK_JS) is True


def _upload_via_dom(cdp: MiniCDPClient, xlsx_path: Path, expected_rows: int) -> None:
    doc = cdp.call("DOM.getDocument", {"depth": -1})
    node = cdp.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#xlsx-file-input"})
    cdp.call("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(xlsx_path.resolve())]})
    _wait_for_js(
        cdp,
        f"Array.isArray(items) && items.length === {expected_rows} && document.querySelectorAll('#items-tbody tr').length >= 1",
        timeout=45.0,
    )


def _save_screenshot(cdp: MiniCDPClient, out_path: Path) -> None:
    res = cdp.call("Page.captureScreenshot", {"format": "png"})
    out_path.write_bytes(base64.b64decode(res["data"]))


def run_browser_e2e() -> None:
    if not Path(CHROME_PATH).exists():
        raise FileNotFoundError(f"Chrome not found at {CHROME_PATH}")
    t0 = time.time()

    tmp_root = Path(tempfile.mkdtemp(prefix="listtrans_v016_m0_ui_"))
    db_dir = tmp_root / "db"
    cfg_dir = tmp_root / "config"
    vec_dir = tmp_root / "vector_cache"
    up_dir = tmp_root / "uploads"
    out_dir = tmp_root / "output"
    log_dir = tmp_root / "logs"
    profile_alice = tmp_root / "chrome_alice"
    profile_bob = tmp_root / "chrome_bob"
    for p in (db_dir, vec_dir, up_dir, out_dir, log_dir, profile_alice, profile_bob):
        p.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "config", cfg_dir)

    mat_xlsx = tmp_root / "seed_materials.xlsx"
    wb_mat = openpyxl.Workbook()
    ws_m = wb_mat.active
    ws_m.append(["标准物料库"])
    ws_m.append(["物料分类", "物料编码", "物料名称", "规格/型号", "计量单位", "常用品牌", "描述"])
    ws_m.append(["阀门", "M0-UI-001", "止回阀", "DN100 PN16 法兰连接", "台", "埃美柯", "止回阀"])
    ws_m.append(["阀门", "M0-UI-002", "法兰止回阀", "DN100 PN16", "台", "埃美柯", "法兰止回阀"])
    ws_m.append(["防爆电气", "3.50.29.0199", "防爆检修电源箱", "ExdIIBT4 380V", "台", "华荣", "防爆箱"])
    wb_mat.save(mat_xlsx)
    wb_mat.close()

    shared_query_xlsx = tmp_root / "same_list.xlsx"
    _write_two_sheet_query_xlsx(shared_query_xlsx)

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
    env["SECRET_KEY"] = "V016_M0_Browser_E2E_Secret_Key_2026!"
    env["ADMIN_PASSWORD"] = "V016M0UiAdmin#2026!"
    env["PYTHONPATH"] = str(ROOT)

    seed_script = f"""
import json
from app.db_manager import init_db, import_materials_from_excel
from app.audit_manager import init_audit_tables
from app.session_manager import init_session_db
from app.flywheel_db import init_flywheel_tables
from app.auth_manager import init_auth_db, admin_create_user_atomic, change_user_credentials_atomic

init_db()
import_materials_from_excel({json.dumps(str(mat_xlsx))}, mode='replace', filename='seed_materials.xlsx')
init_auth_db()
init_audit_tables()
init_session_db()
init_flywheel_tables()
for uname, name, emp_no in [
    ('m0_ui_alice', 'Alice', 'M0-UI-A'),
    ('m0_ui_bob', 'Bob', 'M0-UI-B'),
]:
    res = admin_create_user_atomic(uname, role='employee', name=name, employee_no=emp_no)
    assert res['success'], res
    assert change_user_credentials_atomic(res['id'], res['temp_password'], new_password='M0UiUser#2026!')['success']
print("SEEDED_OK")
"""
    seed_out = subprocess.check_output([sys.executable, "-c", seed_script], cwd=str(ROOT), env=env, text=True, timeout=60)
    assert "SEEDED_OK" in seed_out

    app_port = _free_port()
    server_proc = None
    alice_chrome = None
    bob_chrome = None
    alice_cdp = None
    bob_cdp = None
    report = {"gates": {}, "alice": {}, "alice_retry_e2e": {}, "bob": {}, "sqlite": {}}

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
            raise TimeoutError("隔离 uvicorn 服务未就绪")

        # ---- Context 1: Alice ----
        alice_cdp_port = _free_port()
        alice_chrome, alice_cdp = _launch_chrome_session(alice_cdp_port, profile_alice)
        _login_user(alice_cdp, base_url, "m0_ui_alice", "M0UiUser#2026!")
        _upload_via_dom(alice_cdp, shared_query_xlsx, expected_rows=2)

        alice_row0 = alice_cdp.eval_js("items[0]")
        alice_row1 = alice_cdp.eval_js("items[1]")
        assert alice_row0["document_id"].startswith("doc_"), alice_row0
        assert alice_row0["sheet_id"].startswith("sht_"), alice_row0
        assert alice_row0["source_row_id"].startswith("row_"), alice_row0
        assert alice_row0["algo_version"] != "v1.0", alice_row0
        assert alice_row0["config_version"] != "snap_v1", alice_row0
        report["gates"]["g_alice_upload_identity"] = True

        # Row 0: click candidate card -> 200 on both /api/feedback/submit and /api/flywheel/record
        alice_cdp.eval_js("document.querySelectorAll('#items-tbody tr')[0].click(); true;")
        _wait_for_js(alice_cdp, "selectedItem !== null && document.querySelectorAll('#candidates-container .candidate-card').length >= 1", timeout=10.0)
        alice_cdp.eval_js(
            """
            (() => {
                const cards = Array.from(document.querySelectorAll('#candidates-container .candidate-card'));
                let target = cards.find(c => !c.classList.contains('active'));
                if (!target) {
                    cards[0].click();
                    target = document.querySelectorAll('#candidates-container .candidate-card')[0];
                }
                target.click();
                return true;
            })()
            """
        )
        _wait_for_js(
            alice_cdp,
            "window.__m0Requests.some(r => r.pathname === '/api/flywheel/record' && r.status === 200) && window.__m0Requests.some(r => r.pathname === '/api/feedback/submit' && r.status === 200)",
            timeout=10.0,
        )
        report["gates"]["g_alice_candidate_feedback_200"] = True

        # Rematch row 0 -> revision >= 2 & initial_snapshot_id preserved
        alice_cdp.eval_js(
            """
            (() => {
                const chip = document.querySelector('#params-chips-container .param-chip');
                if (chip) chip.click();
                document.getElementById('rematch-selected-btn').click();
                return true;
            })()
            """
        )
        _wait_for_js(
            alice_cdp,
            "window.__m0Requests.some(r => r.pathname === '/api/match' && r.status === 200)",
            timeout=10.0,
        )
        rematch_req = alice_cdp.eval_js("window.__m0Requests.find(r => r.pathname === '/api/match' && r.status === 200)")
        assert rematch_req["respJson"]["recommendation_revision"] >= 2, rematch_req
        assert alice_cdp.eval_js("items[0].initial_snapshot_id") == alice_row0["initial_snapshot_id"]
        assert alice_cdp.eval_js("items[0].recommendation_revision") >= 2
        report["gates"]["g_alice_rematch_revision_preserved_initial"] = True

        # Confirm row 0
        alice_cdp.eval_js(
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
        _wait_for_js(
            alice_cdp,
            "window.__m0Requests.some(r => r.pathname === '/api/binding/submit' && r.status === 200) && window.__m0Requests.some(r => r.pathname === '/api/flywheel/record' && r.reqBody && r.reqBody.action_type === 'CONFIRM_TOP1' && r.status === 200)",
            timeout=10.0,
        )
        report["gates"]["g_alice_confirm_and_flywheel_200"] = True

        # Save named session & reload & restore named session
        alice_cdp.eval_js("document.getElementById('save-trigger').click(); true;")
        _wait_for_js(alice_cdp, "document.getElementById('save-session-modal').style.display !== 'none'", timeout=5.0)
        alice_cdp.eval_js(
            """
            (() => {
                const inp = document.getElementById('save-session-name');
                inp.value = 'M0_Alice_Session';
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                document.getElementById('confirm-save-session-btn').click();
                return true;
            })()
            """
        )
        _wait_for_js(
            alice_cdp,
            "window.__m0Requests.some(r => r.pathname === '/api/session/save' && r.status === 200)",
            timeout=10.0,
        )
        alice_before_reload_reqs = alice_cdp.eval_js("window.__m0Requests")

        alice_cdp.call("Page.navigate", {"url": f"{base_url}/index.html"})
        _wait_for_js(alice_cdp, "document.readyState === 'complete' && Boolean(document.getElementById('history-trigger'))", timeout=15.0)
        assert alice_cdp.eval_js(INSTALL_CAPTURE_HOOK_JS) is True
        alice_cdp.eval_js("document.getElementById('history-trigger').click(); true;")
        _wait_for_js(alice_cdp, "document.querySelectorAll('.btn-restore-sess').length >= 1", timeout=10.0)
        alice_cdp.eval_js("document.querySelector('.btn-restore-sess').click(); true;")
        _wait_for_js(
            alice_cdp,
            "window.__m0Requests.some(r => r.pathname === '/api/session/annotate' && r.status === 200) && Array.isArray(items) && items.length === 2",
            timeout=15.0,
        )
        restored_row0 = alice_cdp.eval_js("items[0]")
        annotate_rec = alice_cdp.eval_js("window.__m0Requests.find(r => r.pathname === '/api/session/annotate')")
        assert restored_row0["source_row_id"] == alice_row0["source_row_id"]
        assert restored_row0["document_id"] == alice_row0["document_id"]
        assert restored_row0["trust_level"] == "trusted"
        assert annotate_rec["respJson"]["legacy_untrusted_count"] == 0
        assert restored_row0["max_event_seq"] >= 2
        _save_screenshot(alice_cdp, ALICE_SHOT)
        report["gates"]["g_alice_reload_and_named_restore"] = True

        # ---- C-M0-08 Chrome DOM Fault Injection & Same-Event Retry Gate ----
        # Switch to 强电 sheet or row 1 and inject HTTP 500 on /api/flywheel/record (transport boundary mock)
        alice_cdp.eval_js(
            """
            (() => {
                window.__m0Requests = [];
                window.__M0_FEEDBACK_RETRY_CONFIG = { maxAutoRetries: 0, retryDelayMs: 0 };
                window.__m0InjectHttpErrorForPath = {
                    pathname: '/api/flywheel/record',
                    remaining: 1,
                    status: 500,
                    json: { detail: 'injected_chrome_dom_500' }
                };
                // Switch to 强电 tab so row 1 is visible and unbound
                const profBtns = Array.from(document.querySelectorAll('#prof-tabs button'));
                const qdBtn = profBtns.find(b => b.innerText.includes('强电'));
                if (qdBtn) qdBtn.click();
                return true;
            })()
            """
        )
        _wait_for_js(alice_cdp, "document.querySelectorAll('#items-tbody tr').length >= 1", timeout=5.0)
        alice_cdp.eval_js("document.querySelectorAll('#items-tbody tr')[0].click(); true;")
        _wait_for_js(alice_cdp, "selectedItem !== null && document.querySelectorAll('#candidates-container .candidate-card').length >= 1", timeout=10.0)
        alice_cdp.eval_js(
            """
            (() => {
                const cards = Array.from(document.querySelectorAll('#candidates-container .candidate-card'));
                const target = cards.find(c => !c.classList.contains('active'));
                if (target) {
                    target.click();
                } else {
                    cards[0].click();
                    document.querySelectorAll('#candidates-container .candidate-card')[0].click();
                }
                return true;
            })()
            """
        )
        _wait_for_js(
            alice_cdp,
            "Boolean(document.getElementById('feedback-delivery-banner')) && Boolean(document.getElementById('retry-pending-feedback-btn'))",
            timeout=10.0,
        )
        banner_fail_text = alice_cdp.eval_js("document.getElementById('feedback-delivery-banner').innerText")
        assert "投递失败待重试" in banner_fail_text, banner_fail_text
        _save_screenshot(alice_cdp, ALICE_ERR_SHOT)

        # Click real DOM retry button -> replays IDENTICAL client_event_id & event_seq -> 200 clears banner
        alice_cdp.eval_js("document.getElementById('retry-pending-feedback-btn').click(); true;")
        _wait_for_js(
            alice_cdp,
            "document.getElementById('feedback-delivery-banner') === null && window.__m0Requests.filter(r => r.pathname === '/api/flywheel/record' && r.status === 200).length >= 1",
            timeout=10.0,
        )
        retry_phase_reqs = alice_cdp.eval_js("window.__m0Requests.filter(r => r.pathname === '/api/flywheel/record')")
        assert len(retry_phase_reqs) == 2, retry_phase_reqs
        assert retry_phase_reqs[0]["status"] == 500 and retry_phase_reqs[0]["injected"] is True
        assert retry_phase_reqs[1]["status"] == 200 and retry_phase_reqs[1]["injected"] is False
        assert retry_phase_reqs[0]["reqBody"] == retry_phase_reqs[1]["reqBody"]
        report["gates"]["g_alice_chrome_500_pending_and_retry_same_event"] = True

        # ---- C-M0-08 Chrome Page Reload Pending Persistence Gate ----
        # Confirm row 1 under injected 503 on /api/flywheel/record (/api/binding/submit stays 200!), save draft, reload Chrome page with draft restore accepted
        alice_cdp.eval_js(
            """
            (() => {
                window.__m0Requests = [];
                window.__M0_FEEDBACK_RETRY_CONFIG = { maxAutoRetries: 0, retryDelayMs: 0 };
                window.__m0InjectHttpErrorForPath = {
                    pathname: '/api/flywheel/record',
                    remaining: 1,
                    status: 503,
                    json: { detail: 'injected_chrome_503_before_reload' }
                };
                document.getElementById('confirm-match-finished-btn').click();
                return true;
            })()
            """
        )
        _wait_for_js(
            alice_cdp,
            "window.__m0Requests.some(r => r.pathname === '/api/binding/submit' && r.status === 200) && Boolean(document.getElementById('feedback-delivery-banner'))",
            timeout=10.0,
        )
        pending_before_reload = alice_cdp.eval_js("window.getActivePendingFeedbackEvents()")
        assert len(pending_before_reload) == 1, pending_before_reload
        # Save draft so reload offers restore
        alice_cdp.eval_js("saveDraft(); true;")
        _wait_for_js(
            alice_cdp,
            "window.__m0Requests.some(r => r.pathname === '/api/session/draft' && r.method === 'POST' && r.status === 200)",
            timeout=10.0,
        )

        # Navigate reload & accept draft restore in Chrome dialog
        alice_cdp.accept_draft_confirm = True
        alice_cdp.eval_js("localStorage.setItem('__m0AcceptDraftOnReload', '1'); true;")
        alice_cdp.call("Page.navigate", {"url": f"{base_url}/index.html"})
        _wait_for_js(
            alice_cdp,
            "window.__m0Requests.some(r => r.pathname === '/api/session/annotate' && r.status === 200) && Boolean(document.getElementById('feedback-delivery-banner')) && Boolean(document.getElementById('retry-pending-feedback-btn'))",
            timeout=15.0,
        )
        pending_after_reload = alice_cdp.eval_js("window.getActivePendingFeedbackEvents()")
        assert len(pending_after_reload) == 1
        assert pending_after_reload[0]["event_id"] == pending_before_reload[0]["event_id"]
        assert pending_after_reload[0]["event_seq"] == pending_before_reload[0]["event_seq"]

        # Click retry after reload -> 200 clears banner
        alice_cdp.eval_js("document.getElementById('retry-pending-feedback-btn').click(); true;")
        _wait_for_js(
            alice_cdp,
            "document.getElementById('feedback-delivery-banner') === null && window.__m0Requests.some(r => r.pathname === '/api/flywheel/record' && r.status === 200)",
            timeout=10.0,
        )
        retried_after_reload = alice_cdp.eval_js("window.__m0Requests.find(r => r.pathname === '/api/flywheel/record' && r.status === 200)")
        assert retried_after_reload["reqBody"] == pending_before_reload[0]["payload"]
        report["gates"]["g_alice_chrome_reload_pending_persist_and_retry"] = True
        report["alice_retry_e2e"] = {
            "injected_500_event_id": retry_phase_reqs[0]["reqBody"]["client_event_id"],
            "retried_500_event_seq": retry_phase_reqs[1]["reqBody"]["event_seq"],
            "reload_persisted_event_id": pending_after_reload[0]["event_id"],
            "reload_retried_event_seq": retried_after_reload["reqBody"]["event_seq"],
        }

        report["alice"] = {
            "document_id": alice_row0["document_id"],
            "sheet_id": alice_row0["sheet_id"],
            "source_row_id": alice_row0["source_row_id"],
            "initial_snapshot_id": alice_row0["initial_snapshot_id"],
            "restored_max_event_seq": restored_row0["max_event_seq"],
            "captured_flywheel_requests": [
                {"pathname": r["pathname"], "status": r["status"], "reqBody": r["reqBody"]}
                for r in alice_before_reload_reqs
                if r["pathname"] in ("/api/flywheel/record", "/api/feedback/submit")
            ],
        }

        # ---- Context 2: Bob (separate Chrome profile, identical file) ----
        bob_cdp_port = _free_port()
        bob_chrome, bob_cdp = _launch_chrome_session(bob_cdp_port, profile_bob)
        _login_user(bob_cdp, base_url, "m0_ui_bob", "M0UiUser#2026!")
        _upload_via_dom(bob_cdp, shared_query_xlsx, expected_rows=2)

        bob_row0 = bob_cdp.eval_js("items[0]")
        assert bob_row0["document_id"] != alice_row0["document_id"]
        assert bob_row0["sheet_id"] != alice_row0["sheet_id"]
        assert bob_row0["source_row_id"] != alice_row0["source_row_id"]
        assert bob_cdp.eval_js("window.getActivePendingFeedbackEvents().length") == 0
        assert bob_cdp.eval_js("document.getElementById('feedback-delivery-banner') === null") is True

        bob_cdp.eval_js("document.querySelectorAll('#items-tbody tr')[0].click(); true;")
        _wait_for_js(bob_cdp, "selectedItem !== null && document.querySelectorAll('#candidates-container .candidate-card').length >= 1", timeout=10.0)
        bob_cdp.eval_js(
            """
            (() => {
                if (!selectedItem.bindings || selectedItem.bindings.length === 0) {
                    document.querySelector('#candidates-container .candidate-card').click();
                }
                document.getElementById('confirm-match-finished-btn').click();
                return true;
            })()
            """
        )
        _wait_for_js(
            bob_cdp,
            "window.__m0Requests.some(r => r.pathname === '/api/flywheel/record' && r.reqBody && r.reqBody.action_type === 'CONFIRM_TOP1' && r.status === 200)",
            timeout=10.0,
        )
        _save_screenshot(bob_cdp, BOB_SHOT)
        bob_reqs = bob_cdp.eval_js("window.__m0Requests")
        report["gates"]["g_bob_independent_context_and_identity"] = True
        report["bob"] = {
            "document_id": bob_row0["document_id"],
            "sheet_id": bob_row0["sheet_id"],
            "source_row_id": bob_row0["source_row_id"],
            "captured_flywheel_requests": [
                {"pathname": r["pathname"], "status": r["status"], "reqBody": r["reqBody"]}
                for r in bob_reqs
                if r["pathname"] in ("/api/flywheel/record", "/api/feedback/submit")
            ],
        }

        # ---- Direct SQLite verification ----
        mat_conn = sqlite3.connect(db_dir / "materials.db")
        mat_conn.row_factory = sqlite3.Row
        auth_conn = sqlite3.connect(db_dir / "auth.db")
        auth_conn.row_factory = sqlite3.Row
        try:
            u_alice = dict(auth_conn.execute("SELECT id, username, account_epoch FROM users WHERE username='m0_ui_alice'").fetchone())
            u_bob = dict(auth_conn.execute("SELECT id, username, account_epoch FROM users WHERE username='m0_ui_bob'").fetchone())

            fw_all = [dict(r) for r in mat_conn.execute("SELECT * FROM flywheel_records ORDER BY id ASC").fetchall()]
            snaps_alice = [
                dict(r)
                for r in mat_conn.execute(
                    "SELECT * FROM flywheel_recommendation_snapshots WHERE source_row_id = ? ORDER BY revision ASC",
                    (alice_row0["source_row_id"],),
                ).fetchall()
            ]
            fb_all = [dict(r) for r in mat_conn.execute("SELECT * FROM flywheel_feedback_samples ORDER BY id ASC").fetchall()]
        finally:
            mat_conn.close()
            auth_conn.close()

        alice_fw = [r for r in fw_all if r["document_id"] == alice_row0["document_id"]]
        bob_fw = [r for r in fw_all if r["document_id"] == bob_row0["document_id"]]
        alice_row1_fw = [r for r in alice_fw if r["source_row_id"] == alice_row1["source_row_id"]]

        assert len(alice_fw) >= 4
        assert len(alice_row1_fw) == 2
        assert len({r["client_event_id"] for r in alice_row1_fw}) == 2
        assert len(bob_fw) >= 1
        assert all(r["actor_user_id"] == u_alice["id"] and r["actor_account_epoch"] == u_alice["account_epoch"] for r in alice_fw)
        assert all(r["actor_user_id"] == u_bob["id"] and r["actor_account_epoch"] == u_bob["account_epoch"] for r in bob_fw)
        assert sum(1 for r in alice_fw if r["source_row_id"] == alice_row0["source_row_id"] and r["superseded"] == 0) == 1
        assert sum(1 for r in alice_row1_fw if r["superseded"] == 0) == 1
        assert sum(1 for r in bob_fw if r["superseded"] == 0) == 1
        assert all(r["session_id"] != "default_session" for r in fw_all)
        assert all(r["algo_version"] != "v1.0" and r["config_version"] != "snap_v1" for r in fw_all)
        assert len(snaps_alice) >= 2 and snaps_alice[0]["is_initial"] == 1 and snaps_alice[0]["revision"] == 1
        assert len(fb_all) >= 2 and all(r["trust_level"] == "trusted" for r in fb_all)

        report["gates"]["g_sqlite_cross_user_isolation_and_snapshots"] = True
        report["sqlite"] = {
            "alice_records_count": len(alice_fw),
            "alice_row1_retry_records_count": len(alice_row1_fw),
            "bob_records_count": len(bob_fw),
            "alice_snapshots_revisions": [s["revision"] for s in snaps_alice],
            "feedback_samples_count": len(fb_all),
        }
        report["passed"] = all(report["gates"].values())
        report["elapsed_seconds"] = round(time.time() - t0, 3)
        REPORT_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "gates": report["gates"], "sqlite": report["sqlite"]}, ensure_ascii=False))
    finally:
        for c in (alice_cdp, bob_cdp):
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
        for proc in (alice_chrome, bob_chrome, server_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    run_browser_e2e()
