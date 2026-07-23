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
    "检验试验", "冲洗", "调试", "吹扫", "成品保护", "管道及管件安装", "此为成品安装价格"
]

# HVAC Unit Type Rules (14 standard acronyms & Chinese keywords)
HVAC_UNIT_MAP = [
    ("MAU", ["MAU", "全新风处理机组", "全新风机组", "100%全新风", "全新风"]),
    ("PAU", ["PAU", "预空调机组", "新风预冷", "预处理机组"]),
    ("RAU", ["RAU", "回风处理机组", "循环回风"]),
    ("FAU", ["FAU", "新风空调机组", "新风柜"]),
    ("FCU", ["FCU", "风机盘管"]),
    ("RCU", ["RCU", "循环机组"]),
    ("DOAS", ["DOAS", "专用新风系统", "独立新风除湿", "独立新风"]),
    ("VAV", ["VAV", "变风量空调", "变风量"]),
    ("RTU", ["RTU", "屋顶式", "屋顶风冷", "屋顶空调"]),
    ("HRU", ["HRU", "热回收新风", "热回收"]),
    ("ERU", ["ERU", "能量回收"]),
    ("HPU", ["HPU", "湿度处理机组", "湿度处理", "加湿机组", "除湿机组"]),
    ("FPU", ["FPU", "风机动力单元"]),
    ("AHU", ["AHU", "组合式空气处理机组", "空气处理机组", "组合式空调"])
]

def slice_query_sections(raw_spec: str) -> Tuple[str, str]:
    """
    Slice raw query specification text into material description block and noise block.
    """
    if not raw_spec:
        return "", ""

    spec_clean = to_half_width(raw_spec).strip()
    lines = spec_clean.split("\n")

    mat_lines = []
    noise_lines = []
    in_noise = False

    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue

        # Check if line marks the start of noise section
        if any(kw in line_str for kw in NOISE_SECTION_KEYWORDS):
            if re.search(r'^\d+[\.．、]\s*(?:工作内容|施工|单价|敷设|包含|调试|全费用|工程量|此为)', line_str) or any(line_str.startswith(kw) for kw in ["工作内容", "施工部位", "此为成品"]):
                in_noise = True

        if in_noise:
            noise_lines.append(line_str)
        else:
            mat_lines.append(line_str)

    material_block = "\n".join(mat_lines)
    noise_block = "\n".join(noise_lines)
    return material_block, noise_block

def parse_query_kv_blocks(material_block: str) -> Dict[str, str]:
    """
    Parse first-level numbered key-value blocks (e.g. 1.名称:..., 2.规格:...)
    Returns a dictionary of key -> value.
    """
    kv_dict = {}
    if not material_block:
        return kv_dict

    # Find numbered items: e.g. "1.名称：...", "2.规格、型号：..."
    pattern = r'(?:^|\n)(\d+)\s*[\.．、]\s*([^：:，,；;\n\r]{1,15})\s*[:：]\s*([\s\S]*?)(?=(?:\n\d+\s*[\.．、]|$))'
    matches = list(re.finditer(pattern, material_block))

    if matches:
        for m in matches:
            key = m.group(2).strip()
            val = m.group(3).strip()
            kv_dict[key] = val
    else:
        # Fallback to splitting by line or colons
        lines = material_block.split("\n")
        for line in lines:
            if ":" in line or "：" in line:
                parts = re.split(r'[:：]', line, maxsplit=1)
                k = re.sub(r'^\d+[\.．、]\s*', '', parts[0]).strip()
                v = parts[1].strip()
                if k and v:
                    kv_dict[k] = v

    return kv_dict

def extract_hvac_unit_type(text: str) -> Optional[str]:
    """
    Extract HVAC unit classification code (AHU, MAU, PAU, RAU, FAU, FCU, RCU, DOAS, VAV, RTU, HRU, ERU, HPU, FPU).
    """
    if not text:
        return None
    t_clean = to_half_width(text).upper()
    for code, keywords in HVAC_UNIT_MAP:
        for kw in keywords:
            if kw.upper() in t_clean:
                return code
    return None

