# V015-M2 R01 交付哈希清单

- 生成时间：2026-11-14（Asia/Shanghai）；记录者：开发AI。
- 基线commit：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`（未提交、未push）。
- 范围：依据 [M1 R02统筹裁决](../rounds/V015-M1_R02_统筹裁决.md) 仅执行M2 R01；开发自验完成，待独立审核及统筹裁决。
- 算法：SHA256（PowerShell `Get-FileHash -Algorithm SHA256`，小写十六进制）。当前值均为交付时实测，不用不含前序未提交工作的Git HEAD内容代替。
- 保留边界：其他未提交修改、删除、新增及业务资产全部保留；M1门禁、matcher、金标、生产导出布局、M3～M5与后续版本未触碰。
- 对照边界：[改动前工作区哈希](m2_r01_pre_baseline_hashes.md)不是干净HEAD，也不等同于内容备份；新增文件无改动前哈希，不补造。

## R01实现、前端与测试（6个）

| 文件 | R01前SHA256 | 交付时SHA256 | 范围 |
|---|---|---|---|
| app/binding_decisions.py | 新增，无 | 8b5bc83db8cd0ae90dc565c28f7ccd6c47235a04cd4688f86f6f1006f23c49db | 四态、服务端决策表、双指纹、新鲜编码与硬冲突复核 |
| app/server.py | 9ce124244f724e7f9fb46173afab8572c3084720791adc0bcf1a5cd3df34e690 | a621a00cd31bfb4cff30003d33dcb1a152b548634b7bde0c7afd9386abcca1b3 | 规则/匹配/确认/拒绝/恢复与导出fail closed |
| app/session_manager.py | 3a1c31b3059606c44a9495e49d1af607acc8184643ede34f6944c6a76b9c445c | 2a8f7269101127739b395a89749aacdebfaea7de223063285c01217cc3630842 | 决策表幂等初始化与重置级联清理 |
| web/app.js | c8cafc92684ffd71aca135e7a263472a75acdcfcc405ce47807bf72a6a6c0041 | 4a8eb6204c5e3f27cf7c81081e93dfbd71f3040ba1dea0dfdc86720e3caadc09 | 统一显示状态、编辑/重算失效与恢复保守降级；无独立JS/浏览器实测 |
| tests/test_binding_decisions.py | 新增，无 | adb13a41e2a9cd0ed152164e86eb3e8debb43567299b10880a2ac5dda6f05878 | 26项决策/确认/导出/真实HTTP与XLSX/草稿回归 |
| tests/test_auto_binding.py | ab4749f2b75336abc75e57ec42ae6091685e1454a60cc0923656f11f31e049c5 | 0b32c687486edbe85bb0383c2435968526fa4db9fc594d97b60c53d611c8629e | 名称-only规则只推荐，保留真实一对多导出难例 |

## 未改动的关键基线/工具（17个）

| 文件 | SHA256 | 状态/用途 |
|---|---|---|
| app/db_manager.py | 47ed0b50957b8fdcf2d9d58a0ad377447bd05f2e81949c26b3202e940d3dec2c | 与R01前一致 |
| app/report_generator.py | 54ae14d36d894bfc4bcddb428728ac445da3f5158a64acfa6f0746a20d416e07 | 与R01前一致，未改真实XLSX布局 |
| app/matcher.py | 48780dfe4bd799535472b92116139da35a3ac01a87a5ab6803100390e5f1e796 | 未改权重/阈值/召回/排序 |
| app/flywheel_sandbox.py | c4bc5e0ff9b6875208ead853ad1cf291717be128ab719fdfb13a4278057ca0d4 | 未改M1门禁 |
| app/llm_rule_engine.py | f223af4e06c819ca66f3b1342df9187d9b81ac0e005f55d6c30ad476f6e2645f | 与R01前一致 |
| web/index.html | f9883bd95c107586c1aec9542c071c066bbbcdf69499895ddb65081b5f08d435 | 未改 |
| web/index.css | 8b924cacbd07d82e43906ec9e3958908f7ab6fa1f84205a6c40faf1340cd0417 | 未改 |
| tests/conftest.py | 207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033 | 隔离环境与依赖护栏未改 |
| tests/test_query_contract.py | b080c7bf69f376ee46482c8466b1e75eab44419c95d86fd86e5c10ad691a9704 | M1契约回归未改 |
| tests/eval_golden_set.py | 8b22c3b950c4accac9c57f9d05c8eeb02eb035aaf75d4bf3171c0751acce6584 | M1评测实现未改 |
| tests/README.md | 82b82d27b33667291e6d9d766c8635fdfdd7170d0a4a3e7332064d9ad320723a | 未改 |
| config/auto_binding_rules.json | a5338d955b09046ec0b16f3a9625b7955c763aae07dc722e474e6078745f932f | 生产规则配置未改 |
| tests/fixtures/golden_set_v1.json | c8909a21b34fc12fd45ee96d625dec5798f670cdd725988beb99b2f2f216eae5 | 固定金标未改 |
| tests/fixtures/sandbox_materials_v1.json | f4fb0d678d38e717d41f2140df1327edeb432f99c03e1f53d6b84a22abf036f4 | 35行合成fixture未改 |
| .local-archive/business_snapshot.py | 12b2133dd0bb1dd5b30c72a5702f24f5a4d4565cde886a5f9d552df3e28393a7 | 外层业务快照工具未改 |
| C:/Users/tlyer/AppData/Local/Temp/m2_r01_capture_evidence.py | 3d6abaa33ca52096909d18d0ee5705ff389b37593f4edb20881f0c1c5d2b1473 | 仓外一次性真实三规则留证脚本 |
| tests/fixtures/golden_last_run.json | **路径不存在，无内容哈希** | 最终复核False；隔离GOLDEN_DETAIL_FILE亦为False |

## 最终通过证据与业务资产（12个）

| 文件 | SHA256 | 真实结果 |
|---|---|---|
| m2_r01_pre_baseline_hashes.md | 5ab90c038406ecda29eeed66954a342b4623e0b3a1224fb6dbcf7f102d272fc6 | M2代码改动前工作区哈希，不是测试通过证据 |
| m2_r01_required_regressions.log | f74097ad42cbeddfe83a2559ee4d2d6e5d4093efbced5a4101c487134b059c0c | 58 passed/0 failed/0 skipped/16 warnings/14.26s/EXIT=0 |
| m2_r01_compare_boundaries.log | b1086e1b58e537d130e360b9c07c35ec91036e14a9e6bd2b7fa4fa5b6f13310e | 30 passed/0 failed/5 deselected/3 warnings/8.25s/EXIT=0 |
| m2_r01_gate_reports.json | 68f927621911aaaaa62f990efd74c7663543b8364b1579c0482efc8da6483d5a | 真实三规则；harmful false、beneficial/neutral true；两个金标明细均不存在 |
| m2_r01_gate_reports_capture.log | 393125af858a0fc8af700858e2ef4f9c4b42ffd92a6b7b4d2ba5e20e80899d91 | 三规则摘要与54.411s；后台命令真实EXIT=0 |
| m2_r01_full_pytest.log | 20275b683628c24edfc939518decfa83be466ebd05804da323e13a11fff02aed | 固定venv全量146 passed/0 failed/0 skipped/0 errors/20 warnings/115.80s/EXIT=0 |
| m2_r01_venv_verify_before.json | ee8fc512b811b1dff0dd562089bfe3b9ef2b34d9ba387ea1ab2b64eb060cc30a | 最终验证前verify PASS/0，业务无变化 |
| m2_r01_venv_verify_after.json | 54b1d0d9469c29b3caf73df4ac08f0d59a810a1b44886ca4b8eed6856faa6a3f | 末次verify PASS/0；pending/hard errors/business changes均为空 |
| m2_r01_venv_verify_after.log | e7cf875b4928a45d8b775354e8b5f25f24f663971666809b7337ce6fe388ffa2 | 末次verify摘要；包装命令VERIFY_EXIT=0 |
| m2_r01_outer_snapshot_before.json | 5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | 包住组合/边界/三规则/全量的外层业务快照before |
| m2_r01_outer_snapshot_after.json | 5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | after与before字节一致，逐项差异为空 |
| m2_r01_outer_snapshot_after.log | a8d444c183953352648b150e0aa68aaa7a92d9477fcbfc2b28ab7b5a509ecf9b | after生成日志；包装命令SNAPSHOT_EXIT=0 |

## 交付文档（4个，活文档）

| 文件 | 交付时SHA256 |
|---|---|
| doc/V0.1.5/rounds/V015-M2_R01_开发小结.md | aa831226c441fef843ce8e247e7f10a359916f09d0f869387b81c049e5e0219e |
| doc/V0.1.5/02_任务台账.md | 1bb457649358d694b0720891e8e5a57b88a298c95294295e57381401a6348c04 |
| doc/V0.1.5/rounds/README.md | e59a9c48aff00e4187b1e4d5dca2309511c27642c772bd7add28e8ff2b5cfee3 |
| doc/V0.1.5/evidence/README.md | b695432fa8e034f50b766263f4a130e8270f9ad90a95796113da60cf8ee6d0ce |

本清单自身不做自引用哈希；台账、两个README及开发小结为活文档，后续审核/统筹维护会使其交付时哈希失配，属预期。代码/测试、工具及实测证据为稳定核验对象，审核独立重跑产生的新文件应另列，不覆写上述原始留痕。

## 业务资产与运行边界

- before/after快照覆盖8个受监视文件路径及27个现存树文件，原始字节SHA256完全一致；末次verify另确认`business_hash_unchanged=true`。
- 最终全量20个warning实例：vector方法更名1、Pydantic `.dict()`弃用15、pandas两处`fillna`各2。未屏蔽或升级依赖，既有问题仍归M5。
- 真实三规则使用隔离临时物料库、35行合成fixture、只读固定金标及本地离线模型；`LLM_ENABLED=false`，未下载模型或调用付费LLM。
- 无独立JS语法/浏览器运行验证；不以IDE无诊断代替。未提交、push、发布、操作线上账号或迁移真实数据；未启动M3～M5。本清单只证明开发交付内容，不代表M2审核通过、整包放行或版本终审通过。
