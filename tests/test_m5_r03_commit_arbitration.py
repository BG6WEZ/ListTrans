# -*- coding: utf-8 -*-
"""
V0.1.5 M5 R03 确定性复验：A-M5-01 五态发布一致性/缓存整套可逆回滚，
A-M5-02 提交仲裁状态机（staging -> cancelled / committing -> committed|rolled_back）。

设计约束（依据 V015-M5_R02_统筹裁决.md L40-51）：
1. 全部卡点使用 threading.Event / Barrier，不使用随机 sleep；唯一墙钟等待是被显式
   调小的任务超时（与 R02 既有 409 恢复测试同型），超时后由 Event 确定性放行。
2. 每个服务器级用例都保留前/后五态摘要（db/fts/metadata/mem/cache + 写阻断标志 +
   stage 残留）、任务终态与原因、导入锁 owner 证据，断言消息携带完整摘要。
3. 不删除、不弱化 R01/R02 任何既有断言；本文件为纯增量用例。
4. 全程合成数据、隔离临时目录、LLM_ENABLED=false，不触业务库/账号库/规则库/付费模型。
"""

from __future__ import annotations

import io
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import openpyxl
import pytest

np = pytest.importorskip("numpy")

from fastapi.testclient import TestClient

from app import auth_manager, audit_manager, db_manager, excel_boundary, server, vector_index


# ---------------------------------------------------------------------------
# 合成数据与隔离夹具
# ---------------------------------------------------------------------------


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
            ["风管", "R03-NEW-1", "新风管", "1000x500", "m2", "新牌", "后态物料甲"],
            ["阀门", "R03-NEW-2", "新调节阀", "DN100 PN16", "台", "新牌", "后态物料乙"],
        ]
    )


def _append_batch_xlsx() -> bytes:
    return _make_xlsx_bytes(
        [
            ["标准物料库导入模板"],
            ["物料分类", "物料编码", "物料名称", "规格/型号", "计量单位", "常用品牌", "描述"],
            ["水管", "R03-APP-1", "追加水管", "DN50", "m", "追加牌", "追加物料丙"],
            ["弯头", "R03-APP-2", "追加弯头", "DN80 90°", "个", "追加牌", "追加物料丁"],
        ]
    )


def _create_isolated_user(username: str, role: str, emp_no: str) -> Dict[str, Any]:
    conn = auth_manager.get_auth_connection()
    try:
        pw_hash = auth_manager.get_password_hash("R03Boundary#Strong2026!")
        epoch = secrets.token_hex(12)
        conn.execute(
            """
            INSERT OR REPLACE INTO users (
                username, password_hash, name, dept1, dept2, dept3,
                employee_no, position, phone, role, is_active, need_emp_no_setup,
                token_version, must_change_password, account_epoch
            ) VALUES (?, ?, ?, '工程部', '', '', ?, ?, ?, ?, 1, 0, 1, 0, ?)
            """,
            (username, pw_hash, f"R03_{role}", emp_no, role, username, role, epoch),
        )
        conn.commit()
    finally:
        conn.close()
    user = auth_manager.get_user_by_username(username)
    assert user is not None
    token = auth_manager.issue_token_for_user(user)
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture()
def r03_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "r03_iso"
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

    auth_manager.init_auth_db()
    audit_manager.init_audit_tables()
    from app import session_manager

    session_manager.init_session_db()
    db_manager.init_db()

    # R03/R04: 缓存与数据库恢复阻断在夹具前后显式复位，杜绝跨用例串扰。
    vector_index.reset_cache_write_block()
    db_manager.reset_db_write_block()

    if server._db_import_lock.locked():
        try:
            server._db_import_lock.release()
        except RuntimeError:
            pass
    server._db_import_lock_manager = server._new_db_import_lock_manager()

    director = _create_isolated_user("13800002101", "director", "DIR-R03-01")

    client = TestClient(server.app)
    yield {
        "client": client,
        "director": director,
        "upload_dir": upload_dir,
        "vec_dir": vec_dir,
        "root": root,
    }

    vector_index.reset_cache_write_block()
    db_manager.reset_db_write_block()
    if server._db_import_lock.locked():
        try:
            server._db_import_lock.release()
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# 五态检视与播种 helper
# ---------------------------------------------------------------------------


