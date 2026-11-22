# -*- coding: utf-8 -*-
"""
V0.1.5 M5 R02 Independent Audit Negative, Race & Boundary Probes.
Runs strictly in isolated temp sandbox (tests.conftest, LLM_ENABLED=false, TESTING=1).
Never writes to workspace DBs, rules, vector cache, uploads, or developer deliverables.
"""

import io
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import openpyxl

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

os.environ["LLM_ENABLED"] = "false"
os.environ["TESTING"] = "1"

import tests.conftest as cf

missing = cf.MISSING_CORE_DEPS + cf.MISSING_AUTH_DEPS + cf.MISSING_VECTOR_DEPS
if missing:
    raise RuntimeError(f"missing deps: {missing}")

guard_before = cf.run_guardrail_checks()
assert not guard_before["gaps"], guard_before

from fastapi.testclient import TestClient
from app import audit_manager, auth_manager, db_manager, excel_boundary, server, session_manager, vector_index


def _create_admin_headers():
    auth_manager.init_auth_db()
    audit_manager.init_audit_tables()
    session_manager.init_session_db()
    db_manager.init_db()
    conn = auth_manager.get_auth_connection()
    try:
        pw_hash = auth_manager.get_password_hash("AuditPass!123456")
        conn.execute(
            """
            INSERT OR REPLACE INTO users (
                username, password_hash, name, dept1, dept2, dept3,
                employee_no, position, phone, role, is_active, need_emp_no_setup,
                token_version, must_change_password, account_epoch
            ) VALUES (?, ?, 'AuditAdmin', '工程部', '', '', 'AUD-001', 'admin', '13900009999', 'admin', 1, 0, 1, 0, 'audit_epoch_01')
            """,
            ("audit_admin", pw_hash),
        )
        conn.commit()
    finally:
        conn.close()
    user = auth_manager.get_user_by_username("audit_admin")
    assert user is not None
    return {"Authorization": f"Bearer {auth_manager.issue_token_for_user(user)}"}


def _make_materials_xlsx(path: Path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["物料库导出版"])
    ws.append(["物料编码", "物料名称", "规格/型号", "物料分类", "计量单位", "常用品牌", "描述"])
    for code, name, spec, cat in rows:
        ws.append([code, name, spec, cat, "台", "品牌A", ""])
    wb.save(path)


def _setup_seed(tmp_root: Path):
    seed_xlsx = tmp_root / "seed.xlsx"
    _make_materials_xlsx(seed_xlsx, [("OLD-001", "旧阀门", "DN50", "阀门")])
    meta = db_manager.import_materials_from_excel(str(seed_xlsx), mode="replace", filename="seed.xlsx")
    mgr = server.index_manager
    mgr.model = object()
    mgr.embedding_dim = 2
    mgr.use_faiss = False
    mgr.faiss_index = None
    mgr.encode_queries = lambda texts, **kwargs: np.array([[1.0, 0.0] for _ in texts], dtype=np.float32)
    mgr.build_index([{"code": "OLD-001", "name": "旧阀门", "specification": "DN50", "category": "阀门"}])
    return meta, mgr


