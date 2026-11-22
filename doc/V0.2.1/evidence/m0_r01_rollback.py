"""Build and verify a reverse patch in an isolated native directory."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[2]
TRACKED = (".dockerignore", "Dockerfile", "app/acceptance_calibration.py",
           "app/feedback_contract.py", "app/flywheel_db.py", "app/matcher.py", "app/server.py")
ADDED = ("app/match_assessment.py", "tests/test_v021_m0_initial_match.py")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run():
    baseline = json.loads((EVIDENCE / "m0_r01_dispatch_baseline.json").read_bytes())
    head = baseline["head"]
    for path, expected in baseline["effective_baseline_sha256"].items():
        if path in TRACKED:
            old = subprocess.check_output(["git", "show", f"{head}:{path}"], cwd=ROOT)
            assert expected in {hashlib.sha256(form).hexdigest() for form in (old, old.replace(b"\n", b"\r\n"))}
        else:
            assert digest(ROOT / path) == expected, path

    patch = b""
    for path in TRACKED:
        hunk = subprocess.check_output(["git", "diff", "--binary", head, "--", path], cwd=ROOT)
        original = subprocess.check_output(["git", "show", f"{head}:{path}"], cwd=ROOT)
        if hashlib.sha256(original).hexdigest() != baseline["effective_baseline_sha256"][path]:
            header, marker, body = hunk.partition(b"@@")
            assert marker, path
            # Keep patch control lines in LF while preserving the signed worktree's CRLF content.
            hunk = header + marker + b"".join(
                line.replace(b"\n", b"\r\n") if line[:1] in (b" ", b"+", b"-") else line
                for line in body.splitlines(keepends=True)
            )
        patch += hunk
    for path in ADDED:
        result = subprocess.run(["git", "diff", "--binary", "--no-index", "--", "/dev/null", path],
                                cwd=ROOT, capture_output=True)
        assert result.returncode == 1 and result.stdout, path
        patch += result.stdout
    patch_path = EVIDENCE / "m0_r01_implementation.patch"
    patch_path.write_bytes(patch)
    with TemporaryDirectory(prefix="v021_rollback_") as directory:
        target = Path(directory)
        for path in (*TRACKED, *ADDED):
            out = target / path
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, out)
        subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--reverse",
                        "--whitespace=nowarn", str(patch_path)],
                       cwd=target, check=True)
        observed = {path: digest(target / path) for path in TRACKED}
        for path, value in observed.items():
            assert value == baseline["effective_baseline_sha256"][path], path
        assert all(not (target / path).exists() for path in ADDED)
        migration_path = str(target / "rollback.db")
        current = runpy.run_path(str(ROOT / "app/flywheel_db.py"))
        legacy = runpy.run_path(str(target / "app/flywheel_db.py"))
        current["init_flywheel_tables"](db_path=migration_path)
        sql = """
            INSERT INTO flywheel_recommendation_snapshots
            (snapshot_id, document_id, sheet_id, source_row_id, revision, professional, parsed_spec_json,
             initial_bindings_json, algo_version, model_version, config_version, config_digest_full)
            VALUES (?, 'doc', 'sheet', ?, 1, '暖通', '{}', '[]', '', '', '', '')
        """
        with closing(sqlite3.connect(migration_path)) as conn, conn:
            conn.execute(sql, ("new", "row-new"))
            conn.execute("UPDATE flywheel_recommendation_snapshots SET initial_match_json = '{}'")
        legacy["init_flywheel_tables"](db_path=migration_path)
        with closing(sqlite3.connect(migration_path)) as conn, conn:
            conn.execute(sql, ("old", "row-old"))
            assert conn.execute(
                "SELECT snapshot_id, initial_match_json FROM flywheel_recommendation_snapshots ORDER BY snapshot_id"
            ).fetchall() == [("new", "{}"), ("old", None)]
    (EVIDENCE / "m0_r01_rollback.json").write_text(json.dumps({
        "head": head, "baseline_entries_verified": len(baseline["effective_baseline_sha256"]),
        "implementation_patch_sha256": digest(patch_path),
        "restored_baseline_sha256": observed, "added_files_removed_from_temporary_copy": ADDED,
        "old_schema_initializer_and_legacy_insert_preserve_v021_snapshot": True,
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
