"""Confirm eligible Train/Dev rows in a loopback-isolated catalog copy."""

import argparse
from collections import Counter, defaultdict
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import uuid

spec = importlib.util.spec_from_file_location(
    "v021_m0_upload_replay", Path(__file__).with_name("m0_r01_replay.py"),
)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)

PROBES = {"GT-0300", "GT-0551", "GT-0004"}


def run(mode, output):
    replay.configure_arm("off", None)
    data, labels = replay.read_projection()
    split_report = defaultdict(lambda: defaultdict(lambda: {
        "rows": 0, "priority": 0, "eligible": 0, "confirmed": 0, "not_acted_without_gold_evidence": 0,
    }))
    probes = {}
    priority_dispositions = {}
    with TemporaryDirectory(prefix="v021_m2_replay_", ignore_cleanup_errors=True) as tmp:
        staged = replay.stage(Path(tmp) / "isolated", mode, replay.read_json(replay.V017 / "m0_r01_before.json"))
        sys.addaudithook(replay.block_external_network)
        from fastapi.testclient import TestClient
        from app.server import app
        from app.vector_index import index_manager
        materials, _ = replay.read_catalog()
        with TestClient(app) as client:
            index = replay.checked_index(mode, index_manager, materials)
            headers = replay.configure_actor()
            for start in range(0, len(data), 50):
                rows, _ = replay.upload(client, headers, data[start:start + 50])
                entries = []
                targets = {}
                for row in rows:
                    gid = row["original_code"]
                    split = labels[gid]["split"]
                    metrics = split_report[split][row["professional"]]
                    metrics["rows"] += 1
                    initial = row["initial_match"]
                    is_priority = initial["tier"] == "A"
                    metrics["priority"] += is_priority
                    if gid in PROBES or is_priority:
                        entry = {**row, "scope_filename": "v021-replay.xlsx",
                                 "bindings": initial["effective_bindings"]}
                        entries.append(entry)
                        targets[row["source_row_id"]] = (entry, metrics)
                if not entries:
                    continue
                preflight = client.post("/api/binding/priority", headers=headers, json={"rows": entries})
                preflight.raise_for_status()
                selected = []
                for eligibility in preflight.json()["rows"]:
                    entry, metrics = targets[eligibility["source_row_id"]]
                    gid = entry["original_code"]
                    if entry["initial_match"]["tier"] == "A":
                        priority_dispositions[gid] = eligibility["reason"]
                    if gid in PROBES:
                        probes[gid] = {
                            "initial": entry["initial_match"], "eligible": eligibility["eligible"],
                            "reason": eligibility["reason"], "confirmed": False, "split": labels[gid]["split"],
                        }
                    if not eligibility["eligible"]:
                        continue
                    metrics["eligible"] += 1
                    # Isolated replay only submits when the reviewed code is supported by
                    # frozen Train/Dev adjudication. Unknown correctness stays pending.
                    allowed = set().union(*labels[gid]["target_alternatives"].values())
                    if entry["bindings"][0]["code"] not in allowed:
                        metrics["not_acted_without_gold_evidence"] += 1
                        continue
                    selected.append({
                        **entry, "idempotency_key": eligibility["idempotency_key"],
                        "reviewed_missing": bool(eligibility["missing"]),
                    })
                if selected:
                    confirmation = client.post("/api/binding/priority/submit", headers=headers, json={
                        "batch_request_id": uuid.uuid4().hex, "rows": selected,
                    })
                    confirmation.raise_for_status()
                    assert confirmation.json()["feedback_delivery_ok"], confirmation.json()
                    for receipt in confirmation.json()["rows"]:
                        assert receipt["ok"], receipt
                        entry, metrics = targets[receipt["source_row_id"]]
                        metrics["confirmed"] += 1
                        if entry["original_code"] in PROBES:
                            probes[entry["original_code"]]["confirmed"] = True
                print(f"{mode}: {start + len(rows)}/{len(data)}", flush=True)
    assert probes.keys() == PROBES
    assert {split: sum(bucket["rows"] for bucket in rows.values()) for split, rows in split_report.items()} == {
        "train": 842, "dev": 218,
    }
    output.write_text(json.dumps({
        "mode": mode, "index": index, "staged_inputs": staged,
        "confirmed_definition": "Successful isolated HTTP commit after Train/Dev gold-supported simulated review",
        "by_split_and_professional": split_report, "probes": probes,
        "priority_disposition_counts": Counter(priority_dispositions.values()),
        "priority_disposition_by_sample": priority_dispositions,
        "heldout_payloads_decoded": 0, "heldout_rows_used": 0,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("keyword", "vector"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.mode, args.out)
