# -*- coding: utf-8 -*-
"""Verify zero existing test assertions were dropped compared with R02 baseline (.local-archive/m4_r03_pre_backup/)."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PRE_BACKUP = ROOT / ".local-archive" / "m4_r03_pre_backup"
EV = ROOT / "doc" / "V0.1.5" / "evidence"

FILES = [
    "tests/test_auto_binding.py",
    "tests/test_binding_decisions.py",
    "tests/test_m3_hard_constraints.py",
    "tests/test_m3_hard_constraints_http.py",
    "tests/test_v0_0_8_llm_rules.py",
    "tests/test_m4_auth_lifecycle.py",
    "tests/test_m4_permissions_and_tasks.py",
]


def _extract_assertions(path: Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            results.append(ast.dump(node))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr.startswith("assert"):
                results.append(ast.dump(node))
    return results


report = {"files": {}, "old_assertions_missing": 0}
for rel in FILES:
    old_file = PRE_BACKUP / rel
    cur_file = ROOT / rel
    old_list = _extract_assertions(old_file)
    cur_list = _extract_assertions(cur_file)
    cur_counts = {}
    for a in cur_list:
        cur_counts[a] = cur_counts.get(a, 0) + 1
    missing = []
    for a in old_list:
        if cur_counts.get(a, 0) > 0:
            cur_counts[a] -= 1
        else:
            missing.append(a[:120])
    report["files"][rel] = {
        "old_count": len(old_list),
        "new_count": len(cur_list),
        "missing_count": len(missing),
        "missing_samples": missing[:5],
    }
    report["old_assertions_missing"] += len(missing)

out = EV / "m4_r03_test_assertions_check.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"old_assertions_missing": report["old_assertions_missing"], "files": {k: (v["old_count"], v["new_count"], v["missing_count"]) for k, v in report["files"].items()}}, ensure_ascii=False))
assert report["old_assertions_missing"] == 0, report
