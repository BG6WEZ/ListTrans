import os
import time
import json
import shutil
import threading
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

# Prevent OpenBLAS/PyTorch OpenMP thread thrashing and HuggingFace Tokenizers deadlock
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import numpy as np
from typing import List, Dict, Tuple, Any, Optional
from app.runtime_jobs import checkpoint
from app.db_manager import catalog_lock, catalog_read, get_db_revision, read_db_revision

# Attempt to import faiss, if not available, fallback is handled.
try:
    import faiss
    HAS_FAISS = True
except Exception:
    HAS_FAISS = False

try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except Exception:
    HAS_SENTENCE_TRANSFORMERS = False

DB_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(DB_DIR)
MODEL_DIR = os.path.join(PROJECT_ROOT, "model_offline")
CONFIG_DIR = os.getenv("CONFIG_DIR", os.path.join(PROJECT_ROOT, "config"))
# Test isolation: VECTOR_CACHE_DIR redirects embedding/index cache away from the business cache
CACHE_DIR = os.getenv("VECTOR_CACHE_DIR", os.path.join(DB_DIR, "vector_cache"))
os.makedirs(CACHE_DIR, exist_ok=True)


def get_cache_dir() -> str:
    """Runtime cache directory.

    CACHE_DIR is captured from VECTOR_CACHE_DIR at import time, but per-test isolation
    injects a new VECTOR_CACHE_DIR afterwards (monkeypatch.setenv); cache staging must
    resolve the variable at call time so writes land inside the active isolation root.
    """
    return os.getenv("VECTOR_CACHE_DIR", CACHE_DIR)


# Well-known on-disk cache file names managed as one reversible generation.
CACHE_FILE_NAMES = (
    "materials.index",
    "materials_embeddings.npy",
    "index_to_material.json",
    "cache_metadata.json",
)
SNAPSHOT_FILE = "cache_snapshot.zip"
VECTOR_FIELDS = ("code", "name", "specification", "category")


def _freeze_material_value(value):
    if isinstance(value, MappingProxyType):
        return value
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_material_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_material_value(item) for item in value)
    return value


def _copy_material_value(value):
    if isinstance(value, MappingProxyType):
        return {key: _copy_material_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_copy_material_value(item) for item in value]
    return value


class CacheRollbackError(RuntimeError):
    """Raised when a recovery-blocked write is attempted OR when cache promotion fails
    and restoring the backed-up generation also fails. The persistent recovery block
    stays engaged until verified cross-layer consistency reconciliation clears it."""


RECOVERY_MARKER_FILENAME = ".recovery_blocked.json"
_cache_write_blocked = False
_recovery_block_details: Optional[Dict[str, Any]] = None
_blocked_cache_dirs: set[str] = set()


def _recovery_marker_path(cache_dir: Optional[str] = None) -> str:
    base = cache_dir if cache_dir is not None else get_cache_dir()
    return os.path.join(base, RECOVERY_MARKER_FILENAME)


def is_cache_write_blocked(cache_dir: Optional[str] = None) -> bool:
    if _cache_write_blocked:
        return True
    if os.path.isfile(_recovery_marker_path(cache_dir)):
        return True
    if cache_dir is None:
        return any(os.path.isfile(_recovery_marker_path(d)) for d in _blocked_cache_dirs)
    return False


