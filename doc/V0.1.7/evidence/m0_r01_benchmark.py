"""Replay the frozen gold set and both production HTTP paths on isolated snapshots."""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
import ipaddress
import math
import os
from pathlib import Path
import platform
import shutil
import sys
import threading
import time

import psutil

from m0_r01_build_governance import ROOT, CATALOG, GOLDEN, read_catalog, read_json, save_json, sha

MODEL = "bge-small-zh-v1.5"
SCOPES = {
    "MATERIALS_DB_PATH": "db/materials.db", "AUTH_DB_PATH": "db/auth.db",
    "CONFIG_DIR": "config", "VECTOR_CACHE_DIR": "vectors", "UPLOAD_DIR": "uploads",
    "OUTPUT_DIR": "output", "LOG_DIR": "logs", "BINDING_FEEDBACK_FILE": "runtime/binding.json",
    "FEEDBACK_SAMPLES_FILE": "runtime/samples.json", "GOLDEN_DETAIL_FILE": "runtime/detail.json",
}
DIRECTORIES = {"CONFIG_DIR", "VECTOR_CACHE_DIR", "UPLOAD_DIR", "OUTPUT_DIR", "LOG_DIR"}
MIB = 1024 ** 2


def block_external_network(event, args):
    if event == "socket.getaddrinfo":
        host = args[0] or "localhost"
    elif event in {"socket.connect", "socket.sendto"} and isinstance(args[1], tuple):
        host = args[1][0]
    else:
        return
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise RuntimeError("Benchmark blocked outbound network access")


def stage(root, mode, before):
    if not root.is_absolute() or ROOT in root.parents or root == ROOT:
        raise ValueError("Use a fresh native absolute root outside the project")
    root.mkdir(parents=True, exist_ok=False)
    for key, relative in SCOPES.items():
        path = root / relative
        (path if key in DIRECTORIES else path.parent).mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    os.environ.update(
        LISTTRANS_TEST_ROOT=str(root), TESTING="1", LLM_ENABLED="false", AUTH_DEV_MODE="false",
        PYTHON_DOTENV_DISABLED="1", DASHSCOPE_API_KEY="", DEEPSEEK_API_KEY="",
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", EMBEDDING_MODEL=MODEL,
        LISTTRANS_EVAL_KEYWORD_ONLY="1" if mode == "keyword" else "0",
        LISTTRANS_EVAL_REQUIRE_VECTOR="1" if mode == "vector" else "0",
        HF_HOME=str(root / "hf"), TORCH_HOME=str(root / "torch"),
    )
    model_files = {name: digest for name, digest in before["protected_sha256"].items()
                   if name.startswith(f"model_offline/{MODEL}/")}
    if not model_files or any(sha(ROOT / name) != digest for name, digest in model_files.items()):
        raise ValueError("Offline model does not match the frozen file manifest")
    business = before["business"]
    if sha(CATALOG) != business["files"]["app/materials.db"]:
        raise ValueError("Frozen catalog digest mismatch")
    isolation_db = Path(os.environ["MATERIALS_DB_PATH"])
    shutil.copy2(CATALOG, isolation_db)
    if sha(isolation_db) != sha(CATALOG):
        raise ValueError("Snapshot copy failed digest verification")
    configs = business["trees"]["config"]["files"]
    vectors = business["trees"]["app/vector_cache"]["files"] if mode == "vector" else {}
    for source, files, destination in (("config", configs, "CONFIG_DIR"),
                                       ("app/vector_cache", vectors, "VECTOR_CACHE_DIR")):
        for name, digest in files.items():
            origin = ROOT / source / name
            target = Path(os.environ[destination]) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, target)
            if sha(target) != digest:
                raise ValueError(f"Frozen {source} asset digest mismatch")
    return {"catalog": sha(isolation_db), "config": configs, "cache": vectors,
            "model": model_files if mode == "vector" else {}}


@contextmanager
def phase(report, key):
    process = psutil.Process()
    stop = threading.Event()
    memory = [process.memory_info().rss]

    def sample():
        while not stop.wait(.025):
            memory.append(process.memory_info().rss)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    start = time.perf_counter()
    try:
        yield
    finally:
        stop.set()
        sampler.join()
        final = process.memory_info()
        memory.append(final.rss)
        report[key] = {"seconds": time.perf_counter() - start,
                       "sampled_peak_rss_mib": max(memory) / MIB,
                       "process_peak_working_set_mib": getattr(final, "peak_wset", max(memory)) / MIB}


