"""V018-M3 R01 Dev-stage candidate selection, paired re-measurement, and error analysis.

STRICT SPLIT DISCIPLINE:
- Operates EXCLUSIVELY on Train+Dev (1,060 rows: Train 842 / 529 core answerable;
  Dev 218 / 122 core answerable; 115 core negatives).
- Test 160 rows are strictly excluded and asserted absent at load time.
- Each configuration runs in its own fresh subprocess with a fresh native temp
  isolation root outside the repository, `TESTING=1`, `LLM_ENABLED=false`, and
  outbound network blocked, so latency and peak working set are measured cleanly
  under identical quiet-load conditions.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
V017_EV = ROOT / "doc" / "V0.1.7" / "evidence"
V018_EV = ROOT / "doc" / "V0.1.8" / "evidence"
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

SIGNED_BUDGETS = {
    "keyword": {
        "single_p50_ms": 30.0,
        "single_p95_ms": 300.0,
        "up50_p50_ms": 1750.0,
        "up50_p95_ms": 10000.0,
        "wset_mib": 768.0,
    },
    "vector": {
        "single_p50_ms": 90.0,
        "single_p95_ms": 375.0,
        "up50_p50_ms": 6000.0,
        "up50_p95_ms": 12500.0,
        "wset_mib": 2048.0,
    },
}

CANDIDATE_SPECS = [
    {"id": "B0_KW_OFF", "mode": "keyword", "m0": "0", "m1": "0", "m2": "0", "top_k": 50, "fusion": "union", "cap": 0, "http": True},
    {"id": "B0_VEC_OFF", "mode": "vector", "m0": "0", "m1": "0", "m2": "0", "top_k": 50, "fusion": "union", "cap": 0, "http": True},
    {"id": "A3_VEC", "mode": "vector", "m0": "0", "m1": "0", "m2": "1", "top_k": 50, "fusion": "rrf", "cap": 200, "http": True},
    {"id": "A1_VEC", "mode": "vector", "m0": "0", "m1": "0", "m2": "1", "top_k": 100, "fusion": "union", "cap": 0, "http": True},
    {"id": "M1_KW", "mode": "keyword", "m0": "0", "m1": "1", "m2": "0", "top_k": 50, "fusion": "union", "cap": 0, "http": True},
    {"id": "M0M1_KW", "mode": "keyword", "m0": "1", "m1": "1", "m2": "0", "top_k": 50, "fusion": "union", "cap": 0, "http": True},
    {"id": "M1_VEC", "mode": "vector", "m0": "0", "m1": "1", "m2": "0", "top_k": 50, "fusion": "union", "cap": 0, "http": True},
    {"id": "C2_VEC", "mode": "vector", "m0": "1", "m1": "1", "m2": "1", "top_k": 100, "fusion": "rrf", "cap": 200, "http": True},
    # Degradation parity checks (OBS-COORD-M2-02): M2 RRF+cap enabled while vector unavailable
    {"id": "A3_KW_DEG", "mode": "keyword", "m0": "0", "m1": "0", "m2": "1", "top_k": 50, "fusion": "rrf", "cap": 200, "http": False},
    {"id": "C2_KW_DEG", "mode": "keyword", "m0": "1", "m1": "1", "m2": "1", "top_k": 100, "fusion": "rrf", "cap": 200, "http": False},
]


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


def analyze_misses(items, diagnostics, gov_samples):
    """Classify every core answerable target slot into recall/truncation/ranking outcomes."""
    buckets = Counter()
    missed_examples = []
    truncated_gt200 = []
    ranked_miss_examples = []
    fallback_triggered = []

    for it in items:
        if not core(it) or gov_samples[it["id"]]["status"] != "answerable":
            continue
        row = diagnostics[it["id"]]
        pool_size = row["pool_size"]
        for orig in it["target_codes"]:
            alts = gov_samples[it["id"]]["target_alternatives"][orig]
            targets = [row["targets"][c] for c in alts]
            positions = [t["recall_position"] for t in targets if t["recall_position"] is not None]
            ranks = [t["rank"] for t in targets if t["rank"] is not None and t["state"] != "hard_blocked"]
            channels = sorted({ch for t in targets for ch in t["channels"]})
            states = [t["state"] for t in targets]

            if "fallback" in channels:
                fallback_triggered.append({
                    "id": it["id"],
                    "domain": it["professional"],
                    "name": it["name"],
                    "recall_pos": min(positions) if positions else None,
                    "rank": min(ranks) if ranks else None,
                    "channels": channels,
                })

            if not positions:
                buckets["not_recalled"] += 1
                missed_examples.append({
                    "id": it["id"],
                    "domain": it["professional"],
                    "split": gov_samples[it["id"]]["split"],
                    "name": it["name"],
                    "pool_size": pool_size,
                    "target_codes": alts,
                })
            else:
                best_pos = min(positions)
                if best_pos > 200:
                    buckets["recalled_gt200_truncated"] += 1
                    truncated_gt200.append({
                        "id": it["id"],
                        "domain": it["professional"],
                        "split": gov_samples[it["id"]]["split"],
                        "name": it["name"],
                        "recall_pos": best_pos,
                        "pool_size": pool_size,
                        "channels": channels,
                    })
                elif best_pos > 100:
                    buckets["recalled_101_to_200"] += 1
                else:
                    buckets["recalled_le100"] += 1

                if not ranks:
                    buckets["recalled_but_hard_blocked"] += 1
                else:
                    best_rank = min(ranks)
                    if best_rank == 1:
                        buckets["rank_hit1"] += 1
                    elif best_rank <= 3:
                        buckets["rank_2_to_3"] += 1
                    elif best_rank <= 10:
                        buckets["rank_4_to_10"] += 1
                    else:
                        buckets["recalled_but_ranked_gt10"] += 1
                        if best_pos <= 200:
                            ranked_miss_examples.append({
                                "id": it["id"],
                                "domain": it["professional"],
                                "split": gov_samples[it["id"]]["split"],
                                "name": it["name"],
                                "recall_pos": best_pos,
                                "rank": best_rank,
                                "channels": channels,
                            })

    return {
        "buckets": dict(buckets),
        "not_recalled_by_domain": dict(Counter(x["domain"] for x in missed_examples)),
        "truncated_gt200_by_domain": dict(Counter(x["domain"] for x in truncated_gt200)),
        "ranked_gt10_by_domain": dict(Counter(x["domain"] for x in ranked_miss_examples)),
        "fallback_target_hits": fallback_triggered,
        "not_recalled_sample_ids": [x["id"] for x in missed_examples[:30]],
        "truncated_gt200_items": truncated_gt200,
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

    rank_all = by_domain(core_ans, lambda rows: rank_metrics(rows, diagnostics, acceptable, exclude_blocked=True))
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

    # Also evaluate 1toN kit confirmed all-components within this split
    kit_items = [it for it in items if it["label_type"] == "1toN"]
    confirmed_kit = [
        it for it in kit_items
        if not it["ambiguous"] and not it["qc_suspect"]
        and len(gov_samples[it["id"]]["target_alternatives"]) == len(it["target_codes"])
    ]
    kit_metrics = (
        rank_metrics(confirmed_kit, diagnostics, acceptable, exclude_blocked=True, require_all=True)
        if confirmed_kit else None
    )

    upload_dec = decisions(core_items, batch_rows, label, acceptable_all) if batch_rows else None
    upload_ans_dec = decisions(core_ans, batch_rows, label, acceptable_all) if batch_rows else None
    upload_neg_dec = decisions(core_neg, batch_rows, label, acceptable_all) if batch_rows else None
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
            "confirmed_kit_1toN": len(confirmed_kit),
        },
        "avg_pool_size": round(sum(pool_sizes) / len(pool_sizes), 2) if pool_sizes else 0.0,
        "max_pool_size": max(pool_sizes) if pool_sizes else 0,
        "rank": rank_all,
        "kit_confirmed_all_components": kit_metrics,
        "exclusive_channel_slots": dict(exclusive_ch),
        "upload_decisions": upload_dec,
        "upload_answerable_decisions": upload_ans_dec,
        "upload_negative_decisions": upload_neg_dec,
        "single_decisions": single_dec,
        "gt_guard_status": gt_guard,
        "miss_analysis": analyze_misses(items, diagnostics, gov_samples),
    }


def extract_per_sample_compact(td_items, diagnostics, gov_samples):
    """Store compact per-sample target recall_position/rank/channels for paired diffing."""
    out = {}
    for it in td_items:
        gid = it["id"]
        st = gov_samples[gid]["status"]
        if not core(it) or st != "answerable":
            continue
        row = diagnostics[gid]
        orig = it["target_codes"][0]
        alts = gov_samples[gid]["target_alternatives"][orig]
        targets = [row["targets"][c] for c in alts]
        pos_list = [t["recall_position"] for t in targets if t["recall_position"] is not None]
        rank_list = [t["rank"] for t in targets if t["rank"] is not None and t["state"] != "hard_blocked"]
        ch_list = sorted({c for t in targets for c in t["channels"]})
        out[gid] = {
            "split": gov_samples[gid]["split"],
            "domain": it["professional"],
            "name": it["name"],
            "pool_size": row["pool_size"],
            "recall_pos": min(pos_list) if pos_list else None,
            "rank": min(rank_list) if rank_list else None,
            "channels": ch_list,
        }
    return out


def run_single_worker(spec: dict, out_path: Path):
    before = read_json(V017_EV / "m0_r01_before.json")
    asset = read_json(ROOT / "tests/fixtures/golden_governance_v1.json")
    all_items = read_json(GOLDEN)["items"]
    td_items = [it for it in all_items if asset["samples"][it["id"]]["split"] in ("train", "dev")]
    assert len(td_items) == 1060, f"Expected 1060 Train+Dev rows, got {len(td_items)}"
    assert all(asset["samples"][it["id"]]["split"] != "test" for it in td_items), "TEST SPLIT LEAK DETECTED"

    tmp_parent = Path(tempfile.mkdtemp(prefix=f"v018_m3_{spec['id']}_"))
    iso_root = tmp_parent / "iso"
    try:
        frozen = stage(iso_root, spec["mode"], before)
        os.environ["LISTTRANS_V018_M0_RECALL"] = spec["m0"]
        os.environ["LISTTRANS_V018_M1_RECALL"] = spec["m1"]
        os.environ["LISTTRANS_V018_M2_RECALL"] = spec["m2"]
        os.environ["LISTTRANS_V018_M2_TEXT_MODE"] = "legacy"
        os.environ["LISTTRANS_V018_M2_VECTOR_TOPK"] = str(spec["top_k"])
        os.environ["LISTTRANS_V018_M2_FUSION_MODE"] = spec["fusion"]
        os.environ["LISTTRANS_V018_M2_RRF_K"] = "60"
        os.environ["LISTTRANS_V018_M2_CANDIDATE_CAP"] = str(spec["cap"])

        sys.addaudithook(block_external_network)

        phases = {}
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
                idx_info = checked_index(spec["mode"], index_manager, materials)
                headers = configure_actor()
                for it in td_items[:5]:
                    send_match(client, headers, it)
                send_upload(client, headers, td_items[:5])

            with phase(phases, "full_rank_replay"):
                diag, eval_sec = diagnostic_rows(td_items, asset["samples"])

            semantic_count = sum(r["semantic_in_pool"] for r in diag.values())
            if semantic_count != (len(td_items) if spec["mode"] == "vector" else 0):
                raise RuntimeError(f"Unexpected semantic coverage {semantic_count}/{len(td_items)}")

            if spec["http"]:
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

        single_dist = dist_ms(single_ms)
        batch_dist = dist_ms(full_batches_ms)
        lim = SIGNED_BUDGETS[spec["mode"]]
        budget_check = {
            "wset_ok": peak_wset <= lim["wset_mib"],
            "single_p50_ok": (single_dist["p50_ms"] <= lim["single_p50_ms"]) if single_dist["p50_ms"] is not None else None,
            "single_p95_ok": (single_dist["p95_ms"] <= lim["single_p95_ms"]) if single_dist["p95_ms"] is not None else None,
            "up50_p50_ok": (batch_dist["p50_ms"] <= lim["up50_p50_ms"]) if batch_dist["p50_ms"] is not None else None,
            "up50_p95_ok": (batch_dist["p95_ms"] <= lim["up50_p95_ms"]) if batch_dist["p95_ms"] is not None else None,
        }

        rep = {
            "id": spec["id"],
            "mode": spec["mode"],
            "config": {
                "m0": spec["m0"] == "1",
                "m1": spec["m1"] == "1",
                "m2": spec["m2"] == "1",
                "text_mode": "legacy",
                "top_k": spec["top_k"],
                "fusion": spec["fusion"],
                "rrf_k": 60,
                "cap": spec["cap"],
                "http": spec["http"],
            },
            "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "hardware": hardware(),
            "golden_sha256": sha(GOLDEN),
            "governance_sha256": sha(ROOT / "tests/fixtures/golden_governance_v1.json"),
            "catalog_sha256": frozen["catalog"],
            "vector_index": idx_info,
            "model_metadata": model_meta,
            "semantic_rows": semantic_count,
            "evaluation_seconds": round(eval_sec, 3),
            "phases": phases,
            "peak_wset_mib": peak_wset,
            "latency": {
                "single_http": single_dist,
                "batch_http_50rows": batch_dist,
            },
            "budget_check": budget_check,
            "splits": {
                "train_dev": summarize_split(td_items, diag, single_rows, batch_rows, asset["samples"]),
                "train": summarize_split(train_items, diag, single_rows, batch_rows, asset["samples"]),
                "dev": summarize_split(dev_items, diag, single_rows, batch_rows, asset["samples"]),
            },
            "per_sample_ans": extract_per_sample_compact(td_items, diag, asset["samples"]),
        }
        save_json(out_path, rep)
        td = rep["splits"]["train_dev"]["rank"]["all"]
        dv = rep["splits"]["dev"]["rank"]["all"]
        print(
            f"[{spec['id']}] TD R100={td['candidate_recall@100']['n']} R200={td['candidate_recall@200']['n']} "
            f"H1={td['hit@1']['n']} H3={td['hit@3']['n']} MRR10={td['mrr_at_10']} | "
            f"Dev R100={dv['candidate_recall@100']['n']} R200={dv['candidate_recall@200']['n']} "
            f"H1={dv['hit@1']['n']} H3={dv['hit@3']['n']} | "
            f"WSet={peak_wset}MiB P50={single_dist['p50_ms']}ms P95={single_dist['p95_ms']}ms "
            f"UpP50={batch_dist['p50_ms']}ms UpP95={batch_dist['p95_ms']}ms",
            flush=True,
        )
    finally:
        shutil.rmtree(tmp_parent, ignore_errors=True)


def build_paired_diff(base_samples: dict, cand_samples: dict) -> dict:
    r100_gained, r100_lost = [], []
    r200_gained, r200_lost = [], []
    h1_gained, h1_lost = [], []
    for gid, b in base_samples.items():
        c = cand_samples[gid]
        b100 = b["recall_pos"] is not None and b["recall_pos"] <= 100
        c100 = c["recall_pos"] is not None and c["recall_pos"] <= 100
        b200 = b["recall_pos"] is not None and b["recall_pos"] <= 200
        c200 = c["recall_pos"] is not None and c["recall_pos"] <= 200
        bh1 = b["rank"] == 1
        ch1 = c["rank"] == 1
        rec = {
            "id": gid,
            "split": b["split"],
            "domain": b["domain"],
            "name": b["name"],
            "base_pos": b["recall_pos"],
            "cand_pos": c["recall_pos"],
            "base_pool": b["pool_size"],
            "cand_pool": c["pool_size"],
            "base_rank": b["rank"],
            "cand_rank": c["rank"],
            "base_channels": b["channels"],
            "cand_channels": c["channels"],
        }
        if c100 and not b100:
            r100_gained.append(rec)
        elif b100 and not c100:
            r100_lost.append(rec)
        if c200 and not b200:
            r200_gained.append(rec)
        elif b200 and not c200:
            r200_lost.append(rec)
        if ch1 and not bh1:
            h1_gained.append(rec)
        elif bh1 and not ch1:
            h1_lost.append(rec)
    return {
        "r100_gained_count": len(r100_gained),
        "r100_lost_count": len(r100_lost),
        "r200_gained_count": len(r200_gained),
        "r200_lost_count": len(r200_lost),
        "h1_gained_count": len(h1_gained),
        "h1_lost_count": len(h1_lost),
        "r200_lost_items": r200_lost,
        "r200_gained_sample_ids": [x["id"] for x in r200_gained[:20]],
        "h1_lost_by_domain": dict(Counter(x["domain"] for x in h1_lost)),
        "h1_lost_sample_items": h1_lost[:15],
    }


def run_all(out_summary: Path, selected_ids=None):
    tmp_dir = Path(tempfile.mkdtemp(prefix="v018_m3_dev_runs_"))
    try:
        specs = CANDIDATE_SPECS
        if selected_ids:
            wanted = set(selected_ids)
            specs = [s for s in CANDIDATE_SPECS if s["id"] in wanted]
        raw_results = {}
        for spec in specs:
            out_file = tmp_dir / f"{spec['id']}.json"
            cmd = [
                sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()),
                "--worker", spec["id"], "--out", str(out_file),
            ]
            proc = subprocess.run(cmd, cwd=str(ROOT), check=True)
            assert proc.returncode == 0
            raw_results[spec["id"]] = read_json(out_file)

        # Build concise summary with paired diffs and gate checks
        v17 = read_json(V017_EV / "m3_r01_metrics.json")
        v17_td = {}
        for mode in ("keyword", "vector"):
            ms = v17[mode]["portfolios"]["governed_multi_split"]
            tr = ms["train"]["answerable"]["with_equivalence"]["all"]
            dv = ms["dev"]["answerable"]["with_equivalence"]["all"]
            tr_rej = ms["train"]["reject"]["http"]["batch"]["all"]["correct_rejection_rate"]
            dv_rej = ms["dev"]["reject"]["http"]["batch"]["all"]["correct_rejection_rate"]
            v17_td[mode] = {
                "train_dev": {
                    "r100": tr["candidate_recall@100"]["n"] + dv["candidate_recall@100"]["n"],
                    "r200": tr["candidate_recall@200"]["n"] + dv["candidate_recall@200"]["n"],
                    "hit1": tr["hit@1"]["n"] + dv["hit@1"]["n"],
                    "hit3": tr["hit@3"]["n"] + dv["hit@3"]["n"],
                    "neg_rej": tr_rej["n"] + dv_rej["n"],
                },
                "dev": {
                    "r100": dv["candidate_recall@100"]["n"],
                    "r200": dv["candidate_recall@200"]["n"],
                    "hit1": dv["hit@1"]["n"],
                    "hit3": dv["hit@3"]["n"],
                    "mrr10": dv["mrr_at_10"],
                    "neg_rej": dv_rej["n"],
                },
            }

        summary_runs = {}
        for cid, rep in raw_results.items():
            base_id = "B0_VEC_OFF" if rep["mode"] == "vector" else "B0_KW_OFF"
            base_rep = raw_results.get(base_id)
            paired = None
            if base_rep and cid != base_id:
                paired = build_paired_diff(base_rep["per_sample_ans"], rep["per_sample_ans"])

            # Strip per_sample_ans from summary JSON to keep it compact & readable
            compact = {k: v for k, v in rep.items() if k != "per_sample_ans"}
            compact["paired_vs_same_mode_off"] = paired
            summary_runs[cid] = compact

        # Check degradation parity (OBS-COORD-M2-02)
        deg_parity = {}
        if "B0_KW_OFF" in raw_results and "A3_KW_DEG" in raw_results:
            deg_parity["A3_KW_DEG_equals_B0_KW_OFF"] = (
                raw_results["A3_KW_DEG"]["per_sample_ans"] == raw_results["B0_KW_OFF"]["per_sample_ans"]
            )
        if "M0M1_KW" in raw_results and "C2_KW_DEG" in raw_results:
            deg_parity["C2_KW_DEG_equals_M0M1_KW"] = (
                raw_results["C2_KW_DEG"]["per_sample_ans"] == raw_results["M0M1_KW"]["per_sample_ans"]
            )

        # Compare fallback targets under B0_KW_OFF vs M1_KW (OBS-A-M2-01)
        fallback_paired = []
        if "B0_KW_OFF" in raw_results and "M1_KW" in raw_results:
            fb_hits = raw_results["B0_KW_OFF"]["splits"]["train_dev"]["miss_analysis"]["fallback_target_hits"]
            m1_ps = raw_results["M1_KW"]["per_sample_ans"]
            for h in fb_hits:
                m1_row = m1_ps.get(h["id"])
                fallback_paired.append({
                    **h,
                    "m1_recall_pos": m1_row["recall_pos"] if m1_row else None,
                    "m1_rank": m1_row["rank"] if m1_row else None,
                    "m1_channels": m1_row["channels"] if m1_row else [],
                })

        final_doc = {
            "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "split_policy": "Train+Dev only (1,060 rows: Train 842 / 529 core ans; Dev 218 / 122 core ans; Test 160 strictly excluded)",
            "signed_budgets_v017": SIGNED_BUDGETS,
            "v017_same_split_reference": v17_td,
            "degradation_parity_checks": deg_parity,
            "obs_a_m2_01_real_fallback_vs_m1": fallback_paired,
            "runs": summary_runs,
        }
        save_json(out_summary, final_doc)
        print(f"Saved Dev selection summary to {out_summary}", flush=True)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--worker", default="", help="Run a single candidate ID in this isolated process")
    p.add_argument("--only", nargs="*", default=None, help="Subset of candidate IDs to run in orchestrator mode")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.worker:
        spec = next(s for s in CANDIDATE_SPECS if s["id"] == args.worker)
        run_single_worker(spec, args.out)
    else:
        run_all(args.out, selected_ids=args.only)


if __name__ == "__main__":
    main()
