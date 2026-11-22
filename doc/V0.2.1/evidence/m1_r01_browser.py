"""Run the M1 workbench in real Chrome against a loopback-only, isolated service."""

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
V017 = ROOT / "doc/V0.1.7/evidence"
sys.path[:0] = [str(ROOT), str(V017)]
from m0_r01_benchmark import block_external_network, make_workbook, stage
from m0_r01_build_governance import read_json


BOX_NAME = "核对配电箱"
BOX_SPEC = "AL-411 配电箱 400x300x200"
ASSEMBLY_NAME = "核对配电箱组合"
ASSEMBLY_SPEC = "AL-411"
BOM_NAME = "304薄壁不锈钢管分段组合"
BOM_SPEC = "DN25 D25.4 304"
BOX_CODES = ("CDP-A", "CDP-B", "CDP-C")
PIPE_CODES = ("2.17.05.0628", "2.17.05.0479")
MATERIALS = [
    *((code, BOX_NAME, BOX_SPEC, "个", "强电") for code in BOX_CODES),
    ("CDP-FAN", "核对轴流风机", "风量 3000m³/h 防爆", "台", "暖通"),
    (PIPE_CODES[0], "304薄壁不锈钢管", "DN25，D25.4*1.0", "m", "不锈钢管"),
    (PIPE_CODES[1], "304薄壁不锈钢管（保温）", "DN25，D25.4×1.0", "m", "不锈钢管"),
]


def item(gid, name, spec, unit, professional, category=None):
    return {"id": gid, "name": name, "specification": spec, "unit": unit, "qty": 1,
            "professional": professional, "supply_category": category or professional, "brand": ""}


def workbook_fixture(root, name, prefix, bins, *, assembly=False):
    rows, classes = [], {}
    for group, count in bins:
        for index in range(count):
            gid = f"{prefix}-{group}-{index:04}"
            if group == "safety_missing":
                rows.append(item(gid, "核对轴流风机", "风量 3000m³/h 防爆未知", "台", "暖通"))
            elif group == "D":
                rows.append(item(gid, f"无候选物料 {index}", "待采购", "个", "暖通"))
            else:
                # Keep a small same-name cohort for the explicit batch-rematch exercise.
                name_for_row = BOX_NAME if group == "A" and index < 3 else f"{BOX_NAME} {gid}"
                rows.append(item(gid, name_for_row, BOX_SPEC, "个", "强电"))
            classes[gid] = group
    if assembly:
        rows.append(item("FIX-COMBO", ASSEMBLY_NAME, ASSEMBLY_SPEC, "个", "强电"))
    (root / name).write_bytes(make_workbook(rows))
    return classes