def checked_index(mode, index_manager, materials):
    if index_manager.is_enabled != (mode == "vector"):
        raise RuntimeError("Requested vector mode is unavailable")
    if mode == "keyword":
        return {"enabled": False}
    fields = ("code", "name", "specification", "category")
    expected = sorted(tuple(row[key] for key in fields) for row in materials)
    indexed = sorted(tuple(row[key] for key in fields) for row in index_manager.index_to_material)
    if expected != indexed or index_manager.numpy_embeddings.shape != (len(materials), 512):
        raise RuntimeError("Vector mapping/embeddings do not cover the same frozen material catalog")
    vector = index_manager.encode_queries(["基准校验 排风机"])
    if vector is None or vector.shape != (1, 512) or not index_manager.search(vector[0], top_k=1):
        raise RuntimeError("Offline BGE encode/search calibration failed")
    return {"enabled": True, "dimension": 512, "rows": len(indexed), "faiss": index_manager.use_faiss}


def diagnostic_rows(items, governance):
    from unittest.mock import patch

    from app import matcher, sandbox_evaluator

    # Leave production scoring intact. Capture the insertion order of the untouched recall pool
    # before rank sorting. Its prefixes are diagnostics of the production union, never reruns.
    recall = matcher.hybrid_recall
    row_match = matcher.match_item
    recalls, row_recalls = [], []

    def observe_recall(*args, **kwargs):
        pool = recall(*args, **kwargs)
        recalls.append([r["code"] for r in pool])
        return pool

    def observe_match(*args, **kwargs):
        before = len(recalls)
        result = row_match(*args, **kwargs)
        if len(recalls) != before + 1:
            raise RuntimeError("Unexpected diagnostic recall count")
        row_recalls.append(recalls.pop())
        return result

    expanded = [
        {**item, "target_codes": list(dict.fromkeys(
            item["target_codes"] + [code for sources in governance[item["id"]]["target_alternatives"].values()
                                    for code in sources]))} for item in items
    ]
    with patch.object(matcher, "hybrid_recall", observe_recall), patch.object(matcher, "match_item", observe_match):
        rows, elapsed = sandbox_evaluator.evaluate(expanded, chunk_size=200)
    if len(rows) != len(items) or any(row.get("error") or row.get("skipped") for row in rows):
        raise RuntimeError("Full gold evaluation failed or skipped a row")
    if len(row_recalls) != len(rows):
        raise RuntimeError("Recall pools and diagnostic rows are out of sync")
    data = {}
    for row, pool in zip(rows, row_recalls, strict=True):
        traces = row["diagnostics"]["targets"]
        positions = {code: rank for rank, code in enumerate(pool, 1)}
        if len(positions) != len(pool) or set(pool) != {r["code"] for r in row["diagnostics"]["candidates"]}:
            raise RuntimeError("Recall order and ranking do not cover the same codes")
        data[row["id"]] = {"targets": {t["code"]: {
            "state": t["state"], "recall_position": positions.get(t["code"]), "rank": t["full_rank"],
            "channels": t["recall_sources"],
        } for t in traces}, "pool_size": len(pool), "top10": [r["code"] for r in row["candidates"]],
            "semantic_in_pool": any("semantic" in r["recall_sources"] for r in row["diagnostics"]["candidates"])}
    return data, elapsed


def make_workbook(items):
    import openpyxl

    workbook = openpyxl.Workbook()
    sheets = {}
    for item in items:
        domain = item["professional"]
        if domain not in sheets:
            if not sheets:
                sheet = workbook.active
                sheet.title = domain
            else:
                sheet = workbook.create_sheet(domain)
            sheet.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量", "甲供材品类", "品牌"])
            sheets[domain] = sheet
        sheets[domain].append([item["id"], item["name"], item["specification"], item["unit"],
                               item.get("qty") or 1, item["supply_category"], item.get("brand") or ""])
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def response_json(response):
    if response.status_code != 200:
        # Response bodies may echo an original query or personal data.
        raise RuntimeError(f"HTTP benchmark failed with status {response.status_code}")
    return response.json()


def send_match(client, headers, item):
    start = time.perf_counter()
    response = client.post("/api/match", headers=headers, json={
        "name": item["name"], "text": item["specification"], "professional": item["professional"],
        "unit": item["unit"], "category": item["supply_category"], "brand": item.get("brand") or "",
    })
    milliseconds = (time.perf_counter() - start) * 1000
    result = response_json(response)
    return {"ms": milliseconds, "top_code": next((c["code"] for c in result["top_candidates"]), None),
            "is_exact": result["is_exact"], "matched_code": result["matched_code"],
            "decision_status": result.get("decision_status"), "bindings": []}


