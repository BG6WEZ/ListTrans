"""Read-only prerequisite check; writes only its new coordination evidence."""
import hashlib
import json
import re
import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / 'doc/V0.1.5/evidence'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def main():
    baseline = json.loads((EV / 'm4_r03_vm401_audit_baseline.json').read_text(encoding='utf-8'))
    sources = baseline.get('source_hashes', {})
    # Use protected source/config/test paths if baseline uses a different source key.
    if not sources:
        sources = {p: h for p, h in baseline['protected_hashes'].items()
                   if p.startswith(('app/', 'tests/', 'scripts/', 'config/', 'web/'))
                   or p in ('Dockerfile', '.dockerignore', 'requirements.txt', '.env')}
    changes = [p for p, h in sources.items() if digest(ROOT / p) != h]
    snapshot_bytes = (EV / 'm4_r03_vm401_audit_snapshot_before.json').read_bytes()
    before = json.loads(snapshot_bytes)
    current = runpy.run_path(str(ROOT / 'tests/verify_isolation.py'))['collect_snapshot']()
    entries = re.findall(r'^\| `([^`]+)` \| `([a-f0-9]{64})` \|$',
                         (EV / 'm4_r03_vm401_audit_delivery_hashes.md').read_text(encoding='utf-8'), re.M)
    active = {'doc/00_项目管理/进度总表.md', 'doc/V0.1.5/02_任务台账.md',
              'doc/V0.1.5/03_终审与版本准入.md'}
    diffs = [p for p, h in entries if digest(ROOT / p) != h]
    unexpected = [p for p in diffs if p not in active]
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    incoming = {name: digest(EV / name) for name in (
        'm4_r03_vm402_verify.py', 'm4_r03_vm402_verify.json', 'm4_r03_vm402_owner_disposition.md')}
    report = dict(date='2026-11-16', role='coordinator', head=head,
                  source_files_checked=len(sources), source_changes=changes,
                  vm401_entries_checked=len(entries), vm401_expected_active_document_differences=diffs,
                  vm401_unexpected_differences=unexpected, business_snapshot_equal=current == before,
                  business_snapshot_reference_sha256=hashlib.sha256(snapshot_bytes).hexdigest(),
                  vm402_incoming_sha256=incoming,
                  current_source_sha256={p: digest(ROOT / p) for p in sources},
                  verification_scope='read-only hashes and snapshot; no application/test/rotation execution',
                  vm402_prior_independent_rerun='90-second timeout retained; no new independent PASS claimed')
    report['verdict'] = 'PASS' if (sources and not changes and not unexpected and current == before
        and head == 'e964fb16848de9dece8e729e801b61c7d81dd341') else 'FAIL'
    (EV / 'm5_r01_dispatch_baseline.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'current_source_sha256'}, ensure_ascii=True))
    return 0 if report['verdict'] == 'PASS' else 1

if __name__ == '__main__':
    raise SystemExit(main())
