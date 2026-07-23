# -*- coding: utf-8 -*-
import unittest
import os
import sys
import numpy as np

# Ensure project root is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.preprocessor import clean_text, extract_params, fts_tokenize, extract_model
from app.db_manager import init_db, search_materials_keyword, query_by_code, DB_PATH
from app.vector_index import index_manager
from app.matcher import match_item, score_candidate

class TestPreprocessor(unittest.TestCase):
    def test_clean_text(self):
        # 1.1 Text cleaning and symbol washing
        raw_text = "１.名称：医用新风柜（室外型）　"
        cleaned = clean_text(raw_text)
        self.assertIn("新风机组", cleaned) # Should replace '新风柜' -> '新风机组'
        self.assertNotIn("　", cleaned)     # Should remove full-width spaces
        
    def test_unit_normalization(self):
        # 1.2 Unit normalization CMH/kw -> m3/h / kW
        text1 = "风量:8000cmh, 功率:5.5kw, DN8O"
        cleaned1 = clean_text(text1)
        self.assertIn("8000m³/h", cleaned1)
        self.assertIn("5.5kW", cleaned1)
        self.assertIn("DN80", cleaned1)
        
    def test_fts_tokenize(self):
        # 1.4 Chinese char-spacing FTS5 token generator
        text = "防排烟阀RAU-Q-D301"
        tokens = fts_tokenize(text)
        self.assertEqual(tokens, "防 排 烟 阀 RAU-Q-D301")
        
    def test_extract_params(self):
        # 1.3 Range parameters parsing and multi-professional extraction
        # HVAC
        text_hvac = "1.名称:风机\n2.规格:风量3000~5000m3/h, 功率:5.5kw, 尺寸: 400×200"
        parsed = extract_params(text_hvac, "暖通")
        self.assertEqual(parsed["attrs"]["风量"], 4000.0) # Mean value of 3000 and 5000
        self.assertEqual(parsed["attrs"]["功率"], 5.5)
        self.assertEqual(parsed["attrs"]["尺寸"], "400*200")
        
        # Water
        text_water = "给水管道 DN100 压力1.6MPa"
        parsed_w = extract_params(text_water, "不锈钢管")
        self.assertEqual(parsed_w["attrs"]["管径"], 100)
        self.assertEqual(parsed_w["attrs"]["压力"], 1.6)
        
        # Electrical Cables
        text_cable = "电力电缆 WDZ-YJY-5*2.5mm2 铜芯"
        parsed_c = extract_params(text_cable, "电气")
        self.assertEqual(parsed_c["attrs"]["芯数"], "5")
        self.assertEqual(parsed_c["attrs"]["截面积"], "2.5")

    def test_multi_word_search(self):
        # Verify that multi-word split search runs without errors and produces list results
        res = search_materials_keyword("电缆; YJY", limit=5)
        self.assertIsInstance(res, list)

class TestVectorIndex(unittest.TestCase):
    def test_numpy_fallback(self):
        # 2.4 Numpy cosine similarity search check
        # Mock some embeddings
        mock_embeddings = np.random.rand(5, 384).astype(np.float32)
        # Normalize
        for row in mock_embeddings:
            row /= np.linalg.norm(row)
        query = np.random.rand(384).astype(np.float32)
        query /= np.linalg.norm(query)
        
        # Point product in numpy
        scores = np.dot(mock_embeddings, query)
        self.assertEqual(len(scores), 5)
        self.assertTrue(all(0.0 <= s <= 1.0 for s in scores))

