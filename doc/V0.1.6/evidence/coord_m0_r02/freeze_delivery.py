"""Freeze coordinator artifacts only; do not include this manifest in itself."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
management = [
    "doc/README.md",
    "doc/00_项目管理/版本路线图.md",
    "doc/00_项目管理/进度总表.md",
    "doc/00_项目管理/问题与版本归属总台账.md",
    "doc/V0.1.6/01_实施方案与任务包.md",
    "doc/V0.1.6/02_任务台账.md",
    "doc/V0.1.6/03_终审与版本准入.md",
    "doc/V0.1.6/rounds/V016-M0_R02_统筹裁决.md",
]
links = []
for rel in management:
    path = ROOT / rel
    for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        if "://" in target or target.startswith("#"):
            continue
        resolved = path.parent / target.split("#")[0]
        # The manifest below is created only after checks and is deliberately not self-hashed.
        if resolved.resolve() == (OUT / "coordinator_delivery.json").resolve():
            continue
        if not resolved.exists():
            links.append({"source": rel, "target": target})
assert not links, links
state = json.loads((OUT / "state_after.json").read_text(encoding="utf-8"))
isolation = json.loads((OUT / "isolation_verify_after.json").read_text(encoding="utf-8"))
assert state["ok"] and isolation["verdict"] == "PASS"
files = [ROOT / rel for rel in management]
files += [path for path in sorted(OUT.iterdir()) if path.is_file() and path.name != "coordinator_delivery.json"]
entries = []
for path in files:
    data = path.read_bytes()
    entries.append({"path": path.relative_to(ROOT).as_posix(), "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest()})
before = (OUT / "business_before.json").read_bytes()
after = (OUT / "business_after.json").read_bytes()
report = {"date": "2026-11-17 Asia/Shanghai", "scope": "coordinator diagnostics and management only",
          "manifest_excludes_itself": True, "files": entries,
          "business_before_sha256": hashlib.sha256(before).hexdigest(),
          "business_after_sha256": hashlib.sha256(after).hexdigest(),
          "business_bytes_identical": before == after, "broken_management_links": links}
assert before == after
with (OUT / "coordinator_delivery.json").open("x", encoding="utf-8") as stream:
    json.dump(report, stream, ensure_ascii=False, indent=2)
print(json.dumps({"files": len(entries), "business_bytes_identical": before == after,
                  "protected_changes": state["protected_changes"],
                  "old_delivery_matches": state["delivery_matched"],
                  "accepted_management_changes": state["accepted_management_changes"],
                  "isolation": isolation["verdict"], "broken_management_links": links}, ensure_ascii=False))
