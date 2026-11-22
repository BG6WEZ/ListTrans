"""Finalize coordination documents without executing application or test suites."""
import hashlib
import json
import re
import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / 'doc/V0.1.5/evidence'
DOCS = [
    'doc/README.md', 'doc/00_项目管理/版本路线图.md',
    'doc/00_项目管理/进度总表.md', 'doc/00_项目管理/问题与版本归属总台账.md',
    'doc/V0.1.5/01_实施方案与任务包.md', 'doc/V0.1.5/02_任务台账.md',
    'doc/V0.1.5/03_终审与版本准入.md', 'doc/V0.1.5/rounds/README.md',
    'doc/V0.1.5/evidence/README.md', 'doc/V0.1.5/rounds/V015-M5_R01_统筹下发.md',
]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def main():
    initial = json.loads((EV / 'm5_r01_dispatch_baseline.json').read_text(encoding='utf-8'))
    old = json.loads((EV / 'm4_r03_vm401_audit_baseline.json').read_text(encoding='utf-8'))
    source_changes = [p for p, h in initial['current_source_sha256'].items() if digest(ROOT / p) != h]
    old_changes = [p for p, h in old['protected_hashes'].items()
                   if p not in DOCS and digest(ROOT / p) != h]
    # Bind both the original VM401 supplement and incoming VM402 artifacts.
    entries = re.findall(r'^\| `([^`]+)` \| `([a-f0-9]{64})` \|$',
                         (EV / 'm4_r03_vm401_audit_delivery_hashes.md').read_text(encoding='utf-8'), re.M)
    vm401_changes = [p for p, h in entries if p not in DOCS and digest(ROOT / p) != h]
    vm402_changes = [p for p, h in initial['vm402_incoming_sha256'].items() if digest(EV / p) != h]
    snapshot = runpy.run_path(str(ROOT / 'tests/verify_isolation.py'))['collect_snapshot']()
    reference = json.loads((EV / 'm4_r03_vm401_audit_snapshot_before.json').read_bytes())
    broken = []
    # All links in the new assignment must exist. Other documents retain historical links.
    issue = ROOT / DOCS[-1]
    for target in re.findall(r'\]\(([^)]+)\)', issue.read_text(encoding='utf-8')):
        if not target.startswith(('http:', 'https:', '#')) and not (issue.parent / target).resolve().is_file():
            broken.append(target)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    report = dict(date='2026-11-16', role='coordinator', task='V015-M5', round='R01',
                  status='ISSUED_AWAITING_DEVELOPMENT', head=head,
                  source_files_checked=len(initial['current_source_sha256']), source_changes=source_changes,
                  old_protected_unexpected_changes=old_changes,
                  vm401_fixed_evidence_changes=vm401_changes, vm402_incoming_changes=vm402_changes,
                  business_snapshot_equal=snapshot == reference,
                  business_snapshot_reference_sha256=initial['business_snapshot_reference_sha256'],
                  broken_assignment_links=broken, active_documents_updated=DOCS,
                  application_or_test_execution=False, actual_credentials_operated=False,
                  pre_release_gate='OPEN_BEFORE_FIRST_REAL_DEPLOYMENT',
                  next_versions='V0.1.6-V0.2.0_LOCKED', version_final_review='NOT_STARTED')
    report['verdict'] = 'PASS' if (not any((source_changes, old_changes, vm401_changes, vm402_changes, broken))
        and snapshot == reference and head == initial['head']) else 'FAIL'
    (EV / 'm5_r01_dispatch_closure.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    files = sorted(set(DOCS + [str(p.relative_to(ROOT)).replace('\\', '/')
                    for p in EV.glob('m5_r01_dispatch_*') if p.is_file() and p.suffix != '.md']))
    lines = ['# V015-M5 R01 统筹下发交付SHA256', '',
             '日期：2026-11-16。仅下发/状态与只读核对，不代表开发或审核通过。清单不含自身。', '',
             '| 文件 | SHA256 |', '|---|---|']
    lines += [f'| `{p}` | `{digest(ROOT / p)}` |' for p in files]
    (EV / 'm5_r01_dispatch_delivery_hashes.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'active_documents_updated'}, ensure_ascii=True))
    print('DELIVERY_FILES=' + str(len(files)))
    return 0 if report['verdict'] == 'PASS' else 1

if __name__ == '__main__':
    raise SystemExit(main())
