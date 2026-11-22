# -*- coding: utf-8 -*-
"""
V015-M0 R03 隔离护栏负向回归（C-M0-01 / C-M0-02 整改的防复发证据）。

本模块只依赖 Python 标准库与 pytest，在 pandas/fastapi 等重依赖缺失时仍真实执行。
违规路径全部通过受控模块替身（SimpleNamespace）注入，全程不导入真实业务模块去
打开注入路径，也不创建注入的业务库/外部 DB 文件；每个用例外层都包一次业务资产
快照比对，证明负向探针本身不触碰业务库、配置、向量缓存等被监视资产。

覆盖：
1. conftest 护栏：业务库路径、隔离根外路径在写入前预检阶段即抛 RuntimeError；
2. 可识别缺依赖（ModuleNotFoundError）只登记 gaps，不抛异常、不伪通过；
3. 导入成功后的非依赖异常（如属性缺失）必须原样传播，禁止降格为环境缺口；
4. 真实可导入模块的解析路径必须落在本次隔离根内，缺口须可独立复现；
5. verify 三态裁决：PASS/0、FAIL/1、PENDING/2 严格对应，含 main() 端到端复验。
"""
import importlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 护栏模块 -> 路径属性，与 tests/conftest.py / tests/verify_isolation.py 的校验清单一致
_ATTR_BY_MODULE = {
    "app.db_manager": "DB_PATH",
    "app.auth_manager": "AUTH_DB_PATH",
    "app.vector_index": "CACHE_DIR",
    "app.llm_rule_engine": "DB_PATH",
    "app.llm_client": "DB_PATH",
    "app.feedback_train": "FEEDBACK_FILE",
}


def _load_source(name: str, rel_path: Path):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# verify_isolation 模块级仅修正 sys.path，无写入副作用，全模块复用一份
VERIFY = _load_source("m0_r03_verify_r03", Path("tests") / "verify_isolation.py")


