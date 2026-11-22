"""Coordinator acceptance sampling: read-only baseline, isolated negative checks, closure."""
import hashlib
import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
EV=ROOT/'doc/V0.1.5/evidence'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def snapshot():
    return runpy.run_path(str(ROOT/'tests/verify_isolation.py'))['collect_snapshot']()

def before():
    entries=[]
    for rel,size,h in re.findall(r'^\| `([^`]+)`(?:（[^|]*）)? \| (\d+) \| `([a-f0-9]{64})`',
                               (EV/'m5_r03_delivery_hashes.md').read_text(encoding='utf-8'),re.M):
        path=ROOT/rel if '/' in rel else EV/rel
        entries.append(dict(path=str(path.relative_to(ROOT)).replace('\\','/'),size=int(size),sha256=h))
    mismatches=[r['path'] for r in entries if sha(ROOT/r['path'])!=r['sha256']
                or (ROOT/r['path']).stat().st_size!=r['size']]
    source={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for base in ('app','tests','scripts','config','web')
            for p in (ROOT/base).rglob('*') if p.is_file() and p.suffix in ('.py','.json','.html','.js')}
    source.update({p:sha(ROOT/p) for p in ('.env','requirements.txt','Dockerfile','.dockerignore','pytest.ini')})
    ref=json.loads((EV/'m5_r03_outer_snapshot_before.json').read_bytes())
    report=dict(head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                manifest_checked=len(entries),manifest_mismatches=mismatches,source_hashes=source,
                fixed_hashes={r['path']:r['sha256'] for r in entries},business_snapshot_equal=snapshot()==ref,
                accepted_audit_sha256=sha(ROOT/'doc/V0.1.5/rounds/V015-M5_R03_审核小结.md'))
    report['integrity_verdict']='PASS' if len(entries)==36 and not mismatches and report['business_snapshot_equal'] else 'FAIL'
    (EV/'m5_r03_coordinator_final_before.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_hashes','fixed_hashes')},ensure_ascii=True))
    return 0 if report['integrity_verdict']=='PASS' else 1

