"""Run isolated Windows M0 evidence commands with fresh logs and bounded runtimes."""
import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from m0_r01_build_governance import ROOT, save_json

EVIDENCE = Path(__file__).resolve().parent
PHASES = ("special", "vector", "keyword", "fixed", "standard", "gate", "host", "verify")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phases", nargs="+", choices=PHASES)
    parser.add_argument("--suffix", default="", help="Short suffix for fresh replay evidence")
    args = parser.parse_args()
    if args.suffix and not re.fullmatch(r"[a-z0-9_]+", args.suffix):
        parser.error("Use lowercase letters, digits or underscores for the evidence suffix")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHON_DOTENV_DISABLED="1",
               PYTHONDONTWRITEBYTECODE="1", LLM_ENABLED="false", TESTING="1",
               AUTH_DEV_MODE="false", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               DASHSCOPE_API_KEY="", DEEPSEEK_API_KEY="")
    for key in ("LISTTRANS_EVAL_KEYWORD_ONLY", "LISTTRANS_EVAL_REQUIRE_VECTOR", "LISTTRANS_TEST_ROOT"):
        env.pop(key, None)
    for phase in args.phases:
        root = Path(tempfile.mkdtemp(prefix=f"lt017_{phase}_")).resolve()
        if ROOT in root.parents:
            raise ValueError("Use a Windows native temporary directory outside the project")
        path = EVIDENCE / f"m0_r01_validation_{phase}{args.suffix}"
        out = path.with_suffix(".json")
        log = path.with_suffix(".log")
        metadata = path.with_suffix(".run.json")
        if any(p.exists() for p in (log, metadata, out)):
            raise FileExistsError(f"Existing evidence for {phase}. Use a fresh --suffix for a replay")
        command = [sys.executable, "-B", "-X", "utf8", "-u"]
        run_env = dict(env)
        if phase in ("special", "fixed", "standard"):
            paths = (
                ["tests/test_v017_m0_export.py", "tests/test_v017_m0_governance.py",
                 "tests/test_v016_m4_lifecycle.py", "tests/test_v016_m4_diagnostics.py"]
                if phase == "special" else ["tests"]
            )
            if phase != "standard":
                run_env["LISTTRANS_TEST_ROOT"] = str(root / "session")
            command += ["-m", "pytest", *paths, "-q", "-ra", "-p", "no:cacheprovider",
                        "--basetemp", str(root / "pytest")]
        elif phase in ("vector", "keyword"):
            command += [str(EVIDENCE / "m0_r01_benchmark.py"), "--mode", phase,
                        "--root", str(root / "benchmark"), "--out", str(out)]
        elif phase == "gate":
            command += [str(ROOT / "doc/V0.1.6/evidence/m4_r01_3rule_gate.py"), "--out", str(out)]
        elif phase == "host":
            command += [str(ROOT / "doc/V0.1.6/evidence/m4_r01_runtime_host.py")]
        else:
            command += ["tests/verify_isolation.py", "--out", str(out)]
        print(f"Running {phase}", flush=True)
        start = time.monotonic()
        started_at = datetime.now(timezone(timedelta(hours=8))).isoformat()
        with log.open("xb") as stream:
            try:
                exit_code = subprocess.run(command, cwd=ROOT, env=run_env, stdout=stream,
                                           stderr=subprocess.STDOUT, timeout=35 * 60).returncode
            except subprocess.TimeoutExpired:
                exit_code = 124
        elapsed = round(time.monotonic() - start, 3)
        save_json(metadata, {"command": command, "exit_code": exit_code, "seconds": elapsed,
                            "started_at": started_at, "native_root": str(root), "cwd": str(ROOT),
                            "explicit_test_root": run_env.get("LISTTRANS_TEST_ROOT"), "llm": False,
                            "dotenv_disabled": True})
        print(f"{phase} EXIT={exit_code} ({elapsed:.1f}s)", flush=True)
        if exit_code:
            return exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
