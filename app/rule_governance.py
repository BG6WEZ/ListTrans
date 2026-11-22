# -*- coding: utf-8 -*-
"""
Unified Rule Governance & Mandatory Publish Gate (app/rule_governance.py).
Implements V016-M2 (M2-C01 .. M2-C08):
- Unified content normalization & SHA256 payload digest across manual DSL, legacy bindings, and flywheel rules.
- Live environment/asset snapshot binding (materials catalog, config, baseline rules, algo, model, evaluator, golden/guard assets).
- Persistent SQLite eval credentials (rule_eval_runs) with fixed 24h server TTL.
- Strict separation of safety_passed vs benefit (improved / neutral / harmful / invalid).
- Fail-closed recovery blocking when file/DB/reload compensation fails.
"""
import datetime
import hashlib
import importlib
import json
import math
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.flywheel_db import init_flywheel_tables, open_flywheel_tx_connection
from app.rule_context import DYNAMIC_UNIVERSAL_TOKENS, canonicalize_professional
from app.runtime_eval_assets import RuntimeAssetError, evaluator_digest, load_report_template, load_runtime_golden, runtime_model_state

EVALUATOR_VERSION = "v016-m3-eval-v1"
MIGRATION_VERSION = "v0.16-m2-v1"
EVAL_TTL_SECONDS = 24 * 3600
SUBPROCESS_TIMEOUT_SECONDS = 300
TASK_TIMEOUT_SECONDS = 660

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PUBLISH_GATE_LOCK = threading.RLock()
_RECOVERY_LOCK = threading.Lock()
_RECOVERY_BLOCK_BY_KEY: Dict[str, Dict[str, Any]] = {}


