# -*- coding: utf-8 -*-
import json
import difflib
import re
from typing import Dict, Any, List, Tuple, Optional
from functools import lru_cache

from app.db_manager import search_materials_keyword, query_by_code, get_db_connection
from app.preprocessor import clean_text, extract_params, extract_device_name, fts_tokenize, load_config
from app.query_preprocessor import extract_query_params

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

def check_unit_compatibility(u1: str, u2: str) -> bool:
    if not u1 or not u2:
        return True
    u1_clean = u1.strip().lower()
    u2_clean = u2.strip().lower()
    if u1_clean == u2_clean:
        return True
        
    u1_cats = [cat for cat, units in UNIT_CLASSES.items() if any(u in u1_clean or u1_clean in u for u in units)]
    u2_cats = [cat for cat, units in UNIT_CLASSES.items() if any(u in u2_clean or u2_clean in u for u in units)]
    
    if not u1_cats or not u2_cats:
        return True
        
    share = set(u1_cats).intersection(u2_cats)
    return len(share) > 0

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

def score_candidate(query_info: Dict[str, Any], candidate: Dict[str, Any], semantic_score: float = 0.0) -> Tuple[float, List[str]]:
    """
    Score a candidate against the query info using V0.0.2 scoring system.
    Returns: (float_score, list_of_match_reasons)
    """
    reasons = []
    
    q_name = query_info.get("name", "")
    q_spec_text = query_info.get("specification", "")
    q_category = query_info.get("category", "")
    
    q_parsed = query_info.get("parsed_spec") or extract_query_params(q_name, q_spec_text, query_info.get("professional", q_category))
    q_attrs = q_parsed.get("attrs", {}) if isinstance(q_parsed, dict) else {}
    q_model = q_parsed.get("model", "") or q_attrs.get("型号", "") or ""
    
    c_name = candidate.get("name", "").strip()
    c_spec_text = candidate.get("specification", "")
    c_category = candidate.get("category", "")
    c_desc = candidate.get("description", "")
    c_full_spec = f"{c_spec_text} {c_desc}".strip()
    
    # Refine generic or short candidate names using specification text
    if len(c_name) <= 3 or any(kw in c_name for kw in ["柜", "线", "管", "阀", "箱", "板", "机", "按钮"]):
        c_name = extract_device_name(c_full_spec, c_name)
    
    # 1. Professional category conflict check
    q_prof = query_info.get("professional", "")
    if q_prof:
        q_prof = re.sub(r'^\d+[\.．\s]*', '', str(q_prof)).strip()
    if q_prof and q_prof != "全部" and q_prof != "其它":
        c_prof = get_professional_category(c_category, c_full_spec)
        if c_prof != "其它" and q_prof != c_prof:
            return 0.0, ["专业冲突阻断"]
            
    # 2. Brand conflict check
    q_attrs = query_info.get("attrs") or q_parsed.get("attrs", {})
    if isinstance(q_attrs, dict) and "attrs" in q_attrs and isinstance(q_attrs["attrs"], dict):
        q_attrs = {**q_attrs, **q_attrs["attrs"]}
    q_brand = query_info.get("brand") or q_attrs.get("品牌")
    c_brand = candidate.get("brand") or candidate.get("brand_tag") or (candidate.get("parsed_params") and "品牌" in candidate["parsed_params"])
    if not c_brand and "parsed_params" in candidate and candidate["parsed_params"]:
        import json
        try:
            c_p = json.loads(candidate["parsed_params"])
            c_brand = c_p.get("attrs", {}).get("品牌", "")
        except Exception:
            pass
    if q_brand and c_brand:
        q_brand_std = standardize_brand(str(q_brand))
        c_brand_std = standardize_brand(str(c_brand))
        if q_brand_std and c_brand_std and q_brand_std != c_brand_std:
            return 0.0, ["品牌冲突阻断"]
            
    # 3. Unit incompatibility check
    q_unit = query_info.get("unit") or query_info.get("original_unit")
    c_unit = candidate.get("unit")
    if q_unit and c_unit and not check_unit_compatibility(str(q_unit), str(c_unit)):
        return 0.0, ["单位不可换算阻断"]
    

    
    # Load pre-computed parsed parameters if present to avoid extremely slow dynamic extraction on every match candidate
    c_parsed = None
    if "parsed_params" in candidate and candidate["parsed_params"]:
        import json
        try:
            if isinstance(candidate["parsed_params"], str):
                c_parsed = json.loads(candidate["parsed_params"])
            elif isinstance(candidate["parsed_params"], dict):
                c_parsed = candidate["parsed_params"]
        except Exception:
            pass
    if not c_parsed:
        c_parsed = extract_params(f"{c_name} {c_full_spec}", c_category)
        
    c_attrs = c_parsed.get("attrs", {}) if isinstance(c_parsed, dict) else {}
    c_model = c_parsed.get("model", "") or c_attrs.get("型号", "") or ""
    
    # 1. Model similarity score
    model_score = 0.0
    model_matched_exact = False
    model_conflict = False
    
    # Check if q_model is a box prefix or alias (AP, AL, ALE, AT, ACP, ACP-P, AX, ATD, AFC)
    from app.domain_extractors import BOX_PREFIX_RULES
    box_matched = False
    if q_model:
        q_model_clean = re.sub(r'[-_]\d+$', '', q_model.upper().strip())
        for prefix, syns in BOX_PREFIX_RULES:
            if q_model_clean == prefix or q_model_clean in [s.upper() for s in syns]:
                if any(s in f"{c_name} {c_full_spec}" for s in syns):
                    model_score = 1.0
                    model_matched_exact = True
                    box_matched = True
                    reasons.append(f"配电箱型匹配({prefix})")
                    break

    if q_model and not box_matched:
        if c_model:
            if q_model.lower() == c_model.lower() or q_model.lower() in c_full_spec.lower() or q_model.lower() in c_name.lower():
                model_score = 1.0
                model_matched_exact = True
                reasons.append("型号完全吻合")
            elif check_series_match(q_model, c_model):
                model_score = 0.7
                reasons.append("型号系列相近")
            elif re.search(r'\d+[HhWwDd]', c_model) or re.search(r'\d+\s*[*x×X]\s*\d+', c_model):
                # Candidate model is just a dimension string (Height x Width x Depth) -> Not a model conflict
                model_score = 0.0
            else:
                model_score = 0.0
                model_conflict = True
                reasons.append("型号冲突")
        else:
            # Candidate has no model (generic catalog item)
            model_score = 0.0
                
    # 2. Text/Category similarity score
    # Calculate name similarity with synonym awareness
    weak_cable_syns = ["网线", "双绞线", "对线", "网络线"]
    is_syn = any(s in q_name for s in weak_cable_syns) and any(s in c_name for s in weak_cable_syns)
    if is_syn:
        name_sim = 0.95
    else:
        name_sim = calculate_text_similarity(q_name, c_name)
        
    cat_sim = calculate_text_similarity(q_category, c_category)
    text_score = max(name_sim, cat_sim)
    if text_score > 0.8:
        reasons.append("品类名称一致")
        
    # 3. Attribute comparison score
    from app.domain_router import route_domain
    from app.domain_extractors import domain_schema
    
    domain = route_domain(c_category, c_name, c_full_spec, q_prof or "")
    domain_config = domain_schema.get(domain, {})
    must_keys = [f["key"] for f in domain_config.get("must", [])]
    should_keys = [f["key"] for f in domain_config.get("should", [])]

    # We will accumulate individual attribute scores and their weights
    attr_scores = {}
    attr_weights = {}
    
    all_attrs_in_tolerance = True
    has_any_attr = False
    missing_penalty = 0.0

    # Filter out range/inequality suffix subfields from top-level loop keys
    processed_keys = set()
    for attr in q_attrs.keys():
        if attr.endswith("_min") or attr.endswith("_max") or attr.endswith("_op") or attr.endswith("_base"):
            continue
        processed_keys.add(attr)

    # A. Match standard flags group if defined in schema
    flags_list = domain_config.get("flags", [])
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

    # B. Match Must & Should attributes
    for attr in processed_keys:
        # Skip flags which are handled as a group above
        if flags_list and attr in flags_list:
            continue
        if is_interval_dim and attr in ["尺寸", "半周长", "长", "宽", "高", "长度"]:
            continue

        q_val = q_attrs.get(attr)
        c_val = c_attrs.get(attr)

        if q_val is None:
            continue

        has_any_attr = True

        if c_val is not None or (f"{attr}_min" in c_attrs and f"{attr}_max" in c_attrs):
            # Candidate has the value. Compare it!
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

            # 1b. Point vs Range interval scoring (single value against candidate interval)
            elif isinstance(q_val, (int, float)) and f"{attr}_min" in c_attrs and f"{attr}_max" in c_attrs:
                score = score_point_vs_interval(float(q_val), float(c_attrs[f"{attr}_min"]), float(c_attrs[f"{attr}_max"]))
                attr_scores[attr] = score
                attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                continue

            # 2. Inequality logic evaluation
            elif f"{attr}_op" in q_attrs:
                op = q_attrs[f"{attr}_op"]
                base_val = q_attrs[f"{attr}_base"]
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
                if q_str == c_str:
                    sim_score = 1.0
                elif (q_str in ["镀锌钢板", "镀锌板", "镀锌", "热镀锌"] and c_str in ["镀锌钢板", "镀锌板", "镀锌", "热镀锌"]) or \
                     (q_str in ["不锈钢", "304", "316l", "不锈钢304"] and c_str in ["不锈钢", "304", "316l", "不锈钢304"]) or \
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
                # Candidate has interval or tolerance dimensions, not missing
                pass
            elif attr in must_keys:
                missing_penalty += 0.15
                reasons.append(f"必填属性缺失处罚: {attr}")
            # Should fields follow "reward if present, do not penalize if missing" strategy

    # Calculate weighted average attributes score
    if attr_scores:
        sum_weighted_scores = sum(attr_scores[k] * attr_weights[k] for k in attr_scores)
        sum_weights = sum(attr_weights[k] for k in attr_scores)
        attr_score = sum_weighted_scores / sum_weights if sum_weights > 0 else 0.0
        
        for k, s in attr_scores.items():
            if (k in tolerance_rules or k in must_keys or k in ["尺寸", "半周长", "材质", "控制方式", "芯数", "截面积"]) and s < 0.8:
                all_attrs_in_tolerance = False
        if all_attrs_in_tolerance:
            reasons.append("核心参数在公差范围内")
    else:
        # Fallback to specification text similarity if no common numerical attributes
        attr_score = calculate_text_similarity(q_spec_text, c_full_spec)
        all_attrs_in_tolerance = False

    # Determine Scenario and weights
    if q_model and c_model:
        scene = "scene_a"
    elif has_any_attr and domain != "generic":
        scene = "scene_b"
    else:
        scene = "scene_c"
        
    weights = weight_rules.get(scene, DEFAULT_WEIGHTS[scene])
    
    # Calculate base score
    base_score = (
        weights.get("model", 0.0) * model_score +
        weights.get("attrs", 0.0) * attr_score +
        weights.get("text", 0.0) * text_score
    ) - missing_penalty
    
    # Add semantic vector contribution (weight is positive boost)
    if semantic_score > 0.0:
        vector_boost = weights.get("vector_boost", 0.20)
        base_score += semantic_score * vector_boost
        if semantic_score > 0.75:
            reasons.append("语义高度相近")
            
    # Apply Positive Boosts
    pos_rules = boost_rules.get("positive", DEFAULT_BOOSTS["positive"])
    if model_matched_exact:
        base_score += pos_rules.get("model_exact", 0.15)
        
    # Exact or synonym name match boost
    name_matched = False
    if q_name == c_name:
        name_matched = True
    else:
        weak_cable_syns = ["网线", "双绞线", "对线", "网络线"]
        if any(s in q_name for s in weak_cable_syns) and any(s in c_name for s in weak_cable_syns):
            name_matched = True
    if name_matched:
        base_score += 0.12
    if attr_scores and all_attrs_in_tolerance:
        base_score += pos_rules.get("attrs_exact", 0.10)
        # Additional confidence boost if names/categories are also reasonably similar
        if text_score >= 0.5:
            base_score += 0.08
    if cat_sim > 0.9:
        base_score += pos_rules.get("category_match", 0.08)
        if q_category and c_category and q_category.strip() == c_category.strip():
            base_score += 0.12
            reasons.append("分类精准匹配")
        elif q_category and c_category and q_category.strip() != c_category.strip():
            base_score -= 0.20
            reasons.append("物理分类不符")
    if check_series_match(q_model, c_model):
        base_score += pos_rules.get("series_match", 0.05)
        reasons.append("属同系列产品")
    if query_info.get("brand") and query_info["brand"].lower() == candidate.get("brand", "").lower():
        base_score += pos_rules.get("brand_match", 0.05)
        reasons.append("品牌一致")

    # Hard cap for candidates with out-of-tolerance core attributes
    if not all_attrs_in_tolerance and has_any_attr:
        base_score = min(base_score, 0.85)
        
    # Check exact string matches using clean_text_standard
    from app.normalizer import clean_text_standard
    q_norm_full = clean_text_standard(f"{q_name} {q_spec_text}")
    c_norm_full = clean_text_standard(f"{c_name} {c_full_spec}")
    q_norm_spec = clean_text_standard(q_spec_text)
    c_norm_spec = clean_text_standard(c_full_spec)

    if q_norm_full and c_norm_full and q_norm_full == c_norm_full:
        base_score += 0.30
        reasons.append("名称与规格完全一致")
    elif q_norm_spec and c_norm_spec and q_norm_spec == c_norm_spec:
        base_score += 0.20
        reasons.append("规格型号完全一致")
        
        
    # Apply Negative Boosts
    neg_rules = boost_rules.get("negative", DEFAULT_BOOSTS["negative"])
    if check_category_conflict(q_category, c_category):
        base_score += neg_rules.get("category_conflict", -0.30)
        reasons.append("品类冲突(偏置)")
    if "冷水" in q_spec_text and "热水" in c_full_spec or "热水" in q_spec_text and "冷水" in c_full_spec:
        base_score += neg_rules.get("medium_conflict", -0.20)
        reasons.append("温水介质冲突")

    # 1. Conduit vs Cable Tray (线管 vs 桥架) Discrimination
    q_is_conduit = any(k in f"{q_name} {q_spec_text}".lower() for k in ["线管", "导管", "穿线管", "套管", "jdg", "kbg"])
    c_is_conduit = any(k in f"{c_name} {c_full_spec}".lower() for k in ["线管", "导管", "穿线管", "套管", "jdg", "kbg"])
    c_is_tray = any(k in f"{c_name} {c_full_spec}".lower() for k in ["桥架", "托盘", "梯架"])
    
    if q_is_conduit and c_is_tray and not c_is_conduit:
        base_score -= 0.35
        reasons.append("物料类型冲突(线管/桥架)")
    elif q_is_conduit and c_is_conduit:
        base_score += 0.15
        reasons.append("线管品类匹配")

    # 2. Line Pipe (JDG/KBG/SC/PVC/PC) Model and Diameter matching
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
            reasons.append(f"线管型号匹配({q_cond_m})")
        elif c_cond_m and c_cond_m != q_cond_m and c_cond_m in CONDUIT_MODELS:
            base_score -= 0.30
            reasons.append("线管型号冲突")
            
    if q_attrs.get("dn") and c_attrs.get("dn"):
        if abs(q_attrs["dn"] - c_attrs["dn"]) < 0.1:
            base_score += 0.25
            reasons.append("管径精确匹配")
        elif abs(q_attrs["dn"] - c_attrs["dn"]) > 0.5 and q_is_conduit:
            base_score -= 0.35
            reasons.append("管径冲突拦截")

    # 3. Electrical Distribution Box & Control Cabinet Prefix matching (AP, AL, ALE, AT, ACP, ACP-P, AX, ATD, AFC)
    from app.domain_extractors import extract_switchgear_params
    q_box = q_attrs.get("box_type") or extract_switchgear_params(f"{q_name} {q_spec_text}").get("box_type")
    c_box = c_attrs.get("box_type") or extract_switchgear_params(f"{c_name} {c_full_spec}").get("box_type")
    
    if q_box:
        if c_box and q_box == c_box:
            base_score += 0.25
            reasons.append(f"配电箱型匹配({q_box})")
        elif c_box and q_box != c_box:
            base_score -= 0.30
            reasons.append("配电箱型冲突")

    # 4. Power Range Matching for Distribution Boxes (e.g. 10~50KW vs 50~100KW or Pe=71kW)
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
            reasons.append(f"功率段对齐({int(q_pmin)}~{int(q_pmax)}KW)")
        else:
            base_score = min(base_score, 0.45)
            reasons.append("功率段不匹配扣分")
    elif q_power is not None and c_pmin is not None and c_pmax is not None:
        if c_pmin <= q_power <= c_pmax:
            base_score += 0.30
            reasons.append(f"功率符合功率段({int(c_pmin)}~{int(c_pmax)}KW)")
        else:
            base_score = min(base_score, 0.45)
            reasons.append("功率超出候选功率段")

    # 5. Switch Gangs & Control Matching (单联/双联/三联/四联 & 单控/双控)
    from app.domain_extractors import extract_switch_gang_control
    q_sw = extract_switch_gang_control(f"{q_name} {q_spec_text}")
    c_sw = extract_switch_gang_control(f"{c_name} {c_full_spec}")
    if q_sw.get("gangs") and c_sw.get("gangs"):
        if q_sw["gangs"] == c_sw["gangs"]:
            base_score += 0.20
            reasons.append("开关联数一致")
        else:
            base_score -= 0.35
            reasons.append("开关联数冲突")
    if q_sw.get("control") and c_sw.get("control"):
        if q_sw["control"] == c_sw["control"]:
            base_score += 0.20
            reasons.append("开关控数一致")
        else:
            base_score -= 0.35
            reasons.append("开关控数冲突")

    # 6. Cable Conductor Material (Copper vs Aluminum / 铜芯 vs 铝芯)
    q_is_copper = any(k in f"{q_name} {q_spec_text}".upper() for k in ["YJY", "BYJ", "BVR", "BV", "YJV", "NH-YJV", "WDZ"])
    c_is_alum = any(k in f"{c_name} {c_full_spec}".upper() for k in ["YJLV", "BLV", "BLVY", "铝芯"])
    if q_is_copper and c_is_alum:
        base_score -= 0.40
        reasons.append("导体材质冲突(铜/铝)")

    # Out of 2x tolerance check
    has_large_deviation = False
    for attr in processed_keys:
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
        reasons.append("参数超限偏离")
        
    if model_conflict:
        base_score += neg_rules.get("model_conflict", -0.25)
        reasons.append("型号冲突扣分")
        
    # === Configuration-driven Hard Gates ===
    # 1. Conflict Caps
    conflict_caps = domain_config.get("hard_gates", {}).get("conflict_caps", {})
    for attr_name, cap_val in conflict_caps.items():
        q_val = q_attrs.get(attr_name)
        c_val = c_attrs.get(attr_name)
        if q_val is not None and c_val is not None:
            if has_conflict(attr_name, q_val, c_val):
                base_score = min(base_score, cap_val / 100.0)
                reasons.append(f"{attr_name}冲突拦截")

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
                reasons.append(f"{attr_name}偏差过大拦截")

    # 3. Ratio Penalties
    ratio_penalties = domain_config.get("hard_gates", {}).get("ratio_penalties", {})
    for attr_name, penalty_ratio in ratio_penalties.items():
        q_val = q_attrs.get(attr_name)
        c_val = c_attrs.get(attr_name)
        if q_val is not None and c_val is not None:
            if has_conflict(attr_name, q_val, c_val):
                base_score = base_score * penalty_ratio
                reasons.append(f"{attr_name}冲突惩罚")

    # === Domain-Specific Hard Conflict Penalties ===
    # A. Cable Parameter Conflict Penalties
    q_main_cores = q_attrs.get("主芯数")
    c_main_cores = c_attrs.get("主芯数")
    q_cores = q_attrs.get("芯数") or q_main_cores
    c_cores = c_attrs.get("芯数") or c_main_cores
    if q_main_cores is not None and c_main_cores is not None:
        try:
            if int(q_main_cores) != int(c_main_cores):
                base_score -= 0.45
                reasons.append("线缆芯数冲突")
        except (ValueError, TypeError):
            if str(q_cores) != str(c_cores):
                base_score -= 0.45
                reasons.append("线缆芯数冲突")
    elif q_cores and c_cores and str(q_cores) != str(c_cores):
        base_score -= 0.45
        reasons.append("线缆芯数冲突")

    q_main_sec = q_attrs.get("主截面")
    c_main_sec = c_attrs.get("主截面")
    q_sec = q_attrs.get("截面积") or q_main_sec
    c_sec = c_attrs.get("截面积") or c_main_sec
    if q_main_sec is not None and c_main_sec is not None:
        try:
            if abs(float(q_main_sec) - float(c_main_sec)) > 0.05:
                base_score -= 0.45
                reasons.append("线缆截面冲突")
        except (ValueError, TypeError):
            if str(q_sec) != str(c_sec):
                base_score -= 0.45
                reasons.append("线缆截面冲突")
    elif q_sec and c_sec and str(q_sec) != str(c_sec):
        base_score -= 0.45
        reasons.append("线缆截面冲突")

    q_cable_m = q_attrs.get("型号")
    c_cable_m = c_attrs.get("型号")
    if q_cable_m and c_cable_m and q_cable_m != c_cable_m:
        base_score -= 0.30
        reasons.append("线缆型号冲突")

    # B. Pipe Parameter Conflict Penalties
    q_dn = q_attrs.get("管径") or q_attrs.get("dn_val")
    c_dn = c_attrs.get("管径") or c_attrs.get("dn_val")
    if q_dn and c_dn and str(q_dn) != str(c_dn):
        base_score -= 0.45
        reasons.append("管径口径冲突")

    q_wall = q_attrs.get("壁厚")
    c_wall = c_attrs.get("壁厚")
    if q_wall and c_wall and isinstance(q_wall, (int, float)) and isinstance(c_wall, (int, float)):
        if abs(q_wall - c_wall) > 0.05:
            base_score -= 0.35
            reasons.append("管材壁厚冲突")

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
            if ("304" in q_mat_str and "316" in c_mat_str) or ("316" in q_mat_str and "304" in c_mat_str):
                base_score -= 0.40
                reasons.append("不锈钢牌号冲突(304/316)")
        elif q_mat_str != c_mat_str:
            base_score -= 0.40
            reasons.append("材质规格冲突")

    q_od = q_attrs.get("外径")
    c_od = c_attrs.get("外径")
    if q_od is not None and c_od is not None:
        try:
            if abs(float(q_od) - float(c_od)) / max(float(q_od), float(c_od), 1e-5) > 0.10:
                base_score -= 0.45
                reasons.append("管材外径冲突")
        except (ValueError, TypeError):
            pass

    # C. Decoration & Equipment Dimension Conflict Penalties
    q_dim = q_attrs.get("尺寸_2d") or q_attrs.get("尺寸")
    c_dim = c_attrs.get("尺寸_2d") or c_attrs.get("尺寸")
    if q_dim and c_dim:
        if check_dimension_conflict(q_dim, c_dim, max_deviation=0.15):
            base_score -= 0.40
            reasons.append("尺寸规格冲突")

    q_th = q_attrs.get("厚度")
    c_th = c_attrs.get("厚度")
    if q_th and c_th and isinstance(q_th, (int, float)) and isinstance(c_th, (int, float)):
        if abs(q_th - c_th) > 0.05:
            base_score -= 0.35
            reasons.append("厚度规格冲突")

    # D. HVAC & Equipment Controls
    q_is_ex = False
    c_is_ex = False
    c_is_non_ex = False
    q_fan_press = None
    is_q_fan = False

    if "两管制" in q_spec_text and "四管制" in c_full_spec or "四管制" in q_spec_text and "两管制" in c_full_spec:
        base_score -= 0.35
        reasons.append("管制模式冲突")

    if "变频电机" in q_spec_text and ("EC风机" in c_full_spec or "直流" in c_full_spec):
        base_score -= 0.25
        reasons.append("电机类型冲突")

    if "底漆" in q_spec_text and "面漆" in c_full_spec or "面漆" in q_spec_text and "底漆" in c_full_spec:
        base_score -= 0.35
        reasons.append("涂膜类型冲突")

    if "手工" in q_spec_text and "机制" in c_full_spec or "机制" in q_spec_text and "手工" in c_full_spec:
        base_score -= 0.35

    # 1. Fire Damper Controls & Hard Gates (方圆硬隔离 & 电动/熔断硬隔离)
    is_q_fire_damper = any(k in f"{q_name} {q_spec_text}" for k in ["防火阀", "排烟防火阀", "常开防火阀", "常闭防火阀", "70℃", "280℃", "70度", "280度"])
    is_c_fire_damper = any(k in f"{c_name} {c_full_spec}" for k in ["防火阀", "排烟防火阀", "电动防火阀"]) or str(candidate.get("code", "")).startswith("2.22.53.") or str(candidate.get("code", "")).startswith("2.22.54.")

    if is_q_fire_damper and is_c_fire_damper:
        # a. Shape Hard Gate: Rectangular vs Circular
        q_is_circular = q_attrs.get("is_circular") is True or "φ" in f"{q_name} {q_spec_text}" or "直径" in f"{q_name} {q_spec_text}" or "DN" in f"{q_name} {q_spec_text}"
        c_code_val = str(candidate.get("code", ""))
        c_is_circular = c_attrs.get("外径") is not None or "φ" in c_full_spec or "直径" in c_full_spec or "半周长" in c_full_spec or c_code_val == "2.22.53.7729"
        
        q_dim_2d = q_attrs.get("尺寸_2d") or q_attrs.get("尺寸")
        if not q_dim_2d:
            d_match = re.search(r'(\d+)\s*[*x×]\s*(\d+)', f"{q_name} {q_spec_text}")
            if d_match:
                q_dim_2d = f"{d_match.group(1)}*{d_match.group(2)}"

        if q_dim_2d and not q_is_circular:
            if c_is_circular:
                base_score = min(base_score, 0.15)
                reasons.append("截面形状冲突(矩形/圆形硬隔离)")
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
                        reasons.append(f"防火阀尺寸精确匹配({q_dim_2d})")
                    else:
                        base_score -= 0.45
                        reasons.append("防火阀尺寸不一致")
        elif q_is_circular and not c_is_circular:
            base_score = min(base_score, 0.15)
            reasons.append("截面形状冲突(圆形/矩形硬隔离)")

        # b. Electric vs Normal Fusible Hard Gate
        q_is_electric = q_attrs.get("电动") is True or any(k in f"{q_name} {q_spec_text}".upper() for k in ["电动", "DV24V", "DC24V", "AC220V", "执行器", "执行机构", "24V"])
        c_is_electric = "电动" in c_name or "DV24V" in c_full_spec or "DC24V" in c_full_spec or "24V" in c_full_spec or c_code_val.startswith("2.22.54.")

        if not q_is_electric and c_is_electric:
            base_score = min(base_score, 0.15)
            reasons.append("控制方式冲突(常开熔断/电动硬隔离)")
        elif q_is_electric and not c_is_electric:
            base_score = min(base_score, 0.15)
            reasons.append("控制方式冲突(电动/手动硬隔离)")
        elif not q_is_electric and not c_is_electric:
            base_score += 0.20
            reasons.append("常开防火阀类型匹配")

        # c. Fusible Temperature (70℃ vs 280℃)
        q_temp = q_attrs.get("熔断温度")
        if not q_temp:
            if "70" in f"{q_name} {q_spec_text}":
                q_temp = 70
            elif "280" in f"{q_name} {q_spec_text}":
                q_temp = 280
        if q_temp:
            if f"{q_temp}℃" in c_full_spec or f"{q_temp}度" in c_full_spec:
                base_score += 0.20
                reasons.append(f"熔断温度匹配({q_temp}℃)")
            elif (q_temp == 70 and "280" in c_full_spec) or (q_temp == 280 and "70" in c_full_spec):
                base_score -= 0.50
                reasons.append("熔断温度冲突(70℃/280℃)")

        # d. Damper Material (镀锌钢板 vs 不锈钢)
        if "镀锌" in f"{q_name} {q_spec_text}" or "镀锌" in str(q_attrs.get("材质", "")):
            if "镀锌钢板" in c_full_spec and "内衬" not in c_full_spec:
                base_score += 0.15
                reasons.append("镀锌钢板材质匹配")
            elif "不锈钢" in c_full_spec or "内衬" in c_full_spec:
                base_score -= 0.35
                reasons.append("阀门材质冲突")

    # 2. AHU / PAU Air Handling Unit Matching
    c_code_val = str(candidate.get("code", ""))
    is_q_ahu = "AHU" in q_name.upper() or "组合式" in q_name or "医用洁卫型空调机组AHU" in q_name
    is_q_pau = "PAU" in q_name.upper() or "新风机组" in q_name or "医用洁卫型空调机组PAU" in q_name

    if is_q_ahu or is_q_pau or c_code_val.startswith("3.60.21."):
        # a. Installation Type (吊顶 vs 落地)
        q_is_ceiling = q_attrs.get("吊装") is True or "吊装" in f"{q_name} {q_spec_text}" or "吊顶" in f"{q_name} {q_spec_text}"
        q_is_floor = q_attrs.get("落地") is True or "落地" in f"{q_name} {q_spec_text}"
        
        c_is_ceiling = "吊顶" in c_name or c_code_val.startswith("3.60.21.30")
        c_is_floor = "净化新风" in c_name or "净化循环" in c_name or c_code_val.startswith("3.60.21.20") or c_code_val.startswith("3.60.21.01") or c_code_val.startswith("3.60.21.00")

        if is_q_pau:
            if q_is_ceiling:
                if c_is_ceiling:
                    base_score += 0.40
                    reasons.append("吊顶新风机组匹配")
                elif c_is_floor:
                    base_score -= 0.45
                    reasons.append("吊顶/落地机组冲突")
            elif q_is_floor:
                if "净化新风机组" in c_name:
                    base_score += 0.40
                    reasons.append("落地净化新风机组匹配")
                elif c_is_ceiling:
                    base_score -= 0.45
                    reasons.append("落地/吊顶机组冲突")

        # b. Self-taking Fresh Air (自取新风 / 净化循环机组)
        q_is_self_fresh = q_attrs.get("自取新风") is True or "自取新风" in f"{q_name} {q_spec_text}"
        if not q_is_self_fresh and is_q_ahu:
            sf_v = q_attrs.get("送风量") or q_attrs.get("风量")
            xf_v = q_attrs.get("新风量")
            if sf_v and xf_v and abs(float(sf_v) - float(xf_v)) < 1.0:
                q_is_self_fresh = True

        if is_q_ahu and q_is_self_fresh:
            if "净化循环机组（自取新风）" in c_name or "自取新风" in c_name:
                base_score += 0.40
                reasons.append("净化循环机组(自取新风)匹配")
            elif "净化新风机组" in c_name or "吊顶新风机组" in c_name:
                base_score -= 0.35
                reasons.append("循环/新风机组类型冲突")

        # Motor type preference (变频电机优选)
        if "变频" in f"{q_name} {q_spec_text}" or "变频" in c_full_spec:
            if "变频电机" in c_full_spec:
                base_score += 0.15
                reasons.append("变频电机配置优选")

        # c. Air Volume & Two-pipe & VFD matching
        q_vol = q_attrs.get("风量") or q_attrs.get("送风量")
        if q_vol is not None:
            c_spec_clean = re.sub(r'm3/h|m³/h|m3|m³', '', c_full_spec, flags=re.I)
            c_vols = [float(x) for x in re.findall(r'(?:送风量|风量|新风量)[^\d]*(\d+)', c_spec_clean)]
            if not c_vols:
                c_vols = [float(x) for x in re.findall(r'(\d+)', c_spec_clean)]
            if any(abs(float(q_vol) - cv) < 5.0 for cv in c_vols):
                base_score += 0.30
                reasons.append(f"机组风量精确匹配({int(q_vol)})")
            elif c_vols and not any(abs(float(q_vol) - cv) / max(float(q_vol), 1e-5) < 0.15 for cv in c_vols):
                base_score -= 0.40
                reasons.append("机组风量偏差过大")

    # 3. Fan Coil Unit (FCU / 风机盘管)
    is_q_fcu = "风机盘管" in q_name or q_attrs.get("卡式") or "KFP" in q_name.upper() or "FCU" in q_name.upper()
    is_c_fcu = "风机盘管" in c_name or c_code_val.startswith("3.50.43.")

    if is_q_fcu and is_c_fcu:
        base_score += 0.25
        reasons.append("风机盘管品类匹配")

        q_is_cassette = q_attrs.get("卡式") is True or any(k in f"{q_name} {q_spec_text}" for k in ["卡式", "四吹", "四面出风", "四方向"])
        c_is_cassette = "卡式" in c_full_spec or "卡式" in c_name
        c_is_horizontal = "卧式" in c_full_spec or "暗装" in c_full_spec

        if q_is_cassette:
            if c_is_cassette:
                base_score += 0.35
                reasons.append("卡式风机盘管匹配")
            elif c_is_horizontal:
                base_score -= 0.40
                reasons.append("卡式/卧式暗装结构冲突")
        elif not q_is_cassette and "卧式" in f"{q_name} {q_spec_text}":
            if c_is_horizontal:
                base_score += 0.25
                reasons.append("卧式暗装结构匹配")
            elif c_is_cassette:
                base_score -= 0.35
                reasons.append("卧式/卡式结构冲突")

        if any(k in f"{q_name} {q_spec_text}" for k in ["提升泵", "自带提升泵", "带提升泵", "冷凝水提升泵"]):
            if "提升泵" in c_full_spec:
                base_score += 0.20
                reasons.append("冷凝水提升泵匹配")

        q_fcu_vol = q_attrs.get("风量") or q_attrs.get("送风量")
        if q_fcu_vol:
            c_fcu_clean = re.sub(r'm3/h|m³/h|m3|m³', '', c_full_spec, flags=re.I)
            c_fcu_vols = [float(x) for x in re.findall(r'风量[^\d]*(\d+)', c_fcu_clean)]
            if not c_fcu_vols:
                c_fcu_vols = [float(x) for x in re.findall(r'(\d+)', c_fcu_clean)]
            if any(abs(float(q_fcu_vol) - v) < 5.0 for v in c_fcu_vols):
                base_score += 0.35
                reasons.append(f"风盘风量精确匹配({int(q_fcu_vol)})")
            elif c_fcu_vols and not any(abs(float(q_fcu_vol) - v) / max(float(q_fcu_vol), 1e-5) < 0.10 for v in c_fcu_vols):
                base_score -= 0.50
                reasons.append("风盘风量偏差过大")

    # 4. Multi-Split VRF Indoor Unit (多联式空调室内机)
    is_q_vrf = "多联机" in q_name or "多联式" in q_name or "VRV" in q_name.upper() or "VRF" in q_name.upper() or q_attrs.get("多联机")
    is_c_vrf = "多联式空调室内机" in c_name or "多联机" in c_name or c_code_val.startswith("3.50.03.")

    if is_q_vrf and is_c_vrf:
        base_score += 0.30
        reasons.append("多联机室内机品类匹配")

        # Two-way cassette with drain pump preference for medical clean room
        if "两方向" in c_full_spec and "提升泵" in c_full_spec:
            base_score += 0.25
            reasons.append("两方向带提升泵机型匹配")

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
                    reasons.append(f"多联机制冷量精确匹配({q_cool}kW)")
                else:
                    base_score -= 0.45
                    reasons.append("多联机制冷量冲突")

    # 5. Fan Interval Matching & Default Preferences (离心风机区间匹配与缺省优选)
    is_q_fan = (domain == "fan" or any(k in q_name for k in ["排风机", "送风机", "静音型排风机", "离心风机", "轴流风机", "通风机"]) or ("风机" in q_name and "风机盘管" not in q_name and "FCU" not in q_name.upper()))
    is_c_fan = "离心风机" in c_name or "通风机" in candidate.get("category", "") or c_code_val.startswith("3.50.29.")

    if is_q_fcu and is_c_fan:
        base_score = min(base_score, 0.15)
        reasons.append("风机盘管/通风机品类冲突")

    if is_q_fan and is_c_fcu:
        base_score = min(base_score, 0.15)
        reasons.append("通风机/风机盘管品类冲突")

    if is_q_fan and is_c_fan:
        base_score += 0.20
        reasons.append("通风机品类匹配")

        # a. Air volume interval matching (风量：1~500m³/h)
        q_fan_vol = q_attrs.get("风量")
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
                    reasons.append(f"风量落在区间内({int(c_min)}~{int(c_max)}m³/h)")
                else:
                    base_score -= 0.50
                    reasons.append("风量超出候选区间")

        # b. External static pressure interval matching (机外余压≤500Pa)
        q_fan_press = q_attrs.get("机外余压") or q_attrs.get("余压")
        if q_fan_press is not None:
            q_fp = float(q_fan_press)
            if "≤500Pa" in c_full_spec or "≤500pa" in c_full_spec:
                if q_fp <= 500.0:
                    base_score += 0.30
                    reasons.append("余压满足≤500Pa要求")
                else:
                    base_score -= 0.50
                    reasons.append("余压超出≤500Pa")
            elif "500Pa＜机外余压≤2000Pa" in c_full_spec:
                if 500.0 < q_fp <= 2000.0:
                    base_score += 0.30
                    reasons.append("余压满足500~2000Pa要求")
                elif q_fp <= 500.0:
                    base_score -= 0.50
                    reasons.append("余压低于候选高压区间")
            elif "＞2000Pa" in c_full_spec or ">2000Pa" in c_full_spec:
                if q_fp > 2000.0:
                    base_score += 0.30
                else:
                    base_score -= 0.60
                    reasons.append("余压远低于超高压区间")

        # c. Default Material Preference (镀锌钢板 vs 不锈钢/玻璃钢)
        q_has_ss = "不锈钢" in f"{q_name} {q_spec_text}"
        q_has_frp = "玻璃钢" in f"{q_name} {q_spec_text}"
        c_has_galv = "镀锌" in c_full_spec
        c_has_ss = "不锈钢" in c_full_spec
        c_has_frp = "玻璃钢" in c_full_spec

        if not q_has_ss and not q_has_frp:
            if c_has_galv:
                base_score += 0.20
                reasons.append("默认标准镀锌钢板优选")
            elif c_has_ss or c_has_frp:
                base_score -= 0.40
                reasons.append("非默认材质降权")
        elif q_has_ss:
            if c_has_ss:
                base_score += 0.30
                reasons.append("不锈钢材质匹配")
            elif c_has_galv:
                base_score -= 0.45
                reasons.append("不锈钢/镀锌材质冲突")

        # d. Default Explosion-proof Preference (非防爆 vs 防爆风机)
        q_is_ex = q_attrs.get("防爆") is True or any(k in f"{q_name} {q_spec_text}" for k in ["防爆", "防爆型", "防爆风机", "EX"])
        c_is_ex = "防爆风机" in c_full_spec or ("防爆" in c_full_spec and "非防爆" not in c_full_spec)
        c_is_non_ex = "非防爆" in c_full_spec

        if q_is_ex:
            if c_is_ex:
                base_score += 0.35
                reasons.append("防爆风机要求匹配")
            elif c_is_non_ex:
                base_score -= 0.60
                reasons.append("非防爆风机拦截")
        else:
            if c_is_non_ex:
                base_score += 0.25
                reasons.append("默认非防爆风机优选")
            elif c_is_ex:
                base_score -= 0.60
                reasons.append("无需防爆降权")

    # 6. Ductwork Matching (镀锌钢板风管厚度匹配)
    is_q_duct = any(k in q_name for k in ["风管", "通风管", "洁净风管", "风道"]) or q_attrs.get("板材厚度")
    is_c_duct = "风管" in c_name or c_code_val.startswith("2.22.45.")

    if is_q_duct:
        if is_c_duct:
            base_score += 0.30
            reasons.append("风管品类匹配")

            # Penalize fittings (管件) when query is a straight duct
            if "管件" in c_name and "管件" not in q_name:
                base_score -= 0.40
                reasons.append("风管/管件形态冲突")

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
                        reasons.append(f"风管厚度精确匹配({q_thick}mm)")
                    else:
                        base_score -= 0.45
                        reasons.append("风管厚度冲突")
        else:
            base_score -= 0.50
            reasons.append("非风管品类冲突")
    # E. Dynamic LLM Matching Rules (must_contain, must_not_contain, score_boost_keywords)
    try:
        from app.llm_rule_engine import LLMRuleManager, DynamicRuleInterpreter, RuleHitStatsTracker
        active_rules = LLMRuleManager.get_active_rules()
        cand_text = f"{c_name} {c_full_spec} {candidate.get('category', '')} {candidate.get('code', '')}"
        q_category = query_info.get("category", "")
        
        def scope_matches(scope_tgt: str, candidate_names: list) -> bool:
            if not scope_tgt:
                return True
            targets = [t.strip().lower() for t in re.split(r'[/,，、|\s]+', scope_tgt) if t.strip()]
            for c in candidate_names:
                if not c:
                    continue
                c_low = str(c).lower()
                if any(t in c_low or c_low in t for t in targets):
                    return True
            return False

        for r in active_rules:
            scope_lvl = r.get("scope_level", "category")
            scope_tgt = r.get("scope_target", "")
            if scope_lvl == "discipline" and scope_tgt and not scope_matches(scope_tgt, [q_category, query_info.get("domain", ""), query_info.get("discipline", ""), "给排水", "通用"]):
                continue
            if scope_lvl == "category" and scope_tgt and not scope_matches(scope_tgt, [q_name, query_info.get("name", ""), query_info.get("raw_name", "")]):
                continue
            dsl = r.get("parsed_dsl", {})
            matching_rules = dsl.get("matching_rules", {})
            if matching_rules:
                is_excl, boost_val, dyn_reasons = DynamicRuleInterpreter.evaluate_matching_filters(
                    q_spec_text, cand_text, matching_rules
                )
                if is_excl:
                    base_score = max(0.0, base_score - 0.60)
                    reasons.extend(dyn_reasons)
                elif boost_val != 0:
                    base_score = min(1.0, max(0.0, base_score + (boost_val / 100.0)))
                    reasons.extend(dyn_reasons)
                    if r.get("rule_id"):
                        RuleHitStatsTracker().record_hit(r["rule_id"])
    except Exception as e:
        logger.error(f"Error evaluating dynamic matching rules: {e}")

    # Bound to [0.0, 1.0] and apply fine-grained tie-breaking if saturated
    if base_score >= 0.999:
        if q_norm_full and c_norm_full and q_norm_full == c_norm_full:
            final_score = 100.0
        elif model_matched_exact:
            final_score = 99.8
        else:
            # Fine-grained tiebreaker based on text edit similarity
            spec_sim = calculate_text_similarity(q_spec_text, c_full_spec)
            name_sim = calculate_text_similarity(q_name, c_name)
            tiebreak = (spec_sim * 0.6 + name_sim * 0.4) * 1.5
            
            # Domain bonus for specialized configurations
            if "提升泵" in c_full_spec:
                tiebreak += 0.3
            if "两方向" in c_full_spec:
                tiebreak += 0.2
            if "变频电机" in c_full_spec:
                tiebreak += 0.4
            if is_q_fan:
                if q_is_ex and c_is_ex:
                    tiebreak += 0.6
                elif not q_is_ex and c_is_non_ex:
                    tiebreak += 0.6
                if q_fan_press is not None and float(q_fan_press) <= 500 and ("≤500Pa" in c_full_spec or "≤500pa" in c_full_spec):
                    tiebreak += 0.4

            final_score = 98.0 + tiebreak
    else:
        final_score = max(0.0, base_score) * 100.0

    final_score = min(100.0, max(0.0, final_score))
    
    # Deduplicate match reasons
    unique_reasons = []
    for r in reasons:
        if r not in unique_reasons:
            unique_reasons.append(r)
            
    return round(final_score, 1), unique_reasons

