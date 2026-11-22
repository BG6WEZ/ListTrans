# -*- coding: utf-8 -*-
"""V018-M1: Canonical name / scoped alias governance and multi-tier sparse FTS recall.

Design constraints:
- Zero import-time DB or file side effects.
- Independent default-OFF toggle (`LISTTRANS_V018_M1_RECALL`, default "0") or per-query
  `recall_options["v018_m1_enabled"]`; turning OFF reproduces the M0-released path verbatim.
- Frozen `config/alias_dict.json` stays strictly read-only; candidate alias rules live in
  `app/recall_candidates/v018_m1_aliases.json` (versioned & rollbackable).
- Reuses existing `materials_fts` via `db_manager.search_materials_fts_raw` / `search_materials_keyword`
  (respects `db_revision` cache key and returns detached candidate copies).
- Preserves channel name, per-channel 1-based rank (`channel_ranks`), and inclusion reason
  (`recall_reasons`); caps candidate volume within a strict budget.
- Never mis-expands material grades or physical specifications, and never bypasses downstream
  hard constraints or binding gates.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.preprocessor import extract_device_tag, fts_tokenize, is_device_tag
from app.rule_context import normalize_professional_category

SPARSE_RECALL_VERSION = "v018-m1-sparse-v1"
DEFAULT_V018_M1_ENABLED = False
DEFAULT_SPARSE_LIMIT = 60
CANDIDATE_ALIAS_PATH = Path(__file__).resolve().parent / "recall_candidates" / "v018_m1_aliases.json"

_CONFIG_LOCK = threading.RLock()
_CACHED_ALIAS_CFG: Optional[Dict[str, Any]] = None

_LEADING_QTY_RE = re.compile(r'^\s*\d+\s*(?:个|套|台|只|组|件|根|米|条|块|扇|樘|项)\s*')
_LEADING_INDEX_TAG_RE = re.compile(r'^\s*(?:\d+#|[A-Za-z]{1,4}\d*#)\s*')
_DEVICE_TAG_INLINE_RE = re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{2,5}(?:-[A-Za-z0-9]+)+)(?![A-Za-z0-9])')
_PAREN_NOISE_RE = re.compile(r'[（\(][^）\)]{1,40}[）\)]')
_ZH_SEQ_RE = re.compile(r'[\u4e00-\u9fff]+')
_CABLE_FULL_RE = re.compile(
    r'(?<![A-Za-z0-9])((?:WDZ[A-Z0-9]*|NH|ZR|Z[ABC]N?)-?(?:YJY22|YJV22|YJY|YJV|RYYSP|RYYP|RYSP|RYY|RYJS|RYJ|KVVP2|KVVP|KVV|RVVP|RVV|BYJR|BYJ|BVR|BV|BLV))'
    r'[\s\-]*(?:(\d+\s*[*x×]\s*\d+(?:\.\d+)?(?:\s*\+\s*\d+\s*[*x×]\s*\d+(?:\.\d+)?)?)|(\d+(?:\.\d+)?)\s*(?:mm2|mm²|平方)?)',
    re.IGNORECASE,
)
_CORE_COUNT_RE = re.compile(r'(\d+)\s*芯')
_PORT_COUNT_RE = re.compile(r'(\d+)\s*口')
_GAS_MEDIA = (
    ("CO2", ("CO2", "二氧化碳")),
    ("N2O", ("N2O", "笑气", "氧化亚氮")),
    ("N2", ("N2", "氮气")),
    ("O2", ("O2", "氧气")),
    ("负压", ("负压", "吸引", "真空")),
    ("压缩空气", ("压缩空气", "医用空气")),
)


def is_v018_m1_recall_enabled(query_info: Optional[Dict[str, Any]] = None) -> bool:
    """Return True if V018-M1 sparse recall channel is enabled for this call."""
    if isinstance(query_info, dict):
        opts = query_info.get("recall_options")
        if isinstance(opts, dict) and "v018_m1_enabled" in opts:
            return bool(opts["v018_m1_enabled"])
    raw = os.getenv("LISTTRANS_V018_M1_RECALL", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def get_sparse_recall_options(query_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve effective M1 sparse recall options."""
    enabled = is_v018_m1_recall_enabled(query_info)
    opts: Dict[str, Any] = {
        "v018_m1_enabled": enabled,
        "sparse_limit": DEFAULT_SPARSE_LIMIT,
        "version": SPARSE_RECALL_VERSION,
        "default_enabled": DEFAULT_V018_M1_ENABLED,
    }
    if isinstance(query_info, dict) and isinstance(query_info.get("recall_options"), dict):
        ro = query_info["recall_options"]
        if "sparse_limit" in ro:
            opts["sparse_limit"] = max(10, min(120, int(ro["sparse_limit"])))
    return opts