@pytest.fixture
def env_restored():
    """保存/恢复环境变量：加载 conftest 源码与运行 verify.main() 都会改写 env。"""
    saved = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def guardrail_cf(env_restored, tmp_path):
    """每次独立加载 conftest 源码，获得专属隔离根（隔离外部 LISTTRANS_TEST_ROOT）；用后只清理专属目录。"""
    dedicated_root = (tmp_path / "guardrail_iso_root").resolve()
    os.environ["LISTTRANS_TEST_ROOT"] = str(dedicated_root)
    cf = _load_source("m0_r03_conftest_r03", Path("tests") / "conftest.py")
    try:
        yield cf
    finally:
        if Path(cf._ISOLATION_ROOT).resolve() == dedicated_root:
            shutil.rmtree(cf._ISOLATION_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def _business_assets_unchanged():
    """同一外层业务快照包住每个负向用例，证明探针不污染被监视业务资产。"""
    before = VERIFY.collect_snapshot()
    yield
    after = VERIFY.collect_snapshot()
    assert VERIFY.diff_snapshots(before, after) == []


def _valid_fake_modules(cf) -> dict:
    """全部路径合法（落在该 conftest 实例隔离根内）的受控模块替身。"""
    layout = cf.LAYOUT
    return {
        "app.db_manager": SimpleNamespace(DB_PATH=str(layout["materials_db"])),
        "app.auth_manager": SimpleNamespace(AUTH_DB_PATH=str(layout["auth_db"])),
        "app.vector_index": SimpleNamespace(CACHE_DIR=str(layout["vector_cache_dir"])),
        "app.llm_rule_engine": SimpleNamespace(DB_PATH=str(layout["materials_db"])),
        "app.llm_client": SimpleNamespace(DB_PATH=str(layout["materials_db"])),
        "app.feedback_train": SimpleNamespace(
            FEEDBACK_FILE=str(layout["feedback_samples_file"])
        ),
    }


# ---------------------------------------------------------------------------
# C-M0-01：conftest 会话护栏负向回归
# ---------------------------------------------------------------------------

def test_precheck_rejects_production_path_before_yield(guardrail_cf):
    """注入业务库路径：预检在首个 next（yield 之前）即抛 RuntimeError，禁止吞掉。"""
    cf = guardrail_cf
    modules = _valid_fake_modules(cf)
    modules["app.db_manager"] = SimpleNamespace(
        DB_PATH=str(cf.PROD_MATERIALS_DB)
    )
    checked_marker_before = os.environ.get("LISTTRANS_GUARDRAIL_CHECKED")
    gen = cf._isolation_guardrails.__wrapped__()
    with patch.object(
        cf.importlib, "import_module", side_effect=lambda name: modules[name]
    ):
        with pytest.raises(RuntimeError, match="测试隔离护栏触发"):
            next(gen)
    # 预检失败后不得产生“复查通过”标记
    assert os.environ.get("LISTTRANS_GUARDRAIL_CHECKED") == checked_marker_before


def test_precheck_rejects_external_path_outside_isolation_root(guardrail_cf):
    """注入隔离根外的其他 DB 路径：同样在预检阶段失败，且该文件永不被打开/创建。"""
    cf = guardrail_cf
    external_db = cf.LAYOUT["root"].parent / "m0_r03_never_opened.db"
    assert not external_db.exists()
    modules = _valid_fake_modules(cf)
    modules["app.db_manager"] = SimpleNamespace(DB_PATH=str(external_db))
    gen = cf._isolation_guardrails.__wrapped__()
    with patch.object(
        cf.importlib, "import_module", side_effect=lambda name: modules[name]
    ):
        with pytest.raises(RuntimeError, match="测试隔离护栏触发"):
            next(gen)
    assert not external_db.exists()


def test_missing_dependency_recorded_as_gap_not_failure(guardrail_cf):
    """可识别缺依赖：登记 gaps、护栏函数与 fixture 均不失败，预检/复查标记含原因。"""
    cf = guardrail_cf
    modules = _valid_fake_modules(cf)

    def controlled_import(name):
        if name == "app.db_manager":
            raise ModuleNotFoundError("No module named 'pandas'")
        return modules[name]

    with patch.object(cf.importlib, "import_module", side_effect=controlled_import):
        result = cf.run_guardrail_checks()
        gen = cf._isolation_guardrails.__wrapped__()
        next(gen)  # 预检通过，到达 yield
        with pytest.raises(StopIteration):
            next(gen)  # 复查通过，生成器正常结束

    assert [name for name, _ in result["gaps"]] == ["app.db_manager"]
    assert len(result["checked"]) == len(_ATTR_BY_MODULE) - 1
    for _, path_str in result["checked"]:
        resolved = Path(path_str).resolve()
        assert (
            resolved == cf._ISOLATION_ROOT
            or cf._ISOLATION_ROOT in resolved.parents
        )
    assert "缺依赖待验证" in os.environ["LISTTRANS_GUARDRAIL_PRECHECK"]
    assert "缺依赖待验证" in os.environ["LISTTRANS_GUARDRAIL_CHECKED"]


def test_non_dependency_error_propagates_as_failure(guardrail_cf):
    """导入成功后的属性缺失（非 ModuleNotFoundError）必须原样传播，不得降格。"""
    cf = guardrail_cf
    modules = _valid_fake_modules(cf)
    modules["app.db_manager"] = SimpleNamespace()  # 无 DB_PATH 也无 _resolve_db_path
    with patch.object(
        cf.importlib, "import_module", side_effect=lambda name: modules[name]
    ):
        with pytest.raises(AttributeError):
            cf.run_guardrail_checks()
        gen = cf._isolation_guardrails.__wrapped__()
        with pytest.raises(AttributeError):
            next(gen)


def test_real_importable_modules_resolve_inside_isolation_root(guardrail_cf):
    """真实导入（无替身）：可导入模块路径必须在隔离根内；每个缺口须可独立复现。"""
    cf = guardrail_cf
    # app.* 可能已被 pytest 会话内其他模块导入并缓存；清缓存后在本 conftest 的
    # 隔离环境下重新导入，结束后恢复原缓存，避免污染同进程其他测试。
    cached = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name.startswith("app.")
    }
    try:
        result = cf.run_guardrail_checks()
        gap_modules = {name for name, _ in result["gaps"]}
        checked_keys = {key for key, _ in result["checked"]}
        expected_keys = {
            f"{name}.{attr}"
            for name, attr in _ATTR_BY_MODULE.items()
            if name not in gap_modules
        }
        assert checked_keys == expected_keys
        for _, path_str in result["checked"]:
            resolved = Path(path_str).resolve()
            assert (
                resolved == cf._ISOLATION_ROOT
                or cf._ISOLATION_ROOT in resolved.parents
            )
        # 缺口只能是可识别缺依赖：独立导入必须复现 ModuleNotFoundError
        for module_name in gap_modules:
            with pytest.raises(ModuleNotFoundError):
                importlib.import_module(module_name)
    finally:
        app_pkg = sys.modules.get("app")
        for name in list(sys.modules):
            if name.startswith("app.") and name not in cached:
                sys.modules.pop(name, None)
                if app_pkg is not None:
                    sub = name.split(".", 1)[1]
                    if "." not in sub and hasattr(app_pkg, sub):
                        delattr(app_pkg, sub)
        sys.modules.update(cached)
        if app_pkg is not None:
            for name, mod in cached.items():
                sub = name.split(".", 1)[1]
                if "." not in sub:
                    setattr(app_pkg, sub, mod)


# ---------------------------------------------------------------------------
# C-M0-02：verify_isolation 三态裁决负向回归
# ---------------------------------------------------------------------------

