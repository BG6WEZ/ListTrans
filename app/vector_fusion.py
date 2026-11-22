# -*- coding: utf-8 -*-
"""V018-M2: Canonical vector encoding text, configurable TopK, and deterministic RRF fusion.

Design constraints:
- Zero import-time DB or file side effects.
- Master switch `LISTTRANS_V018_M2_RECALL` defaults to "0" (OFF); sub-options (`text_mode`,
  `vector_top_k`, `fusion_mode`, `rrf_k`, `candidate_cap`) default to legacy values so turning
  M2 OFF reproduces the M1-released / M0-released path verbatim.
- Reuses offline `bge-small-zh-v1.5` (512-dim, BERT tokenizer `model_max_length=512`).
- Deterministic truncation rule (head + spec tail within `MAX_ENCODE_CHARS=384`), duplicate
  candidate merge, deterministic tie-breaking, and candidate cap.
- Never modifies ranking weights, auto-accept thresholds, or frozen config/golden assets.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

VECTOR_FUSION_VERSION = "v018-m2-fusion-v1"
VECTOR_TEXT_SCHEMA_LEGACY = "legacy-v1"
VECTOR_TEXT_SCHEMA_CANONICAL = "v018-m2-canon-v1"

DEFAULT_V018_M2_ENABLED = False
DEFAULT_VECTOR_TEXT_MODE = "legacy"   # "legacy" | "canonical"
DEFAULT_VECTOR_TOPK = 50              # 50 | 100 | 200
DEFAULT_FUSION_MODE = "union"         # "union" | "rrf"
DEFAULT_RRF_K = 60
DEFAULT_CANDIDATE_CAP = 0             # 0 = no truncation beyond channel union

ALLOWED_VECTOR_TOPK = (50, 100, 200)
ALLOWED_TEXT_MODES = ("legacy", "canonical")
ALLOWED_FUSION_MODES = ("union", "rrf")

# Deterministic char budget safely below bge-small-zh-v1.5 model_max_length=512 BERT tokens
MAX_ENCODE_CHARS = 384
HEAD_KEEP_CHARS = 256
TAIL_KEEP_CHARS = 120

# Default RRF channel weights (balanced sparse + dense; exact_code is always pinned first)
DEFAULT_RRF_CHANNEL_WEIGHTS: Dict[str, float] = {
    "exact_code": 5.0,
    "structured_model": 1.4,
    "model_match": 1.2,
    "semantic": 1.25,
    "sparse_name_param": 1.2,
    "structured_recall": 1.1,
    "fts": 1.0,
    "sparse_alias": 1.0,
    "fts_extracted_name": 0.9,
    "fts_zh": 0.85,
    "sparse_phrase_split": 0.85,
    "sparse_param_fallback": 0.6,
    "fallback": 0.5,
}

_WS_RE = re.compile(r'\s+')
_NOISE_SECTION_RE = re.compile(
    r'(?:^|\n)\s*(?:\d+[\.．、\s]*)?(?:工作内容|施工内容|施工部位|安装要求|施工要求|备注|其他)\s*[:：].*',
    re.DOTALL,
)
_LEADING_QTY_RE = re.compile(r'^\s*\d+\s*(?:个|套|台|只|组|件|根|米|条|块|扇|樘|项)\s*')
_DEVICE_TAG_RE = re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{2,5}(?:-[A-Za-z0-9]+)+)(?![A-Za-z0-9])')


def is_v018_m2_recall_enabled(query_info: Optional[Dict[str, Any]] = None) -> bool:
    """Return True if V018-M2 vector/fusion experimental options are enabled."""
    if isinstance(query_info, dict):
        opts = query_info.get("recall_options")
        if isinstance(opts, dict) and "v018_m2_enabled" in opts:
            return bool(opts["v018_m2_enabled"])
    raw = os.getenv("LISTTRANS_V018_M2_RECALL", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def get_vector_fusion_options(query_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve effective V018-M2 options (env defaults overridden by per-query recall_options)."""
    enabled = is_v018_m2_recall_enabled(query_info)
    if not enabled:
        return {
            "v018_m2_enabled": False,
            "vector_text_mode": DEFAULT_VECTOR_TEXT_MODE,
            "text_schema_version": VECTOR_TEXT_SCHEMA_LEGACY,
            "vector_top_k": DEFAULT_VECTOR_TOPK,
            "fusion_mode": DEFAULT_FUSION_MODE,
            "rrf_k": DEFAULT_RRF_K,
            "candidate_cap": DEFAULT_CANDIDATE_CAP,
            "version": VECTOR_FUSION_VERSION,
            "default_enabled": DEFAULT_V018_M2_ENABLED,
        }

    ro = query_info.get("recall_options") if isinstance(query_info, dict) and isinstance(query_info.get("recall_options"), dict) else {}

    text_mode = str(ro.get("vector_text_mode") or os.getenv("LISTTRANS_V018_M2_TEXT_MODE", DEFAULT_VECTOR_TEXT_MODE)).strip().lower()
    if text_mode not in ALLOWED_TEXT_MODES:
        text_mode = DEFAULT_VECTOR_TEXT_MODE

    raw_k = ro.get("vector_top_k") if "vector_top_k" in ro else os.getenv("LISTTRANS_V018_M2_VECTOR_TOPK", str(DEFAULT_VECTOR_TOPK))
    try:
        top_k = int(raw_k)
    except (TypeError, ValueError):
        top_k = DEFAULT_VECTOR_TOPK
    if top_k <= 0:
        top_k = DEFAULT_VECTOR_TOPK
    top_k = min(top_k, 200)

    fusion_mode = str(ro.get("fusion_mode") or os.getenv("LISTTRANS_V018_M2_FUSION_MODE", DEFAULT_FUSION_MODE)).strip().lower()
    if fusion_mode not in ALLOWED_FUSION_MODES:
        fusion_mode = DEFAULT_FUSION_MODE

    raw_rrf_k = ro.get("rrf_k") if "rrf_k" in ro else os.getenv("LISTTRANS_V018_M2_RRF_K", str(DEFAULT_RRF_K))
    try:
        rrf_k = max(1, int(raw_rrf_k))
    except (TypeError, ValueError):
        rrf_k = DEFAULT_RRF_K

    raw_cap = ro.get("candidate_cap") if "candidate_cap" in ro else os.getenv("LISTTRANS_V018_M2_CANDIDATE_CAP", str(DEFAULT_CANDIDATE_CAP))
    try:
        candidate_cap = max(0, int(raw_cap))
    except (TypeError, ValueError):
        candidate_cap = DEFAULT_CANDIDATE_CAP

    schema_ver = VECTOR_TEXT_SCHEMA_CANONICAL if text_mode == "canonical" else VECTOR_TEXT_SCHEMA_LEGACY
    return {
        "v018_m2_enabled": True,
        "vector_text_mode": text_mode,
        "text_schema_version": schema_ver,
        "vector_top_k": top_k,
        "fusion_mode": fusion_mode,
        "rrf_k": rrf_k,
        "candidate_cap": candidate_cap,
        "version": VECTOR_FUSION_VERSION,
        "default_enabled": DEFAULT_V018_M2_ENABLED,
    }


