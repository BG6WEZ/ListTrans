"""Verify M2 gates and reproduce byte-exact rollback outside the workspace."""
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
EVIDENCE = Path(__file__).resolve().parent
EDITED = "app/server.py"
ADDED = ("app/llm_decision.py", "tests/test_v020_m2_llm_decision.py")
sys.path.insert(0, str(ROOT / "tests"))
from verify_isolation import collect_snapshot, diff_snapshots, sha256_file


def read(name):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8-sig"))


def digest(content):
    return hashlib.sha256(content).hexdigest()


def apply_in(directory, patch, *flags):
    subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", *flags, str(patch)],
                   cwd=directory, check=True)


def archive_source(dispatch):
    archive = dispatch["local_recovery_archive"]
    archive_path = ROOT / archive["path"]
    old_baseline = read("m0_r01_dispatch_baseline.json")["effective_baseline_sha256"]
    if sha256_file(archive_path) != archive["sha256"]:
        raise RuntimeError("local recovery archive changed")
    with zipfile.ZipFile(archive_path) as source:
        entries = source.namelist()
        if (len(entries) != archive["entry_count"] or len(set(entries)) != len(entries)
                or any(digest(source.read(name)) != old_baseline[name] for name in entries)):
            raise RuntimeError("archive entry differs from the recorded pre-M0 bytes")
        return source.read(EDITED)


def verify_rules():
    earlier = read("m1_r01_rule_gate.json")
    current = read("m2_r01_rule_gate.json")
    rule_checks = current["historical_score_delta_counts"]
    differences = [value for group in rule_checks.values() for value in group.values()]
    protected = current["protected_probes"]
    constrained = current["rules"]["beneficial_constrained"]
    if (current["arm"] != "off" or current["exit_code"] or current["subset_rows"] != 1060
            or len(differences) != 18 or any(differences)
            or rule_checks != earlier["historical_score_delta_counts"]
            or not all(current["rule_safety_unchanged"].values())
            or not current["passed_protected_probes"] or protected != earlier["protected_probes"]
            or set(protected) != {"GT-0674", "GT-0677", "GT-0678"}
            or constrained["binding_checks_count"] != 40 or constrained["binding_degraded_count"]
            or not constrained["safety_passed"] or not constrained["guard_passed"]):
        raise RuntimeError("default-off regression gate differs from the approved reference")
    return {"score_groups": len(differences), "score_differences": sum(differences),
            "binding_checks": constrained["binding_checks_count"], "binding_degradations": 0,
            "unchanged_protected_probes": sorted(protected), "slice": "Train/Dev", "rows": current["subset_rows"]}


def verify_assets(dispatch):
    reference = dispatch["business_snapshot_sha256"]
    asset_count = len(reference["files"]) + sum(len(tree["files"]) for tree in reference["trees"].values())
    if asset_count != 35:
        raise RuntimeError("incomplete business asset reference")
    checkpoints = {"current": collect_snapshot()}
    for bound in ("before", "after"):
        report = read(f"m2_r01_isolation_{bound}.json")
        if report["verdict"] != "PASS" or report["exit_code"]:
            raise RuntimeError(f"{bound}: isolation not verified")
        for point in ("before", "after"):
            checkpoints[f"{bound}_{point}"] = report[f"business_snapshot_{point}"]
    differences = {point: diff_snapshots(reference, snapshot) for point, snapshot in checkpoints.items()}
    if any(differences.values()):
        raise RuntimeError(f"business asset drift: {differences}")
    if json.loads((ROOT / "config/auto_binding_rules.json").read_bytes()) != []:
        raise RuntimeError("binding-rule file changed")
    return asset_count, differences


def verify(output, rollback):
    dispatch = read("m2_r01_dispatch_baseline.json")
    baseline = dispatch["effective_baseline_sha256"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != dispatch["head"] or len(baseline) != 196:
        raise RuntimeError("HEAD or effective baseline count changed")
    changed = {name for name, expected in baseline.items() if sha256_file(ROOT / name) != expected}
    if changed != {EDITED} or any(name in baseline for name in ADDED):
        raise RuntimeError(f"unexpected effective-baseline scope change: {sorted(changed)}")
    m1_patch = EVIDENCE / "m1_r01_rollback.patch"
    m1_manifest = read("m1_r01_delivery_sha256.json")["files"]
    m1_release = EVIDENCE / "m1_r01_coord_release.json"
    if (sha256_file(m1_patch) != m1_manifest[m1_patch.relative_to(ROOT).as_posix()]["sha256"]
            or sha256_file(m1_release) != dispatch["m1_coord_release_sha256"]):
        raise RuntimeError("approved M1 recovery reference changed")

    rule_result = verify_rules()
    asset_count, differences = verify_assets(dispatch)
    current = {name: (ROOT / name).read_bytes() for name in (EDITED, *ADDED)}
    with TemporaryDirectory(prefix="v020_m2_rollback_") as directory:
        temp_root = Path(directory).resolve()
        if temp_root == ROOT or ROOT in temp_root.parents:
            raise RuntimeError("rollback replay must be outside the workspace")
        source = temp_root / EDITED
        source.parent.mkdir()
        source.write_bytes(archive_source(dispatch))
        apply_in(temp_root, m1_patch, "-R", f"--include={EDITED}")
        original = source.read_bytes()
        if digest(original) != baseline[EDITED]:
            raise RuntimeError("reconstructed M1 source differs from the M2 dispatch")

        lines = list(difflib.diff_bytes(difflib.unified_diff, current[EDITED].splitlines(keepends=True),
                                        original.splitlines(keepends=True), f"a/{EDITED}".encode(),
                                        f"b/{EDITED}".encode()))
        for name in ADDED:
            lines.append(f"diff --git a/{name} b/{name}\ndeleted file mode 100644\n".encode())
            lines.extend(difflib.diff_bytes(difflib.unified_diff, current[name].splitlines(keepends=True),
                                            [], f"a/{name}".encode(), b"/dev/null"))
        patch = b"".join(lines)
        for name, content in current.items():
            target = temp_root / name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(content)
        temp_patch = temp_root / "rollback.patch"
        temp_patch.write_bytes(patch)
        apply_in(temp_root, temp_patch)
        restored = sha256_file(source)
        if restored != baseline[EDITED] or any((temp_root / name).exists() for name in ADDED):
            raise RuntimeError("rollback did not restore the baseline source and remove added paths")

    with rollback.open("xb") as stream:
        stream.write(patch)
    with output.open("x", encoding="utf-8") as stream:
        json.dump({
            "head": head, "effective_baseline_count": len(baseline), "unchanged_count": len(baseline) - len(changed),
            "archive_entry_count": dispatch["local_recovery_archive"]["entry_count"],
            "finished_sha256": {name: digest(data) for name, data in current.items()},
            "patch_sha256": sha256_file(rollback), "restored_sha256": {EDITED: restored},
            "new_paths_removed": list(ADDED), "business_asset_count": asset_count,
            "business_anchor": "m2_r01_dispatch_baseline.json", "business_differences": differences,
            "dispatch_business_asset_count_field": dispatch["business_snapshot_asset_count"],
            "default_off_rule_gate": rule_result, "exit_code": 0,
        }, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rollback", type=Path, required=True)
    args = parser.parse_args()
    verify(args.out, args.rollback)
