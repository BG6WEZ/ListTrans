import unittest
import os
import json
from app.preprocessor import extract_params as parse_spec, extract_device_name, extract_model
from app.matcher import match_item
from app.report_generator import generate_standard_report

class TestHVACParser(unittest.TestCase):
    def test_model_extraction(self):
        self.assertEqual(extract_model("1.名称:医用洁卫型空调机组(室外型)RAU-Q-D301"), "RAU-Q-D301")
        self.assertEqual(extract_model("新风机组PAU-Q-D301(室外型)"), "PAU-Q-D301")
        self.assertEqual(extract_model("电动防火阀 2.22.54.0113"), "2.22.54.0113")
        self.assertEqual(extract_model("风机管件 DN100"), "DN100")
        
    def test_spec_attribute_parsing(self):
        spec1 = "1.名称:医用洁卫型空调机组(室外型)RAU-Q-D301\n2.规格:机组风量:8000m3/h；新风量:2400m3/h；机外余压:650Pa；过滤段:F8;制冷量:24kw;功率:5.5kw\n3.功能段..."
        parsed1 = parse_spec(spec1)
        self.assertEqual(parsed1["model"], "RAU-Q-D301")
        self.assertEqual(parsed1["attrs"]["风量"], 8000)
        self.assertEqual(parsed1["attrs"]["新风量"], 2400)
        self.assertEqual(parsed1["attrs"]["余压"], 650)
        self.assertEqual(parsed1["attrs"]["功率"], 5.5)
        self.assertEqual(parsed1["attrs"]["制冷量"], 24.0)
        self.assertEqual(parsed1["attrs"]["过滤段"], ["F8"])
        
        spec2 = "送风量m³/h：5200\n新风量m³/h：5200\n四管制，变频电机"
        parsed2 = parse_spec(spec2)
        self.assertEqual(parsed2["attrs"]["风量"], 5200)
        self.assertEqual(parsed2["attrs"]["新风量"], 5200)
        
    def test_device_name_extraction(self):
        text1 = "1.名称:医用洁卫型空调机组(室外型)RAU-Q-D301\n2.规格:..."
        self.assertEqual(extract_device_name(text1, "默认"), "医用洁卫型空调机组(室外型)")
        
class TestHVACMatcher(unittest.TestCase):
    def test_matching_logic(self):
        # We test matching with a mock item
        query = {
            "name": "医用洁卫型空调机组(室外型)",
            "specification": "1.名称:医用洁卫型空调机组(室外型)RAU-Q-D301\n2.规格:机组风量:8000m3/h；新风量:2400m3/h；机外余压:650Pa；过滤段:F8；系统形式:四管制水系统；制冷量:24kw ；制热量:16kw；风机类型及功率:无蜗壳,5.5kw；启动方式：变频",
            "category": "医用洁净型组合式空调机组"
        }
        res = match_item(query)
        self.assertIsNotNone(res["top_candidates"])
        self.assertTrue(len(res["top_candidates"]) > 0)
        # Check if the highest score is high (should find 3.60.21.1553 which has 8000/2400)
        top = res["top_candidates"][0]
        self.assertIn(top["code"], ["3.60.21.1553", "3.60.21.0231"])
        self.assertTrue(top["score"] > 60.0)

if __name__ == "__main__":
    unittest.main()
