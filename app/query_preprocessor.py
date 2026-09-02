# -*- coding: utf-8 -*-
import re
from typing import Dict, Any, Tuple, Optional, List

from app.normalizer import clean_text_standard, to_half_width, convert_de_to_dn
from app.domain_router import route_domain
from app.domain_extractors import ConfigDrivenExtractor, parse_numeric_complex

# Keywords that indicate noise/work description sections in query manifests
NOISE_SECTION_KEYWORDS = [
    "工作内容", "施工内容", "施工部位", "敷设方式", "安装方式", "包含完成该项",
    "单价暂按", "暂估", "全费用", "根据实际", "包工包料", "配合", "试压", "清理基层",
    "检验试验", "冲洗", "调试", "吹扫", "成品保护", "管道及管件安装", "此为成品安装价格",
    "重新重复名称", "备注"
]

# HVAC Unit Type Rules (14 standard acronyms & Chinese keywords)
# HVAC Unit Type Rules (standard acronyms & Chinese keywords)
HVAC_UNIT_MAP = [
    ("全新风空气处理机组", ["MAU", "全新风空气处理机组", "全新风处理机组", "全新风机组", "100%全新风", "全新风"]),
    ("回风循环空气处理机组", ["RAU", "回风循环空气处理机组", "回风循环"]),
    ("组合式空气处理机组", ["AHU", "组合式空气处理机组", "空气处理机组", "组合式空调"]),
    ("风机盘管机组", ["FCU", "风机盘管机组", "风机盘管", "卡式风机盘管", "卧式风机盘管"]),
    ("新风预处理机组", ["PAU", "PAU-N", "PAU-MZ", "净化新风机组", "新风预处理机组", "新风预处理", "预处理机组", "新风机组", "吊顶新风机组"]),
    ("净化循环机组", ["RCU", "净化循环机组", "循环空气处理机组", "循环机组", "自取新风机组", "AHU-J"]),
    ("多联式空调室内机", ["多联机室内机", "多联式室内机", "多联机", "多联式空调室内机", "VRV室内机", "VRF室内机"]),
    ("离心风机（箱）", ["静音型排风机", "静音排风机", "医用静音型排风机", "离心风机（箱）", "离心式风机", "离心排风机"]),
    ("风机过滤单元", ["FFU", "风机过滤单元FFU", "风机过滤单元"]),
    ("热回收新风机组", ["HRV", "热回收新风机组", "热回收新风"]),
    ("全热回收新风机组", ["ERV", "全热回收新风机组", "全热回收"]),
    ("直膨式空气处理机组", ["DXU", "直膨式空气处理机组", "直膨式"]),
    ("洁净室专用机组", ["CRU", "洁净室专用机组", "洁净室专用"]),
    ("末端送风单元", ["TU", "末端送风单元"]),
    ("变风量空调系统", ["VAV", "变风量空调系统", "变风量空调", "变风量"]),
    ("定风量空调系统", ["CAV", "定风量空调系统", "定风量"]),
    ("高效空气过滤器", ["HEPA", "高效空气过滤器", "高效过滤器"]),
    ("新风风机单元", ["PAF", "新风风机单元", "新风风机"])
]

