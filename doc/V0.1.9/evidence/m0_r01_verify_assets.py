"""Verify the dispatch snapshot, archive restore and unchanged business assets."""
import argparse
import ast
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
from verify_isolation import collect_snapshot, diff_snapshots, sha256_file


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def benchmark_source_binding():
    reports = sorted(EVIDENCE.glob("m0_r01_*_*_final.json"))
    measured = [load(report)["source_sha256"] for report in reports if report.name.split("_")[2] in ("keyword", "vector")]
    if not measured:
        return None
    matcher = ROOT / "app/matcher.py"
    installed = matcher.read_bytes()
    final_wrapper = (
        "    V015-M3：保留明细接口的二元组包装。\n"
        '    """\n'
        "    final_score, reasons, _detail = score_candidate_with_detail(query_info, candidate, semantic_score)\n"
        "    return final_score, reasons\n"
    ).encode()
    replay_wrapper = (
        "    V015-M3：保留二元组签名；硬约束明细请调用 score_candidate_with_detail。\n"
        '    """\n'
        "    ctx = _score_candidate_context(query_info, candidate, semantic_score, _scoring_profile())\n"
        "    return _finalize_candidate_score(ctx)\n"
    ).encode()
    if installed.count(final_wrapper) != 1:
        raise RuntimeError("Final score_candidate wrapper changed")
    reconstructed = hashlib.sha256(installed.replace(final_wrapper, replay_wrapper, 1)).hexdigest()
    callers = []
    for file in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(file.read_bytes(), filename=str(file))):
            if (isinstance(node, ast.Name) and node.id == "score_candidate"
                    or isinstance(node, ast.Attribute) and node.attr == "score_candidate"
                    or isinstance(node, ast.alias) and node.name == "score_candidate"):
                callers.append(f"{file.relative_to(ROOT).as_posix()}:{node.lineno}")
    current_server = sha256_file(ROOT / "app/server.py")
    return {
        "measured_runs": len(measured),
        "measured_source_sha256": measured[0],
        "final_source_sha256": {"app/matcher.py": sha256_file(matcher), "app/server.py": current_server},
        "restoring_only_pre_fix_wrapper_reproduces_measured_matcher": all(
            row["app/matcher.py"] == reconstructed for row in measured
        ),
        "measured_server_matches_final": all(row["app/server.py"] == current_server for row in measured),
        "wrapper_references_in_app": callers,
    }


def main(output):
    dispatch = load(EVIDENCE / "m0_r01_dispatch_baseline.json")
    before = load(EVIDENCE / "m0_r01_isolation_before.json")
    prior = load(ROOT / "doc" / "V0.1.8" / "evidence" / "m3_r01_isolation_after.json")
    snapshot = collect_snapshot()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    expected = dispatch["effective_baseline_sha256"]
    changes = {}
    for name, digest in expected.items():
        current = sha256_file(ROOT / name)
        if current != digest:
            changes[name] = {"baseline": digest, "current": current}

    archive_spec = dispatch["source_rollback_archive"]
    archive = ROOT / archive_spec["path"]
    archive_digest = sha256_file(archive)
    if archive_digest != archive_spec["sha256"]:
        raise RuntimeError("Archive digest changed")
    entry_hashes = archive_spec["entry_sha256"]
    with tempfile.TemporaryDirectory(prefix="v019_m0_restore_") as temp:
        root = Path(temp).resolve()
        if root == ROOT or ROOT in root.parents:
            raise RuntimeError("Restore must be outside the workspace")
        with ZipFile(archive) as source:
            if len(source.namelist()) != archive_spec["entries"] or set(source.namelist()) != set(entry_hashes):
                raise RuntimeError("Archive inventory changed")
            for name in source.namelist():
                if root not in (root / name).resolve().parents:
                    raise RuntimeError("Invalid archive path")
                source.extract(name, root)
        restored = {name: sha256_file(root / name) for name in entry_hashes}
        if restored != entry_hashes:
            raise RuntimeError("Restored bytes differ from the dispatch snapshot")
        for name in ("app/matcher.py", "app/server.py"):
            compile((root / name).read_bytes(), str(root / name), "exec")
    slots = len(snapshot["files"]) + sum(len(tree["files"]) for tree in snapshot["trees"].values())
    snapshot_diff = diff_snapshots(before["business_snapshot_before"], snapshot)
    carryover_diff = diff_snapshots(prior["business_snapshot_after"], before["business_snapshot_before"])
    binding = benchmark_source_binding()
    binding_invalid = binding is not None and (
        binding["measured_runs"] != 6 or not binding["restoring_only_pre_fix_wrapper_reproduces_measured_matcher"]
        or not binding["measured_server_matches_final"] or bool(binding["wrapper_references_in_app"])
    )
    exit_code = int(
        bool(set(changes) - {"app/matcher.py", "app/server.py"} or snapshot_diff or carryover_diff)
        or head != dispatch["head"] or slots != dispatch["business_snapshot_slots"] or binding_invalid
    )
    value = {
        "checked_at": datetime.now().astimezone().isoformat(),
        "head": head, "checked_baseline_files": len(expected), "baseline_changes": changes,
        "business_snapshot_slots": slots, "business_diff_from_start": snapshot_diff,
        "business_diff_v018_to_v019_start": carryover_diff,
        "archive_sha256": archive_digest, "archive_restored_files": len(restored),
        "restored_scoring_source": {name: restored[name] for name in ("app/matcher.py", "app/server.py")},
        "benchmark_source_binding": binding,
        "exit_code": exit_code,
    }
    with output.open("x", encoding="utf-8") as report:
        json.dump(value, report, ensure_ascii=False, indent=2)
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.exit(main(args.out))
