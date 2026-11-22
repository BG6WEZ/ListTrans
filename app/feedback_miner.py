# -*- coding: utf-8 -*-
"""
Smart Mining Engine for Data Flywheel (app/feedback_miner.py).
Implements single-transaction incremental discovery, strict trusted-effective collection
boundary, discipline-scoped rule context isolation, and unified parameter aggregation.
"""
from collections import Counter
import json
import logging
import re
import sqlite3
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.runtime_jobs import checkpoint
from app.flywheel_db import (
    init_flywheel_tables,
    open_flywheel_tx_connection,
    trusted_flywheel_where_clause,
)
from app.normalizer import alias_dict, connection_dict, flags_dict, material_dict, to_half_width
from app.rule_context import DYNAMIC_UNIVERSAL_TOKENS, RuleContext

logger = logging.getLogger("listtrans_app")
_MINING_LOCK = threading.Lock()


def get_domain_vocab() -> Set[str]:
    vocab = set()
    for d in [alias_dict, material_dict, connection_dict, flags_dict]:
        if isinstance(d, dict):
            for k in d.keys():
                if isinstance(k, str) and len(k) >= 2:
                    vocab.add(k.strip())
    common_terms = [
        "排风机", "送风机", "新风机组", "风机箱", "离心风机", "轴流风机", "混流风机",
        "防火阀", "调节阀", "止回阀", "蝶阀", "闸阀", "截止阀", "球阀", "减压阀", "安全阀",
        "风口", "百叶风口", "散流器", "旋流风口", "消声器", "静压箱", "过滤器", "高效过滤器",
        "配电箱", "控制箱", "动力箱", "照明箱", "电缆", "电线", "桥架", "母线槽",
        "无缝钢管", "镀锌钢管", "焊接钢管", "紫铜管", "不锈钢管", "PPR管", "PVC管", "UPVC管",
    ]
    vocab.update(common_terms)
    return vocab


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
            chunk = text[i:i + l]
            if chunk in vocab:
                tokens.append(chunk)
                i += l
                matched = True
                break
        if not matched:
            m = re.match(r"^[a-zA-Z0-9\-]+", text[i:])
            if m:
                tokens.append(m.group(0))
                i += len(m.group(0))
            else:
                tokens.append(text[i])
                i += 1
    return tokens


def extract_alias_candidate(raw_name: str, target_name: str, vocab: Set[str]) -> Optional[Tuple[str, str]]:
    raw_norm = to_half_width(raw_name).strip()
    target_norm = to_half_width(target_name).strip()
    if not raw_norm or not target_norm or raw_norm.lower() == target_norm.lower():
        return None

    abbrev_match = re.match(r"^[a-zA-Z0-9\-]{2,12}$", raw_norm)
    if abbrev_match:
        token = abbrev_match.group(0)
        if not re.match(r"^\d+$", token) and not re.match(r"^(dn|de|mm|kw|pa)\d+$", token, re.IGNORECASE):
            return (token, target_name)

    raw_tokens = domain_tokenize(raw_norm, vocab)
    target_tokens = domain_tokenize(target_norm, vocab)

    diff_raw = [t for t in raw_tokens if t not in target_tokens]
    diff_target = [t for t in target_tokens if t not in raw_tokens]

    if len(diff_raw) == 1 and len(diff_target) == 1:
        src = diff_raw[0]
        dst = diff_target[0]
        if 2 <= len(src) <= 12 and not re.match(r"^\d+$", src) and not re.match(r"^(个|米|台|套|kg|m|mm|dn)$", src, re.IGNORECASE):
            return (src, dst)

    if 2 <= len(raw_norm) <= 16 and not re.match(r"^\d+$", raw_norm) and not re.match(r"^(dn|de|mm|kw|pa)\d+$", raw_norm, re.IGNORECASE):
        return (raw_norm, target_name)

    return None


def _parse_json_field(raw_val: Any, expected_type: type, record_id: Any, field_name: str) -> Any:
    if raw_val is None or raw_val == "":
        return expected_type()
    if isinstance(raw_val, expected_type):
        return raw_val
    try:
        parsed = json.loads(raw_val)
    except Exception as exc:
        raise ValueError(f"flywheel_records id={record_id} {field_name} JSON 解析失败") from exc
    if not isinstance(parsed, expected_type):
        raise ValueError(f"flywheel_records id={record_id} {field_name} 类型非法: 期望 {expected_type.__name__}")
    return parsed