def hybrid_recall(query_info: Dict[str, Any], query_vector: Optional[Any] = None) -> List[Dict[str, Any]]:
    """
    Hybrid recall module: combines FTS5, LIKE model search, and Vector search.
    Returns: List of unique candidate material records with similarity scores.
    """
    candidates: Dict[str, Dict[str, Any]] = {}
    
    q_name = query_info.get("name", "")
    q_spec_text = query_info.get("specification", "")
    q_category = query_info.get("category", "")
    
    # Parse specs to get model
    q_parsed = query_info.get("parsed_spec") or extract_params(q_spec_text, q_category)
    q_model = q_parsed.get("model", "")
    
    # 1. Path A1: Model search via FTS5 (avoiding disk table scan)
    if q_model and len(q_model) >= 2:
        model_results = search_materials_keyword(q_model, limit=100)
        for cand in model_results:
            cand["recall_source"] = "model_match"
            cand["semantic_score"] = 0.0
            candidates[cand["code"]] = cand

    # 2. Path A2: SQLite FTS5 search (Top 1000)
    from app.normalizer import clean_text_standard
    clean_keyword = clean_text_standard(q_name)
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
    
    if clean_keyword:
        fts_results = search_materials_keyword(clean_keyword, limit=150)
        for r in fts_results:
            if r["code"] not in candidates:
                r["recall_source"] = "fts"
                r["semantic_score"] = 0.0
                candidates[r["code"]] = r

    # Also search by extracted clean name from dynamic rules / query attributes (e.g. 截止阀)
    q_attrs = q_parsed.get("attrs", {}) if isinstance(q_parsed, dict) else {}
    extracted_name = q_attrs.get("名称") or q_attrs.get("name")
    if extracted_name and len(str(extracted_name)) >= 2 and str(extracted_name) != clean_keyword:
        fts_ext_results = search_materials_keyword(str(extracted_name), limit=150)
        for r in fts_ext_results:
            if r["code"] not in candidates:
                r["recall_source"] = "fts_extracted_name"
                r["semantic_score"] = 0.0
                candidates[r["code"]] = r

    # Also search by Chinese term without leading English prefix (e.g. AP-1 动力配电箱 -> 动力配电箱)
    zh_term = re.sub(r'^[a-zA-Z0-9\-_]+', '', q_name).strip()
    if zh_term and len(zh_term) >= 2 and zh_term != clean_keyword:
        fts_zh_results = search_materials_keyword(zh_term, limit=100)
        for r in fts_zh_results:
            if r["code"] not in candidates:
                r["recall_source"] = "fts_zh"
                r["semantic_score"] = 0.0
                candidates[r["code"]] = r

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
        hvac_recall_terms.append("离心风机（箱）")
        hvac_recall_terms.append("离心风机")
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
            for r in hvac_res:
                if r["code"] not in candidates:
                    r["recall_source"] = f"fts_hvac_{term}"
                    r["semantic_score"] = 0.0
                    candidates[r["code"]] = r

    # 3. Path B: Vector search (Top 50)
    from app.vector_index import index_manager
    if index_manager.is_enabled:
        q_vec = query_vector
        if q_vec is None:
            # Clean text for vector query
            clean_q_text = f"{q_name} {q_spec_text}".strip()
            q_vec = index_manager.encode_single_query(clean_q_text)
            
        if q_vec is not None:
            vector_results = index_manager.search(q_vec, top_k=50)
            for m, score in vector_results:
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

    # Fallback search if candidates is empty
    if not candidates:
        fallback_term = q_name[:4] if len(q_name) > 4 else q_name
        if fallback_term:
            fallback_res = search_materials_keyword(fallback_term, limit=100)
            for r in fallback_res:
                r["recall_source"] = "fallback"
                r["semantic_score"] = 0.0
                candidates[r["code"]] = r

    return list(candidates.values())

