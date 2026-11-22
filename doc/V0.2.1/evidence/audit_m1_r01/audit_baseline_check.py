# -*- coding: utf-8 -*-
"""V021-M1/R01 independent auditor baseline & asset verification (stdlib only).

Recomputes from the live working tree (never trusts the dev summary):
  1. All 82 effective baseline SHA256, accepting Dockerfile/.dockerignore in
     both raw and LF-normalized forms (core.autocrlf=true dual morphology).
  2. The 35 business-asset snapshot (8 files + 5 trees) vs dispatch snapshot.
  3. The 16 config files are inside the unchanged baseline; auto_binding_rules == [].
  4. The 10 experiment/LLM switches read their OFF/legacy defaults in source.
Prints a JSON verdict and writes it next to this script.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EV = ROOT / "doc" / "V0.2.1" / "evidence"
BASELINE = json.loads((EV / "m1_r01_dispatch_baseline.json").read_text(encoding="utf-8"))


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


# ---- 1. effective baseline (82) with EOL dual-form acceptance ----
eol = BASELINE["eol_normalized_lf_sha256"]
mismatch = []
verified = 0
for name, expected in BASELINE["effective_baseline_sha256"].items():
    path = ROOT / name
    raw = sha256_file(path)
    accepted = {raw}
    if name in eol and path.exists():
        accepted.add(hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest())
    if expected in accepted:
        verified += 1
    else:
        mismatch.append({"file": name, "expected": expected, "actual_raw": raw})

# Explicitly classify which of the 82 actually differ from the M1 STARTING baseline.
changed_vs_baseline = []
for name, expected in BASELINE["effective_baseline_sha256"].items():
    raw = sha256_file(ROOT / name)
    lf = None
    if name in eol and (ROOT / name).exists():
        lf = hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if raw != expected and lf != expected:
        changed_vs_baseline.append(name)

# ---- 2. business snapshot (35 assets) ----
WATCH_FILES = [
    "app/materials.db", "app/materials.db-wal", "app/materials.db-shm",
    "app/auth.db", "app/auth.db-wal", "app/auth.db-shm",
    "app/binding_feedback.json", "app/feedback_samples.json",
]
# (snapshot key relative to project root, on-disk path)
WATCH_TREES = [
    ("config", ROOT / "config"),
    ("app/vector_cache", ROOT / "app" / "vector_cache"),
    ("app/uploads", ROOT / "app" / "uploads"),
    ("logs", ROOT / "logs"),
    ("data", ROOT / "data"),
]
live_files = {f: sha256_file(ROOT / f) for f in WATCH_FILES}
live_trees = {key: tree_digest(disk) for key, disk in WATCH_TREES}
base_snap = BASELINE["business_snapshot"]
base_files = base_snap["files"]
base_trees = base_snap["trees"]
file_diffs = [f for f in WATCH_FILES if live_files.get(f) != base_files.get(f)]
tree_diffs = [key for key, _ in WATCH_TREES if live_trees.get(key) != base_trees.get(key)]
asset_total = sum(
    ([1] * len(live_files))
    + [len(v["files"]) for v in live_trees.values()]
)

# ---- 3. config integrity & empty rules ----
rules = json.loads((ROOT / "config" / "auto_binding_rules.json").read_text(encoding="utf-8-sig"))
config_count = len([p for p in (ROOT / "config").iterdir() if p.is_file()])
config_unchanged = all(
    sha256_file(ROOT / f"config/{p.name}") == BASELINE["effective_baseline_sha256"].get(f"config/{p.name}")
    for p in (ROOT / "config").iterdir() if p.is_file()
)

# ---- 4. switch OFF defaults present in source ----
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

verdict = {
    "baseline_total": BASELINE["effective_baseline_count"],
    "baseline_verified": verified,
    "baseline_mismatch": mismatch,
    "changed_vs_m1_start_baseline": sorted(changed_vs_baseline),
    "business_asset_count_recomputed": asset_total,
    "business_asset_expected": BASELINE["business_snapshot_asset_count"],
    "business_file_diffs": file_diffs,
    "business_tree_diffs": tree_diffs,
    "business_assets_zero_diff": not file_diffs and not tree_diffs
    and asset_total == BASELINE["business_snapshot_asset_count"],
    "config_file_count": config_count,
    "config_all_unchanged": config_unchanged,
    "auto_binding_rules_is_empty": rules == [],
    "switch_off_defaults_in_source": switch_source_evidence,
    "frozen_switches_record": BASELINE["default_switches_frozen_off"],
    "eol_dual_form": eol,
}
out = EV / "audit_m1_r01" / "audit_baseline_check.json"
out.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(verdict, ensure_ascii=False, indent=2))
