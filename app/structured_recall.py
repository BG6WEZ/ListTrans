# -*- coding: utf-8 -*-
"""V018-M0: Standard code direct recall, model/tag separation, and multi-field structured recall.

Design constraints:
- Zero import-time DB/file side effects.
- Experimental recall channels are gated behind `LISTTRANS_V018_M0_RECALL` (default "0" / False)
  or explicit per-query `recall_options` so the legacy baseline remains 100% reproducible until
  coordinator approval.
- Revision-aware in-memory index keyed by `db_manager._catalog_key()` = (db_path, db_revision),
  automatically invalidated on any catalog import, reparse, append, swap, or rollback.
- Returned candidate dicts are fresh shallow copies (no shared mutable state).
- Never bypasses matcher hard constraints or binding gates.
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain_router import route_domain_with_confidence
from app.preprocessor import extract_device_tag, extract_model, is_device_tag

STRUCTURED_INDEX_VERSION = "v018-m0-struct-idx-v1"
DEFAULT_V018_M0_ENABLED = False
DEFAULT_STRUCTURED_LIMIT = 50
LOW_CONFIDENCE_DOMAIN_THRESHOLD = 0.80

# Standard catalog code patterns observed in frozen catalog (26,845 dotted + 14 AA+10digits)
_STANDARD_DOTTED_CODE_RE = re.compile(r'(?<![\d\.])(\d{1,2}\.\d{2}\.\d{2}\.\d{4})(?![\d\.])')
_STANDARD_ALPHA_CODE_RE = re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{2}\d{10})(?![A-Za-z0-9])')
_EXPLICIT_STD_CODE_LABEL_RE = re.compile(
    r'(?:标准编码|物料编码|材料编码|国标编码|定额标准码)\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9\.\-_]{3,31})',
    re.IGNORECASE,
)

# Project / BOQ code patterns (GB50500 12-digit codes, golden IDs, project prefixes)
_PROJECT_BOQ_12_RE = re.compile(r'(?<!\d)(\d{12})(?!\d)')
_PROJECT_PREFIX_CODE_RE = re.compile(
    r'(?<![A-Za-z0-9])((?:GT|XM|PRJ|BOQ|QD|GC)-\d+[A-Za-z0-9\-]*)(?![A-Za-z0-9])',
    re.IGNORECASE,
)

# Dimension parser for 2D / 3D sizes
_DIM_RE = re.compile(r'(\d+(?:\.\d+)?)\s*[*x×X]\s*(\d+(?:\.\d+)?)(?:\s*[*x×X]\s*(\d+(?:\.\d+)?))?')
_DN_RE = re.compile(r'(?:DN|dn)\s*[:：]?\s*(\d+)')
_OD_RE = re.compile(r'(?:De|de|[φΦФ⌀]|外径)\s*[:：]?\s*(\d+(?:\.\d+)?)')
_ZH_CHAR_RE = re.compile(r'[\u4e00-\u9fff]')

# Compatible domain families for high-confidence structured lookup
_DOMAIN_FAMILIES: Dict[str, Set[str]] = {
    "valve_water": {"valve_water", "valve_medical_gas", "medical_gas"},
    "valve_medical_gas": {"valve_medical_gas", "valve_water", "medical_gas"},
    "medical_gas": {"medical_gas", "valve_medical_gas", "valve_water", "pipe_ss", "pipe_copper", "pipe_plastic"},
    "pipe_ss": {"pipe_ss", "pipe_steel", "medical_gas"},
    "pipe_steel": {"pipe_steel", "pipe_ss"},
    "pipe_plastic": {"pipe_plastic", "medical_gas"},
    "pipe_copper": {"pipe_copper", "medical_gas"},
    "cable_power": {"cable_power", "cable_control", "wire"},
    "cable_control": {"cable_control", "cable_power", "wire"},
    "wire": {"wire", "cable_power", "cable_control"},
    "ahu": {"ahu", "fan", "filter_ffu"},
    "fan": {"fan", "ahu"},
    "damper": {"damper", "plenum", "air_terminal", "silencer"},
    "plenum": {"plenum", "damper", "silencer"},
    "silencer": {"silencer", "plenum", "damper"},
    "air_terminal": {"air_terminal", "damper"},
    "switchgear": {"switchgear", "ups"},
    "weak_current": {"weak_current", "switchgear"},
    "lighting": {"lighting"},
    "purification": {"purification", "filter_ffu", "door"},
    "door": {"door", "purification"},
    "decoration": {"decoration"},
}


def is_v018_m0_recall_enabled(query_info: Optional[Dict[str, Any]] = None) -> bool:
    """Return True if V018-M0 recall channels are enabled for this call."""
    if isinstance(query_info, dict):
        opts = query_info.get("recall_options")
        if isinstance(opts, dict) and "v018_m0_enabled" in opts:
            return bool(opts["v018_m0_enabled"])
    raw = os.getenv("LISTTRANS_V018_M0_RECALL", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def get_recall_options(query_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve effective recall feature flags for a query."""
    enabled = is_v018_m0_recall_enabled(query_info)
    opts: Dict[str, Any] = {
        "v018_m0_enabled": enabled,
        "exact_code": enabled,
        "structured_model": enabled,
        "structured_fields": enabled,
        "structured_limit": DEFAULT_STRUCTURED_LIMIT,
        "index_version": STRUCTURED_INDEX_VERSION,
        "default_enabled": DEFAULT_V018_M0_ENABLED,
    }
    if isinstance(query_info, dict) and isinstance(query_info.get("recall_options"), dict):
        for k in ("exact_code", "structured_model", "structured_fields", "structured_limit"):
            if k in query_info["recall_options"]:
                opts[k] = query_info["recall_options"][k]
    return opts


