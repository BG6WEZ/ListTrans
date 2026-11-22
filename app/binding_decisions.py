# -*- coding: utf-8 -*-
"""
V015-M2：服务端权威绑定决策状态与人工确认记录。

职责：
1. 定义统一的绑定决策状态/来源契约（推荐/待复核、自动接受、人工确认、拒绝），
   与 matcher 的算法提示 is_exact 解耦；is_exact、首选候选、单一分数都不是
   “已确认/可导出”凭据。
2. 提供内容指纹、硬冲突安全复核、当前有效物料编码新鲜校验等纯服务层函数，
   复用 matcher 已公开的纯函数，不修改 matcher 的权重/召回/阈值/金标。
3. 在账号库维护服务端人工确认/拒绝记录表 binding_decisions，导出/确认均以
   该表与新鲜复核为准，客户端自填 confirmed/accepted 不被信任。

测试隔离：账号库经 AUTH_DB_PATH 环境变量重定向；物料库经 MATERIALS_DB_PATH
重定向，新鲜校验每次新建连接，不复用 query_by_code 的 lru_cache。
"""
import hashlib
import json
import math
import re
from typing import Any, Dict, List, Optional

from app.auth_manager import get_auth_connection
from app.db_manager import catalog_read, get_db_connection
from app.matcher import (
    _to_finite_float,
    check_unit_compatibility,
    get_unit_multiplier,
    standardize_brand,
    get_professional_category,
    normalize_professional_category,
    resolve_material_brand,
    resolve_material_attrs,
    parse_material_attrs,
    normalize_material_grade,
    KNOWN_MATERIAL_GRADES,
    check_dn_od_conflicts,
    detect_three_state_flag,
    check_pump_conflict,
    evaluate_dynamic_exclusions,
    DYNAMIC_RULE_UNAVAILABLE_PREFIX,
    extract_query_params,
)

# ---------------------------------------------------------------------------
# 决策状态常量（后端、前端、保存恢复、重算、导出共用同一语义）
# ---------------------------------------------------------------------------
# 推荐/待复核：算法或旧规则给出的候选建议，未经服务端接受或人工确认，不可直接导出
STATUS_RECOMMENDED = "recommended"
# 自动接受：满足规格/专业等逐项约束且当前编码全部有效的规则命中，由服务端接受
STATUS_AUTO_ACCEPTED = "auto_accepted"
# 人工确认：用户显式动作后，通过服务端有效性与硬冲突核验的确认
STATUS_MANUAL_CONFIRMED = "manual_confirmed"
# 拒绝：人工明确拒绝，或硬冲突阻断；不可导出
STATUS_REJECTED = "rejected"

EXPORTABLE_STATUSES = (STATUS_AUTO_ACCEPTED, STATUS_MANUAL_CONFIRMED)
REVIEW_STATUSES = (STATUS_RECOMMENDED, STATUS_REJECTED)

# 决策来源
SOURCE_MATCHER = "matcher"            # matcher 算法推荐
SOURCE_RULE = "auto_binding_rule"     # 自动绑定规则（名称-only 旧规则 / 约束规则）
SOURCE_MANUAL = "manual"              # 人工确认
SOURCE_MANUAL_REJECT = "manual_reject"  # 人工拒绝
SOURCE_MANUAL_EDIT = "manual_edit"      # 编辑后须重新确认
SOURCE_RESTORE = "legacy_draft"       # 旧草稿保守降级


def init_binding_decision_db():
    """在账号库创建人工确认/拒绝记录表（幂等）。"""
    conn = get_auth_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS binding_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                scope_key TEXT NOT NULL,
                row_fingerprint TEXT NOT NULL,
                binding_fingerprint TEXT NOT NULL,
                decision TEXT NOT NULL,
                source TEXT NOT NULL,
                reason TEXT,
                matched_codes TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_binding_decisions_user_scope "
            "ON binding_decisions(user_id, scope_key)"
        )
        conn.commit()
    finally:
        conn.close()


