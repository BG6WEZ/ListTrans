import os
import re
import sqlite3
import threading
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from app.runtime_jobs import JobRejected, JobStopped, checkpoint, supplement_jobs
from functools import lru_cache, wraps
from contextlib import contextmanager, closing
from copy import deepcopy
from time import time_ns
from pathlib import Path

DB_PATH = os.getenv("MATERIALS_DB_PATH", os.path.join(os.path.dirname(__file__), "materials.db"))

# V017-M1 / M1-C03：解析结构版本单一事实源。
# parsed_schema_version 提升至 3：extract_params 输出含 schema_version=3 及
# typed_attrs / unwhitelisted_attrs（类型化字段与字段白名单）。旧 v0/v1/v2 缓存行
# 在未重解析场景下仍可被 matcher 安全读取（parse_material_attrs 对缺键容错）。
PARSED_SCHEMA_VERSION = 3

# Thread-local SQLite connection pool
_local_db = threading.local()
catalog_lock = threading.RLock()
catalog_writer_lock = threading.RLock()


def _with_catalog_lock(lock):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with lock:
                return function(*args, **kwargs)
        return call
    return decorate


catalog_read = _with_catalog_lock(catalog_lock)
catalog_write = _with_catalog_lock(catalog_writer_lock)


def _db_identity(path):
    path = os.path.abspath(path)
    try:
        stat = os.stat(path)
        return path, stat.st_dev, stat.st_ino
    except FileNotFoundError:
        return path, None, None

def get_thread_db_connection():
    """Retrieve thread-local SQLite connection to eliminate repeated open/close overhead."""
    conn = getattr(_local_db, "conn", None)
    identity = _db_identity(DB_PATH)
    if conn is not None and getattr(_local_db, "identity", None) == identity:
        try:
            conn.execute("SELECT 1")
            return conn
        except Exception:
            _local_db.conn = None
    elif conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local_db.conn = None

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    _local_db.conn = conn
    _local_db.identity = _db_identity(DB_PATH)
    return conn

