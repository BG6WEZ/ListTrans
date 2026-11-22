"""Bounded admission and cooperative cancellation for single-worker runtime jobs."""
import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from functools import wraps
from collections import deque


class JobRejected(RuntimeError):
    pass


class JobStopped(RuntimeError):
    pass


_current = ContextVar("runtime_job", default=None)
_terminal_states = frozenset({"completed", "failed", "timed_out", "cancelled"})


def prune_terminal_tasks(tasks, max_tasks, ttl_seconds, now=None):
    now = time.time() if now is None else now
    finished = sorted(
        (task.get("finished_at", task.get("created_time", now)), key)
        for key, task in tasks.items() if task.get("status") in _terminal_states
    )
    for finished_at, key in finished:
        if now - finished_at > ttl_seconds or len(tasks) > max_tasks:
            tasks.pop(key, None)


class Job:
    def __init__(self, pool, timeout, job_id):
        self.pool = pool
        self.job_id = job_id
        self.admitted_at = time.monotonic()
        self.deadline = self.admitted_at + timeout
        self.cancelled = threading.Event()
        self.timer = None
        self.released = False

    def checkpoint(self):
        if time.monotonic() >= self.deadline:
            self.cancelled.set()
            raise JobStopped("任务处理超时")
        if self.cancelled.is_set():
            raise JobStopped("任务已取消")

    def release(self):
        with self.pool.lock:
            if not self.released:
                self.released = True
                if self.timer is not None:
                    self.timer.cancel()
                self.pool.admitted -= 1
                if self.job_id is not None:
                    self.pool.active.pop(self.job_id, None)

    def submit(self, func, *args, **kwargs):
        def work():
            wait_ms = (time.monotonic() - queued_at) * 1000
            marker = _current.set(self)
            with self.pool.lock:
                self.pool.started += 1
                self.pool.wait_total_ms += wait_ms
                self.pool.wait_max_ms = max(self.pool.wait_max_ms, wait_ms)
                self.pool.wait_samples_ms.append(wait_ms)
            try:
                return func(*args, **kwargs)
            finally:
                _current.reset(marker)

        queued_at = time.monotonic()
        if self.timer is None:
            self.timer = threading.Timer(max(0, self.deadline - queued_at), self.cancelled.set)
            self.timer.daemon = True
            self.timer.start()
        return self.pool.executor.submit(work)

    async def wait(self, func, *args, **kwargs):
        future = self.submit(func, *args, **kwargs)
        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            self.cancelled.set()
            # File and reservation owners may clean up only after the thread exits.
            try:
                await asyncio.shield(wrapped)
            except Exception:
                pass
            raise


class JobPool:
    def __init__(self, name, workers, queued):
        self.capacity = workers + queued
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"listtrans-{name}")
        self.lock = threading.Lock()
        self.admitted = self.rejected = self.started = 0
        self.wait_total_ms = self.wait_max_ms = 0.0
        self.wait_samples_ms = deque(maxlen=1024)
        self.active = {}

    def reserve(self, timeout, job_id=None):
        with self.lock:
            if self.admitted >= self.capacity:
                self.rejected += 1
                raise JobRejected("任务队列已满，请稍后重试")
            job = Job(self, timeout, job_id)
            self.admitted += 1
            if job_id is not None:
                self.active[job_id] = job
            return job

    def cancel(self, job_id):
        with self.lock:
            job = self.active.get(job_id)
            if job is None:
                return False
            job.cancelled.set()
            return True

    def stats(self):
        with self.lock:
            return {
                "capacity": self.capacity, "admitted": self.admitted, "rejected": self.rejected,
                "started": self.started, "wait_total_ms": self.wait_total_ms, "wait_max_ms": self.wait_max_ms,
                "wait_samples_ms": list(self.wait_samples_ms),
            }

    async def run(self, func, *args, timeout=120, **kwargs):
        job = self.reserve(timeout)
        try:
            def checked():
                job.checkpoint()
                return func(*args, **kwargs)
            future = job.submit(checked)
        except BaseException:
            job.release()
            raise
        future.add_done_callback(lambda _: job.release())
        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout)
        except TimeoutError:
            job.cancelled.set()
            raise JobStopped("任务处理超时") from None
        except asyncio.CancelledError:
            job.cancelled.set()
            raise
        finally:
            # Consume a late error after an HTTP timeout without changing the worker outcome.
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())


def current_job():
    return _current.get()


def checkpoint():
    job = current_job()
    if job is not None:
        job.checkpoint()


def http_job(pool, timeout=120):
    """Run a synchronous FastAPI route in its bounded pool, preserving its signature."""
    def decorate(func):
        @wraps(func)
        async def route(*args, **kwargs):
            from fastapi import HTTPException

            try:
                return await pool.run(func, *args, timeout=timeout, **kwargs)
            except JobRejected as exc:
                raise HTTPException(429, str(exc), headers={"Retry-After": "1"}) from exc
            except JobStopped as exc:
                raise HTTPException(504, str(exc)) from exc
        return route
    return decorate


def terminal_ttl():
    return max(1, int(os.getenv("LISTTRANS_TASK_TTL_SECONDS", "1800")))


match_jobs = JobPool("match", 4, 8)
upload_jobs = JobPool("upload", 2, 4)
catalog_jobs = JobPool("catalog", 1, 0)
export_jobs = JobPool("export", 2, 4)
rule_jobs = JobPool("rule", 2, 4)
mining_jobs = JobPool("mining", 1, 1)
eval_jobs = JobPool("eval", 1, 3)
supplement_jobs = JobPool("supplement", 1, 2)
