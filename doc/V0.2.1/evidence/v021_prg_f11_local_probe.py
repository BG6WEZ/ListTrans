"""Verify signing-key rotation with generated values and a disposable RC account DB."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[3]


def worker():
    sys.path[:0] = [str(ROOT), str(ROOT / "doc/V0.1.7/evidence")]
    from m0_r01_benchmark import block_external_network

    sys.addaudithook(block_external_network)
    from fastapi.testclient import TestClient
    from app.server import app

    old_token = sys.stdin.read()
    with TestClient(app) as client:
        unauthorized = client.get("/api/auth/me").status_code
        previous = client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code if old_token else None
        login = client.post("/api/auth/login", json={
            "username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"],
        })
        if login.status_code != 200:
            raise RuntimeError(f"Synthetic login returned {login.status_code}")
        token = login.json()["access_token"]
        current = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
    print("RESULT " + json.dumps({
        "unauthenticated": unauthorized, "previous_token": previous, "current_login": login.status_code,
        "current_token": current, "bootstrap_token": token if not old_token else None,
    }))


def main(output):
    with TemporaryDirectory(prefix="v021-f11-") as directory:
        root = Path(directory)
        env = os.environ.copy()
        for name, relative in {
            "AUTH_DB_PATH": "db/auth.db", "MATERIALS_DB_PATH": "db/materials.db",
            "CONFIG_DIR": "config", "VECTOR_CACHE_DIR": "vectors", "LOG_DIR": "logs",
            "UPLOAD_DIR": "uploads", "OUTPUT_DIR": "output", "BINDING_FEEDBACK_FILE": "state/feedback.json",
            "FEEDBACK_SAMPLES_FILE": "state/samples.json", "GOLDEN_DETAIL_FILE": "state/eval.json",
            "HF_HOME": "hf", "TORCH_HOME": "torch",
        }.items():
            target = root / relative
            (target.parent if target.suffix else target).mkdir(parents=True, exist_ok=True)
            env[name] = str(target)
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        for name in re.findall(r"^!config/([^/*?\r\n]+\.json)$", ignore, re.M):
            shutil.copyfile(ROOT / "config" / name, root / "config" / name)
        freeze = json.loads((ROOT / "doc/V0.2.1/evidence/v021_final_coord_verify.json").read_bytes())
        env.update(freeze["default_switches_frozen_off"])
        env.update(APP_ENV="production", TESTING="0", PYTHON_DOTENV_DISABLED="1", AUTH_DEV_MODE="false",
                   HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", LISTTRANS_EVAL_KEYWORD_ONLY="1",
                   LISTTRANS_EVAL_REQUIRE_VECTOR="0", ADMIN_USERNAME="gate-probe",
                   ADMIN_PASSWORD=secrets.token_urlsafe(32) + "!R7", PYTHONUTF8="1")

        def in_process(secret, previous=""):
            result = subprocess.run([sys.executable, __file__, "--worker"], cwd=root,
                                    env={**env, "SECRET_KEY": secret}, input=previous, text=True,
                                    capture_output=True, timeout=60)
            records = [line[7:] for line in result.stdout.splitlines() if line.startswith("RESULT ")]
            if result.returncode != 0 or len(records) != 1:
                raise RuntimeError(f"Synthetic RC process failed, exit code {result.returncode}")
            return json.loads(records[0])

        before = in_process(secrets.token_urlsafe(48))
        after = in_process(secrets.token_urlsafe(48), before.pop("bootstrap_token"))
        after.pop("bootstrap_token")
        assert before == {"unauthenticated": 401, "previous_token": None, "current_login": 200, "current_token": 200}
        assert after == {"unauthenticated": 401, "previous_token": 401, "current_login": 200, "current_token": 200}
        report = {"exit_code": 0, "scope": "synthetic production-mode runtime only",
                  "before_rotation": before, "after_rotation": after, "real_rotation_verified": False}
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker()
    elif args.out:
        main(args.out)
    else:
        parser.error("--out is required")