def _inspect_live_state(mgr, tmp_root: Path):
    conn = sqlite3.connect(os.environ["MATERIALS_DB_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        mat_codes = [r[0] for r in conn.execute("SELECT code FROM materials ORDER BY code").fetchall()]
        fts_codes = [r[0] for r in conn.execute("SELECT code FROM materials_fts ORDER BY code").fetchall()]
        meta_rows = [dict(r) for r in conn.execute("SELECT * FROM materials_metadata").fetchall()]
    finally:
        conn.close()
    cache_dir = Path(vector_index.get_cache_dir())
    cache_map = cache_dir / "index_to_material.json"
    cache_meta = cache_dir / "cache_metadata.json"
    cache_codes = [m.get("code") for m in json.loads(cache_map.read_text(encoding="utf-8"))] if cache_map.exists() else None
    cache_count = json.loads(cache_meta.read_text(encoding="utf-8")).get("item_count") if cache_meta.exists() else None
    mem_codes = [m.get("code") for m in (mgr.index_to_material or [])]
    return {
        "db_codes": mat_codes,
        "fts_codes": fts_codes,
        "meta_row": meta_rows,
        "mem_codes": mem_codes,
        "cache_codes": cache_codes,
        "cache_count": cache_count,
    }


def main():
    tmp_root = Path(os.environ["UPLOAD_DIR"]) / "audit_probes"
    tmp_root.mkdir(parents=True, exist_ok=True)
    report = {}

    try:
        # 1. R01 Replay 1: replace index failure keeps full pre-state
        _, mgr = _setup_seed(tmp_root)
        new_xlsx = tmp_root / "new1.xlsx"
        _make_materials_xlsx(new_xlsx, [("NEW-001", "新水泵", "DN100", "水泵")])
        upload1 = Path(os.environ["UPLOAD_DIR"]) / "p1_new.xlsx"
        shutil.copy2(new_xlsx, upload1)
        t1 = "audit-r01-replay-1"
        server.set_task_initial(t1, {"status": "processing", "current_step": "init", "result": None, "error": None})
        tok1 = server._db_import_lock_manager.try_acquire()
        with patch.object(mgr, "build_index_candidate", side_effect=RuntimeError("boom_candidate")):
            server.async_process_db_upload_task(t1, str(upload1), "replace", "new1.xlsx", lock_token=tok1)
        st1 = server.get_task_status(t1)
        state1 = _inspect_live_state(mgr, tmp_root)
        report["r01_replay_1_replace_index_fail"] = {
            "task_status": st1.get("status"),
            "state": state1,
            "lock_held": server._db_import_lock.locked(),
            "all_stayed_old": (
                state1["db_codes"] == ["OLD-001"]
                and state1["fts_codes"] == ["OLD-001"]
                and state1["mem_codes"] == ["OLD-001"]
                and state1["cache_codes"] == ["OLD-001"]
                and st1.get("status") == "failed"
                and not server._db_import_lock.locked()
            ),
        }

        # 2. R01 Replay 2 + A-M5-02: alive timed-out worker keeps mutex & returns 409, then recovers
        client = TestClient(server.app)
        headers = _create_admin_headers()

        entered = threading.Event()
        release = threading.Event()

        def held_import(*a, cancel_event=None, **k):
            entered.set()
            release.wait(timeout=10)
            raise excel_boundary.ParseCancelledError("held_cancelled")

        up_bytes = new_xlsx.read_bytes()
        with patch.object(server, "_DB_IMPORT_TASK_TIMEOUT_SECONDS", 0.08), patch.object(
            db_manager, "import_materials_from_excel", side_effect=held_import
        ):
            res_timeout = client.post(
                "/api/db/upload",
                headers=headers,
                files={"file": ("held.xlsx", up_bytes, "application/octet-stream")},
            )
            assert res_timeout.status_code == 200
            tid_to = res_timeout.json()["task_id"]
            st_to = client.get(f"/api/upload/status?task_id={tid_to}", headers=headers).json()
            lock_still_held = server._db_import_lock.locked()
            res_second_409 = client.post(
                "/api/db/upload",
                headers=headers,
                files={"file": ("second.xlsx", up_bytes, "application/octet-stream")},
            )
            release.set()
            # wait for daemon worker cleanup to finish releasing mutex
            for _ in range(50):
                if not server._db_import_lock.locked():
                    break
                time.sleep(0.02)
            lock_released_after_exit = not server._db_import_lock.locked()

        report["r01_replay_2_alive_worker_409"] = {
            "worker_entered": entered.is_set(),
            "timed_out_status": st_to.get("status"),
            "lock_held_while_alive": lock_still_held,
            "second_http_status": res_second_409.status_code,
            "lock_released_after_exit": lock_released_after_exit,
            "passed": (
                entered.is_set()
                and st_to.get("status") == "failed"
                and lock_still_held
                and res_second_409.status_code == 409
                and lock_released_after_exit
            ),
        }

        # 3. R01 Replay 3: publish_candidate aborts when cancel_event set before lock acquisition
        mgr_r3 = vector_index.VectorIndexManager()
        mgr_r3.model = object()
        mgr_r3.embedding_dim = 2
        mgr_r3.use_faiss = False
        mgr_r3.index_to_material = [{"code": "AUDIT-OLD"}]
        mgr_r3.numpy_embeddings = np.array([[0.0, 1.0]], dtype=np.float32)
        mgr_r3.encode_queries = lambda texts, **kwargs: np.array([[1.0, 0.0]], dtype=np.float32)
        mgr_r3.save_cache = lambda: None
        at_swap = threading.Event()
        permit_swap = threading.Event()
        cancel3 = threading.Event()

        class _GateLock:
            def __enter__(self):
                at_swap.set()
                if not permit_swap.wait(10):
                    raise RuntimeError("gate timeout")

            def __exit__(self, *a):
                pass

        mgr_r3._lock = _GateLock()
        errs3 = []

        def _pub():
            try:
                mgr_r3.build_index([{"code": "AUDIT-NEW", "name": "n", "specification": "", "category": ""}], cancel_event=cancel3)
            except Exception as exc:
                errs3.append(type(exc).__name__)

        th3 = threading.Thread(target=_pub)
        th3.start()
        assert at_swap.wait(10)
        cancel3.set()
        permit_swap.set()
        th3.join(10)
        report["r01_replay_3_publish_cancel_aborts"] = {
            "mem_code": mgr_r3.index_to_material[0]["code"],
            "errors": errs3,
            "passed": mgr_r3.index_to_material[0]["code"] == "AUDIT-OLD" and errs3 == ["ParseCancelledError"],
        }

        # 4. R01 Replay 4 & 5: byte budget enforced at direct shared entry + zip loop cancel
        try:
            excel_boundary.load_validated_excel_sheets(
                str(new_xlsx),
                budget=excel_boundary.ExcelParseBudget(max_upload_bytes=1),
            )
            budget_err = None
        except excel_boundary.UploadTooLargeError as exc:
            budget_err = type(exc).__name__

        c5 = threading.Event()
        c5.set()
        try:
            excel_boundary.load_validated_excel_sheets(str(new_xlsx), cancel_event=c5)
            cancel_err = None
        except excel_boundary.ParseCancelledError as exc:
            cancel_err = type(exc).__name__

        exact_sz = new_xlsx.stat().st_size
        exact_ok = len(
            excel_boundary.load_validated_excel_sheets(
                str(new_xlsx),
                budget=excel_boundary.ExcelParseBudget(max_upload_bytes=exact_sz),
            )
        ) == 1
        try:
            excel_boundary.load_validated_excel_sheets(
                str(new_xlsx),
                budget=excel_boundary.ExcelParseBudget(max_upload_bytes=exact_sz - 1),
            )
            minus1_err = None
        except excel_boundary.UploadTooLargeError as exc:
            minus1_err = type(exc).__name__

        report["r01_replay_4_5_excel_boundary"] = {
            "budget_err": budget_err,
            "exact_sz": exact_sz,
            "exact_ok": exact_ok,
            "minus1_err": minus1_err,
            "cancel_err": cancel_err,
            "passed": (
                budget_err == "UploadTooLargeError"
                and exact_ok
                and minus1_err == "UploadTooLargeError"
                and cancel_err == "ParseCancelledError"
            ),
        }

        # 5. CRITICAL R02 PROBE A: Supervisor timeout / cancel lands right AFTER Step 5 DB commit commits,
        # BEFORE Step 6 publish_candidate enters self._lock!
        _, mgr = _setup_seed(tmp_root)
        real_commit = db_manager.commit_staged_materials_import
        upload_a = Path(os.environ["UPLOAD_DIR"]) / "probe_a.xlsx"
        _make_materials_xlsx(upload_a, [("PROBE-A-NEW", "探针A新物料", "DN150", "阀门")])
        t_a = "audit-probe-a-post-swap-cancel"
        server.set_task_initial(t_a, {"status": "processing", "current_step": "init", "result": None, "error": None})
        tok_a = server._db_import_lock_manager.try_acquire()

        def _commit_then_timeout_hits(stage_handle, cancel_event=None):
            res = real_commit(stage_handle, cancel_event=cancel_event)
            # Simulate _DB_IMPORT_TASK_TIMEOUT_SECONDS expiring right inside the tail of Step 5
            # (after SQLite commit already finished and dropped *_old tables) OR between Step 5 and Step 6!
            if cancel_event is not None:
                cancel_event.set()
            return res

        with patch.object(db_manager, "commit_staged_materials_import", side_effect=_commit_then_timeout_hits):
            server.async_process_db_upload_task(t_a, str(upload_a), "replace", "probe_a.xlsx", lock_token=tok_a)

        st_a = server.get_task_status(t_a)
        state_a = _inspect_live_state(mgr, tmp_root)
        report["r02_probe_a_post_swap_cancel_split"] = {
            "task_status": st_a.get("status"),
            "task_error": st_a.get("error"),
            "state": state_a,
            "split_brain_reproduced": (
                st_a.get("status") == "failed"
                and state_a["db_codes"] == ["PROBE-A-NEW"]
                and state_a["fts_codes"] == ["PROBE-A-NEW"]
                and state_a["mem_codes"] == ["OLD-001"]
                and state_a["cache_codes"] == ["OLD-001"]
            ),
        }

        # 6. CRITICAL R02 PROBE B: Real _DB_IMPORT_TASK_TIMEOUT_SECONDS fires INSIDE Step 5 swap
        # (wait until real_commit executes SQL commit, hold commit return 0.35s with timeout=0.25s)
        _, mgr = _setup_seed(tmp_root)
        upload_b = Path(os.environ["UPLOAD_DIR"]) / "probe_b.xlsx"
        _make_materials_xlsx(upload_b, [("PROBE-B-NEW", "探针B新物料", "DN200", "阀门")])
        t_b = "audit-probe-b-real-timeout-in-swap"
        server.set_task_initial(t_b, {"status": "processing", "current_step": "init", "result": None, "error": None})
        tok_b = server._db_import_lock_manager.try_acquire()
        committed_b = threading.Event()

        def _slow_commit_after_sql(stage_handle, cancel_event=None):
            res = real_commit(stage_handle, cancel_event=cancel_event)
            committed_b.set()
            # Ensure supervisor timeout (0.25s) expires after SQLite committed, BEFORE publish_candidate
            time.sleep(0.35)
            return res

        with patch.object(server, "_DB_IMPORT_TASK_TIMEOUT_SECONDS", 0.25), patch.object(
            db_manager, "commit_staged_materials_import", side_effect=_slow_commit_after_sql
        ):
            server.async_process_db_upload_task(t_b, str(upload_b), "replace", "probe_b.xlsx", lock_token=tok_b)
            # wait for daemon worker cleanup
            for _ in range(50):
                if not server._db_import_lock.locked():
                    break
                time.sleep(0.02)

        st_b = server.get_task_status(t_b)
        state_b = _inspect_live_state(mgr, tmp_root)
        report["r02_probe_b_real_timeout_after_sql_commit"] = {
            "committed_b": committed_b.is_set(),
            "task_status": st_b.get("status"),
            "task_error": st_b.get("error"),
            "state": state_b,
            "split_brain_reproduced": (
                committed_b.is_set()
                and st_b.get("status") == "failed"
                and state_b["db_codes"] == ["PROBE-B-NEW"]
                and state_b["mem_codes"] == ["OLD-001"]
                and state_b["cache_codes"] == ["OLD-001"]
            ),
        }

        # 7. CRITICAL R02 PROBE C: Step 7 commit_candidate_cache fails mid-promotion (after DB & memory swapped!)
        _, mgr = _setup_seed(tmp_root)
        upload_c = Path(os.environ["UPLOAD_DIR"]) / "probe_c.xlsx"
        _make_materials_xlsx(upload_c, [("PROBE-C-NEW", "探针C新物料", "DN250", "阀门")])
        t_c = "audit-probe-c-step7-failure"
        server.set_task_initial(t_c, {"status": "processing", "current_step": "init", "result": None, "error": None})
        tok_c = server._db_import_lock_manager.try_acquire()
        real_replace = os.replace
        replace_calls = {"n": 0}

        def _failing_second_replace(src, dst):
            replace_calls["n"] += 1
            if "stage" in str(src) and replace_calls["n"] >= 2:
                raise OSError("simulated disk error on 2nd cache file replace")
            return real_replace(src, dst)

        with patch("app.vector_index.os.replace", side_effect=_failing_second_replace):
            server.async_process_db_upload_task(t_c, str(upload_c), "replace", "probe_c.xlsx", lock_token=tok_c)

        st_c = server.get_task_status(t_c)
        state_c = _inspect_live_state(mgr, tmp_root)
        report["r02_probe_c_step7_cache_replace_failure"] = {
            "task_status": st_c.get("status"),
            "task_error": st_c.get("error"),
            "state": state_c,
            "split_brain_reproduced": (
                st_c.get("status") == "failed"
                and state_c["db_codes"] == ["PROBE-C-NEW"]
                and state_c["mem_codes"] == ["PROBE-C-NEW"]
                and state_c["cache_codes"] != state_c["mem_codes"]
            ),
        }

        # 8. R02 PROBE D: append mode index failure leaves DB mutated vs index stale
        _, mgr = _setup_seed(tmp_root)
        upload_d = Path(os.environ["UPLOAD_DIR"]) / "probe_d.xlsx"
        _make_materials_xlsx(upload_d, [("APPEND-NEW", "追加新物料", "DN300", "阀门")])
        t_d = "audit-probe-d-append-fail"
        server.set_task_initial(t_d, {"status": "processing", "current_step": "init", "result": None, "error": None})
        tok_d = server._db_import_lock_manager.try_acquire()
        with patch.object(mgr, "build_index", side_effect=RuntimeError("append_index_fail")):
            server.async_process_db_upload_task(t_d, str(upload_d), "append", "probe_d.xlsx", lock_token=tok_d)

        st_d = server.get_task_status(t_d)
        state_d = _inspect_live_state(mgr, tmp_root)
        report["r02_probe_d_append_index_failure"] = {
            "task_status": st_d.get("status"),
            "task_error": st_d.get("error"),
            "state": state_d,
            "append_split_reproduced": (
                st_d.get("status") == "failed"
                and "APPEND-NEW" in state_d["db_codes"]
                and "APPEND-NEW" not in state_d["mem_codes"]
            ),
        }

        # 9. R02 PROBE E: replace success sanity check
        _, mgr = _setup_seed(tmp_root)
        upload_e = Path(os.environ["UPLOAD_DIR"]) / "probe_e.xlsx"
        _make_materials_xlsx(upload_e, [("OK-001", "正常新物料", "DN80", "阀门")])
        t_e = "audit-probe-e-success"
        server.set_task_initial(t_e, {"status": "processing", "current_step": "init", "result": None, "error": None})
        tok_e = server._db_import_lock_manager.try_acquire()
        server.async_process_db_upload_task(t_e, str(upload_e), "replace", "probe_e.xlsx", lock_token=tok_e)
        st_e = server.get_task_status(t_e)
        state_e = _inspect_live_state(mgr, tmp_root)
        report["r02_probe_e_replace_success_unified"] = {
            "task_status": st_e.get("status"),
            "state": state_e,
            "passed": (
                st_e.get("status") == "completed"
                and state_e["db_codes"] == ["OK-001"]
                and state_e["fts_codes"] == ["OK-001"]
                and state_e["mem_codes"] == ["OK-001"]
                and state_e["cache_codes"] == ["OK-001"]
                and state_e["cache_count"] == 1
                and state_e["meta_row"] is not None
                and "probe_e.xlsx" in json.dumps(state_e["meta_row"], ensure_ascii=False)
            ),
        }

        out = ROOT / "doc/V0.1.5/evidence/m5_r02_audit_probes.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "r01_replay_1_passed": report["r01_replay_1_replace_index_fail"]["all_stayed_old"],
            "r01_replay_2_passed": report["r01_replay_2_alive_worker_409"]["passed"],
            "r01_replay_3_passed": report["r01_replay_3_publish_cancel_aborts"]["passed"],
            "r01_replay_4_5_passed": report["r01_replay_4_5_excel_boundary"]["passed"],
            "probe_a_split_reproduced": report["r02_probe_a_post_swap_cancel_split"]["split_brain_reproduced"],
            "probe_b_timeout_split_reproduced": report["r02_probe_b_real_timeout_after_sql_commit"]["split_brain_reproduced"],
            "probe_c_step7_split_reproduced": report["r02_probe_c_step7_cache_replace_failure"]["split_brain_reproduced"],
            "probe_d_append_split_reproduced": report["r02_probe_d_append_index_failure"]["append_split_reproduced"],
            "probe_e_success_passed": report["r02_probe_e_replace_success_unified"]["passed"],
        }, ensure_ascii=True, indent=2))
    finally:
        guard_after = cf.run_guardrail_checks()
        assert not guard_after["gaps"], guard_after


if __name__ == "__main__":
    main()
