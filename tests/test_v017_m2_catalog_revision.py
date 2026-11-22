import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app import db_manager as db, vector_index as vectors


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "materials.db"))
    cache = tmp_path / "vectors"
    monkeypatch.setenv("VECTOR_CACHE_DIR", str(cache))
    monkeypatch.delenv("LISTTRANS_EVAL_KEYWORD_ONLY", raising=False)
    manager = vectors.VectorIndexManager()
    manager.model = object()
    manager.embedding_dim = 512

    def encode(texts, **kwargs):
        data = np.zeros((len(texts), manager.embedding_dim), dtype=np.float32)
        for row, text in zip(data, texts):
            row[0 if "oldtoken" in text else 1] = 1.0
        return data

    monkeypatch.setattr(manager, "encode_queries", encode)
    monkeypatch.setattr(vectors, "index_manager", manager)
    db.init_db()
    return manager, cache


def excel(tmp_path, *specs):
    path = tmp_path / f"{specs[-1]}.xlsx"
    pd.DataFrame([
        {"物料分类": "阀门", "物料编码": "M2-VALVE", "物料名称": "合成阀门", "规格/型号": spec,
         "计量单位": "台", "常用品牌": "", "描述": ""}
        for spec in specs
    ]).to_excel(path, index=False, startrow=1)
    return path


def assert_fts(current, previous):
    with closing(db.get_db_connection()) as conn:
        for token, count in ((current, 1), (previous, 0)):
            assert conn.execute("SELECT COUNT(*) FROM materials_fts WHERE materials_fts MATCH ?", (token,)).fetchone()[0] == count
        assert conn.execute("SELECT COUNT(*) FROM materials_fts").fetchone()[0] == conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0]


def assert_revision(manager, cache, previous=0):
    revision = db.get_db_revision()
    assert revision > previous
    assert db.get_db_metadata()["db_revision"] == revision == manager.db_revision
    assert json.loads((cache / "cache_metadata.json").read_text(encoding="utf-8"))["db_revision"] == revision
    return revision


def test_revision_chain_and_same_code_fts(catalog, tmp_path):
    manager, cache = catalog
    db.import_materials_from_excel(str(excel(tmp_path, "oldtoken")))
    revision = assert_revision(manager, cache)
    assert db.search_materials_keyword("oldtoken")[0]["code"] == "M2-VALVE"
    backup = tmp_path / "backup.db"
    with closing(db.get_db_connection()) as conn, closing(sqlite3.connect(backup)) as save:
        conn.backup(save)
        # Simulate duplicate old rows from a legacy append.
        conn.execute("INSERT INTO materials_fts SELECT * FROM materials_fts WHERE code='M2-VALVE'")
        conn.commit()

    db.import_materials_from_excel(str(excel(tmp_path, "midtoken")), mode="append")
    revision = assert_revision(manager, cache, revision)
    db.import_materials_from_excel(str(excel(tmp_path, "newtoken")), mode="append")
    revision = assert_revision(manager, cache, revision)
    assert db.search_materials_keyword("oldtoken") == []
    assert_fts("newtoken", "oldtoken")
    assert_fts("newtoken", "midtoken")

    material = db.query_by_code("M2-VALVE")
    update = (material["code"], material["parsed_params"], '{"attrs":{"size":9}}', "newtoken ")
    assert db.update_material_params([update]) == 1
    revision = assert_revision(manager, cache, revision)
    assert json.loads(db.query_by_code("M2-VALVE")["parsed_params"])["attrs"]["size"] == 9
    assert db.update_material_params([update]) == 0
    assert db.get_db_revision() == revision

    for action in (lambda: db.reparse_materials_db_partial(codes=["M2-VALVE"]), db.reparse_materials_db_asymmetric):
        assert action()["reparsed_rows"] == 1
        revision = assert_revision(manager, cache, revision)
        assert_fts("newtoken", "oldtoken")

    db.restore_materials_catalog(backup)
    revision = assert_revision(manager, cache, revision)
    assert_fts("oldtoken", "newtoken")
    with closing(db.get_db_connection()) as conn:
        assert conn.execute("SELECT value FROM materials_metadata WHERE key='schema_version'").fetchone() is None
        assert conn.execute("SELECT parsed_schema_version FROM materials").fetchone()[0] == 2
    restarted = vectors.VectorIndexManager()
    restarted.model = object()
    restarted.check_db_timestamp_and_build(db.DB_PATH)
    assert restarted.db_revision == revision
    assert restarted.index_to_material[0]["specification"] == "oldtoken"


def test_detached_db_and_vector_reads(catalog, tmp_path):
    manager, _ = catalog
    db.import_materials_from_excel(str(excel(tmp_path, "oldtoken")))
    for get in (lambda: db.query_by_code("M2-VALVE"), lambda: db.search_materials_keyword("oldtoken")[0],
                lambda: db._search_materials_keyword_cached("oldtoken")[0]):
        result = get()
        result["specification"] = "changed"
        result["attrs"] = {"changed": True}
        assert get()["specification"] == "oldtoken"
        assert "attrs" not in get()

    state = manager.snapshot_published_state()
    material = {**state.materials[0], "attrs": {"nested": [5]}}
    manager.publish_candidate(vectors.IndexCandidate(
        [material], state.embeddings, state.faiss_index, state.use_faiss,
        state.model_name, state.dimension, state.db_revision,
    ))
    material["attrs"]["nested"].append(7)
    query = state.embeddings[0]
    manager.search(query)[0][0]["attrs"]["nested"].append(8)
    manager.index_to_material[0]["attrs"]["nested"].append(9)
    assert manager.search(query)[0][0]["attrs"] == {"nested": [5]}
    with pytest.raises(ValueError):
        manager.numpy_embeddings.setflags(write=True)