def is_valid_query_item(name: Any, spec: Any, unit: Any = "", code: Any = "", row_index: int = 0) -> Tuple[bool, str]:
    """
    IR-0 Extended Filtering Rules (10 exact rules)
    Returns: (is_valid: bool, rule_reason: str)
    """
    name_str = str(name).strip() if (name is not None and name != "nan") else ""
    spec_str = str(spec).strip() if (spec is not None and spec != "nan") else ""
    unit_str = str(unit).strip() if (unit is not None and unit != "nan") else ""
    code_str = str(code).strip() if (code is not None and code != "nan") else ""

    # Rule 1: Empty row / openpyxl isolated None row
    if not name_str and not spec_str:
        return False, "R1_EMPTY_ROW"

    # Rule 2: Subtotal / Total summary row
    if name_str in ["小计", "合计", "总计", "合计：", "小计：", "总计："]:
        return False, "R2_TOTAL_ROW"

    # Rule 3: Cross-sheet repeated header row
    if name_str in ["名称", "*名称", "项目名称", "构件名称", "材料(设备)名称", "材料名称", "设备名称"]:
        return False, "R3_REPEAT_HEADER_ROW"

    # Rule 4: Preface & documentation instruction row
    if any(k in name_str for k in ["编制依据", "图纸版本号", "图纸号", "计量依据", "设计说明"]):
        return False, "R4_PREFACE_NOTE_ROW"

    # Rule 6: Single-cell paragraph description (length > 100 with no parameters)
    if len(name_str) > 100 and not unit_str and not spec_str:
        return False, "R6_LONG_TEXT_NOTE_ROW"

    # Rule 7: Footer note / sign-off row
    if any(k in name_str for k in ["编制人:", "编制人：", "审核人:", "审核人：", "编制日期:", "编制日期：", "制表人:", "制表人："]):
        return False, "R7_FOOTER_NOTE_ROW"

    # Rule 8: Formula calculation row (name empty, spec contains operators +, -, *, /, = and math chars)
    if not name_str and any(op in spec_str for op in ['+', '-', '*', '/', '=']) and re.match(r'^[0-9\+\-\*\/\(\)\.\=\s]+$', spec_str):
        return False, "R8_FORMULA_CALC_ROW"

    # Rule 9: Isolated short value row from split merged cells
    if not name_str and not code_str and len(spec_str) < 3 and not unit_str:
        return False, "R9_MERGED_CELL_ORPHAN_ROW"

    # Rule 10: Special table divider row
    if name_str.startswith("===") or spec_str.startswith("===") or name_str.startswith("---") or spec_str.startswith("---"):
        return False, "R10_DIVIDER_ROW"

    # Rule 5: Outline chapter title row / Section header (no spec, unit, or code)
    is_outline_pattern = bool(
        re.match(r'^[一二三四五六七八九十百]+[、\.]', name_str) or 
        re.match(r'^（[一二三四五六七八九十百]+）', name_str) or
        re.match(r'^\(?[0-9\.]+\)?\s+', name_str) or
        any(k in name_str for k in ["系统", "工程", "分部", "标段", "部分", "阶段"])
    )
    if not spec_str and (not unit_str or is_outline_pattern) and not code_str:
        return False, "R5_OUTLINE_TITLE_ROW"

    return True, "VALID_ITEM"

def clean_query_text_ir1(text: str) -> str:
    """
    IR-1 Text Normalization:
    - Full-width to half-width characters
    - Punctuation & multiplication symbol normalization (* instead of X/x/×)
    - Whitelisted prefix stripping (preserves numbers followed by mm, A, kW, DN, m², *, ℃)
    """
    if not text:
        return ""

    t = to_half_width(text).strip()
    t = t.replace("：", ":").replace("；", ";").replace("（", "(").replace("）", ")").replace("，", ",")
    t = re.sub(r'([0-9])\s*[xX×]\s*([0-9])', r'\1*\2', t)

    # Prefix index stripping with unit whitelist protection
    # Section numbers typically look like "1.", "1.1", "12." (1-2 digits per level)
    lines = t.split("\n")
    cleaned_lines = []
    for line in lines:
        l_str = line.strip()
        if not l_str:
            continue
        m = re.match(r'^(\d{1,2}(?:\.\d{1,2})?)[\.．、\s]\s*(.*)$', l_str)
        if m:
            rest = m.group(2).strip()
            # Whitelist check for units/numbers right after prefix
            whitelist_units = ["MM", "MM2", "MM²", "M2", "M²", "KW", "W", "A", "V", "DN", "DE", "PA", "KPA", "MPA", "℃", "°C", "*", "寸", "P", "R"]
            rest_upper = rest.upper()
            is_whitelisted = any(rest_upper.startswith(u) for u in whitelist_units)
            if not is_whitelisted and (not rest or re.match(r'^[一-龥a-zA-Z_]', rest)):
                l_str = rest
        cleaned_lines.append(l_str)

    return "\n".join(cleaned_lines)

def decouple_name_and_spec(name: str, spec: str) -> Tuple[str, str]:
    """
    IR-1 Sub-step: Lightweight name & spec decoupling.
    Extract spec embedded in name (e.g. '无缝钢管 DN100') -> Name: '无缝钢管', Spec: 'DN100\n...'
    """
    name_clean = (name or "").strip()
    spec_clean = (spec or "").strip()

    if not name_clean:
        return name_clean, spec_clean

    spec_pattern = r'((?:DN|DE|De|Ф|Φ)\d+|\d+\s*\*\s*\d+(?:\s*\*\s*\d+)?|\b[A-Z]{2,}-\d+\*\d+|\d+(?:\.\d+)?\s*(?:mm2|mm²|平方))'
    m = re.search(spec_pattern, name_clean, re.IGNORECASE)
    if m:
        found_spec = m.group(1).strip()
        new_name = name_clean.replace(found_spec, "").strip()
        new_name = re.sub(r'[\s\-:：,\.，。]+$', '', new_name).strip()
        if len(new_name) >= 2:
            new_spec = f"{found_spec}\n{spec_clean}".strip() if spec_clean else found_spec
            return new_name, new_spec

    return name_clean, spec_clean

