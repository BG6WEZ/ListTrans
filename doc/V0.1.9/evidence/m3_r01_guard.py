"""Bind M3 source to M2, freeze before held-out evaluation, and verify isolation and rollback."""
import argparse
from datetime import datetime, timezone
import difflib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from m3_r01_quality import EVIDENCE, ROOT, SWITCHES, sha

sys.path.insert(0, str(ROOT / "tests"))
from verify_isolation import collect_snapshot, diff_snapshots

EDITED = ("app/binding_decisions.py", "app/report_generator.py", "app/server.py",
          "web/app.js", "web/index.css", "web/index.html")
ROLLBACK = EVIDENCE / "m3_r01_rollback.patch"
FREEZE = EVIDENCE / "m3_r01_pre_holdout_freeze.json"


def read(name):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8-sig"))


def baseline():
    return {
        **read("m0_r01_dispatch_baseline.json")["effective_baseline_sha256"],
        **read("m0_r01_coord_release.json")["m1_effective_source_baseline_sha256"],
        **read("m1_r01_coord_release.json")["m2_effective_source_updates_sha256"],
        **read("m2_r01_coord_release.json")["m3_effective_source_updates_sha256"],
        **read("m2_r01_coord_release.json")["m3_frontend_and_docker_baseline_sha256"],
    }


def create_patch(backup):
    prior = baseline()
    lines = []
    for name in EDITED:
        before = backup / name
        if sha(before) != prior[name]:
            raise RuntimeError(f"Invalid M2 recovery source: {name}")
        current = ROOT / name
        lines.extend(difflib.diff_bytes(
            difflib.unified_diff, current.read_bytes().splitlines(keepends=True),
            before.read_bytes().splitlines(keepends=True), f"a/{name}".encode(), f"b/{name}".encode(),
        ))
    with ROLLBACK.open("xb") as file:
        file.write(b"".join(lines))


def check_source():
    expected = baseline()
    hashes = {name: sha(ROOT / name) for name in expected}
    changes = {k: {"m2": expected[k], "m3": actual} for k, actual in hashes.items() if expected[k] != actual}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip()
    if len(expected) != 191 or head != read("m2_r01_coord_release.json")["head"] or set(changes) != set(EDITED):
        raise RuntimeError(f"Unexpected M3 source drift: {list(changes)}")
    restored = {}
    with TemporaryDirectory(prefix="v019_m3_rollback_") as path:
        isolated = Path(path).resolve()
        if ROOT == isolated or ROOT in isolated.parents:
            raise RuntimeError("Rollback needs a workspace-external root")
        for name in EDITED:
            target = isolated / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", str(ROLLBACK)],
                       cwd=isolated, check=True)
        for name in EDITED:
            restored[name] = sha(isolated / name)
            if restored[name] != expected[name]:
                raise RuntimeError(f"Rollback digest mismatch: {name}")
    return head, hashes, changes, restored


def freeze():
    head, files, changes, _ = check_source()
    for name in ("m3_r01_browser.json", "m3_r01_cancel.json", "m3_r01_pytest_exit.json", "m3_r01_load_exit.json"):
        if read(name)["exit_code"] != 0:
            raise RuntimeError(f"Pre-freeze verification failed: {name}")
    if any(read("m3_r01_gate_exits.json").values()):
        raise RuntimeError("Rule checks failed")
    concurrency = read("m3_r01_load_exit.json")["budget_checks"]
    if len(concurrency) != 6 or not all(concurrency.values()):
        raise RuntimeError("F23 concurrency is outside the signed limits")
    browser = read("m3_r01_browser.json")
    if (browser["manual_change_rate_delta_pp"] > 0 or not browser["exports"]
            or not all(export["xlsx_verified"] for export in browser["exports"])):
        raise RuntimeError("Native browser operator or workbook verification failed")
    for name, digest in read("m3_r01_pytest_exit.json")["source_sha256"].items():
        if sha(ROOT / name) != digest:
            raise RuntimeError(f"Source changed after regression: {name}")
    input_paths = [
        EVIDENCE / "m3_r01_quality.py",
        ROOT / "tests/test_v019_m3_review.py",
        ROOT / "doc/V0.1.8/evidence/m3_r01_dev_selection.py",
        ROOT / "doc/V0.1.7/evidence/m0_r01_benchmark.py",
        ROOT / "doc/V0.1.7/evidence/m0_r01_summarize.py",
        ROOT / "doc/V0.1.7/evidence/m0_r01_before.json",
        ROLLBACK,
    ]
    for file in input_paths:
        files[file.relative_to(ROOT).as_posix()] = sha(file)
    report = {"head": head, "frozen_at": datetime.now(timezone.utc).isoformat(),
              "default_switches": SWITCHES, "source_changes": changes, "sha256": files,
              "evaluation": "one default run per mode; Test metrics from the full run; no post-Test tuning"}
    with FREEZE.open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)


def after(output):
    head, _, changes, restored = check_source()
    pre = read("m3_r01_isolation_before.json")
    post = read("m3_r01_isolation_after.json")
    m2 = read("m2_r01_isolation_after.json")["business_snapshot_after"]
    samples = [pre["business_snapshot_before"], pre["business_snapshot_after"],
               post["business_snapshot_before"], post["business_snapshot_after"], collect_snapshot()]
    differences = [diff_snapshots(m2, snap) for snap in samples]
    n = len(m2["files"]) + sum(len(tree["files"]) for tree in m2["trees"].values())
    ok = n == 35 and not any(differences) and pre["exit_code"] == post["exit_code"] == 0
    report = {"head": head, "effective_baseline_count": 191, "source_changes": changes,
              "byte_exact_rollback_sha256": restored, "rollback_patch_sha256": sha(ROLLBACK),
              "business_assets": n, "business_differences": differences,
              "dockerfile_sha256": sha(ROOT / "Dockerfile"), "exit_code": 0 if ok else 1}
    with output.open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    return report["exit_code"]


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    actions = cli.add_mutually_exclusive_group(required=True)
    actions.add_argument("--prepare-rollback", type=Path)
    actions.add_argument("--freeze", action="store_true")
    actions.add_argument("--after", type=Path)
    args = cli.parse_args()
    if args.prepare_rollback:
        create_patch(args.prepare_rollback)
        check_source()
    elif args.freeze:
        freeze()
    else:
        sys.exit(after(args.after))
