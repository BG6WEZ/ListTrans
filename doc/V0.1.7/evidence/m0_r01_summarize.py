"""Compare three evaluation populations from an isolated M0 benchmark."""
import argparse
from collections import Counter, defaultdict
import math
from pathlib import Path

from m0_r01_build_governance import ROOT, GOLDEN, core, read_json, save_json, sha

STAGES = ("not_in_catalog", "not_recalled", "hard_blocked", "ranked_gt10", "top10_hit")
REJECT = {"disabled_or_retired", "no_substitute"}
PENDING = "pending_business_review"


def percentage(numerator, denominator):
    return {"n": numerator, "d": denominator,
            "pct": round(100 * numerator / denominator, 4) if denominator else None}


def by_domain(items, summarize):
    domains = defaultdict(list)
    for item in items:
        domains[item["professional"]].append(item)
    return {"all": summarize(items), "domains": {key: summarize(rows) for key, rows in domains.items()}}


def rank_metrics(items, report, acceptable, *, exclude_blocked=False, require_all=False):
    stage_counts = Counter({stage: 0 for stage in STAGES})
    channel_counts, recall_counts, hit_counts = Counter(), Counter(), Counter()
    recall_limits = (50, 100, 200)
    hit_limits = (1, 3, 5, 10)
    slots = total_rr = top10_rr = 0
    for item in items:
        row = report[item["id"]]
        ranks = []
        for original in item["target_codes"]:
            targets = [row["targets"][code] for code in acceptable(item, original)]
            stage_counts[max((t["state"] for t in targets), key=STAGES.index)] += 1
            positions = [t["recall_position"] for t in targets if t["recall_position"] is not None]
            slots += 1
            if positions:
                recall_counts["all"] += 1
                for cutoff in recall_limits:
                    recall_counts[cutoff] += min(positions) <= cutoff
                channel_counts.update({c for t in targets for c in t["channels"]})
            valid_ranks = [t["rank"] for t in targets if t["rank"] is not None
                           and (not exclude_blocked or t["state"] != "hard_blocked")]
            ranks.append(min(valid_ranks, default=math.inf))
        rank = (max if require_all else min)(ranks)
        for cutoff in hit_limits:
            hit_counts[cutoff] += rank <= cutoff
        total_rr += 1 / rank
        if rank <= 10:
            top10_rr += 1 / rank
    return {
        "rows": len(items), "target_slots": slots,
        "candidate_recall": percentage(recall_counts["all"], slots),
        **{f"candidate_recall@{k}": percentage(recall_counts[k], slots) for k in recall_limits},
        **{f"hit@{k}": percentage(hit_counts[k], len(items)) for k in hit_limits},
        "mrr_at_10": round(top10_rr / len(items), 6) if items else None,
        "mrr_full": round(total_rr / len(items), 6) if items else None,
        "stage_counts": dict(stage_counts), "channel_target_slot_counts": dict(channel_counts),
    }


def decisions(items, rows, label, acceptable):
    single = all(rows[item["id"]]["decision_status"] is None for item in items)
    status = Counter(rows[item["id"]]["decision_status"] or "no_decision_exposed" for item in items)
    assessed, accepts, correct = 0, 0, 0
    wrong_proposals = proposed = rejected_negatives = abstained_negatives = negative = 0
    correct_top1 = scored_top1 = is_exact = 0
    for item in items:
        item_id = item["id"]
        row = rows[item_id]
        is_exact += row["is_exact"]
        population = label(item)
        if population not in {"answerable", "reject"}:
            continue
        assessed += 1
        negative += population == "reject"
        predicted = row["bindings"]
        if row["decision_status"] == "auto_accepted":
            accepts += 1
        allowed = acceptable(item) if population == "answerable" else set()
        binding_correct = len(predicted) == 1 and predicted[0] in allowed
        correct += row["decision_status"] == "auto_accepted" and binding_correct
        proposed += bool(predicted)
        wrong_proposals += bool(predicted) and not binding_correct
        if population == "reject":
            rejected_negatives += row["decision_status"] == "rejected"
            abstained_negatives += not predicted and row["decision_status"] != "auto_accepted"
        else:
            correct_top1 += row["top_code"] in allowed
            scored_top1 += 1
    return {
        "rows": len(items), "status_counts": dict(status), "assessed_rows": assessed,
        "auto_accepted_coverage": percentage(status["auto_accepted"], len(items)),
        "assessed_auto_accepted_accuracy": percentage(correct, accepts),
        "assessed_wrong_auto_binding_rate": percentage(accepts - correct, assessed),
        "assessed_wrong_proposed_binding_rate": None if single else percentage(wrong_proposals, assessed),
        "assessed_proposed_binding_rate": None if single else percentage(proposed, assessed),
        "explicit_rejection_rate": None if single else percentage(status["rejected"], len(items)),
        "correct_rejection_rate": None if single else percentage(rejected_negatives, negative),
        "negative_no_binding_rate": None if single else percentage(abstained_negatives, negative),
        "answerable_top1_code_rate": percentage(correct_top1, scored_top1),
        "matcher_is_exact_hint_rate": percentage(is_exact, len(items)),
    }


