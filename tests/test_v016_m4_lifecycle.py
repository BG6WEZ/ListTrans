"""M4 LIFO, leakage, persistent process failures and truthful sample counters."""
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app import flywheel_sandbox, server
from app.db_manager import get_db_connection
from app.rule_governance import compute_publish_env_snapshot, get_eval_run, normalize_rule_spec, reset_rule_publish_block
from tests.test_v016_m2_publish_gate import m2_env, _seed_completed_credential


def wait_task(tid):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = flywheel_sandbox.get_eval_task_status(tid)
        if status and status["status"] in ("completed", "failed", "timed_out"):
            return status
        time.sleep(.05)
    raise TimeoutError(tid)


def test_two_snapshot_lifo_and_absent_config_restoration(m2_env):
    client, admin = m2_env["client"], m2_env["adm"]
    path = Path(os.environ["CONFIG_DIR"]) / "scoped_aliases.json"
    original = path.read_bytes() if path.exists() else None
    snapshots, suggestions = [], []
    try:
        if path.exists():
            path.unlink()
        server.trigger_all_config_reloads(raise_on_error=True)
        before = compute_publish_env_snapshot()
        for source in ("M4别名甲", "M4别名乙"):
            conn = get_db_connection()
            sid = conn.execute("INSERT INTO flywheel_rule_suggestions (discipline,suggestion_type,source_pattern,target_value,status) "
                               "VALUES ('暖通','ALIAS',?,'医用离心式排风机','pending')", (source,)).lastrowid
            conn.commit(); conn.close()
            suggestions.append(sid)
            spec = normalize_rule_spec(rule_source="flywheel_alias", rule_key=f"suggestion:{sid}", rule_type="ALIAS",
                                       discipline="暖通", source_pattern=source, target_value="医用离心式排风机", require_concrete_discipline=True)
            eid = _seed_completed_credential(rule_source="flywheel_alias", rule_key=f"suggestion:{sid}", rule_type="ALIAS",
                                             normalized_spec=spec, owner_user=admin["user"])
            res = client.post("/api/flywheel/suggestions/accept", headers=admin["headers"], json={"suggestion_id": sid, "eval_id": eid})
            assert res.status_code == 200, res.text
            snapshots.append(res.json()["snapshot_version"])
        two = path.read_bytes()
        two_fp = compute_publish_env_snapshot()
        res = client.post("/api/flywheel/rollback", headers=admin["headers"], json={"snapshot_version": snapshots[0]})
        assert res.status_code == 409, res.text
        assert path.read_bytes() == two and compute_publish_env_snapshot() == two_fp
        assert client.post("/api/flywheel/rollback", headers=admin["headers"], json={"snapshot_version": snapshots[1]}).status_code == 200
        assert "M4别名甲" in json.loads(path.read_text(encoding="utf-8"))["暖通"]
        assert "M4别名乙" not in json.loads(path.read_text(encoding="utf-8"))["暖通"]
        assert client.post("/api/flywheel/rollback", headers=admin["headers"], json={"snapshot_version": snapshots[0]}).status_code == 200
        assert not path.exists() and compute_publish_env_snapshot() == before
        conn = get_db_connection()
        assert all(row[0] == "rolled_back" for row in conn.execute(
            "SELECT status FROM flywheel_rule_suggestions WHERE id IN (?,?)", suggestions))
        conn.close()
    finally:
        if original is not None:
            path.write_bytes(original)
        elif path.exists():
            path.unlink()
        conn = get_db_connection()
        for sid in suggestions:
            conn.execute("DELETE FROM flywheel_config_snapshots WHERE suggestion_id=?", (sid,))
            conn.execute("DELETE FROM flywheel_rule_suggestions WHERE id=?", (sid,))
        conn.commit(); conn.close()
        server.trigger_all_config_reloads(raise_on_error=True)


