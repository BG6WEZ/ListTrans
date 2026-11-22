import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _deploy_env import (
    DeployConfigError,
    SSHDeployConfig,
    create_ssh_client,
    load_probe_targets,
    load_ssh_deploy_config,
)


def main():
    try:
        base_cfg = load_ssh_deploy_config(require_remote_dir=False)
        ips = load_probe_targets()
    except DeployConfigError as exc:
        print(f"Configuration error (fail-closed): {exc}")
        sys.exit(1)

    for ip in ips:
        cfg = SSHDeployConfig(
            host=ip,
            port=base_cfg.port,
            user=base_cfg.user,
            remote_dir=base_cfg.remote_dir,
            key_path=base_cfg.key_path,
            password=base_cfg.password,
            known_hosts_path=base_cfg.known_hosts_path,
            allow_unknown_host=base_cfg.allow_unknown_host,
        )
        try:
            ssh = create_ssh_client(cfg)
            _, stdout, _ = ssh.exec_command(
                "hostname; ip addr | grep inet; ls -ld /home/huakang/* /var/www/* /app 2>/dev/null"
            )
            res = stdout.read().decode("utf-8")
            print(f"=== Successfully connected to {ip} as {cfg.user} ===")
            print(res)
            ssh.close()
        except Exception as exc:
            print(f"Could not authenticate/connect to {ip}: {type(exc).__name__}")


if __name__ == "__main__":
    main()
