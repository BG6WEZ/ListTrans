# -*- coding: utf-8 -*-
import os
import json
import re
from typing import Dict, Any, List, Tuple, Optional
from app.normalizer import clean_text_standard, load_json_config, to_half_width, convert_de_to_dn

CONFIG_DIR = os.getenv("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))

# Load schemas and unit conversion dictionaries
domain_schema = load_json_config("domain_schema.json", {})
unit_norm_data = load_json_config("unit_norm.json", {})
unit_classes = unit_norm_data.get("unit_classes", {})

def get_unit_conversion(unit_str: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Look up standard conversion factor and standard unit for a given unit string.
    Returns: (multiplier, standard_unit)
    """
    if not unit_str:
        return None, None
    unit_clean = unit_str.strip()
    for class_name, class_info in unit_classes.items():
        conversions = class_info.get("conversions", {})
        standard_unit = class_info.get("standard", "")
        # Try exact case-insensitive match
        for u_key, mult in conversions.items():
            if u_key.lower() == unit_clean.lower():
                return mult, standard_unit
    return None, None

def parse_numeric_complex(text: str) -> Dict[str, Any]:
    """
    Parse complex numeric strings containing ranges, inequalities,约数, and units.
    Returns structured results: {
        "val": float,         # Average/Nominal value (scaled)
        "min_val": float,     # Range min (scaled)
        "max_val": float,     # Range max (scaled)
        "op": str,            # Logical comparison operator (>=, <=, >, <)
        "base_val": float,    # Comparison threshold (scaled)
        "unit": str           # Clean standard unit string
    }
    """
    res = {}
    if not text:
        return res

    text_clean = to_half_width(text).strip()

    # 1. Extract unit string (e.g. m3/h, kW, kPa, mm)
    # Search for alphabetic unit or custom unit at the end of the text
    unit_match = re.search(r'([a-zA-Z³/㎡²]+)\s*$', text_clean)
    unit_str = unit_match.group(1) if unit_match else ""
    mult, std_unit = get_unit_conversion(unit_str)
    if not mult:
        mult = 1.0
    if std_unit:
        res["unit"] = std_unit

    # Strip unit from value calculation string
    val_str = re.sub(re.escape(unit_str) + r'\s*$', '', text_clean).strip() if unit_str else text_clean

    # Clean approximation prefixes/suffixes ("约", "左右", "~")
    val_str = re.sub(r'^[~约左右\s]+|[~约左右\s]+$', '', val_str).strip()

    # 2. Check for Inequalities: >=, <=, >, <, ≥, ≤
    op = None
    op_match = re.search(r'(>=|<=|>=|<=|>=|<=|>=|<=|≥|≤|>|<)', val_str)
    if op_match:
        op_raw = op_match.group(1)
        if op_raw in [">=", "≥"]:
            op = ">="
        elif op_raw in ["<=", "≤"]:
            op = "<="
        elif op_raw == ">":
            op = ">"
        elif op_raw == "<":
            op = "<"
        res["op"] = op
        # Extract number next to operator
        num_match = re.search(r'(\d+(?:\.\d+)?)', val_str)
        if num_match:
            base_val = float(num_match.group(1)) * mult
            res["base_val"] = base_val
            res["val"] = base_val
        return res

    # 3. Check for Ranges: 1000-2000, 1000~2000, 1000至2000, 1000及2000
    range_match = re.search(r'(\d+(?:\.\d+)?)\s*[-~—至到及或]\s*(\d+(?:\.\d+)?)', val_str)
    if range_match:
        min_v = float(range_match.group(1)) * mult
        max_v = float(range_match.group(2)) * mult
        res["min_val"] = min_v
        res["max_val"] = max_v
        res["val"] = (min_v + max_v) / 2.0
        return res

    # 4. Standard Single value fallback
    num_match = re.search(r'(\d+(?:\.\d+)?)', val_str)
    if num_match:
        val = float(num_match.group(1)) * mult
        res["val"] = val

    return res

class ConfigDrivenExtractor:
    """
    Core dynamic extractor loaded from configuration definitions in domain_schema.json.
    """
    def __init__(self, domain: str):
        self.domain = domain
        self.config = domain_schema.get(domain, {})
        self.must_fields = self.config.get("must", [])
        self.should_fields = self.config.get("should", [])
        self.flags = self.config.get("flags", [])

    def extract(self, text: str) -> Dict[str, Any]:
        attrs = {}
        if not text:
            return attrs

        # 1. Parse Must & Should fields using configure regexes
        fields = self.must_fields + self.should_fields
        for field in fields:
            key = field["key"]
            ftype = field.get("type", "str")
            regex = field.get("extract_regex")
            options = field.get("options")

            # Try to match key pattern first if regex is provided
            val_found = None
            if regex:
                match = re.search(regex, text, re.IGNORECASE)
                if match:
                    # Take the first non-None group value
                    for g in match.groups():
                        if g is not None:
                            val_found = g.strip()
                            break
            
            # Fallback dynamic key-value search if not matched by specific regex
            # e.g., if text has "风量: 1000" or similar
            if not val_found:
                aliases = [key] + field.get("aliases", [])
                for alias in aliases:
                    # Look for K:V matches in text
                    pattern = r'(?:^|[,，;；\s\n\r])' + re.escape(alias) + r'\s*[:：]\s*([^\s,，;；\n\r]{1,40})'
                    kv_match = re.search(pattern, text, re.IGNORECASE)
                    if kv_match:
                        val_found = kv_match.group(1).strip()
                        break

            if val_found:
                if ftype == "float" or ftype == "int":
                    parsed = parse_numeric_complex(val_found)
                    if key in ["管径", "DN", "口径"]:
                        for k_sub in ["val", "min_val", "max_val", "base_val"]:
                            if k_sub in parsed:
                                parsed[k_sub] = convert_de_to_dn(parsed[k_sub], text)
                    if "val" in parsed:
                        attrs[key] = int(parsed["val"]) if ftype == "int" else parsed["val"]
                    if "min_val" in parsed and "max_val" in parsed:
                        attrs[f"{key}_min"] = parsed["min_val"]
                        attrs[f"{key}_max"] = parsed["max_val"]
                    if "op" in parsed and "base_val" in parsed:
                        attrs[f"{key}_op"] = parsed["op"]
                        attrs[f"{key}_base"] = parsed["base_val"]
                elif ftype == "enum" and options:
                    # Match options case-insensitively
                    for opt in options:
                        if opt.lower() in val_found.lower() or opt.lower() in text.lower():
                            attrs[key] = opt
                            break
                else:
                    attrs[key] = val_found
            elif ftype == "enum" and options:
                for opt in options:
                    if opt.lower() in text.lower():
                        attrs[key] = opt
                        break

        # 2. Check for Boolean Flags
        for flag in self.flags:
            if flag in text:
                attrs[flag] = True
            else:
                # If explicit negation is not present, we can just omit or check synonyms
                pass

        # 3. Size/Dimensions post-processing for HVAC plenum/damper/door etc.
        # Ensure standard form "A*B" or "A*B*C"
        dim_match = re.search(r'(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)(?:\s*[*x×]\s*(\d+(?:\.\d+)?))?', text)
        if dim_match:
            val = dim_match.group(0).replace('×', '*').replace('x', '*').replace(' ', '').strip()
            attrs["尺寸"] = val
            if self.domain in ["pipe_steel", "pipe_ss", "pipe_plastic", "pipe_copper"]:
                try:
                    attrs["外径"] = float(dim_match.group(1))
                    attrs["壁厚"] = float(dim_match.group(2))
                except Exception:
                    pass
        elif "尺寸" in attrs and isinstance(attrs["尺寸"], str):
            attrs["尺寸"] = attrs["尺寸"].replace('×', '*').replace('x', '*').replace(' ', '').strip()

        # Materials post-processing fallback
        if "材质" not in attrs:
            for mat in ["不锈钢304", "不锈钢316L", "不锈钢", "碳钢", "镀锌钢板", "玻璃钢", "PP", "PPH", "UPVC", "PE", "铜"]:
                if mat in text:
                    attrs["材质"] = mat
                    break

        # 4. Cable specific extraction override to maintain legacy string format (like "5" and "2.5" or "3+2" and "185+95")
        if self.domain in ["cable_power", "cable_control", "wire"]:
            c_attrs = extract_cable_params(text)
            attrs.update(c_attrs)
        elif self.domain == "fan" or "风机" in text or "排风机" in text:
            fan_attrs = extract_fan_params(text)
            attrs.update(fan_attrs)
        elif self.domain == "conduit":
            conduit_attrs = extract_conduit_params(text)
            attrs.update(conduit_attrs)
        elif self.domain == "ups":
            ups_attrs = extract_ups_params(text)
            attrs.update(ups_attrs)

        return attrs

def extract_fan_params(text: str) -> Dict[str, Any]:
    """
    Extract fan parameters:
    Air volume intervals (e.g. 1~500m³/h), static pressure intervals, material, explosion-proof.
    """
    attrs = {}
    if not text:
        return attrs
    t_clean = to_half_width(text)

    # 1. Flow interval (风量：1~500m³/h)
    m_flow = re.search(r'风量[:：]?\s*(\d+(?:\.\d+)?)\s*[-~—至到及或]\s*(\d+(?:\.\d+)?)', t_clean)
    if m_flow:
        attrs["风量_min"] = float(m_flow.group(1))
        attrs["风量_max"] = float(m_flow.group(2))
        attrs["风量"] = (attrs["风量_min"] + attrs["风量_max"]) / 2.0
    else:
        m_single = re.search(r'风量[:：]?\s*(\d+(?:\.\d+)?)', t_clean)
        if m_single:
            attrs["风量"] = float(m_single.group(1))

    # 2. Pressure interval (机外余压≤500Pa)
    if "≤500" in t_clean or "<=500" in t_clean:
        attrs["机外余压_op"] = "<="
        attrs["机外余压_base"] = 500.0
    elif "500" in t_clean and ("2000" in t_clean or "＜" in t_clean or "<" in t_clean):
        attrs["机外余压_op"] = "500-2000"
        attrs["机外余压_min"] = 500.0
        attrs["机外余压_max"] = 2000.0
    elif "＞2000" in t_clean or ">2000" in t_clean:
        attrs["机外余压_op"] = ">"
        attrs["机外余压_base"] = 2000.0
    else:
        m_pres = re.search(r'(?:机外余压|余压)[:：]?\s*(\d+(?:\.\d+)?)', t_clean)
        if m_pres:
            attrs["机外余压"] = float(m_pres.group(1))

    # 3. Material
    if "镀锌钢板" in t_clean or "镀锌" in t_clean:
        attrs["材质"] = "镀锌钢板"
    elif "不锈钢" in t_clean:
        attrs["材质"] = "不锈钢"
    elif "玻璃钢" in t_clean:
        attrs["材质"] = "玻璃钢"

    # 4. Explosion proof
    if "非防爆" in t_clean:
        attrs["防爆"] = False
    elif "防爆风机" in t_clean or "防爆" in t_clean:
        attrs["防爆"] = True

    return attrs

def extract_cable_params(text: str) -> Dict[str, Any]:
    """
    Extract cable/wire parameters from text:
    Model, Cores, Section (mm²), Voltage rating.
    """
    attrs = {}
    if not text:
        return attrs
    
    t_clean = to_half_width(text).upper().replace('X', '*').replace('×', '*').strip()
    
    # 1. Model extraction (Order matters: longest prefix first)
    models = [
        # WDZN series (Low-smoke zero-halogen flame-retardant fire-resistant)
        "WDZN-YJV22", "WDZN-YJV", "WDZN-BYJR", "WDZN-BYJ", "WDZN-KYJV", "WDZN-KYJV22",
        # WDZB series (Low-smoke zero-halogen Class B flame-retardant)
        "WDZB-YJV22", "WDZB-YJV", "WDZB-YJY", "WDZB-YJY22", "WDZB-BYJR", "WDZB-BYJ", "WDZB-KYJV", "WDZB-KYJV22", "WDZB-KVV", "WDZB-KVVP",
        # WDZA series (Low-smoke zero-halogen Class A flame-retardant)
        "WDZA-YJV22", "WDZA-YJV", "WDZA-YJY", "WDZA-YJY22", "WDZA-BYJR", "WDZA-BYJ", "WDZA-KYJV", "WDZA-KYJV22",
        # WDZC series (Low-smoke zero-halogen Class C flame-retardant)
        "WDZC-YJV22", "WDZC-YJV", "WDZC-YJY", "WDZC-YJY22", "WDZC-BYJR", "WDZC-BYJ", "WDZC-KYJV", "WDZC-KYJV22",
        # WDZ series (Low-smoke zero-halogen flame-retardant)
        "WDZ-YJV22", "WDZ-YJV", "WDZ-YJY", "WDZ-YJY22", "WDZ-BYJR", "WDZ-BYJ", "WDZ-RYJS", "WDZ-RYJ", "WDZ-KYJV", "WDZ-KYJV22",
        # NH series (Fire-resistant)
        "NH-YJV22", "NH-YJV", "NH-BV", "NH-BVR", "NH-KVV", "NH-KVV22",
        # ZR series (Flame-retardant)
        "ZR-YJV22", "ZR-YJV", "ZR-BV", "ZR-BVR", "ZR-KVV", "ZR-KVV22",
        # Basic series
        "YJV22", "YJY22", "YJV", "YJY", "KVVP2-22", "KVVP2", "KVVP", "KVV", "RVVP", "RVV",
        "BYJR", "BYJ", "BVR", "BV", "RYJS", "RYJ"
    ]
    for m in models:
        if m in t_clean:
            attrs["型号"] = m
            for fam in ["BYJR", "BYJ", "BVR", "BV", "BLV", "YJY22", "YJY", "YJV22", "YJV", "KVVP", "KVV", "RVVP", "RVV"]:
                if fam in m:
                    attrs["型号族"] = fam
                    break
            break
            
    # 2. Cable cores & section matching (e.g., 4*50+1*25, 3*2.5, 5*10, 1*240, 2.5)
    # Check compound pattern: 4*50+1*25 or 3*120+2*70
    comp_match = re.search(r'(\d+)\s*\*\s*(\d+(?:\.\d+)?)\s*\+\s*(\d+)\s*\*\s*(\d+(?:\.\d+)?)', t_clean)
    if comp_match:
        attrs["芯数"] = f"{comp_match.group(1)}+{comp_match.group(3)}"
        sec1 = float(comp_match.group(2))
        sec2 = float(comp_match.group(4))
        s1 = f"{int(sec1)}" if sec1.is_integer() else f"{sec1}"
        s2 = f"{int(sec2)}" if sec2.is_integer() else f"{sec2}"
        attrs["截面积"] = f"{s1}+{s2}"
        attrs["主芯数"] = int(comp_match.group(1))
        attrs["主截面"] = sec1
        attrs["零芯数"] = int(comp_match.group(3))
        attrs["零截面"] = sec2
    else:
        # Standard pattern: 3*2.5, 4*16, 5*6
        std_match = re.search(r'(\d+)\s*\*\s*(\d+(?:\.\d+)?)', t_clean)
        if std_match:
            attrs["芯数"] = str(int(std_match.group(1)))
            sec = float(std_match.group(2))
            s_str = f"{int(sec)}" if sec.is_integer() else f"{sec}"
            attrs["截面积"] = s_str
            attrs["主芯数"] = int(std_match.group(1))
            attrs["主截面"] = sec
        else:
            # Single core fallback: BV-2.5, 2.5mm²
            s_match = re.search(r'(?:BV|BYJ|BYJR|YJV|WDZ-YJY|BVR|RV|RVV|WDZ-BYJ)?\s*-?\s*(\d+(?:\.\d+)?)\s*(?:MM2|MM²|平方)?', t_clean)
            if s_match:
                try:
                    val = float(s_match.group(1))
                    if val in [0.5, 0.75, 1.0, 1.5, 2.5, 4.0, 6.0, 10.0, 16.0, 25.0, 35.0, 50.0, 70.0, 95.0, 120.0, 150.0, 185.0, 240.0, 300.0, 400.0, 500.0]:
                        attrs["芯数"] = "1"
                        s_str = f"{int(val)}" if val.is_integer() else f"{val}"
                        attrs["截面积"] = val
                        attrs["主芯数"] = 1
                        attrs["主截面"] = val
                except ValueError:
                    pass

    # 3. Voltage extraction
    v_match = re.search(r'(0\.6/1KV|450/750V|300/500V|10KV)', t_clean)
    if v_match:
        attrs["耐压"] = v_match.group(1)
        
    return attrs

def extract_pipe_params(text: str) -> Dict[str, Any]:
    """
    Extract pipe/tube parameters from text:
    Material, Nominal Diameter (DN), Outer Diameter (φ), Wall Thickness (mm), Connection type.
    """
    attrs = {}
    if not text:
        return attrs
        
    t_clean = to_half_width(text).strip()
    
    # 1. Material
    mats = [
        "磷酸脱氧无缝紫铜管", "脱氧紫铜管", "脱脂无缝紫铜管", "脱脂紫铜管", "无缝紫铜管", "磷酸脱氧紫铜管",
        "S30408", "304L", "316L", "304", "不锈钢", "无缝钢管", "镀锌钢管", "镀锌方管", "镀锌",
        "PPH", "PPR", "UPVC", "PVC", "PE", "紫铜管", "紫铜", "铜管", "铸铁"
    ]
    for m in mats:
        if m.lower() in t_clean.lower():
            if "铜" in m:
                attrs["材质"] = "紫铜管"
            else:
                attrs["材质"] = m
            break
            
    # 2. Nominal Diameter DN / Outer Diameter De / φ
    dn_match = re.search(r'(?:DN|口径|管径)\s*[:：]?\s*(\d+)', t_clean, re.IGNORECASE)
    if dn_match:
        attrs["管径"] = f"DN{dn_match.group(1)}"
        attrs["dn_val"] = int(dn_match.group(1))
    else:
        de_match = re.search(r'(?:DE|de)\s*[:：]?\s*(\d+)', t_clean)
        if de_match:
            dn_val = convert_de_to_dn(float(de_match.group(1)), t_clean)
            if dn_val:
                attrs["管径"] = f"DN{int(dn_val)}"
                attrs["dn_val"] = int(dn_val)
        else:
            phi_match = re.search(r'[φФΦ⌀]\s*(\d+(?:\.\d+)?)', t_clean)
            if phi_match:
                attrs["外径"] = float(phi_match.group(1))

    # 3. Wall thickness: 壁厚1.2mm, 厚1.5mm, 54*2.0, 50*30*2mm
    wall_match = re.search(r'(?:壁厚|厚度|厚)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*mm', t_clean, re.IGNORECASE)
    if not wall_match:
        wall_match = re.search(r'[*x×]\s*(\d+(?:\.\d+)?)\s*mm', t_clean, re.IGNORECASE)
    if not wall_match:
        wall_match = re.search(r'(?:壁厚|厚度|厚)\s*[:：]?\s*(\d+(?:\.\d+)?)', t_clean, re.IGNORECASE)
        
    if wall_match:
        try:
            attrs["壁厚"] = float(wall_match.group(1))
        except ValueError:
            pass

    # 4. Connection type
    conns = ["双卡压", "卡压", "法兰", "螺纹", "焊接", "由令", "热熔", "承插"]
    for c in conns:
        if c in t_clean:
            attrs["连接方式"] = c
            break
            
    return attrs

def extract_decoration_params(text: str) -> Dict[str, Any]:
    """
    Extract decoration parameters from text:
    Subtype, Dimensions (Length*Width), Thickness (mm), Surface/Process.
    """
    attrs = {}
    if not text:
        return attrs
        
    t_clean = to_half_width(text).strip()
    
    # 1. Dimensions 2D (600*600, 300*300, 300*600, 1200*2400, 780*1840, 50*30)
    dim_match = re.search(r'(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)(?:\s*[*x×]\s*(\d+(?:\.\d+)?))?', t_clean, re.IGNORECASE)
    if dim_match:
        l_val = float(dim_match.group(1))
        w_val = float(dim_match.group(2))
        attrs["尺寸_2d"] = f"{int(l_val) if l_val.is_integer() else l_val}*{int(w_val) if w_val.is_integer() else w_val}"
        attrs["尺寸"] = attrs["尺寸_2d"]
        attrs["长"] = l_val
        attrs["宽"] = w_val
        if dim_match.group(3):
            try:
                attrs["厚度"] = float(dim_match.group(3))
            except ValueError:
                pass
                
    # 2. Thickness (0.6mm, 0.8mm, 1.0mm, 1.2mm, 6mm, 8mm, 12mm, 14mm)
    if "厚度" not in attrs:
        th_match = re.search(r'(\d+(?:\.\d+)?)\s*mm\s*厚', t_clean, re.IGNORECASE)
        if not th_match:
            th_match = re.search(r'(?:厚度|板厚|壁厚)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:mm)?', t_clean, re.IGNORECASE)
        if th_match:
            try:
                attrs["厚度"] = float(th_match.group(1))
            except ValueError:
                pass

    # 3. Surface & Process
    surfaces = ["底漆", "面漆", "手工", "机制", "双开", "单开", "子母门", "覆膜", "喷涂", "防霉", "岩棉", "玻镁"]
    for s in surfaces:
        if s in t_clean:
            attrs[s] = True
            
    return attrs

BOX_PREFIX_RULES = [
    ("ACP-P", ["ACP-P", "水泵控制箱", "水泵箱", "水泵控制柜", "水泵控制", "水泵"]),
    ("ACP", ["ACP", "风机控制箱", "风机箱", "风机控制柜", "风机控制"]),
    ("ALE", ["ALE", "应急照明箱", "应急照明配电箱", "应急配电箱", "应急照明配电柜", "应急照明"]),
    ("AL", ["AL", "照明配电箱", "照明箱", "照明柜", "照明插座配电箱"]),
    ("AP", ["AP", "动力配电箱", "动力箱", "动力柜"]),
    ("ATD", ["ATD", "直流配电箱", "直流箱", "直流柜", "直流配电"]),
    ("AT", ["AT", "双电源切换箱", "双电源箱", "双电源柜", "双电源总箱", "双电源"]),
    ("AX", ["AX", "检修插座箱", "插座箱", "电源插座箱"]),
    ("AFC", ["AFC", "消防控制箱", "消防箱", "消防控制柜", "消防控制"])
]

def extract_switchgear_params(text: str) -> Dict[str, Any]:
    """Extract electrical box & control cabinet types according to prefix rules."""
    params = {}
    if not text:
        return params
    t_clean = to_half_width(text).strip()
    for prefix, syns in BOX_PREFIX_RULES:
        pattern = r'\b' + re.escape(prefix) + r'(?:[-_\d\s]|$)'
        if re.search(pattern, t_clean, re.IGNORECASE) or any(s in t_clean for s in syns):
            params["box_type"] = prefix
            params["model"] = prefix
            break
    return params

def extract_conduit_params(text: str) -> Dict[str, Any]:
    """
    Extract electrical conduit parameters with full business rules:
    - Pipe type codes: JDG, KBG, SC, RC, MT, PC, FPC, KPC, CP
    - Outer diameter extraction (direct for JDG/KBG/MT/PC/FPC/KPC, DN→OD table for SC/RC)
    - Installation method codes: WC, CC, FC, CE, WE
    """
    params = {}
    if not text:
        return params
    t_clean = to_half_width(text).strip()

    # ── 1. Pipe type code dictionary (hardcoded fixed rules) ─────────────────
    CONDUIT_TYPE_MAP = {
        "JDG":  "套接紧定式镀锌薄壁钢导管",
        "KBG":  "套接扣压式薄壁钢导管",
        "SC":   "热镀锌焊接厚壁钢管",
        "RC":   "水煤气钢管",
        "MT":   "黑铁薄壁电线管",
        "PC":   "硬质阻燃PVC塑料管",
        "FPC":  "PVC半硬电线管",
        "KPC":  "PVC塑料波纹管",
        "CP":   "金属蛇皮波纹管",
    }

    # ── 2. DN → Outer Diameter mapping for SC / RC (nominal → actual OD mm) ──
    SC_RC_DN_TO_OD = {
        15: 21.3, 20: 26.9, 25: 33.7, 32: 42.4,
        40: 48.3, 50: 60.3, 65: 76.1, 80: 88.9, 100: 114.3,
    }

    # ── 3. Installation method code dictionary ────────────────────────────────
    INSTALL_MODE_MAP = {
        "WC": "暗敷设在墙内",
        "CC": "暗敷设在顶板/屋面内",
        "FC": "暗敷设在地面/地板内",
        "CE": "沿天棚顶板明敷",
        "WE": "沿墙面明敷",
        "BE": "沿屋架或跨屋架明敷",
        "CLE": "沿柱明敷",
        "SCE": "沿钢索明敷",
    }

    # ── 4. Match pipe type code (longest match wins) ─────────────────────────
    # Using Unicode/ASCII safe lookbehind/lookahead to prevent \b boundary failures with Chinese characters
    type_order = ["FPC", "KPC", "KBG", "JDG", "SC", "RC", "MT", "PC", "CP"]
    matched_type = None
    for code in type_order:
        pattern = r'(?<![a-zA-Z])' + re.escape(code) + r'(?![a-zA-Z])'
        if re.search(pattern, t_clean, re.IGNORECASE):
            matched_type = code.upper()
            break

    # Map Chinese material/pipe descriptions to standard codes
    cn_to_code = {
        "PC":  ["塑料管", "硬质聚氯乙烯", "聚氯乙烯", "塑料线管", "pvc"],
        "SC":  ["焊接钢管", "钢导管", "厚壁钢管", "黑铁管", "sc"],
        "RC":  ["镀锌钢管", "水煤气", "热浸镀锌", "rc"],
        "JDG": ["紧定式", "套接紧定", "jdg"],
        "KBG": ["扣压式", "kbg"],
        "MT":  ["电线管", "薄壁黑铁", "mt", "wc"],
        "FPC": ["半硬", "fpc"],
        "KPC": ["波纹管", "kpc"],
        "CP":  ["蛇皮", "可挠", "cp"]
    }
    if not matched_type:
        for code, syns in cn_to_code.items():
            if any(s in t_clean.lower() for s in syns):
                matched_type = code
                break

    # ── 5. Extract size number and wall thickness ──────────────────────────
    size_num = None

    # A. Check for "size*thickness" pattern (e.g., 20*1.6, 25*1.2, DN32*2.0)
    m_thick = re.search(r'(?:DN|de|De|Φ|φ)?\s*(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)', t_clean, re.IGNORECASE)
    if m_thick:
        size_num = float(m_thick.group(1))
        if size_num.is_integer():
            size_num = int(size_num)
        params["壁厚"] = float(m_thick.group(2))
    else:
        # B. Check: Type code followed immediately by optional spaces and digits
        # e.g., "JDG16", "SC 50", "KBG20"
        if matched_type:
            m_type_size = re.search(r'(?<![a-zA-Z])' + re.escape(matched_type) + r'\s*(\d+)\b', t_clean, re.IGNORECASE)
            if m_type_size:
                size_num = int(m_type_size.group(1))
        
        # C. Fallback: Standalone digits
        if size_num is None:
            m_digits = re.search(r'(?:DN|de|De|Φ|φ)?\s*(\d+)\b', t_clean, re.IGNORECASE)
            if m_digits:
                size_num = int(m_digits.group(1))

    if matched_type:
        params["管代号"] = matched_type
        params["管型代码"] = matched_type
        params["管型名称"] = CONDUIT_TYPE_MAP.get(matched_type, matched_type)

    if size_num is not None:
        params["规格数字"] = size_num
        # SC/RC: number = DN nominal diameter → convert to outer diameter
        if matched_type in ("SC", "RC"):
            params["公称直径DN"] = size_num
            od = SC_RC_DN_TO_OD.get(size_num)
            if od:
                params["外径mm"] = od
            else:
                # Linear interpolation fallback (rare sizes)
                params["外径mm"] = size_num  # keep raw if not in table
        else:
            # JDG/KBG/MT/PC/FPC/KPC/CP: number = outer diameter directly
            params["外径mm"] = float(size_num)

    # ── 6. Extract installation method ────────────────────────────────────────
    for code, desc in INSTALL_MODE_MAP.items():
        if re.search(r'\b' + code + r'\b', t_clean, re.IGNORECASE):
            params["敷设方式代码"] = code.upper()
            params["敷设方式"] = desc
            break

    # ── 7. Legacy dn field for backward compatibility ─────────────────────────
    if "外径mm" in params and "dn" not in params:
        params["dn"] = params["外径mm"]
    if "管型代码" in params and "model" not in params:
        params["model"] = params["管型代码"]

    return params

def extract_ups_params(text: str) -> Dict[str, Any]:
    """Extract UPS parameters: capacity (kVA), backup time (min), voltage (V)."""
    params = {}
    if not text:
        return params
    t_clean = to_half_width(text).strip()
    
    # 1. Capacity (容量/Pe/P/额定/KVA)
    cap_match = re.search(r'(?:容量|Pe|P|额定)?\s*(\d+(?:\.\d+)?)\s*(?:kva|千伏安)\b', t_clean, re.IGNORECASE)
    if cap_match:
        params["容量"] = float(cap_match.group(1))
        
    # 2. Backup time (后备时间/后备/min/Min/MIN/分钟)
    time_match = re.search(r'(?:后备时间|后备)?\s*(?:[≥>=]?)?\s*(\d+(?:\.\d+)?)\s*(?:min|分钟)\b', t_clean, re.IGNORECASE)
    if time_match:
        params["后备时间"] = float(time_match.group(1))
        
    # 3. Voltage (电压/380v/220v)
    volt_match = re.search(r'(?:电压|供电电压|工作电压)?\s*(\d+(?:\.\d+)?)\s*(?:v|伏)\b', t_clean, re.IGNORECASE)
    if volt_match:
        params["电压"] = float(volt_match.group(1))
        
    return params

def extract_power_range(text: str) -> Dict[str, float]:
    """Extract power capacity ranges like 10~50KW, 50-100KW or single power Pe=71kW."""
    params = {}
    if not text:
        return params
    t_clean = to_half_width(text).strip()
    m = re.search(r'\b(\d+(?:\.\d+)?)\s*[\-~—至]\s*(\d+(?:\.\d+)?)\s*(?:kw|千瓦)\b', t_clean, re.IGNORECASE)
    if m:
        params["power_min"] = float(m.group(1))
        params["power_max"] = float(m.group(2))
    else:
        m_single = re.search(r'(?:Pe\s*=\s*|P\s*=\s*|功率\s*[:：]?\s*|\b)(\d+(?:\.\d+)?)\s*(?:kw|千瓦)\b', t_clean, re.IGNORECASE)
        if m_single:
            params["power"] = float(m_single.group(1))
    return params

def extract_switch_gang_control(text: str) -> Dict[str, Any]:
    """Extract switch gang count and control count (单联/双联/三联/四联, 单控/双控)."""
    params = {}
    if not text:
        return params
    t_clean = to_half_width(text).strip()
    gang_map = {"单联": 1, "一位": 1, "双联": 2, "两位": 2, "三联": 3, "三位": 3, "四联": 4, "四位": 4}
    for k, v in gang_map.items():
        if k in t_clean:
            params["gangs"] = v
            break
            
    if "单控" in t_clean:
        params["control"] = 1
    elif "双控" in t_clean:
        params["control"] = 2
        
    return params

def extract_half_perimeter_and_length(text: str) -> Dict[str, Any]:
    """
    Extract half-perimeter and length intervals (e.g. for dampers, silencers).
    Supports full-width and half-width colons, e.g.:
    "半周长：1001~1500mm" or "半周长: 3501~4000mm*长度: 1501~2000mm"
    """
    res = {}
    if not text:
        return res
    t_clean = to_half_width(text).strip()
    
    # 1. Half-perimeter extraction
    hp_m = re.search(r'半周长\s*[:：]?\s*(\d+)\s*[-~—至到及或]\s*(\d+)\s*(?:mm|m)?', t_clean, re.IGNORECASE)
    if not hp_m:
        hp_m = re.search(r'半周长\s*[:：]?\s*(\d+)\s*(?:mm|m)?', t_clean, re.IGNORECASE)
        if hp_m:
            val = float(hp_m.group(1))
            res["半周长"] = val
            res["半周长_min"] = val
            res["半周长_max"] = val
    else:
        min_v = float(hp_m.group(1))
        max_v = float(hp_m.group(2))
        res["半周长_min"] = min_v
        res["半周长_max"] = max_v
        res["半周长"] = (min_v + max_v) / 2.0

    # 2. Silencer length extraction (second dimension)
    len_m = re.search(r'长度\s*[:：]?\s*(\d+)\s*[-~—至到及或]\s*(\d+)\s*(?:mm|m)?', t_clean, re.IGNORECASE)
    if not len_m:
        len_m = re.search(r'(?:^|[*x×,\s])L\s*[:：=]?\s*(\d+)\s*[-~—至到及或]\s*(\d+)\s*(?:mm|m)?', t_clean, re.IGNORECASE)
    if len_m:
        min_l = float(len_m.group(1))
        max_l = float(len_m.group(2))
        res["长度_min"] = min_l
        res["长度_max"] = max_l
        res["长"] = (min_l + max_l) / 2.0
        res["长度"] = res["长"]
    else:
        single_len = re.search(r'长度\s*[:：]?\s*(\d+)\s*(?:mm|m)?', t_clean, re.IGNORECASE)
        if single_len:
            val_l = float(single_len.group(1))
            res["长度"] = val_l
            res["长"] = val_l
            res["长度_min"] = val_l
            res["长度_max"] = val_l

    return res

def extract_tolerance_3d_box(text: str) -> Optional[Dict[str, Any]]:
    """
    Parse 3D tolerance dimensions, e.g.:
    （1500±150）*（1500±150）*（900±150）mm or 1500±150*1500±150*900±150
    """
    if not text:
        return None
    t_clean = to_half_width(text).strip()
    pattern = r'[（\(]?\s*(\d+(?:\.\d+)?)\s*±\s*(\d+(?:\.\d+)?)\s*[）\)]?\s*[*x×]\s*[（\(]?\s*(\d+(?:\.\d+)?)\s*±\s*(\d+(?:\.\d+)?)\s*[）\)]?\s*[*x×]\s*[（\(]?\s*(\d+(?:\.\d+)?)\s*±\s*(\d+(?:\.\d+)?)\s*[）\)]?'
    m = re.search(pattern, t_clean)
    if m:
        l_b, l_t = float(m.group(1)), float(m.group(2))
        w_b, w_t = float(m.group(3)), float(m.group(4))
        h_b, h_t = float(m.group(5)), float(m.group(6))
        return {
            "长_base": l_b,
            "长_min": l_b - l_t,
            "长_max": l_b + l_t,
            "宽_base": w_b,
            "宽_min": w_b - w_t,
            "宽_max": w_b + w_t,
            "高_base": h_b,
            "高_min": h_b - h_t,
            "高_max": h_b + h_t,
            "长": l_b,
            "宽": w_b,
            "高": h_b,
            "尺寸": f"{int(l_b)}*{int(w_b)}*{int(h_b)}",
            "尺寸_类型": "3d_tolerance_box"
        }
    return None

def extract_parameters_by_domain(domain: str, text: str) -> Dict[str, Any]:
    """Wrapper function to perform config-driven extraction."""
    extractor = ConfigDrivenExtractor(domain)
    attrs = extractor.extract(text)
    
    # Check for half-perimeter and length
    if domain in ["damper", "silencer", "plenum", "air_terminal"] or "半周长" in text or "长度" in text:
        hp_attrs = extract_half_perimeter_and_length(text)
        attrs.update(hp_attrs)
        
    # Check for 3D tolerance box
    if "±" in text:
        tol_attrs = extract_tolerance_3d_box(text)
        if tol_attrs:
            attrs.update(tol_attrs)
            
    return attrs

def reload_extractor_configs():
    global domain_schema, unit_norm_data, unit_classes
    domain_schema = load_json_config("domain_schema.json", {})
    unit_norm_data = load_json_config("unit_norm.json", {})
    unit_classes = unit_norm_data.get("unit_classes", {})

