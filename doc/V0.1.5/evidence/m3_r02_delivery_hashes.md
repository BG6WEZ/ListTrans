# V015-M3 R02 交付哈希清单

- 生成时间：2026-11-14（Asia/Shanghai）；记录者：开发AI。
- 基线 commit：R02 启动基线 `4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`；当前实测 HEAD `e964fb16848de9dece8e729e801b61c7d81dd341`（工作区含 M0～M3 未提交工作及用户外部 `9477b8d8` README 文档提交与 `e964fb16` 交接清理提交；本会话未执行任何 git 写操作、未 push、无 tag）。
- 范围：依据 [M3 R01统筹裁决](../rounds/V015-M3_R01_统筹裁决.md) 仅执行 `V015-M3 / R02` 对 `A-M3-01 ~ A-M3-04` 四项 P1 缺陷整改及回归；开发自验完成，待独立审核及统筹裁决；M4～M5 与后续版本保持锁定。
- 算法：SHA256（UTF-8 原始字节精确哈希，小写十六进制）。
- 保护边界：R01 交付副本保存在 `.local-archive/m3_r02_pre_backup/`（与 [m3_r02_pre_baseline_hashes.md](m3_r02_pre_baseline_hashes.md) 逐项对应）；历史未提交工作及业务资产全部保留；召回/排序权重、阈值、金标、模型与生产导出布局未触碰。

## R02 实现与测试（M3 交付 8 个代码/测试文件，其中 R02 实际修改 7 个）

| 文件 | R01前（M2基线）SHA256 | R01交付（R02前）SHA256 | R02交付SHA256 | R02改动范围 |
|---|---|---|---|---|
| `app/matcher.py` | `48780dfe4bd799535472b92116139da35a3ac01a87a5ab6803100390e5f1e796` | `fe08ca6a07a3431f75b740837edd573a8514a395da53619652b90b04be92a946` | `4dfc51660debc8d60c217600a1e08bdbff9f55bb63b83719b80bfb93ce234cdb` | 新增 `resolve_material_attrs` / `_extract_fallback_material_attrs`（缺失/坏 JSON 回退与 DN/De 隔离）；重导出并接入双向 `detect_pump_state`/`check_pump_conflict`；抽出 `evaluate_dynamic_exclusions` / `_dyn_scope_matches` / `_dyn_rule_matches_query` |
| `app/binding_decisions.py` | `8b5bc83db8cd0ae90dc565c28f7ccd6c47235a04cd4688f86f6f1006f23c49db` | `3288cccd00c022b6094570621baa6d23eb9fc73d4c36e3e795879b17a9b18f60` | `b012ef44cd871f67bdb7597a79a6ecb3347433c27faa916fe5498b628c7373a3` | 非1倍率 `allow_unit_conversion` / `qty_ratio` / `qty` / 目标单位一致性与 `saved_bindings` 漂移门；接入 `resolve_material_attrs`、双向 `check_pump_conflict`、`evaluate_dynamic_exclusions` 与 `extract_saved_bindings` |
| `app/server.py` | `a621a00cd31bfb4cff30003d33dcb1a152b548634b7bde0c7afd9386abcca1b3` | `abcc88fe7ae289df76e99086031ca4f0919bccb7f90fc2fd47e027d7637a755a` | `b9c5668e3278db9eb3dfc96363456bae2b60261ea353d3dbed25a5caf5ea82f9` | `_evaluate_rule_decision` 传 `allow_unit_conversion=False`；`_annotate_restored_items` 与 `_gate_export_row` 传入 `saved_bindings=extract_saved_bindings(record)` |
| `app/domain_extractors.py` | `08e66817d065293afe862a96560a35337737aa8a06ffb433ba3b52c6e1965e76` | `27d5f63bc78bc6771e2e56ac885d1cf4916eadbfae32a67f664b5f83237eb080` | `0620b5b46030a29972e2f98bc0db814d2f35fb0e80b6ef5e5bf3df63933bb20c` | 新增 `detect_pump_state` / `check_pump_conflict` 双向提升泵单一事实源；`ConfigDrivenExtractor` 不区分大小写优先识别具体不锈钢牌号 |
| `app/llm_rule_engine.py` | `f223af4e06c819ca66f3b1342df9187d9b81ac0e005f55d6c30ad476f6e2645f` | `f223af4e06c819ca66f3b1342df9187d9b81ac0e005f55d6c30ad476f6e2645f` | `6d2e18391516f0e5fb1a69303beb16ab28bfe955f27fabbef069fd2b2dca854b` | `get_active_rules()` 对未建表 `OperationalError` 兜底返 `[]` 并 `finally conn.close()`；`save_rule_draft()` 同步 `cls.clear_active_cache()` |
| `app/query_preprocessor.py` | `8ec776bf186851248fad6e7468edab85d3870b0fde2318f77f2eb53974ffda2e` | `d0753c3a26da448060f9c2044c0fa4e7c0be250e2ca610943b6b57360fcffe25` | `d0753c3a26da448060f9c2044c0fa4e7c0be250e2ca610943b6b57360fcffe25` | R02 未改（保留 R01 三态否定与 DN/De 拆分） |
| `tests/test_m3_hard_constraints.py` | 新增，无 | `ee927e1c94a29d807266459b1f6b0bbe8eaeac9381aa1b656e7ea9ee15157139` | `91b3ff0aafc6157ea97d2884986911e4d7b4ee1fa05838ce74da85ad32aad7dc` | 原 52 项保留 + 追加 13 项 R02 单元/轻量回归（共 65 项） |
| `tests/test_m3_hard_constraints_http.py` | 新增，无 | `bef0487653fd6966760a5dea36df999e5567d08bbf8bd07908691696adbeae38` | `f5057f0400be13924873c2c290101d729a65dff729a98bfdb25fd7d3ab8d149b` | 原 18 项保留 + 追加 4 项 R02 端到端 HTTP/XLSX 闭环回归（共 22 项） |

