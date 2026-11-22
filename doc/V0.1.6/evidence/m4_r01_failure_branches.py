"""Persist controlled M4 fault results and the corresponding real SQLite records."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    evidence = Path(__file__).resolve().parent
    root = Path(tempfile.mkdtemp(prefix="listtrans_m4_faults_")).resolve()
    spec = importlib.util.spec_from_file_location("m4_fault_environment", evidence / "m4_r01_3rule_gate.py")
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    helper._setup_env(root)
    os.environ["LISTTRANS_EVAL_KEYWORD_ONLY"] = "1"
    sys.path.insert(0, str(evidence.parents[2]))
    from tests.test_v016_m4_lifecycle import (m2_env, test_process_faults_persist_fail_closed,
                                             test_real_timeout_kills_and_reaps_child, test_guard_errors_and_skips_reach_database)
    from app import flywheel_sandbox
    from app.rule_governance import get_eval_run
    fixture = m2_env.__wrapped__()
    environment = next(fixture)
    results = {}
    cases = [(fault, lambda f=fault, c=code: test_process_faults_persist_fail_closed(environment, f, c))
             for fault, code in (("nonzero", "eval_baseline_nonzero"), ("missing", "eval_output_missing"), ("corrupt", "eval_corrupt_json"))]
    cases += [("timeout_real_child", lambda: test_real_timeout_kills_and_reaps_child(environment)),
              ("guard_sample_exception", lambda: test_guard_errors_and_skips_reach_database(environment, "guard")),
              ("binding_sample_exception", lambda: test_guard_errors_and_skips_reach_database(environment, "binding"))]
    try:
        for label, execute in cases:
            before = set(flywheel_sandbox._eval_tasks)
            execute()
            created = set(flywheel_sandbox._eval_tasks) - before
            assert len(created) == 1
            task_id = created.pop()
            row = get_eval_run(task_id)
            results[label] = {"regression_assertions_passed": True, "state": flywheel_sandbox.get_eval_task_status(task_id),
                              "database": {key: row[key] for key in ("eval_id", "status", "safety_passed", "benefit_class",
                                  "failed_samples_count", "skipped_samples_count", "baseline_process_exit", "candidate_process_exit", "result_summary_json", "error_message")}}
        with (evidence / "m4_r01_failure_branches.json").open("x", encoding="utf-8") as stream:
            json.dump(results, stream, ensure_ascii=False, indent=2)
        print(json.dumps({label: {"status": value["database"]["status"], "safety_passed": value["database"]["safety_passed"],
                                 "failed": value["database"]["failed_samples_count"], "skipped": value["database"]["skipped_samples_count"]}
                          for label, value in results.items()}))
    finally:
        fixture.close()


if __name__ == "__main__":
    main()
