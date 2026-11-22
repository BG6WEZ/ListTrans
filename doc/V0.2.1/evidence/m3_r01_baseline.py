"""Verify dispatch inputs without opening either sealed golden fixture."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[2]
LEDGER = "doc/V0.2.1/02_任务台账.md"


def sha(path):
    if path.is_file():
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    return None


def read(name):
    return json.loads((EVIDENCE / name).read_bytes())


def save(name, report):
    (EVIDENCE / name).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )


def verify(final):
    dispatch = read("m3_r01_dispatch_baseline.json")
    expected_modifications = {"app/matcher.py", "app/server.py"} if final else set()
    sealed = dispatch["sealed_assets_git_metadata"]
    actual = {}
    for name in dispatch["effective_baseline_sha256"]:
        if name not in sealed:
            actual[name] = sha(ROOT / name)
    drift = []
    for name, digest in actual.items():
        reference = dispatch["effective_baseline_sha256"][name]
        if digest != reference and name in dispatch["dockerfile_lf_normalized_sha256"]:
            lf = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
            if hashlib.sha256(lf).hexdigest() == dispatch["dockerfile_lf_normalized_sha256"][name]:
                continue
        if digest != reference:
            drift.append(name)
    git = lambda *args: subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    metadata = {name: git("rev-parse", f"HEAD:{name}") == info["head_blob"]
                for name, info in sealed.items()}
    business = dispatch["business_snapshot"]
    files = {name: sha(ROOT / name) for name in business["files"]}
    trees = {}
    for name in business["trees"]:
        root = ROOT / name
        trees[name] = {
            "__exists__": root.exists(),
            "files": {path.relative_to(root).as_posix(): sha(path)
                      for path in sorted(root.rglob("*")) if path.is_file()},
        }
    asset = dispatch["authorized_cross_encoder_asset"]
    models = {name: sha(ROOT / asset["local_dir"] / name) for name in asset["core_files_sha256"]}
    head = git("rev-parse", "HEAD")
    plan = sha(ROOT / dispatch["plan_r2_path"])
    business_differences = {name: {key for key in previous.keys() | current.keys()
                                  if previous.get(key) != current.get(key)}
                            for name, previous, current in (
                                ("files", business["files"], files), ("trees", business["trees"], trees)
                            )}
    count = sum(digest is not None for digest in files.values()) + sum(
        len(tree["files"]) for tree in trees.values()
    )
    status = (set(drift) != expected_modifications or not all(metadata.values())
              or head != dispatch["head"] or plan != dispatch["plan_r2_sha256"]
              or any(business_differences.values()) or count != dispatch["business_snapshot_asset_count"]
              or models != asset["core_files_sha256"])
    report = {
        "exit_code": int(bool(status)), "head": head, "plan_sha256": plan,
        "effective_baseline_count": len(actual) + len(metadata), "actual_sha256": actual,
        "sealed_head_blob_matches": metadata, "effective_baseline_changes": drift,
        "expected_modifications": sorted(expected_modifications),
        "ledger_sha256": sha(ROOT / LEDGER),
        "business_assets_on_disk": count, "business_snapshot": {"files": files, "trees": trees},
        "business_differences": {name: sorted(paths) for name, paths in business_differences.items()},
        "model_sha256": models,
    }
    save(f"m3_r01_baseline_{'after' if final else 'before'}.json", report)
    print({k: report[k] for k in ("exit_code", "effective_baseline_count", "effective_baseline_changes",
                                  "business_assets_on_disk", "business_differences")})
    return report["exit_code"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final", action="store_true")
    raise SystemExit(verify(parser.parse_args().final))
