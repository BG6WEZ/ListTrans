# -*- coding: utf-8 -*-
import os
import re
import json
from typing import Dict, Any, List, Optional
from functools import lru_cache

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
os.makedirs(CONFIG_DIR, exist_ok=True)

# Helper function to load json configuration
def load_config(filename: str, default_val: Any) -> Any:
    path = os.path.join(CONFIG_DIR, filename)
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default_val, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error writing default config {filename}: {e}")
        return default_val
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config {filename}: {e}")
        return default_val

# Default configurations
DEFAULT_ALIAS = {
    "新风柜": "新风机组",
    "风两": "风量",
    "余亚": "余压",
    "DN8O": "DN80",
    "dn8O": "DN80",
    "排风机组": "排风机"
}

DEFAULT_UNITS = {
    "m3/h": "m³/h",
    "cmh": "m³/h",
    "立方米每小时": "m³/h",
    "kw": "kW",
    "KW": "kW",
    "千瓦": "kW",
    "pa": "Pa",
    "PA": "Pa",
    "帕": "Pa",
    "dn": "DN",
    "Ф": "DN",
    "Φ": "DN",
    "mm": "mm"
}

DEFAULT_STOP_WORDS = ["定制", "升级款", "含支架", "全新", "标准型", "优质", "精品"]

# Load configuration items
alias_dict = load_config("alias_dict.json", DEFAULT_ALIAS)
unit_rules = load_config("unit_rules.json", DEFAULT_UNITS)
stop_words = load_config("stop_words.json", DEFAULT_STOP_WORDS)

def reload_preprocessor_configs():
    """Reload configurations from file at runtime."""
    global alias_dict, unit_rules, stop_words
    alias_dict = load_config("alias_dict.json", DEFAULT_ALIAS)
    unit_rules = load_config("unit_rules.json", DEFAULT_UNITS)
    stop_words = load_config("stop_words.json", DEFAULT_STOP_WORDS)
    _extract_params_cached.cache_clear()

def to_half_width(text: str) -> str:
    """Convert full-width characters to half-width."""
    result = []
    for char in text:
        code = ord(char)
        if code == 0x3000:  # Full-width space
            code = 0x0020
        elif 0xFF01 <= code <= 0xFF5E:  # Full-width characters
            code -= 0xfee0
        result.append(chr(code))
    return "".join(result)

def clean_text(text: str) -> str:
    """
    Standardize text: convert full-width to half-width, strip whitespace,
    apply alias/typo correction, map units, and remove stopwords.
    """
    if not text or not isinstance(text, str):
        return ""
        
    # 1. Full-width to half-width & remove invisible control characters
    text = to_half_width(text)
    text = re.sub(r'[\u0000-\u001f\u007f-\u009f]', '', text)
    
    # 2. Normalize separators (e.g. replace multiple spaces, tabs or raw delimiters)
    text = re.sub(r'\s+', ' ', text)
    
    # 3. Typo/Alias replacement
    for typo, corrected in alias_dict.items():
        text = re.sub(re.escape(typo), corrected, text, flags=re.IGNORECASE)
        
    # 4. Unit normalization
    for raw_unit, standard in unit_rules.items():
        # Match unit with boundary or non-alphanumeric, to avoid partial word match
        # e.g., kw in keyword shouldn't match. But for units, they are usually next to numbers.
        pattern = r'(?<=\d)\s*' + re.escape(raw_unit) + r'(?=\b|[^a-zA-Z]|$)'
        text = re.sub(pattern, standard, text, flags=re.IGNORECASE)
        
    # 5. Stop words /修饰词 removal
    for stop in stop_words:
        text = re.sub(re.escape(stop), '', text)
        
    # Extra clean up of double spaces and commas
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_model(text: str) -> str:
    """
    Extract device model from a text block.
    """
    if not text:
        return ""
        
    model_regexes = [
        r'([A-Z]+-[A-Z]+-[A-Z0-9\-]+)',    # RAU-Q-D301
        r'([A-Z]+-[A-Z0-9]+)',             # PAU-D301 or RAU-301
        r'([A-Z]{2,}\d+[A-Z0-9\-]+)',       # RAUD301
        r'([A-Z]+\d+[\*x×]\d+[\*x×]?\d*)',  # e.g. DN50*2.5
        r'(DN\d+)',                         # e.g. DN100, DN80
        r'(\d+\.\d+\.\d+\.\d+)'             # e.g. 2.22.54.0113
    ]
    
    for r in model_regexes:
        for match in re.finditer(r, text, re.IGNORECASE):
            val = match.group(1).strip()
            val_upper = val.upper()
            if any(val_upper.startswith(prefix) for prefix in ["DN", "DE", "PN", "Ф", "Φ"]):
                continue
            return val
            
    # Try name lines
    name_match = re.search(r'1\.(?:名称|名称及型号)\s*[:：]\s*(.*)', text)
    if name_match:
        line = name_match.group(1)
        trail_match = re.search(r'([A-Za-z0-9\-]+)$', line)
        if trail_match:
            val = trail_match.group(1).strip()
            val_upper = val.upper()
            if not any(val_upper.startswith(prefix) for prefix in ["DN", "DE", "PN", "Ф", "Φ"]):
                return val
            
    return ""

