# -*- coding: utf-8 -*-
"""
V0.1.5 M5 R04 确定性复验：
  - A-M5-06：缓存备份阶段（backup_move）第 0/1/2/3 次移动失败时不得误删未移动的旧文件，
    完整还原旧集合（含缺失目标与陈旧 FAISS 场景），并在恢复再失败时保留材料、进入阻断。
  - A-M5-07 + OBS-R03-1：DB 反向交换、内存恢复、缓存恢复任一失败均升级为
    recovery_failed + 持久化写入阻断（重启不可绕过），拒绝并发/后续导入、
    try_acquire() 与 /api/refresh_vector、/api/db/reparse、save_cache 等可达写入口；
    仅在孤立副本经真实一致性核验后才解除阻断。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import openpyxl
import pytest

np = pytest.importorskip("numpy")

from fastapi.testclient import TestClient

from app import auth_manager, audit_manager, db_manager, server, vector_index


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_xlsx_bytes(rows: List[List[Any]], sheet_name: str = "物料表") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def _replace_batch_xlsx() -> bytes:
    return _make_xlsx_bytes(
        [
            ["标准物料库导入模板"],
            ["物料分类", "物料编码", "物料名称", "规格/型号", "计量单位", "常用品牌", "描述"],
            ["风管", "R04-NEW-1", "新风管", "1000x500", "m2", "新牌", "后态物料甲"],
            ["阀门", "R04-NEW-2", "新调节阀", "DN100 PN16", "台", "新牌", "后态物料乙"],
        ]
    )


def _append_batch_xlsx() -> bytes:
    return _make_xlsx_bytes(
        [
            ["标准物料库导入模板"],
            ["物料分类", "物料编码", "物料名称", "规格/型号", "计量单位", "常用品牌", "描述"],
            ["水管", "R04-APP-1", "追加水管", "DN50", "m", "追加牌", "追加物料丙"],
        ]
    )


def _create_isolated_user(username: str, role: str, emp_no: str) -> Dict[str, Any]:
    conn = auth_manager.get_auth_connection()
    try:
        pw_hash = auth_manager.get_password_hash("R04Boundary#Strong2026!")
        epoch = secrets.token_hex(12)
        conn.execute(
            """
            INSERT OR REPLACE INTO users (
                username, password_hash, name, dept1, dept2, dept3,
                employee_no, position, phone, role, is_active, need_emp_no_setup,
                token_version, must_change_password, account_epoch
            ) VALUES (?, ?, ?, '工程部', '', '', ?, ?, ?, ?, 1, 0, 1, 0, ?)
            """,
            (username, pw_hash, f"R04_{role}", emp_no, role, username, role, epoch),
        )
        conn.commit()
    finally:
        conn.close()
    user = auth_manager.get_user_by_username(username)
    assert user is not None
    token = auth_manager.issue_token_for_user(user)
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture()
def r04_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "r04_iso"
    upload_dir = root / "uploads"
    output_dir = root / "output"
    log_dir = root / "logs"
    vec_dir = root / "vector_cache"
    for p in (upload_dir, output_dir, log_dir, vec_dir):
        p.mkdir(parents=True, exist_ok=True)

    auth_db = str(root / "auth.db")
    mat_db = str(root / "materials.db")
    fb_file = str(root / "binding_feedback.json")
    fs_file = str(root / "feedback_samples.json")

    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("AUTH_DB_PATH", auth_db)
    monkeypatch.setenv("MATERIALS_DB_PATH", mat_db)
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("LOG_DIR", str(log_dir))
    monkeypatch.setenv("VECTOR_CACHE_DIR", str(vec_dir))
    monkeypatch.setenv("BINDING_FEEDBACK_FILE", fb_file)
    monkeypatch.setenv("FEEDBACK_SAMPLES_FILE", fs_file)

    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", auth_db)
    monkeypatch.setattr(db_manager, "DB_PATH", mat_db)
    monkeypatch.setattr(server, "DB_PATH", mat_db)
    monkeypatch.setattr(server, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(server, "BINDING_FEEDBACK_FILE", fb_file)

    import app.feedback_train as ft

    monkeypatch.setattr(ft, "FEEDBACK_FILE", fs_file)

    vector_index.reset_cache_write_block()
    db_manager.reset_db_write_block(mat_db)

    auth_manager.init_auth_db()
    audit_manager.init_audit_tables()
    from app import session_manager

    session_manager.init_session_db()
    db_manager.init_db()

    if server._db_import_lock.locked():
        try:
            server._db_import_lock.release()
        except RuntimeError:
            pass
    server._db_import_lock_manager = server._new_db_import_lock_manager()

    emp = _create_isolated_user("13800003101", "employee", "EMP-R04-01")
    director = _create_isolated_user("13800003102", "director", "DIR-R04-01")

    client = TestClient(server.app)
    yield {
        "client": client,
        "emp": emp,
        "director": director,
        "upload_dir": upload_dir,
        "vec_dir": vec_dir,
        "mat_db": mat_db,
        "root": root,
    }

    vector_index.reset_cache_write_block()
    db_manager.reset_db_write_block(mat_db)
    if server._db_import_lock.locked():
        try:
            server._db_import_lock.release()
        except RuntimeError:
            pass


def _seed_old_generation(r04_env, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    conn = db_manager.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM materials")
        cur.execute("DELETE FROM materials_fts")
        cur.execute(
            "INSERT INTO materials(code, category, name, specification, unit, brand, description, "
            "search_tokens, parsed_params, parsed_schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 2)",
            ("R04-OLD-1", "阀门", "旧阀门", "DN15", "台", "旧牌", "前态物料", "old valve", "{}"),
        )
        cur.execute(
            "INSERT INTO materials_fts(code, category, name, specification, brand, description) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("R04-OLD-1", "阀门", "旧阀门", "DN15", "旧牌", "前态物料"),
        )
        conn.commit()
    finally:
        conn.close()
    db_manager.update_db_metadata(1, 1, 0, "r04_old_seed.xlsx")
    meta = db_manager.get_db_metadata()

    old_mapping = [
        {"code": "R04-OLD-1", "name": "旧阀门", "specification": "DN15", "category": "阀门"}
    ]
    mgr = server.index_manager
    monkeypatch.setattr(mgr, "model", object())
    monkeypatch.setattr(mgr, "embedding_dim", 2)
    monkeypatch.setattr(mgr, "_snapshot", vector_index.IndexCandidate(
        old_mapping, np.zeros((1, 2), dtype=np.float32), None, False,
        mgr.model_name_or_path, 2, db_manager.get_db_revision(),
    ))
    monkeypatch.setattr(
        mgr,
        "encode_queries",
        lambda texts, **kw: np.zeros((len(texts), 2), dtype=np.float32),
    )
    monkeypatch.setattr(vector_index, "HAS_FAISS", False)

    vec_dir: Path = r04_env["vec_dir"]
    (vec_dir / "index_to_material.json").write_text(
        json.dumps(old_mapping, ensure_ascii=False), encoding="utf-8"
    )
    (vec_dir / "cache_metadata.json").write_text(
        json.dumps({"model_name": mgr.model_name_or_path, "dimension": 2, "item_count": 1}),
        encoding="utf-8",
    )
    np.save(str(vec_dir / "materials_embeddings.npy"), np.zeros((1, 2), dtype=np.float32))
    (vec_dir / "materials.index").write_bytes(b"R04-OLD-FAISS-SIDECAR")
    return meta


def _write_cache_generation(directory: Path, marker: str, names=None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for idx, name in enumerate(names or vector_index.CACHE_FILE_NAMES):
        (directory / name).write_bytes(f"{marker}:{name}:{idx}".encode("utf-8"))


def _snapshot_cache_dir(directory: Path) -> Dict[str, Any]:
    files = sorted(p.name for p in directory.iterdir() if p.is_file()) if directory.is_dir() else []
    managed = {
        name: (
            {
                "size": (directory / name).stat().st_size,
                "sha256": _sha256_bytes((directory / name).read_bytes()),
            }
            if (directory / name).is_file()
            else None
        )
        for name in vector_index.CACHE_FILE_NAMES
    }
    return {
        "files": files,
        "managed": managed,
        "leftover_dirs": sorted(p.name for p in directory.glob(".*") if p.is_dir()),
    }


def _five_state(r04_env) -> Dict[str, Any]:
    db_state = db_manager.inspect_db_recovery_state()
    vec_dir: Path = r04_env["vec_dir"]
    mapping_file = vec_dir / "index_to_material.json"
    cache_codes = (
        [m.get("code") for m in json.loads(mapping_file.read_text(encoding="utf-8"))]
        if mapping_file.is_file()
        else []
    )
    return {
        "db_codes": db_state["db_codes"],
        "fts_codes": db_state["fts_codes"],
        "leftover_tables": db_state["leftover_tables"],
        "metadata": {
            k: (db_state["metadata"] or {}).get(k)
            for k in ("last_upload_date", "filename", "total_rows", "imported_rows", "disabled_rows")
        },
        "mem_codes": [m.get("code") for m in (server.index_manager.index_to_material or [])],
        "cache_codes": cache_codes,
        "cache_snapshot": _snapshot_cache_dir(vec_dir),
        "cache_write_blocked": vector_index.is_cache_write_blocked(),
        "db_write_blocked": db_manager.is_db_write_blocked(),
        "recovery_blocked": server.is_recovery_blocked(),
    }


def _post_import(client: TestClient, headers: Dict[str, str], filename: str, data: bytes, mode: str = "replace"):
    return client.post(
        "/api/db/upload",
        headers=headers,
        files={"file": (filename, data, "application/octet-stream")},
        data={"mode": mode},
    )


def _poll_terminal(client: TestClient, task_id: str, headers: Dict[str, str]) -> Dict[str, Any]:
    last: Dict[str, Any] = {}
    for _ in range(400):
        last = client.get(f"/api/upload/status?task_id={task_id}", headers=headers).json()
        if last.get("status") in ("completed", "failed"):
            return last
        time.sleep(0.02)
    return last


# ---------------------------------------------------------------------------
# A-M5-06：缓存备份移动阶段中断（0/1/2/3）与恢复完整性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fail_position", [0, 1, 2, 3])
def test_r04_am5_06_backup_move_failure_preserves_all_old_files(r04_env, monkeypatch, fail_position):
    mgr = vector_index.VectorIndexManager()
    target = r04_env["vec_dir"] / f"case_bak_fail_{fail_position}"
    stage = target / f".stage_bak_{fail_position}"
    _write_cache_generation(target, "OLD")
    _write_cache_generation(stage, "NEW")
    before = _snapshot_cache_dir(target)

    real_replace = os.replace
    backup_attempts: List[str] = []

    def _fail_backup_move(src, dst):
        src_p, dst_p = Path(src), Path(dst)
        if src_p.parent == target and dst_p.parent.name.startswith(".backup_"):
            backup_attempts.append(src_p.name)
            if len(backup_attempts) == fail_position + 1:
                raise OSError(f"synthetic backup move failure at {fail_position}")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _fail_backup_move)
    with pytest.raises(OSError, match="synthetic backup move failure"):
        mgr.commit_candidate_cache(str(stage), target_dir=str(target))

    after = _snapshot_cache_dir(target)
    assert after["managed"] == before["managed"], json.dumps({"before": before, "after": after}, ensure_ascii=False)
    assert after["files"] == before["files"]
    assert after["leftover_dirs"] == []
    assert not stage.exists()
    assert vector_index.is_cache_write_blocked(str(target)) is False


def test_r04_am5_06_backup_move_failure_with_missing_target_and_stale_faiss(r04_env, monkeypatch):
    mgr = vector_index.VectorIndexManager()
    target = r04_env["vec_dir"] / "case_bak_missing_stale"
    stage = target / ".stage_bak_missing"
    # 旧代缺少 materials_embeddings.npy，但有旧 materials.index；新代无 materials.index
    old_names = ("materials.index", "index_to_material.json", "cache_metadata.json")
    new_names = ("materials_embeddings.npy", "index_to_material.json", "cache_metadata.json")
    _write_cache_generation(target, "OLD", names=old_names)
    _write_cache_generation(stage, "NEW", names=new_names)
    before = _snapshot_cache_dir(target)

    real_replace = os.replace
    backup_attempts: List[str] = []

    def _fail_second_backup(src, dst):
        src_p, dst_p = Path(src), Path(dst)
        if src_p.parent == target and dst_p.parent.name.startswith(".backup_"):
            backup_attempts.append(src_p.name)
            if len(backup_attempts) == 2:
                raise OSError("synthetic backup move #2 failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _fail_second_backup)
    with pytest.raises(OSError, match="backup move #2 failure"):
        mgr.commit_candidate_cache(str(stage), target_dir=str(target))

    after = _snapshot_cache_dir(target)
    assert after["managed"] == before["managed"]
    assert after["files"] == sorted(old_names)
    assert not (target / "materials_embeddings.npy").exists()
    assert after["leftover_dirs"] == []
    assert vector_index.is_cache_write_blocked(str(target)) is False


@pytest.mark.parametrize("fail_position", [0, 1, 2])
def test_r04_am5_06_candidate_promote_failure_first_mid_last_with_missing_and_stale(r04_env, monkeypatch, fail_position):
    mgr = vector_index.VectorIndexManager()
    target = r04_env["vec_dir"] / f"case_prom_fail_{fail_position}"
    stage = target / f".stage_prom_{fail_position}"
    # 旧代：含陈旧 materials.index，缺 materials_embeddings.npy；新代：3 个文件（无 materials.index）
    old_names = ("materials.index", "index_to_material.json", "cache_metadata.json")
    new_names = ("materials_embeddings.npy", "index_to_material.json", "cache_metadata.json")
    _write_cache_generation(target, "OLD", names=old_names)
    _write_cache_generation(stage, "NEW", names=new_names)
    before = _snapshot_cache_dir(target)

    real_replace = os.replace
    promote_attempts: List[str] = []

    def _fail_promote(src, dst):
        src_p, dst_p = Path(src), Path(dst)
        if src_p.parent == stage and dst_p.parent == target:
            promote_attempts.append(src_p.name)
            if len(promote_attempts) == fail_position + 1:
                raise OSError(f"synthetic promote failure at {fail_position}")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _fail_promote)
    with pytest.raises(OSError, match="synthetic promote failure"):
        mgr.commit_candidate_cache(str(stage), target_dir=str(target))

    after = _snapshot_cache_dir(target)
    assert after["managed"] == before["managed"]
    assert after["files"] == sorted(old_names)
    assert not (target / "materials_embeddings.npy").exists()
    assert after["leftover_dirs"] == []
    assert vector_index.is_cache_write_blocked(str(target)) is False


def test_r04_am5_06_backup_move_and_restore_double_failure_blocks_and_preserves(r04_env, monkeypatch):
    mgr = vector_index.VectorIndexManager()
    target = r04_env["vec_dir"] / "case_bak_double"
    stage = target / ".stage_bak_double"
    _write_cache_generation(target, "OLD")
    _write_cache_generation(stage, "NEW")
    before = _snapshot_cache_dir(target)

    real_replace = os.replace
    backup_attempts: List[str] = []

    def _fail_backup_and_restore(src, dst):
        src_p, dst_p = Path(src), Path(dst)
        if src_p.parent == target and dst_p.parent.name.startswith(".backup_"):
            backup_attempts.append(src_p.name)
            if len(backup_attempts) == 2:
                raise OSError("backup move failure")
        if src_p.parent.name.startswith(".backup_") and dst_p.parent == target:
            raise OSError("backup restore failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _fail_backup_and_restore)
    with pytest.raises(vector_index.CacheRollbackError, match="写入阻断"):
        mgr.commit_candidate_cache(str(stage), target_dir=str(target))

    assert vector_index.is_cache_write_blocked(str(target)) is True
    # 恢复材料（.stage_* 与 .backup_*）以及尚未备份的 3 个旧文件必须完整保留
    assert stage.is_dir()
    backups = list(target.glob(".backup_*"))
    assert len(backups) == 1
    assert (backups[0] / "materials.index").is_file()
    for unmoved in ("materials_embeddings.npy", "index_to_material.json", "cache_metadata.json"):
        assert (target / unmoved).is_file()
        assert _sha256_bytes((target / unmoved).read_bytes()) == before["managed"][unmoved]["sha256"]


def test_r04_am5_06_server_backup_move_failure_rolls_back_all_five_layers(r04_env, monkeypatch):
    client: TestClient = r04_env["client"]
    headers = r04_env["director"]["headers"]
    vec_dir: Path = r04_env["vec_dir"]
    _seed_old_generation(r04_env, monkeypatch)
    before = _five_state(r04_env)

    real_replace = os.replace
    backup_attempts: List[str] = []

    def _fail_second_backup(src, dst):
        src_p, dst_p = Path(src), Path(dst)
        if src_p.parent == vec_dir and dst_p.parent.name.startswith(".backup_"):
            backup_attempts.append(src_p.name)
            if len(backup_attempts) == 2:
                raise OSError("server backup move #2 failure")
        return real_replace(src, dst)

    with monkeypatch.context() as mp:
        mp.setattr(os, "replace", _fail_second_backup)
        resp = _post_import(client, headers, "bak_fail.xlsx", _replace_batch_xlsx())
        assert resp.status_code == 200
        st = _poll_terminal(client, resp.json()["task_id"], headers)

    after = _five_state(r04_env)
    assert st["status"] == "failed"
    assert st.get("recovery_failed") is False
    assert st.get("recovery_status") == "rolled_back"
    assert after["db_codes"] == ["R04-OLD-1"]
    assert after["fts_codes"] == ["R04-OLD-1"]
    assert after["leftover_tables"] == []
    assert after["mem_codes"] == ["R04-OLD-1"]
    assert after["cache_snapshot"]["managed"] == before["cache_snapshot"]["managed"]
    assert after["cache_snapshot"]["leftover_dirs"] == []
    assert after["recovery_blocked"] is False

    # 恢复可写：下一次正常导入成功
    ok_resp = _post_import(client, headers, "after_bak_ok.xlsx", _replace_batch_xlsx())
    assert ok_resp.status_code == 200
    ok_st = _poll_terminal(client, ok_resp.json()["task_id"], headers)
    assert ok_st["status"] == "completed"


# ---------------------------------------------------------------------------
# A-M5-07 + OBS-R03-1：DB/内存/缓存任一恢复失败统一隔离、全写入口阻断、重启不绕过、核验后恢复
# ---------------------------------------------------------------------------


def test_r04_am5_07_db_rollback_failure_blocks_concurrent_and_followup_writes(r04_env, monkeypatch):
    client: TestClient = r04_env["client"]
    dir_headers = r04_env["director"]["headers"]
    emp_headers = r04_env["emp"]["headers"]
    _seed_old_generation(r04_env, monkeypatch)
    before = _five_state(r04_env)

    rb_entered = threading.Event()
    rb_release = threading.Event()

    def _parked_db_reverse(*args, **kwargs):
        rb_entered.set()
        assert rb_release.wait(timeout=10.0)
        raise RuntimeError("SYNTHETIC_DB_REVERSE_FAILURE")

    holder: Dict[str, Any] = {}

    def _post_worker_1():
        holder["resp"] = _post_import(client, dir_headers, "db_rb_fail.xlsx", _replace_batch_xlsx())

    post_thread = threading.Thread(target=_post_worker_1, name="r04-db-rb-fail")
    with monkeypatch.context() as mp:
        mp.setattr(server.index_manager, "publish_candidate", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("SYNTHETIC_MEMORY_PUBLISH_FAILURE")))
        mp.setattr(db_manager, "reverse_retained_materials_swap", _parked_db_reverse)

        post_thread.start()
        try:
            assert rb_entered.wait(timeout=5.0)
            # 回滚进行中：并发第二导入必须 409，且不会释放 worker 1 的锁或触碰数据
            concurrent = _post_import(client, dir_headers, "concurrent_2.xlsx", _replace_batch_xlsx())
            assert concurrent.status_code == 409
            assert server._db_import_lock.locked()
            rb_release.set()
        finally:
            post_thread.join(timeout=15.0)
        assert not post_thread.is_alive()

    resp = holder["resp"]
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    st = _poll_terminal(client, task_id, dir_headers)

    after = _five_state(r04_env)
    assert st["status"] == "failed"
    assert st["recovery_failed"] is True
    assert st["recovery_status"] == "recovery_failed_manual_required"
    assert st["write_blocked"] is True
    assert st["layer_results"]["db"] == "rollback_failed"
    assert "db" in st["recovery_errors"]
    assert "恢复失败/需人工处置" in st["error"] and "写入阻断" in st["error"]

    # 恢复材料保留：SQLite 留存 materials_old/materials_fts_old；缓存目录留存 .stage_*
    assert "materials_old" in after["leftover_tables"]
    assert any(d.startswith(".stage_") for d in after["cache_snapshot"]["leftover_dirs"])
    assert after["db_codes"] == ["R04-NEW-1", "R04-NEW-2"]
    assert after["mem_codes"] == ["R04-OLD-1"]
    assert after["cache_snapshot"]["managed"] == before["cache_snapshot"]["managed"]
    assert after["db_write_blocked"] is True
    assert after["cache_write_blocked"] is True
    assert after["recovery_blocked"] is True

    # 后续导入锁不可取得；HTTP 导入/刷新/重解析全部 409 拒绝
    assert server._db_import_lock_manager.try_acquire() is None
    followup = _post_import(client, dir_headers, "followup.xlsx", _replace_batch_xlsx())
    assert followup.status_code == 409
    assert "恢复失败/需人工处置" in followup.json()["detail"]

    r_refresh = client.post("/api/refresh_vector", headers=emp_headers)
    assert r_refresh.status_code == 409
    assert "写入阻断" in r_refresh.json()["detail"]

    r_reparse = client.post("/api/db/reparse", headers=dir_headers)
    assert r_reparse.status_code == 409
    assert "写入阻断" in r_reparse.json()["detail"]

    # 直接调用后台任务入口也必须拒绝且不破坏恢复材料
    direct_tid = "r04_direct_blocked"
    server.set_task_initial(
        direct_tid,
        {"status": "processing", "result": None, "error": None},
        owner_user=r04_env["director"]["user"],
    )
    probe_xlsx = r04_env["upload_dir"] / "direct_blocked.xlsx"
    probe_xlsx.write_bytes(_replace_batch_xlsx())
    server.async_process_db_upload_task(direct_tid, str(probe_xlsx), "replace", "direct_blocked.xlsx")
    direct_st = server.get_task_status(direct_tid)
    assert direct_st["status"] == "failed" and direct_st["recovery_failed"] is True
    assert _five_state(r04_env)["cache_snapshot"]["managed"] == before["cache_snapshot"]["managed"]


def test_r04_am5_07_memory_rollback_failure_blocks_and_preserves(r04_env, monkeypatch):
    client: TestClient = r04_env["client"]
    dir_headers = r04_env["director"]["headers"]
    vec_dir: Path = r04_env["vec_dir"]
    _seed_old_generation(r04_env, monkeypatch)

    real_replace = os.replace

    def _fail_step7(src, dst):
        if ".stage_" in str(src):
            raise OSError("simulated step7 failure")
        return real_replace(src, dst)

    with monkeypatch.context() as mp:
        mp.setattr(os, "replace", _fail_step7)
        mp.setattr(
            server.index_manager,
            "rollback_published_state",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("SYNTHETIC_MEM_ROLLBACK_FAILURE")),
        )
        resp = _post_import(client, dir_headers, "mem_rb_fail.xlsx", _replace_batch_xlsx())
        assert resp.status_code == 200
        st = _poll_terminal(client, resp.json()["task_id"], dir_headers)

    assert st["status"] == "failed"
    assert st["recovery_failed"] is True
    assert st["recovery_status"] == "recovery_failed_manual_required"
    assert st["write_blocked"] is True
    assert st["layer_results"] == {"db": "rolled_back", "memory": "rollback_failed", "cache": "rolled_back"}
    assert "memory" in st["recovery_errors"]
    assert server.is_recovery_blocked() is True
    assert server._db_import_lock_manager.try_acquire() is None
    assert _post_import(client, dir_headers, "blocked_after_mem.xlsx", _replace_batch_xlsx()).status_code == 409
    # DB 与磁盘缓存已回旧态，但内存仍为 R04-NEW-1/2，核验接口必须拒绝解除
    deny_mem = client.post("/api/admin/recovery/verify-clear", headers=dir_headers)
    assert deny_mem.status_code == 409 and deny_mem.json()["detail"]["cleared"] is False
    # 从磁盘缓存重新载入旧态至内存后核验通过
    assert server.index_manager.load_cache() is True
    ok_mem = client.post("/api/admin/recovery/verify-clear", headers=dir_headers)
    assert ok_mem.status_code == 200 and ok_mem.json()["cleared"] is True


def test_r04_am5_07_restart_cannot_bypass_block_and_all_write_entries_reject(r04_env, monkeypatch):
    client: TestClient = r04_env["client"]
    dir_headers = r04_env["director"]["headers"]
    emp_headers = r04_env["emp"]["headers"]
    _seed_old_generation(r04_env, monkeypatch)
    before = _five_state(r04_env)

    # 触发 DB 反向回滚失败，写入持久化阻断标记
    with monkeypatch.context() as mp:
        mp.setattr(server.index_manager, "publish_candidate", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("MEM_FAIL")))
        mp.setattr(db_manager, "reverse_retained_materials_swap", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("DB_REV_FAIL")))
        resp = _post_import(client, dir_headers, "persist_block.xlsx", _replace_batch_xlsx())
        assert resp.status_code == 200
        st = _poll_terminal(client, resp.json()["task_id"], dir_headers)
        assert st["recovery_failed"] is True

    # 模拟进程重启：仅清内存布尔位与重建锁/索引管理器实例，不触碰磁盘持久化阻断标记
    vector_index._cache_write_blocked = False
    vector_index._recovery_block_details = None
    db_manager._db_write_blocked_paths.clear()
    db_manager._db_recovery_details_by_path.clear()
    server._db_import_lock_manager = server._new_db_import_lock_manager()
    fresh_mgr = vector_index.VectorIndexManager()

    assert server.is_recovery_blocked() is True
    assert server._db_import_lock_manager.try_acquire() is None
    assert _post_import(client, dir_headers, "after_restart.xlsx", _replace_batch_xlsx()).status_code == 409
    assert client.post("/api/refresh_vector", headers=emp_headers).status_code == 409
    assert client.post("/api/db/reparse", headers=dir_headers).status_code == 409

    # OBS-R03-1：所有遗留与维护写入口在阻断期全部显式拒绝，绝不覆盖旧缓存/恢复材料
    with pytest.raises(vector_index.CacheRollbackError):
        server.index_manager.save_cache()
    with pytest.raises(vector_index.CacheRollbackError):
        fresh_mgr.save_cache()
    with pytest.raises(vector_index.CacheRollbackError):
        server.index_manager.build_index([{"code": "X", "name": "X", "specification": "", "category": ""}])
    with pytest.raises(vector_index.CacheRollbackError):
        server.index_manager.add_to_index({"code": "X", "name": "X", "specification": "", "category": ""})
    with pytest.raises(vector_index.CacheRollbackError):
        server.index_manager.check_db_timestamp_and_build(r04_env["mat_db"], force=True)

    with pytest.raises(db_manager.RecoveryBlockedError):
        db_manager.reparse_materials_db_asymmetric()
    with pytest.raises(db_manager.RecoveryBlockedError):
        db_manager.reparse_materials_db_partial(codes=["R04-NEW-1"])
    with pytest.raises(db_manager.RecoveryBlockedError):
        dummy_xlsx = r04_env["upload_dir"] / "blocked_direct.xlsx"
        dummy_xlsx.write_bytes(_replace_batch_xlsx())
        db_manager.import_materials_from_excel(str(dummy_xlsx), mode="replace")

    assert _five_state(r04_env)["cache_snapshot"]["managed"] == before["cache_snapshot"]["managed"]


def test_r04_am5_07_verified_manual_recovery_drill_on_isolated_copy(r04_env, monkeypatch):
    client: TestClient = r04_env["client"]
    dir_headers = r04_env["director"]["headers"]
    emp_headers = r04_env["emp"]["headers"]
    vec_dir: Path = r04_env["vec_dir"]
    old_meta = _seed_old_generation(r04_env, monkeypatch)

    # 1. 制造真实 DB 回滚失败：DB=NEW(+materials_old)，内存=OLD，缓存=OLD(+.stage_*)
    with monkeypatch.context() as mp:
        mp.setattr(server.index_manager, "publish_candidate", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("MEM_FAIL")))
        mp.setattr(db_manager, "reverse_retained_materials_swap", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("DB_REV_FAIL")))
        resp = _post_import(client, dir_headers, "drill_fail.xlsx", _replace_batch_xlsx())
        assert resp.status_code == 200
        assert _poll_terminal(client, resp.json()["task_id"], dir_headers)["recovery_failed"] is True

    # 2. 未做真实一致性恢复前调用核验解除接口：必须拒绝（409），阻断继续保持
    deny_1 = client.post("/api/admin/recovery/verify-clear", headers=dir_headers)
    assert deny_1.status_code == 409
    assert deny_1.json()["detail"]["cleared"] is False
    assert server.is_recovery_blocked() is True

    # 3. 仅修复 DB 但未清理 .stage_* 残留：仍必须拒绝（409）
    db_manager.reverse_retained_materials_swap(old_meta)
    deny_2 = client.post("/api/admin/recovery/verify-clear", headers=dir_headers)
    assert deny_2.status_code == 409
    assert deny_2.json()["detail"]["cleared"] is False
    assert server.is_recovery_blocked() is True

    # 4. 完成全部残留清理（在孤立副本中核验全层一致旧态 R04-OLD-1）：核验通过并解除阻断
    for stage_dir in vec_dir.glob(".stage_*"):
        shutil.rmtree(stage_dir)
    ok_clear = client.post("/api/admin/recovery/verify-clear", headers=dir_headers)
    assert ok_clear.status_code == 200
    assert ok_clear.json()["cleared"] is True
    assert ok_clear.json()["verified_count"] == 1
    assert server.is_recovery_blocked() is False

    # 5. 解除后正常 replace/append 导入与 /api/refresh_vector 均可恢复正常
    r_app = _post_import(client, dir_headers, "drill_append.xlsx", _append_batch_xlsx(), mode="append")
    assert r_app.status_code == 200
    st_app = _poll_terminal(client, r_app.json()["task_id"], dir_headers)
    assert st_app["status"] == "completed"
    after = _five_state(r04_env)
    assert set(after["db_codes"]) == {"R04-OLD-1", "R04-APP-1"}
    assert set(after["mem_codes"]) == {"R04-OLD-1", "R04-APP-1"}
    assert set(after["cache_codes"]) == {"R04-OLD-1", "R04-APP-1"}
    assert client.post("/api/refresh_vector", headers=emp_headers).status_code == 200
