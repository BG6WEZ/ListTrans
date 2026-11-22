"""Bind the RC offline inputs, installed locks, and SQLite catalog generations."""
import argparse
from contextlib import closing
import hashlib
from importlib.metadata import distributions
import json
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import sys

from m3_r01_run import EVIDENCE, ROOT, check_assets, collect_snapshot, read_json, sha256_file, write_json

V017 = ROOT / "doc/V0.1.7/evidence"
CATALOG = ROOT / "app/materials.db"
CACHE = ROOT / "app/vector_cache"


def manifest(names, baseline):
    files = {}
    for name in sorted(names):
        path = ROOT / name
        digest = sha256_file(path)
        if digest is None or (name in baseline and digest != baseline[name]):
            raise RuntimeError(f"Missing or changed asset: {name}")
        files[name] = {"sha256": digest, "bytes": path.stat().st_size}
    return files


def sqlite_mapping(source, include_catalog=False):
    for suffix in ("-wal", "-journal"):
        sidecar = source.with_name(source.name + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise RuntimeError("SQLite snapshot must be checkpointed before immutable inspection")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        objects = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name").fetchall()
        result = {"quick_check": conn.execute("PRAGMA quick_check").fetchone()[0],
                  "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
                  "schema_sha256": hashlib.sha256(json.dumps(objects, ensure_ascii=False).encode()).hexdigest(),
                  "tables": [name for kind, name, _, _ in objects if kind == "table"]}
        if not include_catalog:
            return result
        info = dict(conn.execute("SELECT key,value FROM materials_metadata WHERE key IN ('db_revision','schema_version')"))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(materials)")}
        versions = (dict(conn.execute("SELECT parsed_schema_version,count(*) FROM materials GROUP BY parsed_schema_version"))
                    if "parsed_schema_version" in columns else {"legacy_column_absent": True})
        result.update({"metadata": info, "effective_db_revision": int(info.get("db_revision", 0)),
                       "materials_rows": conn.execute("SELECT count(*) FROM materials").fetchone()[0],
                       "fts_rows": conn.execute("SELECT count(*) FROM materials_fts").fetchone()[0],
                       "row_parsed_schema_versions": versions})
        return result


def lock_status(historical):
    normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    installed = {normalize(dist.metadata["Name"]): dist.version for dist in distributions()}
    locks = {}
    for name, digest in historical["generated_lock_sha256"].items():
        path = V017 / name
        if sha256_file(path) != digest:
            raise RuntimeError(f"Historical dependency lock changed: {name}")
        lines = path.read_text(encoding="utf-8").splitlines()
        locked = dict(line.split("==", 1) for line in lines if line and not line.startswith("#"))
        differences = {key: {"expected": value, "installed": installed.get(normalize(key))}
                       for key, value in locked.items() if installed.get(normalize(key)) != value}
        locks[name] = {"sha256": digest, "locked": len(locked), "installed_differences": differences}
    pip = subprocess.run([sys.executable, "-B", "-m", "pip", "--disable-pip-version-check", "check"],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    return {"windows_locks": locks, "pip_check": {"exit_code": pip.returncode,
            "output": (pip.stdout + pip.stderr).strip()}}


def docker_status():
    exe = shutil.which("docker")
    probes = {}
    if exe:
        for pipe in ("dockerDesktopLinuxEngine", "docker_engine"):
            cmd = [exe, "--host", f"npipe:////./pipe/{pipe}", "info", "--format", "{{.OSType}} {{.ServerVersion}}"]
            try:
                result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                        errors="replace", timeout=15)
                probes[pipe] = {"exit_code": result.returncode, "output": (result.stdout + result.stderr).strip()}
            except subprocess.TimeoutExpired:
                probes[pipe] = {"status": "local_daemon_probe_timed_out"}
    return {"cli_found": bool(exe), "local_probes": probes,
            "local_linux_daemon_reached": any(probe.get("exit_code") == 0 and probe.get("output", "").startswith("linux ")
                                              for probe in probes.values()),
            "build_layers_startup": "not_executed_by_inventory"}


def run(isolation_report):
    dispatch = read_json(EVIDENCE / "m3_r01_dispatch_baseline.json")
    assets = check_assets(dispatch, collect_snapshot())
    baseline = dispatch["effective_baseline_sha256"]
    historical = read_json(V017 / "m3_r01_dependency_assets.json")
    model_dir = "model_offline/bge-small-zh-v1.5"
    models = {name for name in baseline if name.startswith(model_dir + "/")}
    historical_bge = {name: entry["sha256"] for name, entry in historical["assets"]["offline_models"].items()
                      if name.startswith(model_dir + "/")}
    actual_models = {path.relative_to(ROOT).as_posix() for path in (ROOT / model_dir).rglob("*") if path.is_file()}
    runtime_eval = {name for name in baseline if name.startswith("app/eval_assets/") and name.endswith(".json")}
    runtime = {name for name in baseline if Path(name).parent.as_posix() == "app" and name.endswith(".py")}
    optional = {name for name in baseline if name.startswith(("app/calibration_assets/", "app/recall_candidates/",
                                                              "app/rerank_candidates/"))}
    operational = {name for name in baseline if name.startswith(("config/", "web/"))}
    if ({name: baseline[name] for name in models} != historical_bge or len(models) != 29
            or models != actual_models or len(runtime_eval) != 3):
        raise RuntimeError("Offline model or runtime evaluation assets incomplete")
    status = lock_status(historical)
    report = read_json(isolation_report)
    native_root = Path(report["isolation_root"]).resolve().parent
    auth_path = (native_root / "keyword/db/auth.db").resolve()
    if (report["verdict"] != "PASS" or native_root.is_relative_to(ROOT)
            or not auth_path.is_relative_to(native_root) or not auth_path.is_file()):
        raise RuntimeError("Use the account schema initialized by the external keyword server")
    catalog, auth = sqlite_mapping(CATALOG, include_catalog=True), sqlite_mapping(auth_path)
    if not {"users", "revoked_tokens"} <= set(auth["tables"]):
        raise RuntimeError("Isolated server did not initialize the authorization schema")
    cache = read_json(CACHE / "cache_metadata.json")
    cache_info = {key: cache.get(key) for key in ("model_name", "dimension", "item_count", "db_revision", "text_schema_version")}
    locked = {name: value["sha256"] for name, value in historical["requirements"].items()}
    missing_docker_inputs = sorted(optional)
    return {"head": dispatch["head"], "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(), **assets, "requirements": manifest(locked, locked), **status,
            "image_copy_inputs": manifest(runtime | runtime_eval | operational | {"requirements.txt"}, baseline),
            "container_recipes": manifest({"Dockerfile", ".dockerignore", "docker-compose.yml"}, baseline),
            "out_of_main_image_experimental_assets": manifest(missing_docker_inputs, baseline),
            "offline_model": manifest(models, baseline), "catalog": catalog, "synthetic_auth_schema": auth,
            "sqlite_catalog_sha256": sha256_file(CATALOG), "vector_cache_metadata": cache_info,
            "vector_cache_sha256": dispatch["business_snapshot_sha256"]["trees"]["app/vector_cache"]["files"],
            "parsed_schema_target": 3, "linux_wheelhouse_verified": False, "docker": docker_status()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolation", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = run(args.isolation)
    write_json(args.out, output)
    good = (output["pip_check"]["exit_code"] == 0
            and all(not lock["installed_differences"] for lock in output["windows_locks"].values())
            and output["catalog"]["quick_check"] == output["synthetic_auth_schema"]["quick_check"] == "ok")
    raise SystemExit(0 if good else 1)
