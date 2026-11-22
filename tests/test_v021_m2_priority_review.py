"""Priority review through the real API with isolated stores and synthetic first recommendations."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import io
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook


def initial(binding, *, requested=False):
    return {
        "tier": "A", "source": "matcher", "effective_bindings": [binding], "actual_prefilled": True,
        "uncapped": True, "distinct_margin": 14, "competitor_code": "M2-RIVAL", "ranking_evidence_status": "current",
        "field_comparisons": [{
            "field": "功率", "state": "missing", "missing_side": "candidate",
            "requested_unverified": True, "safety_critical_unknown": False,
        }] if requested else [],
    }


@pytest.fixture
def api(monkeypatch, tmp_path):
    from app import auth_manager, db_manager, server
    from app.feedback_contract import persist_upload_document_tree
    from app.flywheel_db import init_flywheel_tables
    from app.session_manager import init_session_db
    from app.audit_manager import init_audit_tables

    monkeypatch.setattr(auth_manager, "AUTH_DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "materials.db"))
    monkeypatch.setattr(server, "BINDING_FEEDBACK_FILE", str(tmp_path / "binding_feedback.json"))
    monkeypatch.setattr(server, "UPLOAD_DIR", str(tmp_path))
    auth_manager.init_auth_db()
    init_session_db()
    init_audit_tables()
    db_manager.init_db()
    init_flywheel_tables()
    actors = {}
    for i in (1, 2):
        first = f"m2-fixture-{i}"
        with closing(auth_manager.get_auth_connection()) as conn, conn:
            conn.execute("""
                INSERT INTO users (username, password_hash, role, is_active, must_change_password, account_epoch)
                VALUES (?, 'synthetic', 'employee', 1, 0, ?)
            """, (first, first))
        actor = auth_manager.get_user_by_username(first)
        actors[i] = actor, {"Authorization": f"Bearer {auth_manager.issue_token_for_user(actor)}"}

    client = TestClient(server.app)
    with closing(db_manager.get_db_connection()) as conn, conn:
        for code, name, spec, category in (
            ("M2-CLEAN", "核对配电箱", "AZ-421 配电箱 400x300x200", "强电"),
            ("M2-MISSING", "核对专用支架", "AZ-421", "其它"),
            ("M2-RIVAL", "核对配电箱", "AZ-422 配电箱 400x300x200", "强电"),
        ):
            conn.execute("""
                INSERT INTO materials(code, name, specification, unit, category, brand, description, search_tokens)
                VALUES (?, ?, ?, '个', ?, '', '', '')
            """, (code, name, spec, category))

    def upload(*codes, filename="same.xlsx", requested=False):
        rows = []
        for index, code in enumerate(codes, 1):
            name, spec = (
                ("核对配电箱", "AZ-421 配电箱 400x300x200") if code == "M2-CLEAN" else ("核对专用支架", "AZ-421")
            )
            binding = {"code": code, "name": name, "specification": spec, "unit": "个",
                       "category": "强电", "brand": "", "qty": 3, "qty_ratio": 1}
            row = {
                "original_row_idx": index, "original_name": name, "original_spec": spec,
                "original_qty": 3, "original_unit": "个", "original_loss": 1,
                "original_brand": "", "original_category": "", "original_code": "",
                "professional": "强电", "scope_filename": filename, "decision_status": "recommended",
                "bindings": [binding], "initial_match": initial(binding, requested=requested),
                "matched_code": code, "matched_name": binding["name"], "matched_spec": binding["specification"],
                "matched_unit": "个", "matched_category": binding["category"], "matched_brand": "",
            }
            rows.append(row)
        uid = uuid4().hex
        persist_upload_document_tree(actors[1][0], "doc_" + uid, "task_" + uid, "work_" + uid,
                                     filename, uid, [{} for _ in rows], rows)
        return rows

    def post(path, payload, actor=1):
        return client.post(path, headers=actors[actor][1], json=payload)

    def get(path):
        return client.get(path, headers=actors[1][1])

    return upload, post, tmp_path, get


def preflight(post, *rows):
    response = post("/api/binding/priority", {"rows": list(rows)})
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def prepared(post, row):
    result, = preflight(post, row)
    assert result["eligible"], result
    return {**row, "idempotency_key": result["idempotency_key"], "reviewed_missing": bool(result["missing"])}


def submit(post, *rows, batch_id="synthetic-fixture"):
    response = post("/api/binding/priority/submit", {"batch_request_id": batch_id, "rows": list(rows)})
    assert response.status_code == 200, response.text
    return response.json()


def export(post, item):
    return post("/api/export", {"items": [item], "scope_filename": item["scope_filename"]})


def restore(post, *rows):
    response = post("/api/session/annotate", {"items": list(rows), "scope_filename": rows[0]["scope_filename"]})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def with_receipt(row, result):
    receipt = result["receipt"]
    return {**row, "bindings": receipt["bindings"], "decision_version": receipt["decision_version"]}


def test_priority_export_xlsx_duplicate_rows_and_same_filename(api):
    upload, post, _, _ = api
    first, second = upload("M2-CLEAN", "M2-CLEAN")
    retransmitted, = upload("M2-CLEAN")
    assert [export(post, row).status_code for row in (first, second, retransmitted)] == [422, 422, 422]
    entry = prepared(post, first)
    assert not entry["reviewed_missing"]
    result, = submit(post, entry)["rows"]
    assert result["ok"], result
    first = with_receipt(first, result)
    assert [row["decision_status"] for row in restore(post, first, second, retransmitted)] == [
        "manual_confirmed", "recommended", "recommended",
    ]
    report = export(post, first)
    assert report.status_code == 200, report.text
    workbook = load_workbook(io.BytesIO(report.content), read_only=True)
    material_rows = [cells for cells in workbook.active.values if len(cells) > 8 and cells[4] == "M2-CLEAN"]
    assert len(material_rows) == 1
    assert (material_rows[0][4], material_rows[0][8]) == ("M2-CLEAN", 3)
    workbook.close()
    rejected = post("/api/binding/reject", second)
    assert rejected.status_code == 200, rejected.text
    assert export(post, first).status_code == 200
    assert restore(post, second)[0]["decision_status"] == "rejected"
    assert restore(post, retransmitted)[0]["decision_status"] == "recommended"
    edit = post("/api/binding/invalidate", first)
    assert edit.status_code == 200, edit.text
    assert export(post, first).status_code == 422
    assert restore(post, second)[0]["decision_status"] == "rejected"
    assert prepared(post, retransmitted)


def test_priority_requested_unverified_and_ordinary_missing(api):
    upload, post, _, _ = api
    missing, = upload("M2-MISSING")
    unverified, = upload("M2-MISSING", requested=True)
    eligible, blocked = preflight(post, missing, unverified)
    assert eligible["eligible"] and any(f["missing_side"] == "candidate" for f in eligible["missing"]), eligible
    assert blocked["reason"] == "requested_unverified", blocked
    entry = {**missing, "idempotency_key": eligible["idempotency_key"]}
    assert submit(post, entry)["rows"][0]["reason"] == "missing_review_required"
    assert submit(post, {**entry, "reviewed_missing": True})["rows"][0]["ok"]
    assert submit(post, {**entry, **unverified, "reviewed_missing": True})["rows"][0]["reason"] == "idempotency_conflict"


def test_priority_scoped_forgery_omission_and_epoch(api):
    upload, post, _, _ = api
    row, = upload("M2-CLEAN")
    candidate = prepared(post, row)
    assert post("/api/binding/submit", row, actor=2).status_code == 404
    assert post("/api/binding/reject", {**row, "source_row_id": "row_forged"}).status_code == 422
    absent = {k: v for k, v in row.items() if k not in {"document_id", "sheet_id", "source_row_id"}}
    assert post("/api/binding/submit", absent).status_code == 422
    assert export(post, absent).status_code == 422
    assert post("/api/binding/priority/submit", {"rows": [candidate], "batch_request_id": "foreign"},
                actor=2).json()["rows"][0]["reason"] == "document_not_found_or_forbidden"
    assert post("/api/binding/invalidate", {**row, "sheet_id": ""}).status_code == 422


def test_priority_partial_retry_returns_original_but_never_reactivates(api):
    upload, post, _, _ = api
    one, two = upload("M2-CLEAN", "M2-CLEAN")
    prepared_one, prepared_two = prepared(post, one), prepared(post, two)
    bad = {**prepared_two, "bindings": [{**two["bindings"][0], "qty": 8}]}
    first, failed = submit(post, prepared_one, bad)["rows"]
    assert first["ok"] and not failed["ok"]
    replayed, corrected = submit(post, prepared_one, prepared_two, batch_id="different-batch")["rows"]
    assert replayed["ok"] and replayed["replayed"] and replayed["receipt"] == first["receipt"]
    assert corrected["ok"]
    assert post("/api/binding/invalidate", with_receipt(one, first)).status_code == 200
    inactive = submit(post, prepared_one)["rows"][0]
    assert inactive["reason"] == "confirmation_inactive" and inactive["receipt"] == first["receipt"]
    assert export(post, one).status_code == 422
    assert export(post, with_receipt(two, corrected)).status_code == 200


def test_priority_concurrent_edit_or_rematch_conflicts(api):
    upload, post, _, _ = api
    row, = upload("M2-CLEAN")
    entry = prepared(post, row)
    with ThreadPoolExecutor(max_workers=2) as pool:
        batch = pool.submit(submit, post, entry)
        edit = pool.submit(post, "/api/binding/invalidate", row)
    confirmed = batch.result()["rows"][0]["ok"]
    assert edit.result().status_code == (409 if confirmed else 200)
    if confirmed:
        reviewed = with_receipt(row, batch.result()["rows"][0])
        assert post("/api/binding/invalidate", reviewed).status_code == 200
    assert export(post, row).status_code == 422

    row, = upload("M2-CLEAN")
    entry = prepared(post, row)
    rematch = post("/api/rematch", {**row, "row_id": row["original_row_idx"], "text": row["original_spec"],
                                   "fixed_params": {}})
    assert rematch.status_code == 200, rematch.text
    assert submit(post, entry)["rows"][0]["reason"] == "source_revision_conflict"


def test_priority_outbox_recovery_and_learning_isolation(api):
    upload, post, tmp, get = api
    from app import auth_manager, db_manager
    from app.feedback_train import get_feedback_suggestions
    from app.feedback_miner import FlywheelMiner

    row, = upload("M2-CLEAN")
    entry = prepared(post, row)
    feedback = tmp / "binding_feedback.json"
    feedback.write_text("[]", encoding="utf-8")
    previous = get("/api/binding/suggestions").json()
    with closing(db_manager.get_db_connection()) as db, db:
        db.execute("""
            CREATE TRIGGER m2_fail_delivery BEFORE INSERT ON flywheel_priority_confirmation_events
            BEGIN SELECT RAISE(ABORT, 'isolated delivery failure'); END
        """)
    first = submit(post, entry)
    receipt = first["rows"][0]
    assert receipt["ok"] and not first["feedback_delivery_ok"]
    assert export(post, with_receipt(row, receipt)).status_code == 200
    with closing(auth_manager.get_auth_connection()) as db:
        pending = tuple(db.execute("SELECT * FROM priority_binding_outbox").fetchone())
    with closing(db_manager.get_db_connection()) as db, db:
        db.execute("DROP TRIGGER m2_fail_delivery")
    assert submit(post, entry)["feedback_delivery_ok"]
    with closing(auth_manager.get_auth_connection()) as db, db:
        assert db.execute("SELECT COUNT(*) FROM priority_binding_outbox").fetchone()[0] == 0
        db.execute("INSERT INTO priority_binding_outbox VALUES (?,?,?,?)", pending)
    assert submit(post, entry)["feedback_delivery_ok"]
    with closing(db_manager.get_db_connection()) as db:
        assert db.execute("SELECT source FROM flywheel_priority_confirmation_events").fetchone()[0] == "priority_review"
        assert db.execute("SELECT COUNT(*) FROM flywheel_priority_confirmation_events").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM flywheel_records").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM flywheel_feedback_samples").fetchone()[0] == 0
    assert feedback.read_bytes() == b"[]"
    assert get("/api/binding/suggestions").json() == previous
    assert get_feedback_suggestions() == []
    result = FlywheelMiner().run_mining()
    assert result["processed_records_count"] == result["analyzed_patterns_count"] == 0


def test_auth_write_failure_rolls_back_decisions_and_receipts(api):
    from app.auth_manager import get_auth_connection
    from sqlite3 import IntegrityError

    upload, post, _, _ = api
    first, second = upload("M2-CLEAN", "M2-CLEAN")
    entries = [prepared(post, first), prepared(post, second)]
    with closing(get_auth_connection()) as auth, auth:
        auth.execute("""
            CREATE TRIGGER m2_fail_second_outbox BEFORE INSERT ON priority_binding_outbox
            WHEN (SELECT COUNT(*) FROM priority_binding_outbox) > 0
            BEGIN SELECT RAISE(ABORT, 'isolated auth failure'); END
        """)
    with pytest.raises(IntegrityError, match="isolated auth failure"):
        submit(post, *entries)
    with closing(get_auth_connection()) as auth, auth:
        for table in ("source_binding_decisions", "priority_binding_receipts", "priority_binding_outbox"):
            assert auth.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        auth.execute("DROP TRIGGER m2_fail_second_outbox")
    assert [r["ok"] for r in submit(post, *entries)["rows"]] == [True, True]


def test_scoped_restore_does_not_reuse_old_filename_credential(api):
    from app.auth_manager import get_user_by_username
    from app.binding_decisions import make_scope_key, normalize_bindings, record_decision

    upload, post, _, get = api
    row, = upload("M2-CLEAN")
    actor = get_user_by_username("m2-fixture-1")
    scope = make_scope_key(row["scope_filename"], row["professional"])
    record_decision(actor["id"], scope, row, normalize_bindings(row["bindings"], row["original_qty"]),
                    "manual_confirmed", "manual")
    assert export(post, row).status_code == 422
    assert restore(post, row)[0]["decision_status"] == "recommended"
    save = post("/api/session/draft", {
        "original_filename": row["scope_filename"], "professional": "强电",
        "document_id": row["document_id"], "work_session_id": row["work_session_id"],
        "items_json": json.dumps([{**row, "decision_status": "manual_confirmed"}]),
    })
    assert save.status_code == 200, save.text
    draft = get("/api/session/draft").json()["draft"]
    assert json.loads(draft["items_json"])[0]["decision_status"] == "recommended"