def is_standard_code_format(value: str) -> bool:
    """Check whether a token matches a standard catalog code format."""
    s = (value or "").strip()
    if not s:
        return False
    return bool(_STANDARD_DOTTED_CODE_RE.fullmatch(s) or _STANDARD_ALPHA_CODE_RE.fullmatch(s))


def is_project_code_format(value: str) -> bool:
    """Check whether a token matches a project / BOQ code format."""
    s = (value or "").strip()
    if not s:
        return False
    return bool(_PROJECT_BOQ_12_RE.fullmatch(s) or _PROJECT_PREFIX_CODE_RE.fullmatch(s))


def normalize_model_key(value: str) -> str:
    """Normalize a product model for exact/prefix index lookup."""
    if not value:
        return ""
    return re.sub(r'[\s\-_/]+', '', str(value).strip().upper())


def _normalize_material_canonical(raw_val: Any) -> str:
    """Canonical material grade aligned with matcher.normalize_material_grade."""
    if not raw_val:
        return ""
    s = str(raw_val).strip().upper()
    if not s:
        return ""
    if "316L" in s or "S31603" in s or "SS316L" in s or "SUS316L" in s:
        return "不锈钢316L"
    if "316" in s or "SUS316" in s or "SS316" in s:
        return "不锈钢316"
    if "304" in s or "S30408" in s or "SS304" in s or "SUS304" in s or "06CR19NI10" in s:
        return "不锈钢304"
    if "不锈钢" in s:
        return "不锈钢"
    if "UPVC" in s or "PVC-U" in s:
        return "UPVC"
    if "CPVC" in s:
        return "CPVC"
    if "PVC" in s:
        return "PVC"
    if "PPR" in s or "PP-R" in s:
        return "PPR"
    if "PPH" in s:
        return "PPH"
    if "HDPE" in s:
        return "HDPE"
    if "PE" == s or "聚乙烯" in s:
        return "PE"
    if "镀锌" in s:
        return "镀锌"
    if "紫铜" in s or "脱脂铜" in s or "脱氧铜" in s:
        return "紫铜"
    if "黄铜" in s:
        return "黄铜"
    if "铜" in s:
        return "铜"
    if "铸铁" in s or "球墨" in s:
        return "铸铁"
    if "碳钢" in s or "无缝钢" in s or "Q235" in s:
        return "碳钢"
    if "铝" in s:
        return "铝"
    if "玻璃钢" in s or "FRP" in s:
        return "玻璃钢"
    return s


def _normalize_dn(attrs: Dict[str, Any], text: str = "") -> Optional[int]:
    for key in ("dn_val", "管径", "口径"):
        val = attrs.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
            return int(round(val))
        m = re.search(r'(\d+)', str(val))
        if m:
            iv = int(m.group(1))
            if iv > 0:
                return iv
    if text:
        m = _DN_RE.search(text)
        if m:
            iv = int(m.group(1))
            if iv > 0:
                return iv
    return None


