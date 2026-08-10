# -*- coding: utf-8 -*-
import os
import json
import re
from typing import Dict, Any, List

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")

def load_json_config(filename: str, default_val: Any) -> Any:
    path = os.path.join(CONFIG_DIR, filename)
    if not os.path.exists(path):
        return default_val
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config {filename}: {e}")
        return default_val

# Load category domain mapping configuration
category_map_data = load_json_config("category_domain_map.json", {})
category_map = category_map_data.get("map", {})
high_risk_categories = category_map_data.get("high_risk_categories", [])
overwrite_rules = category_map_data.get("overwrite_rules", [])

# Standard 30 Domain Identifier Aliases to schema domain keys mapping
DOMAIN_SCHEMA_MAP = {
    "fan_hvac": "fan",
    "valve_hvac": "damper",
    "cable_power": "cable_power",
    "cable_control": "cable_control",
    "wire": "wire",
    "silencer": "silencer",
    "air_filter": "filter_ffu",
    "header_steam": "manifold",
    "plenum": "plenum",
    "chiller": "chiller",
    "air_terminal": "air_terminal",
    "door_special": "door",
    "ahu": "ahu",
    "med_gas": "medical_gas",
    "pipe_stainless": "pipe_ss",
    "pipe_water": "pipe_plastic",
    "valve_water": "valve_water",
    "distribution_box": "switchgear",
    "clean_room": "purification",
    "lighting": "lighting",
    "switch_socket": "switchgear",
    "bridge_raceway": "cable_tray",
    "conduit_elec": "conduit",
    "floor_tile": "decoration",
    "wall_paint": "decoration",
    "ceiling_board": "decoration",
    "sanitary_ware": "decoration",
    "sealant": "decoration",
    "insulation": "decoration",
    "ups": "ups",
    "generic_civil": "generic"
}

def compute_sheet_hint_vector(professional_hint: str) -> Dict[str, float]:
    """
    Compute probabilistic hint vector for Sheet / Professional context.
    Returns domain weights dict, e.g. {"hvac": 0.4, "elec": 0.4, "deco": 0.2}
    """
    hint = (professional_hint or "").lower()
    vec = {"hvac": 0.0, "elec": 0.0, "deco": 0.0, "plumb": 0.0, "gas": 0.0}

    if any(k in hint for k in ["暖通", "空调", "通风", "净化"]):
        vec["hvac"] += 0.5
    if any(k in hint for k in ["电气", "强电", "弱电", "动力", "照明", "智能化", "电", "水电"]):
        vec["elec"] += 0.5
    if any(k in hint for k in ["给排水", "给水", "排水", "水专", "纯水", "水", "水电"]):
        vec["plumb"] += 0.5
    if any(k in hint for k in ["医气", "气体", "动力气体"]):
        vec["gas"] += 0.5
    if any(k in hint for k in ["装饰", "土建", "建材", "装修"]):
        vec["deco"] += 0.5

    total = sum(vec.values())
    if total > 0:
        return {k: v / total for k, v in vec.items()}
    return vec

