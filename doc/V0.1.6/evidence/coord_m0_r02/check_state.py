"""Coordinator-only, standard-library baseline and business snapshot checks.

Does not import app modules, execute tests, or modify earlier evidence.
"""
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
LEDGER = "doc/V0.1.6/02_任务台账.md"
MANIFEST = ROOT / "doc/V0.1.6/evidence/m0_r02_delivery_hashes.md"


def digest(path):
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def write_new(path, obj):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2)


mode = sys.argv[1]
assert mode in ("before", "after")
spec = importlib.util.spec_from_file_location("coord_snapshot", ROOT / "tests/verify_isolation.py")
snapshot_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot_module)
snapshot = snapshot_module.collect_snapshot()
write_new(OUT / f"business_{mode}.json", snapshot)
rows = []
for line in MANIFEST.read_text(encoding="utf-8").splitlines():
    match = re.match(r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*([0-9a-f]{64})\s*\|$", line.strip())
    if match:
        rel, size, sha = match.groups()
        actual = digest(ROOT / rel)
        rows.append({"path": rel, "expected": {"bytes": int(size), "sha256": sha},
                     "actual": actual, "match": actual == {"bytes": int(size), "sha256": sha}})

protected = set()
for pattern in ("m0_r01_*", "m0_r02_*"):
    protected.update(p for p in (ROOT / "doc/V0.1.6/evidence").glob(pattern) if p.is_file())
for directory in ("audit_m0_r01", "audit_m0_r02"):
    protected.update(p for p in (ROOT / "doc/V0.1.6/evidence" / directory).rglob("*") if p.is_file())
protected.update(p for p in (ROOT / "doc/V0.1.6/rounds").glob("*小结.md"))
protected.update(ROOT / row["path"] for row in rows if row["path"] != LEDGER)
protected.update((ROOT / "app").rglob("*.py"))
protected.update((ROOT / "tests").rglob("*.py"))
protected_hashes = {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(protected)}
audit_snapshot = json.loads((ROOT / "doc/V0.1.6/evidence/audit_m0_r02/outer_snapshot_after.json").read_text(encoding="utf-8"))
report = {"mode": mode, "delivery_total": len(rows), "delivery_matched": sum(row["match"] for row in rows),
          "delivery_results": rows, "protected": protected_hashes,
          "audit_snapshot_changes": snapshot_module.diff_snapshots(audit_snapshot, snapshot),
          "audit_r02_file_count": len(list((ROOT / "doc/V0.1.6/evidence/audit_m0_r02").iterdir()))}
if mode == "before":
    report["ok"] = len(rows) == 28 and all(row["match"] for row in rows) and not report["audit_snapshot_changes"]
else:
    before = json.loads((OUT / "state_before.json").read_text(encoding="utf-8"))
    prior_snapshot = json.loads((OUT / "business_before.json").read_text(encoding="utf-8"))
    report["business_changes"] = snapshot_module.diff_snapshots(prior_snapshot, snapshot)
    report["protected_changes"] = [rel for rel in sorted(set(before["protected"]) | set(protected_hashes))
                                   if before["protected"].get(rel) != protected_hashes.get(rel)]
    report["accepted_management_changes"] = [row["path"] for row in rows if not row["match"] and row["path"] == LEDGER]
    report["ok"] = (not report["business_changes"] and not report["protected_changes"]
                    and not report["audit_snapshot_changes"]
                    and all(row["match"] or row["path"] == LEDGER for row in rows))
write_new(OUT / f"state_{mode}.json", report)
print(json.dumps({key: value for key, value in report.items() if key not in ("delivery_results", "protected")}, ensure_ascii=False))
raise SystemExit(0 if report["ok"] else 2)