def _has_table(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def read_db_revision(conn) -> int:
    """Read the committed revision, or the uncommitted revision on a writer's connection."""
    try:
        row = conn.execute("SELECT value FROM materials_metadata WHERE key='db_revision'").fetchone()
    except sqlite3.OperationalError:
        if not _has_table(conn, "materials_metadata"):
            return 0  # Pre-revision catalogs can be opened without a write migration.
        raise
    return int(row[0]) if row else 0


@catalog_read
def get_db_revision() -> int:
    return read_db_revision(get_thread_db_connection()) if os.path.isfile(DB_PATH) else 0


def _next_db_revision(previous):
    return max(time_ns() // 1_000, previous + 1)


def _set_db_revision(conn, revision):
    conn.execute("CREATE TABLE IF NOT EXISTS materials_metadata (key TEXT PRIMARY KEY, value TEXT)")
    if revision:
        conn.execute("INSERT OR REPLACE INTO materials_metadata VALUES ('db_revision', ?)", (str(revision),))
    else:
        conn.execute("DELETE FROM materials_metadata WHERE key='db_revision'")


@lru_cache(maxsize=32)
def _HAS_FTS_CACHE(catalog_key):
    return _has_table(get_thread_db_connection(), "materials_fts")


def _catalog_key():
    return _db_identity(DB_PATH), get_db_revision()

# R02 A-M5-01: replace imports populate these isolated stage tables first; the live
# materials/materials_fts tables are only touched by the exclusive RENAME swap.
# Names intentionally contain "_stage" so residue checks can assert zero leftovers.
STAGE_MATERIALS_TABLE = "materials_stage"
STAGE_FTS_TABLE = "materials_fts_stage"


class RecoveryBlockedError(RuntimeError):
    """Raised when a DB/import write is attempted while an unverified recovery-failure
    block is active (DB, memory, or cache rollback failure)."""


_db_write_blocked_paths: set[str] = set()
_db_recovery_details_by_path: Dict[str, Dict[str, Any]] = {}


def _resolved_db_key(db_path: Optional[str] = None) -> str:
    return os.path.abspath(db_path if db_path is not None else DB_PATH)


def _db_recovery_marker_path(db_path: Optional[str] = None) -> str:
    return f"{_resolved_db_key(db_path)}.recovery_blocked.json"


def is_db_write_blocked(db_path: Optional[str] = None) -> bool:
    key = _resolved_db_key(db_path)
    if key in _db_write_blocked_paths:
        return True
    return os.path.isfile(_db_recovery_marker_path(key))


def get_db_recovery_block_info(db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    key = _resolved_db_key(db_path)
    marker = _db_recovery_marker_path(key)
    if os.path.isfile(marker):
        try:
            import json
            with open(marker, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            return {"blocked": True, "marker_path": marker}
    if key in _db_write_blocked_paths:
        return dict(_db_recovery_details_by_path.get(key) or {"blocked": True, "db_path": key})
    return None


def block_db_writes(
    reason: str,
    details: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> None:
    import json
    import datetime

    key = _resolved_db_key(db_path)
    _db_write_blocked_paths.add(key)
    payload = {
        "blocked": True,
        "reason": reason,
        "details": details or {},
        "db_path": key,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _db_recovery_details_by_path[key] = payload
    try:
        parent = os.path.dirname(key)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(_db_recovery_marker_path(key), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def clear_db_write_block_on_verified_recovery(db_path: Optional[str] = None) -> None:
    """Clear DB recovery block ONLY after verified cross-layer consistency check."""
    key = _resolved_db_key(db_path)
    _db_write_blocked_paths.discard(key)
    _db_recovery_details_by_path.pop(key, None)
    marker = _db_recovery_marker_path(key)
    if os.path.isfile(marker):
        os.remove(marker)


def reset_db_write_block(db_path: Optional[str] = None) -> None:
    """Reset hook for isolated test fixtures; production code never calls this."""
    if db_path is None:
        keys = set(_db_write_blocked_paths)
        keys.add(_resolved_db_key())
    else:
        keys = {_resolved_db_key(db_path)}
    for key in keys:
        _db_write_blocked_paths.discard(key)
        _db_recovery_details_by_path.pop(key, None)
        marker = _db_recovery_marker_path(key)
        if os.path.isfile(marker):
            try:
                os.remove(marker)
            except OSError:
                pass


def _assert_db_writable() -> None:
    if is_db_write_blocked():
        info = get_db_recovery_block_info() or {}
        reason = info.get("reason") or "存在未核验的恢复失败状态"
        raise RecoveryBlockedError(
            "物料库处于恢复失败写入阻断状态（需人工核验全层一致性后解除）："
            f"{reason}"
        )
    from app.vector_index import is_cache_write_blocked, get_cache_recovery_block_info

    if is_cache_write_blocked():
        info = get_cache_recovery_block_info() or {}
        reason = info.get("reason") or "向量缓存处于写入阻断状态"
        raise RecoveryBlockedError(
            "向量缓存处于恢复失败写入阻断状态（需人工核验全层一致性后解除）："
            f"{reason}"
        )


@catalog_read
def inspect_db_recovery_state() -> Dict[str, Any]:
    """Read-only inspection of live, FTS, metadata and leftover stage/old tables."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        tables = {
            row[0]
            for row in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        has_live = "materials" in tables
        has_fts = "materials_fts" in tables
        db_codes = (
            [r[0] for r in cur.execute("SELECT code FROM materials ORDER BY code").fetchall()]
            if has_live
            else []
        )
        fts_codes = (
            [r[0] for r in cur.execute("SELECT code FROM materials_fts ORDER BY code").fetchall()]
            if has_fts
            else None
        )
        leftover_tables = sorted(
            name
            for name in (
                STAGE_MATERIALS_TABLE,
                STAGE_FTS_TABLE,
                "materials_old",
                "materials_fts_old",
            )
            if name in tables
        )
        return {
            "has_live": has_live,
            "has_fts": has_fts,
            "db_codes": db_codes,
            "fts_codes": fts_codes,
            "leftover_tables": leftover_tables,
            "metadata": get_db_metadata() if has_live else {},
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn

@contextmanager
def get_db_cursor(commit: bool = True):
    """Context manager for SQLite database cursor with automatic transaction handling."""
    conn = get_thread_db_connection()
    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    finally:
        cur.close()


@catalog_write
def init_db(xlsx_path=None, force_reload=False):
    """Initialize an empty catalog or import Excel through the normal publication path."""
    with closing(get_db_connection()) as conn, conn:
        has_materials = _has_table(conn, "materials")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(materials)")}
        if xlsx_path and os.path.isfile(xlsx_path) and (
            force_reload or "parsed_params" not in cols or not conn.execute("SELECT 1 FROM materials LIMIT 1").fetchone()
        ):
            return import_materials_from_excel(xlsx_path, mode="replace", filename=os.path.basename(xlsx_path))
        if has_materials and not force_reload and "parsed_params" in cols:
            if "parsed_schema_version" not in cols:
                _assert_db_writable()
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("ALTER TABLE materials ADD COLUMN parsed_schema_version INTEGER DEFAULT 0")
                _commit_materials_write(conn)
            return
        _assert_db_writable()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE IF EXISTS materials")
        conn.execute("DROP TABLE IF EXISTS materials_fts")
        _create_materials_table(conn, "materials")
        _create_fts_table(conn, "materials_fts")
        if has_materials:
            _commit_materials_write(conn)


_MATERIAL_COLUMNS = (
    "code", "category", "name", "specification", "unit", "brand", "description",
    "search_tokens", "parsed_params", "parsed_schema_version",
)
_FTS_COLUMNS = ("code", "category", "name", "specification", "brand", "description")


def _create_materials_table(conn, table):
    conn.execute(f"""
        CREATE TABLE {table} (
            code TEXT PRIMARY KEY,
            category TEXT,
            name TEXT,
            specification TEXT,
            unit TEXT,
            brand TEXT,
            description TEXT,
            search_tokens TEXT,
            parsed_params TEXT,
            parsed_schema_version INTEGER DEFAULT 0
        )
    """)


def _create_fts_table(conn, table):
    try:
        conn.execute(f"""
            CREATE VIRTUAL TABLE {table} USING fts5(
                code UNINDEXED, category, name, specification, brand, description, tokenize='unicode61'
            )
        """)
    except sqlite3.OperationalError as exc:
        if "no such module: fts5" not in str(exc):
            raise
        return False
    return True


def _write_fts_records(conn, table, rows, cancel_event=None):
    from app.excel_boundary import ParseCancelledError
    from app.preprocessor import fts_tokenize

    def records():
        for code, *fields in rows:
            checkpoint()
            if cancel_event is not None and cancel_event.is_set():
                raise ParseCancelledError("物料库导入在构建 FTS 索引前超时取消")
            yield (code, *(fts_tokenize(field or "") for field in fields))

    conn.executemany(f"""
        INSERT INTO {table} (code, category, name, specification, brand, description)
        VALUES (?, ?, ?, ?, ?, ?)
    """, records())


def _refresh_fts(conn, codes=None):
    if not _has_table(conn, "materials_fts"):
        if not _create_fts_table(conn, "materials_fts"):
            return
        codes = None
    select = f"SELECT {', '.join(_FTS_COLUMNS)} FROM materials"
    if codes is None:
        conn.execute("DELETE FROM materials_fts")
        _write_fts_records(conn, "materials_fts", conn.execute(select))
        return
    conn.executemany("DELETE FROM materials_fts WHERE code=?", ((code,) for code in codes))
    _write_fts_records(conn, "materials_fts", (
        conn.execute(f"{select} WHERE code=?", (code,)).fetchone() for code in codes
    ))
    # Heal catalogs produced by legacy append, which could contain duplicate FTS rows.
    if conn.execute("""
        SELECT (SELECT COUNT(*) FROM materials) != (SELECT COUNT(*) FROM materials_fts)
    """).fetchone()[0]:
        _refresh_fts(conn)


def _commit_materials_write(conn):
    from app.vector_index import index_manager

    _set_db_revision(conn, _next_db_revision(read_db_revision(conn)))
    index_manager.commit_catalog_snapshot(conn)


@catalog_read
def get_all_materials(limit=1000):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM materials LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@catalog_read
def query_by_code(code: str) -> Optional[Dict[str, Any]]:
    return deepcopy(_query_by_code(_catalog_key(), code.strip()))


@lru_cache(maxsize=50000)
def _query_by_code(catalog_key, code):
    conn = get_thread_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM materials WHERE code = ?", (code,))
    row = cursor.fetchone()
    return dict(row) if row else None

@catalog_read
def _search_materials_keyword_cached(keyword: str, fetch_limit: int = 150) -> List[Dict[str, Any]]:
    return deepcopy(_keyword_rows(_catalog_key(), keyword, fetch_limit))


@lru_cache(maxsize=10000)
def _keyword_rows(catalog_key, keyword, fetch_limit):
    """
    Core cached search routine with standard fetch_limit=150 to maximize LRU cache hit rates.
    """
    if not keyword:
        return []
        
    conn = get_thread_db_connection()
    cursor = conn.cursor()
    
    # Clean keyword of special punctuation and split by delimiters (space, comma, semicolon, *, x, ×, etc.)
    kw_clean = keyword.replace('"', ' ').replace("'", ' ').replace('(', ' ').replace(')', ' ').replace('（', ' ').replace('）', ' ')
    
    # Query expansion for 2D rectangular dimensions (e.g. 630*400 -> derive half-perimeter 1030)
    dim_match = re.search(r'(\d+)\s*[*x×]\s*(\d+)', kw_clean)
    extra_words = []
    if dim_match:
        try:
            d1 = int(dim_match.group(1))
            d2 = int(dim_match.group(2))
            hp_val = str(d1 + d2)
            extra_words.append(hp_val)
        except Exception:
            pass

    kw_clean = kw_clean.replace('*', ' ').replace('×', ' ').replace('x', ' ')
    raw_words = [w.strip() for w in re.split(r'[;；,，\s]+', kw_clean) if w.strip()]
    for ew in extra_words:
        if ew not in raw_words:
            raw_words.append(ew)

    if not raw_words:
        return []
        
    has_fts = _HAS_FTS_CACHE(catalog_key)
    
    if has_fts:
        from app.preprocessor import fts_tokenize
        fts_clauses = []
        synonym_map = {
            "网线": ["网线", "双绞线", "对线", "网络线"],
            "双绞线": ["网线", "双绞线", "对线", "网络线"],
            "配电箱": ["配电箱", "配电柜"],
            "配电柜": ["配电箱", "配电柜"],
            "开关": ["开关", "功能件", "面板"],
            "按钮": ["按钮", "呼叫", "开关"],
            "净化循环空气处理机组": ["净化循环机组", "循环机组", "净化循环"],
            "新风预处理机组": ["净化新风机组", "吊顶新风机组", "新风机组"],
            "吊顶新风预处理机组": ["吊顶新风机组", "吊顶新风"],
            "净化新风预处理机组": ["净化新风机组", "净化新风"],
            "风机盘管机组": ["风机盘管", "风机盘管配件"],
            "医用离心式排风机(箱）": ["医用离心式排风机(箱）", "医用离心式排风机", "离心风机（箱）", "离心风机", "通风机"],
            "医用离心式排风机": ["医用离心式排风机(箱）", "医用离心式排风机", "离心风机（箱）", "离心风机", "通风机"],
            "医用静音型排风机": ["医用离心式排风机(箱）", "医用离心式排风机", "离心风机（箱）", "离心风机", "通风机"],
            "离心式排风机(箱）": ["医用离心式排风机(箱）", "离心风机（箱）", "离心风机", "通风机"],
            "离心式排风机": ["医用离心式排风机(箱）", "离心风机（箱）", "离心风机", "通风机"],
            "静音型排风机": ["医用离心式排风机(箱）", "离心风机（箱）", "离心风机", "通风机"],
            "洁净镀锌钢板通风管安装费": ["镀锌钢板风管", "风管"],
            "洁净镀锌通风管": ["镀锌钢板风管", "风管"],
            "通风管": ["镀锌钢板风管", "风管"],
            "多联机室内机": ["多联式空调室内机", "多联机"],
            "常开防火阀": ["防火阀"]
        }
        for rw in raw_words:
            # Check if this raw word has synonyms
            syns = None
            for key, val in synonym_map.items():
                if rw == key:
                    syns = val
                    break
            
            if syns:
                # Build an OR group of FTS token phrases
                syn_parts = []
                for s in syns:
                    tokenized = fts_tokenize(s)
                    tokens = [t for t in tokenized.split() if t]
                    if tokens:
                        syn_parts.append(" AND ".join([f'"{t}"' for t in tokens]))
                if syn_parts:
                    fts_clauses.append("(" + " OR ".join([f"({p})" for p in syn_parts]) + ")")
            else:
                tokenized = fts_tokenize(rw)
                tokens = [t for t in tokenized.split() if t]
                if tokens:
                    query_parts = []
                    for t in tokens:
                        if t in ["线", "缆"]:
                            query_parts.append('("线" OR "缆")')
                        elif t in ["箱", "柜"]:
                            query_parts.append('("箱" OR "柜")')
                        elif t.isdigit():
                            query_parts.append(f'"{t}"*')
                        else:
                            query_parts.append(f'"{t}"')
                    fts_clauses.append(" AND ".join(query_parts))
                    
        if fts_clauses:
            fts_query = " AND ".join(fts_clauses)
            try:
                cursor.execute("""
                    SELECT m.* 
                    FROM materials_fts fts
                    JOIN materials m ON fts.code = m.code
                    WHERE materials_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (fts_query, fetch_limit))
                results = cursor.fetchall()
                return [dict(r) for r in results]
            except (sqlite3.OperationalError, sqlite3.DatabaseError, sqlite3.Error):
                pass
                
    # Standard query fallback (LIKE '%token%')
    from app.preprocessor import fts_tokenize
    like_tokens = []
    for rw in raw_words:
        tokenized_word = fts_tokenize(rw)
        like_tokens.extend([t for t in tokenized_word.split() if t])
        
    if not like_tokens:
        like_tokens = raw_words
        
    like_clauses = " AND ".join(["search_tokens LIKE ?"] * len(like_tokens))
    params = [f"%{t.lower()}%" for t in like_tokens] + [fetch_limit]
    
    cursor.execute(f"""
        SELECT * FROM materials 
        WHERE {like_clauses}
        LIMIT ?
    """, params)
    
    results = cursor.fetchall()
    return [dict(r) for r in results]

_search_materials_keyword_cached.cache_clear = _keyword_rows.cache_clear
query_by_code.cache_clear = _query_by_code.cache_clear


def search_materials_keyword(keyword: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Public search entry point with unified LRU caching."""
    fetch_limit = max(150, limit)
    cached = _search_materials_keyword_cached(keyword, fetch_limit)
    return cached[:limit]


@lru_cache(maxsize=512)
def _fts_raw_rows(catalog_key, fts_query, fetch_limit):
    """Revision-keyed cached raw FTS5 MATCH execution over existing materials_fts table."""
    if not fts_query or not _HAS_FTS_CACHE(catalog_key):
        return []
    conn = get_thread_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT m.*
            FROM materials_fts fts
            JOIN materials m ON fts.code = m.code
            WHERE materials_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, fetch_limit),
        )
        return [dict(r) for r in cursor.fetchall()]
    except (sqlite3.OperationalError, sqlite3.DatabaseError, sqlite3.Error):
        return []


@catalog_read
def search_materials_fts_raw(fts_query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Public revision-consistent raw FTS5 query entry returning detached copies."""
    if not fts_query or limit <= 0:
        return []
    fetch_limit = max(60, limit)
    cached = _fts_raw_rows(_catalog_key(), fts_query.strip(), fetch_limit)
    return deepcopy(cached[:limit])


search_materials_fts_raw.cache_clear = _fts_raw_rows.cache_clear

@catalog_read
def get_db_metadata() -> Dict[str, Any]:
    """Retrieve database row counts and last upload timestamps."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("BEGIN")
        
        # Get row count from materials table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='materials'")
        has_materials = cursor.fetchone() is not None
        row_count = 0
        if has_materials:
            cursor.execute("SELECT COUNT(*) FROM materials")
            row_count = cursor.fetchone()[0]
        
        metadata = dict(cursor.execute("SELECT key, value FROM materials_metadata")) if _has_table(conn, "materials_metadata") else {}
        return {
            "row_count": row_count,
            "last_upload_date": metadata.get("last_upload_date", "未知"),
            "filename": metadata.get("filename", "未知"),
            "total_rows": int(metadata.get("total_rows", row_count)),
            "imported_rows": int(metadata.get("imported_rows", row_count)),
            "disabled_rows": int(metadata.get("disabled_rows", 0)),
            "db_revision": int(metadata.get("db_revision", 0)),
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass

def update_db_metadata(total_rows: int, imported_rows: int, disabled_rows: int, filename: str):
    """Save upload logs and timestamp details to SQLite."""
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS materials_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("INSERT OR REPLACE INTO materials_metadata (key, value) VALUES ('last_upload_date', ?)", (now_str,))
        cursor.execute("INSERT OR REPLACE INTO materials_metadata (key, value) VALUES ('filename', ?)", (filename,))
        cursor.execute("INSERT OR REPLACE INTO materials_metadata (key, value) VALUES ('total_rows', ?)", (str(total_rows),))
        cursor.execute("INSERT OR REPLACE INTO materials_metadata (key, value) VALUES ('imported_rows', ?)", (str(imported_rows),))
        cursor.execute("INSERT OR REPLACE INTO materials_metadata (key, value) VALUES ('disabled_rows', ?)", (str(disabled_rows),))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass

def parse_disable_annotation(text: str) -> Tuple[bool, Optional[Any], str]:
    """
    Parse whether text contains disable annotation and extract date if present.
    Returns: (has_disable_keyword, stop_date_obj_or_None, matched_keyword_snippet)
    """
    import datetime
    DISABLE_PATTERN = re.compile(r'(?:禁用|停用|无效)\s*(\d{6})?')
    match = DISABLE_PATTERN.search(text)
    if not match:
        return False, None, ""
    
    dt_str = match.group(1)
    if dt_str:
        try:
            y = 2000 + int(dt_str[0:2])
            m = int(dt_str[2:4])
            d = int(dt_str[4:6])
            stop_date = datetime.date(y, m, d)
            return True, stop_date, f"{match.group(0)}"
        except Exception:
            return True, None, f"{match.group(0)}"
    return True, None, f"{match.group(0)}"

def _clean_excel_str_col(series: pd.Series) -> pd.Series:
    """Safely convert an Excel Series to stripped strings without Pandas4 fillna downcasting warnings."""
    s_obj = series.astype(object)
    return s_obj.where(pd.notna(s_obj), "").astype(str).str.strip()


def _cleanup_staged_tables() -> None:
    """Drop any leftover stage tables (and their indexes/FTS shadow tables). Idempotent."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {STAGE_FTS_TABLE}")
        cur.execute(f"DROP TABLE IF EXISTS {STAGE_MATERIALS_TABLE}")
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def cleanup_staged_materials_import() -> None:
    """Public failure-path hook: discard staged replace-import artifacts without touching live tables."""
    _cleanup_staged_tables()


def get_staged_materials() -> List[Dict[str, Any]]:
    """Read the staged material rows (vector build input) before the live tables are swapped."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT code, name, specification, category FROM {STAGE_MATERIALS_TABLE}"
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass


@catalog_read
def _swap_materials_tables_exclusive(
    handle: Dict[str, Any],
    cancel_event: Optional[threading.Event] = None,
    retain_old_generation: bool = False,
) -> Dict[str, Any]:
    """Publish staged tables via BEGIN EXCLUSIVE + atomic RENAME; metadata is written in the
    same transaction. Any failure rolls back, leaving the full pre-state visible, and the
    leftover stage tables are cleaned up afterwards.

    retain_old_generation=True keeps materials_old/materials_fts_old instead of dropping
    them, so the worker can reverse the publication if memory/cache publication fails;
    the retained generation is discarded by drop_retained_materials_old_generation() only
    after every publication layer has succeeded.
    """
    import datetime
    from app.excel_boundary import ParseCancelledError

    conn = sqlite3.connect(DB_PATH, isolation_level="EXCLUSIVE")
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN EXCLUSIVE TRANSACTION")
        if read_db_revision(conn) != handle["base_revision"]:
            raise RuntimeError("物料库版本已变化，请重新暂存导入")
        # In-transaction arbitration: a cancellation arriving between the caller's
        # pre-check and the first destructive DDL still aborts inside this transaction,
        # so no late/timed-out worker can publish a partially checked-out state.
        if cancel_event is not None and cancel_event.is_set():
            raise ParseCancelledError("物料库导入在原子交换事务内被取消，已回滚并清理暂存表")

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (STAGE_MATERIALS_TABLE,),
        )
        if cursor.fetchone() is None:
            raise RuntimeError("暂存物料表不存在，无法执行原子交换（materials_stage missing）")
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (STAGE_FTS_TABLE,),
        )
        has_stage_fts = cursor.fetchone() is not None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='materials'"
        )
        has_live = cursor.fetchone() is not None
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='materials_fts'"
        )
        has_live_fts = cursor.fetchone() is not None

        if has_live:
            # Index names are global in SQLite: drop the old live indexes before renaming so
            # the published table can recreate them under their production names.
            cursor.execute("DROP INDEX IF EXISTS idx_materials_code")
            cursor.execute("DROP INDEX IF EXISTS idx_materials_category")
            cursor.execute("DROP TABLE IF EXISTS materials_old")
            cursor.execute("ALTER TABLE materials RENAME TO materials_old")
        cursor.execute(f"ALTER TABLE {STAGE_MATERIALS_TABLE} RENAME TO materials")

        # SQLite preserves index names across ALTER TABLE ... RENAME, so the stage
        # indexes are now attached to the live table. Drop the '*_stage*' names before
        # recreating the production-named indexes (zero staged names may remain).
        cursor.execute("DROP INDEX IF EXISTS idx_materials_stage_code")
        cursor.execute("DROP INDEX IF EXISTS idx_materials_stage_category")

        if has_live_fts:
            cursor.execute("DROP TABLE IF EXISTS materials_fts_old")
            cursor.execute("ALTER TABLE materials_fts RENAME TO materials_fts_old")
        if has_stage_fts:
            cursor.execute(f"ALTER TABLE {STAGE_FTS_TABLE} RENAME TO materials_fts")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_materials_code ON materials(code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_materials_category ON materials(category)")

        # Metadata is part of the same atomic transaction.
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS materials_metadata (key TEXT PRIMARY KEY, value TEXT)"
        )
        metadata_pairs = [
            ("last_upload_date", now_str),
            ("filename", handle["filename"]),
            ("total_rows", str(handle["total_rows"])),
            ("imported_rows", str(handle["imported_rows"])),
            ("disabled_rows", str(handle["disabled_rows"])),
        ]
        cursor.executemany(
            "INSERT OR REPLACE INTO materials_metadata (key, value) VALUES (?, ?)",
            metadata_pairs,
        )
        if "schema_version" in handle:
            if handle["schema_version"] is None:
                cursor.execute("DELETE FROM materials_metadata WHERE key='schema_version'")
            else:
                cursor.execute("INSERT OR REPLACE INTO materials_metadata VALUES ('schema_version', ?)",
                               (handle["schema_version"],))

        if has_live and not retain_old_generation:
            cursor.execute("DROP TABLE IF EXISTS materials_old")
        if has_live_fts and not retain_old_generation:
            cursor.execute("DROP TABLE IF EXISTS materials_fts_old")

        _set_db_revision(conn, handle["db_revision"])
        if retain_old_generation:
            conn.commit()  # The server publishes the matching staged cache under the same read barrier.
        else:
            from app.vector_index import index_manager
            index_manager.commit_catalog_snapshot(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        _cleanup_staged_tables()
        raise
    else:
        try:
            conn.close()
        except Exception:
            pass

    return get_db_metadata()


@catalog_write
def commit_staged_materials_import(
    handle: Dict[str, Any],
    cancel_event: Optional[threading.Event] = None,
    retain_old_generation: bool = False,
) -> Dict[str, Any]:
    """Commit a stage_only replace/append import: exclusive atomic swap + same-transaction metadata."""
    from app.excel_boundary import ParseCancelledError

    _assert_db_writable()
    if not isinstance(handle, dict) or not handle.get("stage_only"):
        raise ValueError("无效的暂存导入句柄：commit_staged_materials_import 需要 stage_only 句柄")
    if cancel_event is not None and cancel_event.is_set():
        _cleanup_staged_tables()
        raise ParseCancelledError("物料库导入在原子交换前被取消，已清理暂存表并保留原库")
    return _swap_materials_tables_exclusive(
        handle,
        cancel_event=cancel_event,
        retain_old_generation=retain_old_generation,
    )


def drop_retained_materials_old_generation() -> None:
    """Discard the materials_old/materials_fts_old generation retained by a forward swap
    run with retain_old_generation=True, once every publication layer has succeeded."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("DROP TABLE IF EXISTS materials_fts_old")
        cur.execute("DROP TABLE IF EXISTS materials_old")
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


@catalog_read
def reverse_retained_materials_swap(previous_metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Reverse a committed forward swap under one BEGIN EXCLUSIVE transaction.

    The just-published materials/materials_fts tables are dropped and the retained
    materials_old/materials_fts_old tables are renamed back with production indexes
    recreated; the five metadata keys captured before the forward swap are restored in
    the same transaction. If no retained generation exists (first-ever import with no
    prior live table), the failed new generation is removed so no mixed state survives.
    """
    conn = sqlite3.connect(DB_PATH, isolation_level="EXCLUSIVE")
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN EXCLUSIVE TRANSACTION")
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='materials_old'"
        )
        has_retained = cursor.fetchone() is not None
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='materials_fts_old'"
        )
        has_retained_fts = cursor.fetchone() is not None

        cursor.execute("DROP INDEX IF EXISTS idx_materials_code")
        cursor.execute("DROP INDEX IF EXISTS idx_materials_category")
        if has_retained:
            cursor.execute("DROP TABLE IF EXISTS materials")
            cursor.execute("ALTER TABLE materials_old RENAME TO materials")
            if has_retained_fts:
                cursor.execute("DROP TABLE IF EXISTS materials_fts")
                cursor.execute("ALTER TABLE materials_fts_old RENAME TO materials_fts")
            else:
                # The pre-state had no FTS table: remove the new generation's FTS too.
                cursor.execute("DROP TABLE IF EXISTS materials_fts")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_materials_code ON materials(code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_materials_category ON materials(category)")
        else:
            cursor.execute("DROP TABLE IF EXISTS materials_fts")
            cursor.execute("DROP TABLE IF EXISTS materials")

        cursor.execute(
            "CREATE TABLE IF NOT EXISTS materials_metadata (key TEXT PRIMARY KEY, value TEXT)"
        )
        metadata_pairs = [
            ("last_upload_date", str(previous_metadata.get("last_upload_date", "未知"))),
            ("filename", str(previous_metadata.get("filename", "未知"))),
            ("total_rows", str(previous_metadata.get("total_rows", 0))),
            ("imported_rows", str(previous_metadata.get("imported_rows", 0))),
            ("disabled_rows", str(previous_metadata.get("disabled_rows", 0))),
        ]
        cursor.executemany(
            "INSERT OR REPLACE INTO materials_metadata (key, value) VALUES (?, ?)",
            metadata_pairs,
        )
        _set_db_revision(conn, previous_metadata.get("db_revision", 0))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        raise
    else:
        try:
            conn.close()
        except Exception:
            pass
    return get_db_metadata()


@catalog_write
def import_materials_from_excel(
    xlsx_path: str, 
    mode: str = "replace", 
    filename: str = "materials.xlsx", 
    task_id: Optional[str] = None,
    disable_mode: str = "on_expire",
    cancel_event: Optional[threading.Event] = None,
    stage_only: bool = False,
) -> Any:
    """
    Import materials from Excel file into SQLite database.
    mode: "replace" (full replacement) or "append" (incremental add).
    disable_mode: "on_expire" (only disable if stop_date <= today or no date) or "immediate" (disable all marked).
    stage_only: populate the *_stage tables (replace: the batch only; append: the whole
                live generation plus the batch) and return a handle without swapping the
                live tables. The caller commits the swap via
                commit_staged_materials_import() after downstream vector/cache staging
                succeeds, so failures there leave the full database pre-state intact.
                When False, the atomic swap self-commits before the function returns.
    """
    import json
    import datetime
    from app.excel_boundary import (
        ExcelBoundaryError,
        ParseCancelledError,
        load_validated_dataframe_from_excel,
        validate_extension,
    )

    _assert_db_writable()
    
    # Critical Production Protection Guardrail
    is_testing = "PYTEST_CURRENT_TEST" in os.environ or os.getenv("TESTING") == "1"
    prod_db_canonical = os.path.abspath(os.path.join(os.path.dirname(__file__), "materials.db"))
    current_db_canonical = os.path.abspath(DB_PATH)
    if is_testing and mode == "replace" and current_db_canonical == prod_db_canonical:
        raise RuntimeError(
            "CRITICAL SAFETY GUARD: Refusing to execute import_materials_from_excel(mode='replace') "
            f"on production database ({prod_db_canonical}) during automated testing! "
            "Tests MUST use an isolated temporary database via DB_PATH monkeypatching or MATERIALS_DB_PATH."
        )

    if not os.path.exists(xlsx_path):
        raise FileNotFoundError(f"Excel file not found: {xlsx_path}")

    if filename and filename != "materials.xlsx":
        validate_extension(filename, frozenset({".xlsx", ".xls"}), context_label="物料库导入")

    df = load_validated_dataframe_from_excel(
        xlsx_path,
        header_row_index=1,
        allowed_exts=frozenset({".xlsx", ".xls"}),
        original_filename=xlsx_path,
        cancel_event=cancel_event,
        context_label="物料库导入",
    )
    
    # Map columns
    df = df.rename(columns={
        "物料分类": "category",
        "物料编码": "code",
        "物料名称": "name",
        "规格/型号": "specification",
        "计量单位": "unit",
        "常用品牌": "brand",
        "描述": "description"
    })

    if "code" not in df.columns or "name" not in df.columns:
        raise ExcelBoundaryError(
            "物料库表格缺少必填列（第2行表头需包含“物料编码”和“物料名称”）",
            status_code=400,
            code="missing_material_columns",
        )
    
    required_cols = ["code", "category", "name", "specification", "unit", "brand", "description"]
    for col in required_cols:
        if col not in df.columns:
            df[col] = ""
            
    df_cleaned = df[required_cols].copy()
    for col in required_cols:
        df_cleaned[col] = _clean_excel_str_col(df_cleaned[col])
    df_cleaned = df_cleaned[df_cleaned["code"] != ""].copy()

    if len(df_cleaned) == 0:
        raise ExcelBoundaryError(
            "物料库表格不包含有效物料数据行（有效物料编码为空）",
            status_code=400,
            code="empty_material_rows",
        )
    
    # Date-aware full-field disabled scan across: code, name, category, specification, description
    today = datetime.date.today()

    def check_row_disabled(row: pd.Series) -> Tuple[bool, str, bool, str]:
        fields = [
            ("code", str(row.get("code", ""))),
            ("name", str(row.get("name", ""))),
            ("category", str(row.get("category", ""))),
            ("specification", str(row.get("specification", ""))),
            ("description", str(row.get("description", "")))
        ]
        for field_name, val in fields:
            val_clean = val.strip()
            has_kw, stop_date, snippet = parse_disable_annotation(val_clean)
            if has_kw:
                stop_date_str = stop_date.strftime("%Y-%m-%d") if stop_date else ""
                if disable_mode == "immediate":
                    return True, stop_date_str, True, f"字段[{field_name}]命中禁用标记({snippet})[立即生效模式]"
                
                # on_expire mode:
                if stop_date is None:
                    return True, "", True, f"字段[{field_name}]命中无日期禁用标记({snippet})[已即刻失效]"
                elif stop_date <= today:
                    return True, stop_date_str, True, f"字段[{field_name}]停用日期({stop_date_str})已到期(<= {today})"
                else:
                    return False, stop_date_str, False, f"字段[{field_name}]停用日期({stop_date_str})未到期(> {today})[正常在用]"
        return False, "", False, ""

    disabled_results = df_cleaned.apply(check_row_disabled, axis=1)
    df_cleaned["is_disabled"] = [res[0] for res in disabled_results]
    df_cleaned["stop_date"] = [res[1] for res in disabled_results]
    df_cleaned["is_expired"] = [res[2] for res in disabled_results]
    df_cleaned["disabled_reason"] = [res[3] for res in disabled_results]

    df_disabled = df_cleaned[df_cleaned["is_disabled"]]
    df_imported = df_cleaned[~df_cleaned["is_disabled"]]
    df_audit = df_cleaned[df_cleaned["is_disabled"] | (df_cleaned["stop_date"] != "") | (df_cleaned["disabled_reason"] != "")]

    total_rows = len(df_cleaned)
    disabled_rows = len(df_disabled)

    # Export disabled rows and annotated materials audit CSV for business verification
    try:
        # Test isolation: OUTPUT_DIR redirects generated audit files away from business data/
        data_dir = os.getenv(
            "OUTPUT_DIR",
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"),
        )
        os.makedirs(data_dir, exist_ok=True)
        dt_str = datetime.datetime.now().strftime("%Y%m%d")
        audit_path = os.path.join(data_dir, f"audit_disabled_materials_{dt_str}.csv")
        df_audit.to_csv(audit_path, index=False, encoding="utf-8-sig")
    except Exception as e:
        print(f"Failed to export disabled audit CSV: {e}")

    # Deduplicate duplicate codes in imported rows (preserve most complete record)
    df_imported = df_imported.copy()
    completeness = df_imported.apply(lambda r: len(f"{r['name']}{r['specification']}{r['description']}"), axis=1)
    df_imported["_completeness"] = completeness
    df_imported = df_imported.sort_values(by=["_completeness"], ascending=False).drop_duplicates(subset=["code"], keep="first")
    df_imported = df_imported.drop(columns=["_completeness", "is_disabled", "stop_date", "is_expired", "disabled_reason"])

    # Work with deduplicated imported rows
    df_cleaned = df_imported
    
    # Create search tokens and pre-parse parameters
    from app.preprocessor import extract_params, extract_device_name
    from app.domain_router import route_domain
    from app.normalizer import clean_text_standard
    from app.domain_extractors import domain_schema
    from app.llm_client import llm_client
    
    llm_enabled = llm_client.is_enabled()
    pending_tasks = []
    
    search_tokens_list = []
    parsed_params_list = []
    
    for idx, (_, row) in enumerate(df_cleaned.iterrows()):
        if cancel_event is not None and cancel_event.is_set():
            raise ParseCancelledError("物料库导入已取消或超时，已放弃解析")
        tokens = f"{row['name']} {row['specification']} {row['category']}".lower()
        search_tokens_list.append(tokens)
        
        # Pre-parse params and serialize to JSON
        full_spec = f"{row['name']} {row['specification']} {row['description']}".strip()
        parsed = extract_params(full_spec, row['category'])
        parsed_params_list.append(json.dumps(parsed, ensure_ascii=False))
        
        if llm_enabled:
            # Check if any must keys are missing
            from app.rule_context import RuleContext
            name_extracted = extract_device_name(full_spec, row['category'])
            text_clean = clean_text_standard(
                full_spec,
                rule_context=RuleContext.from_fields(
                    material_category=row['category'],
                    name=row['name'] or name_extracted,
                    raw_name=row['name'],
                    stage="cleaning",
                ),
            )
            domain = route_domain(row['category'], name_extracted, text_clean, row['category'])
            domain_config = domain_schema.get(domain, {})
            must_keys = [field["key"] for field in domain_config.get("must", [])]
            
            is_missing_must = False
            for mk in must_keys:
                if mk not in parsed.get("attrs", {}):
                    is_missing_must = True
                    break
            if is_missing_must:
                pending_tasks.append((row['code'], full_spec, row['category']))
                
        if task_id and idx % 100 == 0:
            try:
                from app.server import upload_tasks
                if task_id in upload_tasks:
                    prog = min(90, int(10 + (idx / total_rows) * 80))
                    upload_tasks[task_id]["structuring_progress"] = prog
                    upload_tasks[task_id]["current_step"] = f"正在解析属性: {row['name'][:15]} ({idx}/{total_rows})"
            except Exception as e:
                print(f"Error updating task progress in import_materials_from_excel: {e}")
        
    df_cleaned = df_cleaned.copy()  # Avoid SettingWithCopyWarning
    df_cleaned["search_tokens"] = search_tokens_list
    df_cleaned["parsed_params"] = parsed_params_list
    
    if cancel_event is not None and cancel_event.is_set():
        raise ParseCancelledError("物料库导入已在落库前超时取消")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("BEGIN IMMEDIATE")
        base_revision = read_db_revision(conn)
        # R03 A-M5-01: replace and append stage whole generations. Until the
        # atomic swap, both live tables remain readable, even on cancellation.
        _prepare_catalog_stage(conn)
        if mode == "append" and _has_table(conn, "materials"):
            columns = ", ".join(_MATERIAL_COLUMNS)
            cursor.execute(f"INSERT INTO {STAGE_MATERIALS_TABLE} ({columns}) SELECT {columns} FROM materials")

        # Build FTS from the final, deduplicated material rows, including old
        # rows carried into an append. FTS5 does not enforce uniqueness on code.
        records = df_cleaned[list(_MATERIAL_COLUMNS[:-1])].values.tolist()
        columns = ", ".join(_MATERIAL_COLUMNS)
        cursor.executemany(
            f"INSERT OR REPLACE INTO {STAGE_MATERIALS_TABLE} ({columns}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ((*record, 2) for record in records),
        )
        _populate_staged_fts(conn, cancel_event)
        staged_row_count = cursor.execute(f"SELECT COUNT(*) FROM {STAGE_MATERIALS_TABLE}").fetchone()[0]

        if cancel_event is not None and cancel_event.is_set():
            raise ParseCancelledError("物料库导入在数据库提交前超时取消")

        conn.commit()
        try:
            conn.execute("PRAGMA optimize;")
        except Exception:
            pass
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        if mode in ("replace", "append"):
            # Defensive zero-residue guarantee on top of transaction rollback. Both
            # modes now populate the *_stage tables; the live tables were never touched.
            try:
                conn.execute(f"DROP TABLE IF EXISTS {STAGE_FTS_TABLE}")
                conn.execute(f"DROP TABLE IF EXISTS {STAGE_MATERIALS_TABLE}")
                conn.commit()
            except Exception:
                pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if mode == "replace":
        # Replace publishes exactly this batch's counts.
        stage_handle: Dict[str, Any] = {
            "stage_only": True,
            "filename": filename,
            "total_rows": total_rows,
            "imported_rows": staged_row_count,
            "disabled_rows": disabled_rows,
        }
    else:
        # Append: the staged generation already holds every live row plus this batch,
        # so the swap's same-transaction metadata must carry the merged counts. Read
        # here while the live tables still hold the old generation (pre-swap).
        old_meta = get_db_metadata()
        stage_handle = {
            "stage_only": True,
            "filename": filename,
            "total_rows": old_meta.get("total_rows", 0) + total_rows,
            "imported_rows": staged_row_count,
            "disabled_rows": old_meta.get("disabled_rows", 0) + disabled_rows,
        }
    stage_handle.update(base_revision=base_revision, db_revision=_next_db_revision(base_revision))
    if stage_only:
        # Caller publishes via commit_staged_materials_import() once downstream
        # vector/cache staging has succeeded.
        return stage_handle
    # Self-contained default path: stage then immediately run the atomic swap.
    meta = commit_staged_materials_import(stage_handle, cancel_event=cancel_event)

    if llm_enabled and pending_tasks:
        process_llm_extraction_async(pending_tasks)

    # Return fresh metadata
    return meta

def _prepare_catalog_stage(conn):
    conn.execute(f"DROP TABLE IF EXISTS {STAGE_FTS_TABLE}")
    conn.execute(f"DROP TABLE IF EXISTS {STAGE_MATERIALS_TABLE}")
    _create_materials_table(conn, STAGE_MATERIALS_TABLE)
    _create_fts_table(conn, STAGE_FTS_TABLE)


def _populate_staged_fts(conn, cancel_event=None):
    if _has_table(conn, STAGE_FTS_TABLE):
        rows = conn.execute(f"SELECT {', '.join(_FTS_COLUMNS)} FROM {STAGE_MATERIALS_TABLE}")
        _write_fts_records(conn, STAGE_FTS_TABLE, rows, cancel_event)


@catalog_write
def restore_materials_catalog(source_db) -> Dict[str, Any]:
    """Restore the catalog from a read-only SQLite backup, preserving other live tables."""
    _assert_db_writable()
    source = Path(source_db).resolve(strict=True)
    if Path(DB_PATH).is_file() and source.samefile(DB_PATH):
        raise ValueError("恢复源必须是独立的 SQLite 备份")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as backup:
        backup.execute("BEGIN")
        source_columns = {row[1] for row in backup.execute("PRAGMA table_info(materials)")}
        missing = set(_MATERIAL_COLUMNS[:-1]) - source_columns
        if missing:
            raise ValueError(f"恢复源物料表缺少字段: {sorted(missing)}")
        fields = ", ".join(col if col in source_columns else "0" for col in _MATERIAL_COLUMNS)
        backup_revision = read_db_revision(backup)
        source_schema = None
        if _has_table(backup, "materials_metadata"):
            row = backup.execute("SELECT value FROM materials_metadata WHERE key='schema_version'").fetchone()
            source_schema = row[0] if row else None
        with closing(get_db_connection()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            base_revision = read_db_revision(conn)
            _prepare_catalog_stage(conn)
            conn.executemany(f"""
                INSERT INTO {STAGE_MATERIALS_TABLE} ({', '.join(_MATERIAL_COLUMNS)})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, backup.execute(f"SELECT {fields} FROM materials"))
            _populate_staged_fts(conn)
            row_count = conn.execute(f"SELECT COUNT(*) FROM {STAGE_MATERIALS_TABLE}").fetchone()[0]
    return commit_staged_materials_import({
        "stage_only": True, "filename": source.name, "total_rows": row_count,
        "imported_rows": row_count, "disabled_rows": 0, "base_revision": base_revision,
        "db_revision": _next_db_revision(max(base_revision, backup_revision)),
        "schema_version": source_schema,
    })


def reparse_materials_db_asymmetric() -> Dict[str, Any]:
    """Reparse the full catalog and publish DB, FTS and cache as one logical generation."""
    return _reparse_materials(full=True)


@catalog_read
def get_db_schema_stats() -> Dict[str, Any]:
    """
    V017-M1 / M1-C04：填充率与正确率分开报告、去虚高。

    关键变化（相对旧版）：
    1. 无 Must 领域（如 generic）的 must_fill_rate 不再记 1.0 抬高总体填充率，
       而是单独分列 must_fill_rate = None，并独立统计行数/占比。
    2. 有 Must 领域报告真实 Must 填充率；另附 Should/Flags 填充率与白名单过滤计数。
    3. 总体填充率仅对有 Must 领域计算（分母为有 Must 领域的行数），杜绝 generic 虚高。
    4. 路由分布与字段抽取质量（Precision/Recall/F1）独立于填充率；无金标时如实标注
       "未评估"（None），不伪造正确率。
    """
    import json
    from app.domain_router import route_domain
    from app.domain_extractors import domain_schema
    from app.typed_fields import partition_whitelisted_attrs, is_whitelisted_key

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT category, name, specification, parsed_params FROM materials")
        rows = cursor.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    total_rows = len(rows)
    if total_rows == 0:
        return {"total_rows": 0, "domains": {}, "overall_structuring_rate": 0.0}

    domain_counts: Dict[str, int] = {}
    # 每个域累积：有 must 域的真实填充率总和（行级求和），无 must 域单列行数
    domain_must_fill_sum: Dict[str, float] = {}
    domain_has_must: Dict[str, bool] = {}
    domain_should_fill_sum: Dict[str, float] = {}
    domain_flags_fill_sum: Dict[str, float] = {}
    domain_whitelist_filtered: Dict[str, int] = {}
    domain_rows: Dict[str, List[Dict[str, Any]]] = {}

    for row in rows:
        cat = row["category"]
        name = row["name"]
        spec = row["specification"]
        parsed_str = row["parsed_params"]

        domain = route_domain(cat, name, spec)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        domain_rows.setdefault(domain, []).append(row)

        parsed = {}
        if parsed_str:
            try:
                parsed = json.loads(parsed_str)
            except Exception:
                pass

        attrs = parsed.get("attrs", {}) if isinstance(parsed, dict) else {}
        domain_config = domain_schema.get(domain, {}) if isinstance(domain_schema, dict) else {}
        must_keys = [field["key"] for field in domain_config.get("must", []) or []]
        should_keys = [field["key"] for field in domain_config.get("should", []) or []]
        flags_keys = list(domain_config.get("flags", []) or [])

        # 白名单过滤计数：attrs 中未命中受控白名单的键数 + 顶层 unwhitelisted_attrs 键数
        # V017-M1 / M1-C04 修复 A-M1-04：旧逻辑仅扫 attrs，漏掉 parsed["unwhitelisted_attrs"]，
        # 导致 v3 库恒为 0；此处合并统计二者。
        _wl, _unwl = partition_whitelisted_attrs(attrs)
        _unwl_count = len(_unwl)
        _unwl_top = parsed.get("unwhitelisted_attrs") if isinstance(parsed, dict) else None
        if isinstance(_unwl_top, dict):
            _unwl_count += len(_unwl_top)
        domain_whitelist_filtered[domain] = domain_whitelist_filtered.get(domain, 0) + _unwl_count

        if must_keys:
            domain_has_must[domain] = True
            extracted_must = sum(1 for k in must_keys if k in attrs and attrs[k] is not None and attrs[k] != "")
            domain_must_fill_sum[domain] = domain_must_fill_sum.get(domain, 0.0) + (extracted_must / len(must_keys))
        else:
            # 无 Must 领域：不记填充率（避免 1.0 虚高），仅计行数
            domain_has_must[domain] = False
            domain_must_fill_sum[domain] = domain_must_fill_sum.get(domain, 0.0)

        # Should 填充率（有 should 字段时）
        if should_keys:
            extracted_should = sum(1 for k in should_keys if k in attrs and attrs[k] is not None and attrs[k] != "")
            domain_should_fill_sum[domain] = domain_should_fill_sum.get(domain, 0.0) + (extracted_should / len(should_keys))

        # Flags 填充率（有 flags 时，按"至少命中一个 flag"计）
        if flags_keys:
            hit_flag = any(attrs.get(f) is True for f in flags_keys)
            domain_flags_fill_sum[domain] = domain_flags_fill_sum.get(domain, 0.0) + (1.0 if hit_flag else 0.0)

    # 汇总
    domain_stats = {}
    total_must_fill_sum = 0.0
    has_must_rows_total = 0
    no_must_domains = {}

    for d in domain_counts:
        count = domain_counts[d]
        has_must = domain_has_must.get(d, False)
        if has_must:
            fill_sum = domain_must_fill_sum.get(d, 0.0)
            avg_rate = fill_sum / count if count > 0 else 0.0
            total_must_fill_sum += fill_sum
            has_must_rows_total += count
            must_fill_rate = round(avg_rate * 100, 2)
        else:
            must_fill_rate = None

        entry = {
            "count": count,
            "has_must": has_must,
            "must_fill_rate": must_fill_rate,
            "structuring_rate": must_fill_rate,  # 兼容旧字段；无 must 域为 None
        }
        if should_keys_present(d):
            entry["should_fill_rate"] = round((domain_should_fill_sum.get(d, 0.0) / count) * 100, 2) if count else None
        # V017-M1 / M1-C04 修复 A-M1-04：补返回 flags_fill_rate（此前已计算未写入 entry）
        if flags_keys_present(d):
            entry["flags_fill_rate"] = round((domain_flags_fill_sum.get(d, 0.0) / count) * 100, 2) if count else None
        entry["whitelist_filtered_count"] = domain_whitelist_filtered.get(d, 0)
        domain_stats[d] = entry

        if not has_must:
            no_must_domains[d] = {"count": count, "share_pct": round(count * 100.0 / total_rows, 2)}

    # 总体填充率：仅对有 must 域计算，分母为有 must 域行数（杜绝 generic 虚高）
    overall_structuring_rate = (
        (total_must_fill_sum / has_must_rows_total) * 100
        if has_must_rows_total > 0 else 0.0
    )

    return {
        "total_rows": total_rows,
        "has_must_rows": has_must_rows_total,
        "overall_structuring_rate": round(overall_structuring_rate, 2),
        "domains": domain_stats,
        "no_must_domains": no_must_domains,
        "routing_distribution": {d: c for d, c in sorted(domain_counts.items(), key=lambda x: -x[1])},
        # 正确率独立于填充率；无金标/未评估时如实为 None（M1-C05 用结构化评测集实测）
        "extraction_quality": {
            "routing_accuracy": None,
            "field_precision": None,
            "field_recall": None,
            "field_f1": None,
            "note": "未评估：需结构化抽取金标（见 M1-C05 structured_extraction_eval_v1.json）",
        },
    }


def should_keys_present(domain: str) -> bool:
    """判断领域是否配置了 should 字段（用于统计报告）。"""
    from app.domain_extractors import domain_schema
    if not isinstance(domain_schema, dict):
        return False
    cfg = domain_schema.get(domain, {})
    return isinstance(cfg, dict) and bool(cfg.get("should"))


def flags_keys_present(domain: str) -> bool:
    """判断领域是否配置了 flags 字段（用于统计报告）。"""
    from app.domain_extractors import domain_schema
    if not isinstance(domain_schema, dict):
        return False
    cfg = domain_schema.get(domain, {})
    return isinstance(cfg, dict) and bool(cfg.get("flags"))

def reparse_materials_db_partial(category: str = None, codes: List[str] = None) -> Dict[str, Any]:
    """
    Partially reparse materials in the active database.
    Updates parsed_params and search_tokens for selected categories/codes,
    and sets parsed_schema_version to PARSED_SCHEMA_VERSION (V017-M1 = 3).
    """
    return _reparse_materials(category=category, codes=codes)


@catalog_write
def _reparse_materials(category=None, codes=None, full=False):
    import json
    from app.preprocessor import extract_params, extract_device_name
    from app.llm_client import llm_client
    from app.domain_router import route_domain
    from app.normalizer import clean_text_standard
    from app.domain_extractors import domain_schema

    _assert_db_writable()
    query = "SELECT code, category, name, specification, unit, brand, description FROM materials"
    params = []
    
    if codes:
        placeholders = ",".join(["?"] * len(codes))
        query += f" WHERE code IN ({placeholders})"
        params = list(codes)
    elif category:
        query += " WHERE category = ?"
        params = [category]
    elif not full:
        query += f" WHERE parsed_schema_version < {PARSED_SCHEMA_VERSION}"
        
    with closing(get_db_connection()) as conn:
        if not _has_table(conn, "materials"):
            return {"status": "error", "message": "materials table does not exist"}
        base_revision = read_db_revision(conn)
        rows = conn.execute(query, params).fetchall()
    if not rows:
        return {"status": "success", "reparsed_rows": 0}

    llm_enabled = llm_client.is_enabled()
    pending_tasks = []
    
    updated_records = []
    for row in rows:
        checkpoint()
        code, cat, name, spec, unit, brand, desc = row
        tokens = f"{name} {spec} {cat}".lower()
        full_spec = f"{spec} {desc}".strip()
        parsed = extract_params(full_spec, cat)
        parsed_params_str = json.dumps(parsed, ensure_ascii=False)
        updated_records.append([tokens, parsed_params_str, PARSED_SCHEMA_VERSION, code])
        
        if llm_enabled:
            from app.rule_context import RuleContext
            name_extracted = extract_device_name(full_spec, cat)
            text_clean = clean_text_standard(
                full_spec,
                rule_context=RuleContext.from_fields(
                    material_category=cat,
                    name=name or name_extracted,
                    raw_name=name,
                    stage="cleaning",
                ),
            )
            domain = route_domain(cat, name_extracted, text_clean, cat)
            domain_config = domain_schema.get(domain, {})
            must_keys = [field["key"] for field in domain_config.get("must", [])]
            
            is_missing_must = False
            for mk in must_keys:
                if mk not in parsed.get("attrs", {}):
                    is_missing_must = True
                    break
            if is_missing_must:
                pending_tasks.append((code, full_spec, cat))
        
    with closing(get_db_connection()) as conn, conn:
        checkpoint()
        conn.execute("BEGIN IMMEDIATE")
        if read_db_revision(conn) != base_revision:
            raise RuntimeError("物料库版本已变化，请重新解析")
        conn.executemany("""
            UPDATE materials SET search_tokens=?, parsed_params=?, parsed_schema_version=?
            WHERE code=?
        """, updated_records)
        _refresh_fts(conn, None if full else [row["code"] for row in rows])
        if full:
            _set_db_revision(conn, base_revision)
            conn.execute("INSERT OR REPLACE INTO materials_metadata VALUES ('schema_version', ?)",
                         (str(PARSED_SCHEMA_VERSION),))
        checkpoint()
        _commit_materials_write(conn)

    if llm_enabled and pending_tasks:
        process_llm_extraction_async(pending_tasks)
        
    return {"status": "success", "reparsed_rows": len(rows)}


@catalog_write
def update_material_params(updates) -> int:
    """Apply parsed parameter updates with an optimistic per-row check in one revision."""
    _assert_db_writable()
    with closing(get_db_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        updated_codes = []
        for code, previous, parsed, previous_spec in updates:
            result = conn.execute("""
                UPDATE materials SET parsed_params=? WHERE code=? AND parsed_params IS ?
                AND (COALESCE(specification, '') || ' ' || COALESCE(description, '')) IS ?
            """, (parsed, code, previous, previous_spec))
            if result.rowcount:
                updated_codes.append(code)
        if updated_codes:
            _refresh_fts(conn, list(dict.fromkeys(updated_codes)))
            _commit_materials_write(conn)
        return len(updated_codes)


def process_llm_extraction_async(pending_tasks: List[Tuple[str, str, str]]):
    """
    Asynchronously calls LLM to extract parameters for the given list of (code, full_spec, category).
    Then updates the database.
    """
    import logging

    logger_db = logging.getLogger("db_manager_async")
    tasks_to_run = pending_tasks[:50]
    try:
        job = supplement_jobs.reserve(600)
    except JobRejected:
        logger_db.warning("Async LLM extraction skipped because its bounded queue is full.")
        return
    
    def run():
        from app.llm_client import llm_client
        from app.domain_router import route_domain
        from app.preprocessor import extract_device_name
        from app.normalizer import clean_text_standard
        from app.domain_extractors import domain_schema
        import json
        checkpoint()
        if is_db_write_blocked():
            logger_db.warning("DB recovery write block is active. Skipping async LLM extraction.")
            return
        logger_db.info(f"Starting async LLM extraction for {len(tasks_to_run)} items (truncated from {len(pending_tasks)}).")
        
        # Connect to DB
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            for code, full_spec, category in tasks_to_run:
                # Check circuit breaker
                if llm_client.check_circuit_breaker():
                    logger_db.warning("Circuit breaker is open. Aborting remaining async extraction.")
                    break
                
                try:
                    # Add delay between calls to respect rate limit
                    job.cancelled.wait(1.0)
                    checkpoint()
                
                    # Route domain
                    from app.rule_context import RuleContext
                    name_extracted = extract_device_name(full_spec, category)
                    text_clean = clean_text_standard(
                        full_spec,
                        rule_context=RuleContext.from_fields(
                            material_category=category,
                            name=name_extracted,
                            stage="cleaning",
                        ),
                    )
                    domain = route_domain(category, name_extracted, text_clean, category)
                
                    domain_config = domain_schema.get(domain, {})
                    must_keys = [field["key"] for field in domain_config.get("must", [])]
                    should_keys = [field["key"] for field in domain_config.get("should", [])]
                    flags = domain_config.get("flags", [])
                
                    # Fetch current parsed_params to merge with
                    cursor.execute("""
                        SELECT parsed_params, COALESCE(specification, '') || ' ' || COALESCE(description, '')
                        FROM materials WHERE code = ?
                    """, (code,))
                    row = cursor.fetchone()
                    if not row or row[1].strip() != full_spec:
                        continue
                    parsed = json.loads(row[0] or '{"attrs": {}}')
                    if not isinstance(parsed.get("attrs"), dict):
                        parsed["attrs"] = {}
                
                    llm_parsed = llm_client.extract_params_by_llm(full_spec, domain, must_keys, should_keys, flags)
                    checkpoint()
                    if llm_parsed:
                        # Update parsed dict
                        if llm_parsed.get("model"):
                            parsed["model"] = llm_parsed["model"]
                        for k, v in llm_parsed.get("attrs", {}).items():
                            parsed["attrs"][k] = v
                        
                        # Also double-write keys for frontend compatibility
                        if "机外余压" in parsed["attrs"]:
                            parsed["attrs"]["余压"] = parsed["attrs"]["机外余压"]
                        if "管径" in parsed["attrs"]:
                            parsed["attrs"]["口径"] = parsed["attrs"]["管径"]
                        if "过滤等级" in parsed["attrs"]:
                            parsed["attrs"]["过滤段"] = [parsed["attrs"]["过滤等级"]] if isinstance(parsed["attrs"]["过滤等级"], str) else parsed["attrs"]["过滤等级"]
                    
                        # Update DB
                        update_material_params([(code, row[0], json.dumps(parsed, ensure_ascii=False), row[1])])
                except JobStopped:
                    raise
                except Exception as e:
                    logger_db.error(f"Failed to process async LLM extraction for code {code}: {e}")
                
        finally:
            conn.close()
        logger_db.info("Async LLM extraction completed.")
        
    def work():
        try:
            run()
        except JobStopped as exc:
            logger_db.info("Async LLM extraction stopped: %s", exc)
        finally:
            job.release()

    try:
        job.submit(work)
    except BaseException:
        job.release()
        raise
