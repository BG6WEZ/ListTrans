"""Run Coordinator M4 R01 / V0.1.6 Final Audit cross-package 78-item targeted pytest suite."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent

TESTS = [
    "tests/test_v016_m4_diagnostics.py",
    "tests/test_v016_m4_lifecycle.py",
    "tests/test_v0_0_8_llm_rules.py",
    "tests/test_v016_m3_runtime_assets.py",
    "tests/test_v016_m2_publish_gate.py",
    "tests/test_v016_m1_mining_and_rule_context.py",
    "tests/test_feedback_miner.py",
    "tests/test_v016_m0_feedback_contract.py",
    "tests/test_llm_integration.py",
    "tests/test_m0_isolation.py",
    "tests/test_m0_guardrail_regression.py",
]


def main() -> int:
    iso_root = Path(tempfile.mkdtemp(prefix="coord_m4_r01_")).resolve()
    assert iso_root.is_absolute() and ROOT not in iso_root.parents
    env = dict(
        os.environ,
        PYTHONIOENCODING="utf-8",
        PYTHON_DOTENV_DISABLED="1",
        LLM_ENABLED="false",
        TESTING="1",
        AUTH_DEV_MODE="false",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        DASHSCOPE_API_KEY="",
        DEEPSEEK_API_KEY="",
        LISTTRANS_TEST_ROOT=str(iso_root),
    )
    basetemp = iso_root / "pytest_base"
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "pytest",
        *TESTS,
        "-v",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(basetemp),
    ]
    log_path = OUT / "targeted_pytest_local.log"
    exit_path = OUT / "targeted_pytest_local.exit.txt"
    run_path = OUT / "targeted_pytest_local.run.json"

    start = time.monotonic()
    meta = {
        "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "command": cmd,
        "cwd": str(ROOT),
        "native_temp_root": str(iso_root),
        "explicit_test_root": str(iso_root),
        "basetemp": str(basetemp),
        "dotenv_disabled": True,
        "llm_enabled": False,
        "test_files": TESTS,
    }
    with log_path.open("wb") as stream:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    elapsed = round(time.monotonic() - start, 2)
    exit_path.write_text(str(proc.returncode), encoding="ascii")
    meta.update(exit_code=proc.returncode, elapsed_sec=elapsed)
    run_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"exit_code": proc.returncode, "elapsed_sec": elapsed, "root": str(iso_root)}))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
