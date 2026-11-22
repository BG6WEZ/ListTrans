# -*- coding: utf-8 -*-
"""V016-M0 R03 独立审核：交付清单哈希重算 + R02 冻结文件 + 改前备份核对。

只读脚本：解析开发交付 Markdown 清单，逐项重算字节数与 SHA256；
另核 R02 清单中声称 R03 零改动的文件、.local-archive 改前备份及 llm_client.py。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EVID = ROOT / "doc" / "V0.1.6" / "evidence"
AUDIT_DIR = EVID / "audit_m0_r03"
DELIVERY_MD = EVID / "m0_r03_delivery_hashes.md"
R02_MD = EVID / "m0_r02_delivery_hashes.md"
PRE_MD = EVID / "m0_r03_pre_baseline_hashes.md"
BACKUP = ROOT / ".local-archive" / "v016_m0_r03_pre_backup"

ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(\d+)\s*\|\s*([0-9a-f]{64})\s*\|\s*$")


def parse_table(md_path: Path) -> list[dict]:
    items = []
    for line in md_path.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if m:
            items.append({"file": m.group(1), "size": int(m.group(2)), "sha256": m.group(3)})
    return items


def sha256_file(path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return size, h.hexdigest()


def recheck(items: list[dict], base: Path) -> list[dict]:
    rows = []
    for item in items:
        p = base / item["file"]
        if not p.exists():
            rows.append({**item, "actual_size": None, "actual_sha256": None, "ok": False, "reason": "missing"})
            continue
        actual_size, actual_sha = sha256_file(p)
        rows.append({
            **item,
            "actual_size": actual_size,
            "actual_sha256": actual_sha,
            "ok": actual_size == item["size"] and actual_sha == item["sha256"],
            "reason": "",
        })
    return rows


def main() -> int:
    delivery = parse_table(DELIVERY_MD)
    rows = recheck(delivery, ROOT)
    matched = sum(1 for r in rows if r["ok"])

    # R02→R03：8 项声明零改动，4 项声明有改动（server/app.js/test_v016/台账）
    r02 = {r["file"]: r for r in parse_table(R02_MD)}
    r03 = {r["file"]: r for r in delivery}
    expected_changed = {
        "app/server.py",
        "web/app.js",
        "tests/test_v016_m0_feedback_contract.py",
        "doc/V0.1.6/02_任务台账.md",
    }
    frozen = []
    changed = []
    for rel, r02_item in r02.items():
        if rel not in r03:
            continue
        same = r03[rel]["sha256"] == r02_item["sha256"] and r03[rel]["size"] == r02_item["size"]
        entry = {"file": rel, "r02_sha256": r02_item["sha256"], "r03_sha256": r03[rel]["sha256"], "unchanged": same}
        (changed if rel in expected_changed else frozen).append(entry)
    frozen_all_unchanged = all(f["unchanged"] for f in frozen)
    changed_all_changed = all(not c["unchanged"] for c in changed) and len(changed) == len(expected_changed)

    # 改前备份：备份文件哈希必须等于 pre_baseline 清单；
    # 6 项整改对象当前必须已变更；llm_client.py 为保护参照，当前必须与备份一致（下方单列）。
    pre_items = parse_table(PRE_MD)
    pre_rows = []
    for item in pre_items:
        rel = item["file"]
        bp = BACKUP / rel
        b_size, b_sha = sha256_file(bp) if bp.exists() else (None, None)
        c_size, c_sha = sha256_file(ROOT / rel) if (ROOT / rel).exists() else (None, None)
        entry = {
            "file": rel,
            "backup_matches_manifest": b_size == item["size"] and b_sha == item["sha256"],
            "current_sha256": c_sha,
        }
        if rel == "app/llm_client.py":
            entry["current_unchanged_from_backup"] = c_sha == item["sha256"]
        else:
            entry["current_changed_from_backup"] = c_sha != item["sha256"]
        pre_rows.append(entry)

    def pre_ok(r: dict) -> bool:
        if not r["backup_matches_manifest"]:
            return False
        return r.get("current_unchanged_from_backup", False) if r["file"] == "app/llm_client.py" else r.get("current_changed_from_backup", False)

    # llm_client.py 在改前备份但不在 R03 交付清单：当前必须与备份逐字节一致
    llm_size, llm_sha = sha256_file(ROOT / "app" / "llm_client.py")
    llm_unchanged = llm_sha == "e4971eb95647c6c0e1b606145f1f76e120c7ad0d126b17339041001e4859c51b"

    report = {
        "delivery_total": len(delivery),
        "delivery_matched": matched,
        "all_ok": matched == len(delivery),
        "rows": rows,
        "frozen_vs_r02": frozen,
        "changed_vs_r02": changed,
        "frozen_all_unchanged": frozen_all_unchanged,
        "changed_all_changed": changed_all_changed,
        "pre_backup": pre_rows,
        "pre_backup_all_ok": all(pre_ok(r) for r in pre_rows),
        "llm_client_unchanged": llm_unchanged,
        "llm_client_sha256": llm_sha,
        "llm_client_size": llm_size,
    }
    out = AUDIT_DIR / "delivery_hash_recheck.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"delivery {matched}/{len(delivery)} matched")
    print(f"frozen_vs_r02 all_unchanged={frozen_all_unchanged} ({len(frozen)} files)")
    print(f"changed_vs_r02 all_changed={changed_all_changed} ({len(changed)} files)")
    print(f"pre_backup all_ok={report['pre_backup_all_ok']}")
    print(f"llm_client_unchanged={llm_unchanged}")
    bad = [r["file"] for r in rows if not r["ok"]]
    if bad:
        print("MISMATCH:", bad)
    return 0 if (
        report["all_ok"]
        and frozen_all_unchanged
        and changed_all_changed
        and report["pre_backup_all_ok"]
        and llm_unchanged
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
