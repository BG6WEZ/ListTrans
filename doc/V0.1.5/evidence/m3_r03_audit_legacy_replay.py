"""Trace a legacy payload to predecessor functions, then use CURRENT real HTTP gates.

Selected R01 functions are replayed only as an isolated compatibility-fixture producer.
This is supplemental provenance evidence, not a substitute for full model/HTTP tests.
"""
import ast
import hashlib
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
from app import binding_decisions as current
from app.db_manager import get_db_connection
from openpyxl import load_workbook

source=ROOT/".local-archive/m3_r02_pre_backup/app/binding_decisions.py"
source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
assert source_sha=="3288cccd00c022b6094570621baa6d23eb9fc73d4c36e3e795879b17a9b18f60"
tree=ast.parse(source.read_text(encoding="utf-8-sig"))
names={"evaluate_hard_conflicts","validate_bindings_against_row","record_decision"}
selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
assert len(selected)==3
namespace=dict(vars(current))
exec(compile(ast.Module(body=selected,type_ignores=[]),str(source),"exec"),namespace)

M3HttpBase.setUpClass()
c=M3HttpBase();c.setUp()
try:
    code="R03AUD-LEGACY-REPLAY"
    c._seed(code,"镀锌钢管","DN15","m","暖通",parsed_params='{"attrs":{"dn_val":15}}')
    row=c._row(name="镀锌钢管",specification="DN15",unit="km",qty=10)
    binds=[c._bind(code,name="镀锌钢管",specification="DN15",unit="km",qty=10000,qty_ratio=1000)]
    item=c._export_item(row,binds)
    old_validation=namespace["validate_bindings_against_row"](item,binds)
    assert old_validation["ok"],old_validation
    namespace["record_decision"](c.user["id"],current.make_scope_key("m3http.xlsx","暖通"),item,binds,
                                 current.STATUS_MANUAL_CONFIRMED,current.SOURCE_MANUAL,"synthetic predecessor fixture replay")
    record=current.get_decision(c.user["id"],current.make_scope_key("m3http.xlsx","暖通"),item,binds)
    saved=current.extract_saved_bindings(record)
    conn=get_db_connection();conn.execute("UPDATE materials SET unit='km' WHERE code=?",(code,));conn.commit();conn.close()
    rest=c.client.post("/api/session/annotate",json={"items":[item],"scope_filename":"m3http.xlsx"},headers=c.auth)
    exported=c.client.post("/api/export",json={"items":[item],"scope_filename":"m3http.xlsx"},headers=c.auth)
    result=dict(source_sha256=source_sha,selected_predecessor_functions=sorted(names),old_validation=old_validation,
                initial_live_unit="m",legacy_saved_bindings=saved,current_live_unit="km",restore_http=rest.status_code,
                restore=rest.json(),export_http=exported.status_code)
    if exported.status_code==200:
        book=load_workbook(io.BytesIO(exported.content),data_only=True)
        result["xlsx_rows"]=[list(r) for r in book.active.iter_rows(values_only=True)]
    else:
        result["export_detail"]=exported.json()
    (EV/"m3_r03_audit_legacy_replay.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(dict(old_validation=old_validation,current_restore=rest.json()["items"][0]["decision_status"],current_export=exported.status_code)),flush=True)
finally:
    c.tearDown()
    assert not cf.run_guardrail_checks()["gaps"]