def _seed_old_generation(r03_env, monkeypatch: pytest.MonkeyPatch):
    """播种 R03-OLD-1 到 DB/FTS/metadata、内存索引与磁盘缓存，作为可核验前态。"""
    conn = db_manager.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM materials")
        cur.execute("DELETE FROM materials_fts")
        cur.execute(
            "INSERT INTO materials(code, category, name, specification, unit, brand, description, "
            "search_tokens, parsed_params, parsed_schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 2)",
            ("R03-OLD-1", "阀门", "旧阀门", "DN15", "台", "旧牌", "前态物料", "old valve", "{}"),
        )
        cur.execute(
            "INSERT INTO materials_fts(code, category, name, specification, brand, description) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("R03-OLD-1", "阀门", "旧阀门", "DN15", "旧牌", "前态物料"),
        )
        conn.commit()
    finally:
        conn.close()
    db_manager.update_db_metadata(1, 1, 0, "r03_old_seed.xlsx")

    old_mapping = [
        {"code": "R03-OLD-1", "name": "旧阀门", "specification": "DN15", "category": "阀门"}
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

    vec_dir: Path = r03_env["vec_dir"]
    (vec_dir / "index_to_material.json").write_text(
        json.dumps(old_mapping, ensure_ascii=False), encoding="utf-8"
    )
    (vec_dir / "cache_metadata.json").write_text(
        json.dumps({"model_name": "r03-old", "dimension": 2, "item_count": 1}),
        encoding="utf-8",
    )
    np.save(str(vec_dir / "materials_embeddings.npy"), np.zeros((1, 2), dtype=np.float32))
    (vec_dir / "materials.index").write_bytes(b"R03-OLD-FAISS-SIDECAR")


def _db_codes() -> List[str]:
    conn = db_manager.get_db_connection()
    try:
        return [r[0] for r in conn.execute("SELECT code FROM materials ORDER BY code")]
    finally:
        conn.close()


def _fts_codes() -> List[str]:
    conn = db_manager.get_db_connection()
    try:
        try:
            return [r[0] for r in conn.execute("SELECT code FROM materials_fts ORDER BY code")]
        except Exception:
            return []
    finally:
        conn.close()


def _stage_residue() -> List[str]:
    conn = db_manager.get_db_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%r03_stage%' OR name LIKE '%_stage%'"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _mem_codes() -> List[str]:
    return [m.get("code") for m in (server.index_manager.index_to_material or [])]


def _five_state(r03_env) -> Dict[str, Any]:
    """五态 + 写阻断 + 残留目录的 JSON 安全快照，用于前后对比与断言证据。"""
    vec_dir: Path = r03_env["vec_dir"]
    mapping_file = vec_dir / "index_to_material.json"
    meta_file = vec_dir / "cache_metadata.json"
    cache_codes = (
        [m.get("code") for m in json.loads(mapping_file.read_text(encoding="utf-8"))]
        if mapping_file.exists()
        else []
    )
    cache_count = (
        json.loads(meta_file.read_text(encoding="utf-8")).get("item_count")
        if meta_file.exists()
        else None
    )
    leftover_dirs = sorted(p.name for p in vec_dir.glob(".*") if p.is_dir())
    return {
        "db_codes": _db_codes(),
        "fts_codes": _fts_codes(),
        "metadata": {
            k: db_manager.get_db_metadata().get(k)
            for k in ("last_upload_date", "filename", "total_rows", "imported_rows", "disabled_rows")
        },
        "mem_codes": _mem_codes(),
        "cache_codes": cache_codes,
        "cache_count": cache_count,
        "cache_write_blocked": vector_index.is_cache_write_blocked(),
        "stage_tables": _stage_residue(),
        "leftover_cache_dirs": leftover_dirs,
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


class _RecordingArbiter(server.ImportCommitArbiter):
    """记录每次任务的仲裁器实例，供用例直接核验状态机迁移。"""

    instances: List["_RecordingArbiter"] = []

    def __init__(self) -> None:
        super().__init__()
        _RecordingArbiter.instances.append(self)


# ---------------------------------------------------------------------------
# 一、ImportCommitArbiter 纯状态机单元测试（无 I/O）
# ---------------------------------------------------------------------------


def test_r03_arbiter_cancel_wins_before_commit_gate():
    arbiter = server.ImportCommitArbiter()
    assert arbiter.state == server.ImportCommitArbiter.STAGING

    assert arbiter.request_cancel() is True
    assert arbiter.state == server.ImportCommitArbiter.CANCELLED
    # 门点必须拒绝已被取消的提交请求。
    assert arbiter.begin_commit() is False
    assert arbiter.wait_terminal(timeout=0.1) == server.ImportCommitArbiter.CANCELLED
    # 终态后迟到的取消/提交均不得再迁移状态。
    assert arbiter.request_cancel() is False
    assert arbiter.begin_commit() is False


def test_r03_arbiter_commit_gate_revokes_late_cancel():
    arbiter = server.ImportCommitArbiter()
    assert arbiter.begin_commit() is True
    assert arbiter.state == server.ImportCommitArbiter.COMMITTING
    # 提交获准后，监督线程的迟到超时/取消必须落败。
    assert arbiter.request_cancel() is False
    assert arbiter.state == server.ImportCommitArbiter.COMMITTING

    arbiter.finish_commit()
    assert arbiter.state == server.ImportCommitArbiter.COMMITTED
    assert arbiter.wait_terminal(timeout=0.1) == server.ImportCommitArbiter.COMMITTED
    assert arbiter.request_cancel() is False


def test_r03_arbiter_rollback_terminal_carries_reason():
    arbiter = server.ImportCommitArbiter()
    arbiter.begin_commit()
    arbiter.fail_commit("SYNTHETIC_ROLLBACK_REASON")
    assert arbiter.state == server.ImportCommitArbiter.ROLLED_BACK
    assert arbiter.terminal_reason == "SYNTHETIC_ROLLBACK_REASON"
    assert arbiter.wait_terminal(timeout=0.1) == server.ImportCommitArbiter.ROLLED_BACK
    # 回滚终态同样不可被迟到取消改写。
    assert arbiter.request_cancel() is False


def test_r03_arbiter_parallel_cas_has_exactly_one_winner():
    # 真并发不变量：begin_commit 与 request_cancel 同时冲撞时，每轮恰有一方胜出，
    # 绝不出现双胜（脑裂）或双败（悬挂）。重复 20 轮覆盖两种胜出次序。
    winners = {"commit": 0, "cancel": 0}
    for _ in range(20):
        arbiter = server.ImportCommitArbiter()
        barrier = threading.Barrier(2)
        outcomes: List[str] = []

        def _try_commit() -> None:
            barrier.wait()
            if arbiter.begin_commit():
                outcomes.append("commit")

        def _try_cancel() -> None:
            barrier.wait()
            if arbiter.request_cancel():
                outcomes.append("cancel")

        t1 = threading.Thread(target=_try_commit)
        t2 = threading.Thread(target=_try_cancel)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert len(outcomes) == 1, f"CAS 冲撞必须恰有一个胜者: {outcomes}"
        winners[outcomes[0]] += 1
        assert arbiter.state in (
            server.ImportCommitArbiter.COMMITTING,
            server.ImportCommitArbiter.CANCELLED,
        )
        assert arbiter.wait_terminal(timeout=0.1) in (
            server.ImportCommitArbiter.COMMITTING,
            server.ImportCommitArbiter.CANCELLED,
        )
    # 20 轮 Barrier 冲撞在实践中必然两种次序都出现；保留统计证据但不依赖时序做核心断言。
    assert winners["commit"] + winners["cancel"] == 20


# ---------------------------------------------------------------------------
# 二、db_manager：append 暂存化、retain/reverse 世代仲裁
# ---------------------------------------------------------------------------


def test_r03_append_stage_copies_live_generation_then_merges_on_swap(r03_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _seed_old_generation(r03_env, monkeypatch)
    xlsx_path = tmp_path / "append_stage.xlsx"
    xlsx_path.write_bytes(_append_batch_xlsx())

    # 暂存阶段：live 世代完全不动，stage 表 = 旧世代 + 本批。
    handle = db_manager.import_materials_from_excel(
        str(xlsx_path), mode="append", filename=xlsx_path.name, stage_only=True
    )
    assert handle["stage_only"] is True
    assert handle["total_rows"] == 3 and handle["imported_rows"] == 3
    assert _db_codes() == ["R03-OLD-1"]
    assert _fts_codes() == ["R03-OLD-1"]
    assert db_manager.get_db_metadata()["filename"] == "r03_old_seed.xlsx"

    conn = db_manager.get_db_connection()
    try:
        stage_codes = [r[0] for r in conn.execute("SELECT code FROM materials_stage ORDER BY code")]
        stage_fts = [r[0] for r in conn.execute("SELECT code FROM materials_fts_stage ORDER BY code")]
    finally:
        conn.close()
    assert stage_codes == ["R03-APP-1", "R03-APP-2", "R03-OLD-1"]
    assert stage_fts == ["R03-APP-1", "R03-APP-2", "R03-OLD-1"]

    # retain 交换：新世代发表，旧世代保留待全部层成功后丢弃。
    meta = db_manager.commit_staged_materials_import(handle, retain_old_generation=True)
    assert _db_codes() == ["R03-APP-1", "R03-APP-2", "R03-OLD-1"]
    assert _fts_codes() == ["R03-APP-1", "R03-APP-2", "R03-OLD-1"]
    assert meta["filename"] == xlsx_path.name and meta["total_rows"] == 3
    conn = db_manager.get_db_connection()
    try:
        old_codes = [r[0] for r in conn.execute("SELECT code FROM materials_old ORDER BY code")]
        assert old_codes == ["R03-OLD-1"]
        assert _stage_residue() == []
    finally:
        conn.close()

    db_manager.drop_retained_materials_old_generation()
    conn = db_manager.get_db_connection()
    try:
        retained = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE name IN ('materials_old', 'materials_fts_old')"
            )
        ]
        assert retained == []
    finally:
        conn.close()
    assert _db_codes() == ["R03-APP-1", "R03-APP-2", "R03-OLD-1"]


def test_r03_replace_retained_swap_reverse_restores_old_and_metadata(r03_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _seed_old_generation(r03_env, monkeypatch)
    previous_meta = db_manager.get_db_metadata()
    xlsx_path = tmp_path / "replace_retain.xlsx"
    xlsx_path.write_bytes(_replace_batch_xlsx())

    handle = db_manager.import_materials_from_excel(
        str(xlsx_path), mode="replace", filename=xlsx_path.name, stage_only=True
    )
    db_manager.commit_staged_materials_import(handle, retain_old_generation=True)
    assert set(_db_codes()) == {"R03-NEW-1", "R03-NEW-2"}

    # 联动回滚：单 EXCLUSIVE 事务反向交换并还原 metadata 五键。
    restored = db_manager.reverse_retained_materials_swap(previous_meta)
    assert _db_codes() == ["R03-OLD-1"]
    assert _fts_codes() == ["R03-OLD-1"]
    assert restored["filename"] == "r03_old_seed.xlsx"
    assert restored["total_rows"] == 1 and restored["imported_rows"] == 1
    assert restored["disabled_rows"] == 0
    assert restored["last_upload_date"] == previous_meta["last_upload_date"]
    conn = db_manager.get_db_connection()
    try:
        idx_rows = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name IN "
                "('idx_materials_code', 'idx_materials_category')"
            )
        ]
        assert set(idx_rows) == {"idx_materials_code", "idx_materials_category"}
        assert _stage_residue() == []
        leftovers = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE name IN ('materials_old', 'materials_fts_old')"
            )
        ]
        assert leftovers == []
    finally:
        conn.close()


