# -*- coding: utf-8 -*-
"""
V016-M0 / F05: 反馈身份、文档-工作表-源行归属、版本追溯与事件幂等/乱序契约模块。

依赖与导入副作用说明：
- 模块顶层仅导入 Python 标准库，无任何数据库建表、文件写入、模型加载或网络副作用。
- 业务模块（db_manager、auth_manager、flywheel_db、preprocessor、vector_index）
  均在运行期函数内通过 importlib 按需解析，确保测试隔离环境变量与重载安全。
"""
import hashlib
import importlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

CONTRACT_VERSION = "v0.1.6-m0-v1"

_ALGO_SOURCE_FILES = (
    "app/matcher.py",
    "app/bom.py",
    "app/binding_decisions.py",
    "app/server.py",
    "app/query_preprocessor.py",
    "app/preprocessor.py",
    "app/normalizer.py",
    "app/domain_extractors.py",
    "app/domain_router.py",
)


class FeedbackContractError(Exception):
    """Domain error carrying deterministic HTTP status and structured detail."""

    def __init__(self, status_code: int, code: str, message: str, extra: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = code
        self.message = message
        self.detail: Dict[str, Any] = {"error": code, "message": message}
        if extra:
            self.detail.update(extra)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_config_dir() -> Path:
    env_dir = os.getenv("CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    return _project_root() / "config"


def compute_runtime_versions() -> Dict[str, Any]:
    """Compute real, reproducible algorithm, model, and config versions from live state.

    Never returns hardcoded placeholders like 'v1.0' or 'snap_v1'.
    """
    root = _project_root()
    config_dir = _resolve_config_dir()

    # 1. Config digest over all JSON files in CONFIG_DIR + active published llm_rules
    config_entries: Dict[str, Any] = {}
    if config_dir.is_dir():
        for fp in sorted(config_dir.glob("*.json"), key=lambda p: p.name):
            try:
                raw_bytes = fp.read_bytes()
                parsed = json.loads(raw_bytes.decode("utf-8"))
                config_entries[fp.name] = {
                    "sha256": _sha256_bytes(_sha256_text(canonical_json(parsed)).encode("utf-8")),
                    "canonical_sha256": _sha256_text(canonical_json(parsed)),
                }
            except Exception:
                config_entries[fp.name] = {"raw_sha256": _sha256_bytes(fp.read_bytes())}

    published_rules_digest: List[Dict[str, Any]] = []
    try:
        db_mod = importlib.import_module("app.db_manager")
        conn = db_mod.get_db_connection()
        try:
            has_tbl = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_rules'"
            ).fetchone()
            if has_tbl:
                rows = conn.execute(
                    """
                    SELECT rule_id, version, scope_level, scope_target, discipline, parsed_dsl, updated_at
                    FROM llm_rules
                    WHERE status = 'published' AND is_active = 1
                    ORDER BY rule_id ASC
                    """
                ).fetchall()
                for r in rows:
                    published_rules_digest.append({
                        "rule_id": r["rule_id"],
                        "version": r["version"],
                        "scope_level": r["scope_level"],
                        "scope_target": r["scope_target"],
                        "discipline": r["discipline"],
                        "dsl_sha256": _sha256_text(str(r["parsed_dsl"] or "")),
                    })
        finally:
            conn.close()
    except Exception:
        published_rules_digest = []

    config_manifest = {
        "files": {k: v.get("canonical_sha256") or v.get("raw_sha256") for k, v in config_entries.items()},
        "published_llm_rules": published_rules_digest,
    }
    config_digest_full = _sha256_text(canonical_json(config_manifest))
    config_version = f"cfg:{config_digest_full[:16]}"

    # 2. Algorithm code digest over matching/preprocessing source modules
    algo_file_hashes: Dict[str, Optional[str]] = {}
    for rel in _ALGO_SOURCE_FILES:
        fp = root / rel
        algo_file_hashes[rel] = _sha256_bytes(fp.read_bytes()) if fp.is_file() else None
    algo_code_digest = _sha256_text(canonical_json(algo_file_hashes))
    algo_version = f"listtrans-0.1.6-m0|{algo_code_digest[:12]}"

    # 3. Model availability & real model identifier (never fake loaded when unavailable)
    model_version = "unavailable:vector_index_uninitialized"
    try:
        vi_mod = importlib.import_module("app.vector_index")
        mgr = getattr(vi_mod, "index_manager", None)
        if mgr is None:
            model_version = "unavailable:no_index_manager"
        else:
            model_name = str(getattr(mgr, "model_name_or_path", "") or "").strip() or "unknown"
            is_enabled = bool(getattr(mgr, "is_enabled", False))
            if not is_enabled:
                model_version = f"{model_name}|unavailable:vector_disabled"
            elif getattr(mgr, "model", None) is not None:
                model_version = f"{model_name}|loaded"
            elif (
                getattr(mgr, "numpy_embeddings", None) is not None
                or getattr(mgr, "faiss_index", None) is not None
            ):
                model_version = f"{model_name}|index_cached_model_unloaded"
            else:
                model_version = f"{model_name}|unavailable:not_loaded"
    except Exception as exc:
        model_version = f"unavailable:{type(exc).__name__}"

    return {
        "contract_version": CONTRACT_VERSION,
        "algo_version": algo_version,
        "algo_code_digest": algo_code_digest,
        "model_version": model_version,
        "config_version": config_version,
        "config_digest_full": config_digest_full,
    }


def extract_verified_actor(current_user: Any) -> Dict[str, Any]:
    """Extract immutable (user_id, account_epoch) and verify active account epoch in auth.db."""
    if not isinstance(current_user, dict):
        raise FeedbackContractError(401, "unauthenticated", "请先登录后再执行操作。")
    raw_id = current_user.get("id")
    raw_epoch = str(current_user.get("account_epoch") or "").strip()
    if raw_id is None or isinstance(raw_id, bool) or not raw_epoch:
        raise FeedbackContractError(401, "invalid_actor_identity", "认证主体缺少不可变用户ID或账号周期。")
    try:
        actor_id = int(raw_id)
    except (TypeError, ValueError):
        raise FeedbackContractError(401, "invalid_actor_identity", "认证主体用户ID无效。")
    if actor_id <= 0:
        raise FeedbackContractError(401, "invalid_actor_identity", "认证主体用户ID无效。")

    auth_mod = importlib.import_module("app.auth_manager")
    conn = auth_mod.get_auth_connection()
    try:
        row = conn.execute(
            "SELECT id, username, name, role, is_active, must_change_password, account_epoch FROM users WHERE id = ?",
            (actor_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row or not int(row["is_active"] or 0):
        raise FeedbackContractError(401, "inactive_or_missing_actor", "账号不存在或已停用，请重新登录。")
    db_epoch = str(row["account_epoch"] or "").strip()
    if not db_epoch or db_epoch != raw_epoch:
        raise FeedbackContractError(401, "stale_account_epoch", "账号凭据已失效（账号生命周期已变更），请重新登录。")
    if int(row["must_change_password"] or 0):
        raise FeedbackContractError(403, "must_change_password", "MUST_CHANGE_PASSWORD: 请先修改初始或重置密码。")

    return {
        "actor_user_id": int(row["id"]),
        "actor_account_epoch": db_epoch,
        "actor_username": str(row["username"] or ""),
        "actor_display_name": str(row["name"] or row["username"] or str(row["id"])),
        "role": str(row["role"] or "employee"),
    }


def _clean_text_norm(text: str, professional: str = "", material_category: str = "") -> str:
    try:
        pp = importlib.import_module("app.preprocessor")
        return (
            str(pp.clean_text(text or "", professional=professional, category=material_category) or "").strip()
            or str(text or "").strip()
        )
    except Exception:
        return str(text or "").strip()


def persist_upload_document_tree(
    actor_user: Dict[str, Any],
    document_id: str,
    upload_task_id: str,
    work_session_id: str,
    original_filename: str,
    file_sha256: str,
    raw_rows: List[Dict[str, Any]],
    parsed_rows: List[Dict[str, Any]],
    runtime_versions: Optional[Dict[str, Any]] = None,
    collection_boundary: str = "server_upload",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically persist document -> sheet -> source_row -> initial snapshot(rev=1) in materials.db.

    Mutates parsed_rows in-place to attach immutable server-issued identifiers and versions.
    """
    actor = extract_verified_actor(actor_user)
    fw_mod = importlib.import_module("app.flywheel_db")
    fw_mod.init_flywheel_tables(db_path=db_path)

    versions = runtime_versions or compute_runtime_versions()
    if len(raw_rows) != len(parsed_rows):
        raise FeedbackContractError(500, "upload_row_count_mismatch", "上传原始行与匹配结果数量不一致")

    # Precompute cleaned row names outside BEGIN IMMEDIATE to avoid nested rule DB queries under write lock
    precomputed_norms: List[str] = []
    for raw, res_row in zip(raw_rows, parsed_rows):
        r_name = str(res_row.get("original_name") if res_row.get("original_name") is not None else raw.get("name", ""))
        r_prof = str(res_row.get("professional") or raw.get("professional") or "暖通")
        r_cat = str(
            res_row.get("original_category")
            if res_row.get("original_category") is not None
            else raw.get("category", "")
        )
        precomputed_norms.append(_clean_text_norm(r_name, professional=r_prof, material_category=r_cat))

    conn = fw_mod.open_flywheel_tx_connection(db_path=db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO flywheel_documents (
                document_id, upload_task_id, work_session_id,
                actor_user_id, actor_account_epoch, original_filename,
                file_sha256, collection_boundary, contract_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                upload_task_id,
                work_session_id,
                actor["actor_user_id"],
                actor["actor_account_epoch"],
                str(original_filename or ""),
                str(file_sha256 or ""),
                collection_boundary,
                CONTRACT_VERSION,
            ),
        )

        sheet_map: Dict[Tuple[int, str], str] = {}
        for idx, (raw, res_row) in enumerate(zip(raw_rows, parsed_rows)):
            sheet_index = int(raw.get("sheet_index", 0) if raw.get("sheet_index") is not None else 0)
            sheet_name = str(raw.get("sheet_name") or res_row.get("sheet_name") or "Sheet1")
            source_row_num = int(raw.get("source_row_num") or res_row.get("source_row_num") or raw.get("row_idx") or (idx + 1))
            display_row_idx = int(res_row.get("original_row_idx") or raw.get("row_idx") or (idx + 1))

            sheet_key = (sheet_index, sheet_name)
            sheet_id = sheet_map.get(sheet_key)
            if not sheet_id:
                sheet_id = f"sht_{uuid.uuid4().hex[:16]}"
                sheet_map[sheet_key] = sheet_id
                cur.execute(
                    """
                    INSERT INTO flywheel_sheets (sheet_id, document_id, sheet_index, sheet_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    (sheet_id, document_id, sheet_index, sheet_name),
                )

            source_row_id = f"row_{uuid.uuid4().hex[:16]}"
            raw_name = str(res_row.get("original_name") if res_row.get("original_name") is not None else raw.get("name", ""))
            raw_spec = str(res_row.get("original_spec") if res_row.get("original_spec") is not None else raw.get("specification", ""))
            raw_unit = str(res_row.get("original_unit") if res_row.get("original_unit") is not None else raw.get("unit", ""))
            raw_qty = float(res_row.get("original_qty") if res_row.get("original_qty") is not None else (raw.get("qty") or 0.0))
            raw_loss = float(res_row.get("original_loss") if res_row.get("original_loss") is not None else (raw.get("loss") or 1.0))
            raw_category = str(res_row.get("original_category") if res_row.get("original_category") is not None else raw.get("category", ""))
            raw_brand = str(res_row.get("original_brand") if res_row.get("original_brand") is not None else raw.get("brand", ""))
            raw_code = str(res_row.get("original_code") if res_row.get("original_code") is not None else raw.get("code", ""))
            professional = str(res_row.get("professional") or raw.get("professional") or "暖通")
            parsed_spec = res_row.get("parsed_spec") if isinstance(res_row.get("parsed_spec"), dict) else {}
            parsed_spec_json = json.dumps(parsed_spec, ensure_ascii=False)

            cur.execute(
                """
                INSERT INTO flywheel_source_rows (
                    source_row_id, document_id, sheet_id, source_row_num, display_row_idx,
                    raw_name, raw_name_norm, raw_spec, raw_unit, raw_qty, raw_loss,
                    raw_category, raw_brand, raw_code, professional, initial_parsed_spec_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_row_id,
                    document_id,
                    sheet_id,
                    source_row_num,
                    display_row_idx,
                    raw_name,
                    precomputed_norms[idx],
                    raw_spec,
                    raw_unit,
                    raw_qty,
                    raw_loss,
                    raw_category,
                    raw_brand,
                    raw_code,
                    professional,
                    parsed_spec_json,
                ),
            )

            top_cands = res_row.get("top_candidates") or []
            top1 = top_cands[0] if top_cands else {}
            auto_top1_code = str(top1.get("code") or res_row.get("matched_code") or "")
            auto_top1_score = float(top1.get("score") or 0.0) if top1 else 0.0
            auto_top1_name = str(top1.get("name") or res_row.get("matched_name") or "")
            compact_cands = [
                {
                    "code": c.get("code", ""),
                    "name": c.get("name", ""),
                    "score": float(c.get("score") or 0.0),
                    "specification": c.get("specification", ""),
                    "unit": c.get("unit", ""),
                }
                for c in top_cands[:3]
            ]
            initial_bindings_json = json.dumps(res_row.get("bindings") or [], ensure_ascii=False)
            snapshot_id = f"snap_{uuid.uuid4().hex[:16]}"

            cur.execute(
                """
                INSERT INTO flywheel_recommendation_snapshots (
                    snapshot_id, document_id, sheet_id, source_row_id,
                    revision, is_initial, snapshot_kind, professional, parsed_spec_json,
                    auto_top1_code, auto_top1_score, auto_top1_name, auto_candidates_json,
                    initial_bindings_json, decision_status,
                    algo_version, model_version, config_version, config_digest_full
                ) VALUES (?, ?, ?, ?, 1, 1, 'initial_upload', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    document_id,
                    sheet_id,
                    source_row_id,
                    professional,
                    parsed_spec_json,
                    auto_top1_code,
                    auto_top1_score,
                    auto_top1_name,
                    json.dumps(compact_cands, ensure_ascii=False),
                    initial_bindings_json,
                    str(res_row.get("decision_status") or "recommended"),
                    versions["algo_version"],
                    versions["model_version"],
                    versions["config_version"],
                    versions["config_digest_full"],
                ),
            )

            res_row.update({
                "document_id": document_id,
                "upload_task_id": upload_task_id,
                "work_session_id": work_session_id,
                "sheet_id": sheet_id,
                "sheet_index": sheet_index,
                "sheet_name": sheet_name,
                "source_row_id": source_row_id,
                "source_row_num": source_row_num,
                "initial_snapshot_id": snapshot_id,
                "recommendation_snapshot_id": snapshot_id,
                "recommendation_revision": 1,
                "initial_auto_code": auto_top1_code,
                "algo_version": versions["algo_version"],
                "model_version": versions["model_version"],
                "config_version": versions["config_version"],
                "config_digest_full": versions["config_digest_full"],
                "trust_level": fw_mod.TRUST_LEVEL_TRUSTED,
                "identity_status": "verified",
                "feedback_eligible": True,
                "max_event_seq": 0,
                "max_feedback_seq": 0,
                "contract_version": CONTRACT_VERSION,
            })

        conn.execute("COMMIT")
        return {
            "document_id": document_id,
            "upload_task_id": upload_task_id,
            "work_session_id": work_session_id,
            "sheet_count": len(sheet_map),
            "row_count": len(parsed_rows),
            **versions,
        }
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _validate_non_empty_id(value: Optional[str], label: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned or cleaned.lower() in ("default_session", "null", "none", "undefined"):
        raise FeedbackContractError(422, "missing_or_invalid_identity", f"缺少或非法的 {label}，禁止退回 default_session。")
    return cleaned


def verify_document_ownership(
    cur,
    actor_user_id: int,
    actor_account_epoch: str,
    document_id: Optional[str],
) -> Dict[str, Any]:
    """Verify server-issued document exists and belongs to exact (user_id, account_epoch).

    Returns uniform 404 when unissued, cross-user, or stale-epoch to avoid existence leakage.
    """
    doc_id = _validate_non_empty_id(document_id, "document_id")
    doc = cur.execute(
        "SELECT * FROM flywheel_documents WHERE document_id = ?",
        (doc_id,),
    ).fetchone()
    if (
        not doc
        or int(doc["actor_user_id"]) != int(actor_user_id)
        or str(doc["actor_account_epoch"] or "") != str(actor_account_epoch or "")
    ):
        raise FeedbackContractError(404, "document_not_found_or_forbidden", "目标业务文档不存在或无权访问。")
    return dict(doc)


def verify_row_ownership(
    cur,
    actor_user_id: int,
    actor_account_epoch: str,
    document_id: Optional[str],
    sheet_id: Optional[str],
    source_row_id: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Verify full server hierarchy document -> sheet -> source_row and owner+epoch."""
    doc = verify_document_ownership(cur, actor_user_id, actor_account_epoch, document_id)
    sh_id = _validate_non_empty_id(sheet_id, "sheet_id")
    sr_id = _validate_non_empty_id(source_row_id, "source_row_id")

    sheet = cur.execute(
        "SELECT * FROM flywheel_sheets WHERE sheet_id = ? AND document_id = ?",
        (sh_id, doc["document_id"]),
    ).fetchone()
    if not sheet:
        raise FeedbackContractError(422, "sheet_mismatch", "工作表标识不存在或不属于当前业务文档。")

    source_row = cur.execute(
        "SELECT * FROM flywheel_source_rows WHERE source_row_id = ? AND document_id = ? AND sheet_id = ?",
        (sr_id, doc["document_id"], sh_id),
    ).fetchone()
    if not source_row:
        raise FeedbackContractError(422, "source_row_mismatch", "源行标识不存在或不属于当前工作表与业务文档。")

    return doc, dict(sheet), dict(source_row)


def _parse_strict_event_seq(raw_seq: Any) -> int:
    """Validate event_seq as a strict positive integer across both HTTP and contract boundaries."""
    if raw_seq is None:
        raise FeedbackContractError(422, "missing_event_seq", "缺少单调递增事件序号 event_seq。")
    if isinstance(raw_seq, bool) or not isinstance(raw_seq, int):
        raise FeedbackContractError(422, "invalid_event_seq", "event_seq 必须为严格正整数。")
    if raw_seq < 1:
        raise FeedbackContractError(422, "invalid_event_seq", "event_seq 必须大于等于 1。")
    return raw_seq


def _recompute_server_parsed_spec(source_row: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Recompute authoritative professional and parsed_spec from server source_row."""
    server_prof = str(source_row.get("professional") or "暖通").strip() or "暖通"
    qp_mod = importlib.import_module("app.query_preprocessor")
    qinfo = qp_mod.prepare_query(
        str(source_row.get("raw_name") or ""),
        str(source_row.get("raw_spec") or ""),
        server_prof,
        category=str(source_row.get("raw_category") or ""),
        brand=str(source_row.get("raw_brand") or ""),
        unit=str(source_row.get("raw_unit") or ""),
    )
    return server_prof, dict(qinfo.get("parsed_spec") or {})


def _validate_match_override_parsed_spec(
    override: Any,
    server_parsed: Dict[str, Any],
) -> Dict[str, Any]:
    """Ensure /api/match overrides only toggle subsets of server-extracted model/attrs."""
    if override is None:
        return dict(server_parsed)
    if not isinstance(override, dict):
        raise FeedbackContractError(422, "unverified_parsed_spec_override", "parsed_spec 必须为对象。")

    allowed_top_keys = {"model", "attrs", "clean_text", "name", "spec"}
    unknown_top = set(override.keys()) - allowed_top_keys
    if unknown_top:
        raise FeedbackContractError(
            422,
            "unverified_parsed_spec_override",
            f"单条重算禁止注入未核验顶层字段: {sorted(unknown_top)}",
        )
    for k in ("clean_text", "name", "spec"):
        if k in override and str(override[k] or "") != str(server_parsed.get(k) or ""):
            raise FeedbackContractError(
                422,
                "unverified_parsed_spec_override",
                f"单条重算禁止篡改服务端文本字段: {k}",
            )

    server_model = str(server_parsed.get("model") or "")
    if "model" in override:
        req_model = str(override.get("model") or "")
        if req_model not in ("", server_model):
            raise FeedbackContractError(
                422,
                "unverified_parsed_spec_override",
                "单条重算禁止注入服务端未解析出的型号。",
            )
    else:
        req_model = server_model

    server_attrs = dict(server_parsed.get("attrs") or {})
    if "attrs" in override:
        req_attrs_raw = override.get("attrs")
        if not isinstance(req_attrs_raw, dict):
            raise FeedbackContractError(422, "unverified_parsed_spec_override", "attrs 必须为对象。")
        req_attrs: Dict[str, Any] = {}
        for k, v in req_attrs_raw.items():
            if k not in server_attrs or server_attrs[k] != v:
                raise FeedbackContractError(
                    422,
                    "unverified_parsed_spec_override",
                    f"单条重算仅允许切换服务端已解析特征维度，禁止注入或修改属性: {k}",
                )
            req_attrs[k] = v
    else:
        req_attrs = server_attrs

    return {
        "clean_text": server_parsed.get("clean_text", ""),
        "name": server_parsed.get("name", ""),
        "spec": server_parsed.get("spec", ""),
        "model": req_model,
        "attrs": req_attrs,
    }


def load_verified_source_row(
    actor_user: Dict[str, Any],
    document_id: str,
    sheet_id: str,
    source_row_id: str,
    professional: Optional[str] = None,
    raw_text: Optional[str] = None,
    parsed_spec_override: Any = None,
    has_parsed_spec_override: bool = False,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify actor ownership and server source context before rematch/match_override execution."""
    actor = extract_verified_actor(actor_user)
    fw_mod = importlib.import_module("app.flywheel_db")
    fw_mod.init_flywheel_tables(db_path=db_path)
    conn = fw_mod.open_flywheel_tx_connection(db_path=db_path)
    try:
        cur = conn.cursor()
        _, _, source_row = verify_row_ownership(
            cur,
            actor["actor_user_id"],
            actor["actor_account_epoch"],
            document_id,
            sheet_id,
            source_row_id,
        )
    finally:
        conn.close()

    server_prof, server_parsed = _recompute_server_parsed_spec(source_row)
    if professional is not None and str(professional).strip() != "" and str(professional).strip() != server_prof:
        raise FeedbackContractError(
            422,
            "source_professional_mismatch",
            "重算请求的专业字段与服务端源行不一致。",
        )
    if raw_text is not None and str(raw_text).strip() != str(source_row.get("raw_spec") or "").strip():
        raise FeedbackContractError(
            422,
            "source_context_mismatch",
            "重算请求的规格文本与服务端源行不一致。",
        )
    validated_parsed = (
        _validate_match_override_parsed_spec(parsed_spec_override, server_parsed)
        if has_parsed_spec_override
        else server_parsed
    )
    return {
        "source_row": source_row,
        "server_professional": server_prof,
        "server_parsed_spec": server_parsed,
        "validated_parsed_spec": validated_parsed,
    }


def record_rematch_snapshot(
    actor_user: Dict[str, Any],
    document_id: str,
    sheet_id: str,
    source_row_id: str,
    professional: str,
    parsed_spec: Dict[str, Any],
    match_result: Dict[str, Any],
    snapshot_kind: str = "rematch",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a new recommendation revision (revision >= 2) without ever mutating revision=1."""
    actor = extract_verified_actor(actor_user)
    fw_mod = importlib.import_module("app.flywheel_db")
    fw_mod.init_flywheel_tables(db_path=db_path)
    versions = compute_runtime_versions()

    conn = fw_mod.open_flywheel_tx_connection(db_path=db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        _, _, source_row = verify_row_ownership(
            cur,
            actor["actor_user_id"],
            actor["actor_account_epoch"],
            document_id,
            sheet_id,
            source_row_id,
        )
        server_prof, server_parsed = _recompute_server_parsed_spec(source_row)
        if professional is not None and str(professional).strip() != "" and str(professional).strip() != server_prof:
            raise FeedbackContractError(
                422,
                "source_professional_mismatch",
                "重算请求的专业字段与服务端源行不一致。",
            )

        if snapshot_kind == "match_override":
            sanitized_parsed = _validate_match_override_parsed_spec(parsed_spec, server_parsed)
            stored_parsed = {**sanitized_parsed, "_provenance": "server_verified_subset"}
            effective_kind = "match_override"
        else:
            req_spec = parsed_spec if isinstance(parsed_spec, dict) else {}
            req_model = str(req_spec.get("model") if "model" in req_spec else (server_parsed.get("model") or ""))
            raw_attrs = req_spec.get("attrs") if isinstance(req_spec.get("attrs"), dict) else dict(server_parsed.get("attrs") or {})
            sanitized_attrs = {
                str(k): v
                for k, v in raw_attrs.items()
                if isinstance(v, (str, int, float, bool)) or v is None
            }
            sanitized_parsed = {
                "clean_text": server_parsed.get("clean_text", ""),
                "name": server_parsed.get("name", ""),
                "spec": server_parsed.get("spec", ""),
                "model": req_model,
                "attrs": sanitized_attrs,
            }
            is_client_param = (
                req_model != str(server_parsed.get("model") or "")
                or sanitized_attrs != (server_parsed.get("attrs") or {})
            )
            effective_kind = "rematch_client_param" if is_client_param else "rematch"
            stored_parsed = {
                **sanitized_parsed,
                "_provenance": "client_param_override" if is_client_param else "server_recomputed",
            }

        max_row = cur.execute(
            "SELECT COALESCE(MAX(revision), 0) AS max_rev FROM flywheel_recommendation_snapshots WHERE source_row_id = ?",
            (source_row_id,),
        ).fetchone()
        next_rev = max(2, int(max_row["max_rev"] or 0) + 1)

        top_cands = match_result.get("top_candidates") or []
        top1 = top_cands[0] if top_cands else {}
        auto_top1_code = str(top1.get("code") or match_result.get("matched_code") or "")
        auto_top1_score = float(top1.get("score") or 0.0) if top1 else 0.0
        auto_top1_name = str(top1.get("name") or "")
        compact_cands = [
            {
                "code": c.get("code", ""),
                "name": c.get("name", ""),
                "score": float(c.get("score") or 0.0),
                "specification": c.get("specification", ""),
                "unit": c.get("unit", ""),
            }
            for c in top_cands[:3]
        ]
        snapshot_id = f"snap_{uuid.uuid4().hex[:16]}"
        cur.execute(
            """
            INSERT INTO flywheel_recommendation_snapshots (
                snapshot_id, document_id, sheet_id, source_row_id,
                revision, is_initial, snapshot_kind, professional, parsed_spec_json,
                auto_top1_code, auto_top1_score, auto_top1_name, auto_candidates_json,
                initial_bindings_json, decision_status,
                algo_version, model_version, config_version, config_digest_full
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, '[]', 'recommended', ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                document_id,
                sheet_id,
                source_row_id,
                next_rev,
                effective_kind,
                server_prof,
                json.dumps(stored_parsed, ensure_ascii=False),
                auto_top1_code,
                auto_top1_score,
                auto_top1_name,
                json.dumps(compact_cands, ensure_ascii=False),
                versions["algo_version"],
                versions["model_version"],
                versions["config_version"],
                versions["config_digest_full"],
            ),
        )
        conn.execute("COMMIT")
        return {
            "recommendation_snapshot_id": snapshot_id,
            "recommendation_revision": next_rev,
            "snapshot_kind": effective_kind,
            "professional": server_prof,
            "parsed_spec": sanitized_parsed,
            **versions,
        }
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _normalize_targets_for_digest(final_targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    norm = []
    for t in final_targets or []:
        if not isinstance(t, dict):
            continue
        norm.append({
            "code": str(t.get("code") or "").strip(),
            "name": str(t.get("name") or "").strip(),
            "spec": str(t.get("spec") if t.get("spec") is not None else (t.get("specification") or "")).strip(),
            "unit": str(t.get("unit") or "").strip(),
            "qty_ratio": round(float(t.get("qty_ratio") if t.get("qty_ratio") is not None else 1.0), 6),
        })
    return norm


def _resolve_row_snapshots(
    cur,
    document_id: str,
    sheet_id: str,
    source_row_id: str,
    requested_snapshot_id: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    initial_snap = cur.execute(
        """
        SELECT * FROM flywheel_recommendation_snapshots
        WHERE document_id = ? AND sheet_id = ? AND source_row_id = ? AND is_initial = 1
        ORDER BY revision ASC LIMIT 1
        """,
        (document_id, sheet_id, source_row_id),
    ).fetchone()
    if not initial_snap:
        raise FeedbackContractError(422, "missing_initial_snapshot", "服务端缺少该源行的初始推荐快照。")

    req_snap_id = str(requested_snapshot_id or "").strip()
    if req_snap_id:
        target_snap = cur.execute(
            """
            SELECT * FROM flywheel_recommendation_snapshots
            WHERE snapshot_id = ? AND document_id = ? AND sheet_id = ? AND source_row_id = ?
            """,
            (req_snap_id, document_id, sheet_id, source_row_id),
        ).fetchone()
        if not target_snap:
            raise FeedbackContractError(422, "snapshot_mismatch", "推荐快照标识与当前文档/工作表/源行不匹配。")
    else:
        target_snap = cur.execute(
            """
            SELECT * FROM flywheel_recommendation_snapshots
            WHERE document_id = ? AND sheet_id = ? AND source_row_id = ?
            ORDER BY revision DESC LIMIT 1
            """,
            (document_id, sheet_id, source_row_id),
        ).fetchone() or initial_snap

    return dict(initial_snap), dict(target_snap)


def _check_spoofed_actor_fields(payload: Dict[str, Any], actor_user_id: int) -> None:
    """Reject explicit client attempts to specify another user_id/actor_user_id."""
    for key in ("user_id", "actor_user_id"):
        if key in payload and payload[key] is not None and str(payload[key]).strip() != "":
            try:
                if int(str(payload[key]).strip()) != int(actor_user_id):
                    raise FeedbackContractError(422, "forged_actor_id", "请求体不得指定或伪造他人用户身份。")
            except ValueError:
                raise FeedbackContractError(422, "forged_actor_id", "请求体不得指定非法的用户身份。")


def record_flywheel_event(
    actor_user: Dict[str, Any],
    payload: Dict[str, Any],
    db_path: Optional[str] = None,
    _tx_hook: Optional[Callable[[str, Any], None]] = None,
) -> Dict[str, Any]:
    """Persist a verified, idempotent, revision-ordered flywheel feedback event."""
    actor = extract_verified_actor(actor_user)
    _check_spoofed_actor_fields(payload, actor["actor_user_id"])

    client_event_id = _validate_non_empty_id(payload.get("client_event_id"), "client_event_id")
    action_type = _validate_non_empty_id(payload.get("action_type"), "action_type")
    event_seq = _parse_strict_event_seq(payload.get("event_seq"))

    final_targets = payload.get("final_targets")
    if not isinstance(final_targets, list):
        raise FeedbackContractError(422, "invalid_final_targets", "final_targets 必须为数组。")

    document_id = payload.get("document_id")
    sheet_id = payload.get("sheet_id")
    source_row_id = payload.get("source_row_id")

    fw_mod = importlib.import_module("app.flywheel_db")
    fw_mod.init_flywheel_tables(db_path=db_path)
    conn = fw_mod.open_flywheel_tx_connection(db_path=db_path)

    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()

        doc, sheet, source_row = verify_row_ownership(
            cur,
            actor["actor_user_id"],
            actor["actor_account_epoch"],
            document_id,
            sheet_id,
            source_row_id,
        )
        initial_snap, target_snap = _resolve_row_snapshots(
            cur,
            doc["document_id"],
            sheet["sheet_id"],
            source_row["source_row_id"],
            payload.get("recommendation_snapshot_id"),
        )

        # Authoritative content digest for idempotency conflict detection
        norm_targets = _normalize_targets_for_digest(final_targets)
        digest_input = {
            "endpoint": "flywheel_record",
            "actor_user_id": actor["actor_user_id"],
            "actor_account_epoch": actor["actor_account_epoch"],
            "document_id": doc["document_id"],
            "sheet_id": sheet["sheet_id"],
            "source_row_id": source_row["source_row_id"],
            "recommendation_snapshot_id": target_snap["snapshot_id"],
            "event_seq": event_seq,
            "action_type": action_type,
            "final_targets": norm_targets,
        }
        payload_digest = _sha256_text(canonical_json(digest_input))

        existing_evt = cur.execute(
            "SELECT * FROM flywheel_records WHERE client_event_id = ?",
            (client_event_id,),
        ).fetchone()
        if existing_evt:
            same_actor = (
                existing_evt["actor_user_id"] is not None
                and int(existing_evt["actor_user_id"]) == actor["actor_user_id"]
                and str(existing_evt["actor_account_epoch"] or "") == actor["actor_account_epoch"]
            )
            same_object = (
                str(existing_evt["document_id"] or "") == doc["document_id"]
                and str(existing_evt["sheet_id"] or "") == sheet["sheet_id"]
                and str(existing_evt["source_row_id"] or "") == source_row["source_row_id"]
            )
            same_digest = str(existing_evt["payload_digest"] or "") == payload_digest
            if not (same_actor and same_object and same_digest):
                raise FeedbackContractError(
                    409,
                    "idempotency_key_conflict",
                    "事件幂等键 client_event_id 冲突：同键不允许用于不同内容、不同对象或不同用户。",
                )
            eff = cur.execute(
                """
                SELECT id, event_seq FROM flywheel_records
                WHERE actor_user_id = ? AND actor_account_epoch = ?
                  AND document_id = ? AND sheet_id = ? AND source_row_id = ?
                  AND trust_level = 'trusted' AND superseded = 0
                ORDER BY event_seq DESC, id DESC LIMIT 1
                """,
                (
                    actor["actor_user_id"],
                    actor["actor_account_epoch"],
                    doc["document_id"],
                    sheet["sheet_id"],
                    source_row["source_row_id"],
                ),
            ).fetchone()
            conn.execute("COMMIT")
            return {
                "code": 0,
                "msg": "操作流水幂等重放（未重复写入）",
                "idempotent_replay": True,
                "record_id": int(existing_evt["id"]),
                "document_id": doc["document_id"],
                "sheet_id": sheet["sheet_id"],
                "source_row_id": source_row["source_row_id"],
                "event_seq": int(existing_evt["event_seq"] or 0),
                "superseded": int(existing_evt["superseded"] or 0),
                "is_stale_arrival": int(existing_evt["is_stale_arrival"] or 0),
                "effective_record_id": int(eff["id"]) if eff else int(existing_evt["id"]),
                "effective_event_seq": int(eff["event_seq"]) if eff else int(existing_evt["event_seq"] or 0),
                "initial_snapshot_id": initial_snap["snapshot_id"],
                "recommendation_snapshot_id": target_snap["snapshot_id"],
                "recommendation_revision": int(target_snap["revision"]),
                "algo_version": target_snap["algo_version"],
                "model_version": target_snap["model_version"],
                "config_version": target_snap["config_version"],
                "config_digest_full": target_snap["config_digest_full"],
                "contract_version": CONTRACT_VERSION,
            }

        # Check revision / concurrency monotonicity on this exact object
        seq_conflict = cur.execute(
            """
            SELECT id FROM flywheel_records
            WHERE actor_user_id = ? AND actor_account_epoch = ?
              AND document_id = ? AND sheet_id = ? AND source_row_id = ?
              AND trust_level = 'trusted' AND event_seq = ?
            LIMIT 1
            """,
            (
                actor["actor_user_id"],
                actor["actor_account_epoch"],
                doc["document_id"],
                sheet["sheet_id"],
                source_row["source_row_id"],
                event_seq,
            ),
        ).fetchone()
        if seq_conflict:
            raise FeedbackContractError(
                409,
                "event_seq_conflict",
                f"同一源行存在相同序号 event_seq={event_seq} 的并发冲突事件，请使用新事件序号重试。",
            )

        max_row = cur.execute(
            """
            SELECT COALESCE(MAX(event_seq), 0) AS max_seq
            FROM flywheel_records
            WHERE actor_user_id = ? AND actor_account_epoch = ?
              AND document_id = ? AND sheet_id = ? AND source_row_id = ?
              AND trust_level = 'trusted'
            """,
            (
                actor["actor_user_id"],
                actor["actor_account_epoch"],
                doc["document_id"],
                sheet["sheet_id"],
                source_row["source_row_id"],
            ),
        ).fetchone()
        max_existing_seq = int(max_row["max_seq"] or 0)
        is_stale_arrival = 1 if event_seq < max_existing_seq else 0
        initial_superseded = 1 if is_stale_arrival else 0

        # Server-authoritative context (never trust client-supplied raw_*, auto_*, or versions)
        saved_session_id = str(payload.get("saved_session_id") or "").strip()
        client_extra = payload.get("extracted_params") if isinstance(payload.get("extracted_params"), dict) else {}
        server_parsed_spec = json.loads(target_snap["parsed_spec_json"] or "{}")
        extracted_payload = {
            "parsed_spec": server_parsed_spec,
            "client_adjusted_params": client_extra if action_type == "PARAM_ADJUST" else {},
        }

        cur.execute(
            """
            INSERT INTO flywheel_records (
                client_event_id, session_id, row_idx, discipline,
                raw_name, raw_name_norm, raw_spec, raw_unit, raw_category, extracted_params,
                auto_top1_code, auto_top1_score, auto_top1_name, auto_candidates_json,
                algo_version, config_version, action_type, final_targets_json,
                user_id, user_name, superseded, is_processed,
                actor_user_id, actor_account_epoch, document_id, upload_task_id,
                work_session_id, saved_session_id, sheet_id, sheet_name, sheet_index,
                source_row_id, source_row_num, event_seq, payload_digest,
                initial_snapshot_id, recommendation_snapshot_id, recommendation_revision,
                initial_auto_top1_code, initial_auto_top1_score, initial_auto_top1_name,
                initial_auto_candidates_json, model_version, config_digest_full,
                parsed_spec_json, trust_level, legacy_reason, collection_boundary,
                is_stale_arrival, contract_version
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, 0,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, 'trusted', '', ?,
                ?, ?
            )
            """,
            (
                client_event_id,
                doc["work_session_id"],
                int(source_row["display_row_idx"]),
                str(target_snap["professional"] or source_row["professional"] or "暖通"),
                source_row["raw_name"],
                source_row["raw_name_norm"],
                source_row["raw_spec"],
                source_row["raw_unit"],
                source_row["raw_category"],
                json.dumps(extracted_payload, ensure_ascii=False),
                target_snap["auto_top1_code"],
                float(target_snap["auto_top1_score"] or 0.0),
                target_snap["auto_top1_name"],
                target_snap["auto_candidates_json"],
                target_snap["algo_version"],
                target_snap["config_version"],
                action_type,
                json.dumps(norm_targets, ensure_ascii=False),
                str(actor["actor_user_id"]),
                actor["actor_display_name"],
                initial_superseded,
                actor["actor_user_id"],
                actor["actor_account_epoch"],
                doc["document_id"],
                doc["upload_task_id"],
                doc["work_session_id"],
                saved_session_id,
                sheet["sheet_id"],
                sheet["sheet_name"],
                int(sheet["sheet_index"]),
                source_row["source_row_id"],
                int(source_row["source_row_num"]),
                event_seq,
                payload_digest,
                initial_snap["snapshot_id"],
                target_snap["snapshot_id"],
                int(target_snap["revision"]),
                initial_snap["auto_top1_code"],
                float(initial_snap["auto_top1_score"] or 0.0),
                initial_snap["auto_top1_name"],
                initial_snap["auto_candidates_json"],
                target_snap["model_version"],
                target_snap["config_digest_full"],
                target_snap["parsed_spec_json"],
                doc["collection_boundary"],
                is_stale_arrival,
                CONTRACT_VERSION,
            ),
        )
        new_id = int(cur.lastrowid)
        if _tx_hook:
            _tx_hook("after_insert", cur)

        if not is_stale_arrival:
            cur.execute(
                """
                UPDATE flywheel_records
                SET superseded = 1
                WHERE actor_user_id = ? AND actor_account_epoch = ?
                  AND document_id = ? AND sheet_id = ? AND source_row_id = ?
                  AND trust_level = 'trusted' AND id != ? AND superseded = 0
                """,
                (
                    actor["actor_user_id"],
                    actor["actor_account_epoch"],
                    doc["document_id"],
                    sheet["sheet_id"],
                    source_row["source_row_id"],
                    new_id,
                ),
            )
            if _tx_hook:
                _tx_hook("after_supersede", cur)

        eff = cur.execute(
            """
            SELECT id, event_seq FROM flywheel_records
            WHERE actor_user_id = ? AND actor_account_epoch = ?
              AND document_id = ? AND sheet_id = ? AND source_row_id = ?
              AND trust_level = 'trusted' AND superseded = 0
            ORDER BY event_seq DESC, id DESC LIMIT 1
            """,
            (
                actor["actor_user_id"],
                actor["actor_account_epoch"],
                doc["document_id"],
                sheet["sheet_id"],
                source_row["source_row_id"],
            ),
        ).fetchone()
        conn.execute("COMMIT")

        return {
            "code": 0,
            "msg": "操作流水已成功记录" if not is_stale_arrival else "迟到旧序号事件已归档至历史（未覆盖最新最终状态）",
            "idempotent_replay": False,
            "record_id": new_id,
            "document_id": doc["document_id"],
            "sheet_id": sheet["sheet_id"],
            "source_row_id": source_row["source_row_id"],
            "event_seq": event_seq,
            "superseded": initial_superseded,
            "is_stale_arrival": is_stale_arrival,
            "effective_record_id": int(eff["id"]) if eff else new_id,
            "effective_event_seq": int(eff["event_seq"]) if eff else event_seq,
            "initial_snapshot_id": initial_snap["snapshot_id"],
            "recommendation_snapshot_id": target_snap["snapshot_id"],
            "recommendation_revision": int(target_snap["revision"]),
            "algo_version": target_snap["algo_version"],
            "model_version": target_snap["model_version"],
            "config_version": target_snap["config_version"],
            "config_digest_full": target_snap["config_digest_full"],
            "contract_version": CONTRACT_VERSION,
        }
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def record_feedback_sample_event(
    actor_user: Dict[str, Any],
    payload: Dict[str, Any],
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist a verified, idempotent, revision-ordered sample from /api/feedback/submit."""
    actor = extract_verified_actor(actor_user)
    _check_spoofed_actor_fields(payload, actor["actor_user_id"])

    client_event_id = _validate_non_empty_id(payload.get("client_event_id"), "client_event_id")
    event_seq = _parse_strict_event_seq(payload.get("event_seq"))

    manual_code = str(payload.get("manual_code") or "").strip()
    if not manual_code:
        raise FeedbackContractError(422, "missing_manual_code", "manual_code 不能为空。")
    is_correct = bool(payload.get("is_correct"))

    fw_mod = importlib.import_module("app.flywheel_db")
    fw_mod.init_flywheel_tables(db_path=db_path)
    conn = fw_mod.open_flywheel_tx_connection(db_path=db_path)

    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()

        doc, sheet, source_row = verify_row_ownership(
            cur,
            actor["actor_user_id"],
            actor["actor_account_epoch"],
            payload.get("document_id"),
            payload.get("sheet_id"),
            payload.get("source_row_id"),
        )
        initial_snap, target_snap = _resolve_row_snapshots(
            cur,
            doc["document_id"],
            sheet["sheet_id"],
            source_row["source_row_id"],
            payload.get("recommendation_snapshot_id"),
        )

        # Authoritative server auto_code & raw_text (never trust client spoofing)
        server_auto_code = str(target_snap["auto_top1_code"] or initial_snap["auto_top1_code"] or "")
        server_raw_text = f"{source_row['raw_name']} {source_row['raw_spec']}".strip()

        digest_input = {
            "endpoint": "feedback_submit",
            "actor_user_id": actor["actor_user_id"],
            "actor_account_epoch": actor["actor_account_epoch"],
            "document_id": doc["document_id"],
            "sheet_id": sheet["sheet_id"],
            "source_row_id": source_row["source_row_id"],
            "recommendation_snapshot_id": target_snap["snapshot_id"],
            "event_seq": event_seq,
            "manual_code": manual_code,
            "is_correct": is_correct,
        }
        payload_digest = _sha256_text(canonical_json(digest_input))

        existing_evt = cur.execute(
            "SELECT * FROM flywheel_feedback_samples WHERE client_event_id = ?",
            (client_event_id,),
        ).fetchone()
        if existing_evt:
            same_actor = (
                existing_evt["actor_user_id"] is not None
                and int(existing_evt["actor_user_id"]) == actor["actor_user_id"]
                and str(existing_evt["actor_account_epoch"] or "") == actor["actor_account_epoch"]
            )
            same_object = (
                str(existing_evt["document_id"] or "") == doc["document_id"]
                and str(existing_evt["sheet_id"] or "") == sheet["sheet_id"]
                and str(existing_evt["source_row_id"] or "") == source_row["source_row_id"]
            )
            same_digest = str(existing_evt["payload_digest"] or "") == payload_digest
            if not (same_actor and same_object and same_digest):
                raise FeedbackContractError(
                    409,
                    "idempotency_key_conflict",
                    "反馈幂等键 client_event_id 冲突：同键不允许用于不同内容、不同对象或不同用户。",
                )
            conn.execute("COMMIT")
            return {
                "code": 0,
                "msg": "反馈提交幂等重放",
                "idempotent_replay": True,
                "sample_id": int(existing_evt["id"]),
                "document_id": doc["document_id"],
                "sheet_id": sheet["sheet_id"],
                "source_row_id": source_row["source_row_id"],
                "event_seq": int(existing_evt["event_seq"] or 0),
                "superseded": int(existing_evt["superseded"] or 0),
                "algo_version": existing_evt["algo_version"],
                "model_version": existing_evt["model_version"],
                "config_version": existing_evt["config_version"],
                "sample_record": {
                    "client_event_id": str(existing_evt["client_event_id"]),
                    "actor_user_id": int(existing_evt["actor_user_id"]),
                    "actor_account_epoch": str(existing_evt["actor_account_epoch"] or ""),
                    "document_id": doc["document_id"],
                    "sheet_id": sheet["sheet_id"],
                    "source_row_id": source_row["source_row_id"],
                    "event_seq": int(existing_evt["event_seq"] or 0),
                    "row_id": int(source_row["display_row_idx"]),
                    "raw_text": str(existing_evt["raw_text"] or server_raw_text),
                    "auto_code": str(existing_evt["auto_code"] or server_auto_code),
                    "manual_code": str(existing_evt["manual_code"] or manual_code),
                    "is_correct": bool(existing_evt["is_correct"]),
                    "superseded": int(existing_evt["superseded"] or 0),
                    "trust_level": str(existing_evt["trust_level"] or "trusted"),
                    "algo_version": existing_evt["algo_version"],
                    "model_version": existing_evt["model_version"],
                    "config_version": existing_evt["config_version"],
                    "contract_version": str(existing_evt["contract_version"] or CONTRACT_VERSION),
                },
            }

        seq_conflict = cur.execute(
            """
            SELECT id FROM flywheel_feedback_samples
            WHERE actor_user_id = ? AND actor_account_epoch = ?
              AND document_id = ? AND sheet_id = ? AND source_row_id = ?
              AND trust_level = 'trusted' AND event_seq = ?
            LIMIT 1
            """,
            (
                actor["actor_user_id"],
                actor["actor_account_epoch"],
                doc["document_id"],
                sheet["sheet_id"],
                source_row["source_row_id"],
                event_seq,
            ),
        ).fetchone()
        if seq_conflict:
            raise FeedbackContractError(
                409,
                "event_seq_conflict",
                f"同一源行存在相同反馈序号 event_seq={event_seq} 的并发冲突。",
            )

        max_row = cur.execute(
            """
            SELECT COALESCE(MAX(event_seq), 0) AS max_seq
            FROM flywheel_feedback_samples
            WHERE actor_user_id = ? AND actor_account_epoch = ?
              AND document_id = ? AND sheet_id = ? AND source_row_id = ?
              AND trust_level = 'trusted'
            """,
            (
                actor["actor_user_id"],
                actor["actor_account_epoch"],
                doc["document_id"],
                sheet["sheet_id"],
                source_row["source_row_id"],
            ),
        ).fetchone()
        max_existing_seq = int(max_row["max_seq"] or 0)
        is_stale = 1 if event_seq < max_existing_seq else 0

        cur.execute(
            """
            INSERT INTO flywheel_feedback_samples (
                client_event_id, payload_digest,
                actor_user_id, actor_account_epoch,
                document_id, sheet_id, source_row_id, event_seq,
                initial_snapshot_id, recommendation_snapshot_id,
                raw_name, raw_spec, raw_text,
                auto_code, manual_code, is_correct,
                algo_version, model_version, config_version,
                trust_level, legacy_reason, superseded, is_stale_arrival, contract_version
            ) VALUES (
                ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                'trusted', '', ?, ?, ?
            )
            """,
            (
                client_event_id,
                payload_digest,
                actor["actor_user_id"],
                actor["actor_account_epoch"],
                doc["document_id"],
                sheet["sheet_id"],
                source_row["source_row_id"],
                event_seq,
                initial_snap["snapshot_id"],
                target_snap["snapshot_id"],
                source_row["raw_name"],
                source_row["raw_spec"],
                server_raw_text,
                server_auto_code,
                manual_code,
                1 if is_correct else 0,
                target_snap["algo_version"],
                target_snap["model_version"],
                target_snap["config_version"],
                1 if is_stale else 0,
                is_stale,
                CONTRACT_VERSION,
            ),
        )
        new_id = int(cur.lastrowid)

        if not is_stale:
            cur.execute(
                """
                UPDATE flywheel_feedback_samples
                SET superseded = 1
                WHERE actor_user_id = ? AND actor_account_epoch = ?
                  AND document_id = ? AND sheet_id = ? AND source_row_id = ?
                  AND trust_level = 'trusted' AND id != ? AND superseded = 0
                """,
                (
                    actor["actor_user_id"],
                    actor["actor_account_epoch"],
                    doc["document_id"],
                    sheet["sheet_id"],
                    source_row["source_row_id"],
                    new_id,
                ),
            )
        conn.execute("COMMIT")
        return {
            "code": 0,
            "msg": "反馈提交成功",
            "idempotent_replay": False,
            "sample_id": new_id,
            "document_id": doc["document_id"],
            "sheet_id": sheet["sheet_id"],
            "source_row_id": source_row["source_row_id"],
            "event_seq": event_seq,
            "superseded": 1 if is_stale else 0,
            "is_stale_arrival": is_stale,
            "algo_version": target_snap["algo_version"],
            "model_version": target_snap["model_version"],
            "config_version": target_snap["config_version"],
            "sample_record": {
                "client_event_id": client_event_id,
                "actor_user_id": actor["actor_user_id"],
                "actor_account_epoch": actor["actor_account_epoch"],
                "document_id": doc["document_id"],
                "sheet_id": sheet["sheet_id"],
                "source_row_id": source_row["source_row_id"],
                "event_seq": event_seq,
                "row_id": int(source_row["display_row_idx"]),
                "raw_text": server_raw_text,
                "auto_code": server_auto_code,
                "manual_code": manual_code,
                "is_correct": is_correct,
                "superseded": 1 if is_stale else 0,
                "trust_level": "trusted",
                "algo_version": target_snap["algo_version"],
                "model_version": target_snap["model_version"],
                "config_version": target_snap["config_version"],
                "contract_version": CONTRACT_VERSION,
            },
        }
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def annotate_items_identity(
    actor_user: Dict[str, Any],
    items: List[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Verify restored session items against server document/sheet/source_row ownership.

    Never promotes unverified/legacy/foreign items into server snapshots.
    Returns (items, legacy_untrusted_count).
    """
    actor = extract_verified_actor(actor_user)
    fw_mod = importlib.import_module("app.flywheel_db")
    fw_mod.init_flywheel_tables(db_path=db_path)

    conn = fw_mod.open_flywheel_tx_connection(db_path=db_path)
    legacy_count = 0
    try:
        cur = conn.cursor()
        for item in items:
            doc_id = str(item.get("document_id") or "").strip()
            sh_id = str(item.get("sheet_id") or "").strip()
            sr_id = str(item.get("source_row_id") or "").strip()

            verified_row = None
            if (
                doc_id and sh_id and sr_id
                and doc_id.lower() != "default_session"
            ):
                verified_row = cur.execute(
                    """
                    SELECT
                        d.document_id, d.upload_task_id, d.work_session_id,
                        s.sheet_id, s.sheet_index, s.sheet_name,
                        r.source_row_id, r.source_row_num, r.display_row_idx,
                        r.professional AS server_professional
                    FROM flywheel_documents d
                    JOIN flywheel_sheets s ON s.document_id = d.document_id
                    JOIN flywheel_source_rows r ON r.sheet_id = s.sheet_id AND r.document_id = d.document_id
                    WHERE d.document_id = ? AND s.sheet_id = ? AND r.source_row_id = ?
                      AND d.actor_user_id = ? AND d.actor_account_epoch = ?
                    """,
                    (doc_id, sh_id, sr_id, actor["actor_user_id"], actor["actor_account_epoch"]),
                ).fetchone()

            if verified_row:
                init_snap = cur.execute(
                    """
                    SELECT snapshot_id, auto_top1_code, algo_version, model_version, config_version, config_digest_full
                    FROM flywheel_recommendation_snapshots
                    WHERE source_row_id = ? AND is_initial = 1
                    ORDER BY revision ASC LIMIT 1
                    """,
                    (sr_id,),
                ).fetchone()
                latest_snap = cur.execute(
                    """
                    SELECT snapshot_id, revision, algo_version, model_version, config_version, config_digest_full
                    FROM flywheel_recommendation_snapshots
                    WHERE source_row_id = ?
                    ORDER BY revision DESC LIMIT 1
                    """,
                    (sr_id,),
                ).fetchone()
                max_fw = cur.execute(
                    """
                    SELECT COALESCE(MAX(event_seq), 0) AS mx
                    FROM flywheel_records
                    WHERE actor_user_id = ? AND actor_account_epoch = ?
                      AND document_id = ? AND sheet_id = ? AND source_row_id = ?
                      AND trust_level = 'trusted'
                    """,
                    (actor["actor_user_id"], actor["actor_account_epoch"], doc_id, sh_id, sr_id),
                ).fetchone()
                max_fb = cur.execute(
                    """
                    SELECT COALESCE(MAX(event_seq), 0) AS mx
                    FROM flywheel_feedback_samples
                    WHERE actor_user_id = ? AND actor_account_epoch = ?
                      AND document_id = ? AND sheet_id = ? AND source_row_id = ?
                      AND trust_level = 'trusted'
                    """,
                    (actor["actor_user_id"], actor["actor_account_epoch"], doc_id, sh_id, sr_id),
                ).fetchone()

                item["document_id"] = verified_row["document_id"]
                item["upload_task_id"] = verified_row["upload_task_id"]
                item["work_session_id"] = verified_row["work_session_id"]
                item["sheet_id"] = verified_row["sheet_id"]
                item["sheet_index"] = int(verified_row["sheet_index"])
                item["sheet_name"] = verified_row["sheet_name"]
                item["source_row_id"] = verified_row["source_row_id"]
                item["source_row_num"] = int(verified_row["source_row_num"])
                if init_snap:
                    item["initial_snapshot_id"] = init_snap["snapshot_id"]
                    item["initial_auto_code"] = init_snap["auto_top1_code"]
                if latest_snap:
                    item["recommendation_snapshot_id"] = latest_snap["snapshot_id"]
                    item["recommendation_revision"] = int(latest_snap["revision"])
                    item["algo_version"] = latest_snap["algo_version"]
                    item["model_version"] = latest_snap["model_version"]
                    item["config_version"] = latest_snap["config_version"]
                    item["config_digest_full"] = latest_snap["config_digest_full"]
                item["max_event_seq"] = int(max_fw["mx"] or 0)
                item["max_feedback_seq"] = int(max_fb["mx"] or 0)
                item["trust_level"] = "trusted"
                item["identity_status"] = "verified"
                item["feedback_eligible"] = True
                item.pop("legacy_isolation_notice", None)
            else:
                legacy_count += 1
                item["document_id"] = ""
                item["sheet_id"] = ""
                item["source_row_id"] = ""
                item["work_session_id"] = ""
                item["initial_snapshot_id"] = ""
                item["recommendation_snapshot_id"] = ""
                item["trust_level"] = "legacy_untrusted"
                item["identity_status"] = "legacy_unverified"
                item["feedback_eligible"] = False
                item["legacy_isolation_notice"] = (
                    "该草稿/会话行缺少可核验的服务端文档/工作表/源行身份，已隔离为只读复核；"
                    "不会提交可信反馈，请重新上传清单以开启新的采集边界。"
                )
        return items, legacy_count
    finally:
        conn.close()


def get_row_trace(
    actor_user: Dict[str, Any],
    document_id: str,
    source_row_id: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Read authoritative source row, initial snapshot, all revisions, events, and effective final state."""
    actor = extract_verified_actor(actor_user)
    fw_mod = importlib.import_module("app.flywheel_db")
    fw_mod.init_flywheel_tables(db_path=db_path)

    conn = fw_mod.open_flywheel_tx_connection(db_path=db_path)
    try:
        cur = conn.cursor()
        doc = verify_document_ownership(
            cur, actor["actor_user_id"], actor["actor_account_epoch"], document_id
        )
        sr_id = _validate_non_empty_id(source_row_id, "source_row_id")
        source_row = cur.execute(
            "SELECT * FROM flywheel_source_rows WHERE source_row_id = ? AND document_id = ?",
            (sr_id, doc["document_id"]),
        ).fetchone()
        if not source_row:
            raise FeedbackContractError(404, "source_row_not_found", "目标源行不存在或无权访问。")

        sheet = cur.execute(
            "SELECT * FROM flywheel_sheets WHERE sheet_id = ?",
            (source_row["sheet_id"],),
        ).fetchone()
        snapshots = [
            dict(r)
            for r in cur.execute(
                """
                SELECT * FROM flywheel_recommendation_snapshots
                WHERE document_id = ? AND source_row_id = ?
                ORDER BY revision ASC
                """,
                (doc["document_id"], sr_id),
            ).fetchall()
        ]
        events = [
            dict(r)
            for r in cur.execute(
                """
                SELECT * FROM flywheel_records
                WHERE actor_user_id = ? AND actor_account_epoch = ?
                  AND document_id = ? AND source_row_id = ?
                  AND trust_level = 'trusted'
                ORDER BY event_seq ASC, id ASC
                """,
                (actor["actor_user_id"], actor["actor_account_epoch"], doc["document_id"], sr_id),
            ).fetchall()
        ]
        effective_record = cur.execute(
            """
            SELECT * FROM flywheel_effective_records
            WHERE actor_user_id = ? AND actor_account_epoch = ?
              AND document_id = ? AND source_row_id = ?
            ORDER BY event_seq DESC, id DESC LIMIT 1
            """,
            (actor["actor_user_id"], actor["actor_account_epoch"], doc["document_id"], sr_id),
        ).fetchone()
        feedback_samples = [
            dict(r)
            for r in cur.execute(
                """
                SELECT * FROM flywheel_feedback_samples
                WHERE actor_user_id = ? AND actor_account_epoch = ?
                  AND document_id = ? AND source_row_id = ?
                  AND trust_level = 'trusted'
                ORDER BY event_seq ASC, id ASC
                """,
                (actor["actor_user_id"], actor["actor_account_epoch"], doc["document_id"], sr_id),
            ).fetchall()
        ]
        initial_snapshot = next((s for s in snapshots if int(s.get("is_initial") or 0) == 1), None)
        return {
            "document": doc,
            "sheet": dict(sheet) if sheet else {},
            "source_row": dict(source_row),
            "initial_snapshot": initial_snapshot,
            "snapshots": snapshots,
            "events": events,
            "effective_record": dict(effective_record) if effective_record else None,
            "feedback_samples": feedback_samples,
        }
    finally:
        conn.close()
