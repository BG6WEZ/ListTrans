# -*- coding: utf-8 -*-
"""
V0.0.8 LLM-Driven Rule Engine & Audit Governance Module.
Supports:
1. Multi-vendor LLM provider management & priority router with auto failover.
2. Natural language description to Rule DSL JSON compilation.
3. Rule draft editing, testing, submission, approval/rejection audit workflow.
4. Dynamic execution of active DSL rules in cleaning, parameter extraction, and item matching.
5. Thread-safe hit tracking and leaderboard statistics.
"""

import os
import re
import json
import time
import sqlite3
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("llm_rule_engine")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "app", "materials.db")

def get_db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn

def init_rule_engine_db():
    """Ensure llm_providers, llm_rules, and rule_audit_logs tables exist in materials.db."""
    conn = get_db_conn()
    cursor = conn.cursor()
    
    # 1. Multi-vendor providers table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS llm_providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            model_name TEXT NOT NULL,
            api_key_enc TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 10,
            timeout_seconds REAL DEFAULT 8.0,
            is_active INTEGER DEFAULT 1,
            total_calls INTEGER DEFAULT 0,
            total_successes INTEGER DEFAULT 0,
            total_failovers INTEGER DEFAULT 0,
            avg_latency_ms REAL DEFAULT 0.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 2. LLM rules definition table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS llm_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id TEXT UNIQUE NOT NULL,
            rule_name TEXT NOT NULL,
            scope_level TEXT NOT NULL DEFAULT 'category',
            scope_target TEXT,
            discipline TEXT,
            priority INTEGER DEFAULT 10,
            description TEXT,
            rule_json TEXT NOT NULL,
            author_id INTEGER NOT NULL,
            author_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            hit_count INTEGER DEFAULT 0,
            last_hit_at DATETIME,
            approver_id INTEGER,
            approver_name TEXT,
            approval_comment TEXT,
            approved_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 3. Rule audit logs table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rule_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id TEXT NOT NULL,
            action TEXT NOT NULL,
            operator_id INTEGER NOT NULL,
            operator_name TEXT NOT NULL,
            operator_role TEXT NOT NULL,
            comment TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    
    # Seed default LLM providers if table is empty
    cursor.execute("SELECT COUNT(*) FROM llm_providers")
    if cursor.fetchone()[0] == 0:
        dashscope_key = os.getenv("DASHSCOPE_API_KEY", "")
        cursor.execute("""
            INSERT INTO llm_providers 
            (provider_id, name, base_url, model_name, api_key_enc, priority, timeout_seconds, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "qwen-max-default",
            "阿里云 DashScope (Qwen-Max)",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen-max",
            dashscope_key,
            1,
            10.0,
            1 if dashscope_key else 0
        ))
        cursor.execute("""
            INSERT INTO llm_providers 
            (provider_id, name, base_url, model_name, api_key_enc, priority, timeout_seconds, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "qwen-plus-fallback",
            "阿里云 DashScope (Qwen-Plus)",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen-plus",
            dashscope_key,
            2,
            8.0,
            1 if dashscope_key else 0
        ))
        cursor.execute("""
            INSERT INTO llm_providers 
            (provider_id, name, base_url, model_name, api_key_enc, priority, timeout_seconds, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "deepseek-v3-fallback",
            "DeepSeek API (DeepSeek-V3)",
            "https://api.deepseek.com/v1",
            "deepseek-chat",
            os.getenv("DEEPSEEK_API_KEY", ""),
            3,
            10.0,
            1 if os.getenv("DEEPSEEK_API_KEY") else 0
        ))
        conn.commit()
    conn.close()

# Initialize DB on module import
try:
    init_rule_engine_db()
except Exception as e:
    logger.error(f"Failed to init rule engine DB: {e}")


class LLMProviderManager:
    """Manages multi-vendor LLM providers, priority routing, and automatic failover."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(LLMProviderManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def list_providers(self, include_keys: bool = False) -> List[Dict[str, Any]]:
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM llm_providers ORDER BY priority ASC, id ASC")
        rows = cursor.fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            if not include_keys:
                # Mask API key for security
                key = d.get("api_key_enc", "")
                d["api_key_enc"] = key[:4] + "****" + key[-4:] if len(key) >= 8 else ("****" if key else "")
            results.append(d)
        return results

    def add_provider(self, provider_data: Dict[str, Any]) -> Dict[str, Any]:
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO llm_providers 
            (provider_id, name, base_url, model_name, api_key_enc, priority, timeout_seconds, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
                name=excluded.name,
                base_url=excluded.base_url,
                model_name=excluded.model_name,
                api_key_enc=excluded.api_key_enc,
                priority=excluded.priority,
                timeout_seconds=excluded.timeout_seconds,
                is_active=excluded.is_active,
                updated_at=CURRENT_TIMESTAMP
        """, (
            provider_data["provider_id"].strip(),
            provider_data["name"].strip(),
            provider_data["base_url"].strip(),
            provider_data["model_name"].strip(),
            provider_data.get("api_key_enc", "").strip(),
            int(provider_data.get("priority", 10)),
            float(provider_data.get("timeout_seconds", 8.0)),
            1 if provider_data.get("is_active", True) else 0
        ))
        conn.commit()
        conn.close()
        return {"success": True, "detail": "大模型节点添加/更新成功。"}

    def update_provider(self, provider_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        conn = get_db_conn()
        cursor = conn.cursor()
        field_map = {
            "name": "name",
            "base_url": "base_url",
            "model_name": "model_name",
            "priority": "priority",
            "timeout_seconds": "timeout_seconds",
            "is_active": "is_active"
        }
        set_clauses = []
        params = []
        for k, col in field_map.items():
            if k in updates and updates[k] is not None:
                set_clauses.append(f"{col} = ?")
                if k == "is_active":
                    params.append(1 if updates[k] else 0)
                else:
                    params.append(updates[k])
                    
        if updates.get("api_key_enc"):
            set_clauses.append("api_key_enc = ?")
            params.append(updates["api_key_enc"])
            
        if not set_clauses:
            conn.close()
            return {"success": True}
            
        set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        params.append(provider_id)
        
        cursor.execute(f"UPDATE llm_providers SET {', '.join(set_clauses)} WHERE provider_id = ?", params)
        conn.commit()
        conn.close()
        return {"success": True, "detail": "节点配置更新成功。"}

    def delete_provider(self, provider_id: str) -> Dict[str, Any]:
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM llm_providers WHERE provider_id = ?", (provider_id,))
        conn.commit()
        conn.close()
        return {"success": True, "detail": "节点已删除。"}

    def _update_stats(self, provider_id: str, success: bool, latency_ms: float, is_failover: bool):
        conn = get_db_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE llm_providers SET 
                total_calls = total_calls + 1,
                total_successes = total_successes + ?,
                total_failovers = total_failovers + ?,
                avg_latency_ms = (avg_latency_ms * total_calls + ?) / (total_calls + 1),
                updated_at = CURRENT_TIMESTAMP
                WHERE provider_id = ?
            """, (1 if success else 0, 1 if is_failover else 0, latency_ms, provider_id))
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update provider stats: {e}")
        finally:
            conn.close()

    def call_with_priority_fallback(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """
        Executes LLM API request using active providers sorted by priority.
        If Provider N times out or fails, automatically failover to Provider N+1.
        """
        from openai import OpenAI
        
        providers = self.list_providers(include_keys=True)
        active_providers = [p for p in providers if p.get("is_active") and p.get("api_key_enc")]
        
        if not active_providers:
            # Fallback to env VAR DASHSCOPE_API_KEY
            env_key = os.getenv("DASHSCOPE_API_KEY", "")
            if env_key:
                active_providers = [{
                    "provider_id": "env-dashscope",
                    "name": "Env DashScope",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "model_name": "qwen-max",
                    "api_key_enc": env_key,
                    "timeout_seconds": 10.0
                }]
            else:
                return {
                    "success": False,
                    "error": "系统未配置可用的大模型 API Key。请管理员在后台设置。",
                    "response_text": None
                }

        attempts = 0
        last_error = None

        for p in active_providers:
            attempts += 1
            is_failover = (attempts > 1)
            provider_id = p["provider_id"]
            start_t = time.time()
            
            try:
                client = OpenAI(
                    api_key=p["api_key_enc"],
                    base_url=p["base_url"]
                )
                timeout = float(p.get("timeout_seconds", 8.0))
                
                logger.info(f"Attempting LLM call using [{p['name']}] (Priority Attempt #{attempts})")
                
                response = client.chat.completions.create(
                    model=p["model_name"],
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    timeout=timeout
                )
                
                latency = (time.time() - start_t) * 1000
                content = response.choices[0].message.content
                
                self._update_stats(provider_id, success=True, latency_ms=latency, is_failover=is_failover)
                
                return {
                    "success": True,
                    "provider_name": p["name"],
                    "model_name": p["model_name"],
                    "latency_ms": round(latency, 1),
                    "is_failover": is_failover,
                    "response_text": content
                }
            except Exception as e:
                latency = (time.time() - start_t) * 1000
                logger.warning(f"LLM Provider [{p['name']}] failed after {latency:.1f}ms: {e}")
                self._update_stats(provider_id, success=False, latency_ms=latency, is_failover=is_failover)
                last_error = str(e)
                continue

        return {
            "success": False,
            "error": f"所有大模型节点均调用超时或异常，最后一次报错: {last_error}",
            "response_text": None
        }


class DynamicRuleInterpreter:
    """Interprets and executes compiled JSON Rule DSLs locally in < 2ms."""
    
    @staticmethod
    def apply_cleaning_rules(text: str, rules_list: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
        """Applies text cleaning transformations."""
        cleaned = text
        applied_actions = []
        for r in rules_list:
            action = r.get("action")
            if action == "regex_replace":
                pat = r.get("pattern", "")
                rep = r.get("replacement", "")
                if pat:
                    # Adapt full-width punctuation and prevent swallowing newlines
                    adapted_pat = pat.replace("：", "[：:]").replace("（", "[（\\(]").replace("）", "[）\\)]")
                    if "[^：]+" in adapted_pat:
                        adapted_pat = adapted_pat.replace("[^：]+", "[^：:\\r\\n]+")
                    elif "[^:]+" in adapted_pat:
                        adapted_pat = adapted_pat.replace("[^:]+", "[^：:\\r\\n]+")
                    try:
                        new_text = re.sub(adapted_pat, rep, cleaned, flags=re.IGNORECASE)
                        if new_text != cleaned:
                            applied_actions.append(f"正则替换 [{pat}] -> [{rep}]")
                            cleaned = new_text
                    except Exception:
                        try:
                            new_text = re.sub(pat, rep, cleaned, flags=re.IGNORECASE)
                            if new_text != cleaned:
                                applied_actions.append(f"正则替换 [{pat}] -> [{rep}]")
                                cleaned = new_text
                        except Exception:
                            pass
            elif action == "synonym_replace":
                target = r.get("target", "")
                rep = r.get("replacement", "")
                if target:
                    if target in cleaned:
                        cleaned = cleaned.replace(target, rep)
                        applied_actions.append(f"同义词替换 [{target}] -> [{rep}]")
                    # Also try half-width normalized target
                    target_hw = target.replace("（", "(").replace("）", ")").replace("：", ":")
                    if target_hw != target and target_hw in cleaned:
                        cleaned = cleaned.replace(target_hw, rep)
                        applied_actions.append(f"同义词替换 [{target_hw}] -> [{rep}]")
            elif action == "remove_token":
                token = r.get("token", "")
                if token:
                    if token in cleaned:
                        cleaned = cleaned.replace(token, "")
                        applied_actions.append(f"移除词汇 [{token}]")
                    token_hw = token.replace("（", "(").replace("）", ")").replace("：", ":")
                    if token_hw != token and token_hw in cleaned:
                        cleaned = cleaned.replace(token_hw, "")
                        applied_actions.append(f"移除词汇 [{token_hw}]")
        return cleaned, applied_actions

    @staticmethod
    def apply_extraction_rules(text: str, rules_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Extracts structured key attributes and performs unit conversions."""
        extracted = {}
        for r in rules_list:
            key = r.get("key")
            pat = r.get("pattern")
            if not key or not pat:
                continue
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                groups = match.groups()
                if len(groups) >= 2:
                    # Number + Unit pattern
                    try:
                        val = float(groups[0])
                        unit = groups[1].strip()
                        convert_map = r.get("unit_convert", {})
                        multiplier = convert_map.get(unit, 1.0)
                        extracted[key] = val * multiplier
                    except ValueError:
                        extracted[key] = groups[0]
                elif len(groups) == 1:
                    val_str = groups[0].strip()
                    # Only convert to float for numeric metric keys
                    if key.lower() in ["name", "名称", "material", "材质", "model", "型号", "specification", "规格", "connection", "连接形式", "brand", "品牌"]:
                        extracted[key] = val_str
                    else:
                        try:
                            extracted[key] = float(val_str)
                        except ValueError:
                            extracted[key] = val_str
            elif "default_value" in r and r["default_value"] is not None:
                extracted[key] = r["default_value"]
        return extracted

    @staticmethod
    def evaluate_matching_filters(query_text: str, candidate_text: str, matching_rules: Dict[str, Any]) -> Tuple[bool, float, List[str]]:
        """
        Evaluates matching criteria against candidate item.
        Returns: (is_excluded, score_adjustment, triggered_reasons)
        """
        reasons = []
        # Check hard exclusion keywords
        for not_word in matching_rules.get("must_not_contain", []):
            if not_word and not_word.strip():
                nw = not_word.strip()
                # Check if candidate contains not_word, but ignore if it is preceded by negation (e.g. "不带POE", "非POE", "无POE")
                if nw.lower() in candidate_text.lower():
                    # Strip negated mentions
                    filtered_cand = re.sub(r'(?:不带|非|无|不含|未配|无配|不具有)\s*' + re.escape(nw), '', candidate_text, flags=re.IGNORECASE)
                    if nw.lower() in filtered_cand.lower():
                        reasons.append(f"包含排除词 [{nw}]")
                        return True, -999.0, reasons
                
        # Check hard required keywords
        for must_word in matching_rules.get("must_contain", []):
            if must_word and must_word.strip():
                mw = must_word.strip()
                if mw.lower() not in candidate_text.lower():
                    reasons.append(f"缺少必含词 [{mw}]")
                    return True, -999.0, reasons

        # Calculate score boosts
        score_adj = 0.0
        boost_map = matching_rules.get("score_boost_keywords", {})
        for kw, boost in boost_map.items():
            if kw and str(kw).strip():
                kw_str = str(kw).strip()
                if kw_str.lower() in candidate_text.lower():
                    score_adj += float(boost)
                    reasons.append(f"命中加分词 [{kw_str}] (+{boost})")

        return False, score_adj, reasons


class RuleHitStatsTracker:
    """Thread-safe buffered tracker for counting rule hit frequency."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(RuleHitStatsTracker, cls).__new__(cls, *args, **kwargs)
                    cls._instance._buffer = {} # rule_id -> count
                    cls._buffer_lock = threading.RLock()
        return cls._instance

    def record_hit(self, rule_id: str):
        with self._buffer_lock:
            self._buffer[rule_id] = self._buffer.get(rule_id, 0) + 1
            if len(self._buffer) >= 10:
                self.flush()

    def flush(self):
        with self._buffer_lock:
            if not self._buffer:
                return
            to_flush = dict(self._buffer)
            self._buffer.clear()

        conn = get_db_conn()
        cursor = conn.cursor()
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            for rule_id, count in to_flush.items():
                cursor.execute("""
                    UPDATE llm_rules SET 
                    hit_count = hit_count + ?,
                    last_hit_at = ?
                    WHERE rule_id = ?
                """, (count, now_str, rule_id))
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to flush hit counts to DB: {e}")
        finally:
            conn.close()


class LLMRuleManager:
    """Rule CRUD, LLM compilation, audit workflow, and in-memory active rules cache."""
    _active_rules_cache = None
    _cache_lock = threading.Lock()

    @classmethod
    def clear_active_cache(cls):
        with cls._cache_lock:
            cls._active_rules_cache = None

    @classmethod
    def get_active_rules(cls) -> List[Dict[str, Any]]:
        with cls._cache_lock:
            if cls._active_rules_cache is not None:
                return cls._active_rules_cache
            
            conn = get_db_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM llm_rules WHERE status = 'approved' ORDER BY priority ASC")
            rows = cursor.fetchall()
            conn.close()
            
            rules = []
            for r in rows:
                item = dict(r)
                try:
                    item["parsed_dsl"] = json.loads(item["rule_json"])
                except Exception:
                    item["parsed_dsl"] = {}
                rules.append(item)
                
            cls._active_rules_cache = rules
            return rules

    @classmethod
    def generate_rule_from_prompt(cls, description: str, scope_level: str, scope_target: str, discipline: str, sample_text: str = "") -> Dict[str, Any]:
        """
        Uses Priority LLM Router to parse natural language into a valid Rule DSL JSON.
        """
        system_prompt = """你是一个专业的建筑工程物料数据清洗与自动化匹配规则编译器。
你需要将用户用自然语言描述的清洗、提取和比对规则，编译为严格符合格式规范的 JSON Rule DSL 对象。

必须返回包含以下结构的 JSON 对象：
{
  "rule_name": "规则简明名称",
  "cleaning_rules": [
    {
      "action": "regex_replace" 或 "synonym_replace" 或 "remove_token",
      "pattern": "正则模式(若为regex_replace)",
      "target": "目标词(若为synonym_replace)",
      "replacement": "替换词",
      "token": "要移除的词",
      "description": "规则说明"
    }
  ],
  "extraction_rules": [
    {
      "key": "英文属性名(如 cooling_capacity)",
      "label": "中文显示名(如 制冷量)",
      "pattern": "提取正则(如 (\\\\d+(?:\\\\.\\\\d+)?)\\\\s*(kW|RT))",
      "unit_convert": {"RT": 3.517, "kW": 1.0},
      "default_value": null
    }
  ],
  "matching_rules": {
    "must_contain": ["必含关键词列"],
    "must_not_contain": ["排除关键词列"],
    "score_boost_keywords": {"加分关键词": 10.0}
  },
  "test_cases": [
    {
      "input_text": "测试样本原文",
      "expected_note": "预期解析说明"
    }
  ]
}
只输出标准的 JSON，不要包裹 Markdown 格式，不要包含任何其他文字解释。"""

        user_prompt = f"""【规则生成请求】
作用层级: {scope_level}
作用目标: {scope_target or '通用'}
所属专业: {discipline or '通用'}
自然语言描述需求: {description}
测试样本原文: {sample_text or '无'}
"""

        router = LLMProviderManager()
        res = router.call_with_priority_fallback(system_prompt, user_prompt)
        
        if not res["success"]:
            return res

        raw_text = res["response_text"].strip()
        # Clean JSON markdown quotes if present
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        try:
            dsl_json = json.loads(raw_text)
            return {
                "success": True,
                "provider_name": res["provider_name"],
                "latency_ms": res["latency_ms"],
                "dsl_json": dsl_json
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"LLM 输出的 JSON 格式解析失败: {e}. 原始输出: {raw_text[:200]}"
            }

    @classmethod
    def test_rule_execution(cls, dsl_json: Dict[str, Any], sample_text: str, candidate_sample: str = "") -> Dict[str, Any]:
        """Executes DynamicRuleInterpreter against sample text."""
        cleaning_rules = dsl_json.get("cleaning_rules", [])
        extraction_rules = dsl_json.get("extraction_rules", [])
        matching_rules = dsl_json.get("matching_rules", {})

        cleaned_text, cleaning_logs = DynamicRuleInterpreter.apply_cleaning_rules(sample_text, cleaning_rules)
        extracted_params = DynamicRuleInterpreter.apply_extraction_rules(cleaned_text, extraction_rules)
        
        match_result = None
        if candidate_sample:
            is_excluded, score_adj, match_reasons = DynamicRuleInterpreter.evaluate_matching_filters(
                cleaned_text, candidate_sample, matching_rules
            )
            match_result = {
                "candidate": candidate_sample,
                "is_excluded": is_excluded,
                "score_adjustment": score_adj,
                "reasons": match_reasons
            }

        return {
            "success": True,
            "original_text": sample_text,
            "cleaned_text": cleaned_text,
            "cleaning_logs": cleaning_logs,
            "extracted_params": extracted_params,
            "match_eval": match_result
        }

    @classmethod
    def save_rule_draft(cls, rule_data: Dict[str, Any], user_info: Dict[str, Any]) -> Dict[str, Any]:
        conn = get_db_conn()
        cursor = conn.cursor()
        
        rule_id = rule_data.get("rule_id")
        if not rule_id:
            rule_id = f"RULE-{int(time.time())}"
            
        dsl_str = json.dumps(rule_data.get("parsed_dsl", {}), ensure_ascii=False)
        
        cursor.execute("SELECT id FROM llm_rules WHERE rule_id = ?", (rule_id,))
        exists = cursor.fetchone()
        
        if exists:
            cursor.execute("""
                UPDATE llm_rules SET
                rule_name = ?, scope_level = ?, scope_target = ?, discipline = ?,
                priority = ?, description = ?, rule_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE rule_id = ?
            """, (
                rule_data.get("rule_name", "未命名规则"),
                rule_data.get("scope_level", "category"),
                rule_data.get("scope_target", ""),
                rule_data.get("discipline", ""),
                int(rule_data.get("priority", 10)),
                rule_data.get("description", ""),
                dsl_str,
                rule_id
            ))
            action = "update_draft"
        else:
            cursor.execute("""
                INSERT INTO llm_rules 
                (rule_id, rule_name, scope_level, scope_target, discipline, priority, description, rule_json, author_id, author_name, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
            """, (
                rule_id,
                rule_data.get("rule_name", "未命名规则"),
                rule_data.get("scope_level", "category"),
                rule_data.get("scope_target", ""),
                rule_data.get("discipline", ""),
                int(rule_data.get("priority", 10)),
                rule_data.get("description", ""),
                dsl_str,
                user_info["id"],
                user_info["username"]
            ))
            action = "create_draft"
            
        cursor.execute("""
            INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (rule_id, action, user_info["id"], user_info["username"], user_info.get("role", "employee"), "保存规则草稿"))
        
        conn.commit()
        conn.close()
        return {"success": True, "rule_id": rule_id, "detail": "规则草稿保存成功。"}

    @classmethod
    def submit_rule(cls, rule_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM llm_rules WHERE rule_id = ?", (rule_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {"success": False, "error": "规则不存在。"}
            
        cursor.execute("""
            UPDATE llm_rules SET status = 'pending_approval', updated_at = CURRENT_TIMESTAMP WHERE rule_id = ?
        """, (rule_id,))
        cursor.execute("""
            INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
            VALUES (?, 'submit', ?, ?, ?, '提交规则审核')
        """, (rule_id, user_info["id"], user_info["username"], user_info.get("role", "employee")))
        conn.commit()
        conn.close()
        return {"success": True, "detail": "规则已提交审核。"}

    @classmethod
    def audit_rule(cls, rule_id: str, action: str, comment: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Approve ('approve') or Reject ('reject') or Deprecate ('deprecate') a rule.
        """
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {"success": False, "error": "规则不存在。"}

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        if action == "approve":
            new_status = "approved"
            cursor.execute("""
                UPDATE llm_rules SET status = ?, approver_id = ?, approver_name = ?, approval_comment = ?, approved_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE rule_id = ?
            """, (new_status, user_info["id"], user_info["username"], comment, now_str, rule_id))
        elif action == "reject":
            new_status = "rejected"
            cursor.execute("""
                UPDATE llm_rules SET status = ?, approver_id = ?, approver_name = ?, approval_comment = ?, updated_at = CURRENT_TIMESTAMP
                WHERE rule_id = ?
            """, (new_status, user_info["id"], user_info["username"], comment, rule_id))
        elif action == "deprecate":
            new_status = "deprecated"
            cursor.execute("""
                UPDATE llm_rules SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE rule_id = ?
            """, (new_status, rule_id))
        else:
            conn.close()
            return {"success": False, "error": "未知的审核动作。"}

        cursor.execute("""
            INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (rule_id, action, user_info["id"], user_info["username"], user_info.get("role", "manager"), comment))

        conn.commit()
        conn.close()

        # Invalidate active rules cache
        cls.clear_active_cache()
        return {"success": True, "detail": f"规则审核完成，当前状态: {new_status}"}

    @classmethod
    def get_rule_by_id(cls, rule_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        try:
            d["parsed_dsl"] = json.loads(d["rule_json"])
        except Exception:
            d["parsed_dsl"] = {}
        return d

    @classmethod
    def update_rule(cls, rule_id: str, update_data: Dict[str, Any], user_info: Dict[str, Any]) -> Dict[str, Any]:
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {"success": False, "error": "规则不存在。"}

        dsl = update_data.get("parsed_dsl") or update_data.get("dsl_json") or {}
        dsl_str = json.dumps(dsl, ensure_ascii=False) if dsl else row["rule_json"]
        rule_name = update_data.get("rule_name", row["rule_name"])
        scope_level = update_data.get("scope_level", row["scope_level"])
        scope_target = update_data.get("scope_target", row["scope_target"])
        discipline = update_data.get("discipline", row["discipline"])
        priority = int(update_data.get("priority", row["priority"]))
        description = update_data.get("description", row["description"])
        
        cursor.execute("""
            UPDATE llm_rules SET
            rule_name = ?, scope_level = ?, scope_target = ?, discipline = ?,
            priority = ?, description = ?, rule_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE rule_id = ?
        """, (rule_name, scope_level, scope_target, discipline, priority, description, dsl_str, rule_id))

        cursor.execute("""
            INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
            VALUES (?, 'update', ?, ?, ?, ?)
        """, (rule_id, user_info["id"], user_info["username"], user_info.get("role", "employee"), "修改规则内容与参数"))

        conn.commit()
        conn.close()

        cls.clear_active_cache()
        return {"success": True, "detail": "规则修改成功。"}

    @classmethod
    def toggle_rule_status(cls, rule_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM llm_rules WHERE rule_id = ?", (rule_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {"success": False, "error": "规则不存在。"}

        current_status = row["status"]
        if current_status == "approved":
            new_status = "disabled"
            action_desc = "停用规则"
        else:
            new_status = "approved"
            action_desc = "启用规则"

        cursor.execute("""
            UPDATE llm_rules SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE rule_id = ?
        """, (new_status, rule_id))

        cursor.execute("""
            INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (rule_id, f"toggle_{new_status}", user_info["id"], user_info["username"], user_info.get("role", "employee"), action_desc))

        conn.commit()
        conn.close()

        cls.clear_active_cache()
        return {"success": True, "new_status": new_status, "detail": f"规则已成功{action_desc}！"}

    @classmethod
    def delete_rule(cls, rule_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {"success": False, "error": "规则不存在。"}

        cursor.execute("DELETE FROM llm_rules WHERE rule_id = ?", (rule_id,))
        cursor.execute("""
            INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
            VALUES (?, 'delete', ?, ?, ?, '删除规则')
        """, (rule_id, user_info["id"], user_info["username"], user_info.get("role", "employee")))

        conn.commit()
        conn.close()

        cls.clear_active_cache()
        return {"success": True, "detail": "规则已成功删除。"}

    @classmethod
    def list_rules(cls, status: Optional[str] = None, discipline: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = get_db_conn()
        cursor = conn.cursor()
        sql = "SELECT * FROM llm_rules WHERE 1=1"
        params = []
        if status:
            if status == "all_active":
                sql += " AND status IN ('approved', 'disabled')"
            else:
                sql += " AND status = ?"
                params.append(status)
        if discipline:
            sql += " AND discipline = ?"
            params.append(discipline)
        sql += " ORDER BY created_at DESC"
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["parsed_dsl"] = json.loads(d["rule_json"])
            except Exception:
                d["parsed_dsl"] = {}
            results.append(d)
        return results

    @classmethod
    def get_rule_stats(cls) -> Dict[str, Any]:
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM llm_rules WHERE status = 'approved'")
        approved_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM llm_rules WHERE status = 'pending_approval'")
        pending_count = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(hit_count) FROM llm_rules")
        total_hits = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT rule_id, rule_name, discipline, scope_target, hit_count, author_name, approved_at FROM llm_rules WHERE status = 'approved' ORDER BY hit_count DESC LIMIT 10")
        top_rules = [dict(r) for r in cursor.fetchall()]
        
        conn.close()
        return {
            "approved_count": approved_count,
            "pending_count": pending_count,
            "total_hits": total_hits,
            "top_rules": top_rules
        }
