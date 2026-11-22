# -*- coding: utf-8 -*-
"""
V015-M3 / R01 硬约束反例测试（纯函数 / 轻依赖层）。

覆盖统筹裁决六条锁定项的反例回归，顶层仅依赖标准库与 app 纯函数模块
（matcher / domain_extractors / binding_decisions / query_preprocessor /
llm_rule_engine），不导入 fastapi/server，在 conftest 隔离会话下恒真实执行。

覆盖映射：
1. 否定/unknown 三态：否定前缀优先于肯定词，未说明=None 不臆断；
2. 专业别名：医用气体=医气，真跨专业拦截，候选“其它”unknown 宽容；
3. 真实品牌值：品牌列空时解析 parsed_params.attrs.品牌 字符串，
   坏 JSON/布尔 True/非字符串一律 unknown（""），绝不赋布尔 True；
4. 数值/单位：15=15.0 数值归一；米/平方米跨维度阻断；厘米/毫米同维度但
   无可靠倍率→阻断静默接受；非有限值（None/bool/NaN/Inf/DN 字符串）不当满足；
5. DN/外径/材质：分字段比较、跨字段不比、外径 10% 阈值；304/316L 枚举硬隔离；
6. blocked/max_score 不可逆：先封 0.3 再动态 +100 不突破；动态排除双归零；
   blocked 后追加 boost 不可复活；match_item 系列 +5 不复活 hard_blocked。

动态规则一律经 LLMRuleManager._active_rules_cache 类缓存注入，不写库、不调 LLM；
tearDown 必须 clear_active_cache()，避免跨用例污染（extract_query_params 第 8 步
同样读取该缓存）。
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.domain_extractors import (
    NEGATION_PREFIXES,
    detect_three_state_flag,
    detect_pump_state,
    check_pump_conflict,
)
from app.matcher import (
    KNOWN_MATERIAL_GRADES,
    NEGATION_PREFIXES as MATCHER_NEGATION_PREFIXES,
    _to_finite_float,
    check_dn_od_conflicts,
    check_series_match,
    check_unit_compatibility,
    evaluate_dynamic_exclusions,
    get_unit_multiplier,
    normalize_material_grade,
    normalize_professional_category,
    parse_material_attrs,
    resolve_material_attrs,
    resolve_material_brand,
    score_candidate,
    score_candidate_with_detail,
    match_item,
)
from app.binding_decisions import evaluate_hard_conflicts, validate_bindings_against_row
from app.query_preprocessor import prepare_query
from app.llm_rule_engine import LLMRuleManager


# ---------------------------------------------------------------------------
# 夹具 helpers
# ---------------------------------------------------------------------------

def _row(**overrides):
    """清单行夹具：evaluate_hard_conflicts 仅消费 original_*/professional 键。"""
    base = {
        "original_name": "通用材料",
        "original_spec": "",
        "original_unit": "",
        "original_brand": "",
        "professional": "",
        "original_category": "",
    }
    base.update(overrides)
    return base


def _material(**overrides):
    """物料行夹具：字段对齐 materials 表与 fetch_live_material_map 输出。"""
    base = {
        "code": "M3T-MAT",
        "category": "",
        "specification": "",
        "description": "",
        "unit": "",
        "brand": "",
        "parsed_params": "",
    }
    base.update(overrides)
    return base


def _dynamic_rule(matching_rules, rule_id="m3t-rule"):
    """构造 active_rules 缓存条目；scope_target 空串表示全量命中。"""
    return {
        "rule_id": rule_id,
        "scope_level": "category",
        "scope_target": "",
        "parsed_dsl": {"matching_rules": matching_rules},
    }


# ---------------------------------------------------------------------------
# 1. 三态否定：否定前缀优先，unknown 不臆断
# ---------------------------------------------------------------------------

class TestThreeStateFlag(unittest.TestCase):

    def test_negation_precedence_over_positive(self):
        # “非防爆”旧逻辑因子串命中“防爆”误置 True，M3 修复后必须为 False
        self.assertIs(
            detect_three_state_flag("非防爆风机", ["防爆", "防爆型", "防爆风机", "EX"]),
            False,
        )
        self.assertIs(
            detect_three_state_flag("不带提升泵", ["提升泵", "带提升泵", "冷凝水提升泵", "泵"]),
            False,
        )
        self.assertIs(detect_three_state_flag("无防爆要求", ["防爆"]), False)

    def test_explicit_positive(self):
        self.assertIs(detect_three_state_flag("防爆风机", ["防爆", "防爆型", "防爆风机"]), True)
        self.assertIs(detect_three_state_flag("带提升泵", ["提升泵", "泵"]), True)

    def test_unknown_returns_none(self):
        self.assertIsNone(detect_three_state_flag("普通离心风机", ["防爆", "EX"]))
        self.assertIsNone(detect_three_state_flag("", ["防爆"]))
        self.assertIsNone(detect_three_state_flag("机组一台", ["提升泵", "泵"]))

    def test_negation_table_single_source(self):
        # matcher 仅允许重导出，词表漂移会立刻被本断言捕获
        self.assertIs(MATCHER_NEGATION_PREFIXES, NEGATION_PREFIXES)
        for prefix in ("不带", "非", "无", "不含", "未配", "无配", "不具有", "没有"):
            self.assertIn(prefix, NEGATION_PREFIXES)


# ---------------------------------------------------------------------------
# 2. 单位双门：物理维度 + 可靠倍率
# ---------------------------------------------------------------------------

class TestUnitDualGates(unittest.TestCase):

    def test_cross_dimension_blocked(self):
        # 米（长度）vs 平方米（面积）：不得因子串包含判兼容
        self.assertFalse(check_unit_compatibility("米", "平方米"))
        self.assertFalse(check_unit_compatibility("m", "m2"))

    def test_unknown_unit_tolerant(self):
        # 任一未说明/未知单位=unknown，不臆断冲突
        self.assertTrue(check_unit_compatibility("", "米"))
        self.assertTrue(check_unit_compatibility("米", ""))
        self.assertTrue(check_unit_compatibility("批次", "米"))

    def test_same_dimension_compatible_but_multiplier_gated(self):
        # 厘米/毫米同维度兼容，但倍率表未登记有序对→不可靠换算
        self.assertTrue(check_unit_compatibility("厘米", "毫米"))
        self.assertIsNone(get_unit_multiplier("厘米", "毫米"))

    def test_multiplier_same_and_synonym(self):
        self.assertEqual(get_unit_multiplier("米", "米"), 1.0)
        self.assertEqual(get_unit_multiplier("米", "m"), 1.0)
        self.assertEqual(get_unit_multiplier("千米", "km"), 1.0)

    def test_multiplier_registered_pairs(self):
        self.assertEqual(get_unit_multiplier("km", "m"), 1000.0)
        self.assertEqual(get_unit_multiplier("m", "km"), 0.001)

    def test_multiplier_unknown_inputs_none(self):
        self.assertIsNone(get_unit_multiplier("", "m"))
        self.assertIsNone(get_unit_multiplier("米", "奇怪单位"))


# ---------------------------------------------------------------------------
# 3. 非有限值隔离 + DN/外径分字段
# ---------------------------------------------------------------------------

class TestFiniteFloatAndDnOd(unittest.TestCase):

    def test_to_finite_float_rejects_abnormal(self):
        self.assertEqual(_to_finite_float(15), 15.0)
        self.assertEqual(_to_finite_float("15.0"), 15.0)
        # bool 不应当数值、DN 真值字符串不可数值化、NaN/Inf 拒绝
        self.assertIsNone(_to_finite_float(True))
        self.assertIsNone(_to_finite_float(None))
        self.assertIsNone(_to_finite_float("DN15"))
        self.assertIsNone(_to_finite_float(float("nan")))
        self.assertIsNone(_to_finite_float(float("inf")))
        self.assertIsNone(_to_finite_float(float("-inf")))

    def test_dn_numeric_equivalence_15_vs_15_0(self):
        # 15 与 15.0 按数值比较，相容不阻断
        self.assertIsNone(check_dn_od_conflicts({"dn_val": 15}, {"dn_val": 15.0}))
        self.assertIsNone(check_dn_od_conflicts({"管径": 15}, {"dn_val": 15.0}))

    def test_dn_conflict_blocked(self):
        self.assertEqual(
            check_dn_od_conflicts({"dn_val": 15}, {"dn_val": 20}),
            "管径口径冲突阻断",
        )

    def test_dn_string_form_from_real_extractor(self):
        # 真实抽取器把 管径 写成 "DN15" 字符串、dn_val 写数值；字符串不得短路 DN 门
        self.assertEqual(
            check_dn_od_conflicts({"管径": "DN15", "dn_val": 15.0}, {"dn_val": 20}),
            "管径口径冲突阻断",
        )
        # 双侧均为字符串形态（物料侧 domain_extractors 同构输出）
        self.assertEqual(
            check_dn_od_conflicts({"管径": "DN15"}, {"管径": "DN20"}),
            "管径口径冲突阻断",
        )
        # 字符串 "DN15" 与数值 15.0 按数值归一，相容不阻断
        self.assertIsNone(check_dn_od_conflicts({"管径": "DN15"}, {"dn_val": 15.0}))
        # De/φ 属外径族：出现在 DN 字段也不得解析为 DN 数值，按 unknown 不阻断
        self.assertIsNone(check_dn_od_conflicts({"管径": "De20"}, {"dn_val": 20}))

    def test_dn_and_od_never_cross_compared(self):
        # DN100 vs 外径100 是两个物理字段，严禁跨字段比较
        self.assertIsNone(check_dn_od_conflicts({"dn_val": 100}, {"外径": 100}))
        self.assertIsNone(check_dn_od_conflicts({"外径": 100}, {"dn_val": 100}))

    def test_outer_diameter_10_percent_threshold(self):
        self.assertIsNone(check_dn_od_conflicts({"外径": 100}, {"外径": 105}))
        # 边界：100 vs 110 相对差 9.09% ≤ 10%，相容
        self.assertIsNone(check_dn_od_conflicts({"外径": 100}, {"外径": 110}))
        self.assertEqual(
            check_dn_od_conflicts({"外径": 100}, {"外径": 120}),
            "管材外径冲突阻断",
        )

    def test_missing_side_is_unknown(self):
        self.assertIsNone(check_dn_od_conflicts({"dn_val": 15}, {}))
        self.assertIsNone(check_dn_od_conflicts({}, {"外径": 50}))


# ---------------------------------------------------------------------------
# 4. 材质枚举与专业别名
# ---------------------------------------------------------------------------

class TestMaterialAndProfessionalEnums(unittest.TestCase):

    def test_material_grade_alias_normalization(self):
        self.assertEqual(normalize_material_grade("304"), "不锈钢304")
        self.assertEqual(normalize_material_grade("S30408"), "不锈钢304")
        self.assertEqual(normalize_material_grade("316L"), "不锈钢316L")
        self.assertEqual(normalize_material_grade("316l"), "不锈钢316L")

    def test_known_grades_set_locked(self):
        self.assertEqual(KNOWN_MATERIAL_GRADES, {"不锈钢304", "不锈钢316L"})

    def test_unknown_grade_not_fabricated(self):
        self.assertEqual(normalize_material_grade("201"), "201")
        self.assertEqual(normalize_material_grade(""), "")
        self.assertEqual(normalize_material_grade(None), "")

    def test_professional_medical_gas_alias(self):
        self.assertEqual(normalize_professional_category("医用气体"), "医气")
        self.assertEqual(normalize_professional_category("医疗气体"), "医气")
        self.assertEqual(normalize_professional_category("气体"), "医气")
        self.assertEqual(normalize_professional_category("医气"), "医气")

    def test_professional_unknown_and_empty(self):
        self.assertEqual(normalize_professional_category("暖通"), "暖通")
        self.assertEqual(normalize_professional_category(""), "")


# ---------------------------------------------------------------------------
# 5. 真实品牌值：空品牌列回退 parsed_params，异常值 unknown
# ---------------------------------------------------------------------------

class TestResolveMaterialBrand(unittest.TestCase):

    def test_brand_column_priority(self):
        self.assertEqual(resolve_material_brand({"brand": "远东"}), "远东")

    def test_brand_from_parsed_params_json_string(self):
        cand = {"brand": "", "parsed_params": json.dumps({"attrs": {"品牌": "起帆"}}, ensure_ascii=False)}
        self.assertEqual(resolve_material_brand(cand), "起帆")

    def test_brand_from_parsed_params_dict(self):
        cand = {"brand": "", "parsed_params": {"attrs": {"品牌": "西门子"}}}
        self.assertEqual(resolve_material_brand(cand), "西门子")

    def test_bad_json_is_unknown(self):
        self.assertEqual(parse_material_attrs({"parsed_params": "{损坏json"}), {})
        self.assertEqual(resolve_material_brand({"brand": "", "parsed_params": "{损坏json"}), "")

    def test_bool_brand_never_true(self):
        # 回归旧缺陷：品牌异常值不得被当成布尔 True 误判
        self.assertEqual(resolve_material_brand({"brand": True}), "")
        cand = {"brand": "", "parsed_params": {"attrs": {"品牌": 123}}}
        self.assertEqual(resolve_material_brand(cand), "")


# ---------------------------------------------------------------------------
# 6. evaluate_hard_conflicts 七道门（推荐/规则接受/人工确认/导出同源）
# ---------------------------------------------------------------------------

class TestEvaluateHardConflictsSevenGates(unittest.TestCase):

    # 门 1：专业别名归一 + 真跨专业拦截 + 其它 unknown
    def test_gate1_medical_gas_alias_compatible(self):
        row = _row(original_name="气体终端", professional="医用气体")
        mat = _material(category="医气", specification="氧气终端")
        self.assertIsNone(evaluate_hard_conflicts(row, mat))

    def test_gate1_true_cross_professional_blocked(self):
        row = _row(professional="强电")
        mat = _material(category="暖通", description="排风机")
        self.assertEqual(evaluate_hard_conflicts(row, mat), "专业冲突阻断")

    def test_gate1_other_category_tolerant(self):
        row = _row(professional="强电")
        mat = _material(category="其它", description="通用配件")
        self.assertIsNone(evaluate_hard_conflicts(row, mat))

    # 门 2：品牌列空时取 parsed_params 真实值；同品牌相容、冲突阻断、坏值 unknown
    def test_gate2_same_brand_via_parsed_params(self):
        row = _row(original_brand="远东")
        mat = _material(parsed_params=json.dumps({"attrs": {"品牌": "远东"}}, ensure_ascii=False))
        self.assertIsNone(evaluate_hard_conflicts(row, mat))

    def test_gate2_brand_conflict_blocked(self):
        row = _row(original_brand="远东")
        mat = _material(parsed_params=json.dumps({"attrs": {"品牌": "起帆"}}, ensure_ascii=False))
        self.assertEqual(evaluate_hard_conflicts(row, mat), "品牌冲突阻断")

    def test_gate2_bad_json_and_bool_unknown(self):
        row = _row(original_brand="远东")
        self.assertIsNone(evaluate_hard_conflicts(row, _material(parsed_params="{损坏")))
        self.assertIsNone(evaluate_hard_conflicts(row, _material(brand=True)))

    # 门 3：单位物理维度与可靠倍率双门
    def test_gate3_dimension_blocked(self):
        row = _row(original_unit="米")
        self.assertEqual(evaluate_hard_conflicts(row, _material(unit="平方米")), "单位不可换算阻断")

    def test_gate3_unreliable_multiplier_blocked(self):
        row = _row(original_unit="厘米")
        self.assertEqual(evaluate_hard_conflicts(row, _material(unit="毫米")), "单位倍率不可靠阻断")

    def test_gate3_same_unit_pass(self):
        row = _row(original_unit="米")
        self.assertIsNone(evaluate_hard_conflicts(row, _material(unit="米")))

    # 门 4：DN/外径分字段（走 pipe_copper 真实抽取，φ 恒为 float 外径）
    def test_gate4_outer_diameter_conflict_full_chain(self):
        row = _row(original_name="紫铜管", original_spec="φ100", original_unit="米")
        mat = _material(unit="米", parsed_params=json.dumps({"attrs": {"外径": 120}}))
        self.assertEqual(evaluate_hard_conflicts(row, mat), "管材外径冲突阻断")

    def test_gate4_outer_diameter_within_tolerance_pass(self):
        row = _row(original_name="紫铜管", original_spec="φ100", original_unit="米")
        mat = _material(unit="米", parsed_params=json.dumps({"attrs": {"外径": 105}}))
        self.assertIsNone(evaluate_hard_conflicts(row, mat))

    # 门 5：304/316L 枚举硬隔离，别名归一后同牌号相容
    def test_gate5_grade_304_vs_316l_blocked(self):
        row = _row(original_name="不锈钢管", original_spec="304")
        mat = _material(parsed_params=json.dumps({"attrs": {"材质": "不锈钢316L"}}, ensure_ascii=False))
        self.assertEqual(
            evaluate_hard_conflicts(row, mat),
            "不锈钢牌号冲突硬隔离(304/316L)",
        )

    def test_gate5_grade_alias_equal_pass(self):
        row = _row(original_name="不锈钢管", original_spec="304")
        mat = _material(parsed_params=json.dumps({"attrs": {"材质": "304"}}, ensure_ascii=False))
        self.assertIsNone(evaluate_hard_conflicts(row, mat))

    # 门 6：提升泵否定三态；未说明=unknown 不阻断
    def test_gate6_pump_negation_blocked(self):
        row = _row(original_name="风机盘管", original_spec="不带提升泵")
        mat = _material(description="冷凝水提升泵")
        self.assertEqual(
            evaluate_hard_conflicts(row, mat),
            "提升泵否定冲突硬隔离(不带泵/带泵)",
        )

    def test_gate6_pump_unknown_no_block(self):
        row = _row(original_name="风机盘管")
        mat = _material(description="冷凝水提升泵")
        self.assertIsNone(evaluate_hard_conflicts(row, mat))

    # 门 7：防爆双向互斥；未说明=unknown 不阻断
    def test_gate7_non_ex_requirement_vs_ex_material(self):
        row = _row(original_name="排风机", original_spec="非防爆")
        mat = _material(description="防爆风机")
        self.assertEqual(evaluate_hard_conflicts(row, mat), "非防爆要求vs防爆硬隔离")

    def test_gate7_ex_requirement_vs_non_ex_material(self):
        # 三态纪律：候选必须含显式否定（c=False）才与 q=True 互斥；
        # 未提及防爆的普通文本是 unknown(None)，不得臆断成“非防爆”
        row = _row(original_name="防爆风机")
        mat = _material(description="非防爆型离心式通风机")
        self.assertEqual(evaluate_hard_conflicts(row, mat), "防爆要求vs非防爆硬隔离")

    def test_gate7_ex_unknown_no_block(self):
        row = _row(original_name="排风机")
        mat = _material(description="防爆风机")
        self.assertIsNone(evaluate_hard_conflicts(row, mat))


# ---------------------------------------------------------------------------
# 7. 评分管线：先封 0.3 再强制 +100；动态排除双归零且不可逆
# ---------------------------------------------------------------------------

class TestScoreCapAndDynamicBoost(unittest.TestCase):

    def setUp(self):
        LLMRuleManager.clear_active_cache()
        LLMRuleManager._active_rules_cache = []

    def tearDown(self):
        LLMRuleManager.clear_active_cache()

    @staticmethod
    def _cap_query():
        # YJV → cable_power；extract_query_cable_params 标准式产出 芯数="4"（字符串）
        return prepare_query("电力电缆", "YJV-4*25", "强电")

    @staticmethod
    def _cap_candidate():
        # 候选 parsed_params 优先：芯数 "3" 与查询 "4" 触发 conflict_caps=30（0.3 上限）
        return {
            "code": "M3T-CAP-01",
            "name": "电力电缆",
            "specification": "YJV-3*25",
            "category": "强电",
            "unit": "米",
            "brand": "",
            "description": "",
            "parsed_params": json.dumps({"attrs": {"芯数": "3", "截面积": "25"}}, ensure_ascii=False),
            "semantic_score": 0.0,
        }

    def test_conflict_cap_sets_max_score_03(self):
        score, reasons, detail = score_candidate_with_detail(self._cap_query(), self._cap_candidate(), 0.0)
        self.assertFalse(detail["hard_blocked"])
        self.assertAlmostEqual(detail["max_score"], 0.3, places=6)
        self.assertLessEqual(score, 30.0)
        self.assertIn("芯数冲突拦截", reasons)

    def test_force_boost_100_cannot_break_cap(self):
        LLMRuleManager._active_rules_cache = [
            _dynamic_rule({"score_boost_keywords": {"YJV": 100.0}}, rule_id="m3t-boost")
        ]
        score, reasons, detail = score_candidate_with_detail(self._cap_query(), self._cap_candidate(), 0.0)
        # 强制 +100 分（1.0 基）也必须被先置 0.3 上限收口
        self.assertFalse(detail["hard_blocked"])
        self.assertAlmostEqual(detail["max_score"], 0.3, places=6)
        self.assertLessEqual(score, 30.0)
        self.assertIn("命中加分词 [YJV] (+100.0)", reasons)

    def test_dynamic_exclusion_double_zero(self):
        LLMRuleManager._active_rules_cache = [
            _dynamic_rule({"must_not_contain": ["YJV"]}, rule_id="m3t-excl")
        ]
        score, reasons, detail = score_candidate_with_detail(self._cap_query(), self._cap_candidate(), 0.0)
        self.assertTrue(detail["hard_blocked"])
        self.assertEqual(detail["max_score"], 0.0)
        self.assertEqual(score, 0.0)
        self.assertIn("包含排除词 [YJV]", reasons)

    def test_missing_must_contain_double_zero(self):
        LLMRuleManager._active_rules_cache = [
            _dynamic_rule({"must_contain": ["NO-SUCH-WORD"]}, rule_id="m3t-must")
        ]
        score, reasons, detail = score_candidate_with_detail(self._cap_query(), self._cap_candidate(), 0.0)
        self.assertTrue(detail["hard_blocked"])
        self.assertEqual(detail["max_score"], 0.0)
        self.assertEqual(score, 0.0)
        self.assertIn("缺少必含词 [NO-SUCH-WORD]", reasons)

    def test_blocked_irreversible_by_later_boost(self):
        # 同批先排除后加分：blocked/max_score=0 已成立，后置 boost 不可复活
        LLMRuleManager._active_rules_cache = [
            _dynamic_rule({"must_not_contain": ["YJV"]}, rule_id="m3t-excl-first"),
            _dynamic_rule({"score_boost_keywords": {"YJV": 100.0}}, rule_id="m3t-boost-late"),
        ]
        score, reasons, detail = score_candidate_with_detail(self._cap_query(), self._cap_candidate(), 0.0)
        self.assertTrue(detail["hard_blocked"])
        self.assertEqual(detail["max_score"], 0.0)
        self.assertEqual(score, 0.0)
        self.assertIn("包含排除词 [YJV]", reasons)
        self.assertIn("命中加分词 [YJV] (+100.0)", reasons)

    def test_score_candidate_two_tuple_contract_kept(self):
        # V0.1.4 审核锁定的二元组薄包装签名不得漂移
        result = score_candidate(self._cap_query(), self._cap_candidate(), 0.0)
        self.assertEqual(len(result), 2)
        score, reasons = result
        self.assertLessEqual(score, 30.0)
        self.assertIn("芯数冲突拦截", reasons)


# ---------------------------------------------------------------------------
# 8. match_item 层：系列 +5 不复活 hard_blocked，全员受各自 max_score 收口
# ---------------------------------------------------------------------------

class TestMatchItemSeriesGuard(unittest.TestCase):

    def setUp(self):
        # 隔离库建空表，供 match_item 的批量 enrich SELECT 使用；合成候选 code 不在表中，
        # db_data 命中失败即保留 monkeypatch 原值，不会被 DB 行覆盖
        from app.db_manager import init_db
        init_db()
        LLMRuleManager.clear_active_cache()
        LLMRuleManager._active_rules_cache = []

    def tearDown(self):
        LLMRuleManager.clear_active_cache()

    @staticmethod
    def _series_query():
        query_info = prepare_query("通用装置", "ABC-100", "")
        query_info["parsed_spec"]["model"] = "ABC-100"
        return query_info

    @staticmethod
    def _fake_candidates():
        common = {"name": "通用装置", "category": "其它", "unit": "台", "brand": "", "description": ""}
        # blocked 候选刻意与查询同系列（ABC 前缀、前缀在同批确认列表中），仅因动态排除词
        # 被 hard_blocked——用于证明 not hard_blocked 守卫真正跳过系列 +5，而非前缀不匹配
        blocked = dict(common, **{
            "code": "M3T-SER-EX",
            "specification": "ABC-900 EXCL专用装置",
            "parsed_params": json.dumps({"model": "ABC-900", "attrs": {}}, ensure_ascii=False),
            "semantic_score": 0.0,
        })
        normal = dict(common, **{
            "code": "M3T-SER-OK",
            "specification": "ABC-999 普通装置",
            "parsed_params": json.dumps({"model": "ABC-999", "attrs": {}}, ensure_ascii=False),
            "semantic_score": 0.0,
        })
        return [blocked, normal]

    def test_series_boost_not_revive_hard_blocked(self):
        # 动态排除词 EXCL 仅出现在 blocked 候选规格文本（cand_text 含 c_full_spec）
        LLMRuleManager._active_rules_cache = [
            _dynamic_rule({"must_not_contain": ["EXCL"]}, rule_id="m3t-series-excl")
        ]
        query_info = self._series_query()
        # 前置事实：两个候选都满足系列前缀判定，且 abc 前缀在同批确认列表中——
        # blocked 候选“本该”吃到 +5，能否挡住全靠 not hard_blocked 守卫
        self.assertTrue(check_series_match("ABC-100", "ABC-900 EXCL专用装置"))
        self.assertTrue(check_series_match("ABC-100", "ABC-999 普通装置"))
        confirmed_series = ["abc", "zzex"]
        self.assertIn("abc", confirmed_series)

        with patch("app.matcher.hybrid_recall", return_value=self._fake_candidates()):
            boosted = match_item(query_info, batch_confirmed_series=confirmed_series)
        with patch("app.matcher.hybrid_recall", return_value=self._fake_candidates()):
            baseline = match_item(query_info, batch_confirmed_series=[])

        boost_tops = {c["code"]: c for c in boosted["top_candidates"]}
        base_tops = {c["code"]: c for c in baseline["top_candidates"]}
        blocked_boost, blocked_base = boost_tops["M3T-SER-EX"], base_tops["M3T-SER-EX"]
        normal_boost, normal_base = boost_tops["M3T-SER-OK"], base_tops["M3T-SER-OK"]

        # blocked 候选：双归零；虽满足系列全部前置条件，+5 被硬守卫跳过，两次运行均为 0
        self.assertTrue(blocked_boost["hard_blocked"])
        self.assertEqual(blocked_boost["max_score"], 0.0)
        self.assertEqual(blocked_boost["score"], 0.0)
        self.assertEqual(blocked_base["score"], 0.0)
        self.assertNotIn("系列优先", blocked_boost["match_reasons"])

        # 正常候选：确吃到系列 +5。match_item 输出层把 match_reasons 截前 2 条，
        # “系列优先”作为第 3 条不可见（见 matcher.py 输出截断），故用两次运行精确分差锁定。
        # 夹具经实测选型：ABC-999 基础分 24.6 远低于自身 cap 85，+5 不被钳平，分差可见
        self.assertFalse(normal_boost["hard_blocked"])
        self.assertAlmostEqual(normal_boost["score"] - normal_base["score"], 5.0, places=2)

        # 全员无一突破各自不可逆上限
        for result in (boosted, baseline):
            for cand in result["top_candidates"]:
                self.assertLessEqual(cand["score"], cand["max_score"] * 100.0 + 1e-9)


# ---------------------------------------------------------------------------
# V015-M3 R02 新增纯函数/轻依赖回归：关闭 A-M3-01 ~ A-M3-04
# ---------------------------------------------------------------------------

class TestR02UnitMultiplierQuantityGate(unittest.TestCase):
    """A-M3-01：非1倍率单位换算与确认/规则/漂移工程量安全门控。"""

    def setUp(self):
        from app.db_manager import init_db, get_db_connection, _search_materials_keyword_cached
        init_db()
        self._seeded = []
        conn = get_db_connection()
        for code, unit in (("R02-UNIT-M", "m"), ("R02-UNIT-G", "g")):
            self._seeded.append(code)
            conn.execute(
                "INSERT OR REPLACE INTO materials "
                "(code, category, name, specification, unit, brand, description, search_tokens, "
                "parsed_params, parsed_schema_version) VALUES (?,?,?,?,?,?,?,?,?,2)",
                (code, "暖通", "镀锌钢管" if unit == "m" else "不锈钢焊丝", "DN15", unit, "", "",
                 "镀锌钢管 DN15", json.dumps({"attrs": {"dn_val": 15}}, ensure_ascii=False)),
            )
        conn.commit()
        conn.close()
        _search_materials_keyword_cached.cache_clear()

    def tearDown(self):
        from app.db_manager import get_db_connection, _search_materials_keyword_cached
        if self._seeded:
            conn = get_db_connection()
            marks = ",".join("?" * len(self._seeded))
            conn.execute(f"DELETE FROM materials WHERE code IN ({marks})", tuple(self._seeded))
            conn.commit()
            conn.close()
            _search_materials_keyword_cached.cache_clear()

    def test_non_one_multiplier_blocked_without_explicit_conversion(self):
        for orig_qty, orig_unit, cand_unit in ((10, "km", "m"), (10, "kg", "g")):
            row = _row(
                original_name="镀锌钢管",
                original_spec="DN15",
                original_qty=orig_qty,
                original_unit=orig_unit,
                professional="暖通",
            )
            mat = _material(
                code="R02-UNIT-M",
                category="暖通",
                name="镀锌钢管",
                specification="DN15",
                unit=cand_unit,
                parsed_params=json.dumps({"attrs": {"dn_val": 15}}),
            )
            self.assertEqual(
                evaluate_hard_conflicts(row, mat),
                "单位非1倍率工程量需按目标单位显式换算复核阻断",
            )
            self.assertEqual(
                evaluate_hard_conflicts(
                    row, mat,
                    binding={"code": "R02-UNIT-M", "unit": cand_unit, "qty": 10000, "qty_ratio": 1000},
                    allow_unit_conversion=False,
                ),
                "单位非1倍率工程量需按目标单位显式换算复核阻断",
            )

    def test_raw_copy_non_one_multiplier_rejected_even_when_conversion_allowed(self):
        row = _row(
            original_name="镀锌钢管",
            original_spec="DN15",
            original_qty=10,
            original_unit="km",
            professional="暖通",
        )
        mat = _material(
            code="R02-UNIT-M",
            category="暖通",
            name="镀锌钢管",
            specification="DN15",
            unit="m",
            parsed_params=json.dumps({"attrs": {"dn_val": 15}}),
        )
        raw_copy = {"code": "R02-UNIT-M", "unit": "m", "qty": 10, "qty_ratio": 1}
        self.assertEqual(
            evaluate_hard_conflicts(row, mat, binding=raw_copy, allow_unit_conversion=True),
            "单位非1倍率工程量未按目标单位换算阻断",
        )

    def test_explicit_converted_binding_passes_when_unit_and_qty_match(self):
        row = _row(
            original_name="镀锌钢管",
            original_spec="DN15",
            original_qty=10,
            original_unit="km",
            professional="暖通",
        )
        mat = _material(
            code="R02-UNIT-M",
            category="暖通",
            name="镀锌钢管",
            specification="DN15",
            unit="m",
            parsed_params=json.dumps({"attrs": {"dn_val": 15}}),
        )
        converted = {"code": "R02-UNIT-M", "unit": "m", "qty": 10000, "qty_ratio": 1000}
        self.assertIsNone(
            evaluate_hard_conflicts(row, mat, binding=converted, allow_unit_conversion=True)
        )

        # 绑定目标单位与实时物料单位不一致仍阻断
        spoofed_unit = {"code": "R02-UNIT-M", "unit": "km", "qty": 10000, "qty_ratio": 1000}
        self.assertEqual(
            evaluate_hard_conflicts(row, mat, binding=spoofed_unit, allow_unit_conversion=True),
            "绑定目标单位与实时物料单位不一致阻断",
        )

    def test_unit_multiplier_one_and_one_to_many_ratio_preserved(self):
        row = _row(
            original_name="镀锌钢管",
            original_spec="DN15",
            original_qty=10,
            original_unit="米",
            professional="暖通",
        )
        mat = _material(
            code="R02-UNIT-M",
            category="暖通",
            name="镀锌钢管",
            specification="DN15",
            unit="m",
            parsed_params=json.dumps({"attrs": {"dn_val": 15}}),
        )
        self.assertIsNone(evaluate_hard_conflicts(row, mat, allow_unit_conversion=False))
        self.assertIsNone(
            evaluate_hard_conflicts(
                row, mat,
                binding={"code": "R02-UNIT-M", "unit": "m", "qty": 15, "qty_ratio": 1.5},
                allow_unit_conversion=True,
            )
        )

    def test_validate_bindings_rule_deny_and_saved_unit_drift(self):
        from app.db_manager import get_db_connection, _search_materials_keyword_cached

        row = _row(
            original_name="镀锌钢管",
            original_spec="DN15",
            original_qty=10,
            original_unit="km",
            professional="暖通",
        )
        converted = [{"code": "R02-UNIT-M", "unit": "m", "qty": 10000, "qty_ratio": 1000}]
        # 规则模式 (allow_unit_conversion=False) 即使带换算量也不静默自动接受
        res_rule = validate_bindings_against_row(row, converted, allow_unit_conversion=False)
        self.assertFalse(res_rule["ok"])
        self.assertEqual(res_rule["conflicts"][0]["reason"], "单位非1倍率工程量需按目标单位显式换算复核阻断")

        # 确认时 unit=m，随后库内漂移为 km：saved_bindings 独立抓出目标单位漂移与客户端不一致
        conn = get_db_connection()
        conn.execute("UPDATE materials SET unit='km' WHERE code='R02-UNIT-M'")
        conn.commit()
        conn.close()
        _search_materials_keyword_cached.cache_clear()

        row_m = _row(
            original_name="镀锌钢管",
            original_spec="DN15",
            original_qty=10,
            original_unit="米",
            professional="暖通",
        )
        res_drift = validate_bindings_against_row(
            row_m,
            [{"code": "R02-UNIT-M", "unit": "km", "qty": 10, "qty_ratio": 1}],
            allow_unit_conversion=True,
            saved_bindings=[{"code": "R02-UNIT-M", "unit": "m", "qty": 10, "qty_ratio": 1}],
        )
        self.assertFalse(res_drift["ok"])
        self.assertEqual(res_drift["conflicts"][0]["reason"], "单位倍率不可靠阻断")
        row_no_unit = _row(
            original_name="镀锌钢管",
            original_spec="DN15",
            original_qty=10,
            original_unit="",
            professional="暖通",
        )
        res_saved_drift = validate_bindings_against_row(
            row_no_unit,
            [{"code": "R02-UNIT-M", "unit": "km", "qty": 10, "qty_ratio": 1}],
            allow_unit_conversion=True,
            saved_bindings=[{"code": "R02-UNIT-M", "unit": "m", "qty": 10, "qty_ratio": 1}],
        )
        self.assertFalse(res_saved_drift["ok"])
        self.assertEqual(res_saved_drift["conflicts"][0]["reason"], "确认后物料目标单位发生漂移阻断")


class TestR02DynamicExclusionParity(unittest.TestCase):
    """A-M3-02：动态规则作用域、must_not_contain / must_contain 跨链路同源及缓存生命周期。"""

    def setUp(self):
        from app.db_manager import init_db
        init_db()
        LLMRuleManager.clear_active_cache()
        LLMRuleManager._active_rules_cache = []
        self._draft_rule_ids = []

    def tearDown(self):
        if self._draft_rule_ids:
            from app.llm_rule_engine import get_db_conn
            conn = get_db_conn()
            for rid in self._draft_rule_ids:
                conn.execute("DELETE FROM llm_rules WHERE rule_id = ?", (rid,))
                conn.execute("DELETE FROM rule_audit_logs WHERE rule_id = ?", (rid,))
            conn.commit()
            conn.close()
        LLMRuleManager.clear_active_cache()

    def test_must_not_contain_and_must_contain_parity_across_scopes(self):
        rules = [
            {
                "rule_id": "r02-cat-empty",
                "scope_level": "category",
                "scope_target": "",
                "parsed_dsl": {"matching_rules": {"must_not_contain": ["EXCL"]}},
            },
            {
                "rule_id": "r02-cat-pipe",
                "scope_level": "category",
                "scope_target": "钢管",
                "parsed_dsl": {"matching_rules": {"must_contain": ["304"]}},
            },
            {
                "rule_id": "r02-univ",
                "scope_level": "universal",
                "scope_target": "任意不生效目标",
                "parsed_dsl": {"matching_rules": {"must_not_contain": ["UNIVBAD"]}},
            },
        ]
        LLMRuleManager._active_rules_cache = rules

        q = prepare_query("镀锌钢管", "DN15 304", "暖通")
        row = _row(original_name="镀锌钢管", original_spec="DN15 304", professional="暖通")

        # 命中 must_not_contain
        cand_excl = _material(code="C-EXCL", category="暖通", name="镀锌钢管", specification="DN15 304 EXCL")
        score_e, reasons_e, detail_e = score_candidate_with_detail(q, cand_excl)
        self.assertTrue(detail_e["hard_blocked"])
        self.assertEqual(score_e, 0.0)
        self.assertEqual(detail_e["max_score"], 0.0)
        self.assertIn("包含排除词 [EXCL]", reasons_e)
        self.assertIn("包含排除词 [EXCL]", evaluate_dynamic_exclusions(row, cand_excl))
        self.assertIn("动态规则硬排除: 包含排除词 [EXCL]", evaluate_hard_conflicts(row, cand_excl))

        # 缺失 must_contain
        cand_missing = _material(code="C-MISS", category="暖通", name="镀锌钢管", specification="DN15")
        score_m, reasons_m, detail_m = score_candidate_with_detail(q, cand_missing)
        self.assertTrue(detail_m["hard_blocked"])
        self.assertEqual(score_m, 0.0)
        self.assertEqual(detail_m["max_score"], 0.0)
        self.assertIn("缺少必含词 [304]", reasons_m)
        self.assertIn("缺少必含词 [304]", evaluate_dynamic_exclusions(row, cand_missing))
        self.assertIn("动态规则硬排除: 缺少必含词 [304]", evaluate_hard_conflicts(row, cand_missing))

        # universal scope 命中
        cand_univ = _material(code="C-UNIV", category="暖通", name="镀锌钢管", specification="DN15 304 UNIVBAD")
        self.assertIn("包含排除词 [UNIVBAD]", evaluate_dynamic_exclusions(row, cand_univ))

        # 满足 must_contain 且无排除词放行
        cand_ok = _material(code="C-OK", category="暖通", name="镀锌钢管", specification="DN15 304")
        self.assertEqual(evaluate_dynamic_exclusions(row, cand_ok), [])
        self.assertIsNone(evaluate_hard_conflicts(row, cand_ok))

        # 不匹配作用域 (阀门 != 钢管) 不触发 must_contain=[304]
        LLMRuleManager._active_rules_cache = [rules[1]]
        valve_row = _row(original_name="闸阀", original_spec="DN15", professional="暖通")
        valve_cand = _material(code="C-VALVE", category="暖通", name="闸阀", specification="DN15 铸铁")
        self.assertEqual(evaluate_dynamic_exclusions(valve_row, valve_cand), [])
        self.assertIsNone(evaluate_hard_conflicts(valve_row, valve_cand))

    def test_get_active_rules_missing_table_safe_and_draft_clears_cache(self):
        import sqlite3
        LLMRuleManager.clear_active_cache()
        with patch("app.llm_rule_engine.get_db_conn", return_value=sqlite3.connect(":memory:")):
            self.assertEqual(LLMRuleManager.get_active_rules(), [])

        from app.auth_manager import init_auth_db, get_auth_connection, get_user_by_username, issue_token_for_user
        init_auth_db()
        conn = get_auth_connection()
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch) VALUES ('r02tester', 'hash', 'r02tester', 'employee', 1, 0, 'ep-r02')"
        )
        conn.commit()
        conn.close()
        r02_user = get_user_by_username("r02tester")
        issue_token_for_user(r02_user)

        LLMRuleManager._active_rules_cache = [{"rule_id": "stale"}]
        self._draft_rule_ids.append("R02-DRAFT-CLEAR")
        res = LLMRuleManager.save_rule_draft(
            {
                "rule_id": "R02-DRAFT-CLEAR",
                "rule_name": "R02清理测试",
                "parsed_dsl": {"matching_rules": {"must_not_contain": ["X"]}},
            },
            r02_user,
        )
        self.assertTrue(res["success"])
        self.assertIsNone(LLMRuleManager._active_rules_cache)


class TestR02CandidateFallbackAttrsParity(unittest.TestCase):
    """A-M3-03：候选缺 parsed_params 或坏 JSON 时与 matcher 同源物理参数抽取及分字段隔离。"""

    def setUp(self):
        LLMRuleManager.clear_active_cache()
        LLMRuleManager._active_rules_cache = []

    def tearDown(self):
        LLMRuleManager.clear_active_cache()

    def test_bad_json_strict_contracts_preserved(self):
        self.assertEqual(parse_material_attrs({"parsed_params": "{bad"}), {})
        self.assertEqual(resolve_material_brand({"brand": "", "parsed_params": "{bad"}), "")

    def test_dn_and_grade_fallback_parity_when_parsed_params_empty_or_bad(self):
        for bad_cache in ("", "{损坏json"):
            # DN15 vs DN20
            q_dn = prepare_query("镀锌钢管", "DN15", "暖通")
            r_dn = _row(original_name="镀锌钢管", original_spec="DN15", professional="暖通")
            c_dn = _material(
                name="镀锌钢管", specification="DN20", category="暖通", parsed_params=bad_cache
            )
            c_dn_attrs = resolve_material_attrs(c_dn)
            self.assertEqual(_to_finite_float(c_dn_attrs.get("dn_val")), 20.0)
            score_dn, _, detail_dn = score_candidate_with_detail(q_dn, c_dn)
            self.assertFalse(detail_dn["hard_blocked"])
            self.assertAlmostEqual(detail_dn["max_score"], 0.15, places=6)
            self.assertLessEqual(score_dn, 15.0 + 1e-6)
            self.assertEqual(evaluate_hard_conflicts(r_dn, c_dn), "管径口径冲突阻断")

            # 304 vs 316L
            q_mat = prepare_query("不锈钢管", "304", "暖通")
            r_mat = _row(original_name="不锈钢管", original_spec="304", professional="暖通")
            c_mat = _material(
                name="不锈钢管", specification="316L 不锈钢管", category="暖通", parsed_params=bad_cache
            )
            self.assertEqual(normalize_material_grade(resolve_material_attrs(c_mat).get("材质")), "不锈钢316L")
            score_m, _, detail_m = score_candidate_with_detail(q_mat, c_mat)
            self.assertAlmostEqual(detail_m["max_score"], 0.15, places=6)
            self.assertLessEqual(score_m, 15.0 + 1e-6)
            self.assertEqual(evaluate_hard_conflicts(r_mat, c_mat), "不锈钢牌号冲突硬隔离(304/316L)")

            # 06Cr19Ni10 / S30408 别名识别为 304，与 304 兼容而与 316L 冲突
            c_304_alias = _material(
                name="不锈钢管", specification="06Cr19Ni10 不锈钢管", category="暖通", parsed_params=bad_cache
            )
            self.assertEqual(normalize_material_grade(resolve_material_attrs(c_304_alias).get("材质")), "不锈钢304")
            self.assertIsNone(evaluate_hard_conflicts(r_mat, c_304_alias))

    def test_dn_and_od_fields_never_cross_contaminate_on_fallback(self):
        c_de = _material(name="PPR给水管", specification="De25", category="给排水", parsed_params="")
        c_de_attrs = resolve_material_attrs(c_de)
        self.assertEqual(_to_finite_float(c_de_attrs.get("外径")), 25.0)
        self.assertIsNone(c_de_attrs.get("dn_val"))

        # DN15 vs De25：分字段不跨比
        r_dn = _row(original_name="给水管", original_spec="DN15", professional="给排水")
        self.assertIsNone(evaluate_hard_conflicts(r_dn, c_de))

        # De20 vs De25：同为外径且超 10% 阈值阻断
        r_de = _row(original_name="PPR给水管", original_spec="De20", professional="给排水")
        self.assertEqual(evaluate_hard_conflicts(r_de, c_de), "管材外径冲突阻断")

    def test_cached_parsed_params_overrides_text_on_drift(self):
        r_mat = _row(original_name="不锈钢管", original_spec="304", professional="暖通")
        drifted = _material(
            name="不锈钢管",
            specification="304 不锈钢管",
            category="暖通",
            parsed_params=json.dumps({"attrs": {"材质": "316L"}}, ensure_ascii=False),
        )
        self.assertEqual(evaluate_hard_conflicts(r_mat, drifted), "不锈钢牌号冲突硬隔离(304/316L)")


class TestR02LiftPumpBidirectionalGate(unittest.TestCase):
    """A-M3-04：提升泵双向互斥（带泵↔不带泵/无提升泵/无泵；unknown 不阻断）。"""

    def setUp(self):
        LLMRuleManager.clear_active_cache()
        LLMRuleManager._active_rules_cache = []

    def tearDown(self):
        LLMRuleManager.clear_active_cache()

    def test_detect_pump_state_and_check_conflict_bidirectional(self):
        for neg in ("不带提升泵", "无提升泵", "不带冷凝水提升泵", "无泵"):
            self.assertIs(detect_pump_state(f"风机盘管 {neg}"), False, neg)
            self.assertEqual(
                check_pump_conflict("带提升泵", neg),
                "提升泵要求冲突硬隔离(带泵/不带泵)",
            )
        for pos in ("带提升泵", "含提升泵", "配提升泵"):
            self.assertIs(detect_pump_state(f"风机盘管 {pos}"), True, pos)
            self.assertEqual(
                check_pump_conflict("不带提升泵", pos),
                "提升泵否定冲突硬隔离(不带泵/带泵)",
            )
        # 通用水泵名不误判；未说明保持 None (unknown)
        self.assertIsNone(detect_pump_state("风机盘管 FP-68"))
        self.assertIsNone(check_pump_conflict("带提升泵", "风机盘管 FP-68"))
        self.assertIsNone(check_pump_conflict("风机盘管 FP-68", "不带提升泵"))

    def test_pump_required_vs_no_pump_full_chain_parity(self):
        q = prepare_query("风机盘管", "带提升泵", "暖通")
        r = _row(original_name="风机盘管", original_spec="带提升泵", professional="暖通")

        for mat_kw in (
            {"specification": "不带提升泵", "description": "", "parsed_params": ""},
            {"specification": "无泵", "description": "", "parsed_params": ""},
            {"specification": "FP-68", "description": "无提升泵", "parsed_params": ""},
            {"specification": "FP-68", "description": "", "parsed_params": json.dumps({"attrs": {"提升泵": False}}, ensure_ascii=False)},
        ):
            cand = _material(category="暖通", name="风机盘管", **mat_kw)
            score, reasons, detail = score_candidate_with_detail(q, cand)
            self.assertFalse(detail["hard_blocked"])
            self.assertAlmostEqual(detail["max_score"], 0.15, places=6)
            self.assertLessEqual(score, 15.0 + 1e-6)
            self.assertIn("提升泵要求冲突硬隔离(带泵/不带泵)", reasons)
            self.assertEqual(
                evaluate_hard_conflicts(r, cand),
                "提升泵要求冲突硬隔离(带泵/不带泵)",
            )

        # 对称正向：带泵 vs 带泵不冲突；带泵 vs unknown 不冲突
        self.assertIsNone(evaluate_hard_conflicts(r, _material(category="暖通", name="风机盘管", specification="带提升泵")))
        self.assertIsNone(evaluate_hard_conflicts(r, _material(category="暖通", name="风机盘管", specification="FP-68")))


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# V015-M3 R03：A-M3-01 / A-M3-02 / A-M3-05 纯函数层闭环测试
# ---------------------------------------------------------------------------

class TestM3R03AuditClosureUnit(unittest.TestCase):
    """V015-M3 R03：目标单位证据与漂移、规则读取 fail-closed、动态规则作用域隔离。"""

    def setUp(self):
        LLMRuleManager.clear_active_cache()

    def tearDown(self):
        LLMRuleManager.clear_active_cache()

    def test_a_m3_01_non_one_multiplier_rejects_missing_target_unit(self):
        row = _row(
            original_name="镀锌钢管",
            original_spec="DN100",
            original_qty=10,
            original_unit="km",
            professional="暖通",
            original_category="暖通",
        )
        mat = _material(
            code="CONC-M",
            name="镀锌钢管",
            specification="DN100",
            unit="m",
            category="暖通",
        )
        # 无目标 unit 提交 10 km -> 10000 必须被阻断
        reason_missing = evaluate_hard_conflicts(
            row, mat,
            binding={"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0},
            allow_unit_conversion=True,
        )
        self.assertIsNotNone(reason_missing)
        self.assertIn("缺失目标单位", reason_missing)

        # 显式目标 unit="m" 且换算准确 -> 放行
        reason_ok = evaluate_hard_conflicts(
            row, mat,
            binding={"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m"},
            allow_unit_conversion=True,
        )
        self.assertIsNone(reason_ok)

    def test_a_m3_01_saved_unit_evidence_drift_and_tamper_checks(self):
        row = _row(
            original_name="镀锌钢管",
            original_spec="DN100",
            original_qty=10,
            original_unit="km",
            professional="暖通",
            original_category="暖通",
        )
        live_m = {
            "CONC-M": _material(
                code="CONC-M", name="镀锌钢管", specification="DN100", unit="m", category="暖通"
            )
        }
        live_km = {
            "CONC-M": _material(
                code="CONC-M", name="镀锌钢管", specification="DN100", unit="km", category="暖通"
            )
        }
        binds_m = [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m"}]
        binds_no_unit = [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0}]

        with patch("app.binding_decisions.validate_binding_codes", return_value=[]):
            # 1) 遗留无单位证据记录 + require_saved_unit_evidence=True -> 无论请求带不带 unit 都阻断
            with patch("app.binding_decisions.fetch_live_material_map", return_value=live_m):
                res_legacy_no_unit = validate_bindings_against_row(
                    row, binds_no_unit, saved_bindings=binds_no_unit, require_saved_unit_evidence=True
                )
                self.assertFalse(res_legacy_no_unit["ok"])
                self.assertIn("缺失目标单位", res_legacy_no_unit["conflicts"][0]["reason"])

                res_legacy_with_req_unit = validate_bindings_against_row(
                    row, binds_m, saved_bindings=binds_no_unit, require_saved_unit_evidence=True
                )
                self.assertFalse(res_legacy_with_req_unit["ok"])
                self.assertIn("缺少权威目标单位证据", res_legacy_with_req_unit["conflicts"][0]["reason"])

            # 2) 库单位从 m 漂移成 km -> 无论客户端保留 m / 改写 km / 删 unit 一律阻断
            saved_ok = [{
                "code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m",
                "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": 1000.0,
            }]
            with patch("app.binding_decisions.fetch_live_material_map", return_value=live_km):
                for client_binds in (
                    [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m"}],
                    [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "km"}],
                    binds_no_unit,
                ):
                    res_drift = validate_bindings_against_row(
                        row, client_binds, saved_bindings=saved_ok, require_saved_unit_evidence=True
                    )
                    self.assertFalse(res_drift["ok"], client_binds)

            # 3) 库未漂移但客户端删去或篡改 unit -> 阻断
            with patch("app.binding_decisions.fetch_live_material_map", return_value=live_m):
                res_del = validate_bindings_against_row(
                    row, binds_no_unit, saved_bindings=saved_ok, require_saved_unit_evidence=True
                )
                self.assertFalse(res_del["ok"])
                self.assertIn("缺失目标单位", res_del["conflicts"][0]["reason"])

                res_chg = validate_bindings_against_row(
                    row,
                    [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "km"}],
                    saved_bindings=saved_ok,
                    require_saved_unit_evidence=True,
                )
                self.assertFalse(res_chg["ok"])

                # 正例：一切一致 -> 放行
                res_pass = validate_bindings_against_row(
                    row, binds_m, saved_bindings=saved_ok, require_saved_unit_evidence=True
                )
                self.assertTrue(res_pass["ok"], res_pass)

            # 4) 1.0 倍率自定义一对多系数 + 米/m 等价 -> 保留放行
            row_one = _row(
                original_name="镀锌钢管", original_spec="DN100", original_qty=200,
                original_unit="米", professional="暖通", original_category="暖通",
            )
            saved_one = [{
                "code": "CONC-M", "qty": 300.0, "qty_ratio": 1.5, "unit": "m",
                "confirmed_unit": "m", "confirmed_orig_unit": "米", "confirmed_unit_mult": 1.0,
            }]
            with patch("app.binding_decisions.fetch_live_material_map", return_value=live_m):
                res_one = validate_bindings_against_row(
                    row_one,
                    [{"code": "CONC-M", "qty": 300.0, "qty_ratio": 1.5, "unit": "米"}],
                    saved_bindings=saved_one,
                    require_saved_unit_evidence=True,
                )
                self.assertTrue(res_pass["ok"] and res_one["ok"], res_one)

    def test_a_m3_02_get_active_rules_distinguishes_missing_table_vs_errors_and_recovers(self):
        import sqlite3
        import tempfile
        from app.llm_rule_engine import DynamicRuleUnavailableError

        with tempfile.TemporaryDirectory() as tmpdir:
            db_missing = os.path.join(tmpdir, "rules_missing.db")
            db_broken = os.path.join(tmpdir, "rules_broken.db")
            conn = sqlite3.connect(db_missing)
            try:
                conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
                conn.commit()
            finally:
                conn.close()

            # 1) 全新未建表库确实没有 llm_rules 表 -> 允许返回 []
            with patch.dict(os.environ, {"MATERIALS_DB_PATH": db_missing, "TESTING": "1"}):
                with patch("app.llm_rule_engine.init_rule_engine_db", lambda: None):
                    LLMRuleManager.clear_active_cache()
                    self.assertEqual(LLMRuleManager.get_active_rules(), [])

            # 2) llm_rules 表存在但缺列(如缺 priority) -> 必须抛 DynamicRuleUnavailableError 且不写缓存
            conn = sqlite3.connect(db_broken)
            try:
                conn.execute(
                    "CREATE TABLE llm_rules (id INTEGER PRIMARY KEY, rule_id TEXT, status TEXT, rule_json TEXT)"
                )
                conn.commit()
            finally:
                conn.close()
            with patch.dict(os.environ, {"MATERIALS_DB_PATH": db_broken, "TESTING": "1"}):
                with patch("app.llm_rule_engine.init_rule_engine_db", lambda: None):
                    LLMRuleManager.clear_active_cache()
                    with self.assertRaises(DynamicRuleUnavailableError):
                        LLMRuleManager.get_active_rules()
                    self.assertIsNone(LLMRuleManager._active_rules_cache)

                    # 3) 补齐列但写入损坏 JSON -> 必须抛 DynamicRuleUnavailableError
                    from app.llm_rule_engine import compute_rule_content_digest
                    conn = sqlite3.connect(db_broken)
                    try:
                        for ddl in (
                            "ALTER TABLE llm_rules ADD COLUMN priority INTEGER DEFAULT 10",
                            "ALTER TABLE llm_rules ADD COLUMN scope_level TEXT DEFAULT 'universal'",
                            "ALTER TABLE llm_rules ADD COLUMN scope_target TEXT DEFAULT ''",
                            "ALTER TABLE llm_rules ADD COLUMN discipline TEXT DEFAULT '全专业'",
                            "ALTER TABLE llm_rules ADD COLUMN revision INTEGER DEFAULT 1",
                            "ALTER TABLE llm_rules ADD COLUMN content_digest TEXT DEFAULT ''",
                            "ALTER TABLE llm_rules ADD COLUMN approved_revision INTEGER DEFAULT 1",
                            "ALTER TABLE llm_rules ADD COLUMN approved_digest TEXT DEFAULT ''",
                            "ALTER TABLE llm_rules ADD COLUMN author_id INTEGER DEFAULT 1",
                            "ALTER TABLE llm_rules ADD COLUMN approver_id INTEGER DEFAULT 2",
                            "ALTER TABLE llm_rules ADD COLUMN publisher_id INTEGER DEFAULT 3",
                        ):
                            conn.execute(ddl)
                        conn.execute(
                            """
                            INSERT INTO llm_rules (
                                rule_id, status, rule_json, priority, scope_level, scope_target, discipline,
                                revision, content_digest, approved_revision, approved_digest,
                                author_id, approver_id, publisher_id
                            ) VALUES (?, 'published', ?, 1, 'universal', '', '全专业', 1, '', 1, '', 1, 2, 3)
                            """,
                            ("bad-json", "{broken_json"),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    LLMRuleManager.clear_active_cache()
                    with self.assertRaises(DynamicRuleUnavailableError):
                        LLMRuleManager.get_active_rules()

                    # 4) 合法 JSON 但 matching_rules 结构非法(非 dict) -> 必须抛 DynamicRuleUnavailableError
                    conn = sqlite3.connect(db_broken)
                    try:
                        bad_struct_json = json.dumps({"matching_rules": ["wrong_type"]}, ensure_ascii=False)
                        bad_struct_digest = compute_rule_content_digest("universal", "", "全专业", 1, bad_struct_json)
                        conn.execute(
                            "UPDATE llm_rules SET rule_json = ?, content_digest = ?, approved_digest = ? WHERE rule_id = 'bad-json'",
                            (bad_struct_json, bad_struct_digest, bad_struct_digest),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    LLMRuleManager.clear_active_cache()
                    with self.assertRaises(DynamicRuleUnavailableError):
                        LLMRuleManager.get_active_rules()

                    # 5) 修复后恢复可用
                    conn = sqlite3.connect(db_broken)
                    try:
                        good_json = json.dumps({"matching_rules": {"must_not_contain": ["禁用词"]}}, ensure_ascii=False)
                        good_digest = compute_rule_content_digest("universal", "", "全专业", 1, good_json)
                        conn.execute(
                            "UPDATE llm_rules SET rule_json = ?, content_digest = ?, approved_digest = ? WHERE rule_id = 'bad-json'",
                            (good_json, good_digest, good_digest),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    LLMRuleManager.clear_active_cache()
                    rules = LLMRuleManager.get_active_rules()
                    self.assertEqual(len(rules), 1)
                    self.assertEqual(rules[0]["rule_id"], "bad-json")

    def test_a_m3_02_rule_unavailability_fails_closed_across_pure_chains_and_recovers(self):
        from app.llm_rule_engine import DynamicRuleUnavailableError

        row = _row(
            original_name="镀锌钢管", original_spec="DN100", original_qty=10,
            original_unit="米", professional="暖通", original_category="暖通",
        )
        mat = _material(
            code="PIPE100", name="镀锌钢管", specification="DN100", unit="米", category="暖通",
            semantic_score=0.0,
        )
        q_info = {
            "name": "镀锌钢管", "raw_name": "镀锌钢管", "spec": "DN100",
            "professional": "暖通", "category": "暖通", "original_category": "暖通",
            "unit": "米", "brand": "", "params": {"dn": 100}, "attrs": {"dn": 100},
        }
        with patch.object(
            LLMRuleManager, "get_active_rules", side_effect=DynamicRuleUnavailableError("db col err")
        ):
            dyn_reasons = evaluate_dynamic_exclusions(row, mat)
            self.assertTrue(dyn_reasons)
            self.assertIn("动态规则不可用", dyn_reasons[0])

            conflict = evaluate_hard_conflicts(
                row, mat, binding={"code": "PIPE100", "qty": 10, "qty_ratio": 1.0, "unit": "米"}
            )
            self.assertIsNotNone(conflict)
            self.assertIn("动态规则不可用", conflict)

            score, reasons, detail = score_candidate_with_detail(q_info, mat, 0.0)
            self.assertEqual(score, 0.0)
            self.assertTrue(detail["hard_blocked"])
            self.assertTrue(any("动态规则不可用" in r for r in reasons), reasons)

            with patch("app.matcher.hybrid_recall", return_value=[mat]):
                matched = match_item(q_info)
            tops = matched["top_candidates"]
            self.assertTrue(tops)
            self.assertEqual(tops[0]["score"], 0.0)
            self.assertTrue(tops[0].get("hard_blocked"))

        # 规则库恢复为空规则时立即恢复正常
        LLMRuleManager._active_rules_cache = []
        self.assertEqual(evaluate_dynamic_exclusions(row, mat), [])
        self.assertIsNone(
            evaluate_hard_conflicts(
                row, mat, binding={"code": "PIPE100", "qty": 10, "qty_ratio": 1.0, "unit": "米"}
            )
        )
        ok_score, _, _ = score_candidate_with_detail(q_info, mat, 0.0)
        self.assertGreater(ok_score, 0.0)

    def test_a_m3_05_discipline_scope_isolation_and_universal_or_alias_rules(self):
        from app.normalizer import normalize_professional_category as norm_prof

        self.assertEqual(norm_prof("医用气体"), normalize_professional_category("医用气体"))

        plumbing_rule = {
            "rule_id": "plumb-only-excl",
            "scope_level": "discipline",
            "scope_target": "给排水",
            "parsed_dsl": {
                "matching_rules": {"must_not_contain": ["禁用特征"], "score_boost_keywords": {}},
                "cleaning_rules": [{"action": "remove_token", "token": "CLEANME"}],
                "extraction_rules": [{"key": "plumb_tag", "pattern": r"PTAG-(\d+)"}],
            },
        }
        LLMRuleManager._active_rules_cache = [plumbing_rule]

        # 负例：暖通行不受给排水规则拦截，也不触发给排水清洗规则与参数抽取规则
        hvac_row = _row(
            original_name="轴流排风机", original_spec="FX-100", original_qty=1,
            original_unit="台", professional="暖通", original_category="暖通",
        )
        hvac_cand = _material(
            code="FAN-1", name="轴流排风机", specification="FX-100 禁用特征", unit="台", category="暖通"
        )
        hvac_q = {
            "name": "轴流排风机", "raw_name": "轴流排风机", "spec": "FX-100",
            "professional": "暖通", "category": "暖通", "original_category": "暖通",
            "unit": "台", "brand": "", "params": {}, "attrs": {},
        }
        self.assertEqual(evaluate_dynamic_exclusions(hvac_row, hvac_cand), [])
        self.assertIsNone(
            evaluate_hard_conflicts(
                hvac_row, hvac_cand, binding={"code": "FAN-1", "qty": 1, "qty_ratio": 1.0, "unit": "台"}
            )
        )
        hvac_score, _, _ = score_candidate_with_detail(hvac_q, hvac_cand, 0.0)
        self.assertGreater(hvac_score, 0.0)

        from app.normalizer import clean_text_standard
        self.assertIn("cleanme", clean_text_standard("FX-100 CLEANME", discipline="暖通", category="暖通").lower())
        pq_hvac = prepare_query("轴流排风机 CLEANME", "FX-100 PTAG-88", "暖通", category="暖通")
        self.assertIn("cleanme", pq_hvac.get("name", "").lower())
        self.assertNotIn("plumb_tag", (pq_hvac.get("parsed_spec") or {}).get("attrs", {}))

        # 正例：给排水行受给排水规则硬拦截，且触发给排水清洗规则与参数抽取规则
        plumb_row = _row(
            original_name="给水泵", original_spec="Q=10", original_qty=1,
            original_unit="台", professional="给排水", original_category="给排水",
        )
        plumb_cand = _material(
            code="PUMP-1", name="给水泵", specification="Q=10 禁用特征", unit="台", category="给排水"
        )
        plumb_q = {
            "name": "给水泵", "raw_name": "给水泵", "spec": "Q=10",
            "professional": "给排水", "category": "给排水", "original_category": "给排水",
            "unit": "台", "brand": "", "params": {}, "attrs": {},
        }
        self.assertTrue(evaluate_dynamic_exclusions(plumb_row, plumb_cand))
        self.assertIsNotNone(
            evaluate_hard_conflicts(
                plumb_row, plumb_cand, binding={"code": "PUMP-1", "qty": 1, "qty_ratio": 1.0, "unit": "台"}
            )
        )
        plumb_score, _, plumb_detail = score_candidate_with_detail(plumb_q, plumb_cand, 0.0)
        self.assertEqual(plumb_score, 0.0)
        self.assertTrue(plumb_detail["hard_blocked"])
        self.assertNotIn("cleanme", clean_text_standard("Q=10 CLEANME", discipline="给排水", category="给排水").lower())
        pq_plumb = prepare_query("给水泵 CLEANME", "Q=10 PTAG-88", "给排水", category="给排水")
        self.assertNotIn("cleanme", pq_plumb.get("name", "").lower())
        self.assertEqual((pq_plumb.get("parsed_spec") or {}).get("attrs", {}).get("plumb_tag"), 88.0)

        # 专业别名命中：医气规则匹配“医用气体”查询，不误伤“暖通”
        LLMRuleManager._active_rules_cache = [{
            "rule_id": "medgas-excl",
            "scope_level": "discipline",
            "scope_target": "医气",
            "parsed_dsl": {"matching_rules": {"must_not_contain": ["禁用特征"]}},
        }]
        med_row = _row(
            original_name="氧气终端", original_spec="", original_qty=1,
            original_unit="套", professional="医用气体", original_category="医用气体",
        )
        med_cand = _material(
            code="MED-1", name="氧气终端", specification="禁用特征", unit="套", category="医气"
        )
        self.assertTrue(evaluate_dynamic_exclusions(med_row, med_cand))
        self.assertEqual(evaluate_dynamic_exclusions(hvac_row, hvac_cand), [])

        # 显式通用规则("通用" / scope_level="universal")对暖通与给排水均生效
        for univ_rule in (
            {
                "rule_id": "univ-target",
                "scope_level": "discipline",
                "scope_target": "通用",
                "parsed_dsl": {"matching_rules": {"must_not_contain": ["禁用特征"]}},
            },
            {
                "rule_id": "univ-level",
                "scope_level": "universal",
                "scope_target": "给排水",
                "parsed_dsl": {"matching_rules": {"must_not_contain": ["禁用特征"]}},
            },
        ):
            LLMRuleManager._active_rules_cache = [univ_rule]
            self.assertTrue(evaluate_dynamic_exclusions(hvac_row, hvac_cand), univ_rule["rule_id"])
            self.assertTrue(evaluate_dynamic_exclusions(plumb_row, plumb_cand), univ_rule["rule_id"])


# ---------------------------------------------------------------------------
# V015-M3 R04：A-M3-01 区分单位值与权威来源证据（强证据门单测）
# ---------------------------------------------------------------------------

class TestM3R04LegacyUnitEvidenceUnit(unittest.TestCase):
    """V015-M3 R04：遗留非空 unit / 缺漏/残缺/类型异常 confirmed_* 一律不升格为权威证据。"""

    def test_r04_legacy_nonempty_unit_without_confirmed_fields_blocked(self):
        row = _row(
            original_name="镀锌钢管",
            original_spec="DN100",
            original_qty=10,
            original_unit="km",
            professional="暖通",
            original_category="暖通",
        )
        live_m = {
            "CONC-M": _material(
                code="CONC-M", name="镀锌钢管", specification="DN100", unit="m", category="暖通"
            )
        }
        live_km = {
            "CONC-M": _material(
                code="CONC-M", name="镀锌钢管", specification="DN100", unit="km", category="暖通"
            )
        }
        legacy_nonempty_saved = [
            {"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "km"}
        ]
        legacy_empty_saved = [
            {"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": ""}
        ]
        legacy_missing_saved = [
            {"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0}
        ]
        req_variants = (
            [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "km"}],
            [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m"}],
            [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0}],
            [{
                "code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "km",
                "confirmed_unit": "km", "confirmed_orig_unit": "km",
                "confirmed_unit_mult": 1.0, "confirmed_unit_version": "r04-v1",
            }],
        )
        with patch("app.binding_decisions.validate_binding_codes", return_value=[]):
            for live_map in (live_m, live_km):
                with patch("app.binding_decisions.fetch_live_material_map", return_value=live_map):
                    for saved_binds in (legacy_nonempty_saved, legacy_empty_saved, legacy_missing_saved):
                        for req_binds in req_variants:
                            res = validate_bindings_against_row(
                                row,
                                req_binds,
                                saved_bindings=saved_binds,
                                require_saved_unit_evidence=True,
                            )
                            self.assertFalse(res["ok"], (live_map, saved_binds, req_binds, res))
                            self.assertTrue(res["conflicts"], res)

    def test_r04_partial_malformed_and_inconsistent_confirmed_fields_rejected(self):
        row = _row(
            original_name="镀锌钢管",
            original_spec="DN100",
            original_qty=10,
            original_unit="km",
            professional="暖通",
            original_category="暖通",
        )
        live_m = {
            "CONC-M": _material(
                code="CONC-M", name="镀锌钢管", specification="DN100", unit="m", category="暖通"
            )
        }
        req_m = [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m"}]

        bad_saved_cases = [
            # 仅部分 confirmed_* 字段
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m"}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km"}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_unit_mult": 1000.0}],
            [{"code": "CONC-M", "unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": 1000.0}],
            # 类型异常 / bool / NaN / <=0
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": 123, "confirmed_orig_unit": "km", "confirmed_unit_mult": 1000.0}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": True, "confirmed_orig_unit": "km", "confirmed_unit_mult": 1000.0}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": 999, "confirmed_unit_mult": 1000.0}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": "1000.0"}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": True}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": -1000.0}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": 0.0}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": float("nan")}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": 1000.0, "confirmed_orig_qty": True}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": 1000.0, "confirmed_orig_qty": "10"}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": 1000.0, "confirmed_orig_qty": 0.0}],
            # 非法/不可信来源及版本标识
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": 1000.0, "confirmed_unit_source": "client_legacy"}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": 1000.0, "confirmed_unit_version": "legacy-v0"}],
            # 自相矛盾与上下文漂移（orig_qty 变化 / mult 与 km->m 矛盾 / orig_unit 不符）
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": 1.0}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "m", "confirmed_unit_mult": 1.0}],
            [{"code": "CONC-M", "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": 1000.0, "confirmed_orig_qty": 5.0}],
        ]

        with patch("app.binding_decisions.validate_binding_codes", return_value=[]), patch(
            "app.binding_decisions.fetch_live_material_map", return_value=live_m
        ):
            for saved_binds in bad_saved_cases:
                res = validate_bindings_against_row(
                    row,
                    req_m,
                    saved_bindings=saved_binds,
                    require_saved_unit_evidence=True,
                )
                self.assertFalse(res["ok"], f"must reject {saved_binds}, got {res}")

            # 完整权威证据与上下文一致时放行
            res_ok = validate_bindings_against_row(
                row,
                req_m,
                saved_bindings=[{
                    "code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m",
                    "confirmed_unit": "m", "confirmed_orig_unit": "km",
                    "confirmed_orig_qty": 10.0, "confirmed_unit_mult": 1000.0,
                    "confirmed_unit_source": "server_live_material",
                    "confirmed_unit_version": "r04-v1",
                }],
                require_saved_unit_evidence=True,
            )
            self.assertTrue(res_ok["ok"], res_ok)

    def test_r04_record_decision_strips_forged_evidence_and_derives_from_live_material(self):
        from app.auth_manager import init_auth_db
        from app.db_manager import init_db, get_db_connection
        from app.binding_decisions import (
            STATUS_MANUAL_CONFIRMED,
            SOURCE_MANUAL,
            clear_binding_decisions,
            extract_saved_bindings,
            get_decision,
            init_binding_decision_db,
            record_decision,
            UNIT_EVIDENCE_SOURCE,
            UNIT_EVIDENCE_VERSION,
        )

        init_auth_db()
        init_db()
        init_binding_decision_db()
        mconn = get_db_connection()
        try:
            mconn.execute("DELETE FROM materials WHERE code IN ('R04-CONC-M', 'R04-EMPTY-U')")
            mconn.execute(
                "INSERT INTO materials (code, name, specification, unit, category) VALUES (?, ?, ?, ?, ?)",
                ("R04-CONC-M", "镀锌钢管", "DN100", "m", "暖通"),
            )
            mconn.execute(
                "INSERT INTO materials (code, name, specification, unit, category) VALUES (?, ?, ?, ?, ?)",
                ("R04-EMPTY-U", "无单位衬套", "DN100", "", "暖通"),
            )
            mconn.commit()
        finally:
            mconn.close()

        try:
            row = _row(
                original_name="镀锌钢管", original_spec="DN100",
                original_qty=10, original_unit="km", professional="暖通",
            )
            forged_binds = [{
                "code": "R04-CONC-M",
                "qty": 10000.0,
                "qty_ratio": 1000.0,
                "unit": "m",
                "confirmed_unit": "km",
                "confirmed_orig_unit": "m",
                "confirmed_orig_qty": 999.0,
                "confirmed_unit_mult": 1.0,
                "confirmed_unit_source": "client_fake",
                "confirmed_unit_version": "fake-v9",
            }]
            record_decision(
                user_id=1, scope_key="r04_u.xlsx|暖通", row=row, bindings=forged_binds,
                decision=STATUS_MANUAL_CONFIRMED, source=SOURCE_MANUAL, reason="test",
            )
            rec = get_decision(user_id=1, scope_key="r04_u.xlsx|暖通", row=row, bindings=forged_binds)
            saved = extract_saved_bindings(rec)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["confirmed_unit"], "m")
            self.assertEqual(saved[0]["confirmed_orig_unit"], "km")
            self.assertEqual(saved[0]["confirmed_orig_qty"], 10.0)
            self.assertEqual(saved[0]["confirmed_unit_mult"], 1000.0)
            self.assertEqual(saved[0]["confirmed_unit_source"], UNIT_EVIDENCE_SOURCE)
            self.assertEqual(saved[0]["confirmed_unit_version"], UNIT_EVIDENCE_VERSION)

            # 空单位物料不得借客户端 unit/伪造 confirmed_* 洗白出权威证据
            row_e = _row(
                original_name="无单位衬套", original_spec="DN100",
                original_qty=10, original_unit="km", professional="暖通",
            )
            forged_empty = [{
                "code": "R04-EMPTY-U",
                "qty": 10.0,
                "qty_ratio": 1.0,
                "unit": "km",
                "confirmed_unit": "km",
                "confirmed_unit_mult": 1.0,
                "confirmed_unit_version": UNIT_EVIDENCE_VERSION,
            }]
            record_decision(
                user_id=1, scope_key="r04_e.xlsx|暖通", row=row_e, bindings=forged_empty,
                decision=STATUS_MANUAL_CONFIRMED, source=SOURCE_MANUAL, reason="test",
            )
            rec_e = get_decision(user_id=1, scope_key="r04_e.xlsx|暖通", row=row_e, bindings=forged_empty)
            saved_e = extract_saved_bindings(rec_e)
            self.assertNotIn("confirmed_unit", saved_e[0])
            self.assertNotIn("confirmed_unit_version", saved_e[0])
            res_e = validate_bindings_against_row(
                row_e, forged_empty, saved_bindings=saved_e, require_saved_unit_evidence=True
            )
            self.assertFalse(res_e["ok"], res_e)
        finally:
            clear_binding_decisions()
            mconn = get_db_connection()
            try:
                mconn.execute("DELETE FROM materials WHERE code IN ('R04-CONC-M', 'R04-EMPTY-U')")
                mconn.commit()
            finally:
                mconn.close()
