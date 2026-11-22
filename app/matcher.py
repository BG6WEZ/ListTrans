# -*- coding: utf-8 -*-
import json
import difflib
import math
import re
import logging
import os
from typing import Dict, Any, List, Tuple, Optional
from functools import lru_cache

from app.db_manager import search_materials_keyword, query_by_code, get_db_connection, catalog_read, get_db_revision
from app.preprocessor import clean_text, extract_params, extract_device_name, fts_tokenize, load_config
from app.query_preprocessor import extract_query_params
# V015-M3：三态否定单一事实源在 domain_extractors（无环低层），此处导入即重导出，
# binding_decisions 等模块仍可经 app.matcher 复用同一套语义
from app.domain_extractors import (
    NEGATION_PREFIXES,
    detect_three_state_flag,
    detect_pump_state,
    check_pump_conflict,
)
# V015-M3 R03 / V016-M1：专业别名归一化与动态规则作用域匹配收敛到 app.rule_context 单一事实源（无环依赖）
from app.rule_context import (
    PROFESSIONAL_ALIASES,
    RuleContext,
    normalize_professional_category,
    dynamic_scope_matches as _dyn_scope_matches,
    dynamic_rule_matches_query as _dyn_rule_matches_query,
)

logger = logging.getLogger(__name__)

# Default weights config
DEFAULT_WEIGHTS = {
    "scene_a": {
        "model": 0.50,
        "attrs": 0.30,
        "text": 0.20,
        "vector_boost": 0.20,
        "base_attr": 0.10
    },
    "scene_b": {
        "attrs": 0.50,
        "text": 0.30,
        "model": 0.10,
        "vector_boost": 0.20,
        "base_attr": 0.10
    },
    "scene_c": {
        "text": 0.60,
        "vector_boost": 0.30,
        "model": 0.05,
        "attrs": 0.05
    }
}

# Default tolerances
DEFAULT_TOLERANCE = {
    "风量": 0.15,
    "新风量": 0.15,
    "余压": 0.15,
    "功率": 0.10,
    "制冷量": 0.10,
    "制热量": 0.10,
    "管径": 0.00,
    "压力": 0.10,
    "电压": 0.00,
    "电流": 0.10,
    "极数": 0.00,
    "芯数": 0.00,
    "截面积": 0.00
}

# Default boosts
DEFAULT_BOOSTS = {
    "positive": {
        "model_exact": 0.15,
        "attrs_exact": 0.10,
        "category_match": 0.08,
        "series_match": 0.05,
        "brand_match": 0.05
    },
    "negative": {
        "category_conflict": -0.30,
        "medium_conflict": -0.20,
        "pipe_conflict": -0.40,
        "cable_conflict": -0.40,
        "model_conflict": -0.25,
        "attrs_deviation_2x": -0.15
    }
}

# Model, typed attributes and name/category have different diagnostic value
# across disciplines. Multipliers preserve the configured scenario's total weight.
_DISCIPLINE_WEIGHT_MULTIPLIERS = {
    "强电": (0.80, 1.25, 1.00),
    "弱电": (0.90, 1.20, 1.00),
    "暖通": (0.80, 1.25, 1.00),
    "装饰": (0.70, 1.10, 1.25),
    "医气": (0.85, 1.20, 1.00),
    "给排水": (0.85, 1.20, 1.00),
    "纯水": (0.85, 1.20, 1.00),
}


def _scoring_profile() -> str:
    profile = os.getenv("LISTTRANS_V019_M0_SCORING", "legacy")
    return profile if profile in ("dedup", "calibrated") else "legacy"


# Load configurations
weight_rules = load_config("weight_rules.json", DEFAULT_WEIGHTS)
tolerance_rules = load_config("tolerance_rules.json", DEFAULT_TOLERANCE)
boost_rules = load_config("boost_rules.json", DEFAULT_BOOSTS)

def reload_matcher_configs():
    """Reload configuration files dynamically."""
    global weight_rules, tolerance_rules, boost_rules
    weight_rules = load_config("weight_rules.json", DEFAULT_WEIGHTS)
    tolerance_rules = load_config("tolerance_rules.json", DEFAULT_TOLERANCE)
    boost_rules = load_config("boost_rules.json", DEFAULT_BOOSTS)
    calculate_text_similarity.cache_clear()

@lru_cache(maxsize=100000)
def calculate_text_similarity(s1: str, s2: str) -> float:
    """Calculate normalized similarity score using SequenceMatcher."""
    if not s1 or not s2:
        return 0.0
    s1, s2 = s1.lower().strip(), s2.lower().strip()
    return difflib.SequenceMatcher(None, s1, s2).ratio()

def score_point_vs_interval(point_val: float, min_val: float, max_val: float) -> float:
    """
    Point value vs Interval match score (0.0 ~ 1.0).
    - Inside interval: 1.0 (exact match)
    - Outside: Smooth linear decay normalized by interval width: max(0.0, 1.0 - dist / width)
    """
    if min_val <= point_val <= max_val:
        return 1.0
    width = max(max_val - min_val, 1.0)
    dist = (min_val - point_val) if point_val < min_val else (point_val - max_val)
    return max(0.0, 1.0 - dist / width)

def check_dimension_conflict(q_val, c_val, max_deviation=0.15):
    """
    Check if two dimension strings (like A*B or A*B*C) conflict.
    Extracts sorted numeric components and checks relative deviations.
    """
    if not q_val or not c_val:
        return False
    # If candidate is a tolerance or interval text, do not treat as conflict
    if "±" in str(c_val) or "半周长" in str(c_val) or "~" in str(c_val):
        return False
    try:
        q_parts = sorted([float(x) for x in re.findall(r'\d+(?:\.\d+)?', str(q_val))], reverse=True)
        c_parts = sorted([float(x) for x in re.findall(r'\d+(?:\.\d+)?', str(c_val))], reverse=True)
        if len(q_parts) == len(c_parts) and len(q_parts) > 0:
            for q_p, c_p in zip(q_parts, c_parts):
                if abs(q_p - c_p) / max(q_p, c_p, 1e-5) > max_deviation:
                    return True
            return False
        elif len(q_parts) > 0 and len(c_parts) > 0:
            min_len = min(len(q_parts), len(c_parts))
            for i in range(min_len):
                if abs(q_parts[i] - c_parts[i]) / max(q_parts[i], c_parts[i], 1e-5) > max_deviation:
                    return True
            return False
    except Exception:
        pass
    return str(q_val).strip().lower() != str(c_val).strip().lower()

def has_conflict(attr: str, q_v, c_v) -> bool:
    """Generic conflict check for attributes."""
    if q_v is None or c_v is None:
        return False
    if attr == "尺寸":
        return check_dimension_conflict(q_v, c_v, max_deviation=0.10)
    
    # Try numeric conversion
    q_num, c_num = None, None
    try:
        q_num = float(q_v)
    except (ValueError, TypeError):
        pass
    try:
        c_num = float(c_v)
    except (ValueError, TypeError):
        pass
        
    if q_num is not None and c_num is not None:
        tol = tolerance_rules.get(attr, 0.0)
        tol = max(tol, 1e-5)
        diff_ratio = abs(q_num - c_num) / max(q_num, c_num, 1e-5)
        return diff_ratio > tol
        
    return str(q_v).strip().lower() != str(c_v).strip().lower()

def check_category_conflict(cat1: str, cat2: str) -> bool:
    """Detect if two category strings are physically incompatible."""
    c1, c2 = cat1.lower(), cat2.lower()
    
    # If both are fans or fan-related, do not conflict
    if "风机" in c1 and "风机" in c2:
        return False
        
    incompatible_pairs = [
        ("风机", "泵"), ("风机", "阀"), ("风机", "箱"), ("风管", "水管"), 
        ("阀", "管道"), ("消声", "风机"), ("冷水", "热水"), ("空调", "风阀"),
        ("电线", "电缆"), ("控制器", "阀")
    ]
    for p1, p2 in incompatible_pairs:
        if (p1 in c1 and p2 in c2) or (p1 in c2 and p2 in c1):
            # Special bypass for "风机" vs "箱" where one is a fan cabinet/box (风机箱/机型)
            if (p1 == "风机" and p2 == "箱") or (p1 == "箱" and p2 == "风机"):
                if "风机" in c1 and ("风机" in c2 or "机" in c2):
                    continue
                if "风机" in c2 and ("风机" in c1 or "机" in c1):
                    continue
            # Special bypass for "电线" vs "电缆" when category contains combined "电线电缆" or "电线、电缆"
            if (p1 == "电线" and p2 == "电缆") or (p1 == "电缆" and p2 == "电线"):
                if "电线电缆" in c1 or "电线电缆" in c2 or "电线、电缆" in c1 or "电线、电缆" in c2:
                    continue
            return True
    return False

def check_series_match(m1: str, m2: str) -> bool:
    """Check if two model strings share the same series prefix."""
    if not m1 or not m2:
        return False
    # Series is typically letters before numbers, or first hyphenated segment
    p1 = m1.split("-")[0].strip().lower()
    p2 = m2.split("-")[0].strip().lower()
    return p1 == p2 and len(p1) > 1

UNIT_CLASSES = {
    "length": ["米", "千米", "厘米", "毫米", "m", "km", "cm", "mm", "根", "支", "条", "卷", "盘"],
    "area": ["平方米", "平方厘米", "m2", "m²", "cm2", "平方"],
    "weight": ["千克", "吨", "克", "公斤", "kg", "t", "g"],
    "volume": ["立方米", "升", "毫升", "m3", "m³", "l", "ml"],
    "quantity": ["个", "套", "台", "根", "块", "卷", "盘", "箱", "只", "支", "把", "双", "盒", "具", "樘", "顶", "盏", "面", "座", "台", "套", "件"]
}

# V015-M3：同名异写单位（倍率 1.0 的可靠同义换算），用于工程量复核
UNIT_SYNONYMS = {
    "米": "m", "m": "m",
    "千米": "km", "km": "km",
    "厘米": "cm", "cm": "cm",
    "毫米": "mm", "mm": "mm",
    "平方米": "m2", "m2": "m2", "m²": "m2", "平方厘米": "cm2", "cm2": "cm2",
    "立方米": "m3", "m3": "m3", "m³": "m3",
    "升": "l", "l": "l", "毫升": "ml", "ml": "ml",
    "千克": "kg", "kg": "kg", "公斤": "kg",
    "吨": "t", "t": "t", "克": "g", "g": "g",
}

# V015-M3：非 1.0 倍率的可靠换算表；未登记的跨名单位视为不可靠换算
UNIT_MULTIPLIERS = {
    ("km", "m"): 1000.0, ("m", "km"): 0.001,
    ("厘米", "m"): 0.01, ("cm", "m"): 0.01, ("m", "厘米"): 100.0, ("m", "cm"): 100.0,
    ("毫米", "m"): 0.001, ("mm", "m"): 0.001, ("m", "毫米"): 1000.0, ("m", "mm"): 1000.0,
    ("t", "kg"): 1000.0, ("吨", "kg"): 1000.0, ("kg", "t"): 0.001, ("kg", "吨"): 0.001,
    ("kg", "g"): 1000.0, ("g", "kg"): 0.001,
}


def _unit_classes(unit: str) -> set:
    """按物理维度归类单位字符串；未命中任何已知单位返回空集合（unknown 三态）。

    先精确命中（同一 token 可跨类，如 卷/盘 既长度又数量），
    再按最长 token 包含匹配兜底，避免 '米' 命中 '平方米'、'm' 命中 'm2'。
    """
    if not unit:
        return set()
    text = unit.strip().lower()
    if not text:
        return set()
    exact = {
        cat for cat, units in UNIT_CLASSES.items()
        if text in [u.lower() for u in units]
    }
    if exact:
        return exact
    best_token = None
    best_cat = None
    for cat, units in UNIT_CLASSES.items():
        for tok in units:
            if tok in text and (best_token is None or len(tok) > len(best_token)):
                best_token = tok
                best_cat = cat
    return {best_cat} if best_cat is not None else set()


def check_unit_compatibility(u1: str, u2: str) -> bool:
    """单位物理维度兼容判定（V015-M3 严格化）。

    - 任一未说明：unknown，不臆断硬冲突（三态：不判否也不认定满足）；
    - 已知单位跨物理维度（如 米/平方米、m/m2）：明确不兼容；
    - 同维度（含 卷/盘 这类跨类计数单位）：兼容。
    """
    if not u1 or not u2:
        return True
    u1_clean = u1.strip().lower()
    u2_clean = u2.strip().lower()
    if not u1_clean or not u2_clean:
        return True
    if u1_clean == u2_clean:
        return True

    u1_cats = _unit_classes(u1_clean)
    u2_cats = _unit_classes(u2_clean)
    if not u1_cats or not u2_cats:
        # 未知单位不臆断冲突；是否需显式复核由 get_unit_multiplier 的调用方决定
        return True
    return len(u1_cats.intersection(u2_cats)) > 0


def get_unit_multiplier(u1: str, u2: str) -> Optional[float]:
    """返回 u1→u2 的可靠换算倍率；同名/同义异写返回 1.0；无法可靠换算返回 None。"""
    if not u1 or not u2:
        return None
    a = u1.strip().lower()
    b = u2.strip().lower()
    if a == b:
        return 1.0
    syn_a = UNIT_SYNONYMS.get(a)
    syn_b = UNIT_SYNONYMS.get(b)
    if syn_a and syn_b and syn_a == syn_b:
        return 1.0
    return UNIT_MULTIPLIERS.get((a, b))

def standardize_brand(b: str) -> str:
    if not b:
        return ""
    b_clean = b.strip().lower()
    mappings = {
        "华为": "huawei", "huanwei": "huawei",
        "华三": "h3c", "h3c": "h3c",
        "海康威视": "hikvision", "海康": "hikvision", "hik": "hikvision",
        "大华": "dahua",
        "联塑": "liansu", "lesson": "liansu",
        "远东": "yuandong",
        "起帆": "qifan",
        "西门子": "siemens",
        "施耐德": "schneider",
        "abb": "abb"
    }
    for k, v in mappings.items():
        if k in b_clean:
            return v
    return b_clean

def get_professional_category(category_name: str, spec_text: str = "") -> str:
    cat = category_name or ""
    spec = spec_text or ""
    # 暖通
    if any(k in cat for k in ["暖通", "多联机", "空调", "通风"]) or any(k in spec for k in ["机组", "风机", "风管", "冷水机组", "风量"]):
        return "暖通"
    # 强电
    if any(k in cat for k in ["强电", "动力", "照明", "变配电", "配电", "电气"]) or any(k in spec for k in ["电缆", "导线", "断路器", "开关", "配电箱", "Pe=", "配电柜"]):
        return "强电"
    # 弱电
    if any(k in cat for k in ["弱电", "智能化", "自控", "监控", "消防", "网络", "通信"]) or any(k in spec for k in ["对讲", "呼叫", "总服务器", "液晶呼叫", "分机", "总线"]):
        return "弱电"
    # 给排水
    if any(k in cat for k in ["给排水", "给水", "排水", "水处理", "消防水"]) or any(k in spec for k in ["水盆", "洗手盆", "感应龙头", "水管", "阀门", "蝶阀", "消火栓", "喷淋"]):
        return "给排水"
    # 医气
    if any(k in cat for k in ["医气", "气体", "医用气体"]) or any(k in spec for k in ["氧气", "吸引", "医用气体", "终端", "脱脂无缝紫铜管"]):
        return "医气"
    # 纯水
    if any(k in cat for k in ["纯水"]):
        return "纯水"
    # 装饰
    if any(k in cat for k in ["装饰", "建材", "装修"]) or any(k in spec for k in ["砌块墙", "硫酸钡", "水泥", "地砖", "橡胶卷材", "金属板", "防霉", "涂料", "涂刷"]):
        return "装饰"
    return "其它"


# ---------------------------------------------------------------------------
# V015-M3：确认/导出/规则/恢复四链路共用的硬约束纯函数（三态语义，unknown 不臆断）
# ---------------------------------------------------------------------------
# V015-M3 R03：PROFESSIONAL_ALIASES / normalize_professional_category 单一事实源已收敛至 app.normalizer，
# 模块顶部导入并重导出，保持外部调用兼容。

# 材质牌号枚举：仅登记可确定的等价写法，未知写法原样返回（不臆断牌号）
MATERIAL_GRADE_ALIASES = {
    "304": "不锈钢304",
    "s30408": "不锈钢304",
    "06cr19ni10": "不锈钢304",
    "不锈钢304": "不锈钢304",
    "316l": "不锈钢316L",
    "s31603": "不锈钢316L",
    "不锈钢316l": "不锈钢316L",
}
KNOWN_MATERIAL_GRADES = set(MATERIAL_GRADE_ALIASES.values())

# V015-M3：NEGATION_PREFIXES / detect_three_state_flag 的单一事实源在
# app.domain_extractors，模块顶部已导入并重导出，此处不再重复定义，避免否定词表漂移。


def parse_material_attrs(material: Dict[str, Any]) -> Dict[str, Any]:
    """解析物料行 parsed_params 的 attrs；坏 JSON/缺值返回 {}（unknown）。"""
    parsed = material.get("parsed_params") if material else None
    if not parsed:
        return {}
    try:
        data = json.loads(parsed) if isinstance(parsed, str) else parsed
    except (ValueError, TypeError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("attrs"), dict):
        return data["attrs"]
    return {}


_MATERIAL_GRADE_RE = re.compile(
    r'(?<![a-zA-Z0-9])(316L|S31603|06Cr19Ni10|304|S30408)(?![a-zA-Z0-9])',
    re.IGNORECASE,
)
_EXPLICIT_DN_RE = re.compile(r'(?:DN|公称直径)\s*[:：]?\s*(\d+(?:\.\d+)?)', re.IGNORECASE)
_EXPLICIT_OD_RE = re.compile(r'(?:De|de|[φΦФ⌀]|外径)\s*[:：]?\s*(\d+(?:\.\d+)?)')
_EXPLICIT_DIM_RE = re.compile(r'\d+(?:\.\d+)?\s*[*x×]\s*\d+(?:\.\d+)?')