def _normalize_od(attrs: Dict[str, Any], text: str = "") -> Optional[int]:
    for key in ("外径", "de_val"):
        val = attrs.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
            return int(round(val))
        m = re.search(r'(\d+(?:\.\d+)?)', str(val))
        if m:
            fv = float(m.group(1))
            if fv > 0:
                return int(round(fv))
    if text:
        m = _OD_RE.search(text)
        if m:
            fv = float(m.group(1))
            if fv > 0:
                return int(round(fv))
    return None


def _normalize_dims(attrs: Dict[str, Any], text: str = "") -> List[str]:
    """Return canonical dimension keys (both ordered and sorted 2D) for exact lookup."""
    keys: List[str] = []
    seen: Set[str] = set()

    def _add_nums(nums: List[float]):
        clean = [str(int(n)) if abs(n - round(n)) < 1e-6 else f"{n:g}" for n in nums if n > 0]
        if len(clean) >= 2:
            raw_k = "x".join(clean)
            if raw_k not in seen:
                seen.add(raw_k)
                keys.append(raw_k)
            if len(clean) == 2:
                sorted_k = "x".join(sorted(clean, key=float))
                if sorted_k not in seen:
                    seen.add(sorted_k)
                    keys.append(sorted_k)
            elif len(clean) == 3:
                k2 = "x".join(clean[:2])
                if k2 not in seen:
                    seen.add(k2)
                    keys.append(k2)
                s2 = "x".join(sorted(clean[:2], key=float))
                if s2 not in seen:
                    seen.add(s2)
                    keys.append(s2)

    if attrs.get("长") and attrs.get("宽"):
        try:
            nums = [float(attrs["长"]), float(attrs["宽"])]
            if attrs.get("高"):
                nums.append(float(attrs["高"]))
            _add_nums(nums)
        except Exception:
            pass
    for key in ("尺寸", "尺寸_2d"):
        val = attrs.get(key)
        if val:
            m = _DIM_RE.search(str(val))
            if m:
                nums = [float(g) for g in m.groups() if g is not None]
                _add_nums(nums)
    if not keys and text:
        m = _DIM_RE.search(text)
        if m:
            nums = [float(g) for g in m.groups() if g is not None]
            _add_nums(nums)
    return keys


