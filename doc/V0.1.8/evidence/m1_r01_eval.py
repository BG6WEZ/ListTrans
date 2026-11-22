"""V018-M1 paired Train/Dev evaluation: supports M0/M1 independent & combined switches.

Strictly excludes the 160-item holdout test split (reserved for V018-M3).
Runs on fresh isolated roots outside the project with TESTING=1, LLM_ENABLED=false,
and outbound network blocked.
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
V017_EV = ROOT / "doc" / "V0.1.7" / "evidence"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(V017_EV))

from m0_r01_benchmark import (
    block_external_network,
    checked_index,
    configure_actor,
    diagnostic_rows,
    hardware,
    phase,
    send_match,
    send_upload,
    stage,
)
from m0_r01_build_governance import GOLDEN, core, read_catalog, read_json, save_json, sha
from m0_r01_summarize import by_domain, decisions, rank_metrics


def dist_ms(values):
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    vs = sorted(values)
    return {
        "n": len(vs),
        "p50_ms": round(vs[math.ceil(len(vs) * 0.50) - 1], 3),
        "p95_ms": round(vs[math.ceil(len(vs) * 0.95) - 1], 3),
        "max_ms": round(vs[-1], 3),
    }


def summarize_split(items, diagnostics, single_rows, batch_rows, gov_samples):
    def acceptable(item, original):
        return gov_samples[item["id"]]["target_alternatives"][original]

    def acceptable_all(item):
        out = set()
        for orig in item["target_codes"]:
            out.update(gov_samples[item["id"]]["target_alternatives"][orig])
        return out

    def label(item):
        st = gov_samples[item["id"]]["status"]
        if st == "answerable":
            return "answerable"
        if st in ("disabled_or_retired", "no_substitute"):
            return "reject"
        return "pending"

    core_items = [it for it in items if core(it)]
    core_ans = [it for it in core_items if gov_samples[it["id"]]["status"] == "answerable"]
    core_neg = [it for it in core_items if gov_samples[it["id"]]["status"] in ("disabled_or_retired", "no_substitute")]
    core_pending = [it for it in core_items if gov_samples[it["id"]]["status"] == "pending_business_review"]

    rank_all = by_domain(core_ans, lambda rows: rank_metrics(rows, diagnostics, acceptable))

    # Exclusive channel contributions & irrelevant candidate stats on core_ans
    exclusive_ch = Counter()
    m1_channels = {"sparse_name_param", "sparse_alias", "sparse_phrase_split", "sparse_param_fallback"}
    m0_channels = {"exact_code", "structured_model", "structured_recall"}
    pool_sizes = []
    non_target_counts = []
    for it in core_ans:
        row = diagnostics[it["id"]]
        pool_sizes.append(row["pool_size"])
        alts = acceptable_all(it)
        recalled_targets = sum(1 for c in alts if row["targets"].get(c, {}).get("recall_position") is not None)
        non_target_counts.append(max(0, row["pool_size"] - recalled_targets))
        for orig in it["target_codes"]:
            t_alts = acceptable(it, orig)
            ch_set = {c for code in t_alts for c in row["targets"][code]["channels"]}
            if len(ch_set) == 1:
                exclusive_ch[next(iter(ch_set))] += 1
            elif ch_set and ch_set.issubset(m1_channels):
                exclusive_ch["v018_m1_only"] += 1
            elif ch_set and ch_set.issubset(m0_channels):
                exclusive_ch["v018_m0_only"] += 1
            elif ch_set and ch_set.issubset(m0_channels | m1_channels):
                exclusive_ch["v018_m0_m1_only"] += 1

    avg_pool = round(sum(pool_sizes) / len(pool_sizes), 2) if pool_sizes else 0.0
    avg_non_target = round(sum(non_target_counts) / len(non_target_counts), 2) if non_target_counts else 0.0
    max_pool = max(pool_sizes) if pool_sizes else 0

    upload_dec = decisions(core_items, batch_rows, label, acceptable_all) if batch_rows else None
    single_dec = decisions(core_items, single_rows, label, acceptable_all) if single_rows else None

    gt_guard = {}
    for gid in ("GT-0674", "GT-0677", "GT-0678"):
        if batch_rows and gid in batch_rows:
            gt_guard[gid] = batch_rows[gid]["decision_status"]

    return {
        "counts": {
            "total": len(items),
            "core_total": len(core_items),
            "core_answerable": len(core_ans),
            "core_negative": len(core_neg),
            "core_pending": len(core_pending),
        },
        "avg_pool_size": avg_pool,
        "max_pool_size": max_pool,
        "avg_irrelevant_candidates": avg_non_target,
        "rank": rank_all,
        "exclusive_channel_slots": dict(exclusive_ch),
        "upload_decisions": upload_dec,
        "single_decisions": single_dec,
        "gt_guard_status": gt_guard,
    }


def run_one(mode: str, m0_enabled: bool, m1_enabled: bool, include_http: bool, batch_size: int = 50):
    before = read_json(V017_EV / "m0_r01_before.json")
    asset = read_json(ROOT / "tests/fixtures/golden_governance_v1.json")
    all_items = read_json(GOLDEN)["items"]
    td_items = [it for it in all_items if asset["samples"][it["id"]]["split"] in ("train", "dev")]
    assert len(td_items) == 1060, f"Unexpected Train+Dev count: {len(td_items)}"
    assert all(asset["samples"][it["id"]]["split"] != "test" for it in td_items)

    tmp_parent = Path(tempfile.mkdtemp(prefix=f"v018_m1_{mode}_m0{int(m0_enabled)}_m1{int(m1_enabled)}_"))
    iso_root = tmp_parent / "iso"
    try:
        frozen = stage(iso_root, mode, before)
        os.environ["LISTTRANS_V018_M0_RECALL"] = "1" if m0_enabled else "0"
        os.environ["LISTTRANS_V018_M1_RECALL"] = "1" if m1_enabled else "0"
        sys.addaudithook(block_external_network)

        phases = {}
        with phase(phases, "startup_prepare"):
            from fastapi.testclient import TestClient
            from app.server import app
            from app.vector_index import index_manager
            from app.structured_recall import get_structured_index_info, invalidate_structured_index
            from app.sparse_recall import get_sparse_recall_info

            invalidate_structured_index()
            materials, _ = read_catalog()

        single_rows = {}
        batch_rows = {}
        uploads_meta = []

        with TestClient(app) as client:
            with phase(phases, "warmup"):
                idx_info = checked_index(mode, index_manager, materials)
                headers = configure_actor()
                for it in td_items[:5]:
                    send_match(client, headers, it)
                send_upload(client, headers, td_items[:5])

            with phase(phases, "full_rank_replay"):
                diag, eval_sec = diagnostic_rows(td_items, asset["samples"])

            semantic_count = sum(r["semantic_in_pool"] for r in diag.values())
            if semantic_count != (len(td_items) if mode == "vector" else 0):
                raise RuntimeError(f"Unexpected semantic coverage {semantic_count}/{len(td_items)}")

            if include_http:
                with phase(phases, "single_http"):
                    single_rows = {it["id"]: send_match(client, headers, it) for it in td_items}
                with phase(phases, "batch_http"):
                    for start in range(0, len(td_items), batch_size):
                        chunk = td_items[start:start + batch_size]
                        rows, meta = send_upload(client, headers, chunk)
                        batch_rows.update(rows)
                        uploads_meta.append(meta)

        train_items = [it for it in td_items if asset["samples"][it["id"]]["split"] == "train"]
        dev_items = [it for it in td_items if asset["samples"][it["id"]]["split"] == "dev"]

        single_ms = [v["ms"] for v in single_rows.values()] if single_rows else []
        full_batches_ms = [u["ms"] for u in uploads_meta if u["rows"] == batch_size] if uploads_meta else []

        return {
            "mode": mode,
            "v018_m0_enabled": m0_enabled,
            "v018_m1_enabled": m1_enabled,
            "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "hardware": hardware(),
            "golden_sha256": sha(GOLDEN),
            "governance_sha256": sha(ROOT / "tests/fixtures/golden_governance_v1.json"),
            "catalog_sha256": frozen["catalog"],
            "vector_index": idx_info,
            "structured_index": get_structured_index_info(),
            "sparse_recall": get_sparse_recall_info(),
            "semantic_rows": semantic_count,
            "evaluation_seconds": round(eval_sec, 3),
            "phases": phases,
            "latency": {
                "single_http": dist_ms(single_ms),
                "batch_http_50rows": dist_ms(full_batches_ms),
            },
            "splits": {
                "train_dev": summarize_split(td_items, diag, single_rows, batch_rows, asset["samples"]),
                "train": summarize_split(train_items, diag, single_rows, batch_rows, asset["samples"]),
                "dev": summarize_split(dev_items, diag, single_rows, batch_rows, asset["samples"]),
            },
        }
    finally:
        shutil.rmtree(tmp_parent, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("keyword", "vector"), required=True)
    parser.add_argument("--m0", choices=("0", "1"), default="0")
    parser.add_argument("--m1", choices=("0", "1"), required=True)
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = run_one(args.mode, args.m0 == "1", args.m1 == "1", include_http=args.http)
    save_json(args.out, report)
    td = report["splits"]["train_dev"]
    ra = td["rank"]["all"]
    print(
        f"[{args.mode} m0={args.m0} m1={args.m1}] Train+Dev core_ans={ra['rows']} "
        f"R@100={ra['candidate_recall@100']['n']}/{ra['candidate_recall@100']['d']} "
        f"R@200={ra['candidate_recall@200']['n']}/{ra['candidate_recall@200']['d']} "
        f"Hit@1={ra['hit@1']['n']}/{ra['hit@1']['d']} "
        f"Hit@3={ra['hit@3']['n']}/{ra['hit@3']['d']} "
        f"MRR10={ra['mrr_at_10']} avg_pool={td['avg_pool_size']} eval_s={report['evaluation_seconds']}"
    )


if __name__ == "__main__":
    main()
