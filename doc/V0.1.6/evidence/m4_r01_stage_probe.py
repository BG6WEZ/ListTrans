"""Persist the controlled five-stage regression observations as reviewable JSON."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    evidence = Path(__file__).resolve().parent
    project = evidence.parents[2]
    root = Path(tempfile.mkdtemp(prefix="listtrans_m4_stage_")).resolve()
    spec = importlib.util.spec_from_file_location("m4_stage_environment", evidence / "m4_r01_3rule_gate.py")
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    helper._setup_env(root)
    os.environ["LISTTRANS_EVAL_KEYWORD_ONLY"] = "1"
    sys.path.insert(0, str(project))
    from pytest import MonkeyPatch
    from tests.test_v016_m4_diagnostics import diagnostic_catalog, item
    from app import matcher, sandbox_evaluator

    with MonkeyPatch.context() as monkeypatch:
        fixture = diagnostic_catalog.__wrapped__(monkeypatch)
        next(fixture)
        try:
            rows, _ = sandbox_evaluator.evaluate([item(c) for c in ("MISSING", "NOTREC", "BLOCK", "LOW", "HIT")])
            actual_match = matcher.match_item
            def failing(query, *args, **kwargs):
                if query["name"] == "FAIL":
                    raise RuntimeError("controlled_sample_exception")
                return actual_match(query, *args, **kwargs)
            monkeypatch.setattr(matcher, "match_item", failing)
            failures, _ = sandbox_evaluator.evaluate([item("HIT"), item("FAIL"), item("EMPTY") | {"target_codes": []}, {"id": "INVALID"}])
            report = {"method": "controlled_recall_and_scores_using_M4_regression_fixture",
                      "stage_metrics": sandbox_evaluator.metrics(rows), "rows": rows,
                      "failure_skip_metrics": sandbox_evaluator.metrics(failures), "failure_skip_rows": failures}
            assert report["stage_metrics"]["candidate_recall"] == 60 and report["stage_metrics"]["miss@10"] == 80
            assert report["failure_skip_metrics"]["failed_samples_count"] == 1 and report["failure_skip_metrics"]["skipped_samples_count"] == 2
            with (evidence / "m4_r01_stage_matrix.json").open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
            print(json.dumps({"stages": [r["diagnostics"]["targets"][0]["state"] for r in rows],
                              "candidate_recall": report["stage_metrics"]["candidate_recall"], "miss@10": report["stage_metrics"]["miss@10"],
                              "failures": report["failure_skip_metrics"]["failed_samples_count"], "skips": report["failure_skip_metrics"]["skipped_samples_count"]}))
        finally:
            fixture.close()


if __name__ == "__main__":
    main()
