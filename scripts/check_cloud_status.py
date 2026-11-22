import os
import sys
import json

sys.path.insert(0, os.path.dirname(__file__))
from _deploy_env import DeployConfigError, create_ssh_client, load_ssh_deploy_config


def main():
    try:
        cfg = load_ssh_deploy_config(require_remote_dir=False)
    except DeployConfigError as exc:
        print(f"Configuration error (fail-closed): {exc}")
        sys.exit(1)

    ssh = create_ssh_client(cfg)

    def run(cmd):
        stdin, stdout, stderr = ssh.exec_command(cmd)
        return stdout.read().decode("utf-8").strip(), stderr.read().decode("utf-8").strip()

    print("=== 1. Cloud Docker Containers ===")
    out, _ = run("docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'")
    print(out)

    print(f"\n=== 2. Git Status in {cfg.remote_dir} ===")
    out, _ = run(f"cd {cfg.remote_dir} && git status && git log -n 3 --oneline")
    print(out)

    print("\n=== 3. Cloud System Users (Metadata Only) ===")
    py_cmd = (
        f"python3 -c \""
        f"import sqlite3, json; "
        f"conn = sqlite3.connect('{cfg.remote_dir}/app/auth.db'); "
        f"c = conn.cursor(); "
        f"c.execute('SELECT id, username, name, employee_no, role, dept1, is_active FROM users ORDER BY id ASC'); "
        f"rows = [dict(zip([col[0] for col in c.description], r)) for r in c.fetchall()]; "
        f"print(json.dumps(rows, ensure_ascii=False, indent=2))\""
    )
    out, err = run(py_cmd)
    print(out)
    if err:
        print("Err:", err)

    ssh.close()


if __name__ == "__main__":
    main()
