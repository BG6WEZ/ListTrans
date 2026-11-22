"""Measure M3 admission and live HTTP responsiveness on private catalog copies."""
import argparse
import json
import math
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from m0_r01_benchmark import (
    block_external_network, hardware, phase, send_upload, stage,
)
from m0_r01_build_governance import GOLDEN, ROOT, read_json, save_json


def distribution(values):
    values = sorted(values)
    return {"n": len(values), "p50_ms": round(values[math.ceil(len(values) * .5) - 1], 3),
            "p95_ms": round(values[math.ceil(len(values) * .95) - 1], 3), "max_ms": round(values[-1], 3)}


def synthetic_admin():
    from contextlib import closing
    from secrets import token_urlsafe
    from app.auth_manager import get_auth_connection, get_password_hash, get_user_by_username, issue_token_for_user

    with closing(get_auth_connection()) as conn, conn:
        conn.execute("""INSERT INTO users(username,password_hash,name,role,is_active,must_change_password,account_epoch)
                     VALUES('m3-load',?,'m3-load','admin',1,0,'m3-test-epoch')""", (get_password_hash(token_urlsafe(28)),))
    actor = get_user_by_username("m3-load")
    return actor, {"Authorization": "Bearer " + issue_token_for_user(actor)}


def seed_load(actor):
    from app.binding_decisions import make_scope_key, record_decision
    from app.db_manager import get_db_cursor

    with get_db_cursor() as cur:
        material = dict(cur.execute("""SELECT code,name,specification,category,brand,unit FROM materials
            WHERE unit IN ('台', '个', '米') AND COALESCE(specification,'') != ''
            AND code NOT LIKE '%禁%' AND code NOT LIKE '%停%' ORDER BY code LIMIT 1""").fetchone())
        cur.execute("DELETE FROM flywheel_records")
        cur.execute("""INSERT INTO flywheel_documents(document_id,upload_task_id,work_session_id,actor_user_id,
            actor_account_epoch,original_filename,file_sha256,contract_version)
            VALUES('m3-mining','m3-mining-upload','m3-mining-session',?,?,'synthetic.xlsx','synthetic','v0.1.6-m0-v1')""",
                    (actor["id"], actor["account_epoch"]))
        cur.executemany("""INSERT INTO flywheel_source_rows(source_row_id,document_id,sheet_id,source_row_num,
            display_row_idx,raw_name,raw_name_norm) VALUES(?, 'm3-mining','m3-sheet',?,?,'合成风机','合成风机')""",
                        ((f"m3-row-{i}", i + 2, i + 1) for i in range(2500)))
        target = json.dumps([{"code": material["code"], "name": material["name"], "spec": material["specification"]}], ensure_ascii=False)
        cur.executemany("""INSERT INTO flywheel_records(client_event_id,session_id,row_idx,discipline,raw_name,
            raw_name_norm,action_type,final_targets_json,user_id,actor_user_id,actor_account_epoch,
            document_id,sheet_id,source_row_id,payload_digest,event_seq,trust_level)
            VALUES(?,'m3-mining-session',?,'暖通','合成风机','合成风机','MANUAL_REPLACE',?,'m3-load',?,?,'m3-mining',
            'm3-sheet',?,'m3-synthetic-digest',1,'trusted')""",
                        ((f"m3-event-{i}", i, target, actor["id"], actor["account_epoch"], f"m3-row-{i}")
                         for i in range(2500)))

    binding = {"code": material["code"], "name": material["name"], "unit": material["unit"], "qty": 1,
               "qty_ratio": 1.0,
               "category": material["category"] or "", "brand": material["brand"] or "",
               "specification": material["specification"] or ""}
    report = {"scope_filename": "m3-load.xlsx", "items": []}
    for index in range(160):
        row = {"original_row_idx": index, "original_name": binding["name"], "original_spec": binding["specification"],
               "original_unit": binding["unit"], "original_qty": 1, "original_loss": 1,
               "original_category": binding["category"], "professional": "暖通", "bindings": [binding],
               "matched_code": binding["code"], "matched_name": binding["name"], "matched_unit": binding["unit"],
               "matched_category": binding["category"], "matched_spec": binding["specification"],
               "matched_brand": binding["brand"], "decision_status": "manual_confirmed"}
        record_decision(actor["id"], make_scope_key(report["scope_filename"], "暖通"), row,
                        [binding], "manual_confirmed", "manual")
        report["items"].append(row)
    return report


