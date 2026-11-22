# -*- coding: utf-8 -*-
"""Non-clobbering audit runner for the R02 offline 3-rule flywheel gate."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
src = (ROOT / "doc/V0.1.5/evidence/m5_r02_3rule_gate.py").read_text(encoding="utf-8")
patched = src.replace(
    '"doc/V0.1.5/evidence/m5_r02_gate_reports.json"',
    '"doc/V0.1.5/evidence/m5_r02_audit_gate_reports.json"',
)
assert patched != src
exec(compile(patched, str(ROOT / "doc/V0.1.5/evidence/m5_r02_3rule_gate.py"), "exec"), {"__file__": str(ROOT / "doc/V0.1.5/evidence/m5_r02_3rule_gate.py"), "__name__": "__main__"})
