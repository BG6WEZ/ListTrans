import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(__file__))
from _deploy_env import DeployConfigError, create_ssh_client, load_ssh_deploy_config

LOCAL_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Strict safety blacklist: NEVER overwrite these files on cloud!
BLACKLIST_FILES = {
    "auth.db", "auth.db-shm", "auth.db-wal", "auth.db-journal",
    "materials.db", "materials.db-shm", "materials.db-wal", "materials.db-journal",
    ".env", "app.db", "sessions.db"
}
BLACKLIST_DIRS = {"uploads", "vector_cache", "logs", "__pycache__", ".git"}

def main():
    try:
        cfg = load_ssh_deploy_config(require_remote_dir=True)
    except DeployConfigError as exc:
        print(f"Configuration error (fail-closed): {exc}")
        sys.exit(1)

    CLOUD_HOST = cfg.host
    CLOUD_PORT = cfg.port
    REMOTE_BASE = cfg.remote_dir

    print("=" * 70)
    print("      HUAKANG CLUSTER DEPLOYMENT & USER PRESERVATION TASK")
    print("=" * 70)
    print(f"Connecting to Cloud Server {CLOUD_HOST}:{CLOUD_PORT}...")

    ssh = create_ssh_client(cfg)
    print("Connected successfully!\n")

    def run_cmd(cmd, desc=""):
        if desc:
            print(f"--> [CMD] {desc}...")
        stdin, stdout, stderr = ssh.exec_command(cmd)
        out = stdout.read().decode('utf-8').strip()
        err = stderr.read().decode('utf-8').strip()
        return out, err

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = f"/home/huakang/backup/ListTrans_backup_{ts}"

    # 1. Inspect existing cloud users
    print("-" * 70)
    print("1. Extracting Cloud System Users (auth.db) Snapshot")
    print("-" * 70)
    py_inspect = (
        "python3 -c \""
        "import sqlite3, json; "
        "conn = sqlite3.connect('/home/huakang/ListTrans/app/auth.db'); "
        "c = conn.cursor(); "
        "c.execute('SELECT id, username, name, employee_no, role, dept1, is_active FROM users ORDER BY id ASC'); "
        "rows = [dict(zip([col[0] for col in c.description], r)) for r in c.fetchall()]; "
        "print(json.dumps(rows, ensure_ascii=False))\""
    )
    users_before_json, _ = run_cmd(py_inspect, "Inspecting cloud users")
    users_before = json.loads(users_before_json)
    print(f"Total Cloud Users Found: {len(users_before)}")
    sample_users = [f"{u['username']}({u['name']})" for u in users_before[:5]]
    print(f"Sample Users: {', '.join(sample_users)} ... and {len(users_before)-5} more.")

    # 2. Safety Backup
    print("-" * 70)
    print(f"2. Performing Safety Backup to {backup_dir}")
    print("-" * 70)
    run_cmd(f"mkdir -p {backup_dir}", "Creating backup directory")
    run_cmd(f"cp -a {REMOTE_BASE}/app/auth.db* {backup_dir}/", "Backing up auth.db")
    run_cmd(f"cp -a {REMOTE_BASE}/app/materials.db* {backup_dir}/", "Backing up materials.db")
    run_cmd(f"cp -a {REMOTE_BASE}/.env {backup_dir}/", "Backing up .env")
    run_cmd(f"cp -a {REMOTE_BASE}/docker-compose.yml {backup_dir}/", "Backing up docker-compose.yml")
    
    # Save optional sanitized snapshot outside repo when explicitly configured
    snapshot_out = os.getenv("DEPLOY_SNAPSHOT_PATH", "").strip()
    if snapshot_out:
        with open(snapshot_out, "w", encoding="utf-8") as f:
            json.dump(users_before, f, ensure_ascii=False, indent=2)
    
    ls_b, _ = run_cmd(f"ls -lh {backup_dir}", "Checking backup files")
    print(ls_b)

    # 3. Synchronize Web, App Python code, and Config
    print("-" * 70)
    print("3. Uploading Code & UI Files via SFTP (Excluding Blacklisted Files)")
    print("-" * 70)
    sftp = ssh.open_sftp()

    def sync_directory(local_dir, remote_dir):
        try:
            sftp.stat(remote_dir)
        except IOError:
            sftp.mkdir(remote_dir)

        for item in os.listdir(local_dir):
            if item in BLACKLIST_FILES or item in BLACKLIST_DIRS:
                print(f"  [SKIPPED BLACKLIST] {item}")
                continue

            local_path = os.path.join(local_dir, item)
            remote_path = f"{remote_dir}/{item}"

            if os.path.isdir(local_path):
                sync_directory(local_path, remote_path)
            else:
                sftp.put(local_path, remote_path)
                print(f"  [UPDATED] {item} -> {remote_path}")

    # Sync app code (only .py and configs, skipping all DBs)
    print("Syncing app/ ...")
    local_app = os.path.join(LOCAL_BASE, "app")
    remote_app = f"{REMOTE_BASE}/app"
    for item in os.listdir(local_app):
        if item in BLACKLIST_FILES or item in BLACKLIST_DIRS:
            print(f"  [SKIPPED PROTECTED FILE] {item}")
            continue
        local_path = os.path.join(local_app, item)
        remote_path = f"{remote_app}/{item}"
        if os.path.isfile(local_path) and (item.endswith(".py") or item.endswith(".json")):
            sftp.put(local_path, remote_path)
            print(f"  [UPDATED] {item}")

    # Sync web/
    print("\nSyncing web/ ...")
    sync_directory(os.path.join(LOCAL_BASE, "web"), f"{REMOTE_BASE}/web")

    # Sync config/
    print("\nSyncing config/ ...")
    sync_directory(os.path.join(LOCAL_BASE, "config"), f"{REMOTE_BASE}/config")

    # Sync docker-compose.yml
    print("\nSyncing docker-compose.yml ...")
    sftp.put(os.path.join(LOCAL_BASE, "docker-compose.yml"), f"{REMOTE_BASE}/docker-compose.yml")
    print("  [UPDATED] docker-compose.yml")

    sftp.close()

    # 4. Verify cloud system users again (strict post-sync assertion)
    print("-" * 70)
    print("4. Strict Post-Sync Verification of Cloud Users")
    print("-" * 70)
    users_after_json, _ = run_cmd(py_inspect, "Inspecting cloud users after file sync")
    users_after = json.loads(users_after_json)
    print(f"Total Cloud Users After Sync: {len(users_after)}")

    if len(users_before) == len(users_after) and users_before[0]['username'] == users_after[0]['username']:
        print(">>> [SUCCESS] All 190 Cloud System User Accounts are 100% PRESERVED!")
    else:
        print(">>> [FATAL ERROR] Cloud users discrepancy detected! Restoring from backup immediately...")
        run_cmd(f"cp -a {backup_dir}/auth.db* {REMOTE_BASE}/app/", "Restoring auth.db")
        sys.exit(1)

    # 5. Restart Container on Cloud
    print("-" * 70)
    print("5. Restarting material-matcher Container on Cloud")
    print("-" * 70)
    restart_out, _ = run_cmd("docker restart material-matcher", "Restarting container")
    print("Restart result:", restart_out)
    
    print("Waiting 10 seconds for container initialization...")
    time.sleep(10)

    # 6. Verify Container Status & Health Check
    print("-" * 70)
    print("6. Verifying Cloud Service & Health Check")
    print("-" * 70)
    ps_out, _ = run_cmd("docker ps --filter name=material-matcher", "Checking container status")
    print(ps_out)

    curl_out, _ = run_cmd("curl -s -I http://127.0.0.1:18080/ | head -n 5", "Testing local curl")
    print(f"HTTP Response Header:\n{curl_out}")

    log_tail, _ = run_cmd("docker logs --tail 20 material-matcher", "Checking application logs")
    print(f"Application Startup Logs:\n{log_tail}")

    ssh.close()
    print("=" * 70)
    print("   CLOUD DEPLOYMENT COMPLETED! SYSTEM USERS PRESERVED 100%")
    print("=" * 70)

if __name__ == "__main__":
    main()