## 未改动的关键基线/工具（20 个，不含 R02 新改的 `llm_rule_engine.py`）

| 文件 | SHA256 | 状态/用途 |
|---|---|---|
| `app/db_manager.py` | `47ed0b50957b8fdcf2d9d58a0ad377447bd05f2e81949c26b3202e940d3dec2c` | 与 R01 一致 |
| `app/report_generator.py` | `54ae14d36d894bfc4bcddb428728ac445da3f5158a64acfa6f0746a20d416e07` | 与 R01 一致 |
| `app/flywheel_sandbox.py` | `c4bc5e0ff9b6875208ead853ad1cf291717be128ab719fdfb13a4278057ca0d4` | 未改 M1 门禁 |
| `app/session_manager.py` | `2a8f7269101127739b395a89749aacdebfaea7de223063285c01217cc3630842` | 与 R01 一致 |
| `web/app.js` | `4a8eb6204c5e3f27cf7c81081e93dfbd71f3040ba1dea0dfdc86720e3caadc09` | 与 R01 一致 |
| `tests/conftest.py` | `207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033` | 隔离环境与依赖护栏未改 |
| `tests/verify_isolation.py` | `090ba89a25813f42910bd245b1bd2cd05bf580ffdc888ce3fe19ec523ad1eb62` | 隔离校验未改 |
| `tests/test_m0_isolation.py` | `92c97d4e7b7d6b68490986feeb79a32e317b7211914258d942d638130acafa3a` | M0 隔离回归未改 |
| `tests/test_m0_guardrail_regression.py` | `ece380d56a8899b0c1f9f735f7ed28823470036d7ac32fbe00b784877e55839c` | M0 护栏回归未改 |
| `tests/test_query_contract.py` | `b080c7bf69f376ee46482c8466b1e75eab44419c95d86fd86e5c10ad691a9704` | M1 契约回归未改 |
| `tests/test_auto_binding.py` | `0b32c687486edbe85bb0383c2435968526fa4db9fc594d97b60c53d611c8629e` | 真实导出回归未改 |
| `tests/test_binding_decisions.py` | `adb13a41e2a9cd0ed152164e86eb3e8debb43567299b10880a2ac5dda6f05878` | M2 决策回归未改 |
| `tests/test_flywheel_sandbox.py` | `8b56971b34c26d0a83d0f9da07419ad99e0699c0e1e0048b67902c65ded87901` | M1 沙箱边界回归未改 |
| `tests/eval_golden_set.py` | `8b22c3b950c4accac9c57f9d05c8eeb02eb035aaf75d4bf3171c0751acce6584` | M1 评测实现未改 |
| `tests/fixtures/golden_set_v1.json` | `c8909a21b34fc12fd45ee96d625dec5798f670cdd725988beb99b2f2f216eae5` | 固定金标未改 |
| `tests/fixtures/sandbox_materials_v1.json` | `f4fb0d678d38e717d41f2140df1327edeb432f99c03e1f53d6b84a22abf036f4` | 35 行合成 fixture 未改 |
| `config/auto_binding_rules.json` | `a5338d955b09046ec0b16f3a9625b7955c763aae07dc722e474e6078745f932f` | 生产规则配置未改 |
| `config/model_config.json` | `a76a24f33a6670d43bb3a72ce2f94735b019f7ae0963bd06c062a549f8064940` | 嵌入模型配置未改（`bge-small-zh-v1.5`） |
| `config/llm_config.json` | `35461ef62f87ff1736f3beafa0acc649c9b56e3951bf735826f044f4f2439feb` | LLM 配置未改（测试全程 `LLM_ENABLED=false`） |
| `.local-archive/business_snapshot.py` | `12b2133dd0bb1dd5b30c72a5702f24f5a4d4565cde886a5f9d552df3e28393a7` | 外层业务快照工具未改 |
| `tests/fixtures/golden_last_run.json` 与 `data/golden_last_run.json` | **路径均不存在，无内容哈希** | 最终复核均为 `False` |

