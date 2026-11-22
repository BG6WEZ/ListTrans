# V015-M0 R02 交付哈希清单

- 生成时间：2026-11-13（Asia/Shanghai）
- 基线 commit：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`（`git rev-parse HEAD` 实测一致，未提交、未 push）
- 算法：SHA256（PowerShell `Get-FileHash -Algorithm SHA256`，小写十六进制）
- 说明：本清单覆盖 R02 针对 R01 审核发现 A-M0-01/02/03 实际改动的 8 个代码/测试文件、3 个新证据文件与 4 个管理/索引文档；R01 已交付且 R02 未改动的文件哈希沿用 [m0_delivery_hashes.md](m0_delivery_hashes.md)，本清单不重复列示。仓库中其他未提交差异（目录治理、scripts/、doc 其他版本目录等）为用户/前序工作，本包未改动。

## R02 整改代码与测试文件（8 个）

| SHA256 | 文件 | 对应发现 |
|---|---|---|
| e4971eb95647c6c0e1b606145f1f76e120c7ad0d126b17339041001e4859c51b | app/llm_client.py | A-M0-01 |
| 2f7c7bb85a4743a3382385db2f984afb5d22729598d9aab3a62a5c6f7110a091 | tests/test_llm_integration.py | A-M0-01 |
| f223af4e06c819ca66f3b1342df9187d9b81ac0e005f55d6c30ad476f6e2645f | app/llm_rule_engine.py | A-M0-02 |
| 616f8603a95ec61e8a622c56cb3eea61e4651752f58a5175994da8124f932aad | tests/test_auto_binding.py | A-M0-02 |
| 2da38800fe4cc9160ecc714f2f47c47e7ed220d7afb4be7d0ef9df5bbc2ad930 | app/flywheel_sandbox.py | A-M0-02 |
| 2532fe1b1f8d337697cab1558463a523289020ceba0d275a09f60c86bc72fa25 | tests/eval_golden_set.py | A-M0-02 |
| 85daa88f7decfd51675cfd0739c970a52581580a94934246e8a183ce620440b3 | tests/conftest.py | A-M0-03 |
| 85543b567faff752c90de796044a396fb64d3e327975341b6f739f6164dc8dd6 | tests/verify_isolation.py | A-M0-03 |

## R02 evidence 实测记录（3 个）

| SHA256 | 文件 |
|---|---|
| 56538394bf496eea1aa32eab790deebbbe9464f014ddc5de8613d7a8abc62535 | doc/V0.1.5/evidence/m0_r02_isolation_verify.json（末次复验重写；结论仍 verdict=PASS、business_hash_unchanged=true） |
| a3f7f67eca1d396a5d76af5fbb16ba563e3a02858de737067933e88d88f693be | doc/V0.1.5/evidence/m0_r02_full_pytest.log |
| 3514101613b3c4c09aeb58fce6a8b878b07a6354df7eba7de0ac8af7c676c1b7 | doc/V0.1.5/evidence/m0_r02_smoke_pytest.log |

## R02 管理与小结文档（4 个）

| SHA256 | 文件 |
|---|---|
| dd36254fb04fa741201d3a9a8a2dea5e6d945dc6765f8ee98bc98649719a0f3c | doc/V0.1.5/rounds/V015-M0_R02_开发小结.md |
| c0d54a387abd7e3383c0a606603c211280dfabc5ec05f2c9a9bcf51e3f087457 | doc/V0.1.5/02_任务台账.md（仅 M0 行状态、审核问题表三项状态与证据链接） |
| 23625000eeffa1172de5f845db79d4c474e1e354843926f49c0fe689484e2d93 | doc/V0.1.5/evidence/README.md（占位说明补为证据索引） |
| bfc85110308577f8267cb3a2c87aa7453834d478059851b23340c884ec4e8a74 | doc/V0.1.5/rounds/README.md（占位说明补为轮次索引） |

注：本清单自身哈希自指无意义，以文件系统实测重算结果为准。

## 业务资产零改动佐证

- R02 全量 pytest（41 passed/4 skipped）、smoke（5 passed）与 verify 运行后，`git status --short -- app config logs data` 无业务库/配置/缓存/日志/CSV 的新增或修改条目。
- 业务文件级与目录树哈希前后快照见 [m0_r02_isolation_verify.json](m0_r02_isolation_verify.json)：`business_hash_unchanged=true`、`business_changes=[]`、`verdict=PASS`；materials.db、auth.db、两个运行时 JSON、config 16 文件、vector_cache 5 文件、logs、data 前后哈希逐项一致。
- 一次性副作用/AST 探针脚本位于仓库外 `%TEMP%`（`listtrans_r02_probe.py`、`listtrans_r02_ast_probe.py`），未入库、不属交付物。
