"""Independent negative probes: synthetic workbooks and isolated SQLite only."""
import json
import os
import runpy
import sys
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
os.environ.update(LLM_ENABLED='false',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
layout=runpy.run_path(str(ROOT/'tests/conftest.py'))['LAYOUT']

import numpy as np
import openpyxl
from unittest.mock import patch
from app import auth_manager, db_manager, server, excel_boundary, preprocessor, vector_index

db_manager.init_db()
auth_manager.init_auth_db()
from app.audit_manager import init_audit_tables
init_audit_tables()
owner=auth_manager.get_user_by_username('admin')
folder=layout['upload_dir']
report={}

def workbook(name):
    path=folder/name
    wb=openpyxl.Workbook()
    ws=wb.active
    ws.append(['SYNTHETIC MATERIALS'])
    ws.append(['物料编码','物料名称','规格/型号','计量单位'])
    ws.append(['AUDIT-NEW-1','测试阀门','DN15','个'])
    ws.append(['AUDIT-NEW-2','测试阀门','DN20','个'])
    wb.save(path)
    wb.close()
    return path

def seed():
    db_manager.init_db()
    conn=db_manager.get_db_connection()
    conn.execute('DELETE FROM materials')
    conn.execute("INSERT INTO materials(code,name,specification,category) VALUES('AUDIT-OLD','old','DN15','阀门')")
    conn.commit()
    conn.close()

def codes():
    conn=db_manager.get_db_connection()
    result=[r[0] for r in conn.execute('SELECT code FROM materials ORDER BY code')]
    conn.close()
    return result

def task(tid):
    server.set_task_initial(tid,dict(status='processing',result=None,error=None),owner_user=owner)

# Real importer commits database, then an injected index failure is surfaced as failed.
seed()
p=workbook('index_failure.xlsx')
task('audit-index-failure')
token=server._db_import_lock_manager.try_acquire()
with patch.object(server.index_manager,'build_index',side_effect=RuntimeError('SYNTHETIC_INDEX_FAILURE')):
    server.async_process_db_upload_task('audit-index-failure',str(p),'replace',p.name,lock_token=token)
state=server.get_task_snapshot('audit-index-failure') if hasattr(server,'get_task_snapshot') else dict(server.upload_tasks['audit-index-failure'])
report['failed_index_after_database_commit']=dict(status=state['status'],codes_after=codes(),
    reproduced=state['status']=='failed' and 'AUDIT-OLD' not in codes() and 'AUDIT-NEW-1' in codes())

# Real DROP/CREATE happens before implicit INSERT transaction. Cancellation rollback loses OLD.
seed()
p=workbook('cancel_rollback.xlsx')
cancel=threading.Event()
real_tokenize=preprocessor.fts_tokenize
def mark_cancel(text):
    cancel.set()
    return real_tokenize(text)
error=None
with patch.object(preprocessor,'fts_tokenize',side_effect=mark_cancel):
    try:
        db_manager.import_materials_from_excel(str(p),'replace',p.name,cancel_event=cancel)
    except excel_boundary.ParseCancelledError as exc:
        error=type(exc).__name__
report['replace_rollback_loses_old_table']=dict(exception=error,codes_after=codes(),
    reproduced=error=='ParseCancelledError' and 'AUDIT-OLD' not in codes())

# Supervisor releases ownership while an actual worker is still alive and has not acknowledged cancellation.
entered=threading.Event()
release=threading.Event()
exited=threading.Event()
def held_import(*args,cancel_event=None,**kwargs):
    entered.set()
    try:
        if not release.wait(10):
            raise RuntimeError('audit synchronization timeout')
        if cancel_event.is_set():
            raise excel_boundary.ParseCancelledError('synthetic cooperative exit')
        return {}
    finally:
        exited.set()
task('audit-timeout-lock')
p=workbook('timeout.xlsx')
token=server._db_import_lock_manager.try_acquire()
with patch.object(server,'_DB_IMPORT_TASK_TIMEOUT_SECONDS',0.05),patch.object(db_manager,'import_materials_from_excel',side_effect=held_import):
    server.async_process_db_upload_task('audit-timeout-lock',str(p),'replace',p.name,lock_token=token)
    second=server._db_import_lock_manager.try_acquire()
    report['timeout_releases_lock_before_worker_exit']=dict(worker_entered=entered.is_set(),worker_exited=exited.is_set(),
        second_import_acquired=bool(second),temp_file_still_exists=p.exists(),
        reproduced=entered.is_set() and not exited.is_set() and bool(second))
    if second:
        server._db_import_lock_manager.release_for_token(second)
    release.set()
    assert exited.wait(10)

# Cancel after the last check but before acquiring the swap lock; actual build_index still swaps.
at_swap=threading.Event()
permit_swap=threading.Event()
cancel=threading.Event()
class GateLock:
    def __enter__(self):
        at_swap.set()
        if not permit_swap.wait(10):
            raise RuntimeError('audit swap synchronization timeout')
    def __exit__(self,*args):
        pass
mgr=vector_index.VectorIndexManager()
mgr.model=object()
mgr.embedding_dim=2
mgr.index_to_material=[dict(code='AUDIT-OLD')]
mgr.numpy_embeddings=np.array([[0,1]],dtype=np.float32)
mgr._lock=GateLock()
mgr.encode_queries=lambda texts,**kwargs:np.array([[1,0]],dtype=np.float32)
mgr.save_cache=lambda:None
thread_errors=[]
def rebuild():
    try:
        mgr.build_index([dict(code='AUDIT-NEW',name='new',specification='',category='')],cancel_event=cancel)
    except Exception as exc:
        thread_errors.append(type(exc).__name__)
with patch.object(vector_index,'HAS_FAISS',False):
    worker=threading.Thread(target=rebuild)
    worker.start()
    assert at_swap.wait(10)
    cancel.set()
    permit_swap.set()
    worker.join(10)
report['cancel_before_swap_still_publishes_index']=dict(cancel_set=cancel.is_set(),
    published_code=mgr.index_to_material[0]['code'],errors=thread_errors,
    reproduced=mgr.index_to_material[0]['code']=='AUDIT-NEW')

# Shared file reader does not enforce max_upload_bytes; personnel calls this reader directly.
p=workbook('direct_read.xlsx')
parsed=excel_boundary.load_validated_excel_sheets(str(p),budget=excel_boundary.ExcelParseBudget(max_upload_bytes=1))
report['direct_reader_ignores_file_size_budget']=dict(file_bytes=p.stat().st_size,budget_bytes=1,
    parsed_rows=len(parsed[0][1]),reproduced=len(parsed[0][1])>0)

report['scope']='Synthetic isolated workbooks/SQLite; fault injection only at index failure and deterministic concurrency barriers; real importer and swap implementation.'
report['reproduced_count']=sum(v.get('reproduced',False) for v in report.values() if isinstance(v,dict))
(ROOT/'doc/V0.1.5/evidence/m5_r01_audit_probes.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=True))
assert report['reproduced_count']==5