def route_domain(category: str, name: str, specification: str, professional_hint: str = "") -> str:
    """
    Three-Tier Weighted Router for 30 Domain Classification Matrix (QueryDomainRouter v1.2)
    Tier 1 (Confidence 1.0): Spec & Name Strong Keyword Overwrites
    Tier 2 (Confidence 0.85): Name Head Noun Controlled Dictionary Matching
    Tier 3 (Confidence 0.60): Sheet/Professional Probabilistic Hint Vector + Secondary Feature Fusion
    """
    cat_clean = (category or "").strip()
    name_clean = (name or "").strip()
    spec_clean = (specification or "").strip()
    full_text = f"{name_clean} {spec_clean}"
    full_upper = full_text.upper()

    # ==========================================
    # Tier 1: Strong Keyword Overwrite (1.0 Confidence)
    # ==========================================
    for rule in overwrite_rules:
        keywords = rule.get("trigger_keywords", [])
        target_domain = rule.get("override_domain")
        if any(kw in full_text for kw in keywords):
            return DOMAIN_SCHEMA_MAP.get(target_domain, target_domain)

    # Strong spec/name markers
    if "消声器" in name_clean or "消声弯头" in name_clean:
        return "silencer"
    if any(kw in name_clean for kw in ["静压箱", "消声静压箱", "集风箱", "稳压箱"]):
        return "plenum"
    # Medical/HVAC filter routing (with safe English boundaries)
    if any(kw in name_clean for kw in ["高效过滤器", "中效过滤器", "高效空气过滤器"]):
        return "filter_ffu"
    if any(re.search(r'(?<![a-zA-Z])' + re.escape(acronym) + r'(?![a-zA-Z])', full_upper) for acronym in ["H13", "H14", "HEPA", "FFU"]):
        return "filter_ffu"

    # HVAC unit routing (with safe English boundaries)
    hvac_cn_keywords = [
        "全新风空气处理机组", "回风循环空气处理机组", "组合式空气处理机组", "风机盘管机组", "新风预处理机组",
        "循环空气处理机组", "热回收新风机组", "全热回收新风机组", "直膨式空气处理机组", "洁净室专用机组",
        "末端送风单元", "变风量空调系统", "定风量空调系统", "新风风机单元", "定风量空调", "热回收"
    ]
    hvac_en_acronyms = [
        "MAU", "PAU", "RAU", "FAU", "FCU", "RCU", "DOAS", "VAV", "RTU", "HRU", "ERU", "HPU", "FPU", "AHU",
        "HRV", "ERV", "DXU", "CRU", "TU", "CAV", "PAF"
    ]
    if any(kw in full_upper for kw in hvac_cn_keywords):
        return "ahu"
    if any(re.search(r'(?<![a-zA-Z])' + re.escape(acronym) + r'(?![a-zA-Z])', full_upper) for acronym in hvac_en_acronyms):
        return "ahu"
    if "YJV22" in full_upper or "YJV" in full_upper or "WDZ-YJY" in full_upper:
        if "控制" not in full_upper and "KVV" not in full_upper:
            return "cable_power"

    # UPS (不间断电源) detection
    if any(kw in full_upper for kw in ["UPS", "不间断电源", "蓄电池屏", "蓄电池柜"]):
        return "ups"

    # ── Conduit (线管/电气导管) detection ─────────────────────────────────────
    # Check for electrical conduit type codes in name or spec
    has_conduit_code = False
    if any(c in full_upper for c in ["JDG", "KBG", "FPC", "KPC", "MT", "CP"]):
        has_conduit_code = True
    elif re.search(r'\b(?:SC|RC)\s*(?:DN|DE|De)?\s*\d+', full_upper, re.IGNORECASE) or re.search(r'(?:SC|RC)\d+', full_upper, re.IGNORECASE):
        has_conduit_code = True

    if has_conduit_code:
        # Only route as conduit if it looks like an electrical context
        # (avoid matching SC/RC plumbing pipes in water contexts)
        elec_hint = any(kw in full_text for kw in [
            "线管", "导管", "穿线", "电气", "电线", "配管", "敷设", "JDG", "KBG",
            "WC", "CC", "FC", "CE", "WE"
        ])
        water_hint = any(kw in full_text for kw in ["给水", "排水", "消防水", "自动喷水", "水管"])
        if elec_hint or not water_hint:
            return "conduit"
    # Name-based conduit detection
    if any(kw in name_clean for kw in ["线管", "钢导管", "镀锌薄壁", "配管", "管线"]):
        return "conduit"


    # ==========================================
    # Tier 2: Name Head Noun & Subcategory Controlled Dict (0.85 Confidence)
    # ==========================================
    # Cable & Wire subcategories
    if any(kw in cat_clean or kw in name_clean for kw in ["电线", "电缆", "配线", "控制线", "双绞线", "缆", "线"]) and "套管" not in cat_clean and "管" not in cat_clean and "管" not in name_clean:
        if any(kw in full_upper for kw in ["KVV", "KVVR", "RVVP", "控制电缆"]):
            return "cable_control"
        elif ("*" not in spec_clean and "×" not in spec_clean) and any(kw in full_upper for kw in ["BV", "BVR", "BYJ", "BYJR", "BLV", "电线", "配线", "线"]):
            return "wire"
        else:
            return "cable_power"

    # Pipe subcategories
    if any(kw in cat_clean or kw in name_clean for kw in ["不锈钢管", "304管", "316L管"]):
        return "pipe_ss"
    if any(kw in cat_clean or kw in name_clean for kw in ["紫铜管", "脱脂铜管", "紫铜", "脱氧紫铜", "脱氧铜", "冷媒铜管"]):
        return "pipe_copper"
    if any(kw in cat_clean or kw in name_clean for kw in ["PVC管", "PPR管", "PPH管", "塑料管", "UPVC"]):
        return "pipe_plastic"
    if any(kw in cat_clean or kw in name_clean for kw in ["无缝钢管", "焊接钢管", "镀锌钢管", "钢管"]):
        return "pipe_steel"

    # Valve subcategories
    if any(kw in cat_clean or kw in name_clean for kw in ["阀", "阀门", "调节阀"]):
        if any(kw in full_text for kw in ["风", "防火", "排烟", "密闭", "调节"]):
            return "damper"
        elif any(kw in full_text for kw in ["医气", "氧气", "负压", "吸引"]):
            return "valve_medical_gas"
        else:
            return "valve_water"

    # HVAC & Equipment head nouns
    if any(kw in name_clean or kw in cat_clean for kw in ["消声器", "微穿孔", "消音器"]):
        return "silencer"
    if any(kw in name_clean or kw in cat_clean for kw in ["集风箱", "静压箱", "消声静压箱"]):
        return "plenum"
    if "风机" in name_clean and "风机盘管" not in name_clean:
        return "fan"
    if "冷水机" in name_clean or "冷热水机组" in name_clean:
        return "chiller"
    if "集配器" in name_clean or "分水器" in name_clean or "集水器" in name_clean:
        return "manifold"
    if "风口" in name_clean or "散流器" in name_clean or "百叶" in name_clean:
        return "air_terminal"
    if any(kw in name_clean or kw in cat_clean for kw in ["配电箱", "配电柜", "控制柜", "开关柜", "控制箱", "AP", "AL", "ALE", "AT", "ACP"]):
        return "switchgear"
    if any(kw in name_clean or kw in cat_clean for kw in ["桥架", "线槽", "梯架", "托盘"]):
        return "cable_tray"
    if any(kw in name_clean or kw in cat_clean for kw in ["气密门", "医用门", "特种门", "防火门", "自动趟门"]):
        return "door"
    if any(kw in name_clean or kw in cat_clean for kw in ["彩钢板", "手工板", "岩棉板", "洁净板"]):
        return "purification"
    if any(kw in name_clean or kw in cat_clean for kw in ["平板灯", "泪珠灯", "应急灯", "LED", "灯具"]):
        return "lighting"
    if any(kw in name_clean or kw in cat_clean for kw in ["插座", "开关", "五孔", "86型"]):
        return "switchgear"
    if any(kw in name_clean or kw in cat_clean for kw in ["地砖", "瓷砖", "釉面砖", "踢脚线"]):
        return "decoration"
    if any(kw in name_clean or kw in cat_clean for kw in ["乳胶漆", "涂料", "无机涂料", "漆"]):
        return "decoration"
    if any(kw in name_clean or kw in cat_clean for kw in ["铝扣板", "石膏板", "矿棉板", "窗帘盒"]):
        return "decoration"
    if any(kw in name_clean or kw in cat_clean for kw in ["水龙头", "洗手盆", "拖布池", "地漏", "洁具"]):
        return "decoration"
    if any(kw in name_clean or kw in cat_clean for kw in ["密封胶", "玻璃胶", "结构胶"]):
        return "decoration"
    if any(kw in name_clean or kw in cat_clean for kw in ["橡塑", "玻璃棉", "岩棉保温"]):
        return "decoration"

    # Category map lookup
    if cat_clean in category_map:
        return DOMAIN_SCHEMA_MAP.get(category_map[cat_clean], category_map[cat_clean])
    for k, domain in category_map.items():
        if k in cat_clean:
            return DOMAIN_SCHEMA_MAP.get(domain, domain)

    # ==========================================
    # Tier 3: Sheet/Professional Probabilistic Hint Vector (0.60 Confidence)
    # ==========================================
    hint_vec = compute_sheet_hint_vector(professional_hint or cat_clean)

    if hint_vec.get("hvac", 0.0) > 0.3:
        if "风" in full_text:
            return "fan" if "机" in full_text else "damper"
        elif "管" in full_text:
            return "pipe_steel"
        return "ahu"

    if hint_vec.get("elec", 0.0) > 0.3:
        if any(kw in full_upper for kw in ["YJV", "BV", "BVR", "BYJ", "缆", "线"]):
            return "cable_power"
        elif "箱" in full_text or "柜" in full_text:
            return "switchgear"
        return "switchgear"

    if hint_vec.get("plumb", 0.0) > 0.3:
        if "阀" in full_text:
            return "valve_water"
        elif "管" in full_text:
            return "pipe_plastic"
        return "valve_water"

    if hint_vec.get("gas", 0.0) > 0.3:
        return "medical_gas"

    if hint_vec.get("deco", 0.0) > 0.3:
        return "decoration"

    # General civil fallback
    return "generic"

def reload_router_configs():
    global category_map_data, category_map, high_risk_categories, overwrite_rules
    category_map_data = load_json_config("category_domain_map.json", {})
    category_map = category_map_data.get("map", {})
    high_risk_categories = category_map_data.get("high_risk_categories", [])
    overwrite_rules = category_map_data.get("override_rules", category_map_data.get("overwrite_rules", []))

