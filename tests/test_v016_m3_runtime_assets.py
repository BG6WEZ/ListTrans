import re
import shutil
import sqlite3

import pytest

from app import runtime_eval_assets as assets
from app.flywheel_sandbox import get_eval_task_status, start_governed_evaluation
from app.rule_governance import EvalGateError, require_materials_catalog


@pytest.mark.parametrize(("filename", "payload", "error"), [
    ("golden_runtime_v1.json", None, "golden_missing"),
    ("golden_runtime_v1.json", b"{", "golden_invalid"),
    ("golden_runtime_v1.json", b"{}", "golden_digest_mismatch"),
    ("report_template_v1.json", b"", "template_invalid"),
    ("sandbox_evaluator.py", None, "evaluator_unavailable"),
    ("sandbox_evaluator.py", b"def (", "evaluator_unavailable"),
])
def test_damaged_runtime_assets_are_immediate_failures(tmp_path, monkeypatch, filename, payload, error):
    copied = tmp_path / "runtime_assets"
    shutil.copytree(assets.ASSET_DIR, copied)
    monkeypatch.setattr(assets, "ASSET_DIR", copied)
    monkeypatch.delenv("GOLDEN_SET_FILE", raising=False)
    path = copied / filename
    if filename == "sandbox_evaluator.py":
        monkeypatch.setattr(assets, "EVALUATOR_PATH", path)
    if payload is None and path.exists():
        path.unlink()
    elif payload is not None:
        path.write_bytes(payload)

    database = tmp_path / "isolated.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE materials (code, name, specification, unit, brand, category, parsed_params)")
        db.execute("INSERT INTO materials VALUES ('SYNTH', '测试阀', 'DN25', '个', '', '给排水', '{}')")
    eval_id = start_governed_evaluation(
        rule_source="flywheel_alias", rule_key="synthetic", rule_type="ALIAS", normalized_spec={},
        db_path=str(database),
    )
    status = get_eval_task_status(eval_id)
    assert status["status"] == "failed"
    assert status["safety_passed"] is False
    assert status["benefit"] == "invalid"
    assert status["report"]["error_code"] == error
    with sqlite3.connect(database) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='rule_eval_runs'").fetchone()


def test_missing_or_empty_catalog_does_not_self_seed(tmp_path):
    db = tmp_path / "missing.db"
    with pytest.raises(EvalGateError, match="物料库缺失"):
        require_materials_catalog(str(db))
    assert not db.exists()
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE materials (code, name, specification, unit, brand, category, parsed_params)")
    with pytest.raises(EvalGateError) as exc:
        require_materials_catalog(str(db))
    assert exc.value.status_code == 409 and exc.value.code == "materials_unavailable"


def test_static_api_prefixes_precede_matching_parameter_routes():
    from fastapi.routing import APIRoute
    from app.server import app

    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    for i, route in enumerate(routes):
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        for prior in routes[:i]:
            if not (prior.methods & route.methods) or not prior.path_regex.fullmatch(path):
                continue
            old_parts = prior.path.strip("/").split("/")
            new_parts = route.path.strip("/").split("/")
            assert not any(old.startswith("{") and not new.startswith("{")
                           for old, new in zip(old_parts, new_parts)), (prior.path, route.path)
