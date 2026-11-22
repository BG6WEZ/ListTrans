"""Source-row decisions and the priority review feedback outbox."""

import hashlib
import json
import logging
import math
import sqlite3
from contextlib import closing, contextmanager

from app.acceptance_calibration import _physical_evidence
from app.auth_manager import get_auth_connection
from app.binding_decisions import (
    SOURCE_MANUAL_EDIT,
    STATUS_MANUAL_CONFIRMED,
    STATUS_RECOMMENDED,
    STATUS_REJECTED,
    _enrich_saved_bindings,
    binding_fingerprint,
    get_unit_multiplier,
    normalize_bindings,
    row_content_fingerprint,
    validate_bindings_against_row,
)
from app.feedback_contract import FeedbackContractError, verify_row_ownership
from app.flywheel_db import open_flywheel_tx_connection
from app.query_preprocessor import prepare_query


IDENTITY = ("document_id", "sheet_id", "source_row_id")
SOURCE_PRIORITY_REVIEW = "priority_review"
_ROW_FIELDS = (
    "name", "spec", "qty", "unit", "loss", "category", "brand", "code",
)
_KEY_FILTER = "user_id = ? AND account_epoch = ? AND document_id = ? AND sheet_id = ? AND source_row_id = ?"
logger = logging.getLogger(__name__)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def scoped_binding_fingerprint(bindings):
    # The legacy fingerprint deliberately omitted target units. New receipts bind them.
    units = sorted((str(b["code"]).strip(), str(b.get("unit") or "").strip()) for b in bindings)
    return _digest([binding_fingerprint(bindings), units])


@contextmanager
def source_write():
    with closing(get_auth_connection()) as conn, conn:
        # Callers take the catalog lock first. SQLite serializes auth writes across processes.
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def resolve_source(materials, actor, row, filename, *, document_id=None):
    ids = tuple(str(row.get(field) or "").strip() for field in IDENTITY)
    epoch = str(actor.get("account_epoch") or "")
    owner = (actor["id"], epoch)
    if not any(ids) and not document_id:
        if epoch and materials.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='flywheel_documents'"
        ).fetchone():
            # An older client may omit the complete lineage. Never look up legacy
            # credentials for a registered upload or an unchanged registered row.
            registered = materials.execute("""
                SELECT 1 FROM flywheel_documents d
                JOIN flywheel_source_rows r ON r.document_id = d.document_id
                WHERE d.actor_user_id = ? AND d.actor_account_epoch = ?
                  AND ((? <> '' AND d.original_filename = ?)
                       OR (r.raw_name = ? AND r.raw_spec = ? AND r.raw_qty = ?
                           AND r.raw_unit = ? AND r.professional = ?))
                LIMIT 1
            """, (*owner, filename, filename, row.get("original_name"), row.get("original_spec") or "",
                  row.get("original_qty"), row.get("original_unit") or "", row.get("professional") or "暖通")).fetchone()
            if registered:
                raise FeedbackContractError(422, "source_identity_required", "已登记清单须提供服务端源行身份。")
        return None
    if document_id and ids[0] != document_id:
        raise FeedbackContractError(422, "source_identity_required", "源行缺少当前文档身份或属于另一文档。")
    doc, sheet, source = verify_row_ownership(materials.cursor(), *owner, *ids)
    if row.get("original_row_idx") != source["display_row_idx"]:
        raise FeedbackContractError(422, "source_row_mismatch", "清单行号与服务端源行不一致。")
    snapshot = materials.execute("""
        SELECT revision, is_initial, initial_match_json, decision_status FROM flywheel_recommendation_snapshots
        WHERE document_id = ? AND sheet_id = ? AND source_row_id = ? ORDER BY revision DESC LIMIT 1
    """, ids).fetchone()
    if not snapshot:
        raise FeedbackContractError(422, "source_revision_missing", "源行缺少服务端推荐版本。")
    original = {f"original_{field}": source[f"raw_{field}"] for field in _ROW_FIELDS}
    original["professional"] = source["professional"]
    return {
        "key": (*owner, doc["document_id"], sheet["sheet_id"], source["source_row_id"]),
        "revision": snapshot["revision"],
        "initial": json.loads(snapshot["initial_match_json"] or "null") if snapshot["is_initial"] else None,
        "initial_decision": snapshot["decision_status"],
        "original_fingerprint": row_content_fingerprint(original),
    }


