# -*- coding: utf-8 -*-
import unittest

from app.query_preprocessor import (
    is_valid_query_item,
    clean_query_text_ir1,
    decouple_name_and_spec,
    slice_query_sections,
    parse_query_kv_blocks,
    extract_from_name_spec_fallback,
    normalize_unit_scales,
    extract_query_params
)
from app.domain_router import (
    route_domain,
    compute_sheet_hint_vector
)

class TestQueryCleaningPipeline(unittest.TestCase):

    # ==========================================
    # 1. IR-0 10 Group Filtering Rules Tests
    # ==========================================
    def test_ir0_rule1_empty_row(self):
        valid, reason = is_valid_query_item("", "")
        self.assertFalse(valid)
        self.assertEqual(reason, "R1_EMPTY_ROW")

    def test_ir0_rule2_total_row(self):
        valid, reason = is_valid_query_item("小计", "")
        self.assertFalse(valid)
        self.assertEqual(reason, "R2_TOTAL_ROW")

        valid2, reason2 = is_valid_query_item("合计：", "100.00")
        self.assertFalse(valid2)
        self.assertEqual(reason2, "R2_TOTAL_ROW")

    def test_ir0_rule3_repeat_header_row(self):
        valid, reason = is_valid_query_item("项目名称", "规格型号")
        self.assertFalse(valid)
        self.assertEqual(reason, "R3_REPEAT_HEADER_ROW")

    def test_ir0_rule4_preface_note_row(self):
        valid, reason = is_valid_query_item("按设计说明施工", "")
        self.assertFalse(valid)
        self.assertEqual(reason, "R4_PREFACE_NOTE_ROW")

    def test_ir0_rule5_outline_title_row(self):
        valid, reason = is_valid_query_item("一、暖通通风工程", "", unit="")
        self.assertFalse(valid)
        self.assertEqual(reason, "R5_OUTLINE_TITLE_ROW")

    def test_ir0_rule6_long_text_note_row(self):
        long_note = "说明：" + "本工程要求所有施工安装按照国家工程验收标准规范执行。" * 5
        valid, reason = is_valid_query_item(long_note, "", unit="")
        self.assertFalse(valid)
        self.assertEqual(reason, "R6_LONG_TEXT_NOTE_ROW")

    def test_ir0_rule7_footer_note_row(self):
        valid, reason = is_valid_query_item("编制人:张三", "")
        self.assertFalse(valid)
        self.assertEqual(reason, "R7_FOOTER_NOTE_ROW")

    def test_ir0_rule8_formula_calc_row(self):
        valid, reason = is_valid_query_item("", "10+20*1.5=40")
        self.assertFalse(valid)
        self.assertEqual(reason, "R8_FORMULA_CALC_ROW")

    def test_ir0_rule9_merged_cell_orphan_row(self):
        valid, reason = is_valid_query_item("", "12", unit="", code="")
        self.assertFalse(valid)
        self.assertEqual(reason, "R9_MERGED_CELL_ORPHAN_ROW")

    def test_ir0_rule10_divider_row(self):
        valid, reason = is_valid_query_item("========", "")
        self.assertFalse(valid)
        self.assertEqual(reason, "R10_DIVIDER_ROW")

    def test_ir0_valid_item(self):
        valid, reason = is_valid_query_item("离心风机", "风量5000m3/h", unit="台")
        self.assertTrue(valid)
        self.assertEqual(reason, "VALID_ITEM")

    # ==========================================
    # 2. IR-1 Text Normalization & Protection Tests
    # ==========================================
    def test_ir1_prefix_stripping_whitelist(self):
        # Whitelisted numbers (followed by mm², ℃, A, kW, DN, etc.) must NOT be stripped
        t1 = clean_query_text_ir1("2.5mm² 铜芯电线")
        self.assertIn("2.5mm²", t1)

        t2 = clean_query_text_ir1("70℃常开防火阀")
        self.assertIn("70℃", t2)

        # Pure index prefix (followed by Chinese) SHOULD be stripped
        t3 = clean_query_text_ir1("1.1 配电箱安装")
        self.assertEqual(t3, "配电箱安装")

    def test_ir1_name_spec_decoupling(self):
        n1, s1 = decouple_name_and_spec("无缝钢管 DN100", "1.材质：304")
        self.assertEqual(n1, "无缝钢管")
        self.assertIn("DN100", s1)

        n2, s2 = decouple_name_and_spec("防滑地砖 600*600", "")
        self.assertEqual(n2, "防滑地砖")
        self.assertIn("600*600", s2)

    def test_ir1_noise_key_masking(self):
        raw = "1.名称：卫生级球阀\n2.规格：DN50\n3.工作内容：管道切割、组对安装\n4.重新重复名称：卫生级球阀\n5.备注：暂估价"
        mat_block, noise_block = slice_query_sections(raw)
        self.assertNotIn("工作内容", mat_block)
        self.assertNotIn("重新重复名称", mat_block)
        self.assertNotIn("备注", mat_block)
        self.assertIn("DN50", mat_block)
        self.assertIn("工作内容", noise_block)

    # ==========================================
    # 3. KV Parser v1.2 Tests
    # ==========================================
    def test_kv_parser_v1_2_paren_units(self):
        mat_block = "1.风量(m³/h): 5000\n2.机外余压(Pa): 250\n3.功率(kW): 5.5"
        kv = parse_query_kv_blocks(mat_block)
        self.assertEqual(kv.get("风量"), {"value": "5000", "unit": "m³/h"})
        self.assertEqual(kv.get("机外余压"), {"value": "250", "unit": "Pa"})
        self.assertEqual(kv.get("功率"), {"value": "5.5", "unit": "kW"})

    # ==========================================
    # 4. NameSpecExtractor Back-filling Tests
    # ==========================================
    def test_name_spec_extractor_empty_spec(self):
        res = extract_query_params("同透PVC卷材 2.0mm厚", "", "装饰")
        attrs = res["attrs"]
        self.assertEqual(attrs.get("材质"), "PVC")
        self.assertEqual(attrs.get("厚度"), 2.0)

    # ==========================================
    # 5. 30 Domain Router & Vector Fusion Tests
    # ==========================================
    def test_domain_router_30_matrix(self):
        self.assertEqual(route_domain("暖通", "离心风机", "风量3000m3/h"), "fan")
        self.assertEqual(route_domain("暖通", "常开防火阀", "70℃"), "damper")
        self.assertEqual(route_domain("电气", "电力电缆", "YJV22-4*25+1*16"), "cable_power")
        self.assertEqual(route_domain("电气", "控制电缆", "KVV-4*1.5"), "cable_control")
        self.assertEqual(route_domain("电气", "单芯电线", "BV-2.5"), "wire")
        self.assertEqual(route_domain("暖通", "微穿孔消声器", "600*300"), "silencer")
        self.assertEqual(route_domain("净化", "高效过滤器", "H13"), "filter_ffu")
        self.assertEqual(route_domain("暖通", "分水器", "DN100"), "manifold")
        self.assertEqual(route_domain("暖通", "静压箱", "1000*300*400"), "plenum")
        self.assertEqual(route_domain("暖通", "螺杆式冷水机组", "制冷量500kW"), "chiller")
        self.assertEqual(route_domain("暖通", "散流器风口", "300*300"), "air_terminal")
        self.assertEqual(route_domain("装饰", "气密门", "1200*2100"), "door")
        self.assertEqual(route_domain("暖通", "组合式空调机组", "AHU-01"), "ahu")
        self.assertEqual(route_domain("医气", "医用气体终端带", "氧气"), "medical_gas")
        self.assertEqual(route_domain("给排水", "304不锈钢管", "DN50"), "pipe_ss")
        self.assertEqual(route_domain("给排水", "PPR水管", "De32"), "pipe_plastic")
        self.assertEqual(route_domain("给排水", "法兰闸阀", "DN100 PN1.6"), "valve_water")
        self.assertEqual(route_domain("电气", "照明配电箱", "ALE-01"), "switchgear")
        self.assertEqual(route_domain("净化", "洁净彩钢板", "50mm"), "purification")
        self.assertEqual(route_domain("电气", "LED面板灯", "30W"), "lighting")
        self.assertEqual(route_domain("电气", "槽式桥架", "200*100"), "cable_tray")

    def test_sheet_hint_vector(self):
        vec = compute_sheet_hint_vector("附件三-净化系统项目-装饰水电标段")
        self.assertGreater(vec.get("hvac", 0), 0)
        self.assertGreater(vec.get("elec", 0), 0)
        self.assertGreater(vec.get("deco", 0), 0)

if __name__ == "__main__":
    unittest.main()
