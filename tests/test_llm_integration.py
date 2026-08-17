# -*- coding: utf-8 -*-
import os
import sys
import unittest
import json
import sqlite3
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm_client import llm_client, QwenLLMClient

class TestLLMIntegration(unittest.TestCase):

    def setUp(self):
        # Reset llm_client status for test consistency
        llm_client.consecutive_failures = 0
        llm_client.circuit_broken_until = 0.0
        # Clear stats
        for k in llm_client.stats:
            if isinstance(llm_client.stats[k], float):
                llm_client.stats[k] = 0.0
            else:
                llm_client.stats[k] = 0
                
        # Clear database cache table
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "materials.db")
        db_conn = sqlite3.connect(db_path)
        cursor = db_conn.cursor()
        cursor.execute("DELETE FROM llm_cache")
        db_conn.commit()
        db_conn.close()


    def test_config_loading(self):
        self.assertIsNotNone(llm_client.config)
        self.assertIn("model_matching", llm_client.config)
        self.assertIn("l2_score_min", llm_client.config)

    def test_cache_mechanism(self):
        key = "test_cache_key_123"
        val = {"chosen_code": "code1", "reason": "test"}
        
        # Initially not in cache
        self.assertIsNone(llm_client.get_cache(key))
        
        # Set cache
        llm_client.set_cache(key, val)
        
        # Read cache back
        cached = llm_client.get_cache(key)
        self.assertEqual(cached, val)

    def test_circuit_breaker(self):
        self.assertFalse(llm_client.check_circuit_breaker())
        
        # Force failures up to limit
        max_failures = llm_client.config.get("max_consecutive_failures", 5)
        for _ in range(max_failures - 1):
            llm_client.record_failure()
            self.assertFalse(llm_client.check_circuit_breaker())
            
        # Tripping point
        llm_client.record_failure()
        self.assertTrue(llm_client.check_circuit_breaker())
        
        # Test recovery via record_success
        llm_client.record_success()
        self.assertFalse(llm_client.check_circuit_breaker())
        self.assertEqual(llm_client.consecutive_failures, 0)

    @patch("openai.resources.chat.completions.Completions.create")
    def test_mock_extract_params_by_llm(self, mock_create):
        # Setup mock return value
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=json.dumps({
            "model": "FD-100",
            "attrs": {
                "风量": "1000",
                "机外余压": "200",
                "非法字段": "不应保留"
            }
        })))]
        mock_create.return_value = mock_response

        # Mock env vars
        os.environ["LLM_ENABLED"] = "true"
        os.environ["DASHSCOPE_API_KEY"] = "sk-mock-key"

        # Parameters for extraction
        must_keys = ["风量", "机外余压"]
        should_keys = ["功率"]
        flags = ["防爆"]

        res = llm_client.extract_params_by_llm("新风机 FD-100 风量1000m3/h", "fan", must_keys, should_keys, flags)
        
        self.assertIsNotNone(res)
        self.assertEqual(res["model"], "FD-100")
        # "风量" and "机外余压" should be present, "非法字段" should be whitelisted out
        self.assertIn("风量", res["attrs"])
        self.assertIn("机外余压", res["attrs"])
        self.assertNotIn("非法字段", res["attrs"])

    @patch("openai.resources.chat.completions.Completions.create")
    def test_mock_matching_by_llm(self, mock_create):
        # Setup mock return value
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=json.dumps({
            "chosen_index": 1,
            "reason": "型号规格一致"
        })))]
        mock_create.return_value = mock_response

        # Mock env vars
        os.environ["LLM_ENABLED"] = "true"
        os.environ["DASHSCOPE_API_KEY"] = "sk-mock-key"

        query_info = {"name": "防爆轴流风机", "specification": "T35-11-4.0", "category": "通风机"}
        candidates = [
            {"code": "C1", "name": "轴流风机", "specification": "T35-11-3.0"},
            {"code": "C2", "name": "防爆轴流风机", "specification": "T35-11-4.0"},
            {"code": "C3", "name": "管道风机", "specification": "GD30-10"}
        ]

        res = llm_client.check_match_by_llm(query_info, candidates)
        self.assertIsNotNone(res)
        self.assertEqual(res["chosen_code"], "C2")
        self.assertEqual(res["chosen_index"], 1)
        self.assertEqual(res["reason"], "型号规格一致")

    def test_circuit_breaker_blocks_api(self):
        # Setup circuit broken
        llm_client.consecutive_failures = llm_client.config.get("max_consecutive_failures", 5)
        llm_client.circuit_broken_until = 9999999999.0
        
        # Enabled but circuit broken
        os.environ["LLM_ENABLED"] = "true"
        os.environ["DASHSCOPE_API_KEY"] = "sk-mock-key"
        
        res = llm_client.check_match_by_llm({}, [{"code": "C1"}])
        self.assertIsNone(res)

if __name__ == "__main__":
    unittest.main()
