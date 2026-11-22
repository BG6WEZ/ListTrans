"""Fit only on the frozen Train partition and compare four predeclared plans on Dev."""
import argparse
import gzip
import json
import os
from pathlib import Path
import sys
from time import perf_counter_ns

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "doc/V0.1.7/evidence")]
for env in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ[env] = "1"

from app.candidate_reranker import FEATURES, OPTIONS, reordered_slots
from m0_r01_build_governance import GOLDEN, core, read_json, save_json, sha
from m0_r01_summarize import by_domain, rank_metrics
from m0_r01_compare import paired_rank, summarize_rank
from m1_r01_capture import stamp

import numpy as np
from sklearn import __version__ as sklearn_version
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

SEED = 1901
REGULARIZATION = (0.03, 0.3, 3.0)
MODES = ("keyword", "vector")
SIGNED_DOMAINS = ("强电", "暖通", "装饰", "医用气体", "弱电", "给排水")


def allowed(item, labels, original):
    return labels[item["id"]]["target_alternatives"][original]


def fit_examples(rows, items, labels):
    examples = []
    for row in rows:
        if row["split"] != "train":
            raise RuntimeError("Non-Train row passed to model fitting")
        gid = row["id"]
        item = items[gid]
        if not core(item) or labels[gid]["status"] != "answerable":
            continue
        targets = {code for original in item["target_codes"] for code in allowed(item, labels, original)}
        matches = [int(row["ranked"][slot][0] in targets) for slot, _, _ in row["features"]]
        positive = sum(matches)
        if positive and positive < len(matches):
            examples.append((row, matches, positive))
    return examples


def fit(examples, regularization):
    x, y, weights = [], [], []
    for row, matches, count in examples:
        x.extend(values for _, _, values in row["features"])
        y.extend(matches)
        weights.extend(0.5 / (count if match else len(matches) - count) for match in matches)
    matrix = np.asarray(x, dtype=np.float64)
    scaler = StandardScaler().fit(matrix)
    estimator = LogisticRegression(C=regularization, solver="liblinear", random_state=SEED, max_iter=1000)
    estimator.fit(scaler.transform(matrix), y, sample_weight=weights)
    weights = estimator.coef_[0] / scaler.scale_
    intercept = float(estimator.intercept_[0] - scaler.mean_.dot(weights))
    return weights.tolist(), intercept


def permute(row, profile, model):
    if profile == "legacy":
        return row["ranked"]
    depth, blend = OPTIONS[profile]
    features = [entry for entry in row["features"] if entry[0] < depth]
    ranked = list(row["ranked"])
    order = reordered_slots(features, *model, blend)
    for slot, original in order.items():
        ranked[slot] = row["ranked"][original]
    return ranked


def cv_train(rows, items, labels):
    examples = fit_examples(rows, items, labels)
    groups = [row["query_group"] for row, _, _ in examples]
    folds = list(GroupKFold(n_splits=4).split(examples, groups=groups))
    cv = {}
    for regularization in REGULARIZATION:
        scores = []
        for train, validation in folds:
            parameters = fit([examples[i] for i in train], regularization)
            rr = []
            for i in validation:
                row, _, _ = examples[i]
                gid = row["id"]
                targets = {code for codes in labels[gid]["target_alternatives"].values() for code in codes}
                ranked = permute(row, "linear-50", parameters)
                first = next((p for p, (code, blocked) in enumerate(ranked, 1) if code in targets and not blocked), None)
                rr.append(1 / first if first else 0.0)
            scores.append({"n": len(rr), "mrr_full": sum(rr) / len(rr)})
        cv[regularization] = scores
    best = max(REGULARIZATION, key=lambda c: sum(f["mrr_full"] * f["n"] for f in cv[c]))
    weights, intercept = fit(examples, best)
    return {
        "weights": weights, "intercept": intercept, "selected_c": best,
        "train_split_rows": len(rows), "train_rows_with_movable_positive_and_negative": len(examples),
        "train_query_groups": len(set(groups)), "train_candidate_rows": sum(len(y) for _, y, _ in examples),
        "train_positive_candidates": sum(n for _, _, n in examples), "cross_validation": cv,
        "train_ids_consumed_sha256": stamp(sorted(row["id"] for row, _, _ in examples)),
    }


