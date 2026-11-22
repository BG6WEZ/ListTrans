"""Verify dispatch integrity, scope, and byte-exact rollback in an external temporary root."""
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
sys.path.insert(0, str(ROOT / "tests"))
from verify_isolation import collect_snapshot, diff_snapshots, sha256_file

EDITED = "app/matcher.py"
ADDED = ("app/feedback_ranker.py", "scripts/v020_feedback_audit.py", "tests/test_v020_m0_feedback_ranker.py")


def read(name):
    return json.loads((EV / name).read_text(encoding="utf-8-sig"))


def write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def verify(output, rollback):
    dispatch = read("m0_r01_dispatch_baseline.json")
    expected = dispatch["effective_baseline_sha256"]
    mismatches = {name for name, digest in expected.items() if sha256_file(ROOT / name) != digest}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != dispatch["head"] or len(expected) != 191 or mismatches != {EDITED}:
        raise ValueError(f"unexpected effective source changes: {sorted(mismatches)}")
    m0_test = "tests/test_v019_m0_ranking.py"
    v019_manifest = ROOT / "doc/V0.1.9/evidence/m0_r01_delivery_sha256.json"
    m0_test_digest = json.loads(v019_manifest.read_text(encoding="utf-8-sig"))["files"][m0_test]["sha256"]
    if sha256_file(ROOT / m0_test) != m0_test_digest:
        raise ValueError("inherited V019-M0 regression drift")
    archive = dispatch["local_recovery_archive"]
    archive_path = ROOT / archive["path"]
    if sha256_file(archive_path) != archive["sha256"]:
        raise ValueError("recovery archive digest mismatch")
    with zipfile.ZipFile(archive_path) as source:
        if (len(source.namelist()) != archive["entry_count"] or len(set(source.namelist())) != archive["entry_count"]
                or any(hashlib.sha256(source.read(name)).hexdigest() != expected.get(name)
                       for name in source.namelist())):
            raise ValueError("recovery archive member mismatch")
        previous = source.read(EDITED)

    current = (ROOT / EDITED).read_bytes()
    patch = b"".join(difflib.diff_bytes(difflib.unified_diff, current.splitlines(keepends=True),
                                       previous.splitlines(keepends=True),
                                       f"a/{EDITED}".encode(), f"b/{EDITED}".encode()))
    for name in ADDED:
        raw = (ROOT / name).read_bytes()
        patch += f"diff --git a/{name} b/{name}\ndeleted file mode 100644\n".encode()
        patch += b"".join(difflib.diff_bytes(difflib.unified_diff, raw.splitlines(keepends=True),
                                            [], f"a/{name}".encode(), b"/dev/null"))
    if rollback.exists() and rollback.read_bytes() != patch:
        raise ValueError("existing rollback patch does not match the current delivery")
    if not rollback.exists():
        rollback.write_bytes(patch)

    with TemporaryDirectory(prefix="listtrans_v020_rollback_") as directory:
        root = Path(directory).resolve()
        if ROOT == root or ROOT in root.parents:
            raise ValueError("rollback check requires a workspace-external root")
        for name in (EDITED, *ADDED):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", str(rollback.resolve())],
                       cwd=root, check=True)
        restored = sha256_file(root / EDITED)
        removed = [name for name in ADDED if not (root / name).exists()]
        if restored != expected[EDITED] or len(removed) != len(ADDED):
            raise ValueError("source rollback is not byte exact")

    original = dispatch["business_snapshot_sha256"]
    snapshots = {"current": collect_snapshot()}
    for name in ("before", "after"):
        report = read(f"m0_r01_isolation_{name}.json")
        if report.get("verdict") != "PASS" or report.get("exit_code") != 0:
            raise ValueError(f"{name}: isolation check failed")
        snapshots[f"{name}_start"] = report["business_snapshot_before"]
        snapshots[f"{name}_end"] = report["business_snapshot_after"]
    start = snapshots["before_start"]
    differences = {key: diff_snapshots(start, snap) for key, snap in snapshots.items()}
    if any(differences.values()):
        raise ValueError(f"unexpected business asset changes: {differences}")
    dispatch_drift = diff_snapshots(original, start)
    preexisting_append = None
    if dispatch_drift:
        expected_logs = original["trees"]["logs"]["files"]
        actual_logs = start["trees"]["logs"]["files"]
        log = "security.log"
        if dispatch_drift != ["目录变化: logs"] or {**actual_logs, log: expected_logs[log]} != expected_logs:
            raise ValueError(f"unexpected business asset differences at start: {dispatch_drift}")
        content = (ROOT / "logs" / log).read_bytes()
        prefix_hash = hashlib.sha256()
        offset = 0
        for line in content.splitlines(keepends=True):
            offset += len(line)
            prefix_hash.update(line)
            if prefix_hash.hexdigest() == expected_logs[log]:
                break
        else:
            raise ValueError("security log change at start does not preserve dispatch bytes")
        preexisting_append = {"file": f"logs/{log}", "dispatch_sha256": expected_logs[log],
                              "first_isolation_sha256": actual_logs[log],
                              "dispatch_prefix_bytes": offset, "appended_before_first_snapshot_bytes": len(content) - offset}
    off, on = (read(f"m0_r01_gate_{arm}.json") for arm in ("off", "on"))
    rule_names = ("empty", "harmful", "beneficial", "beneficial_constrained", "neutral")
    if (any(report["exit_code"] != 0 or not report["passed_protected_probes"] for report in (off, on))
            or off["protected_probes"] != on["protected_probes"]
            or any(not off["rule_safety_unchanged"][rule] or not on["rule_safety_unchanged"][rule]
                   for rule in rule_names)):
        raise ValueError("OFF/ON rule or protected-export regression")
    write(output, {
        "head": head, "effective_baseline_count": len(expected), "effective_baseline_unchanged": len(expected) - 1,
        "recovery_archive_count": archive["entry_count"], "recovery_archive_sha256": archive["sha256"],
        "inherited_test_sha256": {m0_test: m0_test_digest},
        "business_file_count": len(original["files"]) + sum(len(x["files"]) for x in original["trees"].values()),
        "preexisting_dispatch_log_append": preexisting_append,
        "business_snapshot_differences": differences, "rollback_sha256": sha256_file(rollback),
        "rule_safety_parity": list(rule_names), "protected_probe_parity": sorted(off["protected_probes"]),
        "restored": {EDITED: restored}, "removed_added_files_in_rollback_copy": removed,
        "source_sha256": {name: sha256_file(ROOT / name) for name in (EDITED, *ADDED)},
        "exit_code": 0,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rollback", type=Path, required=True)
    args = parser.parse_args()
    verify(args.out, args.rollback)