def test_concurrent_load_search_and_publication(catalog, tmp_path):
    manager, _ = catalog
    initial = excel(tmp_path, "oldtoken")
    newer = excel(tmp_path, "newtoken")
    db.import_materials_from_excel(str(initial))
    query = manager.numpy_embeddings[0]
    start = threading.Barrier(4)

    def search():
        start.wait(timeout=10)
        for _ in range(90):
            (material, score), = manager.search(query, top_k=1)
            assert (material["specification"], score) in {("oldtoken", 1.0), ("newtoken", 0.0)}

    def load():
        start.wait(timeout=10)
        for _ in range(15):
            assert manager.load_cache()

    def write():
        start.wait(timeout=10)
        for path in (newer, initial, newer):
            db.import_materials_from_excel(str(path), mode="append")
            manager.check_db_timestamp_and_build(db.DB_PATH, force=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in [pool.submit(fn) for fn in (search, search, load, write)]:
            future.result(timeout=30)


@pytest.mark.parametrize("fault", ["fts", "sql", "encode", "disk", "bundle"])
def test_import_failure_keeps_old_generation(catalog, tmp_path, monkeypatch, fault):
    manager, cache = catalog
    db.import_materials_from_excel(str(excel(tmp_path, "oldtoken")))
    revision = assert_revision(manager, cache)
    old_cache = {p.name: p.read_bytes() for p in cache.iterdir()}
    if fault == "sql":
        with closing(db.get_db_connection()) as conn, conn:
            conn.execute("""CREATE TRIGGER fail_revision BEFORE INSERT ON materials_metadata
                WHEN NEW.key='db_revision' BEGIN SELECT RAISE(FAIL, 'm2 fault'); END""")
    elif fault == "encode":
        monkeypatch.setattr(manager, "encode_queries", lambda *args, **kwargs: None)
    elif fault == "fts":
        def fail_fts(*args, **kwargs):
            raise RuntimeError("m2 fault")
        monkeypatch.setattr(db, "_write_fts_records", fail_fts)
    else:
        real_replace = os.replace
        target = "index_to_material.json" if fault == "disk" else vectors.SNAPSHOT_FILE

        def fail_promote(source, destination):
            if Path(source).parent.name.startswith(".stage_") and Path(destination).name == target:
                raise OSError("m2 fault")
            return real_replace(source, destination)

        monkeypatch.setattr(os, "replace", fail_promote)

    with pytest.raises(Exception, match="m2 fault|向量编码失败"):
        db.import_materials_from_excel(str(excel(tmp_path, "newtoken")), mode="append")
    assert assert_revision(manager, cache) == revision
    assert_fts("oldtoken", "newtoken")
    assert db.query_by_code("M2-VALVE")["specification"] == "oldtoken"
    assert manager.index_to_material[0]["specification"] == "oldtoken"
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == old_cache


def test_dimension_change_rebuilds_and_bad_reload_keeps_memory(catalog, tmp_path):
    manager, cache = catalog
    db.import_materials_from_excel(str(excel(tmp_path, "oldtoken")))
    revision = assert_revision(manager, cache)
    manager.embedding_dim = 384
    assert not manager.has_current_index(revision)
    assert manager.load_cache() is False
    assert manager.numpy_embeddings.shape == (1, 512)
    manager.check_db_timestamp_and_build(db.DB_PATH)
    assert assert_revision(manager, cache) == revision
    assert manager.numpy_embeddings.shape == (1, 384)
    assert manager.has_current_index(revision)
    (cache / vectors.SNAPSHOT_FILE).write_bytes(b"corrupt")
    assert manager.load_cache() is False
    assert manager.search(manager.numpy_embeddings[0])[0][0]["code"] == "M2-VALVE"
    with pytest.raises(RuntimeError, match="增量入口已停用"):
        manager.add_to_index({"code": "UNVERSIONED"})


def test_finalize_failure_rolls_back_reparse_and_disk(catalog, tmp_path, monkeypatch):
    manager, cache = catalog
    db.import_materials_from_excel(str(excel(tmp_path, "oldtoken")))
    revision = assert_revision(manager, cache)
    old = db.query_by_code("M2-VALVE")
    old_cache = {p.name: p.read_bytes() for p in cache.iterdir()}
    commit_cache = manager.commit_candidate_cache

    def interrupt_database_commit(stage, *, finalize):
        def fail():
            raise sqlite3.OperationalError("m2 finalize fault")
        return commit_cache(stage, finalize=fail)

    monkeypatch.setattr(manager, "commit_candidate_cache", interrupt_database_commit)
    with pytest.raises(sqlite3.OperationalError, match="m2 finalize fault"):
        db.reparse_materials_db_partial(codes=["M2-VALVE"])
    assert assert_revision(manager, cache) == revision
    assert db.query_by_code("M2-VALVE") == old
    assert_fts("oldtoken", "newtoken")
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == old_cache
