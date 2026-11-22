# V015-M0 R04 交付哈希清单

- 生成时间：2026-11-13（Asia/Shanghai）
- 基线 commit：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`（未提交、未 push；R04 为该基线上的未提交改动）
- 算法：SHA256（PowerShell `Get-FileHash -Algorithm SHA256`，小写十六进制，2026-11-13 落盘前重算复核一致）
- 说明：本清单覆盖 R04 按 [R03 统筹裁决](../rounds/V015-M0_R03_统筹裁决.md) 实际改动的 5 个测试文件、1 个测试依赖文件、2 个本机一次性诊断脚本（`.local-archive/`，AGENTS.md 约定目录，保留备查）与 5 个 R04 新证据文件。R01/R02/R03 已交付且 R04 未改动的文件哈希沿用 [m0_delivery_hashes.md](m0_delivery_hashes.md)、[m0_r02_delivery_hashes.md](m0_r02_delivery_hashes.md) 与 [m0_r03_delivery_hashes.md](m0_r03_delivery_hashes.md)，本清单不重复列示；旧探针、JSON 与交付清单未覆写。仓库中其他未提交差异（app/ 前序轮次改动、目录治理、scripts/、其他版本 doc 等）为用户/前序工作，本包未触碰。

## R04 整改代码与测试文件（6 个）

| SHA256 | 文件 | 对应发现 |
|---|---|---|
| 92c97d4e7b7d6b68490986feeb79a32e317b7211914258d942d638130acafa3a | tests/test_m0_isolation.py | A-M0-04 |
| 21a414db9f939b197b523a283c5ba69d9a2043e8c1dcfe99cc05b3adef8e8752 | tests/test_admin_dashboard.py | A-M0-05 |
| 4071b1d88343c19f65bc01b9889d6dc7a1275de8e284f6b4ccc68bb828d56055 | tests/test_auth_security_fix.py | A-M0-05 |
| da8e67a3c236e779adde1a03836ec0e45b56900b05039d550fb02f0dc9e13d49 | tests/test_auto_binding.py | A-M0-05 |
| 3c70c6660a0c0ec111903841ddf0888795e6cd81cd6c613e3e364cc3895a4228 | tests/test_feedback_miner.py | A-M0-05 |
| 1444253aae55a9121e8c549472a24ef2e4eb4e512d18e35a14e7536a34139c2b | requirements-test.txt（新增 psutil>=5.9.0，A-M0-05 修复后暴露的 Windows 环境依赖缺口） | A-M0-05 后续暴露 |

## R04 本机一次性诊断脚本（2 个，.local-archive/）

| SHA256 | 文件 | 用途 |
|---|---|---|
| 6ad48d3a78a786fdff2f3d1610d93e8da39dcf1e12acd796bfaf03c84a3df06d | .local-archive/r04_asset_snapshot.py | 业务资产前后快照（23 文件 sha256 对比） |
| a254bc8a0685479cafcebac24968c5699641439c7e15c49a1b4c869303dfb9b5 | .local-archive/r04_legacy01_diagnose.py | LEGACY-01 定向诊断（独立隔离根内复现沙箱用例，不改测试断言） |

## R04 evidence 实测记录（5 个）

| SHA256 | 文件 |
|---|---|
| d0b3f13a1bdfedb572f6bbfe770a0aebba1a7930139d0fd6042cf6fef469028c | doc/V0.1.5/evidence/m0_r04_precheck_verify.json（venv verify_isolation，PASS/0） |
| f802230c095cb5f88da8acd9b48da929d92aab0a528efe2c941dad59d42f5b99 | doc/V0.1.5/evidence/m0_r04_assets_before.json（运行前业务资产快照，23 文件） |
| b85c573116aff9f7475f9a6ddbe2db6e2c2af00ef03da0be11ca3f1370ec06af | doc/V0.1.5/evidence/m0_r04_assets_after.json（运行后业务资产快照，与 before 零差异） |
| f7fcfb4cd978031961e27d432a563d6dcd974822dc56472a49019d2a1020b338 | doc/V0.1.5/evidence/m0_r04_legacy01_diagnose.json（LEGACY-01 诊断证据） |
| 7c8302e09b0d2821e349c07cfcdba70f23197d1c95fc9bdaf99e01488da8282f | doc/V0.1.5/evidence/m0_r04_venv_full.log（venv 全量 pytest：1 failed/76 passed/1 skipped/1 warning，exit 1） |

## R04 管理与小结文档（5 个）

| SHA256 | 文件 |
|---|---|
| c5aa9bd46c9eaf3033584770cb4cc80f3950400838033c6f68d10a125fc8d5d1 | doc/V0.1.5/rounds/V015-M0_R04_开发小结.md |
| 27c89b2f82790adc866cbfb16c500f3de067ab27972bff77298ce1d79af741dc | doc/V0.1.5/02_任务台账.md（M0 行状态、A-M0-04/05 与 LEGACY-01 进展、R04 开发留痕；台账为活文档，后续轮次更新会使本哈希失配，属预期） |
| 032e48f251d6b2f48028d15c96271a7faee97cd06124e3a113a48174a3e8dc7a | doc/V0.1.5/evidence/README.md（补 R04 证据索引） |
| fb74abdd46300d64d43ff73ca387cf84468fd46a4f059ebc4c11b3fa765229b9 | doc/V0.1.5/rounds/README.md（补 R04 轮次索引） |
| （本清单自身哈希自指无意义） | doc/V0.1.5/evidence/m0_r04_delivery_hashes.md |

注：台账、两个 README 与本清单为活文档，审核/统筹后续更新会使上表哈希失配（与 R03 清单 25/28 相符同理），历史核验记录保留；上三节代码/脚本/证据共 13 项哈希为 R04 交付的稳定核验对象。

## 业务资产零改动佐证

- [m0_r04_assets_before.json](m0_r04_assets_before.json) 与 [m0_r04_assets_after.json](m0_r04_assets_after.json) 覆盖 app/materials.db、app/auth.db、config/ 16 文件、app/vector_cache/、doc/sample/ 共 23 项，前后逐项 sha256 零差异。
- venv 全量 pytest（97.83s）、四模块各自空库单跑、组合双序运行期间，外层快照始终包住所有子进程。
- 所有隔离根位于系统临时目录（`%TEMP%\listtrans_*`）；未下载模型（向量模型实测从本地 `model_offline/bge-small-zh-v1.5` 加载，dim=512），未调用真实付费 LLM（LLM_ENABLED=false），未操作线上账号。
