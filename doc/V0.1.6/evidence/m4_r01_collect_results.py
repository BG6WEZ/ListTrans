"""Bind accepted M4 evidence and reject incomplete or mismatched final checks."""
import hashlib
import json
from pathlib import Path
import re

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[2]


def read(name):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pytest_result(prefix, expected):
    run = read(prefix + ".run.json")
    assert run["exit_code"] == 0
    text = (EVIDENCE / (prefix + ".log")).read_text(encoding="utf-8-sig")
    matches = re.findall(r"(\d+) passed in ([0-9.]+)s", text)
    count, seconds = matches[-1]
    assert int(count) == expected
    actual_root = re.search(r"LISTTRANS 测试隔离根目录: (.+)", text).group(1).strip()
    assert Path(actual_root).is_absolute() and ROOT not in Path(actual_root).parents
    return {"passed": int(count), "pytest_elapsed_sec": float(seconds), "runner_elapsed_sec": run["elapsed_sec"],
            "actual_isolation_root": actual_root,
            "run_metadata": prefix + ".run.json", "log_sha256": sha(EVIDENCE / (prefix + ".log"))}


def main():
    results = {"baseline_head": read("m4_r01_baseline.json")["head"],
               "fixed": pytest_result("m4_r01_full_fixed_native", 373),
               "standard": pytest_result("m4_r01_full_standard", 373),
               "cold": pytest_result("m4_r01_cold_llm_rules", 4)}
    assert "12 passed" in (EVIDENCE / "m4_r01_special_final.log").read_text(encoding="utf-8-sig")
    gate = read("m4_r01_gate_reports.json")
    assert read("m4_r01_3rule_gate.run.json")["exit_code"] == 0
    assert len(gate["comparisons"]) == 6 and all(row["score_diff_count"] == 0 for group in gate["comparisons"].values() for row in group.values())
    expected = {"empty": ("invalid", False), "harmful": ("harmful", False), "beneficial": ("harmful", False),
                "beneficial_constrained": ("improved", True), "neutral": ("neutral", True)}
    assert {key: (value["benefit"], value["safety_passed"]) for key, value in gate["rules"].items()} == expected
    for key, (benefit, safe) in expected.items():
        db = gate["persisted_runs"][key]
        assert db["status"] == "completed" and db["benefit_class"] == benefit and bool(db["safety_passed"]) == safe
    r_probe = read("m4_r01_r_m3_01_diagnostics.json")
    for name in ("GT-0674", "GT-0677", "GT-0678"):
        for trace in (r_probe[name], gate["r_m3_01_diagnostics"][name]):
            assert not trace["targets"][0]["hard_blocked"] and abs(trace["targets"][0]["margin"] - .1) < 1e-6
            assert trace["decision"]["decision_status"] == "recommended" and trace["export_gate"]["http_status"] == 422
            assert trace["query_explosion_state"] is None
    layer = read("m4_r01_image_layers_final.json")
    assert layer["source_hashes_match"] and not layer["violations"] and len(layer["layers"]) == 12 and layer["application_files"] == 57
    for info in layer["layers"].values():
        assert all(sha(ROOT / name) == digest for name, digest in info["app_files"].items())
    script_sha = sha(EVIDENCE / "m4_r01_runtime_smoke.py")
    containers = {}
    for mode in ("keyword", "vector"):
        data = read(f"m4_r01_docker_{mode}_verified.json")
        assert data["runner"]["exit_code"] == 0 and data["runner"]["script_sha256"] == script_sha
        assert data["no_tests_doc"] and data["lifo_409"] and data["restored_config_db_memory_fingerprint"]
        assert data["database_states"]["before_rejected_rollback"] == data["database_states"]["after_rejected_rollback"]
        assert data["fingerprints"]["before"] == data["fingerprints"]["rollback_alias"]
        assert data["fingerprints"]["alias"] == data["fingerprints"]["rollback_binding"]
        vectors = [v["report"]["snapshot"]["candidate"]["vector_channel"] for v in data["evaluations"].values()]
        assert all(v["enabled"] == (mode == "vector") for v in vectors)
        if mode == "vector":
            assert all(v["dim"] == 512 for v in vectors)
        assert len(data["calls"]) == 51 and data["exports"] == {"benchmark_count": 5, "triplet_count": 1}
        containers[mode] = {"calls": len(data["calls"]), "elapsed_sec": data["runner"]["elapsed_sec"], "xlsx_bytes": data["xlsx_bytes"], "vector_enabled": mode == "vector"}
    for when in ("before", "after"):
        isolation = read(f"m4_r01_isolation_{when}.json")
        assert isolation["verdict"] == "PASS" and isolation["business_hash_unchanged"]
    faults = read("m4_r01_failure_branches.json")
    assert all(not value["database"]["safety_passed"] for value in faults.values())
    assert all(faults[key]["database"]["failed_samples_count"] == 1 and faults[key]["database"]["skipped_samples_count"] == 7
               for key in ("guard_sample_exception", "binding_sample_exception"))
    results.update(special_passed=12, baseline_groups_zero_diff=len(gate["comparisons"]),
                   benefits={k: v[0] for k, v in expected.items()}, image_id=layer["image_id"], layers=12, application_files=57,
                   containers=containers, r_m3_01={name: {"T": value["targets"][0]["T"], "W": value["targets"][0]["W"],
                                                        "margin": value["targets"][0]["margin"], "export_status": 422} for name, value in r_probe.items()},
                   isolation_before_after="PASS", actual_paid_llm_calls=0, production_release_authorized=False,
                   code_hashes={name: sha(ROOT / name) for name in ("app/matcher.py", "app/sandbox_evaluator.py", "app/flywheel_sandbox.py", "app/server.py", "tests/test_v0_0_8_llm_rules.py")})
    with (EVIDENCE / "m4_r01_results.json").open("x", encoding="utf-8") as stream:
        json.dump(results, stream, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
