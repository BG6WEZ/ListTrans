"""Bind inherited M2 evidence, pre-backups, frozen assets, and read-only AUDIT-SIDE-02 facts."""
import difflib
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent


def sha(p: Path) -> str:
    with p.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    # 1. Pre-baseline 19 backup checks & unified diff
    pre = ROOT / 'doc/V0.1.6/evidence/m2_r01_pre_baseline_hashes.md'
    pre_rows = re.findall(r'\| `([^`]+)` \| (\d+) \| `([0-9a-f]{64})` \|', pre.read_text(encoding='utf-8-sig'))
    pre_checks = []
    diffs = []
    for name, size_str, digest in pre_rows:
        p = ROOT / '.local-archive/v016_m2_r01_pre_backup' / name
        match = p.is_file() and sha(p) == digest
        pre_checks.append({
            'path': name,
            'declared_bytes': int(size_str),
            'actual_bytes': p.stat().st_size if p.is_file() else None,
            'match': match,
        })
        if p.is_file() and (ROOT / name).is_file():
            a = p.read_text(encoding='utf-8-sig').splitlines()
            b = (ROOT / name).read_text(encoding='utf-8-sig').splitlines()
            diff = list(difflib.unified_diff(a, b, fromfile='pre/' + name, tofile=name, n=3))
            diffs.extend(diff)
    (OUT / 'm2_only_review.diff').write_text('\n'.join(diffs) + ('\n' if diffs else ''), encoding='utf-8')

    # 2. Check M1 final frozen hashes for remaining M2-touched files not in 19-item pre-backup
    m1_final = json.loads((ROOT / 'doc/V0.1.6/evidence/coord_m1_r01/state_final.json').read_text(encoding='utf-8'))
    m1_binding = json.loads((ROOT / 'doc/V0.1.6/evidence/coord_m1_r01/evidence_binding.json').read_text(encoding='utf-8'))
    extra_pre_from_m1 = {}
    for rel in (
        'tests/test_auto_binding.py',
        'tests/test_binding_decisions.py',
        'tests/test_m3_hard_constraints_http.py',
        'tests/test_v0_0_8_llm_rules.py',
    ):
        extra_pre_from_m1[rel] = m1_final.get('protected', {}).get(rel)

    # 3. Frozen offline model/fixture assets (43 items from M1 evidence_binding)
    assets = {}
    for folder in ('model_offline', 'tests/fixtures'):
        for p in sorted((ROOT / folder).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:
                assets[p.relative_to(ROOT).as_posix()] = sha(p)
    asset_mismatches = [
        k for k in set(m1_binding.get('assets', {})) | set(assets)
        if m1_binding.get('assets', {}).get(k) != assets.get(k)
    ]

    # 4. Developer 9 extra evidence files vs delivered_evidence_opening_hashes.log
    opening_log = ROOT / 'doc/V0.1.6/evidence/audit_m2_r01/delivered_evidence_opening_hashes.log'
    dev_evid_checks = []
    for line in opening_log.read_text(encoding='utf-8').splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            fname, expected_sha = parts
            fp = ROOT / 'doc/V0.1.6/evidence' / fname
            actual_sha = sha(fp) if fp.is_file() else None
            dev_evid_checks.append({
                'file': f'doc/V0.1.6/evidence/{fname}',
                'declared': expected_sha,
                'actual': actual_sha,
                'match': actual_sha == expected_sha,
            })

    # 5. Audit M2 R01 evidence inventory (16 files)
    audit_evid = {
        p.relative_to(ROOT).as_posix(): sha(p)
        for p in sorted((ROOT / 'doc/V0.1.6/evidence/audit_m2_r01').iterdir())
        if p.is_file()
    }

    # 6. Cross-check developer gate report vs auditor gate report
    dev_gate = json.loads((ROOT / 'doc/V0.1.6/evidence/m2_r01_gate_reports.json').read_text(encoding='utf-8'))
    aud_gate = json.loads((ROOT / 'doc/V0.1.6/evidence/audit_m2_r01/audit_gate_reports.json').read_text(encoding='utf-8'))
    comparisons_equal = dev_gate.get('comparisons') == aud_gate.get('comparisons')
    probes_equal = dev_gate.get('probe_gt_decisions') == aud_gate.get('probe_gt_decisions')

    # 7. Read-only SQLite logical verification for AUDIT-SIDE-02
    mat_uri = f"file:{(ROOT / 'app/materials.db').as_posix()}?mode=ro"
    auth_uri = f"file:{(ROOT / 'app/auth.db').as_posix()}?mode=ro"
    mat_counts = {}
    conn_m = sqlite3.connect(mat_uri, uri=True)
    try:
        for tbl in (
            'materials', 'materials_fts', 'materials_metadata', 'llm_rules',
            'rule_audit_logs', 'rule_eval_runs', 'flywheel_rule_suggestions',
            'flywheel_config_snapshots', 'flywheel_records',
        ):
            mat_counts[tbl] = conn_m.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
        mat_meta = [dict(zip(('key', 'value', 'updated_at'), r)) for r in conn_m.execute('SELECT key, value, updated_at FROM flywheel_schema_meta').fetchall()]
        llm_prov = [
            {'rule_id': r[0], 'status': r[1], 'governance_provenance': r[2], 'migration_version': r[3]}
            for r in conn_m.execute('SELECT rule_id, status, governance_provenance, migration_version FROM llm_rules ORDER BY id').fetchall()
        ]
    finally:
        conn_m.close()

    auth_counts = {}
    conn_a = sqlite3.connect(auth_uri, uri=True)
    try:
        for tbl in ('users', 'audit_logs', 'work_sessions', 'active_heartbeats', 'issued_temp_credentials', 'revoked_tokens'):
            auth_counts[tbl] = conn_a.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
        m2_adm_left = conn_a.execute("SELECT COUNT(*) FROM users WHERE username LIKE 'm2_%' OR account_epoch LIKE 'audit%'").fetchone()[0]
    finally:
        conn_a.close()

    result = {
        'pre_backup_checks': pre_checks,
        'pre_backup_match_count': sum(1 for c in pre_checks if c['match']),
        'pre_backup_total': len(pre_checks),
        'pre_hashes_from_m1_final': extra_pre_from_m1,
        'assets': assets,
        'assets_total': len(assets),
        'asset_mismatches_vs_m1': asset_mismatches,
        'delivered_extra_evidence_checks': dev_evid_checks,
        'delivered_extra_evidence_all_match': all(c['match'] for c in dev_evid_checks),
        'audit_evidence': audit_evid,
        'audit_evidence_count': len(audit_evid),
        'gate_cross_check': {
            'comparisons_equal': comparisons_equal,
            'probe_gt_decisions_equal': probes_equal,
            'comparisons': aud_gate.get('comparisons'),
            'probe_gt_decisions': aud_gate.get('probe_gt_decisions'),
        },
        'audit_side_02_readonly_db_check': {
            'materials_db_counts': mat_counts,
            'flywheel_schema_meta': mat_meta,
            'llm_rules_provenance': llm_prov,
            'auth_db_counts': auth_counts,
            'residual_audit_users': m2_adm_left,
            'hash_chain': {
                'app/materials.db': {
                    'm1_closing': m1_final['business']['files']['app/materials.db'],
                    'audit_intermediate_10_45': '8c005e59929ea9c0e22464b23f21fdd467d4fbea5193198f4bcac6e564dc1093',
                    'current_final': sha(ROOT / 'app/materials.db'),
                },
                'app/auth.db': {
                    'm1_closing': m1_final['business']['files']['app/auth.db'],
                    'audit_intermediate_10_45': 'e0125622eda7d3b04fbeb197bff61de13de45800d5eab978dbff581ac6a61c29',
                    'current_final': sha(ROOT / 'app/auth.db'),
                },
                'logs/security.log': {
                    'm1_closing': m1_final['business']['trees']['logs']['files']['security.log'],
                    'current_final': sha(ROOT / 'logs/security.log'),
                },
            },
        },
        'wording_correction': (
            'Audit summary section 1 states 12 pre-backup files matched 12/12; '
            'actual m2_r01_pre_baseline_hashes.md and .local-archive/v016_m2_r01_pre_backup/ contain 19 items '
            '(12 app/ + 4 tests/ + 3 web/), and all 19/19 match. '
            'Original audit summary is preserved without silent modification.'
        ),
    }

    (OUT / 'evidence_binding.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        'pre_backup': f"{result['pre_backup_match_count']}/{result['pre_backup_total']}",
        'assets_total': result['assets_total'],
        'asset_mismatches': len(asset_mismatches),
        'dev_evid_match': f"{sum(1 for c in dev_evid_checks if c['match'])}/{len(dev_evid_checks)}",
        'audit_evid_count': result['audit_evidence_count'],
        'comparisons_equal': comparisons_equal,
        'probes_equal': probes_equal,
        'residual_audit_users': m2_adm_left,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