def run(root):
    frozen = read_json(Path(__file__).with_name("m0_r01_before.json"))
    stage(root, "keyword", frozen)
    sys.addaudithook(block_external_network)
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient
    from app.runtime_jobs import export_jobs, match_jobs, mining_jobs, upload_jobs
    from app.server import app, set_task_initial

    labels = read_json(ROOT / "tests/fixtures/golden_governance_v1.json")["samples"]
    items = [row for row in read_json(GOLDEN)["items"] if labels[row["id"]]["split"] == "train"][:100]
    metrics = {"hardware": hardware(), "mode": "keyword", "phases": {}, "max_probe_ms": 2000}
    with TestClient(app) as client:
        actor, headers = synthetic_admin()
        report = seed_load(actor)
        set_task_initial("m3-probe", {"status": "processing"}, owner_user=actor)
        work = []
        gate = threading.Barrier(19)
        origin = time.perf_counter()

        def request(kind, fn):
            gate.wait(timeout=30)
            start = time.perf_counter()
            details = fn()
            return {"kind": kind, "start_ms": (start - origin) * 1000,
                    "ms": (time.perf_counter() - start) * 1000, **details}

        def batch():
            payload = {"rows": [{"id": i, "text": row["name"] + " " + row["specification"],
                                 "professional": row["professional"], "unit": row["unit"]}
                                for i, row in enumerate(items[:35])]}
            response = client.post("/api/batch_match", json=payload, headers=headers)
            if response.status_code == 200:
                assert len(response.json()["results"]) == len(payload["rows"])
            return {"status": response.status_code}

        def upload():
            result, _ = send_upload(client, headers, items)
            return {"status": 200, "rows": len(result)}

        def export():
            response = client.post("/api/export", json=report, headers=headers)
            if response.status_code != 200:
                detail = response.json().get("detail", {})
                reason = detail.get("row", {}).get("reason", "") if isinstance(detail, dict) else ""
                raise RuntimeError(f"Controlled export failed with status {response.status_code}, reason={reason}")
            return {"status": response.status_code, "xlsx": response.content[:2] == b"PK"}

        def mine():
            response = client.post("/api/flywheel/miner/run", headers=headers)
            return {"status": response.status_code,
                    "rows": response.json().get("result", {}).get("processed_records_count")}

        probes = {"health": [], "status": []}
        with phase(metrics["phases"], "concurrent_load"), ThreadPoolExecutor(max_workers=18) as threads:
            for kind, fn, count in (("batch", batch, 15), ("upload", upload, 1), ("export", export, 1), ("mining", mine, 1)):
                work.extend(threads.submit(request, kind, fn) for _ in range(count))
            gate.wait(timeout=30)
            while not all(future.done() for future in work):
                for kind, path in (("health", "/api/admin/health"), ("status", "/api/upload/status?task_id=m3-probe")):
                    start = time.perf_counter()
                    response = client.get(path, headers=headers)
                    milliseconds = (time.perf_counter() - start) * 1000
                    assert response.status_code == 200
                    probes[kind].append(milliseconds)
                time.sleep(0.05)
            metrics["work"] = [future.result() for future in work]
        successful = {row["kind"] for row in metrics["work"] if row["status"] == 200}
        assert successful == {"batch", "upload", "export", "mining"}, [(row["kind"], row["status"]) for row in metrics["work"]]
        assert all(row["status"] in (200, 429) for row in metrics["work"])
        assert any(row["status"] == 429 for row in metrics["work"])
        assert any(row.get("xlsx") for row in metrics["work"])
        assert any(row.get("rows") == 2500 for row in metrics["work"])
        metrics["status_counts"] = dict(Counter(row["status"] for row in metrics["work"]))
        metrics["probes"] = {kind: distribution(values) for kind, values in probes.items()}
        metrics["queues"] = {}
        for name, pool in (("match", match_jobs), ("export", export_jobs), ("upload", upload_jobs), ("mining", mining_jobs)):
            state = pool.stats()
            state["wait"] = distribution(state.pop("wait_samples_ms"))
            assert state["admitted"] == 0
            metrics["queues"][name] = state
        metrics["probe_gate_passed"] = all(row["max_ms"] < metrics["max_probe_ms"] for row in metrics["probes"].values())
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Choose a new evidence file")
    metrics = run(args.root)
    save_json(args.out, metrics)
    raise SystemExit(0 if metrics["probe_gate_passed"] else 1)