def slice_query_sections(raw_spec: str) -> Tuple[str, str]:
    """
    Slice raw query specification text into material description block and noise block.
    """
    if not raw_spec:
        return "", ""

    spec_clean = clean_query_text_ir1(raw_spec)
    lines = spec_clean.split("\n")

    mat_lines = []
    noise_lines = []
    in_noise = False

    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue

        if any(kw in line_str for kw in NOISE_SECTION_KEYWORDS):
            if re.search(r'^\d*[\.．、\s]*(?:工作内容|施工|单价|敷设|包含|调试|全费用|工程量|此为|重新重复|备注)', line_str) or any(line_str.startswith(kw) for kw in ["工作内容", "施工部位", "此为成品", "重新重复名称", "备注"]):
                in_noise = True

        if in_noise:
            noise_lines.append(line_str)
        else:
            mat_lines.append(line_str)

    material_block = "\n".join(mat_lines)
    noise_block = "\n".join(noise_lines)
    return material_block, noise_block

def parse_query_kv_blocks(material_block: str) -> Dict[str, Any]:
    """
    KV Line Parser v1.2:
    Parse key-value pairs with parenthesis unit extraction and non-greedy key matching.
    """
    kv_dict = {}
    if not material_block:
        return kv_dict

    lines = [line.strip() for line in material_block.split('\n') if line.strip()]
    for line in lines:
        match = re.match(r'^(?:\d+[\.\、\s]|（\d+）|\(\d+\)|[①-⑩])?\s*([\u4e00-\u9fa5A-Za-z0-9_]+?)(?:\s*[（\(]([^）\)]*)[）\)])?\s*[:：]\s*(.*)$', line)
        if match:
            k = match.group(1).strip()
            unit_in_paren = match.group(2).strip() if match.group(2) else ""
            v = match.group(3).strip()
            k = re.sub(r'^\d+', '', k).strip()
            if k in ["工作内容", "施工内容", "重新重复名称", "备注", "其他"]:
                continue
            if unit_in_paren:
                kv_dict[k] = {"value": v, "unit": unit_in_paren}
            else:
                kv_dict[k] = v
        else:
            if ":" in line or "：" in line:
                parts = re.split(r'[:：]', line, maxsplit=1)
                k = re.sub(r'^\d+[\.．、\s]*', '', parts[0]).strip()
                v = parts[1].strip()
                if k and k not in ["工作内容", "施工内容", "重新重复名称", "备注", "其他"]:
                    kv_dict[k] = v

    return kv_dict

