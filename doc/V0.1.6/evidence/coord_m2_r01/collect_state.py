"""Read-only coordinator baseline collection for V016-M2 R01; standard library only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent


def digest(path):
    if not path.is_file():
        return None
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(paths):
    return {
        str(p.relative_to(ROOT)).replace('\\', '/'): digest(p)
        for p in sorted(set(paths))
        if p.is_file() and '__pycache__' not in p.parts
    }


def main():
    stage = sys.argv[1]
    manifest = ROOT / 'doc/V0.1.6/evidence/m2_r01_delivery_hashes.md'
    rows = re.findall(r'\| `([^`]+)` \| (\d+) \| `([0-9a-f]{64})` \|', manifest.read_text(encoding='utf-8-sig'))
    checks = []
    for name, size_str, expected in rows:
        p = ROOT / name
        actual = digest(p)
        checks.append({
            'path': name,
            'declared_bytes': int(size_str),
            'actual_bytes': p.stat().st_size if p.is_file() else None,
            'declared': expected,
            'actual': actual,
            'match': actual == expected,
        })

    protected = []
    for folder in ('app', 'tests'):
        protected.extend((ROOT / folder).rglob('*.py'))
    for folder in ('doc/V0.1.6/rounds', 'doc/V0.1.6/evidence'):
        protected.extend(p for p in (ROOT / folder).rglob('*') if OUT not in p.parents and p != OUT)
    protected.extend(
        ROOT / name
        for name in ('Dockerfile', '.dockerignore', 'requirements.txt', 'web/admin.html', 'web/app.js', 'web/index.html')
    )

    spec = importlib.util.spec_from_file_location('coord_snapshot', ROOT / '.local-archive/business_snapshot.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = {
        'time': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'delivery': checks,
        'delivery_count': len(checks),
        'delivery_mismatches': [c for c in checks if not c['match']],
        'protected': inventory(protected),
        'business': mod.snapshot(),
    }
    if stage in ('after', 'final'):
        before = json.loads((OUT / 'state_before.json').read_text(encoding='utf-8'))
        result['protected_changes'] = sorted(
            k for k in set(before['protected']) | set(result['protected'])
            if before['protected'].get(k) != result['protected'].get(k)
        )
        result['business_unchanged'] = result['business'] == before['business']

    (OUT / f'state_{stage}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('protected', 'business', 'delivery')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
