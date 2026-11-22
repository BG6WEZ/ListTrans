"""Serial reviewer probes wrapped by read-only business snapshots."""
import json
from pathlib import Path
import runpy

EV=Path(__file__).resolve().parent
h=runpy.run_path(str(EV/"m3_r03_audit_runner.py"))
summary=dict(snapshot_before=h["snapshot"]("before_probes"),steps=[])
try:
    summary["steps"].append(h["run"]("probes",["-u",str(EV/"m3_r03_audit_probes.py")]))
    old=json.loads((EV/"m3_r02_audit_gate_reports.json").read_text(encoding="utf-8"))
    new=json.loads((EV/"m3_r03_audit_gate_reports.json").read_text(encoding="utf-8"))
    compare={}
    for name in ("harmful","beneficial","neutral"):
        a,b=old["reports"][name],new["reports"][name]
        x,y={r["id"]:r for r in a["score_comparisons"]},{r["id"]:r for r in b["score_comparisons"]}
        compare[name]=dict(passed=[a["passed"],b["passed"]],targeted=[a["targeted_count"],b["targeted_count"]],
                           score_comparison_diffs=[i for i in sorted(x.keys()|y.keys()) if x.get(i)!=y.get(i)],
                           snapshot_errors=b["snapshot"]["errors"])
    h["write"]("r02_compare",compare)
finally:
    summary.update(snapshot_after=h["snapshot"]("after_probes"),hashes_after=h["hashes"]())
    h["write"]("extra_summary",summary)
