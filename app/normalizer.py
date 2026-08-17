# -*- coding: utf-8 -*-
import os
import re
import json
from typing import Dict, Any, List, Optional, Tuple

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

alias_rx, alias_map = None, {}
material_rx, material_map = None, {}
connection_rx, connection_map = None, {}
flags_rx, flags_map = None, {}
unit_rx_list = []

def compile_configs():
    global alias_rx, alias_map, material_rx, material_map, connection_rx, connection_map, flags_rx, flags_map, unit_rx_list
    
    def _build_compiled_regex(d: dict):
        if not d:
            return None, {}
        lower_map = {k.lower(): v for k, v in d.items()}
        sorted_keys = sorted(d.keys(), key=len, reverse=True)
        pattern = "|".join(map(re.escape, sorted_keys))
        rx = re.compile(pattern, re.IGNORECASE)
        return rx, lower_map

    alias_rx, alias_map = _build_compiled_regex(alias_dict)
    material_rx, material_map = _build_compiled_regex(material_dict)
    connection_rx, connection_map = _build_compiled_regex(connection_dict)
    flags_rx, flags_map = _build_compiled_regex(flags_dict)
    
    unit_rx_list = []
    for raw_unit, standard in unit_rules.items():
        pattern = re.compile(r'(?<=\d)\s*' + re.escape(raw_unit) + r'(?=\b|[^a-zA-Z]|$)', re.IGNORECASE)
        unit_rx_list.append((pattern, standard))

# Build initial compiled regexes
compile_configs()

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

def clean_text_standard(text: str, discipline: Optional[str] = None, category: Optional[str] = None) -> str:
    """
    Apply character cleaning, full-to-half conversion, lowercase normalization,
    and alias synonym replacements on raw text.
    """
    if not text or not isinstance(text, str):
        return ""

    # 0. Apply dynamic LLM DSL cleaning rules on raw structured input (e.g. multi-line manifests)
    try:
        from app.llm_rule_engine import LLMRuleManager, DynamicRuleInterpreter, RuleHitStatsTracker
        active_rules = LLMRuleManager.get_active_rules()
        
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
            scope_lvl = r.get("scope_level", "universal")
            scope_tgt = r.get("scope_target", "")
            if scope_lvl == "discipline" and scope_tgt and discipline and not scope_matches(scope_tgt, [discipline]):
                continue
            if scope_lvl == "category" and scope_tgt and category and not scope_matches(scope_tgt, [category]):
                continue
            dsl = r.get("parsed_dsl", {})
            cleaning_rules = dsl.get("cleaning_rules", [])
            if cleaning_rules:
                text_before = text
                text, actions = DynamicRuleInterpreter.apply_cleaning_rules(text, cleaning_rules)
                if text != text_before and r.get("rule_id"):
                    RuleHitStatsTracker().record_hit(r["rule_id"])
    except Exception:
        pass

    # 1. Full-width to half-width & remove invisible control characters (except \n and \r)
    text = to_half_width(text)
    text = re.sub(r'[\u0000-\u0009\u000b\u000c\u000e-\u001f\u007f-\u009f]', '', text)

    # 2. Normalize spaces (horizontal only to preserve newlines)
    text = re.sub(r'[ \t]+', ' ', text)

    # 3. Apply alias dictionary typos replacement
    if alias_rx:
        text = alias_rx.sub(lambda m: alias_map.get(m.group(0).lower(), m.group(0)), text)

    # 4. Standardize materials, connections and flags using synonym dictionaries
    if material_rx:
        text = material_rx.sub(lambda m: material_map.get(m.group(0).lower(), m.group(0)), text)
    if connection_rx:
        text = connection_rx.sub(lambda m: connection_map.get(m.group(0).lower(), m.group(0)), text)
    if flags_rx:
        text = flags_rx.sub(lambda m: flags_map.get(m.group(0).lower(), m.group(0)), text)

    # 5. Clean up units in raw text using unit_rules mapping
    for pattern, standard in unit_rx_list:
        text = pattern.sub(standard, text)

    # 6. Remove unit labels from parameter names to avoid regex matching digits inside units (e.g. 风量m3/h -> 风量)
    header_unit_pattern = r'(风量|新风量|处理风量|循环风量|余压|机外余压|静压|风压|制冷量|冷量|制热量|加湿量|功率|配电功率|管径|口径|DN|De|截面积)\s*[\(\uff08]?\s*(?:m³/h|m3/h|kW|kw|W|w|Pa|pa|kg/h|mm|mm²|mm2)\s*[\)\uff09]?'
    text = re.sub(header_unit_pattern, r'\1', text, flags=re.IGNORECASE)

    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'^(?:医用|医气|医院专用)', '', text.strip())

    # 7. Apply dynamic LLM DSL cleaning rules (post-normalization pass)
    try:
        from app.llm_rule_engine import LLMRuleManager, DynamicRuleInterpreter, RuleHitStatsTracker
        active_rules = LLMRuleManager.get_active_rules()
        for r in active_rules:
            scope_lvl = r.get("scope_level", "universal")
            scope_tgt = r.get("scope_target", "")
            if scope_lvl == "discipline" and scope_tgt and discipline and scope_tgt != discipline:
                continue
            if scope_lvl == "category" and scope_tgt and category and scope_tgt != category:
                continue
            dsl = r.get("parsed_dsl", {})
            cleaning_rules = dsl.get("cleaning_rules", [])
            if cleaning_rules:
                text_before = text
                text, actions = DynamicRuleInterpreter.apply_cleaning_rules(text, cleaning_rules)
                if text != text_before and r.get("rule_id"):
                    RuleHitStatsTracker().record_hit(r["rule_id"])
    except Exception:
        pass

    return text.strip()

def reload_normalizer_configs():
    global alias_dict, material_dict, connection_dict, flags_dict, unit_rules
    alias_dict = load_json_config("alias_dict.json", {})
    material_dict = load_json_config("material_dict.json", {})
    connection_dict = load_json_config("connection_dict.json", {})
    flags_dict = load_json_config("flags_dict.json", {})
    unit_rules = load_json_config("unit_rules.json", {})
    compile_configs()

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

