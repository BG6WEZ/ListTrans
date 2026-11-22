"""Read-only, versioned inputs for rule evaluation and standard report export."""
import hashlib
import json
import os
from pathlib import Path


ASSET_DIR = Path(__file__).resolve().with_name("eval_assets")
EVALUATOR_PATH = Path(__file__).resolve().with_name("sandbox_evaluator.py")
GOLDEN_SHA256 = "10e44b73d2b945e27146ad83eac018c1a736df945ed95fbdc710190dc7aa2057"
TEMPLATE_SHA256 = "d1cfc598623f7aee6d24ad3561ac5bb87410c976953569c939337519e0ae4767"


class RuntimeAssetError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_checked_json(path: Path, expected: str, kind: str):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeAssetError(f"{kind}_missing", f"无法读取运行资产 {kind}。") from exc
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeAssetError(f"{kind}_invalid", f"运行资产 {kind} 为空或不是有效 JSON。") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected.lower().strip():
        raise RuntimeAssetError(f"{kind}_digest_mismatch", f"运行资产 {kind} 的 SHA256 与配置不符。")
    return raw, data, digest


def load_runtime_golden():
    override = os.getenv("GOLDEN_SET_FILE")
    path = Path(override) if override else ASSET_DIR / "golden_runtime_v1.json"
    expected = os.getenv("GOLDEN_SET_SHA256", "") if override else GOLDEN_SHA256
    raw, data, digest = load_checked_json(path, expected, "golden")
    items = data.get("items") if isinstance(data, dict) else None
    meta = data.get("meta") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items or not isinstance(meta, dict) or not meta.get("version"):
        raise RuntimeAssetError("golden_invalid", "运行评测样本为空或缺少版本。")
    ids = set()
    for item in items:
        if (not isinstance(item, dict)
                or not all(isinstance(item.get(key), str) for key in ("id", "name", "professional", "specification"))
                or not item["id"] or not item["name"] or item["id"] in ids
                or item.get("label_type") not in ("1to1", "1toN")
                or not isinstance(item.get("target_codes"), list) or not item["target_codes"]
                or not all(isinstance(code, str) and code.strip() for code in item["target_codes"])):
            raise RuntimeAssetError("golden_invalid", "运行评测样本缺少有效字段、目标编码或唯一 ID。")
        ids.add(item["id"])
    return raw, data, digest


def load_report_template():
    _, template, digest = load_checked_json(ASSET_DIR / "report_template_v1.json", TEMPLATE_SHA256, "template")
    if (not isinstance(template, dict) or template.get("sheet") != "模板"
            or not isinstance(template.get("headers"), list) or len(template["headers"]) != 15
            or not all(isinstance(header, str) and header for header in template["headers"])):
        raise RuntimeAssetError("template_invalid", "运行报表模板缺少有效工作表或表头。")
    return template, digest


def evaluator_digest() -> str:
    try:
        data = EVALUATOR_PATH.read_bytes()
        compile(data, str(EVALUATOR_PATH), "exec")
    except (OSError, SyntaxError, ValueError) as exc:
        raise RuntimeAssetError("evaluator_unavailable", "正式沙箱评测入口缺失或无法编译。") from exc
    if not data.strip():
        raise RuntimeAssetError("evaluator_unavailable", "正式沙箱评测入口为空。")
    return hashlib.sha256(data).hexdigest()


def runtime_model_state():
    from app.vector_index import index_manager

    model = index_manager.model_name_or_path
    enabled = index_manager.is_enabled
    return {
        "model": model,
        "enabled": enabled,
        "dim": index_manager.embedding_dim if enabled else None,
        "model_version": f"{model}|loaded" if enabled else f"{model}|unavailable:keyword_only",
    }
