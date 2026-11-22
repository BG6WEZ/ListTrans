# -*- coding: utf-8 -*-
"""
Database schema definitions, atomic migration, and legacy quarantine for Data Flywheel tables in materials.db.
"""
import importlib
import sqlite3
from typing import Any, Callable, Dict, List, Optional

FLYWHEEL_CONTRACT_VERSION = "v0.1.6-m0-v1"
TRUST_LEVEL_TRUSTED = "trusted"
TRUST_LEVEL_LEGACY_UNTRUSTED = "legacy_untrusted"

_FLYWHEEL_RECORD_MIGRATION_COLUMNS = (
    ("actor_user_id", "INTEGER"),
    ("actor_account_epoch", "TEXT DEFAULT ''"),
    ("document_id", "TEXT DEFAULT ''"),
    ("upload_task_id", "TEXT DEFAULT ''"),
    ("work_session_id", "TEXT DEFAULT ''"),
    ("saved_session_id", "TEXT DEFAULT ''"),
    ("sheet_id", "TEXT DEFAULT ''"),
    ("sheet_name", "TEXT DEFAULT ''"),
    ("sheet_index", "INTEGER DEFAULT -1"),
    ("source_row_id", "TEXT DEFAULT ''"),
    ("source_row_num", "INTEGER DEFAULT 0"),
    ("event_seq", "INTEGER DEFAULT 0"),
    ("payload_digest", "TEXT DEFAULT ''"),
    ("initial_snapshot_id", "TEXT DEFAULT ''"),
    ("recommendation_snapshot_id", "TEXT DEFAULT ''"),
    ("recommendation_revision", "INTEGER DEFAULT 0"),
    ("initial_auto_top1_code", "TEXT DEFAULT ''"),
    ("initial_auto_top1_score", "REAL DEFAULT 0.0"),
    ("initial_auto_top1_name", "TEXT DEFAULT ''"),
    ("initial_auto_candidates_json", "TEXT DEFAULT '[]'"),
    ("model_version", "TEXT DEFAULT ''"),
    ("config_digest_full", "TEXT DEFAULT ''"),
    ("parsed_spec_json", "TEXT DEFAULT '{}'"),
    ("trust_level", "TEXT DEFAULT 'legacy_untrusted'"),
    ("legacy_reason", "TEXT DEFAULT ''"),
    ("collection_boundary", "TEXT DEFAULT 'legacy'"),
    ("is_stale_arrival", "INTEGER DEFAULT 0"),
    ("contract_version", "TEXT DEFAULT ''"),
    ("created_at", "TIMESTAMP"),
)


def _resolve_materials_db_path() -> str:
    db_mod = importlib.import_module("app.db_manager")
    resolver = getattr(db_mod, "_resolve_db_path", None)
    return str(resolver() if callable(resolver) else db_mod.DB_PATH)


