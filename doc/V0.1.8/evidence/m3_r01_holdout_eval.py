"""V018-M3 R01 One-Shot Holdout Evaluation (post-freeze ONLY).

Enforces the cryptographic freeze chain:
1. Reads `m3_r01_candidate_freeze.json` and verifies all frozen code/test SHA256
   digests match disk before executing holdout.
2. Executes the single frozen submission configuration (`M0=0, M1=0, M2=0`) ONCE
   for Keyword mode and ONCE for Offline BGE Vector mode in fresh native temp
   isolation roots outside the repository (`TESTING=1`, `LLM_ENABLED=false`,
   outbound network blocked).
3. Computes exact V0.1.7-aligned Test 160 holdout metrics, Full 1,220 governed
   metrics, Wilson 95% CIs, paired deltas vs V0.1.7 signed baseline, per-domain
   and long-tail results, latency/memory budget checks, and miss root causes.
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
from collections import Counter
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
from m0_r01_summarize import summarize

SIGNED_V017_GATES = {
    "keyword": {
        "r100": 399, "r200": 426, "hit1": 180, "hit3": 254,
        "mrr_at_10": 0.309329, "mrr_full": 0.314174,
        "wrong_auto_binding_max": 0,
        "neg_explicit_rejection_min": 43,
        "wrong_proposed_ans_max": 256,
        "wrong_proposed_neg_max": 87,
        "kit_confirmed_top10_min": 3,
        "single_p50_ms": 30.0, "single_p95_ms": 300.0,
        "up50_p50_ms": 1750.0, "up50_p95_ms": 10000.0,
        "wset_mib": 768.0,
        "domains": {
            "强电": (81, 82, 45),
            "暖通": (62, 79, 33),
            "装饰": (80, 83, 26),
            "医用气体": (72, 74, 23),
            "弱电": (70, 74, 37),
            "给排水": (18, 18, 7),
        },
    },
    "vector": {
        "r100": 594, "r200": 643, "hit1": 183, "hit3": 288,
        "mrr_at_10": 0.344091, "mrr_full": 0.355303,
        "wrong_auto_binding_max": 0,
        "neg_explicit_rejection_min": 42,
        "wrong_proposed_ans_max": 368,
        "wrong_proposed_neg_max": 109,
        "kit_confirmed_top10_min": 3,
        "single_p50_ms": 90.0, "single_p95_ms": 375.0,
        "up50_p50_ms": 6000.0, "up50_p95_ms": 12500.0,
        "wset_mib": 2048.0,
        "domains": {
            "强电": (166, 171, 50),
            "暖通": (69, 90, 29),
            "装饰": (114, 117, 29),
            "医用气体": (92, 94, 22),
            "弱电": (83, 98, 25),
            "给排水": (50, 50, 19),
        },
    },
}


def wilson_ci95(k: int, n: int):
    if n <= 0:
        return None
    z = 1.959963984540054
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)) / denom
    return [round(max(0.0, center - half) * 100.0, 2), round(min(1.0, center + half) * 100.0, 2)]


def verify_freeze_chain(freeze_path: Path) -> dict:
    freeze = read_json(freeze_path)
    mismatches = []
    for rel, expected in freeze["frozen_code_and_test_sha256"].items():
        actual = sha(ROOT / rel)
        if actual != expected:
            mismatches.append({"path": rel, "expected": expected, "actual": actual})
    if mismatches:
        raise RuntimeError(f"Freeze chain violated before holdout: {mismatches}")
    return freeze


def run_mode_worker(mode: str, freeze_path: Path, out_raw: Path):
    freeze = verify_freeze_chain(freeze_path)
    cfg = freeze["frozen_submission_config"]
    before = read_json(V017_EV / "m0_r01_before.json")
    asset = read_json(ROOT / "tests/fixtures/golden_governance_v1.json")
    items = read_json(GOLDEN)["items"]
    assert len(items) == 1220, f"Expected 1220 items, got {len(items)}"

    tmp_parent = Path(tempfile.mkdtemp(prefix=f"v018_m3_holdout_{mode}_"))
    iso_root = tmp_parent / "iso"
    try:
        frozen = stage(iso_root, mode, before)
        for k, v in cfg.items():
            if k.startswith("LISTTRANS_"):
                os.environ[k] = str(v)

        sys.addaudithook(block_external_network)

        phases = {}
        with phase(phases, "startup_prepare"):
            from fastapi.testclient import TestClient
            from app.server import app
            from app.vector_index import index_manager
            from app.structured_recall import invalidate_structured_index

            invalidate_structured_index()
            materials, _ = read_catalog()

        with TestClient(app) as client:
            with phase(phases, "warmup"):
                idx_info = checked_index(mode, index_manager, materials)
                headers = configure_actor()
                for it in items[:5]:
                    send_match(client, headers, it)
                send_upload(client, headers, items[:5])

            with phase(phases, "full_rank_replay"):
                diag, eval_sec = diagnostic_rows(items, asset["samples"])

            semantic_count = sum(r["semantic_in_pool"] for r in diag.values())
            if semantic_count != (len(items) if mode == "vector" else 0):
                raise RuntimeError(f"Unexpected semantic coverage {semantic_count}/{len(items)}")

            with phase(phases, "single_http"):
                single_rows = {it["id"]: send_match(client, headers, it) for it in items}

            batch_rows = {}
            uploads = []
            with phase(phases, "batch_http"):
                for start in range(0, len(items), 50):
                    chunk = items[start:start + 50]
                    rows, meta = send_upload(client, headers, chunk)
                    batch_rows.update(rows)
                    uploads.append(meta)

        model_meta = index_manager.get_model_metadata()
        bench = {
            "mode": mode,
            "input_hashes": frozen,
            "golden_sha256": sha(GOLDEN),
            "governance_sha256": sha(ROOT / "tests/fixtures/golden_governance_v1.json"),
            "hardware": hardware(),
            "phases": phases,
            "index": idx_info,
            "model_metadata": model_meta,
            "semantic_rows": semantic_count,
            "evaluation_seconds": eval_sec,
            "batch_size": 50,
            "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "diagnostics": diag,
            "single": single_rows,
            "batch": batch_rows,
            "uploads": uploads,
        }
        save_json(out_raw, bench)
        print(f"[holdout worker {mode}] completed 1,220 rows, eval_s={eval_sec:.2f}", flush=True)
    finally:
        shutil.rmtree(tmp_parent, ignore_errors=True)


def extract_split_summary(sum_mode: dict, v17_mode: dict, split_name: str):
    cur_sp = sum_mode["portfolios"]["governed_multi_split"][split_name]
    v17_sp = v17_mode["portfolios"]["governed_multi_split"][split_name]

    cur_a = cur_sp["answerable"]["with_equivalence"]["all"]
    v17_a = v17_sp["answerable"]["with_equivalence"]["all"]

    cur_b_ans = cur_sp["answerable"]["http_with_equivalence"]["batch"]["all"]
    v17_b_ans = v17_sp["answerable"]["http_with_equivalence"]["batch"]["all"]

    cur_b_neg = cur_sp["reject"]["http"]["batch"]["all"]
    v17_b_neg = v17_sp["reject"]["http"]["batch"]["all"]

    cur_kit = cur_sp["kit_1toN"]["confirmed_all_components"]["all"]
    v17_kit = v17_sp["kit_1toN"]["confirmed_all_components"]["all"]

    def _pair(metric_key):
        cn, cd = cur_a[metric_key]["n"], cur_a[metric_key]["d"]
        vn = v17_a[metric_key]["n"]
        return {
            "current": {"n": cn, "d": cd, "pct": cur_a[metric_key]["pct"], "ci95_pct": wilson_ci95(cn, cd)},
            "v017": {"n": vn, "d": cd, "pct": v17_a[metric_key]["pct"]},
            "delta_n": cn - vn,
        }

    domains = {}
    cur_doms = cur_sp["answerable"]["with_equivalence"]["domains"]
    v17_doms = v17_sp["answerable"]["with_equivalence"]["domains"]
    for dom in sorted(set(cur_doms) | set(v17_doms)):
        cd = cur_doms[dom]
        vd = v17_doms[dom]
        domains[dom] = {
            "n": cd["rows"],
            "r100": cd["candidate_recall@100"]["n"],
            "r100_v017": vd["candidate_recall@100"]["n"],
            "r100_delta": cd["candidate_recall@100"]["n"] - vd["candidate_recall@100"]["n"],
            "r200": cd["candidate_recall@200"]["n"],
            "r200_v017": vd["candidate_recall@200"]["n"],
            "r200_delta": cd["candidate_recall@200"]["n"] - vd["candidate_recall@200"]["n"],
            "hit1": cd["hit@1"]["n"],
            "hit1_v017": vd["hit@1"]["n"],
            "hit1_delta": cd["hit@1"]["n"] - vd["hit@1"]["n"],
            "hit3": cd["hit@3"]["n"],
            "hit3_v017": vd["hit@3"]["n"],
            "hit3_delta": cd["hit@3"]["n"] - vd["hit@3"]["n"],
            "mrr_at_10": cd["mrr_at_10"],
            "mrr_at_10_v017": vd["mrr_at_10"],
            "stage_counts": cd["stage_counts"],
        }

    auto_err_ans = cur_b_ans["assessed_wrong_auto_binding_rate"]["n"]
    auto_err_neg = cur_b_neg["assessed_wrong_auto_binding_rate"]["n"]
    assessed_total = cur_b_ans["assessed_rows"] + cur_b_neg["assessed_rows"]

    return {
        "rows": cur_sp["rows"],
        "answerable_rows": cur_a["rows"],
        "reject_rows": cur_b_neg["rows"],
        "pending_rows": cur_sp["pending"]["n"],
        "recall_at_100": _pair("candidate_recall@100"),
        "recall_at_200": _pair("candidate_recall@200"),
        "hit_at_1": _pair("hit@1"),
        "hit_at_3": _pair("hit@3"),
        "mrr_at_10": {
            "current": cur_a["mrr_at_10"],
            "v017": v17_a["mrr_at_10"],
            "delta": round(cur_a["mrr_at_10"] - v17_a["mrr_at_10"], 6),
        },
        "mrr_full": {
            "current": cur_a["mrr_full"],
            "v017": v17_a["mrr_full"],
            "delta": round(cur_a["mrr_full"] - v17_a["mrr_full"], 6),
        },
        "stage_counts": cur_a["stage_counts"],
        "channel_target_slot_counts": cur_a["channel_target_slot_counts"],
        "auto_accepted_ans": cur_b_ans["status_counts"].get("auto_accepted", 0),
        "auto_accepted_neg": cur_b_neg["status_counts"].get("auto_accepted", 0),
        "wrong_auto_binding": {"n": auto_err_ans + auto_err_neg, "d": assessed_total},
        "negative_explicit_rejection": {
            "current": cur_b_neg["correct_rejection_rate"],
            "v017": v17_b_neg["correct_rejection_rate"],
            "delta_n": cur_b_neg["correct_rejection_rate"]["n"] - v17_b_neg["correct_rejection_rate"]["n"],
        },
        "wrong_proposed_binding_ans": {
            "current": cur_b_ans["assessed_wrong_proposed_binding_rate"],
            "v017": v17_b_ans["assessed_wrong_proposed_binding_rate"],
            "delta_n": cur_b_ans["assessed_wrong_proposed_binding_rate"]["n"] - v17_b_ans["assessed_wrong_proposed_binding_rate"]["n"],
        },
        "wrong_proposed_binding_neg": {
            "current": cur_b_neg["assessed_wrong_proposed_binding_rate"],
            "v017": v17_b_neg["assessed_wrong_proposed_binding_rate"],
            "delta_n": cur_b_neg["assessed_wrong_proposed_binding_rate"]["n"] - v17_b_neg["assessed_wrong_proposed_binding_rate"]["n"],
        },
        "kit_confirmed_all_components_top10": {
            "current": cur_kit["hit@10"],
            "v017": v17_kit["hit@10"],
            "delta_n": cur_kit["hit@10"]["n"] - v17_kit["hit@10"]["n"],
        },
        "domains": domains,
    }


def check_v017_gates(mode: str, all_summary: dict, latency: dict, peak_wset: float) -> dict:
    g = SIGNED_V017_GATES[mode]
    checks = {
        "r100_ok": all_summary["recall_at_100"]["current"]["n"] >= g["r100"],
        "r200_ok": all_summary["recall_at_200"]["current"]["n"] >= g["r200"],
        "hit1_ok": all_summary["hit_at_1"]["current"]["n"] >= g["hit1"],
        "hit3_ok": all_summary["hit_at_3"]["current"]["n"] >= g["hit3"],
        "mrr10_ok": all_summary["mrr_at_10"]["current"] >= g["mrr_at_10"],
        "mrr_full_ok": all_summary["mrr_full"]["current"] >= g["mrr_full"],
        "wrong_auto_binding_ok": all_summary["wrong_auto_binding"]["n"] <= g["wrong_auto_binding_max"],
        "neg_rejection_ok": all_summary["negative_explicit_rejection"]["current"]["n"] >= g["neg_explicit_rejection_min"],
        "wrong_proposed_ans_ok": all_summary["wrong_proposed_binding_ans"]["current"]["n"] <= g["wrong_proposed_ans_max"],
        "wrong_proposed_neg_ok": all_summary["wrong_proposed_binding_neg"]["current"]["n"] <= g["wrong_proposed_neg_max"],
        "kit_top10_ok": all_summary["kit_confirmed_all_components_top10"]["current"]["n"] >= g["kit_confirmed_top10_min"],
        "single_p50_ok": latency["single_all"]["p50_ms"] <= g["single_p50_ms"],
        "single_p95_ok": latency["single_all"]["p95_ms"] <= g["single_p95_ms"],
        "up50_p50_ok": latency["upload_full_batches"]["p50_ms"] <= g["up50_p50_ms"],
        "up50_p95_ok": latency["upload_full_batches"]["p95_ms"] <= g["up50_p95_ms"],
        "wset_ok": peak_wset <= g["wset_mib"],
    }
    domain_checks = {}
    for dom, (er100, er200, eh1) in g["domains"].items():
        d = all_summary["domains"][dom]
        domain_checks[dom] = {
            "r100_ok": d["r100"] >= er100,
            "r200_ok": d["r200"] >= er200,
            "hit1_ok": d["hit1"] >= eh1,
            "observed": [d["r100"], d["r200"], d["hit1"]],
            "floor": [er100, er200, eh1],
        }
    return {"global": checks, "domains": domain_checks}


def run_orchestrator(freeze_path: Path, out_report: Path):
    freeze = verify_freeze_chain(freeze_path)
    freeze_digest = sha(freeze_path)
    v17 = read_json(V017_EV / "m3_r01_metrics.json")
    items = read_json(GOLDEN)["items"]
    asset = read_json(ROOT / "tests/fixtures/golden_governance_v1.json")

    tmp_dir = Path(tempfile.mkdtemp(prefix="v018_m3_holdout_orch_"))
    try:
        modes_out = {}
        for mode in ("keyword", "vector"):
            raw_path = tmp_dir / f"holdout_raw_{mode}.json"
            cmd = [
                sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()),
                "--worker", mode, "--freeze", str(freeze_path), "--out", str(raw_path),
            ]
            subprocess.run(cmd, cwd=str(ROOT), check=True)
            bench = read_json(raw_path)
            summarized = summarize(items, asset, bench)
            peak_wset = round(max(v["process_peak_working_set_mib"] for v in bench["phases"].values()), 2)

            gt_guard = {
                gid: bench["batch"][gid]["decision_status"]
                for gid in ("GT-0674", "GT-0677", "GT-0678")
            }

            splits_summary = {}
            for sp in ("test", "all", "train", "dev"):
                splits_summary[sp] = extract_split_summary(summarized, v17[mode], sp)

            gate_check = check_v017_gates(mode, splits_summary["all"], summarized["latency"], peak_wset)

            # Collect Test 160 missed/blocked/ranked_gt10 sample IDs for root cause analysis
            test_items = [it for it in items if asset["samples"][it["id"]]["split"] == "test" and core(it) and asset["samples"][it["id"]]["status"] == "answerable"]
            test_misses = []
            for it in test_items:
                row = bench["diagnostics"][it["id"]]
                orig = it["target_codes"][0]
                alts = asset["samples"][it["id"]]["target_alternatives"][orig]
                targs = [row["targets"][c] for c in alts]
                positions = [t["recall_position"] for t in targs if t["recall_position"] is not None]
                ranks = [t["rank"] for t in targs if t["rank"] is not None and t["state"] != "hard_blocked"]
                if not positions or min(positions) > 200 or not ranks or min(ranks) > 10:
                    test_misses.append({
                        "id": it["id"],
                        "domain": it["professional"],
                        "name": it["name"],
                        "pool_size": row["pool_size"],
                        "recall_pos": min(positions) if positions else None,
                        "rank": min(ranks) if ranks else None,
                        "states": sorted({t["state"] for t in targs}),
                        "channels": sorted({c for t in targs for c in t["channels"]}),
                    })

            modes_out[mode] = {
                "started_at": bench["started_at"],
                "hardware": bench["hardware"],
                "index": bench["index"],
                "model_metadata": bench["model_metadata"],
                "semantic_rows": bench["semantic_rows"],
                "evaluation_seconds": round(bench["evaluation_seconds"], 3),
                "peak_wset_mib": peak_wset,
                "phases": bench["phases"],
                "latency": summarized["latency"],
                "gt_guard_status": gt_guard,
                "splits": splits_summary,
                "v017_gate_check": gate_check,
                "test_holdout_miss_details": test_misses,
            }
            ts = splits_summary["test"]
            al = splits_summary["all"]
            print(
                f"[HOLDOUT {mode:7s}] TEST(n={ts['answerable_rows']}): "
                f"R100={ts['recall_at_100']['current']['n']}(d={ts['recall_at_100']['delta_n']:+d}) "
                f"R200={ts['recall_at_200']['current']['n']}(d={ts['recall_at_200']['delta_n']:+d}) "
                f"H1={ts['hit_at_1']['current']['n']}(d={ts['hit_at_1']['delta_n']:+d}) "
                f"H3={ts['hit_at_3']['current']['n']}(d={ts['hit_at_3']['delta_n']:+d}) "
                f"MRR10={ts['mrr_at_10']['current']} | "
                f"ALL(729): R100={al['recall_at_100']['current']['n']} R200={al['recall_at_200']['current']['n']} "
                f"H1={al['hit_at_1']['current']['n']} H3={al['hit_at_3']['current']['n']} "
                f"WSet={peak_wset}MiB P50={summarized['latency']['single_all']['p50_ms']}ms "
                f"P95={summarized['latency']['single_all']['p95_ms']}ms "
                f"UpP50={summarized['latency']['upload_full_batches']['p50_ms']}ms "
                f"UpP95={summarized['latency']['upload_full_batches']['p95_ms']}ms",
                flush=True,
            )

        report = {
            "package": "V018-M3/R01",
            "stage": "ONE_SHOT_POST_FREEZE_HOLDOUT_VERIFICATION",
            "completed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "freeze_record_path": "doc/V0.1.8/evidence/m3_r01_candidate_freeze.json",
            "freeze_record_sha256": freeze_digest,
            "frozen_at": freeze["frozen_at"],
            "adoption_decision": freeze["adoption_decision"],
            "frozen_submission_config": freeze["frozen_submission_config"],
            "golden_sha256": sha(GOLDEN),
            "governance_sha256": sha(ROOT / "tests/fixtures/golden_governance_v1.json"),
            "catalog_sha256": sha(ROOT / "app/materials.db"),
            "modes": modes_out,
        }
        save_json(out_report, report)
        print(f"Saved one-shot holdout report to {out_report}", flush=True)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--worker", choices=("keyword", "vector"), default="")
    p.add_argument("--freeze", type=Path, default=V018_EV / "m3_r01_candidate_freeze.json")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.worker:
        run_mode_worker(args.worker, args.freeze, args.out)
    else:
        run_orchestrator(args.freeze, args.out)


if __name__ == "__main__":
    main()
