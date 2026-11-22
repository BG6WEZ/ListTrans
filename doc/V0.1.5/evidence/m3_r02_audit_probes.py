"""Independent adversarial checks; observations retain incorrect HTTP successes."""
import io
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
EV=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
os.environ.update(PYTHONIOENCODING="utf-8",LLM_ENABLED="false",AUTH_DEV_MODE="false",
                  HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",OMP_NUM_THREADS="1",MKL_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1")
import tests.conftest as cf
assert not cf.run_guardrail_checks()["gaps"]
from tests.test_m3_hard_constraints_http import M3HttpBase
from app import server as srv
from app.db_manager import get_db_connection
from app.llm_rule_engine import LLMRuleManager,get_db_conn
from app.matcher import score_candidate_with_detail,evaluate_dynamic_exclusions
from openpyxl import load_workbook

M3HttpBase.setUpClass()
c=M3HttpBase()
results=[]

def confirm(row,binds):
    return c.client.post("/api/binding/submit",json=c._confirm_payload(row,binds),headers=c.auth)

def export(row,binds):
    return c.client.post("/api/export",json={"items":[c._export_item(row,binds)],"scope_filename":"m3http.xlsx"},headers=c.auth)

def restore(row,binds):
    return c.client.post("/api/session/annotate",json={"items":[c._export_item(row,binds)],"scope_filename":"m3http.xlsx"},headers=c.auth)

def response(r):
    if r.status_code!=200:
        return dict(http=r.status_code,detail=r.json().get("detail"))
    if "spreadsheetml" in r.headers.get("content-type",""):
        book=load_workbook(io.BytesIO(r.content),data_only=True)
        return dict(http=200,xlsx_rows=[list(row) for row in book.active.iter_rows(values_only=True)])
    data=r.json()
    return dict(http=200,decision=data.get("decision"),downgraded=data.get("downgraded"),items=data.get("items"))

def batch_rule(row,binds):
    raw=f"{row['name']} {row['specification']}".strip()
    srv.save_auto_binding_rules([dict(original_name=raw,original_spec=row["specification"],professional=row["professional"],bound_materials=binds)])
    r=c.client.post("/api/batch_match",json={"rows":[dict(id=0,text=raw,qty=row["qty"],unit=row["unit"],category=row["category"],professional=row["professional"])]},headers=c.auth)
    item=r.json()["results"][0]
    ex=c.client.post("/api/export",json={"items":[item],"scope_filename":"m3http.xlsx"},headers=c.auth)
    return dict(http=r.status_code,status=item["decision_status"],reason=item["decision_reason"],export_http=ex.status_code)

def material(code,name,spec,unit="台",parsed=""):
    c._seed(code,name,spec,unit,"暖通",parsed_params=parsed)
    return dict(code=code,name=name,specification=spec,description="",category="暖通",unit=unit,brand="",parsed_params=parsed)

