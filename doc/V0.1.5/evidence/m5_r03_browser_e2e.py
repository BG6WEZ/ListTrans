# -*- coding: utf-8 -*-
"""
V0.1.5 M5 R03 — UI-M5-01 真实浏览器 (Chrome CDP) 端到端回归脚本（A-M5-05 维持关闭的回归复验）。

R03 定位（见 V015-M5_R02_统筹裁决.md 第 1 节 A-M5-05 行）：A-M5-05 已按 R02 复审
关闭，R03 仅闭环 A-M5-01/02，本脚本复刻 R02 全部真实 DOM/HTTP 回归口径，断言与
22 个 gates 不降级，证据产物改用 m5_r03_* 命名并校验 R02 原件未覆写。

历史缺陷背景（R01 审核小结第 29 行 / R01 统筹裁决第 13 行）：
  R01 在 eval_js 内直接 `fetch('/api/binding/reject')` 并手工执行
  `it.decision_status = st; renderTable();`，以测试赋值替代了受测 UI 动作，
  不构成真实 DOM 拒绝闭环。R02 已整改，R03 保持同一红线不回退。

R02/R03 共同红线（本脚本不可回退）：
  1. 所有状态变更均由真实 DOM 控件的真实事件触发：
     - .candidate-card click -> applyMatchCandidate -> invalidateItemDecision
     - .btn-delete-binding click -> splice + invalidateItemDecision
     - .bound-qty-input input 事件（native value setter + bubbling Event）
       -> 真实 input 监听器 -> invalidateItemDecision
     - #confirm-match-finished-btn click -> 真实 /api/binding/submit
  2. 产品工作台不存在“拒绝”按钮（index.html 72 处按钮枚举、app.js/index.html
     对 /api/binding/reject 零引用，仅 server.py:2652 提供真实产品端点）。
     本脚本在页面内创建【真实 button DOM 节点】并经 addEventListener 绑定真实
     点击监听器，监听器读取产品自身全局 selectedItem 拼装 BindingSubmitRequest
     契约，调用真实产品端点 /api/binding/reject；测试代码不直接写任何
     item.decision_status。该注入入口只证明“真实 DOM 事件 -> 真实产品 HTTP ->
     服务端持久化 -> 保存恢复权威改写”闭环，不证明产品自身具备原生拒绝入口，
     此 UI 证据限定不得在小结/证据中淡化或移除。拒绝的有效性仅通过服务端权威
     效果观测：
       a) window.fetch 网络钩子实证 POST 200；
       b) 真实导出按钮 -> 服务端 _gate_export_row 422「人工拒绝」且零下载；
       c) 真实保存命名会话 -> 历史恢复 -> /api/session/annotate 权威注释，
          旧 items_json 中的 manual_confirmed 凭据不能被恢复，行被服务端
          改写为 rejected（_annotate_restored_items server.py:582-586）。
  3. API 级检查（legacy annotate 降级、admin health）仅作补充，不替代 DOM 链路。
  4. 保留 R02 既有口径：.xls 客户端拒绝、未确认 422 零下载、确认后 200 真实
     XLSX 下载并以 openpyxl 重读字段/单位/数量；R02 原件保持不动并校验哈希。

隔离：tmp 七子目录 + 独立 materials/auth DB、配置、向量缓存、上传/输出/日志、
独立 Chrome profile；LLM_ENABLED=false；uvicorn 单 worker；不触达业务库与线上。
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
import urllib.request
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

R02_SCRIPT = EVIDENCE_DIR / "m5_r02_browser_e2e.py"
R02_REPORT = EVIDENCE_DIR / "m5_r02_browser_e2e_report.json"
R02_SHOT_WORKBENCH = EVIDENCE_DIR / "m5_r02_ui_workbench.png"
R02_SHOT_REJECTED = EVIDENCE_DIR / "m5_r02_ui_rejected_restored.png"
R02_SHOT_EXPORT = EVIDENCE_DIR / "m5_r02_ui_export_done.png"
R03_REPORT = EVIDENCE_DIR / "m5_r03_browser_e2e_report.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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


# ---- 页面内注入：网络钩子与真实 DOM 拒绝控件（字符串常量，便于审计复核） ----

INSTALL_FETCH_HOOK_JS = """
(() => {
    if (window.__r03NetInstalled) return true;
    window.__r03NetInstalled = true;
    window.__r03Net = [];
    const origFetch = window.fetch.bind(window);
    window.fetch = async function(...args) {
        const input = args[0];
        const url = String((input && input.url) || input || '');
        const init = args[1] || {};
        const method = String(init.method || (input && input.method) || 'GET').toUpperCase();
        try {
            const resp = await origFetch(...args);
            window.__r03Net.push({url: url, method: method, status: resp.status});
            return resp;
        } catch (e) {
            window.__r03Net.push({url: url, method: method, status: 0, error: String(e)});
            throw e;
        }
    };
    return true;
})()
"""

# 真实 DOM 按钮 + 真实 addEventListener 监听器；只调用产品端点，绝不写 item 状态。
INSTALL_REJECT_BUTTON_JS = """
(() => {
    if (document.getElementById('r03-reject-btn')) return true;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'r03-reject-btn';
    btn.className = 'btn btn-danger';
    btn.textContent = 'R03测试线束-拒绝当前推荐';
    btn.style.margin = '8px';
    btn.addEventListener('click', async () => {
        try {
            const it = selectedItem;
            if (!it) { window.__r03RejectResult = {ok: false, reason: 'no_selected_item'}; return; }
            const payload = {
                original_name: it.original_name,
                original_spec: it.original_spec,
                original_qty: it.original_qty,
                original_unit: it.original_unit || '',
                original_loss: it.original_loss || 0,
                original_row_idx: it.original_row_idx || 0,
                professional: it.professional || '暖通',
                scope_filename: originalFileName || '',
                original_category: it.original_category || '',
                original_brand: it.original_brand || '',
                original_code: it.original_code || '',
                bindings: (it.bindings || []).map(b => ({
                    code: b.code,
                    name: b.name || '',
                    specification: b.specification || '',
                    unit: b.unit || '',
                    brand: b.brand || '',
                    category: b.category || '',
                    qty: b.qty,
                    qty_ratio: (b.qty_ratio === undefined || b.qty_ratio === null) ? null : b.qty_ratio
                }))
            };
            const resp = await fetch('/api/binding/reject', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const body = await resp.json().catch(() => ({}));
            window.__r03RejectResult = {ok: true, http_status: resp.status, body: body};
        } catch (e) {
            window.__r03RejectResult = {ok: false, error: String(e)};
        }
    });
    (document.getElementById('detail-editor') || document.body).appendChild(btn);
    return true;
})()
"""

# 经原生 input value setter 赋值并派发冒泡 input 事件，触发产品自身监听器。
EDIT_QTY_JS = """
(() => {
    const input = document.querySelector('.bound-qty-input');
    if (!input) return {ok: false, reason: 'bound_qty_input_not_found'};
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, '4');
    input.dispatchEvent(new Event('input', {bubbles: true}));
    return {ok: true, rendered_value: input.value};
})()
"""


def _pill_text(cdp: MiniCDPClient, row_idx: int) -> str:
    return cdp.eval_js(
        f"document.querySelectorAll('#items-tbody tr .row-status-pill')[{row_idx}].innerText.trim()"
    )


def _item_state(cdp: MiniCDPClient, idx: int) -> dict:
    return cdp.eval_js(
        f"""
        (() => {{
            const it = items[{idx}];
            return {{
                decision_status: it.decision_status,
                decision_source: it.decision_source,
                decision_reason: it.decision_reason,
                is_exact: it.is_exact,
                binding_count: (it.bindings || []).length,
                first_qty: (it.bindings && it.bindings[0]) ? it.bindings[0].qty : null,
                matched_code: it.matched_code || ''
            }};
        }})()
        """
    )


def _net_records(cdp: MiniCDPClient, fragment: str) -> list:
    return cdp.eval_js(
        f"window.__r03Net.filter(r => r.url.indexOf({json.dumps(fragment)}) !== -1)"
    )


def run_browser_e2e() -> None:
    if not Path(CHROME_PATH).exists():
        raise FileNotFoundError(f"Chrome not found at {CHROME_PATH}")
    t0 = time.time()

    # R02 原件哈希基线（结束时复核，证明未覆写；脚本+报告+3 张截图共 5 件）
    r02_hashes_before = {
        "m5_r02_browser_e2e.py": _sha256(R02_SCRIPT),
        "m5_r02_browser_e2e_report.json": _sha256(R02_REPORT),
        "m5_r02_ui_workbench.png": _sha256(R02_SHOT_WORKBENCH),
        "m5_r02_ui_rejected_restored.png": _sha256(R02_SHOT_REJECTED),
        "m5_r02_ui_export_done.png": _sha256(R02_SHOT_EXPORT),
    }

    tmp_root = Path(tempfile.mkdtemp(prefix="listtrans_m5_r03_ui_"))
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
    env["SECRET_KEY"] = "M5_R03_UI_Browser_E2E_Secret_Key_2026!"
    env["ADMIN_PASSWORD"] = "M5R03UiAdmin#2026!"
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
    report = {"ruling_reference": "doc/V0.1.5/rounds/V015-M5_R02_统筹裁决.md#A-M5-05", "steps": {}, "gates": {}}

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
            raise TimeoutError("Chrome CDP websocket 未出现")

        cdp = MiniCDPClient(ws_url)
        cdp.call("Page.enable")
        cdp.call("DOM.enable")
        cdp.call("Runtime.enable")
        try:
            cdp.call("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(dl_dir.resolve())})
        except Exception:
            cdp.call("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(dl_dir.resolve())})

        # 1. 员工真实登录
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

        # alert/confirm 捕获 + 全量 fetch 网络钩子（在任何受测动作前安装）
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
        assert cdp.eval_js(INSTALL_FETCH_HOOK_JS) is True

        # 2. accept 属性契约（真实 DOM 读取）
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

        # 3. .xls 经真实 #xlsx-file-input change 事件被客户端拒绝（无 /api/upload 派发）
        doc = cdp.call("DOM.getDocument", {"depth": -1})
        node = cdp.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#xlsx-file-input"})
        cdp.call("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(legacy_xls.resolve())]})
        # CDP setFileInputFiles 模拟用户选文件并原生派发 change，不得再手工 dispatch
        # （实跑证实手工补派发会导致同一文件触发两次 /api/upload POST）
        _wait_for_js(cdp, "window.__m5Alerts.length > 0", timeout=5.0)
        xls_reject_state = cdp.eval_js(
            "({alerts: window.__m5Alerts.slice(), itemCount: (window.items || []).length, inputVal: document.getElementById('xlsx-file-input').value})"
        )
        assert any(".xlsx" in a for a in xls_reject_state["alerts"]), xls_reject_state
        assert xls_reject_state["itemCount"] == 0, xls_reject_state
        assert xls_reject_state["inputVal"] == "", xls_reject_state
        # 精确 pathname 匹配，排除 /api/upload/status 轮询端点
        upload_dispatched = cdp.eval_js(
            "window.__r03Net.some(r => new URL(r.url, window.location.origin).pathname === '/api/upload')"
        )
        assert upload_dispatched is False, "xls 被客户端拒绝时不得派发 /api/upload"
        report["steps"]["client_xls_rejected"] = xls_reject_state
        report["gates"]["g_xls_client_reject"] = True

        # 4. 真实 .xlsx 上传 -> 2 行匹配渲染
        doc = cdp.call("DOM.getDocument", {"depth": -1})
        node = cdp.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#xlsx-file-input"})
        cdp.eval_js("window.__m5Alerts = []; true;")
        cdp.call("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(query_xlsx.resolve())]})
        # 同上：仅依赖 CDP 原生 change，保证一次选文件只产生一次 /api/upload POST
        try:
            _wait_for_js(cdp, "Array.isArray(items) && items.length === 2 && document.querySelectorAll('#items-tbody tr').length === 2", timeout=45.0)
        except TimeoutError:
            diag = cdp.eval_js(
                "({alerts: window.__m5Alerts, itemsLen: Array.isArray(items) ? items.length : -1, trLen: document.querySelectorAll('#items-tbody tr').length})"
            )
            raise TimeoutError(f"上传等待超时，诊断={diag}")
        initial_rows = cdp.eval_js(
            "items.map(it => ({name: it.original_name, code: it.matched_code, status: it.decision_status}))"
        )
        assert initial_rows[1]["status"] == "recommended", initial_rows
        report["steps"]["xlsx_upload_and_match"] = initial_rows
        report["gates"]["g_upload_two_rows"] = True

        # 5. 未确认即导出：真实导出按钮 -> 422、零下载、网络钩子实证
        cdp.eval_js("window.__m5Alerts = []; document.getElementById('export-btn').click(); true;")
        _wait_for_js(cdp, "window.__m5Alerts.some(a => a.includes('导出失败'))", timeout=10.0)
        blocked_alerts = cdp.eval_js("window.__m5Alerts.slice()")
        assert list(dl_dir.iterdir()) == [], f"422 阻断期间出现意外下载: {list(dl_dir.iterdir())}"
        export_recs = _net_records(cdp, "/api/export")
        assert any(r["status"] == 422 and r["method"] == "POST" for r in export_recs), export_recs
        report["steps"]["preconfirm_export_422_no_download"] = {"alerts": blocked_alerts, "net": export_recs}
        report["gates"]["g_preconfirm_export_422_zero_download"] = True

        # ---- A-M5-05 核心段 1：真实 DOM 确认 -> 三条真实编辑失效链路 -> 重新确认 ----

        # 6. 选中第 1 行，必要时经真实候选卡绑定，再点真实确认按钮
        cdp.eval_js("document.querySelectorAll('#items-tbody tr')[0].click(); true;")
        _wait_for_js(cdp, "selectedItem !== null && selectedItem.original_row_idx === items[0].original_row_idx", timeout=10.0)
        bind_guard = cdp.eval_js(
            """
            (() => {
                if (!selectedItem.bindings || selectedItem.bindings.length === 0) {
                    const card = document.querySelector('#candidates-container .candidate-card');
                    if (card) card.click();
                }
                return {bindings: (selectedItem.bindings || []).length};
            })()
            """
        )
        assert bind_guard["bindings"] >= 1, bind_guard
        cdp.eval_js("document.getElementById('confirm-match-finished-btn').click(); true;")
        _wait_for_js(cdp, "items[0].decision_status === 'manual_confirmed'", timeout=10.0)
        pill_confirmed = _pill_text(cdp, 0)
        assert "已确认" in pill_confirmed, pill_confirmed
        report["steps"]["dom_confirm_baseline"] = {"pill": pill_confirmed, "state": _item_state(cdp, 0)}
        report["gates"]["g_dom_confirm_then_pill"] = True

        # 7. 真实删除绑定按钮 -> invalidateItemDecision -> recommended / 未匹配
        # 上一步确认成功后产品会 jumpToItem 跳选下一未确认行，须先真实点回第 1 行
        cdp.eval_js("document.querySelectorAll('#items-tbody tr')[0].click(); true;")
        _wait_for_js(cdp, "selectedItem !== null && selectedItem.original_row_idx === items[0].original_row_idx", timeout=10.0)
        cdp.eval_js("document.querySelector('.btn-delete-binding').click(); true;")
        _wait_for_js(cdp, "items[0].decision_status === 'recommended' && items[0].bindings.length === 0", timeout=10.0)
        st_deleted = _item_state(cdp, 0)
        assert st_deleted["decision_source"] == "matcher", st_deleted
        assert "绑定已修改" in (st_deleted["decision_reason"] or ""), st_deleted
        assert st_deleted["is_exact"] is False, st_deleted
        pill_after_delete = _pill_text(cdp, 0)
        assert "未匹配" in pill_after_delete, pill_after_delete
        report["steps"]["dom_delete_binding_invalidates"] = {"state": st_deleted, "pill": pill_after_delete}
        report["gates"]["g_dom_delete_binding_invalidates"] = True

        # 8. 真实候选卡点击 -> applyMatchCandidate -> 重新绑定且旧确认失效
        cdp.eval_js("document.querySelector('#candidates-container .candidate-card').click(); true;")
        _wait_for_js(cdp, "items[0].bindings.length === 1 && items[0].decision_status === 'recommended'", timeout=10.0)
        st_rebind = _item_state(cdp, 0)
        assert "绑定已修改" in (st_rebind["decision_reason"] or ""), st_rebind
        assert st_rebind["is_exact"] is False, st_rebind
        report["steps"]["dom_candidate_rebind_invalidates"] = st_rebind
        report["gates"]["g_dom_candidate_rebind_invalidates"] = True

        # 9. 真实数量输入（native setter + input 事件）-> 数量 2 改 4 -> 旧确认失效
        qty_edit = cdp.eval_js(EDIT_QTY_JS)
        assert qty_edit.get("ok") is True, qty_edit
        _wait_for_js(cdp, "items[0].decision_status === 'recommended' && items[0].bindings[0].qty === 4", timeout=10.0)
        st_qty = _item_state(cdp, 0)
        assert "数量已修改" in (st_qty["decision_reason"] or ""), st_qty
        assert st_qty["is_exact"] is False, st_qty
        pill_after_qty = _pill_text(cdp, 0)
        assert "待确认" in pill_after_qty, pill_after_qty
        report["steps"]["dom_qty_edit_invalidates"] = {"state": st_qty, "pill": pill_after_qty}
        report["gates"]["g_dom_qty_edit_invalidates"] = True

        # 10. 编辑失效后导出必须 422（指纹失配，服务端不承认旧确认），零下载
        cdp.eval_js("window.__m5Alerts = []; document.getElementById('export-btn').click(); true;")
        _wait_for_js(cdp, "window.__m5Alerts.some(a => a.includes('导出失败'))", timeout=10.0)
        edit_422_alerts = cdp.eval_js("window.__m5Alerts.slice()")
        assert any("未确认" in a for a in edit_422_alerts), edit_422_alerts
        assert list(dl_dir.iterdir()) == [], f"编辑失效 422 期间出现意外下载: {list(dl_dir.iterdir())}"
        export_recs = _net_records(cdp, "/api/export")
        assert sum(1 for r in export_recs if r["status"] == 422) >= 2, export_recs
        report["steps"]["post_edit_export_422"] = {"alerts": edit_422_alerts, "net": export_recs}
        report["gates"]["g_post_edit_export_422"] = True

        # 11. 真实重新确认（数量 4 的新绑定指纹）-> manual_confirmed
        cdp.eval_js("document.querySelectorAll('#items-tbody tr')[0].click(); true;")
        _wait_for_js(cdp, "selectedItem !== null && selectedItem.original_row_idx === items[0].original_row_idx", timeout=10.0)
        cdp.eval_js("document.getElementById('confirm-match-finished-btn').click(); true;")
        _wait_for_js(cdp, "items[0].decision_status === 'manual_confirmed' && items[0].bindings[0].qty === 4", timeout=10.0)
        pill_reconfirmed = _pill_text(cdp, 0)
        assert "已确认" in pill_reconfirmed, pill_reconfirmed
        report["steps"]["dom_reconfirm_after_edit"] = {"pill": pill_reconfirmed, "state": _item_state(cdp, 0)}
        report["gates"]["g_reconfirm_after_edit"] = True

        # ---- A-M5-05 核心段 2：真实 DOM 拒绝闭环（测试不写受测状态） ----

        # 12. 注入真实拒绝按钮 DOM 节点并真实点击；监听器只调产品端点
        # 第 11 步确认成功后产品已 jumpToItem 到第 2 行，必须真实点回第 1 行再拒绝
        cdp.eval_js("document.querySelectorAll('#items-tbody tr')[0].click(); true;")
        _wait_for_js(
            cdp,
            "selectedItem !== null && selectedItem.original_row_idx === items[0].original_row_idx && (selectedItem.bindings || []).length >= 1",
            timeout=10.0,
        )
        assert cdp.eval_js(INSTALL_REJECT_BUTTON_JS) is True
        local_state_before_click = _item_state(cdp, 0)
        cdp.eval_js("window.__r03RejectResult = null; document.getElementById('r03-reject-btn').click(); true;")
        _wait_for_js(cdp, "window.__r03RejectResult && window.__r03RejectResult.ok === true", timeout=10.0)
        reject_result = cdp.eval_js("window.__r03RejectResult")
        assert reject_result["http_status"] == 200, reject_result
        assert reject_result["body"]["decision"]["decision_status"] == "rejected", reject_result
        reject_net = [r for r in _net_records(cdp, "/api/binding/reject") if r["method"] == "POST"]
        assert any(r["status"] == 200 for r in reject_net), reject_net

        # 反证：测试线束未赋值受测状态，工作台本地状态仍为点击前的 manual_confirmed；
        # 拒绝事实只存在于服务端权威决策表，须经门禁/恢复链路观测。
        local_state_after_click = _item_state(cdp, 0)
        assert local_state_after_click["decision_status"] == local_state_before_click["decision_status"], (
            local_state_before_click, local_state_after_click
        )
        assert local_state_after_click["decision_status"] == "manual_confirmed", local_state_after_click
        report["steps"]["reject_real_dom_request"] = {
            "result": reject_result,
            "net": reject_net,
            "local_state_before_click": local_state_before_click,
            "local_state_after_click_untouched": local_state_after_click,
        }
        report["gates"]["g_reject_dom_button_real_request_200"] = True
        report["gates"]["g_reject_no_test_state_assignment"] = True

        # 13. 真实导出按钮 -> 服务端行级拒绝门禁 422「人工拒绝」，零下载
        cdp.eval_js("window.__m5Alerts = []; document.getElementById('export-btn').click(); true;")
        _wait_for_js(cdp, "window.__m5Alerts.some(a => a.includes('导出失败'))", timeout=10.0)
        reject_422_alerts = cdp.eval_js("window.__m5Alerts.slice()")
        assert any("人工拒绝" in a for a in reject_422_alerts), reject_422_alerts
        assert list(dl_dir.iterdir()) == [], f"拒绝态 422 期间出现意外下载: {list(dl_dir.iterdir())}"
        export_recs = _net_records(cdp, "/api/export")
        assert any(r["status"] == 422 for r in export_recs), export_recs
        report["steps"]["reject_export_422_human_reject"] = {"alerts": reject_422_alerts, "net": export_recs}
        report["gates"]["g_reject_export_422_human_reject"] = True

        # 14. 真实保存命名会话（产品 UI 控件序列）
        cdp.eval_js(
            """
            (() => {
                document.getElementById('save-trigger').click();
                document.getElementById('save-session-name').value = 'M5_R03_E2E_Session';
                window.__m5Alerts = [];
                document.getElementById('confirm-save-session-btn').click();
                return true;
            })()
            """
        )
        _wait_for_js(cdp, "window.__m5Alerts.some(a => a.includes('进度保存成功'))", timeout=10.0)
        save_net = _net_records(cdp, "/api/session/save")
        assert any(r["method"] == "POST" and r["status"] == 200 for r in save_net), save_net
        report["steps"]["named_session_saved"] = {"net": save_net}

        # 15. 真实历史恢复：loadSession -> annotateRestoredItems -> 服务端权威改写为 rejected
        cdp.eval_js("document.getElementById('history-trigger').click(); true;")
        _wait_for_js(cdp, "document.querySelectorAll('#history-sessions-tbody .btn-restore-sess').length >= 1", timeout=10.0)
        cdp.eval_js("window.__m5Alerts = []; document.querySelector('#history-sessions-tbody .btn-restore-sess').click(); true;")
        _wait_for_js(
            cdp,
            "window.__m5Alerts.some(a => a.includes('进度已成功恢复')) && Array.isArray(items) && items.length === 2",
            timeout=15.0,
        )
        annotate_net = _net_records(cdp, "/api/session/annotate")
        assert any(r["method"] == "POST" and r["status"] == 200 for r in annotate_net), annotate_net
        st_restored = _item_state(cdp, 0)
        assert st_restored["decision_status"] == "rejected", st_restored
        assert st_restored["decision_source"] == "manual_reject", st_restored
        assert "拒绝" in (st_restored["decision_reason"] or ""), st_restored
        pill_rejected = _pill_text(cdp, 0)
        assert "已拒绝" in pill_rejected, (pill_rejected, st_restored)
        # 旧 items_json 声称的 manual_confirmed 凭据未被恢复（服务端不承认旧凭据）
        assert st_restored["decision_status"] != "manual_confirmed", st_restored
        report["steps"]["restore_server_authority_rejected"] = {
            "state": st_restored,
            "pill": pill_rejected,
            "net": annotate_net,
        }
        report["gates"]["g_save_restore_server_authority_rejected"] = True
        report["gates"]["g_rejected_pill_dom"] = True

        shot_reject = cdp.call("Page.captureScreenshot", {"format": "png"})
        (EVIDENCE_DIR / "m5_r03_ui_rejected_restored.png").write_bytes(base64.b64decode(shot_reject["data"]))

        # 16. 真实重新确认覆盖服务端拒绝（record_decision 同行 UPDATE），第 2 行同样真实确认
        cdp.eval_js("document.querySelectorAll('#items-tbody tr')[0].click(); true;")
        _wait_for_js(cdp, "selectedItem !== null && selectedItem.original_row_idx === items[0].original_row_idx", timeout=10.0)
        assert cdp.eval_js("(selectedItem.bindings || []).length") >= 1
        cdp.eval_js("document.getElementById('confirm-match-finished-btn').click(); true;")
        _wait_for_js(cdp, "items[0].decision_status === 'manual_confirmed'", timeout=10.0)
        assert "已确认" in _pill_text(cdp, 0)
        report["gates"]["g_recovery_reconfirm_overwrites_reject"] = True

        cdp.eval_js("document.querySelectorAll('#items-tbody tr')[1].click(); true;")
        _wait_for_js(cdp, "selectedItem !== null && selectedItem.original_row_idx === items[1].original_row_idx", timeout=10.0)
        cdp.eval_js(
            """
            (() => {
                if (!selectedItem.bindings || selectedItem.bindings.length === 0) {
                    const card = document.querySelector('#candidates-container .candidate-card');
                    if (card) card.click();
                }
                return {bindings: (selectedItem.bindings || []).length};
            })()
            """
        )
        cdp.eval_js("document.getElementById('confirm-match-finished-btn').click(); true;")
        _wait_for_js(cdp, "items[1].decision_status === 'manual_confirmed'", timeout=10.0)
        assert "已确认" in _pill_text(cdp, 1)
        report["gates"]["g_row2_confirm"] = True
        report["steps"]["both_rows_confirmed"] = [_item_state(cdp, 0), _item_state(cdp, 1)]

        shot_wb = cdp.call("Page.captureScreenshot", {"format": "png"})
        (EVIDENCE_DIR / "m5_r03_ui_workbench.png").write_bytes(base64.b64decode(shot_wb["data"]))

        # 17. API 级补充：legacy 伪凭据经 annotate 必须降级（不替代 DOM 链路）
        # 此时两行均持有真实确认记录，故伪 legacy 草稿必须篡改绑定（qty=99）制造
        # 绑定指纹失配：服务端 get_decision 不命中 -> 不得升级为可导出，统一降 recommended。
        legacy_downgrade = cdp.eval_js(
            """
            (async () => {
                const fakeLegacy = items.map(it => ({
                    ...it,
                    is_exact: true,
                    decision_status: 'auto_selected',
                    decision_source: 'legacy',
                    bindings: (it.bindings || []).map(b => ({...b, qty: 99}))
                }));
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
        assert all(it["decision_status"] == "recommended" for it in legacy_downgrade["items"]), legacy_downgrade
        report["steps"]["legacy_annotate_downgrade_supplement"] = {
            "downgraded": legacy_downgrade["downgraded"],
            "restored_statuses": [it["decision_status"] for it in legacy_downgrade["items"]],
        }
        report["gates"]["g_legacy_downgrade"] = True

        # 18. 真实导出 200 -> 真实 XLSX 下载 -> SHA256 + openpyxl 字段/单位/数量重读
        cdp.eval_js("window.__m5Alerts = []; document.getElementById('export-btn').click(); true;")
        dl_deadline = time.time() + 20.0
        exported_file = None
        while time.time() < dl_deadline:
            ready = [p for p in dl_dir.glob("*.xlsx") if not p.name.endswith(".crdownload")]
            if ready and ready[0].stat().st_size > 0:
                exported_file = ready[0]
                break
            time.sleep(0.25)
        assert exported_file is not None, f"预期 200 下载 .xlsx，实际: {list(dl_dir.iterdir())}"
        export_recs = _net_records(cdp, "/api/export")
        assert any(r["status"] == 200 and r["method"] == "POST" for r in export_recs), export_recs
        report["gates"]["g_export_200_download"] = True

        export_sha = _sha256(exported_file)
        wb_exp = openpyxl.load_workbook(exported_file, data_only=True)
        ws_exp = wb_exp["模板"] if "模板" in wb_exp.sheetnames else wb_exp.active
        all_rows = [[ws_exp.cell(r, c).value for c in range(1, 17)] for r in range(1, ws_exp.max_row + 1)]
        wb_exp.close()

        # 列位：5=清单编码 6=清单名称 7=规格 8=单位 9=工程量
        bind_row_idx = next(i for i, row in enumerate(all_rows) if str(row[4]).strip() == "M5-UI-001")
        bind_row = all_rows[bind_row_idx]
        original_row = all_rows[bind_row_idx - 1]
        assert str(original_row[5]).strip() == "止回阀", original_row
        assert "DN100" in str(original_row[6]), original_row
        assert str(original_row[7]).strip() == "台", original_row
        assert original_row[8] == 2, original_row
        assert str(bind_row[7]).strip() == "台", bind_row
        # DOM 数量编辑 2->4 贯穿确认/拒绝/保存恢复/重新确认/导出全链路
        assert bind_row[8] == 4, bind_row

        ex_rows = [i for i, row in enumerate(all_rows) if str(row[4] or "").strip().startswith("3.50.29.02")]
        assert ex_rows, all_rows
        ex_bind = all_rows[ex_rows[0]]
        ex_original = all_rows[ex_rows[0] - 1]
        assert "防爆检修电源箱" in str(ex_original[5]), ex_original
        assert ex_original[8] == 1, ex_original
        assert ex_bind[8] == 1, ex_bind
        report["steps"]["export_xlsx_reread"] = {
            "filename": exported_file.name,
            "size_bytes": exported_file.stat().st_size,
            "sha256": export_sha,
            "check_valve_original_row": original_row[:11],
            "check_valve_binding_row": bind_row[:11],
            "exproof_original_row": ex_original[:11],
            "exproof_binding_row": ex_bind[:11],
        }
        report["gates"]["g_export_xlsx_field_unit_qty_reread"] = True

        shot_done = cdp.call("Page.captureScreenshot", {"format": "png"})
        (EVIDENCE_DIR / "m5_r03_ui_export_done.png").write_bytes(base64.b64decode(shot_done["data"]))

        # 19. 员工 403 / admin 200 health（API 级补充）
        emp_health_status = cdp.eval_js("(async () => (await fetch('/api/admin/health')).status)()")
        assert emp_health_status == 403, emp_health_status

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
            {"username": "admin", "password": "M5R03UiAdmin#2026!"},
        )
        assert st_l == 200 and login_d.get("access_token"), (st_l, login_d)
        st_h, health_d = _req_json(
            f"{base_url}/api/admin/health",
            headers={"Authorization": f"Bearer {login_d['access_token']}"},
        )
        assert st_h == 200 and health_d["status"] == "healthy", (st_h, health_d)
        assert health_d["metrics_available"] is True and health_d["hardware"]["memory"]["total_gb"] > 0, health_d
        report["steps"]["admin_health_live"] = {
            "employee_forbidden_status": emp_health_status,
            "status": health_d["status"],
            "metrics_available": health_d["metrics_available"],
        }
        report["gates"]["g_admin_health"] = True

        # 20. 网络钩子完整性汇总
        net_summary = cdp.eval_js(
            """
            (() => ({
                reject_calls: window.__r03Net.filter(r => r.url.indexOf('/api/binding/reject') !== -1),
                export_calls: window.__r03Net.filter(r => r.url.indexOf('/api/export') !== -1),
                annotate_calls: window.__r03Net.filter(r => r.url.indexOf('/api/session/annotate') !== -1),
                upload_calls: window.__r03Net.filter(r => new URL(r.url, window.location.origin).pathname === '/api/upload'),
                upload_status_polls: window.__r03Net.filter(r => new URL(r.url, window.location.origin).pathname === '/api/upload/status')
            }))()
            """
        )
        assert len(net_summary["reject_calls"]) == 1 and net_summary["reject_calls"][0]["status"] == 200, net_summary
        assert [r["status"] for r in net_summary["export_calls"]].count(422) == 3, net_summary
        assert any(r["status"] == 200 for r in net_summary["export_calls"]), net_summary
        assert all(r["status"] == 200 for r in net_summary["annotate_calls"]), net_summary
        assert len(net_summary["upload_calls"]) == 1 and net_summary["upload_calls"][0]["status"] == 200, net_summary
        report["steps"]["network_hook_summary"] = net_summary
        report["gates"]["g_network_hook_completeness"] = True

        # 21. R02 原件未覆写复核（脚本 + 报告 + 3 张截图）
        r02_hashes_after = {
            "m5_r02_browser_e2e.py": _sha256(R02_SCRIPT),
            "m5_r02_browser_e2e_report.json": _sha256(R02_REPORT),
            "m5_r02_ui_workbench.png": _sha256(R02_SHOT_WORKBENCH),
            "m5_r02_ui_rejected_restored.png": _sha256(R02_SHOT_REJECTED),
            "m5_r02_ui_export_done.png": _sha256(R02_SHOT_EXPORT),
        }
        assert r02_hashes_after == r02_hashes_before, (r02_hashes_before, r02_hashes_after)
        report["r02_evidence_preserved"] = {"before": r02_hashes_before, "after": r02_hashes_after, "overwritten": False}
        report["gates"]["g_r02_evidence_untouched"] = True

        assert all(report["gates"].values()), report["gates"]
        report["passed"] = True
        report["elapsed_seconds"] = round(time.time() - t0, 3)
        R03_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("M5_R03_UI_E2E_PASS:", json.dumps({"gates": report["gates"], "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False))
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
        report["temp_context_cleanup_verified"] = not tmp_root.exists()
        if report.get("passed"):
            R03_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    run_browser_e2e()