class EvalGateError(Exception):
    """Structured fail-closed error for unified rule evaluation and publish gates."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = code
        self.message = message
        self.details: Dict[str, Any] = details or {}

    def to_detail(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "error": self.code,
            "message": self.message,
            "details": self.details,
        }


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve_config_dir() -> Path:
    env_dir = os.getenv("CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    return _PROJECT_ROOT / "config"


def _resolve_materials_db_path() -> str:
    db_mod = importlib.import_module("app.db_manager")
    resolver = getattr(db_mod, "_resolve_db_path", None)
    return str(resolver() if callable(resolver) else db_mod.DB_PATH)


def _recovery_marker_path() -> Path:
    cfg_dir = _resolve_config_dir().resolve()
    return cfg_dir / ".rule_publish_recovery_blocked.json"


def _recovery_scope_key() -> str:
    return f"{os.path.abspath(_resolve_materials_db_path())}|{_resolve_config_dir().resolve()}"


def get_rule_recovery_block_info() -> Optional[Dict[str, Any]]:
    key = _recovery_scope_key()
    with _RECOVERY_LOCK:
        if key in _RECOVERY_BLOCK_BY_KEY:
            return dict(_RECOVERY_BLOCK_BY_KEY[key])
    marker = _recovery_marker_path()
    if marker.is_file():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("blocked"):
                return data
        except Exception:
            return {"blocked": True, "marker_path": str(marker)}
    return None


def is_rule_publish_blocked() -> bool:
    return get_rule_recovery_block_info() is not None


def block_rule_publish_recovery(reason: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    key = _recovery_scope_key()
    payload = {
        "blocked": True,
        "reason": str(reason or "规则发布补偿恢复失败，已锁定规则发布入口。"),
        "details": details or {},
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with _RECOVERY_LOCK:
        _RECOVERY_BLOCK_BY_KEY[key] = payload
    marker = _recovery_marker_path()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return payload


def clear_rule_publish_block_on_verified_recovery() -> None:
    key = _recovery_scope_key()
    with _RECOVERY_LOCK:
        _RECOVERY_BLOCK_BY_KEY.pop(key, None)
    marker = _recovery_marker_path()
    if marker.is_file():
        marker.unlink()


def reset_rule_publish_block() -> None:
    """Isolated test cleanup helper; production callers use verified recovery."""
    key = _recovery_scope_key()
    with _RECOVERY_LOCK:
        _RECOVERY_BLOCK_BY_KEY.pop(key, None)
    marker = _recovery_marker_path()
    if marker.is_file():
        try:
            marker.unlink()
        except OSError:
            pass


def assert_rule_publish_not_blocked() -> None:
    info = get_rule_recovery_block_info()
    if info:
        raise EvalGateError(
            409,
            "rule_recovery_blocked",
            f"规则发布处于恢复失败写入阻断状态，需人工核验文件/DB/内存一致性后解除：{info.get('reason') or ''}",
            details=info,
        )


def validate_concrete_discipline(raw_discipline: Any, *, origin: str = "") -> str:
    """Ensure governed aliases/bindings carry a real non-universal discipline (no silent upgrade to 通用)."""
    if str(origin or "").strip() in ("legacy_unscoped", "legacy_unknown"):
        raise EvalGateError(
            422,
            "unscoped_legacy_rule",
            "缺失专业作用域的历史遗留建议（legacy_unscoped/legacy_unknown）禁止直接升级为可信规则。",
            details={"source_origin": origin},
        )
    canon = canonicalize_professional(str(raw_discipline or "").strip())
    if not canon or canon.lower() in DYNAMIC_UNIVERSAL_TOKENS or canon == "其它":
        raise EvalGateError(
            422,
            "missing_discipline_scope",
            "全局别名或绑定规则必须绑定明确专业（如暖通/给排水/强电/医气），禁止缺专业或升级为“通用”。",
            details={"discipline": str(raw_discipline or "")},
        )
    return canon


def _normalize_bound_materials(bound_materials: Any) -> List[Dict[str, Any]]:
    if isinstance(bound_materials, str):
        try:
            bound_materials = json.loads(bound_materials)
        except Exception as exc:
            raise EvalGateError(
                422,
                "invalid_binding_target",
                f"绑定目标 JSON 解析失败: {exc}",
            ) from exc
    if not isinstance(bound_materials, list) or not bound_materials:
        raise EvalGateError(422, "invalid_binding_target", "绑定规则必须包含至少一项有效物料目标。")

    norm_list: List[Dict[str, Any]] = []
    for item in bound_materials:
        if not isinstance(item, dict):
            raise EvalGateError(422, "invalid_binding_target", "绑定物料条目格式非法。")
        code = str(item.get("code") or "").strip()
        if not code:
            raise EvalGateError(422, "invalid_binding_target", "绑定物料编码不能为空。")
        raw_ratio = item.get("qty_ratio") if item.get("qty_ratio") is not None else item.get("weight", 1.0)
        try:
            if isinstance(raw_ratio, bool):
                raise ValueError("boolean ratio")
            ratio = float(raw_ratio)
        except (TypeError, ValueError) as exc:
            raise EvalGateError(422, "invalid_binding_target", f"绑定数量倍率非法: {item.get('qty_ratio')}") from exc
        if not math.isfinite(ratio) or round(ratio, 6) <= 0:
            raise EvalGateError(422, "invalid_binding_target", f"绑定数量倍率必须为正数: {ratio}")
        norm_list.append({
            "code": code,
            "name": str(item.get("name") or "").strip(),
            "specification": str(item.get("specification") or item.get("spec") or "").strip(),
            "unit": str(item.get("unit") or "").strip(),
            "brand": str(item.get("brand") or "").strip(),
            "category": str(item.get("category") or "").strip(),
            "qty_ratio": round(ratio, 6),
        })
    return norm_list


def normalize_rule_spec(
    *,
    rule_source: str,
    rule_key: str,
    rule_type: str,
    revision: int = 1,
    discipline: str = "",
    scope_level: str = "",
    scope_target: str = "",
    priority: int = 10,
    source_pattern: str = "",
    target_value: Any = "",
    original_spec: str = "",
    parsed_dsl: Optional[Dict[str, Any]] = None,
    rule_name: str = "",
    source_origin: str = "",
    require_concrete_discipline: bool = False,
) -> Dict[str, Any]:
    """Build deterministic canonical representation of a rule's effective execution semantics."""
    src = str(rule_source or "").strip()
    rkey = str(rule_key or "").strip()
    rtype = str(rule_type or "").strip().upper()
    rev = int(revision or 1)

    if rtype == "LLM_DSL":
        sc_level = str(scope_level or "category").strip()
        sc_target = str(scope_target or "").strip()
        raw_disc = str(discipline or "").strip()
        if not raw_disc and sc_level == "discipline":
            raw_disc = sc_target
        if require_concrete_discipline:
            if sc_level == "universal":
                raise EvalGateError(
                    422,
                    "missing_discipline_scope",
                    "手工规则不得设置为“通用/全局”作用域，必须绑定明确专业。",
                    details={"scope_level": sc_level},
                )
            canon_disc = validate_concrete_discipline(raw_disc, origin=source_origin)
        else:
            canon_disc = canonicalize_professional(raw_disc) if raw_disc else ""
        return {
            "rule_source": src,
            "rule_key": rkey,
            "rule_type": "LLM_DSL",
            "revision": rev,
            "rule_name": str(rule_name or "").strip(),
            "scope_level": sc_level,
            "scope_target": sc_target,
            "discipline": canon_disc,
            "priority": int(priority if priority is not None else 10),
            "parsed_dsl": parsed_dsl if isinstance(parsed_dsl, dict) else {},
        }

    if rtype == "ALIAS":
        canon_disc = (
            validate_concrete_discipline(discipline, origin=source_origin)
            if require_concrete_discipline
            else (canonicalize_professional(str(discipline or "").strip()) if discipline else "")
        )
        src_pat = str(source_pattern or "").strip()
        tgt = str(target_value or "").strip()
        if not src_pat or not tgt:
            raise EvalGateError(422, "invalid_alias_rule", "别名映射的原始词与目标词均不能为空。")
        return {
            "rule_source": src,
            "rule_key": rkey,
            "rule_type": "ALIAS",
            "revision": rev,
            "scope_level": "discipline",
            "scope_target": canon_disc,
            "discipline": canon_disc,
            "source_pattern": src_pat,
            "target_value": tgt,
        }

    if rtype in ("BINDING", "BINDING_RULE"):
        canon_disc = (
            validate_concrete_discipline(discipline, origin=source_origin)
            if require_concrete_discipline
            else (canonicalize_professional(str(discipline or "").strip()) if discipline else "")
        )
        orig_name = str(source_pattern or "").strip()
        if not orig_name:
            raise EvalGateError(422, "invalid_binding_rule", "绑定规则原始名称不能为空。")
        norm_targets = _normalize_bound_materials(target_value)
        result = {
            "rule_source": src,
            "rule_key": rkey,
            "rule_type": "BINDING_RULE",
            "revision": rev,
            "scope_level": "discipline",
            "scope_target": canon_disc,
            "discipline": canon_disc,
            "original_name": orig_name,
            "original_spec": str(original_spec or "").strip(),
            "bound_materials": norm_targets,
        }
        from app.bom import is_bom_name, rule_metadata

        if is_bom_name(orig_name):
            try:
                if src != "legacy_binding":
                    raise ValueError("BOM 规则须由管理员通过绑定规则评测入口明确声明")
                result["bom"] = rule_metadata(orig_name, result["original_spec"], canon_disc, norm_targets)
            except ValueError as exc:
                raise EvalGateError(422, "invalid_bom_rule", str(exc)) from exc
        return result

    raise EvalGateError(400, "unsupported_rule_type", f"不支持的规则类型: {rtype}")