def get_cache_recovery_block_info(cache_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    marker = _recovery_marker_path(cache_dir)
    if os.path.isfile(marker):
        try:
            with open(marker, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            return {"blocked": True, "marker_path": marker}
    if _cache_write_blocked:
        return dict(_recovery_block_details or {"blocked": True})
    return None


def block_cache_writes(
    reason: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    target_dir: Optional[str] = None,
) -> None:
    """Engage the persistent recovery write block after an unrecoverable failure."""
    global _cache_write_blocked, _recovery_block_details
    _cache_write_blocked = True
    resolved_dir = os.path.abspath(target_dir if target_dir is not None else get_cache_dir())
    _blocked_cache_dirs.add(resolved_dir)
    payload: Dict[str, Any] = {
        "blocked": True,
        "reason": reason or "恢复失败/需人工处置：缓存或关联层未能恢复一致状态",
        "details": details or {},
        "target_dir": resolved_dir,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _recovery_block_details = payload
    try:
        os.makedirs(resolved_dir, exist_ok=True)
        marker = _recovery_marker_path(resolved_dir)
        with open(marker, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        # Even if writing the marker file fails (e.g. disk full), the in-memory block
        # stays engaged and recovery materials remain untouched.
        pass


def clear_cache_write_block_on_verified_recovery(cache_dir: Optional[str] = None) -> None:
    """Clear the cache write block ONLY after verified cross-layer consistency check."""
    global _cache_write_blocked, _recovery_block_details
    _cache_write_blocked = False
    _recovery_block_details = None
    dirs_to_clean = set(_blocked_cache_dirs)
    dirs_to_clean.add(os.path.abspath(cache_dir if cache_dir is not None else get_cache_dir()))
    for d in dirs_to_clean:
        marker = _recovery_marker_path(d)
        if os.path.isfile(marker):
            os.remove(marker)
    _blocked_cache_dirs.clear()


def reset_cache_write_block() -> None:
    """Reset hook for isolated test fixtures; production code never calls this."""
    global _cache_write_blocked, _recovery_block_details
    _cache_write_blocked = False
    _recovery_block_details = None
    dirs_to_clean = set(_blocked_cache_dirs)
    dirs_to_clean.add(os.path.abspath(get_cache_dir()))
    for d in dirs_to_clean:
        marker = _recovery_marker_path(d)
        if os.path.isfile(marker):
            try:
                os.remove(marker)
            except OSError:
                pass
    _blocked_cache_dirs.clear()


def _assert_cache_writable(cache_dir: Optional[str] = None) -> None:
    if is_cache_write_blocked(cache_dir):
        info = get_cache_recovery_block_info(cache_dir) or {}
        reason = info.get("reason") or "上一次回滚未能恢复完整一致状态"
        raise CacheRollbackError(
            "向量缓存已处于写入阻断状态（需人工核验全层一致性后解除）："
            f"{reason}，禁止继续接受导入或缓存写入。"
        )


@dataclass(frozen=True, eq=False)
class IndexCandidate:
    """Immutable shadow index snapshot built off the live index.

    Construction never mutates VectorIndexManager state; publish_candidate() performs
    the single in-lock swap, and write/commit_candidate_cache handle disk staging.
    """

    materials: tuple
    embeddings: Optional[np.ndarray]
    faiss_index: Optional[Any]
    use_faiss: bool
    model_name: str
    dimension: int
    db_revision: int = 0
    text_schema_version: str = "legacy-v1"

    def __post_init__(self):
        object.__setattr__(self, "materials", tuple(_freeze_material_value(row) for row in self.materials))
        if self.embeddings is not None:
            array = np.asarray(self.embeddings, dtype=np.float32, order="C")
            if array.shape != (len(self.materials), self.dimension):
                raise ValueError("向量矩阵维度或行数与索引快照不符")
            object.__setattr__(self, "embeddings", np.frombuffer(array.tobytes(), dtype=np.float32).reshape(array.shape))
        if self.use_faiss:
            index = self.faiss_index
            if index is None or index.d != self.dimension or index.ntotal != len(self.materials):
                raise ValueError("FAISS 维度或行数与索引快照不符")
            object.__setattr__(self, "faiss_index", faiss.clone_index(index))


class VectorIndexManager:
    def __init__(self):
        self.model: Optional[Any] = None
        self._lock = catalog_lock
        self._model_lock = threading.RLock()

        # Load model name from config or environment variable (Default to bge-small-zh-v1.5)
        self.model_name_or_path = self._get_configured_model_name()
        self.embedding_dim = 512 if "bge" in self.model_name_or_path.lower() else 384
        self._snapshot = IndexCandidate((), None, None, False, self.model_name_or_path, self.embedding_dim)
        # V018-M2：隔离存放非 legacy 候选编码文本的只读快照，不覆盖原有 legacy-v1 索引
        self._candidate_snapshots: Dict[str, IndexCandidate] = {}

    def get_candidate_cache_dir(self, text_schema_version: str = "legacy-v1") -> str:
        base = get_cache_dir()
        if not text_schema_version or text_schema_version == "legacy-v1":
            return base
        safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in str(text_schema_version))
        return os.path.join(base, "candidates", safe)

    def get_model_metadata(self) -> Dict[str, Any]:
        """Return offline model, tokenizer, and active snapshot metadata for auditability."""
        tok_cfg_path = os.path.join(MODEL_DIR, self.model_name_or_path, "tokenizer_config.json")
        tok_info: Dict[str, Any] = {}
        if os.path.isfile(tok_cfg_path):
            try:
                with open(tok_cfg_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                tok_info = {
                    "tokenizer_class": raw.get("tokenizer_class"),
                    "model_max_length": raw.get("model_max_length"),
                    "do_lower_case": raw.get("do_lower_case"),
                }
            except Exception:
                tok_info = {}
        with self._lock:
            snap = self._snapshot
        return {
            "model_name": self.model_name_or_path,
            "dimension": self.embedding_dim,
            "offline_path_exists": os.path.isdir(os.path.join(MODEL_DIR, self.model_name_or_path)),
            "tokenizer": tok_info,
            "active_db_revision": snap.db_revision,
            "active_text_schema_version": snap.text_schema_version,
            "active_item_count": len(snap.materials),
            "active_use_faiss": snap.use_faiss,
            "candidate_versions_loaded": sorted(self._candidate_snapshots.keys()),
        }

    @property
    @catalog_read
    def index_to_material(self):
        return _copy_material_value(self._snapshot.materials)

    @property
    @catalog_read
    def numpy_embeddings(self):
        return self._snapshot.embeddings

    @property
    @catalog_read
    def use_faiss(self):
        return self._snapshot.use_faiss

    @property
    @catalog_read
    def faiss_index(self):
        index = self._snapshot.faiss_index
        return faiss.clone_index(index) if index is not None else None

    @property
    @catalog_read
    def db_revision(self):
        return self._snapshot.db_revision

    @catalog_read
    def has_current_index(self, revision):
        snapshot = self._snapshot
        return (snapshot.db_revision == revision and snapshot.model_name == self.model_name_or_path
                and snapshot.dimension == self.embedding_dim and snapshot.embeddings is not None)

    def _get_configured_model_name(self) -> str:
        """Read model name from env or config/model_config.json, fallback to bge-small-zh-v1.5."""
        env_model = os.environ.get("EMBEDDING_MODEL")
        if env_model:
            return env_model.strip()

        config_path = os.path.join(CONFIG_DIR, "model_config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    return cfg.get("embedding_model", "bge-small-zh-v1.5")
            except Exception as e:
                print(f"Warning: Failed to load model_config.json: {e}")
        return "bge-small-zh-v1.5"

    def _initialize_model(self):
        if not HAS_SENTENCE_TRANSFORMERS or self.model is not None:
            return

        # Force offline mode to prevent intranet/network hangs (P0 Fix H3)
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

        offline_model_path = os.path.join(MODEL_DIR, self.model_name_or_path)
        if os.path.exists(offline_model_path):
            model_path = offline_model_path
            print(f"Loading offline sentence-transformers model from {model_path}...")
        else:
            model_path = self.model_name_or_path
            print(f"Offline directory absent at {offline_model_path}. Loading from local HuggingFace cache...")

        try:
            self.model = SentenceTransformer(model_path)
            try:
                dim_fn = getattr(self.model, "get_embedding_dimension", None) or getattr(
                    self.model, "get_sentence_embedding_dimension", None
                )
                if callable(dim_fn):
                    self.embedding_dim = int(dim_fn())
            except Exception:
                pass
            print(f"SentenceTransformer model [{self.model_name_or_path}] loaded successfully (dim={self.embedding_dim}).")
        except Exception as e:
            print(f"Warning: Local SentenceTransformer model load failed: {e}. Fallback to keyword FTS5 search.")
            self.model = None

    @property
    def is_enabled(self) -> bool:
        if os.getenv("LISTTRANS_EVAL_KEYWORD_ONLY") == "1":
            return False
        if self.model is None and HAS_SENTENCE_TRANSFORMERS:
            with self._model_lock:
                if self.model is None:
                    self._initialize_model()
        return self.model is not None

    def encode_queries(self, texts: List[str], batch_size: int = 128) -> Optional[np.ndarray]:
        """Encode a batch of query texts into normalized embeddings."""
        if not self.is_enabled or not texts:
            return None
        try:
            with self._model_lock:
                embeddings = self.model.encode(texts, batch_size=batch_size, show_progress_bar=False, convert_to_numpy=True)
            # L2 normalize using pure Numpy (preventing FAISS C++ OpenMP deadlock on Windows)
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            embeddings = embeddings / norms
            return embeddings
        except Exception as e:
            if os.getenv("LISTTRANS_EVAL_REQUIRE_VECTOR") == "1":
                raise RuntimeError("沙箱向量编码失败。") from e
            print(f"Error encoding query texts: {e}")
            return None

    def encode_single_query(self, text: str) -> Optional[np.ndarray]:
        """Encode a single query text into a normalized embedding."""
        res = self.encode_queries([text])
        if res is not None:
            return res[0]
        return None

    @catalog_read
    def load_cache(self, db_path: Optional[str] = None) -> bool:
        """Load a complete snapshot for the live catalog, leaving memory alone on failure."""
        from app import db_manager

        db_path = db_path or db_manager.DB_PATH
        cache_dir = Path(get_cache_dir())
        try:
            model_ready = self.is_enabled
            revision, live_materials = self._read_catalog(db_path)
            bundle = cache_dir / SNAPSHOT_FILE
            if bundle.is_file():
                with zipfile.ZipFile(bundle) as archive:
                    meta = json.loads(archive.read("cache_metadata.json"))
                    if meta.get("db_revision") != revision:
                        return False
                    candidate = self._read_cache_candidate(meta, archive.open, set(archive.namelist()), revision)
            else:
                # Older installations shipped flat files without a revision. Reuse them
                # only when their complete vector inputs match the current catalog.
                meta = json.loads((cache_dir / "cache_metadata.json").read_text(encoding="utf-8"))
                if "db_revision" in meta:
                    return False  # An interrupted new-format publication is not legacy.
                # Legacy startup searched its persisted NumPy matrix. Keep its
                # ranking order until the first full rebuild of this generation.
                legacy_files = {"materials_embeddings.npy"} if (cache_dir / "materials_embeddings.npy").is_file() else set()
                candidate = self._read_cache_candidate(
                    meta, lambda name: (cache_dir / name).open("rb"), legacy_files, revision
                )
            if sorted(candidate.materials, key=lambda row: row["code"]) != sorted(live_materials, key=lambda row: row["code"]):
                return False
            if model_ready and candidate.embeddings is None:
                return False
            self._snapshot = candidate
            return True
        except Exception as exc:
            print(f"Vector cache unavailable; rebuild required: {exc}")
            return False

    def _read_cache_candidate(self, meta, open_file, names, revision, expected_text_schema: Optional[str] = None):
        if meta.get("model_name") != self.model_name_or_path or meta.get("dimension") != self.embedding_dim:
            raise ValueError("缓存模型或维度与当前模型不一致")
        schema_ver = str(meta.get("text_schema_version") or "legacy-v1")
        if expected_text_schema is not None and schema_ver != expected_text_schema:
            raise ValueError(f"缓存编码文本版本不匹配: expected={expected_text_schema}, actual={schema_ver}")
        with open_file("index_to_material.json") as stream:
            materials = json.load(stream)
        if not isinstance(materials, list) or len(materials) != meta.get("item_count"):
            raise ValueError("缓存映射行数与元数据不一致")
        npy = "materials_embeddings.npy"
        embeddings = None
        if npy in names:
            with open_file(npy) as stream:
                embeddings = np.load(stream, allow_pickle=False)
        index_file = "materials.index"
        index = None
        if HAS_FAISS and index_file in names:
            with open_file(index_file) as stream:
                index = faiss.deserialize_index(np.frombuffer(stream.read(), dtype=np.uint8))
        return IndexCandidate(materials, embeddings, index, index is not None,
                              self.model_name_or_path, self.embedding_dim, revision, schema_ver)

    @staticmethod
    def _catalog_materials(conn):
        return [dict(zip(VECTOR_FIELDS, row)) for row in conn.execute(
            "SELECT code, name, specification, category FROM materials ORDER BY code"
        )]

    @classmethod
    def _read_catalog(cls, db_path):
        if not os.path.isfile(db_path):
            return 0, []
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("BEGIN")
            return read_db_revision(conn), cls._catalog_materials(conn)

    @catalog_read
    def save_cache(self):
        """Persist the current complete snapshot without replacing it in memory."""
        _assert_cache_writable()
        if self._snapshot.db_revision != get_db_revision():
            raise ValueError("拒绝保存与当前物料库版本不一致的索引")
        self._persist_candidate(self._snapshot)

    def _persist_candidate(self, candidate, finalize=None, cancel_event=None):
        from app.excel_boundary import ParseCancelledError

        cache_dir = get_cache_dir()
        _assert_cache_writable(cache_dir)
        os.makedirs(cache_dir, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".stage_", dir=cache_dir)
        try:
            self.write_candidate_cache_to_dir(stage, candidate)
            checkpoint()
            if cancel_event is not None and cancel_event.is_set():
                raise ParseCancelledError("向量索引发布已取消，保留原有索引")
            self.commit_candidate_cache(stage, finalize=finalize)
        finally:
            if not is_cache_write_blocked(cache_dir):
                shutil.rmtree(stage, ignore_errors=True)

    def build_index_candidate(
        self,
        materials: List[Dict[str, Any]],
        task_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        db_revision: int = 0,
        *,
        text_mode: str = "legacy",
    ) -> IndexCandidate:
        """
        Build an immutable shadow index candidate from materials without touching
        the live index or disk cache. Publication happens later via publish_candidate().
        materials: List of Dict containing 'code', 'name', 'specification', 'category'
        """
        from app.excel_boundary import ParseCancelledError
        from app.vector_fusion import (
            VECTOR_TEXT_SCHEMA_CANONICAL,
            VECTOR_TEXT_SCHEMA_LEGACY,
            build_material_vector_text,
        )

        if cancel_event is not None and cancel_event.is_set():
            raise ParseCancelledError("向量索引候选构建已在开始前取消")

        schema_ver = VECTOR_TEXT_SCHEMA_CANONICAL if text_mode == "canonical" else VECTOR_TEXT_SCHEMA_LEGACY

        # Build new mappings and texts without mutating the current active index
        new_index_to_material = []
        texts = []
        for m in materials:
            new_index_to_material.append({key: m[key] for key in VECTOR_FIELDS})
            if text_mode == "canonical":
                enc_text, _ = build_material_vector_text(m, mode="canonical")
                texts.append(enc_text)
            else:
                texts.append(f"{m['name']} {m['specification']}".strip())

        # Model unavailable: still return a mapping-only candidate so the DB/cache can
        # publish a consistent (embedding-less) new state; search falls back to FTS.
        if not self.is_enabled:
            if self._snapshot.embeddings is not None and os.getenv("LISTTRANS_EVAL_KEYWORD_ONLY") != "1":
                raise RuntimeError("向量模型不可用，保留上一版物料索引")
            return IndexCandidate(
                new_index_to_material, None, None, False,
                self.model_name_or_path, self.embedding_dim, db_revision, schema_ver,
            )

        print(f"Building vector index candidate for {len(materials)} materials with model [{self.model_name_or_path}] (dim={self.embedding_dim})...")
        start_time = time.time()

        # Encode all texts in chunked batches to report progress
        embeddings_list = []
        chunk_size = 1000
        total_items = len(texts)

        for idx in range(0, total_items, chunk_size):
            checkpoint()
            if cancel_event is not None and cancel_event.is_set():
                raise ParseCancelledError("向量索引候选构建已超时取消，保留原有索引")
            chunk_texts = texts[idx : idx + chunk_size]
            chunk_embs = self.encode_queries(chunk_texts, batch_size=256)
            if chunk_embs is None:
                raise RuntimeError(f"向量编码失败（批次起始行 {idx}）")
            embeddings_list.append(chunk_embs)

            if task_id:
                try:
                    from app.server import update_task_progress
                    prog = min(95, int(10 + ((idx + len(chunk_texts)) / max(1, total_items)) * 85))
                    update_task_progress(task_id, vectorization_progress=prog, current_step=f"正在重建向量特征: ({idx + len(chunk_texts)}/{total_items})")
                except Exception as e:
                    print(f"Error updating task progress in build_index_candidate: {e}")

        new_numpy_embeddings = (
            np.vstack(embeddings_list) if embeddings_list else np.empty((0, self.embedding_dim), dtype=np.float32)
        )

        new_faiss_index = None
        new_use_faiss = False
        if HAS_FAISS:
            new_faiss_index = faiss.IndexFlatIP(self.embedding_dim)
            new_faiss_index.add(new_numpy_embeddings)
            new_use_faiss = True
            print(f"FAISS candidate built in {time.time() - start_time:.2f}s.")
        else:
            print(f"Numpy candidate built in {time.time() - start_time:.2f}s.")

        return IndexCandidate(
            new_index_to_material,
            new_numpy_embeddings,
            new_faiss_index,
            new_use_faiss,
            self.model_name_or_path,
            self.embedding_dim,
            db_revision,
            schema_ver,
        )

    def publish_candidate(
        self,
        candidate: Optional[IndexCandidate],
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Atomically swap the live in-memory index to the candidate.

        Cancellation is re-checked inside self._lock so the check and the swap are one
        atomic operation (closes the check-then-swap TOCTOU window).
        """
        from app.excel_boundary import ParseCancelledError

        if candidate is None:
            return
        with self._lock:
            if cancel_event is not None and cancel_event.is_set():
                raise ParseCancelledError("向量索引发布已取消，保留原有索引")
            if (candidate.model_name, candidate.dimension) != (self.model_name_or_path, self.embedding_dim):
                raise ValueError("向量候选的模型或维度已变化")
            self._snapshot = replace(candidate)

    def snapshot_published_state(self) -> IndexCandidate:
        """Capture the published state for coordinated rollback."""
        with self._lock:
            return replace(self._snapshot)

    def rollback_published_state(self, snapshot: IndexCandidate) -> None:
        """Restore the previous generation without changing the configured encoder."""
        with self._lock:
            self._snapshot = replace(snapshot)

    @catalog_read
    def commit_catalog_snapshot(self, conn):
        """Stage the candidate before committing a DB/FTS transaction."""
        revision = read_db_revision(conn)
        materials = self._catalog_materials(conn)
        old = self._snapshot
        if (old.embeddings is not None and old.model_name == self.model_name_or_path
                and old.dimension == self.embedding_dim
                and sorted(old.materials, key=lambda row: row["code"]) == materials):
            candidate = replace(old, db_revision=revision)
        else:
            candidate = self.build_index_candidate(materials, db_revision=revision)
        self._persist_candidate(candidate, finalize=conn.commit)
        self._snapshot = candidate

    def write_candidate_cache_to_dir(self, target_dir: str, candidate: IndexCandidate) -> None:
        """Write candidate cache files into target_dir (a '.stage_*' directory in the
        runtime cache root). No live cache file is touched; the caller promotes the
        directory via commit_candidate_cache() after the DB swap succeeds."""
        _assert_cache_writable()
        _assert_cache_writable(os.path.dirname(os.path.abspath(target_dir)))
        os.makedirs(target_dir, exist_ok=True)
        index_file = os.path.join(target_dir, "materials.index")
        npy_file = os.path.join(target_dir, "materials_embeddings.npy")
        mapping_file = os.path.join(target_dir, "index_to_material.json")
        meta_file = os.path.join(target_dir, "cache_metadata.json")

        with open(mapping_file, "w", encoding="utf-8") as f:
            json.dump(_copy_material_value(candidate.materials), f, ensure_ascii=False, indent=4)

        if candidate.use_faiss and candidate.faiss_index is not None:
            faiss.write_index(candidate.faiss_index, index_file)

        # Always persist embeddings when the candidate carries them (unlike save_cache,
        # this path must not silently skip the .npy sidecar).
        if candidate.embeddings is not None:
            np.save(npy_file, candidate.embeddings)

        metadata = {
            "model_name": candidate.model_name,
            "dimension": candidate.dimension,
            "item_count": len(candidate.materials),
            "db_revision": candidate.db_revision,
            "text_schema_version": candidate.text_schema_version,
            "timestamp": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=4)

        # The flat files remain available to legacy diagnostics. Live loaders open
        # a single ZIP generation, so even another process cannot mix its files.
        with zipfile.ZipFile(os.path.join(target_dir, SNAPSHOT_FILE), "w") as archive:
            for name in CACHE_FILE_NAMES:
                path = os.path.join(target_dir, name)
                if os.path.isfile(path):
                    archive.write(path, name)
        with open(os.path.join(target_dir, SNAPSHOT_FILE), "rb+") as snapshot_file:
            os.fsync(snapshot_file.fileno())

    def _restore_cache_backup(
        self,
        target_dir: str,
        backup_dir: str,
        promoted_files: List[str],
        backup_moved: List[str],
    ) -> Optional[OSError]:
        """Best-effort restore of the backed-up cache generation.

        Only removes files that were actually promoted from the staging directory
        (never deleting untouched old files when backup-phase move failed), then moves
        every file in backup_moved back into target_dir.
        Returns None on success or the first OSError encountered.
        """
        try:
            for name in promoted_files:
                promoted = os.path.join(target_dir, name)
                if os.path.exists(promoted) and not (name == SNAPSHOT_FILE and name in backup_moved):
                    os.remove(promoted)
            for name in backup_moved:
                backup_file = os.path.join(backup_dir, name)
                if os.path.exists(backup_file):
                    os.replace(backup_file, os.path.join(target_dir, name))
            return None
        except OSError as exc:
            return exc

    @catalog_read
    def commit_candidate_cache(self, stage_dir: str, target_dir: Optional[str] = None, *, finalize=None) -> None:
        """Promote the staged cache generation as one reversible unit.

        Tracks three explicit sets:
          - backup_moved: old files actually moved to '.backup_*'
          - promoted_files: new candidate files actually moved into target_dir
          - untouched files: live files not in backup_moved or promoted_files
        If either backup move or candidate promotion fails, only promoted_files are
        removed and backup_moved are restored (untouched old files are never deleted).
        If restoration itself fails, persistent recovery block is engaged, both
        directories are preserved on disk as recovery material, and CacheRollbackError
        is raised.
        """
        if target_dir is None:
            target_dir = get_cache_dir()
        _assert_cache_writable()
        _assert_cache_writable(target_dir)
        staged_names = sorted(os.listdir(stage_dir), key=lambda name: (name == SNAPSHOT_FILE, name))
        os.makedirs(target_dir, exist_ok=True)
        # The backup name must not contain the word "stage": failure-injection harnesses
        # count promotions by staging path, and backup/restore moves are a separate concern.
        backup_dir = os.path.join(
            target_dir, f".backup_{os.getpid()}_{threading.get_ident()}"
        )
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
        os.makedirs(backup_dir, exist_ok=True)

        backup_candidates = list(CACHE_FILE_NAMES) + [
            name for name in staged_names if name not in CACHE_FILE_NAMES and name != SNAPSHOT_FILE
        ]
        backup_moved: List[str] = []
        promoted_files: List[str] = []
        failure_stage = "backup_move"
        promotion_error: Optional[Exception] = None
        try:
            # Keep the live bundle readable until its atomic replacement.
            live_bundle = os.path.join(target_dir, SNAPSHOT_FILE)
            if SNAPSHOT_FILE in staged_names and os.path.isfile(live_bundle):
                shutil.copyfile(live_bundle, os.path.join(backup_dir, SNAPSHOT_FILE))
            # 1. Move every existing flat cache file into the backup.
            for name in backup_candidates:
                live_file = os.path.join(target_dir, name)
                if os.path.exists(live_file):
                    os.replace(live_file, os.path.join(backup_dir, name))
                    backup_moved.append(name)
            # 2. Promote the staged generation file by file (sorted for deterministic tests).
            failure_stage = "candidate_promote"
            for name in staged_names:
                os.replace(
                    os.path.join(stage_dir, name),
                    os.path.join(target_dir, name),
                )
                promoted_files.append(name)
                if name == SNAPSHOT_FILE and os.path.isfile(os.path.join(backup_dir, name)):
                    backup_moved.append(name)
            if finalize is not None:
                failure_stage = "finalize"
                finalize()
        except Exception as exc:
            promotion_error = exc

        if promotion_error is not None:
            restore_error = self._restore_cache_backup(
                target_dir, backup_dir, promoted_files, backup_moved
            )
            if restore_error is not None:
                # Both stage failure and restore failed: keep both directories as recovery
                # material, engage persistent write block and surface a distinct error.
                block_cache_writes(
                    reason=(
                        f"缓存整套切换在 {failure_stage} 阶段失败且回滚未完成，"
                        f"已保留备份与暂存材料并进入写入阻断状态: "
                        f"error={promotion_error!r}, restore={restore_error!r}"
                    ),
                    details={
                        "failure_stage": failure_stage,
                        "promotion_error": repr(promotion_error),
                        "restore_error": repr(restore_error),
                        "backup_moved": list(backup_moved),
                        "promoted_files": list(promoted_files),
                        "stage_dir": os.path.abspath(stage_dir),
                        "backup_dir": os.path.abspath(backup_dir),
                        "target_dir": os.path.abspath(target_dir),
                    },
                    target_dir=target_dir,
                )
                raise CacheRollbackError(
                    "缓存整套切换失败且回滚未完成，已保留备份与暂存材料并进入写入阻断状态: "
                    f"stage={failure_stage}, error={promotion_error!r}, restore={restore_error!r}"
                ) from promotion_error
            # Restore succeeded: discard the empty/stale staging and backup directories,
            # then propagate the original failure so the worker rolls DB/memory back too.
            shutil.rmtree(stage_dir, ignore_errors=True)
            shutil.rmtree(backup_dir, ignore_errors=True)
            raise promotion_error

        # 3. Whole generation promoted: the backup generation is now obsolete.
        shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.rmtree(stage_dir, ignore_errors=True)

    @catalog_read
    def build_index(
        self,
        materials: List[Dict[str, Any]],
        task_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        *,
        db_revision: Optional[int] = None,
    ):
        """Build and persist a complete generation, then publish it in memory."""
        _assert_cache_writable()
        revision = get_db_revision() if db_revision is None else db_revision
        candidate = self.build_index_candidate(
            materials, task_id=task_id, cancel_event=cancel_event, db_revision=revision
        )
        if db_revision is None:
            from app import db_manager
            current_revision, live = self._read_catalog(db_manager.DB_PATH)
            if current_revision != revision or live != sorted(candidate.materials, key=lambda row: row["code"]):
                raise ValueError("构建输入与当前物料库版本不一致")
        self._persist_candidate(candidate, cancel_event=cancel_event)
        self._snapshot = candidate

    def add_to_index(self, material: Dict[str, Any]):
        """Reject unversioned changes. Update the catalog through its transaction."""
        _assert_cache_writable()
        raise RuntimeError("单条向量增量入口已停用，请通过物料库事务更新索引")

    def _search_snapshot(self, snapshot: IndexCandidate, query_vector: np.ndarray, top_k: int) -> List[Tuple[Dict[str, str], float]]:
        if query_vector is None or top_k <= 0 or query_vector.shape[-1] != snapshot.dimension or not snapshot.materials:
            return []
        faiss_idx = snapshot.faiss_index
        numpy_embs = snapshot.embeddings
        materials = snapshot.materials

        results = []
        q_vec = np.expand_dims(query_vector, axis=0) if query_vector.ndim == 1 else query_vector

        if snapshot.use_faiss and faiss_idx is not None:
            try:
                scores, indices = faiss_idx.search(q_vec, min(top_k, faiss_idx.ntotal))
                for score, idx in zip(scores[0], indices[0]):
                    if idx < 0 or idx >= len(materials):
                        continue
                    results.append((_copy_material_value(materials[idx]), float(score)))
                return results
            except Exception:
                pass

        if numpy_embs is not None:
            try:
                q_vec_1d = query_vector.squeeze()
                scores = np.einsum('ij,j->i', numpy_embs, q_vec_1d)
                k = min(top_k, len(scores))
                top_indices = np.argpartition(scores, -k)[-k:]
                top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]
                for idx in top_indices:
                    results.append((_copy_material_value(materials[idx]), float(scores[idx])))
                return results
            except Exception:
                pass
        return []

    def search(self, query_vector: np.ndarray, top_k: int = 200) -> List[Tuple[Dict[str, str], float]]:
        """Search the active legacy-v1 snapshot for top_k similar materials."""
        if query_vector is None:
            return []
        with self._lock:
            snapshot = self._snapshot
        return self._search_snapshot(snapshot, query_vector, top_k)

    @catalog_read
    def load_or_build_candidate_index(
        self,
        text_mode: str = "canonical",
        db_path: Optional[str] = None,
        *,
        persist: bool = True,
    ) -> Optional[IndexCandidate]:
        """Load or build an isolated versioned candidate index without modifying legacy-v1 cache."""
        from app import db_manager
        from app.vector_fusion import VECTOR_TEXT_SCHEMA_CANONICAL, VECTOR_TEXT_SCHEMA_LEGACY

        schema_ver = VECTOR_TEXT_SCHEMA_CANONICAL if text_mode == "canonical" else VECTOR_TEXT_SCHEMA_LEGACY
        if schema_ver == VECTOR_TEXT_SCHEMA_LEGACY:
            return self._snapshot if self._snapshot.embeddings is not None else None
        if not self.is_enabled:
            return None

        db_path = db_path or db_manager.DB_PATH
        revision, live_materials = self._read_catalog(db_path)

        cached = self._candidate_snapshots.get(schema_ver)
        if (
            cached is not None
            and cached.db_revision == revision
            and cached.model_name == self.model_name_or_path
            and cached.dimension == self.embedding_dim
            and cached.text_schema_version == schema_ver
            and cached.embeddings is not None
            and len(cached.materials) == len(live_materials)
        ):
            return cached

        cand_dir = Path(self.get_candidate_cache_dir(schema_ver))
        bundle = cand_dir / SNAPSHOT_FILE
        if bundle.is_file():
            try:
                with zipfile.ZipFile(bundle) as archive:
                    meta = json.loads(archive.read("cache_metadata.json"))
                    if meta.get("db_revision") == revision:
                        cand = self._read_cache_candidate(
                            meta, archive.open, set(archive.namelist()), revision, expected_text_schema=schema_ver
                        )
                        if (
                            cand.embeddings is not None
                            and sorted(cand.materials, key=lambda r: r["code"]) == sorted(live_materials, key=lambda r: r["code"])
                        ):
                            self._candidate_snapshots[schema_ver] = cand
                            return cand
            except Exception:
                pass

        # Enrich with brand for canonical material text without altering VECTOR_FIELDS snapshot identity
        enriched_rows = []
        if os.path.isfile(db_path):
            with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                enriched_rows = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT code, name, specification, category, brand FROM materials ORDER BY code"
                    )
                ]
        else:
            enriched_rows = live_materials

        candidate = self.build_index_candidate(enriched_rows, db_revision=revision, text_mode=text_mode)
        if candidate.embeddings is None:
            return None
        if persist:
            _assert_cache_writable(str(cand_dir))
            os.makedirs(cand_dir, exist_ok=True)
            stage = tempfile.mkdtemp(prefix=".stage_", dir=str(cand_dir))
            try:
                self.write_candidate_cache_to_dir(stage, candidate)
                self.commit_candidate_cache(stage, target_dir=str(cand_dir))
            finally:
                if not is_cache_write_blocked(str(cand_dir)):
                    shutil.rmtree(stage, ignore_errors=True)
        self._candidate_snapshots[schema_ver] = candidate
        return candidate

    def search_candidate(
        self,
        query_vector: np.ndarray,
        top_k: int = 50,
        text_schema_version: str = "legacy-v1",
    ) -> List[Tuple[Dict[str, str], float]]:
        """Search either the legacy-v1 snapshot or an isolated versioned candidate snapshot."""
        if query_vector is None:
            return []
        if not text_schema_version or text_schema_version == "legacy-v1":
            return self.search(query_vector, top_k=top_k)
        with self._lock:
            snap = self._candidate_snapshots.get(text_schema_version)
        if snap is None or snap.embeddings is None:
            return []
        return self._search_snapshot(snap, query_vector, top_k)

    @catalog_read
    def check_db_timestamp_and_build(self, db_path: str, force: bool = False):
        """Compatibility entry point: reconcile using the DB revision and model."""
        _assert_cache_writable()
        if not self.is_enabled:
            if force:
                raise RuntimeError("向量模型不可用，无法重建索引")
            return
        if not force and self.load_cache(db_path):
            return
        revision, materials = self._read_catalog(db_path)
        self.build_index(materials, db_revision=revision)

# Global instance
index_manager = VectorIndexManager()
