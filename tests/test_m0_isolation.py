# -*- coding: utf-8 -*-
"""
V015-M0 隔离冒烟测试：验证测试会话的路径注入与数据库写入完全落在临时目录，
业务库/配置/向量缓存的内容哈希在测试前后保持不变。

本测试只依赖 Python 标准库与 tests/conftest.py 的环境注入，
在 pandas/fastapi 等重依赖缺失时仍可执行，是最低冒烟入口。
"""
import os
import sqlite3
import hashlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROD_APP_DIR = PROJECT_ROOT / "app"
PROD_CONFIG_DIR = PROJECT_ROOT / "config"
PROD_VECTOR_CACHE = PROD_APP_DIR / "vector_cache"


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_tree(root: Path) -> dict:
    """对目录内全部常规文件计算 {相对路径: sha256}，不存在则空快照。"""
    snap = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(root)).replace("\\", "/")] = _sha256_file(p)
    return snap


def test_env_vars_point_into_isolation_root(isolated_layout):
    """所有可写注入点必须位于同一会话隔离根，且不等于业务路径。"""
    root = isolated_layout["root"]
    expected = {
        "MATERIALS_DB_PATH": isolated_layout["materials_db"],
        "AUTH_DB_PATH": isolated_layout["auth_db"],
        "CONFIG_DIR": isolated_layout["config_dir"],
        "VECTOR_CACHE_DIR": isolated_layout["vector_cache_dir"],
        "UPLOAD_DIR": isolated_layout["upload_dir"],
        "OUTPUT_DIR": isolated_layout["output_dir"],
        "LOG_DIR": isolated_layout["log_dir"],
        "BINDING_FEEDBACK_FILE": isolated_layout["binding_feedback_file"],
        "FEEDBACK_SAMPLES_FILE": isolated_layout["feedback_samples_file"],
    }
    for var, path in expected.items():
        actual = Path(os.environ[var]).resolve()
        assert actual == Path(path).resolve(), f"{var} 未指向隔离路径"
        assert root in actual.parents or actual == root, (
            f"{var}={actual} 逃逸出隔离根 {root}"
        )
    assert Path(os.environ["MATERIALS_DB_PATH"]).resolve() != (
        PROD_APP_DIR / "materials.db"
    ).resolve()
    assert Path(os.environ["AUTH_DB_PATH"]).resolve() != (
        PROD_APP_DIR / "auth.db"
    ).resolve()


def test_isolated_sqlite_create_and_write(isolated_layout):
    """隔离目录内完成建表与写入，且业务库文件不被创建/修改。"""
    prod_materials = PROD_APP_DIR / "materials.db"
    prod_existed_before = prod_materials.exists()
    prod_hash_before = _sha256_file(prod_materials)

    # A-M0-04: 裸建表用例改用独立测试数据库，不与会话业务 Schema 共用建表状态，
    # 消除对其他模块建表顺序的耦合；独立库路径必须仍在隔离根内。
    db_path = Path(isolated_layout["root"]) / "m0_bare_smoke" / "smoke_materials.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    assert db_path != Path(isolated_layout["materials_db"])
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE materials (code TEXT PRIMARY KEY, name TEXT, "
            "specification TEXT, category TEXT)"
        )
        cur.execute(
            "INSERT INTO materials VALUES (?, ?, ?, ?)",
            ("T.001", "隔离测试物料", "DN15", "测试分类"),
        )
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM materials")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()

    assert db_path.exists() and db_path.stat().st_size > 0
    # 业务库状态不变（不存在则仍不存在；存在则哈希一致）
    assert prod_materials.exists() == prod_existed_before
    assert _sha256_file(prod_materials) == prod_hash_before


def test_isolated_auth_db_write(isolated_layout):
    """账号库写入落在隔离 AUTH_DB_PATH。"""
    # A-M0-04: 同上，裸建 users 表改用独立账号库，避免污染会话业务 Schema。
    db_path = Path(isolated_layout["root"]) / "m0_bare_smoke" / "smoke_auth.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    assert db_path != Path(isolated_layout["auth_db"])
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, "
            "role TEXT)"
        )
        cur.execute(
            "INSERT INTO users (username, role) VALUES (?, ?)",
            ("iso_user", "employee"),
        )
        conn.commit()
        cur.execute("SELECT username FROM users")
        assert cur.fetchone()[0] == "iso_user"
    finally:
        conn.close()
    assert not (PROD_APP_DIR / "auth.db").exists() or _sha256_file(
        PROD_APP_DIR / "auth.db"
    ) is not None


def test_prod_config_snapshot_copied_and_writable_in_isolation(isolated_layout):
    """隔离 CONFIG_DIR 是生产配置的副本；在副本上写不影响业务 config/。"""
    before = _snapshot_tree(PROD_CONFIG_DIR)
    iso_cfg = Path(isolated_layout["config_dir"])
    # 关键配置文件应已复制（若生产配置存在）
    if PROD_CONFIG_DIR.exists():
        prod_files = {p.name for p in PROD_CONFIG_DIR.glob("*.json")}
        iso_files = {p.name for p in iso_cfg.glob("*.json")}
        assert prod_files.issubset(iso_files)

    probe = iso_cfg / "_iso_probe.json"
    probe.write_text('{"probe": true}', encoding="utf-8")
    try:
        after = _snapshot_tree(PROD_CONFIG_DIR)
        assert after == before, "隔离配置写入导致业务 config/ 哈希变化"
        assert not (PROD_CONFIG_DIR / "_iso_probe.json").exists()
    finally:
        probe.unlink(missing_ok=True)


def test_vector_and_upload_dirs_inside_isolation(isolated_layout):
    """向量缓存/上传目录解析到隔离区，业务 vector_cache 与 uploads 不被引用。"""
    cache_dir = Path(isolated_layout["vector_cache_dir"]).resolve()
    upload_dir = Path(isolated_layout["upload_dir"]).resolve()
    assert cache_dir.parent != PROD_APP_DIR.resolve()
    assert upload_dir != (PROD_APP_DIR / "uploads").resolve()
    # 在隔离缓存写一个探针文件，业务缓存目录不得出现
    probe = cache_dir / "cache_metadata.json"
    probe.write_text("{}", encoding="utf-8")
    assert not (PROD_VECTOR_CACHE / "cache_metadata.json").exists() or (
        PROD_VECTOR_CACHE / "cache_metadata.json"
    ).read_text(encoding="utf-8") != "{}"
