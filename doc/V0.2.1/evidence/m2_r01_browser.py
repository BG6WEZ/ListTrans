"""Extend the released M1 Chrome scenarios with real M2 API interactions."""

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

import m1_r01_browser as m1


def browser_data(root, server):
    fixture = m1.configure_fixture(root, server)
    from app.db_manager import get_db_connection
    from contextlib import closing

    with closing(get_db_connection()) as conn, conn:
        conn.execute("""
            INSERT INTO materials(code, name, specification, unit, category, brand, description, search_tokens)
            VALUES ('BROWSER-M2', '核对专用支架', 'AZ-421', '个', '其它', '', '', '')
        """)
    for name, codes in (("priority-mixed", ("M2-MISSING", "M2-BLOCKED")), ("priority-export", ("M2-MISSING",))):
        rows = [m1.item(code, "核对专用支架", "AZ-421", "个", "强电") for code in codes]
        for row in rows:
            row["qty"] = 3
        for theme in ("dark", "light"):
            (root / f"{name}-{theme}.xlsx").write_bytes(m1.make_workbook(rows))

    def controlled_rows(raw_rows, task_id=None):
        if not raw_rows or not all(raw.get("code", "").startswith("M2-") for raw in raw_rows):
            return fixture(raw_rows, task_id)
        rows = []
        for raw in raw_rows:
            binding = {
                "code": "BROWSER-M2", "name": "核对专用支架", "specification": "AZ-421",
                "unit": "个", "category": "其它", "brand": "", "qty": 3, "qty_ratio": 1,
            }
            missing = {
                "field": "功率" if raw["code"] == "M2-BLOCKED" else "专业", "state": "missing",
                "missing_side": "candidate", "query_value": "40-50w" if raw["code"] == "M2-BLOCKED" else "强电",
                "requested_unverified": raw["code"] == "M2-BLOCKED", "safety_critical_unknown": False,
            }
            initial = {
                "tier": "A", "source": "matcher", "actual_prefilled": True, "effective_bindings": [binding],
                "ranking_score": 96, "distinct_margin": 14, "competitor_code": "CDP-B", "uncapped": True,
                "ranking_evidence_status": "current", "field_comparisons": [missing], "reason_codes": ["priority_review"],
                "professional": "强电",
            }
            rows.append({
                **server._build_row_item_from_raw(raw), "original_row_idx": raw["row_idx"], "initial_match": initial,
                "decision_status": "recommended", "decision_source": "matcher",
                "bindings": [binding], "matched_code": binding["code"], "top_candidates": [{**binding, "score": 96}],
            })
        return rows

    return controlled_rows


def run(chrome, out):
    out.mkdir(parents=True, exist_ok=True)
    evidence = Path(__file__).parent
    baseline = json.loads((evidence / "m2_r01_dispatch_baseline.json").read_bytes())
    os.environ.update(baseline["default_switches_frozen_off"])
    with TemporaryDirectory(prefix="v021_m2_browser_", ignore_cleanup_errors=True) as directory:
        root = Path(directory).resolve()
        m1.stage(root / "service", "keyword", m1.read_json(m1.V017 / "m0_r01_before.json"))
        sys.addaudithook(m1.block_external_network)
        import uvicorn
        from contextlib import closing
        from app import server
        from app.auth_manager import get_auth_connection, get_password_hash, get_user_by_username, issue_token_for_user

        original = (evidence / "m1_r01_browser.mjs").read_text(encoding="utf-8")
        feature = (evidence / "m2_r01_browser.mjs").read_text(encoding="utf-8")
        anchor = "    await advanced();"
        assert original.count(anchor) == 1
        original = original.replace(
            "accept: !params.message.includes('未保存的编辑草稿') || approveDraft",
            "accept: !params.message.includes('报表导出成功') && (!params.message.includes('未保存的编辑草稿') || approveDraft)",
        )
        driver = root / "browser.mjs"
        driver.write_text(feature + "\n" + original.replace(
            anchor, anchor + "\n    await priorityScenario('light');\n    await priorityScenario('dark');",
        ), encoding="utf-8")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            service = uvicorn.Server(uvicorn.Config(server.app, lifespan="on", log_level="warning"))
            thread = threading.Thread(target=service.run, kwargs={"sockets": [listener]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 50
                while not service.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.025)
                if not service.started:
                    raise RuntimeError("Isolated browser service did not start")
                handler = browser_data(root, server)
                with closing(get_auth_connection()) as auth, auth:
                    auth.execute("""
                        INSERT INTO users (username,password_hash,name,role,is_active,must_change_password,account_epoch)
                        VALUES ('v021-m2-browser',?,'浏览器测试','employee',1,0,'m2-fixture')
                    """, (get_password_hash(secrets.token_urlsafe(24)),))
                token = issue_token_for_user(get_user_by_username("v021-m2-browser"))
                with patch.object(server, "batch_match_rows", handler):
                    subprocess.run([
                        "node", str(driver), str(chrome), f"http://127.0.0.1:{listener.getsockname()[1]}",
                        str(root), str(out),
                    ], check=True, env={**os.environ, "V021_M1_TOKEN": token})
            finally:
                service.should_exit = True
                thread.join(timeout=20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", type=Path, default=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.chrome, args.out.resolve())
