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

def route_domain(category: str, name: str, specification: str, professional_hint: str = "") -> str:
    """
    Route item to standard domain using Category Overwrite logic and subcategory分流 rules.
    Priority: Specification Strong Keywords Overwrite > Name/Subcategory Match > Category Map.
    """
    cat_clean = (category or "").strip()
    name_clean = (name or "").strip()
    spec_clean = (specification or "").strip()
    full_text = f"{name_clean} {spec_clean}"

    # 1. First Priority: Category Overwrite logic based on spec/name strong keywords (open to all categories)
    for rule in overwrite_rules:
        keywords = rule.get("trigger_keywords", [])
        target_domain = rule.get("override_domain")
        if any(kw in full_text for kw in keywords):
            return target_domain

    # 2. Second Priority: Subcategory matching rules
    # Cable Subcategory分流 rules
    if cat_clean == "电气装备用电线电缆" or any(kw in cat_clean for kw in ["电线", "电缆"]) or any(kw in name_clean for kw in ["电线", "电缆"]):
        # KVV/KVVR/RVVP/控制电缆
        if any(kw in spec_clean.upper() for kw in ["KVV", "KVVR", "RVVP"]) or "控制" in full_text:
            return "cable_control"
        # Wire (single-core BV/BVR/BYJ/BLV/etc)
        elif ("*" not in spec_clean and "×" not in spec_clean) and (any(kw in spec_clean.upper() for kw in ["BV", "BVR", "BYJ", "BLV"]) or "电线" in name_clean or "电线" in cat_clean):
            return "wire"
        else:
            return "cable_power"

    # Valve Subcategory分流 rules
    if cat_clean == "阀门调节阀" or cat_clean == "调节阀":
        if "风" in full_text:
            return "damper"
        elif "水" in full_text:
            return "valve_water"
        else:
            # Default to damper if it has wind properties or valve if it looks like pipeline valve
            if any(kw in full_text for kw in ["法兰", "口径", "DN", "阀门", "截止", "止回"]):
                return "valve_water"
            return "damper"

    # Copper pipe
    if cat_clean == "脱脂无缝紫铜管" or "紫铜管" in name_clean:
        return "pipe_copper"

    # 3. Third Priority: Name-based keyword checks for explicit routing
    if ("风机" in name_clean and "风机盘管" not in name_clean) or "送风" in name_clean or "排风" in name_clean:
        return "fan"
    if "消声器" in name_clean:
        return "silencer"
    if any(kw in name_clean for kw in ["静压箱", "集风箱", "稳压箱", "风箱", "负压箱", "回风箱"]):
        return "plenum"
    if "过滤器" in name_clean or "FFU" in name_clean:
        return "filter_ffu"
    if "风口" in name_clean or "百叶" in name_clean:
        return "air_terminal"
    if "冷热水" in name_clean or "冷水机" in name_clean:
        return "chiller"
    if any(kw in full_text.upper() for kw in ["AHU", "MAU", "PAU", "RAU", "FAU", "FCU", "RCU", "DOAS", "VAV", "RTU", "HRU", "ERU", "HPU", "FPU", "空调", "风机盘管", "新风柜", "全新风", "回风机组", "预空调", "变风量", "热回收"]):
        return "ahu"
    if "集配器" in name_clean:
        return "manifold"
    if "电线" in name_clean:
        return "wire"
    if "电缆" in name_clean:
        if "控制" in name_clean:
            return "cable_control"
        return "cable_power"
    if any(kw in name_clean or kw in cat_clean for kw in ["配电箱", "配电柜", "控制柜", "开关柜", "控制箱", "接线箱", "等电位箱", "等电位端子箱", "变压器柜", "UPS"]):
        return "switchgear"
    if any(kw in name_clean or kw in cat_clean for kw in ["对讲", "摄像机", "呼叫分机", "报警", "弱电终端", "探测器"]):
        return "weak_current"

    # Bypass switchgear routing for small terminal components (switches/sockets) to avoid cabinet attribute penalties
    if any(kw in name_clean or kw in cat_clean for kw in ["插座", "底盒", "面板", "五孔", "双绞线", "网线", "光缆", "光纤", "对线"]):
        if not any(k in name_clean or k in cat_clean for k in ["开关柜", "负荷开关", "隔离开关"]):
            return "generic"
    if "开关" in name_clean or "开关" in cat_clean:
        if not any(k in name_clean or k in cat_clean for k in ["开关柜", "负荷开关", "隔离开关"]):
            return "generic"

    # 4. Fourth Priority: Category Map Lookup
    if cat_clean in category_map:
        return category_map[cat_clean]

    # Fallback search by substring matching
    for k, domain in category_map.items():
        if k in cat_clean:
            return domain

    # 5. Fifth Priority: Professional sheet/general hint keywords fallback
    cat_upper = cat_clean.upper()
    if any(kw in cat_upper for kw in ["暖通", "空调", "通风"]):
        if "风机" in full_text:
            return "fan"
        elif "风阀" in full_text or "防火阀" in full_text or "调节阀" in full_text:
            return "damper"
        elif "消声" in full_text:
            return "silencer"
        elif "静压" in full_text:
            return "plenum"
        return "ahu"
    elif any(kw in cat_upper for kw in ["强电", "动力", "照明", "电气"]):
        spec_upper = spec_clean.upper()
        if any(kw in spec_upper for kw in ["KVV", "KVVR", "RVVP"]) or "控制" in spec_upper:
            return "cable_control"
        elif any(kw in spec_upper for kw in ["YJV", "YJY", "YJLV", "WDZ-YJY", "ZR-YJV", "NH-YJV", "电缆", "C*", "*C"]):
            return "cable_power"
        elif any(kw in spec_upper for kw in ["BV", "BVR", "BYJ", "BLV", "电线", "导线"]):
            return "wire"
        elif "MM2" in spec_upper or "MM²" in spec_upper or "平方" in spec_upper:
            if "*" in spec_upper or "×" in spec_upper:
                return "cable_power"
            return "wire"
        elif any(kw in name_clean for kw in ["线", "缆", "BV", "BVR", "YJV"]):
            return "wire" if "线" in name_clean else "cable_power"
        return "switchgear"
    elif any(kw in cat_upper for kw in ["弱电", "智能化", "自控", "消防"]):
        return "weak_current"
    elif any(kw in cat_upper for kw in ["给排水", "给水", "排水", "水处理", "纯水", "水"]):
        if "阀" in full_text:
            return "valve_water"
        elif "管" in full_text:
            if "不锈钢" in full_text:
                return "pipe_ss"
            elif "塑料" in full_text or "PVC" in full_text or "PPR" in full_text:
                return "pipe_plastic"
            return "pipe_steel"
        return "valve_water"

    # Default Fallback
    return "generic"

def reload_router_configs():
    global category_map_data, category_map, high_risk_categories, overwrite_rules
    category_map_data = load_json_config("category_domain_map.json", {})
    category_map = category_map_data.get("map", {})
    high_risk_categories = category_map_data.get("high_risk_categories", [])
    overwrite_rules = category_map_data.get("overwrite_rules", [])
