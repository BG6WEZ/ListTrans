# -*- coding: utf-8 -*-
"""V017-M1 / F26：统一类型化字段结构与字段白名单单一事实源。

职责：
1. 提供 `build_typed_attrs`：把扁平 `attrs` 包装为结构化 `typed_attrs`（每个字段含
   value/unit/operator/min_val/max_val/source/source_span/confidence/status），其中
   status 区分 known / negated / unknown。
2. 提供 `FIELD_WHITELIST` 与 `partition_whitelisted_attrs`：把未命中受控白名单的
   自由文本键隔离至 `unwhitelisted_attrs`，严禁非白名单噪声键进入 `attrs` 参与
   `app/matcher.py` 属性打分。

设计约束（低侵入、向后兼容）：
- `attrs` 顶层保持原有扁平语义，仅做"白名单过滤"，不改变命中键的取值形态；
- `typed_attrs` 是**新增并行结构**，matcher 仍读 `attrs`，故不破坏现有打分链路；
- 白名单以 `config/domain_schema.json` 的 must/should key + flags 为基础，叠加一组
   跨域通用几何/物理字段与既有别名键，避免把合法字段误判为噪声。
"""
import os
import json
import re
from typing import Any, Dict, List, Set

from app.domain_extractors import domain_schema, detect_three_state_flag

