# -*- coding: utf-8 -*-
"""V015-M4 / R01 real headless Chrome CDP E2E browser verification against isolated uvicorn."""

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
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "doc" / "V0.1.5" / "evidence"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_get_json(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_status(url: str, headers: dict = None) -> int:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


class MiniCDPClient:
    """Minimal stdlib RFC 6455 WebSocket client for Chrome DevTools Protocol."""

    def __init__(self, ws_url: str):
        assert ws_url.startswith("ws://")
        rest = ws_url[5:]
        host_port, path = rest.split("/", 1)
        host, port_s = host_port.split(":")
        self.sock = socket.create_connection((host, int(port_s)), timeout=15.0)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {host_port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket handshake failed")
            buf += chunk
        head, self._extra = buf.split(b"\r\n\r\n", 1)
        if b"101" not in head.splitlines()[0]:
            raise RuntimeError(f"WebSocket upgrade rejected: {head!r}")
        self._msg_id = 0

    def _recv_exact(self, n: int) -> bytes:
        out = bytearray()
        if self._extra:
            take = min(n, len(self._extra))
            out.extend(self._extra[:take])
            self._extra = self._extra[take:]
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise RuntimeError("WebSocket closed")
            out.extend(chunk)
        return bytes(out)

    def _send_text(self, text: str):
        data = text.encode("utf-8")
        header = bytearray([0x81])
        mask_bit = 0x80
        length = len(data)
        if length < 126:
            header.append(mask_bit | length)
        elif length < 65536:
            header.append(mask_bit | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(mask_bit | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _recv_frame(self) -> str:
        chunks = bytearray()
        while True:
            b1, b2 = self._recv_exact(2)
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            payload = self._recv_exact(length)
            if opcode == 0x8:
                raise RuntimeError("WebSocket closed by Chrome")
            if opcode == 0x9:
                pong = bytearray([0x8A, 0x80 | len(payload)]) + os.urandom(4)
                self.sock.sendall(bytes(pong))
                continue
            if opcode in (0x1, 0x0):
                chunks.extend(payload)
                if fin:
                    return chunks.decode("utf-8", errors="replace")

    def call(self, method: str, params: dict = None):
        self._msg_id += 1
        cid = self._msg_id
        self._send_text(json.dumps({"id": cid, "method": method, "params": params or {}}))
        while True:
            raw = self._recv_frame()
            msg = json.loads(raw)
            if msg.get("id") == cid:
                if "error" in msg:
                    raise RuntimeError(f"CDP error for {method}: {msg['error']}")
                return msg.get("result", {})

    def eval_js(self, expr: str):
        res = self.call(
            "Runtime.evaluate",
            {"expression": expr, "awaitPromise": True, "returnByValue": True},
        )
        if "exceptionDetails" in res:
            raise RuntimeError(f"JS exception: {res['exceptionDetails']}")
        return res.get("result", {}).get("value")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def _wait_for_js(cdp: MiniCDPClient, expr: str, timeout: float = 10.0):
    deadline = time.time() + timeout
    last_val = None
    while time.time() < deadline:
        try:
            last_val = cdp.eval_js(expr)
            if last_val:
                return last_val
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for JS condition: {expr} (last={last_val!r})")


def run_browser_e2e():
    if not os.path.isfile(CHROME_PATH):
        raise FileNotFoundError(f"Chrome binary missing: {CHROME_PATH}")

    tmp_root = Path(tempfile.mkdtemp(prefix="m4_browser_e2e_"))
    server_proc = None
    chrome_proc = None
    cdp = None

    try:
        db_dir = tmp_root / "db"
        cfg_dir = tmp_root / "config"
        up_dir = tmp_root / "uploads"
        out_dir = tmp_root / "outputs"
        log_dir = tmp_root / "logs"
        chrome_profile = tmp_root / "chrome_profile"
        for d in (db_dir, up_dir, out_dir, log_dir, chrome_profile):
            d.mkdir(parents=True, exist_ok=True)
        shutil.copytree(PROJECT_ROOT / "config", cfg_dir)

        env = os.environ.copy()
        env["TESTING"] = "1"
        env["LLM_ENABLED"] = "false"
        env["AUTH_DEV_MODE"] = "false"
        env["MATERIALS_DB_PATH"] = str(db_dir / "materials.db")
        env["AUTH_DB_PATH"] = str(db_dir / "auth.db")
        env["CONFIG_DIR"] = str(cfg_dir)
        env["UPLOAD_DIR"] = str(up_dir)
        env["OUTPUT_DIR"] = str(out_dir)
        env["LOG_DIR"] = str(log_dir)
        env["SECRET_KEY"] = "M4_Browser_E2E_Test_Secret_Key_2026_XYZ123"
        env["PYTHONPATH"] = str(PROJECT_ROOT)

        # Seed isolated DB accounts & sample pending_publish rule in subprocess cleanly
        seed_code = """
import json
from app.db_manager import init_db
from app.audit_manager import init_audit_tables
from app.session_manager import init_session_db
from app.flywheel_db import init_flywheel_tables
from app.auth_manager import init_auth_db, admin_create_user_atomic, get_user_by_username
from app.llm_rule_engine import init_rule_engine_db, LLMRuleManager

init_db()
init_auth_db()
init_audit_tables()
init_session_db()
init_flywheel_tables()
init_rule_engine_db()

emp = admin_create_user_atomic('br_emp', role='employee', name='浏览器员工', employee_no='BR-E01')
mgr = admin_create_user_atomic('br_mgr', role='manager', name='浏览器经理', employee_no='BR-M01')
assert emp['success'] and mgr['success']

emp_u = get_user_by_username('br_emp')
mgr_u = get_user_by_username('br_mgr')
LLMRuleManager.save_rule_draft({
    'rule_id': 'M4-BR-RULE-1',
    'rule_name': '浏览器权限提示验证规则',
    'scope_level': 'universal',
    'scope_target': '',
    'discipline': '暖通',
    'priority': 10,
    'parsed_dsl': {'matching_rules': {'must_not_contain': ['BR_TEST_BAD']}}
}, emp_u)
LLMRuleManager.submit_rule('M4-BR-RULE-1', emp_u)
LLMRuleManager.audit_rule('M4-BR-RULE-1', 'approve', 'approve for ui check', mgr_u)
print(json.dumps({'temp_pwd': emp['temp_password']}))
"""
        seed_out = subprocess.check_output(
            [sys.executable, "-c", seed_code],
            cwd=str(PROJECT_ROOT),
            env=env,
            text=True,
            timeout=30,
        )
        temp_pwd = json.loads(seed_out.strip().splitlines()[-1])["temp_pwd"]

        app_port = _free_port()
        cdp_port = _free_port()
        server_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(app_port),
                "--workers",
                "1",
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        base_url = f"http://127.0.0.1:{app_port}"
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if _http_status(f"{base_url}/login.html") == 200:
                break
            time.sleep(0.3)
        else:
            raise TimeoutError("Isolated uvicorn server did not become ready in time")

        chrome_proc = subprocess.Popen(
            [
                CHROME_PATH,
                "--headless=new",
                f"--remote-debugging-port={cdp_port}",
                f"--user-data-dir={chrome_profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
                "--window-size=1400,900",
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
                page_targets = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
                if page_targets:
                    ws_url = page_targets[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.25)
        if not ws_url:
            raise TimeoutError("Chrome CDP websocket did not appear")

        cdp = MiniCDPClient(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")

        report = {
            "server_port": app_port,
            "steps": {},
        }

        # Step 1: Open login page & verify no weak default hint
        cdp.call("Page.navigate", {"url": f"{base_url}/login.html"})
        _wait_for_js(cdp, "document.readyState === 'complete' && Boolean(document.querySelector('input[type=\"password\"]'))")
        login_html_text = cdp.eval_js("document.body.innerText || ''")
        assert "hkjj@12345" not in login_html_text
        report["steps"]["login_page_clean"] = True

        # Step 2: Login with server-generated temp password
        cdp.eval_js(
            f"""
            (() => {{
                const u = document.querySelector('#username') || document.querySelector('input[type="text"]');
                const p = document.querySelector('#password') || document.querySelector('input[type="password"]');
                u.value = 'br_emp';
                u.dispatchEvent(new Event('input', {{bubbles: true}}));
                p.value = {json.dumps(temp_pwd)};
                p.dispatchEvent(new Event('input', {{bubbles: true}}));
                const btn = document.querySelector('#loginBtn') || document.querySelector('button[type="submit"]') || document.querySelector('button');
                btn.click();
                return true;
            }})()
            """
        )
        _wait_for_js(cdp, "window.location.pathname.endsWith('/index.html') || window.location.pathname === '/'", timeout=15.0)
        _wait_for_js(cdp, "Boolean(document.getElementById('account-settings-modal') && document.getElementById('forced-password-banner'))")
        _wait_for_js(cdp, "getComputedStyle(document.getElementById('account-settings-modal')).display !== 'none'", timeout=10.0)

        # Step 3: Verify forced-password-banner visible + modal blocks closing via close/cancel buttons
        banner_visible = cdp.eval_js("getComputedStyle(document.getElementById('forced-password-banner')).display !== 'none'")
        cdp.eval_js("document.getElementById('close-account-modal-btn').click(); document.getElementById('cancel-account-modal-btn').click(); true;")
        modal_open_after_close = cdp.eval_js("getComputedStyle(document.getElementById('account-settings-modal')).display !== 'none'")
        old_token = cdp.eval_js("localStorage.getItem('token')")
        assert banner_visible is True
        assert modal_open_after_close is True
        assert bool(old_token)
        report["steps"]["forced_modal_blocks_close"] = True

        # Step 4: Change password -> redirects to login & invalidates old token
        new_pwd = "BrEmpUpdated#2026!"
        cdp.eval_js(
            f"""
            (() => {{
                const modal = document.getElementById('account-settings-modal');
                const pwds = Array.from(modal.querySelectorAll('input[type="password"]'));
                if (pwds[0]) {{ pwds[0].value = {json.dumps(temp_pwd)}; pwds[0].dispatchEvent(new Event('input', {{bubbles: true}})); }}
                if (pwds[1]) {{ pwds[1].value = {json.dumps(new_pwd)}; pwds[1].dispatchEvent(new Event('input', {{bubbles: true}})); }}
                if (pwds[2]) {{ pwds[2].value = {json.dumps(new_pwd)}; pwds[2].dispatchEvent(new Event('input', {{bubbles: true}})); }}
                document.getElementById('save-account-settings-btn').click();
                return true;
            }})()
            """
        )
        _wait_for_js(cdp, "window.location.pathname.endsWith('/login.html')", timeout=15.0)
        old_token_me_status = _http_status(
            f"{base_url}/api/auth/me",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert old_token_me_status == 401
        report["steps"]["password_change_relogin_and_old_token_401"] = True

        # Step 5: Re-login with updated password -> normal UI unlocked -> permission error feedback on admin publish
        _wait_for_js(cdp, "document.readyState === 'complete' && Boolean(document.querySelector('input[type=\"password\"]'))")
        cdp.eval_js(
            f"""
            (() => {{
                const u = document.querySelector('#username') || document.querySelector('input[type="text"]');
                const p = document.querySelector('#password') || document.querySelector('input[type="password"]');
                u.value = 'br_emp';
                u.dispatchEvent(new Event('input', {{bubbles: true}}));
                p.value = {json.dumps(new_pwd)};
                p.dispatchEvent(new Event('input', {{bubbles: true}}));
                const btn = document.querySelector('#loginBtn') || document.querySelector('button[type="submit"]') || document.querySelector('button');
                btn.click();
                return true;
            }})()
            """
        )
        _wait_for_js(cdp, "window.location.pathname.endsWith('/index.html') || window.location.pathname === '/'", timeout=15.0)
        _wait_for_js(cdp, "Boolean(localStorage.getItem('token')) && localStorage.getItem('must_change_password') === '0'", timeout=10.0)
        relogin_token = cdp.eval_js("localStorage.getItem('token')")
        assert _http_status(f"{base_url}/api/auth/me", headers={"Authorization": f"Bearer {relogin_token}"}) == 200
        banner_hidden_after = cdp.eval_js("getComputedStyle(document.getElementById('forced-password-banner')).display === 'none'")
        assert banner_hidden_after is True

        # Trigger UI rule workflow fetch/publish action as employee -> verify 403 permission handling cleanly
        publish_status = cdp.eval_js(
            f"""
            (async () => {{
                const res = await fetch('/api/llm-rules/M4-BR-RULE-1/publish', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + localStorage.getItem('token')
                    }},
                    body: '{{}}'
                }});
                return res.status;
            }})()
            """
        )
        assert publish_status == 403
        report["steps"]["relogin_normal_and_publish_403_verified"] = True

        # Capture screenshot of unlocked workbench page
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        (EVIDENCE_DIR / "m4_r01_browser_e2e.png").write_bytes(base64.b64decode(shot["data"]))

        # Step 6: Logout clears local token & revokes jti server-side (401)
        cdp.eval_js("window.confirm = () => true; window.alert = () => {}; document.getElementById('logout-btn').click(); true;")
        _wait_for_js(cdp, "window.location.pathname.endsWith('/login.html')", timeout=10.0)
        revoked_status = _http_status(
            f"{base_url}/api/auth/me",
            headers={"Authorization": f"Bearer {relogin_token}"},
        )
        assert revoked_status == 401
        report["steps"]["logout_revokes_token_401"] = True
        report["passed"] = True

        (EVIDENCE_DIR / "m4_r01_browser_e2e.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("BROWSER E2E PASS:", json.dumps(report, ensure_ascii=False))
    finally:
        if cdp is not None:
            cdp.close()
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