def test_r03_append_fts_failure_keeps_live_generation_zero_residue(r03_env, tmp_path: Path, monkeypatch):
    from app import preprocessor

    _seed_old_generation(r03_env, monkeypatch)
    xlsx_path = tmp_path / "append_fts_fail.xlsx"
    xlsx_path.write_bytes(_append_batch_xlsx())

    real_tokenize = preprocessor.fts_tokenize
    cancel = threading.Event()

    def _arm_cancel(text):
        cancel.set()
        return real_tokenize(text)

    monkeypatch.setattr(preprocessor, "fts_tokenize", _arm_cancel)
    with pytest.raises(excel_boundary.ParseCancelledError):
        db_manager.import_materials_from_excel(
            str(xlsx_path),
            mode="append",
            filename=xlsx_path.name,
            cancel_event=cancel,
            stage_only=True,
        )

    assert _db_codes() == ["R03-OLD-1"]
    assert _fts_codes() == ["R03-OLD-1"]
    assert db_manager.get_db_metadata()["filename"] == "r03_old_seed.xlsx"
    assert _stage_residue() == []


def test_r03_append_metadata_merged_counts_published_in_one_transaction(r03_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _seed_old_generation(r03_env, monkeypatch)
    xlsx_path = tmp_path / "append_meta.xlsx"
    xlsx_path.write_bytes(_append_batch_xlsx())

    handle = db_manager.import_materials_from_excel(
        str(xlsx_path), mode="append", filename="append_meta.xlsx", stage_only=True
    )
    meta = db_manager.commit_staged_materials_import(handle)
    # 旧世代 1 行 + 本批 2 行：计数随同一 EXCLUSIVE 事务发表，杜绝事务外 meta 分裂。
    assert meta["total_rows"] == 3
    assert meta["imported_rows"] == 3
    assert meta["disabled_rows"] == 0
    assert meta["filename"] == "append_meta.xlsx"
    assert db_manager.get_db_metadata()["total_rows"] == 3
    assert _stage_residue() == []


# ---------------------------------------------------------------------------
# 三、vector_index：缓存整套备份-提升-恢复（首/中/末失败、目标缺文件、旧 FAISS 失效、
#    双失败写阻断）
# ---------------------------------------------------------------------------


def _write_generation(directory: Path, marker: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for idx, name in enumerate(vector_index.CACHE_FILE_NAMES):
        (directory / name).write_text(f"{marker}:{name}:{idx}", encoding="utf-8")


def _live_generation_bytes(directory: Path) -> Dict[str, bytes]:
    # 容忍“目标原不存在”边界：仅快照实际存在的受管文件，缺件由用例另行显式断言。
    return {
        name: (directory / name).read_bytes()
        for name in vector_index.CACHE_FILE_NAMES
        if (directory / name).exists()
    }


@pytest.mark.parametrize("fail_position", [0, 1, 2, 3])
def test_r03_cache_promotion_failure_restores_full_old_generation(r03_env, monkeypatch, fail_position):
    mgr = vector_index.VectorIndexManager()
    target = r03_env["vec_dir"] / f"case_fail_{fail_position}"
    stage = target / f".stage_unit_{fail_position}"
    _write_generation(target, "OLD")
    _write_generation(stage, "NEW")
    before = _live_generation_bytes(target)

    real_replace = os.replace
    calls = {"n": 0}

    def _failing_replace(src, dst):
        if ".stage" in str(src) or "stage_unit" in str(src):
            calls["n"] += 1
            if calls["n"] == fail_position + 1:
                raise OSError(f"simulated promotion failure at position {fail_position}")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _failing_replace)
    with pytest.raises(OSError, match="simulated promotion failure"):
        mgr.commit_candidate_cache(str(stage), target_dir=str(target))

    # 旧世代四件套逐字节还原，暂存/备份目录清空，未进入写阻断。
    assert _live_generation_bytes(target) == before
    assert not stage.exists()
    assert not list(target.glob(".backup_*"))
    assert vector_index.is_cache_write_blocked() is False


def test_r03_cache_missing_target_files_backup_only_existing(r03_env, monkeypatch):
    mgr = vector_index.VectorIndexManager()
    target = r03_env["vec_dir"] / "case_missing"
    stage = target / ".stage_unit_missing"
    target.mkdir(parents=True)
    # 旧世代只有两件套（覆盖“目标原不存在”边界）。
    for name in ("index_to_material.json", "cache_metadata.json"):
        (target / name).write_text(f"OLD:{name}", encoding="utf-8")
    _write_generation(stage, "NEW")
    before = _live_generation_bytes(target)

    real_replace = os.replace
    calls = {"n": 0}

    def _fail_second_stage_replace(src, dst):
        if ".stage" in str(src) or "stage_unit" in str(src):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated missing-target failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _fail_second_stage_replace)
    with pytest.raises(OSError, match="missing-target"):
        mgr.commit_candidate_cache(str(stage), target_dir=str(target))

    assert _live_generation_bytes(target) == before
    assert not (target / "materials.index").exists()
    assert not (target / "materials_embeddings.npy").exists()
    assert not stage.exists() and not list(target.glob(".backup_*"))
    assert vector_index.is_cache_write_blocked() is False


