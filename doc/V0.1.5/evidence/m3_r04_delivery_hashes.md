# V015-M3 R04 开发交付文件清单与 SHA256 核验

- **基线提交**：`HEAD` = `e964fb16848de9dece8e729e801b61c7d81dd341`（无 `git commit` / `push` / `tag` / `checkout` / `reset`）
- **改动前基线清单与备份**：[m3_r04_pre_baseline_hashes.md](m3_r04_pre_baseline_hashes.md)；R03 基线副本保存在 `.local-archive/m3_r04_pre_backup/`（保留 `.local-archive/m3_r02_pre_backup/` 与 `.local-archive/m3_r03_pre_backup/`）。

## 代码与测试文件（4 个涉范围对象，含改动前/交付双 SHA256）

| 文件 | R04 改动前 SHA256 | R04 交付时 SHA256 | 变更说明 |
|---|---|---|---|
| `app/binding_decisions.py` | `82d6b2ed03fd0385e773cf35f1b29f551550f0c5cf8fa3d4054a4c36507d9593` | `d0c7ed77a1a3903109fadc35e1e3710d80e3d737251c036b0286019a088e8716` | `A-M3-01`：强证据门禁止 `confirmed_unit or unit` 降级升格；要求完整且自洽权威 `confirmed_*` 上下文及可信来源版本标识；`_enrich_saved_bindings` 先剥离伪造键并仅按非空 `materials.unit` 固化 |
| `app/server.py` | `9f5bf79e9fa8523c20424da3aac284a0ab1726b2b9ad81363c50ca2ec766b6b6` | `9f5bf79e9fa8523c20424da3aac284a0ab1726b2b9ad81363c50ca2ec766b6b6` | R04 未改动（保持 R03 对 `_annotate_restored_items` 与 `_gate_export_row` 传入 `require_saved_unit_evidence=True`） |
| `tests/test_m3_hard_constraints.py` | `e0c182e7a95d48af7e937ad5e4bde0771f3eca40c79c0c4c25d3bef89e7440dd` | `81da3a23ccf2695d7987ad8912f96aaeff8267886745c2a258e97a619a920cea` | 新增 `TestM3R04LegacyUnitEvidenceUnit`（+3 项） |
| `tests/test_m3_hard_constraints_http.py` | `fc592be1c9d2e35509af741549682def38fcbbe0376cbdd8d0fc2547de406087` | `ee1cb20cbbcaab86f009da3f3bbbaa2ca4552b9d047d04c56320b7a566ac2e1c` | 新增 `TestM3R04LegacyUnitEvidenceHttp`（+1 项真实 FastAPI HTTP/SQLite/XLSX 闭环） |

其余 R03 代码文件（`app/normalizer.py`、`app/query_preprocessor.py`、`app/llm_rule_engine.py`、`app/matcher.py`、`app/domain_extractors.py`）R04 未改动，维持 [m3_r03_delivery_hashes.md](m3_r03_delivery_hashes.md) 基线。

## 实测证据文件（19 个，`doc/V0.1.5/evidence/`）

