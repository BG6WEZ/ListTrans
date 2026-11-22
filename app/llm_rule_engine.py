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
import hashlib
import sqlite3
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("llm_rule_engine")


def compute_rule_content_digest(
    scope_level: str,
    scope_target: Optional[str],
    discipline: Optional[str],
    priority: int,
    rule_json_str: str,
) -> str:
    """Compute canonical SHA256 digest over all execution-affecting fields of a rule."""
    try:
        parsed_dsl = json.loads(rule_json_str) if isinstance(rule_json_str, str) else rule_json_str
    except Exception:
        parsed_dsl = rule_json_str
    canonical = json.dumps(
        {
            "scope_level": scope_level or "category",
            "scope_target": scope_target or "",
            "discipline": discipline or "",
            "priority": int(priority if priority is not None else 10),
            "rule_json": parsed_dsl,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# F25: honor the same injectable materials DB as db_manager instead of a fixed business DB
DB_PATH = os.getenv(
    "MATERIALS_DB_PATH", os.path.join(BASE_DIR, "app", "materials.db")
)

def _resolve_db_path() -> str:
    """实时解析物料库路径，保证测试会话中后注入的 MATERIALS_DB_PATH 生效。"""
    return os.getenv(
        "MATERIALS_DB_PATH", os.path.join(BASE_DIR, "app", "materials.db")
    )


# 已完成惰性建表的数据库路径（每路径仅初始化一次），替代原模块导入期无条件建表
_initialized_db_paths = set()
_init_lock = threading.Lock()


def _open_read_conn() -> sqlite3.Connection:
    """Open a read-only SQLite connection for active rules without triggering DDL BEGIN IMMEDIATE locks."""
    db_path = _resolve_db_path()
    if os.getenv("TESTING") == "1":
        prod_db = os.path.join(BASE_DIR, "app", "materials.db")
        if os.path.abspath(db_path) == os.path.abspath(prod_db):
            raise RuntimeError(
                "测试隔离护栏触发：llm_rule_engine 在 TESTING=1 下解析到业务库 "
                f"{db_path}，请先由 tests/conftest.py 注入 MATERIALS_DB_PATH 隔离路径。"
            )
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass
    return conn


def get_db_conn():
    db_path = _resolve_db_path()
    # A-M0-02: 测试态阻断护栏——隔离环境变量缺失或指回业务库时立即失败，杜绝误写生产库
    if os.getenv("TESTING") == "1":
        prod_db = os.path.join(BASE_DIR, "app", "materials.db")
        if os.path.abspath(db_path) == os.path.abspath(prod_db):
            raise RuntimeError(
                "测试隔离护栏触发：llm_rule_engine 在 TESTING=1 下解析到业务库 "
                f"{db_path}，请先由 tests/conftest.py 注入 MATERIALS_DB_PATH 隔离路径。"
            )
    # 惰性建表：模块导入不再写库，首次实际获取连接时按路径初始化一次，生产行为保持兼容
    if db_path not in _initialized_db_paths:
        with _init_lock:
            if db_path not in _initialized_db_paths:
                init_rule_engine_db()
                _initialized_db_paths.add(db_path)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn

def init_rule_engine_db(db_path: Optional[str] = None):
    """Ensure llm_providers, llm_rules, and rule_audit_logs exist and run transactional M4 rule schema migration."""
    db_path = db_path or _resolve_db_path()
    if os.getenv("TESTING") == "1":
        prod_db = os.path.join(BASE_DIR, "app", "materials.db")
        if os.path.abspath(db_path) == os.path.abspath(prod_db):
            raise RuntimeError(
                "测试隔离护栏触发：init_rule_engine_db 在 TESTING=1 下解析到业务库 "
                f"{db_path}，请先由 tests/conftest.py 注入 MATERIALS_DB_PATH 隔离路径。"
            )
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
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

        # 2. LLM rules definition table (with M4 revision/digest/publish/disable provenance columns)
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
                revision INTEGER NOT NULL DEFAULT 1,
                content_digest TEXT,
                approved_revision INTEGER,
                approved_digest TEXT,
                publisher_id INTEGER,
                publisher_name TEXT,
                published_at DATETIME,
                disabled_by_id INTEGER,
                disabled_by_name TEXT,
                disabled_reason TEXT,
                disabled_at DATETIME,
                last_eval_id TEXT DEFAULT '',
                last_eval_digest TEXT DEFAULT '',
                neutral_publish_reason TEXT DEFAULT '',
                governance_provenance TEXT DEFAULT 'pending_eval',
                migration_version TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Idempotent schema migration for pre-M4 / pre-M2 llm_rules tables
        existing_cols = [r[1] for r in cursor.execute("PRAGMA table_info(llm_rules)").fetchall()]
        m4_rule_cols = [
            ("revision", "INTEGER NOT NULL DEFAULT 1"),
            ("content_digest", "TEXT"),
            ("approved_revision", "INTEGER"),
            ("approved_digest", "TEXT"),
            ("publisher_id", "INTEGER"),
            ("publisher_name", "TEXT"),
            ("published_at", "DATETIME"),
            ("disabled_by_id", "INTEGER"),
            ("disabled_by_name", "TEXT"),
            ("disabled_reason", "TEXT"),
            ("disabled_at", "DATETIME"),
            ("last_eval_id", "TEXT DEFAULT ''"),
            ("last_eval_digest", "TEXT DEFAULT ''"),
            ("neutral_publish_reason", "TEXT DEFAULT ''"),
            ("governance_provenance", "TEXT DEFAULT 'pending_eval'"),
            ("migration_version", "TEXT DEFAULT ''"),
            ("updated_at", "DATETIME"),
        ]
        m2_gov_col_added = False
        for col_name, col_type in m4_rule_cols:
            if col_name not in existing_cols:
                cursor.execute(f"ALTER TABLE llm_rules ADD COLUMN {col_name} {col_type}")
                if col_name == "governance_provenance":
                    m2_gov_col_added = True
        if m2_gov_col_added:
            cursor.execute(
                """
                UPDATE llm_rules
                SET governance_provenance = CASE
                        WHEN COALESCE(last_eval_id, '') != '' THEN governance_provenance
                        ELSE 'legacy_unverified'
                    END,
                    migration_version = CASE
                        WHEN COALESCE(migration_version, '') != '' THEN migration_version
                        ELSE 'v0.16-m2-v1'
                    END
                WHERE COALESCE(last_eval_id, '') = ''
                """
            )

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

        # Backfill revision & content_digest safely
        cursor.execute("UPDATE llm_rules SET revision = 1 WHERE revision IS NULL OR revision < 1")
        all_rule_rows = cursor.execute("SELECT * FROM llm_rules").fetchall()
        for rr in all_rule_rows:
            computed_digest = compute_rule_content_digest(
                rr["scope_level"],
                rr["scope_target"],
                rr["discipline"],
                int(rr["priority"] if rr["priority"] is not None else 10),
                rr["rule_json"],
            )
            if rr["content_digest"] != computed_digest and not rr["content_digest"]:
                cursor.execute(
                    "UPDATE llm_rules SET content_digest = ? WHERE id = ?",
                    (computed_digest, rr["id"]),
                )
            # Conservative legacy migration: unverified legacy 'approved' rules cannot stay active
            # and must be re-submitted/approved/published by a distinct approver + admin.
            if rr["status"] == "approved":
                valid_provenance = (
                    rr["approver_id"] is not None
                    and int(rr["approver_id"]) != int(rr["author_id"])
                    and rr["publisher_id"] is not None
                    and rr["approved_digest"] == computed_digest
                    and rr["approved_revision"] == rr["revision"]
                )
                if not valid_provenance:
                    cursor.execute(
                        """
                        UPDATE llm_rules
                        SET status = 'disabled',
                            approved_revision = NULL,
                            approved_digest = NULL,
                            disabled_by_name = 'system_migration',
                            disabled_reason = 'M4_LEGACY_UNVERIFIED_REAPPROVAL_REQUIRED',
                            disabled_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (rr["id"],),
                    )
                    cursor.execute(
                        """
                        INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
                        VALUES (?, 'legacy_migration_disable', 0, 'system_migration', 'system',
                                '历史无双人审批或发布凭据的approved规则保守停用，需重新提交审核并由管理员发布')
                        """,
                        (rr["rule_id"],),
                    )

        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    # Provider seeding uses separate connection safely (keeping original behavior)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
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
    finally:
        try:
            conn.close()
        except Exception:
            pass

# A-M0-02: 不再在模块导入时执行 init_rule_engine_db()，消除导入即建表/写默认节点的副作用。
# 生产路径：server 启动或相关功能首次使用时按需调用 init_rule_engine_db()；
# 测试路径：conftest 已注入隔离 MATERIALS_DB_PATH，由测试（如 test_rule_deadlock_fix）显式初始化到隔离库。


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
    def _compile_cleaning_regex(pat: str) -> re.Pattern:
        """Compile cleaning regex with full-width punctuation adaptation, failing explicitly on invalid patterns."""
        adapted_pat = pat.replace("：", "[：:]").replace("（", "[（\\(]").replace("）", "[）\\)]")
        if "[^：]+" in adapted_pat:
            adapted_pat = adapted_pat.replace("[^：]+", "[^：:\\r\\n]+")
        elif "[^:]+" in adapted_pat:
            adapted_pat = adapted_pat.replace("[^:]+", "[^：:\\r\\n]+")
        try:
            return re.compile(adapted_pat, flags=re.IGNORECASE)
        except re.error:
            try:
                return re.compile(pat, flags=re.IGNORECASE)
            except re.error as exc:
                raise DynamicRuleUnavailableError(f"动态清洗规则正则非法: {exc}") from exc

    @staticmethod
    def validate_rule_stages(dsl: Any, rule_id: Any = "?") -> Dict[str, Any]:
        """Validate cleaning, extraction, and matching stages of a Rule DSL without silent swallow."""
        if not isinstance(dsl, dict):
            raise DynamicRuleUnavailableError(f"动态规则 {rule_id} parsed_dsl 结构非法: 期望 dict")

        cleaning_rules = dsl.get("cleaning_rules")
        if cleaning_rules is not None:
            if not isinstance(cleaning_rules, list):
                raise DynamicRuleUnavailableError(f"动态规则 {rule_id} cleaning_rules 结构非法: 期望 list")
            for idx, item in enumerate(cleaning_rules):
                if not isinstance(item, dict):
                    raise DynamicRuleUnavailableError(
                        f"动态规则 {rule_id} cleaning_rules[{idx}] 结构非法: 期望 dict"
                    )
                if item.get("action") == "regex_replace" and item.get("pattern"):
                    DynamicRuleInterpreter._compile_cleaning_regex(str(item.get("pattern")))

        extraction_rules = dsl.get("extraction_rules")
        if extraction_rules is not None:
            if not isinstance(extraction_rules, list):
                raise DynamicRuleUnavailableError(f"动态规则 {rule_id} extraction_rules 结构非法: 期望 list")
            for idx, item in enumerate(extraction_rules):
                if not isinstance(item, dict):
                    raise DynamicRuleUnavailableError(
                        f"动态规则 {rule_id} extraction_rules[{idx}] 结构非法: 期望 dict"
                    )
                pat = item.get("pattern")
                if pat:
                    try:
                        re.compile(str(pat), re.IGNORECASE)
                    except re.error as exc:
                        key = item.get("key") or f"#{idx}"
                        raise DynamicRuleUnavailableError(f"动态提取规则({key})正则非法: {exc}") from exc

        if "matching_rules" in dsl:
            DynamicRuleInterpreter.validate_matching_rules(dsl.get("matching_rules"), rule_id=rule_id)
        return dsl
    
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
                    compiled = DynamicRuleInterpreter._compile_cleaning_regex(pat)
                    try:
                        new_text = compiled.sub(rep, cleaned)
                        if new_text != cleaned:
                            applied_actions.append(f"正则替换 [{pat}] -> [{rep}]")
                            cleaned = new_text
                    except Exception as exc:
                        raise DynamicRuleUnavailableError(f"动态清洗规则执行异常: {type(exc).__name__}") from exc
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
            try:
                match = re.search(pat, text, re.IGNORECASE)
            except re.error as exc:
                raise DynamicRuleUnavailableError(f"动态提取规则({key})正则非法: {exc}") from exc
            except Exception as exc:
                raise DynamicRuleUnavailableError(f"动态提取规则({key})执行异常: {type(exc).__name__}") from exc
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
    def validate_matching_rules(matching_rules: Any, rule_id: Any = "?") -> Dict[str, Any]:
        """V015-M3 R03：校验动态规则 matching_rules 结构，损坏时显式抛 DynamicRuleUnavailableError。"""
        if matching_rules is None:
            return {}
        if not isinstance(matching_rules, dict):
            raise DynamicRuleUnavailableError(f"动态规则 {rule_id} matching_rules 结构非法: 期望 dict")
        for list_key in ("must_not_contain", "must_contain"):
            if list_key in matching_rules and matching_rules[list_key] is not None:
                val = matching_rules[list_key]
                if not isinstance(val, list) or any(not isinstance(item, str) for item in val):
                    raise DynamicRuleUnavailableError(
                        f"动态规则 {rule_id} {list_key} 结构非法: 期望字符串列表"
                    )
        if "score_boost_keywords" in matching_rules and matching_rules["score_boost_keywords"] is not None:
            boosts = matching_rules["score_boost_keywords"]
            if not isinstance(boosts, dict):
                raise DynamicRuleUnavailableError(
                    f"动态规则 {rule_id} score_boost_keywords 结构非法: 期望 dict"
                )
            for kw, boost in boosts.items():
                if not isinstance(kw, str):
                    raise DynamicRuleUnavailableError(
                        f"动态规则 {rule_id} score_boost_keywords 键非法"
                    )
                try:
                    float(boost)
                except (TypeError, ValueError) as exc:
                    raise DynamicRuleUnavailableError(
                        f"动态规则 {rule_id} score_boost_keywords 分值非法: {boost}"
                    ) from exc
        return matching_rules

    @staticmethod
    def evaluate_matching_filters(query_text: str, candidate_text: str, matching_rules: Dict[str, Any]) -> Tuple[bool, float, List[str]]:
        """
        Evaluates matching criteria against candidate item.
        Returns: (is_excluded, score_adjustment, triggered_reasons)
        """
        validated_rules = DynamicRuleInterpreter.validate_matching_rules(matching_rules)
        reasons = []
        # Check hard exclusion keywords
        for not_word in (validated_rules.get("must_not_contain") or []):
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
        for must_word in (validated_rules.get("must_contain") or []):
            if must_word and must_word.strip():
                mw = must_word.strip()
                if mw.lower() not in candidate_text.lower():
                    reasons.append(f"缺少必含词 [{mw}]")
                    return True, -999.0, reasons

        # Calculate score boosts
        score_adj = 0.0
        boost_map = validated_rules.get("score_boost_keywords") or {}
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
                    cls._instance._session_counts = {} # rule_id -> cumulative process hits
                    cls._buffer_lock = threading.RLock()
        return cls._instance

    def record_hit(self, rule_id: str):
        with self._buffer_lock:
            self._buffer[rule_id] = self._buffer.get(rule_id, 0) + 1
            self._session_counts[rule_id] = self._session_counts.get(rule_id, 0) + 1
            if len(self._buffer) >= 10:
                self.flush()

    def get_session_hit_count(self, rule_id: str) -> int:
        with self._buffer_lock:
            return int(self._session_counts.get(rule_id, 0))

    def reset_session_hits(self):
        with self._buffer_lock:
            self._session_counts.clear()

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


class DynamicRuleUnavailableError(RuntimeError):
    """V015-M3 R03：动态规则库读取、schema 或 DSL 解析失败时显式 fail-closed，禁止静默视作无规则。"""


class LLMRuleManager:
    """Rule CRUD, LLM compilation, audit workflow, and in-memory active rules cache."""
    _active_rules_cache = None
    _cache_lock = threading.Lock()

    @classmethod
    def clear_active_cache(cls):
        with cls._cache_lock:
            cls._active_rules_cache = None

    @classmethod
    def _resolve_verified_actor(cls, user_info: Any) -> Tuple[Optional[Dict[str, Any]], int, str]:
        """Resolve operator identity & DB role from auth.db; never trust unverified caller dicts."""
        from app.auth_manager import resolve_verified_user
        actor = resolve_verified_user(user_info)
        if not actor:
            return None, 401, "未认证或无效的操作账号，拒绝执行规则操作。"
        if actor.get("must_change_password"):
            return None, 403, "MUST_CHANGE_PASSWORD: 请先修改初始或重置密码后再操作规则。"
        return actor, 200, "ok"

    @classmethod
    def _validate_rule_payload(cls, dsl: Any, scope_level: str) -> Tuple[bool, str, str]:
        if scope_level not in ("universal", "discipline", "category"):
            return False, f"非法作用层级 '{scope_level}'。", ""
        try:
            DynamicRuleInterpreter.validate_rule_stages(dsl, rule_id="draft_validation")
        except DynamicRuleUnavailableError as exc:
            return False, str(exc), ""
        return True, "ok", json.dumps(dsl, ensure_ascii=False)

    @classmethod
    def get_active_rules(cls) -> List[Dict[str, Any]]:
        with cls._cache_lock:
            if cls._active_rules_cache is not None:
                return cls._active_rules_cache

            conn = None
            try:
                conn = _open_read_conn()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM llm_rules WHERE status = 'published' ORDER BY priority ASC")
                rows = cursor.fetchall()
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "no such table" in msg and "llm_rules" in msg and conn is not None:
                    try:
                        table_row = conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_rules'"
                        ).fetchone()
                        if table_row is None:
                            return []
                    except Exception:
                        pass
                raise DynamicRuleUnavailableError(f"动态规则库查询失败: {exc}") from exc
            except Exception as exc:
                raise DynamicRuleUnavailableError(f"动态规则库连接或读取异常: {exc}") from exc
            finally:
                if conn is not None:
                    conn.close()

            rules = []
            for r in rows:
                item = dict(r)
                rule_id = item.get("rule_id") or item.get("id", "?")
                raw_json = item.get("rule_json")
                if not isinstance(raw_json, str) or not raw_json.strip():
                    raise DynamicRuleUnavailableError(f"动态规则 {rule_id} rule_json 缺失或非字符串")
                try:
                    parsed = json.loads(raw_json)
                except Exception as exc:
                    raise DynamicRuleUnavailableError(
                        f"动态规则 {rule_id} rule_json 解析失败: {exc}"
                    ) from exc
                if not isinstance(parsed, dict):
                    raise DynamicRuleUnavailableError(f"动态规则 {rule_id} rule_json 顶层必须为 dict")
                if "matching_rules" in parsed:
                    DynamicRuleInterpreter.validate_matching_rules(
                        parsed.get("matching_rules"), rule_id=rule_id
                    )

                # M4-01 fail-closed invariant: every active published rule must have matching
                # revision/digest, distinct approver != author, and publisher provenance.
                computed_digest = compute_rule_content_digest(
                    item.get("scope_level", "category"),
                    item.get("scope_target"),
                    item.get("discipline"),
                    int(item.get("priority") if item.get("priority") is not None else 10),
                    raw_json,
                )
                approver_id = item.get("approver_id")
                author_id = item.get("author_id")
                publisher_id = item.get("publisher_id")
                if (
                    approver_id is None
                    or author_id is None
                    or int(approver_id) == int(author_id)
                    or publisher_id is None
                    or item.get("approved_revision") != item.get("revision")
                    or item.get("approved_digest") != computed_digest
                    or item.get("content_digest") != computed_digest
                ):
                    raise DynamicRuleUnavailableError(
                        f"动态规则 {rule_id} 缺少完整双人审批或发布内容摘要凭据，拒绝加载"
                    )

                item["parsed_dsl"] = parsed
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
    def test_rule_execution(
        cls,
        dsl_json: Dict[str, Any],
        sample_text: str,
        candidate_sample: str = "",
        *,
        scope_level: Optional[str] = None,
        scope_target: Optional[str] = None,
        professional: Optional[str] = None,
        material_category: Optional[str] = None,
        sample_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes DynamicRuleInterpreter against sample text with optional RuleContext scope evaluation."""
        from app.rule_context import RuleContext

        scope_result = None
        if scope_level is not None or professional is not None or material_category is not None or sample_name is not None:
            ctx = RuleContext.from_fields(
                professional=professional or "",
                material_category=material_category or "",
                name=sample_name or "",
                raw_name=sample_name or "",
                original_name=sample_name or "",
                stage="test_preview",
            )
            rule_stub = {
                "scope_level": scope_level or "universal",
                "scope_target": scope_target or "",
                "discipline": professional or "",
            }
            scope_result = ctx.evaluate_rule(rule_stub)
            if not scope_result.matched:
                return {
                    "success": True,
                    "in_scope": False,
                    "scope_reason": scope_result.reason,
                    "context": ctx.to_scope_dict(),
                    "original_text": sample_text,
                    "cleaned_text": sample_text,
                    "cleaning_logs": [],
                    "extracted_params": {},
                    "match_eval": None,
                }

        try:
            if not isinstance(dsl_json, dict):
                raise DynamicRuleUnavailableError("规则 DSL 必须为 JSON 对象（dict）。")
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
                    "reasons": match_reasons,
                }

            return {
                "success": True,
                "in_scope": True if scope_result is None else scope_result.matched,
                "scope_reason": "unscoped_preview" if scope_result is None else scope_result.reason,
                "original_text": sample_text,
                "cleaned_text": cleaned_text,
                "cleaning_logs": cleaning_logs,
                "extracted_params": extracted_params,
                "match_eval": match_result,
            }
        except Exception as exc:
            logger.error("Rule test execution failed (%s): %s", type(exc).__name__, exc)
            return {
                "success": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "in_scope": True if scope_result is None else scope_result.matched,
                "scope_reason": "unscoped_preview" if scope_result is None else scope_result.reason,
            }

    @classmethod
    def save_rule_draft(cls, rule_data: Dict[str, Any], user_info: Dict[str, Any]) -> Dict[str, Any]:
        actor, err_code, err_msg = cls._resolve_verified_actor(user_info)
        if not actor:
            return {"success": False, "status_code": err_code, "error": err_msg}

        rule_id = rule_data.get("rule_id")
        if not rule_id:
            rule_id = f"RULE-{int(time.time() * 1000)}"

        scope_level = rule_data.get("scope_level", "category")
        scope_target = rule_data.get("scope_target", "") or ""
        discipline = rule_data.get("discipline", "") or ""
        priority = int(rule_data.get("priority", 10))
        rule_name = rule_data.get("rule_name", "未命名规则")
        description = rule_data.get("description", "") or ""
        raw_dsl = rule_data.get("parsed_dsl") if "parsed_dsl" in rule_data else rule_data.get("dsl_json", {})

        ok_dsl, dsl_err, dsl_str = cls._validate_rule_payload(raw_dsl, scope_level)
        if not ok_dsl:
            return {"success": False, "status_code": 400, "error": dsl_err}

        digest = compute_rule_content_digest(scope_level, scope_target, discipline, priority, dsl_str)
        admin_proxy_edit = bool(rule_data.get("admin_proxy_edit"))

        conn = get_db_conn()
        cursor = conn.cursor()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,))
            exists = cursor.fetchone()

            if exists:
                cur_status = exists["status"]
                if cur_status == "pending_approval":
                    conn.rollback()
                    return {"success": False, "status_code": 409, "error": "规则正在待审核中，修改前请先撤销提交（withdraw）。"}
                if cur_status == "deprecated":
                    conn.rollback()
                    return {"success": False, "status_code": 409, "error": "已废弃规则不可继续修改，请新建规则。"}

                is_owner = int(exists["author_id"]) == int(actor["id"])
                if not is_owner:
                    if not (actor["role"] == "admin" and admin_proxy_edit):
                        conn.rollback()
                        return {"success": False, "status_code": 403, "error": "无权修改他人规则（管理员代编辑需显式声明 admin_proxy_edit 并变更作者）。"}

                new_rev = int(exists["revision"] or 1) + 1
                new_author_id = actor["id"] if not is_owner else exists["author_id"]
                new_author_name = actor["username"] if not is_owner else exists["author_name"]
                action = "proxy_update_draft" if not is_owner else "update_draft"

                cursor.execute(
                    """
                    UPDATE llm_rules SET
                        rule_name = ?, scope_level = ?, scope_target = ?, discipline = ?,
                        priority = ?, description = ?, rule_json = ?,
                        author_id = ?, author_name = ?,
                        status = 'draft',
                        revision = ?, content_digest = ?,
                        approver_id = NULL, approver_name = NULL, approval_comment = NULL, approved_at = NULL,
                        approved_revision = NULL, approved_digest = NULL,
                        publisher_id = NULL, publisher_name = NULL, published_at = NULL,
                        last_eval_id = '', last_eval_digest = '', neutral_publish_reason = '',
                        governance_provenance = 'pending_eval',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE rule_id = ? AND revision = ?
                    """,
                    (
                        rule_name, scope_level, scope_target, discipline,
                        priority, description, dsl_str,
                        new_author_id, new_author_name,
                        new_rev, digest,
                        rule_id, int(exists["revision"] or 1),
                    ),
                )
                if cursor.rowcount == 0:
                    conn.rollback()
                    return {"success": False, "status_code": 409, "error": "并发修订冲突，请刷新规则后重试。"}
                audit_comment = "管理员代编辑并退回草稿重新审核" if not is_owner else "保存规则草稿（已失效旧审批，需重新送审）"
            else:
                new_rev = 1
                cursor.execute(
                    """
                    INSERT INTO llm_rules (
                        rule_id, rule_name, scope_level, scope_target, discipline,
                        priority, description, rule_json, author_id, author_name,
                        status, revision, content_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?)
                    """,
                    (
                        rule_id, rule_name, scope_level, scope_target, discipline,
                        priority, description, dsl_str, actor["id"], actor["username"],
                        digest,
                    ),
                )
                action = "create_draft"
                audit_comment = "创建规则草稿"

            cursor.execute(
                """
                INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (rule_id, action, actor["id"], actor["username"], actor["role"], audit_comment),
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

        cls.clear_active_cache()
        return {
            "success": True,
            "rule_id": rule_id,
            "status": "draft",
            "revision": new_rev,
            "content_digest": digest,
            "detail": "规则草稿保存成功。",
        }

    @classmethod
    def withdraw_rule(cls, rule_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        """Author withdraws a pending_approval rule back to draft before editing."""
        actor, err_code, err_msg = cls._resolve_verified_actor(user_info)
        if not actor:
            return {"success": False, "status_code": err_code, "error": err_msg}

        conn = get_db_conn()
        cursor = conn.cursor()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,)).fetchone()
            if not row:
                conn.rollback()
                return {"success": False, "status_code": 404, "error": "规则不存在。"}
            if int(row["author_id"]) != int(actor["id"]):
                conn.rollback()
                return {"success": False, "status_code": 403, "error": "仅规则作者本人可撤销待审提交。"}
            if row["status"] != "pending_approval":
                conn.rollback()
                return {"success": False, "status_code": 409, "error": f"当前状态 '{row['status']}' 不可撤销提交。"}

            cursor.execute(
                "UPDATE llm_rules SET status = 'draft', updated_at = CURRENT_TIMESTAMP WHERE rule_id = ? AND status = 'pending_approval'",
                (rule_id,),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                return {"success": False, "status_code": 409, "error": "规则状态已变更，撤销失败。"}
            cursor.execute(
                """
                INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
                VALUES (?, 'withdraw', ?, ?, ?, '作者撤回待审规则至草稿')
                """,
                (rule_id, actor["id"], actor["username"], actor["role"]),
            )
            conn.commit()
            return {"success": True, "rule_id": rule_id, "status": "draft", "detail": "已撤回为草稿状态。"}
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    @classmethod
    def submit_rule(cls, rule_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        actor, err_code, err_msg = cls._resolve_verified_actor(user_info)
        if not actor:
            return {"success": False, "status_code": err_code, "error": err_msg}

        conn = get_db_conn()
        cursor = conn.cursor()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,)).fetchone()
            if not row:
                conn.rollback()
                return {"success": False, "status_code": 404, "error": "规则不存在。"}
            if int(row["author_id"]) != int(actor["id"]):
                conn.rollback()
                return {"success": False, "status_code": 403, "error": "仅规则作者本人可将本人草稿或退回稿提交审核。"}
            if row["status"] not in ("draft", "rejected"):
                conn.rollback()
                return {"success": False, "status_code": 409, "error": f"当前状态 '{row['status']}' 不允许提交审核，仅草稿或被退回规则可提交。"}

            try:
                parsed = json.loads(row["rule_json"])
            except Exception as exc:
                conn.rollback()
                return {"success": False, "status_code": 400, "error": f"规则 JSON 无效: {exc}"}
            ok_dsl, dsl_err, _ = cls._validate_rule_payload(parsed, row["scope_level"])
            if not ok_dsl:
                conn.rollback()
                return {"success": False, "status_code": 400, "error": dsl_err}

            digest = compute_rule_content_digest(
                row["scope_level"], row["scope_target"], row["discipline"],
                int(row["priority"] if row["priority"] is not None else 10), row["rule_json"],
            )
            cursor.execute(
                """
                UPDATE llm_rules
                SET status = 'pending_approval',
                    content_digest = ?,
                    approver_id = NULL, approver_name = NULL, approval_comment = NULL, approved_at = NULL,
                    approved_revision = NULL, approved_digest = NULL,
                    publisher_id = NULL, publisher_name = NULL, published_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE rule_id = ? AND status IN ('draft', 'rejected') AND revision = ?
                """,
                (digest, rule_id, int(row["revision"] or 1)),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                return {"success": False, "status_code": 409, "error": "并发状态冲突，提交审核失败。"}
            cursor.execute(
                """
                INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
                VALUES (?, 'submit', ?, ?, ?, '提交规则审核')
                """,
                (rule_id, actor["id"], actor["username"], actor["role"]),
            )
            conn.commit()
            return {
                "success": True,
                "rule_id": rule_id,
                "status": "pending_approval",
                "revision": int(row["revision"] or 1),
                "content_digest": digest,
                "detail": "规则已提交审核。",
            }
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    @classmethod
    def audit_rule(
        cls,
        rule_id: str,
        action: str,
        comment: str,
        user_info: Dict[str, Any],
        expected_revision: Optional[int] = None,
        expected_digest: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Approve -> approved_pending_publish (no self-review), Reject -> rejected, or Deprecate (admin-only)."""
        actor, err_code, err_msg = cls._resolve_verified_actor(user_info)
        if not actor:
            return {"success": False, "status_code": err_code, "error": err_msg}

        if action not in ("approve", "reject", "deprecate"):
            return {"success": False, "status_code": 400, "error": "未知的审核动作。"}

        if action in ("approve", "reject") and actor["role"] not in ("manager", "director", "admin"):
            return {"success": False, "status_code": 403, "error": "仅主管/总监或管理员可执行审核操作。"}
        if action == "deprecate" and actor["role"] != "admin":
            return {"success": False, "status_code": 403, "error": "仅管理员有权废弃全局规则。"}

        conn = get_db_conn()
        cursor = conn.cursor()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,)).fetchone()
            if not row:
                conn.rollback()
                return {"success": False, "status_code": 404, "error": "规则不存在。"}

            cur_rev = int(row["revision"] or 1)
            cur_digest = compute_rule_content_digest(
                row["scope_level"], row["scope_target"], row["discipline"],
                int(row["priority"] if row["priority"] is not None else 10), row["rule_json"],
            )

            if expected_revision is not None and int(expected_revision) != cur_rev:
                conn.rollback()
                return {"success": False, "status_code": 409, "error": "规则修订号已变更，请刷新后重试。"}
            if expected_digest is not None and expected_digest != cur_digest:
                conn.rollback()
                return {"success": False, "status_code": 409, "error": "规则内容摘要已变更，请刷新后重试。"}

            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            if action in ("approve", "reject"):
                if row["status"] != "pending_approval":
                    conn.rollback()
                    return {"success": False, "status_code": 409, "error": f"当前状态 '{row['status']}' 不可审核，仅待审版本可同意或退回。"}
                if int(row["author_id"]) == int(actor["id"]):
                    conn.rollback()
                    return {"success": False, "status_code": 403, "error": "禁止自审：作者（含管理员）不得审核本人提交或代编辑的规则。"}

                if action == "approve":
                    try:
                        parsed = json.loads(row["rule_json"])
                    except Exception as exc:
                        conn.rollback()
                        return {"success": False, "status_code": 400, "error": f"规则 JSON 无效: {exc}"}
                    ok_dsl, dsl_err, _ = cls._validate_rule_payload(parsed, row["scope_level"])
                    if not ok_dsl:
                        conn.rollback()
                        return {"success": False, "status_code": 400, "error": dsl_err}

                    new_status = "approved_pending_publish"
                    cursor.execute(
                        """
                        UPDATE llm_rules
                        SET status = ?,
                            content_digest = ?,
                            approver_id = ?, approver_name = ?, approval_comment = ?, approved_at = ?,
                            approved_revision = ?, approved_digest = ?,
                            publisher_id = NULL, publisher_name = NULL, published_at = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE rule_id = ? AND status = 'pending_approval' AND revision = ?
                        """,
                        (
                            new_status, cur_digest,
                            actor["id"], actor["username"], comment or "", now_str,
                            cur_rev, cur_digest,
                            rule_id, cur_rev,
                        ),
                    )
                else:
                    new_status = "rejected"
                    cursor.execute(
                        """
                        UPDATE llm_rules
                        SET status = ?,
                            approver_id = ?, approver_name = ?, approval_comment = ?, approved_at = NULL,
                            approved_revision = NULL, approved_digest = NULL,
                            publisher_id = NULL, publisher_name = NULL, published_at = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE rule_id = ? AND status = 'pending_approval' AND revision = ?
                        """,
                        (new_status, actor["id"], actor["username"], comment or "", rule_id, cur_rev),
                    )
            else:
                if row["status"] == "deprecated":
                    conn.rollback()
                    return {"success": False, "status_code": 409, "error": "该规则已处于废弃状态。"}
                new_status = "deprecated"
                cursor.execute(
                    """
                    UPDATE llm_rules
                    SET status = 'deprecated',
                        disabled_by_id = ?, disabled_by_name = ?, disabled_reason = ?, disabled_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE rule_id = ?
                    """,
                    (actor["id"], actor["username"], comment or "管理员废弃", rule_id),
                )

            if cursor.rowcount == 0:
                conn.rollback()
                return {"success": False, "status_code": 409, "error": "并发修订冲突，操作已拒绝。"}

            cursor.execute(
                """
                INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (rule_id, action, actor["id"], actor["username"], actor["role"], comment or ""),
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

        cls.clear_active_cache()
        return {
            "success": True,
            "rule_id": rule_id,
            "status": new_status,
            "revision": cur_rev,
            "content_digest": cur_digest,
            "detail": f"规则审核完成，当前状态: {new_status}",
        }

    @classmethod
    def _build_governed_spec_from_row(cls, row: Any, *, require_concrete_discipline: bool = True) -> Tuple[Dict[str, Any], str]:
        from app.rule_governance import compute_rule_digest, normalize_rule_spec

        parsed = json.loads(row["rule_json"]) if row["rule_json"] else {}
        spec = normalize_rule_spec(
            rule_source="manual_llm",
            rule_key=row["rule_id"],
            rule_type="LLM_DSL",
            revision=int(row["revision"] or 1),
            discipline=row["discipline"] or "",
            scope_level=row["scope_level"] or "category",
            scope_target=row["scope_target"] or "",
            priority=int(row["priority"] if row["priority"] is not None else 10),
            parsed_dsl=parsed,
            rule_name=row["rule_name"] or "",
            require_concrete_discipline=require_concrete_discipline,
        )
        return spec, compute_rule_digest(spec)

    @classmethod
    def start_rule_evaluation(cls, rule_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        """Start an isolated M2 sandbox evaluation for a manual LLM DSL rule (employee+ role)."""
        from app.rule_governance import EvalGateError
        from app.flywheel_sandbox import start_governed_evaluation

        actor, err_code, err_msg = cls._resolve_verified_actor(user_info)
        if not actor:
            return {"success": False, "status_code": err_code, "error": err_msg}

        conn = get_db_conn()
        try:
            row = conn.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return {"success": False, "status_code": 404, "error": "规则不存在。"}
        if row["status"] == "deprecated":
            return {"success": False, "status_code": 409, "error": "已废弃规则不可执行评测。"}

        try:
            row_dict = dict(row)
            gov_spec, gov_digest = cls._build_governed_spec_from_row(row_dict)
            task_id = start_governed_evaluation(
                rule_source="manual_llm",
                rule_key=rule_id,
                rule_type="LLM_DSL",
                normalized_spec=gov_spec,
                rule_revision=int(row_dict.get("revision") or 1),
                owner_user=actor,
                candidate_llm_rule_row=row_dict,
                db_path=_resolve_db_path(),
            )
            return {
                "success": True,
                "task_id": task_id,
                "eval_id": task_id,
                "rule_id": rule_id,
                "rule_revision": int(row_dict.get("revision") or 1),
                "payload_digest": gov_digest,
                "status": "pending",
            }
        except EvalGateError as exc:
            return {
                "success": False,
                "status_code": exc.status_code,
                "error": exc.message,
                "error_code": exc.code,
                "details": exc.details,
            }

    @classmethod
    def publish_rule(
        cls,
        rule_id: str,
        user_info: Dict[str, Any],
        expected_revision: Optional[int] = None,
        expected_digest: Optional[str] = None,
        eval_id: Optional[str] = None,
        neutral_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Admin-only activation step: promotes approved_pending_publish -> published with mandatory M2 eval gate."""
        from app.rule_governance import EvalGateError, _PUBLISH_GATE_LOCK, verify_eval_credential_for_publish

        actor, err_code, err_msg = cls._resolve_verified_actor(user_info)
        if not actor:
            return {"success": False, "status_code": err_code, "error": err_msg}
        if actor["role"] != "admin":
            return {"success": False, "status_code": 403, "error": "仅管理员（admin）有权将已审核规则发布到全局运行集。"}

        db_path = _resolve_db_path()
        with _PUBLISH_GATE_LOCK:
            conn = get_db_conn()
            cursor = conn.cursor()
            try:
                pre_row = cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,)).fetchone()
                if not pre_row:
                    return {"success": False, "status_code": 404, "error": "规则不存在。"}
                if pre_row["status"] != "approved_pending_publish":
                    return {"success": False, "status_code": 409, "error": f"当前状态 '{pre_row['status']}' 不可发布，仅已审核待发布（approved_pending_publish）规则可发布。"}
                pre_rev = int(pre_row["revision"] or 1)
                pre_digest = compute_rule_content_digest(
                    pre_row["scope_level"], pre_row["scope_target"], pre_row["discipline"],
                    int(pre_row["priority"] if pre_row["priority"] is not None else 10), pre_row["rule_json"],
                )
                if expected_revision is not None and int(expected_revision) != pre_rev:
                    return {"success": False, "status_code": 409, "error": "待发布修订号已过期，请刷新后重试。"}
                if expected_digest is not None and expected_digest != pre_digest:
                    return {"success": False, "status_code": 409, "error": "待发布规则摘要不匹配，内容可能已被修改。"}
                pre_approver = pre_row["approver_id"]
                if (
                    pre_approver is None
                    or int(pre_approver) == int(pre_row["author_id"])
                    or pre_row["approved_revision"] != pre_rev
                    or pre_row["approved_digest"] != pre_digest
                    or pre_row["content_digest"] != pre_digest
                ):
                    return {"success": False, "status_code": 409, "error": "规则审批修订或内容摘要凭据无效/不一致，禁止发布。"}

                try:
                    gov_spec, gov_digest = cls._build_governed_spec_from_row(pre_row, require_concrete_discipline=True)
                    ev = verify_eval_credential_for_publish(
                        eval_id=eval_id,
                        rule_source="manual_llm",
                        rule_key=rule_id,
                        rule_revision=pre_rev,
                        normalized_spec=gov_spec,
                        neutral_reason=neutral_reason,
                        db_path=db_path,
                    )
                except EvalGateError as exc:
                    return {
                        "success": False,
                        "status_code": exc.status_code,
                        "error": exc.message,
                        "error_code": exc.code,
                        "details": exc.details,
                    }

                conn.execute("BEGIN IMMEDIATE")
                row = cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,)).fetchone()
                if not row:
                    conn.rollback()
                    return {"success": False, "status_code": 404, "error": "规则不存在。"}
                if row["status"] != "approved_pending_publish":
                    conn.rollback()
                    return {"success": False, "status_code": 409, "error": f"并发状态冲突：当前状态 '{row['status']}' 不可发布。"}

                cur_rev = int(row["revision"] or 1)
                cur_digest = compute_rule_content_digest(
                    row["scope_level"], row["scope_target"], row["discipline"],
                    int(row["priority"] if row["priority"] is not None else 10), row["rule_json"],
                )
                _, cur_gov_digest = cls._build_governed_spec_from_row(row)
                if cur_rev != pre_rev or cur_digest != pre_digest or cur_gov_digest != ev["payload_digest"]:
                    conn.rollback()
                    return {"success": False, "status_code": 409, "error": "并发修订或内容冲突：规则在校验后发生变更，旧评测已失效。"}

                clean_neutral = str(neutral_reason or "").strip() if ev.get("benefit_class") == "neutral" else ""
                cursor.execute(
                    """
                    UPDATE llm_rules
                    SET status = 'published',
                        publisher_id = ?, publisher_name = ?, published_at = CURRENT_TIMESTAMP,
                        disabled_by_id = NULL, disabled_by_name = NULL, disabled_reason = NULL, disabled_at = NULL,
                        last_eval_id = ?, last_eval_digest = ?, neutral_publish_reason = ?,
                        governance_provenance = 'verified',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE rule_id = ?
                      AND status = 'approved_pending_publish'
                      AND revision = ?
                      AND approved_revision = ?
                      AND approved_digest = ?
                    """,
                    (
                        actor["id"], actor["username"],
                        ev["eval_id"], ev["payload_digest"], clean_neutral,
                        rule_id, cur_rev, cur_rev, cur_digest,
                    ),
                )
                if cursor.rowcount == 0:
                    conn.rollback()
                    return {"success": False, "status_code": 409, "error": "并发冲突：规则状态或摘要已变更，发布已拒绝。"}

                audit_comment = f"管理员核验审批与评测凭据({ev['eval_id']}, benefit={ev['benefit_class']})并发布生效"
                if clean_neutral:
                    audit_comment += f" [中性启用理由: {clean_neutral}]"
                cursor.execute(
                    """
                    INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
                    VALUES (?, 'publish', ?, ?, ?, ?)
                    """,
                    (rule_id, actor["id"], actor["username"], actor["role"], audit_comment),
                )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                conn.close()

            cls.clear_active_cache()
            return {
                "success": True,
                "rule_id": rule_id,
                "status": "published",
                "revision": cur_rev,
                "content_digest": cur_digest,
                "eval_id": ev["eval_id"],
                "benefit_class": ev["benefit_class"],
                "detail": "规则已成功发布生效！",
            }

    @classmethod
    def get_rule_by_id(cls, rule_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,))
            row = cursor.fetchone()
        finally:
            try:
                conn.close()
            except Exception:
                pass
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
        actor, err_code, err_msg = cls._resolve_verified_actor(user_info)
        if not actor:
            return {"success": False, "status_code": err_code, "error": err_msg}

        admin_proxy_edit = bool(update_data.get("admin_proxy_edit"))
        expected_revision = update_data.get("expected_revision")

        conn = get_db_conn()
        cursor = conn.cursor()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,)).fetchone()
            if not row:
                conn.rollback()
                return {"success": False, "status_code": 404, "error": "规则不存在。"}

            cur_status = row["status"]
            if cur_status == "pending_approval":
                conn.rollback()
                return {"success": False, "status_code": 409, "error": "待审核中的规则不可直接修改，请先撤销提交。"}
            if cur_status == "deprecated":
                conn.rollback()
                return {"success": False, "status_code": 409, "error": "已废弃规则不可修改。"}

            is_owner = int(row["author_id"]) == int(actor["id"])
            if not is_owner:
                if not (actor["role"] == "admin" and admin_proxy_edit):
                    conn.rollback()
                    return {"success": False, "status_code": 403, "error": "无权修改他人规则（管理员代编辑需显式启用 admin_proxy_edit）。"}

            cur_rev = int(row["revision"] or 1)
            if expected_revision is not None and int(expected_revision) != cur_rev:
                conn.rollback()
                return {"success": False, "status_code": 409, "error": "规则修订号已变更，请刷新后重试。"}

            raw_dsl = update_data.get("parsed_dsl") or update_data.get("dsl_json")
            if raw_dsl is None:
                try:
                    raw_dsl = json.loads(row["rule_json"])
                except Exception:
                    raw_dsl = {}
            scope_level = update_data.get("scope_level", row["scope_level"])
            ok_dsl, dsl_err, dsl_str = cls._validate_rule_payload(raw_dsl, scope_level)
            if not ok_dsl:
                conn.rollback()
                return {"success": False, "status_code": 400, "error": dsl_err}

            rule_name = update_data.get("rule_name", row["rule_name"])
            scope_target = update_data.get("scope_target", row["scope_target"]) or ""
            discipline = update_data.get("discipline", row["discipline"]) or ""
            priority = int(update_data.get("priority", row["priority"] if row["priority"] is not None else 10))
            description = update_data.get("description", row["description"]) or ""

            new_digest = compute_rule_content_digest(scope_level, scope_target, discipline, priority, dsl_str)
            new_rev = cur_rev + 1
            new_author_id = actor["id"] if not is_owner else row["author_id"]
            new_author_name = actor["username"] if not is_owner else row["author_name"]

            # Editing ANY execution field always invalidates approval/publication and returns rule to draft.
            cursor.execute(
                """
                UPDATE llm_rules SET
                    rule_name = ?, scope_level = ?, scope_target = ?, discipline = ?,
                    priority = ?, description = ?, rule_json = ?,
                    author_id = ?, author_name = ?,
                    status = 'draft',
                    revision = ?, content_digest = ?,
                    approver_id = NULL, approver_name = NULL, approval_comment = NULL, approved_at = NULL,
                    approved_revision = NULL, approved_digest = NULL,
                    publisher_id = NULL, publisher_name = NULL, published_at = NULL,
                    last_eval_id = '', last_eval_digest = '', neutral_publish_reason = '',
                    governance_provenance = 'pending_eval',
                    updated_at = CURRENT_TIMESTAMP
                WHERE rule_id = ? AND revision = ?
                """,
                (
                    rule_name, scope_level, scope_target, discipline,
                    priority, description, dsl_str,
                    new_author_id, new_author_name,
                    new_rev, new_digest,
                    rule_id, cur_rev,
                ),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                return {"success": False, "status_code": 409, "error": "并发修改冲突，请刷新后重试。"}

            action = "proxy_update" if not is_owner else "update"
            comment = "管理员代编辑规则内容，清除旧审批并回退为草稿" if not is_owner else "修改规则内容与参数，旧审批已失效并回退草稿"
            cursor.execute(
                """
                INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (rule_id, action, actor["id"], actor["username"], actor["role"], comment),
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

        cls.clear_active_cache()
        return {
            "success": True,
            "rule_id": rule_id,
            "status": "draft",
            "revision": new_rev,
            "content_digest": new_digest,
            "detail": "规则修改成功，已退回草稿状态，重新提交审核及管理员发布后方可生效。",
        }

    @classmethod
    def toggle_rule_status(
        cls,
        rule_id: str,
        user_info: Dict[str, Any],
        reason: str = "",
        eval_id: Optional[str] = None,
        neutral_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Admin-only toggle between published <-> disabled; re-enabling enforces dual provenance and M2 eval gate."""
        from app.rule_governance import EvalGateError, _PUBLISH_GATE_LOCK, verify_eval_credential_for_publish

        actor, err_code, err_msg = cls._resolve_verified_actor(user_info)
        if not actor:
            return {"success": False, "status_code": err_code, "error": err_msg}
        if actor["role"] != "admin":
            return {"success": False, "status_code": 403, "error": "仅管理员（admin）有权启停全局规则。"}

        db_path = _resolve_db_path()
        with _PUBLISH_GATE_LOCK:
            conn = get_db_conn()
            cursor = conn.cursor()
            try:
                pre_row = cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,)).fetchone()
                if not pre_row:
                    return {"success": False, "status_code": 404, "error": "规则不存在。"}

                verified_eval = None
                effective_neutral_reason = ""
                if pre_row["status"] == "disabled":
                    pre_rev = int(pre_row["revision"] or 1)
                    pre_digest = compute_rule_content_digest(
                        pre_row["scope_level"], pre_row["scope_target"], pre_row["discipline"],
                        int(pre_row["priority"] if pre_row["priority"] is not None else 10), pre_row["rule_json"],
                    )
                    approver_id = pre_row["approver_id"]
                    if (
                        approver_id is None
                        or int(approver_id) == int(pre_row["author_id"])
                        or pre_row["approved_revision"] != pre_rev
                        or pre_row["approved_digest"] != pre_digest
                        or pre_row["content_digest"] != pre_digest
                    ):
                        return {
                            "success": False,
                            "status_code": 409,
                            "error": "该停用规则缺少有效双人审批与摘要凭据（如历史迁移停用规则），请修订并重新审核发布。",
                        }
                    target_eval_id = str(eval_id or pre_row["last_eval_id"] or "").strip()
                    effective_neutral_reason = str(
                        neutral_reason if neutral_reason is not None else (pre_row["neutral_publish_reason"] or "")
                    ).strip()
                    try:
                        gov_spec, _ = cls._build_governed_spec_from_row(pre_row, require_concrete_discipline=True)
                        verified_eval = verify_eval_credential_for_publish(
                            eval_id=target_eval_id,
                            rule_source="manual_llm",
                            rule_key=rule_id,
                            rule_revision=pre_rev,
                            normalized_spec=gov_spec,
                            neutral_reason=effective_neutral_reason,
                            db_path=db_path,
                        )
                    except EvalGateError as exc:
                        return {
                            "success": False,
                            "status_code": exc.status_code,
                            "error": exc.message,
                            "error_code": exc.code,
                            "details": exc.details,
                        }

                conn.execute("BEGIN IMMEDIATE")
                row = cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,)).fetchone()
                if not row:
                    conn.rollback()
                    return {"success": False, "status_code": 404, "error": "规则不存在。"}

                current_status = row["status"]
                cur_rev = int(row["revision"] or 1)
                if current_status == "published":
                    new_status = "disabled"
                    action_desc = "停用规则"
                    cursor.execute(
                        """
                        UPDATE llm_rules
                        SET status = 'disabled',
                            disabled_by_id = ?, disabled_by_name = ?, disabled_reason = ?, disabled_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE rule_id = ? AND status = 'published' AND revision = ?
                        """,
                        (actor["id"], actor["username"], reason or "管理员停用规则", rule_id, cur_rev),
                    )
                elif current_status == "disabled":
                    if verified_eval is None:
                        conn.rollback()
                        return {"success": False, "status_code": 409, "error": "并发状态冲突，请重新核验评测凭据后启用。"}
                    cur_digest = compute_rule_content_digest(
                        row["scope_level"], row["scope_target"], row["discipline"],
                        int(row["priority"] if row["priority"] is not None else 10), row["rule_json"],
                    )
                    _, cur_gov_digest = cls._build_governed_spec_from_row(row)
                    if cur_rev != int(verified_eval["rule_revision"]) or cur_gov_digest != verified_eval["payload_digest"]:
                        conn.rollback()
                        return {"success": False, "status_code": 409, "error": "并发修订冲突，规则内容已变更。"}
                    new_status = "published"
                    action_desc = "重新启用规则"
                    cursor.execute(
                        """
                        UPDATE llm_rules
                        SET status = 'published',
                            publisher_id = COALESCE(publisher_id, ?),
                            publisher_name = COALESCE(publisher_name, ?),
                            published_at = COALESCE(published_at, CURRENT_TIMESTAMP),
                            disabled_by_id = NULL, disabled_by_name = NULL, disabled_reason = NULL, disabled_at = NULL,
                            last_eval_id = ?, last_eval_digest = ?, neutral_publish_reason = ?,
                            governance_provenance = 'verified',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE rule_id = ? AND status = 'disabled' AND revision = ? AND approved_digest = ?
                        """,
                        (
                            actor["id"], actor["username"],
                            verified_eval["eval_id"], verified_eval["payload_digest"], effective_neutral_reason,
                            rule_id, cur_rev, cur_digest,
                        ),
                    )
                else:
                    conn.rollback()
                    return {
                        "success": False,
                        "status_code": 409,
                        "error": f"状态 '{current_status}' 不可直接启停；未审核/待发布规则需先经审核与发布流程。",
                    }

                if cursor.rowcount == 0:
                    conn.rollback()
                    return {"success": False, "status_code": 409, "error": "并发状态冲突，操作失败。"}

                cursor.execute(
                    """
                    INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (rule_id, f"toggle_{new_status}", actor["id"], actor["username"], actor["role"], reason or action_desc),
                )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                conn.close()

            cls.clear_active_cache()
            return {"success": True, "new_status": new_status, "detail": f"规则已成功{action_desc}！"}

    @classmethod
    def delete_rule(cls, rule_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        actor, err_code, err_msg = cls._resolve_verified_actor(user_info)
        if not actor:
            return {"success": False, "status_code": err_code, "error": err_msg}

        conn = get_db_conn()
        cursor = conn.cursor()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = cursor.execute("SELECT * FROM llm_rules WHERE rule_id = ?", (rule_id,)).fetchone()
            if not row:
                conn.rollback()
                return {"success": False, "status_code": 404, "error": "规则不存在。"}

            if int(row["author_id"]) != int(actor["id"]):
                conn.rollback()
                return {"success": False, "status_code": 403, "error": "仅作者本人可删除自己未提交/被退回的草稿规则。"}

            if row["status"] not in ("draft", "rejected"):
                conn.rollback()
                return {
                    "success": False,
                    "status_code": 409,
                    "error": f"状态 '{row['status']}' 的规则禁止删除（已提交/已发布/停用/废弃规则请保留审计记录）。",
                }

            cursor.execute("DELETE FROM llm_rules WHERE rule_id = ? AND status IN ('draft', 'rejected')", (rule_id,))
            if cursor.rowcount == 0:
                conn.rollback()
                return {"success": False, "status_code": 409, "error": "规则状态已变化，删除失败。"}
            cursor.execute(
                """
                INSERT INTO rule_audit_logs (rule_id, action, operator_id, operator_name, operator_role, comment)
                VALUES (?, 'delete', ?, ?, ?, '作者删除草稿/退回稿规则')
                """,
                (rule_id, actor["id"], actor["username"], actor["role"]),
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
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
                sql += " AND status IN ('published', 'disabled')"
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
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM llm_rules WHERE status = 'published'")
            published_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM llm_rules WHERE status = 'pending_approval'")
            pending_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM llm_rules WHERE status = 'approved_pending_publish'")
            pending_publish_count = cursor.fetchone()[0]
            cursor.execute("SELECT SUM(hit_count) FROM llm_rules")
            total_hits = cursor.fetchone()[0] or 0

            cursor.execute(
                "SELECT rule_id, rule_name, discipline, scope_target, hit_count, author_name, approved_at, published_at FROM llm_rules WHERE status = 'published' ORDER BY hit_count DESC LIMIT 10"
            )
            top_rules = [dict(r) for r in cursor.fetchall()]

            return {
                "approved_count": published_count,
                "published_count": published_count,
                "pending_count": pending_count,
                "pending_publish_count": pending_publish_count,
                "total_hits": total_hits,
                "top_rules": top_rules,
            }
        finally:
            try:
                conn.close()
            except Exception:
                pass