def load_candidate_alias_config(force_reload: bool = False) -> Dict[str, Any]:
    """Lazy-load rollbackable V018-M1 candidate alias config."""
    global _CACHED_ALIAS_CFG
    with _CONFIG_LOCK:
        if _CACHED_ALIAS_CFG is not None and not force_reload:
            return _CACHED_ALIAS_CFG
        if CANDIDATE_ALIAS_PATH.exists():
            data = json.loads(CANDIDATE_ALIAS_PATH.read_text(encoding="utf-8"))
        else:
            data = {}
        _CACHED_ALIAS_CFG = data
        return data


def get_sparse_recall_info() -> Dict[str, Any]:
    """Return version & config metadata for auditability."""
    cfg = load_candidate_alias_config()
    return {
        "version": SPARSE_RECALL_VERSION,
        "alias_config_version": cfg.get("version", "missing"),
        "default_enabled": DEFAULT_V018_M1_ENABLED,
        "suppressed_over_specific_count": len(cfg.get("suppress_over_specific_aliases", {})),
        "cross_domain_blocklist_count": len(cfg.get("cross_domain_blocklist", [])),
    }


def _strip_tags_and_noise(raw_name: str) -> str:
    """Remove leading quantities, index prefixes, and project device tags/box numbers."""
    s = (raw_name or "").strip()
    if not s:
        return ""
    s = _LEADING_QTY_RE.sub("", s)
    s = _LEADING_INDEX_TAG_RE.sub("", s)
    # Remove inline device tags / electrical box numbers (AHU-J-01, PAU-D301, AP-1, AL-2, ALE-1)
    def _drop_tag(m: re.Match) -> str:
        tok = m.group(1)
        if is_device_tag(tok):
            return " "
        return tok
    s = _DEVICE_TAG_INLINE_RE.sub(_drop_tag, s)
    s = _PAREN_NOISE_RE.sub(" ", s)
    s = re.sub(r'\s+', ' ', s).strip(" -_/，,。")
    return s


