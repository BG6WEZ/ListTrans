"""V018-M2 Train/Dev ablation & HTTP performance evaluator (Test 160 strictly excluded).

Supports isolated evaluation of:
- Vector encoding text: legacy-v1 vs v018-m2-canon-v1
- Vector TopK: 50 / 100 / 200
- Candidate merge / fusion: union vs rrf (with configurable rrf_k & candidate_cap)
- Combination with M0/M1 and keyword-only degradation path
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
import tempfile
import time
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
    exclusive_ch = Counter()
    pool_sizes = []
    for it in core_ans:
        row = diagnostics[it["id"]]
        pool_sizes.append(row["pool_size"])
        for orig in it["target_codes"]:
            t_alts = acceptable(it, orig)
            ch_set = {c for code in t_alts for c in row["targets"][code]["channels"]}
            if len(ch_set) == 1:
                exclusive_ch[next(iter(ch_set))] += 1

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
        "avg_pool_size": round(sum(pool_sizes) / len(pool_sizes), 2) if pool_sizes else 0.0,
        "max_pool_size": max(pool_sizes) if pool_sizes else 0,
        "rank": rank_all,
        "exclusive_channel_slots": dict(exclusive_ch),
        "upload_decisions": upload_dec,
        "single_decisions": single_dec,
        "gt_guard_status": gt_guard,
    }


def run_config(args):
    before = read_json(V017_EV / "m0_r01_before.json")
    asset = read_json(ROOT / "tests/fixtures/golden_governance_v1.json")
    all_items = read_json(GOLDEN)["items"]
    td_items = [it for it in all_items if asset["samples"][it["id"]]["split"] in ("train", "dev")]
    assert len(td_items) == 1060
    assert all(asset["samples"][it["id"]]["split"] != "test" for it in td_items)

    tmp_parent = Path(tempfile.mkdtemp(prefix=f"v018_m2_{args.name}_"))
    iso_root = tmp_parent / "iso"
    try:
        frozen = stage(iso_root, args.mode, before)
        os.environ["LISTTRANS_V018_M0_RECALL"] = args.m0
        os.environ["LISTTRANS_V018_M1_RECALL"] = args.m1
        os.environ["LISTTRANS_V018_M2_RECALL"] = args.m2
        os.environ["LISTTRANS_V018_M2_TEXT_MODE"] = args.text_mode
        os.environ["LISTTRANS_V018_M2_VECTOR_TOPK"] = str(args.top_k)
        os.environ["LISTTRANS_V018_M2_FUSION_MODE"] = args.fusion
        os.environ["LISTTRANS_V018_M2_RRF_K"] = str(args.rrf_k)
        os.environ["LISTTRANS_V018_M2_CANDIDATE_CAP"] = str(args.cap)

        # If a pre-built isolated canonical cache dir is provided, copy it into iso VECTOR_CACHE_DIR/candidates/
        if args.canon_cache_dir and args.text_mode == "canonical" and args.mode == "vector":
            src_cand = Path(args.canon_cache_dir)
            if src_cand.is_dir():
                dst_cand = Path(os.environ["VECTOR_CACHE_DIR"]) / "candidates" / "v018-m2-canon-v1"
                dst_cand.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src_cand, dst_cand, dirs_exist_ok=True)

        sys.addaudithook(block_external_network)

        phases = {}
        canon_build_sec = None
        with phase(phases, "startup_prepare"):
            from fastapi.testclient import TestClient
            from app.server import app
            from app.vector_index import index_manager
            from app.structured_recall import invalidate_structured_index

            invalidate_structured_index()
            materials, _ = read_catalog()

        single_rows = {}
        batch_rows = {}
        uploads_meta = []

        with TestClient(app) as client:
            with phase(phases, "warmup"):
                idx_info = checked_index(args.mode, index_manager, materials)
                if args.mode == "vector" and args.m2 == "1" and args.text_mode == "canonical":
                    t0 = time.perf_counter()
                    snap = index_manager.load_or_build_candidate_index(text_mode="canonical", persist=True)
                    canon_build_sec = round(time.perf_counter() - t0, 3)
                    if snap is None or snap.embeddings is None:
                        raise RuntimeError("Canonical candidate vector index build failed")
                    # Export to shared isolated dir if requested so subsequent ablations reuse it
                    if args.save_canon_cache_dir:
                        src_dir = Path(index_manager.get_candidate_cache_dir("v018-m2-canon-v1"))
                        dst_dir = Path(args.save_canon_cache_dir)
                        if dst_dir.exists():
                            shutil.rmtree(dst_dir)
                        shutil.copytree(src_dir, dst_dir)
                headers = configure_actor()
                for it in td_items[:5]:
                    send_match(client, headers, it)
                send_upload(client, headers, td_items[:5])

            with phase(phases, "full_rank_replay"):
                diag, eval_sec = diagnostic_rows(td_items, asset["samples"])

            semantic_count = sum(r["semantic_in_pool"] for r in diag.values())
            if semantic_count != (len(td_items) if args.mode == "vector" else 0):
                raise RuntimeError(f"Unexpected semantic coverage {semantic_count}/{len(td_items)}")

            if args.http:
                with phase(phases, "single_http"):
                    single_rows = {it["id"]: send_match(client, headers, it) for it in td_items}
                with phase(phases, "batch_http"):
                    for start in range(0, len(td_items), 50):
                        chunk = td_items[start:start + 50]
                        rows, meta = send_upload(client, headers, chunk)
                        batch_rows.update(rows)
                        uploads_meta.append(meta)

        model_meta = index_manager.get_model_metadata()
        train_items = [it for it in td_items if asset["samples"][it["id"]]["split"] == "train"]
        dev_items = [it for it in td_items if asset["samples"][it["id"]]["split"] == "dev"]

        single_ms = [v["ms"] for v in single_rows.values()] if single_rows else []
        full_batches_ms = [u["ms"] for u in uploads_meta if u["rows"] == 50] if uploads_meta else []
        peak_wset = round(max(v["process_peak_working_set_mib"] for v in phases.values()), 2)

        return {
            "name": args.name,
            "mode": args.mode,
            "config": {
                "m0": args.m0 == "1",
                "m1": args.m1 == "1",
                "m2": args.m2 == "1",
                "text_mode": args.text_mode,
                "top_k": args.top_k,
                "fusion": args.fusion,
                "rrf_k": args.rrf_k,
                "cap": args.cap,
            },
            "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "hardware": hardware(),
            "golden_sha256": sha(GOLDEN),
            "governance_sha256": sha(ROOT / "tests/fixtures/golden_governance_v1.json"),
            "catalog_sha256": frozen["catalog"],
            "vector_index": idx_info,
            "model_metadata": model_meta,
            "canonical_index_load_or_build_seconds": canon_build_sec,
            "semantic_rows": semantic_count,
            "evaluation_seconds": round(eval_sec, 3),
            "phases": phases,
            "peak_wset_mib": peak_wset,
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", required=True)
    p.add_argument("--mode", choices=("keyword", "vector"), default="vector")
    p.add_argument("--m0", choices=("0", "1"), default="0")
    p.add_argument("--m1", choices=("0", "1"), default="0")
    p.add_argument("--m2", choices=("0", "1"), required=True)
    p.add_argument("--text-mode", choices=("legacy", "canonical"), default="legacy")
    p.add_argument("--top-k", type=int, default=50)
    p.add_argument("--fusion", choices=("union", "rrf"), default="union")
    p.add_argument("--rrf-k", type=int, default=60)
    p.add_argument("--cap", type=int, default=0)
    p.add_argument("--http", action="store_true")
    p.add_argument("--canon-cache-dir", default="")
    p.add_argument("--save-canon-cache-dir", default="")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    rep = run_config(args)
    save_json(args.out, rep)
    td = rep["splits"]["train_dev"]
    ra = td["rank"]["all"]
    print(
        f"[{args.name}] R100={ra['candidate_recall@100']['n']}/{ra['candidate_recall@100']['d']} "
        f"R200={ra['candidate_recall@200']['n']}/{ra['candidate_recall@200']['d']} "
        f"H1={ra['hit@1']['n']} H3={ra['hit@3']['n']} MRR10={ra['mrr_at_10']} "
        f"pool={td['avg_pool_size']} wset={rep['peak_wset_mib']}MiB eval_s={rep['evaluation_seconds']}"
    )


if __name__ == "__main__":
    main()
