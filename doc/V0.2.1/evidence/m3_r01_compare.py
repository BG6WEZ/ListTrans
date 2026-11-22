"""Summarize frozen paired quality, protected probes and V017 budgets."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import math
import sys

import numpy as np

from m3_r01_baseline import EVIDENCE, save, sha
from m3_r01_projection import projection, seal_holdout


DOMAINS = ("强电", "暖通", "装饰", "医用气体", "弱电", "给排水", "自控", "纯水", "设备")
FLOORS = {
    "强电": (202, (81, 82, 45), (166, 171, 50)),
    "暖通": (92, (62, 79, 33), (69, 90, 29)),
    "装饰": (126, (80, 83, 26), (114, 117, 29)),
    "医用气体": (112, (72, 74, 23), (92, 94, 22)),
    "弱电": (114, (70, 74, 37), (83, 98, 25)),
    "给排水": (57, (18, 18, 7), (50, 50, 19)),
}
SAFETY = ("GT-0674", "GT-0677", "GT-0678")
PRIORITY = ("GT-0300", "GT-0551", "GT-0004")
METRICS = ("hit@1", "hit@3", "mrr@10")
SIGNED = ("candidate_recall@100", "candidate_recall@200", "hit@1")
R2 = {
    "keyword": {"A": 34, "B": 205, "C": 344, "D": 477, "prefilled": 510,
                "low_score_prefill": 271, "safety_missing": 73},
    "vector": {"A": 36, "B": 440, "C": 303, "D": 281, "prefilled": 694,
               "low_score_prefill": 218, "safety_missing": 85},
}


def load(path):
    return json.loads(path.read_bytes())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def wilson(correct, n):
    if not n:
        return None
    z = 1.959963984540054
    p = correct / n
    scale = 1 + z * z / n
    center = (p + z * z / (2 * n)) / scale
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / scale
    return [max(0.0, center - half), min(1.0, center + half)]


def paired(values):
    n = len(values)
    if not n:
        return {"n": 0, "off": None, "on": None, "win": 0, "tie": 0, "loss": 0, "ci95": None}
    off, on = np.asarray(values, dtype=float).T
    differences = on - off
    samples, counts = np.unique(differences, return_counts=True)
    rng = np.random.default_rng(210301)
    boot = rng.multinomial(n, counts / n, size=10000) @ samples / n
    return {
        "n": n, "off": float(off.mean()), "on": float(on.mean()),
        "off_count_or_sum": float(off.sum()), "on_count_or_sum": float(on.sum()),
        "delta": float(differences.mean()), "win": int(sum(differences > 0)),
        "tie": int(sum(differences == 0)), "loss": int(sum(differences < 0)),
        "ci95": np.quantile(boot, [0.025, 0.975]).tolist(),
    }


def core(item):
    return item["label_type"] == "1to1" and not item["ambiguous"] and not item["qc_suspect"]


def alternatives(label):
    return set().union(*label["target_alternatives"].values())


def scores(row, codes):
    targets = [row["rank"]["targets"][code] for code in codes]
    rank = min((t["rank"] for t in targets if t["rank"] is not None and t["state"] != "hard_blocked"),
               default=math.inf)
    recall = min((t["recall_position"] for t in targets if t["recall_position"] is not None), default=math.inf)
    return (rank <= 1, rank <= 3, 1 / rank if rank <= 10 else 0.0, recall <= 100, recall <= 200)


def binding_counts(items, arm, labels):
    counts = Counter({k: 0 for k in ("A", "B", "C", "D", "prefilled", "low_score_prefill", "safety_missing",
                                    "A_correct", "A_wrong", "A_unknown", "wrong_auto", "auto_unknown",
                                    "auto_correct", "auto_total")})
    for item in items:
        gid = item["id"]
        entry = arm["rows"][gid]["upload"]
        counts.update((entry["tier"],))
        counts["prefilled"] += entry["prefilled"]
        if entry["subtype"]:
            counts.update((entry["subtype"],))
        label = labels[gid]
        expected = alternatives(label)
        is_judged = core(item) and label["status"] in {"answerable", "disabled_or_retired", "no_substitute"}

        def correct(codes):
            return len(codes) == 1 and codes[0] in expected and label["status"] == "answerable"

        if entry["tier"] == "A":
            key = ("A_correct" if correct(entry["effective_codes"]) else "A_wrong") if is_judged else "A_unknown"
            counts[key] += 1
        if entry["decision"] == "auto_accepted":
            counts["auto_total"] += 1
            key = ("auto_correct" if correct(entry["decision_codes"]) else "wrong_auto") if is_judged else "auto_unknown"
            counts[key] += 1
    denom = counts["A_correct"] + counts["A_wrong"]
    return {"N": len(items), **counts, "A_adjudicable": denom,
            "A_correct_ratio": counts["A_correct"] / denom if denom else None,
            "A_correct_wilson95": wilson(counts["A_correct"], denom)}


def cohort(items, off, on, labels):
    answerable = [r for r in items if core(r) and labels[r["id"]]["status"] == "answerable"]
    ranking = {}
    for scope in ("strict", "equivalent"):
        observations = []
        for item in answerable:
            gid = item["id"]
            selected = item["target_codes"] if scope == "strict" else alternatives(labels[gid])
            observations.append(tuple(zip(scores(off["rows"][gid], selected),
                                          scores(on["rows"][gid], selected), strict=True)))
        keys = (*METRICS, *SIGNED[:2])
        ranking[scope] = {metric: paired([entry[index] for entry in observations])
                          for index, metric in enumerate(keys)}
    bindings = {arm["arm"]: binding_counts(items, arm, labels) for arm in (off, on)}
    coverage = paired([
        (off["rows"][row["id"]]["upload"]["tier"] == "A", on["rows"][row["id"]]["upload"]["tier"] == "A")
        for row in items
    ])
    return {"rows": len(items), "core_answerable": len(answerable), "ranking": ranking,
            "upload": bindings, "paired_A_coverage": coverage}


def floor(mode, domain, cohort_data):
    n = cohort_data["core_answerable"]
    if n < 20:
        return {"n": n, "disposition": "observation_only"}
    if domain not in FLOORS:
        raise ValueError(f"An unsigned domain now has {n} answerable rows")
    denominator, keyword, vector = FLOORS[domain]
    report = {}
    for metric, num in zip(SIGNED, keyword if mode == "keyword" else vector, strict=True):
        result = cohort_data["ranking"]["equivalent"][metric]
        report[metric] = {"signed_rate": num / denominator, "on": result["on"], "off": result["off"],
                          "signed_floor_met": result["on"] * denominator + 1e-12 >= num,
                          "paired_non_regression": result["delta"] >= -1e-12}
    return {"n": n, "disposition": "gated",
            "passed": all(r["signed_floor_met"] and r["paired_non_regression"] for r in report.values()),
            "per_metric": report}


def safety_ok(arm):
    probes = arm["protected_probes"]
    return all(probes[gid]["upload"]["tier"] == "C" and probes[gid]["upload"]["subtype"] == "safety_missing"
               and not probes[gid]["upload"]["effective_codes"] and probes[gid]["export_http"] == 422
               and any(f["safety_critical_unknown"] for f in probes[gid]["fields"]) for gid in SAFETY)


def compare(mode, off, on, archived, items, labels):
    if any(off[field] != on[field] for field in (
        "input_hashes", "index", "queries_sha256", "labels_sha256", "protocol_sha256",
    )) or off["labels_sha256"] != digest(labels):
        raise ValueError("Arms differ in frozen inputs")
    if set(off["rows"]) != set(on["rows"]) or set(off["rows"]) != set(labels):
        raise ValueError("Arms differ in paired population")
    if (set(row["professional"] for row in items) - set(DOMAINS)):
        raise ValueError("Unexpected discipline in the frozen projection")
    splits = {}
    for split in ("train", "dev", "train_dev"):
        population = [r for r in items if split == "train_dev" or labels[r["id"]]["split"] == split]
        report = cohort(population, off, on, labels)
        report["by_domain"] = {}
        for domain in DOMAINS:
            current = cohort([r for r in population if r["professional"] == domain], off, on, labels)
            current["floor"] = floor(mode, domain, current)
            report["by_domain"][domain] = current
        splits[split] = report
    dev = splits["dev"]
    before, after = dev["upload"]["off"], dev["upload"]["on"]
    baseline = splits["train_dev"]["upload"]["off"]
    r2_delta = {key: baseline[key] - expected for key, expected in R2[mode].items()}
    m2 = load(EVIDENCE / f"m2_r01_{mode}_replay_final.json")
    current_priority = {gid: entry["reason"] for gid, entry in off["priority_dispositions"].items()
                        if off["rows"][gid]["upload"]["tier"] == "A"}
    prior_priority = m2["priority_disposition_by_sample"]
    priority_delta = sorted(gid for gid in prior_priority.keys() | current_priority.keys()
                            if prior_priority.get(gid) != current_priority.get(gid))
    rowwise = {}
    if archived is not None:
        for field in ("input_hashes", "index", "queries_sha256", "labels_sha256", "protocol_sha256"):
            if off[field] != archived[field]:
                raise ValueError(f"Reconstructed M2 input differs in {field}")
        for field in ("rows", "protected_probes", "priority_dispositions"):
            after_values, before_values = off[field], archived[field]
            rowwise[field] = sorted(gid for gid in after_values.keys() | before_values.keys()
                                    if after_values.get(gid) != before_values.get(gid))
    protected = {gid: {
        arm["arm"]: {**arm["protected_probes"][gid],
                     "priority_preflight": arm["priority_dispositions"].get(gid),
                     "rank": arm["rows"][gid]["rank"]}
        for arm in (off, on)} for gid in (*SAFETY, *PRIORITY)}
    probe_checks = {
        "safety": safety_ok(off) and safety_ok(on),
        "priority_M2": all(not on["priority_dispositions"][gid]["eligible"] for gid in PRIORITY),
        "priority_off_against_m2": all(
            off["priority_dispositions"][gid] == {k: m2["probes"][gid][k] for k in ("eligible", "reason")}
            for gid in PRIORITY
        ),
    }
    selected = {
        "dev_equivalent_hit1_strict_gain": dev["ranking"]["equivalent"]["hit@1"]["delta"] > 0,
        "dev_equivalent_mrr10_strict_gain": dev["ranking"]["equivalent"]["mrr@10"]["delta"] > 0,
        "dev_A_strict_gain": after["A"] > before["A"],
        "dev_adjudicable_A_precision_non_regression": (
            before["A_correct_ratio"] is not None and after["A_correct_ratio"] is not None
            and after["A_correct_ratio"] >= before["A_correct_ratio"]
        ),
        "zero_frozen_wrong_automatics": splits["train_dev"]["upload"]["on"]["wrong_auto"] == 0,
        "protected_checks": all(probe_checks.values()),
        "domain_floors": all(d["floor"].get("passed", True) for split in splits.values()
                             for d in split["by_domain"].values()),
        "all_signed_resources_on": all(on["budget_pass"].values()),
        "on_completed_without_degradation": on["arm_exercised_without_degradation"],
        "off_R2_and_M2": not any(r2_delta.values()) and not priority_delta
        and bool(rowwise) and not any(rowwise.values()),
    }
    parity = {
        "R2_delta": r2_delta, "M2_priority_row_delta": priority_delta,
        "reconstructed_M2_row_differences": rowwise or None,
        "output_row_sha256": digest(off["rows"]), "rows": len(off["rows"]),
    }
    row_pairs = {}
    for item in items:
        gid = item["id"]
        label = labels[gid]
        row_pairs[gid] = {
            "split": label["split"], "domain": item["professional"],
            "rank_core": core(item) and label["status"] == "answerable",
            "upload_tier": {arm["arm"]: arm["rows"][gid]["upload"]["tier"] for arm in (off, on)},
            "prefilled": {arm["arm"]: arm["rows"][gid]["upload"]["prefilled"] for arm in (off, on)},
        }
        if row_pairs[gid]["rank_core"]:
            for scope, codes in (("strict", item["target_codes"]), ("equivalent", alternatives(label))):
                row_pairs[gid][scope] = {
                    arm["arm"]: scores(arm["rows"][gid], codes)[:3] for arm in (off, on)
                }
    return {
        "mode": mode, "splits": splits, "probes": protected, "probe_checks": probe_checks,
        "resource_gate": {arm["arm"]: arm["budget_pass"] for arm in (off, on)},
        "off_parity": parity, "checks": selected, "candidate_meets_gates": all(selected.values()),
        "per_row_pairs": row_pairs,
        "signed_zero_population_discipline": "洁净装饰",
        "heldout_samples_read": 0, "heldout_samples_evaluated": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projection", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    args = parser.parse_args()
    seal_holdout()
    items, labels = projection(args.projection)
    performance = {}
    for mode in ("keyword", "vector"):
        off_path = args.raw_dir / f"{mode}-off.json"
        on_path = args.raw_dir / f"{mode}-on-verified.json"
        off, on = load(off_path), load(on_path)
        archived_path = args.raw_dir / f"{mode}-m2.json"
        archived = load(archived_path)
        result = compare(mode, off, on, archived, items, labels)
        save(f"m3_r01_{mode}_paired.json", result)
        performance[mode] = {
            "candidate_meets_gates": result["candidate_meets_gates"],
            "arms": {arm["arm"]: {k: v for k, v in arm.items()
                                  if k not in ("rows", "protected_probes", "priority_dispositions")}
                     for arm in (off, on)},
            "raw_artifact_sha256": {"off": sha(off_path), "on": sha(on_path)},
        }
    save("m3_r01_performance.json", {
        "modes": performance,
        "recommended_default": "off",
        "note": "V017 budgets unchanged. Test embargo limits replay to 1060 singles and 21 full uploads per arm.",
    })


if __name__ == "__main__":
    sys.exit(main())
