"""Verify V020-M1 assets and exercise byte-exact rollback in an external copy."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile

ROOT = Path(__file__).resolve().parents[3]
EV = Path(__file__).resolve().parent
EDITED = (
    "app/binding_decisions.py", "app/feedback_contract.py", "app/flywheel_sandbox.py",
    "app/rule_governance.py", "app/sandbox_evaluator.py", "app/server.py",
)
ADDED = ("app/bom.py", "tests/test_v020_m1_bom.py")
sys.path.insert(0, str(ROOT / "tests"))
from verify_isolation import collect_snapshot, diff_snapshots, sha256_file


def read(name):
    return json.loads((EV / name).read_text(encoding="utf-8-sig"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify(output, rollback):
    dispatch = read("m1_r01_dispatch_baseline.json")
    baseline = dispatch["effective_baseline_sha256"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != dispatch["head"] or len(baseline) != 194:
        raise RuntimeError("HEAD or effective baseline count changed")
    changed = {name for name, expected in baseline.items() if sha256_file(ROOT / name) != expected}
    if changed != set(EDITED):
        raise RuntimeError(f"unexpected scope change: {sorted(changed)}")
    archive = dispatch["local_recovery_archive"]
    archive_path = ROOT / archive["path"]
    old_baseline = read("m0_r01_dispatch_baseline.json")["effective_baseline_sha256"]
    if sha256_file(archive_path) != archive["sha256"]:
        raise RuntimeError("release recovery archive changed")
    with zipfile.ZipFile(archive_path) as source:
        entries = source.namelist()
        if (len(entries) != archive["entry_count"] or len(set(entries)) != len(entries)
                or any(digest(source.read(name)) != old_baseline[name] for name in entries)):
            raise RuntimeError("release recovery archive entry differs from its recorded bytes")
        original = {name: source.read(name) for name in EDITED}
    if any(digest(content) != baseline[name] for name, content in original.items()):
        raise RuntimeError("archived rollback source does not match the M1 dispatch")

    if sha256_file(EV / "m0_r01_coord_release.json") != dispatch["m0_coord_release_sha256"]:
        raise RuntimeError("M0 coordinator release digest mismatch")
    m0_manifest = read("m0_r01_delivery_sha256.json")
    m0_post = EV / "m0_r01_isolation_after.json"
    if sha256_file(m0_post) != m0_manifest["files"][m0_post.relative_to(ROOT).as_posix()]["sha256"]:
        raise RuntimeError("historical M0 post-isolation snapshot digest mismatch")
    reference = read(m0_post.name)["business_snapshot_after"]
    declared = dispatch["business_assets_sha256"]
    if declared and declared != reference:
        raise RuntimeError("M1 dispatch and M0 release asset snapshots differ")
    checkpoints = {"current": collect_snapshot()}
    for bound in ("before", "after"):
        report = read(f"m1_r01_isolation_{bound}.json")
        if report["verdict"] != "PASS" or report["exit_code"] != 0:
            raise RuntimeError(f"{bound}: isolation not verified")
        for point in ("before", "after"):
            checkpoints[f"{bound}_{point}"] = report[f"business_snapshot_{point}"]
    differences = {point: diff_snapshots(reference, actual) for point, actual in checkpoints.items()}
    if any(differences.values()):
        raise RuntimeError(f"business asset drift: {differences}")

    lines = []
    current = {name: (ROOT / name).read_bytes() for name in (*EDITED, *ADDED)}
    for name in EDITED:
        lines.extend(difflib.diff_bytes(difflib.unified_diff, current[name].splitlines(keepends=True),
                                        original[name].splitlines(keepends=True),
                                        f"a/{name}".encode(), f"b/{name}".encode()))
    for name in ADDED:
        lines.append(f"diff --git a/{name} b/{name}\ndeleted file mode 100644\n".encode())
        lines.extend(difflib.diff_bytes(difflib.unified_diff, current[name].splitlines(keepends=True),
                                        [], f"a/{name}".encode(), b"/dev/null"))
    patch = b"".join(lines)
    with TemporaryDirectory(prefix="v020_m1_rollback_") as directory:
        temp_root = Path(directory).resolve()
        if temp_root == ROOT or ROOT in temp_root.parents:
            raise RuntimeError("rollback replay must be outside the repository")
        for name, content in current.items():
            target = temp_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        temp_patch = temp_root / "rollback.patch"
        temp_patch.write_bytes(patch)
        subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", str(temp_patch)],
                       cwd=temp_root, check=True)
        restored = {name: sha256_file(temp_root / name) for name in EDITED}
        if any(value != baseline[name] for name, value in restored.items()):
            raise RuntimeError("rollback did not restore baseline bytes")
        if any((temp_root / name).exists() for name in ADDED):
            raise RuntimeError("rollback did not remove the added source and test files")
    with rollback.open("xb") as stream:
        stream.write(patch)
    with output.open("x", encoding="utf-8") as stream:
        json.dump({
            "head": head, "effective_baseline_count": len(baseline), "unchanged_count": len(baseline) - len(changed),
            "archive_entry_count": len(entries), "finished_sha256": {name: digest(data) for name, data in current.items()},
            "patch_sha256": sha256_file(rollback), "restored_sha256": restored, "new_paths_removed": list(ADDED),
            "business_asset_count": len(reference["files"]) + sum(len(x["files"]) for x in reference["trees"].values()),
            "business_differences": differences, "business_anchor": m0_post.name,
            "m1_dispatch_business_asset_count_field": dispatch["business_assets_count"],
            "m1_dispatch_business_snapshot_entry_count": len(declared), "exit_code": 0,
        }, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rollback", type=Path, required=True)
    args = parser.parse_args()
    verify(args.out, args.rollback)
