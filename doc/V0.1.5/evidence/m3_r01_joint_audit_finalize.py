"""Additional reviewer counterexamples and unknown-margin diagnosis after serial rerun."""
import json
from pathlib import Path
import runpy

EV = Path(__file__).resolve().parent
helpers = runpy.run_path(str(EV / "m3_r01_joint_audit_runner.py"))
summary = {"snapshot_before_probes": helpers["snapshot"]("before_probes"), "steps": []}
try:
    summary["steps"].append(helpers["run"]("counterexamples", ["-u", str(EV / "m3_r01_joint_audit_probes.py")]))
    summary["steps"].append(helpers["run"]("rm3_diag", ["-u", str(EV / "m3_r01_score_shift_diag.py"),
                                                              "GT-0674", "GT-0677", "GT-0678"]))
    compare = (EV / "m3_r01_pre_post_compare.py").read_text(encoding="utf-8")
    compare_path = EV / "m3_r01_joint_audit2_compare.py"
    compare_path.write_text(compare.replace("m3_r01_gate_reports.json", "m3_r01_joint_audit2_gate_reports.json")
                           .replace("m3_r01_pre_post_compare.json", "m3_r01_joint_audit2_compare.json"), encoding="utf-8")
    summary["steps"].append(helpers["run"]("compare", [str(compare_path)]))
finally:
    summary["snapshot_post_probes"] = helpers["snapshot"]("post_probes")
    summary["hashes_post_probes"] = helpers["hashes"]()
    summary["interrupted_attempts"] = [
        {"prefix": "m3_r01_joint_audit", "result": "interrupted; verify PASS only, pytest did not complete"},
        {"prefix": "m3_r01_joint_audit2_probes.log", "result": "interrupted sandbox attempt; faulthandler showed asyncio socketpair accept blocked"},
    ]
    helpers["write"]("extra_summary", summary)