def extract_query_cable_params(text: str, kv_dict: Dict[str, str]) -> Dict[str, Any]:
    """
    Query-specific cable and wire parameter extraction.
    """
    attrs = {}
    t_clean = to_half_width(text).upper().replace('X', '*').replace('×', '*').strip()

    models = [
        "WDZN-YJV22", "WDZN-YJV", "WDZN-BYJ", "WDZ-YJY", "WDZ-BYJ", "WDZ-RYJS", "WDZ-RYJ",
        "WDZB-YJY", "WDZB-YJV", "NH-YJV22", "NH-YJV", "NH-BV", "ZR-YJV22", "ZR-YJV", "ZR-BV", "ZR-BVR",
        "YJV22", "YJY22", "YJV", "YJY", "KVVP2-22", "KVVP2", "KVVP", "KVV", "RVVP", "RVV",
        "BYJ", "BVR", "BV", "RYJS", "RYJ"
    ]
    for m in models:
        if m in t_clean:
            attrs["型号"] = m
            break

    comp_match = re.search(r'(\d+)\s*\*\s*(\d+(?:\.\d+)?)\s*\+\s*(\d+)\s*\*\s*(\d+(?:\.\d+)?)', t_clean)
    if comp_match:
        attrs["芯数"] = f"{comp_match.group(1)}+{comp_match.group(3)}"
        attrs["截面积"] = f"{float(comp_match.group(2)):.1f}+{float(comp_match.group(4)):.1f}"
        attrs["主芯数"] = int(comp_match.group(1))
        attrs["主截面"] = float(comp_match.group(2))
        attrs["零芯数"] = int(comp_match.group(3))
        attrs["零截面"] = float(comp_match.group(4))
        return attrs

    std_match = re.search(r'(\d+)\s*\*\s*(\d+(?:\.\d+)?)', t_clean)
    if std_match:
        attrs["芯数"] = str(int(std_match.group(1)))
        attrs["截面积"] = f"{float(std_match.group(2)):.1f}"
        attrs["主芯数"] = int(std_match.group(1))
        attrs["主截面"] = float(std_match.group(2))
        return attrs

    sec_match = re.search(r'(?:截面|断面)?\s*(\d+(?:\.\d+)?)(?:\s*[\/到~-]\s*(\d+(?:\.\d+)?))?\s*(?:MM2|MM²|平方)', t_clean)
    if sec_match:
        val1 = float(sec_match.group(1))
        val2 = float(sec_match.group(2)) if sec_match.group(2) else None
        target_val = val2 if val2 else val1
        attrs["截面积"] = f"{target_val:.1f}"
        attrs["主截面"] = target_val
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

def extract_query_ductwork_params(text: str, kv_dict: Dict[str, str]) -> Dict[str, Any]:
    """
    Extract ductwork / purification duct parameters:
    - 板材厚度 (e.g. 0.6mm, 板材厚度：0.6mm)
    - 材质 (e.g. 优质镀锌钢板 -> 镀锌钢板)
    - 形状 (e.g. 矩形, 圆形)
    - 连接方式 (e.g. 角钢法兰, 共板法兰)
    """
    attrs = {}
    t_clean = to_half_width(text)

    # 1. Thickness
    thick_match = re.search(r'(?:板材厚度|钢板厚度|壁厚|厚度|δ)[:：]?\s*(\d+(?:\.\d+)?)\s*(?:mm|MM)?', t_clean)
    if not thick_match:
        thick_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:mm|MM)?\s*厚', t_clean)
    if thick_match:
        try:
            attrs["板材厚度"] = float(thick_match.group(1))
            attrs["壁厚"] = float(thick_match.group(1))
        except Exception:
            pass

    # 2. Shape
    if "矩形" in t_clean:
        attrs["形状"] = "矩形"
    elif "圆形" in t_clean:
        attrs["形状"] = "圆形"

    # 3. Connection type
    for conn in ["角钢法兰", "共板法兰", "插条", "咬口", "焊接"]:
        if conn in t_clean:
            attrs["连接方式"] = conn
            break

    return attrs

