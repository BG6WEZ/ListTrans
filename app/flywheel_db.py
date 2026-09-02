# -*- coding: utf-8 -*-
"""
Database schema definitions and initialization for Data Flywheel tables in materials.db.
"""
import sqlite3
from app.db_manager import get_db_cursor, DB_PATH

def init_flywheel_tables():
    """Create Flywheel related tables and indexes if they do not exist."""
    with get_db_cursor(commit=True) as cur:
        # 1. Flow of user feedback records
        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_event_id TEXT UNIQUE NOT NULL,
            session_id TEXT NOT NULL,
            row_idx INTEGER NOT NULL,
            discipline TEXT DEFAULT '通用',
            
            raw_name TEXT NOT NULL,
            raw_name_norm TEXT NOT NULL,
            raw_spec TEXT DEFAULT '',
            raw_unit TEXT DEFAULT '',
            raw_category TEXT DEFAULT '',
            extracted_params TEXT DEFAULT '{}',
            
            auto_top1_code TEXT DEFAULT '',
            auto_top1_score REAL DEFAULT 0.0,
            auto_top1_name TEXT DEFAULT '',
            auto_candidates_json TEXT DEFAULT '[]',
            algo_version TEXT DEFAULT 'v1.0',
            config_version TEXT DEFAULT '',
            
            action_type TEXT NOT NULL,
            final_targets_json TEXT NOT NULL,
            
            user_id TEXT NOT NULL,
            user_name TEXT DEFAULT '',
            superseded INTEGER DEFAULT 0,
            is_processed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        
        # Indexes for flywheel_records
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fwr_session_row ON flywheel_records(session_id, row_idx);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fwr_raw_norm ON flywheel_records(raw_name_norm);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fwr_user_id ON flywheel_records(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fwr_created_at ON flywheel_records(created_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fwr_mining ON flywheel_records(is_processed, superseded);")
        
        # 2. Rule suggestions table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_rule_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_type TEXT NOT NULL,
            source_pattern TEXT NOT NULL,
            target_value TEXT NOT NULL,
            discipline TEXT DEFAULT '通用',
            
            support_count INTEGER DEFAULT 1,
            distinct_users INTEGER DEFAULT 1,
            consistency REAL DEFAULT 1.0,
            sample_ids_json TEXT DEFAULT '[]',
            
            status TEXT DEFAULT 'pending',
            applied_rule_id TEXT DEFAULT '',
            reviewer_id TEXT DEFAULT '',
            reviewed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fws_status ON flywheel_rule_suggestions(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fws_pattern ON flywheel_rule_suggestions(source_pattern);")
        
        # 3. Configuration snapshot table for LIFO stack rollback
        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_config_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_version TEXT NOT NULL UNIQUE,
            target_type TEXT NOT NULL,
            config_before TEXT NOT NULL,
            config_after TEXT NOT NULL,
            suggestion_id INTEGER,
            reason TEXT DEFAULT '',
            operator_id TEXT NOT NULL,
            targeted_eval_passed INTEGER DEFAULT 1,
            golden_eval_score REAL DEFAULT 0.0,
            golden_eval_delta REAL DEFAULT 0.0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_snap_active ON flywheel_config_snapshots(is_active, created_at);")
        
        # 4. Pattern aggregates summary table for historical archive
        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_pattern_aggregates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_pattern TEXT NOT NULL,
            suggestion_type TEXT NOT NULL,
            target_value TEXT NOT NULL,
            archived_support INTEGER DEFAULT 0,
            archived_distinct_users INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_pattern, suggestion_type, target_value)
        );
        """)

if __name__ == "__main__":
    init_flywheel_tables()
    print("Flywheel tables initialized successfully in materials.db")
