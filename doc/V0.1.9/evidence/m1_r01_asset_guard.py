"""Prove the M0 source boundary, unchanged business snapshots and exact M0 rollback."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
from verify_isolation import collect_snapshot, diff_snapshots, sha256_file


def read(name):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8-sig"))


def restored_matcher(content):
    edits = (
        (
            '    rerank_profile = os.getenv("LISTTRANS_V019_M1_RERANK", "off")\n'
            '    rerank_enabled = rerank_profile not in ("", "0", "off")\n', "",
        ),
        (
            '        if rerank_enabled:\n'
            '            scored_candidates[-1]["_rerank_inputs"] = (\n'
            '                max_score_base, cand["semantic_score"], c_parsed or {},\n'
            '            )\n', "",
        ),
        (
            '    exact_threshold = 75.0\n'
            '    rerank_detail = None\n'
            '    if rerank_enabled:\n'
            '        from app.candidate_reranker import rerank\n'
            '\n'
            '        rerank_detail = rerank(query_info, scored_candidates, rerank_profile, exact_threshold=exact_threshold)\n'
            '        for candidate in scored_candidates:\n'
            '            candidate.pop("_rerank_inputs")\n'
            '\n', "",
        ),
        ('        if top_one["score"] >= exact_threshold and not top_one.get("hard_blocked"):\n',
         '        if top_one["score"] >= 75.0 and not top_one.get("hard_blocked"):\n'),
        ('        if rerank_detail is not None:\n            result["diagnostics"]["rerank"] = rerank_detail\n', ""),
    )
    for current, previous in edits:
        old = current.encode()
        if content.count(old) != 1:
            raise RuntimeError("M1 matcher hook cannot be reversed exactly")
        content = content.replace(old, previous.encode(), 1)
    return content


def main(output):
    dispatch = read("m0_r01_dispatch_baseline.json")
    release = read("m0_r01_coord_release.json")
    expected = {**dispatch["effective_baseline_sha256"], **release["m1_effective_source_baseline_sha256"]}
    changes = {}
    for name, digest in expected.items():
        observed = sha256_file(ROOT / name)
        if observed != digest:
            changes[name] = {"m0": digest, "m1": observed}
    with tempfile.TemporaryDirectory(prefix="v019_m1_rollback_") as temp:
        directory = Path(temp).resolve()
        if directory == ROOT or ROOT in directory.parents:
            raise RuntimeError("Rollback check must use a native temporary directory")
        path = directory / "matcher.py"
        path.write_bytes(restored_matcher((ROOT / "app/matcher.py").read_bytes()))
        compile(path.read_bytes(), str(path), "exec")
        restored_sha = sha256_file(path)

    before = read("m1_r01_isolation_before.json")["business_snapshot_before"]
    prior = read("m0_r01_isolation_post_rerun.json")["business_snapshot_after"]
    live = collect_snapshot()
    n = len(live["files"]) + sum(len(tree["files"]) for tree in live["trees"].values())
    baseline_delta = diff_snapshots(prior, before)
    runtime_delta = diff_snapshots(before, live)
    check_archive = dispatch["source_rollback_archive"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    archive_intact = sha256_file(ROOT / check_archive["path"]) == check_archive["sha256"]
    inventory = sorted(p.name for p in (ROOT / "model_offline").iterdir() if p.is_dir())
    ok = (
        head == release["head"] and archive_intact and len(expected) == 175 and n == 35
        and list(changes) == ["app/matcher.py"] and not baseline_delta and not runtime_delta
        and restored_sha == expected["app/matcher.py"]
        and inventory == sorted(release["frozen_assets_check"]["offline_models_inventory"])
    )
    result = {
        "head": head, "effective_baseline_files": len(expected), "baseline_changes": changes,
        "exact_m0_matcher_sha256_restored_in_temp": restored_sha,
        "m0_matcher_sha256_expected": expected["app/matcher.py"],
        "business_files_checked": n, "m0_to_m1_start_diff": baseline_delta, "m1_runtime_diff": runtime_delta,
        "prior_rollback_archive_unchanged": archive_intact, "offline_models": inventory,
        "cross_encoder": "not_run_no_offline_weights", "exit_code": 0 if ok else 1,
    }
    with output.open("x", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
    return result["exit_code"]


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    sys.exit(main(args.out))
