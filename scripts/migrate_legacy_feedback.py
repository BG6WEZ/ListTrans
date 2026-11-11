# -*- coding: utf-8 -*-
"""
Migrate legacy feedback samples from app/feedback_samples.json into materials.db flywheel_records.
"""
import os
import sys
import json
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, PROJECT_ROOT)

from app.db_manager import query_by_code, get_db_cursor
from app.preprocessor import clean_text, extract_device_name
from app.flywheel_db import init_flywheel_tables
FEEDBACK_FILE = os.path.join(PROJECT_ROOT, "app", "feedback_samples.json")

def migrate_legacy_samples():
    init_flywheel_tables()
    if not os.path.exists(FEEDBACK_FILE):
        print(f"No legacy feedback file found at {FEEDBACK_FILE}")
        return

    with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
        samples = json.load(f)

    print(f"Loaded {len(samples)} legacy feedback samples. Starting migration...")
    migrated_count = 0

    with get_db_cursor(commit=True) as cur:
        for i, s in enumerate(samples):
            raw_text = s.get("raw_text", "")
            raw_name = extract_device_name(raw_text) or raw_text.split("\n")[0][:50]
            raw_name_norm = clean_text(raw_name)
            auto_code = s.get("auto_code", "")
            manual_code = s.get("manual_code", "")
            is_correct = s.get("is_correct", False)
            row_id = s.get("row_id", i + 1)
            
            auto_mat = query_by_code(auto_code) if auto_code else None
            auto_top1_name = auto_mat.get("name", "") if auto_mat else ""
            auto_candidates_json = json.dumps([{"code": auto_code, "name": auto_top1_name, "score": 80.0 if is_correct else 50.0}])

            manual_mat = query_by_code(manual_code) if manual_code else None
            final_name = manual_mat.get("name", "") if manual_mat else auto_top1_name
            final_spec = manual_mat.get("specification", "") if manual_mat else ""
            final_targets = [{"code": manual_code or auto_code, "name": final_name, "spec": final_spec, "qty_ratio": 1.0}]

            action_type = "CONFIRM_TOP1" if is_correct else "MANUAL_REPLACE"
            client_event_id = f"legacy_seed_{i}_{row_id}_{manual_code}"

            cur.execute("""
            INSERT OR IGNORE INTO flywheel_records (
                client_event_id, session_id, row_idx, discipline,
                raw_name, raw_name_norm, raw_spec, raw_unit, raw_category,
                extracted_params, auto_top1_code, auto_top1_score, auto_top1_name,
                auto_candidates_json, algo_version, config_version, action_type,
                final_targets_json, user_id, user_name, superseded, is_processed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                client_event_id, "legacy_session", row_id, "通用",
                raw_name, raw_name_norm, raw_text, "个", "",
                "{}", auto_code, 80.0 if is_correct else 50.0, auto_top1_name,
                auto_candidates_json, "legacy_v0", "snap_legacy_seed", action_type,
                json.dumps(final_targets, ensure_ascii=False),
                "legacy_anonymous", "历史匿名操作员", 0, 0
            ))
            if cur.rowcount > 0:
                migrated_count += 1

    print(f"Migration completed. Inserted {migrated_count} unique legacy records into flywheel_records.")

if __name__ == "__main__":
    migrate_legacy_samples()
