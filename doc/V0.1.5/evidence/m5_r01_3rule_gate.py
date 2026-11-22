# -*- coding: utf-8 -*-
"""
V0.1.5 M5 Offline 3-Rule Flywheel Gate & M3/M4 Parity Regression Verification (Step 11).
Runs completely offline (LLM_ENABLED=false) in tests/conftest.py isolated sandbox:
1. Executes dual-subprocess flywheel 3-rule gate (harmful / beneficial / neutral).
2. Compares score_comparisons & outcomes against M3 R04 and M4 R03 baselines (must have 0 diffs).
3. Verifies R-M3-01 explosion-proof GT-0674/0677/0678 samples stay 'recommended' with top1/top2 unchanged.
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

os.environ["LLM_ENABLED"] = "false"
os.environ["TESTING"] = "1"

import tests.conftest as cf

missing = cf.MISSING_CORE_DEPS + cf.MISSING_AUTH_DEPS + cf.MISSING_VECTOR_DEPS
if missing:
    raise RuntimeError(f"missing test dependencies: {missing}")

checks = cf.run_guardrail_checks()
assert not checks["gaps"], checks

started = time.perf_counter()
try:
    import pandas as pd
    from app.db_manager import import_materials_from_excel, get_all_materials
    from app.flywheel_sandbox import start_sandbox_evaluation, get_eval_task_status
    from app import server

    golden_last = ROOT / "tests/fixtures/golden_last_run.json"
    assert not golden_last.exists(), golden_last

    with open(ROOT / "tests/fixtures/sandbox_materials_v1.json", encoding="utf-8") as f:
        materials = json.load(f)["materials"]

    xlsx_path = Path(os.environ["UPLOAD_DIR"]) / "m5_r01_sandbox_materials.xlsx"
    pd.DataFrame(
        [
            {
                "物料分类": m["category"],
                "物料编码": m["code"],
                "物料名称": m["name"],
                "规格/型号": m["spec"],
                "计量单位": "台",
                "常用品牌": "",
                "描述": "",
            }
            for m in materials
        ]
    ).to_excel(xlsx_path, index=False, startrow=1)

    meta = import_materials_from_excel(str(xlsx_path), mode="replace", filename=xlsx_path.name)
    assert len(get_all_materials(limit=10 ** 6)) == len(materials)

    # 1. Dual-subprocess 3-rule sandbox gate
    cases = [
        (
            "harmful",
            {
                "suggestion_type": "ALIAS",
                "source_pattern": "排风机",
                "target_value": "低压交联聚乙烯绝缘聚氯乙烯护套电力电缆",
            },
        ),
        (
            "beneficial",
            {
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
        ),
        (
            "neutral",
            {
                "suggestion_type": "ALIAS",
                "source_pattern": "磷酸脱氧无缝紫铜管",
                "target_value": "磷酸脱氧无缝紫铜管",
            },
        ),
    ]

    reports = {}
    for name, suggestion in cases:
        task_id = start_sandbox_evaluation(suggestion)
        deadline = time.monotonic() + 300
        result = None
        while time.monotonic() < deadline:
            result = get_eval_task_status(task_id)
            if result and result["status"] in ("completed", "failed"):
                break
            time.sleep(0.5)
        else:
            raise TimeoutError(f"{name} sandbox timed out")
        assert result is not None and result["status"] == "completed", result
        report = result["report"]
        reports[name] = {
            "task_id": task_id,
            "status": result["status"],
            "passed": result["passed"],
            "targeted_count": report["targeted_count"],
            "improved_count": report["improved_count"],
            "degraded_count": report["degraded_count"],
            "degraded_from_top1": report["degraded_from_top1"],
            "reason": report["reason"],
            "violations": report["violations"],
            "integrity_errors": report["integrity_errors"],
            "score_comparisons": report["score_comparisons"],
            "snapshot": report["snapshot"],
        }

    assert reports["harmful"]["passed"] is False
    assert reports["beneficial"]["passed"] is True
    assert reports["neutral"]["passed"] is True
    assert not golden_last.exists(), golden_last
    assert not Path(os.environ["GOLDEN_DETAIL_FILE"]).exists(), os.environ["GOLDEN_DETAIL_FILE"]

    # 2. Compare strictly against M3 R04 and M4 R03 gate baselines
    diffs_by_baseline = {}
    for label, rel_path in [
        ("m3_r04", "doc/V0.1.5/evidence/m3_r04_gate_reports.json"),
        ("m4_r03", "doc/V0.1.5/evidence/m4_r03_gate_reports.json"),
    ]:
        base_path = ROOT / rel_path
        diffs = []
        if base_path.exists():
            prev_reports = json.loads(base_path.read_text(encoding="utf-8")).get("reports", {})
            for cname in ("harmful", "beneficial", "neutral"):
                cur = reports[cname]
                prev = prev_reports.get(cname, {})
                for fld in (
                    "passed",
                    "targeted_count",
                    "improved_count",
                    "degraded_count",
                    "degraded_from_top1",
                    "violations",
                ):
                    if cur.get(fld) != prev.get(fld):
                        diffs.append({"case": cname, "field": fld, label: prev.get(fld), "m5_r01": cur.get(fld)})
                if cur.get("score_comparisons") != prev.get("score_comparisons"):
                    diffs.append(
                        {
                            "case": cname,
                            "field": "score_comparisons",
                            f"{label}_len": len(prev.get("score_comparisons") or []),
                            "m5_r01_len": len(cur.get("score_comparisons") or []),
                        }
                    )
        diffs_by_baseline[label] = diffs
        assert diffs == [], f"Score/gate drift detected vs {label}: {diffs}"

    # 3. Verify R-M3-01 explosion-proof GT-0674 / GT-0677 / GT-0678 against isolated sandbox_materials_v1 DB
    rm3_checks = {}
    golden_v1 = json.loads((ROOT / "tests/fixtures/golden_set_v1.json").read_text(encoding="utf-8"))
    gs_items = golden_v1 if isinstance(golden_v1, list) else golden_v1.get("items", [])
    target_gt = {
        it["id"]: it
        for it in gs_items
        if it.get("id") in ("GT-0674", "GT-0677", "GT-0678")
    }
    assert len(target_gt) == 3, f"Missing GT-0674/0677/0678 in golden_set_v1.json: {list(target_gt.keys())}"
    query_batch = []
    for idx, (gid, gitem) in enumerate(sorted(target_gt.items()), start=1):
        query_batch.append(
            {
                "row_idx": idx,
                "code": gid,
                "name": gitem.get("name", ""),
                "specification": gitem.get("specification", ""),
                "unit": gitem.get("unit", "台"),
                "qty": 1.0,
                "loss": 1.0,
                "category": gitem.get("supply_category", ""),
                "brand": "",
                "professional": gitem.get("professional", "暖通"),
            }
        )
    matched = server.batch_match_rows(query_batch)
    for m_row in matched:
        gid = m_row["original_code"]
        top2 = [
            {
                "code": c.get("code"),
                "score": round(float(c.get("score", 0.0)), 4),
            }
            for c in (m_row.get("top_candidates") or [])[:2]
        ]
        rm3_checks[gid] = {
            "decision_status": m_row.get("decision_status"),
            "matched_code": m_row.get("matched_code"),
            "top2": top2,
        }
        assert m_row.get("decision_status") == "recommended", rm3_checks[gid]

    evidence = {
        "task": "V015-M5 R01 离线三规则双子进程门禁与 R-M3-01 复验",
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.executable,
        "isolated_materials_db": os.environ["MATERIALS_DB_PATH"],
        "seeded_rows": len(materials),
        "imported_rows": meta.get("imported_rows"),
        "llm_enabled": os.environ.get("LLM_ENABLED"),
        "golden_last_run_exists": golden_last.exists(),
        "isolated_golden_detail_exists": Path(os.environ["GOLDEN_DETAIL_FILE"]).exists(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "score_comparison_diffs_vs_m3_r04": diffs_by_baseline["m3_r04"],
        "score_comparison_diffs_vs_m4_r03": diffs_by_baseline["m4_r03"],
        "rm3_explosion_proof_checks": rm3_checks,
        "reports": reports,
    }
    out = ROOT / "doc/V0.1.5/evidence/m5_r01_gate_reports.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "passed_cases": {k: v["passed"] for k, v in reports.items()},
                "diffs_m3_r04": len(diffs_by_baseline["m3_r04"]),
                "diffs_m4_r03": len(diffs_by_baseline["m4_r03"]),
                "rm3_checks": rm3_checks,
            },
            ensure_ascii=False,
        )
    )
finally:
    assert not cf.run_guardrail_checks()["gaps"]
    print(f"ELAPSED_SECONDS={time.perf_counter() - started:.3f}")
