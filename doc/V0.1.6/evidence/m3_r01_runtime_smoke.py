"""Run against a clean runtime tree or via Docker stdin. Seeds synthetic inputs only."""
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
    with tempfile.TemporaryDirectory(prefix="listtrans_m3_smoke_") as work:
        try:
            run(project, Path(work).resolve())
        finally:
            logging.shutdown()


def run(project, root):
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))
    password = f"M3Smoke_{secrets.token_hex(12)}!"
    new_password = f"M3Changed_{secrets.token_hex(12)}!"
    paths = {
        "MATERIALS_DB_PATH": root / "db/materials.db", "AUTH_DB_PATH": root / "db/auth.db",
        "CONFIG_DIR": root / "config", "VECTOR_CACHE_DIR": root / "cache",
        "UPLOAD_DIR": root / "uploads", "OUTPUT_DIR": root / "output", "LOG_DIR": root / "logs",
        "BINDING_FEEDBACK_FILE": root / "runtime/binding.json",
        "FEEDBACK_SAMPLES_FILE": root / "runtime/samples.json", "GOLDEN_DETAIL_FILE": root / "runtime/detail.json",
    }
    assert len(paths) == 10 and all(root in p.resolve().parents for p in paths.values())
    for path in paths.values():
        (path.parent if path.suffix else path).mkdir(parents=True, exist_ok=True)
    shutil.copytree(project / "config", paths["CONFIG_DIR"], dirs_exist_ok=True)
    for key in ("GOLDEN_SET_FILE", "GOLDEN_SET_SHA256", "EMBEDDING_MODEL", "LISTTRANS_EVAL_REQUIRE_VECTOR", "LISTTRANS_EVAL_KEYWORD_ONLY"):
        os.environ.pop(key, None)
    os.environ.update({k: str(v) for k, v in paths.items()})
    os.environ.update({
        "LLM_ENABLED": "false", "TESTING": "1", "AUTH_DEV_MODE": "false", "APP_ENV": "test",
        "DASHSCOPE_API_KEY": "", "DEEPSEEK_API_KEY": "", "ADMIN_USERNAME": "m3_admin", "ADMIN_PASSWORD": password,
        "SECRET_KEY": secrets.token_hex(32), "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HOME": str(root / "empty_hf"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "PYTHONIOENCODING": "utf-8",
    })
    from openpyxl import Workbook, load_workbook
    from app.auth_manager import get_auth_connection, get_password_hash, init_auth_db
    from app.db_manager import import_materials_from_excel, init_db, get_db_connection
    from app.flywheel_db import init_flywheel_tables
    from app.runtime_eval_assets import load_checked_json, ASSET_DIR

    _, fixture, _ = load_checked_json(ASSET_DIR / "smoke_materials_v1.json",
                                       "f79a97ce989f79c1204758f35f95c8ab77bd485509603e13a468026b7f6899de", "smoke")
    materials = fixture["materials"]
    init_auth_db()
    auth = get_auth_connection()
    auth.execute("UPDATE users SET must_change_password=1 WHERE username='m3_admin'")
    for username, role in (("m3_emp", "employee"), ("m3_mgr", "manager")):
        auth.execute("INSERT INTO users(username,password_hash,name,role,is_active,must_change_password,account_epoch) "
                     "VALUES (?,?,?,?,1,0,?)", (username, get_password_hash(password), username, role, secrets.token_hex(12)))
    auth.commit()
    auth.close()
    init_db()
    wb = Workbook()
    sheet = wb.active
    sheet.append(["M3 合成物料"])
    sheet.append(["物料分类", "物料编码", "物料名称", "规格/型号", "计量单位", "常用品牌", "描述"])
    for m in materials:
        sheet.append([m["category"], m["code"], m["name"], m["specification"], m["unit"], "", ""])
    xlsx = paths["UPLOAD_DIR"] / "seed.xlsx"
    wb.save(xlsx)
    wb.close()
    import_materials_from_excel(str(xlsx), mode="replace", filename=xlsx.name)
    init_flywheel_tables()
    db = get_db_connection()
    assert db.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == len(materials)
    suggestion = db.execute("INSERT INTO flywheel_rule_suggestions "
                            "(suggestion_type, source_pattern, target_value, discipline, source_origin) "
                            "VALUES ('ALIAS', '排风机', '排风机', '暖通', 'trusted_miner')").lastrowid
    db.commit()
    db.close()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    results = {"catalog_rows": len(materials), "no_tests_doc": True, "calls": [], "evaluations": {}}

    def call(method, url, body=None, token=None, expected=200, *, record=True):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = urlopen(Request(base + url, data, headers, method=method), timeout=25)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            status = response.status
            is_json = response.headers.get_content_type() == "application/json"
        if record:
            results["calls"].append([method, url, status])
        assert status in (expected if isinstance(expected, tuple) else (expected,)), (method, url, status, raw[:500])
        return json.loads(raw) if is_json else raw

    def login(name, pwd):
        return call("POST", "/api/auth/login", {"username": name, "password": pwd})

    def finished(prefix, eval_id, token):
        deadline = time.monotonic() + 95
        while time.monotonic() < deadline:
            response = call("GET", f"{prefix}/{eval_id}", token=token, record=False)
            if response["status"] in ("completed", "failed", "timed_out"):
                return response
            time.sleep(0.35)
        raise TimeoutError(f"{prefix} reached smoke deadline")

    expect_vectors = os.getenv("M3_EXPECT_VECTOR") == "1"

    def check_eval(label, status):
        assert status["status"] == "completed" and status["safety_passed"], status
        report = status["report"]
        assert report["rule_execution_hits"] > 0 and report["guard_count"] > 0 and report["binding_checks_count"] > 0
        vector = report["snapshot"]["baseline"]["vector_channel"]
        assert vector["enabled"] == expect_vectors, vector
        assert ("|loaded" if expect_vectors else "|unavailable:keyword_only") in vector["model_version"], vector
        if expect_vectors:
            assert vector["dim"] == 512 and vector["cache_embeddings_sha256"] and len(vector["active_material_codes"]) == len(materials)
        results["evaluations"][label] = {
            "benefit": status["benefit"], "hits": report["rule_execution_hits"], "guard": report["guard_count"],
            "vector_enabled": vector["enabled"], "model_version": vector["model_version"],
        }
        return {"eval_id": status["eval_id"], "neutral_reason": "合成样本稳态验证"}

    log_path = paths["LOG_DIR"] / "server_stdout.log"
    with log_path.open("wb") as log:
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1",
                                   "--port", str(port), "--workers", "1"], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 45
            while True:
                try:
                    call("GET", "/api/llm-rules/stats", expected=(401, 403))
                    break
                except OSError:
                    if server.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Runtime HTTP server did not start")
                    time.sleep(0.35)
            initial = login("m3_admin", password)
            assert initial["must_change_password"] is True
            old_token = initial["access_token"]
            call("GET", "/api/llm-rules/stats", token=old_token, expected=403)
            change = call("PUT", "/api/auth/profile", {"old_password": password, "new_password": new_password}, old_token)
            assert change["relogin_required"]
            call("GET", "/api/llm-rules/stats", token=old_token, expected=(401, 403))
            admin = login("m3_admin", new_password)["access_token"]
            employee = login("m3_emp", password)["access_token"]
            manager = login("m3_mgr", password)["access_token"]

            empty = call("GET", "/api/llm-rules/stats", token=admin)
            assert empty["published_count"] == empty["pending_count"] == 0 and empty["top_rules"] == []
            rid = "stats"
            dsl = {"rule_name": "M3合成稳态验证", "cleaning_rules": [{"action": "synonym_replace",
                   "target": "镀锌钢管", "replacement": " 镀锌钢管"}], "extraction_rules": [], "matching_rules": {}}
            call("POST", "/api/llm-rules", {"rule_id": rid, "rule_name": dsl["rule_name"], "scope_level": "discipline",
                 "scope_target": "给排水", "discipline": "给排水", "priority": 10, "parsed_dsl": dsl}, employee)
            stats = call("GET", "/api/llm-rules/stats", token=employee)
            assert set(stats) == set(empty) and "rule_id" not in stats
            call("POST", f"/api/llm-rules/{rid}/submit", token=employee)
            assert call("GET", "/api/llm-rules/stats", token=employee)["pending_count"] == 1
            call("POST", f"/api/llm-rules/{rid}/audit", {"action": "approve"}, manager)
            pub_path = f"/api/llm-rules/{rid}/publish"
            call("POST", pub_path, {}, admin, 409)
            eval_id = call("POST", f"/api/llm-rules/{rid}/eval", token=admin)["eval_id"]
            payload = check_eval("manual_llm", finished("/api/llm-rules/eval", eval_id, admin))
            call("GET", f"/api/llm-rules/eval/{eval_id}", token=employee, expected=404)
            golden = ASSET_DIR / "golden_runtime_v1.json"
            original = golden.read_bytes()
            try:
                golden.rename(golden.with_suffix(".hidden"))
                miss = call("POST", pub_path, payload, admin, 409)
                assert miss["detail"]["code"] == "golden_missing"
                blocked = call("POST", f"/api/llm-rules/{rid}/eval", token=admin)["eval_id"]
                invalid = finished("/api/llm-rules/eval", blocked, admin)
                assert invalid["status"] == "failed" and invalid["benefit"] == "invalid" and not invalid["safety_passed"]
            finally:
                golden.with_suffix(".hidden").rename(golden)
            assert golden.read_bytes() == original
            call("POST", pub_path, payload, admin)

            m = materials[1]
            bind = dict(m, qty=1.0, qty_ratio=1.0, brand="")
            legacy = dict(original_name=m["name"], original_spec="70℃", professional=m["category"], bindings=[bind])
            call("POST", "/api/binding/rules/confirm", legacy, admin, 409)
            eval_id = call("POST", "/api/binding/rules/eval", legacy, admin)["eval_id"]
            payload = check_eval("legacy_binding", finished("/api/binding/rules/eval", eval_id, admin))
            call("POST", "/api/binding/rules/confirm", legacy | payload, admin)

            call("POST", "/api/flywheel/suggestions/accept", {"suggestion_id": suggestion}, admin, 409)
            eval_id = call("POST", "/api/flywheel/suggestions/eval", {"suggestion_id": suggestion}, admin)["eval_id"]
            payload = check_eval("flywheel", finished("/api/flywheel/eval", eval_id, admin))
            call("POST", "/api/flywheel/suggestions/accept", {"suggestion_id": suggestion} | payload, admin)

            scope = "m3_synthetic.xlsx"
            row = dict(original_row_idx=1, original_name=m["name"], original_spec=m["specification"],
                       original_qty=1.0, original_unit=m["unit"], original_loss=1.0, matched_code=m["code"],
                       matched_name=m["name"], matched_category=m["category"], matched_spec=m["specification"],
                       matched_unit=m["unit"], matched_brand="", professional=m["category"], bindings=[bind])
            call("POST", "/api/binding/submit", row | {"scope_filename": scope}, employee)
            export_request = {"scope_filename": scope, "items": [row]}
            workbook = call("POST", "/api/export", export_request, employee)
            output = load_workbook(io.BytesIO(workbook), read_only=True)
            assert output["模板"]["E1"].value == "*清单编码"
            assert m["code"] in [c[0] for c in output["模板"].iter_rows(min_col=5, max_col=5, values_only=True)]
            output.close()
            results["export"] = {"xlsx_bytes": len(workbook), "header_and_exported_code": True}
            template = ASSET_DIR / "report_template_v1.json"
            original_template = template.read_bytes()
            try:
                template.write_bytes(b"invalid")
                invalid = call("POST", "/api/export", export_request, employee, 500)
                assert invalid["detail"]["code"] == "template_invalid"
            finally:
                template.write_bytes(original_template)
            print("M3_RUNTIME_SMOKE=" + json.dumps(results, ensure_ascii=False), flush=True)
        except Exception:
            print(log_path.read_text(encoding="utf-8", errors="replace")[-11000:], file=sys.stderr)
            raise
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    main()
