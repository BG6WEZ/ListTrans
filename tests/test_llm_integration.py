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

    _ENV_KEYS = ("LLM_ENABLED", "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY")

    @staticmethod
    def _snapshot_env():
        return {k: os.environ.get(k) for k in TestLLMIntegration._ENV_KEYS}

    @staticmethod
    def _restore_env(saved):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _reset_llm_client_state():
        llm_client.consecutive_failures = 0
        llm_client.circuit_broken_until = 0.0
        for k in llm_client.stats:
            llm_client.stats[k] = 0.0 if isinstance(llm_client.stats[k], float) else 0

    def setUp(self):
        self._saved_env = self._snapshot_env()
        self.addCleanup(self._restore_env, self._saved_env)
        self.addCleanup(self._reset_llm_client_state)
        self._reset_llm_client_state()

        # A-M0-01: 只允许操作 conftest 注入的隔离物料库，禁止硬编码业务 app/materials.db
        db_path = os.environ.get("MATERIALS_DB_PATH")
        self.assertTrue(
            db_path,
            "MATERIALS_DB_PATH 未注入：必须在 tests/conftest.py 隔离环境下运行，禁止写业务库。",
        )
        db_conn = sqlite3.connect(db_path)
        try:
            cursor = db_conn.cursor()
            cursor.execute("DELETE FROM llm_cache")
            db_conn.commit()
        finally:
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

        with patch.dict(os.environ, {"LLM_ENABLED": "true", "DASHSCOPE_API_KEY": "sk-mock-key"}):
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

        query_info = {"name": "防爆轴流风机", "specification": "T35-11-4.0", "category": "通风机"}
        candidates = [
            {"code": "C1", "name": "轴流风机", "specification": "T35-11-3.0"},
            {"code": "C2", "name": "防爆轴流风机", "specification": "T35-11-4.0"},
            {"code": "C3", "name": "管道风机", "specification": "GD30-10"}
        ]

        with patch.dict(os.environ, {"LLM_ENABLED": "true", "DASHSCOPE_API_KEY": "sk-mock-key"}):
            res = llm_client.check_match_by_llm(query_info, candidates)

        self.assertIsNotNone(res)
        self.assertEqual(res["chosen_code"], "C2")
        self.assertEqual(res["chosen_index"], 1)
        self.assertEqual(res["reason"], "型号规格一致")

    def test_circuit_breaker_blocks_api(self):
        # Setup circuit broken
        llm_client.consecutive_failures = llm_client.config.get("max_consecutive_failures", 5)
        llm_client.circuit_broken_until = 9999999999.0

        with patch.dict(os.environ, {"LLM_ENABLED": "true", "DASHSCOPE_API_KEY": "sk-mock-key"}):
            res = llm_client.check_match_by_llm({}, [{"code": "C1"}])
        self.assertIsNone(res)

    def test_post_mock_env_restored_and_daemon_external_llm_blocked(self):
        """C-M0-09: after mock scope exits, env/singleton stay clean and external LLM calls stay blocked even in daemon threads."""
        import threading

        # Mock scope exited -> env & is_enabled must remain disabled/empty
        self.assertEqual(os.environ.get("LLM_ENABLED", "false").lower(), "false")
        self.assertEqual(os.environ.get("DASHSCOPE_API_KEY", ""), "")
        self.assertFalse(llm_client.is_enabled())

        conftest_mod = sys.modules.get("conftest") or sys.modules.get("tests.conftest")
        self.assertIsNotNone(conftest_mod)
        before_blocks = len(conftest_mod.BLOCKED_EXTERNAL_ATTEMPTS)
        thread_errors = []

        def leaked_daemon():
            try:
                with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "sk-leaked-daemon-key"}):
                    res = llm_client._call_qwen_api("sys", "user", "qwen-plus")
                    thread_errors.append(("returned", res))
            except Exception as exc:
                thread_errors.append(("raised", repr(exc)))

        t = threading.Thread(target=leaked_daemon, daemon=True)
        t.start()
        t.join(timeout=5.0)
        self.assertFalse(t.is_alive())
        self.assertGreater(len(conftest_mod.BLOCKED_EXTERNAL_ATTEMPTS), before_blocks)
        new_blocks = conftest_mod.BLOCKED_EXTERNAL_ATTEMPTS[before_blocks:]
        self.assertTrue(
            any("Blocked external" in item or "Offline test sandbox" in item for item in new_blocks),
            new_blocks,
        )

if __name__ == "__main__":
    unittest.main()
