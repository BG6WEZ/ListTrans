"""Replay the signed V017 source on the same Train/Dev catalog for attribution."""
import argparse
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from zipfile import ZipFile

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[2]
DOC = ROOT / "doc"
V017 = DOC / "V0.1.7" / "evidence"
V018 = DOC / "V0.1.8" / "evidence"
sys.path[:0] = [str(ROOT), str(V017), str(V018)]

import m0_r01_benchmark as benchmark
from m0_r01_build_governance import GOLDEN, read_json, save_json, sha
from m3_r01_dev_selection import extract_per_sample_compact, summarize_split


class RestoredSources(importlib.abc.MetaPathFinder):
    """Load only verified archived app modules with their original asset locations."""

    def __init__(self, paths):
        self.paths = paths

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.paths:
            staged = self.paths[fullname]

            class RestoredLoader(importlib.machinery.SourceFileLoader):
                def get_code(self, name):
                    return self.source_to_code(staged.read_bytes(), self.path)

            original = str(ROOT / (fullname.replace(".", "/") + ".py"))
            return importlib.util.spec_from_file_location(fullname, original, loader=RestoredLoader(fullname, original))
        return None


def stage_reference(root):
    prior = read_json(V018 / "v018_final_coord_verify.json")["v017_rollback_recovery"]
    manifest = read_json(V018 / "m0_r01_dispatch_baseline.json")["coord_source_and_test_worktree_sha256"]
    archive = read_json(EVIDENCE / "m0_r01_dispatch_baseline.json")["source_rollback_archive"]
    archive_file = ROOT / archive["path"]
    if sha(archive_file) != archive["sha256"]:
        raise RuntimeError("V018 archive digest mismatch")
    paths = {}
    with ZipFile(archive_file) as frozen:
        for name, digest in manifest.items():
            if name in prior["restored_source_sha256"]:
                content = (ROOT / prior["local_archive"] / name).read_bytes()
            else:
                content = frozen.read(name) if name in archive["entry_sha256"] else (ROOT / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise RuntimeError(f"V017 source digest mismatch: {name}")
            if name.startswith("app/") and name.endswith(".py"):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                paths[name[:-3].replace("/", ".")] = target
    return paths, manifest


def run(mode, output):
    if output.exists():
        raise FileExistsError(output)
    os.environ.update({
        "LISTTRANS_V019_M0_SCORING": "legacy", "LISTTRANS_V018_M0_RECALL": "0",
        "LISTTRANS_V018_M1_RECALL": "0", "LISTTRANS_V018_M2_RECALL": "0", "ENABLE_SERIES_RERUN": "false",
    })
    governance_file = ROOT / "tests" / "fixtures" / "golden_governance_v1.json"
    governance = read_json(governance_file)["samples"]
    items = [it for it in read_json(GOLDEN)["items"] if governance[it["id"]]["split"] in ("train", "dev")]
    if len(items) != 1060:
        raise RuntimeError("Expected 1060 Train/Dev rows")
    # The staged SQLite connection can survive until process exit on Windows.
    with tempfile.TemporaryDirectory(prefix="v019_v017_reference_", ignore_cleanup_errors=True) as directory:
        root = Path(directory).resolve()
        if ROOT in root.parents or root == ROOT:
            raise RuntimeError("Use a native temporary directory outside the repository")
        paths, manifest = stage_reference(root / "source")
        frozen = benchmark.stage(root / "runtime", mode, read_json(V017 / "m0_r01_before.json"))
        sys.meta_path.insert(0, RestoredSources(paths))
        sys.addaudithook(benchmark.block_external_network)

        from fastapi.testclient import TestClient
        from app.server import app
        from app.vector_index import index_manager

        materials, _ = benchmark.read_catalog()
        uploads = {}
        with TestClient(app) as client:
            index = benchmark.checked_index(mode, index_manager, materials)
            headers = benchmark.configure_actor()
            for item in items[:5]:
                benchmark.send_match(client, headers, item)
            benchmark.send_upload(client, headers, items[:5])
            diagnostics, elapsed = benchmark.diagnostic_rows(items, governance)
            semantic = sum(row["semantic_in_pool"] for row in diagnostics.values())
            if semantic != (0 if mode == "keyword" else len(items)):
                raise RuntimeError("Unexpected semantic channel coverage")
            for start in range(0, len(items), 50):
                rows, _ = benchmark.send_upload(client, headers, items[start:start + 50])
                uploads.update(rows)
        splits = {"train_dev": items, **{
            split: [r for r in items if governance[r["id"]]["split"] == split] for split in ("train", "dev")
        }}
        summaries = {key: summarize_split(subset, diagnostics, {}, uploads, governance)
                     for key, subset in splits.items()}
        for report in summaries.values():
            report.pop("miss_analysis")
        sample_rows = extract_per_sample_compact(items, diagnostics, governance)
        for row in sample_rows.values():
            row.pop("name")
        save_json(output, {
            "mode": mode, "exit_code": 0, "failed_rows": 0, "skipped_rows": 0,
            "golden_sha256": sha(GOLDEN), "governance_sha256": sha(governance_file),
            "catalog_sha256": frozen["catalog"], "vector_index": index,
            "restored_manifest_files_checked": len(manifest),
            "restored_source_sha256": {name: digest for name, digest in manifest.items()
                                       if name.startswith("app/") and name.endswith(".py")},
            "semantic_rows": semantic, "ranking_elapsed_seconds": elapsed,
            "splits": summaries, "per_sample_ans": sample_rows, "upload": uploads,
            "top10": {gid: row["top10"] for gid, row in diagnostics.items()},
        })


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("keyword", "vector"), required=True)
    p.add_argument("--out", type=Path, required=True)
    options = p.parse_args()
    run(options.mode, options.out)
