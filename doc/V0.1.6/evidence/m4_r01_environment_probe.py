"""Diagnose dotenv discovery for production-auth subprocesses without printing values."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    project = Path(__file__).resolve().parents[3]
    evidence = Path(__file__).resolve().parent
    root = Path(tempfile.mkdtemp(prefix="listtrans_m4_env_")).resolve()
    inside = project / ".local-archive" / ("m4_env_" + root.name)
    inside.mkdir()
    paths = {"MATERIALS_DB_PATH": root / "materials.db", "AUTH_DB_PATH": root / "auth.db", "CONFIG_DIR": root / "config",
             "VECTOR_CACHE_DIR": root / "cache", "UPLOAD_DIR": root / "uploads", "OUTPUT_DIR": root / "output", "LOG_DIR": root / "logs",
             "BINDING_FEEDBACK_FILE": root / "binding.json", "FEEDBACK_SAMPLES_FILE": root / "samples.json", "GOLDEN_DETAIL_FILE": root / "detail.json"}
    assert len(paths) == 10 and all(root in p.parents for p in paths.values())
    env = dict(os.environ, **{k: str(v) for k, v in paths.items()}, TESTING="1", LLM_ENABLED="false", PYTHONPATH=str(project), APP_ENV="production")
    for key in ("SECRET_KEY", "ADMIN_PASSWORD", "AUTH_DEV_MODE", "PYTHON_DOTENV_DISABLED"):
        env.pop(key, None)
    # Match the existing auth regression: the child deliberately probes TESTING=0.
    child = dict(env, TESTING="0")
    results = {}
    for label, cwd, disabled in (("inside_repo", inside, False), ("outside_repo", root, False), ("inside_repo_dotenv_disabled", inside, True)):
        run_env = dict(child)
        if disabled:
            run_env["PYTHON_DOTENV_DISABLED"] = "1"
        proc = subprocess.run([sys.executable, "-c", "import app.auth_manager"], cwd=cwd, env=run_env, capture_output=True, text=True)
        results[label] = {"returncode": proc.returncode, "requires_secret_in_error": "SECRET_KEY" in proc.stderr}
    assert results["inside_repo"]["returncode"] == 0
    assert results["outside_repo"]["returncode"] != 0 and results["inside_repo_dotenv_disabled"]["returncode"] != 0
    report = {"observation": "ENV-M4-01", "cause": "python -c dotenv discovery walks repository ancestors from an in-repository basetemp",
              "checks": results, "resolution": "Use native system-temp fixed roots and PYTHON_DOTENV_DISABLED=1; no code or test assertion changed"}
    with (evidence / "m4_r01_environment_probe.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
