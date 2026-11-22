"""Run the exact M4 runtime script in a network-none production image."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["keyword", "vector"])
    parser.add_argument("--suffix", default="")
    args = parser.parse_args()
    evidence = Path(__file__).resolve().parent
    project = evidence.parents[2]
    assert all(c.isalnum() or c == "_" for c in args.suffix)
    prefix = evidence / ("m4_r01_docker_" + args.mode + args.suffix)
    assert not prefix.with_suffix(".json").exists(), "Refuse to overwrite accepted result"
    cmd = ["docker", "run", "--rm", "--init", "--network", "none", "-i"]
    if args.mode == "vector":
        model = project / "model_offline"
        assert model.is_dir()
        cmd += ["--mount", f"type=bind,source={model},target=/app/model_offline,readonly", "-e", "M4_EXPECT_VECTOR=1"]
    cmd += ["listtrans:v016-m4-r01-local", "python", "-u", "-"]
    start = time.monotonic()
    script = (evidence / "m4_r01_runtime_smoke.py").read_bytes()
    proc = subprocess.run(cmd, input=script, capture_output=True)
    prefix.with_suffix(".log").write_bytes(proc.stdout + b"\nSTDERR:\n" + proc.stderr)
    prefix.with_suffix(".exit.txt").write_text(str(proc.returncode), encoding="ascii")
    if proc.returncode:
        print(proc.stderr.decode("utf-8", "replace")[-6000:])
        return proc.returncode
    line = next(line for line in proc.stdout.decode("utf-8").splitlines() if line.startswith("M4_RUNTIME_SMOKE="))
    result = json.loads(line.split("=", 1)[1])
    result["runner"] = {"command": cmd, "script_sha256": hashlib.sha256(script).hexdigest(),
                        "exit_code": proc.returncode, "elapsed_sec": round(time.monotonic()-start, 2)}
    with prefix.with_suffix(".json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"mode": args.mode, "calls": len(result["calls"]), "lifo_409": result["lifo_409"],
                      "restored": result["restored_config_db_memory_fingerprint"], "xlsx_bytes": result["xlsx_bytes"],
                      "elapsed_sec": result["runner"]["elapsed_sec"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
