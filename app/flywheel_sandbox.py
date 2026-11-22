# -*- coding: utf-8 -*-
"""
Subprocess Sandbox Isolation Evaluation Service (app/flywheel_sandbox.py).
Implements M2 unified subprocess sandbox evaluation across flywheel suggestions,
legacy binding rules, and manual LLM DSL rules:
- Strict 300s per-subprocess timeout & 660s per-task deadline with kill & cleanup.
- Real production pipeline execution (app.sandbox_evaluator --eval-mode sandbox_v2):
  targeted + stratified global guard set + batch_match_rows binding decision checks + hit counts.
- Explicit separation of safety_passed vs benefit (improved / neutral / harmful / invalid).
- Persistent SQLite eval credentials (rule_eval_runs) bound to normalized payload & live snapshot.
"""
import json
import logging
import math
import os
import shutil
import sqlite3
import subprocess
from app.runtime_jobs import JobStopped, checkpoint, eval_jobs, prune_terminal_tasks, terminal_ttl
from app.runtime_retention import active_path, sandbox_directory
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.rule_context import DYNAMIC_UNIVERSAL_TOKENS, canonicalize_professional
from app.runtime_eval_assets import RuntimeAssetError, load_runtime_golden
from app.rule_governance import (
    EvalGateError,
    _resolve_materials_db_path,
    SUBPROCESS_TIMEOUT_SECONDS,
    TASK_TIMEOUT_SECONDS,
    compute_publish_env_snapshot,
    compute_rule_digest,
    create_eval_run_record,
    finalize_eval_run_record,
    get_eval_run,
    mark_eval_run_running,
    normalize_rule_spec,
)

logger = logging.getLogger("listtrans_app")
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
CONFIG_PROD_DIR = os.getenv("CONFIG_DIR", os.path.join(PROJECT_ROOT, "config"))
SCORE_EPS = 1e-6


def _row_score_maps(row: Dict[str, Any]):
    """Extract {code: raw_score} map from evaluation row; fail closed on missing/invalid/NaN scores."""
    src = row.get("candidates")
    note = "raw"
    if not isinstance(src, list) or not src:
        return None, "missing_raw_candidates"
    score_map = {}
    for c in src:
        if not isinstance(c, dict):
            return None, note
        code = c.get("code")
        if not isinstance(code, str) or not code or code in score_map:
            return None, note
        try:
            sc = float(c.get("score"))
        except (TypeError, ValueError):
            return None, note
        if not math.isfinite(sc):
            return None, note
        score_map[code] = sc
    return score_map, note


def _compare_evaluation_snapshots(base_data: Dict[str, Any], cand_data: Dict[str, Any]):
    """Verify baseline and candidate subprocesses ran against identical materials/golden/vector/budget."""
    base_snapshot = base_data.get("summary", {}).get("snapshot")
    cand_snapshot = cand_data.get("summary", {}).get("snapshot")
    errors = []
    if not isinstance(base_snapshot, dict) or not isinstance(cand_snapshot, dict):
        errors.append("基线/候选缺少运行快照")
    else:
        for key in (
            "materials_db_sha256",
            "golden_set_sha256",
            "golden_set_item_count",
            "vector_channel",
            "budget",
        ):
            if base_snapshot.get(key) != cand_snapshot.get(key):
                errors.append(f"基线/候选 {key} 不一致")
        for key, message in (
            ("materials_db_sha256", "物料快照哈希缺失"),
            ("golden_set_sha256", "金标快照哈希缺失"),
        ):
            if not base_snapshot.get(key):
                errors.append(message)
    return {"baseline": base_snapshot, "candidate": cand_snapshot, "errors": errors}


