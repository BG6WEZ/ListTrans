# -*- coding: utf-8 -*-
import json
import os
import tempfile
import threading
from typing import Any, Dict, List, Optional

FEEDBACK_FILE = os.getenv(
    "FEEDBACK_SAMPLES_FILE",
    os.path.join(os.path.dirname(__file__), "feedback_samples.json"),
)

_feedback_file_lock = threading.Lock()


def _resolve_feedback_file() -> str:
    return os.getenv("FEEDBACK_SAMPLES_FILE", FEEDBACK_FILE)


def _atomic_write_json_list(filepath: str, data: List[Dict[str, Any]]) -> None:
    dir_name = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix="fb_tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        with open(tmp_path, "r", encoding="utf-8") as f:
            json.load(f)
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def _quarantine_legacy_samples(samples: List[Any]) -> List[Dict[str, Any]]:
    """Preserve legacy samples while tagging unverified records against SQLite flywheel_feedback_samples."""
    from app.db_manager import get_db_cursor

    verified_keys = set()
    try:
        with get_db_cursor() as cur:
            has_tbl = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='flywheel_feedback_samples'"
            ).fetchone()
            if has_tbl:
                for row in cur.execute(
                    """
                    SELECT client_event_id, actor_user_id, actor_account_epoch,
                           document_id, sheet_id, source_row_id
                    FROM flywheel_feedback_samples
                    WHERE trust_level = 'trusted'
                    """
                ).fetchall():
                    verified_keys.add((
                        str(row["client_event_id"] or ""),
                        int(row["actor_user_id"]) if row["actor_user_id"] is not None else None,
                        str(row["actor_account_epoch"] or ""),
                        str(row["document_id"] or ""),
                        str(row["sheet_id"] or ""),
                        str(row["source_row_id"] or ""),
                    ))
    except Exception:
        verified_keys = set()

    normalized: List[Dict[str, Any]] = []
    for item in samples:
        if not isinstance(item, dict):
            continue
        rec = dict(item)
        uid_val = rec.get("actor_user_id")
        try:
            uid_int = int(uid_val) if uid_val is not None and not isinstance(uid_val, bool) else None
        except (TypeError, ValueError):
            uid_int = None
        key = (
            str(rec.get("client_event_id") or ""),
            uid_int,
            str(rec.get("actor_account_epoch") or "").strip(),
            str(rec.get("document_id") or "").strip(),
            str(rec.get("sheet_id") or "").strip(),
            str(rec.get("source_row_id") or "").strip(),
        )
        has_identity = (
            rec.get("trust_level") == "trusted"
            and key[0] != ""
            and key[1] is not None
            and all(bool(part) for part in key[2:])
            and key in verified_keys
        )
        if not has_identity:
            rec["trust_level"] = "legacy_untrusted"
            rec["legacy_reason"] = (
                "unverified_in_server_feedback_db"
                if rec.get("client_event_id")
                else "missing_verified_actor_or_document_identity"
            )
        normalized.append(rec)
    return normalized


def sync_trusted_feedback_file(sample_record: Dict[str, Any]) -> None:
    """Atomically append/supersede a server-verified feedback sample in FEEDBACK_FILE."""
    if not isinstance(sample_record, dict) or sample_record.get("trust_level") != "trusted":
        raise ValueError("Refusing to write untrusted feedback sample without verified identity.")

    target_file = _resolve_feedback_file()
    with _feedback_file_lock:
        samples: List[Dict[str, Any]] = []
        if os.path.exists(target_file):
            with open(target_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    samples = _quarantine_legacy_samples(loaded)

        evt_id = str(sample_record.get("client_event_id") or "")
        if any(str(s.get("client_event_id") or "") == evt_id for s in samples):
            return

        if not sample_record.get("superseded"):
            for existing in samples:
                if (
                    existing.get("trust_level") == "trusted"
                    and existing.get("actor_user_id") == sample_record.get("actor_user_id")
                    and existing.get("actor_account_epoch") == sample_record.get("actor_account_epoch")
                    and existing.get("document_id") == sample_record.get("document_id")
                    and existing.get("sheet_id") == sample_record.get("sheet_id")
                    and existing.get("source_row_id") == sample_record.get("source_row_id")
                ):
                    existing["superseded"] = 1

        samples.append(dict(sample_record))
        _atomic_write_json_list(target_file, samples)


def save_feedback_sample(
    sample_data: Dict[str, Any],
    actor_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Save an operator correction feedback sample ONLY after full server identity verification.

    Direct/unauthenticated calls without actor_user are rejected unconditionally.
    Also self-heals JSON sync on idempotent DB replay when the file copy is missing.
    """
    if actor_user is None:
        raise ValueError(
            "Feedback submission requires authenticated actor_user and verified document/sheet/source_row identity."
        )

    from app.feedback_contract import record_feedback_sample_event

    result = record_feedback_sample_event(actor_user, sample_data)
    sample_record = result.get("sample_record")
    if isinstance(sample_record, dict) and sample_record.get("trust_level") == "trusted":
        sync_trusted_feedback_file(sample_record)
    return result


def get_feedback_suggestions() -> List[Dict[str, Any]]:
    """Analyze server-verified, trusted, non-superseded DB feedback samples only."""
    from app.db_manager import get_db_cursor, query_by_code
    from app.flywheel_db import init_flywheel_tables

    init_flywheel_tables()
    samples: List[Dict[str, Any]] = []

    with get_db_cursor() as cur:
        rows = cur.execute(
            """
            SELECT client_event_id, raw_text, auto_code, manual_code, is_correct
            FROM flywheel_feedback_samples
            WHERE trust_level = 'trusted' AND superseded = 0
            ORDER BY id ASC
            """
        ).fetchall()
        for r in rows:
            samples.append({
                "client_event_id": str(r["client_event_id"] or ""),
                "raw_text": r["raw_text"],
                "auto_code": r["auto_code"],
                "manual_code": r["manual_code"],
                "is_correct": bool(r["is_correct"]),
                "trust_level": "trusted",
                "superseded": 0,
            })

    suggestions = []
    mismatch_counts: Dict[tuple, int] = {}

    for s in samples:
        if not s.get("is_correct") and s.get("manual_code") and s.get("auto_code") != s.get("manual_code"):
            raw_text = s.get("raw_text", "")
            manual_code = s.get("manual_code")
            material = query_by_code(manual_code)
            if material:
                std_name = material.get("name", "")
                key = (raw_text, std_name)
                mismatch_counts[key] = mismatch_counts.get(key, 0) + 1

    for (raw, std), count in mismatch_counts.items():
        raw_clean = raw.strip()
        std_clean = std.strip()
        if raw_clean and std_clean and raw_clean != std_clean:
            suggestions.append({
                "suggested_typo": raw_clean[:20],
                "suggested_correction": std_clean,
                "confidence_count": count,
            })

    suggestions.sort(key=lambda x: x["confidence_count"], reverse=True)
    return suggestions
