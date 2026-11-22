"""Run isolated V021-M3 ranking, real HTTP and rule-gate arms using only Train/Dev."""

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import perf_counter
from unittest.mock import patch

import psutil

from m3_r01_baseline import EVIDENCE, sha
from m3_r01_projection import projection, seal_holdout


PINNED_PROTOCOL = "ea3fa4a1cff5be0de57a32ed8addf95e4335bbea4670bff814151a62e44cccf9"
SAFETY = ("GT-0674", "GT-0677", "GT-0678")
PRIORITY = ("GT-0300", "GT-0551", "GT-0004")
PROBES = SAFETY + PRIORITY

spec = importlib.util.spec_from_file_location("v021_m0_upload_replay", EVIDENCE / "m0_r01_replay.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def offline_guard():
    attempts = []

    def audit(event, args):
        try:
            replay.block_external_network(event, args)
        except RuntimeError:
            attempts.append(event)
            raise

    sys.addaudithook(audit)
    return attempts


@contextmanager
def memory_peak(report):
    parent = psutil.Process()
    finished = Event()
    peaks = Counter()

    def sample():
        while not finished.is_set():
            parent_info = parent.memory_info()
            peaks["app_peak_working_set_mib"] = max(peaks["app_peak_working_set_mib"],
                                                   getattr(parent_info, "peak_wset", parent_info.rss) / (1 << 20))
            combined = parent_info.rss
            for child in parent.children():
                try:
                    memory = child.memory_info()
                except psutil.NoSuchProcess:
                    continue
                combined += memory.rss
                peaks["worker_peak_working_set_mib"] = max(peaks["worker_peak_working_set_mib"],
                                                          getattr(memory, "peak_wset", memory.rss) / (1 << 20))
            peaks["combined_sampled_working_set_mib"] = max(peaks["combined_sampled_working_set_mib"],
                                                          combined / (1 << 20))
            finished.wait(0.025)

    sampler = Thread(target=sample, daemon=True)
    sampler.start()
    try:
        yield
    finally:
        finished.set()
        sampler.join()
        report.update(peaks)


def selected_fields(row):
    initial = row["initial_match"]
    return {
        "decision": row["decision_status"],
        "prefilled": initial["actual_prefilled"],
        "tier": initial["tier"], "subtype": initial["subtype"], "source": initial["source"],
        "effective_codes": [b["code"] for b in initial["effective_bindings"]],
        "decision_codes": [b["code"] for b in row["bindings"]],
        "top10": [[c["code"], c["score"]] for c in row["top_candidates"]],
        **{key: initial[key] for key in ("ranking_score", "distinct_margin", "competitor_code",
                                         "uncapped", "ranking_evidence_status", "model_output", "reason_codes")},
    }


def single(client, headers, item):
    started = perf_counter()
    response = client.post("/api/match", headers=headers, json={
        "name": item["name"], "text": item["specification"], "professional": item["professional"],
        "unit": item["unit"], "category": item["supply_category"], "brand": item.get("brand") or "",
    })
    latency = (perf_counter() - started) * 1000
    result = replay.response_json(response)
    return {"ms": latency, "status": result.get("cross_encoder_rerank"),
            "codes": [[c["code"], c["score"]] for c in result["top_candidates"]],
            "matched": result["matched_code"], "is_exact": result["is_exact"]}


def review_probes(client, headers, actor, batch, probes, priority, export_gate, http_error):
    targets = {}
    for row in batch:
        gid = row["original_code"]
        if gid in PROBES:
            probes[gid] = {"upload": selected_fields(row),
                           "fields": [{k: field[k] for k in ("field", "state", "missing_side",
                                                             "safety_critical_unknown", "requested_unverified")}
                                      for field in row["initial_match"]["field_comparisons"]]}
            if gid in SAFETY:
                try:
                    export_gate(row, actor, "v021-replay.xlsx")
                except http_error as error:
                    probes[gid]["export_http"] = error.status_code
                else:
                    probes[gid]["export_http"] = 200
        if row["initial_match"]["tier"] == "A" or gid in PRIORITY:
            targets[row["source_row_id"]] = {**row, "scope_filename": "v021-replay.xlsx",
                                             "bindings": row["initial_match"]["effective_bindings"]}
    if not targets:
        return
    response = replay.response_json(client.post("/api/binding/priority", headers=headers,
                                                json={"rows": list(targets.values())}))
    assert len(response["rows"]) == len(targets)
    for entry in response["rows"]:
        gid = targets[entry["source_row_id"]]["original_code"]
        priority[gid] = {key: entry[key] for key in ("eligible", "reason")}


def benchmark(mode, arm, output, items, labels, attempts, query_sha):
    from m0_r01_benchmark import diagnostic_rows

    with TemporaryDirectory(prefix="v021_m3_", ignore_cleanup_errors=True) as root:
        staged = replay.stage(Path(root) / "isolated", mode, replay.read_json(replay.V017 / "m0_r01_before.json"))
        stages, memory = {}, {}
        with memory_peak(memory), replay.phase(stages, "startup"):
            from fastapi import HTTPException
            from fastapi.testclient import TestClient
            from app import matcher
            from app.auth_manager import get_user_by_username
            from app.server import _gate_export_row, app
            from app.vector_index import index_manager
            materials, _ = replay.read_catalog()

        upload_rows, upload_states, probes, priority = {}, Counter(), {}, {}
        upload_ms = []
        with memory_peak(memory):
            with TestClient(app) as client:
                with replay.phase(stages, "warmup"):
                    index = replay.checked_index(mode, index_manager, materials)
                    headers = replay.configure_actor()
                    actor = get_user_by_username("benchmark")
                    warm = [row for row in items if labels[row["id"]]["split"] == "train"][:5]
                    for item in warm:
                        single(client, headers, item)
                    replay.upload(client, headers, warm)

                print(f"{mode} {arm} ranking", flush=True)
                rank_status = Counter()
                match_original = matcher.match_item

                def observe(*args, **kwargs):
                    result = match_original(*args, **kwargs)
                    status = result.get("cross_encoder_rerank", {"status": "off"})
                    rank_status[(status["status"], status.get("reason", ""))] += 1
                    return result

                with replay.phase(stages, "rank"), patch.object(matcher, "match_item", observe):
                    ranks, _ = diagnostic_rows(items, labels)

                print(f"{mode} {arm} single route", flush=True)
                with replay.phase(stages, "single"):
                    singles = {item["id"]: single(client, headers, item) for item in items}
                print(f"{mode} {arm} upload route", flush=True)
                with replay.phase(stages, "upload"):
                    for start in range(0, len(items), 50):
                        batch, elapsed = replay.upload(client, headers, items[start:start + 50])
                        if len(batch) == 50:
                            upload_ms.append(elapsed)
                        for row in batch:
                            status = row.get("cross_encoder_rerank", {"status": "off_or_non_matcher"})
                            upload_states[(status["status"], status.get("reason", ""))] += 1
                            upload_rows[row["original_code"]] = selected_fields(row)
                        # The upload helper's timer ends before eligibility/export checks.
                        review_probes(client, headers, actor, batch, probes, priority,
                                      _gate_export_row, HTTPException)
                        print(f"{mode} {arm} upload {len(upload_rows)}/{len(items)}", flush=True)

    assert len(upload_ms) == 21
    assert ranks.keys() == singles.keys() == upload_rows.keys() == labels.keys()
    assert sum(entry["semantic_in_pool"] for entry in ranks.values()) == (len(items) if mode == "vector" else 0)
    assert probes.keys() == set(PROBES)
    assert not attempts
    status_counts = lambda counts: {f"{status}:{reason}": count for (status, reason), count in sorted(counts.items())}
    single_ms = [row.pop("ms") for row in singles.values()]
    single_states = Counter((s["status"], s.get("reason", "")) for row in singles.values()
                            if (s := row.pop("status")) is not None)
    if arm == "off":
        assert "app.cross_encoder_reranker" not in sys.modules
    single_stats = replay.dist_ms(single_ms)
    upload_stats = replay.dist_ms(upload_ms)
    peak = max(memory["combined_sampled_working_set_mib"], memory["app_peak_working_set_mib"],
               memory.get("worker_peak_working_set_mib", 0))
    observed = {
        "single_p50_ms": single_stats["p50_ms"], "single_p95_ms": single_stats["p95_ms"],
        "up50_p50_ms": upload_stats["p50_ms"], "up50_p95_ms": upload_stats["p95_ms"], "wset_mib": peak,
    }
    budgets = replay.SIGNED_BUDGETS[mode]
    protocol_ok = (arm == "off" or all(any(status == "applied" for status, _ in counts)
                   and all(status != "degraded" for status, _ in counts)
                   for counts in (rank_status, single_states, upload_states)))
    report = {
        "mode": mode, "arm": arm, "arm_exercised_without_degradation": protocol_ok,
        "input_hashes": staged, "queries_sha256": query_sha, "protocol_sha256": PINNED_PROTOCOL,
        "labels_sha256": hashlib.sha256(json.dumps(labels, sort_keys=True, ensure_ascii=False,
                                                 separators=(",", ":")).encode("utf-8")).hexdigest(),
        "hardware": replay.hardware(), "index": index, "phases": stages, "memory": dict(memory),
        "latency": {"single": single_stats, "upload50": upload_stats},
        "elapsed_ms": {"single": single_ms, "upload50": upload_ms},
        "observed": observed, "budget": budgets, "budget_pass": {k: observed[k] <= budgets[k] for k in budgets},
        "worker_states": {"rank": status_counts(rank_status), "single": status_counts(single_states),
                          "upload": status_counts(upload_states)},
        "blocked_external_attempts": attempts, "sealed_fixture_opens": 0,
        "rows": {gid: {"split": labels[gid]["split"], "rank": ranks[gid], "single": singles[gid],
                       "upload": upload_rows[gid]} for gid in labels},
        "protected_probes": probes, "priority_dispositions": priority,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0 if protocol_ok else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projection", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("keyword", "vector", "rules"))
    parser.add_argument("--arm", choices=("off", "on"), default="off")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists() or sha(EVIDENCE / "m3_r01_protocol_frozen.json") != PINNED_PROTOCOL:
        raise ValueError("Use a new output and the frozen M3 protocol")
    seal_holdout()
    attempts = offline_guard()
    replay.configure_arm("off", None)
    os.environ.update(LISTTRANS_V021_M3_RERANKER=args.arm, LLM_ENABLED="false",
                      LISTTRANS_V020_M2_LLM_DECISION="off", PYTHON_DOTENV_DISABLED="1")
    items, labels = projection(args.projection)
    if args.mode == "rules":
        if args.arm != "off":
            parser.error("The V021 three-rule regression uses OFF")
        import m0_r01_replay as previous
        with patch.object(previous, "read_projection", return_value=(items, labels)):
            return previous.rule_gate("off", None, args.out)
    return benchmark(args.mode, args.arm, args.out, items, labels, attempts, sha(args.projection))


if __name__ == "__main__":
    raise SystemExit(main())
