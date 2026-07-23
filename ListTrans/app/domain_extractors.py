# -*- coding: utf-8 -*-
import os
import json
import re
from typing import Dict, Any, List, Tuple, Optional
from app.normalizer import clean_text_standard, load_json_config, to_half_width, convert_de_to_dn

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")

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
                    # Take the first capture group
                    val_found = match.group(1).strip()
            
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
        "WDZN-YJV22", "WDZN-YJV", "WDZN-BYJ", "WDZ-YJY", "WDZ-BYJ", "WDZ-RYJS", "WDZ-RYJ",
        "NH-YJV22", "NH-YJV", "NH-BV", "ZR-YJV22", "ZR-YJV", "ZR-BV", "ZR-BVR",
        "YJV22", "YJY22", "YJV", "YJY", "KVVP2-22", "KVVP2", "KVVP", "KVV", "RVVP", "RVV",
        "BYJ", "BVR", "BV", "RYJS", "RYJ"
    ]
    for m in models:
        if m in t_clean:
            attrs["型号"] = m
            break
            
    # 2. Cable cores & section matching (e.g., 4*50+1*25, 3*2.5, 5*10, 1*240, 2.5)
    # Check compound pattern: 4*50+1*25 or 3*120+2*70
    comp_match = re.search(r'(\d+)\s*\*\s*(\d+(?:\.\d+)?)\s*\+\s*(\d+)\s*\*\s*(\d+(?:\.\d+)?)', t_clean)
    if comp_match:
        attrs["芯数"] = f"{comp_match.group(1)}+{comp_match.group(3)}"
        attrs["截面积"] = f"{comp_match.group(2)}+{comp_match.group(4)}"
        attrs["主芯数"] = int(comp_match.group(1))
        attrs["主截面"] = float(comp_match.group(2))
        attrs["零芯数"] = int(comp_match.group(3))
        attrs["零截面"] = float(comp_match.group(4))
    else:
        # Standard pattern: 3*2.5, 4*16, 5*6
        std_match = re.search(r'(\d+)\s*\*\s*(\d+(?:\.\d+)?)', t_clean)
        if std_match:
            attrs["芯数"] = str(int(std_match.group(1)))
            attrs["截面积"] = str(float(std_match.group(2)))
            attrs["主芯数"] = int(std_match.group(1))
            attrs["主截面"] = float(std_match.group(2))
        else:
            # Single core fallback: BV-2.5, 2.5mm²
            s_match = re.search(r'(?:BV|BYJ|YJV|WDZ-YJY|BVR|RV|RVV|WDZ-BYJ)?\s*-?\s*(\d+(?:\.\d+)?)\s*(?:MM2|MM²|平方)?', t_clean)
            if s_match:
                try:
                    val = float(s_match.group(1))
                    if val in [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300, 400, 500]:
                        attrs["芯数"] = "1"
                        attrs["截面积"] = str(val)
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
        "S30408", "304L", "316L", "304", "不锈钢", "无缝钢管", "镀锌钢管", "镀锌方管", "镀锌",
        "PPH", "PPR", "UPVC", "PVC", "PE", "紫铜管", "紫铜", "铜管", "铸铁"
    ]
    for m in mats:
        if m.lower() in t_clean.lower():
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
            phi_match = re.search(r'[φФΦ]\s*(\d+(?:\.\d+)?)', t_clean)
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
        th_match = re.search(r'(\d+(?:\.\d+)?)\s*mm\s*(?:厚)?', t_clean, re.IGNORECASE)
        if not th_match:
            th_match = re.search(r'(?:厚度|厚)\s*[:：]?\s*(\d+(?:\.\d+)?)', t_clean, re.IGNORECASE)
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
    """Extract line pipe & conduit parameters (JDG, KBG, SC, PVC, diameter, thickness)."""
    params = {}
    if not text:
        return params
    t_clean = to_half_width(text).strip()
    
    # 1. Combined Model & Diameter (e.g. JDG20 -> model: JDG, dn: 20)
    jdg_match = re.search(r'\b(JDG|KBG|SC|PVC-U|PVC|KJG|TC)\s*(\d+(?:\.\d+)?)\b', t_clean, re.IGNORECASE)
    if jdg_match:
        params["model"] = jdg_match.group(1).upper()
        params["dn"] = float(jdg_match.group(2))
    else:
        model_match = re.search(r'\b(JDG|KBG|SC|PVC-U|PVC|KJG|TC)\b', t_clean, re.IGNORECASE)
        if model_match:
            params["model"] = model_match.group(1).upper()

        size_match = re.search(r'(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)', t_clean)
        if size_match:
            params["dn"] = float(size_match.group(1))
            params["wall_thickness"] = float(size_match.group(2))
        else:
            dn_match = re.search(r'(?:DN|Φ|de|Φ)?\s*(\d+(?:\.\d+)?)', t_clean)
            if dn_match:
                params["dn"] = float(dn_match.group(1))

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

def extract_parameters_by_domain(domain: str, text: str) -> Dict[str, Any]:
    """Wrapper function to perform config-driven extraction."""
    extractor = ConfigDrivenExtractor(domain)
    return extractor.extract(text)

def reload_extractor_configs():
    global domain_schema, unit_norm_data, unit_classes
    domain_schema = load_json_config("domain_schema.json", {})
    unit_norm_data = load_json_config("unit_norm.json", {})
    unit_classes = unit_norm_data.get("unit_classes", {})

