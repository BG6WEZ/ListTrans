"""Fit Train confidence and select a joint policy using Dev only."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"

from m2_r01_capture import EVIDENCE, EQUIVALENCES, GOVERNANCE, ROOT, frozen_inputs, stamp
from m0_r01_build_governance import GOLDEN, core, read_json, save_json, sha
from m0_r01_compare import with_interval
from m0_r01_summarize import percentage
from app.acceptance_calibration import FEATURES, VERSION, Policy, confidence

import numpy as np
from sklearn import __version__ as sklearn_version
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

SEED = 1902
MODES = ("keyword", "vector")
MARGINS = (0.1, 0.5, 1., 2., 3., 5., 10.)
THRESHOLDS = (0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.92, 0.94, 0.96, 0.98, 0.99, 1.0)
REJECT_LEVELS = (0., 10., 20., 30., 40., 50., 60., 70.)
NEGATIVE = frozenset({"disabled_or_retired", "no_substitute"})
VARIABLE_REASONS = frozenset({"ambiguous_distinct_candidate", "confidence_below_policy"})


def load_capture():
    governance, split_ids, items = frozen_inputs()
    ids = {it["id"] for it in items}
    splits = {k: stamp(v) for k, v in split_ids.items()}
    rows = {split: {mode: [] for mode in MODES} for split in ("train", "dev")}
    for mode in MODES:
        record = read_json(EVIDENCE / f"m2_r01_{mode}_capture.json")
        if (record["mode"] != mode or record["split_id_sha256"] != splits
                or record["catalog_sha256"] != governance["inputs"]["app/materials.db"]
                or record["governance_sha256"] != sha(GOVERNANCE) or record["golden_sha256"] != sha(GOLDEN)
                or record["equivalence_sha256"] != sha(EQUIVALENCES) or set(record["rows"]) != ids
                or tuple(record["features"]) != FEATURES):
            raise RuntimeError("Frozen capture identity mismatch")
        for item in items:
            gid = item["id"]
            label = governance["samples"][gid]
            if not core(item) or label["status"] not in NEGATIVE | {"answerable"}:
                continue
            proposal = record["rows"][gid]
            approved = {code for group in label["target_alternatives"].values() for code in group}
            rows[label["split"]][mode].append({
                "id": gid, "split": label["split"], "answerable": label["status"] == "answerable",
                "correct": proposal["top_code"] in approved,
                "strict": proposal["top_code"] in item["target_codes"],
                **proposal,
            })
    return rows, splits


def fit(rows):
    consumed = [row for mode in MODES for row in rows[mode]]
    if any(row["split"] != "train" for row in consumed):
        raise RuntimeError("Confidence fitting requires Train")
    x = np.asarray([r["features"] for r in consumed], dtype=np.float64)
    labels = [r["correct"] for r in consumed]
    scaler = StandardScaler().fit(x)
    estimator = LogisticRegression(C=0.3, solver="liblinear", random_state=SEED, max_iter=1000)
    estimator.fit(scaler.transform(x), labels)
    weights = estimator.coef_[0] / scaler.scale_
    return tuple(float(x) for x in weights), float(estimator.intercept_[0] - scaler.mean_.dot(weights))


def prediction(row, policy):
    flags = set(row["reason_codes"]) - VARIABLE_REASONS
    if flags & {"no_answer", "hard_conflict"}:
        return "rejected"
    if row["ranking_score"] < policy.reject_below and not row["missing_fields"]:
        return "rejected"
    if row["missing_fields"]:
        return "needs_parameters"
    gap = row["distinct_margin"]
    if (not flags and gap is not None and gap >= policy.min_margin
            and policy.threshold is not None and row["fitted_confidence"] >= policy.threshold):
        return "accepted"
    return "review"


def evaluate(rows, policy):
    summary = Counter()
    for row in rows:
        state = prediction(row, policy)
        summary["answerable"] += row["answerable"]
        summary["negative"] += not row["answerable"]
        summary["auto_accepted"] += state == "accepted"
        summary["wrong_auto"] += state == "accepted" and not row["correct"]
        summary["strict_auto"] += state == "accepted" and row["correct"] and row["strict"]
        summary["equivalent_auto"] += state == "accepted" and row["correct"] and not row["strict"]
        summary["rejected_negatives"] += state == "rejected" and not row["answerable"]
        summary["rejected_correct_top"] += state == "rejected" and row["correct"]
        summary["additional_rejected_correct_top"] += (
            state == "rejected" and row["correct"] and not set(row["reason_codes"]) & {"no_answer", "hard_conflict"}
        )
        summary[state] += 1
    accepted = summary["auto_accepted"]
    return {
        "rows": len(rows), "counts": dict(summary),
        "precision": with_interval(percentage(accepted - summary["wrong_auto"], accepted)),
        "coverage": with_interval(percentage(accepted, len(rows))),
        "explicit_negative_rejection": with_interval(percentage(summary["rejected_negatives"], summary["negative"])),
    }


def run(model, out):
    if model.exists() or out.exists():
        raise FileExistsError("Use new output paths")
    rows, split_heads = load_capture()
    weights, intercept = fit(rows["train"])
    base = Policy(weights, intercept, None, MARGINS[0], 0, {})
    for split in ("train", "dev"):
        for mode in MODES:
            for row in rows[split][mode]:
                row["fitted_confidence"] = confidence(row["features"], base)

    def policy(margin, threshold, reject):
        return Policy(weights, intercept, threshold, margin, reject, {})

    def stats(split, candidate):
        return {mode: evaluate(rows[split][mode], candidate) for mode in MODES}

    # Train filters policies before Dev selection. No relaxation of physical gates.
    train_reject = []
    for reject in REJECT_LEVELS:
        training = stats("train", policy(MARGINS[0], None, reject))
        if not any(p["counts"]["additional_rejected_correct_top"] for p in training.values()):
            train_reject.append(reject)
    reject_grid = {}
    for reject in REJECT_LEVELS:
        reject_grid[reject] = {"train": stats("train", policy(MARGINS[0], None, reject)),
                               "dev": stats("dev", policy(MARGINS[0], None, reject))}
    selectable_reject = [r for r in train_reject if not any(
        p["counts"]["additional_rejected_correct_top"] for p in reject_grid[r]["dev"].values()
    )]
    selected_reject = max(selectable_reject, key=lambda reject: (
        sum(p["counts"]["rejected_negatives"] for p in reject_grid[reject]["dev"].values()),
        sum(p["counts"]["rejected"] for p in reject_grid[reject]["train"].values()),
        -reject,
    ))
    grid = []
    for margin in MARGINS:
        for threshold in THRESHOLDS:
            candidate = policy(margin, threshold, selected_reject)
            training = stats("train", candidate)
            dev = stats("dev", candidate)
            safe = not any(p["counts"]["wrong_auto"] for p in [*training.values(), *dev.values()])
            grid.append({"margin": margin, "threshold": threshold, "train": training, "dev": dev, "zero_errors": safe})
    candidates = [r for r in grid if r["zero_errors"] and sum(p["counts"]["auto_accepted"] for p in r["dev"].values())]
    winner = max(candidates, key=lambda r: (
        sum(p["counts"]["auto_accepted"] for p in r["dev"].values()),
        min(p["counts"]["auto_accepted"] for p in r["dev"].values()), r["margin"], r["threshold"],
    ), default=None)
    selected = policy(winner["margin"] if winner else max(MARGINS),
                      winner["threshold"] if winner else None, selected_reject)
    fit_report = {
        "version": VERSION, "features": FEATURES, "seed": SEED, "sklearn_version": sklearn_version,
        "weights": weights, "intercept": intercept, "threshold": selected.threshold,
        "min_margin": selected.min_margin, "reject_below": selected.reject_below,
        "equivalence_sha256": sha(EQUIVALENCES), "split_id_sha256": split_heads,
        "train_inputs_sha256": {mode: stamp(sorted(r["id"] for r in rows["train"][mode])) for mode in MODES},
    }
    model.parent.mkdir(parents=True, exist_ok=True)
    with model.open("x", encoding="utf-8") as file:
        json.dump(fit_report, file, sort_keys=True, separators=(",", ":"))
    # A fixed-margin slice is the Dev precision–coverage curve. Keep the full
    # margin grid to expose zero-denominator and non-adopted points.
    save_json(out, {
        "exit_code": 0, "policy_sha256": sha(model), "policy": fit_report,
        "counts": {s: {m: len(r) for m, r in groups.items()} for s, groups in rows.items()},
        "selected": {s: stats(s, selected) for s in ("train", "dev")},
        "dev_precision_coverage": [r for r in grid if r["margin"] == selected.min_margin],
        "threshold_sweep": grid, "rejection_sweep": reject_grid,
        "train_rejection_candidates": train_reject,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.model, args.out)