def test_r03_cache_stale_faiss_sidecar_removed_on_successful_promotion(r03_env):
    mgr = vector_index.VectorIndexManager()
    target = r03_env["vec_dir"] / "case_stale_faiss"
    stage = target / ".stage_unit_stale"
    _write_generation(target, "OLD")
    # 新世代为纯 numpy/mapping 候选，不再携带 FAISS sidecar。
    stage.mkdir(parents=True)
    for name in ("materials_embeddings.npy", "index_to_material.json", "cache_metadata.json"):
        (stage / name).write_text(f"NEW:{name}", encoding="utf-8")

    mgr.commit_candidate_cache(str(stage), target_dir=str(target))

    assert not (target / "materials.index").exists()
    for name in ("materials_embeddings.npy", "index_to_material.json", "cache_metadata.json"):
        assert (target / name).read_text(encoding="utf-8") == f"NEW:{name}"
    assert not stage.exists() and not list(target.glob(".backup_*"))


def test_r03_cache_double_failure_engages_write_block_and_preserves_material(r03_env, monkeypatch):
    mgr = vector_index.VectorIndexManager()
    target = r03_env["vec_dir"] / "case_double"
    stage = target / ".stage_unit_double"
    _write_generation(target, "OLD")
    _write_generation(stage, "NEW")

    real_replace = os.replace
    calls = {"n": 0}

    def _fail_promotion_and_restore(src, dst):
        src_s = str(src)
        if ".stage" in src_s or "stage_unit" in src_s:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated promotion disk error")
        # 备份恢复移动（源位于 .backup_*）同样失败：构造双失败。
        if ".backup_" in src_s:
            raise OSError("simulated restore disk error")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _fail_promotion_and_restore)
    with pytest.raises(vector_index.CacheRollbackError, match="写入阻断"):
        mgr.commit_candidate_cache(str(stage), target_dir=str(target))

    assert vector_index.is_cache_write_blocked() is True
    # 恢复材料必须保留：暂存目录与备份目录都不得被清理。
    assert stage.is_dir() and list(target.glob(".backup_*"))
    # 写阻断期间任何新的缓存写入尝试都必须被拒绝。
    with pytest.raises(vector_index.CacheRollbackError):
        mgr.write_candidate_cache_to_dir(str(target / ".stage_should_be_rejected"), None)


