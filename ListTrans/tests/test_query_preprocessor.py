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
        self.assertEqual(res1["attrs"].get("机组类型"), "全新风空气处理机组")

        res2 = extract_query_params("风机盘管", "1.名称：暗装卧式风机盘管 FCU-02", "暖通")
        self.assertEqual(res2["attrs"].get("机组类型"), "风机盘管机组")

        res3 = extract_query_params("组合式空气处理机组", "1.名称：组合式空气处理机组 AHU-03", "暖通")
        self.assertEqual(res3["attrs"].get("机组类型"), "组合式空气处理机组")

    def test_hvac_original_manifest_extraction(self):
        raw_spec = (
            "1.名称：医用洁卫型空调机组PAU-S1201\n"
            "2.规格：机组风量5200m3/h；机外余压400Pa；机组过滤器G4+F7；\n"
            "系统形式：两管制水系统；制冷量60kw ；制热量45kw ；\n"
            "风机类型：后倾型；参考功率3kw；启动方式：普通；\n"
            "3.安装形式：落地安装\n"
            "4.工作内容：水平搬运、就位、连接、上螺栓、找正、找平、固定、外表污物清理、调试\n"
            "5.含橡胶减震垫\n"
            "6.含水平搬运，不含垂直运输"
        )
        res = extract_query_params("医用洁卫型空调机组PAU-S1201", raw_spec, "暖通")
        attrs = res["attrs"]
        self.assertEqual(res["domain"], "ahu")
        self.assertEqual(attrs.get("机组类型"), "新风预处理机组")
        self.assertEqual(attrs.get("风量"), 5200.0)
        self.assertEqual(attrs.get("机外余压"), 400.0)
        self.assertEqual(attrs.get("过滤段"), ["G4", "F7"])
        self.assertEqual(attrs.get("过滤等级"), "初效")
        self.assertEqual(attrs.get("系统形式"), "两管制水系统")
        self.assertEqual(attrs.get("制冷量"), 60.0)
        self.assertEqual(attrs.get("制热量"), 45.0)

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

    def test_medical_gas_extraction(self):
        # Example 1: Medical gas pressure reducing box
        raw_spec1 = (
            "1.名称：医用压缩空气二级减压箱\n"
            "2.规格：≤20m3/h\n"
            "3.工作内容：支架制作安装、本体安装、与管道连接、气体检查"
        )
        res1 = extract_query_params("医用压缩空气二级减压箱", raw_spec1, "医用气体设备")
        attrs1 = res1["attrs"]
        self.assertEqual(res1["domain"], "medical_gas")
        self.assertEqual(attrs1.get("介质"), "空压")  # mapped from 压缩空气
        self.assertEqual(attrs1.get("流量"), 20.0)

        # Example 2: Copper piping
        raw_spec2 = (
            "1.材质：医用脱脂无缝紫铜管\n"
            "2.规格：DN6（φ8*1.0）\n"
            "3.焊接方法：硬钎焊焊接\n"
            "4.工作内容：调直、切管、焊接..."
        )
        res2 = extract_query_params("医用脱脂无缝紫铜管", raw_spec2, "脱脂无缝紫铜管")
        attrs2 = res2["attrs"]
        self.assertEqual(res2["domain"], "pipe_copper")
        self.assertEqual(attrs2.get("材质"), "脱脂紫铜")  # mapped to standard options
        self.assertEqual(attrs2.get("外径"), 8.0)
        self.assertEqual(attrs2.get("壁厚"), 1.0)

    def test_conduit_extraction(self):
        # Example 1: PVC16
        raw_spec1 = (
            "1.名称：配管\n"
            "2.材质：塑料管\n"
            "3.规格：PVC16\n"
            "4.工作内容：测位、划线、接管、配管、固定、穿引线"
        )
        res1 = extract_query_params("配管", raw_spec1, "塑料管")
        attrs1 = res1["attrs"]
        self.assertEqual(res1["domain"], "conduit")
        self.assertEqual(attrs1.get("管代号"), "PC")
        self.assertEqual(attrs1.get("外径mm"), 16.0)

        # Example 2: JDG20
        raw_spec2 = (
            "1.名称：配管\n"
            "2.材质：套接紧定式镀锌钢导管JDG\n"
            "3.规格：JDG20\n"
            "4.工作内容：测位、划线、打眼、埋螺栓、锯管、配管、接地、穿引线"
        )
        res2 = extract_query_params("配管", raw_spec2, "套接紧定式镀锌钢导管JDG")
        attrs2 = res2["attrs"]
        self.assertEqual(res2["domain"], "conduit")
        self.assertEqual(attrs2.get("管代号"), "JDG")
        self.assertEqual(attrs2.get("外径mm"), 20.0)

        # Example 3: SC20
        raw_spec3 = (
            "1.名称：配管\n"
            "2.材质：焊接钢管\n"
            "3.规格：SC20\n"
            "4.工作内容：测位、划线、接管、配管、固定、穿引线"
        )
        res3 = extract_query_params("配管", raw_spec3, "焊接钢管")
        attrs3 = res3["attrs"]
        self.assertEqual(res3["domain"], "conduit")
        self.assertEqual(attrs3.get("管代号"), "SC")
        self.assertEqual(attrs3.get("外径mm"), 26.9)

    def test_cable_and_wire_extraction(self):
        # Example 1: WDZB-BYJR-10mm²
        raw_spec1 = (
            "1.名称：配线\n"
            "2.配线形式：管内穿线\n"
            "3.规格：WDZB-BYJR-10mm²\n"
            "4.材质：铜芯线\n"
            "5.工作内容：穿引线、扫管、穿线、编号、焊接包头、线路检测、调试"
        )
        res1 = extract_query_params("配线", raw_spec1, "铜芯线")
        attrs1 = res1["attrs"]
        self.assertEqual(res1["domain"], "wire")
        self.assertEqual(attrs1.get("型号"), "WDZB-BYJR")
        self.assertEqual(attrs1.get("型号族"), "BYJR")
        self.assertEqual(attrs1.get("主截面"), 10.0)

        # Example 2: WDZB-YJY-4*70+1*35mm²
        raw_spec2 = (
            "1.名称：电力电缆\n"
            "2.规格、型号：WDZB-YJY-4*70+1*35mm²\n"
            "3.材质：铜芯电缆\n"
            "4.敷设方式、部位：桥架敷设/穿管敷设\n"
            "5.工作内容：电缆敷设、线路检测、调试"
        )
        res2 = extract_query_params("电力电缆", raw_spec2, "铜芯电缆")
        attrs2 = res2["attrs"]
        self.assertEqual(res2["domain"], "cable_power")
        self.assertEqual(attrs2.get("型号"), "WDZB-YJY")
        self.assertEqual(attrs2.get("型号族"), "YJY")
        self.assertEqual(attrs2.get("主芯数"), 4)
        self.assertEqual(attrs2.get("主截面"), 70.0)
        self.assertEqual(attrs2.get("零芯数"), 1)
        self.assertEqual(attrs2.get("零截面"), 35.0)

    def test_hvac_copper_silencer_plenum(self):
        # 1. 磷酸脱氧无缝紫铜管 φ22.23
        spec1 = (
            "1.材质：磷酸脱氧无缝紫铜管\n"
            "2.规格：φ22.23\n"
            "3.焊接方法：冷媒管钎焊\n"
            "4.工作内容：调直、切管、煨弯、焊接、管道安装、气密性试验、吹扫、加氟试压\n"
            "5.管道内的制冷剂为甲供"
        )
        res1 = extract_query_params("无缝紫铜管", spec1, "磷酸脱氧无缝紫铜管")
        self.assertEqual(res1["domain"], "pipe_copper")
        self.assertEqual(res1["attrs"].get("外径"), 22.23)
        self.assertEqual(res1["attrs"].get("材质"), "紫铜")

        # 2. 消声器 800*500*2000
        spec2 = (
            "1.名称:超静音卫生型微穿孔板(双层)消声器\n"
            "2.规格:800*500*2000\n"
            "3.材质：镀锌钢板\n"
            "4.工作内容：支架制作安装、消声器安装、粘贴保温\n"
            "5.此为成品安装价格"
        )
        res2 = extract_query_params("超静音卫生型微穿孔板(双层)消声器", spec2, "消声器")
        self.assertEqual(res2["domain"], "silencer")
        self.assertEqual(res2["attrs"].get("尺寸"), "800*500*2000")
        self.assertEqual(res2["attrs"].get("材质"), "镀锌钢板")

        # 3. 集风箱 1125*500*300
        spec3 = (
            "1.名称:集风箱\n"
            "2.规格:1125*500*300\n"
            "3.工作内容：支吊架制作安装、吊装、组对、制垫、加垫、找平、找正、紧固固定\n"
            "4.此为成品安装价格，不含制作箱体的费用"
        )
        res3 = extract_query_params("集风箱", spec3, "集风箱")
        self.assertEqual(res3["domain"], "plenum")
        self.assertEqual(res3["attrs"].get("尺寸"), "1125*500*300")

    def test_wire_bvr_25(self):
        spec = (
            "1.名称：电线\n"
            "2.配线形式：管内穿线\n"
            "3.规格：BVR-25mm²\n"
            "4.材质：铜芯\n"
            "5.工作内容：穿引线、扫管、穿线、编号、焊接包头、线路检测、调试"
        )
        res = extract_query_params("电线", spec, "铜芯")
        self.assertEqual(res["domain"], "wire")
        self.assertEqual(res["attrs"].get("型号"), "BVR")
        self.assertEqual(res["attrs"].get("型号族"), "BVR")
        self.assertEqual(res["attrs"].get("主截面"), 25.0)

if __name__ == "__main__":
    unittest.main()
