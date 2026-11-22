# -*- coding: utf-8 -*-
"""V021-M2/R01 independent auditor baseline/scope/asset verification (stdlib only).

Recomputes from the live working tree:
  1. 82 effective baseline SHA256 (EOL dual-form accepted for Dockerfile/.dockerignore).
  2. Scope drift: which of the 82 differ from the M2 dispatch baseline.
  3. 35-item dispatch snapshot deltas (expected stale sidecars) AND 33-item
     coordinator reanchored snapshot zero-diff.
  4. config 16 files unchanged, auto_binding_rules == [], 10 switches OFF defaults.
  5. sealed Test-160 fixture assets unchanged vs HEAD blob (no decode).
Writes audit_baseline_check.json next to this script.
"""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EV = ROOT / "doc" / "V0.2.1" / "evidence"
DISPATCH = json.loads((EV / "m2_r01_dispatch_baseline.json").read_text(encoding="utf-8"))
COORD = json.loads((EV / "m2_r01_coord_handover_baseline.json").read_text(encoding="utf-8"))


def sha256_file(path: Path):
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(root: Path):
    if not root.exists():
        return {"__exists__": False, "files": {}}
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files[str(p.relative_to(root)).replace("\\", "/")] = sha256_file(p)
    return {"__exists__": True, "files": files}


WATCH_FILES = [
    "app/materials.db", "app/materials.db-wal", "app/materials.db-shm",
    "app/auth.db", "app/auth.db-wal", "app/auth.db-shm",
    "app/binding_feedback.json", "app/feedback_samples.json",
]
WATCH_TREES = [
    ("config", ROOT / "config"),
    ("app/vector_cache", ROOT / "app" / "vector_cache"),
    ("app/uploads", ROOT / "app" / "uploads"),
    ("logs", ROOT / "logs"),
    ("data", ROOT / "data"),
]
live_files = {f: sha256_file(ROOT / f) for f in WATCH_FILES}
live_trees = {key: tree_digest(disk) for key, disk in WATCH_TREES}

# ---- 1 & 2 : 82 baseline, scope drift ----
eol = DISPATCH["eol_normalized_lf_sha256"]
verified = 0
mismatch = []
changed = []
for name, expected in DISPATCH["effective_baseline_sha256"].items():
    path = ROOT / name
    raw = sha256_file(path)
    accepted = {raw}
    if name in eol and path.exists():
        accepted.add(hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest())
    if expected in accepted:
        verified += 1
    else:
        mismatch.append({"file": name, "expected": expected, "actual_raw": raw})
    if raw != expected and not (name in eol and hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == expected):
        changed.append(name)

# ---- 3 : snapshots ----
def compare_snapshot(snap):
    fd = [f for f in WATCH_FILES if live_files.get(f) != snap["files"].get(f)]
    td = [k for k, _ in WATCH_TREES if live_trees.get(k) != snap["trees"].get(k)]
    return fd, td

dispatch_fd, dispatch_td = compare_snapshot(DISPATCH["business_snapshot"])
reanch_fd, reanch_td = compare_snapshot(COORD["reanchored_audit_business_snapshot"])
asset_total = sum(1 for v in live_files.values() if v is not None) + sum(len(v["files"]) for v in live_trees.values())

# ---- 4 : config & switches ----
rules = json.loads((ROOT / "config" / "auto_binding_rules.json").read_text(encoding="utf-8-sig"))
config_count = len([p for p in (ROOT / "config").iterdir() if p.is_file()])
config_unchanged = all(
    sha256_file(ROOT / f"config/{p.name}") == DISPATCH["effective_baseline_sha256"].get(f"config/{p.name}")
    for p in (ROOT / "config").iterdir() if p.is_file()
)
switch_checks = {
    "app/feedback_ranker.py": ['os.getenv("LISTTRANS_V020_M0_RANKER") != "on"'],
    "app/llm_client.py": ['os.getenv("LLM_ENABLED", "false")'],
    "app/matcher.py": ['os.getenv("LISTTRANS_V019_M1_RERANK", "off")'],
    "app/server.py": ['os.getenv("ENABLE_SERIES_RERUN", "false")'],
}
switch_source_evidence = {}
for rel, needles in switch_checks.items():
    text = (ROOT / rel).read_text(encoding="utf-8")
    switch_source_evidence[rel] = {n: (n in text) for n in needles}

# ---- 5 : sealed assets (metadata only, never decode) ----
sealed = {}
for rel, meta in COORD["sealed_assets_git_metadata"].items():
    blob = subprocess.run(["git", "rev-parse", f"HEAD:{rel}"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()
    diff = subprocess.run(["git", "diff", "--", rel], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout
    sealed[rel] = {
        "head_blob_matches": blob == meta["head_blob"],
        "git_diff_empty": diff == "",
    }

report = {
    "baseline_total": DISPATCH["effective_baseline_count"],
    "baseline_verified": verified,
    "baseline_mismatch": mismatch,
    "changed_vs_m2_dispatch_baseline": sorted(changed),
    "dispatch_35_asset_count": DISPATCH["business_snapshot_asset_count"],
    "dispatch_snapshot_file_diffs": dispatch_fd,
    "dispatch_snapshot_tree_diffs": dispatch_td,
    "reanchored_33_file_diffs": reanch_fd,
    "reanchored_33_tree_diffs": reanch_td,
    "live_asset_count": asset_total,
    "reanchored_33_zero_diff": not reanch_fd and not reanch_td and asset_total == COORD["reanchored_audit_business_asset_count"],
    "config_file_count": config_count,
    "config_all_unchanged": config_unchanged,
    "auto_binding_rules_is_empty": rules == [],
    "switch_off_defaults_in_source": switch_source_evidence,
    "frozen_switches_record": DISPATCH["default_switches_frozen_off"],
    "sealed_test160_assets": sealed,
    "eol_dual_form": eol,
}
out = EV / "audit_m2_r01" / "audit_baseline_check.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
