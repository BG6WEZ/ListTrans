"""Run real frontend payloads through isolated HTTP endpoints and SQLite.

Reuses only the audited setup prefix, stopping before all audit cases/output.
The first flywheel response is lost after HTTP 200/commit. The first feedback
request gets a real HTTP 500 from an injected JSON sync error after DB commit.
"""
import hashlib
import faulthandler
import json
import os
import socket
import subprocess
from pathlib import Path
from unittest.mock import patch
import threading

faulthandler.enable()
faulthandler.dump_traceback_later(25, repeat=True)
watchdog = threading.Timer(80, lambda: os._exit(124))
watchdog.daemon = True
watchdog.start()

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "LLM_ENABLED": "false"})
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex


def offline_connect(sock, address):
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost"):
        return original_connect(sock, address)
    raise RuntimeError("Coordinator probe blocked non-loopback connection")


def offline_connect_ex(sock, address):
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost"):
        return original_connect_ex(sock, address)
    raise RuntimeError("Coordinator probe blocked non-loopback connection")


with patch.object(socket.socket, "connect", offline_connect), patch.object(socket.socket, "connect_ex", offline_connect_ex):
    setup_path = ROOT / "doc/V0.1.6/evidence/audit_m0_r02/counterexamples.py"
    source = setup_path.read_text(encoding="utf-8")
    marker = "# ---------------------------------------------------------------------------\n# CE-1 无 actor"
    assert source.count(marker) == 1
    prefix = source.split(marker)[0]
    context = {"__file__": str(setup_path), "__name__": "coord_isolated_setup"}
    exec(compile(prefix, str(setup_path), "exec"), context)
    print("ISOLATED_SETUP_COMPLETED", flush=True)
    client, actor = context["client"], context["u1"]
    item = dict(context["row_a"])
    material = context["materials"][0]
    item["bindings"] = [{"code": material["code"], "name": material["name"], "qty": 1}]
    item_path = context["AUDIT_ROOT"] / "frontend_item.json"
    item_path.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
    process = subprocess.Popen(["node", str(OUT / "frontend_http_bridge.js"), str(item_path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", cwd=ROOT)
    http_results = []
    per_scenario = {}
    result = None
    try:
        for line in process.stdout:
            message = json.loads(line)
            if message["type"] == "result":
                result = message["result"]
                break
            scenario = message["scenario"]
            print("HTTP_REQUEST " + scenario, flush=True)
            per_scenario[scenario] = per_scenario.get(scenario, 0) + 1
            first = per_scenario[scenario] == 1
            if scenario == "feedback_json_sync_500" and first:
                with patch.object(context["feedback_train"], "sync_trusted_feedback_file",
                                  side_effect=OSError("coordinator_injected_json_sync_failure")):
                    response = client.post(message["url"], headers=actor["headers"], json=message["body"])
            else:
                response = client.post(message["url"], headers=actor["headers"], json=message["body"])
            reply = {"id": message["id"], "status": response.status_code, "body": response.json()}
            if scenario == "flywheel_response_lost" and first:
                assert response.status_code == 200, response.text
                reply["network_error"] = True
            http_results.append({"scenario": scenario, "attempt": per_scenario[scenario],
                                 "status": response.status_code, "event_id": message["body"]["client_event_id"],
                                 "event_seq": message["body"]["event_seq"],
                                 "response_dropped": reply.get("network_error", False),
                                 "idempotent_replay": response.json().get("idempotent_replay")})
            process.stdin.write(json.dumps(reply, ensure_ascii=False) + "\n")
            process.stdin.flush()
        process.wait(timeout=20)
        assert process.returncode == 0, process.stderr.read()
        assert result is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    sql = context["_materials_sql"]
    scope = (item["document_id"], item["source_row_id"])
    rows = {}
    for table in ("flywheel_records", "flywheel_feedback_samples"):
        rows[table] = sql(f"SELECT client_event_id,event_seq,superseded FROM {table} "
                          "WHERE document_id=? AND source_row_id=? ORDER BY event_seq", scope)
    file_path = context["LAYOUT"]["feedback_samples_file"]
    json_records = json.loads(file_path.read_text(encoding="utf-8")) if file_path.exists() else []
    fb_ids = {row["client_event_id"] for row in rows["flywheel_feedback_samples"]}
    mirrored = {row.get("client_event_id") for row in json_records} & fb_ids
    summary = {"scope": "isolated TestClient HTTP + real SQLite; real JS functions via IPC fetch",
               "isolation_root": str(context["AUDIT_ROOT"]),
               "app_js_sha256": result["app_js_sha256"], "http": http_results, "sqlite": rows,
               "frontend": {key: value for key, value in result.items() if key != "calls"},
               "feedback_db_events": len(fb_ids), "feedback_json_events": len(mirrored),
               "feedback_missing_json_events": sorted(fb_ids - mirrored),
               "outside_network": "non-loopback socket connections blocked; model offline"}
    summary["counterexample_reproduced"] = (
        [r["status"] for r in http_results] == [200, 200, 500, 200]
        and result["flywheel_returned_before_response"]
        and not result["feedback_500_thrown_to_caller"]
        and len(rows["flywheel_records"]) == 2 and len(fb_ids) == 2 and len(mirrored) == 1
        and all(row["idempotent_replay"] is False for row in http_results if row["status"] == 200))
    with (OUT / "frontend_http_report.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))
    watchdog.cancel()
    faulthandler.cancel_dump_traceback_later()
    raise SystemExit(0 if summary["counterexample_reproduced"] else 2)