def fts_tokenize(text: str) -> str:
    """
    Generate space-separated tokens for FTS5 indexing and queries.
    Splits Chinese characters character-by-character, keeping numbers/English words intact.
    """
    if not text:
        return ""
        
    tokens = []
    current_token = []
    
    for char in text:
        is_chinese = '\u4e00' <= char <= '\u9fff'
        if is_chinese:
            if current_token:
                tokens.append("".join(current_token))
                current_token = []
            tokens.append(char)
        else:
            # Keep alphanumeric, hyphen, dot, and asterisk
            if char.isalnum() or char in '-.*':
                current_token.append(char)
            else:
                if current_token:
                    tokens.append("".join(current_token))
                    current_token = []
                    
    if current_token:
        tokens.append("".join(current_token))
        
    return " ".join([t for t in tokens if t.strip()])

def parse_number_or_range(match_str: str) -> Optional[float]:
    """Parse a single number or a range like '3000~5000' (takes average)."""
    if not match_str:
        return None
    # Check for range pattern
    range_match = re.search(r'(\d+(?:\.\d+)?)\s*[-~~—至]\s*(\d+(?:\.\d+)?)', match_str)
    if range_match:
        val1 = float(range_match.group(1))
        val2 = float(range_match.group(2))
        return (val1 + val2) / 2.0
        
    # Single number
    num_match = re.search(r'(\d+(?:\.\d+)?)', match_str)
    if num_match:
        return float(num_match.group(1))
    return None

def parse_numbered_headers(text: str) -> Dict[str, str]:
    """Parse first-level numbered headers (e.g. 1.名称, 2.规格)."""
    pattern = r'(?:\b|\n|^)(\d+)\s*[\.．、\s]\s*([^：:，,；;\n\r]{1,15})\s*[:：]'
    matches = list(re.finditer(pattern, text))
    
    sections = {}
    if not matches:
        return sections
        
    for idx, match in enumerate(matches):
        header_name = match.group(2).strip()
        start_idx = match.end()
        end_idx = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        content = text[start_idx:end_idx].strip()
        content = re.sub(r'[\s,，;；]+$', '', content).strip()
        sections[header_name] = content
        
    return sections

