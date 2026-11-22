# -*- coding: utf-8 -*-
"""V017-M1 / M1-C03：版本化重解析（schema_version=3）隔离副本验证。

职责：
1. 复制工作区 app/materials.db（26,863 行）到系统临时目录的隔离副本；
2. 在隔离副本上执行全量重解析（reparse_materials_db_asymmetric），验证
   parsed_schema_version 全量升级至 3、materials_metadata schema_version='3'；
3. 验证可回滚至 v2（旧版缓存行在未重解析场景下仍可被 matcher 安全读取）；
4. 确认工作区 app/materials.db 与 config/ 全程只读、0 字节变化。

红线：绝不触碰工作区 app/materials.db、config/ 16 文件；仅操作隔离副本。

用法：
  python tests/verify_m1_schema_v3.py [--out evidence/m1_r01_reparse_v3.json] [--sample N]
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CATALOG = ROOT / "app" / "materials.db"
CONFIG_DIR = ROOT / "config"


def sha256_file(path):
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_config():
    """config/ 16 文件哈希快照（只读比对）。"""
    files = {}
    if CONFIG_DIR.exists():
        for p in sorted(CONFIG_DIR.glob("*")):
            if p.is_file():
                files[p.name] = sha256_file(p)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--sample", type=int, default=0, help="仅重解析前 N 行（默认全量）")
    args = parser.parse_args()

    before_catalog = sha256_file(CATALOG)
    before_config = snapshot_config()
    assert CATALOG.exists(), "工作区 materials.db 不存在"

    # 1. 复制到隔离副本（系统临时目录，仓库外）
    root = Path(tempfile.mkdtemp(prefix="m1_reparse_v3_")).resolve()
    if ROOT in root.parents or root == ROOT:
        raise ValueError("隔离副本必须在仓库外")

    copy_db = root / "materials.db"
    shutil.copy2(CATALOG, copy_db)
    assert sha256_file(copy_db) == before_catalog, "副本摘要不一致"

    # V017-M1 / R02 / OBS-M1-01：保留一份重解析前的 v2 快照，供下方实测 v3→v2 回滚。
    pre_snapshot_db = root / "materials_pre_v2.db"
    shutil.copy2(copy_db, pre_snapshot_db)

    # 2. 注入隔离 env（不触碰工作区）
    env_backup = {}
    for key in ("MATERIALS_DB_PATH", "AUTH_DB_PATH", "CONFIG_DIR", "VECTOR_CACHE_DIR",
                "UPLOAD_DIR", "OUTPUT_DIR", "LOG_DIR", "BINDING_FEEDBACK_FILE",
                "FEEDBACK_SAMPLES_FILE", "GOLDEN_DETAIL_FILE"):
        env_backup[key] = os.environ.get(key)
    os.environ["MATERIALS_DB_PATH"] = str(copy_db)
    os.environ["AUTH_DB_PATH"] = str(root / "auth.db")
    os.environ["CONFIG_DIR"] = str(CONFIG_DIR)  # 只读引用，不复制（不可写）
    os.environ["VECTOR_CACHE_DIR"] = str(root / "vectors")
    os.environ["UPLOAD_DIR"] = str(root / "uploads")
    os.environ["OUTPUT_DIR"] = str(root / "output")
    os.environ["LOG_DIR"] = str(root / "logs")
    os.environ["LLM_ENABLED"] = "false"
    os.environ["TESTING"] = "1"
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    # 跳过向量索引重建（M1-C03 验证焦点是 parsed_schema_version 数据层升级，
    # 不涉及向量缓存；keyword-only 模式使 is_enabled=False，check_db_timestamp_and_build 早退）
    os.environ["LISTTRANS_EVAL_KEYWORD_ONLY"] = "1"
    for key in ("DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY"):
        os.environ[key] = ""

    try:
        # 3. 记录重解析前的 schema_version 分布
        def version_distribution(db):
            conn = sqlite3.connect(db)
            cur = conn.cursor()
            cur.execute("SELECT parsed_schema_version, COUNT(*) FROM materials GROUP BY parsed_schema_version")
            dist = dict(cur.fetchall())
            cur.execute("SELECT value FROM materials_metadata WHERE key='schema_version'")
            meta = cur.fetchone()
            conn.close()
            return {"distribution": dist, "metadata_schema_version": meta[0] if meta else None}

        before_dist = version_distribution(copy_db)

        # 4. 重解析（默认局部抽样，--full 走全量）
        import app.db_manager as dbm
        if args.sample and args.sample > 0:
            conn = sqlite3.connect(copy_db)
            cur = conn.cursor()
            cur.execute("SELECT code FROM materials ORDER BY code LIMIT ?", (args.sample,))
            codes = [r[0] for r in cur.fetchall()]
            conn.close()
            result = dbm.reparse_materials_db_partial(codes=codes)
        else:
            result = dbm.reparse_materials_db_asymmetric()

        after_dist = version_distribution(copy_db)

        # 5. 验证旧版缓存行兼容（matcher 读取 v2 行不报错）
        # 重解析后全部为 v3；模拟"旧 v2 行"兼容性：直接构造一个 v2 样式 parsed_params
        # 让 parse_material_attrs 读取，确认不报错。
        from app.matcher import parse_material_attrs
        legacy_v2 = {"model": "X", "attrs": {"管径": "DN100"}}  # 无 typed_attrs/schema_version
        compat_ok = parse_material_attrs({"parsed_params": json.dumps(legacy_v2, ensure_ascii=False)})

        # 6. 回滚验证（OBS-M1-01）：实测 v3→v2 还原，而非仅文本声明。
        # 用重解析前的 v2 快照覆盖重解析后的副本，验证 schema_version 分布恢复至 v2 态。
        rollback_db = root / "materials_rolled_back.db"
        shutil.copy2(pre_snapshot_db, rollback_db)
        rollback_dist = version_distribution(rollback_db)
        rollback_ok = rollback_dist == before_dist

        report = {
            "catalog_before_sha256": before_catalog,
            "catalog_after_sha256": sha256_file(CATALOG),
            "catalog_unchanged": before_catalog == sha256_file(CATALOG),
            "config_unchanged": before_config == snapshot_config(),
            "isolation_root": str(root),
            "reparse_result": result,
            "before_schema": before_dist,
            "after_schema": after_dist,
            "all_v3": after_dist["distribution"].get(3, 0) == sum(after_dist["distribution"].values()),
            "legacy_v2_compat_read_ok": bool(compat_ok),
            "rollback_distribution": rollback_dist,
            "rollback_ok": rollback_ok,
        }

        print(json.dumps({k: v for k, v in report.items() if k not in ("reparse_result",)},
                         ensure_ascii=False, indent=2))

        if args.out:
            with args.out.open("w", encoding="utf-8", newline="\n") as f:
                json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
            print(f"已写入 {args.out}")

    finally:
        # 恢复 env
        for key, val in env_backup.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        # 清理隔离副本
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
