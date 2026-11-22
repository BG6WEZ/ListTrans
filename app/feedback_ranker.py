"""V020-M0 offline experiment. Ordering never changes a business score or cap."""
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import unicodedata

from app.candidate_reranker import _known_value
from app.domain_extractors import detect_pump_state, detect_three_state_flag

FIELDS = (
    "管径", "外径", "材质", "型号", "防爆", "提升泵",
    "厚度", "尺寸", "额定电流", "电压", "主芯数", "主截面",
)
VERSION = "v020-m0-hard-negative-v1"
DEPTH = 20
_SAFETY_TEXT = re.compile(r"防爆|(?<![a-z0-9])EX(?![a-z0-9])|(?:提升|冷凝水|排水)泵|不带泵|带泵|无泵", re.I)
_SAFETY_NAMES = ("风机", "室内机", "多联机", "空调箱")
_GRADE = re.compile(r"(?<![a-z0-9])(?:sus|s)?(316l|304)(?![a-z0-9])", re.I)
_MODEL_FAMILY = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*)-[a-z0-9]+$")


def normalized(value):
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def structured_values(name, specification, parsed):
    text = f"{name or ''} {specification or ''}"
    values = {key: _known_value(parsed, key) for key in FIELDS}
    grade = _GRADE.search(str(values["材质"] or ""))
    if grade is None:
        grade = _GRADE.search(text) if "不锈钢" in text or re.search(r"\bSUS", text, re.I) else None
    if grade:
        values["材质"] = grade[1].casefold()
    values["型号"] = normalized(parsed.get("model")) or None
    values["防爆"] = detect_three_state_flag(text, ["防爆"]) if "未知" not in text else None
    values["提升泵"] = detect_pump_state(text, parsed.get("attrs"))
    return values


def differences(left, right):
    return tuple(key for key in FIELDS if left[key] is not None and right[key] is not None
                 and (left[key] != right[key] or isinstance(left[key], bool) != isinstance(right[key], bool)))


def model_suffix_differs(left, right):
    first, second = (_MODEL_FAMILY.fullmatch(value or "") for value in (left, right))
    return bool(first and second and first[1] == second[1] and left != right)


def features(query_values, candidate_values):
    mismatches = set(differences(query_values, candidate_values))
    return tuple(0 if query_values[key] is None or candidate_values[key] is None else
                 -1 if key in mismatches else 1 for key in FIELDS)


def _protected(name, specification, parsed):
    return (parsed.get("domain") in {"fan", "ahu", "fcu"}
            or any(term in str(name or "") for term in _SAFETY_NAMES)
            or bool(_SAFETY_TEXT.search(f"{name or ''} {specification or ''}")))


@lru_cache(maxsize=4)
def _load_weights(path, expected_digest):
    try:
        raw = Path(path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_digest:
            return None
        asset = json.loads(raw)
        if asset["version"] != VERSION or tuple(asset["fields"]) != FIELDS:
            return None
        weights = tuple(asset["weights"])
        if (len(weights) != len(FIELDS)
                or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                       for value in weights)
                or not 0 < sum(weights) <= 6):
            return None
        return weights, digest
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def active_ranker():
    if os.getenv("LISTTRANS_V020_M0_RANKER") != "on":
        return None
    path = os.getenv("LISTTRANS_V020_M0_RANKER_ASSET", "")
    digest = os.getenv("LISTTRANS_V020_M0_RANKER_SHA256", "")
    if not path or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return None
    # Invalid assets can be provisioned before a subsequent request.
    if not Path(path).is_file():
        return None
    return _load_weights(path, digest)


def rerank(query, candidates, parsed_candidates, model, *, exact_threshold):
    """Reorder only unblocked, uncapped slots within their original score band."""
    parsed = query.get("parsed_spec") or {}
    if (_protected(query.get("name"), query.get("specification"), parsed)
            or any("exact_code" in candidate["recall_sources"] for candidate in candidates)):
        return 0
    weights, _ = model
    query_values = structured_values(query.get("name"), query.get("specification"), parsed)
    bands = {False: [], True: []}
    for index, candidate in enumerate(candidates[:DEPTH]):
        cap, candidate_parsed = parsed_candidates[candidate["code"]]
        score = candidate["score"]
        if (candidate["hard_blocked"] or cap != 1 or score <= 0
                or _protected(candidate["name"], candidate["specification"], candidate_parsed)):
            continue
        values = structured_values(candidate["name"], candidate["specification"], candidate_parsed)
        preference = score + sum(weight * value for weight, value in
                                 zip(weights, features(query_values, values), strict=True))
        bands[score >= exact_threshold].append((index, preference))
    originals = candidates[:DEPTH]
    moved = 0
    for band in bands.values():
        ranked = sorted(band, key=lambda row: row[1], reverse=True)
        for (slot, _), (source, _) in zip(band, ranked, strict=True):
            candidates[slot] = originals[source]
            moved += slot != source
    return moved
