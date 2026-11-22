"""独立审核：证明 M2 改动模块经环境变量解析到仓外隔离库，未裸连仓库主库。

在子进程中以临时目录设置 MATERIALS_DB_PATH / AUTH_DB_PATH，
再导入 M2 相关模块，通过各模块自身的连接工厂打开连接并读取 PRAGMA database_list。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

repo_materials = ROOT / "app" / "materials.db"
repo_auth = ROOT / "app" / "auth.db"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


before = {str(p): sha(p) for p in (repo_materials, repo_auth)}

with tempfile.TemporaryDirectory(prefix="audit_m2_dbpath_", ignore_cleanup_errors=True) as tmp:
    root = Path(tmp).resolve()
    assert ROOT not in root.parents and root != ROOT
    mats = root / "materials.db"
    auth = root / "auth.db"
    shutil.copyfile(repo_materials, mats)
    shutil.copyfile(repo_auth, auth)
    os.environ["MATERIALS_DB_PATH"] = str(mats)
    os.environ["AUTH_DB_PATH"] = str(auth)
    os.environ.update(
        TESTING="1", LISTTRANS_TEST_ROOT=str(root), LLM_ENABLED="false",
        AUTH_DEV_MODE="false", PYTHON_DOTENV_DISABLED="1",
        DASHSCOPE_API_KEY="", DEEPSEEK_API_KEY="", HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        LOG_DIR=str(root / "logs"), UPLOAD_DIR=str(root / "uploads"),
        OUTPUT_DIR=str(root / "outputs"), CONFIG_DIR=str(root / "config"),
        VECTOR_CACHE_DIR=str(root / "vector_cache"),
    )

    from app import db_manager, auth_manager, flywheel_db, binding_decisions
    from app import source_binding_review  # noqa: F401  (导入即校验无导入期裸连)

    paths = {}
    with closing(db_manager.get_db_connection()) as conn:
        paths["db_manager.get_db_connection"] = conn.execute("PRAGMA database_list").fetchall()[0][2]
    with closing(auth_manager.get_auth_connection()) as conn:
        paths["auth_manager.get_auth_connection"] = conn.execute("PRAGMA database_list").fetchall()[0][2]
    with closing(flywheel_db.open_flywheel_tx_connection()) as conn:
        paths["flywheel_db.open_flywheel_tx_connection"] = conn.execute("PRAGMA database_list").fetchall()[0][2]
    binding_decisions.init_binding_decision_db()
    with closing(auth_manager.get_auth_connection()) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    flywheel_db.init_flywheel_tables()
    with closing(flywheel_db.open_flywheel_tx_connection()) as conn:
        fwnames = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

expected = {"db_manager.get_db_connection": str(mats),
            "auth_manager.get_auth_connection": str(auth),
            "flywheel_db.open_flywheel_tx_connection": str(mats)}
all_ok = all(Path(paths[k]).resolve() == Path(v).resolve() for k, v in expected.items())
after = {str(p): sha(p) for p in (repo_materials, repo_auth)}
report = {
    "verdict": "PASS" if all_ok and before == after else "FAIL",
    "isolated_paths": paths,
    "expected_paths": expected,
    "repo_db_sha_unchanged": before == after,
    "auth_m2_tables_created_in_isolated_db": sorted(
        n for n in ("source_binding_decisions", "priority_binding_receipts", "priority_binding_outbox")
        if n in names),
    "materials_m2_table_created_in_isolated_db": "flywheel_priority_confirmation_events" in fwnames,
    "note": "db_manager 未定义 _resolve_db_path；flywheel_db 回退到导入期 db_manager.DB_PATH（由 MATERIALS_DB_PATH 初始化）。本探针在导入前设置环境变量，符合 conftest/stage 与 M2 脚本的实际调用顺序。",
}
out = Path(__file__).with_name("audit_db_path_probe.json")
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
if report["verdict"] != "PASS":
    raise SystemExit(1)