def test_r03_cache_successful_promotion_unifies_generation(r03_env):
    mgr = vector_index.VectorIndexManager()
    target = r03_env["vec_dir"] / "case_ok"
    stage = target / ".stage_unit_ok"
    _write_generation(target, "OLD")
    _write_generation(stage, "NEW")

    mgr.commit_candidate_cache(str(stage), target_dir=str(target))

    for idx, name in enumerate(vector_index.CACHE_FILE_NAMES):
        assert (target / name).read_text(encoding="utf-8") == f"NEW:{name}:{idx}"
    assert not stage.exists() and not list(target.glob(".backup_*"))
    assert vector_index.is_cache_write_blocked() is False


# ---------------------------------------------------------------------------
# 四、服务器级七步流程：Probe A/B/C/D + append 成功/替换成功对照
# ---------------------------------------------------------------------------


def test_r03_probe_a1_timeout_before_gate_failed_full_old_409_then_recovers(r03_env, monkeypatch):
    client: TestClient = r03_env["client"]
    headers = r03_env["director"]["headers"]
    upload_dir: Path = r03_env["upload_dir"]
    _seed_old_generation(r03_env, monkeypatch)
    before = _five_state(r03_env)

    started = threading.Event()
    release = threading.Event()
    real_write = server.index_manager.write_candidate_cache_to_dir

    def _parking_write(target_dir, candidate):
        real_write(target_dir, candidate)
        started.set()
        # 门点（Step5）之前停车：监督线程超时必然在 staging 状态赢得取消。
        release.wait(timeout=10.0)

    _RecordingArbiter.instances.clear()
    with monkeypatch.context() as mp:
        mp.setattr(server, "_DB_IMPORT_TASK_TIMEOUT_SECONDS", 0.12)
        mp.setattr(server, "ImportCommitArbiter", _RecordingArbiter)
        mp.setattr(server.index_manager, "write_candidate_cache_to_dir", _parking_write)

        resp = _post_import(client, headers, "probe_a1.xlsx", _replace_batch_xlsx())
        assert resp.status_code == 200
        assert started.wait(timeout=5.0)
        task_id = resp.json()["task_id"]

        st = _poll_terminal(client, task_id, headers)
        assert st["status"] == "failed" and "超时" in st["error"]
        # 锁 owner 证据：worker 存活期间锁保持占用。
        assert server._db_import_lock.locked()
        assert _RecordingArbiter.instances[-1].state == server.ImportCommitArbiter.CANCELLED

        # 第二导入确定性 409，且不可能释放前一 owner 的令牌。
        rejected = _post_import(client, headers, "rejected.xlsx", _replace_batch_xlsx())
        assert rejected.status_code == 409
        assert "请稍候再试" in rejected.json()["detail"]
        assert server._db_import_lock.locked()

        release.set()
        for _ in range(250):
            if not server._db_import_lock.locked():
                break
            time.sleep(0.02)
        assert not server._db_import_lock.locked()

    after = _five_state(r03_env)
    assert after["db_codes"] == ["R03-OLD-1"], json.dumps({"before": before, "after": after}, ensure_ascii=False)
    assert after["fts_codes"] == ["R03-OLD-1"]
    assert after["metadata"]["filename"] == "r03_old_seed.xlsx"
    assert after["mem_codes"] == ["R03-OLD-1"]
    assert after["cache_codes"] == ["R03-OLD-1"]
    assert after["stage_tables"] == [] and after["leftover_cache_dirs"] == []
    assert after["cache_write_blocked"] is False
    assert os.listdir(upload_dir) == []

    # worker 退出后恢复导入成功。
    recover = _post_import(client, headers, "recover.xlsx", _replace_batch_xlsx())
    assert recover.status_code == 200
    final = _poll_terminal(client, recover.json()["task_id"], headers)
    assert final["status"] == "completed", final
    assert set(_db_codes()) == {"R03-NEW-1", "R03-NEW-2"}
    assert not server._db_import_lock.locked()


