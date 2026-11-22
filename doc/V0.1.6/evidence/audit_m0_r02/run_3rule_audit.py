# -*- coding: utf-8 -*-
"""独立审核运行原三规则脚本，仅重定向报告并强制核验冻结基线（R02）。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "doc/V0.1.6/evidence/m0_r02_3rule_gate.py"
OUT_DIR = ROOT / "doc/V0.1.6/evidence/audit_m0_r02"
OUT_DIR.mkdir(parents=True, exist_ok=True)

baseline_manifest = {}
for label, rel in (
    ("m3_r04", "doc/V0.1.5/evidence/m3_r04_gate_reports.json"),
    ("m4_r03", "doc/V0.1.5/evidence/m4_r03_gate_reports.json"),
    ("m5_r04", "doc/V0.1.5/evidence/m5_r04_gate_reports.json"),
):
    path = ROOT / rel
    assert path.is_file(), f"missing frozen baseline: {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    reports = data.get("reports") or {}
    assert set(("harmful", "beneficial", "neutral")).issubset(reports), label
    counts = {name: len(reports[name].get("score_comparisons") or []) for name in reports}
    assert all(counts[name] > 0 for name in ("harmful", "beneficial", "neutral")), (label, counts)
    baseline_manifest[label] = {"path": rel, "bytes": path.stat().st_size, "score_comparison_counts": counts}
(OUT_DIR / "baseline_manifest.json").write_text(
    json.dumps(baseline_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)

source = SOURCE.read_text(encoding="utf-8")
old = 'out = ROOT / "doc/V0.1.6/evidence/m0_r02_gate_reports.json"'
new = 'out = ROOT / "doc/V0.1.6/evidence/audit_m0_r02/m0_r02_gate_reports.json"'
assert source.count(old) == 1
source = source.replace(old, new)
exec(compile(source, str(SOURCE), "exec"), {"__name__": "__main__", "__file__": str(SOURCE)})
