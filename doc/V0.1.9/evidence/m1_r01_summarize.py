"""Compare the frozen offline screening and paired HTTP replays."""
import argparse
from collections import Counter
import json
from pathlib import Path

from m0_r01_compare import summarize_decisions

EVIDENCE = Path(__file__).resolve().parent


def load(name):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8-sig"))


def http_summary(run):
    return {
        "latency": run["latency"], "peak_working_set_mib": run["peak_wset_mib"],
        "budget_check": run["budget_check"], "rerank_cpu": run["rerank_cpu"],
        "failures": run["failed_rows"], "skips": run["skipped_rows"],
        "splits": {name: {"upload": summarize_decisions(split), "guards": split["gt_guard_status"],
                           "kit": split["kit_confirmed_all_components"]}
                   for name, split in run["splits"].items()},
    }


def changed_rows(before, after):
    if before.keys() != after.keys():
        raise RuntimeError("HTTP row identity changed")
    changes = {}
    counts = Counter({key: 0 for key in ("single", "upload", "upload_binding_codes",
                                         "upload_decision_status", "is_exact_hint")})
    for gid, old in before.items():
        new = after[gid]
        changed = {
            "single": old["match"] != new["match"],
            "upload": old["upload"] != new["upload"],
            "upload_binding_codes": old["upload"]["bindings"] != new["upload"]["bindings"],
            "upload_decision_status": old["upload"]["decision_status"] != new["upload"]["decision_status"],
            "is_exact_hint": old["upload"]["is_exact"] != new["upload"]["is_exact"],
        }
        if any(changed.values()):
            counts.update({k: int(v) for k, v in changed.items()})
            changes[gid] = {"comparison": changed, "baseline": old, "experiment": new}
    return {"counts": dict(counts), "per_row": changes}


def compare(mode, profiles, fit, prior):
    runs = {profile: load(f"m1_r01_{mode}_{profile}_http.json") for profile in profiles}
    baseline = runs["legacy"]
    m0 = load(f"m0_r01_{mode}_legacy_final.json")
    keys = (
        "rank", "upload_decisions", "upload_answerable_decisions",
        "upload_negative_decisions", "kit_confirmed_all_components",
    )
    rollback = {split: {key: m0["splits"][split][key] == baseline["splits"][split][key] for key in keys}
                for split in ("train", "dev", "train_dev")}
    row_parity = {key: m0[key] == baseline[key] for key in ("rows", "per_sample_ans")}
    if not all(flag for split in rollback.values() for flag in split.values()) or not all(row_parity.values()):
        raise RuntimeError(f"{mode} OFF failed the M0 release reproduction")
    for profile, run in runs.items():
        if any(run[k] != baseline[k] for k in (
            "catalog_sha256", "golden_sha256", "governance_sha256", "model_sha256", "source_sha256",
            "vector_index", "recall_order_sha256",
        )):
            raise RuntimeError(f"{mode}/{profile} did not use the common frozen context")
        if run["live_top10_sha256"] != fit[profile]["top10_by_id_sha256"]:
            raise RuntimeError("Measured and offline rankings differ")
    signed_domains = {}
    requirements = ("candidate_recall@100", "candidate_recall@200", "hit@1")
    prior_domains = prior["v017_to_v018_attribution"]["splits"]["train_dev"]["domains"]
    for profile in profiles:
        signed_domains[profile] = {}
        for domain, rank in fit[profile]["metrics"]["train_dev"]["domains"].items():
            old = prior_domains.get(domain)
            if old is None or rank is None:
                signed_domains[profile][domain] = {"rows": 0, "measured": False}
                continue
            signed_domains[profile][domain] = {
                "rows": rank["rows"], "measured": True,
                "same_slice_v017": {metric: old[metric]["n"] for metric in requirements},
                "current": {metric: rank[metric]["n"] for metric in requirements},
                "delta": {metric: rank[metric]["n"] - old[metric]["n"] for metric in requirements},
            }
    if any(runs[profile]["splits"]["train_dev"]["upload_decisions"]["assessed_wrong_auto_binding_rate"]["n"]
           or not all(s == "recommended" for s in runs[profile]["splits"]["train_dev"]["gt_guard_status"].values())
           or len(runs[profile]["splits"]["train_dev"]["gt_guard_status"]) != 3 for profile in profiles):
        raise RuntimeError(f"{mode} changed a binding safety gate")
    return {
        "m0_off_reproduction": rollback, "m0_off_row_parity": row_parity,
        "v017_signed_domain_same_slice": signed_domains,
        "arms": {p: http_summary(run) for p, run in runs.items()},
        "per_row_pairs": {p: changed_rows(baseline["rows"], run["rows"])
                          for p, run in runs.items() if p != "legacy"},
        "depth_50_vs_20": changed_rows(runs[profiles[1]]["rows"], runs[profiles[2]]["rows"]),
    }


def main(output):
    fit = load("m1_r01_offline_comparison.json")
    prior = load("m0_r01_comparison.json")
    result = {"exit_code": 0, "model_sha256": fit["model_sha256"], "split_id_sha256": fit["split_id_sha256"],
              "cross_encoder": fit["cross_encoder"], "default": "off", "modes": {}}
    for mode, plan in fit["http_selection"].items():
        modes = result["modes"]
        profiles = ["legacy", *plan["http_profiles"]]
        modes[mode] = compare(mode, profiles, fit["modes"][mode], prior["modes"][mode])
    with output.open("x", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    main(args.out)