def source_decision(auth, ctx):
    record = auth.execute(f"SELECT * FROM source_binding_decisions WHERE {_KEY_FILTER}", ctx["key"]).fetchone()
    return dict(record) if record else None


def source_version(record):
    return record["version"] if record else 0


def check_revision(ctx, row):
    if row.get("recommendation_revision") != ctx["revision"]:
        raise FeedbackContractError(409, "source_revision_conflict", "推荐版本已变化，请重新载入该行。")


def check_source_write(ctx, record, row, *, editing=False):
    check_revision(ctx, row)
    if (row.get("decision_version") or 0) != source_version(record):
        raise FeedbackContractError(409, "source_decision_conflict", "该行决定已变化，请重新载入该行。")
    expected = record["row_fingerprint"] if record else ctx["original_fingerprint"]
    if not editing and row_content_fingerprint(row) != expected:
        raise FeedbackContractError(409, "source_content_conflict", "行内容已变化，请先登记编辑。")


def write_source_decision(auth, ctx, record, row, bindings, decision, source, reason):
    version = source_version(record) + 1
    row_fp = row_content_fingerprint(row)
    binding_fp = scoped_binding_fingerprint(bindings)
    payload = _json({"row": row, "bindings": _enrich_saved_bindings(row, bindings)})
    auth.execute("""
        INSERT INTO source_binding_decisions
        (user_id, account_epoch, document_id, sheet_id, source_row_id, version, revision,
         row_fingerprint, binding_fingerprint, decision, source, reason, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (user_id, account_epoch, document_id, sheet_id, source_row_id)
        DO UPDATE SET version=excluded.version, revision=excluded.revision,
          row_fingerprint=excluded.row_fingerprint, binding_fingerprint=excluded.binding_fingerprint,
          decision=excluded.decision, source=excluded.source, reason=excluded.reason, payload_json=excluded.payload_json
    """, (*ctx["key"], version, ctx["revision"], row_fp, binding_fp, decision, source, reason, payload))
    return {"decision_version": version, "row_fingerprint": row_fp, "binding_fingerprint": binding_fp}


def invalidate_source(auth, ctx, record, row):
    if record and record["decision"] == STATUS_REJECTED:
        return write_source_decision(auth, ctx, record, row, [], STATUS_REJECTED, record["source"], record["reason"])
    return write_source_decision(
        auth, ctx, record, row, [], STATUS_RECOMMENDED, SOURCE_MANUAL_EDIT, "内容已修改，请重新确认",
    )


def confirmed_source(record, ctx, row, bindings):
    return (record and record["decision"] == STATUS_MANUAL_CONFIRMED
            and record["revision"] == ctx["revision"]
            and record["row_fingerprint"] == row_content_fingerprint(row)
            and record["binding_fingerprint"] == scoped_binding_fingerprint(bindings))


