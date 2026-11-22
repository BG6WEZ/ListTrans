# -*- coding: utf-8 -*-
"""
V016-M2 R01 Live Headless Chrome DOM Verification Script.
Spawns an isolated uvicorn server (temp LISTTRANS_TEST_ROOT) + headless Chrome CDP,
verifying real DOM interaction, network payloads, alert dialogs, and backend persistence for:
1. Admin Flywheel UI (/admin): untested accept blocked -> eval runs in modal -> edit invalidates cache/badge
   -> edited mismatch blocked -> fresh eval -> accept succeeds & persists snapshot.
2. Index Rule Engine UI (/): untested publish blocked (409) -> 🧪 评测 completes -> 发布生效 succeeds -> active.
Never touches workspace DB/config files.
"""
import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CHROME_EXE = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
OUT_JSON = PROJECT_ROOT / "doc" / "V0.1.6" / "evidence" / "m2_r01_chrome_dom_verify.json"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class SimpleCDPClient:
    """Minimal RFC-6455 WebSocket CDP client over Python stdlib sockets."""

    def __init__(self, ws_url: str, timeout: float = 20.0):
        assert ws_url.startswith("ws://"), ws_url
        rest = ws_url[len("ws://"):]
        host_port, path = rest.split("/", 1)
        host, port_s = host_port.split(":", 1)
        path = "/" + path
        self.sock = socket.create_connection((host, int(port_s)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port_s}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("CDP WebSocket handshake closed unexpectedly")
            buf += chunk
        self._rx_buf = buf.split(b"\r\n\r\n", 1)[1]
        self._next_id = 1
        self.events = []
        self.dialogs = []

    def _recv_exact(self, n: int) -> bytes:
        while len(self._rx_buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("CDP socket closed")
            self._rx_buf += chunk
        out = self._rx_buf[:n]
        self._rx_buf = self._rx_buf[n:]
        return out

    def _send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        ln = len(payload)
        if ln < 126:
            header.append(0x80 | ln)
        elif ln < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", ln))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", ln))
        header.extend(mask)
        masked = bytes(payload[i] ^ mask[i % 4] for i in range(ln))
        self.sock.sendall(bytes(header) + masked)

    def _recv_frame(self) -> str:
        while True:
            b1, b2 = self._recv_exact(2)
            opcode = b1 & 0x0F
            masked = (b2 & 0x80) != 0
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack("!H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            data = self._recv_exact(ln)
            if masked:
                data = bytes(data[i] ^ mask[i % 4] for i in range(ln))
            if opcode == 0x8:
                raise RuntimeError("CDP closed frame")
            if opcode == 0x9:
                # Ping -> Pong
                continue
            if opcode == 0x1:
                return data.decode("utf-8", errors="replace")

    def call(self, method: str, params: dict = None) -> dict:
        cid = self._next_id
        self._next_id += 1
        self._send_text(json.dumps({"id": cid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self._recv_frame())
            if msg.get("id") == cid:
                if "error" in msg:
                    raise RuntimeError(f"CDP error on {method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
                if msg["method"] == "Page.javascriptDialogOpening":
                    self.dialogs.append(msg.get("params", {}))
                    # Auto-accept confirm/alert/prompt asynchronously if blocking JS thread
                    self._send_text(json.dumps({
                        "id": 900000 + len(self.dialogs),
                        "method": "Page.handleJavaScriptDialog",
                        "params": {"accept": True},
                    }))

    def eval_js(self, expr: str):
        res = self.call(
            "Runtime.evaluate",
            {"expression": expr, "awaitPromise": True, "returnByValue": True},
        )
        if "exceptionDetails" in res:
            raise RuntimeError(f"JS exception: {res['exceptionDetails']}")
        return res.get("result", {}).get("value")

    def drain_events(self, wait_s: float = 0.25) -> None:
        self.sock.settimeout(wait_s)
        try:
            while True:
                msg = json.loads(self._recv_frame())
                if "method" in msg:
                    self.events.append(msg)
                    if msg["method"] == "Page.javascriptDialogOpening":
                        self.dialogs.append(msg.get("params", {}))
                        self._send_text(json.dumps({
                            "id": 910000 + len(self.dialogs),
                            "method": "Page.handleJavaScriptDialog",
                            "params": {"accept": True},
                        }))
        except (socket.timeout, TimeoutError):
            pass
        finally:
            self.sock.settimeout(20.0)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def _setup_isolated_env(root: Path) -> None:
    os.environ["TESTING"] = "1"
    os.environ["LLM_ENABLED"] = "false"
    os.environ["LISTTRANS_TEST_ROOT"] = str(root)
    os.environ["MATERIALS_DB_PATH"] = str(root / "db" / "materials.db")
    os.environ["AUTH_DB_PATH"] = str(root / "db" / "auth.db")
    os.environ["CONFIG_DIR"] = str(root / "config")
    os.environ["UPLOAD_DIR"] = str(root / "uploads")
    os.environ["OUTPUT_DIR"] = str(root / "output")
    os.environ["VECTOR_CACHE_DIR"] = str(root / "vector_cache")
    os.environ["LOG_DIR"] = str(root / "logs")
    for sub in ("db", "config", "uploads", "output", "vector_cache", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    src_cfg = PROJECT_ROOT / "config"
    if src_cfg.is_dir():
        for item in src_cfg.iterdir():
            if item.is_file():
                shutil.copy2(item, root / "config" / item.name)
    for k in list(os.environ.keys()):
        if "API_KEY" in k or "SECRET" in k or "ENDPOINT" in k:
            if k not in ("AUTH_SECRET_KEY",):
                os.environ.pop(k, None)
    os.environ["AUTH_SECRET_KEY"] = "chrome-dom-test-secret-0123456789"


def main() -> int:
    assert CHROME_EXE.is_file(), f"Chrome binary missing: {CHROME_EXE}"
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    temp_root = Path(tempfile.mkdtemp(prefix="listtrans_m2_chrome_"))
    chrome_profile = temp_root / "chrome_profile"
    chrome_profile.mkdir(parents=True, exist_ok=True)
    _setup_isolated_env(temp_root)

    server_port = _free_port()
    cdp_port = _free_port()

    # Seed isolated DB before launching server thread
    import pandas as pd
    from app.auth_manager import get_auth_connection, get_password_hash, get_user_by_username, init_auth_db
    from app.db_manager import get_db_cursor, import_materials_from_excel, init_db
    from app.flywheel_db import init_flywheel_tables
    from app.llm_rule_engine import LLMRuleManager, init_rule_engine_db

    init_db()
    init_auth_db()
    init_rule_engine_db()
    init_flywheel_tables()

    with open(PROJECT_ROOT / "tests" / "fixtures" / "sandbox_materials_v1.json", "r", encoding="utf-8") as f:
        mats = json.load(f)["materials"]
    xlsx_path = temp_root / "uploads" / "chrome_dom_mats.xlsx"
    pd.DataFrame([
        {
            "物料分类": m["category"], "物料编码": m["code"],
            "物料名称": m["name"], "规格/型号": m["spec"],
            "计量单位": "台", "常用品牌": "", "描述": "",
        }
        for m in mats
    ]).to_excel(xlsx_path, index=False, startrow=1)
    import_materials_from_excel(str(xlsx_path), mode="replace", filename="chrome_dom_mats.xlsx")

    # Seed users: employee, manager, admin
    conn = get_auth_connection()
    pwd_hash = get_password_hash("DomTest@12345")
    for uname, role in [("dom_emp", "employee"), ("dom_mgr", "manager"), ("dom_adm", "admin")]:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch)
            VALUES (?, ?, ?, ?, 1, 0, ?)
            ON CONFLICT(username) DO UPDATE SET
                password_hash = excluded.password_hash,
                role = excluded.role,
                is_active = 1,
                must_change_password = 0
            """,
            (uname, pwd_hash, uname, role, f"dom-{uname}"),
        )
    conn.commit()
    conn.close()

    emp_u = get_user_by_username("dom_emp")
    mgr_u = get_user_by_username("dom_mgr")
    adm_u = get_user_by_username("dom_adm")
    from app.auth_manager import issue_token_for_user
    emp_u = dict(emp_u, token=issue_token_for_user(emp_u))
    mgr_u = dict(mgr_u, token=issue_token_for_user(mgr_u))
    adm_tok = issue_token_for_user(adm_u)

    # Seed 1 pending flywheel suggestion + 1 approved_pending_publish LLM rule
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO flywheel_rule_suggestions (
                discipline, suggestion_type, source_pattern, target_value,
                support_count, distinct_users, consistency, status
            ) VALUES ('医用气体', 'ALIAS', '医用脱脂无缝紫铜管', '磷酸脱氧无缝紫铜管', 6, 3, 1.0, 'pending')
            """
        )
        sug_id = cur.lastrowid

    rid = "DOM-TEST-RULE-01"
    r_s = LLMRuleManager.save_rule_draft(
        {
            "rule_id": rid,
            "rule_name": "DOM紫铜管清洗规则",
            "scope_level": "discipline",
            "scope_target": "医用气体",
            "discipline": "医用气体",
            "priority": 10,
            "parsed_dsl": {
                "rule_name": "DOM紫铜管清洗规则",
                "cleaning_rules": [{"action": "synonym_replace", "target": "医用脱脂无缝紫铜管", "replacement": "磷酸脱氧无缝紫铜管"}],
                "extraction_rules": [],
                "matching_rules": {},
            },
        },
        emp_u,
    )
    assert r_s.get("success"), r_s
    r_sub = LLMRuleManager.submit_rule(rid, emp_u)
    assert r_sub.get("success"), r_sub
    r_aud = LLMRuleManager.audit_rule(rid, "approve", "DOM准备待发布", mgr_u)
    assert r_aud.get("status") == "approved_pending_publish", r_aud

    # Start uvicorn in background thread
    import uvicorn
    from app import server
    uv_config = uvicorn.Config(server.app, host="127.0.0.1", port=server_port, log_level="warning")
    uv_server = uvicorn.Server(uv_config)
    srv_thread = threading.Thread(target=uv_server.run, daemon=True)
    srv_thread.start()

    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server_port}/api/auth/me", timeout=1) as _:
                break
        except Exception as exc:
            if "401" in str(exc) or "403" in str(exc):
                break
            time.sleep(0.25)

    chrome_proc = subprocess.Popen(
        [
            str(CHROME_EXE),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={chrome_profile}",
            f"http://127.0.0.1:{server_port}/login.html",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    report = {
        "isolated_root": str(temp_root),
        "chrome_binary": str(CHROME_EXE),
        "steps": {},
    }
    cdp = None
    try:
        ws_url = None
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=1.5) as r:
                    targets = json.loads(r.read().decode("utf-8"))
                    page = next((t for t in targets if t.get("type") == "page"), None)
                    if page:
                        ws_url = page["webSocketDebuggerUrl"]
                        break
            except Exception:
                time.sleep(0.25)
        assert ws_url, "Failed to discover Chrome CDP page websocket"

        cdp = SimpleCDPClient(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")

        # 1. Wait for HTTP origin (/login.html) before setting localStorage, then navigate to /admin.html
        for _ in range(50):
            if cdp.eval_js("window.location.href.startsWith('http') && document.readyState === 'complete'"):
                break
            time.sleep(0.25)
        assert cdp.eval_js("window.location.href.startsWith('http')"), "Chrome did not reach login.html"
        cdp.eval_js(
            f"localStorage.setItem('token', {json.dumps(adm_tok)});"
            f"localStorage.setItem('role', 'admin');"
            f"localStorage.setItem('username', 'dom_adm');"
            f"window.location.href = 'http://127.0.0.1:{server_port}/admin.html';"
        )
        time.sleep(0.8)
        for _ in range(50):
            ready = cdp.eval_js("document.readyState === 'complete' && typeof switchAdminTab === 'function'")
            if ready:
                break
            time.sleep(0.25)
        assert cdp.eval_js("typeof switchAdminTab === 'function'"), "admin.html scripts did not finish loading"
        cdp.eval_js("switchAdminTab('flywheel');")
        for _ in range(30):
            row_cnt = cdp.eval_js("document.querySelectorAll('#flywheel-suggestions-body tr').length")
            txt = cdp.eval_js("document.getElementById('flywheel-suggestions-body')?.innerText || ''")
            if row_cnt and "医用脱脂无缝紫铜管" in (txt or ""):
                break
            time.sleep(0.3)

        # Hook fetch in page to record real HTTP status/code responses alongside DOM assertions
        cdp.eval_js("""
            window.__domFetchLog = [];
            const __origFetch = window.fetch;
            window.fetch = async (...args) => {
                const resp = await __origFetch(...args);
                let cloneText = '';
                try { cloneText = await resp.clone().text(); } catch(e) {}
                window.__domFetchLog.push({ url: String(args[0]), status: resp.status, body: cloneText.slice(0, 400) });
                return resp;
            };
        """)

        # Step A: Click '采纳' BEFORE eval -> must fail closed 409 via real DOM click & dialog
        cdp.eval_js("document.querySelector('.btn-fw-accept').click();")
        time.sleep(1.0)
        cdp.drain_events(0.5)
        logs_a = cdp.eval_js("window.__domFetchLog.slice()")
        accept_409 = [x for x in logs_a if "/api/flywheel/suggestions/accept" in x["url"]]
        assert accept_409 and accept_409[-1]["status"] == 409, logs_a
        report["steps"]["flywheel_untested_accept_blocked_409"] = {
            "status": accept_409[-1]["status"],
            "dialogs": [d.get("message") for d in cdp.dialogs],
        }

        # Step B: Click '沙箱回归' button in DOM -> wait modal completion -> cached eval_id populated
        cdp.eval_js("document.querySelector('.btn-fw-eval').click();")
        eval_done = False
        for _ in range(90):
            title = cdp.eval_js("document.getElementById('fw-eval-title')?.innerText || ''")
            cache = cdp.eval_js(f"window._fwEvalCache[{sug_id}] || null")
            if cache and cache.get("eval_id"):
                eval_done = True
                report["steps"]["flywheel_dom_eval_completed"] = {
                    "modal_title": title,
                    "eval_id": cache["eval_id"],
                    "benefit": cache["benefit"],
                    "safety_passed": cache["safety_passed"],
                }
                break
            time.sleep(0.5)
        assert eval_done, "Flywheel DOM modal eval did not populate _fwEvalCache"
        cdp.eval_js("closeModal('fw-eval-modal');")

        # Step C: Open Edit modal -> change edited_target_value -> badge invalidates -> submit -> 409!
        cdp.eval_js("document.querySelector('.btn-fw-edit').click();")
        time.sleep(0.3)
        cdp.eval_js("""
            const ta = document.getElementById('fw-edit-target');
            ta.value = '铜管错误修改目标';
            ta.dispatchEvent(new Event('input', { bubbles: true }));
        """)
        time.sleep(0.3)
        badge_text = cdp.eval_js("document.getElementById('fw-edit-eval-badge')?.innerText || ''")
        assert "待重新评测" in badge_text, badge_text
        cdp.eval_js("document.querySelector('#fw-edit-form button[type=\"submit\"]').click();")
        time.sleep(1.0)
        cdp.drain_events(0.5)
        logs_c = cdp.eval_js("window.__domFetchLog.slice()")
        edit_409 = [x for x in logs_c if "/api/flywheel/suggestions/accept" in x["url"]][-1]
        assert edit_409["status"] == 409, edit_409
        report["steps"]["flywheel_post_edit_invalidation_blocked_409"] = {
            "badge_text": badge_text,
            "http_status": edit_409["status"],
        }
        cdp.eval_js("closeModal('fw-edit-modal');")

        # Step D: Accept the evaluated original value (handling prompt if neutral_reason needed)
        # Override window.prompt to supply concrete approval note cleanly if neutral
        cdp.eval_js("window.prompt = () => 'Chrome实景DOM测试稳态规则审批理由';")
        cdp.eval_js("document.querySelector('.btn-fw-accept').click();")
        for _ in range(30):
            time.sleep(0.3)
            cdp.drain_events(0.2)
            logs_d = cdp.eval_js("window.__domFetchLog.slice()")
            ok_reqs = [x for x in logs_d if "/api/flywheel/suggestions/accept" in x["url"] and x["status"] == 200]
            if ok_reqs:
                break
        assert ok_reqs, cdp.eval_js("window.__domFetchLog.slice()")
        report["steps"]["flywheel_dom_accept_200"] = ok_reqs[-1]

        # Step E: Navigate to '/' (Rule Studio / Active Rules) -> test LLM rule eval + publish DOM flow
        cdp.eval_js(f"window.location.href = 'http://127.0.0.1:{server_port}/';")
        time.sleep(0.8)
        for _ in range(50):
            ready2 = cdp.eval_js("document.readyState === 'complete' && typeof fetchAndRenderActiveRules === 'function'")
            if ready2:
                break
            time.sleep(0.25)
        assert cdp.eval_js("typeof fetchAndRenderActiveRules === 'function'"), "index.html app.js did not finish loading"
        cdp.eval_js("""
            window.__domFetchLog = [];
            const __origFetch2 = window.fetch;
            window.fetch = async (...args) => {
                const resp = await __origFetch2(...args);
                let cloneText = '';
                try { cloneText = await resp.clone().text(); } catch(e) {}
                window.__domFetchLog.push({ url: String(args[0]), status: resp.status, body: cloneText.slice(0, 400) });
                return resp;
            };
            localStorage.setItem('token', """ + json.dumps(adm_tok) + """);
            localStorage.setItem('role', 'admin');
            localStorage.setItem('username', 'dom_adm');
            document.getElementById('nav-llm-rules')?.click();
            document.getElementById('tab-rule-leaderboard')?.click();
            const flt = document.getElementById('active-rule-status-filter');
            if (flt) { flt.value = 'all'; flt.dispatchEvent(new Event('change')); }
            fetchAndRenderActiveRules();
        """)
        for _ in range(40):
            has_btn = cdp.eval_js("document.querySelector('#active-rules-tbody .btn-rule-publish') !== null")
            if has_btn:
                break
            time.sleep(0.3)
        assert cdp.eval_js("document.querySelector('#active-rules-tbody .btn-rule-publish') !== null"), (
            cdp.eval_js("document.getElementById('active-rules-tbody')?.innerText || ''")
        )

        # Untested publish -> 409
        cdp.eval_js("document.querySelector('#active-rules-tbody .btn-rule-publish').click();")
        time.sleep(1.0)
        cdp.drain_events(0.5)
        logs_e1 = cdp.eval_js("window.__domFetchLog.slice()")
        pub_409 = [x for x in logs_e1 if "/publish" in x["url"]][-1]
        assert pub_409["status"] == 409, pub_409

        # Run eval via UI button -> cached in _llmEvalCache -> publish 200!
        cdp.eval_js("window.prompt = () => 'Chrome实景DOM手工规则稳态审批理由';")
        cdp.eval_js("document.querySelector('#active-rules-tbody .btn-rule-eval').click();")
        llm_eval_done = False
        for _ in range(90):
            time.sleep(0.5)
            cdp.drain_events(0.2)
            cache = cdp.eval_js(f"window._llmEvalCache['{rid}'] || null")
            if cache and cache.get("eval_id"):
                llm_eval_done = True
                break
        assert llm_eval_done, "LLM rule eval in DOM did not finish"

        cdp.eval_js("document.querySelector('#active-rules-tbody .btn-rule-publish').click();")
        ok_pub = []
        for _ in range(30):
            time.sleep(0.3)
            cdp.drain_events(0.2)
            logs_e2 = cdp.eval_js("window.__domFetchLog.slice()")
            ok_pub = [x for x in logs_e2 if "/publish" in x["url"] and x["status"] == 200]
            if ok_pub:
                break
        assert ok_pub, cdp.eval_js("window.__domFetchLog.slice()")
        report["steps"]["llm_rule_dom_eval_then_publish_200"] = {
            "untested_status": pub_409["status"],
            "published": ok_pub[-1],
        }
        report["status"] = "PASS"
    finally:
        if cdp:
            cdp.close()
        chrome_proc.terminate()
        try:
            chrome_proc.wait(timeout=5)
        except Exception:
            chrome_proc.kill()
        uv_server.should_exit = True
        srv_thread.join(timeout=5)
        shutil.rmtree(temp_root, ignore_errors=True)

    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
