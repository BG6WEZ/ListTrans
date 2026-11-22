# -*- coding: utf-8 -*-
"""
V016-M3 R01 3-Rule Sandbox Regression Gate Runner & Baseline Attribution.
Runs the 3 reference rules (harmful / beneficial / neutral) in an isolated temp root,
records complete results to doc/V0.1.6/evidence/m3_r01_gate_reports.json (never touching m0/m1/m3/m4/m5 files),
and checks exact row-by-row match_item score_comparisons diffs vs prior milestone baselines.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVID_DIR = PROJECT_ROOT / "doc" / "V0.1.6" / "evidence"
PRIOR_V015_EVID_DIR = PROJECT_ROOT / "doc" / "V0.1.5" / "evidence"
OUT_JSON = EVID_DIR / "m3_r01_gate_reports.json"

RULES = {
    "harmful": {
        "suggestion_type": "ALIAS",
        "source_pattern": "排风机",
        "target_value": "低压交联聚乙烯绝缘聚氯乙烯护套电力电缆",
    },
    "beneficial": {
        "suggestion_type": "BINDING",
        "source_pattern": "医用静音型排风机",
        "target_value": [
            {
                "code": "3.50.29.1369",
                "name": "医用离心式排风机(箱）",
                "spec": "风量：1000m³/h，机外余压250pa，镀锌钢板",
                "weight": 1.0,
            }
        ],
    },
    "beneficial_constrained": {
        "suggestion_type": "BINDING",
        "discipline": "暖通",
        "original_spec": "风量：1000m3/h",
        "source_pattern": "医用静音型排风机",
        "target_value": [
            {
                "code": "3.50.29.1369",
                "name": "医用离心式排风机(箱）",
                "spec": "风量：1000m³/h，机外余压250pa，镀锌钢板",
                "weight": 1.0,
            }
        ],
    },
    "neutral": {
        "suggestion_type": "ALIAS",
        "source_pattern": "磷酸脱氧无缝紫铜管",
        "target_value": "磷酸脱氧无缝紫铜管",
    },
}


def _setup_env(root: Path):
    os.environ["TESTING"] = "1"
    os.environ["LLM_ENABLED"] = "false"
    os.environ["LISTTRANS_TEST_ROOT"] = str(root)
    os.environ["MATERIALS_DB_PATH"] = str(root / "db" / "materials.db")
    os.environ["AUTH_DB_PATH"] = str(root / "db" / "auth.db")
    os.environ["CONFIG_DIR"] = str(root / "config")
    os.environ["UPLOAD_DIR"] = str(root / "uploads")
    os.environ["OUTPUT_DIR"] = str(root / "output")
    os.environ["VECTOR_CACHE_DIR"] = str(root / "vector_cache")
    os.environ["LOG_DIR"] = str(root / "logs")
    os.environ.update({
        "BINDING_FEEDBACK_FILE": str(root / "runtime/binding.json"),
        "FEEDBACK_SAMPLES_FILE": str(root / "runtime/samples.json"),
        "GOLDEN_DETAIL_FILE": str(root / "runtime/detail.json"),
        "GOLDEN_SET_FILE": str(PROJECT_ROOT / "tests/fixtures/golden_set_v1.json"),
        "GOLDEN_SET_SHA256": "c8909a21b34fc12fd45ee96d625dec5798f670cdd725988beb99b2f2f216eae5",
        "DASHSCOPE_API_KEY": "", "DEEPSEEK_API_KEY": "", "AUTH_DEV_MODE": "false",
        "PYTHON_DOTENV_DISABLED": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    keys = ("MATERIALS_DB_PATH", "AUTH_DB_PATH", "CONFIG_DIR", "VECTOR_CACHE_DIR", "UPLOAD_DIR", "OUTPUT_DIR",
            "LOG_DIR", "BINDING_FEEDBACK_FILE", "FEEDBACK_SAMPLES_FILE", "GOLDEN_DETAIL_FILE")
    assert all(root.resolve() in Path(os.environ[key]).resolve().parents for key in keys)
    for sub in ("db", "config", "uploads", "output", "vector_cache", "logs", "runtime"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for f in (PROJECT_ROOT / "config").iterdir():
        if f.is_file():
            shutil.copy2(f, root / "config" / f.name)


def _diff_score_comparisons(cur_report: dict, ref_report: dict):
    cur_map = {r["id"]: r for r in cur_report.get("score_comparisons", [])}
    ref_map = {r["id"]: r for r in ref_report.get("score_comparisons", [])}
    diffs = []
    for k in sorted(set(cur_map) | set(ref_map)):
        if k not in cur_map or k not in ref_map:
            diffs.append({"id": k, "diff": "missing_side"})
            continue
        c, r = cur_map[k], ref_map[k]
        for side in ("baseline", "candidate"):
            for fld in ("T", "W", "margin"):
                cv = c[side].get(fld)
                rv = r[side].get(fld)
                if cv != rv and (cv is None or rv is None or abs(float(cv) - float(rv)) > 1e-6):
                    diffs.append({"id": k, "side": side, "field": fld, "cur": cv, "ref": rv})
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the M3 offline three-rule regression gate.")
    parser.add_argument("--out", type=Path, default=OUT_JSON, help="New report file. Existing reports are protected.")
    output = parser.parse_args().out.resolve()
    if output.exists():
        parser.error("output already exists; choose a fresh evidence path with --out")
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    root = Path(tempfile.mkdtemp(prefix="listtrans_m3_gate_"))
    _setup_env(root)

    try:
        import pandas as pd
        from app.db_manager import import_materials_from_excel, init_db
        from app.flywheel_db import init_flywheel_tables
        from app.flywheel_sandbox import get_eval_task_status, start_sandbox_evaluation
        from app.llm_rule_engine import init_rule_engine_db

        init_db()
        init_rule_engine_db()
        init_flywheel_tables()

        with open(PROJECT_ROOT / "tests" / "fixtures" / "sandbox_materials_v1.json", "r", encoding="utf-8") as f:
            mats = json.load(f)["materials"]
        xlsx = root / "uploads" / "sandbox_materials_v1.xlsx"
        pd.DataFrame([
            {
                "物料分类": m["category"], "物料编码": m["code"],
                "物料名称": m["name"], "规格/型号": m["spec"],
                "计量单位": "台", "常用品牌": "", "描述": "",
            }
            for m in mats
        ]).to_excel(xlsx, index=False, startrow=1)
        import_materials_from_excel(str(xlsx), mode="replace", filename="sandbox_materials_v1.xlsx")

        gate_out = {"isolated_root": str(root), "rules": {}, "comparisons": {}}
        for name, sug in RULES.items():
            tid = start_sandbox_evaluation(sug)
            st = None
            for _ in range(240):
                st = get_eval_task_status(tid)
                if st and st.get("status") in ("completed", "failed"):
                    break
                time.sleep(0.5)
            gate_out["rules"][name] = st

        # Check GT-0674/0677/0678 production decision_status = recommended on baseline batch_match_rows
        from app.server import batch_match_rows
        with open(PROJECT_ROOT / "tests" / "fixtures" / "golden_set_v1.json", "r", encoding="utf-8") as f:
            gs_items = json.load(f)["items"]
        probe_ids = ("GT-0674", "GT-0677", "GT-0678")
        probe_items = [x for x in gs_items if x["id"] in probe_ids]
        bm = batch_match_rows([
            {
                "row_idx": i,
                "name": it["name"],
                "specification": it.get("specification", ""),
                "qty": 1.0,
                "unit": it.get("unit", "台"),
                "loss": 0.0,
                "category": it.get("supply_category", ""),
                "brand": "",
                "professional": it.get("professional", "暖通"),
            }
            for i, it in enumerate(probe_items)
        ])
        gate_out["probe_gt_decisions"] = {
            probe_items[i]["id"]: {
                "decision_status": bm[i]["decision_status"],
                "matched_code": bm[i]["matched_code"],
            }
            for i in range(len(probe_items))
        }

        refs = {
            "m2_r01": EVID_DIR / "m2_r01_gate_reports.json",
            "m0_r03": EVID_DIR / "m0_r03_gate_reports.json",
            "m3_r04": PRIOR_V015_EVID_DIR / "m3_r04_gate_reports.json",
            "m4_r03": PRIOR_V015_EVID_DIR / "m4_r03_gate_reports.json",
            "m5_r04": PRIOR_V015_EVID_DIR / "m5_r04_gate_reports.json",
        }
        for ref_name, ref_path in refs.items():
            assert ref_path.is_file(), ref_path
            ref_data = json.loads(ref_path.read_text(encoding="utf-8"))
            ref_rules = ref_data.get("reports") or ref_data.get("rules") or ref_data
            comp = {}
            for k in ("harmful", "beneficial", "neutral"):
                cur_rep = gate_out["rules"][k]["report"]
                ref_rep = ref_rules[k].get("report", ref_rules[k])
                diffs = _diff_score_comparisons(cur_rep, ref_rep)
                comp[k] = {
                    "score_diff_count": len(diffs),
                    "diffs": diffs[:10],
                    "m2_benefit": gate_out["rules"][k]["benefit"],
                    "m2_safety_passed": gate_out["rules"][k]["safety_passed"],
                    "m2_binding_degraded_count": cur_rep.get("binding_degraded_count"),
                    "m2_constrained_benefit": gate_out["rules"]["beneficial_constrained"]["benefit"] if k == "beneficial" else None,
                    "m2_constrained_safety_passed": gate_out["rules"]["beneficial_constrained"]["safety_passed"] if k == "beneficial" else None,
                }
            gate_out["comparisons"][ref_name] = comp

        with output.open("x", encoding="utf-8") as report:
            json.dump(gate_out, report, ensure_ascii=False, indent=2)
        print(json.dumps(gate_out["comparisons"], ensure_ascii=False, indent=2))
        print("GT probes:", json.dumps(gate_out["probe_gt_decisions"], ensure_ascii=False))
        assert all(row["score_diff_count"] == 0 for group in gate_out["comparisons"].values() for row in group.values())
        assert set(gate_out["probe_gt_decisions"]) == set(probe_ids)
        assert all(result["decision_status"] == "recommended" for result in gate_out["probe_gt_decisions"].values())
        assert {k:(row["benefit"],row["safety_passed"]) for k,row in gate_out["rules"].items()} == {
            "harmful": ("harmful",False), "beneficial": ("harmful",False),
            "beneficial_constrained": ("improved",True), "neutral": ("neutral",True),
        }
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
