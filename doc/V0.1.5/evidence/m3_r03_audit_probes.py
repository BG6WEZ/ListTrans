"""Independent HTTP and SQLite compatibility/fault probes; all writes isolated."""
import io
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
EV=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
os.environ.update(PYTHONIOENCODING="utf-8",LLM_ENABLED="false",AUTH_DEV_MODE="false",HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",
                  OMP_NUM_THREADS="1",MKL_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1")
import tests.conftest as cf
assert not cf.run_guardrail_checks()["gaps"]
from tests.test_m3_hard_constraints_http import M3HttpBase
from app.db_manager import get_db_connection
from app.auth_manager import get_auth_connection
from app.binding_decisions import make_scope_key,row_content_fingerprint,binding_fingerprint,extract_saved_bindings,get_decision
from app.llm_rule_engine import LLMRuleManager,get_db_conn
from app.matcher import score_candidate_with_detail
from app.normalizer import dynamic_rule_matches_query,clean_text_standard
from app import server as srv
from openpyxl import load_workbook

M3HttpBase.setUpClass()
c=M3HttpBase()
results=[]

def observe(resp):
    if resp.status_code!=200:
        return dict(http=resp.status_code,detail=resp.json().get("detail"))
    if "spreadsheetml" in resp.headers.get("content-type",""):
        book=load_workbook(io.BytesIO(resp.content),data_only=True)
        return dict(http=200,xlsx_rows=[list(r) for r in book.active.iter_rows(values_only=True)])
    return dict(http=200,body=resp.json())

def confirm(row,binds):
    return observe(c.client.post("/api/binding/submit",json=c._confirm_payload(row,binds),headers=c.auth))

def exp(row,binds):
    return observe(c.client.post("/api/export",json={"items":[c._export_item(row,binds)],"scope_filename":"m3http.xlsx"},headers=c.auth))

def restore(row,binds):
    return observe(c.client.post("/api/session/annotate",json={"items":[c._export_item(row,binds)],"scope_filename":"m3http.xlsx"},headers=c.auth))

def seed(code="R03AUD-M",name="镀锌钢管",spec="DN15",unit="m",category="暖通"):
    c._seed(code,name,spec,unit,category,parsed_params="")
    return dict(code=code,name=name,specification=spec,unit=unit,category=category,description="",brand="",parsed_params="")

def get_rules():
    try:
        return dict(available=True,count=len(LLMRuleManager.get_active_rules()))
    except Exception as exc:
        return dict(available=False,error_type=type(exc).__name__,message=str(exc))

try:
    c.setUp()
    try:
        m=seed()
        row=c._row(name=m["name"],specification="DN15",unit="km")
        good=[c._bind(m["code"],name=m["name"],specification="DN15",unit="m",qty=10000,qty_ratio=1000)]
        rec=dict(case="NEW-CONFIRM-UNIT-EVIDENCE",empty_unit=confirm(row,[dict(good[0],unit="")]),
                 missing_unit=confirm(row,[{k:v for k,v in good[0].items() if k!="unit"}]),
                 converted=confirm(row,good),converted_export=exp(row,good))
        record=get_decision(c.user["id"],make_scope_key("m3http.xlsx","暖通"),c._export_item(row,good),good)
        rec["saved_bindings"]=extract_saved_bindings(record)
        conn=get_db_connection();conn.execute("UPDATE materials SET unit='km' WHERE code=?",(m["code"],));conn.commit();conn.close()
        rec["drift_variants"]=[dict(unit=b.get("unit"),restore=restore(row,[b]),export=exp(row,[b])) for b in
                                (good[0],dict(good[0],unit="km"),{k:v for k,v in good[0].items() if k!="unit"})]
        results.append(rec)
    finally:
        c.tearDown()

    # A pre-R03 payload contains only the client's unit, without server confirmed_* evidence.
    # The R01 implementation did not validate the binding's unit against the live material.
    c.setUp()
    try:
        m=seed(code="R03AUD-LEGACY")
        row=c._row(name=m["name"],specification="DN15",unit="km")
        good=[c._bind(m["code"],name=m["name"],specification="DN15",unit="m",qty=10000,qty_ratio=1000)]
        rec=dict(case="LEGACY-NONEMPTY-CLIENT-UNIT",new_confirm_control=confirm(row,good))
        legacy=[dict(good[0],unit="km")]
        row_item=c._export_item(row,good)
        payload=dict(row={k:v for k,v in row_item.items() if k.startswith("original_") or k=="professional"},bindings=legacy)
        ac=get_auth_connection()
        ac.execute("UPDATE binding_decisions SET payload_json=? WHERE user_id=? AND scope_key=? AND row_fingerprint=?",
                   (json.dumps(payload,ensure_ascii=False),c.user["id"],make_scope_key("m3http.xlsx","暖通"),row_content_fingerprint(row_item)))
        ac.commit();ac.close()
        conn=get_db_connection();conn.execute("UPDATE materials SET unit='km' WHERE code=?",(m["code"],));conn.commit();conn.close()
        rec.update(legacy_payload=payload,restore=restore(row,legacy),export=exp(row,legacy),
                   original_qty=10,original_unit="km",requested_qty=10000,requested_unit="km")
        results.append(rec)
    finally:
        c.tearDown()

    c.setUp()
    try:
        m=seed(code="R03AUD-FAIL",name="排风机",spec="EXCL专用型",unit="台")
        row=c._row(name=m["name"],specification=m["specification"])
        binds=[c._bind(m["code"],name=m["name"],specification=m["specification"],unit="台",qty=10,qty_ratio=1)]
        rec=dict(case="RULE-READ-PARSE-RECOVERY",initial_confirm=confirm(row,binds))
        conn=get_db_conn()
        dsl={"matching_rules":{"must_not_contain":["EXCL"]}}
        conn.execute("INSERT INTO llm_rules (rule_id,rule_name,scope_level,scope_target,rule_json,author_id,author_name,status) VALUES (?,?,?,?,?,?,?,?)",
                     ("R03AUD-EXCL","synthetic","universal","",json.dumps(dsl),0,"synthetic","approved"))
        conn.commit();conn.close();LLMRuleManager.clear_active_cache()
        rec["valid_exclusion"]=confirm(row,binds)
        conn=get_db_connection();conn.execute("ALTER TABLE llm_rules RENAME COLUMN priority TO audit_missing_priority");conn.commit();conn.close()
        LLMRuleManager.clear_active_cache()
        try:
            query=dict(name=m["name"],specification=m["specification"],professional="暖通",category="暖通",unit="台")
            score,reasons,detail=score_candidate_with_detail(query,m,.9)
            rec["sql_failure"]=dict(rules=get_rules(),matcher=dict(score=score,reasons=reasons,**detail),
                                    confirm=confirm(row,binds),restore=restore(row,binds),export=exp(row,binds))
        finally:
            conn=get_db_connection();conn.execute("ALTER TABLE llm_rules RENAME COLUMN audit_missing_priority TO priority");conn.commit();conn.close()
        rec["malformed"]={}
        for label,raw in [("bad_json","{"),("bad_matching_list",json.dumps({"matching_rules":[]})),
                          ("bad_required_type",json.dumps({"matching_rules":{"must_contain":3}}))]:
            conn=get_db_connection();conn.execute("UPDATE llm_rules SET rule_json=? WHERE rule_id='R03AUD-EXCL'",(raw,));conn.commit();conn.close()
            LLMRuleManager.clear_active_cache()
            rec["malformed"][label]=dict(rules=get_rules(),confirm=confirm(row,binds),export=exp(row,binds))
        conn=get_db_connection();conn.execute("DELETE FROM llm_rules WHERE rule_id='R03AUD-EXCL'");conn.commit();conn.close()
        LLMRuleManager.clear_active_cache()
        rec["recovered_empty"]=dict(rules=get_rules(),confirm=confirm(row,binds),export=exp(row,binds))
        results.append(rec)
    finally:
        c.tearDown()

    # Scope target/cross-profession controls and display alias compatibility.
    c.setUp()
    try:
        m=seed(code="R03AUD-SCOPE",name="通用装置",spec="EXCL型",unit="台",category="其它")
        binds=[c._bind(m["code"],name=m["name"],specification=m["specification"],unit="台")]
        rec=dict(case="SCOPE-CONTROLS",variants=[])
        for scope,prof in [("给排水","暖通"),("给排水","给排水"),("通用","暖通"),("医用气体","医气")]:
            rule=dict(rule_id="R03AUD-SCOPE-RULE",scope_level="discipline",scope_target=scope,
                      parsed_dsl={"matching_rules":{"must_not_contain":["EXCL"]}})
            LLMRuleManager._active_rules_cache=[rule]
            row=c._row(name=m["name"],specification=m["specification"],professional=prof,category=prof)
            rec["variants"].append(dict(scope=scope,professional=prof,matched=dynamic_rule_matches_query(rule,dict(professional=prof,category=prof)),confirm=confirm(row,binds)))
        results.append(rec)
    finally:
        c.tearDown()
finally:
    assert not cf.run_guardrail_checks()["gaps"]
    (EV/"m3_r03_audit_probes.json").write_text(json.dumps(dict(isolation_root=str(cf.LAYOUT["root"]),results=results),ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({r["case"]:"captured" for r in results}),flush=True)
