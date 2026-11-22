# -*- coding: utf-8 -*-
"""
V015-M0 测试隔离与可复验环境（pytest 全局 conftest）。

职责：
1. 在任何 app.* 业务模块导入前，统一把 materials/auth/rules/config/vector/
   upload/output/log/feedback 路径重定向到本次测试会话专属临时目录。
2. 为隔离 CONFIG_DIR 复制一份生产只读配置快照，测试对配置的写入不触及业务 config/。
3. 提供运行时防串扰护栏：测试会话中任何模块若把可写 DB 路径指回业务库，立即失败。
4. 缺主要依赖时按“明确跳过而非伪通过”处理，并记录跳过原因。

环境变量约定（应用侧已支持）：
  MATERIALS_DB_PATH      物料/规则/飞轮业务库（含 llm_rule_engine）
  AUTH_DB_PATH           账号库（auth_manager / session_manager / audit_manager）
  CONFIG_DIR             规则与字典配置目录
  VECTOR_CACHE_DIR       向量索引缓存目录
  UPLOAD_DIR             上传与导出文件目录
  OUTPUT_DIR             生成类输出（审计 CSV 等）
  BINDING_FEEDBACK_FILE  绑定反馈 JSON
  FEEDBACK_SAMPLES_FILE  人工反馈样本 JSON
  GOLDEN_DETAIL_FILE     金标评测可写明细（金标/基线本身只读，不重定向）
  LOG_DIR                运行日志目录
"""
import os
import sys
import socket
import shutil
import tempfile
import ipaddress
import importlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROD_CONFIG_DIR = PROJECT_ROOT / "config"
PROD_APP_DIR = PROJECT_ROOT / "app"

# 业务库/账号库规范路径（用于护栏比对，文件本身可能不存在）
PROD_MATERIALS_DB = (PROD_APP_DIR / "materials.db").resolve()
PROD_AUTH_DB = (PROD_APP_DIR / "auth.db").resolve()
PROD_VECTOR_CACHE = (PROD_APP_DIR / "vector_cache").resolve()

# 隔离根目录：环境未显式指定时，放到系统临时目录，避免污染仓库
_ISOLATION_ROOT = Path(
    os.getenv("LISTTRANS_TEST_ROOT")
    or tempfile.mkdtemp(prefix="listtrans_test_")
).resolve()

# 仅标准库可用的轻量探针；重型依赖在 check_dependencies 中处理
def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


MISSING_CORE_DEPS = [
    name for name in ("pandas", "openpyxl", "fastapi")
    if not _module_available(name)
]
MISSING_AUTH_DEPS = [
    name for name in ("jose", "bcrypt", "dotenv")
    if not _module_available(name)
]
MISSING_VECTOR_DEPS = [
    name for name in ("numpy", "faiss", "sentence_transformers")
    if not _module_available(name)
]


def _isolated_layout(root: Path) -> dict:
    """构造一次测试会话的隔离路径布局并创建目录。"""
    layout = {
        "root": root,
        "materials_db": root / "db" / "materials.db",
        "auth_db": root / "db" / "auth.db",
        "config_dir": root / "config",
        "vector_cache_dir": root / "vector_cache",
        "upload_dir": root / "uploads",
        "output_dir": root / "output",
        "log_dir": root / "logs",
        "binding_feedback_file": root / "runtime" / "binding_feedback.json",
        "feedback_samples_file": root / "runtime" / "feedback_samples.json",
        # A-M0-03: eval_golden_set 的可写评测明细，隔离到 runtime 下（金标/基线只读不动）
        "golden_detail_file": root / "runtime" / "golden_last_run.json",
    }
    for key, path in layout.items():
        if key in ("materials_db", "auth_db", "binding_feedback_file",
                   "feedback_samples_file", "golden_detail_file"):
            path.parent.mkdir(parents=True, exist_ok=True)
        elif key != "root":
            path.mkdir(parents=True, exist_ok=True)
    return layout


