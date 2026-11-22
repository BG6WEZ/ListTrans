# V015-M1 R01 交付哈希清单

- 生成时间：2026-11-13（Asia/Shanghai）
- 基线 commit：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`（未提交、未 push；M1 R01 为该基线上的未提交改动）
- 算法：SHA256（PowerShell `Get-FileHash -Algorithm SHA256`，大写十六进制，落盘前重算复核一致）
- 说明：本清单覆盖 M1 R01 按 [R04 统筹裁决](../rounds/V015-M0_R04_统筹裁决.md) 锁定范围实际改动的 9 个代码/测试文件、2 个新增测试文件与 fixture。M0 各轮已交付文件的哈希沿用各自清单，不重复列示；仓库中其他未提交差异（目录治理、scripts/、其他版本 doc 等）为用户/前序工作，本包未触碰。改动前对照见 [m1_r01_pre_baseline_hashes.md](m1_r01_pre_baseline_hashes.md)。

## M1 代码与测试改动（9 个改动 + 3 个新增）

| SHA256 | 文件 | 对应范围 |
|---|---|---|
| 8ec776bf186851248fad6e7468edab85d3870b0fde2318f77f2eb53974ffda2e | app/query_preprocessor.py | F01/F13：`extract_query_params` 补顶层 name/spec 键；新增统一 `prepare_query()`（规范 name/specification/clean_text、unit/brand/professional/category 回退链、preserve_original 保留原文/原始品类品牌）；discipline 修复 |
| 9ce124244f724e7f9fb46173afab8572c3084720791adc0bcf1a5cd3df34e690 | app/server.py | 四入口（批量 batch_match_rows/单条 api_match_single/重算 api_rematch/黄金集评测）统一接入 `prepare_query`；`MatchedItemModel` 增补 `original_category`/`original_brand`；parsed_spec 合并保持兼容（setdefault） |
| 48780dfe4bd799535472b92116139da35a3ac01a87a5ab6803100390e5f1e796 | app/matcher.py | 三处 fallback 第三参改 professional 优先（L357/L1719/L1928） |
| c8cafc92684ffd71aca135e7a263472a75acdcfcc405ce47807bf72a6a6c0041 | web/app.js | 前端 payload 补 `original_category`/`original_brand`（F19 导出贯通） |
| 6e1d27caf8ea8f24d826980a93b461530c9112af9089723833e0297e0f4dd4ae | tests/eval_golden_set.py | evaluate() 改用 `prepare_query(preserve_original=False)`（黄金集入口契约对齐） |
| b88e666045078f03b51e34bdf5b0e6f60b6277b8738fac8ad72045c9337d8db9 | tests/test_flywheel_sandbox.py | LEGACY-01：新增 `seed_isolated_materials` fixture 播种 + `test_sandbox_fixture_rank_observable`；harmful 原断言原样保留（失败回交统筹）；XLSX 表头构造与 eval_golden_set 导入 stdout 陷阱修复 |
| ab4749f2b75336abc75e57ec42ae6091685e1454a60cc0923656f11f31e049c5 | tests/test_auto_binding.py | ASSET-M1-01：`test_report_generator_multirow` 模板缺失改生成器兜底（不再 skip），补 F19 字段贯通与历史价格继承断言 |
| b080c7bf69f376ee46482c8466b1e75eab44419c95d86fd86e5c10ad691a9704 | tests/test_query_contract.py（新增） | m1-6：11 项契约回归（契约超集/原文保留/噪声隔离/未知值回退/三入口一致/parsed_spec 兼容/黄金集对齐） |
| a66f32f3f3fd432d58ffe310a1612d0658021f122613c875324925250675eb93 | tests/fixtures/gen_standard_template.py（新增） | ASSET-M1-01：可复现标准模板生成器（15 列表头+样式行+历史价格行） |
| f4fb0d678d38e717d41f2140df1327edeb432f99c03e1f53d6b84a22abf036f4 | tests/fixtures/sandbox_materials_v1.json（新增） | LEGACY-01：34 金标目标物料+有害电缆+中性对照合成脱敏 fixture |
| da591eb12b6a7393eca5e9e68acd915c5b562a56b1202862d7b629457ffe6d09 | tests/README.md | 索引更新（V015-M1 节：契约回归 11 项、LEGACY-01 播种 fixture、模板生成器、GOLDEN_DETAIL_FILE 重定向警示） |

## M1 本机一次性工具（4 个，.local-archive/，AGENTS.md 约定目录）

| SHA256 | 文件 | 用途 |
|---|---|---|
| 12b2133dd0bb1dd5b30c72a5702f24f5a4d4565cde886a5f9d552df3e28393a7 | .local-archive/business_snapshot.py | 外层业务资产快照（WATCH_PATHS/WATCH_TREES 与 verify_isolation.py 一致） |
| a7e3244ba40010bdb3d84a4e7b8d8ebcfed7b516cc8f78bd122d67376a156d04 | .local-archive/diag_score_breakdown.py | GT-0313 基线/候选分步打分诊断（模拟沙箱子进程环境） |
| 106b7954c92f365419f62d2c138f9e79c0e0d1cac29e0abffcf3c2a7c3352935 | .local-archive/m1_r01_make_legacy01_evidence.py | LEGACY-01 证据 JSON 合成（脱敏） |
| 3580dec6771ef6133b7e478ca08f285935c1bc148bab23982fab492679769e92 | .local-archive/m1_r01_glr_polluted_backup.json | 留证：本会话诊断脚本误写 `tests/fixtures/golden_last_run.json` 的产物（已恢复原路径为不存在，见开发小结"污染与恢复"节） |

## M1 evidence 实测记录（9 个）

| SHA256 | 文件 |
|---|---|
| 907cf521083a6bf1e71be5a6e3dff252ae28d6aa30e4a840d7c25ce0ff29df9 | m1_r01_pre_baseline_hashes.md（M1 动工前 9 文件哈希基线） |
| 5768bdabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | m1_r01_outer_snapshot_before.json（全量验证前外层业务快照） |
| 5768bdabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | m1_r01_outer_snapshot_after.json（全部验证后，与 before 字节一致、零变化） |
| 0c68ff9f097c9de17cda615fc4593d38b312f013b84600eabef58e681a7c94f3 | m1_r01_venv_verify.json（venv verify_isolation，PASS/0，6/6 模块路径 ok） |
| 464bf67db4a851b4a63295ecd6473b6ae2d069c9b752f71c2cffdb19fcae373 | m1_r01_verify_after.json（全量复验后末次 verify，PASS/0） |
| 06a2817fbaccd0dd88fe44907ea64a67c8e419a88244e4b2068bde94e22be9bc | m1_r01_full_pytest.log（venv 全量：1 failed/89 passed/3 warnings，exit 1，117.44s；唯一失败 LEGACY-01 harmful 保留项） |
| 7d1755dfd929d1d643d3099417463e7c508b5a896f3044927ce0a3c531bd5756 | m1_r01_guardrail_smoke.log（13 项负向回归 + 5 项隔离冒烟，18 passed） |
| ba256e97228ba16c58ccfcc40da654d1f0420d2e6a3744c406bb5fd56cfe795f | m1_r01_diag_score_breakdown.log（GT-0313 基线 88.7 vs 候选 50.7/43.1 分步合分） |
| f9126c649930c9dfec8391cad8e98d3ffbddf929bac74ffb15321ab989d018ae | m1_r01_legacy01_evidence.json（LEGACY-01 收口证据汇总：fixture/模型/门禁报告/rank 可观察/分步诊断） |

## 业务资产零改动佐证

- 外层快照 before/after 原始字节 SHA256 完全一致（上表两行同为 `5768BDCA…`），覆盖 materials.db/auth.db（含 -wal/-shm）、binding_feedback.json、feedback_samples.json、config/、app/vector_cache/、app/uploads/、logs/、data/ 全部 WATCH 清单。
- venv 全量 pytest（117.44s）、guardrail 18 项、verify 两次均被外层快照包住；隔离根位于系统临时目录 `%TEMP%\listtrans_*`。
- 向量模型从本地 `model_offline/bge-small-zh-v1.5`（dim=512）加载，未下载模型；LLM_ENABLED=false，未调用真实付费 LLM，未操作线上账号，无生产数据迁移。

## 备注

- 台账（02_任务台账.md）、两个 README（rounds/evidence）与本清单为活文档，审核/统筹后续更新会使相应哈希失配，属预期；历史核验记录保留。
- `tests/fixtures/golden_last_run.json`：本会话诊断脚本曾在 conftest 外触发沙箱子进程覆写该路径；已按 2026-11-12 目录整理后的基线恢复"路径不存在"状态，误写产物 SHA256 `3580DEC6…` 留证 `.local-archive/m1_r01_glr_polluted_backup.json`（内容为 sandbox_materials_v1 合成数据评测明细，无敏感信息）。恢复后全量复跑确认无再次生成（fixture 路径仍不存在）。
