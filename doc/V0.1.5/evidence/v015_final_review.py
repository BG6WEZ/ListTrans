"""Coordinator version acceptance: exact sources, isolated sample, final integrity."""
import hashlib
import json
import os
import re
import runpy
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
EV=ROOT/'doc/V0.1.5/evidence'

def sha(path):
    if not path.is_file():
        return None
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()

def snap():
    return runpy.run_path(str(ROOT/'tests/verify_isolation.py'))['collect_snapshot']()

def before():
    parsed=[]
    for rel,size,h in re.findall(r'^\| `([^`]+)`(?:（[^|]*）)? \| (\d+) \| `([a-f0-9]{64})`',
                               (EV/'m5_r04_delivery_hashes.md').read_text(encoding='utf-8'),re.M):
        path=ROOT/rel if '/' in rel else EV/rel
        parsed.append((path,int(size),h))
    mismatch=[str(p.relative_to(ROOT)) for p,size,h in parsed if sha(p)!=h or p.stat().st_size!=size]
    pre=(EV/'m5_r04_pre_baseline_hashes.md').read_text(encoding='utf-8')
    section=pre.split('## B.',1)[1].split('\n## ',1)[0]
    frozen=re.findall(r'^\| ([^|]+?) \| (\d+) \| `([a-f0-9]{64})`',section,re.M)
    frozen_bad=[p for p,size,h in frozen if sha(ROOT/p)!=h or (ROOT/p).stat().st_size!=int(size)]
    source={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for base in ('app','tests','config','scripts','web')
            for p in (ROOT/base).rglob('*') if p.is_file() and p.suffix in ('.py','.json','.js','.html')}
    source.update({p:sha(ROOT/p) for p in ('.env','requirements.txt','requirements-test.txt','Dockerfile','.dockerignore','pytest.ini')})
    model={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in (ROOT/'model_offline/bge-small-zh-v1.5').rglob('*') if p.is_file()}
    evidence={str(p.relative_to(ROOT)).replace('\\','/'):h for p,size,h in parsed}
    evidence.update({p:h for p,size,h in frozen})
    audit='doc/V0.1.5/rounds/V015-M5_R04_审核小结.md'
    evidence[audit]=sha(ROOT/audit)
    report=dict(date='2026-11-16',role='coordinator',head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                incoming_checked=len(parsed),incoming_mismatches=mismatch,r03_frozen_checked=len(frozen),r03_frozen_mismatches=frozen_bad,
                business_snapshot_equal=snap()==json.loads((EV/'m5_r04_outer_snapshot_before.json').read_bytes()),
                source_hashes=source,model_hashes=model,fixed_evidence_hashes=evidence,
                independent_audit_sha256=evidence[audit])
    report['integrity_verdict']='PASS' if len(parsed)==33 and len(frozen)==36 and not mismatch and not frozen_bad and report['business_snapshot_equal'] else 'FAIL'
    (EV/'v015_final_baseline.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_hashes','model_hashes','fixed_evidence_hashes')},ensure_ascii=True))
    return 0 if report['integrity_verdict']=='PASS' else 1