def canonicalize_sparse_query_name(
    query_info: Dict[str, Any],
    parsed_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build clean canonical name from original_name without over-specific alias pollution.

    Preserves existing cross-domain blocklist and enforces discipline-scoped abbreviations.
    """
    cfg = load_candidate_alias_config()
    raw_orig = str(query_info.get("original_name") or query_info.get("name") or "").strip()
    q_name = str(query_info.get("name") or "").strip()
    prof_raw = str(query_info.get("professional") or "")
    prof_norm = normalize_professional_category(prof_raw) if prof_raw else ""

    suppressed = cfg.get("suppress_over_specific_aliases", {})
    cross_block = set(cfg.get("cross_domain_blocklist", []))

    was_suppressed = (raw_orig in suppressed) or (raw_orig in cross_block)
    base_name = _strip_tags_and_noise(raw_orig) if raw_orig else _strip_tags_and_noise(q_name)

    # Apply discipline-scoped abbreviation expansion ONLY on standalone tokens within matching discipline
    scoped_abbrs = cfg.get("discipline_scoped_abbreviations", {}).get(prof_norm, {})
    abbr_applied: List[str] = []
    expanded_name = base_name
    if scoped_abbrs and expanded_name:
        for abbr, target in scoped_abbrs.items():
            pat = re.compile(r'(?<![A-Za-z0-9])' + re.escape(abbr) + r'(?![A-Za-z0-9])', re.IGNORECASE)
            if pat.search(expanded_name) and target not in expanded_name:
                expanded_name = pat.sub(target, expanded_name)
                abbr_applied.append(f"{abbr}->{target}[{prof_norm}]")
        expanded_name = re.sub(r'\s+', ' ', expanded_name).strip()

    # Extract head noun (longest suffix match) and stripped modifier core
    head_nouns = sorted(cfg.get("head_noun_suffixes", []), key=len, reverse=True)
    head_noun = ""
    for hn in head_nouns:
        if hn in expanded_name:
            head_noun = hn
            break

    # Strip construction / mounting stop phrases to produce a concise core phrase
    core_phrase = expanded_name
    for sp in sorted(cfg.get("modifier_stop_phrases", []), key=len, reverse=True):
        if sp in core_phrase and len(core_phrase) > len(sp) + 1:
            core_phrase = core_phrase.replace(sp, "")
    core_phrase = re.sub(r'\s+', ' ', core_phrase).strip(" -_/，,。")

    return {
        "original_name": raw_orig,
        "base_name": base_name,
        "canonical_name": expanded_name or base_name,
        "core_phrase": core_phrase,
        "head_noun": head_noun,
        "discipline": prof_norm,
        "was_over_specific_suppressed": was_suppressed,
        "abbr_applied": abbr_applied,
    }


def extract_query_spec_anchors(
    query_info: Dict[str, Any],
    parsed_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Extract strictly query-owned physical spec anchors for sparse FTS recall.

    Never invents or substitutes material grades, diameters, or specs not present in the query.
    """
    q_parsed = parsed_spec if isinstance(parsed_spec, dict) else (query_info.get("parsed_spec") or {})
    q_attrs = q_parsed.get("attrs", {}) if isinstance(q_parsed, dict) else {}
    orig_name = str(query_info.get("original_name") or query_info.get("name") or "")
    orig_spec = str(query_info.get("original_spec") or query_info.get("specification") or "")
    # Use cleaned material block (without 工作内容 noise) if available
    clean_spec = str(q_parsed.get("clean_text") or orig_spec)
    combined = f"{orig_name} {clean_spec}".strip()

    anchors: Dict[str, Any] = {
        "cable_specs": [],     # list of (model_token, section_token)
        "material": "",
        "dn": "",
        "od": "",
        "dim_2d": "",
        "fiber_cores": "",
        "fiber_mode": "",
        "fiber_env": "",
        "gas_medium": "",
        "ports": "",
        "watt": "",
    }

    # 1. Explicit cable model + cores*section from query's OWN text
    for m in _CABLE_FULL_RE.finditer(combined):
        mod = m.group(1).upper().replace(" ", "")
        sec_pair = m.group(2)
        sec_single = m.group(3)
        sec = ""
        if sec_pair:
            sec = re.sub(r'\s+', '', sec_pair).replace('×', '*').replace('x', '*').replace('X', '*')
        elif sec_single:
            sec = sec_single
        if mod:
            entry = (mod, sec)
            if entry not in anchors["cable_specs"]:
                anchors["cable_specs"].append(entry)

    # Also check parsed cable attrs if regex didn't catch separated lines
    if not anchors["cable_specs"] and q_attrs.get("芯数") and q_attrs.get("截面积"):
        mod_f = str(q_attrs.get("型号") or q_attrs.get("型号族") or "").strip().upper()
        c_s = str(q_attrs["芯数"]).strip()
        s_v = q_attrs["截面积"]
        s_s = str(int(s_v)) if isinstance(s_v, float) and s_v.is_integer() else str(s_v).strip()
        if mod_f and c_s and s_s:
            sec_str = f"{c_s}*{s_s}" if "+" not in c_s else ""
            anchors["cable_specs"].append((mod_f, sec_str))

    # 2. Material (strictly from query attrs)
    raw_mat = str(q_attrs.get("材质") or "").strip()
    if "316L" in raw_mat.upper():
        anchors["material"] = "316L"
    elif "304" in raw_mat:
        anchors["material"] = "304"
    elif "UPVC" in raw_mat.upper():
        anchors["material"] = "UPVC"
    elif "PPR" in raw_mat.upper():
        anchors["material"] = "PPR"
    elif "紫铜" in raw_mat:
        anchors["material"] = "紫铜"
    elif "铜" in raw_mat:
        anchors["material"] = "铜"
    elif "镀锌" in raw_mat:
        anchors["material"] = "镀锌"

    # 3. DN vs OD (strictly separate!)
    dn_val = q_attrs.get("dn_val")
    if isinstance(dn_val, (int, float)) and not isinstance(dn_val, bool) and dn_val > 0:
        anchors["dn"] = f"DN{int(round(dn_val))}"
    elif q_attrs.get("管径"):
        m_dn = re.search(r'DN\s*(\d+)', str(q_attrs["管径"]), re.IGNORECASE)
        if m_dn:
            anchors["dn"] = f"DN{m_dn.group(1)}"

    od_val = q_attrs.get("外径") or q_attrs.get("de_val")
    if isinstance(od_val, (int, float)) and not isinstance(od_val, bool) and od_val > 0:
        od_i = int(round(od_val)) if abs(od_val - round(od_val)) < 1e-6 else od_val
        anchors["od"] = f"De{od_i}"

    # 4. 2D Dimensions
    dim_raw = str(q_attrs.get("尺寸_2d") or q_attrs.get("尺寸") or "").strip()
    m_d = re.search(r'(\d+)\s*[*x×]\s*(\d+)', dim_raw)
    if m_d:
        anchors["dim_2d"] = f"{m_d.group(1)}*{m_d.group(2)}"

    # 5. Optical fiber specs (only if query mentions 光纤/光缆)
    if "光" in combined:
        mc = _CORE_COUNT_RE.search(combined)
        if mc:
            anchors["fiber_cores"] = f"{mc.group(1)}芯"
        if "单模" in combined:
            anchors["fiber_mode"] = "单模"
        elif "多模" in combined:
            anchors["fiber_mode"] = "多模"
        if "室外" in combined:
            anchors["fiber_env"] = "室外"
        elif "室内" in combined:
            anchors["fiber_env"] = "室内"

    # 6. Gas medium (for 气体终端 / 汇流排)
    comb_upper = combined.upper()
    if any(k in combined for k in ("终端", "汇流排", "气体", "医气")):
        for canon_gas, kws in _GAS_MEDIA:
            if any(kw.upper() in comb_upper for kw in kws):
                anchors["gas_medium"] = canon_gas
                break

    # 7. Port count (for 交换机 / 配线架 / 网络插座)
    mp = _PORT_COUNT_RE.search(combined)
    if mp and any(k in combined for k in ("交换机", "配线架", "终端盒", "光端盒")):
        anchors["ports"] = f"{mp.group(1)}口"

    # 8. Wattage (for 灯具)
    if "灯" in combined:
        mw = re.search(r'(?<!\d)(\d{1,3})\s*[Ww](?![A-Za-z])', combined)
        if mw:
            anchors["watt"] = f"{mw.group(1)}W"

    return anchors


def _build_fts_and_query(terms: List[str]) -> str:
    """Build a safe FTS5 AND query from a list of phrases/tokens using fts_tokenize."""
    parts: List[str] = []
    seen: Set[str] = set()
    for term in terms:
        if not term:
            continue
        cleaned = re.sub(r'["\'()（）\[\]{}:：;；,，/\\]+', ' ', str(term)).strip()
        if not cleaned:
            continue
        tokenized = fts_tokenize(cleaned)
        for tok in tokenized.split():
            t = tok.strip()
            if not t or t in seen:
                continue
            seen.add(t)
            # Quote every token so hyphens/stars/dots don't trigger FTS5 syntax errors
            parts.append(f'"{t}"')
    return " AND ".join(parts)


def _build_fts_or_query(terms: List[str], max_tokens: int = 10) -> str:
    """Build a bounded FTS5 OR query for parameter/token fallback (replaces q_name[:4])."""
    parts: List[str] = []
    seen: Set[str] = set()
    for term in terms:
        if not term:
            continue
        cleaned = re.sub(r'["\'()（）\[\]{}:：;；,，/\\]+', ' ', str(term)).strip()
        tokenized = fts_tokenize(cleaned)
        for tok in tokenized.split():
            t = tok.strip()
            if not t or t in seen or len(parts) >= max_tokens:
                continue
            seen.add(t)
            parts.append(f'"{t}"')
    return " OR ".join(parts)


def _fts_phrase_query(phrase: str) -> str:
    """Wrap a single phrase as an AND-group of its fts_tokenize tokens."""
    cleaned = re.sub(r'["\'()（）\[\]{}:：;；,，/\\]+', ' ', str(phrase or "")).strip()
    if not cleaned:
        return ""
    toks = [t for t in fts_tokenize(cleaned).split() if t]
    if not toks:
        return ""
    return "(" + " AND ".join(f'"{t}"' for t in toks) + ")"


def recall_sparse_candidates(
    query_info: Dict[str, Any],
    parsed_spec: Optional[Dict[str, Any]] = None,
    *,
    limit: int = DEFAULT_SPARSE_LIMIT,
    existing_codes: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run bounded 4-tier sparse FTS recall with full channel rank & reason provenance.

    Channels:
      1. `sparse_name_param`: clean canonical name + query's own physical spec anchors
      2. `sparse_alias`: discipline-scoped safe alias / synonym expansions
      3. `sparse_phrase_split`: long-AND phrase splitting & head-noun relaxation
      4. `sparse_param_fallback`: BM25 OR-relaxed fallback when sparse (replaces q_name[:4])
    """
    from app.db_manager import search_materials_fts_raw

    cfg = load_candidate_alias_config()
    name_info = canonicalize_sparse_query_name(query_info, parsed_spec)
    anchors = extract_query_spec_anchors(query_info, parsed_spec)

    canon_name = name_info["canonical_name"]
    core_phrase = name_info["core_phrase"]
    head_noun = name_info["head_noun"]
    disc = name_info["discipline"]

    selected: Dict[str, Dict[str, Any]] = {}
    channel_counts: Dict[str, int] = {
        "sparse_name_param": 0,
        "sparse_alias": 0,
        "sparse_phrase_split": 0,
        "sparse_param_fallback": 0,
    }
    executed_queries: List[Dict[str, Any]] = []

    def _run_fts(fts_q: str, channel: str, reason_tag: str, cap: int):
        if not fts_q or len(selected) >= limit or cap <= 0:
            return 0
        rows = search_materials_fts_raw(fts_q, limit=min(cap, limit))
        executed_queries.append({"channel": channel, "reason": reason_tag, "hits": len(rows)})
        added = 0
        for rank_idx, row in enumerate(rows, 1):
            code = row.get("code")
            if not code:
                continue
            reason_str = f"{channel}:{reason_tag}(rank={rank_idx})"
            if code in selected:
                rec = selected[code]
                if channel not in rec["recall_sources"]:
                    rec["recall_sources"].append(channel)
                if channel not in rec["channel_ranks"] or rank_idx < rec["channel_ranks"][channel]:
                    rec["channel_ranks"][channel] = rank_idx
                if reason_str not in rec["recall_reasons"]:
                    rec["recall_reasons"].append(reason_str)
                continue
            if len(selected) >= limit or added >= cap:
                break
            cand = dict(row)
            cand["semantic_score"] = 0.0
            cand["recall_source"] = channel
            cand["recall_sources"] = [channel]
            cand["channel_ranks"] = {channel: rank_idx}
            cand["recall_reasons"] = [reason_str]
            selected[code] = cand
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
            added += 1
        return added

    # ── Tier 1: Clean Canonical Name + Query's Own Spec Anchors ───────────────
    # 1a. Cable exact model + section (highest precision for 配线/控制线/电力电缆)
    for mod, sec in anchors["cable_specs"][:2]:
        if mod and sec:
            # Exact model + section (e.g. WDZB-RYY 2*1, WDZC-RYYP 4*0.75, WDZB-YJY 3*2.5)
            _run_fts(_build_fts_and_query([mod, sec]), "sparse_name_param", f"cable={mod}+{sec}", cap=20)
            # Also handle section with trailing .0 / without .0 (e.g. 2*1.0 vs 2*1)
            if ".0" in sec:
                sec_alt = sec.replace(".0", "")
                _run_fts(_build_fts_and_query([mod, sec_alt]), "sparse_name_param", f"cable={mod}+{sec_alt}", cap=15)
            # Model family suffix (after '-') + section (e.g. RYYP + 4*0.75, YJY + 5*2.5)
            if "-" in mod:
                fam = mod.split("-")[-1]
                if len(fam) >= 2:
                    _run_fts(_build_fts_and_query([fam, sec]), "sparse_name_param", f"cable_fam={fam}+{sec}", cap=20)
        elif mod:
            _run_fts(_build_fts_and_query([mod]), "sparse_name_param", f"cable_mod={mod}", cap=15)

    # 1b. Optical fiber core + mode + env (without polluting GYTA53/GJPFJV)
    if anchors["fiber_cores"] and (anchors["fiber_mode"] or "光" in canon_name):
        f_terms = [t for t in (anchors["fiber_cores"], anchors["fiber_mode"], anchors["fiber_env"], "光缆") if t]
        _run_fts(_build_fts_and_query(f_terms), "sparse_name_param", f"fiber={'+'.join(f_terms)}", cap=20)
        if len(selected) < 5:
            f_short = [t for t in (anchors["fiber_cores"], anchors["fiber_mode"], "光缆") if t]
            _run_fts(_build_fts_and_query(f_short), "sparse_name_param", f"fiber_short={'+'.join(f_short)}", cap=15)

    # 1c. Gas terminal / manifold + query's own gas medium (never O2 override!)
    if anchors["gas_medium"] and any(k in canon_name for k in ("终端", "汇流排")):
        g_head = "气体终端" if "终端" in canon_name else "汇流排"
        _run_fts(_build_fts_and_query([anchors["gas_medium"], g_head]), "sparse_name_param", f"gas={anchors['gas_medium']}+{g_head}", cap=15)

    # 1d. Canonical name / head noun + physical spec anchor (material / DN / OD / dim / ports / watt)
    anchor_head = head_noun or core_phrase or canon_name
    if anchor_head:
        if anchors["material"] and anchors["dn"]:
            _run_fts(
                _build_fts_and_query([anchor_head, anchors["material"], anchors["dn"]]),
                "sparse_name_param", f"{anchor_head}+{anchors['material']}+{anchors['dn']}", cap=20,
            )
        if anchors["material"] and anchors["od"]:
            _run_fts(
                _build_fts_and_query([anchor_head, anchors["material"], anchors["od"]]),
                "sparse_name_param", f"{anchor_head}+{anchors['material']}+{anchors['od']}", cap=20,
            )
        if anchors["dim_2d"]:
            _run_fts(
                _build_fts_and_query([anchor_head, anchors["dim_2d"]]),
                "sparse_name_param", f"{anchor_head}+{anchors['dim_2d']}", cap=20,
            )
        if anchors["ports"]:
            _run_fts(
                _build_fts_and_query([anchor_head, anchors["ports"]]),
                "sparse_name_param", f"{anchor_head}+{anchors['ports']}", cap=15,
            )
        if anchors["watt"]:
            _run_fts(
                _build_fts_and_query([anchor_head, anchors["watt"]]),
                "sparse_name_param", f"{anchor_head}+{anchors['watt']}", cap=15,
            )

    # 1e. Unpolluted canonical name itself (especially when over-specific alias was suppressed)
    if canon_name and (name_info["was_over_specific_suppressed"] or name_info["abbr_applied"] or canon_name != query_info.get("name")):
        _run_fts(_build_fts_and_query([canon_name]), "sparse_name_param", f"canon={canon_name}", cap=25)

    # ── Tier 2: Discipline-Scoped Safe Alias & Synonym Expansion ──────────────
    exp_root = cfg.get("domain_scoped_expansions", {})
    disc_map = exp_root.get(disc, {}) if disc else {}
    univ_map = exp_root.get("*", {})

    matched_aliases: List[Tuple[str, str, str]] = []  # (trigger, expansion, scope)
    seen_exp: Set[str] = set()
    lookup_keys = [k for k in (name_info["original_name"], canon_name, core_phrase, head_noun) if k]
    for scope_name, mapping in ((disc or "none", disc_map), ("*", univ_map)):
        if not mapping:
            continue
        for lk in lookup_keys:
            if lk in mapping:
                for exp in mapping[lk]:
                    if exp not in seen_exp:
                        seen_exp.add(exp)
                        matched_aliases.append((lk, exp, scope_name))
        # Also match longest substring key in canon_name for discipline-specific table
        if scope_name != "*":
            for k in sorted(mapping.keys(), key=len, reverse=True):
                if k in canon_name and k not in lookup_keys:
                    for exp in mapping[k]:
                        if exp not in seen_exp:
                            seen_exp.add(exp)
                            matched_aliases.append((k, exp, scope_name))
                    break

    spec_tail = [t for t in (anchors["material"], anchors["dn"] or anchors["od"], anchors["dim_2d"], anchors["gas_medium"], anchors["ports"]) if t]
    for trig, exp, scope_name in matched_aliases[:6]:
        if len(selected) >= limit:
            break
        # Try alias + query's own spec anchors first (high precision, never mis-expands spec!)
        if spec_tail:
            _run_fts(
                _build_fts_and_query([exp] + spec_tail[:2]),
                "sparse_alias", f"{trig}->{exp}[{scope_name}]+{'+'.join(spec_tail[:2])}", cap=15,
            )
        if len(selected) < limit:
            _run_fts(
                _build_fts_and_query([exp]),
                "sparse_alias", f"{trig}->{exp}[{scope_name}]", cap=15,
            )

    # ── Tier 3: Long-AND Phrase Split & Head-Noun Relaxation ──────────────────
    need_split = (len(selected) < 20) and (
        len(canon_name) >= 5 or name_info["was_over_specific_suppressed"] or core_phrase != canon_name
    )
    if need_split:
        # 3a. Stripped core_phrase (stop modifiers removed)
        if core_phrase and len(core_phrase) >= 2 and core_phrase != canon_name:
            _run_fts(_build_fts_and_query([core_phrase]), "sparse_phrase_split", f"core={core_phrase}", cap=20)
        # 3b. Head noun + single best spec token
        if head_noun:
            for st in spec_tail[:2]:
                _run_fts(_build_fts_and_query([head_noun, st]), "sparse_phrase_split", f"head={head_noun}+{st}", cap=15)
            # 3c. Head noun + non-stop modifier chunk from core_phrase
            rem = core_phrase.replace(head_noun, " ").strip()
            mod_chunks = [m for m in _ZH_SEQ_RE.findall(rem) if len(m) >= 2]
            for mc in mod_chunks[:2]:
                _run_fts(_build_fts_and_query([mc, head_noun]), "sparse_phrase_split", f"chunk={mc}+{head_noun}", cap=15)
            # 3d. Head noun alone (strictly bounded)
            if len(selected) < 15:
                _run_fts(_build_fts_and_query([head_noun]), "sparse_phrase_split", f"head_only={head_noun}", cap=20)

    # ── Tier 4: Parameter / Token BM25 Fallback (Replaces Naive q_name[:4]) ───
    total_available = len(selected) + (len(existing_codes) if existing_codes else 0)
    if total_available < 8 and len(selected) < limit:
        fb_terms: List[str] = []
        if head_noun:
            fb_terms.append(head_noun)
        fb_terms.extend(spec_tail[:3])
        if not fb_terms and core_phrase:
            fb_terms.append(core_phrase)
        elif not fb_terms and canon_name:
            fb_terms.append(canon_name)
        or_q = _build_fts_or_query(fb_terms, max_tokens=8)
        if or_q:
            _run_fts(or_q, "sparse_param_fallback", f"bm25_or={'|'.join(fb_terms[:4])}", cap=20)

    meta = {
        "version": SPARSE_RECALL_VERSION,
        "name_info": name_info,
        "spec_anchors": anchors,
        "channel_counts": channel_counts,
        "total_sparse_candidates": len(selected),
        "executed_query_count": len(executed_queries),
    }
    return list(selected.values()), meta
