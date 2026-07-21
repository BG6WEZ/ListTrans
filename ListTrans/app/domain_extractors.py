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
        if "尺寸" in attrs and isinstance(attrs["尺寸"], str):
            attrs["尺寸"] = attrs["尺寸"].replace('×', '*').replace('x', '*').replace(' ', '').strip()
        else:
            dim_match = re.search(r'(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)(?:\s*[*x×]\s*(\d+(?:\.\d+)?))?', text)
            if dim_match:
                val = dim_match.group(0).replace('×', '*').replace('x', '*').replace(' ', '').strip()
                attrs["尺寸"] = val

        # Materials post-processing fallback
        if "材质" not in attrs:
            for mat in ["不锈钢304", "不锈钢316L", "不锈钢", "碳钢", "镀锌钢板", "玻璃钢", "PP", "PPH", "UPVC", "PE", "铜"]:
                if mat in text:
                    attrs["材质"] = mat
                    break

        # 4. Cable specific extraction override to maintain legacy string format (like "5" and "2.5" or "3+2" and "185+95")
        if self.domain in ["cable_power", "cable_control", "wire"]:
            cable_matches = re.findall(r'(\d+)\s*[*x×]\s*(\d+(?:\.\d+)?)(?:\s*mm²|\s*mm2)?', text, re.IGNORECASE)
            if cable_matches:
                cores_list = [int(m[0]) for m in cable_matches]
                area_list = [float(m[1]) for m in cable_matches]
                attrs['芯数'] = "+".join(str(c) for c in cores_list)
                attrs['截面积'] = "+".join(str(a) for a in area_list)
            else:
                single_match = re.search(r'(?:BV|BYJ|YJV|WDZ-YJY|BVR|RV|RVV|WDZ-BYJ)?\s*-?\s*(\d+(?:\.\d+)?)\s*(?:mm2|mm²)', text, re.IGNORECASE)
                if not single_match:
                    single_match = re.search(r'\b(?:BV|BYJ|YJV|WDZ-YJY|BVR|RV|RVV|WDZ-BYJ)-(\d+(?:\.\d+)?)\b', text, re.IGNORECASE)
                if single_match:
                    attrs['芯数'] = "1"
                    attrs['截面积'] = str(float(single_match.group(1)))

        return attrs

def extract_parameters_by_domain(domain: str, text: str) -> Dict[str, Any]:
    """Wrapper function to perform config-driven extraction."""
    extractor = ConfigDrivenExtractor(domain)
    return extractor.extract(text)

def reload_extractor_configs():
    global domain_schema, unit_norm_data, unit_classes
    domain_schema = load_json_config("domain_schema.json", {})
    unit_norm_data = load_json_config("unit_norm.json", {})
    unit_classes = unit_norm_data.get("unit_classes", {})
