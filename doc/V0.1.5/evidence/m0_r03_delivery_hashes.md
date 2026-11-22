# V015-M0 R03 交付哈希清单

- 生成时间：2026-11-13（Asia/Shanghai）
- 基线 commit：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`（`git rev-parse HEAD` 实测一致，未提交、未 push）
- 算法：SHA256（PowerShell `Get-FileHash -Algorithm SHA256`，小写十六进制）
- 说明：本清单覆盖 R03 按 [R02 统筹裁决](../rounds/V015-M0_R02_统筹裁决.md) 实际改动的 2 个测试设施文件、1 个新增负向回归文件、21 个 R03 证据文件与 4 个管理/小结文档。R01/R02 已交付且 R03 未改动的文件哈希沿用 [m0_delivery_hashes.md](m0_delivery_hashes.md) 与 [m0_r02_delivery_hashes.md](m0_r02_delivery_hashes.md)，本清单不重复列示。独立 venv 位于仓库内 `venv/`（`.gitignore` 忽略，不入库），其解释器版本与完整依赖清单以 [m0_r03_venv_pip_freeze.txt](m0_r03_venv_pip_freeze.txt) 留痕。仓库中其他未提交差异（app/ 前序轮次改动、目录治理、scripts/、其他版本 doc 等）为用户/前序工作，本包未改动。

## R03 整改代码与测试文件（3 个）

| SHA256 | 文件 | 对应发现 |
|---|---|---|
| 207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033 | tests/conftest.py | C-M0-01 |
| 090ba89a25813f42910bd245b1bd2cd05bf580ffdc888ce3fe19ec523ad1eb62 | tests/verify_isolation.py | C-M0-02 |
| ece380d56a8899b0c1f9f735f7ed28823470036d7ac32fbe00b784877e55839c | tests/test_m0_guardrail_regression.py（新增，13 用例负向回归） | C-M0-01/02 |

## R03 evidence 实测记录（21 个）

| SHA256 | 文件 |
|---|---|
| 0827e9c21925ef006388d7c9356a2298f8c9795d7064862d91a6543bbdd283ab | doc/V0.1.5/evidence/m0_r03_isolation_verify.json（系统 Python，PENDING/2） |
| 2d46c7914c83ee031aa6f2bbb8d0d68aebedc825560eb65fea032c2cb3916c47 | doc/V0.1.5/evidence/m0_r03_smoke_pytest.log（5 passed） |
| 42a393c291a82116227cbc9d58cc4a74e357212c79b249e7dbcc660c14c36de6 | doc/V0.1.5/evidence/m0_r03_full_pytest.log（54 passed / 4 skipped） |
| 26d87fa2c175f972a1662c010fabb8b2cbc4aba0ae1d40b07685536abb93d873 | doc/V0.1.5/evidence/m0_r03_guardrail_regression.log（13 passed） |
| 037d58e286f05291bddf4028890fe6ae4490c5641903e1531556dcd48520c8b6 | doc/V0.1.5/evidence/m0_r03_coordinator_probe_rerun.json（统筹探针重定向复跑，negative_probes_reproduced=false） |
| a5371ecd6ef6a2fe4b641d6273f69a1ee3ae0d471948e38bbc8117648ee7a45f | doc/V0.1.5/evidence/m0_r03_venv_pip_install.log（PIP_EXIT=0） |
| 7e4e3cccfe8543da9cb326c0478f264945366789b1a9bf350c0de44a67381008 | doc/V0.1.5/evidence/m0_r03_venv_pip_freeze.txt |
| de96eb2edff9ab68d8a2c3a4a5a2a8099e3f8ed689502677a7f202bc9cffdc3d | doc/V0.1.5/evidence/m0_r03_venv_verify.json（venv 首次 verify，PASS/0） |
| 89f447edc11008b2fa31f400ac2117c7927ce5c09f60eabada7ccdc14e9214fd | doc/V0.1.5/evidence/m0_r03_venv_full_pytest.log（7 failed / 68 passed / 1 skipped / 2 errors，失败如实留痕） |
| 8cc4fd1ce0bf745e91090a60ad722a87c65926db77d410a793222d47b178b858 | doc/V0.1.5/evidence/m0_r03_venv_single_summary.txt（逐模块退出码汇总） |
| 0fc8570d8bc23792b22a86953b2f2046ab276e5018e6f73d22ed00777e4b4014 | doc/V0.1.5/evidence/m0_r03_venv_single_test_admin_dashboard.log（1 failed / 2 errors） |
| 96b06e94592f34f06b2975d8bb1f8430495a248afc344c7d70c3d61cd34a8114 | doc/V0.1.5/evidence/m0_r03_venv_single_test_auth_security_fix.log（1 failed / 1 passed） |
| 8feb051ca20fd8a910a710455263f010254e5f3706bcba1e28d6932c276b452f | doc/V0.1.5/evidence/m0_r03_venv_single_test_auto_binding.log（1 failed / 1 passed / 1 skipped） |
| 0aab6d7a0d90beea9a105df80eb47dd9e30a4964c67be7377ecbe07937514905 | doc/V0.1.5/evidence/m0_r03_venv_single_test_feedback_miner.log（1 failed / 1 passed） |
| 14e9c8bbcb2e65eebe70d9979540fc21349328e689ad356cf9f5df94fc558099 | doc/V0.1.5/evidence/m0_r03_venv_single_test_flywheel_sandbox.log（1 failed / 3 passed，有害规则门禁真实断言失败） |
| 5dcab4e0e74b3e6dba990da54ee70d40d663f15e596ad0b5ac1dab3d62497023 | doc/V0.1.5/evidence/m0_r03_venv_single_test_flywheel_end2end.log（1 passed） |
| 9995c4bcbd9606b9d37074bb796a666171d71356ce4839412b85ef89ba4a9e1a | doc/V0.1.5/evidence/m0_r03_venv_single_test_phase0_phase1.log（3 passed） |
| 35656eb8d1a2b7ee3c00c9e5eb4362114a207a24555cc5082a4941183bac9890 | doc/V0.1.5/evidence/m0_r03_venv_single_test_llm_integration.log（6 passed，全 mock） |
| 5c12c30f55b39e22ad3b492080c25a7f3e3416b2a1d72595d9cf2f276a0279c7 | doc/V0.1.5/evidence/m0_r03_venv_single_test_m0_isolation.log（单跑 5 passed） |
| e85a9590ee43ab810a978e2e480859294344a154169dc8c68ac60b55aa58510a | doc/V0.1.5/evidence/m0_r03_venv_order_coupling.log（end2end+m0_isolation 组合复现 2 failed「already exists」） |
| b4650cac17bff109577e23060e65be8e62ec1e72634d026936f78041245a88d7 | doc/V0.1.5/evidence/m0_r03_venv_verify_final.json（全部 venv 实验后末次 verify，PASS/0，business_hash_unchanged=true） |

## R03 管理与小结文档（4 个）

| SHA256 | 文件 |
|---|---|
| 25dc26ffdd66f6c32c846e192b1fb522c75b6785675f3631c2f598c20d266a0b | doc/V0.1.5/rounds/V015-M0_R03_开发小结.md |
| 79d0980a38e5ac71e1a599e23e44818ddb050561833885b7ac2b802ac78da7a1 | doc/V0.1.5/02_任务台账.md（仅 M0 行状态、C-M0-01/02 与 ENV-M0-01 进展、R03 开发留痕） |
| 68a9f9a3346ee6e14168db70aae6304c1b86e6e942f10e9365d540844ae866df | doc/V0.1.5/evidence/README.md（补 R03 证据索引） |
| f526b58952198f3bb421bbe9a321fe240daa61f53c4e6345edcd7e6d62b263b3 | doc/V0.1.5/rounds/README.md（补 R03 轮次索引） |

注：本清单自身哈希自指无意义，以文件系统实测重算结果为准。

## 业务资产零改动佐证

- 系统 Python 全量 pytest（54 passed/4 skipped）、负向回归（13 passed）、venv 全量与逐模块实验前后，`git status --short -- app config logs data model_offline` 中 config/logs/data/model_offline 无任何条目；app/ 下 8 项改动均为 R01/R02 前序轮次的未提交工作，R03 未修改任何 app 业务代码。
- 业务文件级与目录树哈希快照见 [m0_r03_isolation_verify.json](m0_r03_isolation_verify.json) 与 [m0_r03_venv_verify_final.json](m0_r03_venv_verify_final.json)：两者均 `business_hash_unchanged=true`、`business_changes=[]`；materials.db、auth.db、运行时 JSON、config 16 文件、vector_cache、logs、data 前后哈希逐项一致。
- 所有隔离根位于系统临时目录（`%TEMP%\listtrans_*`），负向回归用后 `shutil.rmtree` 清理；未下载模型（向量模型实测从本地 `model_offline/bge-small-zh-v1.5` 加载），未调用真实付费 LLM（LLM_ENABLED=false 且 LLM 测试全 mock），未操作线上账号。
