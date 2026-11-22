# -*- coding: utf-8 -*-
"""
V015-M0 可复验环境证据脚本（仅用 Python 标准库）。

执行内容：
1. 对业务库（materials.db/auth.db 及 WAL/SHM）、config/、vector_cache/、
   uploads/、logs/、data/ 建立运行前后 SHA256 快照并比对。
2. 在临时隔离根内完成 SQLite 建表与写入，验证不触碰业务库。
3. 探测核心依赖可用性，输出缺失清单（缺依赖记录为环境缺口，不伪通过）。
4. 依赖可用时，导入 app 路径常量模块并校验其解析路径位于隔离根内。

用法：
  python tests/verify_isolation.py
输出：JSON 报告打印到 stdout，并写入 --out 指定路径（默认 evidence 目录）。
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROD_APP_DIR = PROJECT_ROOT / "app"
PROD_CONFIG_DIR = PROJECT_ROOT / "config"
PROD_VECTOR_CACHE = PROD_APP_DIR / "vector_cache"
PROD_UPLOADS = PROD_APP_DIR / "uploads"

# 以脚本方式运行（python tests/verify_isolation.py）时 sys.path[0] 是 tests/，
# 需显式把项目根加入 sys.path，app.* 业务模块才能被逐模块独立导入校验。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WATCH_PATHS = [
    PROD_APP_DIR / "materials.db",
    PROD_APP_DIR / "materials.db-wal",
    PROD_APP_DIR / "materials.db-shm",
    PROD_APP_DIR / "auth.db",
    PROD_APP_DIR / "auth.db-wal",
    PROD_APP_DIR / "auth.db-shm",
    PROD_APP_DIR / "binding_feedback.json",
    PROD_APP_DIR / "feedback_samples.json",
]
WATCH_TREES = [
    PROD_CONFIG_DIR,
    PROD_VECTOR_CACHE,
    PROD_UPLOADS,
    PROJECT_ROOT / "logs",
    PROJECT_ROOT / "data",
]

CORE_DEPS = ["pandas", "openpyxl", "fastapi", "jose", "bcrypt", "dotenv",
             "numpy", "faiss", "sentence_transformers", "pytest"]


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_tree(root: Path) -> dict:
    if not root.exists():
        return {"__exists__": False, "files": {}}
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files[str(p.relative_to(root)).replace("\\", "/")] = sha256_file(p)
    return {"__exists__": True, "files": files}


def collect_snapshot() -> dict:
    snap = {"files": {}, "trees": {}}
    for p in WATCH_PATHS:
        snap["files"][str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")] = (
            sha256_file(p)
        )
    for t in WATCH_TREES:
        snap["trees"][str(t.relative_to(PROJECT_ROOT)).replace("\\", "/")] = (
            snapshot_tree(t)
        )
    return snap


def diff_snapshots(before: dict, after: dict) -> list:
    changes = []
    for key in sorted(set(before["files"]) | set(after["files"])):
        if before["files"].get(key) != after["files"].get(key):
            changes.append(f"文件变化: {key}")
    for key in sorted(set(before["trees"]) | set(after["trees"])):
        if before["trees"].get(key) != after["trees"].get(key):
            changes.append(f"目录变化: {key}")
    return changes


def build_layout(root: Path) -> dict:
    layout = {
        "root": root,
        "materials_db": root / "db" / "materials.db",
        "auth_db": root / "db" / "auth.db",
        "config_dir": root / "config",
        "vector_cache_dir": root / "vector_cache",
        "upload_dir": root / "uploads",
        "output_dir": root / "output",
        "log_dir": root / "logs",
        "binding_feedback_file": root / "runtime" / "binding_feedback.json",
        "feedback_samples_file": root / "runtime" / "feedback_samples.json",
        "golden_detail_file": root / "runtime" / "golden_last_run.json",
    }
    for key, path in layout.items():
        if key in ("materials_db", "auth_db", "binding_feedback_file",
                   "feedback_samples_file", "golden_detail_file"):
            path.parent.mkdir(parents=True, exist_ok=True)
        elif key != "root":
            path.mkdir(parents=True, exist_ok=True)
    if PROD_CONFIG_DIR.exists():
        for item in PROD_CONFIG_DIR.iterdir():
            if item.is_file():
                shutil.copy2(item, layout["config_dir"] / item.name)
    return layout


def apply_env(layout: dict) -> None:
    os.environ.update({
        "MATERIALS_DB_PATH": str(layout["materials_db"]),
        "AUTH_DB_PATH": str(layout["auth_db"]),
        "CONFIG_DIR": str(layout["config_dir"]),
        "VECTOR_CACHE_DIR": str(layout["vector_cache_dir"]),
        "UPLOAD_DIR": str(layout["upload_dir"]),
        "OUTPUT_DIR": str(layout["output_dir"]),
        "LOG_DIR": str(layout["log_dir"]),
        "BINDING_FEEDBACK_FILE": str(layout["binding_feedback_file"]),
        "FEEDBACK_SAMPLES_FILE": str(layout["feedback_samples_file"]),
        "GOLDEN_DETAIL_FILE": str(layout["golden_detail_file"]),
        "TESTING": "1",
        "LLM_ENABLED": "false",
        "AUTH_DEV_MODE": "false",
    })


def exercise_isolated_dbs(layout: dict) -> dict:
    result = {"materials": {}, "auth": {}}
    conn = sqlite3.connect(layout["materials_db"])
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE materials (code TEXT PRIMARY KEY, name TEXT, "
            "specification TEXT, category TEXT)"
        )
        cur.execute(
            "INSERT INTO materials VALUES (?, ?, ?, ?)",
            ("T.001", "隔离验证物料", "DN15", "测试分类"),
        )
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM materials")
        result["materials"]["rows"] = cur.fetchone()[0]
    finally:
        conn.close()
    result["materials"]["path"] = str(layout["materials_db"])
    result["materials"]["size"] = layout["materials_db"].stat().st_size

    conn = sqlite3.connect(layout["auth_db"])
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, "
            "role TEXT)"
        )
        cur.execute(
            "INSERT INTO users (username, role) VALUES (?, ?)",
            ("iso_verify", "employee"),
        )
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM users")
        result["auth"]["rows"] = cur.fetchone()[0]
    finally:
        conn.close()
    result["auth"]["path"] = str(layout["auth_db"])
    result["auth"]["size"] = layout["auth_db"].stat().st_size
    return result


def check_imported_paths(layout: dict) -> dict:
    """逐模块独立导入并校验解析路径在隔离根内。

    C-M0-02: 严格区分三类结果，禁止把真实检查异常笼统描述成环境缺口——
    - ok：导入成功且路径位于隔离根内；
    - pending：可识别的缺依赖（ModuleNotFoundError），模块未校验，记待验证；
    - escape/error：路径逃逸隔离根或导入成功后的其他异常，属真实失败，登记 hard_errors。
    """
    root = layout["root"].resolve()
    report = {
        "available": {},
        "module_path_checks": [],
        "pending": [],
        "hard_errors": [],
        # 兼容历史证据字段：errors 仅保留真实失败，不再混入环境缺口
        "errors": [],
    }
    for dep in CORE_DEPS:
        try:
            report["available"][dep] = (
                importlib.util.find_spec(dep) is not None
            )
        except Exception as exc:
            report["available"][dep] = False
            report["hard_errors"].append(f"{dep} 探测异常: {exc}")
            report["errors"].append(f"{dep} 探测异常: {exc}")

    # 元组：(模块名, 路径属性, 期望隔离路径, 是否优先调用 _resolve_db_path 实时解析)
    checks = [
        ("app.db_manager", "DB_PATH", layout["materials_db"], True),
        ("app.auth_manager", "AUTH_DB_PATH", layout["auth_db"], False),
        ("app.vector_index", "CACHE_DIR", layout["vector_cache_dir"], False),
        ("app.llm_rule_engine", "DB_PATH", layout["materials_db"], True),
        ("app.llm_client", "DB_PATH", layout["materials_db"], True),
        ("app.feedback_train", "FEEDBACK_FILE",
         layout["feedback_samples_file"], False),
    ]
    for module_name, attr, expected, use_resolver in checks:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            # 可识别缺依赖：明确待验证，不计失败、不伪装通过
            report["module_path_checks"].append({
                "module": module_name,
                "attr": attr,
                "actual": None,
                "inside_isolation": False,
                "status": f"pending：缺依赖待验证：{type(exc).__name__}: {exc}",
            })
            report["pending"].append(module_name)
            continue
        try:
            if use_resolver and hasattr(module, "_resolve_db_path"):
                raw_path = module._resolve_db_path()
            else:
                raw_path = getattr(module, attr)
            actual = Path(raw_path).resolve()
            inside = actual == expected.resolve() or root in actual.parents
        except Exception as exc:
            # 导入成功后取路径/解析失败：真实检查异常，必须 FAIL，不得降格为环境缺口
            report["module_path_checks"].append({
                "module": module_name,
                "attr": attr,
                "actual": None,
                "inside_isolation": False,
                "status": f"error：{type(exc).__name__}: {exc}",
            })
            report["hard_errors"].append(
                f"{module_name}.{attr} 路径检查异常: {type(exc).__name__}: {exc}"
            )
            report["errors"].append(report["hard_errors"][-1])
            continue
        status = "ok" if inside else "escape"
        report["module_path_checks"].append({
            "module": module_name,
            "attr": attr,
            "actual": str(actual),
            "inside_isolation": bool(inside),
            "status": status,
        })
        if not inside:
            message = f"{module_name}.{attr}={actual} 逃逸出隔离根 {root}"
            report["hard_errors"].append(message)
            report["errors"].append(message)
    return report


def decide_verdict(changes: list, db_result: dict, import_result: dict) -> tuple:
    """三态裁决，返回 (verdict, exit_code)：

    - 存在路径逃逸/真实检查异常/业务快照变化/隔离写入不符 -> FAIL / 1；
    - 无失败但存在缺依赖待验证模块 -> PENDING / 2（非成功退出码，可与 FAIL 区分）；
    - 全部路径检查、隔离写入、业务快照均满足 -> PASS / 0。
    """
    write_ok = (
        db_result["materials"]["rows"] == 1
        and db_result["auth"]["rows"] == 1
    )
    if changes or import_result["hard_errors"] or not write_ok:
        return "FAIL", 1
    if import_result["pending"]:
        return "PENDING", 2
    return "PASS", 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "doc" / "V0.1.5" / "evidence"
                    / "m0_isolation_verify.json"),
    )
    parser.add_argument("--keep-root", action="store_true",
                        help="保留隔离根目录（默认保留，便于复核）")
    args = parser.parse_args()

    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    isolation_root = Path(
        tempfile.mkdtemp(prefix="listtrans_m0_verify_")
    ).resolve()
    layout = build_layout(isolation_root)
    apply_env(layout)

    before = collect_snapshot()
    db_result = exercise_isolated_dbs(layout)
    import_result = check_imported_paths(layout)
    after = collect_snapshot()
    changes = diff_snapshots(before, after)

    # 隔离根内容清单（证明写入发生在隔离区）
    isolated_files = []
    for p in sorted(isolation_root.rglob("*")):
        if p.is_file():
            isolated_files.append(
                str(p.relative_to(isolation_root)).replace("\\", "/")
            )

    verdict, exit_code = decide_verdict(changes, db_result, import_result)
    report = {
        "task": "V015-M0",
        "started_at": started_at,
        "python": sys.version.split()[0],
        "isolation_root": str(isolation_root),
        "env_layout": {k: str(v) for k, v in layout.items()},
        "isolated_db_write": db_result,
        "isolated_files": isolated_files,
        "dependency_probe": import_result,
        "pending_modules": import_result["pending"],
        "hard_errors": import_result["hard_errors"],
        "business_hash_unchanged": len(changes) == 0,
        "business_changes": changes,
        "business_snapshot_before": before,
        "business_snapshot_after": after,
        # C-M0-02: PASS/0 仅在路径检查、隔离写入、业务快照全部满足时给出；
        # 缺依赖待验证为 PENDING/2；真实失败为 FAIL/1
        "verdict": verdict,
        "exit_code": exit_code,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "verdict": report["verdict"],
        "exit_code": exit_code,
        "business_hash_unchanged": report["business_hash_unchanged"],
        "business_changes": changes,
        "pending_modules": report["pending_modules"],
        "hard_errors": report["hard_errors"],
        "isolation_root": str(isolation_root),
        "out": str(out_path),
        "missing_deps": [
            d for d, ok in import_result["available"].items() if not ok
        ],
    }, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
