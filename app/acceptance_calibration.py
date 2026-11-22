"""V019-M2 matcher acceptance. Ranking and binding authority are separate."""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import unicodedata

ASSETS = Path(__file__).with_name("calibration_assets")
VERSION = "v019-m2-calibration-v1"
FEATURES = ("ranking_score", "distinct_margin", "exact_name", "exact_spec",
            "exact_model", "core_agreement", "core_completeness", "uncapped")
_FIELD_ALIASES = {
    "管径": ("管径", "dn_val", "公称直径DN"), "外径": ("外径", "外径mm", "de_val"),
    "外径mm": ("外径", "外径mm", "de_val"),
    "机外余压": ("机外余压", "余压"), "尺寸": ("尺寸", "尺寸_2d"),
}
_PHYSICAL = (
    "材质", "管径", "外径", "型号族", "额定电流", "电压", "功率", "风量",
    "机外余压", "流量", "扬程", "主芯数", "主截面", "零芯数", "零截面",
    "芯数", "截面积", "壁厚", "厚度", "尺寸", "连接方式",
)
_UNKNOWN = frozenset({"", "-", "—", "/", "未知", "未说明", "待定", "unknown", "n/a", "none"})
_UNKNOWN_FLAG_VALUE = r"(?:未知|未说明|待定|不详|未明确|unknown\b|n/a\b)"


@dataclass(frozen=True)
class Policy:
    weights: tuple[float, ...]
    intercept: float
    threshold: float | None
    min_margin: float
    reject_below: float
    equivalences: dict[str, frozenset[str]]


@lru_cache(maxsize=1)
def load_policy():
    try:
        config = json.loads((ASSETS / "v019_m2_policy.json").read_bytes())
        group_bytes = (ASSETS / "v019_m2_equivalences.json").read_bytes()
        groups = json.loads(group_bytes)
        if config["version"] != VERSION or tuple(config["features"]) != FEATURES:
            return None
        if config["equivalence_sha256"] != hashlib.sha256(group_bytes).hexdigest():
            return None
        weights = tuple(float(x) for x in config["weights"])
        intercept = float(config["intercept"])
        margin = float(config["min_margin"])
        reject = float(config["reject_below"])
        threshold = None if config["threshold"] is None else float(config["threshold"])
        if (len(weights) != len(FEATURES) or not all(math.isfinite(x) for x in (*weights, intercept))
                or not 0 < margin <= 100 or not 0 <= reject < 75
                or (threshold is not None and not 0 < threshold <= 1)):
            return None
        equivalences = {code: frozenset(codes) for code, codes in groups["equivalent_code_groups"].items()
                        if isinstance(codes, list) and len(codes) > 1 and code in codes
                        and all(isinstance(member, str) for member in codes)}
        return Policy(weights, intercept, threshold, margin, reject, equivalences)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return None


def active_policy():
    return load_policy() if os.getenv("LISTTRANS_V019_M2_CALIBRATION", "off") == "on" else None


def _text(value):
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def _nfc(value):
    return unicodedata.normalize("NFC", str(value or "")).strip()


def _equivalent(left, right, approved):
    if left["code"] not in approved.get(right["code"], ()):
        return False
    identity = ("name", "specification", "unit", "category", "brand", "description")
    if any(_nfc(left.get(field)) != _nfc(right.get(field)) for field in identity):
        return False

    def parsed(row):
        value = row.get("parsed_params")
        return json.loads(value) if isinstance(value, str) and value.strip() else value or {}

    try:
        return parsed(left) == parsed(right)
    except (ValueError, TypeError):
        return False


def ranking_evidence(candidates, approved, uncapped):
    """Use the full ranked pool. Ignore only still-identical frozen equivalents."""
    if not candidates:
        return None
    top = candidates[0]
    competitor = None
    equivalent_codes = []
    for other in candidates[1:]:
        if other.get("hard_blocked") or other["score"] <= 0:
            continue
        if _equivalent(top, other, approved):
            equivalent_codes.append(other["code"])
        elif competitor is None or other["score"] > competitor["score"]:
            competitor = other
    return {
        "top_code": top["code"], "ranking_score": top["score"], "uncapped": uncapped,
        "competitor_code": competitor["code"] if competitor else None,
        "margin": top["score"] - competitor["score"] if competitor else None,
        "equivalent_codes": equivalent_codes,
    }


def _known(value):
    if isinstance(value, str):
        return _text(value) not in _UNKNOWN
    if isinstance(value, (bool, int, float)):
        return math.isfinite(value)
    return bool(value)


def _field(attrs, key):
    for alias in _FIELD_ALIASES.get(key, (key,)):
        value = attrs.get(alias)
        if _known(value):
            return value
    return None


def _equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return _text(left) == _text(right)


def _explicit_unknown(text, label):
    return re.search(rf"(?:{label})\s*[:：=]?\s*{_UNKNOWN_FLAG_VALUE}", text, re.I) is not None


