"""Exercise isolated BOM rule approval, changed-payload refusal and config rollback."""
import argparse
import json
import os
from pathlib import Path
from secrets import token_urlsafe
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "doc/V0.1.6/evidence"), str(ROOT / "doc/V0.1.7/evidence")]
from m4_r01_3rule_gate import _setup_env
from m0_r01_benchmark import block_external_network
from m0_r01_build_governance import read_json, save_json, sha

NAME = "304薄壁不锈钢管分段组合"
CATEGORY = "不锈钢管"
SPEC = "DN25 D25.4 304"
BARE, INSULATED = "2.17.05.0628", "2.17.05.0479"
GOLDEN = {
    "meta": {"version": "v020-m1-bom-synthetic-v1"},
    "items": [
        {"id": "SYN-BOM", "name": NAME, "specification": SPEC, "unit": "千米", "qty": 2,
         "professional": "暖通", "supply_category": CATEGORY, "label_type": "1toN",
         "target_codes": [BARE, INSULATED],
         "bom_expected": [{"code": BARE, "qty": 1200, "qty_ratio": 600, "unit": "m"},
                          {"code": INSULATED, "qty": 800, "qty_ratio": 400, "unit": "m"}]},
        {"id": "SYN-GUARD", "name": "304薄壁不锈钢管", "specification": "DN25，D25.4*1.0",
         "unit": "m", "qty": 1, "professional": "暖通", "supply_category": CATEGORY,
         "label_type": "1to1", "target_codes": [BARE]},
    ],
}


def run(root):
    _setup_env(root)
    sys.addaudithook(block_external_network)
    fixture = root / "runtime" / "bom_labelled.json"
    save_json(fixture, GOLDEN)
    os.environ.update(GOLDEN_SET_FILE=str(fixture), GOLDEN_SET_SHA256=sha(fixture))

    from contextlib import closing
    import pandas as pd
    from fastapi.testclient import TestClient
    from app import server
    from app.audit_manager import init_audit_tables
    from app.auth_manager import (
        get_auth_connection, get_password_hash, get_user_by_username, init_auth_db, issue_token_for_user,
    )
    from app.db_manager import import_materials_from_excel, init_db
    from app.flywheel_db import init_flywheel_tables
    from app.llm_rule_engine import init_rule_engine_db

    init_db()
    init_auth_db()
    init_flywheel_tables()
    init_rule_engine_db()
    init_audit_tables()
    workbook = root / "uploads" / "synthetic_bom_catalog.xlsx"
    pd.DataFrame([
        {"物料分类": CATEGORY, "物料编码": code, "物料名称": name, "规格/型号": spec,
         "计量单位": "m", "常用品牌": "", "描述": ""}
        for code, name, spec in ((BARE, "304薄壁不锈钢管", "DN25，D25.4*1.0"),
                                 (INSULATED, "304薄壁不锈钢管（保温）", "DN25，D25.4×1.0"))
    ]).to_excel(workbook, index=False, startrow=1)
    import_materials_from_excel(str(workbook), mode="replace", filename=workbook.name)
    username = "synthetic-bom-admin"
    with closing(get_auth_connection()) as connection, connection:
        connection.execute("""
            INSERT INTO users(username,password_hash,name,role,is_active,must_change_password,account_epoch)
            VALUES (?,?,?,'admin',1,0,?)
        """, (username, get_password_hash(token_urlsafe(24)), username, username))
    headers = {"Authorization": "Bearer " + issue_token_for_user(get_user_by_username(username))}
    targets = [{"code": code, "name": "", "specification": "", "unit": "m", "brand": "",
                "category": "", "qty": 0, "qty_ratio": share}
               for code, share in ((BARE, .6), (INSULATED, .4))]
    legacy = [{"original_name": NAME, "original_spec": SPEC, "professional": "暖通", "bound_materials": targets}]
    path = Path(server._get_auto_binding_rules_path())
    server.atomic_write_json_config(str(path), legacy)
    archived = path.read_bytes()
    row = {"row_idx": 0, "name": NAME, "specification": SPEC, "unit": "千米", "qty": 2,
           "category": CATEGORY, "professional": "暖通", "brand": "", "loss": 0}
    before, = server.batch_match_rows([row])
    assert before["decision_status"] == "recommended" and not before["bindings"]

    client = TestClient(server.app)
    payload = {"original_name": NAME, "original_spec": SPEC, "professional": "暖通", "bindings": targets}
    queued = client.post("/api/binding/rules/eval", json=payload, headers=headers)
    assert queued.status_code == 200, queued.text
    eval_id = queued.json()["eval_id"]
    deadline = time.monotonic() + 200
    while time.monotonic() < deadline:
        checked = client.get(f"/api/binding/rules/eval/{eval_id}", headers=headers)
        checked.raise_for_status()
        state = checked.json()
        if state.get("status") in {"completed", "failed"}:
            break
        time.sleep(.5)
    else:
        raise TimeoutError("isolated BOM evaluation timed out")
    report = state.get("report") or {}
    assert state["status"] == "completed" and report.get("safety_passed"), {
        key: report.get(key) for key in ("benefit", "safety_passed", "targeted_count", "guard_count",
                                         "binding_degraded_count", "integrity_errors")
    }

    changed = {**payload, "eval_id": eval_id, "bindings": [{**binding, "qty_ratio": .5} for binding in targets]}
    refusal = client.post("/api/binding/rules/confirm", json=changed, headers=headers)
    assert refusal.status_code == 409 and path.read_bytes() == archived, refusal.text
    confirmed = client.post("/api/binding/rules/confirm", json={**payload, "eval_id": eval_id}, headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    active = read_json(path)
    assert len(active) == 1 and active[0]["bom"]["version"] == 1
    expanded, = server.batch_match_rows([row])
    assert expanded["decision_status"] == "auto_accepted"
    assert [(b["code"], b["qty"], b["qty_ratio"], b["unit"]) for b in expanded["bindings"]] == [
        (BARE, 1200, 600, "m"), (INSULATED, 800, 400, "m"),
    ]
    server._persist_binding_rules_with_reload(str(path), active, legacy)
    restored, = server.batch_match_rows([row])
    assert path.read_bytes() == archived and restored["decision_status"] == "recommended" and not restored["bindings"]
    return {
        "evaluation": {key: report.get(key) for key in ("benefit", "safety_passed", "targeted_count", "guard_count",
                                                       "binding_degraded_count", "binding_checks_count")},
        "evaluation_http": queued.status_code, "changed_payload_http": refusal.status_code,
        "confirm_http": confirmed.status_code, "old_and_rolled_back_status": restored["decision_status"],
        "activated_status": expanded["decision_status"], "fixture_sha256": sha(fixture),
        "configuration_rollback_byte_exact": True, "exit_code": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="v020_m1_governed_", ignore_cleanup_errors=True) as directory:
        root = Path(directory).resolve()
        if ROOT == root or ROOT in root.parents:
            raise ValueError("use a temporary root outside the workspace")
        result = run(root)
    save_json(args.out, result)