def test_r03_probe_a2_late_cancel_after_swap_still_completes_full_new(r03_env, monkeypatch):
    client: TestClient = r03_env["client"]
    headers = r03_env["director"]["headers"]
    _seed_old_generation(r03_env, monkeypatch)
    before = _five_state(r03_env)

    real_commit = db_manager.commit_staged_materials_import

    def _cancel_after_real_swap(handle, cancel_event=None, retain_old_generation=False, **kwargs):
        result = real_commit(
            handle, cancel_event=cancel_event, retain_old_generation=retain_old_generation, **kwargs
        )
        # 模拟 R02 Probe A：超时恰好落在 Step5 交换完成之后、Step6 发布之前。
        if cancel_event is not None:
            cancel_event.set()
        return result

    _RecordingArbiter.instances.clear()
    with monkeypatch.context() as mp:
        mp.setattr(server, "ImportCommitArbiter", _RecordingArbiter)
        mp.setattr(db_manager, "commit_staged_materials_import", _cancel_after_real_swap)

        resp = _post_import(client, headers, "probe_a2.xlsx", _replace_batch_xlsx())
        assert resp.status_code == 200
        st = _poll_terminal(client, resp.json()["task_id"], headers)

    after = _five_state(r03_env)
    new_codes = {"R03-NEW-1", "R03-NEW-2"}
    # 门后迟到取消不得改写真实完成：唯一终态 completed + 全新五态。
    assert st["status"] == "completed", json.dumps({"before": before, "after": after}, ensure_ascii=False)
    assert st.get("error") is None
    assert _RecordingArbiter.instances[-1].state == server.ImportCommitArbiter.COMMITTED
    assert set(after["db_codes"]) == new_codes
    assert set(after["fts_codes"]) == new_codes
    assert set(after["mem_codes"]) == new_codes
    assert set(after["cache_codes"]) == new_codes
    assert after["metadata"]["filename"] == "probe_a2.xlsx"
    assert after["stage_tables"] == [] and after["leftover_cache_dirs"] == []
    assert not server._db_import_lock.locked()


