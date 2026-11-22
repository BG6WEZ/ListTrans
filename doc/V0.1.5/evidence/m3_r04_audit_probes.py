"""Independent saved-evidence matrix plus real reconfirm/restore/export controls."""
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
from app.auth_manager import get_auth_connection
from app.db_manager import get_db_connection
from app import binding_decisions as bd
from openpyxl import load_workbook

M3HttpBase.setUpClass()
c=M3HttpBase();c.setUp()
result={"matrix":[]}

def observe(r):
    if r.status_code!=200:
        return dict(http=r.status_code,detail=r.json().get("detail"))
    if "spreadsheetml" in r.headers.get("content-type",""):
        wb=load_workbook(io.BytesIO(r.content),data_only=True)
        return dict(http=200,rows=[list(row) for row in wb.active.iter_rows(values_only=True)])
    return dict(http=200,body=r.json())

def confirm(row,binds):
    return observe(c.client.post("/api/binding/submit",json=c._confirm_payload(row,binds),headers=c.auth))

def restore(row,binds):
    return observe(c.client.post("/api/session/annotate",json={"items":[c._export_item(row,binds)],"scope_filename":"m3http.xlsx"},headers=c.auth))

def export(row,binds):
    return observe(c.client.post("/api/export",json={"items":[c._export_item(row,binds)],"scope_filename":"m3http.xlsx"},headers=c.auth))

def replace_payload(row,binds,saved):
    item=c._export_item(row,binds)
    con=get_auth_connection()
    try:
        count=con.execute("UPDATE binding_decisions SET payload_json=? WHERE user_id=? AND scope_key=? AND row_fingerprint=?",
                          (json.dumps(dict(row=item,bindings=[saved]),ensure_ascii=False),c.user["id"],bd.make_scope_key("m3http.xlsx","暖通"),bd.row_content_fingerprint(item))).rowcount
        con.commit()
        assert count==1,count
    finally:
        con.close()

try:
    c._seed("R04AUD-EVIDENCE","镀锌钢管","DN15","m","暖通",parsed_params='{"attrs":{"dn_val":15}}')
    row=c._row(name="镀锌钢管",specification="DN15",unit="km",qty=10)
    good=c._bind("R04AUD-EVIDENCE",name="镀锌钢管",specification="DN15",unit="m",qty=10000,qty_ratio=1000)
    forged=dict(good,confirmed_unit="km",confirmed_orig_unit="km",confirmed_orig_qty=10,
                confirmed_unit_mult=1,confirmed_unit_source="client",confirmed_unit_version="fake")
    result["forged_request_confirm"]=confirm(row,[forged])
    rec=bd.get_decision(c.user["id"],bd.make_scope_key("m3http.xlsx","暖通"),c._export_item(row,[good]),[good])
    saved=bd.extract_saved_bindings(rec)[0]
    result["server_saved_evidence"]=saved
    result["fresh_export"]=export(row,[good])
    core={k:v for k,v in saved.items() if not k.startswith("confirmed_")}
    cases=[("client_unit_only",core),
           ("only_confirmed_unit",dict(core,confirmed_unit="m")),
           ("missing_mult",{k:v for k,v in saved.items() if k!="confirmed_unit_mult"}),
           ("bool_mult",dict(saved,confirmed_unit_mult=True)),
           ("string_mult",dict(saved,confirmed_unit_mult="1000")),
           ("nan_mult",dict(saved,confirmed_unit_mult=float("nan"))),
           ("inf_mult",dict(saved,confirmed_unit_mult=float("inf"))),
           ("zero_mult",dict(saved,confirmed_unit_mult=0)),
           ("string_orig_qty",dict(saved,confirmed_orig_qty="10")),
           ("mismatched_orig_qty",dict(saved,confirmed_orig_qty=11)),
           ("wrong_source",dict(saved,confirmed_unit_source="client")),
           ("wrong_version",dict(saved,confirmed_unit_version="fake")),
           ("inconsistent_orig_unit",dict(saved,confirmed_orig_unit="m"))]
    for label,value in cases:
        replace_payload(row,[good],value)
        rest,exp=restore(row,[forged]),export(row,[forged])
        result["matrix"].append(dict(case=label,restore=rest,export=exp))
        assert rest["body"]["items"][0]["decision_status"]=="recommended",label
        assert exp["http"]==422,label

    # R03 evidence format came from its server-side writer and lacks optional R04 tags.
    r03_style={k:v for k,v in saved.items() if k not in ("confirmed_unit_version","confirmed_unit_source")}
    replace_payload(row,[good],r03_style)
    result["r03_server_evidence_compatibility"]=dict(restore=restore(row,[good]),export=export(row,[good]))
    assert result["r03_server_evidence_compatibility"]["export"]["http"]==200

    # A legacy payload cannot be repaired by injecting evidence through the request.
    replace_payload(row,[good],dict(core,unit="km"))
    conn=get_db_connection();conn.execute("UPDATE materials SET unit='km' WHERE code='R04AUD-EVIDENCE'");conn.commit();conn.close()
    legacy_request=dict(forged,unit="km")
    result["legacy_drift_with_forged_request"]=dict(restore=restore(row,[legacy_request]),export=export(row,[legacy_request]))
    assert result["legacy_drift_with_forged_request"]["export"]["http"]==422

    # Reconfirmation under current live material creates fresh trusted evidence.
    conn=get_db_connection();conn.execute("UPDATE materials SET unit='m' WHERE code='R04AUD-EVIDENCE'");conn.commit();conn.close()
    result["reconfirmed"]=dict(confirm=confirm(row,[good]),restore=restore(row,[good]),export=export(row,[good]))
    assert result["reconfirmed"]["export"]["http"]==200
    rows=result["reconfirmed"]["export"]["rows"]
    assert rows[3][7:9]==["km",10] and rows[4][7:9]==["m",10000],rows

    # Existing 1x unit conversion keeps legitimate custom BOM ratios.
    row_one=c._row(name="镀锌钢管",specification="DN15",unit="米",qty=200)
    custom=dict(good,qty=300,qty_ratio=1.5)
    result["one_to_many_ratio"]=dict(confirm=confirm(row_one,[custom]),restore=restore(row_one,[custom]),export=export(row_one,[custom]))
    assert result["one_to_many_ratio"]["export"]["http"]==200
finally:
    (EV/"m3_r04_audit_probes.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    c.tearDown()
    assert not cf.run_guardrail_checks()["gaps"]
    print(json.dumps(dict(matrix_cases=len(result["matrix"]),status="completed")),flush=True)
