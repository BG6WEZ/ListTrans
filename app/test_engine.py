import unittest
import os
import json
from app.db_manager import init_db

# Ensure SQLite DB is initialized with parsed_params column and data
proj_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
xlsx_path = os.path.join(proj_dir, "doc", "sample", "基础数据", "物料明细.xlsx")
init_db(xlsx_path)

# If the database is empty, insert mock items to ensure tests pass
from app.db_manager import get_db_connection
conn = get_db_connection()
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM materials")
row_count = cursor.fetchone()[0]
if row_count == 0:
    import json
    from app.preprocessor import extract_params, fts_tokenize
    
    mock_materials = [
        ("3.60.21.1553", "医用洁净型组合式空调机组", "医用洁卫型空调机组(室外型)", "机组风量:8000m3/h；新风量:2400m3/h；机外余压:650Pa；功率:5.5kw", "台", "", ""),
        ("3.60.21.0231", "医用洁净型组合式空调机组", "医用洁卫型空调机组(室外型)", "机组风量:8000m3/h；新风量:2400m3/h；机外余压:650Pa；功率:5.5kw", "台", "", ""),
    ]
    
    for code, category, name, spec, unit, brand, desc in mock_materials:
        tokens = f"{name} {spec} {category}".lower()
        full_spec = f"{spec} {desc}".strip()
        parsed = extract_params(full_spec, category)
        parsed_json = json.dumps(parsed, ensure_ascii=False)
        
        cursor.execute("""
            INSERT OR REPLACE INTO materials 
            (code, category, name, specification, unit, brand, description, search_tokens, parsed_params)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (code, category, name, spec, unit, brand, desc, tokens, parsed_json))
        
        # Check FTS support
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='materials_fts'")
        has_fts = cursor.fetchone() is not None
        if has_fts:
            cursor.execute("""
                INSERT OR REPLACE INTO materials_fts 
                (code, category, name, specification, brand, description)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (code, fts_tokenize(category), fts_tokenize(name), fts_tokenize(spec), fts_tokenize(brand), fts_tokenize(desc)))
            
    conn.commit()
    print("Test database populated with mock items.")
conn.close()

from app.preprocessor import extract_params as parse_spec, extract_device_name, extract_model
from app.matcher import match_item
from app.report_generator import generate_standard_report

class TestHVACParser(unittest.TestCase):
    def test_model_extraction(self):
        self.assertEqual(extract_model("1.名称:医用洁卫型空调机组(室外型)RAU-Q-D301"), "RAU-Q-D301")
        self.assertEqual(extract_model("新风机组PAU-Q-D301(室外型)"), "PAU-Q-D301")
        self.assertEqual(extract_model("电动防火阀 2.22.54.0113"), "2.22.54.0113")
        self.assertEqual(extract_model("风机管件 DN100"), "")
        
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
        
        spec3 = "1.名称:复合管\n2.规格:3.材质、规格：PSP钢塑复合管，DN65（75*5.5）"
        parsed3 = parse_spec(spec3, "给排水")
        self.assertEqual(parsed3["attrs"]["材质"], "PSP钢塑复合管")
        self.assertEqual(parsed3["attrs"]["管径"], 65)
        self.assertEqual(parsed3["attrs"]["外径"], 75.0)
        self.assertEqual(parsed3["attrs"]["壁厚"], 5.5)
        
    def test_device_name_extraction(self):
        text1 = "1.名称:医用洁卫型空调机组(室外型)RAU-Q-D301\n2.规格:..."
        self.assertEqual(extract_device_name(text1, "默认"), "医用洁卫型空调机组(室外型)")
        
class TestHVACMatcher(unittest.TestCase):
    def test_matching_logic(self):
        # Force insert mock items to ensure they exist with parameters for this test, resolving test pollution
        from app.db_manager import get_db_connection
        from app.preprocessor import extract_params
        conn = get_db_connection()
        cursor = conn.cursor()
        mock_materials = [
            ("3.60.21.1553", "医用洁净型组合式空调机组", "医用洁卫型空调机组(室外型)", "机组风量:8000m3/h；新风量:2400m3/h；机外余压:650Pa；功率:5.5kw", "台", "", ""),
            ("3.60.21.0231", "医用洁净型组合式空调机组", "医用洁卫型空调机组(室外型)", "机组风量:8000m3/h；新风量:2400m3/h；机外余压:650Pa；功率:5.5kw", "台", "", ""),
        ]
        for code, category, name, spec, unit, brand, desc in mock_materials:
            tokens = f"{name} {spec} {category}".lower()
            full_spec = f"{spec} {desc}".strip()
            parsed = extract_params(full_spec, category)
            parsed_json = json.dumps(parsed, ensure_ascii=False)
            cursor.execute("""
                INSERT OR REPLACE INTO materials 
                (code, category, name, specification, unit, brand, description, search_tokens, parsed_params)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (code, category, name, spec, unit, brand, desc, tokens, parsed_json))
        conn.commit()
        conn.close()

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
        
        # Test 1: Brand Conflict (should get score 0.0 and "品牌冲突阻断")
        brand_query = {
            "name": "交换机",
            "specification": "华为 S5735",
            "category": "网络交换机",
            "brand": "华为",
            "professional": "弱电"
        }
        brand_cand = {
            "name": "交换机",
            "specification": "华三 S5735",
            "category": "网络交换机",
            "brand": "华三",
            "description": ""
        }
        from app.matcher import score_candidate
        score, reasons = score_candidate(brand_query, brand_cand)
        self.assertEqual(score, 0.0)
        self.assertIn("品牌冲突阻断", reasons)

        # Test 2: Unit Incompatibility (should get score 0.0 and "单位不可换算阻断")
        unit_query = {
            "name": "给水管",
            "specification": "PSP DN65",
            "category": "给水管道",
            "unit": "米",
            "professional": "给排水"
        }
        unit_cand = {
            "name": "给水管",
            "specification": "PSP DN65",
            "category": "给水管道",
            "unit": "台",
            "description": ""
        }
        score, reasons = score_candidate(unit_query, unit_cand)
        self.assertEqual(score, 0.0)
        self.assertIn("单位不可换算阻断", reasons)

        # Test 3: Professional Mismatch (should get score 0.0 and "专业冲突阻断")
        prof_query = {
            "name": "复合管",
            "specification": "PSP DN65",
            "category": "给水管道",
            "professional": "给排水"
        }
        prof_cand = {
            "name": "配电箱",
            "specification": "XM-15kW",
            "category": "动力配电箱",
            "description": ""
        }
        score, reasons = score_candidate(prof_query, prof_cand)
        self.assertEqual(score, 0.0)
        self.assertIn("专业冲突阻断", reasons)

if __name__ == "__main__":
    unittest.main()
