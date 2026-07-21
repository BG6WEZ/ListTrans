# -*- coding: utf-8 -*-
import difflib
import re
from typing import Dict, Any, List, Tuple, Optional
from functools import lru_cache

from app.db_manager import search_materials_keyword, query_by_code, get_db_connection
from app.preprocessor import clean_text, extract_params, extract_device_name, fts_tokenize, load_config

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

def check_dimension_conflict(q_val, c_val, max_deviation=0.10):
    """
    Check if two dimension strings (like A*B or A*B*C) conflict.
    If either component has a relative deviation > max_deviation, return True.
    """
    if not q_val or not c_val:
        return False
    try:
        q_parts = [float(x) for x in str(q_val).split('*')]
        c_parts = [float(x) for x in str(c_val).split('*')]
        if len(q_parts) == len(c_parts):
            for q_p, c_p in zip(q_parts, c_parts):
                if abs(q_p - c_p) / max(q_p, c_p, 1e-5) > max_deviation:
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
    if isinstance(q_v, (int, float)) and isinstance(c_v, (int, float)):
        # Check if deviation exceeds the tolerance rule
        tol = tolerance_rules.get(attr, 0.0)
        tol = max(tol, 1e-5)
        diff_ratio = abs(q_v - c_v) / max(q_v, c_v, 1e-5)
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
    
    q_parsed = query_info.get("parsed_spec") or extract_params(q_spec_text, query_info.get("professional", q_category))
    q_model = q_parsed.get("model", "")
    q_attrs = q_parsed.get("attrs", {})
    
    c_name = candidate.get("name", "").strip()
    c_spec_text = candidate.get("specification", "")
    c_category = candidate.get("category", "")
    c_desc = candidate.get("description", "")
    c_full_spec = f"{c_spec_text} {c_desc}".strip()
    
    # Refine generic or short candidate names using specification text
    if len(c_name) <= 3 or any(kw in c_name for kw in ["柜", "线", "管", "阀", "箱", "板", "机", "按钮"]):
        c_name = extract_device_name(c_full_spec, c_name)
    
    # 1. Professional category conflict check
    q_prof = query_info.get("professional")
    if q_prof and q_prof != "全部" and q_prof != "其它":
        c_prof = get_professional_category(c_category, c_full_spec)
        if c_prof != "其它" and q_prof != c_prof:
            return 0.0, ["专业冲突阻断"]
            
    # 2. Brand conflict check
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
    

    
    # Load pre-parsed params if available to boost speed
    c_parsed = None
    if "parsed_params" in candidate and candidate["parsed_params"]:
        import json
        try:
            c_parsed = json.loads(candidate["parsed_params"])
        except Exception:
            pass
            
    if not c_parsed:
        c_parsed = extract_params(c_full_spec, c_category)
        
    c_model = c_parsed.get("model", "")
    c_attrs = c_parsed.get("attrs", {})
    
    # 1. Model similarity score
    model_score = 0.0
    model_matched_exact = False
    model_conflict = False
    if q_model:
        if c_model:
            if q_model.lower() == c_model.lower() or q_model.lower() in c_full_spec.lower() or q_model.lower() in c_name.lower():
                model_score = 1.0
                model_matched_exact = True
                reasons.append("型号完全吻合")
            elif check_series_match(q_model, c_model):
                model_score = 0.7
                reasons.append("型号系列相近")
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

    # B. Match Must & Should attributes
    for attr in processed_keys:
        # Skip flags which are handled as a group above
        if flags_list and attr in flags_list:
            continue

        q_val = q_attrs.get(attr)
        c_val = c_attrs.get(attr)

        if q_val is None:
            continue

        has_any_attr = True

        if c_val is not None:
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
                sim_score = 1.0 if str(q_val).lower() == str(c_val).lower() else 0.0
                attr_scores[attr] = sim_score
                attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                continue
        else:
            # Single-direction missing penalty: only when Query specifies but Candidate lacks
            if attr in must_keys:
                missing_penalty += 0.15
                reasons.append(f"必填属性缺失处罚: {attr}")
            elif attr in should_keys:
                missing_penalty += 0.05
                reasons.append(f"选填属性缺失处罚: {attr}")

    # Calculate weighted average attributes score
    if attr_scores:
        sum_weighted_scores = sum(attr_scores[k] * attr_weights[k] for k in attr_scores)
        sum_weights = sum(attr_weights[k] for k in attr_scores)
        attr_score = sum_weighted_scores / sum_weights if sum_weights > 0 else 0.0
        
        for k, s in attr_scores.items():
            if (k in tolerance_rules or k == "尺寸" or k in ["芯数", "截面积"]) and s < 0.8:
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
    if check_series_match(q_model, c_model):
        base_score += pos_rules.get("series_match", 0.05)
        reasons.append("属同系列产品")
    if query_info.get("brand") and query_info["brand"].lower() == candidate.get("brand", "").lower():
        base_score += pos_rules.get("brand_match", 0.05)
        reasons.append("品牌一致")
        
        
    # Apply Negative Boosts
    neg_rules = boost_rules.get("negative", DEFAULT_BOOSTS["negative"])
    if check_category_conflict(q_category, c_category):
        base_score += neg_rules.get("category_conflict", -0.30)
        reasons.append("品类冲突(偏置)")
    if "冷水" in q_spec_text and "热水" in c_full_spec or "热水" in q_spec_text and "冷水" in c_full_spec:
        base_score += neg_rules.get("medium_conflict", -0.20)
        reasons.append("温水介质冲突")

    # Out of 2x tolerance check
    has_large_deviation = False
    for attr in processed_keys:
        q_val = q_attrs.get(attr)
        c_val = c_attrs.get(attr)
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

    # Bound to [0.0, 1.0] and convert to 100 points
    final_score = max(0.0, min(1.0, base_score)) * 100.0
    
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
    
    # 1. Path A1: Model exact prefix search
    if q_model:
        conn = get_db_connection()
        cursor = conn.cursor()
        # LIKE model% search
        cursor.execute("SELECT * FROM materials WHERE search_tokens LIKE ? LIMIT 100", (f"%{q_model.lower()}%",))
        rows = cursor.fetchall()
        conn.close()
        for r in rows:
            cand = dict(r)
            cand["recall_source"] = "model_match"
            cand["semantic_score"] = 0.0
            candidates[cand["code"]] = cand

    # 2. Path A2: SQLite FTS5 search (Top 1000)
    # Search keywords based on clean name
    clean_keyword = re.sub(r'\(.*?\)|（.*?）|室外型|室内型', ' ', q_name).strip()
    if clean_keyword:
        fts_results = search_materials_keyword(clean_keyword, limit=1000)
        for r in fts_results:
            if r["code"] not in candidates:
                r["recall_source"] = "fts"
                r["semantic_score"] = 0.0
                candidates[r["code"]] = r

    # Search keywords based on category (Top 500)
    if q_category and q_category != q_name:
        fts_cat_results = search_materials_keyword(q_category, limit=500)
        for r in fts_cat_results:
            if r["code"] not in candidates:
                r["recall_source"] = "fts_category"
                r["semantic_score"] = 0.0
                candidates[r["code"]] = r

    # 3. Path B: Vector search (Top 200)
    from app.vector_index import index_manager
    if index_manager.is_enabled:
        q_vec = query_vector
        if q_vec is None:
            # Clean text for vector query
            clean_q_text = f"{q_name} {q_spec_text}".strip()
            q_vec = index_manager.encode_single_query(clean_q_text)
            
        if q_vec is not None:
            vector_results = index_manager.search(q_vec, top_k=200)
            for m, score in vector_results:
                code = m["code"]
                # Query full row if not already in candidates
                if code not in candidates:
                    # Let's query from DB or reuse manager mapping
                    db_row = query_by_code(code)
                    if db_row:
                        db_row["recall_source"] = "semantic"
                        db_row["semantic_score"] = score
                        candidates[code] = db_row
                else:
                    # Just update semantic score
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
        query_info["parsed_spec"] = extract_params(q_spec_text, q_category)
        
    q_model = query_info["parsed_spec"].get("model", "")
    
    # 1. Recall
    candidates = hybrid_recall(query_info, query_vector)
    
    # 2. Score
    scored_candidates = []
    for cand in candidates:
        score, reasons = score_candidate(query_info, cand, cand["semantic_score"])
        
        # Apply series clustering boost if candidate belongs to a confirmed series
        if check_series_match(q_model, cand.get("specification", "")):
            # If batch_confirmed_series list contains this candidate's series prefix
            c_model = extract_params(cand.get("specification", "")).get("model", "")
            if c_model:
                c_prefix = c_model.split("-")[0].strip().lower()
                if c_prefix in batch_confirmed_series:
                    score = min(100.0, score + 5.0)
                    if "同批确认系列优先" not in reasons:
                        reasons.append("系列优先")
                        
        scored_candidates.append({
            "code": cand["code"],
            "name": cand["name"],
            "specification": cand["specification"],
            "category": cand["category"],
            "unit": cand["unit"],
            "brand": cand["brand"],
            "description": cand["description"],
            "score": score,
            "match_reasons": reasons[:2]
        })
        
    # Sort by score descending
    scored_candidates.sort(key=lambda x: x["score"], reverse=True)
    
    # Threshold for 100% exact match
    is_exact = False
    matched_code = None
    
    if scored_candidates:
        top_one = scored_candidates[0]
        # Match threshold V0.0.2: exact match if score >= 85.0
        if top_one["score"] >= 85.0:
            is_exact = True
            matched_code = top_one["code"]
            
    return {
        "is_exact": is_exact,
        "matched_code": matched_code,
        "top_candidates": scored_candidates[:10] # Output Top 10 for detailed view
    }
