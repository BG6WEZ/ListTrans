import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(r"c:\Users\tlyer\project\ListTrans")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
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

    golden = ROOT / "tests/fixtures/golden_last_run.json"
    assert not golden.exists(), golden
    with open(ROOT / "tests/fixtures/sandbox_materials_v1.json", encoding="utf-8") as f:
        materials = json.load(f)["materials"]
    xlsx_path = Path(os.environ["UPLOAD_DIR"]) / "m3_r03_evidence_sandbox_materials.xlsx"
    pd.DataFrame([
        {"物料分类": m["category"], "物料编码": m["code"], "物料名称": m["name"],
         "规格/型号": m["spec"], "计量单位": "台", "常用品牌": "", "描述": ""}
        for m in materials
    ]).to_excel(xlsx_path, index=False, startrow=1)
    meta = import_materials_from_excel(str(xlsx_path), mode="replace", filename=xlsx_path.name)
    assert len(get_all_materials(limit=10 ** 6)) == len(materials)
    cases = [
        ("harmful", {"suggestion_type": "ALIAS", "source_pattern": "排风机",
                     "target_value": "低压交联聚乙烯绝缘聚氯乙烯护套电力电缆"}),
        ("beneficial", {"suggestion_type": "BINDING", "source_pattern": "医用静音型排风机",
                        "target_value": [{"code": "3.50.29.1369", "name": "医用离心式排风机(箱）",
                                         "spec": "风量：1000m³/h，机外余压250pa，镀锌钢板", "weight": 1.0}]}),
        ("neutral", {"suggestion_type": "ALIAS", "source_pattern": "磷酸脱氧无缝紫铜管",
                     "target_value": "磷酸脱氧无缝紫铜管"}),
    ]
    reports = {}
    for name, suggestion in cases:
        task_id = start_sandbox_evaluation(suggestion)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            result = get_eval_task_status(task_id)
            if result and result["status"] in ("completed", "failed"):
                break
            time.sleep(0.5)
        else:
            raise TimeoutError(f"{name} sandbox timed out")
        assert result["status"] == "completed", result
        report = result["report"]
        reports[name] = {
            "task_id": task_id, "status": result["status"], "passed": result["passed"],
            "targeted_count": report["targeted_count"], "improved_count": report["improved_count"],
            "degraded_count": report["degraded_count"], "degraded_from_top1": report["degraded_from_top1"],
            "reason": report["reason"], "violations": report["violations"],
            "integrity_errors": report["integrity_errors"], "score_comparisons": report["score_comparisons"],
            "snapshot": report["snapshot"],
        }
    assert reports["harmful"]["passed"] is False
    assert reports["beneficial"]["passed"] is True
    assert reports["neutral"]["passed"] is True
    assert not golden.exists(), golden
    assert not Path(os.environ["GOLDEN_DETAIL_FILE"]).exists(), os.environ["GOLDEN_DETAIL_FILE"]

    # Compare vs R02 baseline (must have zero score/pass diffs)
    r02_path = ROOT / "doc/V0.1.5/evidence/m3_r02_gate_reports.json"
    score_diffs = []
    if r02_path.exists():
        r02 = json.loads(r02_path.read_text(encoding="utf-8")).get("reports", {})
        for cname in ("harmful", "beneficial", "neutral"):
            cur = reports[cname]
            prev = r02.get(cname, {})
            for fld in ("passed", "targeted_count", "improved_count", "degraded_count", "degraded_from_top1", "violations"):
                if cur.get(fld) != prev.get(fld):
                    score_diffs.append({"case": cname, "field": fld, "r02": prev.get(fld), "r03": cur.get(fld)})
            if cur.get("score_comparisons") != prev.get("score_comparisons"):
                score_diffs.append({
                    "case": cname,
                    "field": "score_comparisons",
                    "r02_len": len(prev.get("score_comparisons") or []),
                    "r03_len": len(cur.get("score_comparisons") or []),
                })

    evidence = {
        "task": "V015-M3 R03 真实双子进程门禁证据",
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.executable,
        "isolated_materials_db": os.environ["MATERIALS_DB_PATH"],
        "seeded_rows": len(materials), "imported_rows": meta.get("imported_rows"),
        "llm_enabled": os.environ.get("LLM_ENABLED"),
        "golden_last_run_exists": golden.exists(),
        "isolated_golden_detail_exists": Path(os.environ["GOLDEN_DETAIL_FILE"]).exists(),
        "elapsed_seconds": time.perf_counter() - started,
        "score_comparison_diffs_vs_r02": score_diffs,
        "reports": reports,
    }
    out = ROOT / "doc/V0.1.5/evidence/m3_r03_gate_reports.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    cmp_out = ROOT / "doc/V0.1.5/evidence/m3_r03_r02_gate_compare.json"
    cmp_out.write_text(
        json.dumps({"score_comparison_diffs": score_diffs, "compared_against": str(r02_path)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "summary": {
            name: {
                "passed": r["passed"],
                "targeted": r["targeted_count"],
                "violations": [v["type"] for v in r["violations"]],
                "snapshot_errors": r["snapshot"]["errors"],
                "score_comparisons": len(r["score_comparisons"]),
            }
            for name, r in reports.items()
        },
        "score_comparison_diffs": score_diffs,
    }, ensure_ascii=False))
finally:
    assert not cf.run_guardrail_checks()["gaps"]
    print(f"ELAPSED_SECONDS={time.perf_counter() - started:.3f}")