def truncate_encode_text(text: str, max_chars: int = MAX_ENCODE_CHARS) -> Tuple[str, bool]:
    """Deterministic head+tail truncation so tail spec parameters survive BERT 512-token cutoff."""
    clean = _WS_RE.sub(" ", (text or "").replace("\r", " ").replace("\n", " ")).strip()
    if len(clean) <= max_chars:
        return clean, False
    head_len = min(HEAD_KEEP_CHARS, max(16, max_chars - TAIL_KEEP_CHARS - 3))
    tail_len = max(0, max_chars - head_len - 3)
    if tail_len <= 0:
        return clean[:max_chars], True
    return f"{clean[:head_len].rstrip()} | {clean[-tail_len:].lstrip()}", True


def build_material_vector_text(
    material: Dict[str, Any],
    mode: str = DEFAULT_VECTOR_TEXT_MODE,
) -> Tuple[str, Dict[str, Any]]:
    """Format a catalog material row into encoding text under legacy or canonical schema."""
    name = str(material.get("name") or "").strip()
    spec = str(material.get("specification") or "").strip()
    cat = str(material.get("category") or "").strip()
    brand = str(material.get("brand") or "").strip()

    if mode != "canonical":
        raw = f"{name} {spec}".strip()
        return raw, {
            "text_schema_version": VECTOR_TEXT_SCHEMA_LEGACY,
            "truncated": False,
            "original_chars": len(raw),
            "final_chars": len(raw),
        }

    parts: List[str] = []
    if cat:
        parts.append(f"品类:{cat}")
    if name:
        parts.append(f"名称:{name}")
    if spec:
        parts.append(f"规格:{spec}")
    if brand and brand not in spec and brand not in name:
        parts.append(f"品牌:{brand}")
    joined = " | ".join(parts)
    final, truncated = truncate_encode_text(joined, MAX_ENCODE_CHARS)
    return final, {
        "text_schema_version": VECTOR_TEXT_SCHEMA_CANONICAL,
        "truncated": truncated,
        "original_chars": len(joined),
        "final_chars": len(final),
    }


