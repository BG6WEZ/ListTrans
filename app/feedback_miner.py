# -*- coding: utf-8 -*-
"""
Smart Mining Engine for Data Flywheel (app/feedback_miner.py).
Implements two-phase scanning, domain dictionary alignment, abbreviation support,
multi-candidate conflict handling, and multi-tier statistical gating (Support, Distinct Users, Consistency).
"""
import os
import json
import re
import logging
from typing import Dict, Any, List, Tuple, Set, Optional
from app.db_manager import get_db_cursor
from app.normalizer import alias_dict, material_dict, connection_dict, flags_dict
from app.preprocessor import clean_text

logger = logging.getLogger("listtrans_app")

# Load domain dictionary keywords
def get_domain_vocab() -> Set[str]:
    vocab = set()
    for d in [alias_dict, material_dict, connection_dict, flags_dict]:
        if isinstance(d, dict):
            for k in d.keys():
                if isinstance(k, str) and len(k) >= 2:
                    vocab.add(k.strip())
    # Add common engineering material terms
    common_terms = [
        "排风机", "送风机", "新风机组", "风机箱", "离心风机", "轴流风机", "混流风机",
        "防火阀", "调节阀", "止回阀", "蝶阀", "闸阀", "截止阀", "球阀", "减压阀", "安全阀",
        "风口", "百叶风口", "散流器", "旋流风口", "消声器", "静压箱", "过滤器", "高效过滤器",
        "配电箱", "控制箱", "动力箱", "照明箱", "电缆", "电线", "桥架", "母线槽",
        "无缝钢管", "镀锌钢管", "焊接钢管", "紫铜管", "不锈钢管", "PPR管", "PVC管", "UPVC管"
    ]
    vocab.update(common_terms)
    return vocab

# Maximum forward matching tokenizer based on domain vocabulary
def domain_tokenize(text: str, vocab: Set[str]) -> List[str]:
    if not text:
        return []
    text = text.strip()
    tokens = []
    i = 0
    max_len = 12
    while i < len(text):
        matched = False
        for l in range(min(max_len, len(text) - i), 1, -1):
            chunk = text[i:i+l]
            if chunk in vocab:
                tokens.append(chunk)
                i += l
                matched = True
                break
        if not matched:
            # Check for English abbreviation / code token (e.g. AP-1, PAF, AL, DN50)
            m = re.match(r'^[a-zA-Z0-9\-]+', text[i:])
            if m:
                tokens.append(m.group(0))
                i += len(m.group(0))
            else:
                tokens.append(text[i])
                i += 1
    return tokens

from app.normalizer import to_half_width

# Extract candidate diff pairs
def extract_alias_candidate(raw_name: str, target_name: str, vocab: Set[str]) -> Optional[Tuple[str, str]]:
    raw_norm = to_half_width(raw_name).strip()
    target_norm = to_half_width(target_name).strip()
    if not raw_norm or not target_norm or raw_norm.lower() == target_norm.lower():
        return None
    
    # 1. Check if raw_name is an English device abbreviation (e.g. "AP-1", "PAF", "ACP", "AL")
    abbrev_match = re.match(r'^[a-zA-Z0-9\-]{2,12}$', raw_norm)
    if abbrev_match:
        token = abbrev_match.group(0)
        # Filter pure numeric or pure DN specs
        if not re.match(r'^\d+$', token) and not re.match(r'^(dn|de|mm|kw|pa)\d+$', token, re.IGNORECASE):
            return (token, target_name)
            
    # 2. Token alignment
    raw_tokens = domain_tokenize(raw_norm, vocab)
    target_tokens = domain_tokenize(target_norm, vocab)
    
    # Find distinct difference
    diff_raw = [t for t in raw_tokens if t not in target_tokens]
    diff_target = [t for t in target_tokens if t not in raw_tokens]
    
    if len(diff_raw) == 1 and len(diff_target) == 1:
        src = diff_raw[0]
        dst = diff_target[0]
        if 2 <= len(src) <= 12 and not re.match(r'^\d+$', src) and not re.match(r'^(个|米|台|套|kg|m|mm|dn)$', src, re.IGNORECASE):
            return (src, dst)
            
    # Fallback to concise meaningful nouns (length 2~16, non-numeric)
    if 2 <= len(raw_norm) <= 16 and not re.match(r'^\d+$', raw_norm) and not re.match(r'^(dn|de|mm|kw|pa)\d+$', raw_norm, re.IGNORECASE):
        return (raw_norm, target_name)
        
    return None