def _physical_evidence(row, query, material):
    from app.domain_extractors import detect_pump_state, detect_three_state_flag, domain_schema
    from app.domain_router import route_domain
    from app.matcher import (
        KNOWN_MATERIAL_GRADES, _dn_field_to_float, get_professional_category, get_unit_multiplier,
        normalize_material_grade, resolve_material_attrs,
    )
    from app.preprocessor import extract_model
    from app.rule_context import canonicalize_professional

    parsed = query.get("parsed_spec") or {}
    q_attrs = parsed.get("attrs") or {}
    c_attrs = resolve_material_attrs(material)
    q_name, c_name = row.get("original_name"), material.get("name")
    q_spec, c_spec = row.get("original_spec"), material.get("specification")
    q_text = f"{q_name or ''} {q_spec or ''}"
    c_spec_text = f"{c_spec or ''} {material.get('description') or ''}".strip()
    c_text = f"{c_name or ''} {c_spec_text}"
    q_prof = canonicalize_professional(row.get("professional"))
    c_prof = canonicalize_professional(get_professional_category(material.get("category", ""), c_spec_text))
    q_domain = parsed.get("domain", "generic")
    c_domain = route_domain(material.get("category", ""), c_name or "", c_spec_text, q_prof)
    fields = set()
    for domain in (q_domain, c_domain):
        fields.update(f["key"] for f in domain_schema.get(domain, {}).get("must", ()))
    fields.update(key for key in _PHYSICAL if _field(q_attrs, key) is not None)
    fields.update(key for key in ("材质", "管径", "外径") if _field(c_attrs, key) is not None)

    missing, different = [], []
    equal = 0
    for key in sorted(fields):
        query_value, candidate_value = _field(q_attrs, key), _field(c_attrs, key)
        if key == "管径":
            query_value, candidate_value = map(_dn_field_to_float, (query_value, candidate_value))
        if key == "材质":
            grades = [normalize_material_grade(value) for value in (query_value, candidate_value)]
            if any(g in KNOWN_MATERIAL_GRADES for g in grades) or any(
                "不锈钢" in str(value) for value in (query_value, candidate_value)
            ):
                query_value, candidate_value = (g if g in KNOWN_MATERIAL_GRADES else None for g in grades)
                key = "材质牌号"
        if query_value is None or candidate_value is None:
            missing.append(key)
        elif _equal(query_value, candidate_value):
            equal += 1
        else:
            different.append(key)

    if q_prof in ("", "全部", "其它", "通用") or c_prof in ("", "全部", "其它", "通用"):
        missing.append("专业")
    elif q_prof != c_prof:
        different.append("专业")
    q_unit, c_unit = row.get("original_unit"), material.get("unit")
    if not _known(q_unit) or not _known(c_unit):
        missing.append("单位倍率")
    elif get_unit_multiplier(str(q_unit), str(c_unit)) != 1.0:
        different.append("单位倍率")

    def explosion_state(text):
        if _explicit_unknown(text, r"防爆(?:要求|等级)?|(?<![a-z0-9])EX(?![a-z0-9])"):
            return None
        state = detect_three_state_flag(text, ["防爆", "防爆型", "防爆风机"])
        if state is None and re.search(r"(?<![a-z0-9])EX(?![a-z0-9])", text, re.I):
            return detect_three_state_flag(text.upper(), ["EX"])
        return state

    def pump_state(text, attrs):
        if _explicit_unknown(text, r"(?:提升|冷凝水|排水)泵"):
            return None
        return detect_pump_state(text, attrs)

    for key, states, relevant in (
        ("防爆", (explosion_state(q_text), explosion_state(c_text)),
         any(domain in ("fan", "ahu") for domain in (q_domain, c_domain)) or "风机" in f"{q_name}{c_name}"),
        ("提升泵", (pump_state(q_text, q_attrs), pump_state(c_text, c_attrs)),
         any(term in f"{q_name}{c_name}" for term in ("风机盘管", "室内机", "多联机"))),
    ):
        a, b = states
        if relevant or a is not None or b is not None:
            if a is None or b is None:
                missing.append(key)
            elif a != b:
                different.append(key)

    q_model = _text(parsed.get("model"))
    c_model = _text(extract_model(c_spec_text))
    model_exact = bool(q_model and q_model == c_model)
    if q_model and not c_model:
        missing.append("型号")
    elif q_model and not model_exact:
        different.append("型号")
    spec_exact = _known(q_spec) and _text(q_spec) == _text(c_spec)
    if not _known(q_spec) or not _known(c_spec):
        missing.append("核心规格")
    elif not spec_exact and not (model_exact and fields and equal == len(fields)):
        different.append("核心规格")
    return {
        "missing": missing, "different": different, "exact_name": _text(q_name) == _text(c_name),
        "exact_spec": bool(spec_exact), "exact_model": model_exact,
        "core_agreement": equal / len(fields) if fields else float(spec_exact),
        "core_completeness": (len(fields) - len([k for k in missing if k in fields or k == "材质牌号"])) / len(fields)
        if fields else float(_known(q_spec) and _known(c_spec)),
    }


