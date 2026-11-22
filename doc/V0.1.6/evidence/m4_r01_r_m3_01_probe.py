"""Dedicated real offline diagnosis of the three frozen unknown-explosion cases."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    evidence = Path(__file__).resolve().parent
    project = evidence.parents[2]
    root = Path(tempfile.mkdtemp(prefix="listtrans_m4_unknown_")).resolve()
    spec = importlib.util.spec_from_file_location("m4_unknown_environment", evidence / "m4_r01_3rule_gate.py")
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    helper._setup_env(root)
    sys.path.insert(0, str(project))
    import pandas as pd
    from app.db_manager import get_all_materials, import_materials_from_excel, init_db
    from app.flywheel_db import init_flywheel_tables
    from app.query_preprocessor import prepare_query
    from app.matcher import match_item, detect_three_state_flag
    from app.server import batch_match_rows, _gate_export_row, HTTPException
    from app.vector_index import index_manager
    init_db(); init_flywheel_tables()
    mats = json.loads((project / "tests/fixtures/sandbox_materials_v1.json").read_bytes())["materials"]
    seed = root / "uploads/unknown_materials.xlsx"
    pd.DataFrame([{"物料分类": m["category"], "物料编码": m["code"], "物料名称": m["name"], "规格/型号": m["spec"],
                   "计量单位": "台", "常用品牌": "", "描述": ""} for m in mats]).to_excel(seed, index=False, startrow=1)
    import_materials_from_excel(str(seed), mode="replace", filename=seed.name)
    assert index_manager.is_enabled
    index_manager.build_index(get_all_materials(limit=10**9)); index_manager.save_cache()
    golden_path = project / "tests/fixtures/golden_set_v1.json"
    assert hashlib.sha256(golden_path.read_bytes()).hexdigest() == os.environ["GOLDEN_SET_SHA256"]
    items = [x for x in json.loads(golden_path.read_bytes())["items"] if x["id"] in ("GT-0674", "GT-0677", "GT-0678")]
    decisions = batch_match_rows([{"row_idx": i, "name": it["name"], "specification": it["specification"], "professional": it["professional"],
                                  "category": it.get("supply_category", ""), "brand": "", "unit": it.get("unit", "台"), "qty": 1., "loss": 0.}
                                 for i, it in enumerate(items)])
    report = {}
    for it, decision in zip(items, decisions):
        query = prepare_query(it["name"], it["specification"], it["professional"], category=it.get("supply_category", ""), unit=it.get("unit", "台"), preserve_original=False)
        trace = match_item(query, diagnostic_targets=it["target_codes"])["diagnostics"]
        target = trace["targets"][0]
        assert target["recalled"] and not target["hard_blocked"] and abs(target["margin"]-.1) < 1e-6, target
        assert detect_three_state_flag(query["name"] + " " + query["specification"], ["防爆", "防爆型", "防爆风机", "EX"]) is None
        assert not isinstance(query["parsed_spec"]["attrs"].get("防爆"), bool)
        assert decision["decision_status"] == "recommended"
        trace["decision"] = {key: decision.get(key) for key in ("decision_status", "decision_source", "decision_reason", "bindings")}
        trace["query_explosion_state"] = None
        try:
            _gate_export_row(decision, {"id": 2147483000}, "m4_unknown_export.xlsx")
        except HTTPException as exc:
            assert exc.status_code == 422 and exc.detail["error"] == "export_blocked"
            trace["export_gate"] = {"http_status": exc.status_code, "detail": exc.detail}
        else:
            raise AssertionError("Unconfirmed recommendation became exportable")
        report[it["id"]] = trace
    assert len(report) == 3
    with (evidence / "m4_r01_r_m3_01_diagnostics.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: {"T": r["targets"][0]["T"], "W": r["targets"][0]["W"], "margin": r["targets"][0]["margin"],
                            "status": r["decision"]["decision_status"], "export": r["export_gate"]["http_status"]} for key, r in report.items()}))


if __name__ == "__main__":
    main()
