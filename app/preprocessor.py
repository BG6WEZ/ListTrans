# -*- coding: utf-8 -*-
import os
import re
import json
from typing import Dict, Any, List, Optional
from functools import lru_cache

CONFIG_DIR = os.getenv("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))
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
    "mm": "mm"
}

DEFAULT_STOP_WORDS = ["定制", "升级款", "含支架", "全新", "标准型", "优质", "精品"]

# Load configuration items
alias_dict = load_config("alias_dict.json", DEFAULT_ALIAS)
unit_rules = load_config("unit_rules.json", DEFAULT_UNITS)
stop_words = load_config("stop_words.json", DEFAULT_STOP_WORDS)

def reload_preprocessor_configs():
    """Reload configurations from file at runtime."""
    global CONFIG_DIR, alias_dict, unit_rules, stop_words
    CONFIG_DIR = os.getenv("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))
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

def clean_text(
    text: str,
    discipline: Optional[str] = None,
    category: Optional[str] = None,
    *,
    rule_context=None,
) -> str:
    """
    Standardize text: delegate to normalizer.py with optional RuleContext.
    """
    from app.normalizer import clean_text_standard
    return clean_text_standard(
        text,
        discipline=discipline,
        category=category,
        rule_context=rule_context,
    )

# V018-M0：材质牌号/项目编码/标准编码不得被误抽为产品型号
_MATERIAL_GRADE_MODEL_RE = re.compile(
    r'^(?:(?:SS|SUS|S)\d{3,5}[A-Z]?|Q\d{3}[A-Z]?|06CR\d+NI\d+|304L?|316L|'
    r'UPVC|CPVC|HDPE|LDPE|PPR|PPH|PVC|PTFE|EPDM|NBR)$',
    re.IGNORECASE,
)
_PROJECT_OR_STANDARD_CODE_RE = re.compile(
    r'^(?:\d{12}|(?:GT|XM|PRJ|BOQ)-\d+|\d{1,2}\.\d{2}\.\d{2}\.\d{4})$',
    re.IGNORECASE,
)


def _is_non_model_token(val: str) -> bool:
    if not val:
        return True
    val_upper = val.strip().upper()
    if any(val_upper.startswith(prefix) for prefix in ["DN", "DE", "PN", "Ф", "Φ"]):
        return True
    if re.fullmatch(r'(MM|CM|M|KG|KW|KVA|PA|KPA|MPA|V|A|HZ|DB|M3|M2|ML|L|T|GB|MB)\d+', val_upper):
        return True
    if re.fullmatch(r'(AC|DC)\d+(?:V|A|W|HZ)?', val_upper):
        return True
    if re.fullmatch(r'(RJ|BNC|HDMI|USB|DP|DVI|VGA|SDI|XLR|TRS|SMA|N|F|TNC|MCX|MMCX|SMP|2M|3M|4M|5M|6M)\d*', val_upper):
        return True
    if _MATERIAL_GRADE_MODEL_RE.fullmatch(val_upper):
        return True
    if _PROJECT_OR_STANDARD_CODE_RE.fullmatch(val_upper):
        return True
    if is_device_tag(val):
        return True
    return False


def extract_model(text: str) -> str:
    """
    Extract device model from a text block.
    """
    if not text:
        return ""
        
    model_regexes = [
        # V017-M1 / M1-C02：电缆/电线型号主体优先。形如 WDZYJY-5*10、WDZB-YJY-4*120+1*70、
        # WDZB-BYJR-10mm²、YJV-4*120+1*70 的"型号-芯数*截面/规格"形式，型号主体是连字符前
        # 的字母序列（WDZYJY / WDZB-YJY / WDZB-BYJR / YJV）。若不优先提取，会被下方
        # `([A-Z]+-[A-Z0-9]+)` 错误截断为 WDZYJY-5 / WDZB-BYJR-10mm（`*`/`²` 阻断），破坏
        # 型号族泛化召回（M0 曾以 cable_params 的 YJY/BYJR 召回命中）。
        r'([A-Z]{2,}(?:-[A-Z0-9]+)*?)(?=\s*-\d+(?:[\*x×]|MM|mm|M2|m2|²)|\s+\d+[\*x×])',
        r'([A-Z]+-[A-Z]+-[A-Z0-9\-]+)',    # RAU-Q-D301
        r'([A-Z]+-[A-Z0-9]+)',             # PAU-D301 or RAU-301
        r'([A-Z]{2,}\d+[A-Z0-9\-]+)',       # RAUD301
        r'([A-Z]+\d+[\*x×]\d+[\*x×]?\d*)',  # e.g. DN50*2.5
        r'(DN\d+)',                         # e.g. DN100, DN80
    ]
    
    for r in model_regexes:
        for match in re.finditer(r, text, re.IGNORECASE):
            val = match.group(1).strip()
            if _is_non_model_token(val):
                continue
            return val
            
    # Try name lines
    name_match = re.search(r'1\.(?:名称|名称及型号)\s*[:：]\s*(.*)', text)
    if name_match:
        line = name_match.group(1)
        trail_match = re.search(r'([A-Za-z0-9\-]+)$', line)
        if trail_match:
            val = trail_match.group(1).strip()
            if not _is_non_model_token(val):
                return val
            
    return ""

# V017-M1 / M1-C02：工程设备位号与箱号前缀（单一事实源，供 extract_model 排除）。
# 仅登记任务书明确列出的位号前缀（AHU/PAU）与电气箱号前缀（与 matcher BOX_PREFIXES 对齐）；
# 其余 HVAC 缩写（RAU/FCU/MAU/RCU 等）可能同时是真实产品型号（如 RAU-Q-D301），
# 不列入位号前缀，避免误伤型号提取。
DEVICE_TAG_PREFIXES = (
    "AHU", "PAU",
    # 电气箱号前缀（与 matcher BOX_PREFIXES 对齐）
    "AP", "AL", "ALE", "AT", "ACP", "AX", "ATD", "AFC",
)


def is_device_tag(value: str) -> bool:
    """判断形如 AHU-J-01 / PAU-D301 / AP-1 / AL-2 的工程设备位号/箱号。

    规则：大写字母前缀命中设备/箱号前缀集合，且紧随连字符+数字/字母编号。
    """
    if not value:
        return False
    v = value.strip().upper()
    if "-" not in v:
        return False
    prefix = v.split("-")[0]
    if prefix not in DEVICE_TAG_PREFIXES:
        return False
    # 位号形式：前缀 + 连字符 + 编号（数字或字母数字），如 AHU-J-01、PAU-D301、AP-1
    remainder = v[len(prefix):]
    return bool(re.match(r'^-[A-Z0-9]+(?:-[A-Z0-9]+)*$', remainder))


def extract_device_tag(text: str) -> str:
    """从文本提取工程设备位号/箱号（归入 tag_id 族，非产品型号）。"""
    if not text:
        return ""
    for m in re.finditer(r'\b([A-Z]{2,}(?:-[A-Z0-9]+)+)\b', text, re.IGNORECASE):
        val = m.group(1).strip()
        if is_device_tag(val):
            return val
    return ""


@lru_cache(maxsize=20000)
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
                # Clean unit suffix and section numbers from key (e.g. 3.材质、规格 -> 材质)
                k_clean = re.sub(r'^\d+[\.．、\s]*', '', k).strip()
                k_clean = re.sub(r'\(.*?\)|（.*?）|\[.*?\]|［.*?］', '', k_clean).strip()
                k_clean = re.sub(r'(?:m³/h|m3/h|kW|kw|W|w|Pa|pa|MPa|kPa|mm|mm2|mm²|m|t|A|V|P)$', '', k_clean, flags=re.IGNORECASE).strip()
                
                k_label = k_clean
                if "材质" in k_clean:
                    k_clean = "材质"
                elif "半周长" in k_clean:
                    k_clean = "半周长"
                elif "长度" in k_clean and "工作" not in k_clean:
                    k_clean = "长度"
                elif k_clean in ["机外余压", "机外静压", "全压", "风压", "余压", "静压"]:
                    k_clean = "机外余压"
                elif k_clean in ["风量", "处理风量"]:
                    k_clean = "风量"
                elif "功率" in k_clean:
                    k_clean = "功率"

                if k_clean:
                    sub_params[k_clean] = v
                if k_label and k_label != k_clean:
                    sub_params[k_label] = v
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
                # V017-M1 / M1-C01：无冒号冗余短句不再写成自引用键（sub_params[part]=part）。
                # 这类"值等于键"的键无任何信息量，属自由文本噪声，会污染 attrs 并进入
                # matcher processed_keys 参与属性打分。仅保留有实际语义的材质关键词归并，
                # 其余短句直接丢弃（由上层白名单隔离兜底追溯）。
                if len(part) < 15 and not any(c.isdigit() for c in part):
                    if any(mat in part for mat in ["钢", "铁", "铜", "铝", "塑", "PVC", "PPR", "PE", "UPVC", "HDPE", "不锈钢", "合金"]):
                        sub_params["材质"] = part
                    # 无信息量短句：不写入自引用键
    return sub_params

def extract_general_params(text: str) -> Dict[str, Any]:
    """Extract structural parameter dimensions dynamically from specifications."""
    attrs = {}
    sections = parse_numbered_headers(text)
    if not sections:
        return parse_sub_parameters(text)

    # Validate that sections look like real Chinese numbered headers.
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
        hk = "材质" if "材质" in k else k
        if ":" in v or "：" in v or ";" in v or "；" in v or "," in v or "，" in v:
            sub = parse_sub_parameters(v)
            if sub:
                for sk, sv in sub.items():
                    attrs[sk] = sv
                continue
        if len(v) <= 40:
            attrs[hk] = v
            
    return attrs

def remove_construction_info(text: str) -> str:
    """Strip out installation instruction lines, billing notes, and construction processes."""
    if not text:
        return ""
    # Split by newline or semicolons
    lines = re.split(r'[\n\r；;]', text)
    clean_lines = []
    exclude_keywords = [
        "工作内容", "施工内容", "安装形式", "安装方式", "本体安装", "打标", "加固", "焊压接线", "端子", "接线", "调试",
        "试运转", "就位", "搬运", "运输", "清理", "保护", "单独计算", "单价暂按", "信息价", "计价",
        "配合比", "搅拌", "捣平", "压实", "对缝", "地漏居中", "打胶要求", "启动方式",
        "减震垫", "不含垂直", "水平搬运", "施工"
    ]
    for line in lines:
        line_strip = line.strip()
        if not line_strip:
            continue
        # Always strip explicit work content headers
        if re.search(r'^\d*[\.．、\s]*(?:工作内容|施工内容|工作包含|安装方式|施工要求)', line_strip):
            continue
        # Never filter out lines that contain explicit spec/parameter markers or dimensions
        has_spec_marker = any(kw in line_strip for kw in ["规格", "型号", "材质", "尺寸", "参数", "DN", "m3/h", "m³/h", "kW", "kw", "Pa", "kPa", "mm"])
        has_dimension = bool(re.search(r'\d+(?:\.\d+)?\s*[*x×]\s*\d+', line_strip))
        
        if not (has_spec_marker or has_dimension):
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
    from app.rule_context import RuleContext

    if not text:
        return {"model": "", "attrs": {}}

    # Clean construction info on raw text to preserve newlines
    text_no_const = remove_construction_info(text)
    name_extracted = extract_device_name(text_no_const, category_hint)
    clean_ctx = RuleContext.from_fields(
        material_category=category_hint,
        name=name_extracted,
        raw_name=name_extracted,
        stage="cleaning",
    )
    text_clean = clean_text_standard(text_no_const, rule_context=clean_ctx)
    
    # 1. Run legacy dynamic general parameter extraction on raw text (preserving original casing/units)
    attrs = extract_general_params(text_no_const)
    
    # 2. Route to domain
    domain = route_domain(category_hint, name_extracted, text_clean, category_hint)
    
    # 3. Extract domain-specific attributes using the routed domain's extractor
    domain_attrs = extract_parameters_by_domain(domain, text_clean)
    for k, v in domain_attrs.items():
        attrs[k] = v

    # 3b. Specialized Extractors Triggering based on domain or keyword hints
    from app.domain_extractors import (
        extract_cable_params, extract_pipe_params, extract_decoration_params,
        extract_conduit_params, extract_switchgear_params, extract_power_range,
        extract_switch_gang_control, extract_ups_params
    )
    if domain in ["medical_gas", "valve_medical_gas"] or any(k in text_clean for k in ["氧气", "负压", "吸引", "真空", "压缩空气", "空压", "二氧化碳", "co2", "n2o", "氮气"]):
        med_map = {
            "氧气": ["氧气", "o2"],
            "负压": ["负压", "吸引", "真空", "vac"],
            "空压": ["空压", "压缩空气", "空气", "air"],
            "CO2": ["co2", "二氧化碳"],
            "N2O": ["n2o", "笑气"],
            "氮气": ["氮气", "n2"]
        }
        for std_name, syns in med_map.items():
            if any(s in text_clean for s in syns):
                attrs["介质"] = std_name
                break

    if domain == "pipe_copper" or any(k in text_clean for k in ["紫铜", "脱脂铜", "铜管", "紫铜管"]):
        if "脱脂" in text_clean:
            attrs["材质"] = "脱脂紫铜"
        else:
            attrs["材质"] = "紫铜"

    if domain in ["ahu", "filter_ffu"] or any(k in text_clean for k in ["mau", "pau", "rau", "ahu", "fcu", "rcu", "ffu", "hrv", "erv", "dxu", "cru", "tu", "vav", "cav", "hepa", "paf", "机组", "空调", "新风", "过滤单元"]):
        from app.query_preprocessor import extract_hvac_unit_type
        unit_type = extract_hvac_unit_type(text_no_const) or extract_hvac_unit_type(category_hint)
        if unit_type:
            attrs["机组类型"] = unit_type

    if domain == "ups" or any(k in text_clean for k in ["ups", "不间断电源", "电池屏", "电池柜", "蓄电池"]):
        ups_attrs = extract_ups_params(text_no_const)
        attrs.update(ups_attrs)
    
    if any(k in text_clean for k in ["kw", "千瓦", "功率", "配电箱", "箱", "柜"]):
        pw_attrs = extract_power_range(text_no_const)
        attrs.update(pw_attrs)

    if any(k in text_clean for k in ["联", "控", "位", "开关", "插座"]):
        sw_gang_attrs = extract_switch_gang_control(text_no_const)
        attrs.update(sw_gang_attrs)

    if domain == "conduit" or any(k in text_clean for k in ["线管", "jdg", "kbg", "导管", "穿线管", "kjg", "fpc", "kpc", "sc", "rc", "mt", "pc"]):
        cd_attrs = extract_conduit_params(text_no_const)
        attrs.update(cd_attrs)

    if any(k in domain for k in ["switchgear", "panel", "cabinet"]) or any(k in text_clean for k in ["箱", "柜", "配电", "控制箱", "ap", "al", "ale", "at", "acp", "ax", "atd", "afc"]):
        sw_attrs = extract_switchgear_params(text_no_const)
        attrs.update(sw_attrs)

    if any(k in domain for k in ["cable", "wire"]) or any(k in text_clean for k in ["缆", "线", "yjv", "byj", "nh", "wdz", "rvv", "bvr", "bv"]):
        c_attrs = extract_cable_params(text_no_const)
        attrs.update(c_attrs)

    if any(k in domain for k in ["pipe", "steel", "ss", "copper"]) or any(k in text_clean for k in ["管", "dn", "de", "不锈钢", "方管", "镀锌", "紫铜"]):
        p_attrs = extract_pipe_params(text_no_const)
        attrs.update(p_attrs)

    if any(k in domain for k in ["decor", "board", "door", "tile", "keel", "panel"]) or any(k in text_clean for k in ["砖", "板", "龙骨", "门", "踢脚线", "石材", "涂料"]):
        d_attrs = extract_decoration_params(text_no_const)
        attrs.update(d_attrs)

    # Post-process all extracted keys to cast to float/int using domain schema definitions
    from app.domain_extractors import domain_schema, parse_numeric_complex
    domain_config = domain_schema.get(domain, {})
    fields = domain_config.get("must", []) + domain_config.get("should", [])
    
    # Track keys to process
    numeric_keys = {f["key"]: f.get("type", "str") for f in fields}
    # Common numeric attributes across domains
    common_numeric = ["功率", "制冷量", "制热量", "风量", "送风量", "新风量", "排风量", "余压", "机外余压", "管径", "口径", "外径", "壁厚", "厚度", "涂膜厚度", "量程", "扬程"]
    for ck in common_numeric:
        if ck not in numeric_keys:
            numeric_keys[ck] = "float"

    for key, ftype in numeric_keys.items():
        if key in ["材质", "品牌", "单位", "型式", "功能", "名称", "规格", "连接方式"]:
            continue
        if domain in ["cable_power", "cable_control", "wire"] and key in ["芯数", "截面积"]:
            continue
        if key in attrs and isinstance(attrs[key], str):
            if ftype in ["float", "int"] or key in common_numeric:
                parsed = parse_numeric_complex(attrs[key])
                if "val" in parsed:
                    attrs[key] = int(parsed["val"]) if ftype == "int" else float(parsed["val"])
                    if "min_val" in parsed and "max_val" in parsed:
                        attrs[f"{key}_min"] = parsed["min_val"]
                        attrs[f"{key}_max"] = parsed["max_val"]
                    if "op" in parsed and "base_val" in parsed:
                        attrs[f"{key}_op"] = parsed["op"]
                        attrs[f"{key}_base"] = parsed["base_val"]
    
    # 4. Extract model
    model = extract_model(text_clean)

    # V017-M1 / M1-C02：工程设备位号/箱号归入 tag_id（非产品型号），避免位号差异误触发型号冲突。
    device_tag = extract_device_tag(text_no_const)
    if device_tag:
        attrs["tag_id"] = device_tag

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
        attrs.pop("主芯数", None)
        attrs.pop("主截面", None)

    if "送风量" in attrs and "风量" not in attrs:
        attrs["风量"] = attrs["送风量"]
    if "机外余压" in attrs and "余压" not in attrs:
        attrs["余压"] = attrs["机外余压"]
    if "涂膜厚度" in attrs and "厚度" not in attrs:
        attrs["厚度"] = attrs["涂膜厚度"]

    # Post-process and merge filter attributes into attrs["过滤段"] as a list
    filter_val = attrs.get("机组过滤器") or attrs.get("过滤段") or attrs.get("过滤等级")
    if filter_val:
        if isinstance(filter_val, str):
            parts = [p.strip() for p in re.split(r'[+/,，\s]', filter_val) if p.strip()]
            attrs["过滤段"] = parts
        elif isinstance(filter_val, list):
            attrs["过滤段"] = filter_val

    # Map filter codes to standard Chinese filter levels for "过滤等级" must-field compatibility
    if "过滤段" in attrs:
        levels = []
        for f in attrs["过滤段"]:
            f_upper = f.upper()
            if any(x in f_upper for x in ["G1", "G2", "G3", "G4", "初效"]):
                levels.append("初效")
            elif any(x in f_upper for x in ["F5", "F6", "F7", "F8", "F9", "中效"]):
                levels.append("中效")
            elif any(x in f_upper for x in ["H10", "H11", "H12", "亚高效"]):
                levels.append("亚高效")
            elif any(x in f_upper for x in ["H13", "H14", "HEPA", "高效"]):
                levels.append("高效")
            elif any(x in f_upper for x in ["U15", "U16", "U17", "超高效"]):
                levels.append("超高效")
        if levels:
            attrs["过滤等级"] = levels[0]

    return {
        "model": model,
        "attrs": attrs,
        "domain": domain,
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
    if "过滤等级" in attrs and "过滤段" not in attrs:
        attrs["过滤段"] = [attrs["过滤等级"]] if isinstance(attrs["过滤等级"], str) else attrs["过滤等级"]

    # V017-M1 / M1-C01：统一类型化字段与字段白名单（Material 端）。
    # 将未命中受控白名单的自由文本键隔离至 unwhitelisted_attrs，仅白名单键保留在
    # attrs 参与 matcher 属性打分；typed_attrs 为新增并行结构化结构。
    from app.typed_fields import (
        build_typed_attrs,
        partition_whitelisted_attrs,
        build_schema_version_payload,
    )
    domain = res.get("domain", "")
    whitelisted, unwhitelisted = partition_whitelisted_attrs(attrs)
    typed_attrs = build_typed_attrs(whitelisted, raw_text=text, domain=domain)

    return {
        "model": res.get("model", ""),
        "attrs": whitelisted,
        "typed_attrs": typed_attrs,
        "unwhitelisted_attrs": unwhitelisted,
        "schema_version": 3,
        "domain": domain,
    }

PARAM_LABEL_WORDS = [
    r'半周长\s*[：:]?', r'周长\s*[：:]?', r'长度\s*[：:]?', r'宽度\s*[：:]?',
    r'高度\s*[：:]?', r'风量\s*[：:]?', r'余压\s*[：:]?', r'静压\s*[：:]?',
    r'面积\s*[：:]?', r'厚度\s*[：:]?', r'壁厚\s*[：:]?'
]
PARAM_LABEL_PATTERN = re.compile('|'.join(PARAM_LABEL_WORDS), re.IGNORECASE)

def clean_extracted_device_name(name: str) -> str:
    """Strip parameter label words, isolated symbols, and trailing delimiters from device name."""
    if not name:
        return ""
    cleaned = PARAM_LABEL_PATTERN.sub('', name)
    cleaned = re.sub(r'[0-9\.\s[:：]]', '', cleaned)
    cleaned = re.sub(r'[A-Za-z0-9\-\(\)/~～±]', '', cleaned)
    cleaned = re.sub(r'[\s~～\-—:：,\.，。/]+$', '', cleaned).strip()
    return cleaned

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
        line = PARAM_LABEL_PATTERN.sub('', line)
        line = re.sub(r'[\s\-:：,\.，。~～]+$', '', line).strip()
        return line if line else category_name
        
    spec_match = re.search(r'(?:材质、规格|材质规格|设备名称)\s*[:：]\s*([^\n\r，,；;]+)', text)
    if spec_match:
        line = spec_match.group(1).strip()
        model = extract_model(text)
        if model:
            line = line.replace(model, "").strip()
        line = re.sub(r'(?:DN\d+|de\d+|\d+寸|\d+mm|型|系列).*$', '', line, flags=re.IGNORECASE).strip()
        line = PARAM_LABEL_PATTERN.sub('', line)
        line = re.sub(r'[\s\-:：,\.，。~～]+$', '', line).strip()
        if len(line) > 2:
            return line
            
    first_line = text.split('\n')[0].strip()
    first_segment = re.split(r'[,，;；]', first_line)[0].strip()
    first_line_clean = clean_extracted_device_name(first_segment)
    
    if len(first_line_clean) >= 2:
        # Check if the extracted segment looks like a property description rather than a device name
        exclude_property_words = ["风量", "流量", "扬程", "功率", "压力", "口径", "管径", "尺寸", "规格", "型号", "材质", "温度", "电压", "电流", "安装", "部位", "介质", "工作", "半周长", "周长", "长度", "宽度", "高度", "面积", "厚度"]
        if not any(w in first_line_clean for w in exclude_property_words):
            return first_line_clean
            
    return category_name