def send_upload(client, headers, items):
    workbook = make_workbook(items)
    start = time.perf_counter()
    up = response_json(client.post("/api/upload", headers=headers, files={
        "file": ("m0-golden.xlsx", workbook,
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}))
    state = response_json(client.get("/api/upload/status", headers=headers, params={"task_id": up["task_id"]}))
    milliseconds = (time.perf_counter() - start) * 1000
    if state["status"] != "completed" or len(state["result"]) != len(items):
        raise RuntimeError(f"Upload did not complete with all {len(items)} input rows")
    recs = {}
    for row in state["result"]:
        item_id = row["original_code"]
        if item_id in recs or not row["source_row_id"] or not row["recommendation_snapshot_id"]:
            raise RuntimeError("Lost upload row identity or recommendation provenance")
        recs[item_id] = {"decision_status": row["decision_status"],
                         "bindings": [b["code"] for b in row["bindings"]],
                         "top_code": next((c["code"] for c in row["top_candidates"]), None),
                         "served_domain": row["professional"], "is_exact": row["is_exact"]}
    if set(recs) != {item["id"] for item in items}:
        raise RuntimeError("Upload changed the source row identity")
    return recs, {"ms": milliseconds, "rows": len(items), "ids": [item["id"] for item in items]}


def configure_actor():
    from app.auth_manager import get_auth_connection, get_password_hash, get_user_by_username, issue_token_for_user
    from secrets import token_urlsafe

    with get_auth_connection() as connection:
        connection.execute(
            "INSERT INTO users(username, password_hash, name, role, is_active, must_change_password, account_epoch) "
            "VALUES ('benchmark', ?, 'benchmark', 'employee', 1, 0, 'benchmark-isolated')",
            (get_password_hash(token_urlsafe(28)),),
        )
    return {"Authorization": "Bearer " + issue_token_for_user(get_user_by_username("benchmark"))}


def hardware():
    return {"cpu": os.getenv("PROCESSOR_IDENTIFIER", platform.processor()),
            "physical_cores": psutil.cpu_count(logical=False), "logical_cores": psutil.cpu_count(),
            "ram_mib": psutil.virtual_memory().total / MIB, "os": platform.platform(),
            "python": platform.python_version()}


def run(args):
    before = read_json(Path(__file__).with_name("m0_r01_before.json"))
    asset = read_json(args.governance)
    if any(sha(ROOT / name) != expected for name, expected in asset["inputs"].items()):
        raise ValueError("Governance asset is bound to a different catalog/golden snapshot")
    items = read_json(GOLDEN)["items"]
    if set(asset["samples"]) != {item["id"] for item in items}:
        raise ValueError("Governance coverage mismatch")
    frozen = stage(args.root.resolve(), args.mode, before)
    sys.addaudithook(block_external_network)
    sys.path.insert(0, str(ROOT))
    phases = {}
    report = {"mode": args.mode, "input_hashes": frozen, "golden_sha256": sha(GOLDEN),
              "governance_sha256": sha(args.governance), "hardware": hardware(), "phases": phases,
              "isolation_root": str(args.root.resolve()), "batch_size": args.batch_size,
              "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat()}
    with phase(phases, "startup_prepare"):
        from fastapi.testclient import TestClient
        from app.server import app
        from app.vector_index import index_manager

        materials, _ = read_catalog()
    with TestClient(app) as client:
        with phase(phases, "warmup"):
            report["index"] = checked_index(args.mode, index_manager, materials)
            headers = configure_actor()
            for item in items[:5]:
                send_match(client, headers, item)
            send_upload(client, headers, items[:5])
        with phase(phases, "full_rank_replay"):
            report["diagnostics"], report["evaluation_seconds"] = diagnostic_rows(items, asset["samples"])
        semantic_count = sum(r["semantic_in_pool"] for r in report["diagnostics"].values())
        if semantic_count != (len(items) if args.mode == "vector" else 0):
            raise RuntimeError(f"Unexpected semantic recall coverage {semantic_count}/{len(items)}")
        report["semantic_rows"] = semantic_count
        print("Beginning all /api/match requests", flush=True)
        with phase(phases, "single_http"):
            report["single"] = {item["id"]: send_match(client, headers, item) for item in items}
        print("Beginning all /api/upload requests", flush=True)
        with phase(phases, "batch_http"):
            report["batch"], report["uploads"] = {}, []
            for start in range(0, len(items), args.batch_size):
                rows, metadata = send_upload(client, headers, items[start:start + args.batch_size])
                report["batch"].update(rows)
                report["uploads"].append(metadata)
    print(f"Completed {len(items)} ranking rows, {len(report['single'])} single rows, "
          f"{len(report['batch'])} upload rows ({len(report['uploads'])} workbooks)", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("keyword", "vector"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--governance", type=Path, default=ROOT / "tests/fixtures/golden_governance_v1.json")
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()
    if not args.root.is_absolute() or args.out.exists() or args.batch_size < 1:
        parser.error("Use a fresh absolute root, a new output file and a positive batch size")
    save_json(args.out, run(args))


if __name__ == "__main__":
    main()