def _apply_env(layout: dict) -> None:
    """在导入 app 业务模块前注入隔离环境变量。"""
    env_map = {
        "MATERIALS_DB_PATH": str(layout["materials_db"]),
        "AUTH_DB_PATH": str(layout["auth_db"]),
        "CONFIG_DIR": str(layout["config_dir"]),
        "VECTOR_CACHE_DIR": str(layout["vector_cache_dir"]),
        "UPLOAD_DIR": str(layout["upload_dir"]),
        "OUTPUT_DIR": str(layout["output_dir"]),
        "LOG_DIR": str(layout["log_dir"]),
        "BINDING_FEEDBACK_FILE": str(layout["binding_feedback_file"]),
        "FEEDBACK_SAMPLES_FILE": str(layout["feedback_samples_file"]),
        "GOLDEN_DETAIL_FILE": str(layout["golden_detail_file"]),
        "GOLDEN_SET_FILE": str(PROJECT_ROOT / "tests" / "fixtures" / "golden_set_v1.json"),
        "GOLDEN_SET_SHA256": "c8909a21b34fc12fd45ee96d625dec5798f670cdd725988beb99b2f2f216eae5",
        # 测试默认关闭 LLM 付费调用与开发态鉴权绕过（防止 .env 透传真实 Key）
        "LLM_ENABLED": "false",
        "DASHSCOPE_API_KEY": "",
        "DEEPSEEK_API_KEY": "",
        "AUTH_DEV_MODE": os.getenv("AUTH_DEV_MODE", "false"),
        "TESTING": "1",
    }
    os.environ.update(env_map)


def _snapshot_prod_config(layout: dict) -> None:
    """把生产 config/ 复制为隔离只读快照（隔离目录内允许测试写入）。"""
    dst = layout["config_dir"]
    if PROD_CONFIG_DIR.exists():
        for item in PROD_CONFIG_DIR.iterdir():
            if item.is_file():
                shutil.copy2(item, dst / item.name)


# ---------------------------------------------------------------------------
# 会话级初始化：必须先于 app.* 导入完成
# ---------------------------------------------------------------------------
LAYOUT = _isolated_layout(_ISOLATION_ROOT)
_apply_env(LAYOUT)
_snapshot_prod_config(LAYOUT)

# 确保项目根在 sys.path，兼容历史测试的 sys.path 写法
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _assert_inside_isolation_root(path: Path, module_name: str, attr: str) -> None:
    """统一规则：所有被校验的可写路径必须属于本次会话隔离根。

    业务库/业务缓存天然位于隔离根之外，因此本规则同时覆盖指回生产路径与
    指向隔离根外任意其他路径（含外部 DB）两类违规，不再只比对生产目标。
    """
    resolved = Path(path).resolve()
    if resolved != _ISOLATION_ROOT and _ISOLATION_ROOT not in resolved.parents:
        raise RuntimeError(
            f"测试隔离护栏触发：{module_name}.{attr}={resolved} "
            f"不属于本次隔离根 {_ISOLATION_ROOT}，禁止在测试会话中写隔离区外路径。"
        )


# 护栏校验对象：(模块名, 路径属性)。模块若提供 _resolve_db_path() 则优先取运行时实际值。
_GUARDRAIL_MODULES = [
    ("app.db_manager", "DB_PATH"),
    ("app.auth_manager", "AUTH_DB_PATH"),
    ("app.vector_index", "CACHE_DIR"),
    ("app.llm_rule_engine", "DB_PATH"),
    ("app.llm_client", "DB_PATH"),
    ("app.feedback_train", "FEEDBACK_FILE"),
]


def run_guardrail_checks() -> dict:
    """逐模块独立校验，严格区分「可识别缺依赖」与「真实违规/异常」。

    - ModuleNotFoundError：可识别的第三方依赖缺失，登记为待验证缺口，不抛异常、不伪通过；
    - 其他导入异常、属性/解析异常、路径逃逸隔离根：真实缺陷，异常原样向上传播，
      由 pytest 判定失败，禁止降格为环境缺口；
    - 正常模块的路径统一要求落在本次隔离根内。
    返回 {"checked": [...], "gaps": [...]}，供 fixture 与负向回归复用。
    """
    checked, gaps = [], []
    for module_name, attr in _GUARDRAIL_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            gaps.append((module_name, f"缺依赖待验证：{type(exc).__name__}: {exc}"))
            continue
        # 导入成功后的任何失败（含其他导入错误、属性缺失、路径违规）都必须传播为失败
        resolver = getattr(module, "_resolve_db_path", None)
        actual = Path(
            resolver() if callable(resolver) else getattr(module, attr)
        ).resolve()
        _assert_inside_isolation_root(actual, module_name, attr)
        checked.append((f"{module_name}.{attr}", str(actual)))
    return {"checked": checked, "gaps": gaps}


