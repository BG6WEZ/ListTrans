# -*- coding: utf-8 -*-
"""审核自有：R02 外层业务快照采集/比对（纯标准库只读，不导入 app.*）。

用法：
  python snapshot_check.py before
  python snapshot_check.py after   # 与 before 逐项 diff
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
AUDIT_DIR = Path(__file__).resolve().parent
VERIFY_SCRIPT = PROJECT_ROOT / "tests" / "verify_isolation.py"

spec = importlib.util.spec_from_file_location("verify_isolation_audit_r02", VERIFY_SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

mode = sys.argv[1] if len(sys.argv) > 1 else "before"
snap_path = AUDIT_DIR / f"outer_snapshot_{mode}.json"
snap = mod.collect_snapshot()
snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {snap_path.name} ({snap_path.stat().st_size} bytes)")

if mode == "after":
    before = json.loads((AUDIT_DIR / "outer_snapshot_before.json").read_text(encoding="utf-8"))
    changes = mod.diff_snapshots(before, snap)
    report = {"unchanged": not changes, "changes": changes}
    report_path = AUDIT_DIR / "outer_snapshot_diff.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    sys.exit(0 if not changes else 2)
