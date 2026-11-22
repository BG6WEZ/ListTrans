# -*- coding: utf-8 -*-
"""独立审核运行原 Chrome E2E，仅重定向截图和报告，保留原检查。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "doc/V0.1.6/evidence/m0_r01_browser_e2e.py"
OUT_DIR = ROOT / "doc/V0.1.6/evidence/audit_m0_r01"
OUT_DIR.mkdir(parents=True, exist_ok=True)
source = SOURCE.read_text(encoding="utf-8")
for filename in (
    "m0_r01_browser_e2e_report.json",
    "m0_r01_ui_alice_restored.png",
    "m0_r01_ui_bob_workbench.png",
):
    old = f'EVIDENCE_DIR / "{filename}"'
    new = f'EVIDENCE_DIR / "audit_m0_r01" / "{filename}"'
    assert source.count(old) == 1, filename
    source = source.replace(old, new)
exec(compile(source, str(SOURCE), "exec"), {"__name__": "__main__", "__file__": str(SOURCE)})
