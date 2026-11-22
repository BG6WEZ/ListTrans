# 测试目录与执行约束

正式回归测试保留，不能为清爽删除仍有业务覆盖的旧版本命名测试。当前全套测试尚未完成业务库隔离；V015-M0整改并审核通过前，不直接执行`pytest tests/`或会初始化真实数据库的历史脚本。

| 类别 | 当前入口 | 说明 |
|---|---|---|
| 清洗与参数 | test_query_preprocessor.py、test_query_cleaning_pipeline.py | 保留已有31项；仍需补否定等真实反例；注意导入副作用 |
| 权限/后台 | test_auth_security_fix.py、test_admin_dashboard.py | 保留；可写DB隔离待V015-M0 |
| 绑定/规则/飞轮 | test_auto_binding.py、test_v0_0_8_llm_rules.py、test_rule_deadlock_fix.py、test_phase0_phase1.py、test_feedback_miner.py、test_flywheel_sandbox.py、test_flywheel_end2end.py | 含有价值的回归，部分会写全局配置/业务库，必须整改隔离 |
| LLM/综合集成 | test_llm_integration.py、integration_test.py | 部分硬编码真实库，禁止未隔离直接运行；不得触发真实付费调用 |
| 评测/性能 | eval_golden_set.py、regression_runner.py、benchmark.py、build_golden_set.py | 是工具入口，不是都属于pytest单测；正式运行资产将在V0.1.6收口 |
| 数据基准 | fixtures/golden_set_v1.json、golden_baseline.json、ground_truth_500.json | 保留，不擅自删难例/重写基线；换码审查在V0.1.7 |

`fixtures/golden_last_run.json`是可生成输出，旧记录已移到`doc/V0.1.4/evidence/golden_last_run_20260902.json`保存，不再当版本化fixture。当前评测程序仍会生成原路径，未来输出独立化在V0.1.6实施。

退役的`app/test_engine.py`是含模块导入写库副作用的旧测试，存为`doc/archive/legacy_tests/app_test_engine.py.txt`，不再置于生产app或被默认发现。独有场景由V015-M0盘点迁移，归档不等于已替代全部覆盖。

## V015-M0 测试隔离与可复验环境

`conftest.py`在任何`app.*`业务模块导入前，统一把可写路径重定向到会话专属临时目录（默认系统临时目录`listtrans_test_*`，可用`LISTTRANS_TEST_ROOT`指定），并把生产`config/`复制为隔离快照：

| 环境变量 | 注入目标 | 默认业务路径 |
|---|---|---|
| MATERIALS_DB_PATH | 物料/规则/飞轮库（db_manager、llm_rule_engine） | app/materials.db |
| AUTH_DB_PATH | 账号/会话/审计库（auth_manager） | app/auth.db |
| CONFIG_DIR | 规则字典配置副本 | config/ |
| VECTOR_CACHE_DIR | 向量索引缓存 | app/vector_cache/ |
| UPLOAD_DIR | 上传与导出文件 | app/uploads/ |
| OUTPUT_DIR | 审计CSV等生成输出 | data/ |
| BINDING_FEEDBACK_FILE / FEEDBACK_SAMPLES_FILE | 反馈JSON | app/*.json |
| LOG_DIR | 运行日志 | logs/ |

测试会话默认`TESTING=1`、`LLM_ENABLED=false`、`AUTH_DEV_MODE=false`，不触发付费LLM；缺依赖时相关用例按原因显式skip，不伪通过。

依赖安装（独立测试依赖清单）：`python -m pip install -r requirements-test.txt`。

最低冒烟（仅标准库，无需重依赖）：

```text
python tests/verify_isolation.py
python -m pytest tests/test_m0_isolation.py -v
```

全量检查入口（依赖完整后）：`python -m pytest tests/ -ra`。未完成依赖安装前禁止直接跑全套历史测试。退役测试独有场景盘点见`doc/V0.1.5/evidence/m0_legacy_test_inventory.md`，迁移在M1/M3执行。

## V015-M1 查询契约回归与 LEGACY-01 播种 fixture

| 新增项 | 说明 |
|---|---|
| test_query_contract.py | prepare_query 契约回归 11 项：四入口（web/CLI/黄金集/飞轮沙箱）一致性、施工噪声不进入查询文本、未知专业/单位/品牌的回退链、preserve_original 原文保留与保存进度兼容 |
| fixtures/sandbox_materials_v1.json | LEGACY-01 沙箱播种 fixture（35 行：34 金标目标物料 + 1 有害电缆），test_flywheel_sandbox.py 的 seed_isolated_materials 据此播种隔离库，使评测 rank 可观察；不改动金标集与生产物料库 |
| fixtures/gen_standard_template.py | 可复现标准模板生成器：生成 ASSET-M1-01 双 sheet XLSX 供 test_auto_binding.py 真实导入导出断言；因 `.gitignore` 全局忽略 `*.xlsx`，模板 XLSX 不入库、按需由该脚本生成（header=1 布局，与 import_materials_from_excel 对齐） |

沙箱评测子进程的明细输出由 conftest 经 `GOLDEN_DETAIL_FILE` 重定向到隔离临时目录；沙箱 worker 也为基线/候选子进程分别强制指定沙箱临时明细文件。仓外独立诊断脚本若直接调用评测/沙箱入口，必须自行注入该环境变量，否则会落回 `tests/fixtures/golden_last_run.json`（M1 R01 发生过一次误写，已留证并恢复为不存在状态，见`doc/V0.1.5/rounds/V015-M1_R01_开发小结.md`污染留痕节）。

### M1 R02 门禁契约

`tests/eval_golden_set.py` 在兼容原 Top5 两位展示分的同时，为每例输出全部 Top10 原始未舍入候选分，以及物料/金标/模型/向量缓存/预算快照。`app/flywheel_sandbox.py` 保留原 rank 门禁，仅对基线正确 Top1 样本逐例保护正确目标分 `T` 与领先间隔 `margin=T-W`；固定绝对浮点容差 `1e-6`，单例退化、不可比或完整性异常直接拒绝，不用其他样本改善抵消。

`test_flywheel_sandbox.py` 的模型替身覆盖得分下降、间隔缩小、缺原始分、非法/非有限分、样本与字段异常、不可比候选、快照不一致和子进程非零退出；有害/有益/中性最终仍走真实离线双子进程。`test_flywheel_end2end.py` 同样使用上述 35 行合成 fixture 建立可观察基线，禁止空库全 0 稳态冒充有益规则通过。实测证据见 `doc/V0.1.5/rounds/V015-M1_R02_开发小结.md`；本轮开发交付不代替独立审核或统筹放行。