def _normalize_cable_sig(attrs: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Return canonical (cores, section) tuple for cable/wire structured lookup."""
    cores = attrs.get("芯数")
    section = attrs.get("截面积")
    if cores is None or section is None:
        return None
    c_str = str(cores).strip()
    if isinstance(section, (int, float)) and not isinstance(section, bool):
        s_str = str(int(section)) if float(section).is_integer() else f"{float(section):g}"
    else:
        s_str = str(section).strip()
    if not c_str or not s_str:
        return None
    return (c_str, s_str)


def classify_query_identifiers(
    query_info: Dict[str, Any],
    parsed_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Classify query identifiers into standard_codes, project_codes, device_tags, product_models.

    Explicitly tracks namespace overlaps so project codes / device tags are never conflated
    with standard catalog codes or product models.
    """
    q_parsed = parsed_spec if isinstance(parsed_spec, dict) else (query_info.get("parsed_spec") or {})
    q_attrs = q_parsed.get("attrs", {}) if isinstance(q_parsed, dict) else {}

    standard_codes: List[Tuple[str, str]] = []  # (code, source_field)
    std_seen: Set[str] = set()
    project_codes: List[Tuple[str, str]] = []
    prj_seen: Set[str] = set()
    device_tags: List[str] = []
    box_types: List[str] = []
    namespace_overlaps: List[Dict[str, str]] = []

    def add_std(code: str, src: str):
        c = (code or "").strip()
        if c and c not in std_seen:
            std_seen.add(c)
            standard_codes.append((c, src))

    def add_prj(code: str, src: str):
        c = (code or "").strip()
        if c and c not in prj_seen:
            prj_seen.add(c)
            project_codes.append((c, src))

    # 1. Explicit standard code fields
    for fld in ("standard_code", "catalog_code", "material_code"):
        val = str(query_info.get(fld) or "").strip()
        if val:
            if is_project_code_format(val) and not is_standard_code_format(val):
                add_prj(val, fld)
                namespace_overlaps.append({"token": val, "field": fld, "reason": "project_code_in_standard_field"})
            elif is_device_tag(val):
                device_tags.append(val)
                namespace_overlaps.append({"token": val, "field": fld, "reason": "device_tag_in_standard_field"})
            else:
                add_std(val, fld)

    # 2. Ambiguous `code` / `original_code` / `project_code` fields
    for fld in ("code", "original_code", "project_code"):
        val = str(query_info.get(fld) or "").strip()
        if not val:
            continue
        if is_standard_code_format(val):
            if fld == "project_code":
                add_prj(val, fld)
                namespace_overlaps.append({"token": val, "field": fld, "reason": "standard_format_in_project_code"})
            else:
                add_std(val, fld)
                if fld == "original_code":
                    namespace_overlaps.append({"token": val, "field": fld, "reason": "standard_format_in_original_code"})
        elif is_project_code_format(val) or fld in ("project_code", "original_code"):
            add_prj(val, fld)
        elif is_device_tag(val):
            device_tags.append(val)

    # 3. Text scan across name & specification
    combined = " ".join(
        str(query_info.get(k) or "")
        for k in ("original_name", "name", "original_spec", "specification")
    ).strip()

    for m in _EXPLICIT_STD_CODE_LABEL_RE.finditer(combined):
        tok = m.group(1).strip()
        if is_device_tag(tok):
            device_tags.append(tok)
            namespace_overlaps.append({"token": tok, "field": "text_label", "reason": "device_tag_after_code_label"})
        elif is_project_code_format(tok) and not is_standard_code_format(tok):
            add_prj(tok, "text_label")
            namespace_overlaps.append({"token": tok, "field": "text_label", "reason": "project_code_after_code_label"})
        else:
            add_std(tok, "text_label")

    for m in _STANDARD_DOTTED_CODE_RE.finditer(combined):
        add_std(m.group(1), "text_dotted")

    for m in _STANDARD_ALPHA_CODE_RE.finditer(combined):
        add_std(m.group(1), "text_alpha_std")

    for m in _PROJECT_BOQ_12_RE.finditer(combined):
        add_prj(m.group(1), "text_boq12")
    for m in _PROJECT_PREFIX_CODE_RE.finditer(combined):
        add_prj(m.group(1), "text_prj_prefix")

    # 4. Device tags & box types from typed attrs / extractor
    t_attr = str(q_attrs.get("tag_id") or "").strip()
    if t_attr and t_attr not in device_tags:
        device_tags.append(t_attr)
    extracted_tag = extract_device_tag(combined)
    if extracted_tag and extracted_tag not in device_tags:
        device_tags.append(extracted_tag)

    b_attr = str(q_attrs.get("box_type") or "").strip()
    if b_attr and b_attr not in box_types:
        box_types.append(b_attr)

    # 5. Product models (strictly excluding standard codes, project codes, tags, material grades)
    product_models: List[str] = []
    for cand_m in (q_parsed.get("model"), q_attrs.get("型号"), extract_model(combined)):
        ms = str(cand_m or "").strip()
        if not ms or len(ms) < 2:
            continue
        if is_standard_code_format(ms) or is_project_code_format(ms) or is_device_tag(ms):
            continue
        if ms not in product_models:
            product_models.append(ms)

    return {
        "standard_codes": standard_codes,
        "project_codes": project_codes,
        "device_tags": device_tags,
        "box_types": box_types,
        "product_models": product_models,
        "namespace_overlaps": namespace_overlaps,
    }


@dataclass(frozen=True)
class _IndexedMaterial:
    code: str
    name: str
    specification: str
    category: str
    unit: str
    brand: str
    description: str
    domain: str
    material: str
    dn: Optional[int]
    od: Optional[int]
    dims: Tuple[str, ...]
    model_norm: str
    cable_sig: Optional[Tuple[str, str]]
    name_zh_chars: frozenset

    def to_candidate_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "specification": self.specification,
            "category": self.category,
            "unit": self.unit,
            "brand": self.brand,
            "description": self.description,
            "semantic_score": 0.0,
        }