CONFIG_DIR = os.getenv("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))

# ---------------------------------------------------------------------------
# 字段白名单
# ---------------------------------------------------------------------------

# 通用几何/物理/元信息字段：跨域存在、不随 domain_schema 枚举而缺失。
_COMMON_WHITELIST_KEYS = {
    # 几何
    "尺寸", "长", "宽", "高", "长度", "半周长", "周长", "面积", "厚度", "壁厚", "板材厚度",
    "涂膜厚度", "缸体直径", "缸体长度",
    # 管材/口径
    "管径", "口径", "外径", "外径mm", "dn_val", "de_val", "水侧管径", "管型代码", "管代号",
    "管型名称", "规格数字", "dn", "公称直径DN", "耐压", "敷设方式",
    # 物理/性能
    "功率", "风量", "送风量", "新风量", "排风量", "处理风量", "循环风量", "余压", "机外余压",
    "静压", "风压", "扬程", "流量", "制冷量", "制热量", "加湿量", "量程", "转速", "速度",
    "噪音", "消声量", "换热面积", "后备时间", "载重", "容量", "电压", "电压等级", "电流",
    "额定电流", "额定风量", "压力", "设计压力", "执行器扭矩",
    # 材质/品牌/单位/型式
    "材质", "品牌", "单位", "型式", "功能", "型号", "型号族", "设备类型", "机型", "类型",
    "model",  # extract_switchgear_params 产出的英文键（与 型号 同义）
    "阀型", "机组类型", "系统形式", "结构形式", "控制方式", "连接方式", "安装形式",
    "开启形式", "散流形式", "封头形式", "消声形式", "敷设方式代码", "屏蔽类型",
    # 过滤/介质/盘管
    "过滤等级", "过滤段", "过滤效率", "机组过滤器", "介质", "盘管排数", "回路数", "接口数",
    "层站", "粒径", "护套颜色", "颜色", "密封面", "防火等级", "熔断温度",
    # 线缆
    "芯数", "截面积", "主芯数", "主截面", "零芯数", "零截面",
    # 三态布尔特征（matcher flags 打分依赖，True/False）
    "提升泵", "带冷凝水提升泵", "自取新风", "循环机组", "吊装", "落地", "卡式", "卧式",
    "电动", "静音", "防爆",
    # V017-M1 / M1-C02：工程设备位号/箱号
    "tag_id", "box_type",
    # 电气/开关/控制（硬编码产出字段）
    "power", "power_min", "power_max", "gangs", "control", "多联机",
    # 其他已落盘通用键
    "名称", "规格", "形状", "is_circular", "尺寸_2d", "尺寸_类型", "余压",
}

# 白名单计算：domain_schema 全量 must/should key + flags + 通用键。
def _collect_schema_keys() -> Set[str]:
    keys: Set[str] = set(_COMMON_WHITELIST_KEYS)
    if isinstance(domain_schema, dict):
        for _domain, cfg in domain_schema.items():
            if not isinstance(cfg, dict):
                continue
            for field_list in (cfg.get("must"), cfg.get("should")):
                if isinstance(field_list, list):
                    for f in field_list:
                        if isinstance(f, dict) and f.get("key"):
                            keys.add(f["key"])
            for flag in (cfg.get("flags") or []):
                keys.add(flag)
    return keys


FIELD_WHITELIST: Set[str] = _collect_schema_keys()


def reload_typed_field_configs() -> None:
    """重新计算白名单（供运行时配置重载使用）。"""
    global FIELD_WHITELIST
    FIELD_WHITELIST = _collect_schema_keys()


# 动态派生后缀键：数值字段会生成 `_min/_max/_op/_base`，均属合法受控键。
_DERIVED_SUFFIXES = ("_min", "_max", "_op", "_base")


def is_whitelisted_key(key: str) -> bool:
    """判定单一 attrs 键是否受控白名单（含派生后缀键）。"""
    if not key:
        return False
    if key in FIELD_WHITELIST:
        return True
    for suffix in _DERIVED_SUFFIXES:
        if key.endswith(suffix) and key[: -len(suffix)] in FIELD_WHITELIST:
            return True
    return False


def partition_whitelisted_attrs(attrs: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """把 attrs 切分为 (whitelisted, unwhitelisted)。

    白名单命中键保留在 `attrs` 参与打分；未命中键（自由文本噪声，如施工描述、
    无冒号冗余短句）隔离至 `unwhitelisted_attrs`，仅供展示/追溯。
    """
    whitelisted: Dict[str, Any] = {}
    unwhitelisted: Dict[str, Any] = {}
    for k, v in attrs.items():
        if is_whitelisted_key(k):
            whitelisted[k] = v
        else:
            unwhitelisted[k] = v
    return whitelisted, unwhitelisted


# ---------------------------------------------------------------------------
# 类型化字段结构
# ---------------------------------------------------------------------------

# 三态否定：known / negated / unknown
_NEGATION_PREFIXES = ("不带", "非", "无", "不含", "未配", "无配", "不具有", "没有")


def _typed_status(value: Any) -> str:
    """根据取值推断字段 status：显式否定→negated；缺省/None→unknown；否则 known。

    布尔 False 与显式否定词均视为 negated；None / 空串 / 空集 视为 unknown。
    """
    if isinstance(value, bool):
        return "negated" if value is False else "known"
    if value is None:
        return "unknown"
    if isinstance(value, str):
        if not value.strip():
            return "unknown"
        for prefix in _NEGATION_PREFIXES:
            if value.startswith(prefix):
                return "negated"
        return "known"
    if isinstance(value, (list, tuple, set, dict)):
        return "known" if len(value) > 0 else "unknown"
    return "known"


def _to_finite_float(value: Any):
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return num


def _build_one_typed(key: str, value: Any, raw_text: str) -> Dict[str, Any]:
    """为单个 attrs 键构造类型化字段描述。"""
    entry: Dict[str, Any] = {
        "key": key,
        "value": value,
        "status": _typed_status(value),
        "source": "parse",
        "source_span": None,
        "confidence": 1.0 if _typed_status(value) == "known" else None,
        "unit": None,
        "operator": None,
        "min_val": None,
        "max_val": None,
    }

    # 数值字段：从派生后缀键补充 operator / min_val / max_val（在 build_typed_attrs 中回填）
    # 单位：白名单字段的原始单位信息无法可靠还原，仅在存在 *_op 时由上层回填。
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        entry["min_val"] = value
        entry["max_val"] = value

    # 否定三态：布尔 False 已在 status 处理；显式否定字符串保留原文 value。
    if isinstance(value, str) and value.strip():
        for prefix in _NEGATION_PREFIXES:
            if value.startswith(prefix):
                entry["operator"] = "not"
                break

    return entry


def build_typed_attrs(
    attrs: Dict[str, Any],
    *,
    raw_text: str = "",
    domain: str = "",
) -> Dict[str, Any]:
    """把扁平 `attrs` 包装为结构化 `typed_attrs`。

    每个字段含 value/unit/operator/min_val/max_val/source/source_span/confidence/status。
    派生后缀键（`_min/_max/_op/_base`）不单独成条，而是回填到主键的 operator/min_val/max_val。
    """
    if not attrs:
        return {}

    typed: Dict[str, Any] = {}

    # 先收集派生后缀键，用于回填主键
    for key, value in attrs.items():
        for suffix in _DERIVED_SUFFIXES:
            if key.endswith(suffix):
                base = key[: -len(suffix)]
                entry = typed.setdefault(base, _build_one_typed(base, attrs.get(base), raw_text))
                if suffix == "_min":
                    entry["min_val"] = value
                elif suffix == "_max":
                    entry["max_val"] = value
                elif suffix == "_op":
                    entry["operator"] = value
                elif suffix == "_base":
                    entry["base_val"] = value
                break

    for key, value in attrs.items():
        if key in typed:
            continue
        if any(key.endswith(suffix) for suffix in _DERIVED_SUFFIXES):
            continue
        typed[key] = _build_one_typed(key, value, raw_text)

    return typed


def build_schema_version_payload(
    *,
    domain: str,
    model: str,
    attrs: Dict[str, Any],
    typed_attrs: Dict[str, Any],
    unwhitelisted_attrs: Dict[str, Any],
    schema_version: int = 3,
) -> Dict[str, Any]:
    """构造版本化解析载荷（parsed_params 顶层结构，M1-C03 schema_version=3）。"""
    return {
        "schema_version": schema_version,
        "domain": domain,
        "model": model,
        "attrs": attrs,
        "typed_attrs": typed_attrs,
        "unwhitelisted_attrs": unwhitelisted_attrs,
    }