def _compare_sandbox_rows(base_rows: List[Dict[str, Any]], cand_rows: List[Dict[str, Any]]):
    """V015-M1 R02 / LEGACY-01 per-case rank, target score T, and margin T-W zero-regression gate."""
    violations: List[Dict[str, Any]] = []
    improved = degraded = degraded_from_top1 = 0
    details: List[Dict[str, Any]] = []
    score_details: List[Dict[str, Any]] = []
    integrity_errors: List[str] = []
    verifiable_baseline = False

    if not cand_rows:
        return False, {
            "passed": False,
            "targeted_count": 0,
            "improved_count": 0,
            "degraded_count": 0,
            "degraded_from_top1": 0,
            "details": [],
            "score_comparisons": [],
            "violations": [{"type": "empty_targeted"}],
            "integrity_errors": ["定向子集为空"],
            "reason": "门禁拦截：定向子集为空，黄金测试集中未匹配到关键词相关用例",
        }

    def _ids(rows):
        return [r.get("id") for r in rows]

    b_ids, c_ids = _ids(base_rows), _ids(cand_rows)
    if any(i is None for i in b_ids + c_ids):
        integrity_errors.append("存在缺 id 的评测行")
    if len(set(b_ids)) != len(b_ids):
        integrity_errors.append("基线样本 ID 重复")
    if len(set(c_ids)) != len(c_ids):
        integrity_errors.append("候选样本 ID 重复")
    if set(b_ids) != set(c_ids):
        integrity_errors.append(
            f"基线/候选样本 ID 集合不一致：only_base={sorted(set(b_ids) - set(c_ids))}, "
            f"only_cand={sorted(set(c_ids) - set(b_ids))}"
        )

    base_map = {r.get("id"): r for r in base_rows}
    cand_map = {r.get("id"): r for r in cand_rows}

    for cid, c_row in cand_map.items():
        b_row = base_map.get(cid)
        if b_row is None:
            violations.append({"id": cid, "type": "sample_missing_in_baseline"})
            continue
        for tag, row in (("baseline", b_row), ("candidate", c_row)):
            if row.get("error"):
                violations.append(
                    {"id": cid, "type": "eval_error", "side": tag, "error": str(row.get("error"))[:200]}
                )
            if not row.get("target_codes"):
                violations.append({"id": cid, "type": "missing_target_codes", "side": tag})
            if "rank" not in row:
                violations.append({"id": cid, "type": "missing_rank", "side": tag})

        gt = set(b_row.get("target_codes") or [])
        if not gt:
            continue
        if set(c_row.get("target_codes") or []) != gt:
            violations.append({"id": cid, "type": "target_codes_mismatch"})
            continue
        try:
            b_rank_raw = int(b_row.get("rank", 0))
            c_rank_raw = int(c_row.get("rank", 0))
        except (TypeError, ValueError):
            violations.append({"id": cid, "type": "non_numeric_rank"})
            continue
        b_rank = b_rank_raw if b_rank_raw > 0 else 999
        c_rank = c_rank_raw if c_rank_raw > 0 else 999

        if b_rank == 1 and c_rank != 1:
            degraded_from_top1 += 1
            details.append(
                {"id": cid, "name": c_row.get("name"), "change": f"Top1 掉出 (1 -> {c_rank})", "type": "critical_degrade"}
            )
        elif c_rank < b_rank:
            improved += 1
            details.append(
                {"id": cid, "name": c_row.get("name"), "change": f"排名提升 ({b_rank} -> {c_rank})", "type": "improved"}
            )
        elif c_rank > b_rank:
            degraded += 1
            details.append(
                {"id": cid, "name": c_row.get("name"), "change": f"排名下降 ({b_rank} -> {c_rank})", "type": "degraded"}
            )

        if b_rank != 1:
            continue
        b_map, b_note = _row_score_maps(b_row)
        c_map, c_note = _row_score_maps(c_row)
        if b_map is None or c_map is None:
            violation_type = (
                "missing_raw_scores"
                if "missing_raw_candidates" in (b_note, c_note)
                else "non_finite_or_missing_score"
            )
            violations.append({"id": cid, "type": violation_type})
            continue

        b_T_vals = [b_map[g] for g in gt if g in b_map]
        if not b_T_vals:
            violations.append({"id": cid, "type": "baseline_target_missing_in_scores"})
            continue
        b_T = max(b_T_vals)
        verifiable_baseline = True
        b_W_vals = [v for k, v in b_map.items() if k not in gt]

        c_T_vals = [c_map[g] for g in gt if g in c_map]
        if not c_T_vals:
            violations.append({"id": cid, "type": "target_score_lost", "base_T": b_T})
            continue
        c_T = max(c_T_vals)
        b_W = max(b_W_vals) if b_W_vals else None
        c_W_vals = [v for k, v in c_map.items() if k not in gt]
        c_W = max(c_W_vals) if c_W_vals else None
        score_details.append(
            {
                "id": cid,
                "type": "score_protection",
                "baseline": {
                    "target_codes": sorted(gt),
                    "T": b_T,
                    "W": b_W,
                    "margin": b_T - b_W if b_W is not None else None,
                },
                "candidate": {
                    "target_codes": sorted(gt),
                    "T": c_T,
                    "W": c_W,
                    "margin": c_T - c_W if c_W is not None else None,
                },
                "score_precision": {"baseline": b_note, "candidate": c_note},
                "absolute_epsilon": SCORE_EPS,
            }
        )

        if c_T < b_T - SCORE_EPS:
            violations.append(
                {"id": cid, "type": "target_score_decreased", "base_T": b_T, "cand_T": c_T, "delta": round(c_T - b_T, 6)}
            )

        if not b_W_vals:
            if c_W_vals:
                violations.append(
                    {"id": cid, "type": "noncomparable_new_nontarget", "base_T": b_T, "cand_T": c_T, "cand_W": max(c_W_vals)}
                )
        else:
            if not c_W_vals:
                violations.append(
                    {"id": cid, "type": "noncomparable_nontarget_lost", "base_T": b_T, "base_W": max(b_W_vals), "cand_T": c_T}
                )
            else:
                b_margin = b_T - max(b_W_vals)
                c_margin = c_T - max(c_W_vals)
                if c_margin < b_margin - SCORE_EPS:
                    violations.append(
                        {
                            "id": cid,
                            "type": "margin_shrunk",
                            "base_margin": round(b_margin, 6),
                            "cand_margin": round(c_margin, 6),
                            "delta": round(c_margin - b_margin, 6),
                            "base_T": b_T,
                            "cand_T": c_T,
                        }
                    )
        if b_note != "raw" or c_note != "raw":
            violations.append(
                {
                    "id": cid,
                    "type": "missing_raw_scores",
                    "baseline_precision": b_note,
                    "candidate_precision": c_note,
                }
            )

    if cand_rows and not verifiable_baseline:
        integrity_errors.append("没有可验证的正确 Top1 基线，禁止以全 0 稳态放行")

    net_gain = improved - degraded
    passed = (
        not integrity_errors
        and not violations
        and degraded_from_top1 == 0
        and net_gain >= 0
    )
    if integrity_errors:
        reason = "门禁拦截：评测结果完整性校验失败（" + "；".join(integrity_errors) + "）"
    elif violations:
        reason = f"门禁拦截：{len(violations)} 例触发逐例得分/领先间隔零退化保护（正确目标得分或领先间隔下降，或结果不可比）"
    elif degraded_from_top1 > 0:
        reason = f"门禁拦截：检测到 {degraded_from_top1} 条原有 Top1 匹配用例发生排名恶化"
    elif net_gain < 0:
        reason = f"门禁拦截：恶化用例数 ({degraded}) 大于改善用例数 ({improved})，净收益为负"
    elif improved == 0 and degraded == 0:
        reason = "门禁通过：稳态无退化（改善: 0 条, 恶化: 0 条，逐例得分/间隔零退化）"
    else:
        reason = f"定向子集验证通过：改善 {improved} 条，恶化 {degraded} 条，Top1 零恶化且逐例得分/间隔零退化"

    report = {
        "passed": passed,
        "targeted_count": len(cand_rows),
        "improved_count": improved,
        "degraded_count": degraded,
        "degraded_from_top1": degraded_from_top1,
        "details": (details + score_details)[:50],
        "score_comparisons": score_details,
        "violations": violations,
        "integrity_errors": integrity_errors,
        "reason": reason,
    }
    return passed, report