def build_query_vector_text(
    query_info: Dict[str, Any],
    parsed_spec: Optional[Dict[str, Any]] = None,
    mode: str = DEFAULT_VECTOR_TEXT_MODE,
) -> Tuple[str, Dict[str, Any]]:
    """Format a query into encoding text under legacy or canonical schema.

    Canonical mode:
    - Uses unpolluted original_name (avoiding over-specific alias_dict rewrites)
    - Strips device tags / electrical box numbers (AHU-J-01, AP-1) while keeping product models
    - Strips 工作内容/施工内容 noise
    - Preserves professional/discipline, material grade, DN/OD, dimensions, and model
    - Applies deterministic head+tail truncation at MAX_ENCODE_CHARS=384
    """
    q_name = str(query_info.get("name") or "").strip()
    q_spec = str(query_info.get("specification") or "").strip()
    if mode != "canonical":
        raw = f"{q_name} {q_spec}".strip()
        return raw, {
            "text_schema_version": VECTOR_TEXT_SCHEMA_LEGACY,
            "truncated": False,
            "original_chars": len(raw),
            "final_chars": len(raw),
            "removed_tags": [],
            "kept_model": "",
        }

    from app.preprocessor import is_device_tag
    from app.rule_context import normalize_professional_category

    q_parsed = parsed_spec if isinstance(parsed_spec, dict) else (query_info.get("parsed_spec") or {})
    q_attrs = q_parsed.get("attrs", {}) if isinstance(q_parsed, dict) else {}

    prof_raw = str(query_info.get("professional") or "").strip()
    prof = normalize_professional_category(prof_raw) if prof_raw else ""
    orig_name = str(query_info.get("original_name") or q_name).strip()
    orig_spec = str(q_parsed.get("clean_text") or query_info.get("original_spec") or q_spec).strip()
    orig_spec = _NOISE_SECTION_RE.sub("", orig_spec).strip()

    removed_tags: List[str] = []

    def _strip_tags(s: str) -> str:
        s = _LEADING_QTY_RE.sub("", s)
        def _repl(m: re.Match) -> str:
            tok = m.group(1)
            if is_device_tag(tok):
                if tok not in removed_tags:
                    removed_tags.append(tok)
                return " "
            return tok
        return _WS_RE.sub(" ", _DEVICE_TAG_RE.sub(_repl, s)).strip()

    clean_name = _strip_tags(orig_name) or _strip_tags(q_name)
    clean_spec = _strip_tags(orig_spec)

    kept_model = str(q_parsed.get("model") or q_attrs.get("型号") or "").strip()
    if kept_model and is_device_tag(kept_model):
        kept_model = ""

    # Structured spec summary tail (concise, deterministic, protects key specs on long queries)
    spec_bits: List[str] = []
    if kept_model and kept_model not in clean_spec and kept_model not in clean_name:
        spec_bits.append(f"型号:{kept_model}")
    for key in ("材质", "管径", "外径", "尺寸"):
        val = q_attrs.get(key)
        if val is not None and str(val).strip():
            sval = str(val).strip()
            if key == "外径" and not sval.lower().startswith("de"):
                sval = f"De{sval}"
            if sval not in clean_spec and sval not in clean_name:
                spec_bits.append(f"{key}:{sval}")

    parts: List[str] = []
    if prof and prof != "其它":
        parts.append(f"专业:{prof}")
    if clean_name:
        parts.append(f"名称:{clean_name}")
    if clean_spec:
        parts.append(f"规格:{clean_spec}")
    if spec_bits:
        parts.append("参数:" + " ".join(spec_bits))

    joined = " | ".join(parts)
    final, truncated = truncate_encode_text(joined, MAX_ENCODE_CHARS)
    return final, {
        "text_schema_version": VECTOR_TEXT_SCHEMA_CANONICAL,
        "truncated": truncated,
        "original_chars": len(joined),
        "final_chars": len(final),
        "removed_tags": removed_tags,
        "kept_model": kept_model,
    }


def _channel_weight(channel: str) -> float:
    if channel in DEFAULT_RRF_CHANNEL_WEIGHTS:
        return DEFAULT_RRF_CHANNEL_WEIGHTS[channel]
    if channel.startswith("fts_hvac_"):
        return 0.75
    return 0.8


