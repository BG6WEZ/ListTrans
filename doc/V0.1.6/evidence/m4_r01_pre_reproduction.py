"""Reproduce old behavior from exact M4 pre-backup, on synthetic isolated state."""
import ast
import asyncio
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
BACKUP = ROOT / ".local-archive/v016_m4_r01_pre_backup"


def original_function(path, name, globals_dict):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    ns = dict(globals_dict)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), ns)
    return ns[name]


def main():
    root = Path(tempfile.mkdtemp(prefix="listtrans_m4_pre_repro_")).resolve()
    spec = importlib.util.spec_from_file_location("m4_gate_environment", EVIDENCE / "m4_r01_3rule_gate.py")
    env = importlib.util.module_from_spec(spec); spec.loader.exec_module(env)
    env._setup_env(root)
    os.environ["LISTTRANS_EVAL_KEYWORD_ONLY"] = "1"
    sys.path.insert(0, str(ROOT))
    from app import db_manager, matcher, server, vector_index
    from app.auth_manager import get_auth_connection, get_password_hash, get_user_by_username, init_auth_db, issue_token_for_user
    from app.flywheel_db import init_flywheel_tables

    db_manager.init_db(); init_auth_db(); init_flywheel_tables()
    auth = get_auth_connection()
    name = "m4_pre_probe"
    auth.execute("INSERT INTO users(username,password_hash,name,role,is_active,must_change_password,account_epoch) "
                 "VALUES (?,?,?,'admin',1,0,?)", (name, get_password_hash(secrets.token_hex(18)), name, secrets.token_hex(10)))
    auth.commit(); auth.close()
    admin = get_user_by_username(name); admin = dict(admin, token=issue_token_for_user(admin))
    config = root / "config/scoped_aliases.json"
    states = [{}, {"暖通": {"M4X": "排风机"}}, {"暖通": {"M4X": "排风机", "M4Y": "防火阀"}}]
    config.write_text(json.dumps(states[2]), encoding="utf-8")
    conn = db_manager.get_db_connection()
    for index in (1, 2):
        sid = conn.execute("INSERT INTO flywheel_rule_suggestions(discipline,suggestion_type,source_pattern,target_value,status) "
                           "VALUES ('暖通','ALIAS',?,'排风机','accepted')", (f"M4{index}",)).lastrowid
        conn.execute("INSERT INTO flywheel_config_snapshots(snapshot_version,target_type,config_before,config_after,suggestion_id,operator_id,is_active) "
                     "VALUES (?,'SCOPED_ALIAS',?,?,?,?,1)", (f"pre_snap{index}", json.dumps(states[index-1]), json.dumps(states[index]), sid, name))
    conn.commit(); conn.close()
    old_rollback = original_function(BACKUP / "app/server.py", "api_flywheel_rollback_snapshot", server.__dict__)
    response = asyncio.run(old_rollback(server.FlywheelRollbackRequest(snapshot_version="pre_snap1"), admin))
    conn = db_manager.get_db_connection()
    active = [r[0] for r in conn.execute("SELECT snapshot_version FROM flywheel_config_snapshots WHERE is_active=1")]
    conn.close()
    assert response["code"] == 0 and active == ["pre_snap2"] and json.loads(config.read_text()) == {}

    old_match = original_function(BACKUP / "app/matcher.py", "match_item", matcher.__dict__)
    query = {"name": "M4X", "specification": "", "professional": "暖通", "parsed_spec": {"model": "", "attrs": {}}}
    with patch.object(matcher, "hybrid_recall", return_value=[]):
        before_match = old_match(query)
    assert "diagnostics" not in before_match

    # Controlled trusted rows isolate only the export filter; full HTTP trust flow is tested separately.
    config.write_text(json.dumps(states[1]), encoding="utf-8")
    row = {"raw_name": "M4X", "raw_spec": "", "final_targets_json": '[{"code":"SYNTHETIC","name":"排风机"}]',
           "action_type": "CONFIRM_TOP1", "discipline": "暖通"}
    class Cursor:
        def execute(self, *args):
            pass
        def fetchall(self):
            return [row]
    @contextmanager
    def rows_cursor(*args, **kwargs):
        yield Cursor()
    old_export = original_function(BACKUP / "app/server.py", "api_flywheel_export_benchmark", server.__dict__)
    with patch.object(db_manager, "get_db_cursor", rows_cursor):
        old_result = json.loads(asyncio.run(old_export()).body)
        new_result = json.loads(asyncio.run(server.api_flywheel_export_benchmark()).body)
    assert old_result["count"] == 1 and new_result["count"] == 0
    report = {"baseline_head": "e964fb16848de9dece8e729e801b61c7d81dd341",
              "backup_sha256": {name: hashlib.sha256((BACKUP/name).read_bytes()).hexdigest() for name in ("app/matcher.py", "app/server.py")},
              "old_explicit_non_top_rollback_allowed": True, "old_higher_snapshot_left_active": active,
              "old_match_diagnostics_absent": True, "controlled_export_before_count": old_result["count"],
              "controlled_export_after_count": new_result["count"], "isolated": True}
    with (EVIDENCE / "m4_r01_pre_reproduction.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