def parse_sub_parameters(content: str) -> Dict[str, str]:
    """Parse sub-parameters within a text block separated by delimiters."""
    sub_params = {}
    if not content:
        return sub_params
        
    parts = re.split(r'[,，;；\n\r]', content)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        colon_match = re.search(r'^([^:：]+)\s*[:：]\s*(.*)$', part)
        if colon_match:
            k = colon_match.group(1).strip()
            v = colon_match.group(2).strip()
            if k and len(k) < 15 and not k.isdigit():
                # Clean unit suffix from key (e.g. 送风量m³/h -> 送风量)
                k_clean = re.sub(r'\(.*?\)|（.*?）|\[.*?\]|［.*?］', '', k)
                k_clean = re.sub(r'(?:m³/h|m3/h|kW|W|Pa|MPa|mm|mm2|mm²|m|t|A|V|P)$', '', k_clean, flags=re.IGNORECASE).strip()
                if k_clean:
                    sub_params[k_clean] = v
        else:
            # No colon present - treat as a boolean feature tag if short and not a number
            if len(part) < 15 and not part.isdigit():
                sub_params[part] = part
    return sub_params

def extract_general_params(text: str) -> Dict[str, Any]:
    """Extract structural parameter dimensions dynamically from specifications."""
    attrs = {}
    sections = parse_numbered_headers(text)
    if not sections:
        return parse_sub_parameters(text)

    # Validate that sections look like real Chinese numbered headers.
    # A valid header key should contain at least one CJK character and
    # must not look like a numeric value or unit string (e.g. "新风量m³/h" is
    # a false-positive where 5750 was parsed as the section number).
    def _is_valid_header_key(key: str) -> bool:
        has_cjk = any('\u4e00' <= c <= '\u9fff' for c in key)
        has_unit_suffix = bool(re.search(r'(?:m³|m3|/h|kW|kw|Pa|MPa|mm|mm²|mm2)\s*$', key))
        return has_cjk and not has_unit_suffix

    if not any(_is_valid_header_key(k) for k in sections):
        # All matched "headers" are false positives; fall back to flat parsing
        return parse_sub_parameters(text)
        
    for k, v in sections.items():
        if "工作内容" in k:
            continue
        if ":" in v or "：" in v or ";" in v or "；" in v or "," in v or "，" in v:
            sub = parse_sub_parameters(v)
            if sub:
                for sk, sv in sub.items():
                    attrs[sk] = sv
                continue
        if len(v) <= 40:
            attrs[k] = v
            
    return attrs

def remove_construction_info(text: str) -> str:
    """Strip out installation instruction lines, billing notes, and construction processes."""
    if not text:
        return ""
    # Split by newline or semicolons
    lines = re.split(r'[\n\r；;]', text)
    clean_lines = []
    exclude_keywords = [
        "工作内容", "安装形式", "安装方式", "本体安装", "打标", "加固", "焊压接线", "端子", "接线", "调试",
        "试运转", "就位", "搬运", "运输", "勾缝", "清理", "保护", "单独计算", "单价暂按", "信息价", "计价",
        "配合比", "搅拌", "捣平", "压实", "铺抹", "对缝", "地漏居中", "甲供", "打胶要求", "启动方式",
        "减震垫", "不含垂直", "水平搬运", "基底", "基层", "面层", "施工"
    ]
    for line in lines:
        line_strip = line.strip()
        if not line_strip:
            continue
        # Check if line contains any of the exclude keywords
        if any(kw in line_strip for kw in exclude_keywords):
            continue
        clean_lines.append(line_strip)
    return "\n".join(clean_lines)

