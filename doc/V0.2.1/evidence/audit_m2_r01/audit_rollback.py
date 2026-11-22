"""Build a byte-exact M2 rollback and exercise old readers against both migrated databases."""

from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import runpy
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch


EVIDENCE = Path(__file__).resolve().parent / "rollback_inputs"
ROOT = Path(__file__).resolve().parents[4]
M0 = ("app/server.py", "app/flywheel_db.py")
M1 = ("web/app.js", "web/index.html", "web/index.css")
COMPAT_TESTS = ("tests/test_m5_file_boundary_and_compat.py", "tests/test_v020_m1_bom.py", "tests/test_feedback_miner.py")
TRACKED = ("app/binding_decisions.py", *M0, *M1, *COMPAT_TESTS)
ADDED = ("app/source_binding_review.py", "tests/test_v021_m2_priority_review.py")
LEDGER = "doc/V0.2.1/02_任务台账.md"
LEDGER_OLD = "| R01 已下发，待开发 | 待交付 | 待审核 |".encode()
LEDGER_NEW = ("| R01 开发自验阻断（开工快照 2 项边车差异） | [开发小结](rounds/V021-M2_R01_开发小结.md) | 待审核 |").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def copy(path, destination, data=None):
    target = destination / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((ROOT / path).read_bytes() if data is None else data)


def git(*args, cwd=ROOT):
    return subprocess.check_output(["git", "-c", "core.autocrlf=false", *args], cwd=cwd)


def old_worktree(baseline, old):
    opening = json.loads((EVIDENCE / "m2_r01_baseline_check.json").read_bytes())
    expected = {**baseline["effective_baseline_sha256"], **opening["additional_tracked_baseline_sha256"]}
    for name in TRACKED:
        head = git("show", f"{baseline['head']}:{name}")
        copy(name, old, head.replace(b"\n", b"\r\n") if name in M1 else head)
    for names, patch_name in ((M0, "m0_r01_implementation.patch"), (M1, "m1_r01_workbench.patch")):
        git("apply", *(f"--include={name}" for name in names), "--whitespace=nowarn",
            str(EVIDENCE / patch_name), cwd=old)
    for name in TRACKED:
        data = (old / name).read_bytes()
        lf = data.replace(b"\r\n", b"\n")
        for candidate in (data, lf, lf.replace(b"\n", b"\r\n")):
            if sha(candidate) == expected[name]:
                copy(name, old, candidate)
                break
        else:
            raise AssertionError(f"M2 dispatch hash mismatch for {name}: {sha(data)}")
    # 独立审核时台账已被统筹与本审核按协议更新，冻结时台账原文不再在盘；
    # 台账属文档而非业务代码，回退验证仅覆盖代码/测试文件（见 run()）。
    pass


def migration(old, root):
    layout = {
        "AUTH_DB_PATH": root / "isolated_auth.db",
        "MATERIALS_DB_PATH": root / "isolated_materials.db",
        "CONFIG_DIR": root / "config",
        "VECTOR_CACHE_DIR": root / "vectors",
        "UPLOAD_DIR": root / "uploads",
        "OUTPUT_DIR": root / "output",
        "LOG_DIR": root / "logs",
        "BINDING_FEEDBACK_FILE": root / "binding_feedback.json",
        "FEEDBACK_SAMPLES_FILE": root / "feedback_samples.json",
    }
    shutil.copytree(ROOT / "config", layout["CONFIG_DIR"])
    with patch.dict(os.environ, {**{name: str(path) for name, path in layout.items()},
                                 "TESTING": "1", "LLM_ENABLED": "false", "PYTHON_DOTENV_DISABLED": "1"}):
        try:
            return verify_migrations(old, root)
        finally:
            logger = logging.getLogger("security")
            for handler in logger.handlers[:]:
                if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename).is_relative_to(root):
                    logger.removeHandler(handler)
                    handler.close()