def extract_query_plenum_params(text: str, kv_dict: Dict[str, str]) -> Dict[str, Any]:
    """
    Extract Plenum box / Air Collector Box parameters (集风箱/静压箱):
    - 尺寸 (e.g. 1000*3150*300)
    - 长, 宽, 高
    """
    attrs = {}
    t_clean = to_half_width(text).replace('×', '*').replace('x', '*')

    # Match 3D dimensions (A*B*C) or 2D dimensions (A*B)
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

def canonicalize_query_attrs(domain: str, attrs: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
    """
    Canonicalize extracted query attribute values to match standard material DB expectations.
    """
    clean_attrs = dict(attrs)

    # 1. Canonicalize Material (材质)
    mat_raw = str(clean_attrs.get("材质", "")).strip()
    if mat_raw or raw_text:
        combined = f"{mat_raw} {raw_text}"
        if any(kw in combined for kw in ["316L", "316l", "S31603"]):
            clean_attrs["材质"] = "不锈钢316L"
        elif any(kw in combined for kw in ["304", "S30408", "06Cr19Ni10"]):
            clean_attrs["材质"] = "不锈钢304"
        elif "不锈钢" in combined:
            clean_attrs["材质"] = "不锈钢"
        elif any(kw in combined for kw in ["紫铜", "脱脂铜管", "脱脂紫铜"]):
            clean_attrs["材质"] = "铜"
        elif any(kw in combined for kw in ["优质镀锌钢板", "镀锌钢板", "镀锌板", "镀锌"]):
            clean_attrs["材质"] = "镀锌钢板"
        elif "碳钢" in combined:
            clean_attrs["材质"] = "碳钢"

    # 2. Canonicalize Section (截面积) formatting
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

    return clean_attrs

def extract_query_params(name: str, specification: str, category: str) -> Dict[str, Any]:
    """
    Main entry point for extracting structured parameters from raw query manifests.
    """
    name_clean = (name or "").strip()
    spec_raw = (specification or "").strip()
    cat_clean = (category or "").strip()

    # Determine domain
    domain = route_domain(cat_clean, name_clean, spec_raw)

    # 1. Slice sections
    material_block, noise_block = slice_query_sections(spec_raw)

    # 2. Parse KV blocks
    kv_dict = parse_query_kv_blocks(material_block)

    # Combine name and material block
    spec_name = kv_dict.get("名称", "")
    effective_name = spec_name if (spec_name and len(spec_name) >= len(name_clean)) else name_clean
    combined_text = f"{effective_name}\n{material_block}".strip()

    # 3. Extract attrs using ConfigDrivenExtractor
    extractor = ConfigDrivenExtractor(domain)
    attrs = extractor.extract(combined_text)

    # Ensure Name is preserved in attrs
    attrs["名称"] = effective_name

    # 4. HVAC Unit Type extraction
    unit_code = extract_hvac_unit_type(combined_text)
    if unit_code:
        attrs["机组类型"] = unit_code

    # 5. Domain specific extractions
    if domain in ["cable_power", "cable_control", "wire"]:
        cable_attrs = extract_query_cable_params(combined_text, kv_dict)
        attrs.update(cable_attrs)
    elif domain == "plenum":
        plenum_attrs = extract_query_plenum_params(combined_text, kv_dict)
        attrs.update(plenum_attrs)

    # Always attempt ductwork parameter extraction if text contains ductwork keywords
    if any(kw in combined_text for kw in ["风管", "管道", "通风管", "净化"]):
        duct_attrs = extract_query_ductwork_params(combined_text, kv_dict)
        attrs.update(duct_attrs)

    # 6. Canonicalize extracted attributes
    canonical_attrs = canonicalize_query_attrs(domain, attrs, combined_text)

    return {
        "domain": domain,
        "attrs": canonical_attrs,
        "clean_text": material_block,
        "noise_text": noise_block,
        "kv_dict": kv_dict
    }
