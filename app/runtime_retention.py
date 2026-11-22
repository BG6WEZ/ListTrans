"""Retention for application-owned, disposable runtime artifacts."""
import logging
import os
import re
import shutil
import stat
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool


_lock = threading.RLock()
_active = {}
_upload = re.compile(r"(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|db_[0-9a-f]{32})_[^/\\]+\.xlsx?", re.I)
_export = re.compile(r"标准格式_[0-9a-f]{8}\.xlsx")
_rotated_log = re.compile(r"app_runtime\.log\.[1-9][0-9]*")
_sandbox = re.compile(r"sandbox_eval_[0-9a-f]{12}_[A-Za-z0-9_]+")


@contextmanager
def active_path(path):
    key = Path(path).resolve()
    with _lock:
        _active[key] = _active.get(key, 0) + 1
    try:
        yield key
    finally:
        with _lock:
            if _active[key] == 1:
                del _active[key]
            else:
                _active[key] -= 1


class ManagedFileResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        lease = active_path(self.path)
        await run_in_threadpool(lease.__enter__)
        try:
            await super().__call__(scope, receive, send)
        finally:
            await run_in_threadpool(lease.__exit__, None, None, None)


def sandbox_directory():
    return Path(os.getenv("OUTPUT_DIR") or (Path(__file__).resolve().parents[1] / "output")) / "runtime_sandboxes"


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _safe_tree(path):
    def fail(error):
        raise error

    return all(not _linked(Path(root) / name) for root, dirs, files in os.walk(path, onerror=fail)
               for name in (*dirs, *files))


def _overlaps(left, right):
    return left == right or left in right.parents or right in left.parents


def _remove_candidate(path, scanned, directories, protect_log_rotation):
    with ExitStack() as guards:
        if protect_log_rotation:
            live_log = path.with_name("app_runtime.log").resolve()
            for handler in logging.getLogger("listtrans_app").handlers:
                filename = getattr(handler, "baseFilename", None)
                if filename and Path(filename).resolve() == live_log:
                    handler.acquire()
                    guards.callback(handler.release)
        current = path.lstat()
        if (current.st_dev, current.st_ino, current.st_mtime_ns) != (
            scanned.st_dev, scanned.st_ino, scanned.st_mtime_ns,
        ):
            return False
        if directories:
            shutil.rmtree(path)
        else:
            path.unlink()
        return True


def clean_runtime_files(upload_dir, log_dir, *, output_dir=None, protected=(), dry_run=True, now=None):
    """Return bounded audit counts. Only allowlisted names are candidates for deletion."""
    from app import auth_manager, db_manager
    from app.vector_index import get_cache_dir

    now = time.time() if now is None else now
    ttl = max(60, int(os.getenv("LISTTRANS_FILE_TTL_SECONDS", "86400")))
    max_files = max(1, int(os.getenv("LISTTRANS_RUNTIME_FILE_LIMIT", "200")))
    project = Path(__file__).resolve().parents[1]
    configured_backup = os.getenv("BACKUP_DIR")
    protected_paths = tuple(Path(path).resolve() for path in (
        db_manager.DB_PATH, auth_manager.AUTH_DB_PATH, get_cache_dir(),
        os.getenv("CONFIG_DIR", str(project / "config")), project / "config", project / "data",
        project / "model_offline", project / "doc", project / "app" / "eval_assets",
        project / "backup", project / "backups", configured_backup, *protected,
    ) if path)
    sandboxes = Path(output_dir) / "runtime_sandboxes" if output_dir else sandbox_directory()
    spaces = (
        ("upload", Path(upload_dir), _upload, False),
        ("export", Path(upload_dir), _export, False),
        ("log", Path(log_dir), _rotated_log, False),
        ("sandbox", sandboxes, _sandbox, True),
    )
    audit = {"dry_run": dry_run, "eligible": 0, "removed": 0, "errors": 0, "kinds": {}}
    with _lock:
        for kind, root, pattern, directories in spaces:
            if not root.is_dir() or _linked(root) or any(p.exists() and _linked(p) for p in root.absolute().parents):
                continue
            candidates = []
            for path in root.iterdir():
                try:
                    if not pattern.fullmatch(path.name) or _linked(path):
                        continue
                    if not (path.is_dir() if directories else path.is_file()):
                        continue
                    absolute = path.resolve()
                    if any(_overlaps(absolute, item) for item in (*protected_paths, *_active)):
                        continue
                    if directories and (not (path / ".listtrans-sandbox").is_file() or not _safe_tree(path)):
                        continue
                    info = path.lstat()
                    candidates.append((info.st_mtime, path, info))
                except OSError:
                    audit["errors"] += 1
            counts = {"eligible": 0, "removed": 0}
            for index, (mtime, path, info) in enumerate(sorted(candidates, reverse=True)):
                if now - mtime <= ttl and (index < max_files or now - mtime <= 60):
                    continue
                counts["eligible"] += 1
                if not dry_run:
                    try:
                        removed = _remove_candidate(path, info, directories, kind == "log")
                    except OSError:
                        audit["errors"] += 1
                    else:
                        counts["removed"] += int(removed)
            audit["kinds"][kind] = counts
            for field in counts:
                audit[field] += counts[field]
    logging.getLogger("listtrans_app").info("Runtime retention summary: %s", audit)
    return audit
