# V015-M3 R03 开发交付文件清单与 SHA256 核验

- **基线提交**：`HEAD` = `e964fb16848de9dece8e729e801b61c7d81dd341`（无 `git commit` / `push` / `tag` / `checkout` / `reset`）
- **改动前基线清单与备份**：[m3_r03_pre_baseline_hashes.md](m3_r03_pre_baseline_hashes.md)；R02 基线副本保存在 `.local-archive/m3_r03_pre_backup/`（原有 `.local-archive/m3_r02_pre_backup/` 完整保留）。

## 代码与测试文件（9 个，含改动前/交付双 SHA256）

| 文件 | R03 改动前 SHA256 | R03 交付时 SHA256 | 变更说明 |
|---|---|---|---|
| `app/normalizer.py` | `711f221ff7933725e131ecd5f659e674b8dd3cef8c2e6897c437d17d167b46cf` | `a89ceb48e883ef9e31925abc31617f7379378109db4f19088cf82a12e792fae3` | `A-M3-05`：专业归并与动态规则作用域无循环单源（SSOT） |
| `app/query_preprocessor.py` | `d0753c3a26da448060f9c2044c0fa4e7c0be250e2ca610943b6b57360fcffe25` | `f537f584604bf837fd0472ca35d013e6f57525ec0ab9f092087ac0e406993221` | `A-M3-05`：步骤 1 透传 `discipline/category`，步骤 8 复用 `dynamic_rule_matches_query` 并去硬编码 |
| `app/llm_rule_engine.py` | `6d2e18391516f0e5fb1a69303beb16ab28bfe955f27fabbef069fd2b2dca854b` | `fce79ce5204ff00266b6974c7492225bb5488486c190b8270bfc72edb20a9f86` | `A-M3-02`：新增 `DynamicRuleUnavailableError` / `validate_matching_rules`，仅确认缺表返回 `[]` |
| `app/matcher.py` | `4dfc51660debc8d60c217600a1e08bdbff9f55bb63b83719b80bfb93ce234cdb` | `6ebb305fe2afa94a01e252c248e103d330717caf47ea03e3310c32ce1bf25659` | `A-M3-02` fail-closed 阻断；`A-M3-05` 从 `app.normalizer` 复用并重导出作用域函数 |
| `app/binding_decisions.py` | `b012ef44cd871f67bdb7597a79a6ecb3347433c27faa916fe5498b628c7373a3` | `82d6b2ed03fd0385e773cf35f1b29f551550f0c5cf8fa3d4054a4c36507d9593` | `A-M3-01` 确认单位证据校验/落库与漂移门；`A-M3-02` 区分规则不可用阻断 |
| `app/server.py` | `b9c5668e3278db9eb3dfc96363456bae2b60261ea353d3dbed25a5caf5ea82f9` | `9f5bf79e9fa8523c20424da3aac284a0ab1726b2b9ad81363c50ca2ec766b6b6` | `A-M3-01` 恢复降级（`require_saved_unit_evidence=True`）及 `auto_accepted`/确认分支导出核验 |
| `app/domain_extractors.py` | `0620b5b46030a29972e2f98bc0db814d2f35fb0e80b6ef5e5bf3df63933bb20c` | `0620b5b46030a29972e2f98bc0db814d2f35fb0e80b6ef5e5bf3df63933bb20c` | R03 未改动（保持 R02 基线） |
| `tests/test_m3_hard_constraints.py` | `91b3ff0aafc6157ea97d2884986911e4d7b4ee1fa05838ce74da85ad32aad7dc` | `e0c182e7a95d48af7e937ad5e4bde0771f3eca40c79c0c4c25d3bef89e7440dd` | 新增 `TestM3R03AuditClosureUnit`（+5 项） |
| `tests/test_m3_hard_constraints_http.py` | `f5057f0400be13924873c2c290101d729a65dff729a98bfdb25fd7d3ab8d149b` | `fc592be1c9d2e35509af741549682def38fcbbe0376cbdd8d0fc2547de406087` | 新增 `TestM3R03AuditClosureHttp`（+3 项真实 HTTP/SQLite/XLSX） |

## 实测证据文件（19 个，`doc/V0.1.5/evidence/`）

