"""Compare experimental acceptance decisions with the frozen rule gate."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from m0_r01_candidate_gate import ROOT, run as run_rule_gate, sha
from m2_r01_http import PATHS
from app import acceptance_calibration


def run(output):
    if output.exists():
        raise FileExistsError(output)
    sources = {p: sha(ROOT / p) for p in (*PATHS, "app/sandbox_evaluator.py", "app/flywheel_sandbox.py")}
    os.environ["LISTTRANS_V019_M1_RERANK"] = "off"
    os.environ["LISTTRANS_V019_M2_CALIBRATION"] = "on"
    observed = []
    assess = acceptance_calibration.assess_match

    def check_unknown(row, query, match, policy, **kwargs):
        result = assess(row, query, match, policy, **kwargs)
        observed.append({k: result[k] for k in ("outcome", "missing_fields", "ranking_score")})
        return result

    with tempfile.TemporaryDirectory(prefix="v019_m2_gate_result_") as directory:
        raw = Path(directory) / "report.json"
        with patch.object(acceptance_calibration, "assess_match", check_unknown):
            code = run_rule_gate("legacy", raw)
        result = json.loads(raw.read_bytes())
    names = ("harmful", "beneficial", "beneficial_constrained", "neutral")
    gate_ok = result["rules"]["beneficial_constrained"]["binding_degraded_count"] == 0
    unknown_ok = len(observed) == 3 and all(
        row["outcome"] == "needs_parameters" and "防爆" in row["missing_fields"] for row in observed
    )
    if not gate_ok or not unknown_ok or any(sha(ROOT / p) != digest for p, digest in sources.items()):
        code = 1
    result.update(calibration_arm="on", calibration_source_sha256=sources, exit_code=code,
                  constrained_no_degradation=gate_ok,
                  protected_calibration_observations=observed,
                  binding_gate_checks={name: {key: result["rules"][name][key] for key in (
                      "binding_checks_count", "binding_degraded_count")} for name in names})
    with output.open("x", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
    return code


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    options = p.parse_args()
    sys.exit(run(options.out))
