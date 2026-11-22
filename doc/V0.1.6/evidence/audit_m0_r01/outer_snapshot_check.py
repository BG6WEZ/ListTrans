# -*- coding: utf-8 -*-
"""审核自有：外层业务快照后置复核（只读，不导入 app.*，无写入副作用）。

复用 tests/verify_isolation.py 的纯标准库快照函数，
与 audit_m0_r01/outer_snapshot_before.json 逐项比对。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
AUDIT_DIR = Path(__file__).resolve().parent
VERIFY_SCRIPT = PROJECT_ROOT / "tests" / "verify_isolation.py"

spec = importlib.util.spec_from_file_location("verify_isolation_audit", VERIFY_SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

before_path = AUDIT_DIR / "outer_snapshot_before.json"
after_path = AUDIT_DIR / "outer_snapshot_after.json"
report_path = AUDIT_DIR / "outer_snapshot_diff.json"

before = json.loads(before_path.read_text(encoding="utf-8"))
after = mod.collect_snapshot()
after_path.write_text(json.dumps(after, ensure_ascii=False, indent=1), encoding="utf-8")

changes = mod.diff_snapshots(before, after)
report = {
    "before": before_path.name,
    "after": after_path.name,
    "unchanged": not changes,
    "changes": changes,
}
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=1))
sys.exit(0 if not changes else 2)
