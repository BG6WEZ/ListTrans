"""Run final M4 checks serially with native temp roots and exact run metadata."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phases", nargs="+", choices=["fixed", "standard", "cold", "gate", "verify"])
    args = parser.parse_args()
    evidence = Path(__file__).resolve().parent
    project = evidence.parents[2]
    for phase in args.phases:
        root = Path(tempfile.mkdtemp(prefix="lt_m4_" + phase + "_")).resolve()
        assert root.is_absolute() and project not in root.parents
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHON_DOTENV_DISABLED="1", LLM_ENABLED="false", TESTING="1",
                   AUTH_DEV_MODE="false", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", DASHSCOPE_API_KEY="", DEEPSEEK_API_KEY="")
        env.pop("LISTTRANS_TEST_ROOT", None)
        prefix = "m4_r01_" + {"fixed": "full_fixed_native", "standard": "full_standard", "cold": "cold_llm_rules", "gate": "3rule_gate", "verify": "isolation_after"}[phase]
        command = [sys.executable, "-u"]
        if phase in ("fixed", "standard", "cold"):
            if phase != "standard":
                env["LISTTRANS_TEST_ROOT"] = str(root)
            command += ["-m", "pytest", "tests/test_v0_0_8_llm_rules.py" if phase == "cold" else "tests", "-v", "-p", "no:cacheprovider"]
            if phase != "standard":
                command += ["--basetemp", str(root / "pytest")]
        elif phase == "gate":
            command += [str(evidence / "m4_r01_3rule_gate.py"), "--out", str(evidence / "m4_r01_gate_reports.json")]
        else:
            command += [str(project / "tests/verify_isolation.py"), "--out", str(evidence / "m4_r01_isolation_after.json")]
        log = evidence / (prefix + ".log")
        assert not log.exists(), f"Refuse overwrite: {log}"
        start = time.monotonic()
        metadata = {"phase": phase, "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "command": command, "cwd": str(project), "native_temp_root": str(root),
                    "explicit_test_root": env.get("LISTTRANS_TEST_ROOT"), "dotenv_disabled": True, "llm_enabled": False,
                    "cold_parent_cache_initial_files": len(list((root / "vector_cache").glob("*")))}
        with log.open("xb") as stream:
            result = subprocess.run(command, cwd=project, env=env, stdout=stream, stderr=subprocess.STDOUT)
        metadata.update(exit_code=result.returncode, elapsed_sec=round(time.monotonic() - start, 2))
        (evidence / (prefix + ".exit.txt")).write_text(str(result.returncode), encoding="ascii")
        (evidence / (prefix + ".run.json")).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"phase": phase, "exit_code": result.returncode, "elapsed_sec": metadata["elapsed_sec"]}), flush=True)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
