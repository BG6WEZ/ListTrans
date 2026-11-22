"""Bind inherited evidence and frozen assets without importing application modules."""
import difflib
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
def sha(p):
    with p.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

pre = ROOT / 'doc/V0.1.6/evidence/m1_r01_pre_baseline_hashes.md'
checks = []
diffs = []
for name, size, digest in re.findall(r'\| (app/[^ |]+) \| (\d+) \| ([0-9a-f]{64}) \|', pre.read_text(encoding='utf-8-sig')):
    p = ROOT / '.local-archive/v016_m1_r01_pre_backup' / name
    checks.append({'path': name, 'match': p.exists() and sha(p) == digest})
    if p.exists():
        diff = list(difflib.unified_diff(p.read_text(encoding='utf-8-sig').splitlines(), (ROOT / name).read_text(encoding='utf-8-sig').splitlines(), fromfile='pre/'+name, tofile=name))
        diffs.extend(diff)
(OUT / 'm1_only_review.diff').write_text('\n'.join(diffs)+'\n', encoding='utf-8')
gate = json.loads((ROOT / 'doc/V0.1.6/evidence/m1_r01_gate_reports.json').read_text(encoding='utf-8'))
assets = {}
for folder in ('model_offline', 'tests/fixtures'):
    for p in sorted((ROOT / folder).rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:
            assets[p.relative_to(ROOT).as_posix()] = sha(p)
result = {
    'pre_backup_checks': checks,
    'assets': assets,
    'gate_regenerated_sha256': sha(ROOT / 'doc/V0.1.6/evidence/m1_r01_gate_reports.json'),
    'gate_generated': gate['generated'],
    'gate_comparisons': {k:v for k,v in gate.items() if 'diff' in k},
    'reports': {k:v.get('passed') for k,v in gate['reports'].items()} if isinstance(gate['reports'],dict) else gate['reports'],
    'audit_evidence': {p.relative_to(ROOT).as_posix():sha(p) for p in sorted((ROOT/'doc/V0.1.6/evidence/audit_m1_r01').iterdir()) if p.is_file()},
    'qualification': 'Original developer gate JSON is unavailable and its original bytes cannot be revalidated. Current JSON is auditor-regenerated evidence; intact developer script/log and independent auditor log are separate sources. No coordinator rerun of gate/full suite/browser is claimed.',
    'wording_correction': '15 core items = 13 code + 2 tests; docs are separate. Current delivery is 31/33 matches: approved auditor ledger update plus AUDIT-SIDE-01. trusted_flywheel_where_clause checks payload_digest and ownership, but does not explicitly test empty client_event_id; M0 ingestion validates IDs and schema is UNIQUE NOT NULL. Do not repeat the audit prose as proof of an explicit non-empty-ID SQL predicate.',
}
(OUT/'evidence_binding.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pre_backup_match':sum(c['match'] for c in checks),'pre_backup_total':len(checks),'asset_count':len(assets),'gate_comparisons':result['gate_comparisons']},ensure_ascii=False))
