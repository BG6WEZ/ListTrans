"""Replay the frozen governance rules with experimental scoring on Train/Dev only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "doc" / "V0.1.9" / "evidence"
sys.path[:0] = [str(ROOT), str(ROOT / "doc" / "V0.1.6" / "evidence"),
                str(ROOT / "doc" / "V0.1.7" / "evidence")]

import m4_r01_3rule_gate as gate
from m0_r01_benchmark import block_external_network
from m0_r01_build_governance import GOLDEN, read_json, sha


def run(profile, output):
    if output.exists():
        raise FileExistsError(output)
    governance = read_json(ROOT / "tests" / "fixtures" / "golden_governance_v1.json")["samples"]
    data = read_json(GOLDEN)
    data["items"] = [r for r in data["items"] if governance[r["id"]]["split"] in {"train", "dev"}]
    ids = {r["id"] for r in data["items"]}
    if len(ids) != 1060:
        raise RuntimeError("Unexpected Train/Dev population")
    refs = {
        "v016_m3_r01": ("V0.1.6", "m3_r01"),
        "v016_m2_r01": ("V0.1.6", "m2_r01"),
        "v016_m0_r03": ("V0.1.6", "m0_r03"),
        "v015_m3_r04": ("V0.1.5", "m3_r04"),
        "v015_m4_r03": ("V0.1.5", "m4_r03"),
        "v015_m5_r04": ("V0.1.5", "m5_r04"),
    }
    rules = {}
    # Windows may keep an isolated SQLite handle open until worker shutdown.
    with tempfile.TemporaryDirectory(prefix="v019_m0_candidate_gate_", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp).resolve()
        gate._setup_env(root)
        subset = root / "runtime" / "train_dev.json"
        subset.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.environ.update({
            "GOLDEN_SET_FILE": str(subset), "GOLDEN_SET_SHA256": sha(subset),
            "LISTTRANS_V019_M0_SCORING": profile, "LISTTRANS_V018_M0_RECALL": "0",
            "LISTTRANS_V018_M1_RECALL": "0", "LISTTRANS_V018_M2_RECALL": "0",
            "ENABLE_SERIES_RERUN": "false",
        })
        sys.addaudithook(block_external_network)

        import pandas as pd
        from app.db_manager import import_materials_from_excel, init_db
        from app.flywheel_db import init_flywheel_tables
        from app.flywheel_sandbox import get_eval_task_status, start_sandbox_evaluation
        from app.llm_rule_engine import init_rule_engine_db
        from app.query_preprocessor import prepare_query
        from app.matcher import match_item
        from app.server import HTTPException, _gate_export_row, batch_match_rows

        init_db()
        init_rule_engine_db()
        init_flywheel_tables()
        fixture = read_json(ROOT / "tests" / "fixtures" / "sandbox_materials_v1.json")
        xlsx = root / "uploads" / "sandbox_materials_v1.xlsx"
        pd.DataFrame([{
            "物料分类": m["category"], "物料编码": m["code"], "物料名称": m["name"],
            "规格/型号": m["spec"], "计量单位": "台", "常用品牌": "", "描述": "",
        } for m in fixture["materials"]]).to_excel(xlsx, index=False, startrow=1)
        import_materials_from_excel(str(xlsx), mode="replace", filename=xlsx.name)

        for name, suggestion in gate.RULES.items():
            task_id = start_sandbox_evaluation(suggestion)
            deadline = time.monotonic() + 180
            state = None
            while time.monotonic() < deadline:
                state = get_eval_task_status(task_id)
                if state and state.get("status") in ("completed", "failed"):
                    break
                time.sleep(0.5)
            if not state or state.get("status") != "completed":
                raise RuntimeError(f"Rule {name} did not complete ({(state or {}).get('status')})")
            report = state["report"]
            comparable = report.get("score_comparisons", [])
            if any(row["id"] not in ids for row in comparable):
                raise RuntimeError("Held-out rule row")
            rules[name] = {key: report.get(key) for key in (
                "benefit", "safety_passed", "guard_passed", "targeted_count", "guard_count",
                "binding_checks_count", "binding_degraded_count", "failed_samples_count",
                "cache_reused_files", "score_comparisons",
            )}
            print(f"{profile} {name} {state.get('benefit')} safe={state.get('safety_passed')}", flush=True)

        probes = {}
        for item in data["items"]:
            gid = item["id"]
            if gid not in ("GT-0674", "GT-0677", "GT-0678"):
                continue
            args = (item["name"], item["specification"], item["professional"])
            query = prepare_query(*args, category=item["supply_category"], unit=item["unit"],
                                  preserve_original=False)
            trace = match_item(query, diagnostic_targets=item["target_codes"])["diagnostics"]["targets"][0]
            row, = batch_match_rows([{
                "row_idx": 0, "name": args[0], "specification": args[1], "professional": args[2],
                "qty": 1.0, "unit": item["unit"], "loss": 0.0, "category": item["supply_category"], "brand": "",
            }])
            try:
                _gate_export_row(row, {"id": 2147483000}, "m0_unknown_export.xlsx")
            except HTTPException as exc:
                export_blocked = exc.status_code == 422 and exc.detail["error"] == "export_blocked"
            else:
                export_blocked = False
            probes[gid] = {"decision_status": row["decision_status"], "export_blocked": export_blocked,
                           **{k: trace[k] for k in ("recalled", "hard_blocked", "T", "W", "margin", "full_rank")}}

        comparisons = {}
        for ref, (version, round_name) in refs.items():
            baseline = read_json(ROOT / "doc" / version / "evidence" / f"{round_name}_gate_reports.json")
            reports = baseline.get("reports") or baseline.get("rules") or baseline
            comparisons[ref] = {
                name: gate._diff_score_comparisons(rules[name], reports[name].get("report", reports[name]))
                for name in ("harmful", "beneficial", "neutral")
            }
        report = {
            "profile": profile, "subset_rows": len(ids), "subset_sha256": sha(subset),
            "source_sha256": {p: sha(ROOT / p) for p in ("app/matcher.py", "app/server.py")},
            "rules": rules, "comparisons": comparisons, "probes": probes,
        }
        report["probes_ok"] = len(probes) == 3 and all(
            row["decision_status"] == "recommended" and row["export_blocked"]
            and row["recalled"] and not row["hard_blocked"] for row in probes.values()
        )
        ref_probe = ROOT / "doc" / "V0.1.8" / "evidence" / "m3_r01_gate_final.json"
        prior = read_json(ref_probe)["r_m3_01_diagnostics"]
        report["protected_margin_comparison"] = {
            gid: {
                "v018_rank": prior[gid]["targets"][0]["full_rank"],
                "v018_margin": prior[gid]["targets"][0]["margin"],
                "candidate_rank": row["full_rank"], "candidate_margin": row["margin"],
            }
            for gid, row in probes.items()
        }
        report["rule_safety_unchanged"] = {
            name: (row["benefit"], row["safety_passed"]) == expected
            for name, row, expected in (
                ("empty", rules["empty"], ("invalid", False)),
                ("harmful", rules["harmful"], ("harmful", False)),
                ("beneficial", rules["beneficial"], ("harmful", False)),
                ("beneficial_constrained", rules["beneficial_constrained"], ("improved", True)),
                ("neutral", rules["neutral"], ("neutral", True)),
            )
        }
        digest = hashlib.sha256(GOLDEN.read_bytes()).hexdigest()
        report["parent_golden_sha256"] = digest
        report["exit_code"] = 0 if report["probes_ok"] and all(report["rule_safety_unchanged"].values()) else 1
        with output.open("x", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
        return report["exit_code"]


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", choices=("dedup", "calibrated"), required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    sys.exit(run(args.profile, args.out))