def compute_rule_digest(normalized_spec: Dict[str, Any]) -> str:
    return _sha256_text(canonical_json(normalized_spec))


def compute_materials_catalog_digest(db_path: Optional[str] = None) -> str:
    """Compute semantic SHA256 digest of the active materials table (ignores unrelated flywheel/audit tables)."""
    target = db_path or _resolve_materials_db_path()
    if not os.path.exists(target):
        return "materials_missing"
    conn = sqlite3.connect(Path(target).resolve().as_uri() + "?mode=ro", uri=True, timeout=15.0)
    try:
        has_tbl = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='materials'"
        ).fetchone()
        if not has_tbl:
            return "materials_no_table"
        h = hashlib.sha256()
        count = 0
        for row in conn.execute(
            "SELECT code, name, specification, unit, brand, category, parsed_params FROM materials ORDER BY code ASC"
        ):
            count += 1
            line = canonical_json([
                str(row[0] or ""),
                str(row[1] or ""),
                str(row[2] or ""),
                str(row[3] or ""),
                str(row[4] or ""),
                str(row[5] or ""),
                str(row[6] or ""),
            ])
            h.update(line.encode("utf-8"))
            h.update(b"\n")
        return f"{count}:{h.hexdigest()}"
    finally:
        conn.close()


def _compute_config_dir_digest(config_dir: Optional[Path] = None) -> str:
    cfg = config_dir or _resolve_config_dir()
    entries: Dict[str, str] = {}
    if cfg.is_dir():
        for fp in sorted(cfg.glob("*.json"), key=lambda p: p.name):
            raw = fp.read_bytes()
            try:
                parsed = json.loads(raw.decode("utf-8"))
                entries[fp.name] = _sha256_text(canonical_json(parsed))
            except Exception:
                entries[fp.name] = _sha256_bytes(raw)
    return _sha256_text(canonical_json(entries))


