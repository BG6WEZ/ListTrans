"""Verify the published HTTP pair, statistical denominators, and rule evidence."""
import argparse
from collections import Counter
import json
from pathlib import Path

from m2_r01_capture import EVIDENCE, ROOT, frozen_inputs
from m2_r01_fit import NEGATIVE
from m2_r01_http import GUARDS, PATHS
from m0_r01_build_governance import core, sha
from m0_r01_compare import with_interval
from m0_r01_summarize import percentage


def load(name):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8-sig"))


def wilson(n, d):
    return with_interval(percentage(n, d))


def counts(items, rows, details, labels, route):
    result = Counter({key: 0 for key in ("strict_auto", "equivalent_auto", "wrong_auto", "review",
                                         "needs_parameters", "explicit_reject")})
    assessed_auto = accepted_auto = assessed = 0
    for it in items:
        gid = it["id"]
        row = rows[gid][route]
        status = row["decision_status"]
        detail = details.get(gid) or {}
        label = labels[gid]
        scorable = core(it) and label["status"] in (NEGATIVE | {"answerable"})
        assessed += scorable
        if status == "auto_accepted":
            accepted_auto += 1
            if not scorable:
                result["unassessed_auto"] += 1
                continue
            assessed_auto += 1
            alternatives = {code for group in label["target_alternatives"].values() for code in group}
            correct = label["status"] == "answerable" and len(row["bindings"]) == 1 and row["bindings"][0] in alternatives
            if correct:
                result["strict_auto" if row["bindings"][0] in it["target_codes"] else "equivalent_auto"] += 1
            else:
                result["wrong_auto"] += 1
        elif detail.get("outcome") == "needs_parameters":
            result["needs_parameters"] += 1
        elif status == "rejected":
            result["explicit_reject"] += 1
        elif status == "recommended":
            result["review"] += 1
        else:
            result["decision_not_exposed"] += 1
    return {"counts": dict(result), "coverage_all": wilson(accepted_auto, len(items)),
            "coverage_assessed": wilson(assessed_auto, assessed),
            "precision_assessed": wilson(assessed_auto - result["wrong_auto"], assessed_auto)}


def arm_summary(run, items, labels):
    uploads = {}
    for split, metrics in run["splits"].items():
        decision = metrics["upload_decisions"]
        subset = [it for it in items if split == "train_dev" or labels[it["id"]]["split"] == split]
        uploads[split] = {
            **{route: counts(subset, run["rows"], run["acceptance_details"][route], labels, route)
               for route in ("match", "upload")},
            "correct_rejection": with_interval(decision["correct_rejection_rate"]),
            "wrong_proposed_answerable": with_interval(
                metrics["upload_answerable_decisions"]["assessed_wrong_proposed_binding_rate"]),
            "wrong_proposed_negative": with_interval(
                metrics["upload_negative_decisions"]["assessed_wrong_proposed_binding_rate"]),
            "wrong_auto_upload": with_interval(decision["assessed_wrong_auto_binding_rate"]),
            "assessed_upload_status_counts": decision["status_counts"],
        }
    return {
        "latency": run["latency"], "peak_working_set_mib": run["peak_wset_mib"],
        "budget_check": run["budget_check"], "semantic_rows": run["semantic_rows"],
        "quality": uploads, "http_probes": run["file_probes"], "source_sha256": run["calibration_source_sha256"],
        "failures": run["failed_rows"], "skips": run["skipped_rows"],
        "fitting_features_reproduced": run.get("fitting_features_reproduced"),
    }


def notable_cases(run, labels):
    rows = run["acceptance_details"]["upload"]
    relevant = {
        "explosion_unknown": list(GUARDS),
        "ambiguous": [gid for gid, row in rows.items() if labels[gid]["split"] == "dev"
                      and row["competitor_code"] and "ambiguous_distinct_candidate" in row["reason_codes"]][:3],
        "no_answer": sorted(
            (gid for gid, row in rows.items() if labels[gid]["split"] == "dev"
             and labels[gid]["status"] in NEGATIVE and row["outcome"] == "rejected"),
            key=lambda gid: ("no_answer" not in rows[gid]["reason_codes"], gid),
        )[:3],
        "equivalent_codes": [gid for gid, row in rows.items() if row["equivalent_codes"]][:3],
    }
    return {kind: {gid: rows[gid] for gid in gids} for kind, gids in relevant.items()}


def route_safety(run):
    probes = run["file_probes"]
    auto = probes["automatic"]
    return (
        set(probes["protected_unknown"]) == set(GUARDS)
        and all(p == {"status": "recommended", "direct_export": 422, "client_claim_export": 422,
                      "client_claim_restore": "recommended"} for p in probes["protected_unknown"].values())
        and probes["confirmed_status"] == probes["rejected_status"] == 200
        and probes["after_confirm"] == {"export": 200, "restore": "manual_confirmed"}
        and probes["after_reject"] == {"export": 422, "restore": "rejected"}
        and probes["changed_qty_after_confirmation_export"] == probes["changed_row_after_confirmation_export"] == 422
        and all(v == {"export": 200, "edited_export": 422, "restore": "auto_accepted"} for v in auto.values())
    )


