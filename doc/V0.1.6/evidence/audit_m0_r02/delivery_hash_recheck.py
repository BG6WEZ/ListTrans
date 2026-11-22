# -*- coding: utf-8 -*-
"""审核自有：R02 交付 SHA256 清单逐项复核（只读）。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
AUDIT_DIR = Path(__file__).resolve().parent
MANIFEST = PROJECT_ROOT / "doc" / "V0.1.6" / "evidence" / "m0_r02_delivery_hashes.md"
REPORT = AUDIT_DIR / "delivery_hash_recheck.json"

row_re = re.compile(r"^\|\s*(?P<path>[^|]+?)\s*\|\s*(?P<bytes>\d+)\s*\|\s*(?P<sha>[0-9a-f]{64})\s*\|$")
items = []
for line in MANIFEST.read_text(encoding="utf-8").splitlines():
    m = row_re.match(line.strip())
    if m:
        items.append((m.group("path"), int(m.group("bytes")), m.group("sha")))

results = []
all_ok = True
for rel, expect_bytes, expect_sha in items:
    p = PROJECT_ROOT / rel
    if not p.is_file():
        results.append({"path": rel, "status": "missing"})
        all_ok = False
        continue
    data = p.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    ok = (len(data) == expect_bytes and actual_sha == expect_sha)
    all_ok = all_ok and ok
    results.append({
        "path": rel,
        "status": "match" if ok else "mismatch",
        "expected_bytes": expect_bytes,
        "actual_bytes": len(data),
        "expected_sha256": expect_sha,
        "actual_sha256": actual_sha,
    })

report = {
    "manifest": str(MANIFEST.relative_to(PROJECT_ROOT)).replace("\\", "/"),
    "total": len(results),
    "matched": sum(1 for r in results if r["status"] == "match"),
    "all_ok": all_ok,
    "results": results,
}
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: report[k] for k in ("total", "matched", "all_ok")}, ensure_ascii=False))
for r in results:
    if r["status"] != "match":
        print(json.dumps(r, ensure_ascii=False))
raise SystemExit(0 if all_ok else 2)