@pytest.mark.parametrize("fault,code", [("nonzero", "eval_baseline_nonzero"), ("missing", "eval_output_missing"), ("corrupt", "eval_corrupt_json")])
def test_process_faults_persist_fail_closed(m2_env, fault, code):
    def run(cmd, **kwargs):
        if fault == "corrupt":
            Path(cmd[cmd.index("--output-json") + 1]).write_text("{bad", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 2 if fault == "nonzero" else 0, "", "controlled_failure")
    with patch.object(flywheel_sandbox, "_run_eval_process", side_effect=run):
        tid = flywheel_sandbox.start_sandbox_evaluation(
            {"suggestion_type": "ALIAS", "discipline": "暖通", "source_pattern": "排风机", "target_value": "排风机"}, owner_user=m2_env["adm"]["user"])
        state = wait_task(tid)
    assert state["status"] == "failed" and not state["safety_passed"]
    record = get_eval_run(tid)
    assert record["status"] == "failed" and record["error_message"]
    assert state["error_code"] == code == json.loads(record["result_summary_json"])["error_code"]


def test_real_timeout_kills_and_reaps_child(m2_env):
    real_run, real_popen = flywheel_sandbox._run_eval_process, subprocess.Popen
    children = []
    def popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child
    def slow(cmd, **kwargs):
        return real_run([sys.executable, "-c", "import time; time.sleep(60)"], capture_output=True, timeout=.2)
    with patch.object(flywheel_sandbox, "_run_eval_process", side_effect=slow), patch.object(subprocess, "Popen", side_effect=popen):
        tid = flywheel_sandbox.start_sandbox_evaluation(
            {"suggestion_type": "ALIAS", "discipline": "暖通", "source_pattern": "排风机", "target_value": "排风机"}, owner_user=m2_env["adm"]["user"])
        state = wait_task(tid)
    assert children and all(p.poll() is not None for p in children)
    assert state["status"] == "failed" and not state["safety_passed"]
    record = get_eval_run(tid)
    assert state["error_code"] == json.loads(record["result_summary_json"])["error_code"] == "eval_baseline_timeout"
    assert record["error_message"]


@pytest.mark.parametrize("error_scope", ["guard", "binding"])
def test_guard_errors_and_skips_reach_database(m2_env, error_scope):
    snap = compute_publish_env_snapshot()
    snapshot = {"materials_db_sha256": snap["materials_revision"], "golden_set_sha256": snap["eval_assets_digest"],
                "model_version": snap["model_version"], "golden_set_item_count": 1, "vector_channel": {}, "budget": {}}
    row = {"id": "target", "target_codes": ["A"], "rank": 1, "candidates": [{"code": "A", "score": 90}, {"code": "B", "score": 50}]}
    payload = {"rows": [row], "guard_rows": [row | {"id": "guard"}],
               "binding_rows": [], "summary": {"snapshot": snapshot, "rule_execution_hits": 1, "skipped_samples_count": 7}}
    if error_scope == "guard":
        payload["guard_rows"][0]["error"] = "controlled_guard_failure"
    else:
        payload["binding_rows"] = [{"id": "guard", "is_guard": True, "error": "controlled_binding_failure"}]
    def run(cmd, **kwargs):
        Path(cmd[cmd.index("--output-json") + 1]).write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    with patch.object(flywheel_sandbox, "_run_eval_process", side_effect=run):
        tid = flywheel_sandbox.start_sandbox_evaluation(
            {"suggestion_type": "ALIAS", "discipline": "暖通", "source_pattern": "排风机", "target_value": "排风机"}, owner_user=m2_env["adm"]["user"])
        state = wait_task(tid)
    assert state["report"]["failed_samples_count"] == 1 and state["report"]["skipped_samples_count"] == 7
    record = get_eval_run(tid)
    assert record["failed_samples_count"] == 1 and record["skipped_samples_count"] == 7
    assert not record["safety_passed"]