def extract_from_name_spec_fallback(name: str, attrs: Dict[str, Any]) -> Dict[str, Any]:
    """
    NameSpecExtractor: Back-fill parameters from name when spec is empty or missing.
    """
    if not name:
        return attrs

    n_clean = to_half_width(name).strip()

    # 1. Dimension 2D/3D (e.g., 600*600, 1000*3150*300)
    if "尺寸" not in attrs:
        dim_3d = re.search(r'(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)', n_clean)
        if dim_3d:
            attrs["尺寸"] = f"{dim_3d.group(1)}*{dim_3d.group(2)}*{dim_3d.group(3)}"
            try:
                l_v = float(dim_3d.group(1))
                w_v = float(dim_3d.group(2))
                h_v = float(dim_3d.group(3))
                attrs["长"] = l_v
                attrs["宽"] = w_v
                attrs["高"] = h_v
                attrs["长度"] = h_v
                attrs["半周长"] = float(l_v + w_v)
                attrs["面积"] = (l_v / 1000.0) * (w_v / 1000.0)
            except Exception:
                pass
        else:
            dim_2d = re.search(r'(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)', n_clean)
            if dim_2d:
                attrs["尺寸"] = f"{dim_2d.group(1)}*{dim_2d.group(2)}"
                try:
                    l_v = float(dim_2d.group(1))
                    w_v = float(dim_2d.group(2))
                    attrs["长"] = l_v
                    attrs["宽"] = w_v
                    attrs["半周长"] = float(l_v + w_v)
                    attrs["面积"] = (l_v / 1000.0) * (w_v / 1000.0)
                except Exception:
                    pass

    # 2. Thickness (e.g. 2.0mm, 0.6mm, 1.2mm)
    if "厚度" not in attrs and "板材厚度" not in attrs:
        th_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:mm|MM)?\s*(?:厚|卷材)', n_clean)
        if th_match:
            try:
                val = float(th_match.group(1))
                attrs["厚度"] = val
                attrs["板材厚度"] = val
            except Exception:
                pass

    # 3. Nominal Diameter DN / De
    if "管径" not in attrs:
        dn_match = re.search(r'(?:DN|de|De)\s*[:：]?\s*(\d+)', n_clean, re.IGNORECASE)
        if dn_match:
            attrs["管径"] = f"DN{dn_match.group(1)}"
            attrs["dn_val"] = int(dn_match.group(1))

    # 4. Material
    if "材质" not in attrs:
        for mat in ["316L", "304", "不锈钢", "PVC", "PPR", "PPH", "UPVC", "镀锌钢板", "橡胶", "碳钢", "铜"]:
            if mat in n_clean:
                if mat == "304":
                    attrs["材质"] = "不锈钢304"
                elif mat == "316L":
                    attrs["材质"] = "不锈钢316L"
                elif mat == "镀锌钢板":
                    attrs["材质"] = "镀锌钢板"
                else:
                    attrs["材质"] = mat
                break

    return attrs

