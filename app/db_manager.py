# -*- coding: utf-8 -*-
import os
import re
import sqlite3
import pandas as pd
from typing import List, Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "materials.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(xlsx_path=None, force_reload=False):
    """
    Initialize SQLite database.
    If database does not exist or force_reload is True, import data from xlsx_path.
    """
    db_exists = os.path.exists(DB_PATH)
    
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
                search_tokens TEXT
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
        
        if xlsx_path and os.path.exists(xlsx_path):
            print(f"Loading data from {xlsx_path}...")
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
            
            # Create search tokens for LIKE optimization
            search_tokens_list = []
            for _, row in df_cleaned.iterrows():
                tokens = f"{row['name']} {row['specification']} {row['category']}".lower()
                search_tokens_list.append(tokens)
            df_cleaned["search_tokens"] = search_tokens_list
            
            # Write to SQL
            records = df_cleaned[["code", "category", "name", "specification", "unit", "brand", "description", "search_tokens"]].values.tolist()
            
            cursor.executemany("""
                INSERT OR REPLACE INTO materials 
                (code, category, name, specification, unit, brand, description, search_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, records)
            
            if has_fts:
                # Import fts_tokenize locally to avoid circular dependencies
                from app.preprocessor import fts_tokenize
                
                # Apply character spacing to columns inside the FTS table
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
            print(f"Imported {len(records)} material rows into database.")
        else:
            print("No source Excel file loaded. Database is empty.")
        
        conn.close()
        
        # Build vector index cache if vector index exists
        try:
            from app.vector_index import index_manager
            index_manager.check_db_timestamp_and_build(DB_PATH, force=True)
        except Exception as e:
            print(f"Error rebuilding vector index during init_db: {e}")
    else:
        # Always check index on startup
        try:
            from app.vector_index import index_manager
            index_manager.check_db_timestamp_and_build(DB_PATH)
        except Exception as e:
            print(f"Error loading vector index during startup check: {e}")

def get_all_materials(limit=1000):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM materials LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def query_by_code(code: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM materials WHERE code = ?", (code.strip(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def search_materials_keyword(keyword: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Search database for materials using keyword.
    Supports multi-word fuzzy search separated by spaces, commas, or semicolons.
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
        
    # Check if materials_fts exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='materials_fts'")
    has_fts = cursor.fetchone() is not None
    
    if has_fts:
        from app.preprocessor import fts_tokenize
        fts_tokens = []
        for rw in raw_words:
            tokenized_word = fts_tokenize(rw)
            fts_tokens.extend([t for t in tokenized_word.split() if t])
            
        if fts_tokens:
            fts_query = " AND ".join([f'"{t}"' for t in fts_tokens])
            try:
                cursor.execute("""
                    SELECT m.* 
                    FROM materials_fts fts
                    JOIN materials m ON fts.code = m.code
                    WHERE materials_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (fts_query, limit))
                results = cursor.fetchall()
                if results:
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
    params = [f"%{t.lower()}%" for t in like_tokens] + [limit]
    
    cursor.execute(f"""
        SELECT * FROM materials 
        WHERE {like_clauses}
        LIMIT ?
    """, params)
    
    results = cursor.fetchall()
    conn.close()
    return [dict(r) for r in results]

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
    
    conn.close()
    return {
        "row_count": row_count,
        "last_upload_date": last_upload_date,
        "filename": filename
    }

def update_db_metadata(row_count: int, filename: str):
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
    conn.commit()
    conn.close()

def import_materials_from_excel(xlsx_path: str, mode: str = "replace", filename: str = "materials.xlsx") -> Dict[str, Any]:
    """
    Import materials from Excel file into SQLite database.
    mode: "replace" (full replacement) or "append" (incremental add).
    """
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
    
    # Create search tokens
    search_tokens_list = []
    for _, row in df_cleaned.iterrows():
        tokens = f"{row['name']} {row['specification']} {row['category']}".lower()
        search_tokens_list.append(tokens)
    df_cleaned["search_tokens"] = search_tokens_list
    
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
    records = df_cleaned[["code", "category", "name", "specification", "unit", "brand", "description", "search_tokens"]].values.tolist()
    cursor.executemany("""
        INSERT OR REPLACE INTO materials 
        (code, category, name, specification, unit, brand, description, search_tokens)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
    
    # Update upload metadata
    update_db_metadata(len(df_cleaned), filename)
    
    # Return fresh metadata
    return get_db_metadata()
