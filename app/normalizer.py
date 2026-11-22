# -*- coding: utf-8 -*-
import logging
import os
import re
import json
import threading
from typing import Dict, Any, List, Optional, Tuple
from app.rule_context import (
    PROFESSIONAL_ALIASES,
    DYNAMIC_UNIVERSAL_TOKENS,
    RuleContext,
    canonicalize_professional,
    normalize_professional_category,
    dynamic_scope_matches,
    dynamic_rule_matches_query,
)

logger = logging.getLogger(__name__)

CONFIG_DIR = os.getenv("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))

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
scoped_aliases_dict = load_json_config("scoped_aliases.json", {})
material_dict = load_json_config("material_dict.json", {})
connection_dict = load_json_config("connection_dict.json", {})
flags_dict = load_json_config("flags_dict.json", {})
unit_rules = load_json_config("unit_rules.json", {})

alias_rx, alias_map = None, {}
scoped_alias_compiled: Dict[str, Tuple[Any, Dict[str, str], Dict[str, str]]] = {}
material_rx, material_map = None, {}
connection_rx, connection_map = None, {}
flags_rx, flags_map = None, {}
unit_rx_list = []

_alias_hit_lock = threading.Lock()
_session_alias_hits: Dict[Tuple[str, str], int] = {}


def reset_session_alias_hits() -> None:
    with _alias_hit_lock:
        _session_alias_hits.clear()


def get_session_alias_hits() -> Dict[Tuple[str, str], int]:
    with _alias_hit_lock:
        return dict(_session_alias_hits)


def _record_alias_hit(discipline: str, source_key: str) -> None:
    with _alias_hit_lock:
        k = (discipline, source_key)
        _session_alias_hits[k] = _session_alias_hits.get(k, 0) + 1


def _build_compiled_regex(d: dict):
    if not d:
        return None, {}
    lower_map = {str(k).lower(): str(v) for k, v in d.items() if isinstance(k, str) and k}
    if not lower_map:
        return None, {}
    sorted_keys = sorted(
        (k for k in d.keys() if isinstance(k, str) and k),
        key=len,
        reverse=True,
    )
    pattern = "|".join(map(re.escape, sorted_keys))
    rx = re.compile(pattern, re.IGNORECASE)
    return rx, lower_map


# V017-M1 / M1-C02：跨品类脏别名映射黑名单（单一事实源，供 compile_configs 过滤）。
# config/alias_dict.json 属外层受保护资产（禁止改写），其中混入若干把"某品类名词"
# 整体替换为"另一品类名词"的错误映射，会把 query/candidate 文本清洗成无关品类，
# 污染召回与属性抽取（例如"医用不锈钢污洗池 -> 角阀"导致召回阀门、"电力电缆头 ->
# 排风机控制盒"、"UPVC球阀 -> WDZC-BVR 6"跨品类）。这些脏映射在代码层跳过，
# 保留原始词，config 文件本身保持 0 字节变化。
#
# 注意（M1 基准回归教训）：仅过滤"跨品类"映射。alias_dict 中另有大量"同品类过度具体化"
# 映射（如"控制线 -> WDZB-RYY 7*1"、"光纤 -> GJPFJV型8芯单模室内光缆"、"网络跳线 ->
# 非屏蔽超五类RJ45跳线(3米)"），它们虽过度具体化，但实测能帮助召回正确候选
# （GT-0318/0319 控制线、GT-0246 网络跳线均依赖这些映射召回），过滤会引入新退化。
# 这类映射有召回价值且不跨品类，不在 M1 范围贸然过滤，另行登记交统筹裁决。
CROSS_DOMAIN_ALIAS_BLOCKLIST = frozenset({
    # 跨品类（源词与目标词属不同品类，100% 有害）：
    "医用不锈钢污洗池",   # -> 角阀（污洗池 -> 阀门）
    "电力电缆头",         # -> 排风机控制盒（电缆 -> 排风机）
    "管道金属支架",       # -> 铜制焊接维修阀（支架 -> 阀门）
    "UPVC球阀",           # -> WDZC-BVR 6（球阀 -> 电缆型号）
    "不锈钢防虫网罩",     # -> WDZC-BVR 25（网罩 -> 电缆型号）
})


def compile_configs():
    global alias_rx, alias_map, scoped_alias_compiled, material_rx, material_map, connection_rx, connection_map, flags_rx, flags_map, unit_rx_list

    # 过滤跨品类脏别名映射（不直接改写受保护的 config/alias_dict.json）
    _safe_alias_dict = {
        k: v for k, v in alias_dict.items()
        if str(k).strip() not in CROSS_DOMAIN_ALIAS_BLOCKLIST
    } if isinstance(alias_dict, dict) else alias_dict

    new_alias_rx, new_alias_map = _build_compiled_regex(_safe_alias_dict)
    new_scoped: Dict[str, Tuple[Any, Dict[str, str], Dict[str, str]]] = {}
    if isinstance(scoped_aliases_dict, dict):
        for raw_disc, mapping in scoped_aliases_dict.items():
            if not isinstance(raw_disc, str) or not isinstance(mapping, dict):
                continue
            canon_disc = canonicalize_professional(raw_disc)
            if not canon_disc or canon_disc.lower() in DYNAMIC_UNIVERSAL_TOKENS or canon_disc == "其它":
                continue
            rx, lmap = _build_compiled_regex(mapping)
            if rx is not None:
                orig_key_map = {k.lower(): k for k in mapping.keys() if isinstance(k, str) and k}
                new_scoped[canon_disc] = (rx, lmap, orig_key_map)

    new_material_rx, new_material_map = _build_compiled_regex(material_dict)
    new_connection_rx, new_connection_map = _build_compiled_regex(connection_dict)
    new_flags_rx, new_flags_map = _build_compiled_regex(flags_dict)
    
    new_unit_rx_list = []
    for raw_unit, standard in unit_rules.items():
        # V017-M1 / M1-C02：外层 config/unit_rules.json 只读，禁止改写；代码层过滤
        # 把 Φ/Ф 误映射为 DN 的条目（外径族前缀，不应被单位归一化暗转为公称直径），
        # 保证 DN 与 De/Φ/外径分字段。
        if raw_unit in ("Ф", "Φ") and str(standard).upper() == "DN":
            continue
        pattern = re.compile(r'(?<=\d)\s*' + re.escape(raw_unit) + r'(?=\b|[^a-zA-Z]|$)', re.IGNORECASE)
        new_unit_rx_list.append((pattern, standard))

    # Atomic assignment
    alias_rx, alias_map = new_alias_rx, new_alias_map
    scoped_alias_compiled = new_scoped
    material_rx, material_map = new_material_rx, new_material_map
    connection_rx, connection_map = new_connection_rx, new_connection_map
    flags_rx, flags_map = new_flags_rx, new_flags_map
    unit_rx_list = new_unit_rx_list

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

def _build_cleaning_rule_context(
    discipline: Optional[str],
    category: Optional[str],
    rule_context: Optional[RuleContext] = None,
) -> RuleContext:
    if rule_context is not None:
        return rule_context
    return RuleContext.from_fields(
        professional=discipline or "",
        material_category=category or "",
        stage="cleaning",
    )


def _cleaning_rule_in_scope(
    rule: Dict[str, Any],
    discipline: Optional[str],
    category: Optional[str],
    *,
    rule_context: Optional[RuleContext] = None,
) -> bool:
    ctx = _build_cleaning_rule_context(discipline, category, rule_context)
    return ctx.matches_rule(rule)


def _apply_dynamic_cleaning_pass(
    text: str,
    discipline: Optional[str],
    category: Optional[str],
    phase: str,
    *,
    rule_context: Optional[RuleContext] = None,
    raise_on_error: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    ctx = _build_cleaning_rule_context(discipline, category, rule_context)
    diagnostics: Dict[str, Any] = {
        "stage": phase,
        "context": ctx.to_scope_dict(),
        "applied_rules": [],
        "skipped_rules": [],
        "errors": [],
        "degraded": False,
    }
    if not text:
        return text, diagnostics

    try:
        from app.llm_rule_engine import LLMRuleManager, DynamicRuleInterpreter, RuleHitStatsTracker
        active_rules = LLMRuleManager.get_active_rules()
    except Exception as exc:
        logger.error("Dynamic %s cleaning rules load failed (%s): %s", phase, type(exc).__name__, exc)
        diagnostics["errors"].append({
            "rule_id": "*",
            "phase": phase,
            "error_type": type(exc).__name__,
            "message": str(exc),
        })
        diagnostics["degraded"] = True
        if raise_on_error:
            raise
        return text, diagnostics

    for r in active_rules:
        rid = str(r.get("rule_id") or r.get("id") or "?")
        try:
            scope_res = ctx.evaluate_rule(r)
            if not scope_res.matched:
                diagnostics["skipped_rules"].append({
                    "rule_id": rid,
                    "phase": phase,
                    "reason": scope_res.reason,
                })
                continue
            dsl = r.get("parsed_dsl")
            if dsl is None:
                dsl = {}
            if not isinstance(dsl, dict):
                raise ValueError(f"动态清洗规则 {rid} parsed_dsl 结构非法")
            cleaning_rules = dsl.get("cleaning_rules", [])
            if cleaning_rules is None:
                cleaning_rules = []
            if not isinstance(cleaning_rules, list):
                raise ValueError(f"动态清洗规则 {rid} cleaning_rules 结构非法")
            if cleaning_rules:
                text_before = text
                text, _actions = DynamicRuleInterpreter.apply_cleaning_rules(text, cleaning_rules)
                diagnostics["applied_rules"].append({"rule_id": rid, "phase": phase})
                if text != text_before and r.get("rule_id"):
                    RuleHitStatsTracker().record_hit(r["rule_id"])
        except Exception as exc:
            logger.error("Dynamic %s cleaning rule %s failed (%s): %s", phase, rid, type(exc).__name__, exc)
            diagnostics["errors"].append({
                "rule_id": rid,
                "phase": phase,
                "error_type": type(exc).__name__,
                "message": str(exc),
            })
            diagnostics["degraded"] = True
            if raise_on_error:
                raise
    return text, diagnostics


def apply_dynamic_cleaning_rules_with_context(
    text: str,
    *,
    rule_context: RuleContext,
    phase: str = "spec-cleaning",
    raise_on_error: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """Apply ONLY dynamic DSL cleaning rules under an explicit RuleContext (no static dict mutation)."""
    if not text or not isinstance(text, str):
        return "", {
            "stage": phase,
            "context": rule_context.to_scope_dict(),
            "applied_rules": [],
            "skipped_rules": [],
            "errors": [],
            "degraded": False,
        }
    return _apply_dynamic_cleaning_pass(
        text,
        None,
        None,
        phase,
        rule_context=rule_context,
        raise_on_error=raise_on_error,
    )


def clean_text_standard_with_diagnostics(
    text: str,
    discipline: Optional[str] = None,
    category: Optional[str] = None,
    *,
    rule_context: Optional[RuleContext] = None,
    raise_on_error: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """Standardize text and return both cleaned string and RuleContext execution diagnostics."""
    ctx = _build_cleaning_rule_context(discipline, category, rule_context)
    merged_diag: Dict[str, Any] = {
        "context": ctx.to_scope_dict(),
        "applied_rules": [],
        "applied_aliases": [],
        "skipped_rules": [],
        "errors": [],
        "degraded": False,
    }
    if not text or not isinstance(text, str):
        return "", merged_diag

    # 0. Apply dynamic LLM DSL cleaning rules on raw structured input (e.g. multi-line manifests)
    text, pre_diag = _apply_dynamic_cleaning_pass(
        text, discipline, category, "pre-normalization",
        rule_context=ctx, raise_on_error=raise_on_error,
    )
    for k in ("applied_rules", "skipped_rules", "errors"):
        merged_diag[k].extend(pre_diag[k])
    merged_diag["degraded"] = merged_diag["degraded"] or pre_diag["degraded"]

    # 1. Full-width to half-width & remove invisible control characters (except \n and \r)
    text = to_half_width(text)
    text = re.sub(r'[\u0000-\u0009\u000b\u000c\u000e-\u001f\u007f-\u009f]', '', text)

    # 2. Normalize spaces (horizontal only to preserve newlines)
    text = re.sub(r'[ \t]+', ' ', text)

    # 3. Apply discipline-scoped aliases (concrete RuleContext only) + frozen baseline alias dictionary
    canon_prof = ctx.normalized_professional or ""
    if canon_prof and canon_prof.lower() not in DYNAMIC_UNIVERSAL_TOKENS and canon_prof != "其它":
        scoped_entry = scoped_alias_compiled.get(canon_prof)
        if scoped_entry is not None:
            s_rx, s_map, s_orig_keys = scoped_entry
            def _scoped_repl(m):
                low = m.group(0).lower()
                orig_k = s_orig_keys.get(low, low)
                _record_alias_hit(canon_prof, orig_k)
                merged_diag["applied_aliases"].append({"discipline": canon_prof, "source": orig_k})
                return s_map.get(low, m.group(0))
            text = s_rx.sub(_scoped_repl, text)

    if alias_rx:
        def _base_alias_repl(m):
            low = m.group(0).lower()
            _record_alias_hit("", low)
            merged_diag["applied_aliases"].append({"discipline": "", "source": low})
            return alias_map.get(low, m.group(0))
        text = alias_rx.sub(_base_alias_repl, text)

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

    # 7. Apply dynamic LLM DSL cleaning rules (post-normalization pass)
    text, post_diag = _apply_dynamic_cleaning_pass(
        text, discipline, category, "post-normalization",
        rule_context=ctx, raise_on_error=raise_on_error,
    )
    for k in ("applied_rules", "skipped_rules", "errors"):
        merged_diag[k].extend(post_diag[k])
    merged_diag["degraded"] = merged_diag["degraded"] or post_diag["degraded"]

    return text.strip(), merged_diag


def clean_text_standard(
    text: str,
    discipline: Optional[str] = None,
    category: Optional[str] = None,
    *,
    rule_context: Optional[RuleContext] = None,
    raise_on_error: bool = False,
) -> str:
    """
    Apply character cleaning, full-to-half conversion, lowercase normalization,
    and alias synonym replacements on raw text.
    """
    cleaned, _diag = clean_text_standard_with_diagnostics(
        text,
        discipline=discipline,
        category=category,
        rule_context=rule_context,
        raise_on_error=raise_on_error,
    )
    return cleaned

def reload_normalizer_configs():
    global CONFIG_DIR, alias_dict, scoped_aliases_dict, material_dict, connection_dict, flags_dict, unit_rules
    CONFIG_DIR = os.getenv("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))
    alias_dict = load_json_config("alias_dict.json", {})
    scoped_aliases_dict = load_json_config("scoped_aliases.json", {})
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