def _extract_fallback_material_attrs(material: Dict[str, Any]) -> Dict[str, Any]:
    """缺失/坏 JSON 候选解析缓存时，从候选原文回退抽取物理参数。

    严格遵守 DN 与外径分字段约定（De 不暗转为 dn_val，DN 不写外径），并兼容
    304/316L 不锈钢牌号与 matcher 同源的抽取语义。
    """
    if not material:
        return {}
    c_name = str(material.get("name", "") or "").strip()
    c_spec = str(material.get("specification", "") or "").strip()
    c_desc = str(material.get("description", "") or "").strip()
    c_cat = str(material.get("category", "") or "").strip()
    c_raw = " ".join(p for p in (c_name, c_spec, c_desc) if p).strip()
    if not c_raw:
        return {}

    parsed = extract_params(c_raw, c_cat)
    fallback = dict(parsed.get("attrs", {})) if isinstance(parsed, dict) else {}

    dn_match = _EXPLICIT_DN_RE.search(c_raw)
    od_match = _EXPLICIT_OD_RE.search(c_raw)
    has_od = bool(od_match or _EXPLICIT_DIM_RE.search(c_raw))

    if dn_match:
        try:
            dn_num = float(dn_match.group(1))
            fallback["dn_val"] = int(dn_num) if dn_num.is_integer() else dn_num
            fallback.setdefault("管径", f"DN{dn_match.group(1)}")
        except (TypeError, ValueError):
            pass
        if not has_od:
            fallback.pop("外径", None)
            fallback.pop("de_val", None)
    elif has_od or fallback.get("外径") is not None:
        # 仅见 De/φ/外径 而无显式 DN 时，禁止保留由 convert_de_to_dn 派生的 dn_val/管径
        for dn_key in ("dn_val", "管径", "口径", "DN"):
            fallback.pop(dn_key, None)
        if od_match and fallback.get("外径") is None:
            try:
                fallback["外径"] = float(od_match.group(1))
            except (TypeError, ValueError):
                pass

    cur_grade = normalize_material_grade(fallback.get("材质"))
    if cur_grade not in KNOWN_MATERIAL_GRADES:
        gm = _MATERIAL_GRADE_RE.search(c_raw)
        if gm and (
            any(k in c_raw for k in ("不锈钢", "管", "板", "箱", "阀"))
            or gm.group(1).upper() in ("316L", "S31603", "S30408", "06CR19NI10")
        ):
            raw_g = gm.group(1).upper()
            fallback["材质"] = "不锈钢316L" if raw_g in ("316L", "S31603") else "不锈钢304"

    return fallback


def resolve_material_attrs(material: Dict[str, Any]) -> Dict[str, Any]:
    """V015-M3 R02：候选物理参数同源读取（parsed_params 优先，缺值/坏 JSON 回退原文抽取）。

    保留 parse_material_attrs 在坏 JSON 返回 {} 与品牌未知契约不变，同时保证
    DN/外径/304/316L 等物理硬约束在缺失 parsed_params 时在推荐与复核端得到一致事实。
    """
    if not material:
        return {}
    cached = dict(parse_material_attrs(material))
    fallback = _extract_fallback_material_attrs(material)
    if not cached:
        return fallback

    merged = dict(fallback)
    merged.update(cached)

    # 若缓存材质仅含泛称 "不锈钢"，而规格原文可确定明确牌号，则补足明确牌号；
    # 若缓存本身已明确给出 304/316L（含 DB 漂移测试场景），以缓存为准。
    cached_mat = str(cached.get("材质", "") or "").strip()
    if cached_mat == "不锈钢" and normalize_material_grade(fallback.get("材质")) in KNOWN_MATERIAL_GRADES:
        merged["材质"] = fallback["材质"]

    # 若缓存仅显式给出外径且无 DN，原文亦无 DN，不带入派生的 dn_val/管径
    c_raw = " ".join(
        str(material.get(k, "") or "").strip() for k in ("name", "specification", "description")
    ).strip()
    if (
        ("外径" in cached or "de_val" in cached)
        and "dn_val" not in cached
        and "管径" not in cached
        and not _EXPLICIT_DN_RE.search(c_raw)
    ):
        for dn_key in ("dn_val", "管径", "口径", "DN"):
            merged.pop(dn_key, None)

    return merged


def resolve_material_brand(cand: Dict[str, Any]) -> str:
    """解析候选物料的真实品牌值。

    品牌列优先；为空时解析 parsed_params.attrs.品牌 的真实字符串；
    坏 JSON/缺值返回 ''（unknown 三态），绝不返回布尔 True。
    """
    if not cand:
        return ""
    brand = cand.get("brand") or cand.get("brand_tag")
    if isinstance(brand, str) and brand.strip():
        return brand.strip()
    attrs = parse_material_attrs(cand)
    val = attrs.get("品牌", "")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return ""


def normalize_material_grade(value: Any) -> str:
    """材质牌号枚举归一；未知/空值返回 '' 或原文，不把未知值臆断成枚举。"""
    if value is None:
        return ""
    text = str(value).strip().lower()
    if not text:
        return ""
    return MATERIAL_GRADE_ALIASES.get(text, str(value).strip())