def _actor_identity_key(row: sqlite3.Row) -> str:
    actor_uid = row["actor_user_id"]
    actor_epoch = str(row["actor_account_epoch"] or "").strip()
    if actor_uid is None or int(actor_uid) <= 0 or not actor_epoch:
        raise ValueError(f"flywheel_records id={row['id']} 缺少服务端不可变 actor 身份")
    return f"{int(actor_uid)}:{actor_epoch}"


def _resolve_record_rule_context(row: sqlite3.Row) -> Optional[RuleContext]:
    raw_disc = str(row["discipline"] or row["source_professional"] or "").strip()
    ctx = RuleContext.from_fields(
        professional=raw_disc,
        material_category=str(row["raw_category"] or "").strip(),
        name=str(row["raw_name"] or "").strip(),
        raw_name=str(row["raw_name"] or "").strip(),
        stage="mining",
    )
    disc = ctx.normalized_professional
    if not disc or disc.lower() in DYNAMIC_UNIVERSAL_TOKENS or disc == "其它":
        return None
    return ctx


class FlywheelMiner:
    """Core mining engine implementing atomic transaction, trusted boundary, and RuleContext isolation."""

    def __init__(self):
        self.vocab = get_domain_vocab()

    def run_mining(
        self,
        db_path: Optional[str] = None,
        _tx_hook: Optional[Callable[[str, sqlite3.Cursor], None]] = None,
    ) -> Dict[str, Any]:
        """Execute incremental discovery and full-table statistical aggregation in one atomic transaction."""
        init_flywheel_tables(db_path=db_path)

        with _MINING_LOCK:
            conn = open_flywheel_tx_connection(db_path=db_path)
            unprocessed_ids: List[int] = []
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.cursor()
                trusted_where = trusted_flywheel_where_clause("fr")

                # 1. Incremental scan over verified trusted effective records only
                cur.execute(
                    f"""
                    SELECT fr.id
                    FROM flywheel_records fr
                    JOIN flywheel_source_rows sr
                      ON sr.source_row_id = fr.source_row_id
                     AND sr.document_id = fr.document_id
                     AND sr.sheet_id = fr.sheet_id
                    WHERE fr.is_processed = 0 AND {trusted_where}
                    ORDER BY fr.id ASC
                    """
                )
                unprocessed_ids = [int(r["id"]) for r in cur.fetchall()]

                # 2. Consistent full-table read over the exact same trusted effective boundary
                cur.execute(
                    f"""
                    SELECT fr.id, fr.discipline, fr.raw_name, fr.raw_name_norm, fr.raw_spec,
                           fr.raw_category, fr.extracted_params, fr.action_type,
                           fr.final_targets_json, fr.actor_user_id, fr.actor_account_epoch,
                           sr.professional AS source_professional
                    FROM flywheel_records fr
                    JOIN flywheel_source_rows sr
                      ON sr.source_row_id = fr.source_row_id
                     AND sr.document_id = fr.document_id
                     AND sr.sheet_id = fr.sheet_id
                    WHERE {trusted_where}
                    ORDER BY fr.id ASC
                    """
                )
                effective_rows = cur.fetchall()

                grouped_patterns: Dict[Tuple[str, str], List[sqlite3.Row]] = {}
                param_adjust_groups: Dict[Tuple[str, str], List[sqlite3.Row]] = {}
                skipped_missing_discipline = 0

                for row in effective_rows:
                    checkpoint()
                    rule_ctx = _resolve_record_rule_context(row)
                    if rule_ctx is None:
                        skipped_missing_discipline += 1
                        continue
                    disc = rule_ctx.normalized_professional
                    raw_norm = str(row["raw_name_norm"] or "").strip()
                    if not raw_norm:
                        continue
                    key = (raw_norm, disc)
                    grouped_patterns.setdefault(key, []).append(row)
                    if row["action_type"] == "PARAM_ADJUST":
                        param_adjust_groups.setdefault(key, []).append(row)

                active_patterns = [key for key, recs in grouped_patterns.items() if len(recs) >= 2]
                first_suggestion_hook_fired = False
                active_suggestion_keys: Set[Tuple[str, str, str, str]] = set()
                all_computed_suggestions: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

                for raw_norm, disc in sorted(grouped_patterns.keys()):
                    checkpoint()
                    recs = grouped_patterns[(raw_norm, disc)]
                    alias_stats = self._compute_alias_stats(recs, disc)
                    binding_stats = self._compute_binding_stats(recs, disc)

                    for s_key, info in alias_stats.items():
                        all_computed_suggestions[s_key] = info
                    for s_key, info in binding_stats.items():
                        all_computed_suggestions[s_key] = info

                    if len(recs) < 2:
                        continue

                    for s_key, info in alias_stats.items():
                        if info["status"] is None:
                            continue
                        self._upsert_suggestion(cur, info)
                        active_suggestion_keys.add(s_key)
                        if _tx_hook and not first_suggestion_hook_fired:
                            first_suggestion_hook_fired = True
                            _tx_hook("after_first_suggestion_write", cur)
                        if _tx_hook:
                            _tx_hook("after_alias_suggestion_write", cur)

                    for s_key, info in binding_stats.items():
                        if info["status"] is None:
                            continue
                        self._upsert_suggestion(cur, info)
                        active_suggestion_keys.add(s_key)
                        if _tx_hook and not first_suggestion_hook_fired:
                            first_suggestion_hook_fired = True
                            _tx_hook("after_first_suggestion_write", cur)
                        if _tx_hook:
                            _tx_hook("after_binding_suggestion_write", cur)

                # Reconcile previously mined suggestions whose effective support fell below threshold
                self._reconcile_stale_suggestions(cur, active_suggestion_keys, all_computed_suggestions)

                # 3. Aggregate parameter adjustments into unified flywheel_pattern_aggregates
                param_groups_count = self._aggregate_parameter_adjustments(
                    cur, param_adjust_groups, _tx_hook=_tx_hook
                )

                # 4. Mark processed inside the exact same transaction
                if unprocessed_ids:
                    placeholders = ",".join(["?"] * len(unprocessed_ids))
                    cur.execute(
                        f"UPDATE flywheel_records SET is_processed = 1 WHERE id IN ({placeholders})",
                        unprocessed_ids,
                    )

                if _tx_hook:
                    _tx_hook("before_commit", cur)

                checkpoint()
                conn.execute("COMMIT")
                return {
                    "processed_records_count": len(unprocessed_ids),
                    "analyzed_patterns_count": len(active_patterns),
                    "param_adjust_groups_count": param_groups_count,
                    "skipped_missing_discipline_count": skipped_missing_discipline,
                    "status": "success",
                }
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                logger.error("Error during flywheel mining execution (%s): %s", type(exc).__name__, exc)
                raise
            finally:
                conn.close()

    def _compute_alias_stats(
        self, recs: List[sqlite3.Row], disc: str
    ) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
        alias_recs = [r for r in recs if r["action_type"] in ("MANUAL_REPLACE", "PICK_CANDIDATE")]
        denominator = len(alias_recs)
        if denominator == 0:
            return {}

        target_stats: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for r in alias_recs:
            targets = _parse_json_field(r["final_targets_json"], list, r["id"], "final_targets_json")
            if not targets:
                continue
            primary_target = targets[0] if isinstance(targets[0], dict) else {}
            target_name = str(primary_target.get("name", "") or "").strip()
            if not target_name:
                continue
            pair = extract_alias_candidate(r["raw_name"], target_name, self.vocab)
            if not pair:
                continue
            src_pattern, target_val = pair
            actor_key = _actor_identity_key(r)
            stat_key = (src_pattern, target_val)
            if stat_key not in target_stats:
                target_stats[stat_key] = {
                    "src": src_pattern,
                    "target": target_val,
                    "count": 0,
                    "users": set(),
                    "sample_ids": [],
                }
            target_stats[stat_key]["count"] += 1
            target_stats[stat_key]["users"].add(actor_key)
            target_stats[stat_key]["sample_ids"].append(int(r["id"]))

        out: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        for stat in target_stats.values():
            support = stat["count"]
            distinct_users = len(stat["users"])
            consistency = round(float(support) / float(denominator), 4)
            status: Optional[str] = None
            if support >= 3 and distinct_users >= 2 and consistency >= 0.80:
                status = "pending"
            elif support >= 2:
                status = "observing"

            rule_ctx = RuleContext.from_fields(
                professional=disc,
                name=stat["src"],
                raw_name=stat["src"],
                stage="mining",
            )
            s_key = ("ALIAS", stat["src"], stat["target"], disc)
            out[s_key] = {
                "suggestion_type": "ALIAS",
                "source_pattern": stat["src"],
                "target_value": stat["target"],
                "discipline": disc,
                "support_count": support,
                "distinct_users": distinct_users,
                "consistency": consistency,
                "sample_ids": sorted(stat["sample_ids"]),
                "status": status,
                "rule_context": rule_ctx.to_scope_dict(),
            }
        return out

    def _compute_binding_stats(
        self, recs: List[sqlite3.Row], disc: str
    ) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
        bind_recs = [r for r in recs if r["action_type"] == "BIND_MULTI"]
        denominator = len(bind_recs)
        if denominator == 0:
            return {}

        binding_groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for r in bind_recs:
            targets = _parse_json_field(r["final_targets_json"], list, r["id"], "final_targets_json")
            if len(targets) <= 1:
                continue
            sorted_targets = sorted(
                (t for t in targets if isinstance(t, dict)),
                key=lambda x: str(x.get("code", "") or ""),
            )
            if len(sorted_targets) <= 1:
                continue
            serialized = json.dumps(sorted_targets, ensure_ascii=False)
            actor_key = _actor_identity_key(r)
            bg_key = (str(r["raw_name"]), serialized)
            if bg_key not in binding_groups:
                binding_groups[bg_key] = {
                    "raw_name": str(r["raw_name"]),
                    "targets_json": serialized,
                    "count": 0,
                    "users": set(),
                    "sample_ids": [],
                }
            binding_groups[bg_key]["count"] += 1
            binding_groups[bg_key]["users"].add(actor_key)
            binding_groups[bg_key]["sample_ids"].append(int(r["id"]))

        out: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        for bg in binding_groups.values():
            support = bg["count"]
            distinct_users = len(bg["users"])
            consistency = round(float(support) / float(denominator), 4)
            status: Optional[str] = None
            if support >= 3 and distinct_users >= 2 and consistency >= 0.80:
                status = "pending"
            elif support >= 2 and denominator >= 2:
                status = "observing"

            rule_ctx = RuleContext.from_fields(
                professional=disc,
                name=bg["raw_name"],
                raw_name=bg["raw_name"],
                stage="mining",
            )
            s_key = ("BINDING_RULE", bg["raw_name"], bg["targets_json"], disc)
            out[s_key] = {
                "suggestion_type": "BINDING_RULE",
                "source_pattern": bg["raw_name"],
                "target_value": bg["targets_json"],
                "discipline": disc,
                "support_count": support,
                "distinct_users": distinct_users,
                "consistency": consistency,
                "sample_ids": sorted(bg["sample_ids"]),
                "status": status,
                "rule_context": rule_ctx.to_scope_dict(),
            }
        return out

    def _reconcile_stale_suggestions(
        self,
        cur: sqlite3.Cursor,
        active_suggestion_keys: Set[Tuple[str, str, str, str]],
        all_computed_suggestions: Dict[Tuple[str, str, str, str], Dict[str, Any]],
    ) -> None:
        existing_rows = cur.execute(
            """
            SELECT id, suggestion_type, source_pattern, target_value, discipline, status
            FROM flywheel_rule_suggestions
            WHERE source_origin = 'trusted_miner'
            """
        ).fetchall()
        for row in existing_rows:
            s_key = (
                str(row["suggestion_type"]),
                str(row["source_pattern"]),
                str(row["target_value"]),
                str(row["discipline"] or ""),
            )
            if s_key in active_suggestion_keys:
                continue
            actual = all_computed_suggestions.get(s_key)
            if actual:
                sup = int(actual["support_count"])
                d_users = int(actual["distinct_users"])
                cons = float(actual["consistency"])
                sids_json = json.dumps(actual["sample_ids"])
            else:
                sup = 0
                d_users = 0
                cons = 0.0
                sids_json = "[]"

            cur_status = str(row["status"] or "")
            new_status = cur_status if cur_status in ("accepted", "rejected", "rolled_back") else "superseded"
            cur.execute(
                """
                UPDATE flywheel_rule_suggestions
                SET support_count = ?, distinct_users = ?, consistency = ?,
                    sample_ids_json = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (sup, d_users, cons, sids_json, new_status, int(row["id"])),
            )

    def _aggregate_parameter_adjustments(
        self,
        cur: sqlite3.Cursor,
        param_adjust_groups: Dict[Tuple[str, str], List[sqlite3.Row]],
        _tx_hook: Optional[Callable[[str, sqlite3.Cursor], None]] = None,
    ) -> int:
        """Aggregate parameter adjustment trends into authoritative flywheel_pattern_aggregates."""
        active_agg_keys: Set[Tuple[str, str]] = set()
        for (raw_norm, disc), recs in sorted(param_adjust_groups.items()):
            checkpoint()
            if len(recs) < 2:
                continue
            active_agg_keys.add((raw_norm, disc))
            summary_payload = self._build_param_adjust_summary(recs, disc)
            cur.execute(
                """
                INSERT INTO flywheel_pattern_aggregates (
                    source_pattern, suggestion_type, target_value, discipline, source_origin,
                    archived_support, archived_distinct_users,
                    active_support, active_distinct_users, total_support,
                    param_adjust_count, dominant_target_code, sample_ids_json, context_summary_json,
                    updated_at
                ) VALUES (?, 'PARAM_ADJUST', '', ?, 'trusted_recompute', 0, 0, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_pattern, suggestion_type, target_value, discipline, source_origin)
                DO UPDATE SET
                    active_support = excluded.active_support,
                    active_distinct_users = excluded.active_distinct_users,
                    total_support = flywheel_pattern_aggregates.archived_support + excluded.active_support,
                    param_adjust_count = excluded.param_adjust_count,
                    dominant_target_code = excluded.dominant_target_code,
                    sample_ids_json = excluded.sample_ids_json,
                    context_summary_json = excluded.context_summary_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    raw_norm,
                    disc,
                    summary_payload["active_support"],
                    summary_payload["active_distinct_users"],
                    summary_payload["active_support"],
                    summary_payload["active_support"],
                    summary_payload["dominant_target_code"],
                    summary_payload["sample_ids_json"],
                    summary_payload["context_summary_json"],
                ),
            )
            if _tx_hook:
                _tx_hook("after_param_aggregate_write", cur)

        # Reconcile stale trusted_recompute rows whose active support dropped below 2
        existing_agg_rows = cur.execute(
            """
            SELECT id, source_pattern, discipline
            FROM flywheel_pattern_aggregates
            WHERE suggestion_type = 'PARAM_ADJUST' AND source_origin = 'trusted_recompute'
            """
        ).fetchall()
        for agg_row in existing_agg_rows:
            agg_key = (str(agg_row["source_pattern"]), str(agg_row["discipline"] or ""))
            if agg_key in active_agg_keys:
                continue
            leftover = param_adjust_groups.get(agg_key, [])
            if leftover:
                summary_payload = self._build_param_adjust_summary(leftover, agg_key[1])
            else:
                summary_payload = {
                    "active_support": 0,
                    "active_distinct_users": 0,
                    "dominant_target_code": "",
                    "sample_ids_json": "[]",
                    "context_summary_json": json.dumps(
                        {"professional": agg_key[1], "adjusted_params": {}}, ensure_ascii=False
                    ),
                }
            cur.execute(
                """
                UPDATE flywheel_pattern_aggregates
                SET active_support = ?,
                    active_distinct_users = ?,
                    total_support = archived_support + ?,
                    param_adjust_count = ?,
                    dominant_target_code = ?,
                    sample_ids_json = ?,
                    context_summary_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    summary_payload["active_support"],
                    summary_payload["active_distinct_users"],
                    summary_payload["active_support"],
                    summary_payload["active_support"],
                    summary_payload["dominant_target_code"],
                    summary_payload["sample_ids_json"],
                    summary_payload["context_summary_json"],
                    int(agg_row["id"]),
                ),
            )
        return len(active_agg_keys)

    @staticmethod
    def _build_param_adjust_summary(recs: List[sqlite3.Row], disc: str) -> Dict[str, Any]:
        users: Set[str] = set()
        sample_ids: List[int] = []
        target_codes: Counter = Counter()
        param_trends: Dict[str, Counter] = {}

        for r in recs:
            users.add(_actor_identity_key(r))
            sample_ids.append(int(r["id"]))
            targets = _parse_json_field(r["final_targets_json"], list, r["id"], "final_targets_json")
            if targets and isinstance(targets[0], dict):
                t_code = str(targets[0].get("code", "") or "").strip()
                if t_code:
                    target_codes[t_code] += 1
            ext = _parse_json_field(r["extracted_params"], dict, r["id"], "extracted_params")
            adj = ext.get("client_adjusted_params") if isinstance(ext.get("client_adjusted_params"), dict) else {}
            if not adj and ext:
                adj = {k: v for k, v in ext.items() if k not in ("parsed_spec", "client_adjusted_params")}
            for pk, pv in adj.items():
                val_str = json.dumps(pv, ensure_ascii=False) if isinstance(pv, (dict, list)) else str(pv)
                param_trends.setdefault(str(pk), Counter())[val_str] += 1

        dominant_target_code = target_codes.most_common(1)[0][0] if target_codes else ""
        trend_summary = {
            pk: {"count": sum(cnt.values()), "values": dict(cnt)}
            for pk, cnt in sorted(param_trends.items())
        }
        return {
            "active_support": len(recs),
            "active_distinct_users": len(users),
            "dominant_target_code": dominant_target_code,
            "sample_ids_json": json.dumps(sorted(sample_ids)),
            "context_summary_json": json.dumps(
                {
                    "professional": disc,
                    "scope_level": "discipline",
                    "scope_target": disc,
                    "adjusted_params": trend_summary,
                },
                ensure_ascii=False,
            ),
        }

    def _upsert_suggestion(self, cur: sqlite3.Cursor, info: Dict[str, Any]) -> None:
        """Insert or update rule suggestions scoped by (type, source, target, discipline, source_origin)."""
        suggestion_type = info["suggestion_type"]
        source_pattern = info["source_pattern"]
        target_value = info["target_value"]
        discipline = info["discipline"]
        support_count = info["support_count"]
        distinct_users = info["distinct_users"]
        consistency = info["consistency"]
        sample_ids = info["sample_ids"]
        status = info["status"]
        rule_ctx_json = json.dumps(info["rule_context"], ensure_ascii=False)

        cur.execute(
            """
            SELECT id, status FROM flywheel_rule_suggestions
            WHERE suggestion_type = ? AND source_pattern = ? AND target_value = ?
              AND discipline = ? AND source_origin = 'trusted_miner'
            """,
            (suggestion_type, source_pattern, target_value, discipline),
        )
        exact_existing = cur.fetchone()

        sample_ids_json = json.dumps(sample_ids)
        if exact_existing:
            current_status = exact_existing["status"]
            new_status = current_status if current_status in ("accepted", "rejected", "rolled_back") else status
            cur.execute(
                """
                UPDATE flywheel_rule_suggestions
                SET support_count = ?, distinct_users = ?, consistency = ?,
                    sample_ids_json = ?, status = ?, rule_context_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    support_count,
                    distinct_users,
                    consistency,
                    sample_ids_json,
                    new_status,
                    rule_ctx_json,
                    int(exact_existing["id"]),
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO flywheel_rule_suggestions (
                    suggestion_type, source_pattern, target_value, discipline,
                    support_count, distinct_users, consistency, sample_ids_json,
                    source_origin, rule_context_json, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'trusted_miner', ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    suggestion_type,
                    source_pattern,
                    target_value,
                    discipline,
                    support_count,
                    distinct_users,
                    consistency,
                    sample_ids_json,
                    rule_ctx_json,
                    status,
                ),
            )


miner = FlywheelMiner()
