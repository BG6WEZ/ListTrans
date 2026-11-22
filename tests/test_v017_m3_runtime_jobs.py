import asyncio
import os
import subprocess
import sys
import threading
import time

import pytest

from app import flywheel_sandbox
from app.runtime_jobs import JobPool, JobRejected, JobStopped, checkpoint, prune_terminal_tasks
from app.runtime_retention import _remove_candidate, active_path, clean_runtime_files


def test_capacity_and_expired_queue_do_not_run_late():
    pool = JobPool("test", 1, 1)
    running = threading.Event()
    release = threading.Event()
    executed = []

    def busy():
        running.set()
        assert release.wait(5)

    async def scenario():
        first = asyncio.create_task(pool.run(busy, timeout=5))
        assert await asyncio.to_thread(running.wait, 2)
        second = asyncio.create_task(pool.run(executed.append, "late", timeout=0.05))
        await asyncio.sleep(0)
        with pytest.raises(JobRejected):
            await pool.run(executed.append, "rejected")
        with pytest.raises(JobStopped, match="超时"):
            await second
        assert pool.stats()["admitted"] == 2
        release.set()
        await first
        assert await pool.run(lambda: True)

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        pool.executor.shutdown()
    assert not executed and pool.stats()["admitted"] == 0


def test_cancel_stops_worker_and_releases_slot():
    pool = JobPool("cancel", 1, 0)
    job = pool.reserve(5, "owned")
    running = threading.Event()

    def cancellable():
        try:
            running.set()
            assert job.cancelled.wait(2)
            checkpoint()
        finally:
            job.release()

    try:
        result = job.submit(cancellable)
        assert running.wait(2)
        assert not pool.cancel("foreign")
        assert pool.cancel("owned")
        with pytest.raises(JobStopped, match="取消"):
            result.result(timeout=3)
        assert not pool.cancel("owned")
        replacement = pool.reserve(2)
        replacement.release()
    finally:
        pool.executor.shutdown()


def test_child_is_killed_and_reaped_on_cancellation(monkeypatch):
    real_popen = subprocess.Popen
    launched = threading.Event()
    children = []

    def spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        launched.set()
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    pool = JobPool("child", 1, 0)
    job = pool.reserve(5, "child")
    try:
        future = job.submit(flywheel_sandbox._run_eval_process,
                            [sys.executable, "-c", "import time; time.sleep(30)"],
                            timeout=4, capture_output=True, text=True)
        assert launched.wait(3)
        assert pool.cancel("child")
        with pytest.raises(JobStopped):
            future.result(timeout=3)
        assert all(child.poll() is not None for child in children)
    finally:
        job.release()
        pool.executor.shutdown()


def test_only_terminal_states_are_pruned_by_age_and_capacity():
    tasks = {
        "old": {"status": "completed", "finished_at": 1},
        "still-running": {"status": "processing", "created_time": 1},
        "queued": {"status": "pending", "created_time": 1},
        "recent": {"status": "failed", "finished_at": 8},
        "latest": {"status": "completed", "finished_at": 9},
    }
    prune_terminal_tasks(tasks, max_tasks=3, ttl_seconds=4, now=10)
    assert set(tasks) == {"still-running", "queued", "latest"}


def test_retention_dry_run_active_and_protected(tmp_path, monkeypatch):
    monkeypatch.setenv("LISTTRANS_RUNTIME_FILE_LIMIT", "1")
    monkeypatch.setenv("LISTTRANS_FILE_TTL_SECONDS", "100")
    upload = tmp_path / "upload"
    logs = tmp_path / "logs"
    sandbox = tmp_path / "output" / "runtime_sandboxes" / "sandbox_eval_000000000001_old"
    untouched_sandbox = sandbox.with_name("sandbox_eval_000000000002_unknown")
    for directory in (upload, logs, sandbox, untouched_sandbox):
        directory.mkdir(parents=True)
    now = time.time()

    def put(path, age=1000):
        path.write_text("synthetic", encoding="utf8")
        os.utime(path, (now - age, now - age))
        return path

    expired = put(upload / "db_00000000000000000000000000000001_old.xlsx")
    active = put(upload / "00000000-0000-0000-0000-000000000001_active.xlsx")
    protected = put(upload / "标准格式_11111111.xlsx")
    unknown = put(upload / "backup.xlsx")
    exported = put(upload / "标准格式_22222222.xlsx", age=80)
    recent = put(upload / "标准格式_33333333.xlsx", age=70)
    rotated = put(logs / "app_runtime.log.1")
    current_log = put(logs / "app_runtime.log")
    put(sandbox / ".listtrans-sandbox")
    os.utime(sandbox, (now - 1000, now - 1000))
    os.utime(untouched_sandbox, (now - 1000, now - 1000))
    args = {"output_dir": tmp_path / "output", "protected": (protected,), "now": now}
    with active_path(active):
        dry = clean_runtime_files(upload, logs, **args)
        assert dry["eligible"] == 4 and dry["removed"] == 0
        assert all(path.exists() for path in (expired, exported, rotated, sandbox))
        actual = clean_runtime_files(upload, logs, dry_run=False, **args)
    assert actual["removed"] == 4 and actual["errors"] == 0
    assert all(path.exists() for path in (active, protected, unknown, recent, current_log, untouched_sandbox))

    monkeypatch.setenv("BACKUP_DIR", str(upload))
    assert clean_runtime_files(upload, logs, dry_run=False, now=now + 200000)["removed"] == 0
    assert all(path.exists() for path in (active, protected, unknown, recent))


def test_retention_refuses_link_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "business.xlsx"
    target.write_bytes(b"keep")
    (outside / ".listtrans-sandbox").touch()
    output = tmp_path / "output"
    sandboxes = output / "runtime_sandboxes"
    sandboxes.mkdir(parents=True)
    link = sandboxes / "sandbox_eval_000000000001_link"
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    audit = clean_runtime_files(tmp_path / "upload", tmp_path / "logs", output_dir=output,
                                dry_run=False, now=time.time() + 200000)
    assert audit["removed"] == 0 and target.read_bytes() == b"keep" and link.exists()


def test_retention_preserves_newly_rotated_log(tmp_path):
    target = tmp_path / "app_runtime.log.1"
    target.write_bytes(b"old generation")
    scanned = target.lstat()
    replacement = tmp_path / "app_runtime.log"
    replacement.write_bytes(b"new generation")
    os.replace(replacement, target)
    assert not _remove_candidate(target, scanned, directories=False, protect_log_rotation=True)
    assert target.read_bytes() == b"new generation"
