# -*- coding: utf-8 -*-
"""Non-clobbering audit runner for the R02 Chrome CDP browser E2E verification."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
src_path = ROOT / "doc/V0.1.5/evidence/m5_r02_browser_e2e.py"
src = src_path.read_text(encoding="utf-8")
replacements = [
    ("m5_r02_browser_e2e_report.json", "m5_r02_audit_browser_e2e_report.json"),
    ("m5_r02_ui_rejected_restored.png", "m5_r02_audit_ui_rejected_restored.png"),
    ("m5_r02_ui_workbench.png", "m5_r02_audit_ui_workbench.png"),
    ("m5_r02_ui_export_done.png", "m5_r02_audit_ui_export_done.png"),
]
patched = src
for old, new in replacements:
    assert old in patched, f"missing {old}"
    patched = patched.replace(old, new)

exec(compile(patched, str(src_path), "exec"), {"__file__": str(src_path), "__name__": "__main__"})
