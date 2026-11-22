"""Independent M5 audit: stdlib orchestration, isolated application execution only."""
import ast
import difflib
import hashlib
import json
import os
import re
import runpy
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / 'doc/V0.1.5/evidence'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

def snapshot():
    return runpy.run_path(str(ROOT / 'tests/verify_isolation.py'))['collect_snapshot']()

def baseline():
    entries = re.findall(r'^\| `([^`]+)` \| \d+ \| `([a-f0-9]{64})` \|$', (EV/'m5_r01_delivery_hashes.md').read_text(encoding='utf-8'), re.M)
    mismatches = [p for p,h in entries if sha(ROOT/p) != h]
    old = ROOT/'.local-archive/m5_r01_pre_backup'
    diffs = []
    for p,h in entries:
        target = ROOT/p
        prior = old/p
        if prior.is_file() and target.suffix in ('.py','.txt','.html','.js'):
            diffs.extend(difflib.unified_diff(prior.read_text(encoding='utf-8').splitlines(True), target.read_text(encoding='utf-8').splitlines(True), fromfile='pre/'+p,tofile='current/'+p))
    (EV/'m5_r01_audit_changes.diff').write_text(''.join(diffs),encoding='utf-8')
    ref = json.loads((EV/'m5_r01_outer_snapshot_before.json').read_bytes())
    current = snapshot()
    protected = {str(p.relative_to(ROOT)).replace('\\','/'):sha(p)
                 for base in ('app','tests','web','scripts','config') for p in (ROOT/base).rglob('*')
                 if p.is_file() and (p.suffix in ('.py','.js','.json','.html') or p.name.endswith('.db'))}
    protected.update({p:sha(ROOT/p) for p in ('.env','requirements.txt','requirements-test.txt','Dockerfile','.dockerignore','pytest.ini')})
    report = dict(head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  delivery_checked=len(entries),delivery_mismatches=mismatches,
                  snapshot_equal=current==ref,business_snapshot=current,protected_hashes=protected,
                  delivery_hashes=dict(entries))
    (EV/'m5_r01_audit_baseline.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('protected_hashes','business_snapshot','delivery_hashes')},ensure_ascii=True))
    return 0 if entries and not mismatches and current==ref else 1

def run_step(name, args):
    env=os.environ.copy()
    env.update(LLM_ENABLED='false',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONUTF8='1')
    start=time.monotonic()
    with (EV/f'm5_r01_audit_{name}.log').open('w',encoding='utf-8') as out:
        result=subprocess.run([sys.executable,*args],cwd=ROOT,env=env,stdout=out,stderr=subprocess.STDOUT,timeout=600)
    report=dict(command=[str(sys.executable),*args],exit_code=result.returncode,elapsed_seconds=round(time.monotonic()-start,3))
    (EV/f'm5_r01_audit_{name}_command.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))
    return result.returncode

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='baseline':
        raise SystemExit(baseline())
    if mode=='verify_before':
        raise SystemExit(run_step(mode,['tests/verify_isolation.py','--out',str(EV/'m5_r01_audit_verify_before.json')]))
    if mode=='full':
        raise SystemExit(run_step(mode,['-m','pytest','tests/','-q']))
    if mode=='full_retry':
        raise SystemExit(run_step(mode,['-m','pytest','tests/','-q','-o','faulthandler_timeout=45']))
    if mode=='verify_after':
        raise SystemExit(run_step(mode,['tests/verify_isolation.py','--out',str(EV/'m5_r01_audit_verify_after.json')]))
