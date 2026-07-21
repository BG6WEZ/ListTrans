# -*- coding: utf-8 -*-
import os
import re
import json
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

# Load synonym & normalizer dictionaries
alias_dict = load_json_config("alias_dict.json", {})
material_dict = load_json_config("material_dict.json", {})
connection_dict = load_json_config("connection_dict.json", {})
flags_dict = load_json_config("flags_dict.json", {})
unit_rules = load_json_config("unit_rules.json", {})

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

def clean_text_standard(text: str) -> str:
    """
    Apply character cleaning, full-to-half conversion, lowercase normalization,
    and alias synonym replacements on raw text.
    """
    if not text or not isinstance(text, str):
        return ""

    # 1. Full-width to half-width & remove invisible control characters (except \n and \r)
    text = to_half_width(text)
    text = re.sub(r'[\u0000-\u0009\u000b\u000c\u000e-\u001f\u007f-\u009f]', '', text)

    # 2. Normalize spaces (horizontal only to preserve newlines)
    text = re.sub(r'[ \t]+', ' ', text)

    # 3. Apply alias dictionary typos replacement
    for typo, corrected in alias_dict.items():
        text = re.sub(re.escape(typo), corrected, text, flags=re.IGNORECASE)

    # 4. Standardize materials, connections and flags using synonym dictionaries
    for syn_word, std_word in material_dict.items():
        text = re.sub(re.escape(syn_word), std_word, text, flags=re.IGNORECASE)
    for syn_word, std_word in connection_dict.items():
        text = re.sub(re.escape(syn_word), std_word, text, flags=re.IGNORECASE)
    for syn_word, std_word in flags_dict.items():
        text = re.sub(re.escape(syn_word), std_word, text, flags=re.IGNORECASE)

    # 5. Clean up units in raw text using unit_rules mapping
    for raw_unit, standard in unit_rules.items():
        pattern = r'(?<=\d)\s*' + re.escape(raw_unit) + r'(?=\b|[^a-zA-Z]|$)'
        text = re.sub(pattern, standard, text, flags=re.IGNORECASE)

    # 6. Remove unit labels from parameter names to avoid regex matching digits inside units (e.g. 风量m3/h -> 风量)
    header_unit_pattern = r'(风量|新风量|处理风量|循环风量|余压|机外余压|静压|风压|制冷量|冷量|制热量|加湿量|功率|配电功率|管径|口径|DN|De|截面积)\s*[\(\uff08]?\s*(?:m³/h|m3/h|kW|kw|W|w|Pa|pa|kg/h|mm|mm²|mm2)\s*[\)\uff09]?'
    text = re.sub(header_unit_pattern, r'\1', text, flags=re.IGNORECASE)

    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def reload_normalizer_configs():
    global alias_dict, material_dict, connection_dict, flags_dict, unit_rules
    alias_dict = load_json_config("alias_dict.json", {})
    material_dict = load_json_config("material_dict.json", {})
    connection_dict = load_json_config("connection_dict.json", {})
    flags_dict = load_json_config("flags_dict.json", {})
    unit_rules = load_json_config("unit_rules.json", {})

def convert_de_to_dn(val: float, text: str) -> float:
    """
    Convert outer diameter De/de to nominal diameter DN according to water industry standards.
    E.g., De32 -> DN25.
    """
    if not text:
        return val
    # Check if De/de prefix exists before the numeric value
    val_str = str(int(val) if val.is_integer() else val)
    pattern = r'(?:De|de)\s*' + re.escape(val_str)
    if re.search(pattern, text):
        de_to_dn_map = {
            16: 10,
            20: 15,
            25: 20,
            32: 25,
            40: 32,
            50: 40,
            63: 50,
            75: 65,
            90: 80,
            110: 100,
            160: 150,
            200: 200,
            225: 200,
            250: 250,
            315: 300
        }
        val_int = int(round(val))
        return float(de_to_dn_map.get(val_int, val))
    return val