def _format_checks(result: dict) -> str:
    parts = [f"{k}={v}" for k, v in result["checked"]]
    parts.extend(f"{k}（{v}）" for k, v in result["gaps"])
    return "|".join(parts)


@pytest.fixture(scope="session", autouse=True)
def _isolation_guardrails():
    """会话级护栏：测试体写入前预检，会话结束后复查；违规在写入前即使 pytest 失败。"""
    # C-M0-01: 预检先于 yield，任何路径违规/非依赖异常在首个测试体执行前直接失败
    pre_check = run_guardrail_checks()
    os.environ["LISTTRANS_GUARDRAIL_PRECHECK"] = _format_checks(pre_check)
    yield
    # 结束后复查，捕获测试执行期间可能发生的路径漂移；违规同样传播为会话失败
    post_check = run_guardrail_checks()
    os.environ["LISTTRANS_GUARDRAIL_CHECKED"] = _format_checks(post_check)


BLOCKED_EXTERNAL_ATTEMPTS = []


def _is_loopback_target(address) -> bool:
    if address is None:
        return True
    if isinstance(address, tuple) and len(address) >= 1:
        host = str(address[0] or "").strip().lower()
    elif isinstance(address, str):
        host = address.strip().lower()
    else:
        return True
    if host in ("", "localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(scope="session", autouse=True)
def _offline_network_and_llm_guard():
    """C-M0-09: 会话级离线护栏，阻断非回环外呼与未 mock 的 LLM API 调用（含 daemon 线程）。"""
    orig_socket = socket.socket
    orig_create_connection = socket.create_connection
    orig_completions_create = None
    completions_cls = None

    if getattr(orig_socket, "_is_offline_guard", False):
        yield
        return

    class _OfflineTestSocket(orig_socket):
        _is_offline_guard = True

        def connect(self, address):
            if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback_target(address):
                msg = f"Offline test sandbox blocked external socket connect to {address!r}"
                BLOCKED_EXTERNAL_ATTEMPTS.append(msg)
                raise RuntimeError(msg)
            return super().connect(address)

        def connect_ex(self, address):
            if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback_target(address):
                msg = f"Offline test sandbox blocked external socket connect_ex to {address!r}"
                BLOCKED_EXTERNAL_ATTEMPTS.append(msg)
                raise RuntimeError(msg)
            return super().connect_ex(address)

    def _guarded_create_connection(address, *args, **kwargs):
        if not _is_loopback_target(address):
            msg = f"Offline test sandbox blocked external create_connection to {address!r}"
            BLOCKED_EXTERNAL_ATTEMPTS.append(msg)
            raise RuntimeError(msg)
        return orig_create_connection(address, *args, **kwargs)

    socket.socket = _OfflineTestSocket
    socket.create_connection = _guarded_create_connection

    try:
        from openai.resources.chat.completions import Completions as _Completions
        completions_cls = _Completions
        orig_completions_create = _Completions.create

        def _guarded_completions_create(self, *args, **kwargs):
            msg = "Offline test sandbox blocked unmocked LLM Completions.create call"
            BLOCKED_EXTERNAL_ATTEMPTS.append(msg)
            raise RuntimeError(msg)

        _Completions.create = _guarded_completions_create
    except Exception:
        completions_cls = None

    try:
        yield
    finally:
        socket.socket = orig_socket
        socket.create_connection = orig_create_connection
        if completions_cls is not None and orig_completions_create is not None:
            completions_cls.create = orig_completions_create


@pytest.fixture()
def isolated_layout():
    """暴露隔离布局给需要断言路径的测试。"""
    return dict(LAYOUT)


# 历史测试模块 -> 顶层导入即需要的第三方依赖（缺失时收集阶段必然 ImportError）。
# 仅登记当前 tests/ 下真实存在的模块；依赖补齐后自动恢复收集，无需改测试代码。
_COLLECTION_REQUIREMENTS = {
    "test_admin_dashboard.py": ("fastapi", "jose", "bcrypt", "dotenv"),
    "test_auth_security_fix.py": ("fastapi", "jose", "bcrypt", "dotenv"),
    "test_auto_binding.py": ("pandas", "openpyxl"),
    "test_feedback_miner.py": ("pandas",),
    "test_flywheel_end2end.py": ("fastapi", "jose", "bcrypt", "dotenv", "pandas"),
    "test_llm_integration.py": ("dotenv",),
    "test_phase0_phase1.py": ("fastapi", "jose", "bcrypt", "dotenv", "pandas"),
}

# A-M0-03: 可正常收集（顶层仅依赖标准库/可导入 app 模块）、但运行期需要重依赖的
# 测试模块 -> 运行期依赖。缺失时仅显式跳过该模块自身，不再一刀切全局跳过。
# 未登记的纯标准库模块（test_query_*、test_v0_0_8_llm_rules、test_rule_deadlock_fix、
# test_m0_isolation）在任何依赖组合下都真实执行。
_RUNTIME_REQUIREMENTS = {
    # flywheel_sandbox 顶层可导入，但正式沙箱子进程需要以下运行依赖。
    # 该链路导入 app.matcher/app.vector_index/app.db_manager，缺向量/pandas 依赖必然失败
    "test_flywheel_sandbox.py": ("numpy", "faiss", "sentence_transformers", "pandas"),
}


def pytest_ignore_collect(collection_path, config):
    """缺依赖模块在收集前显式忽略并告警，杜绝 collection error 伪装成环境问题。"""
    name = Path(str(collection_path)).name
    required = _COLLECTION_REQUIREMENTS.get(name)
    if not required:
        return False
    missing = [dep for dep in required if not _module_available(dep)]
    if missing:
        sys.stderr.write(
            f"[M0 隔离] 显式跳过 {name}：缺少依赖 {', '.join(missing)}；"
            "按 requirements-test.txt 安装后自动恢复收集。\n"
        )
        return True
    return False


def pytest_collection_modifyitems(config, items):
    """按测试模块的实际依赖精细化 skip，替代缺核心依赖即全局跳过的一刀切。"""
    # 运行期依赖缺失的模块（按模块整体显式 skip，原因精确到缺失依赖）。
    # 未登记的纯标准库模块（含 nodeid 带 vector/golden 字样者，如
    # test_query_cleaning_pipeline.py::test_sheet_hint_vector）一律真实执行，
    # 不按用例名关键字猜测依赖，避免过度跳过。
    runtime_skips = {}
    for module_name, required in _RUNTIME_REQUIREMENTS.items():
        missing = [dep for dep in required if not _module_available(dep)]
        if missing:
            runtime_skips[module_name] = pytest.mark.skip(
                reason=f"运行期依赖缺失：{', '.join(missing)}"
            )
    for item in items:
        # smoke 用例只依赖标准库与隔离注入，重依赖缺失时仍必须真实执行
        if item.get_closest_marker("smoke"):
            continue
        module_name = Path(str(item.fspath)).name
        if module_name in runtime_skips:
            item.add_marker(runtime_skips[module_name])


def pytest_report_header(config):
    return [
        f"LISTTRANS 测试隔离根目录: {_ISOLATION_ROOT}",
        f"物料库: {LAYOUT['materials_db']}",
        f"账号库: {LAYOUT['auth_db']}",
        f"缺失核心依赖: {MISSING_CORE_DEPS or '无'}",
        f"缺失鉴权依赖: {MISSING_AUTH_DEPS or '无'}",
        f"缺失向量依赖: {MISSING_VECTOR_DEPS or '无'}",
    ]
