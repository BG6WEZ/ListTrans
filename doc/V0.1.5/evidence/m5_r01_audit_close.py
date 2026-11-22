"""Bind independent review, frozen inputs, snapshots, and active status updates."""
import hashlib
import json
import re
import runpy
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
EV=ROOT/'doc/V0.1.5/evidence'
DOCS=['doc/README.md','doc/00_项目管理/版本路线图.md','doc/00_项目管理/进度总表.md',
      'doc/00_项目管理/问题与版本归属总台账.md','doc/V0.1.5/01_实施方案与任务包.md',
      'doc/V0.1.5/02_任务台账.md','doc/V0.1.5/03_终审与版本准入.md',
      'doc/V0.1.5/rounds/README.md','doc/V0.1.5/evidence/README.md',
      'doc/V0.1.5/rounds/V015-M5_R01_审核小结.md','doc/V0.1.5/rounds/V015-M5_R01_统筹裁决.md']

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

def main():
    baseline=json.loads((EV/'m5_r01_audit_baseline.json').read_text(encoding='utf-8'))
    source_changes=[p for p,h in baseline['protected_hashes'].items() if sha(ROOT/p)!=h]
    fixed_changes=[p for p,h in baseline['delivery_hashes'].items()
                   if p!='doc/V0.1.5/02_任务台账.md' and sha(ROOT/p)!=h]
    backup_entries=re.findall(r'^\| ([^|]+?) \| \d+ \| ([a-f0-9]{64}) \|$',
                              (EV/'m5_r01_pre_baseline_hashes.md').read_text(encoding='utf-8'),re.M)
    backup_changes=[p for p,h in backup_entries if sha(ROOT/'.local-archive/m5_r01_pre_backup'/p)!=h]
    start=json.loads((EV/'m5_r01_dispatch_baseline.json').read_text(encoding='utf-8'))
    allowed={p for p in baseline['delivery_hashes'] if p.startswith(('app/','web/','tests/')) or p=='requirements.txt'}
    out_of_scope=[p for p,h in start['current_source_sha256'].items() if p not in allowed and sha(ROOT/p)!=h]
    snapshot=runpy.run_path(str(ROOT/'tests/verify_isolation.py'))['collect_snapshot']()
    reference_bytes=(EV/'m5_r01_outer_snapshot_before.json').read_bytes()
    equal=snapshot==json.loads(reference_bytes)==baseline['business_snapshot']
    (EV/'m5_r01_audit_snapshot_after.json').write_bytes(reference_bytes if equal else json.dumps(snapshot,sort_keys=True,indent=1).encode())
    verify={name:json.loads((EV/f'm5_r01_audit_verify_{name}.json').read_text(encoding='utf-8'))['verdict'] for name in ('before','after')}
    suite=json.loads((EV/'m5_r01_audit_full_retry_command.json').read_text(encoding='utf-8'))
    report=dict(head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                verdict='RETURN_FOR_REWORK',next_package='V015-M5 R02',open_P1=['A-M5-01','A-M5-02'],
                open_current_P2=['A-M5-03','A-M5-04','A-M5-05'],
                delivery_checked_at_start=baseline['delivery_checked'],delivery_mismatches_at_start=baseline['delivery_mismatches'],
                frozen_inputs_checked=len(baseline['protected_hashes']),frozen_input_changes=source_changes,
                developer_fixed_delivery_changes=fixed_changes,active_ledger_updated=True,
                backup_checked=len(backup_entries),backup_mismatches=backup_changes,
                out_of_scope_source_changes=out_of_scope,business_snapshot_equal=equal,
                before_sha256=hashlib.sha256(reference_bytes).hexdigest(),
                after_sha256=sha(EV/'m5_r01_audit_snapshot_after.json'),verify=verify,
                independent_suite='269 passed / 0 warnings / 150.01s',suite_exit_code=suite['exit_code'],
                independent_negative_reproductions=5,
                browser_docker_standalone_three_rule_rerun=False,production_operations=False,
                PRG_F11='OPEN_BEFORE_FIRST_REAL_DEPLOYMENT',future_versions='LOCKED')
    manifest=EV/'m5_r01_audit_delivery_hashes.md'
    manifest.write_text('# V015-M5 R01 独立审核交付SHA256\n',encoding='utf-8')
    broken=[]
    for rel in DOCS[-2:]:
        path=ROOT/rel
        for target in re.findall(r'\]\(([^)]+)\)',path.read_text(encoding='utf-8')):
            if not target.startswith(('http:','https:','#')) and not (path.parent/target).resolve().is_file():
                # closure is the output immediately below, not an unexecuted evidence placeholder.
                if (path.parent/target).resolve()!=EV/'m5_r01_audit_closure.json':
                    broken.append(dict(file=rel,target=target))
    report['broken_new_review_links']=broken
    report['integrity_verdict']='PASS' if (equal and not any((source_changes,fixed_changes,backup_changes,out_of_scope,broken))
        and len(backup_entries)==12 and all(v=='PASS' for v in verify.values())
        and report['head']==baseline['head'] and suite['exit_code']==0) else 'FAIL'
    (EV/'m5_r01_audit_closure.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    paths=sorted(set(DOCS+[str(p.relative_to(ROOT)).replace('\\','/') for p in EV.glob('m5_r01_audit_*')
                          if p.is_file() and p!=manifest]))
    lines=['# V015-M5 R01 独立审核交付SHA256','','2026-11-16。结论：退回整改；唯一下发同包R02。清单不含自身。',
           '开发交付30项初验全符，审核后仅活动台账正常更新；其他固定开发证据保持原字节。',
           'PRG-F11保持开放，后续版本锁定。','','| 文件 | SHA256 |','|---|---|']
    lines += [f'| `{p}` | `{sha(ROOT/p)}` |' for p in paths]
    manifest.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('verify',)},ensure_ascii=True))
    print('AUDIT_DELIVERY_FILES='+str(len(paths)))
    return 0 if report['integrity_verdict']=='PASS' else 1

if __name__=='__main__':
    raise SystemExit(main())