def test_r03_probe_b_real_timeout_inside_committing_waits_terminal_completed(r03_env, monkeypatch):
    client: TestClient = r03_env["client"]
    headers = r03_env["director"]["headers"]
    _seed_old_generation(r03_env, monkeypatch)
    before = _five_state(r03_env)

    entered = threading.Event()
    release = threading.Event()
    real_commit = db_manager.commit_staged_materials_import

    def _slow_commit_after_swap(handle, cancel_event=None, retain_old_generation=False, **kwargs):
        result = real_commit(
            handle, cancel_event=cancel_event, retain_old_generation=retain_old_generation, **kwargs
        )
        entered.set()
        # 交换已发表、仲裁器处于 committing；令监督线程 0.15s 超时落在此窗口。
        release.wait(timeout=10.0)
        return result

    _RecordingArbiter.instances.clear()
    holder: Dict[str, Any] = {}

    def _do_post() -> None:
        # Starlette TestClient 的 post() 会等待整个 ASGI app task（含后台监督线程）
        # 结束才返回；R03 监督线程在 committing 窗口只循环等终态，因此必须在独立线程
        # 内发起请求，主线程才能在 worker 停车期间观察 committing 中段状态。
        holder["resp"] = _post_import(client, headers, "probe_b.xlsx", _replace_batch_xlsx())

    post_thread = threading.Thread(target=_do_post, name="r03-probe-b-post")
    # upload_tasks 是进程级注册表，早期用例可能遗留历史条目；用前后 id 差集定位本任务，
    # 不依赖注册表的跨用例清理行为。
    before_task_ids = set(server.upload_tasks)
    with monkeypatch.context() as mp:
        mp.setattr(server, "_DB_IMPORT_TASK_TIMEOUT_SECONDS", 0.15)
        mp.setattr(server, "ImportCommitArbiter", _RecordingArbiter)
        mp.setattr(db_manager, "commit_staged_materials_import", _slow_commit_after_swap)

        post_thread.start()
        try:
            assert entered.wait(timeout=5.0)

            # 仲裁器已进入 committing：迟到超时落败，监督线程不得抢写 failed。
            assert _RecordingArbiter.instances[-1].state == server.ImportCommitArbiter.COMMITTING
            # 响应尚未返回（post 线程阻塞于监督线程终态等待），从进程内任务表取本任务 id；
            # 409 分支在任务登记前返回，故差集里恰有一个新登记的 processing 任务。
            new_task_ids = set(server.upload_tasks) - before_task_ids
            assert len(new_task_ids) == 1
            live_task_id = new_task_ids.pop()
            assert server.upload_tasks[live_task_id]["status"] == "processing"
            mid = client.get(f"/api/upload/status?task_id={live_task_id}", headers=headers)
            assert mid.status_code == 200
            assert mid.json()["status"] == "processing"
            assert server._db_import_lock.locked()
            rejected = _post_import(client, headers, "b_rejected.xlsx", _replace_batch_xlsx())
            assert rejected.status_code == 409

            release.set()
        finally:
            post_thread.join(timeout=15.0)
        assert not post_thread.is_alive()

    resp = holder["resp"]
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    st = _poll_terminal(client, task_id, headers)

    after = _five_state(r03_env)
    new_codes = {"R03-NEW-1", "R03-NEW-2"}
    assert st["status"] == "completed", json.dumps({"before": before, "after": after}, ensure_ascii=False)
    assert st.get("error") is None
    assert set(after["db_codes"]) == new_codes
    assert set(after["mem_codes"]) == new_codes and set(after["cache_codes"]) == new_codes
    assert after["stage_tables"] == [] and after["leftover_cache_dirs"] == []
    assert not server._db_import_lock.locked()


def test_r03_probe_c_step7_failure_rolls_back_every_layer_full_old(r03_env, monkeypatch):
    client: TestClient = r03_env["client"]
    headers = r03_env["director"]["headers"]
    upload_dir: Path = r03_env["upload_dir"]
    _seed_old_generation(r03_env, monkeypatch)
    before = _five_state(r03_env)

    real_replace = os.replace
    calls = {"n": 0}

    def _fail_second_stage_promotion(src, dst):
        if "stage" in str(src):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated step7 promotion failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _fail_second_stage_promotion)
    resp = _post_import(client, headers, "probe_c.xlsx", _replace_batch_xlsx())
    assert resp.status_code == 200
    st = _poll_terminal(client, resp.json()["task_id"], headers)

    after = _five_state(r03_env)
    assert st["status"] == "failed" and "step7 promotion failure" in st["error"]
    # 五态全部回到可核验旧态：DB 反向交换、内存回滚、缓存备份恢复。
    assert after["db_codes"] == ["R03-OLD-1"], json.dumps({"before": before, "after": after}, ensure_ascii=False)
    assert after["fts_codes"] == ["R03-OLD-1"]
    assert after["metadata"]["filename"] == "r03_old_seed.xlsx"
    assert after["mem_codes"] == ["R03-OLD-1"]
    assert after["cache_codes"] == ["R03-OLD-1"]
    assert after["cache_count"] == 1
    assert after["stage_tables"] == [] and after["leftover_cache_dirs"] == []
    assert after["cache_write_blocked"] is False
    assert not server._db_import_lock.locked()
    assert os.listdir(upload_dir) == []