def compare(mode, items, labels, fit):
    off, on = (load(f"m2_r01_{mode}_{arm}_http.json") for arm in ("off", "on"))
    m1 = load(f"m1_r01_{mode}_legacy_http.json")
    off_keys = ("rows", "per_sample_ans", "recall_order_sha256", "splits", "scores")
    parity = {k: off[k] == m1[k] for k in off_keys}
    paths = {p: sha(ROOT / p) for p in PATHS}
    for run in (off, on):
        measured_sources = run["calibration_source_sha256"]
        # OFF never reads the policy asset. Its independently timed baseline
        # can predate Dev's final threshold while exercising identical source.
        source_ok = all(measured_sources[p] == digest for p, digest in paths.items()
                        if run is on or p != "app/calibration_assets/v019_m2_policy.json")
        if (not source_ok or run["split_id_sha256"] != fit["policy"]["split_id_sha256"]
                or set(run["rows"]) != {it["id"] for it in items} or run["exit_code"] != 0
                or run["failed_rows"] or run["skipped_rows"]):
            raise RuntimeError(f"{mode}/{run['calibration_arm']} evidence provenance or population differs")
    fixed = {k: off[k] == on[k] for k in ("catalog_sha256", "golden_sha256", "governance_sha256",
                                          "vector_index", "recall_order_sha256", "scores", "per_sample_ans")}
    paired_top = sum((before["top"] != on["rows"][gid]["top"]) for gid, before in off["rows"].items())
    http_top = {route: sum(before[route]["top_code"] != on["rows"][gid][route]["top_code"]
                           for gid, before in off["rows"].items()) for route in ("match", "upload")}
    guard = {gid: detail for gid, detail in on["acceptance_details"]["upload"].items() if gid in GUARDS}
    guard_ok = len(guard) == len(GUARDS) and all(
        r["outcome"] == "needs_parameters" and "防爆" in r["missing_fields"]
        and not on["rows"][gid]["upload"]["bindings"] for gid, r in guard.items()
    )
    baseline, proposal = (arm_summary(run, items, labels) for run in (off, on))
    for split in ("train", "dev"):
        quality = proposal["quality"][split]
        expected = fit["selected"][split][mode]
        if (quality["correct_rejection"] != expected["explicit_negative_rejection"]
                or quality["upload"]["precision_assessed"] != expected["precision"]
                or quality["upload"]["coverage_assessed"] != expected["coverage"]):
            raise RuntimeError(f"{mode}/{split} HTTP decisions disagree with selected fitting output")
    before, after = (arm["quality"]["train_dev"] for arm in (baseline, proposal))
    deltas = {key: after[key]["n"] - before[key]["n"] for key in (
        "correct_rejection", "wrong_proposed_answerable", "wrong_proposed_negative", "wrong_auto_upload",
    )}
    return {
        "m1_off_parity": parity, "unchanged_ranking_inputs_on": fixed, "changed_top3_ranks": paired_top,
        "http_top_code_changes": http_top,
        "quality_checks": {
            "m1_off_parity": all(parity.values()),
            "fixed_ranking": not paired_top and not any(http_top.values()) and all(fixed.values()),
            "fitting_features_reproduced": on["fitting_features_reproduced"]["rows"] == len(items),
            "unknown_protection": guard_ok, "manual_and_export_authority": route_safety(off) and route_safety(on),
            "negative_rejection_nondecreasing": deltas["correct_rejection"] >= 0,
            "wrong_proposals_nonincreasing": max(deltas["wrong_proposed_answerable"], deltas["wrong_proposed_negative"]) <= 0,
            "zero_wrong_auto": not any(after[route]["counts"]["wrong_auto"] for route in ("match", "upload")),
            "guard_budget_off": all(baseline["budget_check"].values()),
            "guard_budget_on": all(proposal["budget_check"].values()),
        },
        "train_dev_count_delta": deltas, "profiles": {"off": baseline, "on": proposal},
        "off_policy_asset_read": False,
        "notable_upload_cases": notable_cases(on, labels),
    }


def run(output):
    frozen, _, items = frozen_inputs()
    fit = load("m2_r01_fit_report.json")
    off_gate = load("m2_r01_gate_off.json")
    on_gate = load("m2_r01_gate_on.json")
    group_counts = {history: {rule: x["score_diff_count"] for rule, x in by_rule.items()}
                    for history, by_rule in off_gate["comparisons"].items()}
    off_diffs = [n for row in group_counts.values() for n in row.values()]
    report = {
        "policy_sha256": fit["policy_sha256"], "split_id_sha256": fit["policy"]["split_id_sha256"],
        "train_rows": 842, "selection_dev_rows": 218, "test_held_out_rows": 160,
        "gate_off_group_diffs": group_counts, "gate_on_safety": on_gate["rule_safety_unchanged"],
        "gate_on_score_diffs_in_train_dev": {
            ref: {rule: len(rows) for rule, rows in by_rule.items()}
            for ref, by_rule in on_gate["comparisons"].items()
        },
        "rule_gate_checks": {
            "off_18_exact": len(off_diffs) == 18 and not any(off_diffs),
            "on_safety": on_gate["exit_code"] == 0 and all(on_gate["rule_safety_unchanged"].values())
            and on_gate["constrained_no_degradation"] and on_gate["probes_ok"],
        },
        "modes": {mode: compare(mode, items, frozen["samples"], fit) for mode in ("keyword", "vector")},
    }
    report["all_checks_passed"] = all(report["rule_gate_checks"].values()) and all(
        all(r["quality_checks"].values()) for r in report["modes"].values()
    )
    with output.open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    run(p.parse_args().out)