def open_flywheel_tx_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open an isolated SQLite connection with autocommit off for explicit BEGIN IMMEDIATE transactions."""
    target = db_path or _resolve_materials_db_path()
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_flywheel_tables(
    db_path: Optional[str] = None,
    _migration_hook: Optional[Callable[[str, sqlite3.Cursor], None]] = None,
) -> Dict[str, Any]:
    """Create and atomically migrate Flywheel tables, indexes, and effective views.

    Uses isolation_level=None + BEGIN IMMEDIATE so DDL (CREATE/ALTER/INDEX) and legacy
    quarantine DML commit or roll back as a single atomic unit with zero partial writes.
    Safe and idempotent to invoke repeatedly on empty or pre-V0.1.6 databases.
    """
    conn = open_flywheel_tx_connection(db_path)
    migrated_columns: List[str] = []
    quarantined_legacy_rows = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Concentrated confirmations are audit feedback only. Training and mining read the
        # trusted single-row event/sample tables, never this separate event stream.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_priority_confirmation_events (
            event_id TEXT PRIMARY KEY,
            source TEXT NOT NULL CHECK (source = 'priority_review'),
            payload_json TEXT NOT NULL,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

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
        if _migration_hook:
            _migration_hook("after_create_flywheel_records", cur)

        existing_cols = {
            row["name"]
            for row in cur.execute("PRAGMA table_info(flywheel_records)").fetchall()
        }
        for col_name, col_def in _FLYWHEEL_RECORD_MIGRATION_COLUMNS:
            if col_name not in existing_cols:
                cur.execute(f"ALTER TABLE flywheel_records ADD COLUMN {col_name} {col_def}")
                migrated_columns.append(col_name)
                if _migration_hook:
                    _migration_hook(f"after_add_column:{col_name}", cur)

        # Quarantine existing pre-V0.1.6 / unverified records without fabricating attribution.
        cur.execute("""
        UPDATE flywheel_records
        SET
            trust_level = 'legacy_untrusted',
            collection_boundary = CASE
                WHEN COALESCE(collection_boundary, '') = '' OR collection_boundary = 'legacy'
                THEN 'legacy_pre_v016'
                ELSE collection_boundary
            END,
            legacy_reason = CASE
                WHEN COALESCE(legacy_reason, '') != '' THEN legacy_reason
                WHEN session_id = 'default_session' THEN 'legacy_default_session'
                WHEN user_id = 'legacy_anonymous' THEN 'legacy_anonymous_actor'
                ELSE 'missing_server_document_identity'
            END
        WHERE COALESCE(trust_level, '') != 'trusted'
           OR actor_user_id IS NULL
           OR COALESCE(actor_account_epoch, '') = ''
           OR COALESCE(document_id, '') = ''
           OR COALESCE(sheet_id, '') = ''
           OR COALESCE(source_row_id, '') = ''
           OR session_id = 'default_session'
           OR user_id = 'legacy_anonymous'
        """)
        quarantined_legacy_rows = cur.rowcount
        if _migration_hook:
            _migration_hook("after_quarantine_legacy", cur)

        # Indexes for flywheel_records
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fwr_session_row ON flywheel_records(session_id, row_idx);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fwr_raw_norm ON flywheel_records(raw_name_norm);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fwr_user_id ON flywheel_records(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fwr_created_at ON flywheel_records(created_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fwr_mining ON flywheel_records(is_processed, superseded);")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_fwr_object_scope "
            "ON flywheel_records(actor_user_id, actor_account_epoch, document_id, sheet_id, source_row_id);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_fwr_trust_level "
            "ON flywheel_records(trust_level, superseded);"
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_fwr_trusted_obj_seq "
            "ON flywheel_records(actor_user_id, actor_account_epoch, document_id, sheet_id, source_row_id, event_seq) "
            "WHERE trust_level = 'trusted' AND event_seq > 0;"
        )

        # Authoritative document -> sheet -> source_row -> recommendation snapshot registry
        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_documents (
            document_id TEXT PRIMARY KEY,
            upload_task_id TEXT UNIQUE NOT NULL,
            work_session_id TEXT NOT NULL,
            actor_user_id INTEGER NOT NULL,
            actor_account_epoch TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            file_sha256 TEXT NOT NULL,
            collection_boundary TEXT NOT NULL DEFAULT 'server_upload',
            contract_version TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_fwd_owner "
            "ON flywheel_documents(actor_user_id, actor_account_epoch);"
        )

        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_sheets (
            sheet_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            sheet_index INTEGER NOT NULL,
            sheet_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(document_id, sheet_index)
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_source_rows (
            source_row_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            source_row_num INTEGER NOT NULL,
            display_row_idx INTEGER NOT NULL,
            raw_name TEXT NOT NULL,
            raw_name_norm TEXT NOT NULL,
            raw_spec TEXT DEFAULT '',
            raw_unit TEXT DEFAULT '',
            raw_qty REAL DEFAULT 0.0,
            raw_loss REAL DEFAULT 1.0,
            raw_category TEXT DEFAULT '',
            raw_brand TEXT DEFAULT '',
            raw_code TEXT DEFAULT '',
            professional TEXT DEFAULT '暖通',
            initial_parsed_spec_json TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(sheet_id, source_row_num, display_row_idx)
        );
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_fwsr_doc_sheet "
            "ON flywheel_source_rows(document_id, sheet_id);"
        )

        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_recommendation_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            source_row_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            is_initial INTEGER NOT NULL DEFAULT 0,
            snapshot_kind TEXT NOT NULL DEFAULT 'initial_upload',
            professional TEXT NOT NULL,
            parsed_spec_json TEXT NOT NULL,
            auto_top1_code TEXT DEFAULT '',
            auto_top1_score REAL DEFAULT 0.0,
            auto_top1_name TEXT DEFAULT '',
            auto_candidates_json TEXT DEFAULT '[]',
            initial_bindings_json TEXT DEFAULT '[]',
            initial_match_json TEXT,
            decision_status TEXT DEFAULT 'recommended',
            algo_version TEXT NOT NULL,
            model_version TEXT NOT NULL,
            config_version TEXT NOT NULL,
            config_digest_full TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_row_id, revision)
        );
        """)
        if "initial_match_json" not in {
            column[1] for column in cur.execute("PRAGMA table_info(flywheel_recommendation_snapshots)")
        }:
            cur.execute("ALTER TABLE flywheel_recommendation_snapshots ADD COLUMN initial_match_json TEXT")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_fwrs_row_rev "
            "ON flywheel_recommendation_snapshots(source_row_id, revision);"
        )

        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_feedback_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_event_id TEXT UNIQUE NOT NULL,
            payload_digest TEXT NOT NULL,
            actor_user_id INTEGER,
            actor_account_epoch TEXT DEFAULT '',
            document_id TEXT DEFAULT '',
            sheet_id TEXT DEFAULT '',
            source_row_id TEXT DEFAULT '',
            event_seq INTEGER DEFAULT 0,
            initial_snapshot_id TEXT DEFAULT '',
            recommendation_snapshot_id TEXT DEFAULT '',
            raw_name TEXT DEFAULT '',
            raw_spec TEXT DEFAULT '',
            raw_text TEXT DEFAULT '',
            auto_code TEXT DEFAULT '',
            manual_code TEXT DEFAULT '',
            is_correct INTEGER NOT NULL DEFAULT 0,
            algo_version TEXT DEFAULT '',
            model_version TEXT DEFAULT '',
            config_version TEXT DEFAULT '',
            trust_level TEXT NOT NULL DEFAULT 'trusted',
            legacy_reason TEXT DEFAULT '',
            superseded INTEGER NOT NULL DEFAULT 0,
            is_stale_arrival INTEGER NOT NULL DEFAULT 0,
            contract_version TEXT NOT NULL DEFAULT 'v0.1.6-m0-v1',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_fwfs_object_scope "
            "ON flywheel_feedback_samples(actor_user_id, actor_account_epoch, document_id, sheet_id, source_row_id);"
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_fwfs_trusted_obj_seq "
            "ON flywheel_feedback_samples(actor_user_id, actor_account_epoch, document_id, sheet_id, source_row_id, event_seq) "
            "WHERE trust_level = 'trusted' AND event_seq > 0;"
        )

        # Effective final trusted view for M0 consumers and M1 miner interface
        cur.execute("DROP VIEW IF EXISTS flywheel_effective_records;")
        cur.execute(
            f"""
        CREATE VIEW flywheel_effective_records AS
        SELECT * FROM flywheel_records
        WHERE {trusted_flywheel_where_clause()};
        """
        )

        # 2. Rule suggestions table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS flywheel_rule_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_type TEXT NOT NULL,
            source_pattern TEXT NOT NULL,
            target_value TEXT NOT NULL,
            discipline TEXT DEFAULT '',

            support_count INTEGER DEFAULT 1,
            distinct_users INTEGER DEFAULT 1,
            consistency REAL DEFAULT 1.0,
            sample_ids_json TEXT DEFAULT '[]',
            source_origin TEXT NOT NULL DEFAULT 'trusted_miner',
            rule_context_json TEXT DEFAULT '{}',

            status TEXT DEFAULT 'pending',
            applied_rule_id TEXT DEFAULT '',
            reviewer_id TEXT DEFAULT '',
            reviewed_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        sug_cols = {
            row["name"]
            for row in cur.execute("PRAGMA table_info(flywheel_rule_suggestions)").fetchall()
        }
        sug_source_origin_added = False
        sug_m2_gov_added = False
        for col_name, col_def in (
            ("created_at", "TIMESTAMP"),
            ("source_origin", "TEXT DEFAULT 'trusted_miner'"),
            ("rule_context_json", "TEXT DEFAULT '{}'"),
            ("updated_at", "TIMESTAMP"),
            ("last_eval_id", "TEXT DEFAULT ''"),
            ("last_eval_digest", "TEXT DEFAULT ''"),
            ("neutral_reason", "TEXT DEFAULT ''"),
            ("governance_provenance", "TEXT DEFAULT 'pending_eval'"),
            ("migration_version", "TEXT DEFAULT ''"),
        ):
            if col_name not in sug_cols:
                cur.execute(f"ALTER TABLE flywheel_rule_suggestions ADD COLUMN {col_name} {col_def}")
                migrated_columns.append(f"flywheel_rule_suggestions.{col_name}")
                if col_name == "source_origin":
                    sug_source_origin_added = True
                if col_name == "governance_provenance":
                    sug_m2_gov_added = True
        if sug_source_origin_added:
            # Preserve pre-M1 suggestions without auto-upgrading unscoped rows to trusted universal rules.
            cur.execute("""
            UPDATE flywheel_rule_suggestions
            SET source_origin = CASE
                WHEN COALESCE(discipline, '') IN ('', '通用', '全部', 'all', 'universal')
                THEN 'legacy_unscoped'
                ELSE 'legacy_pre_m1'
            END,
            updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
            """)
        if sug_m2_gov_added:
            cur.execute("""
            UPDATE flywheel_rule_suggestions
            SET governance_provenance = CASE
                    WHEN COALESCE(last_eval_id, '') != '' THEN governance_provenance
                    ELSE 'legacy_unverified'
                END,
                migration_version = CASE
                    WHEN COALESCE(migration_version, '') != '' THEN migration_version
                    ELSE 'v0.16-m2-v1'
                END
            WHERE COALESCE(last_eval_id, '') = ''
            """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fws_status ON flywheel_rule_suggestions(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fws_pattern ON flywheel_rule_suggestions(source_pattern);")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_fws_scope_key "
            "ON flywheel_rule_suggestions(suggestion_type, source_pattern, target_value, discipline, source_origin);"
        )
        if _migration_hook:
            _migration_hook("after_migrate_rule_suggestions", cur)

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
            targeted_eval_passed INTEGER DEFAULT 0,
            golden_eval_score REAL DEFAULT 0.0,
            golden_eval_delta REAL DEFAULT 0.0,
            eval_id TEXT DEFAULT '',
            rule_digest TEXT DEFAULT '',
            benefit_class TEXT DEFAULT '',
            neutral_reason TEXT DEFAULT '',
            discipline TEXT DEFAULT '',
            governance_provenance TEXT DEFAULT 'verified',
            migration_version TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        snap_cols = {
            row["name"]
            for row in cur.execute("PRAGMA table_info(flywheel_config_snapshots)").fetchall()
        }
        snap_m2_eval_added = False
        for col_name, col_def in (
            ("eval_id", "TEXT DEFAULT ''"),
            ("rule_digest", "TEXT DEFAULT ''"),
            ("benefit_class", "TEXT DEFAULT ''"),
            ("neutral_reason", "TEXT DEFAULT ''"),
            ("discipline", "TEXT DEFAULT ''"),
            ("governance_provenance", "TEXT DEFAULT 'verified'"),
            ("migration_version", "TEXT DEFAULT ''"),
            ("created_at", "TIMESTAMP"),
        ):
            if col_name not in snap_cols:
                cur.execute(f"ALTER TABLE flywheel_config_snapshots ADD COLUMN {col_name} {col_def}")
                migrated_columns.append(f"flywheel_config_snapshots.{col_name}")
                if col_name == "eval_id":
                    snap_m2_eval_added = True
        if snap_m2_eval_added:
            cur.execute("""
            UPDATE flywheel_config_snapshots
            SET targeted_eval_passed = 0,
                governance_provenance = 'legacy_unverified',
                migration_version = 'v0.16-m2-v1'
            WHERE COALESCE(eval_id, '') = ''
            """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_snap_active ON flywheel_config_snapshots(is_active, created_at);")
        if _migration_hook:
            _migration_hook("after_migrate_config_snapshots", cur)

        # 3b. Unified rule evaluation credentials table (M2-C01..M2-C08)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS rule_eval_runs (
            eval_id TEXT PRIMARY KEY,
            rule_source TEXT NOT NULL,
            rule_key TEXT NOT NULL,
            rule_type TEXT NOT NULL,
            rule_revision INTEGER NOT NULL DEFAULT 1,
            normalized_payload_json TEXT NOT NULL,
            payload_digest TEXT NOT NULL,
            baseline_release_version TEXT NOT NULL,
            materials_revision TEXT NOT NULL,
            config_digest TEXT NOT NULL,
            algo_version TEXT NOT NULL,
            model_version TEXT NOT NULL,
            evaluator_version TEXT NOT NULL,
            eval_assets_digest TEXT NOT NULL,
            guard_assets_digest TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            safety_passed INTEGER NOT NULL DEFAULT 0,
            benefit_class TEXT NOT NULL DEFAULT 'invalid',
            targeted_count INTEGER NOT NULL DEFAULT 0,
            guard_count INTEGER NOT NULL DEFAULT 0,
            improved_count INTEGER NOT NULL DEFAULT 0,
            degraded_count INTEGER NOT NULL DEFAULT 0,
            degraded_from_top1 INTEGER NOT NULL DEFAULT 0,
            failed_samples_count INTEGER NOT NULL DEFAULT 0,
            skipped_samples_count INTEGER NOT NULL DEFAULT 0,
            rule_execution_hits INTEGER NOT NULL DEFAULT 0,
            binding_checks_count INTEGER NOT NULL DEFAULT 0,
            baseline_process_exit INTEGER,
            candidate_process_exit INTEGER,
            result_summary_json TEXT NOT NULL DEFAULT '{}',
            error_message TEXT DEFAULT '',
            owner_user_id INTEGER,
            owner_account_epoch TEXT DEFAULT '',
            created_at_epoch REAL NOT NULL,
            completed_at_epoch REAL,
            expires_at_epoch REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            expires_at TIMESTAMP
        );
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_eval_rule_key "
            "ON rule_eval_runs(rule_source, rule_key, rule_revision, created_at_epoch DESC);"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_eval_status ON rule_eval_runs(status);")
        if _migration_hook:
            _migration_hook("after_migrate_rule_eval_runs", cur)

        # 4. Authoritative Pattern Aggregates table (separates archived history from live recompute)
        agg_tbl_row = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='flywheel_pattern_aggregates'"
        ).fetchone()
        agg_cols = {
            row["name"]
            for row in cur.execute("PRAGMA table_info(flywheel_pattern_aggregates)").fetchall()
        } if agg_tbl_row else set()
        required_agg_cols = {
            "id", "source_pattern", "suggestion_type", "target_value", "discipline",
            "source_origin", "archived_support", "archived_distinct_users",
            "active_support", "active_distinct_users", "total_support",
            "param_adjust_count", "dominant_target_code", "sample_ids_json",
            "context_summary_json", "updated_at",
        }
        agg_ddl_sql = str(agg_tbl_row["sql"] or "") if agg_tbl_row else ""
        has_scoped_unique = (
            "discipline" in agg_ddl_sql
            and "source_origin" in agg_ddl_sql
            and "UNIQUE" in agg_ddl_sql.upper()
        )
        agg_create_sql = """
        CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_pattern TEXT NOT NULL,
            suggestion_type TEXT NOT NULL,
            target_value TEXT NOT NULL DEFAULT '',
            discipline TEXT NOT NULL DEFAULT '',
            source_origin TEXT NOT NULL DEFAULT 'trusted_recompute',
            archived_support INTEGER NOT NULL DEFAULT 0,
            archived_distinct_users INTEGER NOT NULL DEFAULT 0,
            active_support INTEGER NOT NULL DEFAULT 0,
            active_distinct_users INTEGER NOT NULL DEFAULT 0,
            total_support INTEGER NOT NULL DEFAULT 0,
            param_adjust_count INTEGER NOT NULL DEFAULT 0,
            dominant_target_code TEXT NOT NULL DEFAULT '',
            sample_ids_json TEXT NOT NULL DEFAULT '[]',
            context_summary_json TEXT NOT NULL DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_pattern, suggestion_type, target_value, discipline, source_origin)
        );
        """
        if not agg_tbl_row:
            cur.execute(agg_create_sql.replace("{table_name}", "flywheel_pattern_aggregates"))
        elif not required_agg_cols.issubset(agg_cols) or not has_scoped_unique:
            cur.execute("DROP TABLE IF EXISTS flywheel_pattern_aggregates_new;")
            cur.execute(agg_create_sql.replace("{table_name}", "flywheel_pattern_aggregates_new"))
            old_rows = cur.execute("SELECT * FROM flywheel_pattern_aggregates ORDER BY id ASC").fetchall()
            seen_keys = set()
            for r in old_rows:
                rd = dict(r)
                if "source_pattern" in rd and rd.get("source_pattern") is not None:
                    src_pat = str(rd.get("source_pattern") or "")
                    sug_type = str(rd.get("suggestion_type") or "PARAM_ADJUST")
                    tgt_val = str(rd.get("target_value") or "")
                    disc = str(rd.get("discipline") or "").strip()
                    origin = str(rd.get("source_origin") or "").strip() or "legacy_archive"
                    arch_sup = int(rd.get("archived_support") or 0)
                    arch_users = int(rd.get("archived_distinct_users") or 0)
                    act_sup = int(rd.get("active_support") or 0)
                    act_users = int(rd.get("active_distinct_users") or 0)
                    pa_cnt = int(rd.get("param_adjust_count") or 0)
                    dom_code = str(rd.get("dominant_target_code") or "")
                    sids_json = str(rd.get("sample_ids_json") or "[]")
                    ctx_json = str(rd.get("context_summary_json") or "{}")
                    upd_at = rd.get("updated_at")
                else:
                    # Legacy pattern_key schema variant: preserve data & mark unknown provenance
                    src_pat = str(rd.get("pattern_key") or "")
                    sug_type = "PARAM_ADJUST"
                    tgt_val = ""
                    disc = str(rd.get("discipline") or "").strip()
                    origin = "legacy_unknown"
                    pa_cnt = int(rd.get("param_adjust_count") or 0)
                    arch_sup = int(rd.get("total_count") or pa_cnt or 0)
                    arch_users = 0
                    act_sup = 0
                    act_users = 0
                    dom_code = str(rd.get("dominant_target_code") or "")
                    sids_json = str(rd.get("sample_ids_json") or "[]")
                    ctx_json = "{}"
                    upd_at = rd.get("updated_at")

                key = (src_pat, sug_type, tgt_val, disc, origin)
                if key in seen_keys:
                    origin = f"{origin}_dup_{rd.get('id', 0)}"
                    key = (src_pat, sug_type, tgt_val, disc, origin)
                seen_keys.add(key)

                cur.execute(
                    """
                    INSERT INTO flywheel_pattern_aggregates_new (
                        source_pattern, suggestion_type, target_value, discipline, source_origin,
                        archived_support, archived_distinct_users, active_support, active_distinct_users,
                        total_support, param_adjust_count, dominant_target_code, sample_ids_json,
                        context_summary_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                    """,
                    (
                        src_pat, sug_type, tgt_val, disc, origin,
                        arch_sup, arch_users, act_sup, act_users,
                        arch_sup + act_sup, pa_cnt, dom_code, sids_json,
                        ctx_json, upd_at,
                    ),
                )
            cur.execute("DROP TABLE flywheel_pattern_aggregates;")
            cur.execute("ALTER TABLE flywheel_pattern_aggregates_new RENAME TO flywheel_pattern_aggregates;")
            migrated_columns.append("flywheel_pattern_aggregates.schema_v1")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_fwpa_scope "
            "ON flywheel_pattern_aggregates(discipline, suggestion_type, source_pattern);"
        )
        if _migration_hook:
            _migration_hook("after_migrate_pattern_aggregates", cur)

        cur.execute("""
        INSERT INTO flywheel_schema_meta (key, value, updated_at)
        VALUES ('contract_version', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """, (FLYWHEEL_CONTRACT_VERSION,))
        cur.execute("""
        INSERT INTO flywheel_schema_meta (key, value, updated_at)
        VALUES ('aggregates_schema_version', 'v0.1.6-m1-v1', CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """)
        cur.execute("""
        INSERT INTO flywheel_schema_meta (key, value, updated_at)
        VALUES ('governance_schema_version', 'v0.16-m2-v1', CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """)
        if _migration_hook:
            _migration_hook("after_schema_meta", cur)

        conn.execute("COMMIT")
        return {
            "contract_version": FLYWHEEL_CONTRACT_VERSION,
            "migrated_columns": migrated_columns,
            "quarantined_legacy_rows": quarantined_legacy_rows,
        }
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def trusted_flywheel_where_clause(table_alias: str = "", include_effective_only: bool = True) -> str:
    """Shared M0->M1 SQL predicate restricting reads to verified trusted effective records."""
    prefix = f"{table_alias}." if table_alias else ""
    clauses = [
        f"{prefix}trust_level = 'trusted'",
        f"{prefix}actor_user_id IS NOT NULL",
        f"{prefix}actor_user_id > 0",
        f"COALESCE({prefix}actor_account_epoch, '') != ''",
        f"COALESCE({prefix}document_id, '') != ''",
        f"COALESCE({prefix}sheet_id, '') != ''",
        f"COALESCE({prefix}source_row_id, '') != ''",
        f"COALESCE({prefix}payload_digest, '') != ''",
        f"{prefix}session_id != 'default_session'",
        f"{prefix}user_id != 'legacy_anonymous'",
        (
            f"EXISTS (SELECT 1 FROM flywheel_documents _fwd "
            f"WHERE _fwd.document_id = {prefix}document_id "
            f"AND _fwd.actor_user_id = {prefix}actor_user_id "
            f"AND _fwd.actor_account_epoch = {prefix}actor_account_epoch)"
        ),
        (
            f"EXISTS (SELECT 1 FROM flywheel_source_rows _fwsr "
            f"WHERE _fwsr.source_row_id = {prefix}source_row_id "
            f"AND _fwsr.document_id = {prefix}document_id "
            f"AND _fwsr.sheet_id = {prefix}sheet_id)"
        ),
    ]
    if include_effective_only:
        clauses.extend([
            f"{prefix}superseded = 0",
            f"COALESCE({prefix}is_stale_arrival, 0) = 0",
        ])
    return " AND ".join(clauses)


if __name__ == "__main__":
    init_flywheel_tables()
    print("Flywheel tables initialized successfully in materials.db")
