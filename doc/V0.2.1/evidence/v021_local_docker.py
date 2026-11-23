"""Rotate real bootstrap credentials for an isolated local Docker instance and publish it locally."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from v021_preproduction_context import ROOT, sha


DOCKER = ["docker", "--context", "desktop-linux"]
EVIDENCE = Path(__file__).resolve().parent
FINAL_NAME = "listtrans-v021-local"
USER = "admin"
BOOTSTRAP = """
import os
from pathlib import Path
private = Path('/run/listtrans-private')
os.environ['SECRET_KEY'] = (private / 'jwt.key').read_text().strip()
os.environ['ADMIN_PASSWORD'] = (private / 'admin-password.txt').read_text().strip()
os.execvp('uvicorn', ['uvicorn', 'app.server:app', '--host', '0.0.0.0', '--port', '18080', '--workers', '1'])
"""
HTTP_PROBE = """
import json, sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

def call(path, method='GET', body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request('http://127.0.0.1:18080' + path, method=method, headers=headers,
                      data=None if body is None else json.dumps(body).encode())
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except HTTPError as failure:
        return failure.code, {}

data = json.load(sys.stdin)
def login(password):
    return call('/api/auth/login', 'POST', {'username': data['username'], 'password': password})

if data['step'] == 'password':
    old_login, old = login(data['old'])
    old_status, _ = call('/api/auth/me', token=old.get('access_token'))
    changed, _ = call('/api/auth/profile', 'PUT', {'old_password': data['old'], 'new_password': data['new']},
                      old.get('access_token'))
    revoked, _ = call('/api/auth/me', token=old.get('access_token'))
    denied, _ = login(data['old'])
    new_login, new = login(data['new'])
    current, state = call('/api/auth/me', token=new.get('access_token'))
    print(json.dumps({
        'before': [old_login, old_status], 'changed': changed, 'old_token': revoked,
        'old_password': denied, 'new_login': new_login, 'new_token': current,
        'must_change_password': state.get('must_change_password'), 'token': new.get('access_token'),
    }))
else:
    expired, _ = call('/api/auth/me', token=data['previous'])
    new_login, new = login(data['new'])
    current, _ = call('/api/auth/me', token=new.get('access_token'))
    print(json.dumps({'old_signature': expired, 'new_login': new_login, 'new_token': current}))
"""


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def docker(*args, **options):
    result = subprocess.run([*DOCKER, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", **options)
    if result.returncode:
        raise RuntimeError(f"Local Docker {args[0]} exited {result.returncode}")
    return result.stdout.strip()


def write(path, data):
    temporary = path.with_name(f".{path.name}.new")
    with temporary.open("x", encoding="ascii") as stream:
        stream.write(data + "\n")
    temporary.replace(path)


def release():
    return load(EVIDENCE / "v021_offline_image.json")


def initialize(state):
    signed = load(EVIDENCE / "m3_r01_dispatch_baseline.json")["business_snapshot"]
    state.mkdir(parents=True, exist_ok=False)
    for folder in ("private", "runtime/db", "runtime/config", "runtime/vectors", "runtime/uploads",
                   "runtime/output", "runtime/logs", "runtime/state", "runtime/hf", "runtime/torch"):
        (state / folder).mkdir(parents=True)
    paths = {ROOT / "app/materials.db": (state / "runtime/db/materials.db", signed["files"]["app/materials.db"])}
    for source_dir, target_dir in (("config", "config"), ("app/vector_cache", "vectors")):
        for name, digest in signed["trees"][source_dir]["files"].items():
            paths[ROOT / source_dir / name] = (state / "runtime" / target_dir / name, digest)
    model_prefix = "model_offline/bge-small-zh-v1.5/"
    signed_model = load(ROOT / "doc/V0.1.7/evidence/m0_r01_before.json")["protected_sha256"]
    for name, digest in signed_model.items():
        if name.startswith(model_prefix):
            paths[ROOT / name] = (state / "model" / name.removeprefix(model_prefix), digest)
    for source, (destination, digest) in paths.items():
        if sha(source) != digest:
            raise ValueError(f"Local initialization input differs from the RC snapshot: {source.name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if sha(destination) != digest:
            raise ValueError(f"Local initialization copy failed digest verification: {source.name}")


def start(state, name, *, mode="keyword", port=None):
    switches = load(EVIDENCE / "v021_final_coord_verify.json")["default_switches_frozen_off"]
    image = release()["image_id"]
    env = {**switches, "APP_ENV": "production", "TESTING": "0", "PYTHON_DOTENV_DISABLED": "1",
           "AUTH_DEV_MODE": "false", "ENABLE_DOCS": "false", "HF_HUB_OFFLINE": "1",
           "TRANSFORMERS_OFFLINE": "1", "ADMIN_USERNAME": USER, "EMBEDDING_MODEL": "bge-small-zh-v1.5",
           "ALLOWED_ORIGINS": f"http://127.0.0.1:{port or 18080},http://localhost:{port or 18080}"}
    for key, relative in {
        "AUTH_DB_PATH": "db/auth.db", "MATERIALS_DB_PATH": "db/materials.db", "CONFIG_DIR": "config",
        "VECTOR_CACHE_DIR": "vectors", "LOG_DIR": "logs", "UPLOAD_DIR": "uploads",
        "OUTPUT_DIR": "output", "BINDING_FEEDBACK_FILE": "state/feedback.json",
        "FEEDBACK_SAMPLES_FILE": "state/samples.json", "GOLDEN_DETAIL_FILE": "state/eval.json",
        "HF_HOME": "hf", "TORCH_HOME": "torch",
    }.items():
        env[key] = f"/runtime/{relative}"
    model = state / "model"
    options = ["--network=none", "--rm"] if port is None else [
        "--network=bridge", "--restart=unless-stopped", "-p", f"127.0.0.1:{port}:18080",
    ]
    env.update(LISTTRANS_EVAL_KEYWORD_ONLY="1" if mode == "keyword" else "0",
               LISTTRANS_EVAL_REQUIRE_VECTOR="1" if mode == "vector" else "0")
    return docker("run", "-d", "--pull=never", "--name", name, "--read-only", "--tmpfs", "/tmp:rw,size=512m",
                  *options, "--mount", f"type=bind,source={state / 'runtime'},target=/runtime",
                  "--mount", f"type=bind,source={state / 'private'},target=/run/listtrans-private,readonly",
                  "--mount", f"type=bind,source={model},target=/app/model_offline/bge-small-zh-v1.5,readonly",
                  *(part for key, value in env.items() for part in ("--env", f"{key}={value}")),
                  "--entrypoint", "python", image, "-c", BOOTSTRAP, timeout=90)


def wait_ready(container):
    code = """
from urllib.request import urlopen
from urllib.error import HTTPError
try:
    urlopen('http://127.0.0.1:18080/api/auth/me', timeout=2)
except HTTPError as error:
    assert error.code == 401
else:
    raise RuntimeError('Unauthenticated request did not return 401')
"""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            docker("exec", container, "python", "-c", code, timeout=8)
            return
        except (RuntimeError, subprocess.TimeoutExpired):
            time.sleep(1)
    raise RuntimeError("Isolated local application did not become ready")


def rotate(state):
    initialize(state)
    username = USER
    old_password, new_password = (secrets.token_urlsafe(36) + "!R7" for _ in range(2))
    private = state / "private"
    write(private / "admin-password.txt", old_password)
    write(private / "jwt.key", secrets.token_urlsafe(48))
    first = start(state, f"{FINAL_NAME}-prg-{uuid.uuid4().hex[:8]}")
    try:
        wait_ready(first)
        password = json.loads(docker("exec", "-i", first, "python", "-c", HTTP_PROBE,
                                     input=json.dumps({"step": "password", "username": username, "old": old_password,
                                                       "new": new_password}), timeout=90))
        prior_key_token = password.pop("token")
        assert password == {"before": [200, 200], "changed": 200, "old_token": 401, "old_password": 400,
                            "new_login": 200, "new_token": 200, "must_change_password": False}
    finally:
        docker("stop", "--time", "15", first, timeout=35)
    write(private / "admin-password.txt", new_password)
    write(private / "jwt.key", secrets.token_urlsafe(48))
    second = start(state, f"{FINAL_NAME}-prg-{uuid.uuid4().hex[:8]}", mode="vector")
    try:
        wait_ready(second)
        signature = json.loads(docker("exec", "-i", second, "python", "-c", HTTP_PROBE,
                                      input=json.dumps({"step": "signature", "username": username, "new": new_password,
                                                        "previous": prior_key_token}), timeout=90))
        assert signature == {"old_signature": 401, "new_login": 200, "new_token": 200}
    finally:
        docker("stop", "--time", "15", second, timeout=35)
    return {"exit_code": 0, "scope": "authorized local Docker application account and signing key",
            "operator_authority": "user-authorized local Docker scope", "image_id": release()["image_id"],
            "verified_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
            "target_account_alias": "local-bootstrap-admin", "local_ssh_service": False,
            "auth_database_sha256": sha(state / "runtime/db/auth.db"),
            "password_rotation": password, "signing_key_rotation": signature,
            "isolated_uvicorn_start_modes": ["keyword", "vector"]}


def publish(state, port):
    proof = load(EVIDENCE / "v021_local_gate_status.json")
    gates = proof["local_mandatory_gates_passed"]
    required = {"ENV-V017-01", "PRG-F11", "A-M3-01"}
    if (proof["image_id"] != release()["image_id"] or set(gates) != required
            or any(gates[name] is not True for name in required)
            or proof["auth_database_sha256"] != sha(state / "runtime/db/auth.db")):
        raise ValueError("Local publication requires all three gates on this image")
    container = start(state, FINAL_NAME, mode="vector", port=port)
    url = f"http://127.0.0.1:{port}"
    try:
        wait_ready(container)
        ports = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", container))
        assert ports == {"18080/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]}
        try:
            urlopen(Request(url + "/api/auth/me"), timeout=10)
        except HTTPError as error:
            if error.code != 401:
                raise
        else:
            raise RuntimeError("Expected 401 without local login")
        result = json.loads(docker("exec", "-i", container, "python", "-c", HTTP_PROBE,
                                   input=json.dumps({"step": "signature", "username": USER,
                                                     "new": (state / "private/admin-password.txt").read_text().strip(),
                                                     "previous": ""}), timeout=60))
        if result != {"old_signature": 401, "new_login": 200, "new_token": 200}:
            raise RuntimeError("Published instance could not verify the rotated credentials")
    except (OSError, RuntimeError, ValueError, AssertionError):
        docker("stop", "--time", "15", container, timeout=35)
        raise
    return {"exit_code": 0, "scope": "localhost only", "url": url, "container": container,
            "anonymous_auth_me": 401, "authenticated_auth_me": 200}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("rotate", "publish"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    result = rotate(args.state.resolve()) if args.action == "rotate" else publish(args.state.resolve(), args.port)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(result)