class StructuredCatalogSnapshot:
    """Immutable revision-bound inverted index over the active materials catalog."""

    def __init__(self, catalog_key: Tuple[str, int], rows: List[Dict[str, Any]]):
        self.catalog_key = catalog_key
        self.db_revision = catalog_key[1]
        self.version = STRUCTURED_INDEX_VERSION
        self.materials: Dict[str, _IndexedMaterial] = {}
        self.by_model_norm: Dict[str, List[str]] = defaultdict(list)
        self.by_domain: Dict[str, Set[str]] = defaultdict(set)
        self.by_material: Dict[str, Set[str]] = defaultdict(set)
        self.by_dn: Dict[int, Set[str]] = defaultdict(set)
        self.by_od: Dict[int, Set[str]] = defaultdict(set)
        self.by_dim: Dict[str, Set[str]] = defaultdict(set)
        self.by_cable_sig: Dict[Tuple[str, str], Set[str]] = defaultdict(set)

        for row in rows:
            code = str(row.get("code") or "").strip()
            if not code:
                continue
            name = str(row.get("name") or "")
            spec = str(row.get("specification") or "")
            cat = str(row.get("category") or "")
            pp_raw = row.get("parsed_params")
            parsed: Dict[str, Any] = {}
            if isinstance(pp_raw, str) and pp_raw:
                try:
                    parsed = json.loads(pp_raw)
                except Exception:
                    parsed = {}
            elif isinstance(pp_raw, dict):
                parsed = pp_raw
            attrs = parsed.get("attrs", {}) if isinstance(parsed, dict) else {}

            domain = str(parsed.get("domain") or "").strip()
            if not domain:
                domain, _, _ = route_domain_with_confidence(cat, name, spec, cat)

            mat_norm = _normalize_material_canonical(attrs.get("材质"))
            if not mat_norm:
                mat_norm = _normalize_material_canonical(f"{name} {spec}" if any(
                    kw in f"{name} {spec}".upper()
                    for kw in ("316L", "304", "不锈钢", "UPVC", "PPR", "PPH", "镀锌", "紫铜", "黄铜")
                ) else "")

            dn = _normalize_dn(attrs, f"{name} {spec}")
            od = _normalize_od(attrs, f"{name} {spec}")
            dims = tuple(_normalize_dims(attrs, f"{name} {spec}"))
            cable_sig = _normalize_cable_sig(attrs)

            raw_model = str(parsed.get("model") or attrs.get("型号") or "").strip()
            if raw_model and (is_standard_code_format(raw_model) or is_project_code_format(raw_model) or is_device_tag(raw_model)):
                raw_model = ""
            model_norm = normalize_model_key(raw_model) if len(raw_model) >= 2 else ""

            zh_chars = frozenset(_ZH_CHAR_RE.findall(name))

            item = _IndexedMaterial(
                code=code,
                name=name,
                specification=spec,
                category=cat,
                unit=str(row.get("unit") or ""),
                brand=str(row.get("brand") or ""),
                description=str(row.get("description") or ""),
                domain=domain,
                material=mat_norm,
                dn=dn,
                od=od,
                dims=dims,
                model_norm=model_norm,
                cable_sig=cable_sig,
                name_zh_chars=zh_chars,
            )
            self.materials[code] = item
            if model_norm:
                self.by_model_norm[model_norm].append(code)
            if domain:
                self.by_domain[domain].add(code)
            if mat_norm:
                self.by_material[mat_norm].add(code)
                if mat_norm in ("不锈钢304", "不锈钢316", "不锈钢316L"):
                    self.by_material["不锈钢"].add(code)
                if mat_norm in ("紫铜", "黄铜"):
                    self.by_material["铜"].add(code)
            if dn is not None:
                self.by_dn[dn].add(code)
            if od is not None:
                self.by_od[od].add(code)
            for dk in dims:
                self.by_dim[dk].add(code)
            if cable_sig is not None:
                self.by_cable_sig[cable_sig].add(code)


_INDEX_LOCK = threading.RLock()
_CACHED_SNAPSHOT: Optional[StructuredCatalogSnapshot] = None