def test_r03_probe_c_double_failure_write_block_preserves_recovery_material(r03_env, monkeypatch):
    client: TestClient = r03_env["client"]
    headers = r03_env["director"]["headers"]
    _seed_old_generation(r03_env, monkeypatch)
    before = _five_state(r03_env)

    real_replace = os.replace
    calls = {"n": 0}

    def _fail_promotion_and_restore(src, dst):
        src_s = str(src)
        if "stage" in src_s:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated double promotion failure")
        if ".backup_" in src_s:
            raise OSError("simulated double restore failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _fail_promotion_and_restore)
    resp = _post_import(client, headers, "probe_c_double.xlsx", _replace_batch_xlsx())
    assert resp.status_code == 200
    st = _poll_terminal(client, resp.json()["task_id"], headers)

    after = _five_state(r03_env)
    assert st["status"] == "failed" and "写入阻断" in st["error"]
    assert vector_index.is_cache_write_blocked() is True
    # 恢复材料保留：.stage_* 与 .backup_* 均不得删除。
    assert any(d.startswith(".stage_") for d in after["leftover_cache_dirs"])
    assert any(d.startswith(".backup_") for d in after["leftover_cache_dirs"])
    # DB 与内存仍完成联动回滚，只有缓存层进入受控阻断。
    assert after["db_codes"] == ["R03-OLD-1"], json.dumps({"before": before, "after": after}, ensure_ascii=False)
    assert after["fts_codes"] == ["R03-OLD-1"]
    assert after["mem_codes"] == ["R03-OLD-1"]
    assert after["metadata"]["filename"] == "r03_old_seed.xlsx"
    assert after["stage_tables"] == []
    assert not server._db_import_lock.locked()


def test_r03_probe_d_append_candidate_failure_keeps_full_old(r03_env, monkeypatch):
    client: TestClient = r03_env["client"]
    headers = r03_env["director"]["headers"]
    upload_dir: Path = r03_env["upload_dir"]
    _seed_old_generation(r03_env, monkeypatch)
    before = _five_state(r03_env)

    def _boom_candidate(*args, **kwargs):
        raise RuntimeError("append_index_fail")

    monkeypatch.setattr(server.index_manager, "build_index_candidate", _boom_candidate)
    resp = _post_import(client, headers, "probe_d.xlsx", _append_batch_xlsx(), mode="append")
    assert resp.status_code == 200
    st = _poll_terminal(client, resp.json()["task_id"], headers)

    after = _five_state(r03_env)
    assert st["status"] == "failed" and "append_index_fail" in st["error"]
    # append 准备阶段失败：旧物料一行不丢，本批一行不发表。
    assert after["db_codes"] == ["R03-OLD-1"], json.dumps({"before": before, "after": after}, ensure_ascii=False)
    assert after["fts_codes"] == ["R03-OLD-1"]
    assert after["mem_codes"] == ["R03-OLD-1"]
    assert after["cache_codes"] == ["R03-OLD-1"]
    assert after["metadata"]["filename"] == "r03_old_seed.xlsx"
    assert after["stage_tables"] == [] and after["leftover_cache_dirs"] == []
    assert not server._db_import_lock.locked()
    assert os.listdir(upload_dir) == []


def test_r03_append_success_retains_old_and_publishes_merged_generation(r03_env, monkeypatch):
    client: TestClient = r03_env["client"]
    headers = r03_env["director"]["headers"]
    _seed_old_generation(r03_env, monkeypatch)
    before = _five_state(r03_env)

    resp = _post_import(client, headers, "append_ok.xlsx", _append_batch_xlsx(), mode="append")
    assert resp.status_code == 200
    st = _poll_terminal(client, resp.json()["task_id"], headers)

    after = _five_state(r03_env)
    merged = {"R03-OLD-1", "R03-APP-1", "R03-APP-2"}
    assert st["status"] == "completed", json.dumps({"before": before, "after": after}, ensure_ascii=False)
    # append 成功必须保留旧物料并增加本批，禁止退化为丢旧数据的替换。
    assert set(after["db_codes"]) == merged
    assert set(after["fts_codes"]) == merged
    assert set(after["mem_codes"]) == merged
    assert set(after["cache_codes"]) == merged
    assert after["cache_count"] == 3
    assert after["metadata"]["filename"] == "append_ok.xlsx"
    assert after["metadata"]["total_rows"] == 3 and after["metadata"]["imported_rows"] == 3
    assert after["stage_tables"] == [] and after["leftover_cache_dirs"] == []
    assert not server._db_import_lock.locked()


def test_r03_replace_success_unifies_five_states(r03_env, monkeypatch):
    client: TestClient = r03_env["client"]
    headers = r03_env["director"]["headers"]
    _seed_old_generation(r03_env, monkeypatch)
    before = _five_state(r03_env)

    resp = _post_import(client, headers, "replace_ok.xlsx", _replace_batch_xlsx())
    assert resp.status_code == 200
    st = _poll_terminal(client, resp.json()["task_id"], headers)

    after = _five_state(r03_env)
    new_codes = {"R03-NEW-1", "R03-NEW-2"}
    assert st["status"] == "completed", json.dumps({"before": before, "after": after}, ensure_ascii=False)
    assert set(after["db_codes"]) == new_codes
    assert set(after["fts_codes"]) == new_codes
    assert set(after["mem_codes"]) == new_codes
    assert set(after["cache_codes"]) == new_codes
    assert after["cache_count"] == 2
    assert after["metadata"]["filename"] == "replace_ok.xlsx"
    assert after["metadata"]["total_rows"] == 2
    assert after["stage_tables"] == [] and after["leftover_cache_dirs"] == []
    assert after["cache_write_blocked"] is False
    assert not server._db_import_lock.locked()
    vec_dir: Path = r03_env["vec_dir"]
    assert (vec_dir / "materials_embeddings.npy").exists()
    assert (vec_dir / "index_to_material.json").exists()
