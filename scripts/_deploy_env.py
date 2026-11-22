# -*- coding: utf-8 -*-
"""Fail-closed environment and SSH configuration loader for deployment scripts."""

import os
from dataclasses import dataclass
from typing import List, Optional


class DeployConfigError(RuntimeError):
    """Raised when required deployment target or SSH credentials are missing or invalid."""


@dataclass(frozen=True)
class SSHDeployConfig:
    host: str
    port: int
    user: str
    remote_dir: str
    key_path: Optional[str]
    password: Optional[str]
    known_hosts_path: Optional[str]
    allow_unknown_host: bool


def load_ssh_deploy_config(
    require_remote_dir: bool = False,
    default_remote_dir: str = "/home/huakang/ListTrans",
) -> SSHDeployConfig:
    """Load SSH deployment settings exclusively from environment variables; fail closed if incomplete."""
    host = os.getenv("DEPLOY_HOST", "").strip()
    user = os.getenv("DEPLOY_USER", "").strip()
    port_raw = os.getenv("DEPLOY_PORT", "22").strip()
    remote_dir = os.getenv("DEPLOY_REMOTE_DIR", default_remote_dir if not require_remote_dir else "").strip()
    key_path = os.getenv("DEPLOY_SSH_KEY_PATH", "").strip() or None
    password = os.getenv("DEPLOY_SSH_PASSWORD", "").strip() or None
    known_hosts_path = os.getenv("DEPLOY_KNOWN_HOSTS", "").strip() or None
    allow_unknown_host = os.getenv("DEPLOY_ALLOW_UNKNOWN_HOST", "false").strip().lower() == "true"

    if not host or host.startswith("<REPLACE"):
        raise DeployConfigError("DEPLOY_HOST environment variable is required.")
    if not user or user.startswith("<REPLACE"):
        raise DeployConfigError("DEPLOY_USER environment variable is required.")
    if require_remote_dir and (not remote_dir or remote_dir.startswith("<REPLACE")):
        raise DeployConfigError("DEPLOY_REMOTE_DIR environment variable is required.")

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise DeployConfigError(f"Invalid DEPLOY_PORT '{port_raw}': {exc}") from exc

    if key_path:
        expanded = os.path.expanduser(key_path)
        if not os.path.isfile(expanded):
            raise DeployConfigError(f"DEPLOY_SSH_KEY_PATH does not exist: {expanded}")
        key_path = expanded

    if not key_path and not password:
        raise DeployConfigError(
            "Missing SSH authentication: configure DEPLOY_SSH_KEY_PATH (recommended) or DEPLOY_SSH_PASSWORD."
        )

    return SSHDeployConfig(
        host=host,
        port=port,
        user=user,
        remote_dir=remote_dir or default_remote_dir,
        key_path=key_path,
        password=password,
        known_hosts_path=known_hosts_path,
        allow_unknown_host=allow_unknown_host,
    )


def load_probe_targets() -> List[str]:
    """Parse DEPLOY_PROBE_HOSTS comma-separated host list without hardcoded IPs."""
    raw = os.getenv("DEPLOY_PROBE_HOSTS", "").strip()
    hosts = [h.strip() for h in raw.split(",") if h.strip() and not h.strip().startswith("<REPLACE")]
    if not hosts:
        raise DeployConfigError("DEPLOY_PROBE_HOSTS environment variable is required (comma-separated IPs/hosts).")
    return hosts


def create_ssh_client(cfg: SSHDeployConfig):
    """Instantiate and connect a paramiko.SSHClient without insecure plaintext fallbacks."""
    import paramiko

    ssh = paramiko.SSHClient()
    if cfg.known_hosts_path:
        ssh.load_host_keys(os.path.expanduser(cfg.known_hosts_path))
    else:
        ssh.load_system_host_keys()

    if cfg.allow_unknown_host:
        ssh.set_missing_host_key_policy(paramiko.WarningPolicy())
    else:
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())

    connect_kwargs = {
        "hostname": cfg.host,
        "port": cfg.port,
        "username": cfg.user,
        "timeout": 15,
    }
    if cfg.key_path:
        connect_kwargs["key_filename"] = cfg.key_path
    else:
        connect_kwargs["password"] = cfg.password

    ssh.connect(**connect_kwargs)
    return ssh
