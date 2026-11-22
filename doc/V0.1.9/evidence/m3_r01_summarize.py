"""Read completed M3 measurements and compare with the signed V0.1.7 criteria."""
import argparse
import json
from pathlib import Path

from m0_r01_compare import with_interval
from m3_r01_quality import EVIDENCE, sha, verify_freeze

SIGNED = {
    "keyword": {"rank": [399, 426, 180, 254, .309329, .314174], "rejected": 43,
                "wrong": [256, 87], "domains": [[81, 82, 45], [62, 79, 33], [80, 83, 26],
                                               [72, 74, 23], [70, 74, 37], [18, 18, 7]]},
    "vector": {"rank": [594, 643, 183, 288, .344091, .355303], "rejected": 42,
               "wrong": [368, 109], "domains": [[166, 171, 50], [69, 90, 29], [114, 117, 29],
                                               [92, 94, 22], [83, 98, 25], [50, 50, 19]]},
}
RANK = ("candidate_recall@100", "candidate_recall@200", "hit@1", "hit@3", "mrr_at_10", "mrr_full")
DOMAINS = ("强电", "暖通", "装饰", "医用气体", "弱电", "给排水")


def load(name):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8-sig"))


def value(metric):
    return metric["n"] if isinstance(metric, dict) else metric


def summarize_mode(mode, freeze_sha):
    report = load(f"m3_r01_final_{mode}.json")
    claim = load(f"m3_r01_final_{mode}.claim.json")
    if report["freeze_sha256"] != freeze_sha or claim["freeze_sha256"] != freeze_sha:
        raise RuntimeError("Final run used a different freeze")
    full = report["splits"]["full"]
    test = report["splits"]["test"]
    if full["counts"]["total"] != 1220 or test["counts"]["total"] != 160:
        raise RuntimeError("Incorrect full or held-out population")
    all_rows = full["rank"]["all"]
    signed = SIGNED[mode]
    ranking = {}
    for key, floor in zip(RANK, signed["rank"], strict=True):
        observed = all_rows[key]
        ranking[key] = {"value": with_interval(observed) if isinstance(observed, dict) else observed,
                        "v017_min": floor, "delta": round(value(observed) - floor, 6),
                        "passed": value(observed) >= floor}
    domains = {}
    for domain, floors in zip(DOMAINS, signed["domains"], strict=True):
        relevant = full["rank"]["domains"][domain]
        domains[domain] = {key: {"value": with_interval(relevant[key]), "v017_min": floor,
                                 "delta": value(relevant[key]) - floor, "passed": value(relevant[key]) >= floor}
                           for key, floor in zip(RANK[:3], floors, strict=True)}
    rejected = full["upload_decisions"]["correct_rejection_rate"]["n"]
    wrong = [full[k]["assessed_wrong_proposed_binding_rate"]["n"]
             for k in ("upload_answerable_decisions", "upload_negative_decisions")]
    error_count = full["upload_decisions"]["assessed_wrong_auto_binding_rate"]["n"]
    precision = full["upload_decisions"]["assessed_auto_accepted_accuracy"]
    checks = {
        "ranking": all(r["passed"] for r in ranking.values()),
        "six_domains": all(r["passed"] for domain in domains.values() for r in domain.values()),
        "negative_rejection": rejected >= signed["rejected"],
        "wrong_proposals": all(v <= floor for v, floor in zip(wrong, signed["wrong"], strict=True)),
        "zero_wrong_auto": error_count == 0,
        "perfect_precision_if_accepted": precision["d"] == 0 or precision["n"] == precision["d"],
        "kit_top10": full["kit_confirmed_all_components"]["hit@10"]["n"] >= 3,
        "performance": all(report["budget_checks"].values()),
    }
    return {"checks": checks, "ranking": ranking, "six_domain_floors": domains,
            "negative_delta": rejected - signed["rejected"],
            "wrong_proposal_delta": [v - floor for v, floor in zip(wrong, signed["wrong"], strict=True)],
            "counts_and_decisions": {name: {key: split[key] for key in (
                "counts", "upload_decisions", "upload_answerable_decisions", "upload_negative_decisions",
                "kit_confirmed_all_components",
            )} for name, split in (("full", full), ("test", test))},
            "test_rank": test["rank"], "latency": report["latency"], "budget_checks": report["budget_checks"],
            "peak_working_set_mib": report["peak_working_set_mib"]}


def run(output):
    freeze = EVIDENCE / "m3_r01_pre_holdout_freeze.json"
    verify_freeze(load(freeze.name))
    stamp = sha(freeze)
    output_data = {"freeze_sha256": stamp,
                   "modes": {mode: summarize_mode(mode, stamp) for mode in ("keyword", "vector")},
                   "experiment_disposition": "keep_default_off",
                   "m2_on_clean_budget": load("m3_r01_m2_on.json")["budget_checks"]}
    with output.open("x", encoding="utf-8") as file:
        json.dump(output_data, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out)
