"""Verify historical rule parity on Train/Dev before the one-time holdout starts."""
import argparse
import json
import os
from pathlib import Path
import sys

from m0_r01_candidate_gate import run as run_legacy
from m2_r01_gate import run as run_calibrated
from m3_r01_quality import ROOT, SWITCHES, sha


def run(mode, output):
    os.environ.update(SWITCHES)
    if mode == "on":
        return run_calibrated(output)
    exit_code = run_legacy("legacy", output)
    report = json.loads(output.read_bytes())
    differences = [len(rows) for by_rule in report["comparisons"].values() for rows in by_rule.values()]
    if len(differences) != 18 or any(differences):
        exit_code = 1
    report.update(calibration_arm="off", historical_score_differences=differences, exit_code=exit_code,
                  review_source_sha256={name: sha(ROOT / name) for name in (
                      "app/server.py", "app/binding_decisions.py", "app/report_generator.py",
                  )})
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("off", "on"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.exit(run(args.mode, args.out))
