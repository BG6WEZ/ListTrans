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

def check_dimension_conflict(q_val, c_val, max_deviation=0.15):
    """
    Check if two dimension strings (like A*B or A*B*C) conflict.
    Extracts sorted numeric components and checks relative deviations.
    """
    if not q_val or not c_val:
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
                q_str, c_str = str(q_val).lower().strip(), str(c_val).lower().strip()
                if q_str == c_str:
                    sim_score = 1.0
                elif (q_str in ["镀锌钢板", "镀锌板", "镀锌"] and c_str in ["镀锌钢板", "镀锌板", "镀锌"]) or \
                     (q_str in ["不锈钢", "304", "316l", "不锈钢304"] and c_str in ["不锈钢", "304", "316l", "不锈钢304"]) or \
                     (q_str in ["紫铜", "脱脂紫铜", "紫铜管", "铜管", "铜"] and c_str in ["紫铜", "脱脂紫铜", "紫铜管", "铜管", "铜"]):
                    sim_score = 1.0
                else:
                    sim_score = 0.0
                attr_scores[attr] = sim_score
                attr_weights[attr] = domain_config.get("weights", {}).get(attr, 0.1)
                continue
        else:
            # Single-direction missing penalty: only when Query specifies Must field but Candidate lacks it
            if attr in must_keys:
                missing_penalty += 0.15
                reasons.append(f"必填属性缺失处罚: {attr}")
            # Should fields follow "reward if present, do not penalize if missing" strategy

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
        if (q_mat_str in copper_syns or any(s in q_mat_str for s in ["紫铜", "脱氧铜", "冷媒铜"])) and (c_mat_str in copper_syns or any(s in c_mat_str for s in ["紫铜", "铜管"])):
            pass
        elif ("304" in q_mat_str and "304" in c_mat_str) or ("316" in q_mat_str and "316" in c_mat_str) or ("不锈钢" in q_mat_str and "304" in c_mat_str) or ("304" in q_mat_str and "不锈钢" in c_mat_str):
            pass
        elif any(m in q_mat_str for m in ["304", "316", "pph", "ppr", "铜"]) or any(m in c_mat_str for m in ["304", "316", "pph", "ppr", "铜"]):
            if q_mat_str != c_mat_str:
                base_score -= 0.40
                reasons.append("管材材质冲突")

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

