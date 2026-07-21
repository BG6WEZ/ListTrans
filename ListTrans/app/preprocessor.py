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
    Standardize text: delegate to normalizer.py
    """
    from app.normalizer import clean_text_standard
    return clean_text_standard(text)

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
            # Check if it looks like a concatenated KeyValue without a colon, e.g. "机外余压1100pa" or "DN100"
            concat_match = re.search(r'^([^\d]+)\s*(\d+(?:\.\d+)?\s*(?:Pa|kPa|MPa|m³/h|m3/h|kW|kw|W|w|mm|m|V|A|HP|db|dB|l/s|CFM)?)$', part, re.IGNORECASE)
            if concat_match:
                k = concat_match.group(1).strip()
                v = concat_match.group(2).strip()
                k_clean = re.sub(r'\(.*?\)|（.*?）|\[.*?\]|［.*?］', '', k)
                k_clean = re.sub(r'(?:m³/h|m3/h|kW|W|Pa|MPa|mm|mm2|mm²|m|t|A|V|P)$', '', k_clean, flags=re.IGNORECASE).strip()
                if k_clean:
                    sub_params[k_clean] = v
            else:
                # No colon present - treat as a boolean feature tag if short, contains no digits, and not a number
                if len(part) < 15 and not any(c.isdigit() for c in part):
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
    Extract category-specific structured parameters using domain router and config-driven extractors.
    """
    from app.domain_router import route_domain
    from app.normalizer import clean_text_standard
    from app.domain_extractors import extract_parameters_by_domain

    if not text:
        return {"model": "", "attrs": {}}

    # Clean construction info on raw text to preserve newlines
    text_no_const = remove_construction_info(text)
    text_clean = clean_text_standard(text_no_const)
    
    # 1. Run legacy dynamic general parameter extraction on raw text (preserving original casing/units)
    attrs = extract_general_params(text_no_const)
    
    # 2. Route to domain
    name_extracted = extract_device_name(text_no_const, category_hint)
    domain = route_domain(category_hint, name_extracted, text_clean, category_hint)
    
    # 3. Extract domain-specific attributes using the routed domain's extractor
    domain_attrs = extract_parameters_by_domain(domain, text_clean)
    for k, v in domain_attrs.items():
        attrs[k] = v

    # Post-process all extracted keys to cast to float/int using domain schema definitions
    from app.domain_extractors import domain_schema, parse_numeric_complex
    domain_config = domain_schema.get(domain, {})
    fields = domain_config.get("must", []) + domain_config.get("should", [])
    for field in fields:
        key = field["key"]
        ftype = field.get("type", "str")
        if domain in ["cable_power", "cable_control", "wire"] and key in ["芯数", "截面积"]:
            continue
        if key in attrs and isinstance(attrs[key], str):
            if ftype == "float" or ftype == "int":
                parsed = parse_numeric_complex(attrs[key])
                if "val" in parsed:
                    attrs[key] = int(parsed["val"]) if ftype == "int" else parsed["val"]
                    if "min_val" in parsed and "max_val" in parsed:
                        attrs[f"{key}_min"] = parsed["min_val"]
                        attrs[f"{key}_max"] = parsed["max_val"]
                    if "op" in parsed and "base_val" in parsed:
                        attrs[f"{key}_op"] = parsed["op"]
                        attrs[f"{key}_base"] = parsed["base_val"]
    
    # 4. Extract model
    model = extract_model(text_clean)

    # 5. Fallback check for common parameters: 功率 and 尺寸
    if "功率" not in attrs:
        power_match = re.search(r'(?:功率|电机功率|风机功率|单机功率)(?:[^\d]*?)(\d+(?:\.\d+)?)\s*(?:kW|kw|千瓦|W|瓦|KW)?', text_clean, re.IGNORECASE)
        if power_match:
            try:
                attrs["功率"] = float(power_match.group(1))
            except Exception:
                pass
    if "尺寸" not in attrs:
        dim_match = re.search(r'(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)(?:\s*[*x×]\s*(\d+(?:\.\d+)?))?', text_clean)
        if dim_match:
            attrs["尺寸"] = dim_match.group(0).replace('×', '*').replace('x', '*').replace(' ', '').strip()

    # 6. Clean cable properties false-positives for non-cable domains
    if domain not in ["cable_power", "cable_control", "wire"]:
        attrs.pop("芯数", None)
        attrs.pop("截面积", None)
        attrs.pop("芯数_min", None)
        attrs.pop("芯数_max", None)
        attrs.pop("截面积_min", None)
        attrs.pop("截面积_max", None)

    return {
        "model": model,
        "attrs": attrs
    }

def extract_params(text: str, category_hint: str = "") -> Dict[str, Any]:
    """
    Extract category-specific structured parameters (with copy safety for cache and frontend aliases compatibility).
    """
    import copy
    res = _extract_params_cached(text, category_hint)
    attrs = copy.deepcopy(res.get("attrs", {}))
    
    # Frontend compatibility alias mapping (Double-write keys to support old frontend columns)
    if "机外余压" in attrs:
        attrs["余压"] = attrs["机外余压"]
    if "管径" in attrs:
        attrs["口径"] = attrs["管径"]
    if "过滤等级" in attrs:
        attrs["过滤段"] = [attrs["过滤等级"]] if isinstance(attrs["过滤等级"], str) else attrs["过滤等级"]

    return {
        "model": res.get("model", ""),
        "attrs": attrs
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
        
    spec_match = re.search(r'(?:材质、规格|材质规格|设备名称)\s*[:：]\s*([^\n\r，,；;]+)', text)
    if spec_match:
        line = spec_match.group(1).strip()
        model = extract_model(text)
        if model:
            line = line.replace(model, "").strip()
        line = re.sub(r'(?:DN\d+|de\d+|\d+寸|\d+mm|型|系列).*$', '', line, flags=re.IGNORECASE).strip()
        line = re.sub(r'[\s\-:：,\.，。]+$', '', line).strip()
        if len(line) > 2:
            return line
            
    first_line = text.split('\n')[0].strip()
    first_segment = re.split(r'[,，;；]', first_line)[0].strip()
    first_line_clean = re.sub(r'[0-9\.\s[:：]]', '', first_segment)
    first_line_clean = re.sub(r'[A-Za-z0-9\-\(\)/]', '', first_line_clean).strip()
    
    if len(first_line_clean) > 2:
        # Check if the extracted segment looks like a property description rather than a device name
        exclude_property_words = ["风量", "流量", "扬程", "功率", "压力", "口径", "管径", "尺寸", "规格", "型号", "材质", "温度", "电压", "电流", "安装", "部位", "介质", "工作"]
        if not any(w in first_line_clean for w in exclude_property_words):
            return first_line_clean
            
    return category_name