def confidence(features, policy):
    value = policy.intercept + sum(f * w for f, w in zip(features, policy.weights, strict=True))
    return (math.tanh(value / 2) + 1) / 2


def assess_match(row, query, match, policy, *, trusted=True):
    """Apply non-compensating safeguards before a learned confidence threshold."""
    from app.binding_decisions import (
        STATUS_AUTO_ACCEPTED, STATUS_RECOMMENDED, STATUS_REJECTED, evaluate_hard_conflicts,
    )

    top = next(iter(match.get("top_candidates") or ()), {})
    ranking = match.get("_acceptance_rank") or {}
    score = float(top.get("score") or 0.0)
    if not math.isfinite(score):
        score = 0.0
    evidence_current = ranking.get("top_code") == top.get("code") and ranking.get("ranking_score") == score
    margin = ranking.get("margin") if evidence_current else None
    uncapped = evidence_current and ranking.get("uncapped") is True
    reasons = []
    physical = {"missing": [], "different": [], "exact_name": False, "exact_spec": False,
                "exact_model": False, "core_agreement": 0.0, "core_completeness": 0.0}
    hard = None
    if top.get("hard_blocked"):
        reasons.append("hard_conflict")
    elif not top or score <= 0:
        reasons.append("no_answer")
    elif not trusted:
        reasons.append("untrusted_parameter_override")
    else:
        hard = evaluate_hard_conflicts(row, top, allow_unit_conversion=False)
        if hard:
            reasons.append("hard_conflict")
        else:
            physical = _physical_evidence(row, query, top)
            if physical["missing"]:
                reasons.append("missing_critical_fields")
            if physical["different"]:
                reasons.append("different_critical_fields")
            if not uncapped:
                reasons.append("score_cap_or_stale_rank")
            if margin is None or margin < policy.min_margin:
                reasons.append("ambiguous_distinct_candidate")
            if score < 75:
                reasons.append("insufficient_ranking_evidence")
    features = (score / 100, max(-1.0, min(1.0, margin / 100)) if margin is not None else 0.0,
                float(physical["exact_name"]), float(physical["exact_spec"]), float(physical["exact_model"]),
                physical["core_agreement"], physical["core_completeness"], float(uncapped))
    ready = not reasons
    value = confidence(features, policy)
    if "no_answer" in reasons or "hard_conflict" in reasons or (
        trusted and score < policy.reject_below and not physical["missing"]
    ):
        status, outcome = STATUS_REJECTED, "rejected"
        if not {"no_answer", "hard_conflict"}.intersection(reasons):
            reasons.append("weak_evidence")
    elif ready and policy.threshold is not None and value >= policy.threshold:
        status, outcome = STATUS_AUTO_ACCEPTED, "accepted"
        reasons.append("joint_evidence_and_confidence")
    else:
        status = STATUS_RECOMMENDED
        outcome = "needs_parameters" if physical["missing"] else "review"
        if ready:
            reasons.append("confidence_below_policy")
    return {
        "decision_status": status, "outcome": outcome, "confidence": value, "ranking_score": score,
        "top_code": top.get("code"), "distinct_margin": margin,
        "competitor_code": ranking.get("competitor_code") if evidence_current else None,
        "equivalent_codes": ranking.get("equivalent_codes", []) if evidence_current else [],
        "reason_codes": reasons, "missing_fields": physical["missing"], "different_fields": physical["different"],
        "hard_conflict": hard, "eligible": ready, "features": features,
    }


def confirmed_binding(row, match, assessment):
    """Return server-sourced live fields and quantity or downgrade an unsafe decision."""
    from app.binding_decisions import (
        STATUS_AUTO_ACCEPTED, STATUS_RECOMMENDED, evaluate_hard_conflicts, fetch_live_material_map,
    )

    if assessment["decision_status"] != STATUS_AUTO_ACCEPTED:
        return []
    top = match["top_candidates"][0]
    live = fetch_live_material_map([top["code"]]).get(top["code"])
    qty = row.get("original_qty")
    same = live and all(live.get(k) == top.get(k) for k in (
        "specification", "name", "unit", "category", "brand", "description", "parsed_params",
    ))
    if (not same or not isinstance(qty, (float, int)) or isinstance(qty, bool)
            or not math.isfinite(qty) or qty <= 0 or evaluate_hard_conflicts(row, live, allow_unit_conversion=False)):
        assessment.update(decision_status=STATUS_RECOMMENDED, outcome="review", eligible=False)
        assessment["reason_codes"].append("live_material_or_quantity_changed")
        return []
    return [{**{k: live.get(k) or "" for k in ("code", "name", "specification", "unit", "brand", "category")},
             "qty": float(qty), "qty_ratio": 1.0}]
