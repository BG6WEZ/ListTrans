"""Versioned BOM for explicitly specified 304 thin-wall stainless pipe segments."""
from dataclasses import dataclass
from decimal import Decimal
import math
import re
import unicodedata

from app.db_manager import catalog_read
from app.matcher import UNIT_SYNONYMS, check_unit_compatibility, get_unit_multiplier
from app.rule_context import canonicalize_professional

FAMILY_NAME = "304薄壁不锈钢管分段组合"
CATEGORY = "不锈钢管"
PROFESSIONAL = "暖通"
VERSION = 1
# Only catalog-confirmed pairs at the same DN, OD, grade and wall thickness.
VARIANTS = {
    (25, Decimal("25.4")): ("2.17.05.0628", "2.17.05.0479", "1.0"),
    (50, Decimal("50.8")): ("2.17.05.0631", "2.17.05.0482", "1.2"),
}
SPEC = re.compile(r"DN([0-9]{1,3})\s+D([0-9]{1,3}(?:\.[0-9]{1,3})?)\s+(?:SUS)?304", re.I)
SOURCE_UNITS = frozenset(("m", "米", "km", "千米", "cm", "厘米", "mm", "毫米"))


def is_bom_name(name):
    return _text(name) == FAMILY_NAME


def _text(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _compact_spec(value):
    return "".join(_text(value).upper().split()).replace("×", "*")


def _condition(specification):
    match = SPEC.fullmatch(_text(specification))
    if not match:
        raise ValueError("BOM 需要明确 DN、外径 D 和 304 材质，规格示例：DN25 D25.4 304")
    key = (int(match[1]), Decimal(match[2]))
    if key not in VARIANTS:
        raise ValueError("BOM 仅支持 DN25/D25.4 或 DN50/D50.8 的 304 薄壁不锈钢管")
    return key


def positive(value):
    if type(value) not in (float, int):
        return False
    try:
        return value > 0 and math.isfinite(value)
    except OverflowError:
        return False


def unit_multiplier(source, target):
    source, target = source.strip().lower(), target.strip().lower()
    return get_unit_multiplier(UNIT_SYNONYMS.get(source, source), UNIT_SYNONYMS.get(target, target))


def rule_metadata(name, specification, professional, components):
    """Build the digest-bound schema from the admin's explicitly declared composition."""
    if not is_bom_name(name):
        return None
    if name != FAMILY_NAME or canonicalize_professional(professional) != PROFESSIONAL:
        raise ValueError("BOM 必须使用标准物料族名称和暖通专业")
    dn, od = _condition(specification)
    if specification != f"DN{dn} D{od.normalize()} 304":
        raise ValueError(f"BOM 规则规格须使用 DN{dn} D{od.normalize()} 304")
    bare, insulated, _ = VARIANTS[dn, od]
    codes = [part["code"] for part in components]
    if len(codes) != 2 or set(codes) != {bare, insulated}:
        raise ValueError("BOM 必须恰好包含当前规格的保温段和非保温段")
    shares = [part.get("qty_ratio") for part in components]
    if not all(positive(share) and share < 1 for share in shares) or not math.isclose(
        math.fsum(shares), 1, rel_tol=0, abs_tol=1e-12,
    ):
        raise ValueError("BOM 两段的声明比例须各自为有限正数且合计为 1")
    if any(get_unit_multiplier(part.get("unit", ""), "m") != 1 for part in components):
        raise ValueError("BOM 子项规则单位必须与冻结目录的米同义")
    return {"version": VERSION, "family": "304-thin-wall-pipe-segments", "category": CATEGORY,
            "dn": dn, "od_mm": float(od), "material_grade": "304"}


@dataclass(frozen=True)
class Expansion:
    bindings: list
    shares: dict
    reason: str
    hard_conflict: bool = False

    def mismatch(self, submitted):
        if not self.bindings:
            return self.reason
        supplied = {item.get("code"): item for item in submitted}
        if len(submitted) != len(self.bindings) or set(supplied) != {b["code"] for b in self.bindings}:
            return "BOM 绑定集合不完整或不精确，须包含两段且每段仅出现一次"
        for expected in self.bindings:
            item = supplied[expected["code"]]
            if get_unit_multiplier(str(item.get("unit") or ""), expected["unit"]) != 1:
                return f"BOM 子项 {expected['code']} 目标单位与实时物料不一致"
            if any(not positive(item.get(k)) or not math.isclose(item[k], expected[k], rel_tol=1e-9)
                   for k in ("qty", "qty_ratio")):
                return f"BOM 子项 {expected['code']} 工程量或比例与规则不一致"
        return None


@catalog_read
def expand(row, rules):
    """Expand the whole bundle atomically, using current catalog evidence and server rules."""
    from app.binding_decisions import evaluate_hard_conflicts, fetch_live_material_map

    def review(reason, hard=False):
        return Expansion([], {}, reason, hard)

    if not is_bom_name(row.get("original_name")):
        return None
    if canonicalize_professional(str(row.get("professional") or "")) != PROFESSIONAL:
        return review("BOM 需要明确的暖通专业")
    if _text(row.get("original_category")) != CATEGORY:
        return review("BOM 需要明确的原始品类：不锈钢管")
    try:
        dn, od = _condition(row.get("original_spec"))
    except ValueError as exc:
        return review(str(exc))
    unit = str(row.get("original_unit") or "").strip().lower()
    qty = row.get("original_qty")
    if unit not in SOURCE_UNITS or not positive(qty):
        return review("BOM 需要已知长度单位和有限正工程量")

    spec = f"DN{dn} D{od.normalize()} 304"
    matching = [rule for rule in rules if is_bom_name(rule.get("original_name"))
                and rule.get("original_spec") == spec
                and canonicalize_professional(str(rule.get("professional") or "")) == PROFESSIONAL]
    if len(matching) != 1:
        return review("BOM 缺少唯一的已评测规格规则，请人工核实")
    rule, = matching
    components = rule.get("bound_materials") or []
    try:
        expected_schema = rule_metadata(FAMILY_NAME, spec, PROFESSIONAL, components)
        schema = rule.get("bom")
        if schema != expected_schema or type(schema.get("version")) is not int:
            return review("BOM 旧规则或版本无效，须重新评测确认")
    except (KeyError, TypeError, ValueError) as exc:
        return review(f"BOM 规则无效：{exc}")

    bare, insulated, wall = VARIANTS[dn, od]
    expected_names = {bare: "304薄壁不锈钢管", insulated: "304薄壁不锈钢管(保温)"}
    materials = fetch_live_material_map(list(expected_names))
    source = dict(row, original_unit=unit)
    bindings, shares = [], {}
    for part in components:
        code = part["code"]
        material = materials.get(code)
        if not material:
            return review(f"BOM 子项编码已失效：{code}", True)
        live_unit = str(material.get("unit") or "").strip()
        if (_text(material.get("name")) != expected_names[code] or material.get("category") != CATEGORY
                or _compact_spec(material.get("specification")) != f"DN{dn},D{od.normalize()}*{wall}"
                or get_unit_multiplier(live_unit, "m") != 1):
            return review(f"BOM 子项规格或实时单位与规则证据不符：{code}", True)
        multiplier = unit_multiplier(unit, live_unit)
        if not check_unit_compatibility(unit, live_unit) or not positive(multiplier):
            return review(f"BOM 子项单位无法可靠换算：{code}")
        share = part["qty_ratio"]
        ratio = share * multiplier
        quantity = qty * ratio
        if not positive(ratio) or not positive(quantity):
            return review(f"BOM 子项工程量或比例超出有效范围：{code}")
        binding = {**{key: material.get(key) or "" for key in ("name", "specification", "brand", "category")},
                   "code": code, "unit": live_unit, "qty": quantity, "qty_ratio": ratio}
        reason = evaluate_hard_conflicts(source, material, binding, allow_unit_conversion=True,
                                         quantity_multiplier=share)
        if reason:
            return review(f"BOM 子项 {code}：{reason}", True)
        bindings.append(binding)
        shares[code] = share
    return Expansion(bindings, shares, "BOM 完整集合、数量比例、目标单位及硬约束均已核验")