class TestMatcher(unittest.TestCase):
    def test_dynamic_scoring(self):
        # Mock query
        query_info = {
            "name": "医用洁卫型空调机组",
            "specification": "机组风量:8000m3/h；新风量:2400m3/h；机外余压:650Pa；功率:5.5kw",
            "category": "医用洁净型组合式空调机组"
        }
        
        # Candidate 1: Exact attributes
        candidate1 = {
            "code": "TEST_C1",
            "name": "医用洁卫型空调机组",
            "specification": "风量:8000m3/h, 新风量:2400m3/h, 余压:650Pa, 功率:5.5kw",
            "category": "医用洁净型组合式空调机组",
            "description": ""
        }
        
        # Candidate 2: Slightly deviated
        candidate2 = {
            "code": "TEST_C2",
            "name": "暖通机组",
            "specification": "风量:7000m3/h, 新风量:2400m3/h, 余压:650Pa, 功率:5.5kw",
            "category": "其他空调设备",
            "description": ""
        }
        
        score1, reasons1 = score_candidate(query_info, candidate1, semantic_score=0.9)
        score2, reasons2 = score_candidate(query_info, candidate2, semantic_score=0.4)
        
        self.assertTrue(score1 > score2)
        self.assertIn("核心参数在公差范围内", reasons1)

    def test_cable_scoring_conflict(self):
        # Query with 5*2.5 cable
        query_info = {
            "name": "电力电缆",
            "specification": "电力电缆 WDZ-YJY-5*2.5mm2",
            "category": "电气"
        }
        # Candidate 1: Exact matches cores/area
        candidate1 = {
            "code": "TEST_CAB1",
            "name": "铜芯电力电缆",
            "specification": "WDZ-YJY 5*2.5",
            "category": "电力电缆"
        }
        # Candidate 2: Different area (mismatch)
        candidate2 = {
            "code": "TEST_CAB2",
            "name": "铜芯电力电缆",
            "specification": "WDZ-YJY 5*4",
            "category": "电力电缆"
        }
        
        score1, reasons1 = score_candidate(query_info, candidate1, semantic_score=0.8)
        score2, reasons2 = score_candidate(query_info, candidate2, semantic_score=0.8)
        
        self.assertTrue(score1 > score2)
        self.assertIn("核心参数在公差范围内", reasons1)
        self.assertIn("截面积冲突拦截", reasons2)

    def test_general_param_extraction(self):
        text = "1.名称: 空压机\n2.规格：排气量 ：≥1.40m3/min,排气压力：0.8MPa,单机功率：≥11KW\n3.材质：不锈钢\n4.工作内容：基础浇筑、设备吊装及调试"
        params = extract_params(text, "空压机")
        attrs = params.get("attrs", {})
        self.assertEqual(attrs.get("名称"), "空压机")
        self.assertEqual(attrs.get("排气量"), "≥1.40m3/min")
        self.assertEqual(attrs.get("排气压力"), "0.8MPa")
        self.assertEqual(attrs.get("单机功率"), "≥11KW")
        self.assertEqual(attrs.get("材质"), "不锈钢")
        self.assertNotIn("工作内容", attrs)
        # Overwritten specific power value
        self.assertEqual(attrs.get("功率"), 11.0)

        # Test boolean tags and unit suffix cleaning
        text2 = "送风量m³/h：5750；新风量m³/h：5750；六管制，变频电机"
        params2 = extract_params(text2, "风机盘管")
        attrs2 = params2.get("attrs", {})
        self.assertEqual(attrs2.get("送风量"), 5750.0)
        self.assertEqual(attrs2.get("新风量"), 5750.0)
        self.assertEqual(attrs2.get("六管制"), "六管制")
        self.assertEqual(attrs2.get("变频电机"), "变频电机")

class TestDatabaseImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.db_manager import DB_PATH, init_db
        import shutil
        cls.db_backup = DB_PATH + ".bak"
        if os.path.exists(DB_PATH):
            shutil.copyfile(DB_PATH, cls.db_backup)
        init_db()

    @classmethod
    def tearDownClass(cls):
        from app.db_manager import DB_PATH
        import shutil
        if hasattr(cls, "db_backup") and os.path.exists(cls.db_backup):
            shutil.copyfile(cls.db_backup, DB_PATH)
            os.remove(cls.db_backup)
            
            # Hot reload FAISS memory cache in running processes if index was modified
            try:
                from app.vector_index import index_manager
                index_manager.load_cache()
            except Exception:
                pass

    def test_disabled_code_filtering(self):
        import pandas as pd
        from app.db_manager import import_materials_from_excel, query_by_code
        
        # Define mock material data including disabled/inactive codes
        data = {
            "物料分类": ["暖通", "暖通", "给排水", "电气", "电气"],
            "物料编码": ["WZ-1001", "WZ-1002（禁用）", "WZ-1003", "WZ-1004(已禁用）", "WZ-1005（无效停用）"],
            "物料名称": ["风机", "风机", "给水管", "电缆", "电缆"],
            "规格/型号": ["FN-1", "FN-2", "DN50", "3*2.5", "5*4"],
            "计量单位": ["台", "台", "米", "米", "米"],
            "常用品牌": ["", "", "", "", ""],
            "描述": ["", "", "", "", ""]
        }
        df = pd.DataFrame(data)
        
        # Write to temporary Excel file with a header offset (using startrow=1 to leave the first row empty)
        temp_xlsx = os.path.join(os.path.dirname(DB_PATH), "temp_test_import.xlsx")
        df.to_excel(temp_xlsx, index=False, startrow=1)
        
        try:
            # Import using "replace" mode
            meta = import_materials_from_excel(temp_xlsx, mode="replace", filename="temp_test_import.xlsx")
            
            # Assert metadata values
            self.assertEqual(meta["total_rows"], 5)
            self.assertEqual(meta["imported_rows"], 2) # WZ-1001, WZ-1003 are valid
            self.assertEqual(meta["disabled_rows"], 3) # WZ-1002, WZ-1004, WZ-1005 are disabled
            
            # Assert query validations
            self.assertIsNotNone(query_by_code("WZ-1001"))
            self.assertIsNotNone(query_by_code("WZ-1003"))
            self.assertIsNone(query_by_code("WZ-1002（禁用）"))
            self.assertIsNone(query_by_code("WZ-1004(已禁用）"))
            self.assertIsNone(query_by_code("WZ-1005（无效停用）"))
            
        finally:
            if os.path.exists(temp_xlsx):
                os.remove(temp_xlsx)

    def test_schema_stats_and_partial_reparse(self):
        from app.db_manager import get_db_schema_stats, reparse_materials_db_partial
        # Load stats
        stats = get_db_schema_stats()
        self.assertIn("total_rows", stats)
        self.assertIn("overall_structuring_rate", stats)
        self.assertIn("domains", stats)
        
        # Test partial reparse (with non-existent code to verify it executes clean query return)
        res = reparse_materials_db_partial(codes=["NON_EXISTENT_CODE_123"])
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["reparsed_rows"], 0)

if __name__ == "__main__":
    unittest.main()
