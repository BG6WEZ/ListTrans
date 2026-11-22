"""Compare the frozen Train/Dev runs and quantify changes to candidate and upload results."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random
import statistics

EVIDENCE = Path(__file__).resolve().parent
V018 = EVIDENCE.parents[1] / "V0.1.8" / "evidence"
PROFILES = ("legacy", "dedup", "calibrated")
RATES = ("candidate_recall@100", "candidate_recall@200", "hit@1", "hit@3")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def with_interval(rate):
    n, d = rate["n"], rate["d"]
    if not d:
        return {**rate, "wilson_95_pct": None}
    p = n / d
    z2 = 1.959963984540054 ** 2
    denom = 1 + z2 / d
    center = (p + z2 / (2 * d)) / denom
    radius = math.sqrt(z2 * (p * (1 - p) / d + z2 / (4 * d ** 2))) / denom
    return {**rate, "wilson_95_pct": [round(100 * max(0, center - radius), 4),
                                       round(100 * min(1, center + radius), 4)]}


def summarize_rank(metrics):
    return {
        **{key: with_interval(metrics[key]) for key in RATES},
        "mrr_at_10": metrics["mrr_at_10"], "mrr_full": metrics["mrr_full"],
        "rows": metrics["rows"], "target_slots": metrics["target_slots"],
        "target_stage_counts": metrics["stage_counts"],
    }


def score_distribution(gids, run):
    rows = [run["rows"][gid] for gid in gids]
    top = [row["top"][0][1] for row in rows if row["top"]]
    gaps = [row["top2_gap"] for row in rows if row["top2_gap"] is not None]
    high_pairs = [row for row in rows if len(row["top"]) > 1 and row["top"][1][1] >= 80]
    return {
        "rows": len(rows), "top1_rows": len(top), "top2_rows": len(gaps),
        "top1_score_median": statistics.median(top) if top else None,
        "top1_score_100": top.count(100), "top1_score_ge_98": sum(x >= 98 for x in top),
        "top2_gap_median": statistics.median(gaps) if gaps else None,
        "top2_tied": gaps.count(0),
        "top2_both_at_least_80": len(high_pairs),
        "top2_tied_when_both_at_least_80": sum(row["top2_gap"] == 0 for row in high_pairs),
    }


def paired_rank(base, candidate):
    pairs = [(row["rank"], candidate[gid]["rank"]) for gid, row in base.items()]
    if not pairs:
        return None

    def rr(rank, limit):
        return 1 / rank if rank and rank <= limit else 0.0

    scores = {
        "hit@1": [int(b == 1) - int(a == 1) for a, b in pairs],
        "hit@3": [int(0 < (b or 0) <= 3) - int(0 < (a or 0) <= 3) for a, b in pairs],
        "mrr_at_10": [rr(b, 10) - rr(a, 10) for a, b in pairs],
        "mrr_full": [rr(b, math.inf) - rr(a, math.inf) for a, b in pairs],
    }
    n = len(pairs)
    rng = random.Random(1901)
    result = {}
    for key, values in scores.items():
        draws = sorted(sum(rng.choices(values, k=n)) / n for _ in range(2000))
        result[key] = {"delta": round(sum(values) / n, 6),
                       "paired_bootstrap_95": [round(draws[49], 6), round(draws[1949], 6)]}
    return {"rows": n, "effects": result}


def summarize_decisions(split):
    keys = ("assessed_wrong_proposed_binding_rate", "assessed_wrong_auto_binding_rate",
            "correct_rejection_rate", "assessed_auto_accepted_accuracy", "auto_accepted_coverage",
            "matcher_is_exact_hint_rate")
    return {
        pop: {k: with_interval(values[k]) for k in keys}
        for pop, suffix in (("core", ""), ("answerable", "_answerable"), ("negative", "_negative"))
        if (values := split[f"upload{suffix}_decisions"]) is not None
    }


def compare_prior_release(baseline, release, governance):
    if any(baseline[k] != release[k] for k in ("golden_sha256", "governance_sha256", "catalog_sha256", "vector_index")):
        raise RuntimeError("V017 and V018 comparison inputs differ")
    old_rows = release["per_sample_ans"]
    new_rows = baseline["per_sample_ans"]
    if old_rows.keys() != new_rows.keys():
        raise RuntimeError("V017 and V018 answerable populations differ")
    shifts = {}
    focus_counts = {"decoration_hit1": Counter(), "strong_current_hit3": Counter()}
    for gid, old in old_rows.items():
        new = new_rows[gid]
        a, b = old["rank"], new["rank"]
        old_upload, new_upload = release["upload"][gid], baseline["rows"][gid]["upload"]
        codes = [c for group in governance[gid]["target_alternatives"].values() for c in group]

        def wrong(upload):
            bindings = upload["bindings"]
            return bool(bindings) and (len(bindings) != 1 or bindings[0] not in codes)

        old_wrong, new_wrong = wrong(old_upload), wrong(new_upload)
        if a == b and old_wrong == new_wrong and old_upload == new_upload:
            continue
        if old["recall_pos"] is None or new["recall_pos"] is None:
            cause = "recall_presence_changed" if old["recall_pos"] != new["recall_pos"] else "unrecalled"
        elif (a is None) != (b is None):
            cause = "hard_block_changed"
        elif old["recall_pos"] != new["recall_pos"]:
            cause = "recall_position_and_rank_changed" if a != b else "recall_position_changed"
        else:
            cause = "recalled_same_position_rank_changed" if a != b else "rank_unchanged"
        focus = set()
        if old["domain"] == "装饰" and (a == 1) != (b == 1):
            focus.add("decoration_hit1")
        if old["domain"] == "强电" and (0 < (a or 0) <= 3) != (0 < (b or 0) <= 3):
            focus.add("strong_current_hit3")
        for key in focus:
            direction = "loss" if (a or math.inf) < (b or math.inf) else "gain"
            focus_counts[key][f"{direction}:{cause}"] += 1
        shifts[gid] = {
            "domain": old["domain"], "split": old["split"], "focus": sorted(focus), "category": cause,
            "v017": {"target": old, "top3": release["top10"][gid][:3], "upload": old_upload,
                     "wrong_suggestion": old_wrong},
            "v018": {"target": new, "top3": [x[0] for x in baseline["rows"][gid]["top"]], "upload": new_upload,
                     "wrong_suggestion": new_wrong},
        }
    return {"focus_counts": focus_counts, "per_sample_changes": shifts,
            "splits": {split: {"rank": summarize_rank(data["rank"]["all"]),
                               "domains": {domain: summarize_rank(ranks)
                                           for domain, ranks in data["rank"]["domains"].items()},
                               "upload": summarize_decisions(data)}
                       for split, data in release["splits"].items()}}


def compare(mode, reports, previous, release, governance):
    legacy = reports["legacy"]
    result = {"profiles": {}, "pairing": {}}
    result["v017_to_v018_attribution"] = compare_prior_release(legacy, release, governance)
    expected = previous["runs"]["B0_KW_OFF" if mode == "keyword" else "B0_VEC_OFF"]["splits"]
    result["v018_off_reproduction"] = {}
    for split in ("train_dev", "train", "dev"):
        observed = legacy["splits"][split]
        reference = expected[split]
        result["v018_off_reproduction"][split] = {
            "rank": observed["rank"] == reference["rank"],
            "upload": all(observed[key] == reference[key] for key in (
                "upload_decisions", "upload_answerable_decisions", "upload_negative_decisions")),
            "kit": observed["kit_confirmed_all_components"] == reference["kit_confirmed_all_components"],
        }

    for profile, run in reports.items():
        if run["source_sha256"] != legacy["source_sha256"] or any(
            run[field] != legacy[field] for field in ("golden_sha256", "governance_sha256", "catalog_sha256",
                                                     "vector_index", "recall_order_sha256")
        ):
            raise RuntimeError(f"{mode}/{profile} changed a fixed input or recall order")
        gap = run["top2_gap"]
        result["profiles"][profile] = {
            "scores": run["scores"],
            "top2_gap_score_points": {"n": gap["n"], "p50": gap["p50_ms"],
                                      "p95": gap["p95_ms"], "maximum": gap["max_ms"]},
            "top2_exact_ties": run["top2_exact_ties"], "budget_check": run["budget_check"],
            "latency": run["latency"], "peak_wset_mib": run["peak_wset_mib"],
            "splits": {},
        }
        if run["rows"].keys() != legacy["rows"].keys():
            raise RuntimeError("Different row IDs")
        for split, report in run["splits"].items():
            rows = {gid: r for gid, r in run["per_sample_ans"].items() if split == "train_dev" or r["split"] == split}
            result["profiles"][profile]["splits"][split] = {
                "rank": summarize_rank(report["rank"]["all"]),
                "domains": {domain: summarize_rank(metrics) for domain, metrics in report["rank"]["domains"].items()},
                "upload": summarize_decisions(report), "protected_decisions": report["gt_guard_status"],
                "answerable_score_distribution": {
                    domain: score_distribution([gid for gid, r in rows.items() if r["domain"] == domain], run)
                    for domain in report["rank"]["domains"]
                },
            }
        if profile == "legacy":
            continue
        samples = legacy["per_sample_ans"]
        domains = sorted({r["domain"] for r in samples.values()})
        rank = {
            split: {"all": paired_rank({gid: r for gid, r in samples.items() if split == "train_dev" or r["split"] == split},
                                       run["per_sample_ans"]),
                    "domains": {domain: paired_rank(
                        {gid: r for gid, r in samples.items() if r["domain"] == domain and
                         (split == "train_dev" or r["split"] == split)}, run["per_sample_ans"])
                        for domain in domains}}
            for split in ("train_dev", "train", "dev")
        }
        effects = Counter()
        changes = {}
        for gid, before in legacy["rows"].items():
            after = run["rows"][gid]
            a = samples.get(gid)
            b = run["per_sample_ans"].get(gid)
            rank_changed = a is not None and a["rank"] != b["rank"]
            top_changed = [x[0] for x in before["top"]] != [x[0] for x in after["top"]]
            upload_changed = after["upload"] is not None and before["upload"] != after["upload"]
            if not (rank_changed or top_changed or upload_changed):
                continue
            effects.update({k: int(v) for k, v in (
                ("target_rank_changed", rank_changed), ("top3_codes_changed", top_changed),
                ("upload_record_changed", upload_changed),
                ("upload_binding_codes_changed", upload_changed and before["upload"]["bindings"] != after["upload"]["bindings"]),
                ("upload_decision_changed", upload_changed and before["upload"]["decision_status"] != after["upload"]["decision_status"]),
            )})
            changes[gid] = {
                "domain": a["domain"] if a else None, "target_rank": [a["rank"], b["rank"]] if a else None,
                "before": before, "after": after,
            }
        result["pairing"][profile] = {"rank": rank, "counts": dict(effects), "changes": changes}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    previous = load(V018 / "m3_r01_dev_selection_summary.json")
    governance = load(EVIDENCE.parents[2] / "tests" / "fixtures" / "golden_governance_v1.json")["samples"]
    result = {
        "v017_same_split_reference": previous["v017_same_split_reference"],
        "profiles_default": "legacy", "holdout": "Test 160 excluded",
        "modes": {},
    }
    for mode in ("keyword", "vector"):
        reports = {profile: load(EVIDENCE / f"m0_r01_{mode}_{profile}_final.json") for profile in PROFILES}
        release = load(EVIDENCE / f"m0_r01_v017_{mode}.json")
        result["modes"][mode] = compare(mode, reports, previous, release, governance)
    with args.out.open("x", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
