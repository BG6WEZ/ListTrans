"""Reconstruct the M2 dispatch source and verify byte-exact M3 reverse application."""

import argparse
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from m3_r01_baseline import EVIDENCE, LEDGER, ROOT, read, save, sha


CHANGED = ("app/matcher.py", "app/server.py")
ADDED = ("app/cross_encoder_reranker.py", "tests/test_v021_m3_cross_encoder.py")
OLD_LEDGER = "| R01 已下发，待开发 | 待交付 | 待审核 |".encode()
NEW_LEDGER = ("| R01 待审核 | [开发小结](rounds/V021-M3_R01_开发小结.md) | 待审核 |").encode()


def git(*args, cwd=ROOT):
    return subprocess.run(["git", "-c", "core.autocrlf=false", *args],
                          cwd=cwd, capture_output=True, check=True).stdout


def write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def original(root):
    dispatch = read("m3_r01_dispatch_baseline.json")
    for name in CHANGED:
        write(root, name, git("show", f"{dispatch['head']}:{name}"))
    for patch, names in (("m0_r01_implementation.patch", CHANGED), ("m2_r01_implementation.patch", CHANGED[1:])):
        git("apply", *(f"--include={name}" for name in names), "--whitespace=nowarn",
            str(EVIDENCE / patch), cwd=root)
    for name in CHANGED:
        if sha(root / name) != dispatch["effective_baseline_sha256"][name]:
            raise ValueError(f"Reconstructed M2 source does not match dispatch: {name}")


def rollback_certificate():
    with TemporaryDirectory(prefix="v021_m3_reverse_") as directory:
        root = Path(directory)
        before, after, check = (root / name for name in ("before", "after", "check"))
        before.mkdir()
        original(before)
        content = (ROOT / LEDGER).read_bytes()
        # This exact table fragment can occur only in the M3 row for this dispatch.
        if content.count(NEW_LEDGER) != 1:
            raise ValueError("Expected the single M3 handoff entry")
        write(before, LEDGER, content.replace(NEW_LEDGER, OLD_LEDGER))
        if sha(before / LEDGER) != read("m3_r01_baseline_before.json")["ledger_sha256"]:
            raise ValueError("Reconstructed ledger does not match the opening bytes")
        names = (*CHANGED, LEDGER, *ADDED)
        for name in names:
            write(after, name, (ROOT / name).read_bytes())
        shutil.copytree(after, check)
        diff = subprocess.run(["git", "-c", "core.autocrlf=false", "diff", "--no-index",
                               "--no-ext-diff", "--binary", "before", "after"],
                              cwd=root, capture_output=True)
        if diff.returncode != 1 or not diff.stdout:
            raise RuntimeError("No isolated rollback patch generated")
        patch = EVIDENCE / "m3_r01_implementation.patch"
        patch.write_bytes(diff.stdout)
        git("apply", "-p2", "--reverse", "--whitespace=nowarn", str(patch), cwd=check)
        restored = {name: sha(check / name) for name in (*CHANGED, LEDGER)}
        assert restored == {name: sha(before / name) for name in restored}
        assert all(not (check / name).exists() for name in ADDED)
        git("apply", "-p2", "--whitespace=nowarn", str(patch), cwd=check)
        exact_forward = {name: sha(check / name) for name in names}
        assert exact_forward == {name: sha(after / name) for name in names}
        save("m3_r01_rollback.json", {
            "exit_code": 0, "patch_sha256": sha(patch), "strip": 2,
            "reverse_restored_sha256": restored, "reverse_removed_new_files": ADDED,
            "forward_restored_sha256": exact_forward, "schema_changes": [],
        })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore-source", type=Path, help="Write archived M2 Python source in a fresh temp path")
    options = parser.parse_args()
    if options.restore_source:
        dest = options.restore_source.resolve()
        if dest == ROOT or ROOT in dest.parents:
            raise ValueError("Restore source outside the workspace only")
        dest.mkdir(parents=True, exist_ok=False)
        original(dest)
    else:
        rollback_certificate()