try:
    # R01 literal counterexamples, with exact-rule path and controlled positives.
    cases=[("RAW-KM","镀锌钢管","DN15","DN15","km","m",10,1,""),
           ("CONVERTED-KM","镀锌钢管","DN15","DN15","km","m",10000,1000,""),
           ("UNIT-ONE","镀锌钢管","DN15","DN15","米","m",15,1.5,""),
           ("DN-EMPTY","镀锌钢管","DN15","DN20","米","米",10,1,""),
           ("DN-BAD","镀锌钢管","DN15","DN20","米","米",10,1,"{bad"),
           ("GRADE-BAD","不锈钢管","304","316L 不锈钢管","米","米",10,1,"{bad"),
           ("GRADE-OK","不锈钢管","304","06Cr19Ni10 不锈钢管","米","米",10,1,""),
           ("PUMP-REV","风机盘管","带提升泵","不带提升泵","台","台",10,1,""),
           ("PUMP-FWD","风机盘管","不带泵","带提升泵","台","台",10,1,""),
           ("PUMP-UNKNOWN","风机盘管","通用型","带提升泵","台","台",10,1,"")]
    for label,name,qspec,cspec,qunit,cunit,qty,ratio,parsed in cases:
        c.setUp()
        try:
            m=material("R02AUD-"+label,name,cspec,cunit,parsed)
            row=c._row(name=name,specification=qspec,unit=qunit)
            binds=[c._bind(m["code"],name=name,specification=cspec,unit=cunit,qty=qty,qty_ratio=ratio)]
            score,reasons,detail=score_candidate_with_detail(dict(name=name,specification=qspec,unit=qunit,professional="暖通",category="暖通"),m,.9)
            results.append(dict(case=label,matcher=dict(score=score,reasons=reasons,**detail),
                                confirm=response(confirm(row,binds)),restore=response(restore(row,binds)),
                                export=response(export(row,binds)),rule=batch_rule(row,binds)))
        finally:
            c.tearDown()

    # Empty target unit suppresses both initial explicit-target check and saved-unit drift checks.
    c.setUp()
    try:
        m=material("R02AUD-EMPTY-UNIT","镀锌钢管","DN15","m")
        row=c._row(name="镀锌钢管",specification="DN15",unit="km")
        binds=[c._bind(m["code"],name=m["name"],specification="DN15",unit="",qty=10000,qty_ratio=1000)]
        rec=dict(case="EMPTY-TARGET-UNIT-DRIFT",confirm=response(confirm(row,binds)),initial_export=response(export(row,binds)))
        con=get_db_connection()
        con.execute("UPDATE materials SET unit='km' WHERE code=?",(m["code"],))
        con.commit();con.close()
        # Unit is excluded from the binding fingerprint, and no saved unit was recorded.
        changed=[dict(binds[0],unit="km")]
        rec.update(restore_after_drift=response(restore(row,changed)),export_after_drift=response(export(row,changed)),
                   original_qty=10,original_unit="km",requested_qty=10000,requested_ratio=1000,requested_unit="km")
        results.append(rec)
    finally:
        c.tearDown()

    # Real persisted approved synthetic rule; no mocked rule reader or paid compilation.
    c.setUp()
    try:
        m=material("R02AUD-DYN-ERROR","排风机","EXCL专用型")
        row=c._row(name="排风机",specification="EXCL专用型")
        binds=[c._bind(m["code"],name=m["name"],specification=m["specification"],unit="台",qty=10,qty_ratio=1)]
        con=get_db_conn()
        con.execute("INSERT INTO llm_rules (rule_id,rule_name,scope_level,scope_target,rule_json,author_id,author_name,status) VALUES (?,?,?,?,?,?,?,?)",
                    ("R02AUD-EXCL","synthetic exclusion","universal","",json.dumps({"matching_rules":{"must_not_contain":["EXCL"]}}),0,"synthetic","approved"))
        con.commit();con.close()
        LLMRuleManager.clear_active_cache()
        rec=dict(case="RULE-READ-FAIL-OPEN",before= response(confirm(row,binds)))
        con=get_db_connection()
        con.execute("ALTER TABLE llm_rules RENAME COLUMN priority TO audit_priority_unavailable")
        con.commit();con.close()
        LLMRuleManager.clear_active_cache()
        try:
            rec.update(active_rules_after_read_error=LLMRuleManager.get_active_rules(),
                       confirm_after_error=response(confirm(row,binds)),export_after_error=response(export(row,binds)),
                       rule_after_error=batch_rule(row,binds))
        finally:
            con=get_db_connection()
            con.execute("ALTER TABLE llm_rules RENAME COLUMN audit_priority_unavailable TO priority")
            con.execute("DELETE FROM llm_rules WHERE rule_id='R02AUD-EXCL'")
            con.commit();con.close()
            LLMRuleManager.clear_active_cache()
        results.append(rec)
    finally:
        c.tearDown()

    # Discipline scope negative control: HVAC row must not match plumbing-only rule.
    c.setUp()
    try:
        m=material("R02AUD-SCOPE","排风机","EXCL专用型")
        row=c._row(name="排风机",specification="EXCL专用型",professional="暖通")
        binds=[c._bind(m["code"],name=m["name"],specification=m["specification"],unit="台")]
        LLMRuleManager._active_rules_cache=[dict(rule_id="scope-only",scope_level="discipline",scope_target="给排水",parsed_dsl={"matching_rules":{"must_not_contain":["EXCL"]}})]
        results.append(dict(case="SCOPE-PLUMBING-ON-HVAC",dynamic_reasons=evaluate_dynamic_exclusions(
                            dict(original_name=row["name"],original_spec=row["specification"],professional="暖通",original_category="暖通"),m),
                            confirm=response(confirm(row,binds))))
    finally:
        c.tearDown()
finally:
    assert not cf.run_guardrail_checks()["gaps"]
    (EV/"m3_r02_audit_probes.json").write_text(json.dumps(dict(isolation_root=str(cf.LAYOUT["root"]),results=results),ensure_ascii=False,indent=2),encoding="utf-8")
    for r in results:
        print(json.dumps({k:v for k,v in r.items() if k not in ("matcher","export","initial_export","export_after_drift","export_after_error")},ensure_ascii=False),flush=True)
