"""Read-only classification of the first workbench recommendation."""

import math
from collections import Counter, defaultdict

from app.acceptance_calibration import _physical_evidence, confidence, match_features
from app.binding_decisions import STATUS_AUTO_ACCEPTED, STATUS_REJECTED, evaluate_hard_conflicts

VERSION = "v021-m0-initial-match-v1"


def assess_initial_match(row, query, match, decision, source, policy, live, db_revision):
    bindings = decision.get("bindings") or []
    top = next(iter(match.get("top_candidates") or ()), {})
    score = float(top.get("score") or 0)
    score = score if math.isfinite(score) else 0
    rank = match.get("_initial_match_rank") or {}
    margin = rank.get("margin")
    if match.get("_initial_rank_stale"):
        rank_state = "stale"
    elif not rank or rank.get("top_code") != top.get("code") or rank.get("ranking_score") != score:
        rank_state = "unavailable"
    else:
        rank_state = "current" if margin is not None else "no_comparable_candidate"

    tier, reason, subtype = "D", "no_candidate", None
    output, comparisons = None, []
    effective = []
    valid_codes = all(binding.get("code") in live for binding in bindings)
    unavailable = source == "matcher" and (policy is None or rank_state in ("stale", "unavailable"))
    if source in ("bom", "rule"):
        if bindings and valid_codes and decision["decision_status"] == STATUS_AUTO_ACCEPTED:
            tier, reason, effective = "A", "verified_binding", bindings
        elif bindings and not valid_codes:
            reason = "invalid_code"
        elif decision["decision_status"] == STATUS_REJECTED:
            reason = "hard_conflict"
        else:
            reason = "unverified_binding"
    elif top.get("hard_blocked") or decision["decision_status"] == STATUS_REJECTED:
        reason = "hard_conflict"
    elif top and score > 0:
        material = live.get(top.get("code"))
        if material is None or not valid_codes:
            reason = "invalid_code"
        elif evaluate_hard_conflicts(row, material, allow_unit_conversion=False):
            reason = "hard_conflict"
        else:
            physical = _physical_evidence(row, query, material if match.get("_initial_rank_stale") else top)
            comparisons = physical["comparisons"]
            uncapped = rank_state in ("current", "no_comparable_candidate") and rank.get("uncapped") is True
            if not unavailable:
                output = confidence(match_features(score, margin, uncapped, physical), policy)

            # The workbench starts from the actual matcher binding, never a bare suggestion.
            if len(bindings) == 1 and bindings[0]["code"] == top["code"]:
                effective = bindings
            if any(field["safety_critical_unknown"] for field in comparisons):
                tier, reason, subtype, effective = "C", "safety_missing", "safety_missing", []
            elif score < 60.0 and effective:
                tier, reason, subtype = "C", "low_score_prefill", "low_score_prefill"
            elif not effective:
                reason = "no_initial_binding"
            elif (score >= 75.0 and output is not None and output >= 0.65
                  and not (set(physical["different"]) - {"核心规格"})):
                tier, reason = "A", "priority_review"
            else:
                tier, reason = "B", "candidate_review"

    reasons = [reason]
    if unavailable and top and score > 0:
        reasons.append("priority_review_unavailable")
    usable_rank = rank_state in ("current", "no_comparable_candidate")
    return {
        "tier": tier, "subtype": subtype, "source": source,
        "effective_bindings": [dict(binding) for binding in effective],
        "actual_prefilled": bool(effective), "reason_codes": reasons,
        "ranking_score": score if source == "matcher" and top else None,
        "distinct_margin": margin if source == "matcher" and usable_rank else None,
        "competitor_code": rank.get("competitor_code") if source == "matcher" and usable_rank else None,
        "ranking_evidence_status": rank_state if source == "matcher" else "not_applicable",
        "uncapped": rank.get("uncapped") if source == "matcher" and usable_rank else None,
        "priority_review_unavailable": unavailable and bool(top) and score > 0,
        "model_output": output, "field_comparisons": comparisons,
        "db_revision": db_revision, "policy_version": VERSION,
        "professional": str(row.get("professional") or "暖通"),
    }


def summarize_initial_matches(items):
    """Count a single set of authoritative source rows for all views."""
    counts = defaultdict(list)
    seen = set()
    for row in items:
        identity = tuple(row.get(key) for key in ("document_id", "sheet_id", "source_row_id"))
        if all(identity):
            if identity in seen:
                continue
            seen.add(identity)
        assessment = row.get("initial_match")
        professional = (assessment or {}).get("professional") or row.get("professional") or "暖通"
        counts[str(professional)].append(assessment)

    def summarize(assessments):
        total = len(assessments)
        known = [item for item in assessments if item is not None]
        prefilled = sum(item["actual_prefilled"] for item in known)
        tiers = Counter(item["tier"] for item in known)
        subtypes = Counter(item["subtype"] for item in known if item["tier"] == "C")
        return {
            "total_rows": total, "known_rows": len(known), "missing_rows": total - len(known),
            "actual_prefilled": prefilled,
            "prefill_rate": prefilled / total if total and len(known) == total else None,
            "status": ("empty" if not total else "unrecorded" if not known
                       else "partial" if len(known) < total else "recorded"),
            "tiers": {tier: tiers[tier] for tier in "ABCD"},
            "c_subtypes": {key: subtypes[key] for key in ("low_score_prefill", "safety_missing")},
        }

    return {
        **summarize([item for assessments in counts.values() for item in assessments]),
        "by_professional": {name: summarize(items) for name, items in counts.items()},
    }
