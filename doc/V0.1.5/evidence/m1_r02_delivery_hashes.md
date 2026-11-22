# V015-M1 R02 交付哈希清单

- 生成时间：2026-11-13（Asia/Shanghai）；记录者：开发 AI。
- 基线 commit：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`（未提交、未 push）。
- 范围：依据 [R01统筹裁决](../rounds/V015-M1_R01_统筹裁决.md) 仅完成 LEGACY-01 同包 R02 整改；实现与测试自验完成，待独立审核及统筹裁决。
- 算法：SHA256（PowerShell `Get-FileHash -Algorithm SHA256`，小写十六进制）。下列值为交付时实测，不用 Git HEAD 中不含前序未提交工作的文件代替当前文件。
- 保留边界：仓库中其他未提交修改、删除、新增及业务资产全部保留；matcher、金标、原断言、M2～M5 与后续版本未触碰。
- 对照边界：`test_flywheel_end2end.py` 的 R02 前哈希未在既有资料中落盘，故不推测、不补造；仅其当前哈希可核验。另三个实现/测试文件的 R02 前哈希取自 R01 已落盘交付记录，与 R02 动工前交接一致。

## R02 实现、测试与说明（5 个）

| 文件 | R02前 SHA256 | 交付时 SHA256 | 范围 |
|---|---|---|---|
| app/flywheel_sandbox.py | 2da38800fe4cc9160ecc714f2f47c47e7ed220d7afb4be7d0ef9df5bbc2ad930 | c4bc5e0ff9b6875208ead853ad1cf291717be128ab719fdfb13a4278057ca0d4 | 保留rank/净收益门禁；逐例T/margin、完整性、运行快照与子进程fail closed |
| tests/eval_golden_set.py | 6e1d27caf8ea8f24d826980a93b461530c9112af9089723833e0297e0f4dd4ae | 8b22c3b950c4accac9c57f9d05c8eeb02eb035aaf75d4bf3171c0751acce6584 | 原始Top10候选分、实际运行快照、显式金标/明细路径 |
| tests/test_flywheel_sandbox.py | b88e666045078f03b51e34bdf5b0e6f60b6277b8738fac8ad72045c9337d8db9 | 8b56971b34c26d0a83d0f9da07419ad99e0699c0e1e0048b67902c65ded87901 | 新增30项边界；原真实链路与断言保留 |
| tests/test_flywheel_end2end.py | **未留存可靠R02前哈希** | d47af7426ad2828d02725ac4589fc3325bbc30d8276e0b2699c074efb48c18c6 | 隔离库真实XLSX播种35行合成fixture；原有益断言保留 |
| tests/README.md | da591eb12b6a7393eca5e9e68acd915c5b562a56b1202862d7b629457ffe6d09（R01交付） | 82b82d27b33667291e6d9d766c8635fdfdd7170d0a4a3e7332064d9ad320723a | 原始分、快照、门禁、隔离明细及可观察基线说明 |

## 未改动的关键基线/工具（7 个）

| 文件 | SHA256 | 用途/状态 |
|---|---|---|
| app/matcher.py | 48780dfe4bd799535472b92116139da35a3ac01a87a5ab6803100390e5f1e796 | R02未改权重、召回或排序算法 |
| tests/conftest.py | 207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033 | 隔离环境与依赖护栏 |
| tests/fixtures/sandbox_materials_v1.json | f4fb0d678d38e717d41f2140df1327edeb432f99c03e1f53d6b84a22abf036f4 | 35行合成脱敏fixture，未改 |
| tests/fixtures/golden_set_v1.json | c8909a21b34fc12fd45ee96d625dec5798f670cdd725988beb99b2f2f216eae5 | 固定金标，未改 |
| .local-archive/business_snapshot.py | 12b2133dd0bb1dd5b30c72a5702f24f5a4d4565cde886a5f9d552df3e28393a7 | 外层业务快照，本机一次性工具 |
| C:/Users/tlyer/.trae/attachments/m1_r02_capture_evidence.py | 9c6136724d4d585ff24e44f8550ed4c67a2e297e5af1c64a1077fec440888e92 | 仓外一次性真实三规则留证脚本；只读来源，未修改 |
| tests/fixtures/golden_last_run.json | **路径不存在，无内容哈希** | 末次复核仍为False；不得据旧Git状态推断其被本轮删除 |

## R02 最终通过证据与业务资产（12 个）

| 文件 | SHA256 | 真实结果 |
|---|---|---|
| m1_r02_gate_reports.json | 5fa119d13e74f78495dc4055e99a795ab8dd19ea338df1acbc49768a8789aa9b | 最新真实三规则完整报告；harmful false、beneficial/neutral true |
| m1_r02_gate_reports_capture_closed.log | 021bb490e5695f4b7369122bdff86a00d499cc61b6f4878cf8e0a8c34154d554 | 三规则总耗时57.816s，CAPTURE_EXIT=0 |
| m1_r02_compare_boundaries_closed.log | 434cdf90f317483db1565849b9e83704a5e43f32b2b3b35a13ca25cee553bcde | 30 passed/5 deselected/3 warnings/7.94s/EXIT=0 |
| m1_r02_sandbox_final.log | 057f927751da99a44d19589584645d3e3c3b95013eebdc17c787465db9a9e0d6 | 真实沙箱5 passed/30 deselected/3 warnings/74.45s/EXIT=0 |
| m1_r02_required_regressions.log | d8726beeef97be97d725dba3d78b7308ee0d19c634489f4ed0fa49f9a4663e61 | M0护栏18+Query契约11+真实导出3，共32 passed/1 warning/11.25s/EXIT=0 |
| m1_r02_end2end_seeded.log | 61398e293f3fec39dbdb1ceb4b68f5a446c7c7b0d7d9ecb93c6c8f08b03ca60f | 播种后端到端1 passed/3 warnings/24.42s/EXIT=0 |
| m1_r02_full_pytest_final.log | 3636d3786baf7670defc690c00acf9a1f862dbb5dd6613394eb7afac60723266 | 最终固定venv全量120 passed/0 failed/0 skipped/0 errors/5 warnings/96.51s/EXIT=0 |
| m1_r02_venv_verify.json | 0d566f739628a602479c4b5ced8c0f112c4885a9c2b70fc6f907eaf6d0803014 | venv verify PASS/0 |
| m1_r02_verify_after.json | 069c59be18996d87abe34c3b0e9fd683863272de0e32c1cedef95f4d2e030f7f | 末次verify PASS/0；pending/hard errors/business changes均为空 |
| m1_r02_verify_after.log | cd9877045015f53d234a0e70bd9172964952c2233b71f0a1a99ccb3edddfcc0e | 末次VERIFY_AFTER_EXIT=0 |
| m1_r02_outer_snapshot_before.json | 5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | 外层业务快照before |
| m1_r02_outer_snapshot_after.json | 5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | 末次留证后重新生成；与before字节一致 |

## R02 失败及中间过程留痕（8 个，均不得计为最终通过证据）

| 文件 | SHA256 | 留痕说明 |
|---|---|---|
| m1_r02_full_pytest.log | 477bbcefe00ac5e951faec7819fe80bf89c75404ec3b05412260ab8ba33194d5 | 首轮全量1 failed/119 passed/0 skipped/3 warnings/98.28s/EXIT=1；空隔离库前置失败 |
| m1_r02_gate_reports_capture.log | 99f049e07222d8cdbb95afea9bc54ef56c79d62e9ee8ef2a45c604460d02718a | 独立留证脚本首次误用接口，CAPTURE_EXIT=1 |
| m1_r02_gate_reports_capture_final.log | 51b5985872fe9a8e0432689386f1b50eee5712e59b091e4d42d327f98450c608 | 早先成功摘要；缺完整总耗时/真实退出码，最终采用closed日志 |
| m1_r02_compare_boundaries.log | 5df4ce094409d087855afe7f1a5213e74753a6cd3623f59a352ab3a01bb71abd | 比较边界开发断点，保留 |
| m1_r02_compare_boundaries_rerun.log | 34a708cbda45ce06ca5e4f65d8ecf8305b11e850ca15a8d94e2a2069aa033920 | 比较边界重跑过程，保留 |
| m1_r02_compare_boundaries_fixed.log | d1ceaca6e330397e26d0e198e13464cbc135d629ad726d76dc441c5d4f46addf | 比较边界修复过程，保留 |
| m1_r02_compare_boundaries_final.log | 6f2f131860ce17ed8d3fce3c1edae66d3243583c41d1bef89214bb8291fc0578 | 比较边界早期final过程日志；最终以closed日志为准 |
| m1_r02_sandbox_run1.log | 291080c25351eebd5d3a98ecabcdb24e08d6da1316859d70edcfcd2f9b9a01c3 | 真实沙箱首轮过程日志，保留 |

## 交付文档（4 个，活文档）

| 文件 | 交付时 SHA256 |
|---|---|
| doc/V0.1.5/rounds/V015-M1_R02_开发小结.md | f0056cbb330444bc26016323e62b25a9f6cb187e36aafb75737b68996e24f7bc |
| doc/V0.1.5/02_任务台账.md | 07f8b6e1e65b8fc096a5b75d0d301430d5fe8d0cfa4759417afbf622394b1576 |
| doc/V0.1.5/rounds/README.md | e51a3ed4aab9421e3086fede318a1df2ec6a2cbb12873c975aa41e29a2d1793f |
| doc/V0.1.5/evidence/README.md | d5a357c021b66d753316212d7d0e00068a581e089e59cc6da45e20a49f31c4be |

本清单自身不做自引用哈希；台账、两个README及开发小结为活文档，后续审核/统筹维护会使其交付时哈希失配，属预期。代码/测试、工具及实测证据为稳定核验对象，审核独立重跑产生的新文件应另列，不覆写上述原始留痕。

## 业务资产与运行边界

- before/after快照覆盖8个受监视文件路径及27个现存树文件，二者原始字节SHA256完全一致；末次verify另确认`business_hash_unchanged=true`。
- 真实三规则留证使用隔离临时物料库、35行合成fixture、只读固定金标及本地离线`bge-small-zh-v1.5`（dim=512）；`LLM_ENABLED=false`，未下载模型或调用付费LLM。
- 未提交、push、发布、操作线上账号或迁移真实数据；未启动M2～M5。本清单只证明开发交付内容，不代表LEGACY-01审核关闭、M1整包放行或版本终审通过。