def group_portfolios(items, bench, governance):
    diagnostic = bench["diagnostics"]

    def strict(item, code):
        return [code]

    def governed(item, code):
        return governance[item["id"]]["target_alternatives"].get(code, [code])

    def governed_label(item):
        if not core(item):
            return "excluded_qc_or_kit"
        status = governance[item["id"]]["status"]
        if status in REJECT:
            return "reject"
        if status == PENDING:
            return PENDING
        return "answerable"

    def original_label(item):
        return "answerable" if all(diagnostic[item["id"]]["targets"][c]["state"] != "not_in_catalog"
                                   for c in item["target_codes"]) else "legacy_missing_target"

    def http_quality(rows, labeling, codes):
        return {
            entry: by_domain(rows, lambda subset: decisions(subset, bench[entry], labeling,
                             lambda item: {alt for c in item["target_codes"] for alt in codes(item, c)}))
            for entry in ("single", "batch")
        }

    def portfolio(rows, labeling):
        return {"ranking": by_domain(rows, lambda subset: rank_metrics(subset, diagnostic, strict)),
                "http": http_quality(rows, labeling, strict)}

    original = [i for i in items if core(i)]
    in_catalog = [i for i in original if original_label(i) == "answerable"]
    splits = {}
    for split in ("all", "train", "dev", "test"):
        selected = [i for i in items if split == "all" or governance[i["id"]]["split"] == split]
        answerable = [i for i in selected if governed_label(i) == "answerable"]
        reject = [i for i in selected if governed_label(i) == "reject"]
        pending = [i for i in selected if governance[i["id"]]["status"] == PENDING]
        kit = [i for i in selected if i["label_type"] == "1toN"]
        confirmed_kit = [i for i in kit if not i["ambiguous"] and not i["qc_suspect"]
                         and len(governance[i["id"]]["target_alternatives"]) == len(i["target_codes"])]

        splits[split] = {
            "rows": len(selected),
            "answerable": {
                "strict_original": by_domain(answerable,
                    lambda subset: rank_metrics(subset, diagnostic, strict, exclude_blocked=True)),
                "with_equivalence": by_domain(answerable,
                    lambda subset: rank_metrics(subset, diagnostic, governed, exclude_blocked=True)),
                "http_strict_original": http_quality(answerable, governed_label, strict),
                "http_with_equivalence": http_quality(answerable, governed_label, governed),
            },
            "reject": {
                "class_counts": dict(Counter(governance[i["id"]]["status"] for i in reject)),
                "original_target_diagnostic": by_domain(reject,
                    lambda subset: rank_metrics(subset, diagnostic, strict)),
                "http": http_quality(reject, governed_label, governed),
            },
            "pending": {"n": len(pending), "core_n": sum(core(i) for i in pending),
                        "domains": dict(Counter(i["professional"] for i in pending))},
            "legacy_excluded_1to1": sum(not core(i) and i["label_type"] == "1to1" for i in selected),
            "kit_1toN": {
                "n": len(kit), "pending_n": sum(governance[i["id"]]["status"] == PENDING for i in kit),
                "strict_all_components_diagnostic": by_domain(kit,
                    lambda subset: rank_metrics(subset, diagnostic, strict, require_all=True)),
                "confirmed_all_components": by_domain(confirmed_kit,
                    lambda subset: rank_metrics(subset, diagnostic, governed, exclude_blocked=True, require_all=True)),
            },
        }
    return {"legacy_full_core": portfolio(original, original_label),
            "in_catalog_core": portfolio(in_catalog, original_label), "governed_multi_split": splits}


def latency(samples):
    samples = sorted(samples)
    return {"n": len(samples), "p50_ms": round(samples[math.ceil(.5 * len(samples)) - 1], 3),
            "p95_ms": round(samples[math.ceil(.95 * len(samples)) - 1], 3),
            "min_ms": round(samples[0], 3), "max_ms": round(samples[-1], 3)}


def summarize(items, asset, bench):
    if bench["golden_sha256"] != sha(GOLDEN):
        raise ValueError("Benchmark digest mismatch")
    expected = {item["id"] for item in items}
    for entry in ("diagnostics", "batch", "single"):
        if set(bench[entry]) != expected:
            raise ValueError(f"Incomplete benchmark coverage: {entry}")
    routing = Counter()
    for item in items:
        domain = item["professional"]
        served = bench["batch"][item["id"]]["served_domain"]
        routing[(domain, served)] += 1
    return {
        "mode": bench["mode"], "hardware": bench["hardware"], "index": bench["index"],
        "semantic_rows": bench["semantic_rows"], "phases": bench["phases"],
        "latency": {
            "single_all": latency([r["ms"] for r in bench["single"].values()]),
            "single_domains": {p: latency([bench["single"][i["id"]]["ms"] for i in items
                                          if i["professional"] == p])
                               for p in sorted({i["professional"] for i in items})},
            "upload_all": latency([u["ms"] for u in bench["uploads"]]),
            "upload_full_batches": latency([u["ms"] for u in bench["uploads"] if u["rows"] == bench["batch_size"]]),
            "upload_batch_sizes": dict(Counter(u["rows"] for u in bench["uploads"])),
        },
        "upload_routing": [{"golden_domain": gold, "served_domain": served, "rows": rows}
                           for (gold, served), rows in sorted(routing.items())],
        "portfolios": group_portfolios(items, bench, asset["samples"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vector", type=Path, required=True)
    parser.add_argument("--keyword", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--governance", type=Path, default=ROOT / "tests/fixtures/golden_governance_v1.json")
    args = parser.parse_args()
    items = read_json(GOLDEN)["items"]
    asset = read_json(args.governance)
    result = {}
    for mode in ("keyword", "vector"):
        bench_path = getattr(args, mode)
        bench = read_json(bench_path)
        if bench["mode"] != mode or bench["governance_sha256"] != sha(args.governance):
            raise ValueError("Input mode or governance digest mismatch")
        result[mode] = {"benchmark_sha256": sha(bench_path), **summarize(items, asset, bench)}
    save_json(args.out, result)


if __name__ == "__main__":
    main()