def probe():
    sys.path.insert(0,str(ROOT))
    os.environ.update(LLM_ENABLED='false',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONUTF8='1')
    layout=runpy.run_path(str(ROOT/'tests/conftest.py'))['LAYOUT']
    from unittest.mock import patch
    from app import vector_index,server,db_manager
    import pytest
    results={}
    folder=layout['upload_dir']/'cf'
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/'cache'
    stage=target/'.stage_new'
    stage.mkdir(parents=True)
    names=vector_index.CACHE_FILE_NAMES
    for name in names:
        (target/name).write_bytes(('OLD:'+name).encode())
        (stage/name).write_bytes(('NEW:'+name).encode())
    old={name:sha(target/name) for name in names}
    real_replace=os.replace
    seen=[]
    def fail_backup(src,dst):
        if Path(src).parent==target and Path(dst).parent.name.startswith('.backup_'):
            seen.append(Path(src).name)
            if len(seen)==2:
                raise OSError('SYNTHETIC_BACKUP_MOVE_FAILURE')
        return real_replace(src,dst)
    error=None
    vector_index.reset_cache_write_block()
    mgr=vector_index.VectorIndexManager()
    with patch.object(os,'replace',side_effect=fail_backup):
        try:
            mgr.commit_candidate_cache(str(stage),str(target))
        except OSError as exc:
            error=str(exc)
    after={name:sha(target/name) for name in names}
    results['backup_move_failure_loses_unmoved_old_files']=dict(error=error,backup_attempts=seen,
        before=old,after=after,missing_files=[n for n in names if after[n] is None],
        cache_write_blocked=vector_index.is_cache_write_blocked(),
        reproduced=old!=after and any(v is None for v in after.values()) and not vector_index.is_cache_write_blocked())

    # Reuse the submitted synthetic fixture for paths/data only; invoke the real server worker directly.
    helpers=runpy.run_path(str(ROOT/'tests/test_m5_r03_commit_arbitration.py'))
    with pytest.MonkeyPatch.context() as mp:
        envgen=helpers['r03_env'].__wrapped__(folder,mp)
        iso=next(envgen)
        try:
            helpers['_seed_old_generation'](iso,mp)
            path=iso['upload_dir']/'reverse_failure.xlsx'
            path.write_bytes(helpers['_replace_batch_xlsx']())
            tid='cfdb'
            server.set_task_initial(tid,dict(status='processing',result=None,error=None),owner_user=iso['director']['user'])
            token=server._db_import_lock_manager.try_acquire()
            before_state=helpers['_five_state'](iso)
            with patch.object(server.index_manager,'publish_candidate',side_effect=RuntimeError('SYNTHETIC_MEMORY_PUBLISH_FAILURE')),\
                 patch.object(db_manager,'reverse_retained_materials_swap',side_effect=RuntimeError('SYNTHETIC_DB_REVERSE_FAILURE')):
                server.async_process_db_upload_task(tid,str(path),'replace',path.name,lock_token=token)
            after_state=helpers['_five_state'](iso)
            followup=server._db_import_lock_manager.try_acquire()
            status=server.get_task_status(tid)
            results['db_rollback_failure_does_not_block_followup']=dict(before=before_state,after=after_state,
                task_status=status['status'],task_error=status.get('error'),next_import_lock_acquired=bool(followup),
                reproduced=(before_state['db_codes']!=after_state['db_codes'] and after_state['mem_codes']==before_state['mem_codes']
                    and not after_state['cache_write_blocked'] and bool(followup)))
            if followup:
                server._db_import_lock_manager.release_for_token(followup)
        finally:
            try:
                next(envgen)
            except StopIteration:
                pass
    results['scope']='Synthetic temporary files and SQLite only; fault injection at backup file move and rollback failure; actual cache/worker implementations unchanged.'
    results['reproduced_count']=sum(v.get('reproduced',False) for v in results.values() if isinstance(v,dict))
    (EV/'m5_r03_coordinator_final_probes.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(results,ensure_ascii=True))
    return 0 if results['reproduced_count']==2 else 1

def close():
    before_report=json.loads((EV/'m5_r03_coordinator_final_before.json').read_text(encoding='utf-8'))
    source_changes=[p for p,h in before_report['source_hashes'].items() if sha(ROOT/p)!=h]
    fixed_changes=[p for p,h in before_report['fixed_hashes'].items() if sha(ROOT/p)!=h]
    audit_digest=sha(ROOT/'doc/V0.1.5/rounds/V015-M5_R03_审核小结.md')
    reference_bytes=(EV/'m5_r03_outer_snapshot_before.json').read_bytes()
    actual=snapshot()
    equal=actual==json.loads(reference_bytes)
    (EV/'m5_r03_coordinator_final_snapshot_after.json').write_bytes(reference_bytes if equal else json.dumps(actual,indent=1,sort_keys=True).encode())
    probes=json.loads((EV/'m5_r03_coordinator_final_probes.json').read_text(encoding='utf-8'))
    docs=['doc/README.md','doc/00_项目管理/版本路线图.md','doc/00_项目管理/进度总表.md',
          'doc/00_项目管理/问题与版本归属总台账.md','doc/V0.1.5/01_实施方案与任务包.md',
          'doc/V0.1.5/02_任务台账.md','doc/V0.1.5/03_终审与版本准入.md',
          'doc/V0.1.5/rounds/README.md','doc/V0.1.5/evidence/README.md',
          'doc/V0.1.5/rounds/V015-M5_R03_统筹裁决.md']
    issue=ROOT/docs[-1]
    planned={EV/'m5_r03_coordinator_final_closure.json',EV/'m5_r03_coordinator_final_delivery_hashes.md'}
    broken=[t for t in re.findall(r'\]\(([^)]+)\)',issue.read_text(encoding='utf-8'))
            if not t.startswith(('http:','https:','#')) and not (issue.parent/t).resolve().is_file()
            and (issue.parent/t).resolve() not in planned]
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    result=dict(date='2026-11-16',role='coordinator',head=head,package_release='WITHHELD',
                version_final_review='NOT_PASSED',next_task='V015-M5 R04',open_P1=['A-M5-06','A-M5-07'],
                original_independent_audit_verdict='PASS_RETAINED_UNCHANGED',
                original_audit_sha256=audit_digest,audit_unchanged=audit_digest==before_report['accepted_audit_sha256'],
                incoming_delivery_checked=before_report['manifest_checked'],fixed_delivery_changes=fixed_changes,
                protected_source_changes=source_changes,business_snapshot_equal=equal,
                before_sha256=hashlib.sha256(reference_bytes).hexdigest(),
                after_sha256=sha(EV/'m5_r03_coordinator_final_snapshot_after.json'),
                new_negative_reproductions=probes['reproduced_count'],full_suite_browser_docker_rerun=False,
                real_credentials_or_deployment_operations=False,broken_new_links=broken,
                PRG_F11='OPEN',future_versions='V0.1.6-V0.2.0_LOCKED')
    result['integrity_verdict']='PASS' if (not source_changes and not fixed_changes and equal and result['audit_unchanged']
        and not broken and head==before_report['head'] and probes['reproduced_count']==2) else 'FAIL'
    (EV/'m5_r03_coordinator_final_closure.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    paths=sorted(set(docs+[str(p.relative_to(ROOT)).replace('\\','/') for p in EV.glob('m5_r03_coordinator_final_*')
                           if p.is_file() and p.suffix!='.md']))
    lines=['# V015-M5 R03统筹终审抽查交付SHA256','','2026-11-16。M5暂不放行，本次版本终审不通过，唯一下发R04。',
           '原独立审核PASS报告及36项固定交付不覆写；本清单记录新增证据与活动文档，不含自身。','',
           '| 文件 | SHA256 |','|---|---|']
    lines += [f'| `{p}` | `{sha(ROOT/p)}` |' for p in paths]
    (EV/'m5_r03_coordinator_final_delivery_hashes.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))
    print('DELIVERY_FILES='+str(len(paths)))
    return 0 if result['integrity_verdict']=='PASS' else 1

if __name__=='__main__':
    mode=sys.argv[1]
    raise SystemExit(before() if mode=='before' else close() if mode=='close' else probe())
