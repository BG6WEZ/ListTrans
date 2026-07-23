# -*- coding: utf-8 -*-
import unittest

from app.query_preprocessor import (
    slice_query_sections,
    parse_query_kv_blocks,
    extract_query_cable_params,
    canonicalize_query_attrs,
    extract_query_params
)

class TestQueryPreprocessor(unittest.TestCase):

    def test_slice_query_sections(self):
        raw = "1.名称：卫生级球阀\n2.规格：DN15\n3.焊接方法：螺纹连接\n4.304不锈钢材质\n5.工作内容：切管、套丝、阀门连接、水压试验"
        mat_block, noise_block = slice_query_sections(raw)
        self.assertIn("304", mat_block)
        self.assertIn("工作内容", noise_block)
        self.assertNotIn("工作内容", mat_block)

    def test_parse_query_kv_blocks(self):
        mat_block = "1.名称：卫生级球阀\n2.规格：DN15\n3.材质：304"
        kv = parse_query_kv_blocks(mat_block)
        self.assertEqual(kv.get("名称"), "卫生级球阀")
        self.assertEqual(kv.get("规格"), "DN15")
        self.assertEqual(kv.get("材质"), "304")

    def test_extract_query_cable_params_terminal(self):
        text = "1.名称：电力电缆终端头\n2.规格、型号：截面10mm2\n3.材质、类型：铜芯电缆、干包式"
        attrs = extract_query_cable_params(text, {})
        self.assertEqual(attrs.get("截面积"), "10.0")
        self.assertEqual(attrs.get("芯数"), "1")

    def test_canonicalize_query_attrs(self):
        attrs = {"材质": "304", "截面积": "25+16"}
        canon = canonicalize_query_attrs("pipe_ss", attrs, "S30408薄壁不锈钢管")
        self.assertEqual(canon.get("材质"), "不锈钢304")
        self.assertEqual(canon.get("截面积"), "25.0+16.0")

    def test_hvac_unit_classification(self):
        res1 = extract_query_params("全新风处理机组", "1.名称：全新风处理机组 MAU-01\n2.风量：5000m3/h", "暖通")
        self.assertEqual(res1["attrs"].get("机组类型"), "MAU")

        res2 = extract_query_params("风机盘管", "1.名称：暗装卧式风机盘管 FCU-02", "暖通")
        self.assertEqual(res2["attrs"].get("机组类型"), "FCU")

        res3 = extract_query_params("组合式空气处理机组", "1.名称：组合式空气处理机组 AHU-03", "暖通")
        self.assertEqual(res3["attrs"].get("机组类型"), "AHU")

    def test_cleanroom_ductwork_extraction(self):
        raw_spec = (
            "1.名称：洁净镀锌通风管安装费（角钢法兰连接）\n"
            "2.材质：优质镀锌钢板\n"
            "3.形状：矩形\n"
            "4.板材厚度：0.6mm"
        )
        res = extract_query_params("洁净镀锌通风管", raw_spec, "暖通")
        attrs = res["attrs"]
        self.assertEqual(attrs.get("材质"), "镀锌钢板")
        self.assertEqual(attrs.get("板材厚度"), 0.6)
        self.assertEqual(attrs.get("形状"), "矩形")

    def test_plenum_collector_box_extraction(self):
        raw_spec = (
            "1.名称:集风箱\n"
            "2.规格:1000*3150*300\n"
            "3.工作内容：支吊架制作安装、吊装、组对、制垫、加垫、找平、找正、紧固固定\n"
            "4.此为成品安装价格，不含制作箱体的费用"
        )
        res = extract_query_params("集风箱", raw_spec, "暖通")
        attrs = res["attrs"]
        self.assertEqual(res["domain"], "plenum")
        self.assertEqual(attrs.get("名称"), "集风箱")
        self.assertEqual(attrs.get("尺寸"), "1000*3150*300")
        self.assertEqual(attrs.get("长"), 1000.0)
        self.assertEqual(attrs.get("宽"), 3150.0)
        self.assertEqual(attrs.get("高"), 300.0)

if __name__ == "__main__":
    unittest.main()
