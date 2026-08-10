# -*- coding: utf-8 -*-
import os
import re
import sqlite3
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from functools import lru_cache

DB_PATH = os.path.join(os.path.dirname(__file__), "materials.db")

# Prevent OpenBLAS/PyTorch thread conflicts or multi-threaded connection locks
_HAS_FTS_CACHE: Optional[bool] = None

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn

def init_db(xlsx_path=None, force_reload=False):
    """
    Initialize SQLite database.
    If database does not exist or force_reload is True, import data from xlsx_path.
    """
    import json
    db_exists = os.path.exists(DB_PATH)
    
    if db_exists and not force_reload:
        conn = get_db_connection()
        cursor = conn.cursor()
        # 1. Self-healing/Migration: check if parsed_schema_version column exists
        try:
            cursor.execute("SELECT parsed_schema_version FROM materials LIMIT 1")
        except sqlite3.OperationalError:
            print("Migrating database: adding parsed_schema_version column...")
            try:
                cursor.execute("ALTER TABLE materials ADD COLUMN parsed_schema_version INTEGER DEFAULT 0")
                conn.commit()
            except Exception as e:
                print(f"Migration error: {e}")
        
        # 2. Check for parsed_params column and row count
        try:
            cursor.execute("SELECT parsed_params FROM materials LIMIT 1")
            cursor.execute("SELECT COUNT(*) FROM materials")
            row_count = cursor.fetchone()[0]
            if row_count == 0:
                print("Database is empty. Triggering auto reload...")
                force_reload = True
        except sqlite3.OperationalError:
            print("Database schema mismatch (missing parsed_params). Triggering auto reload...")
            force_reload = True
        finally:
            conn.close()
            
    if not db_exists or force_reload:
        print("Initializing database...")
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create materials table
        cursor.execute("DROP TABLE IF EXISTS materials")
        cursor.execute("""
            CREATE TABLE materials (
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
        
        # Create FTS5 virtual table for full-text search
        cursor.execute("DROP TABLE IF EXISTS materials_fts")
        try:
            cursor.execute("""
                CREATE VIRTUAL TABLE materials_fts USING fts5(
                    code, category, name, specification, brand, description, tokenize="unicode61"
                )
            """)
            has_fts = True
        except sqlite3.OperationalError:
            print("Warning: FTS5 not supported. Fallback to standard LIKE queries.")
            has_fts = False
            
        conn.commit()
        conn.close()
        
        if xlsx_path and os.path.exists(xlsx_path):
            import_materials_from_excel(xlsx_path, mode="replace", filename=os.path.basename(xlsx_path))
        else:
            print("No source Excel file loaded. Database is empty.")
        
        # Build vector index cache if vector index exists
        try:
            from app.vector_index import index_manager
            index_manager.check_db_timestamp_and_build(DB_PATH, force=True)
        except Exception as e:
            print(f"Error rebuilding vector index during init_db: {e}")

def get_all_materials(limit=1000):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM materials LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@lru_cache(maxsize=50000)
def query_by_code(code: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM materials WHERE code = ?", (code.strip(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

@lru_cache(maxsize=10000)
def _search_materials_keyword_cached(keyword: str, fetch_limit: int = 150) -> List[Dict[str, Any]]:
    """
    Core cached search routine with standard fetch_limit=150 to maximize LRU cache hit rates.
    """
    if not keyword:
        return []
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Split keyword by space, comma, or semicolon delimiters
    raw_words = [w.strip() for w in re.split(r'[;；,，\s]+', keyword) if w.strip()]
    if not raw_words:
        conn.close()
        return []
        
    global _HAS_FTS_CACHE
    if _HAS_FTS_CACHE is None:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='materials_fts'")
        _HAS_FTS_CACHE = (cursor.fetchone() is not None)
    has_fts = _HAS_FTS_CACHE
    
    if has_fts:
        from app.preprocessor import fts_tokenize
        fts_clauses = []
        synonym_map = {
            "网线": ["网线", "双绞线", "对线", "网络线"],
            "双绞线": ["网线", "双绞线", "对线", "网络线"],
            "配电箱": ["配电箱", "配电柜"],
            "配电柜": ["配电箱", "配电柜"],
            "开关": ["开关", "功能件", "面板"],
            "按钮": ["按钮", "呼叫", "开关"]
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
                conn.close()
                return [dict(r) for r in results]
            except sqlite3.OperationalError:
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
    conn.close()
    return [dict(r) for r in results]

def search_materials_keyword(keyword: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Public search entry point with unified LRU caching."""
    fetch_limit = max(150, limit)
    cached = _search_materials_keyword_cached(keyword, fetch_limit)
    return cached[:limit]

def get_db_metadata() -> Dict[str, Any]:
    """Retrieve database row counts and last upload timestamps."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Ensure metadata table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS materials_metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    
    # Get row count from materials table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='materials'")
    has_materials = cursor.fetchone() is not None
    row_count = 0
    if has_materials:
        cursor.execute("SELECT COUNT(*) FROM materials")
        row_count = cursor.fetchone()[0]
    
    # Get last upload date
    cursor.execute("SELECT value FROM materials_metadata WHERE key = 'last_upload_date'")
    row = cursor.fetchone()
    last_upload_date = row[0] if row else "未知"
    
    # Get filename
    cursor.execute("SELECT value FROM materials_metadata WHERE key = 'filename'")
    row = cursor.fetchone()
    filename = row[0] if row else "未知"

    # Get total_rows
    cursor.execute("SELECT value FROM materials_metadata WHERE key = 'total_rows'")
    row = cursor.fetchone()
    total_rows = int(row[0]) if row else row_count

    # Get imported_rows
    cursor.execute("SELECT value FROM materials_metadata WHERE key = 'imported_rows'")
    row = cursor.fetchone()
    imported_rows = int(row[0]) if row else row_count

    # Get disabled_rows
    cursor.execute("SELECT value FROM materials_metadata WHERE key = 'disabled_rows'")
    row = cursor.fetchone()
    disabled_rows = int(row[0]) if row else 0
    
    conn.close()
    return {
        "row_count": row_count,
        "last_upload_date": last_upload_date,
        "filename": filename,
        "total_rows": total_rows,
        "imported_rows": imported_rows,
        "disabled_rows": disabled_rows
    }

def update_db_metadata(total_rows: int, imported_rows: int, disabled_rows: int, filename: str):
    """Save upload logs and timestamp details to SQLite."""
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
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
    conn.close()

def import_materials_from_excel(xlsx_path: str, mode: str = "replace", filename: str = "materials.xlsx", task_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Import materials from Excel file into SQLite database.
    mode: "replace" (full replacement) or "append" (incremental add).
    """
    import json
    if not os.path.exists(xlsx_path):
        raise FileNotFoundError(f"Excel file not found: {xlsx_path}")
        
    df = pd.read_excel(xlsx_path, header=1)
    
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
    
    required_cols = ["code", "category", "name", "specification", "unit", "brand", "description"]
    for col in required_cols:
        if col not in df.columns:
            df[col] = ""
            
    df_cleaned = df[required_cols].dropna(subset=["code"])
    df_cleaned["code"] = df_cleaned["code"].astype(str).str.strip()
    df_cleaned["category"] = df_cleaned["category"].fillna("").astype(str).str.strip()
    df_cleaned["name"] = df_cleaned["name"].fillna("").astype(str).str.strip()
    df_cleaned["specification"] = df_cleaned["specification"].fillna("").astype(str).str.strip()
    df_cleaned["unit"] = df_cleaned["unit"].fillna("").astype(str).str.strip()
    df_cleaned["brand"] = df_cleaned["brand"].fillna("").astype(str).str.strip()
    df_cleaned["description"] = df_cleaned["description"].fillna("").astype(str).str.strip()
    
    # Filter out materials where code contains comments/annotations such as 禁用, 停用, 无效
    disabled_mask = df_cleaned["code"].apply(lambda x: any(kw in str(x) for kw in ["禁用", "停用", "无效"]))
    df_disabled = df_cleaned[disabled_mask]
    df_imported = df_cleaned[~disabled_mask]
    
    total_rows = len(df_cleaned)
    disabled_rows = len(df_disabled)
    imported_rows = len(df_imported)
    
    # Work with imported rows only
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
        tokens = f"{row['name']} {row['specification']} {row['category']}".lower()
        search_tokens_list.append(tokens)
        
        # Pre-parse params and serialize to JSON
        full_spec = f"{row['name']} {row['specification']} {row['description']}".strip()
        parsed = extract_params(full_spec, row['category'])
        parsed_params_list.append(json.dumps(parsed, ensure_ascii=False))
        
        if llm_enabled:
            # Check if any must keys are missing
            name_extracted = extract_device_name(full_spec, row['category'])
            text_clean = clean_text_standard(full_spec)
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
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check FTS support
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='materials_fts'")
    has_fts = cursor.fetchone() is not None
    
    if mode == "replace":
        # Truncate tables
        cursor.execute("DELETE FROM materials")
        if has_fts:
            cursor.execute("DELETE FROM materials_fts")
        conn.commit()
        
    # Write to SQL materials table
    records = df_cleaned[["code", "category", "name", "specification", "unit", "brand", "description", "search_tokens", "parsed_params"]].values.tolist()
    records = [r + [2] for r in records]
    cursor.executemany("""
        INSERT OR REPLACE INTO materials 
        (code, category, name, specification, unit, brand, description, search_tokens, parsed_params, parsed_schema_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    
    # Write to FTS virtual table
    if has_fts:
        from app.preprocessor import fts_tokenize
        fts_records = []
        for _, row in df_cleaned.iterrows():
            fts_records.append([
                row["code"],
                fts_tokenize(row["category"]),
                fts_tokenize(row["name"]),
                fts_tokenize(row["specification"]),
                fts_tokenize(row["brand"]),
                fts_tokenize(row["description"])
            ])
        cursor.executemany("""
            INSERT OR REPLACE INTO materials_fts 
            (code, category, name, specification, brand, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, fts_records)
        
    conn.commit()
    conn.close()
    
    # Compute aggregate stats
    old_meta = get_db_metadata()
    if mode == "append":
        new_total = old_meta.get("total_rows", 0) + total_rows
        new_imported = old_meta.get("imported_rows", 0) + imported_rows
        new_disabled = old_meta.get("disabled_rows", 0) + disabled_rows
    else:
        new_total = total_rows
        new_imported = imported_rows
        new_disabled = disabled_rows
        
    update_db_metadata(new_total, new_imported, new_disabled, filename)
    
    if llm_enabled and pending_tasks:
        process_llm_extraction_async(pending_tasks)
    
    # Return fresh metadata
    return get_db_metadata()

def reparse_materials_db_asymmetric() -> Dict[str, Any]:
    """
    Reparse all materials from existing database into materials_temp,
    then atomically swap tables under write lock to minimize downtime.
    """
    import json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Check if materials table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='materials'")
    if not cursor.fetchone():
        conn.close()
        return {"status": "error", "message": "materials table does not exist"}
        
    # 2. Get all rows
    cursor.execute("SELECT code, category, name, specification, unit, brand, description FROM materials")
    rows = cursor.fetchall()
    
    # 3. Create temp tables
    cursor.execute("DROP TABLE IF EXISTS materials_temp")
    cursor.execute("""
        CREATE TABLE materials_temp (
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
    
    cursor.execute("DROP TABLE IF EXISTS materials_fts_temp")
    # Check FTS support
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='materials_fts'")
    has_fts = cursor.fetchone() is not None
    if has_fts:
        cursor.execute("""
            CREATE VIRTUAL TABLE materials_fts_temp USING fts5(
                code, category, name, specification, brand, description, tokenize="unicode61"
            )
        """)
        
    conn.commit()
    conn.close()
    
    # 4. Perform reparsing in memory or batch inserts
    from app.preprocessor import extract_params, fts_tokenize, extract_device_name
    from app.domain_router import route_domain
    from app.normalizer import clean_text_standard
    from app.domain_extractors import domain_schema
    from app.llm_client import llm_client
    
    llm_enabled = llm_client.is_enabled()
    pending_tasks = []
    
    records = []
    fts_records = []
    for row in rows:
        code, category, name, specification, unit, brand, description = row
        tokens = f"{name} {specification} {category}".lower()
        full_spec = f"{specification} {description}".strip()
        parsed = extract_params(full_spec, category)
        parsed_params_str = json.dumps(parsed, ensure_ascii=False)
        
        records.append([code, category, name, specification, unit, brand, description, tokens, parsed_params_str, 2])
        
        if llm_enabled:
            name_extracted = extract_device_name(full_spec, category)
            text_clean = clean_text_standard(full_spec)
            domain = route_domain(category, name_extracted, text_clean, category)
            domain_config = domain_schema.get(domain, {})
            must_keys = [field["key"] for field in domain_config.get("must", [])]
            
            is_missing_must = False
            for mk in must_keys:
                if mk not in parsed.get("attrs", {}):
                    is_missing_must = True
                    break
            if is_missing_must:
                pending_tasks.append((code, full_spec, category))
        if has_fts:
            fts_records.append([
                code,
                fts_tokenize(category),
                fts_tokenize(name),
                fts_tokenize(specification),
                fts_tokenize(brand),
                fts_tokenize(description)
            ])
            
    # 5. Insert into temp tables
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.executemany("""
        INSERT OR REPLACE INTO materials_temp 
        (code, category, name, specification, unit, brand, description, search_tokens, parsed_params, parsed_schema_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    
    if has_fts:
        cursor.executemany("""
            INSERT OR REPLACE INTO materials_fts_temp 
            (code, category, name, specification, brand, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, fts_records)
        
    conn.commit()
    conn.close()
    
    # 6. Atomic swap tables under write lock (exclusive lock)
    conn = sqlite3.connect(DB_PATH, isolation_level="EXCLUSIVE")
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN EXCLUSIVE TRANSACTION")
        
        # Rename original tables to old
        cursor.execute("DROP TABLE IF EXISTS materials_old")
        cursor.execute("ALTER TABLE materials RENAME TO materials_old")
        
        # Rename temp tables to active
        cursor.execute("ALTER TABLE materials_temp RENAME TO materials")
        
        if has_fts:
            cursor.execute("DROP TABLE IF EXISTS materials_fts_old")
            cursor.execute("ALTER TABLE materials_fts RENAME TO materials_fts_old")
            cursor.execute("ALTER TABLE materials_fts_temp RENAME TO materials_fts")
            
        # Drop old tables
        cursor.execute("DROP TABLE IF EXISTS materials_old")
        if has_fts:
            cursor.execute("DROP TABLE IF EXISTS materials_fts_old")
            
        # Update schema version metadata
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS materials_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("INSERT OR REPLACE INTO materials_metadata (key, value) VALUES ('schema_version', '2')")
        
        conn.commit()
        print("Atomic database swap completed successfully.")
    except Exception as e:
        conn.rollback()
        print(f"Error during atomic database swap: {e}")
        raise e
    finally:
        conn.close()
        
    # 7. Rebuild FAISS index
    try:
        from app.vector_index import index_manager
        index_manager.check_db_timestamp_and_build(DB_PATH, force=True)
        print("Vector index rebuilt successfully.")
    except Exception as e:
        print(f"Error rebuilding vector index: {e}")
        
    if llm_enabled and pending_tasks:
        process_llm_extraction_async(pending_tasks)
        
    return {"status": "success", "reparsed_rows": len(rows)}

def get_db_schema_stats() -> Dict[str, Any]:
    """
    Calculate structuring rate and metrics for the material database.
    """
    import json
    from app.domain_router import route_domain
    from app.domain_extractors import domain_schema
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT category, name, specification, parsed_params FROM materials")
    rows = cursor.fetchall()
    conn.close()
    
    domain_counts = {}
    domain_structured = {}
    
    total_rows = len(rows)
    if total_rows == 0:
        return {"total_rows": 0, "domains": {}}
        
    for row in rows:
        cat = row["category"]
        name = row["name"]
        spec = row["specification"]
        parsed_str = row["parsed_params"]
        
        domain = route_domain(cat, name, spec)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        
        parsed = {}
        if parsed_str:
            try:
                parsed = json.loads(parsed_str)
            except Exception:
                pass
        
        attrs = parsed.get("attrs", {})
        domain_config = domain_schema.get(domain, {})
        must_keys = [field["key"] for field in domain_config.get("must", [])]
        
        if must_keys:
            extracted_must = sum(1 for k in must_keys if k in attrs and attrs[k] is not None and attrs[k] != "")
            must_fill_rate = extracted_must / len(must_keys)
        else:
            must_fill_rate = 1.0
            
        domain_structured[domain] = domain_structured.get(domain, 0.0) + must_fill_rate
        
    domain_stats = {}
    total_structured_rate_sum = 0.0
    for d in domain_counts:
        count = domain_counts[d]
        fill_sum = domain_structured[d]
        avg_rate = fill_sum / count if count > 0 else 0.0
        domain_stats[d] = {
            "count": count,
            "structuring_rate": round(avg_rate * 100, 2)
        }
        total_structured_rate_sum += fill_sum
        
    overall_structuring_rate = (total_structured_rate_sum / total_rows) * 100 if total_rows > 0 else 0.0
    
    return {
        "total_rows": total_rows,
        "overall_structuring_rate": round(overall_structuring_rate, 2),
        "domains": domain_stats
    }

def reparse_materials_db_partial(category: str = None, codes: List[str] = None) -> Dict[str, Any]:
    """
    Partially reparse materials in the active database.
    Updates parsed_params and search_tokens for selected categories/codes,
    and sets parsed_schema_version to 2.
    """
    import json
    from app.preprocessor import extract_params, fts_tokenize
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT code, category, name, specification, unit, brand, description FROM materials"
    params = []
    
    if codes:
        placeholders = ",".join(["?"] * len(codes))
        query += f" WHERE code IN ({placeholders})"
        params = list(codes)
    elif category:
        query += " WHERE category = ?"
        params = [category]
    else:
        query += " WHERE parsed_schema_version < 2"
        
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    if not rows:
        conn.close()
        return {"status": "success", "reparsed_rows": 0}
        
    print(f"Partially reparsing {len(rows)} materials...")
    
    from app.llm_client import llm_client
    from app.domain_router import route_domain
    from app.preprocessor import extract_device_name
    from app.normalizer import clean_text_standard
    from app.domain_extractors import domain_schema
    
    llm_enabled = llm_client.is_enabled()
    pending_tasks = []
    
    updated_records = []
    for row in rows:
        code, cat, name, spec, unit, brand, desc = row
        tokens = f"{name} {spec} {cat}".lower()
        full_spec = f"{spec} {desc}".strip()
        parsed = extract_params(full_spec, cat)
        parsed_params_str = json.dumps(parsed, ensure_ascii=False)
        updated_records.append([tokens, parsed_params_str, 2, code])
        
        if llm_enabled:
            name_extracted = extract_device_name(full_spec, cat)
            text_clean = clean_text_standard(full_spec)
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
        
    cursor.executemany("""
        UPDATE materials 
        SET search_tokens = ?, parsed_params = ?, parsed_schema_version = ?
        WHERE code = ?
    """, updated_records)
    
    conn.commit()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='materials_fts'")
    has_fts = cursor.fetchone() is not None
    if has_fts:
        fts_records = []
        for row in rows:
            code, cat, name, spec, unit, brand, desc = row
            fts_records.append([
                fts_tokenize(cat),
                fts_tokenize(name),
                fts_tokenize(spec),
                fts_tokenize(brand),
                fts_tokenize(desc),
                code
            ])
        cursor.executemany("""
            UPDATE materials_fts 
            SET category = ?, name = ?, specification = ?, brand = ?, description = ?
            WHERE code = ?
        """, fts_records)
        conn.commit()
        
    conn.close()
    
    try:
        from app.vector_index import index_manager
        index_manager.check_db_timestamp_and_build(DB_PATH, force=True)
    except Exception as e:
        print(f"Error rebuilding vector index: {e}")
        
    if llm_enabled and pending_tasks:
        process_llm_extraction_async(pending_tasks)
        
    return {"status": "success", "reparsed_rows": len(rows)}


def process_llm_extraction_async(pending_tasks: List[Tuple[str, str, str]]):
    """
    Asynchronously calls LLM to extract parameters for the given list of (code, full_spec, category).
    Then updates the database.
    """
    import threading
    import logging
    
    def run():
        from app.llm_client import llm_client
        from app.domain_router import route_domain
        from app.preprocessor import extract_device_name
        from app.normalizer import clean_text_standard
        from app.domain_extractors import domain_schema
        import json
        import time
        
        # Limit to first 50 items to prevent billing overflow and circuit breaker flooding
        tasks_to_run = pending_tasks[:50]
        
        logger_db = logging.getLogger("db_manager_async")
        logger_db.info(f"Starting async LLM extraction for {len(tasks_to_run)} items (truncated from {len(pending_tasks)}).")
        
        # Connect to DB
        conn = get_db_connection()
        cursor = conn.cursor()
        
        for code, full_spec, category in tasks_to_run:
            # Check circuit breaker
            if llm_client.check_circuit_breaker():
                logger_db.warning("Circuit breaker is open. Aborting remaining async extraction.")
                break
                
            try:
                # Add delay between calls to respect rate limit
                time.sleep(1.0)
                
                # Route domain
                name_extracted = extract_device_name(full_spec, category)
                text_clean = clean_text_standard(full_spec)
                domain = route_domain(category, name_extracted, text_clean, category)
                
                domain_config = domain_schema.get(domain, {})
                must_keys = [field["key"] for field in domain_config.get("must", [])]
                should_keys = [field["key"] for field in domain_config.get("should", [])]
                flags = domain_config.get("flags", [])
                
                # Fetch current parsed_params to merge with
                cursor.execute("SELECT parsed_params FROM materials WHERE code = ?", (code,))
                row = cursor.fetchone()
                if not row:
                    continue
                parsed = json.loads(row[0])
                
                llm_parsed = llm_client.extract_params_by_llm(full_spec, domain, must_keys, should_keys, flags)
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
                    cursor.execute(
                        "UPDATE materials SET parsed_params = ? WHERE code = ?",
                        (json.dumps(parsed, ensure_ascii=False), code)
                    )
                    conn.commit()
            except Exception as e:
                logger_db.error(f"Failed to process async LLM extraction for code {code}: {e}")
                
        conn.close()
        logger_db.info("Async LLM extraction completed.")
        
    thread = threading.Thread(target=run, name="LLMExtractionAsync")
    thread.daemon = True
    thread.start()
