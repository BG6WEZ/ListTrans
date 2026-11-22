# -*- coding: utf-8 -*-
"""Independent audit check of the built production Docker image listtrans:m5-r02-prod."""
import hashlib
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[3]


def main():
    ins = json.loads(subprocess.check_output(["docker", "image", "inspect", "listtrans:m5-r02-prod"], text=True))[0]
    hist = subprocess.check_output(["docker", "history", "--no-trunc", "listtrans:m5-r02-prod"], text=True)
    env_list = ins["Config"].get("Env") or []
    cmd = ins["Config"].get("Cmd")
    layers = ins["RootFS"]["Layers"]

    inner_py = "\n".join(
        [
            "import hashlib, json, pathlib",
            "root = pathlib.Path('/app')",
            "files = {}",
            "for p in sorted(root.rglob('*')):",
            "    if p.is_file() and '__pycache__' not in p.parts:",
            "        files[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()",
            "print(json.dumps(files))",
        ]
    )
    in_img = json.loads(
        subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--entrypoint",
                "python3",
                "listtrans:m5-r02-prod",
                "-c",
                inner_py,
            ],
            text=True,
        )
    )

    mismatches = []
    for rel, h in in_img.items():
        host_p = ROOT / rel
        if not host_p.exists():
            mismatches.append({"file": rel, "reason": "missing_on_host"})
        else:
            hh = hashlib.sha256(host_p.read_bytes()).hexdigest()
            if hh != h:
                mismatches.append({"file": rel, "reason": "sha256_mismatch", "img": h, "host": hh})

    # Verify fail-closed without SECRET_KEY
    fc = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,exec,size=128m",
            "--env",
            "AUTH_DB_PATH=/tmp/auth.db",
            "--env",
            "MATERIALS_DB_PATH=/tmp/mat.db",
            "listtrans:m5-r02-prod",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )

    # Verify live HTTP server in --network none (internal TestClient equivalent) + /login.html + psutil
    http_py = "\n".join(
        [
            "import json, psutil",
            "from fastapi.testclient import TestClient",
            "from app import server",
            "c = TestClient(server.app)",
            "r = c.get('/login.html')",
            "print(json.dumps({'login_html_status': r.status_code, 'psutil': psutil.__version__}))",
        ]
    )
    http_chk = json.loads(
        subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,exec,size=256m",
                "--env",
                "SECRET_KEY=audit_synth_secret_1234567890abcdef",
                "--env",
                "AUTH_DB_PATH=/tmp/auth.db",
                "--env",
                "MATERIALS_DB_PATH=/tmp/mat.db",
                "--env",
                "UPLOAD_DIR=/tmp/up",
                "--env",
                "OUTPUT_DIR=/tmp/out",
                "--env",
                "LOG_DIR=/tmp/logs",
                "--env",
                "VECTOR_CACHE_DIR=/tmp/vec",
                "--env",
                "LLM_ENABLED=false",
                "--entrypoint",
                "python3",
                "listtrans:m5-r02-prod",
                "-c",
                http_py,
            ],
            text=True,
        )
    )

    forbidden_prefixes = (".env", "doc/", ".local-archive/", "tests/", "model_offline/")
    forbidden_hits = [rel for rel in in_img if rel.startswith(forbidden_prefixes) or rel.endswith(".db")]

    res = {
        "image_id": ins["Id"],
        "layer_count": len(layers),
        "cmd": cmd,
        "env": env_list,
        "in_image_file_count": len(in_img),
        "host_mismatches": mismatches,
        "forbidden_hits": forbidden_hits,
        "history_secret_canary_hits": [l for l in hist.splitlines() if any(k in l for k in ("SECRET_KEY=", "sk-", "PASSWORD="))],
        "fail_closed_no_secret_rc": fc.returncode,
        "fail_closed_msg_ok": ("SECRET_KEY" in (fc.stdout + fc.stderr)),
        "http_chk": http_chk,
    }
    out = ROOT / "doc/V0.1.5/evidence/m5_r02_audit_docker_check.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(res, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
