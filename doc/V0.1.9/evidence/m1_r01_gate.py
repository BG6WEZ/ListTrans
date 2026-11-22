"""Exercise an enabled profile against every frozen rule and protected probe."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from m0_r01_candidate_gate import ROOT, run as run_rule_gate, sha
from app import candidate_reranker


def run(profile, output):
    if output.exists():
        raise FileExistsError(output)
    source = {p: sha(ROOT / p) for p in (
        "app/matcher.py", "app/server.py", "app/candidate_reranker.py",
        candidate_reranker.MODEL_PATH.relative_to(ROOT).as_posix(),
    )}
    os.environ["LISTTRANS_V019_M1_RERANK"] = profile
    observed = {"calls": 0, "rows_with_eligible_slots": 0, "slots_moved": 0}
    actual = candidate_reranker.rerank

    def observe(*args, **kwargs):
        result = actual(*args, **kwargs)
        observed["calls"] += 1
        if result:
            observed["rows_with_eligible_slots"] += 1
            observed["slots_moved"] += result["moved_count"]
        return result

    # The shared runner fixes every other switch and uses the same Train/Dev
    # subset, synthetic catalog, actual child evaluation and export probes.
    with tempfile.TemporaryDirectory(prefix="v019_m1_gate_result_") as directory:
        raw = Path(directory) / "report.json"
        with patch.object(candidate_reranker, "rerank", observe):
            exit_code = run_rule_gate("legacy", raw)
        result = json.loads(raw.read_text(encoding="utf-8"))
    matches = sum(result["rule_safety_unchanged"].values())
    if matches != 5 or not result["probes_ok"] or not observed["calls"]:
        exit_code = 1
    if any(sha(ROOT / p) != digest for p, digest in source.items()):
        raise RuntimeError("Scoring source changed during gate run")
    result.update({"rerank_profile": profile, "source_sha256": source, "exit_code": exit_code,
                   "protected_probe_rerank": observed})
    with output.open("x", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
    return exit_code


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", choices=tuple(candidate_reranker.OPTIONS), required=True)
    p.add_argument("--out", type=Path, required=True)
    options = p.parse_args()
    sys.exit(run(options.profile, options.out))
