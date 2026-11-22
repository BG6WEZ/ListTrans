"""HTTP M4 loop in a clean runtime tree; all accounts, rows and labels are synthetic."""
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    project = Path.cwd().resolve()
    assert not (project / "tests").exists() and not (project / "doc").exists()
    root = Path(tempfile.mkdtemp(prefix="listtrans_m4_runtime_")).resolve()
    paths = {"MATERIALS_DB_PATH": root / "db/materials.db", "AUTH_DB_PATH": root / "db/auth.db",
             "CONFIG_DIR": root / "config", "VECTOR_CACHE_DIR": root / "cache", "UPLOAD_DIR": root / "uploads",
             "OUTPUT_DIR": root / "output", "LOG_DIR": root / "logs", "BINDING_FEEDBACK_FILE": root / "runtime/binding.json",
             "FEEDBACK_SAMPLES_FILE": root / "runtime/samples.json", "GOLDEN_DETAIL_FILE": root / "runtime/detail.json"}
    assert len(paths) == 10 and all(root in p.resolve().parents for p in paths.values())
    for p in paths.values():
        (p.parent if p.suffix else p).mkdir(parents=True, exist_ok=True)
    shutil.copytree(project / "config", paths["CONFIG_DIR"], dirs_exist_ok=True)
    password = "M4Synthetic_" + secrets.token_hex(12) + "!"
    for key in ("EMBEDDING_MODEL", "LISTTRANS_EVAL_REQUIRE_VECTOR", "LISTTRANS_EVAL_KEYWORD_ONLY"):
        os.environ.pop(key, None)
    os.environ.update({k: str(v) for k, v in paths.items()})
    os.environ.update({"LLM_ENABLED": "false", "TESTING": "1", "AUTH_DEV_MODE": "false", "APP_ENV": "test",
                       "ADMIN_USERNAME": "m4_admin", "ADMIN_PASSWORD": password, "SECRET_KEY": secrets.token_hex(32),
                       "DASHSCOPE_API_KEY": "", "DEEPSEEK_API_KEY": "", "PYTHON_DOTENV_DISABLED": "1",
                       "PYTHONDONTWRITEBYTECODE": "1", "HF_HOME": str(root / "empty_hf"), "HF_HUB_OFFLINE": "1",
                       "TRANSFORMERS_OFFLINE": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                       "MKL_NUM_THREADS": "1", "PYTHONIOENCODING": "utf-8"})
    sys.path.insert(0, str(project))
    from openpyxl import Workbook, load_workbook
    from app.auth_manager import get_auth_connection, get_password_hash, init_auth_db
    from app.db_manager import import_materials_from_excel, init_db, get_db_connection
    from app.flywheel_db import init_flywheel_tables
    from app.rule_governance import compute_publish_env_snapshot

    assets = project / "app/eval_assets"
    materials = json.loads((assets / "smoke_materials_v1.json").read_bytes())["materials"]
    golden = json.loads((assets / "golden_runtime_v1.json").read_bytes())
    golden["meta"] = {"version": "m4-synthetic-v1", "source": "synthetic_m4"}
    golden["items"].append(dict(golden["items"][0], id="M4-ALIAS", name="M4FAN"))
    golden_file = root / "runtime/golden.json"
    golden_file.write_text(json.dumps(golden, ensure_ascii=False), encoding="utf-8")
    os.environ.update(GOLDEN_SET_FILE=str(golden_file), GOLDEN_SET_SHA256=hashlib.sha256(golden_file.read_bytes()).hexdigest())
    init_auth_db()
    conn = get_auth_connection()
    conn.execute("UPDATE users SET must_change_password=0 WHERE username='m4_admin'")
    for name in ("m4_emp_a", "m4_emp_b"):
        conn.execute("INSERT INTO users(username,password_hash,name,role,is_active,must_change_password,account_epoch) "
                     "VALUES (?,?,?,'employee',1,0,?)", (name, get_password_hash(password), name, secrets.token_hex(12)))
    conn.commit(); conn.close()
    init_db()
    wb = Workbook(); ws = wb.active
    ws.append(["M4 合成种子"])
    ws.append(["物料分类", "物料编码", "物料名称", "规格/型号", "计量单位", "常用品牌", "描述"])
    for m in materials:
        ws.append([m["category"], m["code"], m["name"], m["specification"], m["unit"], "", ""])
    seed = paths["UPLOAD_DIR"] / "seed.xlsx"; wb.save(seed); wb.close()
    import_materials_from_excel(str(seed), mode="replace", filename=seed.name)
    init_flywheel_tables()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    result = {"no_tests_doc": True, "catalog_rows": len(materials), "golden_sha256": os.environ["GOLDEN_SET_SHA256"],
              "calls": [], "evaluations": {}, "fingerprints": {}}

    def call(method, url, body=None, token=None, expected=200, *, raw=None, content_type=None, record=True):
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": content_type or "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            response = urlopen(Request(base + url, data, headers, method=method), timeout=30)
        except HTTPError as exc:
            response = exc
        with response:
            payload = response.read(); status = response.status
            is_json = response.headers.get_content_type() == "application/json"
        if record:
            result["calls"].append([method, url.split("?")[0], status])
        assert status in (expected if isinstance(expected, tuple) else (expected,)), (method, url, status, payload[:700])
        return json.loads(payload) if is_json else payload

    def upload(token, count=1, professional="暖通", name="M4FAN", specification="风量1000m³/h", unit="台"):
        book = Workbook(); sheet = book.active; sheet.title = professional
        sheet.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量"])
        for idx in range(count):
            sheet.append([f"M4-{idx}", name, specification, unit, 1])
        buffer = io.BytesIO(); book.save(buffer); book.close()
        boundary = "M4Boundary" + secrets.token_hex(6)
        multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="m4_synthetic.xlsx"\r\n'
                     'Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n').encode()
        multipart += buffer.getvalue() + f"\r\n--{boundary}--\r\n".encode()
        task = call("POST", "/api/upload", token=token, raw=multipart, content_type="multipart/form-data; boundary=" + boundary)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = call("GET", "/api/upload/status?task_id=" + task["task_id"], token=token, record=False)
            if state["status"] in ("completed", "failed"):
                assert state["status"] == "completed", state
                result["calls"].append(["GET", "/api/upload/status", 200])
                return state["result"]
            time.sleep(.2)
        raise TimeoutError("upload")

    def record(row, token, action="MANUAL_REPLACE", target=None):
        payload = {k: row[k] for k in ("document_id", "sheet_id", "source_row_id", "recommendation_snapshot_id")}
        payload.update(client_event_id="m4evt_" + secrets.token_hex(10), event_seq=1, action_type=action,
                       final_targets=[dict(target or materials[0], spec=(target or materials[0])["specification"])])
        return call("POST", "/api/flywheel/record", payload, token)

    def evaluate(sid, admin, **overrides):
        eid = call("POST", "/api/flywheel/suggestions/eval", {"suggestion_id": sid} | overrides, admin)["eval_id"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = call("GET", "/api/flywheel/eval/" + eid, token=admin, record=False)
            if state["status"] in ("completed", "failed", "timed_out"):
                assert state["status"] == "completed" and state["safety_passed"], state
                report = state["report"]
                assert report["candidate_diagnostics"] and "candidate_recall" in report["candidate_metrics"]
                assert all(t["diagnostics"]["decision"]["decision_status"] for t in report["candidate_diagnostics"])
                vector = report["snapshot"]["candidate"]["vector_channel"]
                assert vector["enabled"] == (os.getenv("M4_EXPECT_VECTOR") == "1"), vector
                result["evaluations"][str(sid)] = state
                return {"eval_id": eid, "neutral_reason": "M4 合成闭环人工核验稳态"}
            time.sleep(.25)
        raise TimeoutError("evaluation")

    def match(admin):
        rows = [call("POST", "/api/match", {"name": "M4FAN", "text": "风量1000m³/h", "professional": "暖通", "unit": "台"}, admin),
                upload(admin, name="防火阀", specification="500×400 70℃", unit="个")[0]]
        keys = ("parsed_spec", "top_candidates", "bindings", "decision_status", "decision_source", "decision_reason", "matched_code")
        return [{k: row.get(k) for k in keys} for row in rows]

    log_path = paths["LOG_DIR"] / "server.log"
    with log_path.open("wb") as log:
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", str(port), "--workers", "1"], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 50
            while True:
                try:
                    call("GET", "/api/llm-rules/stats", expected=(401, 403)); break
                except OSError:
                    if proc.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Runtime server did not start")
                    time.sleep(.25)
            tokens = {name: call("POST", "/api/auth/login", {"username": name, "password": password})["access_token"]
                      for name in ("m4_admin", "m4_emp_a", "m4_emp_b")}
            admin = tokens["m4_admin"]
            rows_a = upload(tokens["m4_emp_a"], 2); rows_b = upload(tokens["m4_emp_b"], 1)
            assert rows_a[0]["document_id"] != rows_b[0]["document_id"]
            for row, token in [(r, tokens["m4_emp_a"]) for r in rows_a] + [(rows_b[0], tokens["m4_emp_b"])]:
                record(row, token)
            result["mining"] = call("POST", "/api/flywheel/miner/run", token=admin)
            conn = get_db_connection()
            mined = conn.execute("SELECT * FROM flywheel_rule_suggestions WHERE suggestion_type='ALIAS' AND source_pattern='M4FAN' AND status='pending'").fetchone()
            assert mined is not None, [dict(r) for r in conn.execute("SELECT * FROM flywheel_rule_suggestions")]
            sid1 = mined["id"]
            assert mined["support_count"] == 3 and mined["distinct_users"] == 2
            conn.close()
            result["mining_idempotent"] = call("POST", "/api/flywheel/miner/run", token=admin)
            # Confirmed samples must be excluded only in the adopted professional scope.
            confirm = upload(tokens["m4_emp_a"])[0]; record(confirm, tokens["m4_emp_a"], "CONFIRM_TOP1")
            cross = upload(tokens["m4_emp_b"], professional="给排水")[0]; record(cross, tokens["m4_emp_b"], "CONFIRM_TOP1")
            before = match(admin); fp0 = compute_publish_env_snapshot(); result["fingerprints"]["before"] = fp0
            ev1 = evaluate(sid1, admin)
            snap1 = call("POST", "/api/flywheel/suggestions/accept", {"suggestion_id": sid1} | ev1, admin)["snapshot_version"]
            after1 = match(admin); fp1 = compute_publish_env_snapshot(); result["fingerprints"]["alias"] = fp1
            assert after1 != before and fp1["config_digest"] != fp0["config_digest"], {
                "before": before, "after": after1, "mined": dict(mined), "fp_before": fp0, "fp_after": fp1}
            conn = get_db_connection()
            targets = [{"code": "M3-DEMO-02", "name": "防火阀", "specification": "500×400 70℃", "unit": "个", "qty_ratio": 1.0}]
            sid2 = conn.execute("INSERT INTO flywheel_rule_suggestions (discipline,suggestion_type,source_pattern,target_value,status,source_origin) "
                                "VALUES ('暖通','BINDING_RULE','防火阀',?,'pending','synthetic_m4')", (json.dumps(targets, ensure_ascii=False),)).lastrowid
            conn.commit(); conn.close()
            ev2 = evaluate(sid2, admin, original_spec="70℃")
            snap2 = call("POST", "/api/flywheel/suggestions/accept", {"suggestion_id": sid2, "original_spec": "70℃"} | ev2, admin)["snapshot_version"]
            after2 = match(admin); fp2 = compute_publish_env_snapshot()
            result["fingerprints"]["binding"] = fp2
            assert after2[1]["decision_source"] == "auto_binding_rule" and after2[1] != after1[1]
            negative_row = upload(tokens["m4_emp_a"], name="排风机")[0]
            record(negative_row, tokens["m4_emp_a"], target=materials[1])
            benchmark = call("GET", "/api/flywheel/export/benchmark", token=admin)
            # Four MANUAL_REPLACE rows remain, scoped CONFIRM is filtered; other scope stays.
            assert benchmark["count"] == 5, benchmark
            assert sum(i["professional"] == "暖通" for i in benchmark["items"]) == 4
            triplets = call("GET", "/api/flywheel/export/triplets", token=admin)
            assert triplets["count"] == 1, triplets
            assert triplets["triplets"][0]["positive"] == materials[1]["name"] and triplets["triplets"][0]["negative"] == materials[0]["name"]
            result["exports"] = {"benchmark_count": benchmark["count"], "triplet_count": triplets["count"]}
            def database_state():
                conn = get_db_connection()
                try:
                    return {"snapshots": [dict(r) for r in conn.execute("SELECT * FROM flywheel_config_snapshots ORDER BY id")],
                            "suggestions": [dict(r) for r in conn.execute("SELECT * FROM flywheel_rule_suggestions WHERE id IN (?,?) ORDER BY id", (sid1, sid2))]}
                finally:
                    conn.close()
            database_before_reject = database_state()
            config_before_reject = {p.name: p.read_bytes() for p in paths["CONFIG_DIR"].glob("*.json")}
            call("POST", "/api/flywheel/rollback", {"snapshot_version": snap1}, admin, 409)
            assert compute_publish_env_snapshot() == fp2 and match(admin) == after2
            assert config_before_reject == {p.name: p.read_bytes() for p in paths["CONFIG_DIR"].glob("*.json")}
            assert database_state() == database_before_reject
            result["database_states"] = {"before_rejected_rollback": database_before_reject, "after_rejected_rollback": database_state()}
            call("POST", "/api/flywheel/rollback", {"snapshot_version": snap2}, admin)
            assert compute_publish_env_snapshot() == fp1 and match(admin) == after1
            result["fingerprints"]["rollback_binding"] = compute_publish_env_snapshot()
            after_binding_rollback = database_state()
            assert [r["is_active"] for r in after_binding_rollback["snapshots"]] == [1, 0]
            assert [r["status"] for r in after_binding_rollback["suggestions"]] == ["accepted", "rolled_back"]
            result["database_states"]["after_binding_rollback"] = after_binding_rollback
            call("POST", "/api/flywheel/rollback", {"snapshot_version": snap1}, admin)
            assert compute_publish_env_snapshot() == fp0 and match(admin) == before
            result["fingerprints"]["rollback_alias"] = compute_publish_env_snapshot()
            result["database_states"]["after_alias_rollback"] = database_state()
            assert not (paths["CONFIG_DIR"] / "scoped_aliases.json").exists()
            conn = get_db_connection()
            assert [r[0] for r in conn.execute("SELECT status FROM flywheel_rule_suggestions WHERE id IN (?,?) ORDER BY id", (sid1, sid2))] == ["rolled_back", "rolled_back"]
            assert all(r[0] == 0 for r in conn.execute("SELECT is_active FROM flywheel_config_snapshots"))
            conn.close()
            m = materials[1]; binding = dict(m, qty=1., qty_ratio=1., brand="")
            export_row = dict(original_row_idx=1, original_name=m["name"], original_spec=m["specification"], original_qty=1., original_unit=m["unit"], original_loss=1.,
                              matched_code=m["code"], matched_name=m["name"], matched_category=m["category"], matched_spec=m["specification"], matched_unit=m["unit"], matched_brand="", professional=m["category"], bindings=[binding])
            call("POST", "/api/binding/submit", export_row | {"scope_filename": "m4_report.xlsx"}, tokens["m4_emp_a"])
            raw = call("POST", "/api/export", {"scope_filename": "m4_report.xlsx", "items": [export_row]}, tokens["m4_emp_a"])
            book = load_workbook(io.BytesIO(raw), read_only=True)
            assert book["模板"]["E1"].value == "*清单编码"
            assert m["code"] in [r[0] for r in book["模板"].iter_rows(min_col=5, max_col=5, values_only=True)]
            book.close()
            result.update(lifo_409=True, restored_config_db_memory_fingerprint=True, xlsx_bytes=len(raw), snapshots=[snap1, snap2],
                          match_before=before, match_alias=after1, match_binding=after2)
            print("M4_RUNTIME_SMOKE=" + json.dumps(result, ensure_ascii=False), flush=True)
        except Exception:
            print(log_path.read_text(encoding="utf-8", errors="replace")[-14000:], file=sys.stderr)
            raise
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=5)
            logging.shutdown()


if __name__ == "__main__":
    main()