def diagnose(cases, profile, model):
    rows, elapsed, moved = {}, [], 0
    for raw in cases:
        started = perf_counter_ns()
        ranked = permute(raw, profile, model)
        elapsed.append((perf_counter_ns() - started) / 1000)
        if ranked != raw["ranked"]:
            moved += 1
        positions = {code: (rank, blocked) for rank, (code, blocked) in enumerate(ranked, 1)}
        targets = {}
        for code, source in raw["recall_targets"].items():
            rank, blocked = positions.get(code, (None, False))
            state = raw["targets"][code] if rank is None else (
                "hard_blocked" if blocked else "top10_hit" if rank <= 10 else "ranked_gt10"
            )
            targets[code] = {**source, "state": state, "rank": rank}
        rows[raw["id"]] = {"targets": targets, "top10": [code for code, _ in ranked[:10]],
                           "pool_size": raw["pool_size"]}
    elapsed.sort()
    return rows, {"changed_ranks_count": moved, "ranking_kernel_cpu_n": len(elapsed),
                  "ranking_kernel_cpu_p50_us": elapsed[(len(elapsed) - 1) // 2],
                  "ranking_kernel_cpu_p95_us": elapsed[(len(elapsed) * 95 + 99) // 100 - 1],
                  "ranking_kernel_cpu_total_ms": sum(elapsed) / 1000}


def observations(items, labels, diagnostics):
    focus = {"GT-1138", "GT-1139", "GT-0339", "GT-0342", "GT-0343",
             "GT-0346", "GT-0347", "GT-0349", "GT-0350"}
    return {gid: {"target_rank": min((row["rank"] for row in diagnostics[gid]["targets"].values()
                                     if row["rank"] and row["state"] != "hard_blocked"), default=None),
                  "top1": next(iter(diagnostics[gid]["top10"]), None)}
            for gid in sorted(focus & items.keys())}


def cohort(items, labels, diagnostics):
    report = by_domain(items, lambda part: rank_metrics(
        part, diagnostics, lambda item, code: allowed(item, labels, code), exclude_blocked=True
    ))
    return {
        "all": summarize_rank(report["all"]),
        "domains": {domain: summarize_rank(report["domains"][domain]) if domain in report["domains"] else None
                    for domain in SIGNED_DOMAINS},
    }


def evaluate(pool, model, items, labels):
    cases = pool["rows"]
    answerable = {gid: it for gid, it in items.items() if core(it) and labels[gid]["status"] == "answerable"}
    arm_results = {}
    base_pairs = {}
    for profile in ("legacy", *OPTIONS):
        diagnostics, timing = diagnose(cases, profile, model)
        metrics = {}
        for split in ("train", "dev", "train_dev"):
            group = [it for gid, it in answerable.items() if split == "train_dev" or labels[gid]["split"] == split]
            ranked = {it["id"]: {"rank": min(
                (diagnostics[it["id"]]["targets"][code]["rank"] for original in it["target_codes"]
                 for code in allowed(it, labels, original)
                 if diagnostics[it["id"]]["targets"][code]["rank"] is not None
                 and diagnostics[it["id"]]["targets"][code]["state"] != "hard_blocked"), default=None)}
                for it in group}
            if profile == "legacy":
                base_pairs[split] = ranked
            metrics[split] = {**cohort(group, labels, diagnostics), "paired": paired_rank(base_pairs[split], ranked)}
        arm_results[profile] = {
            "metrics": metrics, **timing, "focus": observations(items, labels, diagnostics),
            "top10_by_id_sha256": stamp({gid: row["top10"] for gid, row in diagnostics.items()}),
        }
    return arm_results


def pick_http_pair(arms):
    """Choose a single strategy for the paired 20 vs 50 HTTP runs in each mode."""
    def key(profile):
        metrics = arms[profile]["metrics"]["dev"]["all"]
        return (metrics["hit@1"]["n"], metrics["mrr_at_10"], metrics["hit@3"]["n"],
                metrics["mrr_full"], profile.startswith("blend"), profile.endswith("20"))

    winner = max(OPTIONS, key=key)
    strategy = winner.split("-")[0]
    return {"dev_winner": winner, "http_profiles": [f"{strategy}-20", f"{strategy}-50"]}


def run(model_path, report_path):
    if model_path.exists() or report_path.exists():
        raise FileExistsError("Use new model and report paths")
    governance = ROOT / "tests/fixtures/golden_governance_v1.json"
    labels = read_json(governance)["samples"]
    items = {item["id"]: item for item in read_json(GOLDEN)["items"]
             if labels[item["id"]]["split"] in ("train", "dev")}
    expected = {split: sorted(gid for gid, row in labels.items() if row["split"] == split)
                for split in ("train", "dev", "test")}
    pools = {}
    model = {"version": "v019-m1-linear-v1", "seed": SEED, "features": FEATURES,
             "sklearn_version": sklearn_version, "models": {},
             "split_id_sha256": {k: stamp(v) for k, v in expected.items()}}
    for mode in MODES:
        path = EVIDENCE / f"m1_r01_{mode}_frozen.json.gz"
        with gzip.open(path, "rt", encoding="utf-8") as file:
            pool = json.load(file)
        rows = pool["rows"]
        if (pool["mode"] != mode or pool["golden_sha256"] != sha(GOLDEN) or pool["governance_sha256"] != sha(governance)
                or pool["split_id_sha256"] != model["split_id_sha256"] or len(rows) != len(items)
                or any(row["split"] != labels[row["id"]]["split"] for row in rows)
                or set(row["id"] for row in rows) != set(items)):
            raise RuntimeError(f"{mode} input failed split or snapshot checks")
        pools[mode] = pool
        params = cv_train([row for row in rows if row["split"] == "train"], items, labels)
        model["models"][mode] = {**params, "training_capture_sha256": sha(path)}
    model_path.parent.mkdir(parents=True, exist_ok=True)
    with model_path.open("x", encoding="utf-8") as file:
        json.dump(model, file, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    result = {"model_sha256": sha(model_path), "model_bytes": model_path.stat().st_size,
              "seed": SEED, "options": OPTIONS, "split_id_sha256": model["split_id_sha256"],
              "cross_encoder": "not_run_no_offline_weights", "exit_code": 0, "modes": {}, "http_selection": {}}
    m0 = read_json(EVIDENCE / "m0_r01_comparison.json")
    for mode in MODES:
        params = model["models"][mode]
        arms = evaluate(pools[mode], (params["weights"], params["intercept"]), items, labels)
        result["modes"][mode] = arms
        result["http_selection"][mode] = pick_http_pair(arms)
        arms["m0_signed_offline_reference"] = {
            profile: m0["modes"][mode]["profiles"][profile]["splits"] for profile in ("dedup", "calibrated")
        }
    save_json(report_path, result)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    run(args.model, args.out)