_DB_OK = {"materials": {"rows": 1}, "auth": {"rows": 1}}
_IMPORT_CLEAN = {"hard_errors": [], "pending": []}


@pytest.mark.parametrize(
    "changes,db_result,import_result,expected",
    [
        ([], _DB_OK, _IMPORT_CLEAN, ("PASS", 0)),
        (["文件变化: app/materials.db"], _DB_OK, _IMPORT_CLEAN, ("FAIL", 1)),
        ([], _DB_OK, {"hard_errors": ["路径逃逸"], "pending": []}, ("FAIL", 1)),
        ([], {"materials": {"rows": 0}, "auth": {"rows": 1}},
         _IMPORT_CLEAN, ("FAIL", 1)),
        ([], _DB_OK, {"hard_errors": [], "pending": ["app.db_manager"]},
         ("PENDING", 2)),
    ],
)
def test_decide_verdict_three_states(changes, db_result, import_result, expected):
    assert VERIFY.decide_verdict(changes, db_result, import_result) == expected


def _run_verify_main(out_path: Path, import_side_effect=None,
                     check_wrapper=None) -> tuple:
    patches = []
    if import_side_effect is not None:
        patches.append(
            patch.object(VERIFY.importlib, "import_module",
                         side_effect=import_side_effect)
        )
    if check_wrapper is not None:
        patches.append(
            patch.object(VERIFY, "check_imported_paths", side_effect=check_wrapper)
        )
    with patch.object(sys, "argv",
                      ["verify_isolation.py", "--out", str(out_path)]):
        for started in patches:
            started.start()
        try:
            exit_code = VERIFY.main()
        finally:
            for started in patches:
                started.stop()
    report = json.loads(out_path.read_text(encoding="utf-8"))
    return exit_code, report


def test_verify_main_fail_on_path_escape(env_restored, tmp_path):
    """端到端：替身模块全部指向隔离根外路径 -> FAIL/1，且注入文件永不被打开。"""
    outside = tmp_path / "m0_r03_outside_never_opened.db"
    modules = {
        name: SimpleNamespace(**{attr: str(outside)})
        for name, attr in _ATTR_BY_MODULE.items()
    }
    exit_code, report = _run_verify_main(
        tmp_path / "fail.json",
        import_side_effect=lambda name: modules[name],
    )
    assert exit_code == 1
    assert report["verdict"] == "FAIL"
    assert report["exit_code"] == 1
    assert report["hard_errors"]
    assert all(
        not item["inside_isolation"]
        for item in report["dependency_probe"]["module_path_checks"]
    )
    assert not outside.exists()


def test_verify_main_pending_on_missing_dependency(env_restored, tmp_path):
    """端到端：全部模块可识别缺依赖 -> PENDING/2，不计 hard_errors。"""
    def missing(name):
        raise ModuleNotFoundError("No module named 'pandas'")

    exit_code, report = _run_verify_main(
        tmp_path / "pending.json", import_side_effect=missing
    )
    assert exit_code == 2
    assert report["verdict"] == "PENDING"
    assert report["exit_code"] == 2
    assert report["hard_errors"] == []
    assert len(report["pending_modules"]) == len(_ATTR_BY_MODULE)
    assert report["business_hash_unchanged"] is True


def test_verify_main_pass_when_all_checks_clean(env_restored, tmp_path):
    """端到端：路径全在隔离根内、写入与快照均满足 -> PASS/0。"""
    original_check = VERIFY.check_imported_paths

    def all_valid_check(layout):
        modules = {
            "app.db_manager": SimpleNamespace(
                DB_PATH=str(layout["materials_db"])),
            "app.auth_manager": SimpleNamespace(
                AUTH_DB_PATH=str(layout["auth_db"])),
            "app.vector_index": SimpleNamespace(
                CACHE_DIR=str(layout["vector_cache_dir"])),
            "app.llm_rule_engine": SimpleNamespace(
                DB_PATH=str(layout["materials_db"])),
            "app.llm_client": SimpleNamespace(
                DB_PATH=str(layout["materials_db"])),
            "app.feedback_train": SimpleNamespace(
                FEEDBACK_FILE=str(layout["feedback_samples_file"])),
        }
        with patch.object(
            VERIFY.importlib, "import_module",
            side_effect=lambda name: modules[name]
        ):
            return original_check(layout)

    exit_code, report = _run_verify_main(
        tmp_path / "pass.json", check_wrapper=all_valid_check
    )
    assert exit_code == 0
    assert report["verdict"] == "PASS"
    assert report["exit_code"] == 0
    assert report["hard_errors"] == []
    assert report["pending_modules"] == []
    assert report["business_hash_unchanged"] is True
    assert all(
        item["status"] == "ok"
        for item in report["dependency_probe"]["module_path_checks"]
    )