def _compute_baseline_published_rules_digest(db_path: Optional[str] = None, config_digest: str = "") -> str:
    """Capture currently active baseline rules across llm_rules + file configs."""
    target = db_path or _resolve_materials_db_path()
    published: List[List[Any]] = []
    if os.path.exists(target):
        conn = sqlite3.connect(target, timeout=15.0)
        try:
            has_tbl = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_rules'"
            ).fetchone()
            if has_tbl:
                for row in conn.execute(
                    """
                    SELECT rule_id, COALESCE(revision, 1), COALESCE(content_digest, ''), scope_level,
                           COALESCE(scope_target, ''), COALESCE(discipline, ''), COALESCE(priority, 10)
                    FROM llm_rules
                    WHERE status = 'published'
                    ORDER BY rule_id ASC
                    """
                ):
                    published.append(list(row))
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
    return _sha256_text(canonical_json({"config_digest": config_digest, "published_llm_rules": published}))


def compute_publish_env_snapshot(db_path: Optional[str] = None) -> Dict[str, str]:
    """Compute live authoritative environment/asset fingerprint for evaluation binding & stale invalidation."""
    materials_rev = require_materials_catalog(db_path=db_path)
    fc = importlib.import_module("app.feedback_contract")
    versions = fc.compute_runtime_versions()
    cfg_digest = _compute_config_dir_digest()
    baseline_rel = _compute_baseline_published_rules_digest(db_path=db_path, config_digest=cfg_digest)

    try:
        _, _, eval_assets_digest = load_runtime_golden()
        _, template_digest = load_report_template()
        eval_version = f"{EVALUATOR_VERSION}:{evaluator_digest()}"
    except RuntimeAssetError as exc:
        raise EvalGateError(409, exc.code, str(exc)) from exc
    guard_assets_digest = _sha256_text(
        canonical_json({
            "eval_assets_digest": eval_assets_digest,
            "evaluator_version": eval_version,
            "template_digest": template_digest,
            "guard_mode": "stratified_active_materials_v1",
        })
    )
    return {
        "baseline_release_version": baseline_rel,
        "materials_revision": materials_rev,
        "config_digest": cfg_digest,
        "algo_version": str(versions.get("algo_version") or ""),
        "model_version": runtime_model_state()["model_version"],
        "evaluator_version": eval_version,
        "eval_assets_digest": eval_assets_digest,
        "guard_assets_digest": guard_assets_digest,
    }