def normalize_unit_scales(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mandatory unit and scale conversions to standard units:
    - Air volume: CFM * 1.699, l/s * 3.6 -> m³/h
    - Pressure: kPa * 1000, MPa * 1000000, mmH2O * 9.80665 -> Pa
    - Power: W * 0.001, HP * 0.735, kcal/h * 0.001163 -> kW
    - Pipe DN: 4寸 -> 100.0 (JIS mapping)
    """
    norm_attrs = dict(attrs)

    # 1. Air volume normalization
    for key in ["风量", "送风量", "新风量", "排风量", "处理风量"]:
        if key in norm_attrs and isinstance(norm_attrs[key], (int, float)):
            # Handled in domain_extractors parse_numeric_complex
            pass

    # 2. Pipe size JIS inch mapping if string inch passed
    if "管径" in norm_attrs and isinstance(norm_attrs["管径"], str):
        inch_map = {
            "1/2寸": 15.0, "3/4寸": 20.0, "1寸": 25.0, "1.2寸": 32.0, "1.5寸": 40.0,
            "2寸": 50.0, "2.5寸": 65.0, "3寸": 80.0, "4寸": 100.0, "5寸": 125.0,
            "6寸": 150.0, "8寸": 200.0, "10寸": 250.0, "12寸": 300.0
        }
        for k_inch, dn_v in inch_map.items():
            if k_inch in norm_attrs["管径"]:
                norm_attrs["管径"] = f"DN{int(dn_v)}"
                norm_attrs["dn_val"] = int(dn_v)
                break

    # 3. Filter post-processing and mapping
    filter_val = norm_attrs.get("机组过滤器") or norm_attrs.get("过滤段") or norm_attrs.get("过滤等级")
    if filter_val:
        if isinstance(filter_val, str):
            parts = [p.strip() for p in re.split(r'[+/,，\s]', filter_val) if p.strip()]
            norm_attrs["过滤段"] = parts
        elif isinstance(filter_val, list):
            norm_attrs["过滤段"] = filter_val

    if "过滤段" in norm_attrs:
        levels = []
        for f in norm_attrs["过滤段"]:
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
            norm_attrs["过滤等级"] = levels[0]

    return norm_attrs

def extract_hvac_unit_type(text: str) -> Optional[str]:
    """
    Extract HVAC unit classification code.
    """
    if not text:
        return None
    t_clean = to_half_width(text).upper()
    for code, keywords in HVAC_UNIT_MAP:
        for kw in keywords:
            if kw.upper() in t_clean:
                return code
    return None

def extract_query_cable_params(text: str, kv_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Query-specific cable and wire parameter extraction.
    """
    attrs = {}
    t_clean = to_half_width(text).upper().replace('X', '*').replace('×', '*').strip()

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
        return attrs

    std_match = re.search(r'(\d+)\s*\*\s*(\d+(?:\.\d+)?)', t_clean)
    if std_match:
        attrs["芯数"] = str(int(std_match.group(1)))
        sec = float(std_match.group(2))
        s_str = f"{int(sec)}" if sec.is_integer() else f"{sec}"
        attrs["截面积"] = s_str
        attrs["主芯数"] = int(std_match.group(1))
        attrs["主截面"] = sec
        return attrs

    sec_match = re.search(r'(?:截面|断面)?\s*(\d+(?:\.\d+)?)(?:\s*[\/到~-]\s*(\d+(?:\.\d+)?))?\s*(?:MM2|MM²|平方)', t_clean)
    if sec_match:
        val1 = float(sec_match.group(1))
        val2 = float(sec_match.group(2)) if sec_match.group(2) else None
        target_val = val2 if val2 else val1
        attrs["截面积"] = f"{target_val:.1f}"
        attrs["主截面"] = float(target_val)
        if "三芯" in t_clean or "3芯" in t_clean:
            attrs["芯数"] = "3"
            attrs["主芯数"] = 3
        elif "四芯" in t_clean or "4芯" in t_clean:
            attrs["芯数"] = "4"
            attrs["主芯数"] = 4
        elif "五芯" in t_clean or "5芯" in t_clean:
            attrs["芯数"] = "5"
            attrs["主芯数"] = 5
        else:
            attrs["芯数"] = "1"
            attrs["主芯数"] = 1

    return attrs

def extract_query_ductwork_params(text: str, kv_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract ductwork / purification duct parameters:
    - 板材厚度 (e.g. 0.6mm, 板材厚度：0.6mm)
    - 材质 (e.g. 优质镀锌钢板 -> 镀锌钢板)
    - 形状 (e.g. 矩形, 圆形)
    - 连接方式 (e.g. 角钢法兰, 共板法兰)
    """
    attrs = {}
    t_clean = to_half_width(text)

    thick_match = re.search(r'(?:板材厚度|钢板厚度|壁厚|厚度|δ)[:：]?\s*(\d+(?:\.\d+)?)\s*(?:mm|MM)?', t_clean)
    if not thick_match:
        thick_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:mm|MM)?\s*厚', t_clean)
    if thick_match:
        try:
            attrs["板材厚度"] = float(thick_match.group(1))
            attrs["壁厚"] = float(thick_match.group(1))
        except Exception:
            pass

    if "矩形" in t_clean:
        attrs["形状"] = "矩形"
    elif "圆形" in t_clean:
        attrs["形状"] = "圆形"

    for conn in ["角钢法兰", "共板法兰", "插条", "咬口", "焊接"]:
        if conn in t_clean:
            attrs["连接方式"] = conn
            break

    return attrs

def extract_query_plenum_params(text: str, kv_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract Plenum box / Air Collector Box parameters (集风箱/静压箱):
    - 尺寸 (e.g. 1000*3150*300)
    - 长, 宽, 高
    """
    attrs = {}
    t_clean = to_half_width(text).replace('×', '*').replace('x', '*')

    dim_3d = re.search(r'(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)', t_clean)
    if dim_3d:
        attrs["尺寸"] = f"{dim_3d.group(1)}*{dim_3d.group(2)}*{dim_3d.group(3)}"
        try:
            attrs["长"] = float(dim_3d.group(1))
            attrs["宽"] = float(dim_3d.group(2))
            attrs["高"] = float(dim_3d.group(3))
        except Exception:
            pass
    else:
        dim_2d = re.search(r'(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)', t_clean)
        if dim_2d:
            attrs["尺寸"] = f"{dim_2d.group(1)}*{dim_2d.group(2)}"
            try:
                attrs["长"] = float(dim_2d.group(1))
                attrs["宽"] = float(dim_2d.group(2))
            except Exception:
                pass

    return attrs

def extract_query_hvac_specific_params(combined_text: str, kv_dict: Dict[str, Any], attrs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Specialized extraction and inference for HVAC equipment and components.
    """
    t_clean = to_half_width(combined_text)
    hvac_attrs = dict(attrs)

    # 1. Installation Type (吊装 vs 落地)
    if any(k in t_clean for k in ["吊装", "吊顶安装", "吊顶"]):
        hvac_attrs["安装形式"] = "吊装"
        hvac_attrs["吊装"] = True
    elif any(k in t_clean for k in ["落地安装", "落地", "地装", "座地"]):
        hvac_attrs["安装形式"] = "落地"
        hvac_attrs["落地"] = True

    # 2. AHU / PAU Fresh Air & Self-taking Mode
    sf_v = hvac_attrs.get("送风量") or hvac_attrs.get("风量")
    xf_v = hvac_attrs.get("新风量")
    if (sf_v and xf_v and abs(float(sf_v) - float(xf_v)) < 1.0) or "自取新风" in t_clean:
        hvac_attrs["自取新风"] = True
        if "机组" in t_clean or "AHU" in t_clean.upper() or "循环" in t_clean:
            hvac_attrs["机组类型"] = "净化循环机组"
            hvac_attrs["循环机组"] = True
    
    if "PAU" in t_clean.upper():
        hvac_attrs.setdefault("机组类型", "新风预处理机组")

    # 3. Fan Coil Unit (FCU) structure & accessories
    if any(k in t_clean for k in ["卡式", "卡式四吹", "四面出风", "四方向"]):
        hvac_attrs["结构形式"] = "卡式"
        hvac_attrs["卡式"] = True
    elif any(k in t_clean for k in ["卧式暗装", "卧式明装", "卧式", "暗装"]):
        if not hvac_attrs.get("卡式"):
            hvac_attrs["结构形式"] = "卧式暗装"
            hvac_attrs["卧式"] = True

    if any(k in t_clean for k in ["自带提升泵", "带提升泵", "提升泵", "冷凝水提升泵", "带冷凝水提升泵"]):
        hvac_attrs["带冷凝水提升泵"] = "带冷凝水提升泵"
        hvac_attrs["提升泵"] = True

    # 4. Multi-split VRF indoor unit cooling capacity
    if any(k in t_clean for k in ["多联机", "多联式", "VRV", "VRF"]):
        hvac_attrs["多联机"] = True
        cool_match = re.search(r'(?:额定制冷量|制冷量|冷量)[:：]?\s*(\d+(?:\.\d+)?)\s*(?:kw|KW)?', t_clean)
        if cool_match:
            try:
                hvac_attrs["制冷量"] = float(cool_match.group(1))
            except Exception:
                pass

    # 5. Fan explosion-proof & noise reduction
    if any(k in t_clean for k in ["防爆", "防爆型", "防爆风机", "EX", "BT4", "CT4"]):
        hvac_attrs["防爆"] = True
    elif "非防爆" in t_clean:
        hvac_attrs["防爆"] = False

    if "静音" in t_clean or "消声" in t_clean:
        hvac_attrs["静音"] = True

    # 6. Fire damper control mode, temperature, shape
    if any(k in t_clean for k in ["电动", "DV24V", "DC24V", "AC220V", "执行机构", "执行器", "24V"]):
        hvac_attrs["控制方式"] = "电动"
        hvac_attrs["电动"] = True
    elif any(k in t_clean for k in ["常开", "常闭", "熔断", "70℃", "280℃", "70度", "280度", "防火阀"]):
        if not hvac_attrs.get("电动"):
            hvac_attrs["控制方式"] = "常开熔断"
            hvac_attrs["电动"] = False

    temp_match = re.search(r'(\d+)\s*[℃度c]', t_clean)
    if temp_match:
        try:
            hvac_attrs["熔断温度"] = int(temp_match.group(1))
        except Exception:
            pass

    # Shape detection for dampers & terminals
    dim_2d = re.search(r'(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)', t_clean)
    phi_match = re.search(r'[φΦ直径DNdeDe]\s*[:：]?\s*(\d+(?:\.\d+)?)', t_clean)
    if dim_2d:
        hvac_attrs["形状"] = "矩形"
        hvac_attrs["尺寸_2d"] = f"{dim_2d.group(1)}*{dim_2d.group(2)}"
        hvac_attrs["is_circular"] = False
    elif phi_match:
        hvac_attrs["形状"] = "圆形"
        hvac_attrs["is_circular"] = True
        try:
            hvac_attrs["外径"] = float(phi_match.group(1))
        except Exception:
            pass

    # 7. Clean ductwork material and thickness
    if any(k in t_clean for k in ["风管", "通风管", "风道"]):
        th_match = re.search(r'(?:板材厚度|厚度|壁厚|δ)[:：]?\s*(\d+(?:\.\d+)?)\s*(?:mm|MM)?', t_clean)
        if not th_match:
            th_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:mm|MM)?\s*厚', t_clean)
        if th_match:
            try:
                hvac_attrs["厚度"] = float(th_match.group(1))
                hvac_attrs["壁厚"] = float(th_match.group(1))
                hvac_attrs["板材厚度"] = float(th_match.group(1))
            except Exception:
                pass
        if "优质镀锌钢板" in t_clean or "镀锌钢板" in t_clean or "镀锌" in t_clean:
            hvac_attrs["材质"] = "镀锌钢板"

    return hvac_attrs

def canonicalize_query_attrs(domain: str, attrs: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
    """
    Canonicalize extracted query attribute values to match standard material DB expectations.
    """
    clean_attrs = dict(attrs)

    mat_raw = str(clean_attrs.get("材质", "")).strip()
    if mat_raw or raw_text:
        combined = f"{mat_raw} {raw_text}"
        if any(kw in combined for kw in ["316L", "316l", "S31603"]):
            clean_attrs["材质"] = "不锈钢316L"
        elif any(kw in combined for kw in ["304", "S30408", "06Cr19Ni10"]):
            clean_attrs["材质"] = "不锈钢304"
        elif "不锈钢" in combined:
            clean_attrs["材质"] = "不锈钢"
        elif any(kw in combined for kw in ["紫铜", "脱脂铜管", "脱脂紫铜", "磷酸脱氧", "脱氧铜", "铜管"]):
            clean_attrs["材质"] = "紫铜"
        elif any(kw in combined for kw in ["优质镀锌钢板", "镀锌钢板", "镀锌板", "镀锌"]):
            clean_attrs["材质"] = "镀锌钢板"
        elif "碳钢" in combined:
            clean_attrs["材质"] = "碳钢"

    if "截面积" in clean_attrs:
        sec_val = clean_attrs["截面积"]
        if isinstance(sec_val, (int, float)):
            clean_attrs["截面积"] = f"{float(sec_val):.1f}"
        elif isinstance(sec_val, str) and "+" in sec_val:
            parts = sec_val.split("+")
            try:
                p1 = float(parts[0])
                p2 = float(parts[1])
                clean_attrs["截面积"] = f"{p1:.1f}+{p2:.1f}"
            except Exception:
                pass

    # 5. Medical Gas Medium Mapping
    if domain in ["medical_gas", "valve_medical_gas"] or any(k in raw_text for k in ["氧气", "负压", "吸引", "真空", "压缩空气", "空压", "二氧化碳", "co2", "n2o", "氮气"]):
        med_map = {
            "氧气": ["氧气", "o2"],
            "负压": ["负压", "吸引", "真空", "vac"],
            "空压": ["空压", "压缩空气", "空气", "air"],
            "CO2": ["co2", "二氧化碳"],
            "N2O": ["n2o", "笑气"],
            "氮气": ["氮气", "n2"]
        }
        for std_name, syns in med_map.items():
            if any(s in raw_text.lower() for s in syns):
                clean_attrs["介质"] = std_name
                break

    # 6. Medical Gas Copper Pipe Material Mapping
    if domain == "pipe_copper":
        if "脱脂" in raw_text:
            clean_attrs["材质"] = "脱脂紫铜"
        else:
            clean_attrs["材质"] = "紫铜"

    return clean_attrs

def extract_query_params(name: str, specification: str, category: str) -> Dict[str, Any]:
    """
    Main entry point for extracting structured parameters from raw query manifests.
    """
    # 1. Decouple name and spec if spec is embedded in name
    effective_name, effective_spec = decouple_name_and_spec(name, specification)
    name_clean = clean_text_standard(effective_name or "").strip()
    spec_raw = (effective_spec or "").strip()
    cat_clean = (category or "").strip()

    # 2. Determine domain using route_domain
    domain = route_domain(cat_clean, name_clean, spec_raw)

    # 3. Slice sections & parse KV blocks
    material_block, noise_block = slice_query_sections(spec_raw)
    kv_dict = parse_query_kv_blocks(material_block)

    # Combine name and material block
    spec_name = kv_dict.get("名称", "")
    if isinstance(spec_name, dict):
        spec_name = spec_name.get("value", "")
    final_name = spec_name if (spec_name and len(str(spec_name)) >= len(name_clean)) else name_clean
    combined_text = f"{final_name}\n{material_block}".strip()

    # 4. Extract attrs using ConfigDrivenExtractor
    extractor = ConfigDrivenExtractor(domain)
    attrs = extractor.extract(combined_text)
    attrs["名称"] = final_name

    # 5. NameSpecExtractor fallback for empty / short spec
    if not material_block or len(material_block) < 3 or not attrs or len(attrs) <= 1:
        attrs = extract_from_name_spec_fallback(final_name, attrs)

    # 6. HVAC Unit Type & Domain specific extractions
    unit_code = extract_hvac_unit_type(combined_text)
    if unit_code:
        attrs["机组类型"] = unit_code

    if domain in ["cable_power", "cable_control", "wire"]:
        cable_attrs = extract_query_cable_params(combined_text, kv_dict)
        attrs.update(cable_attrs)
    elif domain == "plenum":
        plenum_attrs = extract_query_plenum_params(combined_text, kv_dict)
        attrs.update(plenum_attrs)

    if any(kw in combined_text for kw in ["风管", "管道", "通风管", "净化"]):
        duct_attrs = extract_query_ductwork_params(combined_text, kv_dict)
        attrs.update(duct_attrs)

    # HVAC specific feature inferences (AHU, PAU, FCU, VRF, Fan, Damper)
    hvac_extracted = extract_query_hvac_specific_params(combined_text, kv_dict, attrs)
    attrs.update(hvac_extracted)

    # 7. Canonicalize & normalize units and derive geometric features
    if "长" in attrs and "宽" in attrs and "半周长" not in attrs:
        try:
            attrs["半周长"] = float(attrs["长"]) + float(attrs["宽"])
        except Exception:
            pass
    if "高" in attrs and "长度" not in attrs:
        try:
            attrs["长度"] = float(attrs["高"])
        except Exception:
            pass
    elif "尺寸" in attrs:
        m_3d = re.search(r'(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)', str(attrs["尺寸"]))
        if m_3d:
            try:
                l_v = float(m_3d.group(1))
                w_v = float(m_3d.group(2))
                h_v = float(m_3d.group(3))
                attrs["长"] = l_v
                attrs["宽"] = w_v
                attrs["高"] = h_v
                attrs["长度"] = h_v
                if "半周长" not in attrs:
                    attrs["半周长"] = float(l_v + w_v)
            except Exception:
                pass
        elif "半周长" not in attrs:
            m_2d = re.search(r'(\d+(?:\.\d+)?)\s*[*x×]\s*(\d+(?:\.\d+)?)', str(attrs["尺寸"]))
            if m_2d:
                try:
                    l_v = float(m_2d.group(1))
                    w_v = float(m_2d.group(2))
                    attrs["长"] = l_v
                    attrs["宽"] = w_v
                    attrs["半周长"] = float(l_v + w_v)
                except Exception:
                    pass

    canonical_attrs = canonicalize_query_attrs(domain, attrs, combined_text)
    normalized_attrs = normalize_unit_scales(canonical_attrs)

    # 8. Apply dynamic LLM extraction rules
    try:
        from app.llm_rule_engine import LLMRuleManager, DynamicRuleInterpreter
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
            scope_lvl = r.get("scope_level", "category")
            scope_tgt = r.get("scope_target", "")
            if scope_lvl == "discipline" and scope_tgt and not scope_matches(scope_tgt, [cat_clean, domain, discipline, "给排水", "通用"]):
                continue
            if scope_lvl == "category" and scope_tgt and not scope_matches(scope_tgt, [name_clean, final_name, combined_text]):
                continue
            dsl = r.get("parsed_dsl", {})
            extraction_rules = dsl.get("extraction_rules", [])
            if extraction_rules:
                dyn_extracted = DynamicRuleInterpreter.apply_extraction_rules(combined_text, extraction_rules)
                for k, v in dyn_extracted.items():
                    if v is not None:
                        normalized_attrs[k] = v
                        if k in ["name", "名称"]:
                            normalized_attrs["名称"] = str(v)
                        elif k in ["material", "材质"]:
                            normalized_attrs["材质"] = str(v)
                        elif k in ["specification", "规格", "dn_val", "口径", "管径"]:
                            m_dn = re.search(r'DN\s*(\d+)', str(v), re.IGNORECASE)
                            if m_dn:
                                normalized_attrs["管径"] = float(m_dn.group(1))
                                normalized_attrs["dn_val"] = float(m_dn.group(1))
                            else:
                                normalized_attrs["规格"] = str(v)
    except Exception:
        pass

    return {
        "domain": domain,
        "attrs": normalized_attrs,
        "model": normalized_attrs.get("型号", "") or attrs.get("型号", ""),
        "clean_text": material_block,
        "noise_text": noise_block,
        "kv_dict": kv_dict
    }