def configure_fixture(root, server):
    import pandas as pd
    from app.db_manager import import_materials_from_excel
    from app.query_preprocessor import prepare_query

    catalogue = root / "synthetic-catalogue.xlsx"
    pd.DataFrame([{"物料编码": code, "物料名称": name, "规格/型号": spec, "计量单位": unit,
                   "物料分类": category, "常用品牌": "", "描述": ""}
                  for code, name, spec, unit, category in MATERIALS]).to_excel(catalogue, index=False, startrow=1)
    import_materials_from_excel(str(catalogue), mode="replace", filename=catalogue.name)
    box = [dict(code=code, name=BOX_NAME, specification=BOX_SPEC, unit="个", category="强电",
                qty_ratio=1) for code in BOX_CODES]
    targets = [dict(code=code, unit="m", qty_ratio=0.5) for code in PIPE_CODES]
    _, bom = server._build_legacy_binding_spec_and_key(BOM_NAME, targets, original_spec=BOM_SPEC, professional="暖通")
    (Path(os.environ["CONFIG_DIR"]) / "auto_binding_rules.json").write_text(json.dumps([
        dict(original_name=ASSEMBLY_NAME, original_spec=ASSEMBLY_SPEC, professional="强电", bound_materials=box),
        dict(original_name=BOM_NAME, original_spec=BOM_SPEC, professional="暖通",
             bound_materials=bom["bound_materials"], bom=bom["bom"]),
    ], ensure_ascii=False), encoding="utf-8")

    bins = [("safety_missing", 10), ("D", 27), ("A", 20), ("B", 32), ("low_score_prefill", 10)]
    classes = workbook_fixture(root, "fixed-100.xlsx", "FIX", bins, assembly=True)
    classes.update(workbook_fixture(root, "keyword-1060.xlsx", "KEY", [
        ("safety_missing", 73), ("D", 477), ("A", 34), ("B", 205), ("low_score_prefill", 271),
    ]))
    (root / "native-bom.xlsx").write_bytes(make_workbook([
        item("REAL-BOM", BOM_NAME, BOM_SPEC, "m", "暖通", "不锈钢管"),
    ]))
    (root / "invalid.xlsx").write_text("isolated invalid XLSX", encoding="utf-8")
    (root / "background-failure.xlsx").write_bytes(make_workbook([
        item("M1-FAIL", BOX_NAME, BOX_SPEC, "个", "强电"),
    ]))
    original = server.batch_match_rows

    def controlled_rows(raw_rows, task_id=None):
        if not task_id:
            return original(raw_rows)
        rows = []
        for raw in raw_rows:
            gid = raw["code"]
            if gid == "M1-FAIL":
                raise RuntimeError("隔离后台失败验收")
            if gid not in classes:
                row, = original([raw], task_id)
                if gid == "FIX-COMBO":
                    # Native BOM v1 supports two segments. The fixed 100-row scenario uses
                    # a real constrained three-part rule and a synthetic M0 BOM source label.
                    assert row["decision_status"] == "auto_accepted" and len(row["bindings"]) == 3
                    row["initial_match"]["source"] = "bom"
                rows.append(row)
                continue
            group = classes[gid]
            safety = group == "safety_missing"
            score = {"safety_missing": 97, "D": 0, "A": 96, "B": 82, "low_score_prefill": 52}[group]
            code, name, spec, unit, category = MATERIALS[3 if safety else 0]
            top = dict(code=code, name=name, specification=spec, unit=unit, category=category, brand="", score=score)
            binding = {key: value for key, value in top.items() if key != "score"}
            binding.update(qty=1, qty_ratio=1)
            tier = "C" if group in ("safety_missing", "low_score_prefill") else group
            legacy = [binding] if score else []
            effective = [] if safety else legacy
            comparisons = [{
                "field": "防爆" if safety else "功率", "state": "missing",
                "missing_side": "query" if safety else "candidate", "query_value": None if safety else "40-50w",
                "safety_critical_unknown": safety, "requested_unverified": not safety,
            }]
            initial = dict(tier=tier, subtype=group if tier == "C" else None, source="matcher",
                           effective_bindings=effective, actual_prefilled=bool(effective),
                           reason_codes=[group if tier == "C" else "no_candidate" if tier == "D" else "priority_review"],
                           ranking_score=score, distinct_margin=14 if tier == "A" else None,
                           competitor_code="CDP-B" if tier == "A" else None,
                           ranking_evidence_status="current" if tier == "A" else "no_comparable_candidate",
                           priority_review_unavailable=group == "B",
                           field_comparisons=comparisons if score else [], professional=raw["professional"])
            status = "recommended"
            parsed = prepare_query(raw["name"], raw["specification"], raw["professional"],
                                   category=raw["category"], unit=raw["unit"])["parsed_spec"]
            row = dict(server._build_row_item_from_raw(raw), original_row_idx=raw["row_idx"],
                       parsed_spec=parsed, top_candidates=[top] if score else [],
                       bindings=legacy, matched_code=code if legacy else "", initial_match=initial,
                       decision_status=status, decision_source="matcher", decision_reason="隔离清单候选")
            rows.append(row)
        return rows

    return controlled_rows


def run(chrome, out):
    out.mkdir(parents=True, exist_ok=True)
    dispatch = read_json(ROOT / "doc/V0.2.1/evidence/m1_r01_dispatch_baseline.json")
    os.environ.update(dispatch["default_switches_frozen_off"])
    with TemporaryDirectory(prefix="v021_m1_browser_", ignore_cleanup_errors=True) as directory:
        root = Path(directory).resolve()
        stage(root / "service", "keyword", read_json(V017 / "m0_r01_before.json"))
        sys.addaudithook(block_external_network)
        import uvicorn
        from app import server
        from app.auth_manager import get_auth_connection, get_password_hash, get_user_by_username, issue_token_for_user

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            service = uvicorn.Server(uvicorn.Config(server.app, lifespan="on", log_level="warning"))
            thread = threading.Thread(target=service.run, kwargs={"sockets": [listener]}, daemon=True)
            thread.start()
            try:
                until = time.monotonic() + 50
                while not service.started and thread.is_alive() and time.monotonic() < until:
                    time.sleep(0.025)
                if not service.started:
                    raise RuntimeError("Isolated browser service did not start")
                handler = configure_fixture(root, server)
                with get_auth_connection() as db:
                    db.execute("""INSERT INTO users
                        (username,password_hash,name,role,is_active,must_change_password,account_epoch)
                        VALUES ('v021-browser',?,'浏览器测试','employee',1,0,'m1-fixture')""",
                               (get_password_hash(secrets.token_urlsafe(24)),))
                token = issue_token_for_user(get_user_by_username("v021-browser"))
                with patch.object(server, "batch_match_rows", handler):
                    subprocess.run(
                        ["node", str(Path(__file__).with_suffix(".mjs")), str(chrome),
                         f"http://127.0.0.1:{port}", str(root), str(out)],
                        env={**os.environ, "V021_M1_TOKEN": token}, check=True,
                    )
            finally:
                service.should_exit = True
                thread.join(timeout=20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", type=Path, default=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.chrome, args.out.resolve())
