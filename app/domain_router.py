# -*- coding: utf-8 -*-
import os
import json
import re
from typing import Dict, Any, List, Tuple

CONFIG_DIR = os.getenv("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))

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

_NOISE_SECTION_RE = re.compile(
    r'(?:^|\n)\s*(?:\d+[\.．、\s]*)?(?:工作内容|施工内容|安装要求|施工要求|备注)\s*[:：].*',
    re.DOTALL,
)


def _strip_routing_noise(spec: str) -> str:
    if not spec:
        return ""
    return _NOISE_SECTION_RE.sub("", spec).strip()


def route_domain_with_confidence(
    category: str,
    name: str,
    specification: str,
    professional_hint: str = "",
) -> Tuple[str, float, str]:
    """
    Three-Tier Weighted Router for 30 Domain Classification Matrix (QueryDomainRouter v1.3 / V018-M0).
    Returns: (domain, confidence, tier)
      - Tier 1 (1.00): Spec & Name Strong Keyword Overwrites
      - Tier 2 (0.85): Name Head Noun Controlled Dictionary / Category Map
      - Tier 3 (0.60): Sheet/Professional Probabilistic Hint Vector (low confidence -> allow cross-domain)
      - Fallback (0.20): generic (unknown -> allow cross-domain)
    """
    cat_clean = (category or "").strip()
    name_clean = (name or "").strip()
    spec_clean = _strip_routing_noise((specification or "").strip())
    full_text = f"{name_clean} {spec_clean}".strip()
    full_upper = full_text.upper()
    name_upper = name_clean.upper()

    # ==========================================
    # Tier 1: Strong Keyword Overwrite (1.0 Confidence)
    # ==========================================
    for rule in overwrite_rules:
        keywords = rule.get("trigger_keywords", [])
        target_domain = rule.get("override_domain")
        if target_domain == "damper" and any(kw in ("风管", "通风管", "风道") for kw in keywords):
            # Guard: "风管机" in spec (e.g. UPVC管 冷凝水管 用于风管机) must not override pipe to damper
            text_no_fgj = full_text.replace("风管机", "")
            if any(kw in text_no_fgj for kw in keywords):
                return DOMAIN_SCHEMA_MAP.get(target_domain, target_domain), 1.0, "tier1_overwrite"
            continue
        if any(kw in full_text for kw in keywords):
            return DOMAIN_SCHEMA_MAP.get(target_domain, target_domain), 1.0, "tier1_overwrite"

    # Strong spec/name markers
    if any(kw in cat_clean or kw in name_clean or kw in full_text for kw in [
        "调节风阀", "多叶调节", "防火阀", "排烟防火阀", "排烟阀", "止回风阀", "密闭风阀",
        "常开防火阀", "常闭防火阀", "定风量阀", "变风量阀", "多叶排烟口", "风阀",
        "对开多叶调节阀", "风量调节阀", "70℃", "280℃", "70度", "280度"
    ]) and any(w in full_text for w in ["阀", "排烟口", "风口调节"]):
        return "damper", 1.0, "tier1_strong"
    if any(kw in cat_clean or kw in name_clean for kw in ["消声器", "消声弯头", "消音器", "消声百叶"]):
        return "silencer", 1.0, "tier1_strong"
    if any(kw in cat_clean or kw in name_clean for kw in ["静压箱", "消声静压箱", "集风箱", "稳压箱"]):
        return "plenum", 1.0, "tier1_strong"
    # Ductwork routing
    if any(kw in cat_clean or kw in name_clean for kw in ["风管", "通风管", "洁净风管", "风道", "排烟风管", "排风管", "送风管", "镀锌钢板风管"]):
        return "damper", 1.0, "tier1_strong"
    # Medical/HVAC filter routing (with safe English boundaries)
    if any(kw in name_clean or kw in cat_clean for kw in ["高效过滤器", "中效过滤器", "高效空气过滤器"]):
        return "filter_ffu", 1.0, "tier1_strong"
    if any(re.search(r'(?<![a-zA-Z])' + re.escape(acronym) + r'(?![a-zA-Z])', full_upper) for acronym in ["H13", "H14", "HEPA", "FFU"]):
        return "filter_ffu", 1.0, "tier1_strong"

    # HVAC unit routing (with safe English boundaries)
    hvac_cn_keywords = [
        "全新风空气处理机组", "回风循环空气处理机组", "组合式空气处理机组", "风机盘管机组", "新风预处理机组",
        "循环空气处理机组", "热回收新风机组", "全热回收新风机组", "直膨式空气处理机组", "洁净室专用机组",
        "末端送风单元", "变风量空调系统", "定风量空调系统", "新风风机单元", "定风量空调", "热回收",
        "风机盘管", "卡式风机盘管", "多联机", "多联式", "VRV", "VRF", "净化循环机组", "净化新风机组",
        "吊顶新风机组", "空调机组", "洁卫型空调机组", "多联式空调室内机"
    ]
    hvac_en_acronyms = [
        "MAU", "PAU", "RAU", "FAU", "FCU", "RCU", "DOAS", "VAV", "RTU", "HRU", "ERU", "HPU", "FPU", "AHU",
        "HRV", "ERV", "DXU", "CRU", "TU", "CAV", "PAF", "VRV", "VRF"
    ]
    if any(kw in full_upper for kw in hvac_cn_keywords):
        return "ahu", 1.0, "tier1_strong"
    if any(re.search(r'(?<![a-zA-Z])' + re.escape(acronym) + r'(?![a-zA-Z])', full_upper) for acronym in hvac_en_acronyms):
        return "ahu", 1.0, "tier1_strong"
    if "YJV22" in full_upper or "YJV" in full_upper or "WDZ-YJY" in full_upper:
        if "控制" not in full_upper and "KVV" not in full_upper:
            return "cable_power", 1.0, "tier1_strong"
    if any(re.search(r'(?<![A-Z])' + kw + r'(?![A-Z])', full_upper) for kw in [
        "KVVP", "KVVR", "KVV", "RVVP", "RYYSP", "RYYP", "RYY"
    ]):
        return "cable_control", 1.0, "tier1_strong"

    # UPS (不间断电源) detection
    if any(kw in full_upper for kw in ["UPS", "不间断电源", "蓄电池屏", "蓄电池柜"]):
        return "ups", 1.0, "tier1_strong"

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
            return "conduit", 1.0, "tier1_strong"
    # Name-based conduit detection
    if any(kw in name_clean for kw in ["线管", "钢导管", "镀锌薄壁", "配管", "管线"]):
        return "conduit", 1.0, "tier1_strong"

    # ==========================================
    # Tier 2: Name Head Noun & Subcategory Controlled Dict (0.85 Confidence)
    # ==========================================
    # Weak current / ELV devices (must precede generic cable/switchgear)
    if any(kw in name_clean for kw in [
        "医护主机", "触摸式控制终端", "控制终端", "上位机", "控制电脑", "交换机",
        "人脸识别", "门禁", "光纤", "光缆", "对讲主机", "呼叫主机", "监控摄像",
        "读卡器", "网络主机", "信息插座", "网络机柜"
    ]):
        return "weak_current", 0.85, "tier2_head_noun"

    # Decoration trim/cladding (e.g. 不锈钢包边（防火门）) must precede door
    if any(kw in name_clean for kw in ["包边", "收边", "门套", "窗套"]):
        return "decoration", 0.85, "tier2_head_noun"

    # Purification / cleanroom equipment (e.g. 传递门/麻醉塔/洁净工作台)
    if any(kw in name_clean or kw in cat_clean or kw in full_text for kw in [
        "彩钢板", "手工板", "岩棉板", "洁净板", "传递门", "传递窗", "麻醉塔", "麻醉吊塔", "医用吊塔", "洁净工作台", "超净工作台"
    ]):
        return "purification", 0.85, "tier2_head_noun"

    # Lighting (check case-insensitively on name_clean before cable fallback)
    if any(kw in name_upper or kw in cat_clean.upper() for kw in ["平板灯", "泪珠灯", "应急灯", "防爆灯", "LED", "灯具"]):
        return "lighting", 0.85, "tier2_head_noun"

    # Cable & Wire subcategories
    if any(kw in cat_clean or kw in name_clean for kw in ["电线", "电缆", "配线", "控制线", "双绞线", "缆", "线"]) and "套管" not in cat_clean and "管" not in cat_clean and "管" not in name_clean:
        if any(kw in full_upper for kw in ["KVV", "KVVR", "RVVP", "RYY", "RYYP", "RYYSP", "控制电缆", "控制线"]):
            return "cable_control", 0.85, "tier2_head_noun"
        elif ("*" not in spec_clean and "×" not in spec_clean) and any(kw in full_upper for kw in ["BV", "BVR", "BYJ", "BYJR", "BLV", "电线", "配线", "线"]):
            return "wire", 0.85, "tier2_head_noun"
        else:
            return "cable_power", 0.85, "tier2_head_noun"

    # Pipe subcategories (guard: valves containing material names like UPVC球阀 go to valve branch)
    if "阀" not in name_clean:
        if any(kw in cat_clean or kw in name_clean for kw in ["不锈钢管", "304管", "316L管"]):
            return "pipe_ss", 0.85, "tier2_head_noun"
        if any(kw in cat_clean or kw in name_clean for kw in ["紫铜管", "脱脂铜管", "紫铜", "脱氧紫铜", "脱氧铜", "冷媒铜管"]):
            return "pipe_copper", 0.85, "tier2_head_noun"
        if any(kw in cat_clean or kw in name_clean for kw in ["PVC管", "PPR管", "PPH管", "塑料管", "UPVC"]):
            return "pipe_plastic", 0.85, "tier2_head_noun"
        if any(kw in cat_clean or kw in name_clean for kw in ["无缝钢管", "焊接钢管", "镀锌钢管", "钢管"]):
            return "pipe_steel", 0.85, "tier2_head_noun"

    # Valve subcategories
    if any(kw in cat_clean or kw in name_clean for kw in ["阀", "阀门", "调节阀"]):
        if any(kw in full_text or kw in cat_clean for kw in ["风", "防火", "排烟", "密闭", "调节", "止回风", "半周长"]):
            return "damper", 0.85, "tier2_head_noun"
        elif any(kw in full_text for kw in ["医气", "氧气", "负压", "吸引"]):
            return "valve_medical_gas", 0.85, "tier2_head_noun"
        else:
            return "valve_water", 0.85, "tier2_head_noun"

    # HVAC & Equipment head nouns
    if any(kw in name_clean or kw in cat_clean for kw in ["消声器", "微穿孔", "消音器"]):
        return "silencer", 0.85, "tier2_head_noun"
    if any(kw in name_clean or kw in cat_clean for kw in ["集风箱", "静压箱", "消声静压箱"]):
        return "plenum", 0.85, "tier2_head_noun"
    if "风机" in name_clean and "风机盘管" not in name_clean:
        return "fan", 0.85, "tier2_head_noun"
    if "冷水机" in name_clean or "冷热水机组" in name_clean:
        return "chiller", 0.85, "tier2_head_noun"
    if "集配器" in name_clean or "分水器" in name_clean or "集水器" in name_clean:
        return "manifold", 0.85, "tier2_head_noun"
    if "风口" in name_clean or "散流器" in name_clean or "百叶" in name_clean:
        return "air_terminal", 0.85, "tier2_head_noun"
    if any(kw in name_clean or kw in cat_clean for kw in ["配电箱", "配电柜", "控制柜", "开关柜", "控制箱", "AP", "AL", "ALE", "AT", "ACP"]):
        return "switchgear", 0.85, "tier2_head_noun"
    if any(kw in name_clean or kw in cat_clean for kw in ["桥架", "线槽", "梯架", "托盘"]):
        return "cable_tray", 0.85, "tier2_head_noun"
    if any(kw in name_clean or kw in cat_clean for kw in ["气密门", "医用门", "特种门", "防火门", "自动趟门"]):
        return "door", 0.85, "tier2_head_noun"
    if any(kw in name_clean or kw in cat_clean for kw in ["插座", "开关", "五孔", "86型"]):
        return "switchgear", 0.85, "tier2_head_noun"
    if any(kw in name_clean or kw in cat_clean for kw in ["地砖", "瓷砖", "釉面砖", "踢脚线"]):
        return "decoration", 0.85, "tier2_head_noun"
    if any(kw in name_clean or kw in cat_clean for kw in ["乳胶漆", "涂料", "无机涂料", "漆"]):
        return "decoration", 0.85, "tier2_head_noun"
    if any(kw in name_clean or kw in cat_clean for kw in ["铝扣板", "石膏板", "矿棉板", "窗帘盒"]):
        return "decoration", 0.85, "tier2_head_noun"
    if any(kw in name_clean or kw in cat_clean for kw in ["水龙头", "洗手盆", "拖布池", "地漏", "洁具"]):
        return "decoration", 0.85, "tier2_head_noun"
    if any(kw in name_clean or kw in cat_clean for kw in ["密封胶", "玻璃胶", "结构胶"]):
        return "decoration", 0.85, "tier2_head_noun"
    if any(kw in name_clean or kw in cat_clean for kw in ["橡塑", "玻璃棉", "岩棉保温"]):
        return "decoration", 0.85, "tier2_head_noun"

    # Category map lookup
    if cat_clean in category_map:
        return DOMAIN_SCHEMA_MAP.get(category_map[cat_clean], category_map[cat_clean]), 0.85, "tier2_category_map"
    for k, domain in category_map.items():
        if k in cat_clean:
            return DOMAIN_SCHEMA_MAP.get(domain, domain), 0.85, "tier2_category_map"

    # ==========================================
    # Tier 3: Sheet/Professional Probabilistic Hint Vector (0.60 Confidence)
    # ==========================================
    hint_src = professional_hint or cat_clean
    hint_vec = compute_sheet_hint_vector(hint_src)

    if any(k in hint_src for k in ["弱电", "智能化"]):
        if any(kw in full_upper for kw in ["KVV", "KVVR", "RVVP", "RYY", "RYYP", "RYYSP"]):
            return "cable_control", 0.60, "tier3_hint_vector"
        return "weak_current", 0.60, "tier3_hint_vector"

    if hint_vec.get("hvac", 0.0) > 0.3:
        if any(kw in full_text for kw in ["风管", "通风管", "风道"]):
            return "damper", 0.60, "tier3_hint_vector"
        elif any(kw in full_text for kw in ["阀", "防火阀", "排烟口"]):
            return "damper", 0.60, "tier3_hint_vector"
        elif any(kw in full_text for kw in ["风机盘管", "多联机", "空调机组", "新风机组", "循环机组", "AHU", "PAU", "FCU", "MAU"]):
            return "ahu", 0.60, "tier3_hint_vector"
        elif "风机" in full_text or "排风" in full_text:
            return "fan", 0.60, "tier3_hint_vector"
        elif "管" in full_text:
            return "pipe_steel", 0.60, "tier3_hint_vector"
        return "ahu", 0.60, "tier3_hint_vector"

    if hint_vec.get("elec", 0.0) > 0.3:
        if any(kw in full_upper for kw in ["YJV", "BV", "BVR", "BYJ", "缆", "线"]):
            return "cable_power", 0.60, "tier3_hint_vector"
        elif "箱" in full_text or "柜" in full_text:
            return "switchgear", 0.60, "tier3_hint_vector"
        return "switchgear", 0.60, "tier3_hint_vector"

    if hint_vec.get("plumb", 0.0) > 0.3:
        if "阀" in full_text:
            return "valve_water", 0.60, "tier3_hint_vector"
        elif "管" in full_text:
            return "pipe_plastic", 0.60, "tier3_hint_vector"
        return "valve_water", 0.60, "tier3_hint_vector"

    if hint_vec.get("gas", 0.0) > 0.3:
        return "medical_gas", 0.60, "tier3_hint_vector"

    if hint_vec.get("deco", 0.0) > 0.3:
        return "decoration", 0.60, "tier3_hint_vector"

    # General civil fallback
    return "generic", 0.20, "fallback_generic"


def route_domain(category: str, name: str, specification: str, professional_hint: str = "") -> str:
    """Three-Tier Weighted Router returning canonical domain key."""
    domain, _, _ = route_domain_with_confidence(category, name, specification, professional_hint)
    return domain

def reload_router_configs():
    global category_map_data, category_map, high_risk_categories, overwrite_rules
    category_map_data = load_json_config("category_domain_map.json", {})
    category_map = category_map_data.get("map", {})
    high_risk_categories = category_map_data.get("high_risk_categories", [])
    overwrite_rules = category_map_data.get("override_rules", category_map_data.get("overwrite_rules", []))

