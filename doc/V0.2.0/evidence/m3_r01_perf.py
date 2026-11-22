"""Sample the released OFF paths through single-match and 50-row HTTP upload."""
import argparse
from collections import Counter
import os
from pathlib import Path
import sys
from unittest.mock import patch

from m3_r01_run import EVIDENCE, ROOT, read_json, write_json
from m0_r01_replay import V017, block_external_network, checked_index, configure_actor, dist_ms
from m0_r01_replay import hardware, phase, read_catalog, read_projection, send_match, send_upload, stage
from m0_r01_replay import SIGNED_BUDGETS


def evenly_spaced(rows, count):
    return [rows[index * len(rows) // count] for index in range(count)]


def run(mode, root, single_only):
    dispatch = read_json(EVIDENCE / "m3_r01_dispatch_baseline.json")
    os.environ.update(dispatch["default_switches_frozen"])
    os.environ["OPENAI_API_KEY"] = ""
    items, labels = read_projection()
    warm = items[:5]
    batches = evenly_spaced(items[5:], 1000)
    singles = evenly_spaced(batches, 200)
    if len({item["id"] for item in batches}) != 1000:
        raise RuntimeError("Performance sample must contain 1,000 distinct Train/Dev rows")
    frozen = stage(root, mode, read_json(V017 / "m0_r01_before.json"))
    outbound_attempts = 0
    provider_attempts = 0

    def offline(event, args):
        nonlocal outbound_attempts
        try:
            block_external_network(event, args)
        except RuntimeError:
            outbound_attempts += 1
            raise

    def unexpected_provider(*args, **kwargs):
        nonlocal provider_attempts
        provider_attempts += 1
        raise RuntimeError("LLM transport called in default-OFF replay")

    sys.addaudithook(offline)
    sys.path.insert(0, str(ROOT))
    stages, upload_ms, single_ms = {}, [], []
    with phase(stages, "startup_prepare"):
        from fastapi.testclient import TestClient
        from openai.resources.chat.completions import AsyncCompletions, Completions
        from app import llm_client, llm_decision
        from app.feedback_ranker import active_ranker
        from app.server import app
        from app.vector_index import index_manager

        materials, fts_rows = read_catalog()
        if len(materials) != fts_rows or llm_decision.active_engine() is not None or active_ranker() is not None:
            raise RuntimeError("Frozen catalog or default OFF switches are inconsistent")
    with (patch.object(Completions, "create", unexpected_provider),
          patch.object(AsyncCompletions, "create", unexpected_provider),
          TestClient(app) as client):
        with phase(stages, "warmup"):
            if llm_client._get_llm_client().is_enabled():
                raise RuntimeError("Legacy LLM is enabled")
            index = checked_index(mode, index_manager, materials)
            headers = configure_actor()
            for item in warm:
                send_match(client, headers, item)
            send_upload(client, headers, warm)
        print(f"{mode}: measuring 200 single requests", flush=True)
        with phase(stages, "single"):
            single_ms = [send_match(client, headers, item)["ms"] for item in singles]
        if not single_only:
            print(f"{mode}: measuring 20 uploads of 50 rows", flush=True)
            with phase(stages, "upload"):
                for start in range(0, len(batches), 50):
                    _, metadata = send_upload(client, headers, batches[start:start + 50])
                    upload_ms.append(metadata["ms"])
                    print(f"{mode}: completed upload {len(upload_ms)}/20", flush=True)
    single, upload = dist_ms(single_ms), dist_ms(upload_ms)
    peak = max(result["process_peak_working_set_mib"] for result in stages.values())
    observed = {"single_p50_ms": single["p50_ms"], "single_p95_ms": single["p95_ms"]}
    if not single_only:
        observed.update(up50_p50_ms=upload["p50_ms"], up50_p95_ms=upload["p95_ms"], wset_mib=peak)
    limits = {key: SIGNED_BUDGETS[mode][key] for key in observed}
    uploaded = [] if single_only else batches
    return {"mode": mode, "single_only": single_only, "hardware": hardware(), "isolation_root": str(root),
            "switches": dispatch["default_switches_frozen"], "input_hashes": frozen,
            "catalog_fts_rows": fts_rows, "model_index": index, "phases": stages,
            "upload_population": dict(Counter(labels[item["id"]]["split"] for item in uploaded)),
            "single_population": dict(Counter(labels[item["id"]]["split"] for item in singles)),
            "warmup_ids": [item["id"] for item in warm], "upload_input_ids": [item["id"] for item in uploaded],
            "single_input_ids": [item["id"] for item in singles], "single_ms": single_ms, "upload50_ms": upload_ms,
            "observed": observed, "budget": limits, "budget_pass": {key: observed[key] <= cap for key, cap in limits.items()},
            "outbound_network_attempts": outbound_attempts, "llm_transport_attempts": provider_attempts}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("keyword", "vector"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--single-only", action="store_true")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Use a new output file")
    result = run(args.mode, args.root.resolve(), args.single_only)
    write_json(args.out, result)
    print(result["observed"], result["budget_pass"], flush=True)
    raise SystemExit(1 if result["outbound_network_attempts"] or result["llm_transport_attempts"] else
                     0 if all(result["budget_pass"].values()) else 2)
