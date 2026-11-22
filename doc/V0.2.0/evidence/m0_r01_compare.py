"""Compare locked OFF/ON replays without using the held-out population."""
import argparse
from collections import Counter
import math
from pathlib import Path

import numpy as np

from m0_r01_data import read_json, save_json, sha

RANK = ("candidate_recall@100", "candidate_recall@200", "hit@1", "hit@3", "mrr_at_10", "mrr_full")
DECISIONS = {
    "wrong_answerable_proposals": ("upload_answerable_decisions", "assessed_wrong_proposed_binding_rate"),
    "wrong_negative_proposals": ("upload_negative_decisions", "assessed_wrong_proposed_binding_rate"),
    "negative_explicit_rejection": ("upload_negative_decisions", "correct_rejection_rate"),
    "wrong_auto_binding": ("upload_decisions", "assessed_wrong_auto_binding_rate"),
    "auto_accepted_coverage": ("upload_decisions", "auto_accepted_coverage"),
    "auto_accepted_accuracy": ("upload_decisions", "assessed_auto_accepted_accuracy"),
}


def wilson_95(metric):
    total = metric["d"]
    if not total:
        return None
    fraction = metric["n"] / total
    z = 1.95996398454
    scale = 1 + z * z / total
    center = (fraction + z * z / (2 * total)) / scale
    radius = z * math.sqrt(fraction * (1 - fraction) / total + z * z / (4 * total * total)) / scale
    return [round(100 * max(0, center - radius), 4), round(100 * min(1, center + radius), 4)]


def delta(left, right):
    if isinstance(left, dict):
        if left["d"] == right["d"]:
            n_delta = right["n"] - left["n"]
        else:
            n_delta = None
        return {"off": {**left, "wilson_95_pct": wilson_95(left)},
                "on": {**right, "wilson_95_pct": wilson_95(right)}, "delta_count": n_delta,
                "delta_pp": round(right["pct"] - left["pct"], 4)
                if left["pct"] is not None and right["pct"] is not None else None}
    return {"off": left, "on": right, "delta": round(right - left, 6)
            if left is not None and right is not None else None}


def bootstrap_intervals(pairs):
    if not pairs:
        return {}
    values = np.array([[a or np.inf, b or np.inf] for a, b in pairs])
    rng = np.random.default_rng(20201120)
    observations = rng.integers(0, len(values), size=(3000, len(values)))
    vectors = {
        "hit@1": (values <= 1).astype(float),
        "hit@3": (values <= 3).astype(float),
        "mrr_at_10": np.where(values <= 10, 1 / values, 0),
        "mrr_full": 1 / values,
    }
    return {key: [round(float(x), 6) for x in np.quantile(
        (vector[:, 1] - vector[:, 0])[observations].mean(axis=1), [.025, .975],
    )] for key, vector in vectors.items()}


def compare_mode(directory, mode):
    paths = {arm: directory / f"m0_r01_{mode}_{arm}.json" for arm in ("off", "on")}
    off, on = (read_json(path) for path in paths.values())
    for key in ("input_hashes", "hardware", "asset_sha256", "model_index", "semantic_count"):
        if off[key] != on[key]:
            raise ValueError(f"{mode}: arm {key} mismatch")
    if off["paired_rank"].keys() != on["paired_rank"].keys():
        raise ValueError(f"{mode}: candidate population mismatch")
    if off["upload_decisions"].keys() != on["upload_decisions"].keys():
        raise ValueError(f"{mode}: route population mismatch")
    if any(off["paired_rank"][gid]["recall_pos"] != row["recall_pos"] for gid, row in on["paired_rank"].items()):
        raise ValueError(f"{mode}: the rank-only arm changed recall")
    results = {}
    for split, control in off["metrics"].items():
        treated = on["metrics"][split]
        if control["counts"] != treated["counts"]:
            raise ValueError(f"{mode}/{split}: changed gold population")
        for key in ("all", "domains"):
            if control["rank"][key].keys() != treated["rank"][key].keys():
                raise ValueError(f"{mode}/{split}: domain population changed")
        ranks = {"all": {name: delta(control["rank"]["all"][name], treated["rank"]["all"][name]) for name in RANK}}
        ranks["domains"] = {domain: {name: delta(rank[name], treated["rank"]["domains"][domain][name])
                                     for name in RANK} for domain, rank in control["rank"]["domains"].items()}
        decisions = {label: delta(control[block][metric], treated[block][metric])
                     for label, (block, metric) in DECISIONS.items()}
        pairs = [(off["paired_rank"][gid]["rank"], row["rank"]) for gid, row in on["paired_rank"].items()
                 if split == "train_dev" or row["split"] == split]
        changes = Counter()
        for a, b in pairs:
            a, b = a or float("inf"), b or float("inf")
            changes["improved" if b < a else "worsened" if b > a else "same"] += 1
        results[split] = {
            "population": control["counts"], "rank": ranks, "decisions": decisions, "paired_rank_changes": dict(changes),
            "paired_row_bootstrap_95_diagnostic_only": bootstrap_intervals(pairs),
        }
    dev = results["dev"]
    rank_changes = [dev["rank"]["all"][name].get("delta", dev["rank"]["all"][name].get("delta_count"))
                    for name in RANK]
    dev_quality = (all(change >= 0 for change in rank_changes) and any(change > 0 for change in rank_changes)
                   and not any(domain["hit@1"]["delta_count"] < 0 for domain in dev["rank"]["domains"].values()))
    decisions_ok = all(
        metrics["decisions"]["wrong_auto_binding"]["on"]["n"] == 0
        and all(metrics["decisions"][key]["delta_count"] <= 0 for key in
                ("wrong_answerable_proposals", "wrong_negative_proposals"))
        and metrics["decisions"]["negative_explicit_rejection"]["delta_count"] >= 0
        for metrics in results.values()
    )
    return {
        "result_sha256": {arm: sha(path) for arm, path in paths.items()}, "splits": results,
        "latency": {report["arm"]: {**report["latency"], "peak_working_set_mib": report["peak_working_set_mib"],
                                   "budget": report["budget"], "budget_pass": report["budget_pass"]}
                    for report in (off, on)},
        "dev_ranking_net_improvement_without_regression": dev_quality,
        "proposal_and_zero_wrong_auto_binding_gate": decisions_ok,
        "on_performance_within_budget": all(on["budget_pass"].values()),
        "upload_decision_rows_changed": sum(off["upload_decisions"][gid] != row
                                           for gid, row in on["upload_decisions"].items()),
    }


def run(output):
    directory = output.parent
    reports = {mode: compare_mode(directory, mode) for mode in ("keyword", "vector")}
    split = read_json(directory / "m0_r01_hard_negatives.json")["split"]
    feedback = read_json(directory / "m0_r01_feedback_sufficiency.json")
    save_json(output, {
        "modes": reports, "project_time_split": split,
        "local_trusted_current_feedback": feedback["trusted_current_events"],
        "online_automatic_training": False, "recommended_ranker_switch": "off",
        "evaluation_scope": "Frozen Train/Dev diagnosis. Signed full-population quality gates remain unverified.",
        "ci_scope": "Paired row-level percentile intervals describe this replay. Shared projects preclude a cross-project inference.",
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out)
