"""Coordinator negative probes for M0 guardrails; never import business modules.

Run from repository root:
  python doc/V0.1.5/evidence/m0_r02_coordinator_probe.py
Controlled module doubles expose invalid paths only. These probes do not replace
runtime acceptance with real dependencies, services, or models.
"""
import hashlib
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).with_suffix(".json")


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def check_delivery():
    manifest = ROOT / "doc/V0.1.5/evidence/m0_r02_delivery_hashes.md"
    rows = re.findall(r"^\| ([0-9a-f]{64}) \| ([^|]+) \|", manifest.read_text(encoding="utf-8"), re.M)
    result = []
    for expected, cell in rows:
        relative = cell.strip().split("（", 1)[0]
        actual = file_hash(ROOT / relative)
        result.append({"file": relative, "expected": expected, "actual": actual, "matches": actual == expected})
    return result


def fake_modules(materials_path, layout):
    return {
        "app.db_manager": SimpleNamespace(DB_PATH=str(materials_path)),
        "app.auth_manager": SimpleNamespace(AUTH_DB_PATH=str(layout["auth_db"])),
        "app.vector_index": SimpleNamespace(CACHE_DIR=str(layout["vector_cache_dir"])),
        "app.llm_rule_engine": SimpleNamespace(DB_PATH=str(layout["materials_db"])),
        "app.llm_client": SimpleNamespace(DB_PATH=str(layout["materials_db"])),
        "app.feedback_train": SimpleNamespace(FEEDBACK_FILE=str(layout["feedback_samples_file"])),
    }


def fixture_probe(cf, materials_path):
    modules = fake_modules(materials_path, cf.LAYOUT)
    fixture = cf._isolation_guardrails.__wrapped__()
    next(fixture)
    raised = None
    with patch.object(cf.importlib, "import_module", side_effect=lambda name: modules[name]):
        try:
            next(fixture)
        except StopIteration:
            pass
        except Exception as exc:
            raised = f"{type(exc).__name__}: {exc}"
    return {
        "injected_materials_path": str(materials_path),
        "exception_escaped_fixture": raised,
        "recorded_guardrail_checks": os.environ.get("LISTTRANS_GUARDRAIL_CHECKED"),
    }


def main():
    verify = load_source("m0_verify_coordinator", ROOT / "tests/verify_isolation.py")
    before = verify.collect_snapshot()
    cf = load_source("m0_conftest_coordinator", ROOT / "tests/conftest.py")
    production = fixture_probe(cf, cf.PROD_MATERIALS_DB)
    external = fixture_probe(cf, cf.LAYOUT["root"].parent / "m0_probe_never_opened.db")

    # Run the real verifier main with controlled imported path values. Only its
    # normal temporary SQLite fixtures are opened; injected paths are never opened.
    def invalid_import_result(layout):
        modules = fake_modules(cf.PROD_MATERIALS_DB, layout)
        with patch.object(verify.importlib, "import_module", side_effect=lambda name: modules[name]):
            return original_check(layout)

    original_check = verify.check_imported_paths
    with tempfile.TemporaryDirectory(prefix="m0_coordinator_result_") as result_dir:
        injected_out = Path(result_dir) / "injected_verify.json"
        with patch.object(verify, "check_imported_paths", side_effect=invalid_import_result), patch.object(sys, "argv", ["verify_isolation.py", "--out", str(injected_out)]):
            exit_code = verify.main()
        injected_report = json.loads(injected_out.read_text(encoding="utf-8"))

    after = verify.collect_snapshot()
    changes = verify.diff_snapshots(before, after)
    rows = check_delivery()
    audit_path = ROOT / "doc/V0.1.5/rounds/V015-M0_R02_审核小结.md"
    report = {
        "task": "V015-M0",
        "role": "coordinator",
        "created_at": datetime.now(timezone(timedelta(hours=8), "Asia/Shanghai")).isoformat(),
        "command": "python doc/V0.1.5/evidence/m0_r02_coordinator_probe.py",
        "python": platform.python_version(),
        "baseline_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "scope": "negative guardrail probes with controlled module doubles; no app modules imported; not full runtime acceptance",
        "delivery_hashes": rows,
        "delivery_hashes_matched": sum(row["matches"] for row in rows),
        "delivery_hashes_total": len(rows),
        "audit_summary_sha256": file_hash(audit_path),
        "fixture_production_path": production,
        "fixture_external_path": external,
        "verify_injected_escape": {
            "exit_code": exit_code,
            "verdict": injected_report["verdict"],
            "module_path_checks": injected_report["dependency_probe"]["module_path_checks"],
            "errors": injected_report["dependency_probe"]["errors"],
        },
        "business_snapshot_matches_r02_audit": before == json.loads((ROOT / "doc/V0.1.5/evidence/m0_r02_audit_verify.json").read_text(encoding="utf-8"))["business_snapshot_after"],
        "business_hash_unchanged": not changes,
        "business_changes": changes,
    }
    report["negative_probes_reproduced"] = (
        production["exception_escaped_fixture"] is None
        and "RuntimeError" in production["recorded_guardrail_checks"]
        and external["exception_escaped_fixture"] is None
        and "m0_probe_never_opened.db" in external["recorded_guardrail_checks"]
        and exit_code == 0 and injected_report["verdict"] == "PASS"
        and any(not row["inside_isolation"] for row in injected_report["dependency_probe"]["module_path_checks"])
    )
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "negative_probes_reproduced": report["negative_probes_reproduced"],
        "business_hash_unchanged": report["business_hash_unchanged"],
        "business_snapshot_matches_r02_audit": report["business_snapshot_matches_r02_audit"],
        "delivery_hashes_matched": report["delivery_hashes_matched"],
        "delivery_hashes_total": len(rows),
        "out": str(OUT),
    }, ensure_ascii=False))
    return 0 if report["negative_probes_reproduced"] and not changes else 1


if __name__ == "__main__":
    raise SystemExit(main())