def require_materials_catalog(db_path: Optional[str] = None) -> str:
    try:
        digest = compute_materials_catalog_digest(db_path=db_path)
    except (OSError, sqlite3.Error) as exc:
        raise EvalGateError(409, "materials_invalid", "当前物料库无法读取或结构不完整。") from exc
    if digest in ("materials_missing", "materials_no_table") or digest.startswith("0:"):
        raise EvalGateError(409, "materials_unavailable", "当前物料库缺失、未初始化或没有物料。")
    return digest


def load_scoped_aliases(config_dir: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    cfg = Path(config_dir) if config_dir else _resolve_config_dir()
    fp = cfg / "scoped_aliases.json"
    if not fp.is_file():
        return {}
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        out: Dict[str, Dict[str, str]] = {}
        for k, v in data.items():
            if isinstance(k, str) and isinstance(v, dict):
                canon_k = canonicalize_professional(k)
                if canon_k:
                    bucket = out.setdefault(canon_k, {})
                    for src, tgt in v.items():
                        if isinstance(src, str) and isinstance(tgt, str):
                            src = src.strip()
                            if src:
                                bucket[src] = tgt
        return out
    except Exception:
        return {}


def create_eval_run_record(
    *,
    eval_id: str,
    rule_source: str,
    rule_key: str,
    rule_type: str,
    rule_revision: int,
    normalized_spec: Dict[str, Any],
    env_snapshot: Dict[str, str],
    owner_user: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    init_flywheel_tables(db_path=db_path)
    payload_json = canonical_json(normalized_spec)
    payload_digest = compute_rule_digest(normalized_spec)
    now_ep = time.time()
    owner_uid = int(owner_user["id"]) if isinstance(owner_user, dict) and owner_user.get("id") is not None else None
    owner_epoch = str(owner_user.get("account_epoch") or "") if isinstance(owner_user, dict) else ""

    conn = open_flywheel_tx_connection(db_path=db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO rule_eval_runs (
                eval_id, rule_source, rule_key, rule_type, rule_revision,
                normalized_payload_json, payload_digest,
                baseline_release_version, materials_revision, config_digest,
                algo_version, model_version, evaluator_version,
                eval_assets_digest, guard_assets_digest,
                status, safety_passed, benefit_class,
                owner_user_id, owner_account_epoch, created_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 'invalid', ?, ?, ?)
            """,
            (
                eval_id,
                rule_source,
                str(rule_key),
                rule_type,
                int(rule_revision),
                payload_json,
                payload_digest,
                env_snapshot["baseline_release_version"],
                env_snapshot["materials_revision"],
                env_snapshot["config_digest"],
                env_snapshot["algo_version"],
                env_snapshot["model_version"],
                env_snapshot["evaluator_version"],
                env_snapshot["eval_assets_digest"],
                env_snapshot["guard_assets_digest"],
                owner_uid,
                owner_epoch,
                now_ep,
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return {"eval_id": eval_id, "payload_digest": payload_digest, **env_snapshot}


def mark_eval_run_running(eval_id: str, db_path: Optional[str] = None) -> None:
    conn = open_flywheel_tx_connection(db_path=db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE rule_eval_runs SET status = 'running' WHERE eval_id = ? AND status = 'pending'",
            (eval_id,),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
    finally:
        conn.close()


def finalize_eval_run_record(
    eval_id: str,
    *,
    status: str,
    safety_passed: bool,
    benefit_class: str,
    targeted_count: int = 0,
    guard_count: int = 0,
    improved_count: int = 0,
    degraded_count: int = 0,
    degraded_from_top1: int = 0,
    failed_samples_count: int = 0,
    skipped_samples_count: int = 0,
    rule_execution_hits: int = 0,
    binding_checks_count: int = 0,
    baseline_process_exit: Optional[int] = None,
    candidate_process_exit: Optional[int] = None,
    result_summary: Optional[Dict[str, Any]] = None,
    error_message: str = "",
    db_path: Optional[str] = None,
) -> None:
    completed_ep = time.time()
    expires_ep = completed_ep + EVAL_TTL_SECONDS
    completed_iso = datetime.datetime.fromtimestamp(completed_ep).strftime("%Y-%m-%d %H:%M:%S")
    expires_iso = datetime.datetime.fromtimestamp(expires_ep).strftime("%Y-%m-%d %H:%M:%S")
    safe_summary = json.dumps(result_summary or {}, ensure_ascii=False)

    conn = open_flywheel_tx_connection(db_path=db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE rule_eval_runs
            SET status = ?,
                safety_passed = ?,
                benefit_class = ?,
                targeted_count = ?,
                guard_count = ?,
                improved_count = ?,
                degraded_count = ?,
                degraded_from_top1 = ?,
                failed_samples_count = ?,
                skipped_samples_count = ?,
                rule_execution_hits = ?,
                binding_checks_count = ?,
                baseline_process_exit = ?,
                candidate_process_exit = ?,
                result_summary_json = ?,
                error_message = ?,
                completed_at_epoch = ?,
                expires_at_epoch = ?,
                completed_at = ?,
                expires_at = ?
            WHERE eval_id = ?
            """,
            (
                status,
                1 if safety_passed else 0,
                benefit_class,
                int(targeted_count),
                int(guard_count),
                int(improved_count),
                int(degraded_count),
                int(degraded_from_top1),
                int(failed_samples_count),
                int(skipped_samples_count),
                int(rule_execution_hits),
                int(binding_checks_count),
                baseline_process_exit,
                candidate_process_exit,
                safe_summary,
                str(error_message or "")[:500],
                completed_ep,
                expires_ep,
                completed_iso,
                expires_iso,
                eval_id,
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def get_eval_run(eval_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not str(eval_id or "").strip():
        return None
    target = Path(db_path or _resolve_materials_db_path()).resolve()
    if not target.is_file():
        return None
    conn = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'rule_eval_runs' AND type = 'table'").fetchone():
            return None
        row = conn.execute("SELECT * FROM rule_eval_runs WHERE eval_id = ?", (str(eval_id).strip(),)).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as exc:
        raise EvalGateError(409, "eval_store_unavailable", "评测凭据库不可读取，禁止发布。") from exc
    finally:
        conn.close()


def verify_eval_credential_for_publish(
    *,
    eval_id: Optional[str],
    rule_source: str,
    rule_key: str,
    rule_revision: int,
    normalized_spec: Dict[str, Any],
    neutral_reason: Optional[str] = None,
    auto_publish: bool = False,
    now_epoch: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Single authoritative gate enforcing M2-C01..C07 across manual DSL, binding, and flywheel sources."""
    assert_rule_publish_not_blocked()

    eid = str(eval_id or "").strip()
    if not eid:
        raise EvalGateError(
            409,
            "eval_gate_required",
            "缺少服务端沙箱评测凭据（eval_id），禁止跳过回归评测直接发布/采纳规则。",
        )

    require_materials_catalog(db_path=db_path)
    ev = get_eval_run(eid, db_path=db_path)
    if not ev:
        raise EvalGateError(
            409,
            "eval_not_found",
            f"指定的沙箱评测凭据 {eid} 不存在，拒绝发布。",
            details={"eval_id": eid},
        )

    if ev["rule_source"] != rule_source or str(ev["rule_key"]) != str(rule_key):
        raise EvalGateError(
            409,
            "eval_wrong_rule",
            "评测凭据归属其他规则或来源，禁止跨规则/跨任务借用凭据。",
            details={
                "eval_id": eid,
                "eval_rule_source": ev["rule_source"],
                "eval_rule_key": ev["rule_key"],
                "target_rule_source": rule_source,
                "target_rule_key": str(rule_key),
            },
        )

    if int(ev["rule_revision"] or 1) != int(rule_revision or 1):
        raise EvalGateError(
            409,
            "eval_revision_mismatch",
            f"规则修订已变更为 v{rule_revision}，旧修订评测 {eid} 已失效，请重新评测。",
        )

    if ev["status"] != "completed":
        raise EvalGateError(
            409,
            "eval_not_completed",
            f"评测任务 {eid} 当前状态为 '{ev['status']}'（非 completed），禁止发布。",
            details={"eval_id": eid, "status": ev["status"]},
        )

    current_digest = compute_rule_digest(normalized_spec)
    if ev["payload_digest"] != current_digest:
        raise EvalGateError(
            409,
            "eval_content_changed",
            "规则内容/目标/专业/作用域/优先级/数量倍率在评测后发生变更，旧评测凭据已失效，请重新评测。",
            details={"eval_id": eid, "eval_digest": ev["payload_digest"], "current_digest": current_digest},
        )

    now_ts = float(now_epoch if now_epoch is not None else time.time())
    comp_ep = ev.get("completed_at_epoch")
    exp_ep = ev.get("expires_at_epoch")
    if comp_ep is None or exp_ep is None or now_ts > float(exp_ep) or (now_ts - float(comp_ep)) > EVAL_TTL_SECONDS:
        raise EvalGateError(
            409,
            "eval_expired",
            f"评测凭据 {eid} 已超过 24 小时有效期，请重新执行沙箱评测。",
            details={"eval_id": eid, "expires_at": ev.get("expires_at")},
        )

    if not int(ev.get("safety_passed") or 0):
        raise EvalGateError(
            409,
            "eval_safety_failed",
            f"沙箱评测 {eid} 未通过安全门禁（存在退化、硬冲突、资产缺失、未命中或异常），禁止发布。",
            details={"eval_id": eid, "benefit_class": ev.get("benefit_class")},
        )

    benefit = str(ev.get("benefit_class") or "invalid")
    if benefit not in ("improved", "neutral"):
        raise EvalGateError(
            409,
            "eval_benefit_blocked",
            f"评测结果收益等级为 '{benefit}'，门禁一律阻断发布。",
            details={"eval_id": eid, "benefit_class": benefit},
        )

    clean_neutral_reason = str(neutral_reason or "").strip()
    if benefit == "neutral":
        if auto_publish:
            raise EvalGateError(
                409,
                "neutral_no_auto_publish",
                "中性（neutral）收益规则禁止自动发布。",
                details={"eval_id": eid},
            )
        if len(clean_neutral_reason) < 2:
            raise EvalGateError(
                422,
                "neutral_reason_required",
                "评测结果为中性稳态（无实际改善），确需启用必须经人工审批并填写明确的中性启用理由（neutral_reason）。",
                details={"eval_id": eid, "benefit_class": "neutral"},
            )

    live_env = compute_publish_env_snapshot(db_path=db_path)
    stale_fields = []
    for env_key in (
        "baseline_release_version",
        "materials_revision",
        "config_digest",
        "algo_version",
        "model_version",
        "evaluator_version",
        "eval_assets_digest",
        "guard_assets_digest",
    ):
        if ev.get(env_key) != live_env.get(env_key):
            stale_fields.append(env_key)

    if stale_fields:
        raise EvalGateError(
            409,
            "eval_env_stale",
            f"评测后运行环境快照已变化（{', '.join(stale_fields)}），旧评测结果已失效，请重新执行沙箱评测。",
            details={"eval_id": eid, "stale_fields": stale_fields},
        )

    return ev
