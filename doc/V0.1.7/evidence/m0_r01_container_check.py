"""Check M4 container behavior with the M0 guard mounted read-only over the frozen image."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

from m0_r01_build_governance import ROOT, save_json, sha

BASE = "listtrans:v016-m4-r01-local"
IMAGE = "sha256:9517cc2a941cf9eebb662f2c833cdf88046709131a98380410b3b8b7a877e1b3"
SMOKE = ROOT / "doc/V0.1.6/evidence/m4_r01_runtime_smoke.py"
SOURCE = ROOT / "app/rule_governance.py"
CONTAINER_SOURCE = "/app/app/rule_governance.py"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("keyword", "vector"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    log = args.out.with_suffix(".log")
    if args.out.exists() or log.exists():
        parser.error("Choose a fresh output path")
    image = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", BASE],
                                    text=True, timeout=30).strip()
    if image != IMAGE:
        raise ValueError("Local M4 image digest differs from the frozen artifact")
    source_digest = sha(SOURCE)
    smoke = SMOKE.read_bytes()
    header = (f"import hashlib, pathlib\n"
              f"assert hashlib.sha256(pathlib.Path({CONTAINER_SOURCE!r}).read_bytes()).hexdigest() == {source_digest!r}\n")
    stdin = header.encode("utf-8") + smoke
    command = ["docker", "run", "--pull=never", "--rm", "--init", "--network", "none", "-i",
               "--mount", f"type=bind,source={SOURCE},target={CONTAINER_SOURCE},readonly"]
    if args.mode == "vector":
        command += ["--mount", f"type=bind,source={ROOT / 'model_offline'},target=/app/model_offline,readonly",
                    "-e", "M4_EXPECT_VECTOR=1"]
    command += [IMAGE, "python", "-u", "-"]
    start = time.monotonic()
    result = subprocess.run(command, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=600, check=False)
    with log.open("xb") as stream:
        stream.write(result.stdout)
    details = {"mode": args.mode, "base_image": image, "mounted_source_sha256": source_digest,
               "reuse_smoke_sha256": sha(SMOKE), "stdin_sha256": hashlib.sha256(stdin).hexdigest(),
               "command": command, "exit_code": result.returncode, "seconds": time.monotonic() - start}
    if result.returncode == 0:
        line = next(line for line in result.stdout.decode("utf-8").splitlines() if line.startswith("M4_RUNTIME_SMOKE="))
        details["smoke"] = json.loads(line.split("=", 1)[1])
    save_json(args.out, details)
    print(f"{args.mode} EXIT={result.returncode} calls={len(details.get('smoke', {}).get('calls', []))}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