def _compare_binding_pipeline_rows(
    base_binding_rows: List[Dict[str, Any]],
    cand_binding_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Verify actual production batch_match_rows + binding conflicts + qty ratios + target set accuracy."""
    integrity_errors: List[str] = []
    violations: List[Dict[str, Any]] = []
    improved = 0
    degraded = 0

    b_ids = [r.get("id") for r in base_binding_rows]
    c_ids = [r.get("id") for r in cand_binding_rows]
    if any(i is None for i in b_ids + c_ids) or set(b_ids) != set(c_ids) or len(set(c_ids)) != len(c_ids):
        integrity_errors.append("绑定评测行 ID 不完整或与基线不一致")
        return {
            "passed": False,
            "checked_count": len(cand_binding_rows),
            "improved_count": 0,
            "degraded_count": 0,
            "violations": violations,
            "integrity_errors": integrity_errors,
        }

    base_map = {r["id"]: r for r in base_binding_rows}
    for c in cand_binding_rows:
        b = base_map[c["id"]]
        cid = c["id"]

        if c.get("rule_applied"):
            if c.get("invalid_codes"):
                degraded += 1
                violations.append({"id": cid, "type": "binding_invalid_code", "invalid_codes": c["invalid_codes"]})
                continue
            if c.get("conflicts"):
                degraded += 1
                violations.append({"id": cid, "type": "binding_hard_conflict", "conflicts": c["conflicts"]})
                continue
            if not c.get("qty_ratios_ok", True):
                degraded += 1
                violations.append({"id": cid, "type": "binding_qty_ratio_mismatch"})
                continue

        prior_base = b.get("prior_matcher_proposal", b)
        prior_candidate = c.get("prior_matcher_proposal", c)
        rejected = c.get("rejected") and not b.get("rejected")
        prior_rejected = prior_candidate.get("rejected") and not prior_base.get("rejected")
        if rejected or prior_rejected:
            evidence = c if rejected else prior_candidate
            degraded += 1
            violations.append({"id": cid, "type": "binding_status_rejected", "reason": evidence.get("decision_reason", "")})
            continue
        lost_correct_binding = b.get("codes_match_gt") and not c.get("codes_match_gt")
        lost_correct_proposal = prior_base.get("codes_match_gt") and not prior_candidate.get("codes_match_gt")
        if lost_correct_binding or lost_correct_proposal:
            previous, current = (b, c) if lost_correct_binding else (prior_base, prior_candidate)
            degraded += 1
            violations.append(
                {
                    "id": cid,
                    "type": "binding_target_code_degraded",
                    "base_code": previous.get("matched_code", ""),
                    "cand_code": current.get("matched_code", ""),
                }
            )
            continue

        if (
            c.get("export_eligible")
            and not b.get("export_eligible")
            and c.get("codes_match_gt")
            and c.get("qty_ratios_ok", True)
            and not c.get("conflicts")
            and not c.get("invalid_codes")
        ) or (
            not (b.get("codes_match_gt") or prior_base.get("codes_match_gt"))
            and c.get("codes_match_gt")
            and not c.get("rejected")
            and not c.get("conflicts")
        ):
            improved += 1

    return {
        "passed": not integrity_errors and not violations and degraded == 0,
        "checked_count": len(cand_binding_rows),
        "improved_count": improved,
        "degraded_count": degraded,
        "violations": violations,
        "integrity_errors": integrity_errors,
    }


_sandbox_eval_lock = threading.Lock()
_eval_tasks: Dict[str, Dict[str, Any]] = {}
_tasks_lock = threading.Lock()
MAX_EVAL_TASKS_CACHE = 100


def expire_eval_tasks():
    with _tasks_lock:
        prune_terminal_tasks(_eval_tasks, MAX_EVAL_TASKS_CACHE, terminal_ttl())


def cancel_eval_task(task_id, owner):
    if not get_eval_task_status_for_user(task_id, owner):
        return None
    return eval_jobs.cancel(task_id)


def get_eval_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    with _tasks_lock:
        prune_terminal_tasks(_eval_tasks, MAX_EVAL_TASKS_CACHE, terminal_ttl())
        task = _eval_tasks.get(task_id)
        if task:
            return dict(task)
    db_row = get_eval_run(task_id)
    if not db_row:
        return None
    try:
        report = json.loads(db_row.get("result_summary_json") or "{}")
    except Exception:
        report = {}
    return {
        "task_id": db_row["eval_id"],
        "eval_id": db_row["eval_id"],
        "owner_user_id": db_row.get("owner_user_id"),
        "owner_account_epoch": db_row.get("owner_account_epoch") or "",
        "status": db_row["status"],
        "progress": 100 if db_row["status"] in ("completed", "failed", "timed_out") else 10,
        "message": db_row.get("error_message") or report.get("reason", ""),
        "passed": bool(db_row.get("safety_passed")),
        "safety_passed": bool(db_row.get("safety_passed")),
        "benefit": db_row.get("benefit_class", "invalid"),
        "report": report or None,
        "expires_at": db_row.get("expires_at"),
    }


def get_eval_task_status_for_user(task_id: str, user_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Strict owner-only retrieval via verified active session; unverified/revoked/mismatched callers return None (404)."""
    from app.auth_manager import resolve_verified_user

    verified = resolve_verified_user(user_info)
    if not verified or not verified.get("id") or not verified.get("account_epoch"):
        return None
    req_uid = int(verified["id"])
    req_epoch = str(verified["account_epoch"])
    task = get_eval_task_status(task_id)
    if not task:
        return None
    owner_uid = task.get("owner_user_id")
    owner_epoch = task.get("owner_account_epoch")
    if owner_uid is None or not owner_epoch:
        return None
    if int(owner_uid) != req_uid or str(owner_epoch) != req_epoch:
        return None
    return dict(task)


def _inject_candidate_rule_into_sandbox(
    *,
    rule_meta: Dict[str, Any],
    candidate_config_dir: str,
    candidate_db_path: str,
    candidate_llm_rule_row: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Inject ONLY candidate rule into isolated sandbox config dir or temp SQLite DB copy."""
    rule_type = str(rule_meta.get("rule_type") or "ALIAS").upper()
    rule_source = str(rule_meta.get("rule_source") or "")

    if rule_type == "ALIAS":
        src_pat = str(rule_meta.get("source_pattern") or "").strip()
        tgt_val = str(rule_meta.get("target_value") or "").strip()
        disc = str(rule_meta.get("discipline") or "").strip()
        canon_disc = canonicalize_professional(disc) if disc else ""
        if canon_disc and canon_disc.lower() not in DYNAMIC_UNIVERSAL_TOKENS and canon_disc != "其它":
            scoped_path = os.path.join(candidate_config_dir, "scoped_aliases.json")
            scoped_data: Dict[str, Dict[str, str]] = {}
            if os.path.exists(scoped_path):
                with open(scoped_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    if isinstance(raw, dict):
                        scoped_data = raw
            scoped_data.setdefault(canon_disc, {})[src_pat] = tgt_val
            with open(scoped_path, "w", encoding="utf-8") as f:
                json.dump(scoped_data, f, ensure_ascii=False, indent=4)
        else:
            # Unscoped direct test-only call from legacy unit fixtures (publish gate still requires concrete discipline)
            alias_path = os.path.join(candidate_config_dir, "alias_dict.json")
            alias_data = {}
            if os.path.exists(alias_path):
                with open(alias_path, "r", encoding="utf-8") as f:
                    alias_data = json.load(f)
            alias_data[src_pat] = tgt_val
            with open(alias_path, "w", encoding="utf-8") as f:
                json.dump(alias_data, f, ensure_ascii=False, indent=4)
        return None

    if rule_type in ("BINDING", "BINDING_RULE"):
        binding_path = os.path.join(candidate_config_dir, "auto_binding_rules.json")
        binding_data = []
        if os.path.exists(binding_path):
            with open(binding_path, "r", encoding="utf-8") as f:
                binding_data = json.load(f)
        orig_name = str(rule_meta.get("original_name") or "").strip()
        orig_spec = str(rule_meta.get("original_spec") or "").strip()
        prof = str(rule_meta.get("discipline") or "").strip()
        new_rule = {
            "original_name": orig_name,
            "original_spec": orig_spec,
            "professional": prof,
            "bound_materials": rule_meta.get("bound_materials") or [],
        }
        if "bom" in rule_meta:
            new_rule["bom"] = rule_meta["bom"]
        binding_data = [
            r for r in binding_data
            if not (
                str(r.get("original_name") or "").strip() == orig_name
                and str(r.get("original_spec") or "").strip() == orig_spec
                and canonicalize_professional(str(r.get("professional") or "").strip()) == canonicalize_professional(prof)
            )
        ]
        binding_data.append(new_rule)
        with open(binding_path, "w", encoding="utf-8") as f:
            json.dump(binding_data, f, ensure_ascii=False, indent=4)
        return None

    if rule_type == "LLM_DSL" or rule_source == "manual_llm":
        from app.llm_rule_engine import compute_rule_content_digest

        cand_db = candidate_db_path
        row_info = candidate_llm_rule_row or {}
        rid = str(rule_meta.get("rule_key") or "")
        dsl_json = json.dumps(rule_meta.get("parsed_dsl") or {}, ensure_ascii=False)
        sc_level = str(rule_meta.get("scope_level") or "category")
        sc_target = str(rule_meta.get("scope_target") or "")
        disc = str(rule_meta.get("discipline") or "")
        prio = int(rule_meta.get("priority") if rule_meta.get("priority") is not None else 10)
        digest = compute_rule_content_digest(sc_level, sc_target, disc, prio, dsl_json)
        conn = sqlite3.connect(cand_db, timeout=15.0)
        try:
            author_id = int(row_info.get("author_id") or 1)
            approver_id = int(row_info.get("approver_id") or (author_id + 1))
            if approver_id == author_id:
                approver_id = author_id + 1
            publisher_id = int(row_info.get("publisher_id") or 1)
            rev = int(rule_meta.get("revision") or 1)
            existing = conn.execute("SELECT 1 FROM llm_rules WHERE rule_id = ?", (rid,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE llm_rules
                    SET rule_json = ?, scope_level = ?, scope_target = ?, discipline = ?, priority = ?,
                        revision = ?, content_digest = ?, approved_revision = ?, approved_digest = ?,
                        approver_id = ?, publisher_id = ?, status = 'published'
                    WHERE rule_id = ?
                    """,
                    (dsl_json, sc_level, sc_target, disc, prio, rev, digest, rev, digest, approver_id, publisher_id, rid),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO llm_rules (
                        rule_id, rule_name, scope_level, scope_target, discipline, priority,
                        rule_json, author_id, author_name, approver_id, approver_name, publisher_id, publisher_name,
                        status, revision, content_digest, approved_revision, approved_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sandbox', ?, 'sandbox_mgr', ?, 'sandbox_adm', 'published', ?, ?, ?, ?)
                    """,
                    (
                        rid, rule_meta.get("rule_name") or rid, sc_level, sc_target, disc, prio,
                        dsl_json, author_id, approver_id, publisher_id, rev, digest, rev, digest,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return cand_db

    return None


def _update_task_state(task_id: str, **fields: Any) -> None:
    with _tasks_lock:
        if task_id in _eval_tasks:
            _eval_tasks[task_id].update(fields)
            if fields.get("status") in ("completed", "failed", "cancelled", "timed_out"):
                _eval_tasks[task_id].setdefault("finished_at", time.time())
                prune_terminal_tasks(_eval_tasks, MAX_EVAL_TASKS_CACHE, terminal_ttl())


def _fail_task_closed(
    task_id: str,
    *,
    message: str,
    error_code: str = "eval_failed",
    status: str = "failed",
    base_rc: Optional[int] = None,
    cand_rc: Optional[int] = None,
    extra_report: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> None:
    summary = dict(extra_report or {})
    summary.update({
        "passed": False,
        "safety_passed": False,
        "benefit": "invalid",
        "error_code": error_code,
        "reason": message,
    })
    try:
        finalize_eval_run_record(
            task_id,
            status=status,
            safety_passed=False,
            benefit_class="invalid",
            baseline_process_exit=base_rc,
            candidate_process_exit=cand_rc,
            result_summary=summary,
            error_message=message,
            db_path=db_path,
        )
    except Exception as db_exc:
        logger.error("Failed persisting failed eval state for %s (%s): %s", task_id, type(db_exc).__name__, db_exc)
    _update_task_state(
        task_id,
        status=status,
        progress=100,
        passed=False,
        safety_passed=False,
        benefit="invalid",
        error_code=error_code,
        message=message,
        report=summary if extra_report else None,
    )


def _snapshot_materials(source: str, dest: Path) -> None:
    src = sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _child_environment(root: Path, side: str, config_dir: str, golden_path: Path, golden_digest: str) -> dict:
    work = root / side
    work.mkdir()
    cache = work / "vector_cache"
    cache.mkdir()
    from app.vector_index import CACHE_FILE_NAMES, SNAPSHOT_FILE, get_cache_dir
    for name in (*CACHE_FILE_NAMES, SNAPSHOT_FILE):
        source = Path(get_cache_dir()) / name
        if source.is_file():
            shutil.copyfile(source, cache / name)
    paths = {
        "MATERIALS_DB_PATH": work / "materials.db",
        "AUTH_DB_PATH": work / "auth.db",
        "CONFIG_DIR": Path(config_dir),
        "VECTOR_CACHE_DIR": cache,
        "UPLOAD_DIR": work / "uploads",
        "OUTPUT_DIR": work / "output",
        "LOG_DIR": work / "logs",
        "BINDING_FEEDBACK_FILE": work / "binding_feedback.json",
        "FEEDBACK_SAMPLES_FILE": work / "feedback_samples.json",
        "GOLDEN_DETAIL_FILE": work / "golden_detail.json",
    }
    if any(root not in path.resolve().parents for path in paths.values()):
        raise RuntimeError("沙箱路径不在任务隔离目录内。")
    for key in ("UPLOAD_DIR", "OUTPUT_DIR", "LOG_DIR"):
        paths[key].mkdir()
    env = os.environ.copy()
    env.pop("LISTTRANS_EVAL_KEYWORD_ONLY", None)
    env.pop("LISTTRANS_EVAL_REQUIRE_VECTOR", None)
    env.update({key: str(value) for key, value in paths.items()})
    env.update({
        "LISTTRANS_EVAL_ROOT": str(root), "PYTHONPATH": PROJECT_ROOT, "PYTHONIOENCODING": "utf-8",
        "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "1", "LLM_ENABLED": "false", "TESTING": "1",
        "DASHSCOPE_API_KEY": "", "DEEPSEEK_API_KEY": "", "PYTHON_DOTENV_DISABLED": "1",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "GOLDEN_SET_FILE": str(golden_path), "GOLDEN_SET_SHA256": golden_digest,
    })
    return env


def _run_eval_process(command, *, timeout, **kwargs):
    checkpoint()
    kwargs.pop("check", None)
    if kwargs.pop("capture_output", False):
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout
    with subprocess.Popen(command, **kwargs) as process:
        try:
            while True:
                checkpoint()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                except subprocess.TimeoutExpired:
                    continue
                checkpoint()
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.communicate()
            raise


def _run_sandbox_worker(
    task_id: str,
    rule_meta: Dict[str, Any],
    *,
    task_deadline_mono: Optional[float] = None,
    candidate_llm_rule_row: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
):
    """Background worker thread for subprocess sandbox regression & binding/guard evaluation."""
    if task_deadline_mono is None:
        task_deadline_mono = time.monotonic() + TASK_TIMEOUT_SECONDS

    with _sandbox_eval_lock:
        _update_task_state(task_id, status="running", progress=10, message="正在准备基线与候选沙箱隔离环境...")
        try:
            mark_eval_run_running(task_id, db_path=db_path)
        except Exception:
            pass

        runtime_root = sandbox_directory()
        runtime_root.mkdir(parents=True, exist_ok=True)
        temp_sandbox_root = tempfile.mkdtemp(prefix=f"sandbox_{task_id}_", dir=runtime_root)
        active = active_path(temp_sandbox_root)
        active.__enter__()
        baseline_config_dir = os.path.join(temp_sandbox_root, "config_baseline")
        candidate_config_dir = os.path.join(temp_sandbox_root, "config_candidate")
        baseline_output_json = os.path.join(temp_sandbox_root, "baseline_result.json")
        candidate_output_json = os.path.join(temp_sandbox_root, "candidate_result.json")
        rule_meta_json = os.path.join(temp_sandbox_root, "rule_meta.json")
        base_rc: Optional[int] = None
        cand_rc: Optional[int] = None

        try:
            (Path(temp_sandbox_root) / ".listtrans-sandbox").touch()
            checkpoint()
            if time.monotonic() >= task_deadline_mono:
                _fail_task_closed(
                    task_id,
                    message=f"评测任务等待超限（总上限 {TASK_TIMEOUT_SECONDS}s），已超时终止。",
                    error_code="eval_task_timeout",
                    db_path=db_path,
                )
                return

            if os.path.isdir(CONFIG_PROD_DIR):
                shutil.copytree(CONFIG_PROD_DIR, baseline_config_dir)
                shutil.copytree(CONFIG_PROD_DIR, candidate_config_dir)
            else:
                os.makedirs(baseline_config_dir, exist_ok=True)
                os.makedirs(candidate_config_dir, exist_ok=True)

            with open(rule_meta_json, "w", encoding="utf-8") as rf:
                json.dump(rule_meta, rf, ensure_ascii=False)

            _update_task_state(task_id, progress=25, message="正在向候选沙箱注入拟发布规则...")
            initial = get_eval_run(task_id, db_path=db_path)
            if not initial:
                raise EvalGateError(409, "eval_not_found", "沙箱评测记录不存在。")
            live = compute_publish_env_snapshot(db_path=db_path)
            if any(live[key] != initial[key] for key in live):
                raise EvalGateError(409, "eval_env_stale", "评测开始前运行资产或物料库已变化。")
            golden_bytes, _, golden_digest = load_runtime_golden()
            if golden_digest != live["eval_assets_digest"]:
                raise EvalGateError(409, "eval_env_stale", "复制运行黄金集时摘要发生变化。")
            sandbox_root = Path(temp_sandbox_root).resolve()
            golden_path = sandbox_root / "golden_input.json"
            golden_path.write_bytes(golden_bytes)
            base_env = _child_environment(sandbox_root, "baseline", baseline_config_dir, golden_path, golden_digest)
            cand_env = _child_environment(sandbox_root, "candidate", candidate_config_dir, golden_path, golden_digest)
            _snapshot_materials(db_path or _resolve_materials_db_path(), Path(base_env["MATERIALS_DB_PATH"]))
            shutil.copyfile(base_env["MATERIALS_DB_PATH"], cand_env["MATERIALS_DB_PATH"])
            _inject_candidate_rule_into_sandbox(
                rule_meta=rule_meta,
                candidate_config_dir=candidate_config_dir,
                candidate_db_path=cand_env["MATERIALS_DB_PATH"],
                candidate_llm_rule_row=candidate_llm_rule_row,
            )

            command = [sys.executable, "-m", "app.sandbox_evaluator", "--eval-mode", "sandbox_v2",
                       "--rule-json", rule_meta_json, "--chunk-size", "50"]
            cmd_baseline = command + ["--output-json", baseline_output_json]
            cmd_candidate = command + ["--output-json", candidate_output_json]

            # Baseline Subprocess with strict timeout
            _update_task_state(task_id, progress=40, message="正在子进程执行基线对比评测 (Baseline)...")
            remaining = task_deadline_mono - time.monotonic()
            if remaining <= 0:
                _fail_task_closed(task_id, message="评测任务已达总时长上限，终止执行。", error_code="eval_task_timeout", db_path=db_path)
                return
            try:
                proc_base = _run_eval_process(
                    cmd_baseline,
                    cwd=PROJECT_ROOT,
                    env=base_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=min(SUBPROCESS_TIMEOUT_SECONDS, remaining),
                )
            except subprocess.TimeoutExpired:
                _fail_task_closed(
                    task_id,
                    message=f"基线评测子进程超时（超过 {SUBPROCESS_TIMEOUT_SECONDS}s），已终止并回收。",
                    error_code="eval_baseline_timeout",
                    db_path=db_path,
                )
                return

            base_rc = proc_base.returncode
            if base_rc != 0:
                _fail_task_closed(
                    task_id,
                    message=f"基线评测子进程非零退出(rc={base_rc}): {(proc_base.stderr or proc_base.stdout or '')[:300]}",
                    error_code="eval_baseline_nonzero",
                    base_rc=base_rc,
                    db_path=db_path,
                )
                return

            # Private copies only: baseline and candidate share a catalog snapshot,
            # never a writable cache directory. load_cache still validates metadata.
            from app.vector_index import CACHE_FILE_NAMES, SNAPSHOT_FILE
            cache_copied = []
            for name in (*CACHE_FILE_NAMES, SNAPSHOT_FILE):
                source = Path(base_env["VECTOR_CACHE_DIR"]) / name
                if source.is_file():
                    shutil.copyfile(source, Path(cand_env["VECTOR_CACHE_DIR"]) / name)
                    cache_copied.append(name)

            # Candidate Subprocess with strict timeout
            _update_task_state(task_id, progress=70, message="正在子进程执行候选规则与全局保护集评测 (Candidate)...")
            remaining = task_deadline_mono - time.monotonic()
            if remaining <= 0:
                _fail_task_closed(
                    task_id,
                    message=f"评测任务总时长超过上限（{TASK_TIMEOUT_SECONDS}s），已终止。",
                    error_code="eval_task_timeout",
                    base_rc=base_rc,
                    db_path=db_path,
                )
                return
            try:
                proc_cand = _run_eval_process(
                    cmd_candidate,
                    cwd=PROJECT_ROOT,
                    env=cand_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=min(SUBPROCESS_TIMEOUT_SECONDS, remaining),
                )
            except subprocess.TimeoutExpired:
                _fail_task_closed(
                    task_id,
                    message=f"候选评测子进程超时（超过 {SUBPROCESS_TIMEOUT_SECONDS}s），已终止并回收。",
                    error_code="eval_candidate_timeout",
                    base_rc=base_rc,
                    db_path=db_path,
                )
                return

            cand_rc = proc_cand.returncode
            if cand_rc != 0:
                _fail_task_closed(
                    task_id,
                    message=f"候选评测子进程非零退出(rc={cand_rc}): {(proc_cand.stderr or proc_cand.stdout or '')[:300]}",
                    error_code="eval_candidate_nonzero",
                    base_rc=base_rc,
                    cand_rc=cand_rc,
                    db_path=db_path,
                )
                return

            _update_task_state(task_id, progress=90, message="评测完成，正在校验定向、全局保护与绑定决策指标...")

            if not (os.path.exists(baseline_output_json) and os.path.exists(candidate_output_json)):
                err_msg = (proc_cand.stderr or proc_base.stderr or "")[:300]
                _fail_task_closed(
                    task_id,
                    message=f"评测子进程未生成输出结果: {err_msg}",
                    error_code="eval_output_missing",
                    base_rc=base_rc,
                    cand_rc=cand_rc,
                    db_path=db_path,
                )
                return

            try:
                with open(baseline_output_json, "r", encoding="utf-8") as f:
                    base_data = json.load(f)
                with open(candidate_output_json, "r", encoding="utf-8") as f:
                    cand_data = json.load(f)
            except Exception as exc:
                _fail_task_closed(
                    task_id,
                    message=f"评测输出 JSON 损坏或不可解析: {exc}",
                    error_code="eval_corrupt_json",
                    base_rc=base_rc,
                    cand_rc=cand_rc,
                    db_path=db_path,
                )
                return

            if not isinstance(base_data, dict) or not isinstance(cand_data, dict) or "rows" not in cand_data:
                _fail_task_closed(
                    task_id,
                    message="评测输出缺少必需结构字段（rows/summary）",
                    error_code="eval_missing_fields",
                    base_rc=base_rc,
                    cand_rc=cand_rc,
                    db_path=db_path,
                )
                return

            base_rows = base_data.get("rows", [])
            cand_rows = cand_data.get("rows", [])
            targeted_passed, report = _compare_sandbox_rows(base_rows, cand_rows)

            snapshot = _compare_evaluation_snapshots(base_data, cand_data)
            snapshot_errors = list(snapshot.get("errors") or [])
            for result in (snapshot.get("baseline"), snapshot.get("candidate")):
                if not isinstance(result, dict):
                    continue
                for actual_key, expected_key in (("golden_set_sha256", "eval_assets_digest"),
                                                 ("materials_db_sha256", "materials_revision"),
                                                 ("model_version", "model_version")):
                    if result.get(actual_key) != initial[expected_key]:
                        snapshot_errors.append(f"子进程快照与评测凭据不符 ({actual_key})")
            if compute_publish_env_snapshot(db_path=db_path) != live:
                snapshot_errors.append("评测执行期间运行环境发生变化")
            report["snapshot"] = snapshot

            base_guard_rows = base_data.get("guard_rows", [])
            cand_guard_rows = cand_data.get("guard_rows", [])
            if cand_guard_rows or base_guard_rows:
                guard_passed, guard_report = _compare_sandbox_rows(base_guard_rows, cand_guard_rows)
            else:
                guard_passed = False
                guard_report = {
                    "passed": False,
                    "targeted_count": 0,
                    "improved_count": 0,
                    "degraded_count": 0,
                    "degraded_from_top1": 0,
                    "violations": [{"type": "missing_guard_assets"}],
                    "integrity_errors": ["全局保护集为空或物料库缺少可验证基线样本"],
                    "reason": "门禁拦截：缺少可验证全局保护样本",
                }

            binding_res = _compare_binding_pipeline_rows(
                base_data.get("binding_rows", []),
                cand_data.get("binding_rows", []),
            )

            cand_summary = cand_data.get("summary") or {}
            rule_execution_hits = int(cand_summary.get("rule_execution_hits") or 0)
            binding_checks_count = int(cand_summary.get("binding_checks_count") or binding_res["checked_count"])

            all_integrity_errors = (
                list(report.get("integrity_errors") or [])
                + snapshot_errors
                + list(guard_report.get("integrity_errors") or [])
                + list(binding_res.get("integrity_errors") or [])
                + [f"绑定求值异常 ({side}/{row.get('id')}): {row['error']}"
                   for side, data in (("baseline", base_data), ("candidate", cand_data))
                   for row in data.get("binding_rows", []) if row.get("error")]
            )
            if report.get("targeted_count", 0) > 0 and rule_execution_hits <= 0:
                all_integrity_errors.append("规则在定向与全局保护集中命中次数为 0（未实际执行生效）")

            total_degraded = (
                int(report.get("degraded_count") or 0)
                + int(guard_report.get("degraded_count") or 0)
                + int(binding_res.get("degraded_count") or 0)
            )
            total_top1_degraded = int(report.get("degraded_from_top1") or 0) + int(guard_report.get("degraded_from_top1") or 0)
            total_improved = int(report.get("improved_count") or 0) + int(binding_res.get("improved_count") or 0)
            has_violations = bool(report.get("violations") or guard_report.get("violations") or binding_res.get("violations"))

            if report.get("targeted_count", 0) == 0 or guard_report.get("targeted_count", 0) == 0 or all_integrity_errors:
                safety_passed = False
                benefit = "invalid"
            elif not targeted_passed or not guard_passed or not binding_res.get("passed") or has_violations or total_degraded > 0 or total_top1_degraded > 0:
                safety_passed = False
                benefit = "harmful"
            elif total_improved > 0:
                safety_passed = True
                benefit = "improved"
            else:
                safety_passed = True
                benefit = "neutral"

            if benefit == "invalid":
                reason = "门禁拦截（无效/未命中）：" + ("；".join(all_integrity_errors) if all_integrity_errors else report.get("reason", "定向/保护集不足"))
            elif benefit == "harmful":
                if not binding_res.get("passed"):
                    reason = f"门禁拦截（绑定/冲突退化）：触发 {len(binding_res['violations'])} 例绑定错误或硬冲突"
                elif not guard_passed:
                    reason = f"门禁拦截（全局保护退化）：{guard_report.get('reason', '全局保护集发生退化')}"
                else:
                    reason = report.get("reason", "门禁拦截：发生退化")
            elif benefit == "neutral":
                reason = report.get("reason", "门禁通过：稳态无退化（中性收益，确需启用须人工审批并填写中性理由）")
            else:
                reason = (
                    f"门禁通过（有效改善）：match改善 {report.get('improved_count', 0)} 例，"
                    f"绑定改善 {binding_res.get('improved_count', 0)} 例，命中 {rule_execution_hits} 次，"
                    f"全局保护 {guard_report.get('targeted_count', 0)} 例零退化"
                )

            report.update({
                "passed": safety_passed,
                "safety_passed": safety_passed,
                "benefit": benefit,
                "guard_count": int(guard_report.get("targeted_count") or 0),
                "guard_passed": bool(guard_passed),
                "guard_degraded_count": int(guard_report.get("degraded_count") or 0),
                "guard_violations": guard_report.get("violations") or [],
                "binding_checks_count": binding_checks_count,
                "binding_improved_count": int(binding_res.get("improved_count") or 0),
                "binding_degraded_count": int(binding_res.get("degraded_count") or 0),
                "binding_violations": binding_res.get("violations") or [],
                "rule_execution_hits": rule_execution_hits,
                "failed_samples_count": len({(scope, row.get("id"))
                    for data in (base_data, cand_data) for scope in ("rows", "guard_rows")
                    for row in data.get(scope, []) if row.get("error")}
                    | {("guard_rows" if row.get("is_guard") else "rows", row.get("id"))
                       for data in (base_data, cand_data) for row in data.get("binding_rows", []) if row.get("error")}),
                "skipped_samples_count": max(int((data.get("summary") or {}).get("skipped_samples_count") or 0)
                                             for data in (base_data, cand_data)),
                "baseline_metrics": (base_data.get("summary") or {}).get("overall", {}),
                "candidate_metrics": cand_summary.get("overall", {}),
                "baseline_guard_metrics": (base_data.get("summary") or {}).get("guard_overall", {}),
                "candidate_guard_metrics": cand_summary.get("guard_overall", {}),
                "candidate_diagnostics": [{"id": row.get("id"), "scope": scope, "diagnostics": row.get("diagnostics")}
                    for scope in ("rows", "guard_rows") for row in cand_data.get(scope, []) if row.get("diagnostics")],
                "cache_reused_files": cache_copied,
                "skipped_samples": cand_summary.get("skipped_samples", []),
                "integrity_errors": all_integrity_errors,
                "reason": reason,
            })

            checkpoint()
            finalize_eval_run_record(
                task_id,
                status="completed",
                safety_passed=safety_passed,
                benefit_class=benefit,
                targeted_count=int(report.get("targeted_count") or 0),
                guard_count=int(report.get("guard_count") or 0),
                improved_count=total_improved,
                degraded_count=total_degraded,
                degraded_from_top1=total_top1_degraded,
                failed_samples_count=int(report.get("failed_samples_count") or 0),
                skipped_samples_count=int(report.get("skipped_samples_count") or 0),
                rule_execution_hits=rule_execution_hits,
                binding_checks_count=binding_checks_count,
                baseline_process_exit=base_rc,
                candidate_process_exit=cand_rc,
                result_summary=report,
                error_message="" if safety_passed else reason,
                db_path=db_path,
            )

            _update_task_state(
                task_id,
                status="completed",
                progress=100,
                passed=safety_passed,
                safety_passed=safety_passed,
                benefit=benefit,
                message=reason,
                report=report,
            )
        except JobStopped as exc:
            _fail_task_closed(task_id, message=str(exc), error_code="eval_stopped",
                              base_rc=base_rc, cand_rc=cand_rc, db_path=db_path)
        except (EvalGateError, RuntimeAssetError) as exc:
            _fail_task_closed(task_id, message=str(exc), error_code=exc.code,
                              base_rc=base_rc, cand_rc=cand_rc, db_path=db_path)
        except Exception as e:
            logger.error("Error in sandbox evaluation task %s (%s): %s", task_id, type(e).__name__, e)
            _fail_task_closed(
                task_id,
                message=f"沙箱评测执行异常: {type(e).__name__}: {e}",
                error_code="eval_runtime_error",
                base_rc=base_rc,
                cand_rc=cand_rc,
                db_path=db_path,
            )
        finally:
            try:
                shutil.rmtree(temp_sandbox_root, ignore_errors=True)
            finally:
                active.__exit__(None, None, None)


def start_governed_evaluation(
    *,
    rule_source: str,
    rule_key: str,
    rule_type: str,
    normalized_spec: Dict[str, Any],
    rule_revision: int = 1,
    owner_user: Optional[Dict[str, Any]] = None,
    candidate_llm_rule_row: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> str:
    """Create persistent eval credential record and launch async isolated sandbox subprocess evaluation."""
    task_id = f"eval_{uuid.uuid4().hex[:12]}"
    job = eval_jobs.reserve(TASK_TIMEOUT_SECONDS, task_id)
    try:
        asset_error = None
        try:
            env_snap = compute_publish_env_snapshot(db_path=db_path)
        except EvalGateError as exc:
            asset_error = exc
        else:
            create_eval_run_record(
                eval_id=task_id,
                rule_source=rule_source,
                rule_key=str(rule_key),
                rule_type=rule_type,
                rule_revision=int(rule_revision or 1),
                normalized_spec=normalized_spec,
                env_snapshot=env_snap,
                owner_user=owner_user,
                db_path=db_path,
            )

        owner_uid = int(owner_user["id"]) if isinstance(owner_user, dict) and owner_user.get("id") is not None else None
        owner_epoch = str(owner_user.get("account_epoch") or "") if isinstance(owner_user, dict) else ""
        with _tasks_lock:
            prune_terminal_tasks(_eval_tasks, MAX_EVAL_TASKS_CACHE, terminal_ttl())

            _eval_tasks[task_id] = {
                "task_id": task_id,
                "eval_id": task_id,
                "owner_user_id": owner_uid,
                "owner_account_epoch": owner_epoch,
                "rule_source": rule_source,
                "rule_key": str(rule_key),
                "rule_revision": int(rule_revision or 1),
                "payload_digest": compute_rule_digest(normalized_spec),
                "status": "pending",
                "created_time": time.time(),
                "progress": 0,
                "message": "已排队等待沙箱评测...",
                "passed": False,
                "safety_passed": False,
                "benefit": "invalid",
                "report": None,
            }

        if asset_error is not None:
            _update_task_state(task_id, status="failed", progress=100, error_code=asset_error.code,
                               message=str(asset_error), report={"safety_passed": False, "benefit": "invalid",
                                                                 "error_code": asset_error.code, "reason": str(asset_error)})
            job.release()
            return task_id

    except BaseException:
        job.release()
        raise

    def work():
        try:
            checkpoint()
            _run_sandbox_worker(
                task_id, dict(normalized_spec), task_deadline_mono=job.deadline,
                candidate_llm_rule_row=candidate_llm_rule_row, db_path=db_path,
            )
        except Exception as exc:
            _fail_task_closed(task_id, message=str(exc), error_code="eval_worker_stopped", db_path=db_path)
        finally:
            job.release()

    try:
        job.submit(work)
    except Exception as exc:
        job.release()
        _fail_task_closed(task_id, message=str(exc), error_code="eval_dispatch_failed", db_path=db_path)
        raise
    return task_id


def start_sandbox_evaluation(suggestion: Dict[str, Any], owner_user: Optional[Dict[str, Any]] = None) -> str:
    """Entrypoint for flywheel suggestions and sandbox regression calls."""
    sug_type = str(suggestion.get("suggestion_type", "ALIAS") or "ALIAS").upper()
    source_pattern = str(suggestion.get("source_pattern", "") or "").strip()
    target_val = suggestion.get("target_value", "")
    disc = str(suggestion.get("discipline", "") or "").strip()
    sug_id = suggestion.get("id")
    rule_source = "flywheel_binding" if sug_type in ("BINDING", "BINDING_RULE") else "flywheel_alias"
    rule_key = f"suggestion:{sug_id}" if sug_id is not None else f"direct:{source_pattern}"

    norm_spec = normalize_rule_spec(
        rule_source=rule_source,
        rule_key=rule_key,
        rule_type=sug_type,
        revision=int(suggestion.get("revision") or 1),
        discipline=disc,
        source_pattern=source_pattern,
        target_value=target_val if target_val is not None else "",
        original_spec=str(suggestion.get("original_spec") or ""),
        require_concrete_discipline=False,
    )
    return start_governed_evaluation(
        rule_source=rule_source,
        rule_key=rule_key,
        rule_type=norm_spec["rule_type"],
        normalized_spec=norm_spec,
        rule_revision=1,
        owner_user=owner_user,
    )