class FlywheelMiner:
    """Core mining engine implementing two-phase scan, multi-candidate tracking and statistical aggregation."""
    
    def __init__(self):
        self.vocab = get_domain_vocab()

    def run_mining(self) -> Dict[str, Any]:
        """Execute incremental discovery and full-table statistical aggregation with transactional safety."""
        unprocessed_ids = []
        try:
            # 1. Phase 1: Incremental scan to discover candidate patterns
            with get_db_cursor(commit=True) as cur:
                cur.execute("""
                SELECT id, raw_name, raw_name_norm, action_type, final_targets_json
                FROM flywheel_records
                WHERE is_processed = 0 AND superseded = 0
                """)
                rows = cur.fetchall()
                for r in rows:
                    unprocessed_ids.append(r["id"])
                    
                if unprocessed_ids:
                    cur.execute(f"UPDATE flywheel_records SET is_processed = 1 WHERE id IN ({','.join(['?']*len(unprocessed_ids))})", unprocessed_ids)
            
            # 2. Phase 2: Full-table statistics for active records
            with get_db_cursor(commit=True) as cur:
                cur.execute("""
                SELECT raw_name_norm, COUNT(*) as total_actions
                FROM flywheel_records
                WHERE superseded = 0
                GROUP BY raw_name_norm
                HAVING total_actions >= 2
                """)
                active_patterns = [row["raw_name_norm"] for row in cur.fetchall()]
                
                for raw_norm in active_patterns:
                    # Analyze ALIAS suggestions
                    self._mine_alias_suggestions(cur, raw_norm)
                    # Analyze BINDING_RULE suggestions
                    self._mine_binding_suggestions(cur, raw_norm)
                    
                # 3. Aggregate parameter adjustments into aggregates table (P1-4 & P1-5)
                self._aggregate_parameter_adjustments(cur)
                    
            return {
                "processed_records_count": len(unprocessed_ids),
                "analyzed_patterns_count": len(active_patterns),
                "status": "success"
            }
        except Exception as e:
            logger.error(f"Error during flywheel mining execution: {e}")
            # Reset is_processed flags on failure (P2-4)
            if unprocessed_ids:
                try:
                    with get_db_cursor(commit=True) as reset_cur:
                        reset_cur.execute(f"UPDATE flywheel_records SET is_processed = 0 WHERE id IN ({','.join(['?']*len(unprocessed_ids))})", unprocessed_ids)
                except Exception as re_err:
                    logger.error(f"Failed to reset is_processed flags: {re_err}")
            raise e

    def _mine_alias_suggestions(self, cur, raw_norm: str):
        """Aggregate alias mapping candidates for a specific raw_name_norm."""
        cur.execute("""
        SELECT COUNT(*) as denom
        FROM flywheel_records
        WHERE raw_name_norm = ? AND superseded = 0 AND action_type IN ('MANUAL_REPLACE', 'PICK_CANDIDATE')
        """, (raw_norm,))
        denom_row = cur.fetchone()
        denominator = denom_row["denom"] if denom_row else 0
        if denominator == 0:
            return

        cur.execute("""
        SELECT raw_name, final_targets_json, user_id, id
        FROM flywheel_records
        WHERE raw_name_norm = ? AND superseded = 0 AND action_type IN ('MANUAL_REPLACE', 'PICK_CANDIDATE')
        """, (raw_norm,))
        records = cur.fetchall()

        target_stats: Dict[str, Dict[str, Any]] = {}
        for r in records:
            targets = json.loads(r["final_targets_json"] or "[]")
            if not targets:
                continue
            primary_target = targets[0]
            target_name = primary_target.get("name", "").strip()
            if not target_name:
                continue
                
            pair = extract_alias_candidate(r["raw_name"], target_name, self.vocab)
            if not pair:
                continue
            src_pattern, target_val = pair
            
            key = f"{src_pattern}__->_{target_val}"
            if key not in target_stats:
                target_stats[key] = {
                    "src": src_pattern,
                    "target": target_val,
                    "count": 0,
                    "users": set(),
                    "sample_ids": []
                }
            target_stats[key]["count"] += 1
            if r["user_id"] != "legacy_anonymous":
                target_stats[key]["users"].add(r["user_id"])
            target_stats[key]["sample_ids"].append(r["id"])

        for stat in target_stats.values():
            support = stat["count"]
            distinct_users = len(stat["users"]) if stat["users"] else 1
            consistency = round(float(support) / float(denominator), 4)
            
            status = None
            if support >= 3 and distinct_users >= 2 and consistency >= 0.80:
                status = "pending"
            elif support >= 2:
                status = "observing"
                
            if status:
                self._upsert_suggestion(
                    cur,
                    suggestion_type="ALIAS",
                    source_pattern=stat["src"],
                    target_value=stat["target"],
                    discipline="通用",
                    support_count=support,
                    distinct_users=distinct_users,
                    consistency=consistency,
                    sample_ids=stat["sample_ids"],
                    status=status
                )

    def _mine_binding_suggestions(self, cur, raw_norm: str):
        """Aggregate 1-to-N binding rules."""
        cur.execute("""
        SELECT COUNT(*) as denom
        FROM flywheel_records
        WHERE raw_name_norm = ? AND superseded = 0 AND action_type = 'BIND_MULTI'
        """, (raw_norm,))
        denom_row = cur.fetchone()
        denominator = denom_row["denom"] if denom_row else 0
        if denominator < 2:
            return

        cur.execute("""
        SELECT raw_name, final_targets_json, user_id, id
        FROM flywheel_records
        WHERE raw_name_norm = ? AND superseded = 0 AND action_type = 'BIND_MULTI'
        """, (raw_norm,))
        records = cur.fetchall()

        binding_groups: Dict[str, Dict[str, Any]] = {}
        for r in records:
            targets = json.loads(r["final_targets_json"] or "[]")
            if len(targets) <= 1:
                continue
            sorted_targets = sorted(targets, key=lambda x: x.get("code", ""))
            serialized = json.dumps(sorted_targets, ensure_ascii=False)
            
            if serialized not in binding_groups:
                binding_groups[serialized] = {
                    "raw_name": r["raw_name"],
                    "targets_json": serialized,
                    "count": 0,
                    "users": set(),
                    "sample_ids": []
                }
            binding_groups[serialized]["count"] += 1
            if r["user_id"] != "legacy_anonymous":
                binding_groups[serialized]["users"].add(r["user_id"])
            binding_groups[serialized]["sample_ids"].append(r["id"])

        for bg in binding_groups.values():
            support = bg["count"]
            distinct_users = len(bg["users"]) if bg["users"] else 1
            consistency = round(float(support) / float(denominator), 4)
            
            status = "pending" if (support >= 3 and distinct_users >= 2 and consistency >= 0.80) else "observing"
            
            self._upsert_suggestion(
                cur,
                suggestion_type="BINDING_RULE",
                source_pattern=bg["raw_name"],
                target_value=bg["targets_json"],
                discipline="通用",
                support_count=support,
                distinct_users=distinct_users,
                consistency=consistency,
                sample_ids=bg["sample_ids"],
                status=status
            )

    def _aggregate_parameter_adjustments(self, cur):
        """Aggregate parameter adjustment trends for long-term tracking (P1-4, P1-5)."""
        cur.execute("""
        SELECT raw_name_norm, extracted_params, COUNT(*) as cnt
        FROM flywheel_records
        WHERE action_type = 'PARAM_ADJUST' AND superseded = 0
        GROUP BY raw_name_norm
        HAVING cnt >= 2
        """)
        rows = cur.fetchall()
        for r in rows:
            cur.execute("""
            INSERT OR REPLACE INTO flywheel_pattern_aggregates (
                pattern_key, discipline, total_count, correct_count, manual_replace_count,
                param_adjust_count, dominant_target_code, sample_ids_json
            ) VALUES (?, '通用', ?, 0, 0, ?, '', '[]')
            """, (r["raw_name_norm"], r["cnt"], r["cnt"]))

    def _upsert_suggestion(self, cur, suggestion_type: str, source_pattern: str, target_value: str,
                           discipline: str, support_count: int, distinct_users: int, consistency: float,
                           sample_ids: List[int], status: str):
        """Insert or update rule suggestions table with multi-candidate tracking (P2-2)."""
        cur.execute("""
        SELECT id, status, support_count FROM flywheel_rule_suggestions
        WHERE suggestion_type = ? AND source_pattern = ? AND target_value = ?
        """, (suggestion_type, source_pattern, target_value))
        exact_existing = cur.fetchone()
        
        sample_ids_json = json.dumps(sample_ids)
        if exact_existing:
            current_status = exact_existing["status"]
            new_status = current_status if current_status in ("accepted", "rejected") else status
            cur.execute("""
            UPDATE flywheel_rule_suggestions
            SET support_count = ?, distinct_users = ?, consistency = ?,
                sample_ids_json = ?, status = ?
            WHERE id = ?
            """, (support_count, distinct_users, consistency, sample_ids_json, new_status, exact_existing["id"]))
        else:
            cur.execute("""
            INSERT INTO flywheel_rule_suggestions (
                suggestion_type, source_pattern, target_value, discipline,
                support_count, distinct_users, consistency, sample_ids_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (suggestion_type, source_pattern, target_value, discipline, support_count, distinct_users, consistency, sample_ids_json, status))

miner = FlywheelMiner()