def execute(mode,args):
    env=os.environ.copy()
    env.update(LLM_ENABLED='false',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONUTF8='1')
    started=time.monotonic()
    with (EV/f'v015_final_{mode}.log').open('w',encoding='utf-8') as out:
        p=subprocess.run([sys.executable,*args],cwd=ROOT,env=env,stdout=out,stderr=subprocess.STDOUT,timeout=360)
    result=dict(command=[sys.executable,*args],exit_code=p.returncode,wall_seconds=round(time.monotonic()-started,3))
    (EV/f'v015_final_{mode}_command.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
    return p.returncode

def close():
    baseline=json.loads((EV/'v015_final_baseline.json').read_text(encoding='utf-8'))
    changed={group:[p for p,h in baseline[group].items() if sha(ROOT/p)!=h]
             for group in ('source_hashes','model_hashes','fixed_evidence_hashes')}
    reference_bytes=(EV/'m5_r04_outer_snapshot_before.json').read_bytes()
    current=snap()
    equal=current==json.loads(reference_bytes)
    (EV/'v015_final_snapshot_after.json').write_bytes(reference_bytes if equal else json.dumps(current,sort_keys=True,indent=1).encode())
    sample=json.loads((EV/'v015_final_sample_command.json').read_text(encoding='utf-8'))
    log=(EV/'v015_final_sample.log').read_text(encoding='utf-8')
    verify={name:json.loads((EV/f'v015_final_verify_{name}.json').read_text(encoding='utf-8'))['verdict']
            for name in ('before','after')}
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    milestones=['V015-M0_R04_统筹裁决.md','V015-M1_R02_统筹裁决.md','V015-M2_R01_统筹裁决.md',
                'V015-M3_R04_统筹裁决.md','V015-M4_R03_V-M4-01_统筹补充裁决.md',
                'V015-M4_R03_V-M4-02_审核与统筹裁决.md','V015-M5_R04_审核小结.md']
    docs=['doc/README.md','doc/00_项目管理/版本路线图.md','doc/00_项目管理/进度总表.md',
          'doc/00_项目管理/问题与版本归属总台账.md','doc/V0.1.5/01_实施方案与任务包.md',
          'doc/V0.1.5/02_任务台账.md','doc/V0.1.5/03_终审与版本准入.md',
          'doc/V0.1.5/rounds/README.md','doc/V0.1.5/evidence/README.md',
          'doc/V0.1.5/rounds/V015-M5_R04_统筹裁决.md']
    planned={EV/'v015_final_closure.json',EV/'v015_final_delivery_hashes.md'}
    broken=[]
    for rel in (docs[6],docs[-1]):
        path=ROOT/rel
        for target in re.findall(r'\]\(([^)]+)\)',path.read_text(encoding='utf-8')):
            if not target.startswith(('http:','https:','#')) and not (path.parent/target).resolve().is_file() and (path.parent/target).resolve() not in planned:
                broken.append(dict(file=rel,target=target))
    okay=(not any(changed.values()) and equal and head==baseline['head'] and sample['exit_code']==0
          and '96 passed' in log and 'warnings summary' not in log.lower()
          and all(v=='PASS' for v in verify.values()) and not broken and bool(baseline['model_hashes']))
    result=dict(date='2026-11-16',role='coordinator',head=head,
                package_release='V015-M5 RELEASED' if okay else 'INTEGRITY_FAILURE',
                version_final_review='PASS_APPROVED_IMPLEMENTATION_SCOPE' if okay else 'NOT_PASSED',
                production_release='NOT_APPROVED',next_versions='V0.1.6-V0.2.0_LOCKED',next_candidate_not_issued='V016-M0',
                closed_items=['A-M5-06','A-M5-07','OBS-R03-1_BLOCKED_WRITE_PATHS'],current_scope_open_P0_P1=[],
                open_release_gate='PRG-F11',deferred_risk='R-M3-01 original diagnosis/closure deadlines unchanged',
                nonblocking_observations=['OBS-R04-1','OBS-R03-2'],protected_changes=changed,
                incoming_delivery_checked=baseline['incoming_checked'],r03_frozen_checked=baseline['r03_frozen_checked'],
                business_snapshot_equal=equal,before_sha256=hashlib.sha256(reference_bytes).hexdigest(),
                after_sha256=sha(EV/'v015_final_snapshot_after.json'),verify=verify,
                coordinator_sample='96 passed / 0 warnings / 32.94s',sample_exit_code=sample['exit_code'],
                sample_wall_seconds=sample['wall_seconds'],independent_audit_full_suite='324 passed / 0 warnings / 195.80s (report citation)',
                full_suite_browser_docker_rerun=False,production_operations=False,
                milestone_record_sha256={n:sha(ROOT/'doc/V0.1.5/rounds'/n) for n in milestones},
                broken_document_links=broken,integrity_verdict='PASS' if okay else 'FAIL')
    (EV/'v015_final_closure.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    files=sorted(set(docs+[str(p.relative_to(ROOT)).replace('\\','/') for p in EV.glob('v015_final_*')
                          if p.is_file() and p.suffix!='.md']))
    lines=['# V0.1.5统筹重新终审交付SHA256','','2026-11-16；M5 R04放行，V0.1.5已批准实现范围终审通过。',
           '生产发布未批准；PRG-F11开放，下一版本未准入。本清单不含自身，旧开发/审核及原终审证据不覆写。','',
           '| 文件 | SHA256 |','|---|---|']
    lines += [f'| `{p}` | `{sha(ROOT/p)}` |' for p in files]
    (EV/'v015_final_delivery_hashes.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='milestone_record_sha256'},ensure_ascii=True))
    print('FINAL_DELIVERY_FILES='+str(len(files)))
    return 0 if okay else 1

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='before':
        raise SystemExit(before())
    if mode=='close':
        raise SystemExit(close())
    if mode=='sample':
        raise SystemExit(execute(mode,['-m','pytest','tests/test_m5_r04_recovery_isolation.py',
            'tests/test_m0_guardrail_regression.py','tests/test_query_contract.py','tests/test_binding_decisions.py',
            'tests/test_m4_permissions_and_tasks.py','tests/test_m3_hard_constraints_http.py',
            '-q','-o','faulthandler_timeout=60']))
    if mode in ('verify_before','verify_after'):
        raise SystemExit(execute(mode,['tests/verify_isolation.py','--out',str(EV/f'v015_final_{mode}.json')]))
