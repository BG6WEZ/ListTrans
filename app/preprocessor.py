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
        match = re.search(r, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
            
    # Try name lines
    name_match = re.search(r'1\.(?:名称|名称及型号)\s*[:：]\s*(.*)', text)
    if name_match:
        line = name_match.group(1)
        trail_match = re.search(r'([A-Za-z0-9\-]+)$', line)
        if trail_match:
            return trail_match.group(1).strip()
            
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

@lru_cache(maxsize=100000)
def _extract_params_cached(text: str, category_hint: str = "") -> Dict[str, Any]:
    """
    Extract category-specific structured parameters using regex (cached internal).
    """
    params = {}
    if not text:
        return params
        
    # Start with dynamic general structural parsing
    attrs = extract_general_params(text)
    
    # Defaults
    params['model'] = extract_model(text)
    
    # 1. Check if category is Electrical (电气)
    is_electrical = any(k in category_hint or k in text for k in ["电气", "电缆", "导线", "断路器", "开关", "配电", "线缆"])
    # 2. Check if category is Water/Plumbing (给排水/管道)
    is_water = any(k in category_hint or k in text for k in ["给水", "排水", "阀门", "管道", "管件", "紫铜管", "不锈钢管", "塑料管", "蝶阀"])
    
    specific_attrs = {}
    if is_electrical:
        # Extract Electrical parameters: 电压 (V), 电流 (A), 极数 (P), 功率 (kW)
        volt_match = re.search(r'(?:电压|额定电压)?(?:[^\d]*?)(\d+)\s*V', text, re.IGNORECASE)
        if volt_match:
            specific_attrs['电压'] = float(volt_match.group(1))
            
        curr_match = re.search(r'(?:电流|额定电流)(?:[^\d]*?)(\d+(?:\.\d+)?)\s*A(?!\w)', text, re.IGNORECASE)
        if curr_match:
            specific_attrs['电流'] = float(curr_match.group(1))
            
        poles_match = re.search(r'(?:极数)(?:[^\d]*?)(\d+)\s*P\b', text, re.IGNORECASE)
        if poles_match:
            specific_attrs['极数'] = int(poles_match.group(1))
            
        pwr_match = re.search(r'(?:功率|容量|电机功率|电机容量)(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:kW|千瓦)', text, re.IGNORECASE)
        if pwr_match:
            specific_attrs['功率'] = float(pwr_match.group(1))

        # Extract Cable/Conductor parameters: 芯数, 截面积
        cable_matches = re.findall(r'(\d+)\s*[*x×]\s*(\d+(?:\.\d+)?)(?:\s*mm²|\s*mm2)?', text, re.IGNORECASE)
        if cable_matches:
            cores_list = [int(m[0]) for m in cable_matches]
            area_list = [float(m[1]) for m in cable_matches]
            specific_attrs['芯数'] = "+".join(str(c) for c in cores_list)
            specific_attrs['截面积'] = "+".join(str(a) for a in area_list)
        else:
            single_match = re.search(r'(?:BV|BYJ|YJV|WDZ-YJY|BVR|RV|RVV|WDZ-BYJ)?\s*-?\s*(\d+(?:\.\d+)?)\s*(?:mm2|mm²)', text, re.IGNORECASE)
            if not single_match:
                single_match = re.search(r'\b(?:BV|BYJ|YJV|WDZ-YJY|BVR|RV|RVV|WDZ-BYJ)-(\d+(?:\.\d+)?)\b', text, re.IGNORECASE)
            if single_match:
                specific_attrs['芯数'] = "1"
                specific_attrs['截面积'] = str(float(single_match.group(1)))
            
    elif is_water:
        # Extract Water parameters: 管径 (DN), 压力 (MPa)
        dn_match = re.search(r'(?:DN|Ф|Φ|管径)(?:[^\d]*?)(\d+)', text, re.IGNORECASE)
        if dn_match:
            specific_attrs['管径'] = int(dn_match.group(1))
            
        press_match = re.search(r'(?:压力|PN|工作压力)(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:MPa|pn)', text, re.IGNORECASE)
        if press_match:
            specific_attrs['压力'] = float(press_match.group(1))
            
    else:
        # Default/HVAC parameters: 风量, 新风量, 余压, 功率, 制冷量, 制热量, 过滤段, 尺寸
        vol_match = re.search(r'(?:机组|送|排|风机|处理)?风量(?:[^\d]*?)(\d+(?:\s*[-~—至]\s*\d+)?)', text)
        if vol_match:
            specific_attrs['风量'] = parse_number_or_range(vol_match.group(1))
            
        fresh_vol_match = re.search(r'新风量(?:[^\d]*?)(\d+(?:\s*[-~—至]\s*\d+)?)', text)
        if fresh_vol_match:
            specific_attrs['新风量'] = parse_number_or_range(fresh_vol_match.group(1))
            
        pressure_match = re.search(r'(?:机外)?余压(?:[^\d]*?)(\d+(?:\s*[-~—至]\s*\d+)?)', text, re.IGNORECASE)
        if pressure_match:
            specific_attrs['余压'] = parse_number_or_range(pressure_match.group(1))
            
        power_match = re.search(r'(?:功率|风机功率|风机类型及功率|电机功率)(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:kW|千瓦)', text, re.IGNORECASE)
        if power_match:
            specific_attrs['功率'] = float(power_match.group(1))
            
        cooling_match = re.search(r'制冷量(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:kW|千瓦)', text, re.IGNORECASE)
        if cooling_match:
            specific_attrs['制冷量'] = float(cooling_match.group(1))
            
        heating_match = re.search(r'制热量(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:kW|千瓦)', text, re.IGNORECASE)
        if heating_match:
            specific_attrs['制热量'] = float(heating_match.group(1))
            
        dim_match = re.search(r'(\d+\s*[\*x×]\s*\d+(?:\s*[\*x×]\s*\d+)?)', text, re.IGNORECASE)
        if dim_match:
            specific_attrs['尺寸'] = dim_match.group(1).replace('×', '*').replace('x', '*').replace(' ', '').strip()
            
        filter_match = re.search(r'(?:过滤段|过滤器|过滤效率)(?:[^\d]*?)([A-Za-z0-9\+\/\s]+)', text)
        if filter_match:
            filters = re.split(r'[\+\/\s]+', filter_match.group(1).strip())
            specific_attrs['过滤段'] = [f.strip() for f in filters if f.strip()]
        else:
            inline_filters = []
            if "中效" in text:
                inline_filters.append("中效")
            if "高效" in text or "H13" in text or "H14" in text:
                inline_filters.append("高效")
            if "粗效" in text or "初效" in text or "G4" in text:
                inline_filters.append("初效")
            specific_attrs['过滤段'] = inline_filters

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