def _to_finite_float(value: Any) -> Optional[float]:
    """仅返回有限浮点；None/异常/NaN/Inf 一律 None（异常值不得当满足）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return num


def _dn_field_to_float(value: Any) -> Optional[float]:
    """DN 字段取值：数值直通；字符串仅接受裸数字或 DN 前缀数字。

    真实查询抽取器的 管径 键常为 "DN15" 字符串（dn_val 才是规范数值），
    通用 _to_finite_float 无法解析该字符串。De/φ 属外径族，即便出现在 DN
    字段也不解析（DN/外径分字段，严禁跨字段比较）；无法解析一律 None（unknown）。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
    else:
        text = str(value).strip()
        m = re.fullmatch(r"DN\s*[:：]?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if m:
            num = float(m.group(1))
        else:
            try:
                num = float(text)
            except (TypeError, ValueError):
                return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return num


def check_dn_od_conflicts(q_attrs: Dict[str, Any], c_attrs: Dict[str, Any]) -> Optional[str]:
    """DN 与外径分字段比较；跨字段不比，严禁从 DN 直接写外径。

    返回阻断原因字符串；无冲突返回 None。任一侧缺值按 unknown 不阻断。
    """
    q_dn = _dn_field_to_float(q_attrs.get("dn_val"))
    if q_dn is None:
        q_dn = _dn_field_to_float(q_attrs.get("管径"))
    c_dn = _dn_field_to_float(c_attrs.get("dn_val"))
    if c_dn is None:
        c_dn = _dn_field_to_float(c_attrs.get("管径"))
    if q_dn is not None and c_dn is not None and abs(q_dn - c_dn) > 1e-6:
        return "管径口径冲突阻断"
    q_od = _to_finite_float(q_attrs.get("外径"))
    c_od = _to_finite_float(c_attrs.get("外径"))
    if q_od is not None and c_od is not None:
        if abs(q_od - c_od) / max(q_od, c_od, 1e-5) > 0.10:
            return "管材外径冲突阻断"
    return None


from dataclasses import dataclass, field
from typing import Set

@dataclass
class ScoringContext:
    """Encapsulates scoring state, inputs, intermediate weights, and reasons for candidate matching."""
    query_info: Dict[str, Any]
    candidate: Dict[str, Any]
    semantic_score: float = 0.0
    profile: str = "legacy"
    
    # Query extracted fields
    q_name: str = ""
    q_spec_text: str = ""
    q_category: str = ""
    q_prof: str = ""
    q_parsed: Dict[str, Any] = field(default_factory=dict)
    q_attrs: Dict[str, Any] = field(default_factory=dict)
    q_model: str = ""
    q_brand: Any = None
    q_unit: Any = None
    
    # Candidate extracted fields
    c_name: str = ""
    c_spec_text: str = ""
    c_category: str = ""
    c_desc: str = ""
    c_full_spec: str = ""
    c_prof: str = ""
    c_brand: Any = None
    c_unit: Any = None
    c_parsed: Dict[str, Any] = field(default_factory=dict)
    c_attrs: Dict[str, Any] = field(default_factory=dict)
    c_model: str = ""
    
    # Scoring components
    model_score: float = 0.0
    model_matched_exact: bool = False
    model_conflict: bool = False
    
    text_score: float = 0.0
    cat_sim: float = 0.0
    name_matched: bool = False
    
    # Schema & Attributes
    domain: str = "generic"
    domain_config: Dict[str, Any] = field(default_factory=dict)
    must_keys: List[str] = field(default_factory=list)
    should_keys: List[str] = field(default_factory=list)
    processed_keys: Set[str] = field(default_factory=set)
    flags_list: List[str] = field(default_factory=list)
    is_interval_dim: bool = False
    has_any_attr: bool = False
    all_attrs_in_tolerance: bool = True
    missing_penalty: float = 0.0
    attr_scores: Dict[str, float] = field(default_factory=dict)
    attr_weights: Dict[str, float] = field(default_factory=dict)
    attr_score: float = 0.0
    ranking_fields: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    score_components: Dict[str, float] = field(default_factory=dict)
    
    scene: str = "scene_c"
    base_score: float = 0.0
    
    # Normalized text cache
    q_norm_full: str = ""
    c_norm_full: str = ""
    q_norm_spec: str = ""
    c_norm_spec: str = ""
    
    # HVAC context tags
    q_is_ex: bool = False
    c_is_ex: bool = False
    c_is_non_ex: bool = False
    q_fan_press: Any = None
    q_fan_vol: Any = None
    is_q_fan: bool = False
    
    # Output reasons and hard gate blocked flag
    reasons: List[str] = field(default_factory=list)
    gate_evidence: List[Dict[str, Any]] = field(default_factory=list)
    blocked: bool = False
    # V015-M3：不可逆硬上限（0-1 基），所有后置动态/系列加分只能收口不能突破
    max_score: float = 1.0


def _check_initial_hard_conflicts(ctx: ScoringContext) -> bool:
    """Pre-parse query and candidate metadata, and evaluate hard blocking gates (Professional, Brand, Unit)."""
    q_info = ctx.query_info
    cand = ctx.candidate
    
    ctx.q_name = q_info.get("name", "")
    ctx.q_spec_text = q_info.get("specification", "")
    ctx.q_category = q_info.get("category", "")
    
    # V015-M1 / V016-M1：fallback 解析第三参严格保持专业语义，材料品类单独传入 material_category，避免混作 professional
    ctx.q_parsed = q_info.get("parsed_spec") or extract_query_params(
        ctx.q_name,
        ctx.q_spec_text,
        q_info.get("professional") or "",
        material_category=q_info.get("material_category") or q_info.get("original_category") or ctx.q_category or "",
    )
    ctx.q_attrs = ctx.q_parsed.get("attrs", {}) if isinstance(ctx.q_parsed, dict) else {}
    ctx.q_model = ctx.q_parsed.get("model", "") or ctx.q_attrs.get("型号", "") or ""
    
    ctx.c_name = cand.get("name", "").strip()
    ctx.c_spec_text = cand.get("specification", "")
    ctx.c_category = cand.get("category", "")
    ctx.c_desc = cand.get("description", "")
    ctx.c_full_spec = f"{ctx.c_spec_text} {ctx.c_desc}".strip()
    
    # Refine generic or short candidate names using specification text
    if len(ctx.c_name) <= 3 or any(kw in ctx.c_name for kw in ["柜", "线", "管", "阀", "箱", "板", "机", "按钮"]):
        ctx.c_name = extract_device_name(ctx.c_full_spec, ctx.c_name)
    
    # 1. Professional category conflict check
    q_prof = q_info.get("professional", "")
    if q_prof:
        q_prof = re.sub(r'^\d+[\.．\s]*', '', str(q_prof)).strip()
    ctx.q_prof = q_prof
    if q_prof and q_prof != "全部" and q_prof != "其它":
        c_prof = get_professional_category(ctx.c_category, ctx.c_full_spec)
        ctx.c_prof = c_prof
        # V015-M3：专业别名归一后比较（医用气体=医气），真跨专业不得泛化放行
        if c_prof != "其它" and normalize_professional_category(q_prof) != normalize_professional_category(c_prof):
            ctx.reasons.append("专业冲突阻断")
            ctx.blocked = True
            # V015-M3：blocked 与不可逆上限双归零，与动态排除路径语义一致
            ctx.max_score = 0.0
            if ctx.profile == "legacy":
                return True
            
    # 2. Brand conflict check
    q_attrs = q_info.get("attrs") or ctx.q_parsed.get("attrs", {})
    if isinstance(q_attrs, dict) and "attrs" in q_attrs and isinstance(q_attrs["attrs"], dict):
        q_attrs = {**q_attrs, **q_attrs["attrs"]}
    ctx.q_attrs = q_attrs
    
    q_brand = q_info.get("brand") or q_attrs.get("品牌")
    # V015-M3：品牌列空时解析 parsed_params.attrs.品牌 真实字符串；
    # 坏 JSON/缺值返回 ""（unknown），杜绝布尔 True 误判
    c_brand = resolve_material_brand(cand)
    ctx.q_brand = q_brand
    ctx.c_brand = c_brand
    if q_brand and c_brand:
        q_brand_std = standardize_brand(str(q_brand))
        c_brand_std = standardize_brand(str(c_brand))
        if q_brand_std and c_brand_std and q_brand_std != c_brand_std:
            ctx.reasons.append("品牌冲突阻断")
            ctx.blocked = True
            # V015-M3：blocked 与不可逆上限双归零，与动态排除路径语义一致
            ctx.max_score = 0.0
            if ctx.profile == "legacy":
                return True
            
    # 3. Unit incompatibility check
    q_unit = q_info.get("unit") or q_info.get("original_unit")
    c_unit = cand.get("unit")
    ctx.q_unit = q_unit
    ctx.c_unit = c_unit
    if q_unit and c_unit and not check_unit_compatibility(str(q_unit), str(c_unit)):
        ctx.reasons.append("单位不可换算阻断")
        ctx.blocked = True
        # V015-M3：blocked 与不可逆上限双归零，与动态排除路径语义一致
        ctx.max_score = 0.0
        return True

    return ctx.blocked


def _extract_candidate_specs(ctx: ScoringContext):
    """Load or extract structured parameters for the candidate."""
    cand = ctx.candidate
    c_parsed = None
    if "parsed_params" in cand and cand["parsed_params"]:
        try:
            import json
            if isinstance(cand["parsed_params"], str):
                c_parsed = json.loads(cand["parsed_params"])
            elif isinstance(cand["parsed_params"], dict):
                c_parsed = cand["parsed_params"]
        except Exception:
            pass
    if not isinstance(c_parsed, dict) or not c_parsed:
        c_parsed = extract_params(f"{ctx.c_name} {ctx.c_full_spec}", ctx.c_category)
        
    ctx.c_parsed = c_parsed
    cand_for_attrs = cand
    if ctx.c_full_spec and not cand.get("description") and not cand.get("specification"):
        cand_for_attrs = {**cand, "specification": ctx.c_full_spec}
    ctx.c_attrs = resolve_material_attrs(cand_for_attrs)
    ctx.c_model = c_parsed.get("model", "") or ctx.c_attrs.get("型号", "") or ""


def _score_model_similarity(ctx: ScoringContext):
    """Calculate model similarity and check model conflict."""
    q_model = ctx.q_model
    c_model = ctx.c_model
    
    # Check if q_model is a box prefix or alias (AP, AL, ALE, AT, ACP, ACP-P, AX, ATD, AFC)
    BOX_PREFIXES = ["AP", "AL", "ALE", "AT", "ACP", "ACP-P", "AX", "ATD", "AFC"]
    is_box_type = False
    if q_model.upper() in BOX_PREFIXES:
        is_box_type = True
    elif "-" in q_model:
        prefix = q_model.split("-")[0].upper()
        if prefix in BOX_PREFIXES:
            is_box_type = True

    if q_model and c_model and not is_box_type:
        if q_model.lower() == c_model.lower():
            ctx.model_score = 1.0
            ctx.model_matched_exact = True
            ctx.reasons.append("型号完全匹配")
        else:
            q_clean = re.sub(r'[^a-zA-Z0-9]', '', q_model).lower()
            c_clean = re.sub(r'[^a-zA-Z0-9]', '', c_model).lower()
            if q_clean == c_clean:
                ctx.model_score = 0.95
                ctx.model_matched_exact = True
                ctx.reasons.append("型号忽略符号匹配")
            elif q_clean in c_clean or c_clean in q_clean:
                ctx.model_score = 0.8
                ctx.reasons.append("型号包含匹配")
            else:
                sim = calculate_text_similarity(q_model, c_model)
                if sim >= 0.7:
                    ctx.model_score = sim
                    ctx.reasons.append("型号高度相似")
                else:
                    ctx.model_score = 0.0
                    ctx.model_conflict = True
    elif q_model and is_box_type:
        ctx.model_score = 0.0


def _score_text_and_category(ctx: ScoringContext):
    """Calculate name similarity with synonym awareness and category similarity."""
    q_name = ctx.q_name
    c_name = ctx.c_name
    q_category = ctx.q_category
    c_category = ctx.c_category
    
    weak_cable_syns = ["网线", "双绞线", "对线", "网络线"]
    is_syn = any(s in q_name for s in weak_cable_syns) and any(s in c_name for s in weak_cable_syns)
    if is_syn:
        name_sim = 0.95
    else:
        name_sim = calculate_text_similarity(q_name, c_name)
        
    cat_sim = calculate_text_similarity(q_category, c_category)
    ctx.cat_sim = cat_sim
    ctx.text_score = max(name_sim, cat_sim)
    if ctx.text_score > 0.8:
        ctx.reasons.append("品类名称一致")


def _score_attributes(ctx: ScoringContext):
    """Match domain schema must/should attributes, numerical tolerances, and 2d/3d intervals."""
    from app.domain_router import route_domain
    from app.domain_extractors import domain_schema
    
    c_category = ctx.c_category
    c_name = ctx.c_name
    c_full_spec = ctx.c_full_spec
    q_prof = ctx.q_prof
    q_attrs = ctx.q_attrs
    c_attrs = ctx.c_attrs
    
    domain = route_domain(c_category, c_name, c_full_spec, q_prof or "")
    ctx.domain = domain
    domain_config = domain_schema.get(domain, {})
    ctx.domain_config = domain_config
    must_keys = [f["key"] for f in domain_config.get("must", [])]
    should_keys = [f["key"] for f in domain_config.get("should", [])]
    ctx.must_keys = must_keys
    ctx.should_keys = should_keys

    attr_scores = {}
    attr_weights = {}
    all_attrs_in_tolerance = True
    has_any_attr = False
    missing_penalty = 0.0

    processed_keys = set()
    for attr in q_attrs.keys():
        if attr.endswith("_min") or attr.endswith("_max") or attr.endswith("_op") or attr.endswith("_base"):
            continue
        processed_keys.add(attr)
    ctx.processed_keys = processed_keys

    # A. Match standard flags group if defined in schema
    flags_list = domain_config.get("flags", [])
    ctx.flags_list = flags_list
    if flags_list:
        q_flags = [f for f in flags_list if q_attrs.get(f) is True]
        c_flags = [f for f in flags_list if c_attrs.get(f) is True]
        if q_flags:
            has_any_attr = True
            matched_flags_count = len(set(q_flags).intersection(set(c_flags)))
            flags_score = matched_flags_count / len(q_flags)
            attr_scores["flags"] = flags_score
            attr_weights["flags"] = domain_config.get("weights", {}).get("flags", 0.1)

    # Special Dimension & Interval Processing
    is_interval_dim = False
    if ("半周长" in q_attrs or "尺寸" in q_attrs or ("长" in q_attrs and "宽" in q_attrs)) and "半周长_min" in c_attrs and "半周长_max" in c_attrs:
        q_hp = q_attrs.get("半周长")
        if q_hp is None and "长" in q_attrs and "宽" in q_attrs:
            try:
                q_hp = float(q_attrs["长"]) + float(q_attrs["宽"])
            except Exception:
                pass
        if q_hp is not None:
            s_hp = score_point_vs_interval(float(q_hp), float(c_attrs["半周长_min"]), float(c_attrs["半周长_max"]))
            c_lmin = c_attrs.get("长度_min") or c_attrs.get("长_min")
            c_lmax = c_attrs.get("长度_max") or c_attrs.get("长_max")
            if c_lmin is not None and c_lmax is not None and ("长度" in q_attrs or "高" in q_attrs or "长" in q_attrs):
                q_l = float(q_attrs.get("长度") or q_attrs.get("高") or q_attrs.get("长"))
                s_len = score_point_vs_interval(q_l, float(c_lmin), float(c_lmax))
                dim_score = 0.6 * s_hp + 0.4 * s_len
                attr_scores["长度"] = s_len
                attr_weights["长度"] = domain_config.get("weights", {}).get("长度", 0.15)
            else:
                dim_score = s_hp
            attr_scores["尺寸"] = dim_score
            attr_scores["半周长"] = s_hp
            attr_weights["尺寸"] = domain_config.get("weights", {}).get("尺寸", 0.25)
            attr_weights["半周长"] = domain_config.get("weights", {}).get("半周长", 0.25)
            is_interval_dim = True
            has_any_attr = True

    elif c_attrs.get("尺寸_类型") == "3d_tolerance_box" and ("长" in q_attrs and "宽" in q_attrs and "高" in q_attrs):
        try:
            s_l = score_point_vs_interval(float(q_attrs["长"]), float(c_attrs["长_min"]), float(c_attrs["长_max"]))
            s_w = score_point_vs_interval(float(q_attrs["宽"]), float(c_attrs["宽_min"]), float(c_attrs["宽_max"]))
            s_h = score_point_vs_interval(float(q_attrs["高"]), float(c_attrs["高_min"]), float(c_attrs["高_max"]))
            tol_score = (s_l + s_w + s_h) / 3.0
            attr_scores["尺寸"] = tol_score
            attr_weights["尺寸"] = domain_config.get("weights", {}).get("尺寸", 0.25)
            is_interval_dim = True
            has_any_attr = True
        except Exception:
            pass

    ctx.is_interval_dim = is_interval_dim

    # B. Match Must & Should attributes
    for attr in processed_keys:
        if flags_list and attr in flags_list:
            continue
        # V017-M1 / M1-C02 修复 A-M1-01：box_type / tag_id 属设备位号与箱号元数据，
        # 已在 _apply_specialized_scoring（第 1293 行起）由配电箱型专用逻辑单独打分，
        # 严禁再进入本通用属性循环计 1.0 分，避免双重计分抬高目标分致 GT-0313 门禁退化。
        if attr in ("box_type", "tag_id"):
            continue
        if is_interval_dim and attr in ["尺寸", "半周长", "长", "宽", "高", "长度"]:
            continue

        q_val = q_attrs.get(attr)
        c_val = c_attrs.get(attr)

        if q_val is None:
            continue

        has_any_attr = True

        if c_val is not None or (f"{attr}_min" in c_attrs and f"{attr}_max" in c_attrs):
            # 1. Range overlap ratio logic
            if f"{attr}_min" in q_attrs and f"{attr}_min" in c_attrs:
                q_min = q_attrs[f"{attr}_min"]
                q_max = q_attrs[f"{attr}_max"]
                c_min = c_attrs[f"{attr}_min"]
                c_max = c_attrs[f"{attr}_max"]
                overlap = max(0.0, min(q_max, c_max) - max(q_min, c_min))
                union = max(q_max, c_max) - min(q_min, c_min)
                overlap_ratio = overlap / union if union > 0 else 0.0
                attr_scores[attr] = overlap_ratio
                attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                continue

            # 1b. Point vs Range interval scoring
            elif isinstance(q_val, (int, float)) and f"{attr}_min" in c_attrs and f"{attr}_max" in c_attrs:
                score = score_point_vs_interval(float(q_val), float(c_attrs[f"{attr}_min"]), float(c_attrs[f"{attr}_max"]))
                attr_scores[attr] = score
                attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                continue

            # 2. Inequality logic evaluation
            elif f"{attr}_op" in q_attrs:
                op = q_attrs[f"{attr}_op"]
                base_val = q_attrs.get(f"{attr}_base")
                if base_val is None:
                    q_min = q_attrs.get(f"{attr}_min")
                    q_max = q_attrs.get(f"{attr}_max")
                    if q_min is not None and q_max is not None:
                        try:
                            if f"{attr}_min" in c_attrs:
                                c_min = float(c_attrs[f"{attr}_min"])
                                c_max = float(c_attrs[f"{attr}_max"])
                            else:
                                c_v = float(c_val)
                                c_min, c_max = c_v, c_v
                            overlap = max(0.0, min(float(q_max), c_max) - max(float(q_min), c_min))
                            union = max(float(q_max), c_max) - min(float(q_min), c_min)
                            score = overlap / union if union > 0 else 0.0
                            attr_scores[attr] = score
                            attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                        except Exception:
                            pass
                    continue
                satisfied = False
                try:
                    if f"{attr}_min" in c_attrs:
                        c_min = c_attrs[f"{attr}_min"]
                        c_max = c_attrs[f"{attr}_max"]
                        if op == ">=": satisfied = (c_min >= base_val)
                        elif op == "<=": satisfied = (c_max <= base_val)
                        elif op == ">": satisfied = (c_min > base_val)
                        elif op == "<": satisfied = (c_max < base_val)
                    else:
                        c_v = float(c_val)
                        if op == ">=": satisfied = (c_v >= base_val)
                        elif op == "<=": satisfied = (c_v <= base_val)
                        elif op == ">": satisfied = (c_v > base_val)
                        elif op == "<": satisfied = (c_v < base_val)
                except Exception:
                    pass

                attr_scores[attr] = 1.0 if satisfied else 0.0
                attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                continue

            # 3. Special non-numerical types (filters, dimensions, cables)
            elif attr == "过滤段":
                q_fils = set(q_val) if isinstance(q_val, list) else set([q_val])
                c_fils = set(c_val) if isinstance(c_val, list) else set([c_val])
                if q_fils and c_fils:
                    intersect = len(q_fils.intersection(c_fils))
                    union = len(q_fils.union(c_fils))
                    fil_score = intersect / union if union > 0 else 0.0
                    attr_scores[attr] = fil_score
                    attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                continue
            elif attr == "尺寸":
                if q_val == c_val:
                    sim = 1.0
                else:
                    sim = calculate_text_similarity(str(q_val), str(c_val))
                attr_scores[attr] = sim
                attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                continue
            elif attr in ["芯数", "截面积"]:
                attr_scores[attr] = 1.0 if q_val == c_val else 0.0
                attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                continue

            # 4. Numerical attributes standard comparison
            elif isinstance(q_val, (int, float)) and isinstance(c_val, (int, float)):
                diff_ratio = abs(q_val - c_val) / max(q_val, c_val, 1e-5)
                tolerance = tolerance_rules.get(attr, 0.15)
                if diff_ratio == 0:
                    sim_score = 1.0
                elif diff_ratio <= tolerance:
                    sim_score = 1.0 - 0.15 * (diff_ratio / tolerance)
                elif tolerance < diff_ratio <= 2 * tolerance:
                    sim_score = 0.85 - ((diff_ratio - tolerance) / tolerance) * 0.45
                else:
                    sim_score = 0.1
                attr_scores[attr] = sim_score
                attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                continue

            # 5. Fallback direct match (boolean flags or enum strings)
            else:
                q_str, c_str = str(q_val).lower().strip(), str(c_val).lower().strip()
                # V015-M3：材质属性先按牌号枚举归一；双方牌号都已知且不同（304 vs 316L）
                # 判 0 分，未知牌号一侧保持 unknown 宽容，不臆断冲突
                q_grade = normalize_material_grade(q_val) if attr == "材质" else ""
                c_grade = normalize_material_grade(c_val) if attr == "材质" else ""
                if q_str == c_str:
                    sim_score = 1.0
                elif attr == "材质" and q_grade in KNOWN_MATERIAL_GRADES and c_grade in KNOWN_MATERIAL_GRADES:
                    sim_score = 1.0 if q_grade == c_grade else 0.0
                # V017-M1 / M1-C02：泛称"不锈钢"与具体牌号（304/316L）之间保持 unknown 宽容
                # ——"宽容"指不臆断冲突、也不判 0 分，而给中等兼容分（泛称确实涵盖具体牌号，
                # 但非完全等价）。这是相对 M0 旧列表（把 304/316L 也混同判 1.0）的纠偏：
                # 既不再把 304 vs 316L 误判等价，也不把"不锈钢"vs"304"误判为 0 分冲突。
                elif attr == "材质" and (
                    (q_str == "不锈钢" and c_grade in KNOWN_MATERIAL_GRADES)
                    or (q_grade in KNOWN_MATERIAL_GRADES and c_str == "不锈钢")
                ):
                    sim_score = 0.8
                # V017-M1 / M1-C02：材质泛称等价仅限"同泛称"之间；严禁把具体牌号
                # （304/316L/不锈钢304）与泛称"不锈钢"混入同一列表判 1.0。具体牌号
                # 已在上一分支按 KNOWN_MATERIAL_GRADES 严格区分；泛称与具体牌号之间
                # 维持 unknown 宽容（不臆断冲突，落 0.0 由下方硬约束单独处理）。
                elif (q_str in ["镀锌钢板", "镀锌板", "镀锌", "热镀锌"] and c_str in ["镀锌钢板", "镀锌板", "镀锌", "热镀锌"]) or \
                     (q_str == "不锈钢" and c_str == "不锈钢") or \
                     (q_str in ["紫铜", "脱脂紫铜", "紫铜管", "铜管", "铜"] and c_str in ["紫铜", "脱脂紫铜", "紫铜管", "铜管", "铜"]) or \
                     (any(s in q_str for s in ["碳钢", "碳素钢", "碳素钢板"]) and any(s in c_str for s in ["碳钢", "碳素钢", "碳素钢板"])) or \
                     (any(s in q_str for s in ["玻璃钢", "frp"]) and any(s in c_str for s in ["玻璃钢", "frp"])):
                    sim_score = 1.0
                else:
                    sim_score = 0.0
                attr_scores[attr] = sim_score
                attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                continue
        else:
            # Single-direction missing penalty: only when Query specifies Must field but Candidate lacks it
            if attr in ["尺寸", "半周长"] and (is_interval_dim or f"半周长_min" in c_attrs or f"长_min" in c_attrs or c_attrs.get("尺寸_类型") == "3d_tolerance_box"):
                pass
            elif attr in must_keys:
                missing_penalty += 0.15
                ctx.reasons.append(f"必填属性缺失处罚: {attr}")

    # Calculate weighted average attributes score
    if attr_scores:
        sum_weighted_scores = sum(attr_scores[k] * attr_weights[k] for k in attr_scores)
        sum_weights = sum(attr_weights[k] for k in attr_scores)
        attr_score = sum_weighted_scores / sum_weights if sum_weights > 0 else 0.0
        
        for k, s in attr_scores.items():
            if (k in tolerance_rules or k in must_keys or k in ["尺寸", "半周长", "材质", "控制方式", "芯数", "截面积"]) and s < 0.8:
                all_attrs_in_tolerance = False
        if all_attrs_in_tolerance:
            ctx.reasons.append("核心参数在公差范围内")
    else:
        attr_score = calculate_text_similarity(ctx.q_spec_text, ctx.c_full_spec)
        all_attrs_in_tolerance = False

    ctx.has_any_attr = has_any_attr
    ctx.attr_scores = attr_scores
    ctx.attr_weights = attr_weights
    ctx.attr_score = attr_score
    ctx.all_attrs_in_tolerance = all_attrs_in_tolerance
    ctx.missing_penalty = missing_penalty


def _deduplicated_attribute_score(ctx: ScoringContext) -> float:
    """Use one weighted comparison for each physical fact, keep all raw comparisons for gates."""
    scores = ctx.attr_scores
    if not scores:
        return ctx.attr_score

    # The parser emits both the canonical fields and compatibility aliases.
    # Composite cable values are redundant only when their components were compared.
    aliases = {
        "dn_val": "管径",
        "de_val": "外径",
        "外径mm": "外径",
        "model": "型号",
    }
    excluded = {"品牌"} if ctx.q_brand and ctx.c_brand else set()
    if ctx.model_score:
        for key in ("型号", "model"):
            if (str(ctx.q_attrs.get(key, "")).casefold() == ctx.q_model.casefold()
                    and str(ctx.c_attrs.get(key, "")).casefold() == ctx.c_model.casefold()):
                excluded.add(key)
    if ctx.q_attrs.get("名称") == ctx.q_name and ctx.c_attrs.get("名称") == ctx.c_name:
        excluded.add("名称")
    if ctx.is_interval_dim:
        excluded.update(("半周长", "长度"))  # already represented by the interval's 尺寸 score
    for composite, main, neutral in (("芯数", "主芯数", "零芯数"), ("截面积", "主截面", "零截面")):
        if main in scores and (neutral in scores or
                               all("+" not in str(a.get(composite, "")) for a in (ctx.q_attrs, ctx.c_attrs))):
            excluded.add(composite)

    groups = {}
    for key in sorted(scores):
        if key in excluded:
            continue
        score = scores[key]
        if not math.isfinite(score):
            continue
        if key != "flags" and any(
            isinstance(attrs.get(key), str) and not attrs[key].strip()
            for attrs in (ctx.q_attrs, ctx.c_attrs)
        ):
            continue
        fact = aliases.get(key, key)
        group = groups.setdefault(fact, [])
        group.append((key, score, ctx.attr_weights[key]))
    weighted_score = total_weight = 0.0
    for fact, group in groups.items():
        ctx.ranking_fields[fact] = tuple(key for key, _, _ in group)
        weight = max(weight for _, _, weight in group)
        total_weight += weight
        weighted_score += min(score for _, score, _ in group) * weight
    return weighted_score / total_weight if total_weight > 0.0 else 0.0


def _ranking_weights(ctx: ScoringContext, weights: Dict[str, float]) -> Dict[str, float]:
    discipline = normalize_professional_category(ctx.q_prof)
    factors = _DISCIPLINE_WEIGHT_MULTIPLIERS.get(discipline) if ctx.profile == "calibrated" else None
    if factors is None:
        return weights
    keys = ("model", "attrs", "text")
    scaled = {key: weights.get(key, 0.0) * factor for key, factor in zip(keys, factors)}
    total = sum(scaled.values())
    if not total:
        return weights
    scale = sum(weights.get(key, 0.0) for key in keys) / total
    return {**weights, **{key: weight * scale for key, weight in scaled.items()}}


def _calculate_base_score_and_positive_boosts(ctx: ScoringContext):
    """Determine matching scenario, compute base score, and apply positive boosts."""
    q_model = ctx.q_model
    c_model = ctx.c_model
    has_any_attr = ctx.has_any_attr
    domain = ctx.domain
    
    if q_model and c_model:
        scene = "scene_a"
    elif has_any_attr and domain != "generic":
        scene = "scene_b"
    else:
        scene = "scene_c"
    ctx.scene = scene
        
    weights = weight_rules.get(scene, DEFAULT_WEIGHTS[scene])
    experimental = ctx.profile != "legacy"
    attr_score = ctx.attr_score
    if experimental:
        weights = _ranking_weights(ctx, weights)
        attr_score = _deduplicated_attribute_score(ctx)
    
    base_score = (
        weights.get("model", 0.0) * ctx.model_score +
        weights.get("attrs", 0.0) * attr_score +
        weights.get("text", 0.0) * ctx.text_score
    ) - ctx.missing_penalty
    
    # Add semantic vector contribution
    if ctx.semantic_score > 0.0:
        vector_boost = weights.get("vector_boost", 0.20)
        base_score += ctx.semantic_score * vector_boost
        if ctx.semantic_score > 0.75:
            ctx.reasons.append("语义高度相近")
            
    # Apply Positive Boosts
    pos_rules = boost_rules.get("positive", DEFAULT_BOOSTS["positive"])
    series_boost = brand_boost = category_penalty = 0.0
    if ctx.model_matched_exact and not experimental:
        base_score += pos_rules.get("model_exact", 0.15)
        
    # Exact or synonym name match boost
    name_matched = False
    if ctx.q_name == ctx.c_name:
        name_matched = True
    else:
        weak_cable_syns = ["网线", "双绞线", "对线", "网络线"]
        if any(s in ctx.q_name for s in weak_cable_syns) and any(s in ctx.c_name for s in weak_cable_syns):
            name_matched = True
    ctx.name_matched = name_matched
    if name_matched and not experimental:
        base_score += 0.12
    if ctx.attr_scores and ctx.all_attrs_in_tolerance and not experimental:
        base_score += pos_rules.get("attrs_exact", 0.10)
        if ctx.text_score >= 0.5:
            base_score += 0.08
    if ctx.cat_sim > 0.9:
        if not experimental:
            base_score += pos_rules.get("category_match", 0.08)
        if ctx.q_category and ctx.c_category and ctx.q_category.strip() == ctx.c_category.strip():
            if not experimental:
                base_score += 0.12
            ctx.reasons.append("分类精准匹配")
        elif ctx.q_category and ctx.c_category and ctx.q_category.strip() != ctx.c_category.strip():
            category_penalty = 0.20
            base_score -= category_penalty
            ctx.reasons.append("物理分类不符")
    if check_series_match(q_model, c_model):
        if not experimental or not ctx.model_score:
            series_boost = pos_rules.get("series_match", 0.05)
            base_score += series_boost
        ctx.reasons.append("属同系列产品")
    # V015-M3：品牌加分复用真实品牌解析（品牌列空时回退 parsed_params.attrs.品牌），
    # 双方均有真实值且标准化后一致才加分；None/缺值/坏 JSON 为 unknown，不臆断
    q_brand_val = ctx.query_info.get("brand") or ctx.q_attrs.get("品牌")
    c_brand_val = resolve_material_brand(ctx.candidate)
    if q_brand_val and c_brand_val:
        if standardize_brand(str(q_brand_val)) == standardize_brand(str(c_brand_val)):
            brand_boost = pos_rules.get("brand_match", 0.05)
            base_score += brand_boost
            ctx.reasons.append("品牌一致")

    # Hard cap for candidates with out-of-tolerance core attributes
    if not ctx.all_attrs_in_tolerance and has_any_attr:
        base_score = min(base_score, 0.85)
        # V015-M3：局部 cap 同步收口不可逆上限，后置动态/系列加分只能收口不能突破
        ctx.max_score = min(ctx.max_score, 0.85)
        
    # Check exact string matches using clean_text_standard under unified RuleContext
    from app.normalizer import clean_text_standard_with_diagnostics
    q_clean_ctx = RuleContext.from_query(ctx.query_info, stage="cleaning")
    c_prof_val = ctx.c_prof if (ctx.c_prof and ctx.c_prof != "其它") else ""
    c_clean_ctx = RuleContext.from_candidate(
        ctx.candidate,
        name=ctx.c_name,
        domain=ctx.domain,
        professional=c_prof_val,
        stage="cleaning",
    )
    q_norm_full, q_diag_full = clean_text_standard_with_diagnostics(
        f"{ctx.q_name} {ctx.q_spec_text}", rule_context=q_clean_ctx
    )
    c_norm_full, c_diag_full = clean_text_standard_with_diagnostics(
        f"{ctx.c_name} {ctx.c_full_spec}", rule_context=c_clean_ctx
    )
    q_norm_spec, q_diag_spec = clean_text_standard_with_diagnostics(
        ctx.q_spec_text, rule_context=q_clean_ctx
    )
    c_norm_spec, c_diag_spec = clean_text_standard_with_diagnostics(
        ctx.c_full_spec, rule_context=c_clean_ctx
    )
    for _cdiag in (q_diag_full, c_diag_full, q_diag_spec, c_diag_spec):
        if _cdiag.get("degraded") and _cdiag.get("errors"):
            ctx.blocked = True
            ctx.max_score = 0.0
            ctx.reasons.append(f"{DYNAMIC_RULE_UNAVAILABLE_PREFIX}: {_cdiag['errors'][0].get('message', '清洗规则异常')}")
            break
    ctx.q_norm_full = q_norm_full
    ctx.c_norm_full = c_norm_full
    ctx.q_norm_spec = q_norm_spec
    ctx.c_norm_spec = c_norm_spec

    exact_full = bool(q_norm_full and c_norm_full and q_norm_full == c_norm_full)
    exact_spec = bool(q_norm_spec and c_norm_spec and q_norm_spec == c_norm_spec)
    if exact_full:
        if not experimental:
            base_score += 0.30
        ctx.reasons.append("名称与规格完全一致")
    elif exact_spec:
        if not experimental:
            base_score += 0.20
        ctx.reasons.append("规格型号完全一致")

    if experimental:
        spec_adjustment = 0.0
        if exact_spec and not ctx.model_score and not ctx.attr_scores:
            # An unparsed exact specification supplies independent evidence.
            # Replace its existing fallback similarity contribution.
            spec_adjustment = max(0.0, 0.20 - weights.get("attrs", 0.0) * attr_score)
            base_score += spec_adjustment
        ctx.score_components = {
            "model": weights.get("model", 0.0) * ctx.model_score,
            "attributes": weights.get("attrs", 0.0) * attr_score,
            "name_or_category": weights.get("text", 0.0) * ctx.text_score,
            "semantic": weights.get("vector_boost", 0.20) * max(0.0, ctx.semantic_score),
            "unparsed_spec_adjustment": spec_adjustment,
            "series": series_boost,
            "brand": brand_boost,
            "category_penalty": category_penalty,
            "missing_penalty": ctx.missing_penalty,
            "pre_gate_score": base_score,
        }
    ctx.base_score = base_score


def _apply_conduit_cable_and_electrical_gates(ctx: ScoringContext):
    """Apply negative boosts and electrical/cable domain rules (Conduit, Boxes, Gangs, Conductor)."""
    base_score = ctx.base_score
    q_name = ctx.q_name
    q_spec_text = ctx.q_spec_text
    c_name = ctx.c_name
    c_full_spec = ctx.c_full_spec
    q_attrs = ctx.q_attrs
    c_attrs = ctx.c_attrs
    
    neg_rules = boost_rules.get("negative", DEFAULT_BOOSTS["negative"])
    if check_category_conflict(ctx.q_category, ctx.c_category):
        base_score += neg_rules.get("category_conflict", -0.30)
        ctx.reasons.append("品类冲突(偏置)")
    if "冷水" in q_spec_text and "热水" in c_full_spec or "热水" in q_spec_text and "冷水" in c_full_spec:
        base_score += neg_rules.get("medium_conflict", -0.20)
        ctx.reasons.append("温水介质冲突")

    # 1. Conduit vs Cable Tray Discrimination
    q_is_conduit = any(k in f"{q_name} {q_spec_text}".lower() for k in ["线管", "导管", "穿线管", "套管", "jdg", "kbg"])
    c_is_conduit = any(k in f"{c_name} {c_full_spec}".lower() for k in ["线管", "导管", "穿线管", "套管", "jdg", "kbg"])
    c_is_tray = any(k in f"{c_name} {c_full_spec}".lower() for k in ["桥架", "托盘", "梯架"])
    
    if q_is_conduit and c_is_tray and not c_is_conduit:
        base_score -= 0.35
        ctx.reasons.append("物料类型冲突(线管/桥架)")
    elif q_is_conduit and c_is_conduit:
        base_score += 0.15
        ctx.reasons.append("线管品类匹配")

    # 2. Line Pipe Model and Diameter matching
    CONDUIT_MODELS = ["JDG", "KBG", "SC", "PVC", "PVC-U", "KJG", "TC", "PC"]
    q_cond_m = q_attrs.get("model")
    c_text_upper = f"{c_name} {c_full_spec}".upper()
    c_cond_m = c_attrs.get("model")
    if not c_cond_m:
        for m in CONDUIT_MODELS:
            if m in c_text_upper:
                c_cond_m = m
                break
    
    if q_cond_m and q_cond_m in CONDUIT_MODELS:
        if c_cond_m == q_cond_m:
            base_score += 0.20
            ctx.reasons.append(f"线管型号匹配({q_cond_m})")
        elif c_cond_m and c_cond_m != q_cond_m and c_cond_m in CONDUIT_MODELS:
            base_score -= 0.30
            ctx.reasons.append("线管型号冲突")
            
    if q_attrs.get("dn") and c_attrs.get("dn"):
        if abs(q_attrs["dn"] - c_attrs["dn"]) < 0.1:
            base_score += 0.25
            ctx.reasons.append("管径精确匹配")
        elif abs(q_attrs["dn"] - c_attrs["dn"]) > 0.5 and q_is_conduit:
            base_score -= 0.35
            ctx.reasons.append("管径冲突拦截")

    # 3. Electrical Distribution Box & Control Cabinet Prefix matching
    from app.domain_extractors import extract_switchgear_params
    q_box = q_attrs.get("box_type") or extract_switchgear_params(f"{q_name} {q_spec_text}").get("box_type")
    c_box = c_attrs.get("box_type") or extract_switchgear_params(f"{c_name} {c_full_spec}").get("box_type")
    
    if q_box:
        if c_box and q_box == c_box:
            base_score += 0.25
            ctx.reasons.append(f"配电箱型匹配({q_box})")
        elif c_box and q_box != c_box:
            base_score -= 0.30
            ctx.reasons.append("配电箱型冲突")

    # 4. Power Range Matching for Distribution Boxes
    from app.domain_extractors import extract_power_range
    c_pw = extract_power_range(f"{c_name} {c_full_spec}")
    q_pw = extract_power_range(f"{q_name} {q_spec_text}")
    q_pmin = q_attrs.get("power_min") or q_pw.get("power_min")
    q_pmax = q_attrs.get("power_max") or q_pw.get("power_max")
    q_power = q_attrs.get("power") or q_pw.get("power")
    c_pmin = c_attrs.get("power_min") or c_pw.get("power_min")
    c_pmax = c_attrs.get("power_max") or c_pw.get("power_max")
    
    if q_pmin is not None and c_pmin is not None:
        if abs(q_pmin - c_pmin) < 0.1 and abs(q_pmax - c_pmax) < 0.1:
            base_score += 0.30
            ctx.reasons.append(f"功率段对齐({int(q_pmin)}~{int(q_pmax)}KW)")
        else:
            base_score = min(base_score, 0.45)
            ctx.max_score = min(ctx.max_score, 0.45)
            ctx.reasons.append("功率段不匹配扣分")
    elif q_power is not None and c_pmin is not None and c_pmax is not None:
        if c_pmin <= q_power <= c_pmax:
            base_score += 0.30
            ctx.reasons.append(f"功率符合功率段({int(c_pmin)}~{int(c_pmax)}KW)")
        else:
            base_score = min(base_score, 0.45)
            ctx.max_score = min(ctx.max_score, 0.45)
            ctx.reasons.append("功率超出候选功率段")

    # 5. Switch Gangs & Control Matching
    from app.domain_extractors import extract_switch_gang_control
    q_sw = extract_switch_gang_control(f"{q_name} {q_spec_text}")
    c_sw = extract_switch_gang_control(f"{c_name} {c_full_spec}")
    if q_sw.get("gangs") and c_sw.get("gangs"):
        if q_sw["gangs"] == c_sw["gangs"]:
            base_score += 0.20
            ctx.reasons.append("开关联数一致")
        else:
            base_score -= 0.35
            ctx.reasons.append("开关联数冲突")
    if q_sw.get("control") and c_sw.get("control"):
        if q_sw["control"] == c_sw["control"]:
            base_score += 0.20
            ctx.reasons.append("开关控数一致")
        else:
            base_score -= 0.35
            ctx.reasons.append("开关控数冲突")

    # 6. Cable Conductor Material
    q_is_copper = any(k in f"{q_name} {q_spec_text}".upper() for k in ["YJY", "BYJ", "BVR", "BV", "YJV", "NH-YJV", "WDZ"])
    c_is_alum = any(k in f"{c_name} {c_full_spec}".upper() for k in ["YJLV", "BLV", "BLVY", "铝芯"])
    if q_is_copper and c_is_alum:
        base_score -= 0.40
        ctx.reasons.append("导体材质冲突(铜/铝)")

    # Out of 2x tolerance check
    has_large_deviation = False
    for attr in ctx.processed_keys:
        q_val = q_attrs.get(attr)
        c_val = c_attrs.get(attr)
        if attr == "风量" and isinstance(q_val, (int, float)):
            c_min = c_attrs.get("风量_min")
            c_max = c_attrs.get("风量_max")
            if c_min is not None and c_max is not None:
                if c_min <= q_val <= c_max:
                    continue
        if isinstance(q_val, (int, float)) and isinstance(c_val, (int, float)):
            diff_ratio = abs(q_val - c_val) / max(q_val, c_val, 1e-5)
            tolerance = tolerance_rules.get(attr, 0.15)
            if diff_ratio > 2 * tolerance:
                has_large_deviation = True
                
    if has_large_deviation:
        base_score += neg_rules.get("attrs_deviation_2x", -0.15)
        ctx.reasons.append("参数超限偏离")
        
    if ctx.model_conflict:
        base_score += neg_rules.get("model_conflict", -0.25)
        ctx.reasons.append("型号冲突扣分")

    ctx.base_score = base_score


def _apply_config_hard_gates(ctx: ScoringContext):
    """Apply domain schema configuration hard gates (conflict caps, deviation caps, ratio penalties)."""
    base_score = ctx.base_score
    domain_config = ctx.domain_config
    q_attrs = ctx.q_attrs
    c_attrs = ctx.c_attrs

    # 1. Conflict Caps
    conflict_caps = domain_config.get("hard_gates", {}).get("conflict_caps", {})
    for attr_name, cap_val in conflict_caps.items():
        q_val = q_attrs.get(attr_name)
        c_val = c_attrs.get(attr_name)
        if q_val is not None and c_val is not None:
            if has_conflict(attr_name, q_val, c_val):
                base_score = min(base_score, cap_val / 100.0)
                # V015-M3：配置型冲突 cap 同步收口不可逆上限
                ctx.max_score = min(ctx.max_score, cap_val / 100.0)
                ctx.reasons.append(f"{attr_name}冲突拦截")

    # 2. Deviation Caps
    deviation_caps = domain_config.get("hard_gates", {}).get("deviation_caps", {})
    for attr_name, cap_config in deviation_caps.items():
        q_val = q_attrs.get(attr_name)
        c_val = c_attrs.get(attr_name)
        if attr_name == "风量" and isinstance(q_val, (int, float)):
            c_min = c_attrs.get("风量_min")
            c_max = c_attrs.get("风量_max")
            if c_min is not None and c_max is not None:
                if c_min <= q_val <= c_max:
                    continue
        if isinstance(q_val, (int, float)) and isinstance(c_val, (int, float)):
            diff_ratio = abs(q_val - c_val) / max(q_val, c_val, 1e-5)
            abs_diff = abs(q_val - c_val)
            
            ratio = cap_config.get("ratio", 0.5)
            cap_val = cap_config.get("cap", 30) / 100.0
            
            small_threshold = cap_config.get("small_threshold")
            abs_diff_small = cap_config.get("abs_diff_small")
            abs_diff_standard = cap_config.get("abs_diff_standard")
            
            if small_threshold is not None and q_val <= small_threshold:
                limit = abs_diff_small
            else:
                limit = abs_diff_standard
                
            if diff_ratio > ratio and (limit is None or abs_diff > limit):
                base_score = min(base_score, cap_val)
                # V015-M3：偏差 cap 同步收口不可逆上限
                ctx.max_score = min(ctx.max_score, cap_val)
                ctx.reasons.append(f"{attr_name}偏差过大拦截")

    # 3. Ratio Penalties
    ratio_penalties = domain_config.get("hard_gates", {}).get("ratio_penalties", {})
    for attr_name, penalty_ratio in ratio_penalties.items():
        q_val = q_attrs.get(attr_name)
        c_val = c_attrs.get(attr_name)
        if q_val is not None and c_val is not None:
            if has_conflict(attr_name, q_val, c_val):
                base_score = base_score * penalty_ratio
                # V015-M3：乘法惩罚后的当前分即新不可逆上限，后置加分不得回到惩罚前
                ctx.max_score = min(ctx.max_score, base_score)
                ctx.reasons.append(f"{attr_name}冲突惩罚")

    ctx.base_score = base_score


def _apply_domain_cable_and_pipe_penalties(ctx: ScoringContext):
    """Apply domain-specific penalties for Cable, Pipe, and Decoration items."""
    base_score = ctx.base_score
    q_attrs = ctx.q_attrs
    c_attrs = ctx.c_attrs

    # A. Cable Parameter Conflict Penalties
    q_main_cores = q_attrs.get("主芯数")
    c_main_cores = c_attrs.get("主芯数")
    q_cores = q_attrs.get("芯数") or q_main_cores
    c_cores = c_attrs.get("芯数") or c_main_cores
    if q_main_cores is not None and c_main_cores is not None:
        try:
            if int(q_main_cores) != int(c_main_cores):
                base_score -= 0.45
                ctx.reasons.append("线缆芯数冲突")
        except (ValueError, TypeError):
            if str(q_cores) != str(c_cores):
                base_score -= 0.45
                ctx.reasons.append("线缆芯数冲突")
    elif q_cores and c_cores and str(q_cores) != str(c_cores):
        base_score -= 0.45
        ctx.reasons.append("线缆芯数冲突")

    q_main_sec = q_attrs.get("主截面")
    c_main_sec = c_attrs.get("主截面")
    q_sec = q_attrs.get("截面积") or q_main_sec
    c_sec = c_attrs.get("截面积") or c_main_sec
    if q_main_sec is not None and c_main_sec is not None:
        try:
            if abs(float(q_main_sec) - float(c_main_sec)) > 0.05:
                base_score -= 0.45
                ctx.reasons.append("线缆截面冲突")
        except (ValueError, TypeError):
            if str(q_sec) != str(c_sec):
                base_score -= 0.45
                ctx.reasons.append("线缆截面冲突")
    elif q_sec and c_sec and str(q_sec) != str(c_sec):
        base_score -= 0.45
        ctx.reasons.append("线缆截面冲突")

    q_cable_m = q_attrs.get("型号")
    c_cable_m = c_attrs.get("型号")
    if q_cable_m and c_cable_m and q_cable_m != c_cable_m:
        base_score -= 0.30
        ctx.reasons.append("线缆型号冲突")

    # B. Pipe Parameter Conflict Penalties
    # V015-M3：DN 与外径分字段硬约束（跨字段不比、缺值 unknown 不阻断、15=15.0 数值归一），
    # 复用四链路共享纯函数；真冲突收口到 0.15 不可逆上限，不再是可被加分抵消的软减分
    dn_od_reason = check_dn_od_conflicts(q_attrs, c_attrs)
    if dn_od_reason:
        base_score = min(base_score, 0.15)
        ctx.max_score = min(ctx.max_score, 0.15)
        ctx.reasons.append(dn_od_reason)

    q_wall = q_attrs.get("壁厚")
    c_wall = c_attrs.get("壁厚")
    if q_wall and c_wall and isinstance(q_wall, (int, float)) and isinstance(c_wall, (int, float)):
        if abs(q_wall - c_wall) > 0.05:
            base_score -= 0.35
            ctx.reasons.append("管材壁厚冲突")

    q_mat = q_attrs.get("材质")
    c_mat = c_attrs.get("材质")
    if q_mat and c_mat and str(q_mat).lower() != str(c_mat).lower():
        q_mat_str = str(q_mat).lower()
        c_mat_str = str(c_mat).lower()
        copper_syns = ["紫铜", "脱脂紫铜", "紫铜管", "铜管", "铜", "无缝紫铜管", "脱脂无缝紫铜管", "磷酸脱氧无缝紫铜管", "脱氧紫铜管"]
        galv_syns = ["镀锌钢板", "镀锌板", "镀锌", "热镀锌", "优质镀锌钢板"]
        ss_syns = ["304", "316", "316l", "不锈钢", "不锈钢304", "不锈钢316l", "s30408"]
        frp_syns = ["玻璃钢", "frp"]
        carbon_syns = ["碳钢", "碳素钢", "碳素钢板", "优质碳素钢", "普通碳素钢", "碳素结构钢", "碳钢板", "碳素钢薄钢板", "钢", "钢板", "优质碳素钢板", "优质钢板"]
        if (any(s in q_mat_str for s in copper_syns)) and (any(s in c_mat_str for s in copper_syns)):
            pass
        elif (any(s in q_mat_str for s in galv_syns)) and (any(s in c_mat_str for s in galv_syns)):
            pass
        elif (any(s in q_mat_str for s in carbon_syns)) and (any(s in c_mat_str for s in carbon_syns)):
            pass
        elif (any(s in q_mat_str for s in frp_syns)) and (any(s in c_mat_str for s in frp_syns)):
            pass
        elif (any(s in q_mat_str for s in ss_syns)) and (any(s in c_mat_str for s in ss_syns)):
            # V015-M3：304/316L 按材质枚举归一，真牌号冲突升级硬隔离，
            # 不被数值归一化或同组加分抵消；一侧牌号未知时保持 unknown 宽容
            q_grade = normalize_material_grade(q_mat)
            c_grade = normalize_material_grade(c_mat)
            if q_grade in KNOWN_MATERIAL_GRADES and c_grade in KNOWN_MATERIAL_GRADES and q_grade != c_grade:
                base_score = min(base_score, 0.15)
                ctx.max_score = min(ctx.max_score, 0.15)
                ctx.reasons.append("不锈钢牌号冲突硬隔离(304/316L)")
        elif q_mat_str != c_mat_str:
            base_score -= 0.40
            ctx.reasons.append("材质规格冲突")

    # V015-M3：外径冲突已由上方 check_dn_od_conflicts 统一按分字段硬约束处理，
    # 此处不再保留 DN/外径混用的旧子串比较块

    # C. Decoration & Equipment Dimension Conflict Penalties
    q_dim = q_attrs.get("尺寸_2d") or q_attrs.get("尺寸")
    c_dim = c_attrs.get("尺寸_2d") or c_attrs.get("尺寸")
    if q_dim and c_dim:
        if check_dimension_conflict(q_dim, c_dim, max_deviation=0.15):
            base_score -= 0.40
            ctx.reasons.append("尺寸规格冲突")

    q_th = q_attrs.get("厚度")
    c_th = c_attrs.get("厚度")
    if q_th and c_th and isinstance(q_th, (int, float)) and isinstance(c_th, (int, float)):
        if abs(q_th - c_th) > 0.05:
            base_score -= 0.35
            ctx.reasons.append("厚度规格冲突")

    ctx.base_score = base_score


def _apply_hvac_gates(ctx: ScoringContext):
    """Apply HVAC domain gates (Dampers, AHU/PAU, FCU, VRF, Fans, Ductwork)."""
    base_score = ctx.base_score
    q_name = ctx.q_name
    q_spec_text = ctx.q_spec_text
    c_name = ctx.c_name
    c_full_spec = ctx.c_full_spec
    q_attrs = ctx.q_attrs
    c_attrs = ctx.c_attrs
    candidate = ctx.candidate
    c_code_val = str(candidate.get("code", ""))

    if "两管制" in q_spec_text and "四管制" in c_full_spec or "四管制" in q_spec_text and "两管制" in c_full_spec:
        base_score -= 0.35
        ctx.reasons.append("管制模式冲突")

    if "变频电机" in q_spec_text and ("EC风机" in c_full_spec or "直流" in c_full_spec):
        base_score -= 0.25
        ctx.reasons.append("电机类型冲突")

    if "底漆" in q_spec_text and "面漆" in c_full_spec or "面漆" in q_spec_text and "底漆" in c_full_spec:
        base_score -= 0.35
        ctx.reasons.append("涂膜类型冲突")

    if "手工" in q_spec_text and "机制" in c_full_spec or "机制" in q_spec_text and "手工" in c_full_spec:
        base_score -= 0.35

    # 1. Fire Damper Controls & Hard Gates
    is_q_fire_damper = any(k in f"{q_name} {q_spec_text}" for k in ["防火阀", "排烟防火阀", "常开防火阀", "常闭防火阀", "70℃", "280℃", "70度", "280度"])
    is_c_fire_damper = any(k in f"{c_name} {c_full_spec}" for k in ["防火阀", "排烟防火阀", "电动防火阀"]) or c_code_val.startswith("2.22.53.") or c_code_val.startswith("2.22.54.")

    if is_q_fire_damper and is_c_fire_damper:
        q_is_circular = q_attrs.get("is_circular") is True or "φ" in f"{q_name} {q_spec_text}" or "直径" in f"{q_name} {q_spec_text}" or "DN" in f"{q_name} {q_spec_text}"
        c_is_circular = c_attrs.get("外径") is not None or "φ" in c_full_spec or "直径" in c_full_spec or "半周长" in c_full_spec or c_code_val == "2.22.53.7729"
        
        q_dim_2d = q_attrs.get("尺寸_2d") or q_attrs.get("尺寸")
        if not q_dim_2d:
            d_match = re.search(r'(\d+)\s*[*x×]\s*(\d+)', f"{q_name} {q_spec_text}")
            if d_match:
                q_dim_2d = f"{d_match.group(1)}*{d_match.group(2)}"

        if q_dim_2d and not q_is_circular:
            if c_is_circular:
                base_score = min(base_score, 0.15)
                ctx.max_score = min(ctx.max_score, 0.15)
                ctx.reasons.append("截面形状冲突(矩形/圆形硬隔离)")
            else:
                c_dim_2d = c_attrs.get("尺寸_2d") or c_attrs.get("尺寸")
                if not c_dim_2d:
                    cd_match = re.search(r'(\d+)\s*[*x×]\s*(\d+)', c_full_spec)
                    if cd_match:
                        c_dim_2d = f"{cd_match.group(1)}*{cd_match.group(2)}"
                if c_dim_2d:
                    q_d = sorted([float(x) for x in re.findall(r'\d+', str(q_dim_2d))])
                    c_d = sorted([float(x) for x in re.findall(r'\d+', str(c_dim_2d))])
                    if q_d == c_d and len(q_d) == 2:
                        base_score += 0.40
                        ctx.reasons.append(f"防火阀尺寸精确匹配({q_dim_2d})")
                    else:
                        base_score -= 0.45
                        ctx.reasons.append("防火阀尺寸不一致")
        elif q_is_circular and not c_is_circular:
            base_score = min(base_score, 0.15)
            ctx.max_score = min(ctx.max_score, 0.15)
            ctx.reasons.append("截面形状冲突(圆形/矩形硬隔离)")

        # Electric vs Normal Fusible Hard Gate
        q_is_electric = q_attrs.get("电动") is True or any(k in f"{q_name} {q_spec_text}".upper() for k in ["电动", "DV24V", "DC24V", "AC220V", "执行器", "执行机构", "24V"])
        c_is_electric = "电动" in c_name or "DV24V" in c_full_spec or "DC24V" in c_full_spec or "24V" in c_full_spec or c_code_val.startswith("2.22.54.")

        if not q_is_electric and c_is_electric:
            base_score = min(base_score, 0.15)
            ctx.max_score = min(ctx.max_score, 0.15)
            ctx.reasons.append("控制方式冲突(常开熔断/电动硬隔离)")
        elif q_is_electric and not c_is_electric:
            base_score = min(base_score, 0.15)
            ctx.max_score = min(ctx.max_score, 0.15)
            ctx.reasons.append("控制方式冲突(电动/手动硬隔离)")
        elif not q_is_electric and not c_is_electric:
            base_score += 0.20
            ctx.reasons.append("常开防火阀类型匹配")

        # Fusible Temperature
        q_temp = q_attrs.get("熔断温度")
        if not q_temp:
            if "70" in f"{q_name} {q_spec_text}":
                q_temp = 70
            elif "280" in f"{q_name} {q_spec_text}":
                q_temp = 280
        if q_temp:
            if f"{q_temp}℃" in c_full_spec or f"{q_temp}度" in c_full_spec:
                base_score += 0.20
                ctx.reasons.append(f"熔断温度匹配({q_temp}℃)")
            elif (q_temp == 70 and "280" in c_full_spec) or (q_temp == 280 and "70" in c_full_spec):
                base_score -= 0.50
                ctx.reasons.append("熔断温度冲突(70℃/280℃)")

        # Damper Material
        if "镀锌" in f"{q_name} {q_spec_text}" or "镀锌" in str(q_attrs.get("材质", "")):
            if "镀锌钢板" in c_full_spec and "内衬" not in c_full_spec:
                base_score += 0.15
                ctx.reasons.append("镀锌钢板材质匹配")
            elif "不锈钢" in c_full_spec or "内衬" in c_full_spec:
                base_score -= 0.35
                ctx.reasons.append("阀门材质冲突")

    # 2. AHU / PAU Air Handling Unit Matching
    is_q_ahu = "AHU" in q_name.upper() or "组合式" in q_name or "医用洁卫型空调机组AHU" in q_name
    is_q_pau = "PAU" in q_name.upper() or "新风机组" in q_name or "医用洁卫型空调机组PAU" in q_name

    if is_q_ahu or is_q_pau or c_code_val.startswith("3.60.21."):
        q_is_ceiling = q_attrs.get("吊装") is True or "吊装" in f"{q_name} {q_spec_text}" or "吊顶" in f"{q_name} {q_spec_text}"
        q_is_floor = q_attrs.get("落地") is True or "落地" in f"{q_name} {q_spec_text}"
        
        c_is_ceiling = "吊顶" in c_name or c_code_val.startswith("3.60.21.30")
        c_is_floor = "净化新风" in c_name or "净化循环" in c_name or c_code_val.startswith("3.60.21.20") or c_code_val.startswith("3.60.21.01") or c_code_val.startswith("3.60.21.00")

        if is_q_pau:
            if q_is_ceiling:
                if c_is_ceiling:
                    base_score += 0.40
                    ctx.reasons.append("吊顶新风机组匹配")
                elif c_is_floor:
                    base_score -= 0.45
                    ctx.reasons.append("吊顶/落地机组冲突")
            elif q_is_floor:
                if "净化新风机组" in c_name:
                    base_score += 0.40
                    ctx.reasons.append("落地净化新风机组匹配")
                elif c_is_ceiling:
                    base_score -= 0.45
                    ctx.reasons.append("落地/吊顶机组冲突")

        q_is_self_fresh = q_attrs.get("自取新风") is True or "自取新风" in f"{q_name} {q_spec_text}"
        if not q_is_self_fresh and is_q_ahu:
            sf_v = q_attrs.get("送风量") or q_attrs.get("风量")
            xf_v = q_attrs.get("新风量")
            if sf_v and xf_v and abs(float(sf_v) - float(xf_v)) < 1.0:
                q_is_self_fresh = True

        if is_q_ahu and q_is_self_fresh:
            if "净化循环机组（自取新风）" in c_name or "自取新风" in c_name:
                base_score += 0.40
                ctx.reasons.append("净化循环机组(自取新风)匹配")
            elif "净化新风机组" in c_name or "吊顶新风机组" in c_name:
                base_score -= 0.35
                ctx.reasons.append("循环/新风机组类型冲突")

        if "变频" in f"{q_name} {q_spec_text}" or "变频" in c_full_spec:
            if "变频电机" in c_full_spec:
                base_score += 0.15
                ctx.reasons.append("变频电机配置优选")

        q_vol = q_attrs.get("风量") or q_attrs.get("送风量")
        if q_vol is not None:
            c_spec_clean = re.sub(r'm3/h|m³/h|m3|m³', '', c_full_spec, flags=re.I)
            c_vols = [float(x) for x in re.findall(r'(?:送风量|风量|新风量)[^\d]*(\d+)', c_spec_clean)]
            if not c_vols:
                c_vols = [float(x) for x in re.findall(r'(\d+)', c_spec_clean)]
            if any(abs(float(q_vol) - cv) < 5.0 for cv in c_vols):
                base_score += 0.30
                ctx.reasons.append(f"机组风量精确匹配({int(q_vol)})")
            elif c_vols and not any(abs(float(q_vol) - cv) / max(float(q_vol), 1e-5) < 0.15 for cv in c_vols):
                base_score -= 0.40
                ctx.reasons.append("机组风量偏差过大")

    # 3. Fan Coil Unit (FCU)
    is_q_fcu = "风机盘管" in q_name or q_attrs.get("卡式") or "KFP" in q_name.upper() or "FCU" in q_name.upper()
    is_c_fcu = "风机盘管" in c_name or c_code_val.startswith("3.50.43.")

    if is_q_fcu and is_c_fcu:
        base_score += 0.25
        ctx.reasons.append("风机盘管品类匹配")

        q_is_cassette = q_attrs.get("卡式") is True or any(k in f"{q_name} {q_spec_text}" for k in ["卡式", "四吹", "四面出风", "四方向"])
        c_is_cassette = "卡式" in c_full_spec or "卡式" in c_name
        c_is_horizontal = "卧式" in c_full_spec or "暗装" in c_name

        if q_is_cassette:
            if c_is_cassette:
                base_score += 0.35
                ctx.reasons.append("卡式风机盘管匹配")
            elif c_is_horizontal:
                base_score -= 0.40
                ctx.reasons.append("卡式/卧式暗装结构冲突")
        elif not q_is_cassette and "卧式" in f"{q_name} {q_spec_text}":
            if c_is_horizontal:
                base_score += 0.25
                ctx.reasons.append("卧式暗装结构匹配")
            elif c_is_cassette:
                base_score -= 0.35
                ctx.reasons.append("卧式/卡式结构冲突")

        # V015-M3：提升泵三态——"不带泵/无提升泵" 否定组合先于肯定词命中；
        # 明确否定 vs 候选带泵升级硬隔离，未说明(unknown)不加分也不惩罚
        q_pump_state = detect_pump_state(f"{q_name} {q_spec_text}", q_attrs)
        c_pump_state = detect_pump_state(f"{c_name} {c_full_spec}", c_attrs)
        if q_pump_state is True and c_pump_state is True:
            base_score += 0.20
            ctx.reasons.append("冷凝水提升泵匹配")

        q_fcu_vol = q_attrs.get("风量") or q_attrs.get("送风量")
        if q_fcu_vol:
            c_fcu_clean = re.sub(r'm3/h|m³/h|m3|m³', '', c_full_spec, flags=re.I)
            c_fcu_vols = [float(x) for x in re.findall(r'风量[^\d]*(\d+)', c_fcu_clean)]
            if not c_fcu_vols:
                c_fcu_vols = [float(x) for x in re.findall(r'(\d+)', c_fcu_clean)]
            if any(abs(float(q_fcu_vol) - v) < 5.0 for v in c_fcu_vols):
                base_score += 0.35
                ctx.reasons.append(f"风盘风量精确匹配({int(q_fcu_vol)})")
            elif c_fcu_vols and not any(abs(float(q_fcu_vol) - v) / max(float(q_fcu_vol), 1e-5) < 0.10 for v in c_fcu_vols):
                base_score -= 0.50
                ctx.reasons.append("风盘风量偏差过大")

    # 4. Multi-Split VRF Indoor Unit
    is_q_vrf = "多联机" in q_name or "多联式" in q_name or "VRV" in q_name.upper() or "VRF" in q_name.upper() or q_attrs.get("多联机")
    is_c_vrf = "多联式空调室内机" in c_name or "多联机" in c_name or c_code_val.startswith("3.50.03.")

    if is_q_vrf and is_c_vrf:
        base_score += 0.30
        ctx.reasons.append("多联机室内机品类匹配")

        if "两方向" in c_full_spec and "提升泵" in c_full_spec:
            base_score += 0.25
            ctx.reasons.append("两方向带提升泵机型匹配")

        q_cool = q_attrs.get("制冷量")
        if not q_cool:
            cm = re.search(r'(?:额定制冷量|制冷量|冷量)[:：]?\s*(\d+(?:\.\d+)?)\s*(?:kw|KW)?', f"{q_name} {q_spec_text}")
            if cm:
                q_cool = float(cm.group(1))

        if q_cool is not None:
            c_cool_matches = re.findall(r'制冷量(?:KW|kw)?[:：]?\s*(\d+(?:\.\d+)?)', c_full_spec)
            if c_cool_matches:
                c_cool_val = float(c_cool_matches[0])
                if abs(float(q_cool) - c_cool_val) < 0.05:
                    base_score += 0.40
                    ctx.reasons.append(f"多联机制冷量精确匹配({q_cool}kW)")
                else:
                    base_score -= 0.45
                    ctx.reasons.append("多联机制冷量冲突")

    # 5. Fan Interval Matching & Default Preferences
    is_q_fan = (ctx.domain == "fan" or any(k in q_name for k in ["排风机", "送风机", "静音型排风机", "离心风机", "轴流风机", "通风机"]) or ("风机" in q_name and "风机盘管" not in q_name and "FCU" not in q_name.upper()))
    is_c_fan = "离心风机" in c_name or "通风机" in candidate.get("category", "") or c_code_val.startswith("3.50.29.")
    ctx.is_q_fan = is_q_fan

    if is_q_fcu and is_c_fan:
        base_score = min(base_score, 0.15)
        ctx.max_score = min(ctx.max_score, 0.15)
        ctx.reasons.append("风机盘管/通风机品类冲突")

    if is_q_fan and is_c_fcu:
        base_score = min(base_score, 0.15)
        ctx.max_score = min(ctx.max_score, 0.15)
        ctx.reasons.append("通风机/风机盘管品类冲突")

    if is_q_fan and is_c_fan:
        base_score += 0.20
        ctx.reasons.append("通风机品类匹配")

        q_fan_vol = q_attrs.get("风量")
        ctx.q_fan_vol = q_fan_vol
        if q_fan_vol is not None:
            q_fv = float(q_fan_vol)
            c_min = c_attrs.get("风量_min")
            c_max = c_attrs.get("风量_max")
            if c_min is None or c_max is None:
                range_m = re.search(r'风量[:：]?\s*(\d+(?:\.\d+)?)\s*[-~—至到及或]\s*(\d+(?:\.\d+)?)', c_full_spec)
                if range_m:
                    c_min = float(range_m.group(1))
                    c_max = float(range_m.group(2))
            
            if c_min is not None and c_max is not None:
                if c_min <= q_fv <= c_max:
                    base_score += 0.40
                    ctx.reasons.append(f"风量落在区间内({int(c_min)}~{int(c_max)}m³/h)")
                else:
                    base_score -= 0.50
                    ctx.reasons.append("风量超出候选区间")

        q_fan_press = q_attrs.get("机外余压") or q_attrs.get("余压")
        ctx.q_fan_press = q_fan_press
        if q_fan_press is not None:
            q_fp = float(q_fan_press)
            if "≤500Pa" in c_full_spec or "≤500pa" in c_full_spec:
                if q_fp <= 500.0:
                    base_score += 0.30
                    ctx.reasons.append("余压满足≤500Pa要求")
                else:
                    base_score -= 0.50
                    ctx.reasons.append("余压超出≤500Pa")
            elif "500Pa＜机外余压≤2000Pa" in c_full_spec:
                if 500.0 < q_fp <= 2000.0:
                    base_score += 0.30
                    ctx.reasons.append("余压满足500~2000Pa要求")
                elif q_fp <= 500.0:
                    base_score -= 0.50
                    ctx.reasons.append("余压低于候选高压区间")
            elif "＞2000Pa" in c_full_spec or ">2000Pa" in c_full_spec:
                if q_fp > 2000.0:
                    base_score += 0.30
                else:
                    base_score -= 0.60
                    ctx.reasons.append("余压远低于超高压区间")

        q_has_ss = "不锈钢" in f"{q_name} {q_spec_text}"
        q_has_frp = "玻璃钢" in f"{q_name} {q_spec_text}"
        c_has_galv = "镀锌" in c_full_spec
        c_has_ss = "不锈钢" in c_full_spec
        c_has_frp = "玻璃钢" in c_full_spec

        if not q_has_ss and not q_has_frp:
            if c_has_galv:
                base_score += 0.20
                ctx.reasons.append("默认标准镀锌钢板优选")
            elif c_has_ss or c_has_frp:
                base_score -= 0.40
                ctx.reasons.append("非默认材质降权")
        elif q_has_ss:
            if c_has_ss:
                base_score += 0.30
                ctx.reasons.append("不锈钢材质匹配")
            elif c_has_galv:
                base_score -= 0.45
                ctx.reasons.append("不锈钢/镀锌材质冲突")

        # V015-M3：防爆三态——否定前缀优先，"非防爆" 不再被 "防爆" 子串吞掉；
        # 抽取器显式布尔优先于文本判定，真冲突升级 0.15 硬隔离，未说明保留 unknown
        ex_attr = q_attrs.get("防爆")
        if ex_attr is True:
            q_ex_state = True
        elif ex_attr is False:
            q_ex_state = False
        else:
            q_ex_state = detect_three_state_flag(
                f"{q_name} {q_spec_text}", ["防爆", "防爆型", "防爆风机", "EX"])
        c_ex_state = detect_three_state_flag(c_full_spec, ["防爆", "防爆型", "防爆风机"])
        q_is_ex = q_ex_state is True
        c_is_ex = c_ex_state is True
        c_is_non_ex = c_ex_state is False
        ctx.q_is_ex = q_is_ex
        ctx.c_is_ex = c_is_ex
        ctx.c_is_non_ex = c_is_non_ex

        if q_ex_state is True:
            if c_is_ex:
                base_score += 0.35
                ctx.reasons.append("防爆风机要求匹配")
            elif c_is_non_ex:
                base_score = min(base_score, 0.15)
                ctx.max_score = min(ctx.max_score, 0.15)
                ctx.reasons.append("防爆要求vs非防爆硬隔离")
        elif q_ex_state is False:
            if c_is_non_ex:
                base_score += 0.25
                ctx.reasons.append("非防爆要求匹配")
            elif c_is_ex:
                base_score = min(base_score, 0.15)
                ctx.max_score = min(ctx.max_score, 0.15)
                ctx.reasons.append("非防爆要求vs防爆硬隔离")

    # 6. Ductwork Matching
    is_q_duct = any(k in q_name for k in ["风管", "通风管", "洁净风管", "风道"]) or q_attrs.get("板材厚度")
    is_c_duct = "风管" in c_name or c_code_val.startswith("2.22.45.")

    if is_q_duct:
        if is_c_duct:
            base_score += 0.30
            ctx.reasons.append("风管品类匹配")

            if "管件" in c_name and "管件" not in q_name:
                base_score -= 0.40
                ctx.reasons.append("风管/管件形态冲突")

            q_thick = q_attrs.get("厚度") or q_attrs.get("板材厚度") or q_attrs.get("壁厚")
            if q_thick is not None:
                c_thick = c_attrs.get("厚度") or c_attrs.get("壁厚")
                if c_thick is None:
                    tm = re.search(r'厚度[:：]?\s*(\d+(?:\.\d+)?)\s*mm', c_full_spec)
                    if tm:
                        c_thick = float(tm.group(1))
                if c_thick is not None:
                    if abs(float(q_thick) - float(c_thick)) < 0.05:
                        base_score += 0.40
                        ctx.reasons.append(f"风管厚度精确匹配({q_thick}mm)")
                    else:
                        base_score -= 0.45
                        ctx.reasons.append("风管厚度冲突")
        else:
            base_score -= 0.50
            ctx.reasons.append("非风管品类冲突")

    pump_conflict = check_pump_conflict(
        f"{q_name} {q_spec_text}", f"{c_name} {c_full_spec}", q_attrs, c_attrs
    )
    if pump_conflict:
        base_score = min(base_score, 0.15)
        ctx.max_score = min(ctx.max_score, 0.15)
        ctx.reasons.append(pump_conflict)

    ctx.base_score = base_score


DYNAMIC_RULE_UNAVAILABLE_PREFIX = "动态规则不可用，无法完成排除复核"


def _dyn_candidate_text(candidate: Dict[str, Any], c_name: str = "", c_full_spec: str = "") -> str:
    name_part = c_name or str(candidate.get("name", "") or "")
    spec_part = c_full_spec or f"{candidate.get('specification', '') or ''} {candidate.get('description', '') or ''}".strip()
    raw_name = str(candidate.get("name", "") or "")
    parts = [raw_name]
    if name_part and name_part != raw_name:
        parts.append(name_part)
    parts.append(spec_part)
    parts.append(str(candidate.get("category", "") or ""))
    parts.append(str(candidate.get("code", "") or ""))
    return " ".join(parts)


def evaluate_dynamic_exclusions(row_or_query: Dict[str, Any], candidate: Dict[str, Any]) -> List[str]:
    """V015-M3 R03 / V016-M1：与 _apply_dynamic_rules 同源的动态硬排除判定；规则不可用/损坏时 fail-closed 返回阻断原因。"""
    try:
        from app.llm_rule_engine import LLMRuleManager, DynamicRuleInterpreter
        active_rules = LLMRuleManager.get_active_rules()
    except Exception as exc:
        logger.error("动态规则读取失败，按 fail-closed 阻断: %s", type(exc).__name__)
        return [f"{DYNAMIC_RULE_UNAVAILABLE_PREFIX}: {exc}"]
    if not active_rules:
        return []
    q_spec = str(row_or_query.get("spec", "") or row_or_query.get("original_spec", "") or "")
    cand_text = _dyn_candidate_text(candidate)
    rule_ctx = RuleContext.from_query(row_or_query, stage="matching")
    excl_reasons: List[str] = []
    for r in active_rules:
        try:
            dsl = r.get("parsed_dsl")
            if dsl is None:
                dsl = {}
            if not rule_ctx.matches_rule(r):
                continue
            DynamicRuleInterpreter.validate_rule_stages(dsl, rule_id=r.get("rule_id") or r.get("id", "?"))
            matching_rules = dsl.get("matching_rules")
            if not matching_rules:
                continue
            is_excl, _boost_val, dyn_reasons = DynamicRuleInterpreter.evaluate_matching_filters(
                q_spec, cand_text, matching_rules
            )
            if is_excl:
                excl_reasons.extend(dyn_reasons)
        except Exception as exc:
            logger.error("动态规则判定异常，按 fail-closed 阻断: %s", type(exc).__name__)
            excl_reasons.append(f"{DYNAMIC_RULE_UNAVAILABLE_PREFIX}: {exc}")
    return excl_reasons


def _apply_dynamic_rules(ctx: ScoringContext):
    """Evaluate LLM-generated active matching rules (fail-closed on rule load/parse errors)."""
    base_score = ctx.base_score
    try:
        from app.llm_rule_engine import LLMRuleManager, DynamicRuleInterpreter, RuleHitStatsTracker
        active_rules = LLMRuleManager.get_active_rules()
        cand_text = _dyn_candidate_text(ctx.candidate, ctx.c_name, ctx.c_full_spec)
        query_scope_info = dict(ctx.query_info)
        if ctx.q_name and not query_scope_info.get("original_name"):
            query_scope_info["original_name"] = ctx.q_name
        rule_ctx = RuleContext.from_query(query_scope_info, stage="matching")

        for r in active_rules:
            dsl = r.get("parsed_dsl")
            if dsl is None:
                dsl = {}
            if not rule_ctx.matches_rule(r):
                continue
            DynamicRuleInterpreter.validate_rule_stages(dsl, rule_id=r.get("rule_id") or r.get("id", "?"))
            matching_rules = dsl.get("matching_rules")
            if matching_rules:
                is_excl, boost_val, dyn_reasons = DynamicRuleInterpreter.evaluate_matching_filters(
                    ctx.q_spec_text, cand_text, matching_rules
                )
                if is_excl:
                    # V015-M3：动态硬排除信号真正不可选、不可自动接受。
                    # blocked 一旦成立不可逆；max_score 同步归零，后续规则/系列加分无法复活
                    ctx.blocked = True
                    ctx.max_score = 0.0
                    base_score = 0.0
                    ctx.reasons.extend(dyn_reasons)
                    if r.get("rule_id"):
                        RuleHitStatsTracker().record_hit(r["rule_id"])
                elif boost_val != 0:
                    # V015-M3：动态加分受不可逆上限收口，强制 +100 也不能突破先前置顶的 cap
                    base_score = min(ctx.max_score, max(0.0, base_score + (boost_val / 100.0)))
                    ctx.reasons.extend(dyn_reasons)
                    if r.get("rule_id"):
                        RuleHitStatsTracker().record_hit(r["rule_id"])
    except Exception as e:
        logger.error("Error evaluating dynamic matching rules (%s): %s", type(e).__name__, e)
        # V015-M3 R03：规则读取/解析失败按 fail-closed 阻断自动接受，禁止退化成无规则通过
        ctx.blocked = True
        ctx.max_score = 0.0
        base_score = 0.0
        ctx.reasons.append(f"{DYNAMIC_RULE_UNAVAILABLE_PREFIX}: {e}")

    ctx.base_score = base_score


def _finalize_candidate_score(ctx: ScoringContext) -> Tuple[float, List[str]]:
    """Apply saturation tie-breakers, bound score to [0, 100], and deduplicate reasons."""
    base_score = ctx.base_score
    c_attrs = ctx.c_attrs
    c_full_spec = ctx.c_full_spec
    
    if ctx.profile != "legacy":
        # Compress the high end monotonically when independent domain rules
        # raise the raw total beyond 100.
        excess = max(0.0, base_score - 0.90)
        value = 0.90 + 0.10 * excess / (excess + 0.10) if excess else base_score
        final_score = value * 100.0
    elif base_score >= 0.999:
        if ctx.q_norm_full and ctx.c_norm_full and ctx.q_norm_full == ctx.c_norm_full:
            final_score = 100.0
        elif ctx.model_matched_exact:
            final_score = 99.8
        else:
            spec_sim = calculate_text_similarity(ctx.q_spec_text, c_full_spec)
            name_sim = calculate_text_similarity(ctx.q_name, ctx.c_name)
            tiebreak = (spec_sim * 0.6 + name_sim * 0.4) * 1.5
            
            if "参数超限偏离" in ctx.reasons:
                tiebreak -= 0.5
            
            if "提升泵" in c_full_spec:
                tiebreak += 0.3
            if "两方向" in c_full_spec:
                tiebreak += 0.2
            if "变频电机" in c_full_spec:
                tiebreak += 0.4
            if ctx.is_q_fan:
                if ctx.q_is_ex and ctx.c_is_ex:
                    tiebreak += 0.6
                elif not ctx.q_is_ex and ctx.c_is_non_ex:
                    tiebreak += 0.6
                if ctx.q_fan_press is not None and float(ctx.q_fan_press) <= 500 and ("≤500Pa" in c_full_spec or "≤500pa" in c_full_spec):
                    tiebreak += 0.4
                if ctx.q_fan_vol is not None and c_attrs.get("风量") is not None:
                    try:
                        c_fv = float(c_attrs["风量"])
                        vol_diff = abs(float(ctx.q_fan_vol) - c_fv) / max(float(ctx.q_fan_vol), c_fv, 1e-5)
                        tiebreak -= vol_diff * 2.0
                    except Exception:
                        pass
                if ctx.q_fan_press is not None and (c_attrs.get("机外余压") is not None or c_attrs.get("余压") is not None):
                    try:
                        c_fp = float(c_attrs.get("机外余压") or c_attrs.get("余压"))
                        press_diff = abs(float(ctx.q_fan_press) - c_fp) / max(float(ctx.q_fan_press), c_fp, 1e-5)
                        tiebreak -= press_diff * 2.0
                    except Exception:
                        pass

            final_score = 98.0 + tiebreak
    else:
        final_score = max(0.0, base_score) * 100.0

    # V015-M3：饱和微调/普通换算两条路径统一受不可逆上限收口；
    # blocked 一旦成立（初始硬冲突或动态排除）一律输出 0 分，任何后置处理不可复活
    cap_hundred = max(0.0, ctx.max_score) * 100.0
    final_score = min(100.0, max(0.0, final_score))
    final_score = min(cap_hundred, final_score)
    if ctx.blocked:
        final_score = 0.0

    unique_reasons = []
    for r in ctx.reasons:
        if r not in unique_reasons:
            unique_reasons.append(r)
            
    rounded_score = round(final_score, 1)
    if ctx.profile != "legacy":
        rounded_score = min(rounded_score, cap_hundred)
    return rounded_score, unique_reasons


def _score_candidate_context(query_info, candidate, semantic_score, profile) -> ScoringContext:
    ctx = ScoringContext(query_info=query_info, candidate=candidate, semantic_score=semantic_score, profile=profile)
    if _check_initial_hard_conflicts(ctx):
        if profile != "legacy":
            ctx.gate_evidence.append({"stage": "initial", "reasons": list(ctx.reasons), "max_score": 0.0})
        return ctx
    _extract_candidate_specs(ctx)
    _score_model_similarity(ctx)
    _score_text_and_category(ctx)
    _score_attributes(ctx)
    _calculate_base_score_and_positive_boosts(ctx)
    for stage, gate in (
        ("electrical", _apply_conduit_cable_and_electrical_gates),
        ("domain_schema", _apply_config_hard_gates),
        ("cable_pipe_dimensions", _apply_domain_cable_and_pipe_penalties),
        ("hvac", _apply_hvac_gates),
        ("dynamic_matching", _apply_dynamic_rules),
    ):
        start = len(ctx.reasons)
        previous_score, previous_cap = ctx.base_score, ctx.max_score
        gate(ctx)
        if profile != "legacy" and (
            len(ctx.reasons) != start or ctx.base_score != previous_score or ctx.max_score != previous_cap
        ):
            ctx.gate_evidence.append({
                "stage": stage, "reasons": ctx.reasons[start:],
                "score_before": previous_score, "score_after": ctx.base_score,
                "max_score_before": previous_cap, "max_score": ctx.max_score,
            })
    return ctx


def _ranking_evidence(ctx: ScoringContext) -> Dict[str, Any]:
    from app.typed_fields import build_typed_attrs

    def typed_fields(attrs, parsed, origin, fallback_origin):
        original = parsed.get("typed_attrs") or {}
        recorded = parsed.get("attrs") or {}
        result = build_typed_attrs(attrs)
        for key, field_info in result.items():
            stored = original.get(key) if isinstance(original, dict) else None
            keys = (key, f"{key}_min", f"{key}_max", f"{key}_op", f"{key}_base")
            unchanged = isinstance(recorded, dict) and key in recorded and all(
                recorded.get(k) == attrs.get(k) for k in keys
            )
            if unchanged and isinstance(stored, dict) and stored.get("value") == attrs.get(key):
                field_info.update(stored)
            field_info["origin"] = origin if unchanged else fallback_origin
        return result

    return {
        "profile": ctx.profile, "domain": ctx.domain, "scene": ctx.scene,
        "components": ctx.score_components, "attribute_scores": ctx.attr_scores,
        "attribute_groups": ctx.ranking_fields,
        "query_fields": typed_fields(ctx.q_attrs, ctx.q_parsed, "query_parse", "query_attrs"),
        "candidate_fields": typed_fields(ctx.c_attrs, ctx.c_parsed, "candidate_parse", "catalog_text_fallback"),
        "metadata": {
            "query": {"professional": ctx.q_prof, "brand": ctx.q_brand, "unit": ctx.q_unit},
            "candidate": {"professional": ctx.c_prof, "brand": ctx.c_brand, "unit": ctx.c_unit},
        },
        "reasons": list(dict.fromkeys(ctx.reasons)), "gates": ctx.gate_evidence,
        "hard_blocked": ctx.blocked, "max_score": ctx.max_score,
    }


def score_candidate_with_detail(
    query_info: Dict[str, Any], candidate: Dict[str, Any], semantic_score: float = 0.0
) -> Tuple[float, List[str], Dict[str, Any]]:
    """Score one candidate, including its irreversible cap and optional ranking evidence."""
    ctx = _score_candidate_context(query_info, candidate, semantic_score, _scoring_profile())
    final_score, reasons = _finalize_candidate_score(ctx)
    detail = {"hard_blocked": ctx.blocked, "max_score": ctx.max_score}
    if ctx.profile != "legacy":
        detail["score_evidence"] = _ranking_evidence(ctx)
    return final_score, reasons, detail


def score_candidate(query_info: Dict[str, Any], candidate: Dict[str, Any], semantic_score: float = 0.0) -> Tuple[float, List[str]]:
    """
    Score a candidate against the query info using modular pipeline (M4 refactoring).
    Returns: (float_score, list_of_match_reasons)
    V015-M3：保留明细接口的二元组包装。
    """
    final_score, reasons, _detail = score_candidate_with_detail(query_info, candidate, semantic_score)
    return final_score, reasons

@catalog_read
def hybrid_recall(query_info: Dict[str, Any], query_vector: Optional[Any] = None) -> List[Dict[str, Any]]:
    """
    Hybrid recall module: combines FTS5, LIKE model search, and Vector search.
    Returns: List of unique candidate material records with similarity scores.
    """
    candidates: Dict[str, Dict[str, Any]] = {}

    def record_source(code, source, reason=None, rank=None):
        channels = candidates[code].setdefault("recall_sources", [])
        if source not in channels:
            channels.append(source)
        if reason:
            reasons_list = candidates[code].setdefault("recall_reasons", [])
            if reason not in reasons_list:
                reasons_list.append(reason)
        if rank is not None:
            ranks_map = candidates[code].setdefault("channel_ranks", {})
            if source not in ranks_map or rank < ranks_map[source]:
                ranks_map[source] = rank
    
    q_name = query_info.get("name", "")
    q_spec_text = query_info.get("specification", "")
    q_category = query_info.get("category", "")
    
    # Parse specs to get model
    # V015-M1：统一契约下各入口均带 parsed_spec；仅外部直调 hybrid_recall 的
    # 旧路径走 fallback，且统一到 extract_query_params（专业语义），不再使用
    # 另一套 preprocessor.extract_params 管线，消除解析器分裂。
    q_parsed = query_info.get("parsed_spec") or extract_query_params(
        q_name,
        q_spec_text,
        query_info.get("professional") or "",
        material_category=query_info.get("material_category") or query_info.get("original_category") or q_category or "",
    )
    q_model = q_parsed.get("model", "")

    # V018-M0：标准编码直达、产品型号/位号分离与多字段结构化召回（受配置开关控制，默认关闭以保留旧基线）
    from app.structured_recall import (
        classify_query_identifiers,
        get_recall_options,
        recall_by_standard_code,
        recall_by_structured_fields,
    )
    v018_opts = get_recall_options(query_info)
    v018_struct_candidates: List[Dict[str, Any]] = []
    if v018_opts["v018_m0_enabled"]:
        v018_ident = classify_query_identifiers(query_info, q_parsed)
        if v018_opts["exact_code"]:
            exact_hits, _ = recall_by_standard_code(query_info, q_parsed, v018_ident)
            for cand in exact_hits:
                code = cand["code"]
                if code not in candidates:
                    candidates[code] = cand
                for r_reason in cand.get("recall_reasons", []):
                    record_source(code, "exact_code", r_reason)
                record_source(code, "exact_code")
        if v018_opts["structured_model"] or v018_opts["structured_fields"]:
            v018_struct_candidates, _ = recall_by_structured_fields(
                query_info,
                q_parsed,
                v018_ident,
                limit=int(v018_opts["structured_limit"]),
                include_model=bool(v018_opts["structured_model"]),
                include_fields=bool(v018_opts["structured_fields"]),
            )
            # High-precision structured_model hits enter before FTS
            for cand in v018_struct_candidates:
                if cand.get("recall_source") == "structured_model":
                    code = cand["code"]
                    if code not in candidates:
                        candidates[code] = cand
                    for r_reason in cand.get("recall_reasons", []):
                        record_source(code, "structured_model", r_reason)
                    record_source(code, "structured_model")
    
    # V018-M1：规范名称、限定域别名与多级稀疏 FTS 召回（独立默认关闭开关，不改写冻结 config）
    from app.sparse_recall import get_sparse_recall_options, recall_sparse_candidates
    v018_m1_opts = get_sparse_recall_options(query_info)
    v018_m1_candidates: List[Dict[str, Any]] = []
    if v018_m1_opts["v018_m1_enabled"]:
        v018_m1_candidates, _ = recall_sparse_candidates(
            query_info,
            q_parsed,
            limit=int(v018_m1_opts["sparse_limit"]),
            existing_codes=set(candidates.keys()),
        )

    def _merge_candidate_list(cands_list: List[Dict[str, Any]], skip_source: Optional[str] = None):
        for cand in cands_list:
            src = cand.get("recall_source", "")
            if skip_source and src == skip_source:
                continue
            code = cand["code"]
            if code not in candidates:
                candidates[code] = cand
            for ch in (cand.get("recall_sources") or [src]):
                ch_rank = (cand.get("channel_ranks") or {}).get(ch)
                record_source(code, ch, rank=ch_rank)
            for r_reason in cand.get("recall_reasons", []):
                record_source(code, src, r_reason)

    # 1. Path A1: Model search via FTS5 (avoiding disk table scan)
    if q_model and len(q_model) >= 2:
        model_results = search_materials_keyword(q_model, limit=100)
        for idx_m, cand in enumerate(model_results, 1):
            if cand["code"] not in candidates:
                cand["recall_source"] = "model_match"
                cand["semantic_score"] = 0.0
                candidates[cand["code"]] = cand
            record_source(cand["code"], "model_match", rank=idx_m)

    # 2. Path A2: SQLite FTS5 search (Top 1000)
    from app.normalizer import clean_text_standard
    clean_keyword = clean_text_standard(
        q_name,
        rule_context=RuleContext.from_query(query_info, stage="cleaning"),
    )
    if not clean_keyword:
        clean_keyword = q_name
    clean_keyword = re.sub(r'[-_][a-zA-Z0-9]+', ' ', clean_keyword).strip()
    
    # Apply HVAC abbreviation expansion dynamically
    from app.query_preprocessor import HVAC_UNIT_MAP
    for cn_name, keywords in HVAC_UNIT_MAP:
        for kw in keywords:
            pattern = r'(?<![a-zA-Z])' + re.escape(kw) + r'(?![a-zA-Z])'
            if re.search(pattern, clean_keyword, re.IGNORECASE):
                clean_keyword = re.sub(pattern, cn_name, clean_keyword, flags=re.IGNORECASE)
                break
    clean_keyword = re.sub(r'\(.*?\)|（.*?）|室外型|室内型', ' ', clean_keyword).strip()
    
    has_interleave = bool(v018_struct_candidates or v018_m1_candidates)
    if clean_keyword:
        fts_results = search_materials_keyword(clean_keyword, limit=150)
        # V018-M0/M1：先写入 FTS 前段（40/50 条），再依次注入 M1 稀疏高精候选与 M0 结构化候选，
        # 最后补齐 FTS 其余条目，使各渠道均能进入排序前 Recall@100 / Recall@200。
        fts_head_cut = 40 if v018_m1_candidates else 50
        first_fts = fts_results[:fts_head_cut] if has_interleave else fts_results
        rest_fts = fts_results[fts_head_cut:] if has_interleave else []
        for idx_f, r in enumerate(first_fts, 1):
            if r["code"] not in candidates:
                r["recall_source"] = "fts"
                r["semantic_score"] = 0.0
                candidates[r["code"]] = r
            record_source(r["code"], "fts", rank=idx_f)
        if v018_m1_candidates:
            _merge_candidate_list(v018_m1_candidates)
        if v018_struct_candidates:
            _merge_candidate_list(v018_struct_candidates, skip_source="structured_model")
        for idx_f, r in enumerate(rest_fts, fts_head_cut + 1):
            if r["code"] not in candidates:
                r["recall_source"] = "fts"
                r["semantic_score"] = 0.0
                candidates[r["code"]] = r
            record_source(r["code"], "fts", rank=idx_f)
    elif has_interleave:
        if v018_m1_candidates:
            _merge_candidate_list(v018_m1_candidates)
        if v018_struct_candidates:
            _merge_candidate_list(v018_struct_candidates, skip_source="structured_model")

    # Also search by extracted clean name from dynamic rules / query attributes (e.g. 截止阀)
    q_attrs = q_parsed.get("attrs", {}) if isinstance(q_parsed, dict) else {}
    extracted_name = q_attrs.get("名称") or q_attrs.get("name")
    if extracted_name and len(str(extracted_name)) >= 2 and str(extracted_name) != clean_keyword:
        fts_ext_results = search_materials_keyword(str(extracted_name), limit=150)
        for idx_e, r in enumerate(fts_ext_results, 1):
            if r["code"] not in candidates:
                r["recall_source"] = "fts_extracted_name"
                r["semantic_score"] = 0.0
                candidates[r["code"]] = r
            record_source(r["code"], "fts_extracted_name", rank=idx_e)

    # Also search by Chinese term without leading English prefix (e.g. AP-1 动力配电箱 -> 动力配电箱)
    zh_term = re.sub(r'^[a-zA-Z0-9\-_]+', '', q_name).strip()
    if zh_term and len(zh_term) >= 2 and zh_term != clean_keyword:
        fts_zh_results = search_materials_keyword(zh_term, limit=100)
        for idx_z, r in enumerate(fts_zh_results, 1):
            if r["code"] not in candidates:
                r["recall_source"] = "fts_zh"
                r["semantic_score"] = 0.0
                candidates[r["code"]] = r
            record_source(r["code"], "fts_zh", rank=idx_z)

    # HVAC specialized recall branches
    hvac_recall_terms = []
    
    # 1. AHU / PAU
    if q_attrs.get("自取新风") or ("AHU" in q_name.upper() and ("自取" in q_spec_text or "4200" in q_spec_text)):
        hvac_recall_terms.append("净化循环机组")
        hvac_recall_terms.append("净化循环机组 自取新风")
    if "PAU" in q_name.upper() or "新风机组" in q_name:
        if q_attrs.get("吊装"):
            hvac_recall_terms.append("吊顶新风机组")
        elif q_attrs.get("落地"):
            hvac_recall_terms.append("净化新风机组")
        else:
            hvac_recall_terms.extend(["吊顶新风机组", "净化新风机组"])

    # 2. FCU
    if q_attrs.get("卡式") or "风机盘管" in q_name or "KFP" in q_name.upper() or "FCU" in q_name.upper():
        hvac_recall_terms.append("风机盘管")
        if q_attrs.get("卡式"):
            hvac_recall_terms.append("卡式 风机盘管")

    # 3. VRF
    if q_attrs.get("多联机") or "多联" in q_name:
        hvac_recall_terms.append("多联式空调室内机")

    # 4. Fans
    if any(k in q_name for k in ["排风机", "送风机", "风机", "静音型排风机", "离心风机"]):
        hvac_recall_terms.append("医用离心式排风机(箱）")
        hvac_recall_terms.append("医用离心式排风机")
        hvac_recall_terms.append("离心风机（箱）")
        hvac_recall_terms.append("离心风机")
        if q_attrs.get("风量"):
            vol_int = int(q_attrs["风量"])
            hvac_recall_terms.append(f"医用离心式排风机 {vol_int}")
            hvac_recall_terms.append(f"离心风机 {vol_int}")
        if q_attrs.get("防爆"):
            hvac_recall_terms.append("离心风机 镀锌 防爆")
            hvac_recall_terms.append("离心风机 防爆")
        else:
            hvac_recall_terms.append("离心风机 镀锌 非防爆")
            hvac_recall_terms.append("离心风机 非防爆")
            hvac_recall_terms.append("离心风机 500Pa 镀锌")

    # 5. Ductwork
    if any(k in q_name for k in ["风管", "通风管", "洁净风管", "风道"]):
        hvac_recall_terms.append("镀锌钢板风管")
        hvac_recall_terms.append("风管")

    # 6. Fire damper
    if any(k in q_name for k in ["防火阀", "常开防火阀", "排烟防火阀"]):
        hvac_recall_terms.append("防火阀")
        d_val = q_attrs.get("尺寸_2d") or q_attrs.get("尺寸")
        if not d_val:
            dm = re.search(r'(\d+)\s*[*x×]\s*(\d+)', f"{q_name} {q_spec_text}")
            if dm:
                d_val = f"{dm.group(1)}*{dm.group(2)}"
        if d_val:
            d_clean = str(d_val).replace('*', ' ').replace('×', ' ').replace('x', ' ')
            hvac_recall_terms.append(f"防火阀 {d_clean}")
            hvac_recall_terms.append(f"{d_clean} 70")
            hvac_recall_terms.append(f"{d_clean} 280")
            hvac_recall_terms.append(f"{d_clean} 镀锌")
            hvac_recall_terms.append(d_clean)

    # 7. Flow & Parameter combination recall
    vol_match = re.search(r'(?:风量|送风量|新风量)[m³/\s：:]*(\d+)', f"{q_name} {q_spec_text}")
    if vol_match:
        v_num = vol_match.group(1)
        if "AHU" in q_name.upper() or "循环" in q_name or q_attrs.get("自取新风"):
            hvac_recall_terms.append(f"净化循环机组 {v_num}")
            hvac_recall_terms.append(f"自取新风 {v_num}")
        if "PAU" in q_name.upper() or "新风" in q_name:
            hvac_recall_terms.append(f"吊顶新风机组 {v_num}")
            hvac_recall_terms.append(f"净化新风机组 {v_num}")
        if "风机盘管" in q_name or "FCU" in q_name.upper():
            hvac_recall_terms.append(f"风机盘管 {v_num}")
        hvac_recall_terms.append(v_num)

    cool_m = re.search(r'(?:制冷量|额定制冷量)[:：]?\s*(\d+(?:\.\d+)?)', f"{q_name} {q_spec_text}")
    if cool_m and ("多联" in q_name or q_attrs.get("多联机")):
        hvac_recall_terms.append(f"多联式空调室内机 {cool_m.group(1)}")
        hvac_recall_terms.append(f"制冷量KW {cool_m.group(1)}")

    th_m = re.search(r'(?:厚度|板材厚度|壁厚)[:：]?\s*(\d+(?:\.\d+)?)\s*mm', f"{q_name} {q_spec_text}")
    if th_m and ("风管" in q_name or "通风管" in q_name):
        hvac_recall_terms.append(f"镀锌钢板风管 {th_m.group(1)}")
        hvac_recall_terms.append(f"厚度 {th_m.group(1)}mm")

    for term in hvac_recall_terms:
        if term and len(term) >= 2:
            hvac_res = search_materials_keyword(term, limit=150)
            for idx_h, r in enumerate(hvac_res, 1):
                if r["code"] not in candidates:
                    r["recall_source"] = f"fts_hvac_{term}"
                    r["semantic_score"] = 0.0
                    candidates[r["code"]] = r
                record_source(r["code"], f"fts_hvac_{term}", rank=idx_h)

    # 3. Path B: Vector search (Legacy Top 50 by default; V018-M2 supports canonical text, TopK 50/100/200 & RRF fusion)
    from app.vector_fusion import (
        VECTOR_TEXT_SCHEMA_LEGACY,
        build_query_vector_text,
        fuse_recall_candidates,
        get_vector_fusion_options,
    )
    from app.vector_index import index_manager

    v018_m2_opts = get_vector_fusion_options(query_info)
    vec_text_mode = v018_m2_opts["vector_text_mode"]
    vec_schema_ver = v018_m2_opts["text_schema_version"]
    vec_top_k = int(v018_m2_opts["vector_top_k"])

    revision = get_db_revision() if index_manager.is_enabled else None
    vector_ready = False
    if revision is not None:
        if vec_schema_ver == VECTOR_TEXT_SCHEMA_LEGACY:
            if not index_manager.has_current_index(revision):
                index_manager.load_cache()
            vector_ready = index_manager.has_current_index(revision)
        else:
            cand_snap = index_manager.load_or_build_candidate_index(text_mode=vec_text_mode)
            vector_ready = cand_snap is not None and cand_snap.embeddings is not None

    semantic_hits_added = False
    if vector_ready:
        q_vec = query_vector
        if vec_text_mode == "canonical":
            if query_info.get("vector_text_schema") != vec_schema_ver or q_vec is None:
                enc_q_text, _ = build_query_vector_text(query_info, q_parsed, mode="canonical")
                q_vec = index_manager.encode_single_query(enc_q_text)
        elif q_vec is None:
            # Clean text for vector query (legacy path)
            clean_q_text = f"{q_name} {q_spec_text}".strip()
            q_vec = index_manager.encode_single_query(clean_q_text)

        if q_vec is not None:
            if vec_schema_ver == VECTOR_TEXT_SCHEMA_LEGACY or not hasattr(index_manager, "search_candidate"):
                vector_results = index_manager.search(q_vec, top_k=vec_top_k)
            else:
                vector_results = index_manager.search_candidate(
                    q_vec, top_k=vec_top_k, text_schema_version=vec_schema_ver
                )
            if vector_results:
                semantic_hits_added = True
            for idx_v, (m, score) in enumerate(vector_results, 1):
                code = m["code"]
                if code not in candidates:
                    cand = dict(m)
                    cand["recall_source"] = "semantic"
                    cand["semantic_score"] = score
                    cand.setdefault("unit", "台")
                    cand.setdefault("brand", "")
                    cand.setdefault("description", "")
                    candidates[code] = cand
                else:
                    candidates[code]["semantic_score"] = score
                sem_reason = (
                    f"semantic({vec_schema_ver},k={vec_top_k},rank={idx_v},sim={score:.4f})"
                    if v018_m2_opts["v018_m2_enabled"]
                    else None
                )
                record_source(code, "semantic", reason=sem_reason, rank=idx_v)

    # Fallback search if candidates is empty (V018-M1 replaces naive q_name[:4] via sparse_param_fallback)
    if not candidates and not v018_m1_opts["v018_m1_enabled"]:
        fallback_term = q_name[:4] if len(q_name) > 4 else q_name
        if fallback_term:
            fallback_res = search_materials_keyword(fallback_term, limit=100)
            for idx_fb, r in enumerate(fallback_res, 1):
                r["recall_source"] = "fallback"
                r["semantic_score"] = 0.0
                candidates[r["code"]] = r
                record_source(r["code"], "fallback", rank=idx_fb)

    pool_list = list(candidates.values())
    allow_kw_rrf = (
        bool((query_info.get("recall_options") or {}).get("allow_keyword_rrf", False))
        if isinstance(query_info, dict)
        else False
    )
    # V018-M3 (OBS-COORD-M2-02): Do not apply M2 RRF/cap when semantic recall is unavailable
    # or yielded zero hits; cleanly degrade back to the verified keyword/FTS path.
    if (
        v018_m2_opts["v018_m2_enabled"]
        and (semantic_hits_added or allow_kw_rrf)
        and (v018_m2_opts["fusion_mode"] == "rrf" or int(v018_m2_opts["candidate_cap"]) > 0)
    ):
        pool_list, _ = fuse_recall_candidates(
            pool_list,
            fusion_mode=v018_m2_opts["fusion_mode"],
            rrf_k=int(v018_m2_opts["rrf_k"]),
            candidate_cap=int(v018_m2_opts["candidate_cap"]),
        )
    return pool_list

@catalog_read
def match_item(query_info: Dict[str, Any], query_vector: Optional[Any] = None, batch_confirmed_series: Optional[List[str]] = None, *, diagnostic_targets: Optional[List[str]] = None, calibration_groups=None, initial_match_groups=None) -> Dict[str, Any]:
    """
    Main entry point for matching a single query item.
    """
    if batch_confirmed_series is None:
        batch_confirmed_series = []
    q_name = query_info.get("name", "")
    q_spec_text = query_info.get("specification", "")
    q_category = query_info.get("category", "")
    
    # Pre-parse specs to inject into query info
    # V015-M1：第三参统一为专业语义（prepare_query 契约），professional 优先
    if "parsed_spec" not in query_info:
        computed_spec = extract_query_params(
            q_name,
            q_spec_text,
            query_info.get("professional") or "",
            material_category=query_info.get("material_category") or query_info.get("original_category") or q_category or "",
        )
        if (computed_spec.get("rule_diagnostics") or {}).get("degraded"):
            query_info = dict(query_info)
        query_info["parsed_spec"] = computed_spec
        
    q_model = query_info["parsed_spec"].get("model", "")
    
    # 1. Recall
    candidates = hybrid_recall(query_info, query_vector)
    
    # Enrich candidates with DB data (parsed_params, unit, brand, description) in a single batch query
    if candidates:
        codes = [c["code"] for c in candidates if c.get("code")]
        if codes:
            from app.db_manager import get_thread_db_connection
            conn = get_thread_db_connection()
            cursor = conn.cursor()
            placeholders = ",".join(["?"] * len(codes))
            cursor.execute(f"""
                SELECT code, unit, brand, description, parsed_params 
                FROM materials 
                WHERE code IN ({placeholders})
            """, codes)
            db_data = {row["code"]: dict(row) for row in cursor.fetchall()}
            
            for cand in candidates:
                code = cand["code"]
                if code in db_data:
                    cand.update(db_data[code])
    
    # 2. Score
    profile = _scoring_profile()
    # Preserve the full ranking and tie-break path for direct standard codes.
    direct_code = profile != "legacy" and any(
        "exact_code" in (c.get("recall_sources") or ()) for c in candidates
    )
    score_contexts = {}
    cross_encoder_enabled = os.getenv("LISTTRANS_V021_M3_RERANKER", "off") == "on"
    capped_codes = (
        set() if cross_encoder_enabled or calibration_groups is not None or initial_match_groups is not None else None
    )
    rerank_profile = os.getenv("LISTTRANS_V019_M1_RERANK", "off")
    rerank_enabled = rerank_profile not in ("", "0", "off")
    feedback_model = None
    if (os.getenv("LISTTRANS_V020_M0_RANKER") == "on" and profile == "legacy"
            and not rerank_enabled and calibration_groups is None):
        from app.feedback_ranker import active_ranker

        feedback_model = active_ranker()
    feedback_inputs = {}
    scored_candidates = []
    for cand in candidates:
        if profile == "legacy":
            score, reasons, hard_detail = score_candidate_with_detail(query_info, cand, cand["semantic_score"])
            hard_blocked = bool(hard_detail.get("hard_blocked"))
            max_score_base = max(0.0, float(hard_detail.get("max_score", 1.0)))
        else:
            ctx = _score_candidate_context(query_info, cand, cand["semantic_score"], "legacy" if direct_code else profile)
            score, reasons = _finalize_candidate_score(ctx)
            hard_blocked = ctx.blocked
            max_score_base = max(0.0, ctx.max_score)
            if not direct_code:
                score_contexts[cand["code"]] = ctx
        cap_hundred = max_score_base * 100.0
        if capped_codes is not None and max_score_base != 1.0:
            capped_codes.add(cand["code"])
        series_adjustment = 0.0

        # Apply series clustering boost if candidate belongs to a confirmed series
        # V015-M3：blocked 候选不享受系列加分；系列 +5 受候选不可逆上限收口，无法突破前置硬约束
        if not hard_blocked and check_series_match(q_model, cand.get("specification", "")):
            # If batch_confirmed_series list contains this candidate's series prefix
            c_parsed = None
            if "parsed_params" in cand and cand["parsed_params"]:
                try:
                    c_parsed = json.loads(cand["parsed_params"])
                except Exception:
                    pass
            c_model = c_parsed.get("model", "") if c_parsed else extract_params(cand.get("specification", "")).get("model", "")
            if c_model:
                c_prefix = c_model.split("-")[0].strip().lower()
                if c_prefix in batch_confirmed_series:
                    adjusted = min(cap_hundred, score + 5.0)
                    series_adjustment = adjusted - score
                    score = adjusted
                    if "同批确认系列优先" not in reasons:
                        reasons.append("系列优先")
                        
        # Extract wall thickness for conduit tie-breaker
        c_parsed = None
        if "parsed_params" in cand and cand["parsed_params"]:
            try:
                c_parsed = json.loads(cand["parsed_params"]) if isinstance(cand["parsed_params"], str) else cand["parsed_params"]
            except Exception:
                pass
        
        c_attrs = c_parsed.get("attrs", {}) if c_parsed else {}
        c_thickness = c_attrs.get("壁厚") or c_attrs.get("厚度")
        # V015-M3：DN 与外径分字段保存，严禁从 DN 直接写外径（字段约定同 check_dn_od_conflicts）
        c_dn = c_attrs.get("管径") or c_attrs.get("dn_val")
        c_od = c_attrs.get("外径")

        scored_candidates.append({
            "code": cand["code"],
            "name": cand["name"],
            "specification": cand["specification"],
            "category": cand["category"],
            "unit": cand["unit"],
            "brand": cand["brand"],
            "description": cand["description"],
            # V015-M3：透传 parsed_params 供确认/导出链路同源复核真实品牌/DN/外径/材质（只增字段）
            "parsed_params": cand.get("parsed_params", ""),
            "score": score,
            "match_reasons": reasons,
            "c_thickness": c_thickness,
            "c_od": c_od,
            # V015-M3：只增字段不改既有契约；DN 独立字段 + 硬约束明细供确认/导出链路同源复核
            "c_dn": c_dn,
            "hard_blocked": hard_blocked,
            "max_score": max_score_base if profile != "legacy" and not direct_code else round(max_score_base, 4),
            "recall_source": cand.get("recall_source", ""),
            "recall_sources": list(cand.get("recall_sources") or [cand.get("recall_source", "")]),
            "recall_reasons": list(cand.get("recall_reasons") or []),
            "channel_ranks": dict(cand.get("channel_ranks") or {}),
        })
        if cand["code"] in score_contexts and series_adjustment:
            score_contexts[cand["code"]].score_components["batch_series_adjustment"] = series_adjustment / 100.0
        if rerank_enabled:
            scored_candidates[-1]["_rerank_inputs"] = (
                max_score_base, cand["semantic_score"], c_parsed or {},
            )
        if feedback_model is not None:
            feedback_inputs[cand["code"]] = (max_score_base, c_parsed or {})
        
    # Check if query has specified wall thickness
    q_parsed = query_info.get("parsed_spec", {}) if query_info else {}
    q_attrs = query_info.get("attrs") or q_parsed.get("attrs", {}) if isinstance(q_parsed, dict) else {}
    q_thickness = q_attrs.get("壁厚") or q_attrs.get("厚度")
    # V015-M3：查询侧 DN/外径分字段；tie-breaker 只做同字段比较，禁止跨字段混用
    q_dn_val = q_attrs.get("管径") or q_attrs.get("dn_val")
    q_od_val = q_attrs.get("外径")

    def get_od_closeness(cand_item):
        if q_od_val is None:
            return 0.0
        c_od_val = cand_item.get("c_od")
        if c_od_val is None:
            return -999.0
        try:
            return -abs(float(c_od_val) - float(q_od_val))
        except Exception:
            return -999.0

    def get_dn_closeness(cand_item):
        # V015-M3：DN 口径独立贴近度，仅同字段比较（15=15.0 由 float 化天然归一）
        if q_dn_val is None:
            return 0.0
        c_dn_val = cand_item.get("c_dn")
        if c_dn_val is None:
            return -999.0
        try:
            return -abs(float(c_dn_val) - float(q_dn_val))
        except Exception:
            return -999.0

    # Helper function to get clean numeric thickness for candidate as tiebreaker sorting key
    def get_thickness_tiebreaker(cand_item):
        if q_thickness is not None:
            return 0.0
        val = cand_item.get("c_thickness")
        if val is None:
            return -999.0
        
        # Standard wall thickness preference for JDG/KBG conduits
        name_upper = str(cand_item.get("name", "")).upper()
        if "JDG" in name_upper or "KBG" in name_upper:
            try:
                thickness = float(val)
                if abs(thickness - 1.6) < 0.05:
                    return 999.0
            except (ValueError, TypeError):
                pass

        if isinstance(val, (int, float)):
            return -float(val)
        try:
            return -float(val)
        except (ValueError, TypeError):
            import re
            m = re.search(r'(\d+(?:\.\d+)?)', str(val))
            if m:
                try:
                    return -float(m.group(1))
                except Exception:
                    pass
            return -999.0

    def get_dim_closeness(cand_item):
        dim_str = q_attrs.get("尺寸") or q_attrs.get("尺寸_2d")
        if not dim_str:
            return 0.0
        q_dims = sorted([float(x) for x in re.findall(r'\d+(?:\.\d+)?', str(dim_str))], reverse=True)
        c_spec = str(cand_item.get("specification", ""))
        c_dims = sorted([float(x) for x in re.findall(r'\d+(?:\.\d+)?', c_spec)], reverse=True)
        if len(q_dims) == len(c_dims) and len(q_dims) > 0:
            diffs = sum(abs(q - c) for q, c in zip(q_dims, c_dims))
            return -diffs
        return -999.0

    # Sort by score descending, with parameter deviations penalty, exact text match, diameter closeness, thickness tie-breaker, and dimension closeness
    scored_candidates.sort(key=lambda x: (
        x["score"],
        -1.0 if "参数超限偏离" in x.get("match_reasons", []) else 0.0,
        1.0 if "名称与规格完全一致" in x.get("match_reasons", []) else (0.5 if "规格型号完全一致" in x.get("match_reasons", []) else 0.0),
        get_od_closeness(x),
        get_dn_closeness(x),
        get_thickness_tiebreaker(x),
        get_dim_closeness(x),
        calculate_text_similarity(q_spec_text, x.get("specification", ""))
    ), reverse=True)
    
    # Format and ensure strict tie-breaker differentiation on scores
    if not score_contexts and len(scored_candidates) > 1:
        for i in range(1, len(scored_candidates)):
            if scored_candidates[i]["score"] >= scored_candidates[i-1]["score"] and scored_candidates[i]["score"] >= 80.0:
                if scored_candidates[i]["specification"] != scored_candidates[i-1]["specification"] or scored_candidates[i]["name"] != scored_candidates[i-1]["name"]:
                    # V015-M3：后置同分微调同样受候选不可逆上限收口，不得借微调突破 cap
                    adj_score = round(max(0.0, scored_candidates[i-1]["score"] - 0.1), 1)
                    item_cap = max(0.0, float(scored_candidates[i].get("max_score", 1.0))) * 100.0
                    scored_candidates[i]["score"] = min(adj_score, item_cap)

    exact_threshold = 75.0
    rerank_detail = None
    if rerank_enabled:
        from app.candidate_reranker import rerank

        rerank_detail = rerank(query_info, scored_candidates, rerank_profile, exact_threshold=exact_threshold)
        for candidate in scored_candidates:
            candidate.pop("_rerank_inputs")

    if feedback_model is not None:
        from app.feedback_ranker import rerank as rerank_feedback

        moved = rerank_feedback(query_info, scored_candidates, feedback_inputs, feedback_model,
                                exact_threshold=exact_threshold)
        rerank_detail = {"profile": "v020-m0", "model_sha256": feedback_model[1], "moved_count": moved}

    if cross_encoder_enabled:
        if profile != "legacy" or rerank_enabled or feedback_model is not None or calibration_groups is not None:
            cross_encoder = {"status": "skipped", "reason": "other_ranking_or_acceptance_profile"}
        else:
            from app.cross_encoder_reranker import rerank

            cross_encoder = rerank(query_info, scored_candidates, capped_codes)

    # Threshold for 100% exact match
    is_exact = False
    matched_code = None
    
    if scored_candidates:
        top_one = scored_candidates[0]
        # Match threshold V0.0.2: exact match if score >= 75.0 without hard conflicts
        # V015-M3：blocked 候选分数已强制为 0，显式追加 hard_blocked 条件防止契约漂移
        if top_one["score"] >= exact_threshold and not top_one.get("hard_blocked"):
            is_exact = True
            matched_code = top_one["code"]
            
    rank = {}
    if capped_codes is not None:
        from app.acceptance_calibration import ranking_evidence

        uncapped = bool(scored_candidates and scored_candidates[0]["code"] not in capped_codes)
        if calibration_groups is not None:
            rank["_acceptance_rank"] = ranking_evidence(scored_candidates, calibration_groups, uncapped)
        if initial_match_groups is not None:
            rank["_initial_match_rank"] = (
                rank["_acceptance_rank"] if initial_match_groups is calibration_groups
                else ranking_evidence(scored_candidates, initial_match_groups, uncapped)
            )

    res_candidates = []
    evidence = {}
    for c in scored_candidates[:10]:
        c_copy = dict(c)
        c_copy["match_reasons"] = c_copy.get("match_reasons", [])[:2]
        if c["code"] in score_contexts:
            c_copy["score_evidence"] = evidence[c["code"]] = _ranking_evidence(score_contexts[c["code"]])
        res_candidates.append(c_copy)

    result = {
        "is_exact": is_exact,
        "matched_code": matched_code,
        "top_candidates": res_candidates, # Output Top 10 for detailed view
        **rank,
    }
    if cross_encoder_enabled:
        result["cross_encoder_rerank"] = cross_encoder
    if diagnostic_targets is not None:
        targets = list(dict.fromkeys(str(code).strip() for code in diagnostic_targets if str(code).strip()))
        from app.db_manager import get_thread_db_connection
        conn = get_thread_db_connection()
        catalog = set()
        if targets:
            catalog = {r[0] for r in conn.execute(
                "SELECT code FROM materials WHERE code IN (" + ",".join("?" for _ in targets) + ")", targets)}
        ranked = {c["code"]: (rank, c) for rank, c in enumerate(scored_candidates, 1)}
        competitor = max((c["score"] for c in scored_candidates if c["code"] not in targets), default=0.0)
        traces = []
        for code in targets:
            rank, candidate = ranked.get(code, (None, {}))
            recalled = rank is not None
            blocked = bool(candidate.get("hard_blocked"))
            state = ("not_in_catalog" if code not in catalog else "not_recalled" if not recalled
                     else "hard_blocked" if blocked else "ranked_gt10" if rank > 10 else "top10_hit")
            score = candidate.get("score")
            traces.append({"code": code, "in_catalog": code in catalog, "recalled": recalled,
                           "recall_sources": candidate.get("recall_sources", []),
                           "channel_ranks": candidate.get("channel_ranks", {}),
                           "recall_reasons": candidate.get("recall_reasons", []),
                           "recall_pool_size": len(candidates), "hard_blocked": blocked,
                           "reasons": list(candidate.get("match_reasons", [])),
                           "max_score": candidate.get("max_score"), "full_rank": rank,
                           "top10": recalled and rank <= 10, "T": score, "W": competitor,
                           "margin": score - competitor if score is not None else None, "state": state})
            if code in score_contexts:
                traces[-1]["score_evidence"] = evidence.get(code) or _ranking_evidence(score_contexts[code])
        result["diagnostics"] = {
            "recall_pool_size": len(candidates), "targets": traces,
            "query_parsed_spec": query_info.get("parsed_spec", {}),
            "candidates": [{k: c.get(k) for k in ("code", "score", "recall_sources", "channel_ranks", "recall_reasons", "hard_blocked", "max_score", "match_reasons")}
                           for c in scored_candidates],
        }
        if rerank_detail is not None:
            result["diagnostics"]["rerank"] = rerank_detail
    return result

