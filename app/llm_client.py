# -*- coding: utf-8 -*-
import os
import json
import time
import hashlib
import sqlite3
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv

logger = logging.getLogger("llm_client")

# Load environment variables (.env file)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Constants & Paths
DB_PATH = os.path.join(BASE_DIR, "app", "materials.db")
CONFIG_PATH = os.path.join(BASE_DIR, "config", "llm_config.json")
PROMPTS_PATH = os.path.join(BASE_DIR, "config", "llm_prompts.json")

class QwenLLMClient:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(QwenLLMClient, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.load_config()
        self.load_prompts()
        self.consecutive_failures = 0
        self.circuit_broken_until = 0.0
        
        # Statistics tracking
        self.stats = {
            "total_calls": 0,
            "cache_hits": 0,
            "api_calls": 0,
            "api_successes": 0,
            "api_failures": 0,
            "circuit_breaker_trips": 0,
            "total_latency_ms": 0.0
        }
        
        # Initialize Semaphore to throttle API concurrency (limit to 3 concurrent requests)
        self._semaphore = threading.Semaphore(3)
        
        # Initialize Cache DB table if db exists
        self.init_cache_table()
        self._initialized = True

    def load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                self.config = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load LLM config: {e}")
            self.config = {
                "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model_matching": "qwen-plus",
                "model_extracting": "qwen-turbo",
                "l2_score_min": 60.0,
                "l2_score_max": 85.0,
                "timeout_seconds": 5.0,
                "max_consecutive_failures": 5,
                "cooldown_seconds": 600
            }

    def load_prompts(self):
        try:
            with open(PROMPTS_PATH, "r", encoding="utf-8") as f:
                self.prompts = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load LLM prompts: {e}")
            self.prompts = {}

    def init_cache_table(self):
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS llm_cache (
                    cache_key TEXT PRIMARY KEY,
                    result_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to create llm_cache table: {e}")
        finally:
            if conn:
                conn.close()

    def is_enabled(self) -> bool:
        # Check LLM_ENABLED env, default to False
        enabled = os.getenv("LLM_ENABLED", "false").lower() in ("true", "1")
        if not enabled:
            return False
        
        # Check if DashScope API key is present
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            return False
            
        return True

    def check_circuit_breaker(self) -> bool:
        """Returns True if the circuit is open (broken/blocked), False otherwise."""
        if self.consecutive_failures >= self.config.get("max_consecutive_failures", 5):
            now = time.time()
            if now < self.circuit_broken_until:
                return True
            else:
                # Cooldown period expired, reset failure counter (half-open state trial)
                logger.info("Circuit breaker entered half-open state, trying API call.")
                # We do not reset consecutive_failures to 0 immediately; we let a success reset it.
                return False
        return False

    def record_success(self):
        self.consecutive_failures = 0
        self.circuit_broken_until = 0.0

    def record_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.config.get("max_consecutive_failures", 5):
            cooldown = self.config.get("cooldown_seconds", 600)
            self.circuit_broken_until = time.time() + cooldown
            self.stats["circuit_breaker_trips"] += 1
            logger.error(f"Circuit breaker tripped! Blocking API calls for {cooldown} seconds.")

    def get_cache(self, key: str) -> Optional[Dict[str, Any]]:
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT result_json FROM llm_cache WHERE cache_key = ?", (key,))
            row = cursor.fetchone()
            if row:
                self.stats["cache_hits"] += 1
                return json.loads(row[0])
        except Exception as e:
            logger.error(f"Cache read error: {e}")
        finally:
            if conn:
                conn.close()
        return None

    def set_cache(self, key: str, value: Dict[str, Any]):
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO llm_cache (cache_key, result_json) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False))
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Cache write error: {e}")
        finally:
            if conn:
                conn.close()

    def generate_hash(self, *args) -> str:
        string_to_hash = "|".join([str(arg) for arg in args])
        return hashlib.md5(string_to_hash.encode("utf-8")).hexdigest()

    def _call_qwen_api(self, system_prompt: str, user_prompt: str, model: str) -> Optional[str]:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            return None

        # Import openai package dynamically to avoid dependencies issues if not installed
        from openai import OpenAI
        
        client = OpenAI(
            api_key=api_key,
            base_url=self.config.get("qwen_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        )
        
        timeout = self.config.get("timeout_seconds", 5.0)
        
        # Try-retry block
        max_attempts = 2
        last_exception = None
        
        with self._semaphore:
            for attempt in range(max_attempts):
                start_time = time.time()
                try:
                    self.stats["api_calls"] += 1
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        response_format={"type": "json_object"},
                        timeout=timeout
                    )
                    latency = (time.time() - start_time) * 1000
                    self.stats["total_latency_ms"] += latency
                    self.stats["api_successes"] += 1
                    
                    self.record_success()
                    return response.choices[0].message.content
                except Exception as e:
                    latency = (time.time() - start_time) * 1000
                    self.stats["total_latency_ms"] += latency
                    self.stats["api_failures"] += 1
                    logger.warning(f"API call attempt {attempt+1} failed: {e}")
                    last_exception = e
                    # wait a tiny bit before retry
                    time.sleep(0.2)
                    
            # If all retries failed
            self.record_failure()
            logger.error(f"API call failed after {max_attempts} attempts: {last_exception}")
            return None

    def extract_params_by_llm(self, text: str, domain: str, must_keys: List[str], should_keys: List[str], flags: List[str]) -> Optional[Dict[str, Any]]:
        """
        Structure a specification text into standardized JSON using Qwen-turbo, aligned to schema definitions.
        """
        self.stats["total_calls"] += 1
        if not self.is_enabled():
            return None
            
        if self.check_circuit_breaker():
            logger.warning("LLM call blocked by circuit breaker.")
            return None

        # Clean/sanitize text input to prevent injection
        sanitized_text = text.replace("`", "").replace("{", "[").replace("}", "]").strip()
        
        # Hash lookup
        cache_key = self.generate_hash("extract", domain, sanitized_text)
        cached = self.get_cache(cache_key)
        if cached is not None:
            return cached

        # Prepare Prompt
        system_tmpl = self.prompts.get("extract_system_prompt", "You are a professional assistant.")
        user_tmpl = self.prompts.get("extract_user_prompt", "")
        
        user_prompt = user_tmpl.format(
            domain=domain,
            text=sanitized_text,
            must_keys=str(must_keys),
            should_keys=str(should_keys),
            flags=str(flags)
        )
        
        model = self.config.get("model_extracting", "qwen-turbo")
        
        result_json = self._call_qwen_api(system_tmpl, user_prompt, model)
        if not result_json:
            return None
            
        try:
            result = json.loads(result_json)
            
            # Whitelist filter extracted attributes
            model_extracted = result.get("model", "")
            attrs_extracted = result.get("attrs", {})
            
            cleaned_attrs = {}
            allowed_keys = set(must_keys + should_keys + flags)
            
            # Map flags if present as boolean
            for k, v in attrs_extracted.items():
                if k in allowed_keys:
                    # If it's a flag, ensure it's bool
                    if k in flags:
                        if isinstance(v, bool):
                            cleaned_attrs[k] = v
                        elif str(v).lower() in ("true", "1", "yes", "是"):
                            cleaned_attrs[k] = True
                        else:
                            cleaned_attrs[k] = False
                    else:
                        cleaned_attrs[k] = v
                        
            final_res = {
                "model": model_extracted,
                "attrs": cleaned_attrs
            }
            
            # Save to Cache
            self.set_cache(cache_key, final_res)
            return final_res
        except Exception as e:
            logger.error(f"Failed to parse LLM response: {e}, content: {result_json}")
            return None

    def check_match_by_llm(self, query_info: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Given a query item and a list of top candidate standard materials, use Qwen-plus to choose the best matching candidate.
        """
        self.stats["total_calls"] += 1
        if not self.is_enabled() or not candidates:
            return None
            
        if self.check_circuit_breaker():
            logger.warning("LLM call blocked by circuit breaker.")
            return None

        # Clean/sanitize inputs
        q_name = query_info.get("name", "").strip()
        q_spec = query_info.get("specification", "").strip()
        q_category = query_info.get("category", "").strip()
        
        # Prepare candidate representations (omit description to minimize prompt length and prevent timeouts)
        candidates_data = []
        candidates_hashes = []
        for idx, c in enumerate(candidates[:5]):  # limit to top 5
            candidates_data.append({
                "index": idx,
                "code": c["code"],
                "name": c["name"],
                "specification": c["specification"],
                "category": c.get("category", "")
            })
            candidates_hashes.append(c["code"])
            
        # Hash lookup: query description and candidate codes
        cand_hash_str = ",".join(sorted(candidates_hashes))
        cache_key = self.generate_hash("match", q_name, q_spec, cand_hash_str)
        cached = self.get_cache(cache_key)
        if cached is not None:
            return cached

        # Prepare Prompt
        system_tmpl = self.prompts.get("matching_system_prompt", "You are a professional assistant.")
        user_tmpl = self.prompts.get("matching_user_prompt", "")
        
        user_prompt = user_tmpl.format(
            query_name=q_name,
            query_spec=q_spec,
            query_category=q_category,
            candidates_json=json.dumps(candidates_data, ensure_ascii=False, indent=2)
        )
        
        model = self.config.get("model_matching", "qwen-plus")
        
        result_json = self._call_qwen_api(system_tmpl, user_prompt, model)
        if not result_json:
            return None
            
        try:
            decision = json.loads(result_json)
            chosen_idx = decision.get("chosen_index", -1)
            reason = decision.get("reason", "LLM决策匹配")
            
            # Validate index
            if chosen_idx != -1 and 0 <= chosen_idx < len(candidates):
                best_match = candidates[chosen_idx]
                final_res = {
                    "chosen_code": best_match["code"],
                    "chosen_index": chosen_idx,
                    "reason": reason
                }
            else:
                final_res = {
                    "chosen_code": None,
                    "chosen_index": -1,
                    "reason": "LLM 判定无匹配或越界"
                }
                
            self.set_cache(cache_key, final_res)
            return final_res
        except Exception as e:
            logger.error(f"Failed to parse LLM matching response: {e}, content: {result_json}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Return statistics for monitoring."""
        total = self.stats["total_calls"]
        hits = self.stats["cache_hits"]
        hit_rate = (hits / total) * 100.0 if total > 0 else 0.0
        
        api_success = self.stats["api_successes"]
        api_fail = self.stats["api_failures"]
        api_total = api_success + api_fail
        api_error_rate = (api_fail / api_total) * 100.0 if api_total > 0 else 0.0
        
        avg_latency = (self.stats["total_latency_ms"] / api_total) if api_total > 0 else 0.0
        
        circuit_status = "CLOSED"
        now = time.time()
        if self.consecutive_failures >= self.config.get("max_consecutive_failures", 5):
            if now < self.circuit_broken_until:
                circuit_status = f"OPEN (until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.circuit_broken_until))})"
            else:
                circuit_status = "HALF-OPEN"

        return {
            "llm_enabled": self.is_enabled(),
            "circuit_breaker_status": circuit_status,
            "consecutive_failures": self.consecutive_failures,
            "total_calls": total,
            "cache_hits": hits,
            "cache_hit_rate": f"{hit_rate:.2f}%",
            "api_calls": api_total,
            "api_successes": api_success,
            "api_failures": api_fail,
            "api_error_rate": f"{api_error_rate:.2f}%",
            "average_api_latency_ms": f"{avg_latency:.2f}ms",
            "circuit_breaker_trips": self.stats["circuit_breaker_trips"]
        }

# Singleton instance export
llm_client = QwenLLMClient()
