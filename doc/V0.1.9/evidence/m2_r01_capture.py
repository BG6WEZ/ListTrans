"""Freeze label-free acceptance evidence from actual Train/Dev upload routes."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "doc/V0.1.7/evidence")]
from m0_r01_benchmark import (
    block_external_network, checked_index, configure_actor, read_catalog, send_upload, stage,
)
from m0_r01_build_governance import GOLDEN, read_json, save_json, sha
from m1_r01_capture import stamp

GOVERNANCE = ROOT / "tests/fixtures/golden_governance_v1.json"
EQUIVALENCES = ROOT / "app/calibration_assets/v019_m2_equivalences.json"


def frozen_inputs():
    governance = read_json(GOVERNANCE)
    labels = governance["samples"]
    split_ids = {split: sorted(gid for gid, entry in labels.items() if entry["split"] == split)
                 for split in ("train", "dev", "test")}
    if [len(ids) for ids in split_ids.values()] != [842, 218, 160]:
        raise RuntimeError("Unexpected golden split")
    items = [it for it in read_json(GOLDEN)["items"] if labels[it["id"]]["split"] in ("train", "dev")]
    if len(items) != 1060:
        raise RuntimeError("Unexpected Train/Dev cohort")
    return governance, split_ids, items


def write_equivalences():
    governance = read_json(GOVERNANCE)
    data = {"version": governance["version"], "governance_sha256": sha(GOVERNANCE),
            "catalog_sha256": governance["inputs"]["app/materials.db"],
            "equivalent_code_groups": governance["equivalent_code_groups"]}
    EQUIVALENCES.parent.mkdir(parents=True, exist_ok=True)
    with EQUIVALENCES.open("x", encoding="utf-8") as file:
        json.dump(data, file, sort_keys=True, separators=(",", ":"))


def capture(mode, output):
    if output.exists():
        raise FileExistsError(output)
    governance, split_ids, items = frozen_inputs()
    groups = read_json(EQUIVALENCES)
    if (groups["governance_sha256"] != sha(GOVERNANCE)
            or groups["equivalent_code_groups"] != governance["equivalent_code_groups"]):
        raise RuntimeError("Frozen equivalences drifted")
    allowed_ids = set(split_ids["train"] + split_ids["dev"])
    source = {p: sha(ROOT / p) for p in ("app/matcher.py", "app/server.py", "app/acceptance_calibration.py")}
    os.environ.update({
        "LISTTRANS_V018_M0_RECALL": "0", "LISTTRANS_V018_M1_RECALL": "0", "LISTTRANS_V018_M2_RECALL": "0",
        "LISTTRANS_V019_M0_SCORING": "legacy", "LISTTRANS_V019_M1_RERANK": "off",
        "LISTTRANS_V019_M2_CALIBRATION": "off", "ENABLE_SERIES_RERUN": "false",
    })
    with tempfile.TemporaryDirectory(prefix="v019_m2_capture_", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp).resolve() / "runtime"
        staged = stage(root, mode, read_json(ROOT / "doc/V0.1.7/evidence/m0_r01_before.json"))
        sys.addaudithook(block_external_network)
        from fastapi.testclient import TestClient
        from app import server
        from app.acceptance_calibration import FEATURES, Policy
        from app.vector_index import index_manager

        # Run the real route in shadow mode with a no-accept policy. Neither
        # training labels nor the Dev threshold are available to the server.
        shadow = Policy((0.0,) * len(FEATURES), 0.0, None, 0.001, 0.0,
                        {code: frozenset(codes) for code, codes in groups["equivalent_code_groups"].items()})
        original = server.assess_match
        observed = {}

        def see(row, query, match, policy, **kwargs):
            result = original(row, query, match, policy, **kwargs)
            gid = row["original_code"]
            if gid not in allowed_ids:
                raise RuntimeError("Non-Train/Dev capture row")
            observed[gid] = {k: v for k, v in result.items() if k != "confidence"}
            return result

        with patch.object(server, "acceptance_policy", lambda: shadow), patch.object(server, "assess_match", see):
            with TestClient(server.app) as client:
                material, _ = read_catalog()
                index = checked_index(mode, index_manager, material)
                headers = configure_actor()
                for start in range(0, len(items), 50):
                    send_upload(client, headers, items[start:start + 50])
                    print(f"{mode} captured {min(start + 50, len(items))}/{len(items)}", flush=True)
        if set(observed) != allowed_ids:
            raise RuntimeError("Upload did not evaluate every frozen Train/Dev row")
        if any(sha(ROOT / p) != digest for p, digest in source.items()):
            raise RuntimeError("Runtime source changed during feature capture")
        save_json(output, {
            "mode": mode, "source_sha256": source, "governance_sha256": sha(GOVERNANCE),
            "golden_sha256": sha(GOLDEN), "equivalence_sha256": sha(EQUIVALENCES),
            "catalog_sha256": staged["catalog"], "index": index,
            "features": FEATURES, "split_id_sha256": {split: stamp(ids) for split, ids in split_ids.items()},
            "rows": observed, "exit_code": 0,
        })


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--write-equivalences", action="store_true")
    p.add_argument("--mode", choices=("keyword", "vector"))
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    if args.write_equivalences:
        write_equivalences()
    else:
        if not args.mode or not args.out:
            p.error("--mode and --out are required for feature capture")
        capture(args.mode, args.out)