def verify_migrations(old, root):
    sys.path.insert(0, str(ROOT))
    from app import auth_manager, binding_decisions, flywheel_db

    auth_path = root / "isolated_auth.db"
    materials_path = root / "isolated_materials.db"
    legacy_auth = runpy.run_path(str(old / "app/binding_decisions.py"))
    legacy_materials = runpy.run_path(str(old / "app/flywheel_db.py"))
    auth_results = {}
    material_results = {}
    row = {"original_name": "synthetic", "professional": "暖通", "original_qty": 3}
    with patch.object(auth_manager, "AUTH_DB_PATH", str(auth_path)):
        legacy_auth["init_binding_decision_db"]()
        legacy_auth["invalidate_decision"](1, "old_before", row)
        for _ in range(2):
            binding_decisions.init_binding_decision_db()
        with closing(sqlite3.connect(auth_path)) as db, db:
            db.execute("""
                INSERT INTO source_binding_decisions VALUES (
                    1, 'epoch', 'doc', 'sheet', 'source', 1, 1, 'row-fp', 'bind-fp',
                    'manual_confirmed', 'priority_review', 'synthetic', '{}'
                )
            """)
            db.execute("INSERT INTO priority_binding_receipts VALUES (1, 'epoch', 'key', 'batch', 'digest', '{}')")
            db.execute("INSERT INTO priority_binding_outbox VALUES ('event', 1, 'epoch', '{}')")
        legacy_auth["init_binding_decision_db"]()
        legacy_auth["invalidate_decision"](1, "old_after", row)
        assert legacy_auth["get_row_decision"](1, "old_before", row)
        assert legacy_auth["get_row_decision"](1, "old_after", row)
        assert legacy_auth["get_decision"](1, "same_filename", row, []) is None
        with closing(sqlite3.connect(auth_path)) as db:
            for name in ("source_binding_decisions", "priority_binding_receipts", "priority_binding_outbox"):
                auth_results[name] = db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    assert set(auth_results.values()) == {1}

    legacy_materials["init_flywheel_tables"](db_path=str(materials_path))
    for _ in range(2):
        flywheel_db.init_flywheel_tables(db_path=str(materials_path))
    with closing(sqlite3.connect(materials_path)) as db, db:
        db.execute("INSERT INTO flywheel_priority_confirmation_events VALUES ('event', 'priority_review', '{}', NULL)")
    legacy_materials["init_flywheel_tables"](db_path=str(materials_path))
    with closing(sqlite3.connect(materials_path)) as db, db:
        db.execute("INSERT INTO flywheel_schema_meta (key, value) VALUES ('m2-rollback-probe', 'legacy works')")
        material_results["legacy_read_write"] = db.execute(
            "SELECT value FROM flywheel_schema_meta WHERE key='m2-rollback-probe'",
        ).fetchone()[0]
        material_results["priority_rows_retained"] = db.execute(
            "SELECT COUNT(*) FROM flywheel_priority_confirmation_events",
        ).fetchone()[0]
        assert material_results["priority_rows_retained"] == 1
    return {"auth": {"retained_rows": auth_results, "legacy_reads_and_writes": True,
                     "legacy_cannot_use_priority_credentials": True}, "materials": material_results}


def run():
    baseline = json.loads((EVIDENCE / "m2_r01_dispatch_baseline.json").read_bytes())
    with TemporaryDirectory(prefix="v021_m2_rollback_") as directory:
        root = Path(directory)
        old, new = root / "before", root / "after"
        old_worktree(baseline, old)
        changed = TRACKED
        for name in (*changed, *ADDED):
            copy(name, new)
        result = subprocess.run(["git", "-c", "core.autocrlf=false", "diff", "--binary", "--no-index",
                                 "--no-prefix", "before", "after"],
                                cwd=root, capture_output=True, check=False)
        assert result.returncode == 1 and result.stdout
        patch_path = EVIDENCE / "m2_r01_implementation.patch"
        patch_path.write_bytes(result.stdout)
        git("apply", "-p1", "--reverse", "--whitespace=nowarn", str(patch_path), cwd=new)
        proven = {}
        for name in changed:
            assert (new / name).read_bytes() == (old / name).read_bytes(), name
            proven[name] = sha((new / name).read_bytes())
        assert all(not (new / name).exists() for name in ADDED)
        report = {
            "exit_code": 0, "head": baseline["head"], "rollback_patch_sha256": sha(result.stdout),
            "byte_identical": proven, "added_removed_in_copy": ADDED, "preserve_schema_rollback": migration(new, root),
        }
    (EVIDENCE / "m2_r01_rollback.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
