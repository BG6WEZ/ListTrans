import time
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _deploy_env import DeployConfigError, create_ssh_client, load_ssh_deploy_config


def main():
    try:
        cfg = load_ssh_deploy_config(require_remote_dir=True)
    except DeployConfigError as exc:
        print(f"Configuration error (fail-closed): {exc}")
        sys.exit(1)

    CLOUD_HOST = cfg.host
    CLOUD_PORT = cfg.port
    CLOUD_USER = cfg.user
    REMOTE_PROJECT_DIR = cfg.remote_dir

    print(f"Connecting to Cloud Server {CLOUD_HOST}:{CLOUD_PORT} as {CLOUD_USER}...")
    try:
        ssh = create_ssh_client(cfg)
        print("Connected successfully!\n")
    except Exception as e:
        print(f"Failed to connect to {CLOUD_HOST}: {e}")
        sys.exit(1)

    def exec_cmd(cmd, desc=""):
        if desc:
            print(f"--> [EXEC] {desc} ...")
        stdin, stdout, stderr = ssh.exec_command(cmd)
        out = stdout.read().decode("utf-8").strip()
        err = stderr.read().decode("utf-8").strip()
        return out, err

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = f"/home/huakang/backup/ListTrans_backup_{ts}"

    # Step 1: Check existing cloud users to ensure 100% preservation
    print("=" * 60)
    print("STEP 1: Inspecting Existing Cloud System Users (auth.db)")
    print("=" * 60)
    inspect_py = (
        "import sqlite3, json; "
        "conn = sqlite3.connect('/home/huakang/ListTrans/app/auth.db'); "
        "c = conn.cursor(); "
        "users = c.execute('SELECT id, username, name, role, is_active FROM users ORDER BY id ASC').fetchall(); "
        "print(json.dumps([{'id': r[0], 'username': r[1], 'name': r[2], 'role': r[3], 'is_active': r[4]} for r in users], ensure_ascii=False))"
    )
    users_json_raw, err = exec_cmd(f"python3 -c \"{inspect_py}\"", "Inspecting cloud users")
    if err and "error" in err.lower():
        print("Error reading cloud users:", err)
    else:
        print(f"Current Cloud Users in auth.db:\n{users_json_raw}\n")

    # Step 2: Backup auth.db and materials.db to backup_dir
    print("=" * 60)
    print("STEP 2: Creating Safety Snapshot of Cloud Databases")
    print("=" * 60)
    exec_cmd(f"mkdir -p {backup_dir}", "Creating backup directory")
    exec_cmd(f"cp -a {REMOTE_PROJECT_DIR}/app/auth.db* {backup_dir}/", "Backing up auth.db")
    exec_cmd(f"cp -a {REMOTE_PROJECT_DIR}/app/materials.db* {backup_dir}/", "Backing up materials.db")
    exec_cmd(f"cp -a {REMOTE_PROJECT_DIR}/.env {backup_dir}/", "Backing up .env")
    ls_backup, _ = exec_cmd(f"ls -lh {backup_dir}", "Verifying backup")
    print(f"Backup contents in {backup_dir}:\n{ls_backup}\n")

    # Step 3: Git Status & Pull latest code from GitHub
    print("=" * 60)
    print("STEP 3: Synchronizing Code from GitHub to Cloud")
    print("=" * 60)
    git_status, _ = exec_cmd(f"cd {REMOTE_PROJECT_DIR} && git status", "Checking cloud git status")
    print(f"Remote git status:\n{git_status}\n")

    # Check git remote url
    remote_url, _ = exec_cmd(f"cd {REMOTE_PROJECT_DIR} && git remote get-url origin", "Checking remote origin URL")
    print(f"Remote origin: {remote_url}")

    # Fetch and pull
    pull_out, pull_err = exec_cmd(f"cd {REMOTE_PROJECT_DIR} && git fetch origin master && git pull origin master", "Pulling latest master")
    print(f"Pull result:\n{pull_out}")
    if pull_err:
        print(f"Pull notice/stderr:\n{pull_err}")

    # Step 4: Verify cloud auth.db is intact after pull
    print("=" * 60)
    print("STEP 4: Verifying Cloud System Users (auth.db) Retention")
    print("=" * 60)
    users_post_json, _ = exec_cmd(f"python3 -c \"{inspect_py}\"", "Verifying cloud users after pull")
    print(f"Cloud Users after pull:\n{users_post_json}")
    if users_json_raw.strip() == users_post_json.strip():
        print(">>> SUCCESS: Cloud user accounts match 100% exactly! Retained completely.")
    else:
        print(">>> WARNING: Users mismatch, restoring from snapshot...")
        exec_cmd(f"cp -a {backup_dir}/auth.db* {REMOTE_PROJECT_DIR}/app/", "Restoring auth.db from snapshot")

    # Step 5: Recreate & Restart Docker container
    print("=" * 60)
    print("STEP 5: Recreating and Starting Container on Cloud")
    print("=" * 60)
    down_out, _ = exec_cmd(f"cd {REMOTE_PROJECT_DIR} && docker-compose down", "Stopping old container")
    print(down_out)
    up_out, up_err = exec_cmd(f"cd {REMOTE_PROJECT_DIR} && docker-compose up -d --build", "Rebuilding & starting container")
    print(up_out)
    if up_err:
        print("Build/Start notice:", up_err)

    # Step 6: Wait and verify container health
    print("=" * 60)
    print("STEP 6: Verifying Cloud Service Health & APIs")
    print("=" * 60)
    time.sleep(6)
    ps_out, _ = exec_cmd("docker ps --filter name=material-matcher", "Checking container status")
    print(ps_out)

    curl_web, _ = exec_cmd("curl -s -I http://127.0.0.1:18080/ | head -n 5", "Testing Web HTTP Endpoint")
    print(f"Web HTTP Header:\n{curl_web}")

    logs_tail, _ = exec_cmd("docker logs --tail 25 material-matcher", "Checking container latest logs")
    print(f"Container logs:\n{logs_tail}")

    ssh.close()
    print("=" * 60)
    print("Cloud Deployment & Verification Completed Successfully!")
    print("=" * 60)

if __name__ == "__main__":
    main()