## 实测证据（`doc/V0.1.5/evidence/m3_r02_*`，18 个）

| 文件 | SHA256 | 真实结果/用途 |
|---|---|---|
| `m3_r02_pre_baseline_hashes.md` | `b992ea41e00b468aead00eadc830d1cf3cee2a01c6d3e88593bc8374e63343a2` | R02 改动前工作区基线与 `.local-archive/m3_r02_pre_backup/` 清单 |
| `m3_r02_outer_snapshot_before.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | R02 前外层业务快照 |
| `m3_r02_outer_snapshot_before.log` | `16ffc53fcb530d01bb01c05ed1341d9fdd032dbe55f1195b310e846a652e6c8c` | R02 前外层快照采集日志/EXIT=0 |
| `m3_r02_venv_verify_before.json` | `9d289aaee2a42478f7c5fa930ed0c2d625e9eafdb41e61120c10286008c9051f` | R02 前 `verify_isolation.py` PASS/0，无业务变化与硬错误 |
| `m3_r02_venv_verify_before.log` | `7d23a053e47305f9cd962ab4df9cc5a4c8e7c45eb0e5ba41314c67965d3d96dc` | R02 前 verify 摘要/EXIT=0 |
| `m3_r02_rerun.ps1` | `b94ede47862c24c329ffd2ea5cc5dd1d9fb81995ec9e39d421dd18f9ddb9e304` | 固定 `venv` 顺序全套复跑脚本 |
| `m3_r02_pytest_m3.txt` | `d7ffb6ddeb8000334f1ce5665eaec0cdebbdb1a740335feef018ee1695bc0feb` | M3 用例 **87 passed / 24 warnings / 11.38s (wall 12.831s) / EXIT=0** |
| `m3_r02_pytest_m0_m2_regression.txt` | `735b56ecd8816080747611dc5ee71452b5a68d1a4ab074ec7466a2606e4769c7` | M0～M2 组合 **58 passed / 16 warnings / 14.03s (wall 15.388s) / EXIT=0** |
| `m3_r02_pytest_sandbox_30.txt` | `822cdd98229d292e7b2f846178ac1820375a2c46a010f1d55a525b9e9d89358c` | 沙箱边界 **30 passed / 5 deselected / 3 warnings / 7.78s (wall 9.069s) / EXIT=0** |
| `m3_r02_capture_evidence.py` | `5f996cf98d6463749695213fbabc2b60c714f64a6ea130f4d1ad9e0f266e3ecc` | 真实双子进程三规则留证脚本 |
| `m3_r02_gate_reports.json` | `a721226727c6ad6b3a6cacd6a64c64f3caf888750b317177fdce1086cd2de78f` | 真实三规则：`harmful passed=false`（GT-0313），`beneficial/neutral passed=true`，`snapshot errors=[]` |
| `m3_r02_3rule_gate.txt` | `fd9ba1211bf8b932d00d265a2ea13f2210d5506320d909b3f726638956eb44b0` | 三规则摘要与 `50.894s (wall 58.251s) / EXIT=0` |
| `m3_r02_r01_gate_compare.json` | `770e0639b75298292f9812c6ea25e69f233e9169e738024ffafd8d7e9e1468ff` | R01 vs R02 三规则逐例对比：`score_comparison_diffs=[]` |
| `m3_r02_pytest_full.txt` | `960054f0ef59d54483ab96b0cf0639f6879a0f45f0b7d15f01d48c514eb3764e` | 固定 `venv` 全量 **233 passed / 0 failed / 0 skipped / 0 errors / 43 warnings / 103.34s (wall 104.859s) / EXIT=0** |
| `m3_r02_venv_verify_after.json` | `fd9554d33c16b2598670bbd6ccdc8d943a18614eefb9334ba7f15fe65a78a910` | 复跑后 `verify_isolation.py` PASS/0，无业务变化、无硬错误 |
| `m3_r02_venv_verify_after.log` | `3fe02a79f93afd16024a34f77527b08bfdeace048cabe79be93f08ec64c45be3` | 复跑后 verify 摘要/EXIT=0 |
| `m3_r02_outer_snapshot_after.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | 复跑后外层业务快照，与 before 字节完全一致 |
| `m3_r02_outer_snapshot_after.log` | `f45a814dbc56355d1f8e0fa49f163456c41b3fc602de32fca97d8b2f48408e5c` | 复跑后外层快照日志/EXIT=0 |

## 交付文档（4 个，活文档）

| 文件 | 交付时 SHA256 |
|---|---|
| `doc/V0.1.5/rounds/V015-M3_R02_开发小结.md` | `b330f40f8bdf316513c66b11c118a72aa065f9fd7b1a57ab7dd1a29d3caad4b0` |
| `doc/V0.1.5/02_任务台账.md` | `efd5ea580a60b2bc6f21fdc75aa125863ead605a23477f6aea3732cdf269a76c` |
| `doc/V0.1.5/rounds/README.md` | `6816dab4c37a7362539f3260600371bc63d175cc1e3b989ee19e4c3fa839fa22` |
| `doc/V0.1.5/evidence/README.md` | `1e3b102b2aea55baf958a1590079c9fd8ce683743d4d77df98f6b3e3bbad7868` |

本清单自身不做自引用哈希；台账、两个 `README.md` 及开发小结为活文档，后续审核/统筹维护会使其交付时哈希变化，属预期。上述 8 个代码/测试文件、20 个未改动基线文件及 18 个实测证据文件为稳定核验对象，审核独立复跑产生的新文件应另列，不覆写上述留痕。