| 文件 | SHA256 | 说明 |
|---|---|---|
| `doc/V0.1.5/evidence/m3_r04_pre_baseline_hashes.md` | `308a7b930a9ae849fa8a74973d48fcb7e4db2f464c541cff93e622fb0a05bbce` | R04 改动前工作区与备份清单 |
| `doc/V0.1.5/evidence/m3_r04_outer_snapshot_before.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | 复跑前外层业务快照 |
| `doc/V0.1.5/evidence/m3_r04_outer_snapshot_before.log` | `643b23a637ec5829154745fa9dbfe23618780cd43149d10029cba3f2456e2fa4` | 复跑前外层快照日志 / EXIT=0 |
| `doc/V0.1.5/evidence/m3_r04_venv_verify_before.json` | `7cb7aade20be4b094704d1d1f3f3ac55a5390e3f75ae79b9b7f971d6afdb211a` | 复跑前 `verify_isolation.py`（PASS/0） |
| `doc/V0.1.5/evidence/m3_r04_venv_verify_before.log` | `f322fb6865ec0ad5a71b7ac3012266672a70d3e6e7a110d356a551668f8b5944` | 复跑前 verify 日志 / EXIT=0 |
| `doc/V0.1.5/evidence/m3_r04_rerun.ps1` | `dd063f42122afa065d5e7fadb6f88fdef04138d499dddb88f34ca2fbe3a75263` | R04 统一固定 `venv` 顺序复跑脚本 |
| `doc/V0.1.5/evidence/m3_r04_py_compile.log` | `2ff6c106cad0e7588a5f1d9a1b63addc36c4fed8b0ff602591d56c3447a40257` | 7 个 `app/*.py` `-W error` 严格编译 / EXIT=0 |
| `doc/V0.1.5/evidence/m3_r04_pytest_m3.txt` | `56a4a018f07a1172c512dc781f01f8ba360e9ccd36e78e66d8b0583d9aacb29d` | M3 专项 **99 passed / 45 warnings / 12.84s / EXIT=0** |
| `doc/V0.1.5/evidence/m3_r04_pytest_m0_m2_regression.txt` | `8534034ee0ee702664c5c42ee510bbad1b5a08f8284513511e6c277ed0a10e08` | M0～M2 组合回归 **58 passed / 16 warnings / 13.32s / EXIT=0** |
| `doc/V0.1.5/evidence/m3_r04_pytest_sandbox_30.txt` | `f29f3aa725c97e38789ff7a569c706a91310794865b46816a042b2beccafae7f` | 沙箱边界 **30 passed / 5 deselected / 3 warnings / 7.38s / EXIT=0** |
| `doc/V0.1.5/evidence/m3_r04_capture_evidence.py` | `7be1b9a5dcfe22141983aadf0789f854680786a653303e28c7acfdf34449396d` | 真实双子进程三规则留证脚本 |
| `doc/V0.1.5/evidence/m3_r04_gate_reports.json` | `cfe66732dd01053b49d3d5b4bcd906a8d0c469544cbb597f321ef0039b79a9fe` | 真实三规则：`harmful passed=false`，`beneficial/neutral passed=true`，`snapshot_errors=[]` |
| `doc/V0.1.5/evidence/m3_r04_3rule_gate.txt` | `b03c4a9d92791008a24547b68bb979e2deab4dd4cbddae6a6b3ad9fa34502b78` | 三规则摘要：`48.875s`（wall `55.95s`）/ EXIT=0 |
| `doc/V0.1.5/evidence/m3_r04_r03_gate_compare.json` | `230221c73f056776938df0b230cba41a1371fe568d1de6f08d00e1d8e14fb7bf` | R03 vs R04 三规则逐例对比：`score_comparison_diffs=[]` |
| `doc/V0.1.5/evidence/m3_r04_pytest_full.txt` | `724ba419ad4cd2941e31425b428e6cd43477404dd56c4c426e441ba448669885` | 固定 `venv` 全量 **245 passed / 0 failed / 0 skipped / 0 errors / 64 warnings / 103.33s / EXIT=0** |
| `doc/V0.1.5/evidence/m3_r04_venv_verify_after.json` | `200e1e77edfd3a0542f8803985f907eea61c6d1a4d069daf31a49cdc17c2deca` | 复跑后 `verify_isolation.py` PASS/0，`business_changes=[]`，`hard_errors=[]` |
| `doc/V0.1.5/evidence/m3_r04_venv_verify_after.log` | `de7647b5d9019d448817053c9751c865f4dae93eb4fdf694ccd2207e9cd6a301` | 复跑后 verify 日志 / EXIT=0 |
| `doc/V0.1.5/evidence/m3_r04_outer_snapshot_after.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | 复跑后外层业务快照，与 before 字节完全一致 |
| `doc/V0.1.5/evidence/m3_r04_outer_snapshot_after.log` | `87d05da204958cd25acf664dc600cd193b3fd288b1bc723eb9b0e10fd1855d37` | 复跑后外层快照日志 / EXIT=0 |

## 交付文档（4 个，活文档）

| 文件 | 交付时 SHA256 |
|---|---|
| `doc/V0.1.5/rounds/V015-M3_R04_开发小结.md` | `6c41f6085918e9b35bf1a9f8c486cc2bb23a6dbad270b8e44730effed03c4825` |
| `doc/V0.1.5/02_任务台账.md` | `cb66854f912f66d13d93ce20f005fc329d5b735392bc6b4eb330d2828e598d19` |
| `doc/V0.1.5/rounds/README.md` | `62b8e77b4fa6a67993fe7b334535288466d54794adfa5dfe0819f1e4952da07e` |
| `doc/V0.1.5/evidence/README.md` | `318c181c54a40327e1fb51bf483fc4ad2b35573199ee26e6f12dc4c8964fec97` |

本清单自身不做自引用哈希；台账、两个 `README.md` 及开发小结为活文档，后续审核/统筹维护会使其交付时哈希变化，属预期。上述 4 个代码/测试文件（含 R03 基线备份）与 19 个实测证据文件为稳定核验对象，审核独立复跑产生的新文件应另列，不覆写上述留痕。