def match_item(query_info: Dict[str, Any], query_vector: Optional[Any] = None, batch_confirmed_series: List[str] = []) -> Dict[str, Any]:
    """
    Main entry point for matching a single query item.
    """
    q_name = query_info.get("name", "")
    q_spec_text = query_info.get("specification", "")
    q_category = query_info.get("category", "")
    
    # Pre-parse specs to inject into query info
    if "parsed_spec" not in query_info:
        query_info["parsed_spec"] = extract_query_params(q_name, q_spec_text, q_category)
        
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
    scored_candidates = []
    for cand in candidates:
        score, reasons = score_candidate(query_info, cand, cand["semantic_score"])
        
        # Apply series clustering boost if candidate belongs to a confirmed series
        if check_series_match(q_model, cand.get("specification", "")):
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
                    score = min(100.0, score + 5.0)
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
        c_od = c_attrs.get("外径") or c_attrs.get("dn_val")

        scored_candidates.append({
            "code": cand["code"],
            "name": cand["name"],
            "specification": cand["specification"],
            "category": cand["category"],
            "unit": cand["unit"],
            "brand": cand["brand"],
            "description": cand["description"],
            "score": score,
            "match_reasons": reasons[:2],
            "c_thickness": c_thickness,
            "c_od": c_od
        })
        
    # Check if query has specified wall thickness
    q_parsed = query_info.get("parsed_spec", {}) if query_info else {}
    q_attrs = query_info.get("attrs") or q_parsed.get("attrs", {}) if isinstance(q_parsed, dict) else {}
    q_thickness = q_attrs.get("壁厚") or q_attrs.get("厚度")
    q_od_val = q_attrs.get("外径") or q_attrs.get("dn_val")

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

    # Sort by score descending, with exact text match, diameter closeness, thickness tie-breaker, and dimension closeness
    scored_candidates.sort(key=lambda x: (
        x["score"],
        1.0 if "名称与规格完全一致" in x.get("match_reasons", []) else (0.5 if "规格型号完全一致" in x.get("match_reasons", []) else 0.0),
        get_od_closeness(x),
        get_thickness_tiebreaker(x),
        get_dim_closeness(x),
        calculate_text_similarity(q_spec_text, x.get("specification", ""))
    ), reverse=True)
    
    # Format and ensure strict tie-breaker differentiation on scores
    if len(scored_candidates) > 1:
        for i in range(1, len(scored_candidates)):
            if scored_candidates[i]["score"] >= scored_candidates[i-1]["score"] and scored_candidates[i]["score"] >= 80.0:
                if scored_candidates[i]["specification"] != scored_candidates[i-1]["specification"] or scored_candidates[i]["name"] != scored_candidates[i-1]["name"]:
                    scored_candidates[i]["score"] = round(max(0.0, scored_candidates[i-1]["score"] - 0.1), 1)

    # Threshold for 100% exact match
    is_exact = False
    matched_code = None
    
    if scored_candidates:
        top_one = scored_candidates[0]
        # Match threshold V0.0.2: exact match if score >= 75.0 without hard conflicts
        if top_one["score"] >= 75.0:
            is_exact = True
            matched_code = top_one["code"]
            
    return {
        "is_exact": is_exact,
        "matched_code": matched_code,
        "top_candidates": scored_candidates[:10] # Output Top 10 for detailed view
    }