def clear_binding_decisions(user_id: Optional[int] = None):
    """系统重置/测试清理：默认清空全部决策；给定 user_id 时仅清该账号。"""
    conn = get_auth_connection()
    try:
        if user_id is None:
            conn.execute("DELETE FROM binding_decisions")
        else:
            conn.execute("DELETE FROM binding_decisions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 指纹与归一化
# ---------------------------------------------------------------------------
def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_number(value: Any) -> Any:
    """等价数值使用同一表示，避免 Pydantic 的 200.0 与草稿 200 产生不同指纹。"""
    if value is None or isinstance(value, bool):
        return value
    try:
        return format(float(value), ".12g")
    except (TypeError, ValueError, OverflowError):
        return value


def normalize_bindings(
    bindings: List[Dict[str, Any]], original_qty: Any = None
) -> List[Dict[str, Any]]:
    """统一确认、恢复和导出使用的绑定数量/比例，不丢弃一对多数量语义。"""
    try:
        base_qty = float(original_qty) if original_qty is not None else 0.0
    except (TypeError, ValueError, OverflowError):
        base_qty = 0.0
    normalized = []
    for binding in bindings or []:
        qty = float(binding.get("qty") or 0.0)
        raw_ratio = binding.get("qty_ratio")
        if raw_ratio is None:
            ratio = qty / base_qty if base_qty > 0 else 1.0
        else:
            ratio = float(raw_ratio)
        normalized.append({**binding, "qty": qty, "qty_ratio": ratio})
    return normalized


def row_content_fingerprint(item: Dict[str, Any]) -> str:
    """原始清单行内容指纹：名称/规格/数量/单位/损耗/品类/品牌/专业/原编码。

    任一影响匹配的字段或数量变化都会改变指纹，使旧确认无法被静默沿用。
    """
    payload = {
        "original_name": str(item.get("original_name", "")).strip(),
        "original_spec": str(item.get("original_spec", "")).strip(),
        "original_qty": _canonical_number(item.get("original_qty")),
        "original_unit": str(item.get("original_unit", "")).strip(),
        "original_loss": _canonical_number(item.get("original_loss")),
        "original_category": str(item.get("original_category", "")).strip(),
        "original_brand": str(item.get("original_brand", "")).strip(),
        "professional": str(item.get("professional", "") or "暖通").strip(),
        "original_code": str(item.get("original_code", "")).strip(),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def binding_fingerprint(bindings: List[Dict[str, Any]]) -> str:
    """目标绑定集合指纹：编码集合 + 数量/比例，按编码排序后哈希，覆盖一对多。"""
    norm = [
        {
            "code": str(b.get("code", "")).strip(),
            "qty": _canonical_number(b.get("qty")),
            "qty_ratio": _canonical_number(b.get("qty_ratio")),
        }
        for b in (bindings or [])
    ]
    norm.sort(key=lambda x: x["code"])
    return hashlib.sha256(_canonical_json(norm).encode("utf-8")).hexdigest()


def make_scope_key(original_filename: Optional[str], professional: Optional[str]) -> str:
    """工作范围键：同一账号内按原始文件名 + 专业隔离确认记录。"""
    return f"{(original_filename or '').strip()}|{(professional or '').strip()}"


# ---------------------------------------------------------------------------
# 当前有效编码新鲜校验（独立连接，不用 lru_cache）
# ---------------------------------------------------------------------------
@catalog_read
def fetch_live_material_map(codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """用独立新鲜连接批量取当前活动 materials 行；不存在的编码不出现在结果中。"""
    clean_codes = [str(c).strip() for c in codes if str(c or "").strip()]
    if not clean_codes:
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        unique_codes = list(dict.fromkeys(clean_codes))
        # 分批避免 SQL 变量上限
        batch = 400
        for i in range(0, len(unique_codes), batch):
            chunk = unique_codes[i:i + batch]
            placeholders = ",".join("?" for _ in chunk)
            rows = cur.execute(
                f"SELECT * FROM materials WHERE code IN ({placeholders})", chunk
            ).fetchall()
            for r in rows:
                d = dict(r)
                result[d["code"]] = d
    finally:
        conn.close()
    return result


def validate_binding_codes(bindings: List[Dict[str, Any]]) -> List[str]:
    """返回失效/缺失编码列表；空列表表示全部当前有效。"""
    codes = [str(b.get("code", "")).strip() for b in (bindings or []) if str(b.get("code", "")).strip()]
    if not codes:
        return []
    live = fetch_live_material_map(codes)
    return [c for c in dict.fromkeys(codes) if c not in live]


# ---------------------------------------------------------------------------
# 硬冲突安全复核（服务层复用 matcher 纯函数，不改 matcher）
# ---------------------------------------------------------------------------
def evaluate_hard_conflicts(
    row: Dict[str, Any],
    material: Dict[str, Any],
    binding: Optional[Dict[str, Any]] = None,
    allow_unit_conversion: bool = False,
    quantity_multiplier: float = 1.0,
) -> Optional[str]:
    """对单个目标物料做硬冲突复核，返回阻断原因；无冲突返回 None。

    V015-M3 / R02：与 matcher 初始硬冲突门、评分管线及动态规则同源，全部经
    app.matcher 重导出的共享纯函数判定，推荐/规则接受/人工确认/恢复/导出全链路复用：
    1. 专业：别名归一后比较（医用气体=医气），候选“其它”/查询“全部、其它”不臆断；
    2. 品牌：品牌列空时解析 parsed_params.attrs.品牌 真实字符串，缺值/坏 JSON 为 unknown；
    3. 单位：物理维度隔离；跨名单位无可靠倍率阻断；非1倍率工程量在自动规则（静默接受）
       与未按倍率换算时一律阻断（防 10 km→10 m 原数照搬）；允许显式换算的人工/恢复/导出
       链路在服务端严格核验目标单位及 qty/qty_ratio；
    4. DN/外径分字段（缺/坏 parsed_params 同源回退原文抽取）、304/316L 材质枚举、
       提升泵双向互斥（不带泵 vs 带泵 及 带泵 vs 不带泵）、防爆双向互斥；
    5. 动态规则硬排除（must_not_contain / must_contain）：与 matcher 同源全链路不可逆阻断。
    """
    q_name = str(row.get("original_name", "") or "")
    unit_multiplier = _row_unit_multiplier(row)
    q_spec = str(row.get("original_spec", "") or "")
    q_text = f"{q_name} {q_spec}".strip()
    c_name = str(material.get("name", "") or "")
    c_spec = str(material.get("specification", "") or "")
    c_desc = str(material.get("description", "") or "")
    c_text = f"{c_spec} {c_desc}".strip()
    c_pump_text = f"{c_name} {c_text}".strip()

    # 1. 专业冲突：编号前缀剥离 + 别名归一，与 matcher._check_initial_hard_conflicts 同源
    q_prof = str(row.get("professional", "") or "").strip()
    if q_prof:
        q_prof = re.sub(r'^\d+[\.．\s]*', '', q_prof).strip()
    if q_prof and q_prof not in ("全部", "其它"):
        c_prof = get_professional_category(material.get("category", ""), c_text)
        if c_prof != "其它" and normalize_professional_category(q_prof) != normalize_professional_category(c_prof):
            return "专业冲突阻断"

    # 2. 品牌冲突：候选品牌列空时回退 parsed_params.attrs.品牌 真实值；任一缺值=unknown 不阻断
    q_brand = str(row.get("original_brand", "") or "").strip()
    c_brand = resolve_material_brand(material)
    if q_brand and c_brand:
        q_brand_std = standardize_brand(q_brand)
        c_brand_std = standardize_brand(c_brand)
        if q_brand_std and c_brand_std and q_brand_std != c_brand_std:
            return "品牌冲突阻断"

    # 3. 单位：物理维度隔离 + 可靠倍率校验 + 非1倍率防原数照搬服务端工程量核验
    q_unit = str(row.get("original_unit", "") or "").strip()
    c_unit = str(material.get("unit", "") or "").strip()
    if q_unit and c_unit:
        if not check_unit_compatibility(q_unit, c_unit):
            return "单位不可换算阻断"
        mult = unit_multiplier(q_unit, c_unit)
        if mult is None:
            return "单位倍率不可靠阻断"
        if binding is not None:
            b_unit = str(binding.get("unit", "") or "").strip()
            if b_unit:
                b_mult = unit_multiplier(b_unit, c_unit)
                if b_mult is None or abs(b_mult - 1.0) > 1e-6:
                    return "绑定目标单位与实时物料单位不一致阻断"
            elif abs(mult - 1.0) > 1e-9 and allow_unit_conversion:
                return "单位非1倍率绑定缺失目标单位阻断"
        if abs(mult - 1.0) > 1e-9:
            if not allow_unit_conversion or binding is None:
                return "单位非1倍率工程量需按目标单位显式换算复核阻断"
            orig_qty = _to_finite_float(row.get("original_qty"))
            if orig_qty is None:
                orig_qty = _to_finite_float(row.get("qty"))
            if orig_qty is None or orig_qty <= 0:
                return "单位非1倍率原行工程量无效阻断"
            b_qty = _to_finite_float(binding.get("qty"))
            b_ratio = _to_finite_float(binding.get("qty_ratio"))
            if b_qty is None or b_ratio is None:
                return "单位非1倍率工程量未按目标单位换算阻断"
            expected_ratio = mult * quantity_multiplier
            expected_qty = orig_qty * expected_ratio
            if (
                abs(b_ratio - expected_ratio) > 1e-6 * max(1.0, abs(expected_ratio))
                or abs(b_qty - expected_qty) > 1e-6 * max(1.0, abs(expected_qty))
            ):
                return "单位非1倍率工程量未按目标单位换算阻断"

    # 查询侧与候选侧参数同源抽取（严格区分 professional 与 material_category）
    q_parsed = extract_query_params(
        q_name,
        q_spec,
        row.get("professional") or "",
        material_category=row.get("original_category") or "",
    )
    q_diag = q_parsed.get("rule_diagnostics") if isinstance(q_parsed, dict) else None
    if isinstance(q_diag, dict) and q_diag.get("degraded") and q_diag.get("errors"):
        return f"动态规则不可用阻断: {q_diag['errors'][0].get('message', '预处理规则异常')}"
    q_attrs = q_parsed.get("attrs", {}) if isinstance(q_parsed, dict) else {}
    c_attrs = resolve_material_attrs(material)

    # 4. DN/外径分字段硬约束（跨字段不比、缺值 unknown 不阻断、15=15.0 数值归一）
    dn_od_reason = check_dn_od_conflicts(q_attrs, c_attrs)
    if dn_od_reason:
        return dn_od_reason

    # 5. 不锈钢牌号枚举：双方已知且不同才硬隔离；一侧牌号未知保持 unknown 宽容
    q_mat = q_attrs.get("材质")
    c_mat = c_attrs.get("材质")
    if q_mat and c_mat and str(q_mat).lower() != str(c_mat).lower():
        q_grade = normalize_material_grade(q_mat)
        c_grade = normalize_material_grade(c_mat)
        if q_grade in KNOWN_MATERIAL_GRADES and c_grade in KNOWN_MATERIAL_GRADES and q_grade != c_grade:
            return "不锈钢牌号冲突硬隔离(304/316L)"

    # 6. 提升泵双向否定/要求三态（不带泵 vs 带泵，以及 带泵 vs 不带泵；unknown 不阻断）
    pump_conflict = check_pump_conflict(q_text, c_pump_text, q_attrs, c_attrs)
    if pump_conflict:
        return pump_conflict

    # 7. 防爆否定三态：抽取布尔优先、文本判定兜底；真肯定/否定互斥硬隔离，未说明 unknown
    q_ex_attr = q_attrs.get("防爆")
    if q_ex_attr is True:
        q_ex_state = True
    elif q_ex_attr is False:
        q_ex_state = False
    else:
        q_ex_state = detect_three_state_flag(
            q_text, ["防爆", "防爆型", "防爆风机", "EX"])
    c_ex_state = detect_three_state_flag(c_text, ["防爆", "防爆型", "防爆风机"])
    if q_ex_state is True and c_ex_state is False:
        return "防爆要求vs非防爆硬隔离"
    if q_ex_state is False and c_ex_state is True:
        return "非防爆要求vs防爆硬隔离"

    # 8. 动态规则硬排除（must_not_contain / must_contain 与 matcher 评分管线同源；规则不可用 fail-closed）
    dyn_reasons = evaluate_dynamic_exclusions(row, material)
    if dyn_reasons:
        if any(DYNAMIC_RULE_UNAVAILABLE_PREFIX in r for r in dyn_reasons):
            return f"动态规则不可用阻断: {'; '.join(dyn_reasons)}"
        return f"动态规则硬排除: {'; '.join(dyn_reasons)}"

    return None


UNIT_EVIDENCE_VERSION = "r04-v1"
UNIT_EVIDENCE_SOURCE = "server_live_material"
_AUTHORITATIVE_UNIT_EVIDENCE_KEYS = (
    "confirmed_unit",
    "confirmed_orig_unit",
    "confirmed_orig_qty",
    "confirmed_unit_mult",
    "confirmed_unit_source",
    "confirmed_unit_version",
)


def _to_strict_positive_float(value: Any) -> Optional[float]:
    """仅接受严格有限正数值（拒绝 bool/字符串/集合/NaN/<=0）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    if not math.isfinite(f) or f <= 0:
        return None
    return f


def _row_unit_multiplier(row):
    from app.bom import is_bom_name, unit_multiplier

    return unit_multiplier if is_bom_name(row.get("original_name")) else get_unit_multiplier


def _validate_saved_binding_unit_evidence(
    row: Dict[str, Any],
    sb: Optional[Dict[str, Any]],
    live_unit: str,
    req_unit: str,
    require_saved_unit_evidence: bool,
) -> Optional[str]:
    """V015-M3 R04：严格区分客户端 unit 与服务端权威确认单位证据来源。"""
    q_unit = str(row.get("original_unit", "") or row.get("unit", "") or "").strip()
    unit_multiplier = _row_unit_multiplier(row)
    raw_row_qty = row.get("original_qty")
    if raw_row_qty is None:
        raw_row_qty = row.get("qty")
    row_orig_qty = (
        None if isinstance(raw_row_qty, bool) else _to_finite_float(raw_row_qty)
    )
    if row_orig_qty is not None and row_orig_qty <= 0:
        row_orig_qty = None

    if require_saved_unit_evidence:
        # 强证据模式：绝不允许将历史或客户端 unit 降级/伪升格为 confirmed_unit
        if not isinstance(sb, dict):
            return "确认记录缺少权威目标单位证据阻断"

        raw_confirmed_unit = sb.get("confirmed_unit")
        if not isinstance(raw_confirmed_unit, str) or not raw_confirmed_unit.strip():
            return "确认记录缺少权威目标单位证据阻断"
        saved_unit = raw_confirmed_unit.strip()

        raw_orig_unit = sb.get("confirmed_orig_unit")
        if not isinstance(raw_orig_unit, str) or not raw_orig_unit.strip():
            return "确认记录缺少权威目标单位证据阻断"
        saved_orig_unit = raw_orig_unit.strip()

        prev_mult = _to_strict_positive_float(sb.get("confirmed_unit_mult"))
        if prev_mult is None:
            return "确认记录缺少权威目标单位证据阻断"

        if (
            "confirmed_unit_source" in sb
            and sb.get("confirmed_unit_source") != UNIT_EVIDENCE_SOURCE
        ):
            return "确认记录缺少权威目标单位证据阻断"
        if (
            "confirmed_unit_version" in sb
            and sb.get("confirmed_unit_version") != UNIT_EVIDENCE_VERSION
        ):
            return "确认记录缺少权威目标单位证据阻断"
        if "confirmed_orig_qty" in sb:
            saved_orig_qty = _to_strict_positive_float(sb.get("confirmed_orig_qty"))
            if saved_orig_qty is None:
                return "确认记录缺少权威目标单位证据阻断"
            if (
                row_orig_qty is not None
                and abs(saved_orig_qty - row_orig_qty) > 1e-6 * max(1.0, abs(row_orig_qty))
            ):
                return "确认后单位换算上下文发生漂移阻断"

        saved_pair_mult = unit_multiplier(saved_orig_unit, saved_unit)
        if (
            saved_pair_mult is None
            or abs(saved_pair_mult - prev_mult) > 1e-6 * max(1.0, abs(prev_mult))
        ):
            return "确认后单位换算上下文发生漂移阻断"

        if not q_unit:
            return "确认记录缺少权威目标单位证据阻断"
        orig_eq_mult = unit_multiplier(saved_orig_unit, q_unit)
        if orig_eq_mult is None or abs(orig_eq_mult - 1.0) > 1e-6:
            return "确认后单位换算上下文发生漂移阻断"

        if not live_unit:
            return "确认后物料目标单位发生漂移阻断"
        s_mult = unit_multiplier(saved_unit, live_unit)
        if s_mult is None or abs(s_mult - 1.0) > 1e-6:
            return "确认后物料目标单位发生漂移阻断"

        curr_mult = unit_multiplier(q_unit, live_unit)
        if (
            curr_mult is None
            or abs(curr_mult - prev_mult) > 1e-6 * max(1.0, abs(prev_mult))
        ):
            return "确认后单位换算上下文发生漂移阻断"

        if not req_unit:
            return "绑定目标单位缺失或与确认记录不一致阻断"
        u_mult = unit_multiplier(req_unit, saved_unit)
        if u_mult is None or abs(u_mult - 1.0) > 1e-6:
            return "绑定目标单位与确认记录不一致阻断"
        return None

    # 非强证据模式（如 /api/binding/submit 初次校验或带显式 saved_bindings 的兼容单元测试）
    saved_unit = str(
        (sb or {}).get("confirmed_unit") or (sb or {}).get("unit") or ""
    ).strip()
    if not saved_unit:
        return None
    if not live_unit:
        return "确认后物料目标单位发生漂移阻断"
    s_mult = unit_multiplier(saved_unit, live_unit)
    if s_mult is None or abs(s_mult - 1.0) > 1e-6:
        return "确认后物料目标单位发生漂移阻断"
    if sb is not None and sb.get("confirmed_unit_mult") is not None:
        prev_mult = _to_strict_positive_float(sb.get("confirmed_unit_mult"))
        if prev_mult is None:
            return "确认后单位换算上下文发生漂移阻断"
        if q_unit and live_unit:
            curr_mult = unit_multiplier(q_unit, live_unit)
            if (
                curr_mult is None
                or abs(curr_mult - prev_mult) > 1e-6 * max(1.0, abs(prev_mult))
            ):
                return "确认后单位换算上下文发生漂移阻断"
    if not req_unit:
        return "绑定目标单位缺失或与确认记录不一致阻断"
    u_mult = unit_multiplier(req_unit, saved_unit)
    if u_mult is None or abs(u_mult - 1.0) > 1e-6:
        return "绑定目标单位与确认记录不一致阻断"
    return None


def validate_bindings_against_row(
    row: Dict[str, Any],
    bindings: List[Dict[str, Any]],
    allow_unit_conversion: bool = True,
    saved_bindings: Optional[List[Dict[str, Any]]] = None,
    require_saved_unit_evidence: bool = False,
    component_shares: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """对整组绑定做新鲜有效性 + 硬冲突 + 确认后目标单位/换算上下文漂移复核。

    返回 {"ok": bool, "invalid_codes": [...], "conflicts": [{code, reason}, ...]}。
    """
    conflicts: List[Dict[str, str]] = []
    live = fetch_live_material_map([b.get("code", "") for b in (bindings or [])])
    invalid_codes = [code for code in dict.fromkeys(str(b.get("code", "")).strip() for b in bindings or [])
                     if code and code not in live]
    saved_by_code: Dict[str, Dict[str, Any]] = {}
    for sb in (saved_bindings or []):
        sc = str(sb.get("code", "")).strip()
        if sc and sc not in saved_by_code:
            saved_by_code[sc] = sb

    for b in (bindings or []):
        code = str(b.get("code", "")).strip()
        if not code or code in invalid_codes:
            continue
        reason = evaluate_hard_conflicts(
            row, live[code], binding=b, allow_unit_conversion=allow_unit_conversion,
            quantity_multiplier=component_shares[code] if component_shares is not None else 1.0,
        )
        if not reason:
            sb = saved_by_code.get(code)
            live_unit = str(live[code].get("unit", "") or "").strip()
            raw_req_unit = b.get("unit")
            req_unit = raw_req_unit.strip() if isinstance(raw_req_unit, str) else ""
            reason = _validate_saved_binding_unit_evidence(
                row,
                sb=sb,
                live_unit=live_unit,
                req_unit=req_unit,
                require_saved_unit_evidence=require_saved_unit_evidence,
            )
        if reason:
            conflicts.append({"code": code, "reason": reason})
    return {
        "ok": not invalid_codes and not conflicts,
        "invalid_codes": invalid_codes,
        "conflicts": conflicts,
        "live_materials": live,
    }


# ---------------------------------------------------------------------------
# 服务端决策记录持久化
# ---------------------------------------------------------------------------
def _row_to_dict(row: Any) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def _enrich_saved_bindings(
    row: Dict[str, Any], bindings: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """V015-M3 R04：落盘前剔除伪造证据并仅从实时物料固化权威目标单位与换算上下文。"""
    live = fetch_live_material_map([b.get("code", "") for b in (bindings or [])])
    q_unit = str(row.get("original_unit", "") or row.get("unit", "") or "").strip()
    unit_multiplier = _row_unit_multiplier(row)
    raw_orig_qty = row.get("original_qty")
    if raw_orig_qty is None:
        raw_orig_qty = row.get("qty")
    orig_qty = (
        None if isinstance(raw_orig_qty, bool) else _to_finite_float(raw_orig_qty)
    )
    if orig_qty is not None and orig_qty <= 0:
        orig_qty = None

    enriched_list: List[Dict[str, Any]] = []
    for b in (bindings or []):
        item = dict(b)
        for key in _AUTHORITATIVE_UNIT_EVIDENCE_KEYS:
            item.pop(key, None)
        code = str(item.get("code", "") or "").strip()
        mat = live.get(code)
        live_unit = str((mat or {}).get("unit", "") or "").strip()
        req_unit = str(item.get("unit", "") or "").strip()
        if mat is not None and live_unit:
            if not req_unit:
                item["unit"] = live_unit
            item["confirmed_unit"] = live_unit
            item["confirmed_orig_unit"] = q_unit
            if orig_qty is not None:
                item["confirmed_orig_qty"] = float(orig_qty)
            unit_mult = unit_multiplier(q_unit, live_unit) if q_unit else None
            if unit_mult is not None and unit_mult > 0:
                item["confirmed_unit_mult"] = float(unit_mult)
                item["confirmed_unit_source"] = UNIT_EVIDENCE_SOURCE
                item["confirmed_unit_version"] = UNIT_EVIDENCE_VERSION
        enriched_list.append(item)
    return enriched_list


def record_decision(
    user_id: int,
    scope_key: str,
    row: Dict[str, Any],
    bindings: List[Dict[str, Any]],
    decision: str,
    source: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """写入/更新一条服务端权威决策（同账号+范围+行指纹唯一）。"""
    if decision not in (STATUS_MANUAL_CONFIRMED, STATUS_REJECTED):
        raise ValueError(f"record_decision 仅记录人工确认/拒绝，收到非法状态: {decision}")
    init_binding_decision_db()
    row_fp = row_content_fingerprint(row)
    bind_fp = binding_fingerprint(bindings)
    codes = ",".join(sorted({str(b.get("code", "")).strip() for b in (bindings or []) if str(b.get("code", "")).strip()}))
    saved_bindings = _enrich_saved_bindings(row, bindings or [])
    payload_json = _canonical_json({"row": row, "bindings": saved_bindings})

    conn = get_auth_connection()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        existing = cur.execute(
            "SELECT id FROM binding_decisions WHERE user_id = ? AND scope_key = ? AND row_fingerprint = ? "
            "ORDER BY id DESC LIMIT 1",
            (user_id, scope_key, row_fp),
        ).fetchone()
        if existing:
            cur.execute(
                """
                UPDATE binding_decisions
                SET binding_fingerprint = ?, decision = ?, source = ?, reason = ?,
                    matched_codes = ?, payload_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (bind_fp, decision, source, reason, codes, payload_json, existing["id"]),
            )
        else:
            cur.execute(
                """
                INSERT INTO binding_decisions
                (user_id, scope_key, row_fingerprint, binding_fingerprint, decision,
                 source, reason, matched_codes, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, scope_key, row_fp, bind_fp, decision, source, reason, codes, payload_json),
            )
        conn.commit()
    finally:
        conn.close()
    return {
        "user_id": user_id,
        "scope_key": scope_key,
        "row_fingerprint": row_fp,
        "binding_fingerprint": bind_fp,
        "decision": decision,
        "source": source,
        "reason": reason,
        "matched_codes": codes,
    }


def invalidate_decision(user_id: int, scope_key: str, row: Dict[str, Any]) -> None:
    """Record an edit without replacing a prior explicit refusal."""
    init_binding_decision_db()
    row_fp = row_content_fingerprint(row)
    conn = get_auth_connection()
    try:
        conn.execute(
            """
            INSERT INTO binding_decisions
                (user_id, scope_key, row_fingerprint, binding_fingerprint,
                 decision, source, reason, matched_codes, payload_json)
            SELECT ?, ?, ?, ?, ?, ?, ?, '', '{}'
            WHERE COALESCE((
                SELECT decision FROM binding_decisions
                WHERE user_id = ? AND scope_key = ? AND row_fingerprint = ?
                ORDER BY id DESC LIMIT 1
            ), '') NOT IN (?, ?)
            """,
            (user_id, scope_key, row_fp, binding_fingerprint([]), STATUS_RECOMMENDED,
             SOURCE_MANUAL_EDIT, "内容已修改，请重新确认", user_id, scope_key, row_fp,
             STATUS_REJECTED, STATUS_RECOMMENDED),
        )
        conn.commit()
    finally:
        conn.close()


def get_row_decision(
    user_id: int, scope_key: str, row: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """取当前行最新人工决定；拒绝优先于自动规则，换绑定不会清除该拒绝。"""
    init_binding_decision_db()
    conn = get_auth_connection()
    try:
        rec = conn.execute(
            """
            SELECT * FROM binding_decisions
            WHERE user_id = ? AND scope_key = ? AND row_fingerprint = ?
            ORDER BY id DESC LIMIT 1
            """,
            (user_id, scope_key, row_content_fingerprint(row)),
        ).fetchone()
        return _row_to_dict(rec)
    finally:
        conn.close()


def get_decision(
    user_id: int, scope_key: str, row: Dict[str, Any], bindings: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """人工确认仅在当前行与完整绑定双指纹均一致时有效。"""
    record = get_row_decision(user_id, scope_key, row)
    if (record and record["decision"] == STATUS_MANUAL_CONFIRMED
            and record["binding_fingerprint"] == binding_fingerprint(bindings)):
        return record
    return None


def has_confirmed_decision(user_id: int, scope_key: str, row: Dict[str, Any], bindings: List[Dict[str, Any]]) -> bool:
    return get_decision(user_id, scope_key, row, bindings) is not None


def extract_saved_bindings(record: Optional[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """从 binding_decisions 记录的 payload_json 中解析确认时保存的绑定列表。"""
    if not record or not record.get("payload_json"):
        return None
    try:
        payload = json.loads(record["payload_json"])
    except (TypeError, ValueError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("bindings"), list):
        return payload["bindings"]
    return None