| 文件 | SHA256 | 说明 |
|---|---|---|
| `doc/V0.1.5/evidence/m3_r03_pre_baseline_hashes.md` | `52fc06f2a689205d693f9ea196ca8b8438e9c06295c94013ae0ceb959331aec0` | R03 改动前工作区与备份清单 |
| `doc/V0.1.5/evidence/m3_r03_outer_snapshot_before.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | 复跑前外层业务快照 |
| `doc/V0.1.5/evidence/m3_r03_outer_snapshot_before.log` | `5d7526387cedf71f57c96e7c23345f31bbab9fb281e0c9e7d028941d04a12078` | 复跑前外层快照日志 / EXIT=0 |
| `doc/V0.1.5/evidence/m3_r03_venv_verify_before.json` | `798801424e780c0facb0f5019324d63cd7acca1c7fcb95f5d6e29da9d1a45293` | 复跑前 `verify_isolation.py`（PASS/0） |
| `doc/V0.1.5/evidence/m3_r03_venv_verify_before.log` | `072bbd72450b3b81a83c40a35747fca0cafdb5f0fae786ab3415bf328d3a1eb8` | 复跑前 verify 日志 / EXIT=0 |
| `doc/V0.1.5/evidence/m3_r03_rerun.ps1` | `f788a565eee4ebc6db4780919ec205bb1fa006f87bccda77fc4ca11e1a981e0c` | R03 统一固定 `venv` 顺序复跑脚本 |
| `doc/V0.1.5/evidence/m3_r03_py_compile.log` | `ca49c0d6e1147c2d96ad06a096e4eec61eb2fe4b88d0ba1c2f0e75b910432427` | 7 个 `app/*.py` `-W error` 严格编译 / EXIT=0 |
| `doc/V0.1.5/evidence/m3_r03_pytest_m3.txt` | `118d9890edc86d88eb0c0b53d24791ed3c10f8d1200844129f678a6e5c306966` | M3 专项 **95 passed / 40 warnings / 11.75s / EXIT=0** |
| `doc/V0.1.5/evidence/m3_r03_pytest_m0_m2_regression.txt` | `5d00919a0214c07d9df67d1cae69fe75060a11966a0104fcc662c10d64230b54` | M0～M2 组合回归 **58 passed / 16 warnings / 13.23s / EXIT=0** |
| `doc/V0.1.5/evidence/m3_r03_pytest_sandbox_30.txt` | `d791d4d84ca20cb15a5ad6db0b7f7e4f6a9f5c79ab3ea02baba761c31f3b351d` | 沙箱边界 **30 passed / 5 deselected / 3 warnings / 7.38s / EXIT=0** |
| `doc/V0.1.5/evidence/m3_r03_capture_evidence.py` | `d756245dc58c273ffa754b7c947fc2f40fc21ffe1005a11fb4c3cffaf1f482a5` | 真实双子进程三规则留证脚本 |
| `doc/V0.1.5/evidence/m3_r03_gate_reports.json` | `88d2b6ba9a31fd87dff5c5fc53baeaa70eae3c2f123ba6c984332f05610fdbab` | 真实三规则：`harmful passed=false`，`beneficial/neutral passed=true`，`snapshot_errors=[]` |
| `doc/V0.1.5/evidence/m3_r03_3rule_gate.txt` | `e301cff84e33e88a52b3556b182a11e40c357ecde198a80f389a68a3a15d64a9` | 三规则摘要：`48.885s`（wall `55.993s`）/ EXIT=0 |
| `doc/V0.1.5/evidence/m3_r03_r02_gate_compare.json` | `efd7c664900787d3cb2aa14edacc977180b844e86a04655f8877fa564fcdda04` | R02 vs R03 三规则逐例对比：`score_comparison_diffs=[]` |
| `doc/V0.1.5/evidence/m3_r03_pytest_full.txt` | `fee8f3382b72d23e52f4c1ba3757af9c3891ea5bbc8fc01c64927999b5c3c664` | 固定 `venv` 全量 **241 passed / 0 failed / 0 skipped / 0 errors / 59 warnings / 101.26s / EXIT=0** |
| `doc/V0.1.5/evidence/m3_r03_venv_verify_after.json` | `6c42a23b87b86c28d99d5114a7fbaa86f1c99285925bcbed14e234a0c9f2c7f2` | 复跑后 `verify_isolation.py` PASS/0，`business_changes=[]`，`hard_errors=[]` |
| `doc/V0.1.5/evidence/m3_r03_venv_verify_after.log` | `c77736e040ca00b64636839b24d2b1e897dcfd0370749523dcf26bd745f16bab` | 复跑后 verify 日志 / EXIT=0 |
| `doc/V0.1.5/evidence/m3_r03_outer_snapshot_after.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | 复跑后外层业务快照，与 before 字节完全一致 |
| `doc/V0.1.5/evidence/m3_r03_outer_snapshot_after.log` | `1d70c78ad83635b4abcf7d8c592b5f4ecae327fece428bd2a70278b37f5b0b8e` | 复跑后外层快照日志 / EXIT=0 |

## 交付文档（4 个，活文档）

| 文件 | 交付时 SHA256 |
|---|---|
| `doc/V0.1.5/rounds/V015-M3_R03_开发小结.md` | `923bf9bd76af64837717bdca94d51d48641edffc417ed6bfdbcca7de25a8be9f` |
| `doc/V0.1.5/02_任务台账.md` | `0169f030deea8a6644e70fbd6bf29c02d3e8bd73bbe48b332a630fc7e3195790` |
| `doc/V0.1.5/rounds/README.md` | `98dff654d5fc4b72ff6630ed99621d8c3fca73ac7025f100877c85fe8bd24f3d` |
| `doc/V0.1.5/evidence/README.md` | `297688c0900856a431f4c4b5cf9d1db6b25063e0d93dcfe5f82d1be75b013f49` |

本清单自身不做自引用哈希；台账、两个 `README.md` 及开发小结为活文档，后续审核/统筹维护会使其交付时哈希变化，属预期。上述 9 个代码/测试文件与 19 个实测证据文件为稳定核验对象，审核独立复跑产生的新文件应另列，不覆写上述留痕。
