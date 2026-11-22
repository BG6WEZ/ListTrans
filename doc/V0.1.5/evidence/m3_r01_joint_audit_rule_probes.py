"""Adversarial production-entry observations, not business implementation changes."""
import io
import json
import os
from pathlib import Path
import sys
import faulthandler
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.update(LLM_ENABLED="false", AUTH_DEV_MODE="false", HF_HUB_OFFLINE="1",
                  TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="1",
                  MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1",
                  NUMEXPR_NUM_THREADS="1")
faulthandler.dump_traceback_later(45, repeat=True)
import tests.conftest as cf  # inject all write paths BEFORE importing any app module
assert not cf.run_guardrail_checks()["gaps"]
from tests.test_m3_hard_constraints_http import M3HttpBase
from app.binding_decisions import evaluate_hard_conflicts
from app.matcher import score_candidate_with_detail, get_unit_multiplier
from app.llm_rule_engine import LLMRuleManager
from openpyxl import load_workbook

M3HttpBase.setUpClass()
case = M3HttpBase()
results = []

def observe(label, name, qspec, cspec, qunit="台", cunit="台", parsed="", dynamic=None):
    case.setUp()
    try:
        code = "AUDIT-" + label
        case._seed(code, name, cspec, cunit, "暖通", parsed_params=parsed)
        row = case._row(name=name, specification=qspec, unit=qunit)
        binds = [case._bind(code, name=name, specification=cspec, unit=cunit, qty=10, qty_ratio=1)]
        material = dict(code=code, name=name, specification=cspec, description="", category="暖通",
                        unit=cunit, brand="", parsed_params=parsed)
        if dynamic:
            LLMRuleManager._active_rules_cache = [case._dynamic_rule(dynamic, rule_id="audit-only")]
        query = dict(name=name, specification=qspec, professional="暖通", category="暖通", unit=qunit)
        score, reasons, detail = score_candidate_with_detail(query, material, 0.9)
        confirm = case.client.post("/api/binding/submit", json=case._confirm_payload(row, binds), headers=case.auth)
        export = case.client.post("/api/export", json={"items": [case._export_item(row, binds)],
                                  "scope_filename": "m3http.xlsx"}, headers=case.auth)
        out = dict(label=label, query=query, material=material, binding=binds[0],
                   multiplier=get_unit_multiplier(qunit,cunit),
                   matcher=dict(score=score, reasons=reasons, **detail),
                   confirm_status=confirm.status_code,
                   confirm_detail=confirm.json().get("detail") if confirm.status_code != 200 else None,
                   export_status=export.status_code)
        if export.status_code == 200:
            book = load_workbook(io.BytesIO(export.content), data_only=True)
            out["xlsx_rows"] = [list(r) for r in book.active.iter_rows(values_only=True)]
        else:
            out["export_detail"] = export.json().get("detail")
        # Check automatic constrained rule path using real current material lookup.
        from app import server as srv
        rule = {"original_name":f"{name} {qspec}", "original_spec":qspec, "professional":"暖通",
                "bound_materials":[dict(binds[0], qty_ratio=1)]}
        srv.save_auto_binding_rules([rule])
        print(f"PROBE {label}: matcher={score}/{detail}; confirm={confirm.status_code}; export={export.status_code}", flush=True)
        match = case.client.post("/api/batch_match", json={"rows":[dict(id=0, text=f"{name} {qspec}",
                          name=name, specification=qspec, qty=10, unit=qunit, category="暖通", professional="暖通")]}, headers=case.auth)
        out["batch_status"] = match.status_code
        if match.status_code == 200:
            r = match.json()["results"][0]
            out["batch"] = {k:r.get(k) for k in ["decision_status","decision_source","decision_reason","bindings"]}
            auto_export = case.client.post("/api/export", json={"items":[r], "scope_filename":"m3http.xlsx"}, headers=case.auth)
            out["auto_export_status"] = auto_export.status_code
        results.append(out)
        print(json.dumps({k:v for k,v in out.items() if k not in ["xlsx_rows","query","material"]}, ensure_ascii=False), flush=True)
    finally:
        case.tearDown()

observe("UNIT-KM", "镀锌钢管", "DN15", "DN15", "km", "m", '{"attrs":{"dn_val":15}}')
observe("UNIT-CONTROL", "镀锌钢管", "DN15", "DN15", "米", "m", '{"attrs":{"dn_val":15}}')
observe("DN-EMPTY", "镀锌钢管", "DN15", "DN20", "米", "米")
observe("DN-CONTROL", "镀锌钢管", "DN15", "DN20", "米", "米", '{"attrs":{"dn_val":20}}')
observe("GRADE-EMPTY", "不锈钢管", "304", "316L 不锈钢管", "米", "米")
observe("DYNAMIC", "排风机", "EXCL专用", "EXCL专用", dynamic={"must_not_contain":["EXCL"]})
observe("PUMP-REVERSE", "风机盘管", "带提升泵", "不带提升泵")
assert not cf.run_guardrail_checks()["gaps"]
(Path(__file__).parent / "m3_r01_joint_audit2_rule_probes.json").write_text(
    json.dumps(dict(isolation_root=str(cf.LAYOUT["root"]), llm_enabled=os.environ["LLM_ENABLED"],
                    results=results), ensure_ascii=False, indent=2), encoding="utf-8")