def invalidate_structured_index() -> None:
    """Explicitly drop cached structured index snapshot."""
    global _CACHED_SNAPSHOT
    with _INDEX_LOCK:
        _CACHED_SNAPSHOT = None


def get_structured_index() -> StructuredCatalogSnapshot:
    """Return the revision-consistent StructuredCatalogSnapshot, rebuilding atomically on revision change."""
    global _CACHED_SNAPSHOT
    from app.db_manager import _catalog_key, get_thread_db_connection

    key = _catalog_key()
    with _INDEX_LOCK:
        if _CACHED_SNAPSHOT is not None and _CACHED_SNAPSHOT.catalog_key == key:
            return _CACHED_SNAPSHOT
        conn = get_thread_db_connection()
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT code, name, specification, category, unit, brand, description, parsed_params FROM materials"
            ).fetchall()
        ]
        snap = StructuredCatalogSnapshot(key, rows)
        _CACHED_SNAPSHOT = snap
        return snap


def get_structured_index_info() -> Dict[str, Any]:
    """Return index metadata for diagnostics and auditability."""
    with _INDEX_LOCK:
        snap = _CACHED_SNAPSHOT
        return {
            "version": STRUCTURED_INDEX_VERSION,
            "default_enabled": DEFAULT_V018_M0_ENABLED,
            "loaded": snap is not None,
            "db_revision": snap.db_revision if snap is not None else None,
            "material_count": len(snap.materials) if snap is not None else 0,
        }