@lru_cache(maxsize=100000)
def _extract_params_cached(text: str, category_hint: str = "") -> Dict[str, Any]:
    """
    Extract category-specific structured parameters using regex (cached internal).
    """
    params = {}
    if not text:
        return params

    # Strip construction notes first
    text_clean = remove_construction_info(text)
    
    # Start with dynamic general structural parsing
    attrs = extract_general_params(text_clean)
    
    # Defaults
    params['model'] = extract_model(text_clean)
    
    # Categorization rules
    is_hvac = any(k in category_hint for k in ["暖通", "多联机", "空调", "通风", "风管", "风阀", "风机", "盘管", "风机盘管"]) or (not category_hint and any(k in text_clean for k in ["机组", "风机", "风管", "冷水机组", "风量"]))
    is_elec_strong = any(k in category_hint for k in ["强电", "动力", "照明", "配电", "电气", "电缆", "线缆", "电线"]) or (not category_hint and any(k in text_clean for k in ["电缆", "导线", "断路器", "开关", "配电箱", "Pe=", "配电柜"]))
    is_elec_weak = any(k in category_hint for k in ["弱电", "智能化", "自控", "监控", "消防", "网络", "通信"]) or (not category_hint and any(k in text_clean for k in ["对讲", "呼叫", "总服务器", "液晶呼叫", "分机", "总线"]))
    is_water = (any(k in category_hint for k in ["给排水", "给水", "排水", "水处理", "纯水", "消防水", "管", "管道", "管件", "阀门", "法兰"]) and not any(k in category_hint for k in ["风管", "风阀", "风机", "盘管"])) or (not category_hint and any(k in text_clean for k in ["水盆", "洗手盆", "感应龙头", "水管", "阀门", "蝶阀", "消火栓", "喷淋"]))
    is_gas = any(k in category_hint for k in ["医气", "气体", "医用气体"]) or (not category_hint and any(k in text_clean for k in ["氧气", "吸引", "医用气体", "终端", "脱脂无缝紫铜管"]))
    is_decor = any(k in category_hint for k in ["装饰", "建材", "装修"]) or (not category_hint and any(k in text_clean for k in ["砌块墙", "硫酸钡", "水泥", "地砖", "橡胶卷材", "金属板", "防霉", "涂料", "涂刷"]))

    specific_attrs = {}

    # 1. Strong Electrical Parameters
    if is_elec_strong:
        # Power Pe
        pe_match = re.search(r'(?:Pe|功率|容量|电机功率|电机容量)(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:kW|kw|千瓦)?', text_clean, re.IGNORECASE)
        if pe_match:
            specific_attrs['功率'] = float(pe_match.group(1))
            
        # Voltage
        volt_match = re.search(r'(?:电压|额定电压)?(?:[^\d]*?)(\d+)\s*V', text_clean, re.IGNORECASE)
        if volt_match:
            specific_attrs['电压'] = float(volt_match.group(1))
            
        # Current
        curr_match = re.search(r'(?:电流|额定电流)(?:[^\d]*?)(\d+(?:\.\d+)?)\s*A(?!\w)', text_clean, re.IGNORECASE)
        if curr_match:
            specific_attrs['电流'] = float(curr_match.group(1))
            
        # Poles
        poles_match = re.search(r'(?:极数)(?:[^\d]*?)(\d+)\s*P\b', text_clean, re.IGNORECASE)
        if not poles_match:
            poles_match = re.search(r'\b(\d+)\s*P\b', text_clean, re.IGNORECASE)
        if poles_match:
            specific_attrs['极数'] = int(poles_match.group(1))
 
        # Cable multiplier (Cores & Area)
        cable_matches = re.findall(r'(\d+)\s*[*x×]\s*(\d+(?:\.\d+)?)(?:\s*mm²|\s*mm2)?', text_clean, re.IGNORECASE)
        if cable_matches:
            cores_list = [int(m[0]) for m in cable_matches]
            area_list = [float(m[1]) for m in cable_matches]
            specific_attrs['芯数'] = "+".join(str(c) for c in cores_list)
            specific_attrs['截面积'] = "+".join(str(a) for a in area_list)
        else:
            single_match = re.search(r'(?:BV|BYJ|YJV|WDZ-YJY|BVR|RV|RVV|WDZ-BYJ)?\s*-?\s*(\d+(?:\.\d+)?)\s*(?:mm2|mm²)', text_clean, re.IGNORECASE)
            if not single_match:
                single_match = re.search(r'\b(?:BV|BYJ|YJV|WDZ-YJY|BVR|RV|RVV|WDZ-BYJ)-(\d+(?:\.\d+)?)\b', text_clean, re.IGNORECASE)
            if single_match:
                specific_attrs['芯数'] = "1"
                specific_attrs['截面积'] = str(float(single_match.group(1)))
 
    # 2. Plumbing / Gas / Pure Water Parameters
    elif is_water or is_gas:
        # Check for outer diameter and wall thickness inside parenthesis, e.g. DN65（75*5.5）
        size_match = re.search(r'(?:DN|de|Ф|Φ|管径|口径)\s*(\d+)\s*[（(]\s*(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)\s*[）)]', text_clean, re.IGNORECASE)
        if size_match:
            specific_attrs['管径'] = int(size_match.group(1))
            specific_attrs['外径'] = float(size_match.group(2))
            specific_attrs['壁厚'] = float(size_match.group(3))
        else:
            dn_match = re.search(r'(?:DN|de|Ф|Φ|管径|口径)(?:[^\d]*?)(\d+)', text_clean, re.IGNORECASE)
            if dn_match:
                specific_attrs['管径'] = int(dn_match.group(1))
                
            # Fallback check for outer diameter and wall thickness: e.g. de75*5.5 or De75*5.5
            de_match = re.search(r'(?:de|De)\s*(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)', text_clean, re.IGNORECASE)
            if de_match:
                specific_attrs['外径'] = float(de_match.group(1))
                specific_attrs['壁厚'] = float(de_match.group(2))
            
        # Pressure (MPa / PN)
        press_match = re.search(r'(?:压力|PN|工作压力|设计压力)(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:MPa|pn)?', text_clean, re.IGNORECASE)
        if not press_match:
            press_match = re.search(r'(\d+(?:\.\d+)?)\s*MPa', text_clean, re.IGNORECASE)
        if press_match:
            specific_attrs['压力'] = float(press_match.group(1))

        # Material
        materials_list = [
            "PSP钢塑复合管", "钢塑复合管", "PSP钢塑", "PSP", "无缝钢管", "镀锌钢管", "焊接钢管", "防腐钢管",
            "不锈钢管", "不锈钢", "紫铜管", "铜管", "PPR", "PVC-U", "PVC", "PE", "铸铁管", "铸铁", "黄铜", "碳钢"
        ]
        for mat in materials_list:
            if mat in text_clean:
                specific_attrs['材质'] = mat
                break
            
    # 3. HVAC Parameters
    elif is_hvac:
        vol_match = re.search(r'(?:机组|送|排|风机|处理)?风量(?:[^\d]*?)(\d+(?:\s*[-~—至]\s*\d+)?)', text_clean, re.IGNORECASE)
        if vol_match:
            specific_attrs['风量'] = parse_number_or_range(vol_match.group(1))
            
        fresh_vol_match = re.search(r'新风量(?:[^\d]*?)(\d+(?:\s*[-~—至]\s*\d+)?)', text_clean, re.IGNORECASE)
        if fresh_vol_match:
            specific_attrs['新风量'] = parse_number_or_range(fresh_vol_match.group(1))
            
        pressure_match = re.search(r'(?:机外)?余压(?:[^\d]*?)(\d+(?:\s*[-~—至]\s*\d+)?)', text_clean, re.IGNORECASE)
        if pressure_match:
            specific_attrs['余压'] = parse_number_or_range(pressure_match.group(1))
            
        power_match = re.search(r'(?:功率|风机功率|风机类型及功率|电机功率)(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:kW|kw|千瓦)?', text_clean, re.IGNORECASE)
        if power_match:
            specific_attrs['功率'] = float(power_match.group(1))
            
        cooling_match = re.search(r'制冷量(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:kW|kw|千瓦)?', text_clean, re.IGNORECASE)
        if cooling_match:
            specific_attrs['制冷量'] = float(cooling_match.group(1))
            
        heating_match = re.search(r'制热量(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:kW|kw|千瓦)?', text_clean, re.IGNORECASE)
        if heating_match:
            specific_attrs['制热量'] = float(heating_match.group(1))
            
        dim_match = re.search(r'(\d+\s*[\*x×]\s*\d+(?:\s*[\*x×]\s*\d+)?)', text_clean, re.IGNORECASE)
        if dim_match:
            specific_attrs['尺寸'] = dim_match.group(1).replace('×', '*').replace('x', '*').replace(' ', '').strip()
            
        filter_match = re.search(r'(?:过滤段|过滤器|过滤效率)\s*[:：]?\s*([A-Za-z0-9\+\/\s]+)', text_clean, re.IGNORECASE)
        if filter_match:
            filters = re.split(r'[\+\/\s]+', filter_match.group(1).strip())
            specific_attrs['过滤段'] = [f.strip() for f in filters if f.strip()]
        else:
            inline_filters = []
            if "中效" in text_clean:
                inline_filters.append("中效")
            if "高效" in text_clean or "H13" in text_clean or "H14" in text_clean:
                inline_filters.append("高效")
            if "粗效" in text_clean or "初效" in text_clean or "G4" in text_clean:
                inline_filters.append("初效")
            specific_attrs['过滤段'] = inline_filters

    # 4. Weak Electrical Parameters
    elif is_elec_weak:
        # Ports/Specs
        ports_match = re.search(r'(\d+)\s*(?:口|位|极|针|线)', text_clean)
        if ports_match:
            specific_attrs['接口数量'] = int(ports_match.group(1))
        # Voltage
        volt_match = re.search(r'(?:AC|DC)?\s*(\d+)\s*V', text_clean, re.IGNORECASE)
        if volt_match:
            specific_attrs['电压'] = float(volt_match.group(1))

    # 5. Decoration Parameters
    elif is_decor:
        # Thickness
        thick_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:mm厚|厚|cm厚)', text_clean)
        if thick_match:
            specific_attrs['厚度'] = float(thick_match.group(1))
        # Dimensions
        dim_match = re.search(r'(\d+)\s*[*x×]\s*(\d+)(?:\s*[*x×]\s*(\d+))?', text_clean, re.IGNORECASE)
        if dim_match:
            specific_attrs['尺寸'] = dim_match.group(0).replace('×', '*').replace('x', '*').replace(' ', '').strip()

    # Extract common parameters (power, dimensions) if not already found in specific_attrs
    if '功率' not in specific_attrs:
        power_match = re.search(r'(?:功率|风机功率|风机类型及功率|电机功率)(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:kW|kw|千瓦)?', text_clean, re.IGNORECASE)
        if power_match:
            specific_attrs['功率'] = float(power_match.group(1))
            
    if '尺寸' not in specific_attrs:
        dim_match = re.search(r'(\d+\s*[\*x×]\s*\d+(?:\s*[\*x×]\s*\d+)?)', text_clean, re.IGNORECASE)
        if dim_match:
            specific_attrs['尺寸'] = dim_match.group(1).replace('×', '*').replace('x', '*').replace(' ', '').strip()

    # Merge specific_attrs into attrs (overwriting any raw string attributes with cleaned values)
    for k, v in specific_attrs.items():
        attrs[k] = v

    params['attrs'] = attrs
    return params

def extract_params(text: str, category_hint: str = "") -> Dict[str, Any]:
    """
    Extract category-specific structured parameters using regex (with copy safety for cache).
    """
    res = _extract_params_cached(text, category_hint)
    return {
        "model": res.get("model", ""),
        "attrs": res.get("attrs", {}).copy()
    }

def extract_device_name(text: str, category_name: str = "") -> str:
    """Extract a clean Chinese device name from the text block."""
    if not text:
        return category_name
        
    name_match = re.search(r'1\.(?:名称|名称及型号)\s*[:：]\s*([^\n\r]+)', text)
    if name_match:
        line = name_match.group(1).strip()
        model = extract_model(text)
        if model:
            line = line.replace(model, "").strip()
        line = re.sub(r'[\s\-:：,\.，。]+$', '', line).strip()
        return line if line else category_name
        
    first_line = text.split('\n')[0].strip()
    first_line_clean = re.sub(r'[0-9\.\s[:：]]', '', first_line)
    first_line_clean = re.sub(r'[A-Za-z0-9\-\(\)]', '', first_line_clean).strip()
    
    if len(first_line_clean) > 2:
        return first_line_clean
        
    return category_name
