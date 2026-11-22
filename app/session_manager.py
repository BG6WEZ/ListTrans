# -*- coding: utf-8 -*-
import os
import sqlite3
import datetime
import uuid
from typing import List, Dict, Any, Optional
from app.auth_manager import get_auth_connection

SESSION_CONTRACT_VERSION = "v0.1.6-m0-v1"
_SESSION_MIGRATION_COLUMNS = (
    ("owner_account_epoch", "TEXT DEFAULT ''"),
    ("document_id", "TEXT DEFAULT ''"),
    ("work_session_id", "TEXT DEFAULT ''"),
    ("schema_version", "TEXT DEFAULT 'v1'"),
    ("trust_level", "TEXT DEFAULT 'legacy_untrusted'"),
)


def init_session_db():
    """Create and atomically migrate work_sessions table in auth.db (idempotent)."""
    conn = get_auth_connection()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
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
        existing_cols = {
            r[1] for r in cursor.execute("PRAGMA table_info(work_sessions)").fetchall()
        }
        for col_name, col_def in _SESSION_MIGRATION_COLUMNS:
            if col_name not in existing_cols:
                cursor.execute(f"ALTER TABLE work_sessions ADD COLUMN {col_name} {col_def}")

        # Quarantine pre-V0.1.6 sessions lacking verifiable server document identity
        cursor.execute("""
            UPDATE work_sessions
            SET trust_level = 'legacy_untrusted'
            WHERE COALESCE(trust_level, '') != 'trusted'
               OR COALESCE(document_id, '') = ''
               OR COALESCE(owner_account_epoch, '') = ''
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_sessions_owner_epoch "
            "ON work_sessions(user_id, owner_account_epoch, session_type)"
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()

    # V015-M2：服务端权威绑定决策表与工作会话同库初始化（幂等）。
    from app.binding_decisions import init_binding_decision_db
    init_binding_decision_db()


def _epoch_clause_and_params(account_epoch: Optional[str]) -> tuple:
    if account_epoch is not None:
        ep = str(account_epoch or "").strip()
        # Reject sessions explicitly owned by another epoch; legacy rows without epoch remain readable only as legacy_untrusted
        return " AND (COALESCE(owner_account_epoch, '') = '' OR owner_account_epoch = ?)", (ep,)
    return "", ()


def save_draft_session(
    user_id: int,
    original_filename: str,
    professional: str,
    items_json: str,
    account_epoch: str = "",
    document_id: str = "",
    work_session_id: str = "",
    trust_level: str = "legacy_untrusted",
) -> str:
    """Save an auto-saved draft. Overwrites previous drafts of the same user+epoch."""
    init_session_db()
    conn = get_auth_connection()
    cursor = conn.cursor()

    ep = str(account_epoch or "").strip()
    doc_id = str(document_id or "").strip()
    ws_id = str(work_session_id or "").strip()
    effective_trust = "trusted" if (trust_level == "trusted" and doc_id and ep) else "legacy_untrusted"

    if effective_trust == "trusted":
        cursor.execute(
            """
            SELECT id FROM work_sessions
            WHERE user_id = ? AND session_type = 'draft'
              AND owner_account_epoch = ? AND trust_level = 'trusted'
            ORDER BY updated_at DESC, rowid DESC LIMIT 1
            """,
            (user_id, ep),
        )
    else:
        cursor.execute(
            """
            SELECT id FROM work_sessions
            WHERE user_id = ? AND session_type = 'draft'
              AND trust_level = 'legacy_untrusted'
              AND (COALESCE(owner_account_epoch, '') = '' OR owner_account_epoch = ?)
            ORDER BY updated_at DESC, rowid DESC LIMIT 1
            """,
            (user_id, ep),
        )
    row = cursor.fetchone()

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if row:
        session_id = row["id"]
        cursor.execute("""
            UPDATE work_sessions
            SET original_filename = ?, professional = ?, items_json = ?,
                owner_account_epoch = ?, document_id = ?, work_session_id = ?,
                schema_version = ?, trust_level = ?, updated_at = ?
            WHERE id = ?
        """, (
            original_filename, professional, items_json,
            ep, doc_id, ws_id, SESSION_CONTRACT_VERSION, effective_trust, now_str, session_id
        ))
    else:
        session_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO work_sessions (
                id, user_id, session_name, original_filename, professional, items_json,
                session_type, owner_account_epoch, document_id, work_session_id,
                schema_version, trust_level, created_at, updated_at
            )
            VALUES (?, ?, 'Auto-Saved Draft', ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id, user_id, original_filename, professional, items_json,
            ep, doc_id, ws_id, SESSION_CONTRACT_VERSION, effective_trust, now_str, now_str
        ))

    conn.commit()
    conn.close()
    return session_id


def get_draft_session(user_id: int, account_epoch: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve the current user's draft (strictly enforcing owner epoch; never promotes legacy rows)."""
    init_session_db()
    conn = get_auth_connection()
    cursor = conn.cursor()
    ep_clause, ep_params = _epoch_clause_and_params(account_epoch)
    cursor.execute(
        f"""
        SELECT * FROM work_sessions
        WHERE user_id = ? AND session_type = 'draft'{ep_clause}
        ORDER BY CASE WHEN trust_level = 'trusted' THEN 0 ELSE 1 END, updated_at DESC, rowid DESC
        LIMIT 1
        """,
        (user_id, *ep_params),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def clear_draft_session(user_id: int, account_epoch: Optional[str] = None):
    """Delete draft session for the user."""
    init_session_db()
    conn = get_auth_connection()
    cursor = conn.cursor()
    ep_clause, ep_params = _epoch_clause_and_params(account_epoch)
    cursor.execute(
        f"DELETE FROM work_sessions WHERE user_id = ? AND session_type = 'draft'{ep_clause}",
        (user_id, *ep_params),
    )
    conn.commit()
    conn.close()


def save_named_session(
    user_id: int,
    session_name: str,
    original_filename: str,
    professional: str,
    items_json: str,
    account_epoch: str = "",
    document_id: str = "",
    work_session_id: str = "",
    trust_level: str = "legacy_untrusted",
) -> str:
    """Save a named session. Creates a new record bound to owner+epoch and optional document."""
    init_session_db()
    conn = get_auth_connection()
    cursor = conn.cursor()
    session_id = str(uuid.uuid4())
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ep = str(account_epoch or "").strip()
    doc_id = str(document_id or "").strip()
    ws_id = str(work_session_id or "").strip()
    effective_trust = "trusted" if (trust_level == "trusted" and doc_id and ep) else "legacy_untrusted"

    cursor.execute("""
        INSERT INTO work_sessions (
            id, user_id, session_name, original_filename, professional, items_json,
            session_type, owner_account_epoch, document_id, work_session_id,
            schema_version, trust_level, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'saved', ?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id, user_id, session_name.strip(), original_filename, professional, items_json,
        ep, doc_id, ws_id, SESSION_CONTRACT_VERSION, effective_trust, now_str, now_str
    ))
    conn.commit()
    conn.close()
    return session_id


def list_saved_sessions(user_id: int, account_epoch: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all saved sessions for the user (excluding stale-epoch rows)."""
    init_session_db()
    conn = get_auth_connection()
    cursor = conn.cursor()
    ep_clause, ep_params = _epoch_clause_and_params(account_epoch)
    cursor.execute(f"""
        SELECT id, session_name, original_filename, professional,
               document_id, work_session_id, trust_level, schema_version,
               created_at, updated_at
        FROM work_sessions
        WHERE user_id = ? AND session_type = 'saved'{ep_clause}
        ORDER BY updated_at DESC
    """, (user_id, *ep_params))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_session_by_id(
    user_id: int, session_id: str, account_epoch: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Retrieve details for a specific session (rejecting foreign user or stale epoch)."""
    init_session_db()
    conn = get_auth_connection()
    cursor = conn.cursor()
    ep_clause, ep_params = _epoch_clause_and_params(account_epoch)
    cursor.execute(
        f"SELECT * FROM work_sessions WHERE id = ? AND user_id = ?{ep_clause}",
        (session_id, user_id, *ep_params),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_named_session(
    user_id: int,
    session_id: str,
    items_json: str,
    account_epoch: Optional[str] = None,
    document_id: Optional[str] = None,
    work_session_id: Optional[str] = None,
    trust_level: Optional[str] = None,
):
    """Overwrite the items in an existing named session."""
    init_session_db()
    conn = get_auth_connection()
    cursor = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ep_clause, ep_params = _epoch_clause_and_params(account_epoch)
    if document_id is not None or work_session_id is not None or trust_level is not None:
        ep = str(account_epoch or "").strip()
        doc_id = str(document_id or "").strip()
        ws_id = str(work_session_id or "").strip()
        effective_trust = "trusted" if (trust_level == "trusted" and doc_id and ep) else "legacy_untrusted"
        cursor.execute(f"""
            UPDATE work_sessions
            SET items_json = ?, document_id = ?, work_session_id = ?,
                trust_level = ?, schema_version = ?, updated_at = ?
            WHERE id = ? AND user_id = ? AND session_type = 'saved'{ep_clause}
        """, (items_json, doc_id, ws_id, effective_trust, SESSION_CONTRACT_VERSION, now_str, session_id, user_id, *ep_params))
    else:
        cursor.execute(f"""
            UPDATE work_sessions
            SET items_json = ?, updated_at = ?
            WHERE id = ? AND user_id = ? AND session_type = 'saved'{ep_clause}
        """, (items_json, now_str, session_id, user_id, *ep_params))
    conn.commit()
    conn.close()


def delete_session_by_id(user_id: int, session_id: str, account_epoch: Optional[str] = None):
    """Delete a work session."""
    init_session_db()
    conn = get_auth_connection()
    cursor = conn.cursor()
    ep_clause, ep_params = _epoch_clause_and_params(account_epoch)
    cursor.execute(
        f"DELETE FROM work_sessions WHERE id = ? AND user_id = ?{ep_clause}",
        (session_id, user_id, *ep_params),
    )
    conn.commit()
    conn.close()


def clear_all_runtime_data():
    """Clear all draft and saved work sessions for system reset."""
    init_session_db()
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM work_sessions")
    conn.commit()
    conn.close()
    # V015-M2：系统重置时级联清空服务端绑定决策，避免旧确认残留绕过门禁。
    from app.binding_decisions import clear_binding_decisions
    clear_binding_decisions()


def clean_old_draft_sessions():
    """Automatically clean drafts that have not been updated for 30 days."""
    init_session_db()
    conn = get_auth_connection()
    cursor = conn.cursor()
    thirty_days_ago = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("DELETE FROM work_sessions WHERE session_type = 'draft' AND updated_at < ?", (thirty_days_ago,))
    conn.commit()
    conn.close()