def recall_by_standard_code(
    query_info: Dict[str, Any],
    parsed_spec: Optional[Dict[str, Any]] = None,
    identifiers: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Direct recall for validated standard catalog codes.

    Hits still go through downstream scoring, hard constraints, and binding gates.
    """
    from app.db_manager import query_by_code

    ident = identifiers if identifiers is not None else classify_query_identifiers(query_info, parsed_spec)
    hits: List[Dict[str, Any]] = []
    unknown_codes: List[str] = []
    matched_codes: List[str] = []

    for code, src in ident["standard_codes"]:
        row = query_by_code(code)
        if row is not None:
            cand = dict(row)
            cand["semantic_score"] = 0.0
            cand["recall_source"] = "exact_code"
            cand["recall_sources"] = ["exact_code"]
            cand["recall_reasons"] = [f"exact_code:{code}({src})"]
            hits.append(cand)
            matched_codes.append(code)
        else:
            unknown_codes.append(code)

    meta = {
        "matched_codes": matched_codes,
        "unknown_codes": unknown_codes,
        "project_codes": [c for c, _ in ident["project_codes"]],
        "device_tags": list(ident["device_tags"]),
        "namespace_overlaps": list(ident["namespace_overlaps"]),
    }
    return hits, meta


def recall_by_structured_fields(
    query_info: Dict[str, Any],
    parsed_spec: Optional[Dict[str, Any]] = None,
    identifiers: Optional[Dict[str, Any]] = None,
    *,
    limit: int = DEFAULT_STRUCTURED_LIMIT,
    include_model: bool = True,
    include_fields: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Multi-field structured recall over revision-bound catalog index.

    Combines product model, trusted/low-confidence domain, material grade, DN, OD, dimensions,
    and cable signature. Low-confidence or unknown domains allow cross-domain recall.
    """
    q_parsed = parsed_spec if isinstance(parsed_spec, dict) else (query_info.get("parsed_spec") or {})
    q_attrs = q_parsed.get("attrs", {}) if isinstance(q_parsed, dict) else {}
    q_name = str(query_info.get("name") or "")
    q_spec = str(query_info.get("specification") or "")
    q_prof = str(query_info.get("professional") or "")
    q_cat = str(query_info.get("material_category") or query_info.get("category") or "")

    domain = str(q_parsed.get("domain") or "").strip()
    domain_conf = q_parsed.get("domain_confidence")
    domain_tier = str(q_parsed.get("domain_tier") or "")
    if not domain or domain_conf is None:
        domain, domain_conf, domain_tier = route_domain_with_confidence(
            q_prof or q_cat, q_name, q_spec, q_prof
        )

    low_conf_or_unknown = (domain == "generic") or (float(domain_conf) < LOW_CONFIDENCE_DOMAIN_THRESHOLD)
    ident = identifiers if identifiers is not None else classify_query_identifiers(query_info, q_parsed)

    snap = get_structured_index()
    q_zh = frozenset(_ZH_CHAR_RE.findall(q_name))

    q_mat = _normalize_material_canonical(q_attrs.get("材质"))
    q_dn = _normalize_dn(q_attrs, f"{q_name} {q_spec}")
    q_od = _normalize_od(q_attrs, f"{q_name} {q_spec}")
    q_dims = _normalize_dims(q_attrs, f"{q_name} {q_spec}")
    q_cable = _normalize_cable_sig(q_attrs)

    dom_family: Set[str] = set()
    if not low_conf_or_unknown and domain:
        allowed_domains = _DOMAIN_FAMILIES.get(domain, {domain})
        for d_key in allowed_domains:
            dom_family.update(snap.by_domain.get(d_key, set()))

    mat_set = snap.by_material.get(q_mat, set()) if q_mat else set()
    dn_set = snap.by_dn.get(q_dn, set()) if q_dn is not None else set()
    od_set = snap.by_od.get(q_od, set()) if q_od is not None else set()
    dim_set: Set[str] = set()
    for dk in q_dims:
        dim_set.update(snap.by_dim.get(dk, set()))
    cable_set = snap.by_cable_sig.get(q_cable, set()) if q_cable is not None else set()

    selected: Dict[str, Tuple[int, str, str]] = {}  # code -> (tier_rank, channel, reason)

    def _material_compatible(item_mat: str) -> bool:
        if not q_mat or not item_mat:
            return True
        if item_mat == q_mat:
            return True
        if q_mat == "不锈钢" and item_mat in ("不锈钢304", "不锈钢316", "不锈钢316L"):
            return True
        if q_mat == "铜" and item_mat in ("紫铜", "黄铜"):
            return True
        return False

    def _add_pool(codes: Set[str] | List[str], tier_rank: int, channel: str, reason: str, cap: int, *, respect_material: bool = False):
        if not codes or len(selected) >= limit:
            return
        # Sort deterministically by (exact domain match, name char overlap, code)
        scored = []
        for code in codes:
            if code in selected:
                continue
            item = snap.materials.get(code)
            if item is None:
                continue
            if respect_material and not _material_compatible(item.material):
                continue
            dom_bonus = 1 if (domain and item.domain == domain) else 0
            overlap = len(q_zh & item.name_zh_chars) if q_zh else 0
            scored.append((dom_bonus, overlap, code))
        scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
        added = 0
        for _, _, code in scored:
            if len(selected) >= limit or added >= cap:
                break
            selected[code] = (tier_rank, channel, reason)
            added += 1

    # 1. Structured exact product model recall (never fires on tag_id/box_type/project_code/material grade)
    if include_model:
        for mod in ident["product_models"]:
            mk = normalize_model_key(mod)
            if mk and mk in snap.by_model_norm:
                _add_pool(
                    snap.by_model_norm[mk],
                    tier_rank=0,
                    channel="structured_model",
                    reason=f"product_model:{mod}",
                    cap=min(25, limit),
                )

    # 2. Multi-field structured recall
    if include_fields:
        if not low_conf_or_unknown and dom_family:
            # High-confidence domain: precision-first within compatible domain family
            if mat_set and dn_set:
                _add_pool(
                    dom_family & mat_set & dn_set,
                    1, "structured_recall",
                    f"S1_dom_mat_dn:domain={domain}({domain_conf:.2f}),mat={q_mat},dn={q_dn}",
                    cap=30,
                )
            if mat_set and od_set:
                _add_pool(
                    dom_family & mat_set & od_set,
                    1, "structured_recall",
                    f"S1_dom_mat_od:domain={domain}({domain_conf:.2f}),mat={q_mat},od={q_od}",
                    cap=30,
                )
            if cable_set:
                _add_pool(
                    dom_family & cable_set,
                    2, "structured_recall",
                    f"S2_dom_cable:domain={domain}({domain_conf:.2f}),cable={q_cable[0]}*{q_cable[1]}",
                    cap=35,
                )
            if dim_set:
                _add_pool(
                    dom_family & dim_set,
                    3, "structured_recall",
                    f"S3_dom_dim:domain={domain}({domain_conf:.2f}),dim={q_dims[0]}",
                    cap=35,
                )
            if dn_set and len(selected) < 20:
                _add_pool(
                    dom_family & dn_set,
                    4, "structured_recall",
                    f"S4_dom_dn:domain={domain}({domain_conf:.2f}),dn={q_dn}",
                    cap=25,
                    respect_material=True,
                )
            if od_set and len(selected) < 20:
                _add_pool(
                    dom_family & od_set,
                    4, "structured_recall",
                    f"S4_dom_od:domain={domain}({domain_conf:.2f}),od={q_od}",
                    cap=25,
                    respect_material=True,
                )
            # Cross-domain fallback for sparse high-conf structured queries (only strong multi-field combos)
            if len(selected) < 15:
                if mat_set and dn_set:
                    _add_pool(
                        mat_set & dn_set,
                        5, "structured_recall",
                        f"S5_xdom_mat_dn:mat={q_mat},dn={q_dn},cross_domain=true",
                        cap=20,
                    )
                if mat_set and od_set:
                    _add_pool(
                        mat_set & od_set,
                        5, "structured_recall",
                        f"S5_xdom_mat_od:mat={q_mat},od={q_od},cross_domain=true",
                        cap=20,
                    )
            if mat_set and len(selected) < 15 and q_zh:
                # Require at least 1 shared Chinese name char when using domain+material alone
                dom_mat_filtered = [
                    c for c in (dom_family & mat_set)
                    if snap.materials[c].name_zh_chars & q_zh
                ]
                _add_pool(
                    dom_mat_filtered,
                    6, "structured_recall",
                    f"S6_dom_mat:domain={domain}({domain_conf:.2f}),mat={q_mat}",
                    cap=20,
                )
        else:
            # Low-confidence or unknown domain: explicitly allow cross-domain candidates!
            if mat_set and dn_set:
                _add_pool(
                    mat_set & dn_set,
                    1, "structured_recall",
                    f"S1_lowconf_mat_dn:domain={domain}({domain_conf:.2f}),mat={q_mat},dn={q_dn},cross_domain=true",
                    cap=30,
                )
            if mat_set and od_set:
                _add_pool(
                    mat_set & od_set,
                    1, "structured_recall",
                    f"S1_lowconf_mat_od:domain={domain}({domain_conf:.2f}),mat={q_mat},od={q_od},cross_domain=true",
                    cap=30,
                )
            if cable_set:
                _add_pool(
                    cable_set,
                    2, "structured_recall",
                    f"S2_lowconf_cable:domain={domain}({domain_conf:.2f}),cable={q_cable[0]}*{q_cable[1]},cross_domain=true",
                    cap=30,
                )
            if dim_set:
                _add_pool(
                    dim_set,
                    3, "structured_recall",
                    f"S3_lowconf_dim:domain={domain}({domain_conf:.2f}),dim={q_dims[0]},cross_domain=true",
                    cap=30,
                )
            if dn_set and len(selected) < 15:
                _add_pool(
                    dn_set,
                    4, "structured_recall",
                    f"S4_lowconf_dn:domain={domain}({domain_conf:.2f}),dn={q_dn},cross_domain=true",
                    cap=20,
                    respect_material=True,
                )
            if od_set and len(selected) < 15:
                _add_pool(
                    od_set,
                    4, "structured_recall",
                    f"S4_lowconf_od:domain={domain}({domain_conf:.2f}),od={q_od},cross_domain=true",
                    cap=20,
                    respect_material=True,
                )

    results: List[Dict[str, Any]] = []
    for code, (_, channel, reason) in selected.items():
        cand = snap.materials[code].to_candidate_dict()
        cand["recall_source"] = channel
        cand["recall_sources"] = [channel]
        cand["recall_reasons"] = [reason]
        results.append(cand)

    meta = {
        "domain": domain,
        "domain_confidence": float(domain_conf),
        "domain_tier": domain_tier,
        "cross_domain_allowed": low_conf_or_unknown,
        "query_features": {
            "material": q_mat,
            "dn": q_dn,
            "od": q_od,
            "dims": q_dims[:2],
            "cable_sig": q_cable,
            "product_models": ident["product_models"],
        },
        "structured_hits": len(results),
        "index_revision": snap.db_revision,
        "index_version": snap.version,
    }
    return results, meta