def fuse_recall_candidates(
    candidates: List[Dict[str, Any]],
    *,
    fusion_mode: str = DEFAULT_FUSION_MODE,
    rrf_k: int = DEFAULT_RRF_K,
    candidate_cap: int = DEFAULT_CANDIDATE_CAP,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Deterministically deduplicate, merge, and optionally RRF-fuse recall candidates.

    Preserves every channel's original `recall_sources`, `channel_ranks`, `recall_reasons`,
    and `semantic_score`. Exact-code hits are always pinned ahead of non-exact candidates.
    Tie-breaker in RRF mode: (-is_exact_code, -round(rrf_score, 10), -round(semantic_score, 8), min_rank, code).
    """
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    duplicate_merges = 0

    for idx, raw_cand in enumerate(candidates, 1):
        code = str(raw_cand.get("code") or "").strip()
        if not code:
            continue
        if code not in merged:
            c = dict(raw_cand)
            srcs = list(dict.fromkeys(s for s in (c.get("recall_sources") or [c.get("recall_source", "")]) if s))
            c["recall_sources"] = srcs
            ranks = dict(c.get("channel_ranks") or {})
            # Ensure every listed channel has a deterministic 1-based rank fallback
            for s in srcs:
                if s not in ranks or ranks[s] is None:
                    ranks[s] = idx
            c["channel_ranks"] = ranks
            c["recall_reasons"] = list(dict.fromkeys(c.get("recall_reasons") or []))
            c["semantic_score"] = float(c.get("semantic_score") or 0.0)
            merged[code] = c
            order.append(code)
        else:
            duplicate_merges += 1
            dst = merged[code]
            dst["semantic_score"] = max(float(dst.get("semantic_score") or 0.0), float(raw_cand.get("semantic_score") or 0.0))
            raw_srcs = list(raw_cand.get("recall_sources") or [raw_cand.get("recall_source", "")])
            raw_ranks = raw_cand.get("channel_ranks") or {}
            for s in raw_srcs:
                if s and s not in dst["recall_sources"]:
                    dst["recall_sources"].append(s)
                if s:
                    rk = raw_ranks.get(s, idx)
                    if rk is not None and (s not in dst["channel_ranks"] or rk < dst["channel_ranks"][s]):
                        dst["channel_ranks"][s] = rk
            for r in (raw_cand.get("recall_reasons") or []):
                if r and r not in dst["recall_reasons"]:
                    dst["recall_reasons"].append(r)

    pre_count = len(order)

    if fusion_mode != "rrf":
        result = [merged[code] for code in order]
        if candidate_cap > 0 and len(result) > candidate_cap:
            result = result[:candidate_cap]
        return result, {
            "fusion_mode": "union",
            "pre_fusion_count": pre_count,
            "post_fusion_count": len(result),
            "truncated_count": max(0, pre_count - len(result)),
            "duplicate_merges": duplicate_merges,
            "candidate_cap": candidate_cap,
        }

    scored_rows: List[Tuple[Tuple[int, float, float, int, str], Dict[str, Any]]] = []
    for code in order:
        cand = merged[code]
        ranks = cand.get("channel_ranks") or {}
        srcs = cand.get("recall_sources") or []
        rrf_score = 0.0
        min_rank = 10**9
        for ch in srcs:
            rk = int(ranks.get(ch) or 1000)
            if rk < min_rank:
                min_rank = rk
            rrf_score += _channel_weight(ch) / float(rrf_k + rk)
        is_exact = 1 if "exact_code" in srcs else 0
        sem = float(cand.get("semantic_score") or 0.0)
        cand["fusion_score"] = round(rrf_score, 8)
        sort_key = (-is_exact, -round(rrf_score, 10), -round(sem, 8), min_rank, code)
        scored_rows.append((sort_key, cand))

    scored_rows.sort(key=lambda pair: pair[0])
    fused: List[Dict[str, Any]] = []
    for f_rank, (_, cand) in enumerate(scored_rows, 1):
        cand["fusion_rank"] = f_rank
        reason = f"rrf(k={rrf_k},score={cand['fusion_score']:.6f},rank={f_rank})"
        if reason not in cand["recall_reasons"]:
            cand["recall_reasons"].append(reason)
        fused.append(cand)

    if candidate_cap > 0 and len(fused) > candidate_cap:
        fused = fused[:candidate_cap]

    return fused, {
        "fusion_mode": "rrf",
        "rrf_k": rrf_k,
        "pre_fusion_count": pre_count,
        "post_fusion_count": len(fused),
        "truncated_count": max(0, pre_count - len(fused)),
        "duplicate_merges": duplicate_merges,
        "candidate_cap": candidate_cap,
    }
