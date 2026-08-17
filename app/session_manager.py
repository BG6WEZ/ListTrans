# -*- coding: utf-8 -*-
import os
import sqlite3
import datetime
import uuid
from typing import List, Dict, Any, Optional
from app.auth_manager import get_auth_connection

def init_session_db():
    """Create work_sessions table in the database."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS work_sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            session_name TEXT,
            original_filename TEXT,
            professional TEXT,
            items_json TEXT NOT NULL,
            session_type TEXT DEFAULT 'draft',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    conn.close()

def save_draft_session(user_id: int, original_filename: str, professional: str, items_json: str) -> str:
    """Save an auto-saved draft. Overwrites previous drafts of the same user."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    
    # Check if a draft already exists for this user
    cursor.execute("SELECT id FROM work_sessions WHERE user_id = ? AND session_type = 'draft'", (user_id,))
    row = cursor.fetchone()
    
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if row:
        session_id = row["id"]
        cursor.execute("""
            UPDATE work_sessions 
            SET original_filename = ?, professional = ?, items_json = ?, updated_at = ?
            WHERE id = ?
        """, (original_filename, professional, items_json, now_str, session_id))
    else:
        session_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO work_sessions (id, user_id, session_name, original_filename, professional, items_json, session_type, created_at, updated_at)
            VALUES (?, ?, 'Auto-Saved Draft', ?, ?, ?, 'draft', ?, ?)
        """, (session_id, user_id, original_filename, professional, items_json, now_str, now_str))
        
    conn.commit()
    conn.close()
    return session_id

def get_draft_session(user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve the current user's draft."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM work_sessions WHERE user_id = ? AND session_type = 'draft'", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def clear_draft_session(user_id: int):
    """Delete draft session for the user."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM work_sessions WHERE user_id = ? AND session_type = 'draft'", (user_id,))
    conn.commit()
    conn.close()

def save_named_session(user_id: int, session_name: str, original_filename: str, professional: str, items_json: str) -> str:
    """Save a named session. Creates a new record."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    session_id = str(uuid.uuid4())
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO work_sessions (id, user_id, session_name, original_filename, professional, items_json, session_type, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'saved', ?, ?)
    """, (session_id, user_id, session_name.strip(), original_filename, professional, items_json, now_str, now_str))
    conn.commit()
    conn.close()
    return session_id

def list_saved_sessions(user_id: int) -> List[Dict[str, Any]]:
    """List all saved sessions for the user."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, session_name, original_filename, professional, created_at, updated_at 
        FROM work_sessions 
        WHERE user_id = ? AND session_type = 'saved' 
        ORDER BY updated_at DESC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_session_by_id(user_id: int, session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve details for a specific session."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM work_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_named_session(user_id: int, session_id: str, items_json: str):
    """Overwrite the items in an existing named session."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        UPDATE work_sessions 
        SET items_json = ?, updated_at = ?
        WHERE id = ? AND user_id = ? AND session_type = 'saved'
    """, (items_json, now_str, session_id, user_id))
    conn.commit()
    conn.close()

def delete_session_by_id(user_id: int, session_id: str):
    """Delete a work session."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM work_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
    conn.commit()
    conn.close()

def clear_all_runtime_data():
    """Clear all draft and saved work sessions for system reset."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM work_sessions")
    conn.commit()
    conn.close()

def clean_old_draft_sessions():
    """Automatically clean drafts that have not been updated for 30 days."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    thirty_days_ago = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("DELETE FROM work_sessions WHERE session_type = 'draft' AND updated_at < ?", (thirty_days_ago,))
    conn.commit()
    conn.close()
