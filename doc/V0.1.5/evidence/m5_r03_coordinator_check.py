"""Coordinator-only hash/snapshot checks. Does not execute application/tests."""
import hashlib
import json
import re
import runpy
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
EV=ROOT/'doc/V0.1.5/evidence'
DOCS=['doc/README.md','doc/00_项目管理/版本路线图.md','doc/00_项目管理/进度总表.md',
      'doc/00_项目管理/问题与版本归属总台账.md','doc/V0.1.5/01_实施方案与任务包.md',
      'doc/V0.1.5/02_任务台账.md','doc/V0.1.5/03_终审与版本准入.md',
      'doc/V0.1.5/rounds/README.md','doc/V0.1.5/evidence/README.md',
      'doc/V0.1.5/rounds/V015-M5_R02_统筹裁决.md']

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

def manifest(path):
    entries={}
    for rel,h in re.findall(r'^\| `([^`]+)` \| \d+ \| `([a-f0-9]+)`',path.read_text(encoding='utf-8'),re.M):
        rel=rel if '/' in rel else 'doc/V0.1.5/evidence/'+rel
        entries[rel]=h
    return entries

def snapshot():
    return runpy.run_path(str(ROOT/'tests/verify_isolation.py'))['collect_snapshot']()

def main():
    if sys.argv[1]=='before':
        audited=manifest(EV/'m5_r02_audit_delivery_hashes.md')
        dev=manifest(EV/'m5_r02_delivery_hashes.md')
        bad_a=[p for p,h in audited.items() if sha(ROOT/p)!=h]
        bad_d=[p for p,h in dev.items() if len(h)==64 and sha(ROOT/p)!=h]
        typos=[dict(path=p,listed=h,actual=sha(ROOT/p)) for p,h in dev.items() if len(h)!=64]
        reference=json.loads((EV/'m5_r02_audit_snapshot_after.json').read_bytes())
        source={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for base in ('app','tests','scripts','config','web')
                for p in (ROOT/base).rglob('*') if p.is_file() and p.suffix in ('.py','.js','.json','.html')}
        source.update({p:sha(ROOT/p) for p in ('.env','requirements.txt','Dockerfile','.dockerignore','pytest.ini')})
        fixed={p:sha(ROOT/p) for p in set(audited)|set(dev) if p not in DOCS}
        fixed.update({p:sha(ROOT/p) for p in ('doc/V0.1.5/evidence/m5_r02_audit_delivery_hashes.md','doc/V0.1.5/evidence/m5_r02_delivery_hashes.md')})
        result=dict(role='coordinator',date='2026-11-16',head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                    audit_manifest_checked=len(audited),audit_mismatches=bad_a,
                    valid_dev_hashes_checked=sum(len(h)==64 for h in dev.values()),dev_mismatches=bad_d,
                    short_hash_errata=typos,business_snapshot_equal=snapshot()==reference,
                    source_hashes=source,fixed_evidence_hashes=fixed,reviewed_probes=['A','B','C','D'],
                    application_tests_rerun=False,review_scope='Read reports, four probe states and commit-window source; hash/snapshot checks only')
        result['integrity_verdict']='PASS' if (not bad_a and not bad_d and result['business_snapshot_equal']
            and len(typos)==2 and all(t['actual']=='5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e' for t in typos)) else 'FAIL'
        (EV/'m5_r03_coordinator_before.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in ('source_hashes','fixed_evidence_hashes')},ensure_ascii=True))
        return 0 if result['integrity_verdict']=='PASS' else 1
    before=json.loads((EV/'m5_r03_coordinator_before.json').read_text(encoding='utf-8'))
    changes={group:[p for p,h in before[group].items() if sha(ROOT/p)!=h] for group in ('source_hashes','fixed_evidence_hashes')}
    ref_bytes=(EV/'m5_r02_audit_snapshot_after.json').read_bytes()
    equal=snapshot()==json.loads(ref_bytes)
    broken=[]
    issue=ROOT/DOCS[-1]
    for target in re.findall(r'\]\(([^)]+)\)',issue.read_text(encoding='utf-8')):
        resolved=(issue.parent/target).resolve()
        planned={EV/'m5_r03_coordinator_closure.json',EV/'m5_r03_coordinator_delivery_hashes.md'}
        if not target.startswith(('http:','https:','#')) and not resolved.is_file() and resolved not in planned:
            broken.append(target)
    result=dict(date='2026-11-16',role='coordinator',head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                decision='V015-M5 R02 RETURN_FOR_REWORK; ISSUE R03',closed=['A-M5-03','A-M5-04','A-M5-05'],
                open_P1=['A-M5-01','A-M5-02'],changes=changes,business_snapshot_equal=equal,
                snapshot_reference_sha256=hashlib.sha256(ref_bytes).hexdigest(),broken_assignment_links=broken,
                production_operations=False,application_tests_rerun=False,PRG_F11='OPEN',future_versions='LOCKED')
    result['integrity_verdict']='PASS' if not any(changes.values()) and equal and not broken and result['head']==before['head'] else 'FAIL'
    (EV/'m5_r03_coordinator_closure.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    files=sorted(set(DOCS+[str(p.relative_to(ROOT)).replace('\\','/') for p in EV.glob('m5_r03_coordinator_*')
                         if p.is_file() and p.suffix!='.md']))
    lines=['# V015-M5 R03统筹下发SHA256','','2026-11-16。仅统筹核对与下发；不代写独立审核结论。清单不含自身。',
           '旧R02审核/开发清单不覆写；活动文档按本清单绑定。','','| 文件 | SHA256 |','|---|---|']
    lines += [f'| `{p}` | `{sha(ROOT/p)}` |' for p in files]
    (EV/'m5_r03_coordinator_delivery_hashes.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))
    print('DELIVERY_FILES='+str(len(files)))
    return 0 if result['integrity_verdict']=='PASS' else 1

if __name__=='__main__':
    raise SystemExit(main())