def priority_eligibility(ctx, record, row, bindings):
    """Assess the frozen first choice against the current catalog and row state."""
    def blocked(reason):
        return {"eligible": False, "reason": reason}

    initial = ctx["initial"]
    if ctx["revision"] != 1 or row.get("recommendation_revision") != ctx["revision"]:
        return blocked("recommendation_changed")
    if record:
        return blocked("decision_changed")
    if not initial or initial["tier"] != "A":
        return blocked("not_priority")
    if initial["source"] != "matcher":
        return blocked("rule_or_bom")
    if ctx["initial_decision"] != STATUS_RECOMMENDED:
        return blocked("decision_changed")
    if initial["uncapped"] is not True:
        return blocked("score_capped")
    margin = initial.get("distinct_margin")
    if (initial["ranking_evidence_status"] != "current" or not initial.get("competitor_code")
            or margin is None or margin < 10):
        return blocked("no_distinct_margin")
    baseline = normalize_bindings(initial["effective_bindings"], row["original_qty"])
    if (len(bindings) != 1 or row_content_fingerprint(row) != ctx["original_fingerprint"]
            or scoped_binding_fingerprint(bindings) != scoped_binding_fingerprint(baseline)):
        return blocked("choice_changed")
    binding = bindings[0]
    if (not binding["code"].strip() or not math.isfinite(binding["qty"]) or binding["qty"] <= 0
            or binding["qty_ratio"] != 1 or not math.isclose(binding["qty"], row["original_qty"])):
        return blocked("invalid_quantity")

    checked = validate_bindings_against_row(row, bindings)
    if checked["invalid_codes"]:
        return blocked("invalid_code")
    if checked["conflicts"]:
        return blocked("hard_conflict")
    live = checked["live_materials"][binding["code"].strip()]
    if not row.get("original_unit") or not live["unit"] or get_unit_multiplier(row["original_unit"], live["unit"]) != 1:
        return blocked("unit_unverified")
    if get_unit_multiplier(binding.get("unit") or "", live["unit"]) != 1:
        return blocked("unit_changed")
    query = prepare_query(
        row["original_name"], row["original_spec"], row["professional"],
        category=row.get("original_category"), brand=row.get("original_brand"), unit=row["original_unit"],
    )
    fields = _physical_evidence(row, query, live)["comparisons"]
    all_fields = fields + initial.get("field_comparisons", [])
    if any(field["safety_critical_unknown"] for field in all_fields):
        return blocked("safety_unverified")
    if any(field["requested_unverified"] for field in all_fields):
        return blocked("requested_unverified")
    missing = [field for field in fields if field["state"] == "missing"]
    return {
        "eligible": True, "reason": "review_missing_fields" if missing else "eligible",
        "missing": missing,
        "catalog_spec": live.get("specification") or "",
        "catalog_name": live.get("name") or "",
        "idempotency_key": priority_key(ctx, row, bindings),
    }


def priority_key(ctx, row, bindings):
    return _digest([ctx["key"], ctx["revision"], row_content_fingerprint(row), scoped_binding_fingerprint(bindings)])


def receipt_for(auth, ctx, key):
    return auth.execute("""
        SELECT payload_digest, receipt_json FROM priority_binding_receipts
        WHERE user_id = ? AND account_epoch = ? AND idempotency_key = ?
    """, (*ctx["key"][:2], key)).fetchone()


def commit_priority(auth, ctx, row, bindings, canonical, key, batch_id, result):
    receipt = {
        "batch_request_id": batch_id, "source_row_id": ctx["key"][-1],
        "recommendation_revision": ctx["revision"], "idempotency_key": key,
        "event_id": "priority_" + key, **result,
    }
    auth.execute("INSERT INTO priority_binding_receipts VALUES (?, ?, ?, ?, ?, ?)",
                 (*ctx["key"][:2], key, batch_id, priority_key(ctx, row, bindings), _json(receipt)))
    event = {
        "source": SOURCE_PRIORITY_REVIEW, "document_id": ctx["key"][2], "sheet_id": ctx["key"][3],
        "source_row_id": ctx["key"][4], "revision": ctx["revision"],
        "targets": [{"code": b["code"], "qty": b["qty"], "unit": b["unit"]} for b in canonical],
    }
    auth.execute("INSERT INTO priority_binding_outbox VALUES (?, ?, ?, ?)",
                 (receipt["event_id"], *ctx["key"][:2], _json(event)))
    return receipt


def deliver_priority_feedback(limit=200):
    """A crash after the materials commit leaves a safely replayable auth outbox record."""
    try:
        with closing(get_auth_connection()) as auth:
            pending = auth.execute("""
                SELECT event_id, payload_json FROM priority_binding_outbox ORDER BY rowid LIMIT ?
            """, (limit,)).fetchall()
            if not pending:
                return True
            with closing(open_flywheel_tx_connection()) as materials, materials:
                materials.execute("BEGIN IMMEDIATE")
                materials.executemany("""
                    INSERT INTO flywheel_priority_confirmation_events (event_id, source, payload_json)
                    VALUES (?, 'priority_review', ?) ON CONFLICT(event_id) DO NOTHING
                """, pending)
            with auth:
                auth.executemany("DELETE FROM priority_binding_outbox WHERE event_id = ?",
                                 [(event["event_id"],) for event in pending])
            return auth.execute("SELECT 1 FROM priority_binding_outbox LIMIT 1").fetchone() is None
    except (OSError, sqlite3.Error) as exc:
        logger.warning("优先核对反馈待重投 (%s)", type(exc).__name__)
        return False
