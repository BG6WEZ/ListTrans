# V015-M3 R01 交付哈希清单

- 生成时间：2026-11-14（Asia/Shanghai）；记录者：开发AI（接续）。
- 基线commit：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`（未提交、未push、无tag）。
- 范围：依据 [M2 R01统筹裁决](../rounds/V015-M2_R01_统筹裁决.md) 第35～59行仅执行M3 R01；开发自验完成，待独立审核及统筹裁决；M4～M5与后续版本未启动。
- 算法：SHA256（PowerShell `Get-FileHash -Algorithm SHA256`，小写十六进制）。当前值均为交付前实算，不用不含前序未提交工作的Git HEAD内容代替。
- 保留边界：其他未提交修改、删除、新增及业务资产全部保留；召回/排序权重、阈值、金标、模型与生产导出布局未触碰。
- 对照边界：[改动前工作区哈希](m3_r01_pre_baseline_hashes.md)（表内实为52行，原脚注"50"为计数笔误，未修改原文件）不是干净HEAD，也不等同于内容备份；新增测试文件无改动前哈希，不补造。

## R01实现与测试（7个交付文件）

| 文件 | R01前SHA256 | 交付时SHA256 | 范围 |
|---|---|---|---|
| app/matcher.py | 48780dfe4bd799535472b92116139da35a3ac01a87a5ab6803100390e5f1e796 | fe08ca6a07a3431f75b740837edd573a8514a395da53619652b90b04be92a946 | 否定/unknown重导出与提升泵/防爆三态、专业别名、JSON真实品牌、单位维度/倍率、DN/外径分字段与`_dn_field_to_float`、304/316L枚举、`blocked`/`max_score`不可逆上限及后置路径收口 |
| app/binding_decisions.py | 8b5bc83db8cd0ae90dc565c28f7ccd6c47235a04cd4688f86f6f1006f23c49db | 3288cccd00c022b6094570621baa6d23eb9fc73d4c36e3e795879b17a9b18f60 | `evaluate_hard_conflicts` 与 matcher 纯函数（专业/品牌/单位/DN外径/材质/防爆/提升泵）七道门四链路同源 |
| app/server.py | a621a00cd31bfb4cff30003d33dcb1a152b548634b7bde0c7afd9386abcca1b3 | abcc88fe7ae289df76e99086031ca4f0919bccb7f90fc2fd47e027d7637a755a | 第二遍系列加分不复活blocked且受`max_score`收口；人工确认链路补齐`parsed_params`/`description` |
| app/query_preprocessor.py | 8ec776bf186851248fad6e7468edab85d3870b0fde2318f77f2eb53974ffda2e | d0753c3a26da448060f9c2044c0fa4e7c0be250e2ca610943b6b57360fcffe25 | 提升泵/防爆否定前缀优先三态；DN与De/φ/直径拆分，DN只写管径字段不写外径 |
| app/domain_extractors.py | 08e66817d065293afe862a96560a35337737aa8a06ffb433ba3b52c6e1965e76 | 27d5f63bc78bc6771e2e56ac885d1cf4916eadbfae32a67f664b5f83237eb080 | `NEGATION_PREFIXES` / `detect_three_state_flag` 三态否定单一事实源 |
| tests/test_m3_hard_constraints.py | 新增，无 | ee927e1c94a29d807266459b1f6b0bbe8eaeac9381aa1b656e7ea9ee15157139 | 52项M3纯函数/管线正反例（含`test_dn_string_form_from_real_extractor`回归） |
| tests/test_m3_hard_constraints_http.py | 新增，无 | bef0487653fd6966760a5dea36df999e5567d08bbf8bd07908691696adbeae38 | 18项真实HTTP匹配/规则接受/人工确认409/导出422与200回归 |

## 未改动的关键基线/工具（22个）

| 文件 | SHA256 | 状态/用途 |
|---|---|---|
| app/db_manager.py | 47ed0b50957b8fdcf2d9d58a0ad377447bd05f2e81949c26b3202e940d3dec2c | 与R01前一致 |
| app/report_generator.py | 54ae14d36d894bfc4bcddb428728ac445da3f5158a64acfa6f0746a20d416e07 | 与R01前一致 |
| app/flywheel_sandbox.py | c4bc5e0ff9b6875208ead853ad1cf291717be128ab719fdfb13a4278057ca0d4 | 未改M1门禁 |
| app/llm_rule_engine.py | f223af4e06c819ca66f3b1342df9187d9b81ac0e005f55d6c30ad476f6e2645f | 与R01前一致 |
| app/session_manager.py | 2a8f7269101127739b395a89749aacdebfaea7de223063285c01217cc3630842 | 与R01前一致 |
| web/app.js | 4a8eb6204c5e3f27cf7c81081e93dfbd71f3040ba1dea0dfdc86720e3caadc09 | 与R01前一致 |
| tests/conftest.py | 207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033 | 隔离环境与依赖护栏未改 |
| tests/verify_isolation.py | 090ba89a25813f42910bd245b1bd2cd05bf580ffdc888ce3fe19ec523ad1eb62 | 隔离校验未改 |
| tests/test_m0_isolation.py | 92c97d4e7b7d6b68490986feeb79a32e317b7211914258d942d638130acafa3a | M0隔离回归未改 |
| tests/test_m0_guardrail_regression.py | ece380d56a8899b0c1f9f735f7ed28823470036d7ac32fbe00b784877e55839c | M0护栏回归未改 |
| tests/test_query_contract.py | b080c7bf69f376ee46482c8466b1e75eab44419c95d86fd86e5c10ad691a9704 | M1契约回归未改 |
| tests/test_auto_binding.py | 0b32c687486edbe85bb0383c2435968526fa4db9fc594d97b60c53d611c8629e | 真实导出回归未改 |
| tests/test_binding_decisions.py | adb13a41e2a9cd0ed152164e86eb3e8debb43567299b10880a2ac5dda6f05878 | M2决策回归未改 |
| tests/test_flywheel_sandbox.py | 8b56971b34c26d0a83d0f9da07419ad99e0699c0e1e0048b67902c65ded87901 | M1沙箱边界回归未改 |
| tests/eval_golden_set.py | 8b22c3b950c4accac9c57f9d05c8eeb02eb035aaf75d4bf3171c0751acce6584 | M1评测实现未改 |
| tests/fixtures/golden_set_v1.json | c8909a21b34fc12fd45ee96d625dec5798f670cdd725988beb99b2f2f216eae5 | 固定金标未改 |
| tests/fixtures/sandbox_materials_v1.json | f4fb0d678d38e717d41f2140df1327edeb432f99c03e1f53d6b84a22abf036f4 | 35行合成fixture未改 |
| config/auto_binding_rules.json | a5338d955b09046ec0b16f3a9625b7955c763aae07dc722e474e6078745f932f | 生产规则配置未改 |
| config/model_config.json | a76a24f33a6670d43bb3a72ce2f94735b019f7ae0963bd06c062a549f8064940 | 嵌入模型配置未改（`bge-small-zh-v1.5`） |
| config/llm_config.json | 35461ef62f87ff1736f3beafa0acc649c9b56e3951bf735826f044f4f2439feb | LLM配置未改（测试全程`LLM_ENABLED=false`） |
| .local-archive/business_snapshot.py | 12b2133dd0bb1dd5b30c72a5702f24f5a4d4565cde886a5f9d552df3e28393a7 | 外层业务快照工具未改 |
| tests/fixtures/golden_last_run.json 与 data/golden_last_run.json | **路径均不存在，无内容哈希** | 最终复核均为False |

## 实测证据与诊断工具（`doc/V0.1.5/evidence/m3_r01_*`，29个）

### 接续会话独立复跑证据（含对照与诊断，20个）

| 文件 | SHA256 | 真实结果/用途 |
|---|---|---|
| m3_r01_rerun.ps1 | 63b4fddca78385ac8d8ed72ac37f9cd94a4ff016316a277d3fc5fae0b966c5d7 | 固定venv顺序复跑脚本（16:18:31～16:22:29） |
| m3_r01_rerun_m3_cases.log | 0f86b93a81e0481ec7fa6b2af275820121947829acdb9fb0bf196ed4833a397a | M3用例 70 passed/0 failed/5 warnings/11.05s/EXIT=0 |
| m3_r01_rerun_required_regressions.log | b996d1d945b049ed59d0ede3c5d8b20079c406b8fd9063966846ef308c267fa2 | M0～M2组合 58 passed/0 failed/16 warnings/15.74s/EXIT=0 |
| m3_r01_compare_boundaries.log | 32b869e34973b25513424894ad08fe3beb0de74e8f67686d1a19a392535662b2 | 沙箱边界 30 passed/5 deselected/3 warnings/8.48s/EXIT=0 |
| m3_r01_capture_evidence.py | 5cc397a4b1e78686dc0bce1ec1995b16c72c84f4b1ea94c69970906f58613e35 | 真实双子进程三规则留证脚本（仅将m2脚本输出名改m3） |
| m3_r01_gate_reports.json | 2bd151ce6d7762de064b9f397a782d5f29f28faab8833aefff87b0d16373773a | 真实三规则：harmful false（GT-0313），beneficial/neutral true，snapshot errors=[] |
| m3_r01_gate_reports_capture.log | aaf7aee0fe3e13f9a06734e82b1b582670813b8b1a5319c67d2e22bf31e5353b | 三规则摘要与56.949s/EXIT=0 |
| m3_r01_rerun_full_pytest.log | c3ba27dba052fbcb3ffb334d729a736b51a28e5bcff2c2e6aaf928d2df5333aa | 固定venv全量 216 passed/0 failed/0 skipped/0 errors/24 warnings/113.51s/EXIT=0 |
| m3_r01_rerun_venv_verify_before.json | bd1c0b32dbc1da0ef809dd59563cf272927e7fd125201dbc95f9a5a01bfb11ee | 复跑前verify PASS/0，business_changes=[] |
| m3_r01_rerun_venv_verify_before.log | a9fb3ca514e89b981be74f93231bbe4cded3fce5f928347350fb72f94593c45f | 复跑前verify摘要/EXIT=0 |
| m3_r01_rerun_venv_verify_after.json | 1390c5b2710cf40ebb5b1424e803ff01d7a63d41a5216a207dba9401299de8cb | 复跑后verify PASS/0，pending/hard_errors/business_changes均空 |
| m3_r01_rerun_venv_verify_after.log | 0612f1ce833b48beecad1ee5eca421dabd3e3c4f4ea330d5a4573e922aeae8ac | 复跑后verify摘要/EXIT=0 |
| m3_r01_rerun_outer_snapshot_before.json | 5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | 复跑前外层业务快照 |
| m3_r01_rerun_outer_snapshot_before.log | 56fa89592e3b05c769eba0e157547d97c52ad9bfec2f02a13fa20b69c03a2bc0 | 复跑前外层快照日志/EXIT=0 |
| m3_r01_rerun_outer_snapshot_after.json | 5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | 复跑后外层业务快照，与before字节一致 |
| m3_r01_rerun_outer_snapshot_after.log | d1fcf86c843f8d23dc65c4bfe679fb716f8fd5097d0ea2d6180117fd3cf23d3d | 复跑后外层快照日志/EXIT=0 |
| m3_r01_pre_post_compare.py | ce0d8e7b9bae59250a8834a61952c9618d43559961c130b9fbe9da21649dfb63 | M2 vs M3 三规则报告只读比对脚本 |
| m3_r01_pre_post_compare.json | d56f5bc36ab74e92a80ab96f097430b04cad871479a8545c6c8243bdbdd2d21c | 改动前后对照结果（6例分数变化，目标仍均top1） |
| m3_r01_score_shift_diag.py | 07b76c47f6f1e35472126258131b98f13d70d79896d5ff4722ba88af60864bb6 | 隔离根下5例分数变化诊断脚本 |
| m3_r01_score_shift_diag.log | edc665b7f3d20e1bf1881c15c58dad627746d2215bda6b47cd8fe57d11066d1b | 诊断输出（DIAG_EXIT=0；诊断前后外层快照仍`5768bdca…`） |

### 前一开发会话原始留痕（保留不覆写，9个）

| 文件 | SHA256 | 说明 |
|---|---|---|
| m3_r01_pre_baseline_hashes.md | 9547fd24f6d4578ea5c9b1122b4777bc868d664dd8398b849c711c226f3e2888 | M3代码改动前工作区哈希（52行），非测试通过证据 |
| m3_r01_outer_snapshot_before.json | 5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | 前会话外层快照before |
| m3_r01_outer_snapshot_before.log | ee9f76e8e54451d409529a52be5982d43431678ccc785b235c9e9a4dfd5a6ae2 | 前会话before采集日志 |
| m3_r01_outer_snapshot_after.json | 5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | 前会话外层快照after，与before同哈希 |
| m3_r01_outer_snapshot_after.log | 4f4024aee42cf70a4cd6e72c6f7f80eea6e4d5082e10e8c19a041e33c41a3305 | 前会话after采集日志 |
| m3_r01_required_regressions.log | 4cdd019c03485cbaf76f7b0f1c9d15c203c0f86e5f3f7e39127584f521029aea | 前会话58项组合回归日志（16.77s） |
| m3_r01_full_pytest.log | b4fefe940fdad5a7330275bba2c8288e5c122db8c0e110642be1d5352ea45734 | 前会话216项全量日志（103.59s/EXIT=0） |
| m3_r01_venv_verify_after.json | 4f1a79af3cb4cfd28b8963741c7ba368d3833e859a2c6142a81977068ca7b5c0 | 前会话verify PASS/0 |
| m3_r01_venv_verify_after.log | b0cc671a209eb2333c69294d33695d3aac03c40e1342a69bb210f4edb4835b5c | 前会话verify摘要 |

## 交付文档（5个，活文档）

| 文件 | 交付时SHA256 |
|---|---|
| doc/V0.1.5/rounds/V015-M3_R01_开发小结.md | 45e35102409f7030c2fdeb17b6121cff853cd7dea7260e1636a399a264f91a16 |
| doc/V0.1.5/rounds/V015-M3_R01_开发交接说明.md | c28bd7aa75b188788b8f841ec0a0716d042735b6b1d48d9743167798a98b53c2 |
| doc/V0.1.5/02_任务台账.md | 939339218f5eeefa0e9aca48d0c8408efa724b8ca6d08c70fa0e4a69a4b9788d |
| doc/V0.1.5/rounds/README.md | ec3b02909a4ab4d4425f7a48d8d007510927b2787d292abfd13dd5e73eff444d |
| doc/V0.1.5/evidence/README.md | c0c719b71cebe5087000f93153bd04cd571b8a6a93df07c77d3921ba7ec56be0 |

本清单自身不做自引用哈希；台账、两个README及开发小结为活文档，后续审核/统筹维护会使其交付时哈希失配，属预期。7个代码/测试文件、未改动基线及29个实测证据文件为稳定核验对象，审核独立复跑产生的新文件应另列，不覆写上述留痕。

## 业务资产与运行边界

- 全部外层业务快照均维持 `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`，末次verify确认 `business_hash_unchanged=true`、`business_changes=[]`。
- 全量216项24个warning实例：vector方法更名1、Pydantic `.dict()`弃用19（`server.py:2729` 15次、`server.py:2734` 4次）、pandas两处`fillna`各2。未屏蔽或升级依赖，仍归COMPAT-M5-01。
- 真实三规则使用隔离临时物料库、35行合成fixture、只读固定金标及本地离线 `bge-small-zh-v1.5`；`LLM_ENABLED=false`，未下载模型或调用付费LLM。
- 本清单只证明M3 R01开发交付内容，不代表M3审核通过、整包放行或版本终审通过；未提交、push、打tag、发布、操作线上账号或迁移真实数据；未启动M4～M5。
