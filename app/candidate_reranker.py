"""Optional V019-M1 CPU reranking of a fixed, business-scored prefix."""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re


MODEL_PATH = Path(__file__).with_name("rerank_candidates") / "v019_m1_linear.json"
FEATURES = (
    "business_score", "reciprocal_rank", "semantic_score", "semantic_channel",
    "model_channel", "fts_channel", "name_overlap", "spec_overlap",
    "model_exact", "literal_spec_match", "attrs_in_tolerance", "conflict_reason",
    "matched_typed_fields", "different_typed_fields", "missing_typed_fields", "model_field_match",
)
OPTIONS = {"linear-20": (20, 1.0), "linear-50": (50, 1.0),
           "blend-20": (20, 0.30), "blend-50": (50, 0.30)}
_TOKEN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]")
_FIELDS = (
    "管径", "外径", "材质", "额定电流", "电压", "功率", "风量", "机外余压",
    "主芯数", "主截面", "零芯数", "零截面", "厚度", "壁厚", "尺寸", "连接方式", "防爆", "提升泵",
)


def _tokens(value):
    return frozenset(_TOKEN.findall(str(value or "").casefold()))


def _overlap(query_tokens, text):
    candidate_tokens = _tokens(text)
    union = query_tokens | candidate_tokens
    return len(query_tokens & candidate_tokens) / len(union) if union else 0.0


def _known_value(parsed, key):
    typed = (parsed.get("typed_attrs") or {}).get(key)
    if isinstance(typed, dict):
        if typed.get("status") == "unknown":
            return None
        # A range or inequality is not a scalar exact match.
        if typed.get("operator") not in (None, "", "=", "==", "not"):
            return None
        value = typed.get("value")
    else:
        value = (parsed.get("attrs") or {}).get(key)
    if isinstance(value, str):
        return value.strip().casefold() or None
    if isinstance(value, (bool, int, float)) and math.isfinite(value):
        return value
    return None


def feature_rows(query, candidates, *, depth, exact_threshold):
    """Return only movable slots and their label-free, bounded feature vectors."""
    if any(row.get("recall_source") == "exact_code" or "exact_code" in row["recall_sources"] for row in candidates):
        return []
    parsed = query.get("parsed_spec") or {}
    fields = [(key, value) for key in _FIELDS if (value := _known_value(parsed, key)) is not None]
    model = str(parsed.get("model") or "").strip().casefold()
    name_tokens = _tokens(query.get("name"))
    spec_tokens = _tokens(query.get("specification"))
    rows = []
    for index, candidate in enumerate(candidates[:depth]):
        cap, semantic, candidate_parsed = candidate["_rerank_inputs"]
        score = candidate["score"]
        if candidate["hard_blocked"] or cap != 1.0 or not math.isfinite(score) or score <= 0.0:
            continue
        reasons = candidate["match_reasons"]
        sources = candidate["recall_sources"]
        comparisons = [_known_value(candidate_parsed, key) for key, _ in fields]
        matched = different = missing = 0
        for (_, query_value), candidate_value in zip(fields, comparisons, strict=True):
            if candidate_value is None:
                missing += 1
            elif query_value == candidate_value and isinstance(query_value, bool) == isinstance(candidate_value, bool):
                matched += 1
            else:
                different += 1
        total = len(fields) or 1
        candidate_model = str(candidate_parsed.get("model") or "").strip().casefold()
        rows.append((index, score >= exact_threshold, (
            score / 100, 1 / (index + 1), max(0.0, min(1.0, semantic)) if math.isfinite(semantic) else 0.0,
            float("semantic" in sources), float("model_match" in sources),
            float(any(source.startswith("fts") for source in sources)),
            _overlap(name_tokens, candidate["name"]), _overlap(spec_tokens, candidate["specification"]),
            float("型号完全匹配" in reasons or "型号忽略符号匹配" in reasons),
            float("名称与规格完全一致" in reasons or "规格型号完全一致" in reasons),
            float("核心参数在公差范围内" in reasons),
            float(any("冲突" in reason or "偏离" in reason or "拦截" in reason for reason in reasons)),
            matched / total, different / total, missing / total, float(bool(model) and model == candidate_model),
        )))
    return rows


@lru_cache(maxsize=1)
def _load_model():
    try:
        raw = MODEL_PATH.read_bytes()
        data = json.loads(raw)
        if data["version"] != "v019-m1-linear-v1" or tuple(data["features"]) != FEATURES:
            return None
        models = {}
        for mode in ("keyword", "vector"):
            weights = tuple(float(value) for value in data["models"][mode]["weights"])
            intercept = float(data["models"][mode]["intercept"])
            if len(weights) != len(FEATURES) or not all(math.isfinite(x) for x in (*weights, intercept)):
                return None
            models[mode] = (weights, intercept)
        return models, hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def rank_value(features, weights, intercept, blend):
    linear = intercept + sum(x * w for x, w in zip(features, weights, strict=True))
    probability = (math.tanh(linear / 2) + 1) / 2
    return (1 - blend) * features[0] + blend * probability


def reordered_slots(rows, weights, intercept, blend):
    """Permute eligible indices within each unchanged business threshold band."""
    bands = {False: [], True: []}
    for index, band, features in rows:
        bands[band].append((index, rank_value(features, weights, intercept, blend)))
    order = {}
    for positions in bands.values():
        ranked = sorted(positions, key=lambda pair: pair[1], reverse=True)
        order.update((slot, original) for (slot, _), (original, _) in zip(positions, ranked, strict=True))
    return order


def rerank(query, candidates, profile, *, exact_threshold):
    """Keep scores, ineligible slots, the tail, and candidate membership intact."""
    options = OPTIONS.get(profile)
    model = _load_model() if options and len(candidates) > 1 else None
    if model is None:
        return None
    depth, blend = options
    rows = feature_rows(query, candidates, depth=depth, exact_threshold=exact_threshold)
    if len(rows) < 2:
        return None
    mode = "vector" if any("semantic" in row["recall_sources"] for row in candidates) else "keyword"
    models, digest = model
    order = reordered_slots(rows, *models[mode], blend)
    original = candidates[:depth]
    for slot, index in order.items():
        candidates[slot] = original[index]
    return {"profile": profile, "depth": depth, "mode": mode, "model_sha256": digest,
            "eligible_count": len(rows), "moved_count": sum(slot != index for slot, index in order.items())}
