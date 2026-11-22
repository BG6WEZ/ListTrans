"""Verify the frozen M1 surface, native isolation, and byte-exact source rollback."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
from verify_isolation import collect_snapshot, diff_snapshots, sha256_file

CHANGED = ("app/matcher.py", "app/server.py", "app/sandbox_evaluator.py", "app/flywheel_sandbox.py")


def load(name):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8-sig"))


def run(output):
    dispatch = load("m0_r01_dispatch_baseline.json")
    release_m0 = load("m0_r01_coord_release.json")
    release_m1 = load("m1_r01_coord_release.json")
    expected = {**dispatch["effective_baseline_sha256"], **release_m0["m1_effective_source_baseline_sha256"],
                **release_m1["m2_effective_source_updates_sha256"]}
    changed = {path: {"m1_sha256": sha, "m2_sha256": digest}
               for path, sha in expected.items() if (digest := sha256_file(ROOT / path)) != sha}
    patch = EVIDENCE / "m2_r01_rollback.patch"
    rollback = {}
    with TemporaryDirectory(prefix="v019_m2_rollback_") as tmp:
        staged = Path(tmp).resolve()
        if staged == ROOT or ROOT in staged.parents:
            raise RuntimeError("Rollback requires a native temporary directory")
        for file in CHANGED:
            target = staged / file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / file).read_bytes())
        command = ["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn"]
        subprocess.run([*command, "--check", str(patch)], cwd=staged, check=True)
        subprocess.run([*command, str(patch)], cwd=staged, check=True)
        for file in CHANGED:
            target = staged / file
            compile(target.read_bytes(), file, "exec")
            rollback[file] = {"restored_sha256": sha256_file(target), "m1_sha256": expected[file]}

    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    start = load("m2_r01_isolation_before.json")
    end = load("m2_r01_isolation_after.json")
    snapshots = (load("m1_r01_isolation_after.json")["business_snapshot_after"],
                 start["business_snapshot_before"], start["business_snapshot_after"],
                 end["business_snapshot_before"], end["business_snapshot_after"], collect_snapshot())
    changes = [diff_snapshots(snapshots[0], sample) for sample in snapshots[1:]]
    n = len(snapshots[0]["files"]) + sum(len(tree["files"]) for tree in snapshots[0]["trees"].values())
    archive = dispatch["source_rollback_archive"]
    archive_intact = sha256_file(ROOT / archive["path"]) == archive["sha256"]
    models = sorted(p.name for p in (ROOT / "model_offline").iterdir() if p.is_dir())
    expected_models = sorted(release_m0["frozen_assets_check"]["offline_models_inventory"])
    valid = (head == release_m1["head"] and len(expected) == release_m1["m2_effective_baseline_count"] == 178
             and set(changed) == set(CHANGED) and n == 35 and not any(changes)
             and start["exit_code"] == end["exit_code"] == 0 and archive_intact and models == expected_models
             and all(r["restored_sha256"] == r["m1_sha256"] for r in rollback.values()))
    result = {
        "head": head, "effective_baseline_files": len(expected), "baseline_changes": changed,
        "source_rollback": rollback, "source_rollback_patch_sha256": sha256_file(patch),
        "business_files_checked": n, "business_diffs_vs_m1": changes, "prior_archive_intact": archive_intact,
        "offline_models": models, "exit_code": 0 if valid else 1,
    }
    with output.open("x", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
    return result["exit_code"]


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    sys.exit(run(p.parse_args().out))
