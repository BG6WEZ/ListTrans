# V0.2.0 RC 总复核矩阵

对应 V020-M3 R01 开发自检。历史问题和初始复现见[独立审核](../../项目全面审核_2026-11-12.md)，处分和阶段范围见[问题总台账](../../00_项目管理/问题与版本归属总台账.md)。表中“闭合”沿用已放行实现范围，复验单位为 [R01 串行回归日志](m3_r01_serial/m3_r01_pytest_full.log)中的用例，支持状态以[候选清单](v020_release_candidate_manifest.md)为准。

## F01—F28

| 编号 | 事项 | 当前边界 | R01 复核定位 |
|---|---|---|---|
| F01 | 清洗输入与评测、生产契约 | 已闭合，持续回归 | `test_query_contract.py`、`test_query_cleaning_pipeline.py` 验证单条、批量、表格及评测共享输入契约 |
| F02 | 绑定权威与导出 | 已闭合，持续回归 | `test_binding_decisions.py`、`test_v019_m3_review.py`，无凭据或已失效 `422`，有效确认恢复后 `200` |
| F03 | 名称规则的规格、专业、品牌与可用性约束 | 基础门禁闭合，BOM 受支持族夹具闭合，当前规则 `[]` | `test_auto_binding.py`、`test_v020_m1_bom.py`、[OFF 三规则门禁](m3_r01_serial/m3_r01_rule_gate.json) |
| F04 | 权限、审批、发布状态 | 已闭合，持续回归 | `test_m4_permissions_and_tasks.py` 的 F04 HTTP、服务层身份、撤销及状态机用例 |
| F05 | 反馈用户、文件、行与幂等投递 | 已闭合，持续回归 | `test_v016_m0_feedback_contract.py`、`test_flywheel_end2end.py` |
| F06 | 飞轮聚合 SQL 和结构 | 已闭合，持续回归 | `test_v016_m1_mining_and_rule_context.py`、`test_feedback_miner.py` |
| F07 | 评测、凭据发布和 LIFO | 已闭合，持续回归 | `test_flywheel_end2end.py` 走真实子进程 `eval → 确认 → 回滚`，`test_v016_m2_publish_gate.py` 验证凭据拒绝，`test_v016_m4_lifecycle.py` 验证非栈顶 `409`、两次栈顶回滚 |
| F08 | 镜像内沙箱资产 | 历史实现与断网容器验收已放行，本 RC Linux 镜像待验证 | `test_v016_m3_runtime_assets.py` 复核坏资产、空库、静态路由，[清单及本地 Docker 探针](m3_r01_followup/m3_r01_inventory.json)。承接 `ENV-V017-01` |
| F09 | 否定语义与专业别名 | 已闭合，持续回归 | `test_m3_hard_constraints.py`、`test_m3_hard_constraints_http.py` |
| F10 | 硬冲突、拒绝项及不可用目录项 | 已闭合，持续回归 | `test_m3_hard_constraints_http.py` 验证动态排除、二次加分和预绑定。门禁同时复核三条防爆 unknown 保护 |
| F11 | 静态去明文与真实凭据处置 | 构建防护实现已闭合，实际凭据轮换 `PRG-F11` 开放 | `test_m4_permissions_and_tasks.py::test_f11_deploy_fail_closed_zero_hardcoded_secrets_and_docker_boundary`。[轮换与恢复门禁](../../../deploy_guide.md#首次真实部署前凭据门禁) |
| F12 | 品牌缺值、属性 JSON 和 unknown | 已闭合，持续回归 | `test_m3_hard_constraints.py`、`test_m3_hard_constraints_http.py` |
| F13 | 单位维度、换算与一对多数量 | 通用量纲保护已闭合。BOM 集合、比例、单位在受支持族夹具验证，生产规则 `[]` | 硬约束 HTTP、`test_v020_m1_bom.py`，DN25／D25.4、DN50／D50.8。[M1 放行范围](../rounds/V020-M1_R01_整包放行_统筹裁决.md) |
| F14 | 缓存副本及失效 | 已闭合，持续回归 | `test_v017_m2_catalog_revision.py::test_detached_db_and_vector_reads` 和 revision 链 |
| F15 | 目录与向量 revision 一致 | 已闭合，持续回归 | `test_revision_chain_and_same_code_fts` 覆盖备份恢复、重启加载。[冻结快照 schema 映射](m3_r01_followup/m3_r01_inventory.json) |
| F16 | 原子发布、增量入口及恢复 | 已闭合，持续回归 | `test_v017_m2_catalog_revision.py` 的并发读写、故障注入与模型维度变化，`test_m5_r04_recovery_isolation.py` 的跨层回退及持久写阻断 |
| F17 | 同码 FTS 更新 | 已闭合，持续回归 | `test_revision_chain_and_same_code_fts` 验证旧词消失、同码单记录、FTS 对齐和旧备份恢复 |
| F18 | 文件格式边界 | 已闭合，持续回归 | `test_m5_file_boundary_and_compat.py` 的 F18，清单 `.xlsx`，物料／人员 `.xlsx` 及真实 BIFF8 `.xls` |
| F19 | 标准报表与品类、品牌、工程量 | 已闭合，持续回归 | `test_query_contract.py`、`test_v019_m3_review.py` 的导出档位、列位与工程量验证 |
| F20 | 排序与接受分离 | 默认保护已闭合，校准默认 OFF，冻结全量质量和接受覆盖开放 | `test_v019_m2_calibration.py`、遗留 18 组评分零差检查。GT-0674／0677／0678 要求 `recommended` + `422` |
| F21 | 任务归属与账号代际 | 已闭合，持续回归 | `test_m4_permissions_and_tasks.py::test_f21_task_ownership_across_upload_db_and_flywheel_eval` |
| F22 | Token 撤销、唯一临时口令、首次改密 | 已闭合，持续回归 | `test_m4_auth_lifecycle.py` 验证首次改密、登出撤销、同步角色变更及停用账号保护。真实凭据另归 F11 |
| F23 | 工作池、时延、工作集、容量、取消 | V019-M3 已完成实现、并发及取消终验。本轮单条时延抽测与复测超限，M3 性能验收未达 | `test_v017_m3_runtime_jobs.py` 回归通过。[V019 终审](../../V0.1.9/03_终审与版本准入.md) · [双模式性能数据](v020_release_candidate_manifest.md#性能抽复验) |
| F24 | 文件预算、导入锁、取消及资源释放 | 已闭合，持续回归 | `test_m5_file_boundary_and_compat.py`、`test_m5_r03_commit_arbitration.py`、`test_v017_m3_runtime_jobs.py` |
| F25 | 测试资产隔离 | 已闭合，持续回归 | `test_m0_isolation.py`、`test_m0_guardrail_regression.py`，完整资产哈希及前后隔离证据 |
| F26 | 类型化字段、DN/De/外径与版本兼容 | 已闭合，持续回归 | `test_v017_m1_typed_fields.py`、`test_m3_hard_constraints.py`、`test_v017_m2_catalog_revision.py` |
| F27 | RuleContext 与作用域 | 已闭合，持续回归 | `test_v016_m1_mining_and_rule_context.py`、`test_v016_m2_publish_gate.py` |
| F28 | 发布统一与静态路由 | 已闭合，持续回归 | `test_v016_m2_publish_gate.py` 三源凭据验证，`test_v016_m3_runtime_assets.py` 静态前缀路由 |

## 恢复演练

| 演练 | 隔离回归结果 |
|---|---|
| SQLite 目录升级、索引切换、备份还原 | `test_revision_chain_and_same_code_fts` 通过。用 SQLite backup 保存旧目录，逐次追加、补参和重解析，检查目录 revision、索引内存、磁盘元数据同步，同码旧 FTS 词消失。恢复以新的 revision 发布旧代数据，重启向量管理器后仍能读到恢复内容。向量编码使用确定性夹具，真实 BGE 编码另由性能复验验证。 |
| 故障回滚和写阻断 | `test_import_failure_keeps_old_generation`、`test_finalize_failure_rolls_back_reparse_and_disk` 和 `test_r04_am5_07_verified_manual_recovery_drill_on_isolated_copy` 通过。故障覆盖落盘、索引或配置切换，校验旧 DB、FTS、内存向量和磁盘缓存。跨层恢复失败后保持写阻断和恢复材料，管理员一致性核验通过后再恢复写入。 |
| 规则评测及快照回退 | `test_flywheel_end_to_end_pipeline` 走真实子进程评测，缺凭据和内容变化返回 `409`，有效凭据确认后出现快照，回滚 `200`。双快照用例依次拒绝非栈顶 `409`、两次栈顶 `200`，最终恢复原指纹及原本不存在的配置。双快照用例凭据由隔离夹具预置。 |
| 草稿恢复、导出与 BOM | `test_confirm_restore_and_export_share_canonical_cells_and_ratios`、`test_match_upload_full_manual_draft_restore_and_excel` 和 `test_legacy_draft_cannot_bypass_export` 通过。核对已确认数量和单位、草稿恢复、未确认导出 `422` 以及有效确认后的真实 `.xlsx`。另执行 [BOM 评测、凭据确认及配置回退](m3_r01_followup/m3_r01_bom_governed.json)。 |

本轮回归按 497 项日志核对。所有写入均发生于仓外副本和夹具。冻结业务快照保持原 revision 与文件哈希。[回归日志](m3_r01_serial/m3_r01_pytest_full.log) · [资产守卫](m3_r01_asset_guard.json)

## 观察项与受控能力

| 编号 | 状态与证据 |
|---|---|
| OBS-A-M3-01 | 全量排名缺口开放。729 条治理可回答历史实测中，关键词 Hit@1 少 2、Hit@3 少 4，BGE Hit@3 少 9，两模式 MRR 低于签认，装饰 Hit@1 分别少 2／1。本轮性能子集仅用于抽样，不重算全量质量。 |
| OBS-A-M3-02 | 全量错误建议缺口开放。关键词 270/729（上限 256），BGE 379/729（上限 368）。签认线不变。 |
| OBS-A-M2-01 | 冻结核心历史自动接受覆盖 0/1,067，可判定接受分母为 0，准确率为 `null`。校准默认 OFF，零错绑观测不能证明有覆盖时的精度。 |
| GT-0003／GT-0155 | 历史路由标签待业务核定。保持原冻结计分。 |
| V020-M3-PERF-01 | 本轮新增，性能签认未达。关键词单条 P50/P95 和 BGE 单条 P95 在相同输入的两次测量中超限。原预算保持，待独立审核和统筹裁决。 |
| ENV-V017-01 | Linux 断网正式镜像待验证。V019-M3 的 ON 竞争重跑和 `OBS-A-M2-03` 策略资产白名单边界按该项跟踪。 |
| PRG-F11 | 首次真实部署前凭据核验保持开放，放行依据仍为授权运维脱敏证据。 |
| OBS-AUDIT-M0-01 | 高负载时评测子进程偶发超时仍留痕。R01 低竞争串行的单项与全量耗时见[回归摘要](m3_r01_serial/m3_r01_pytest_summary.json)。 |
| OBS-AUDIT-M1-01 | BOM 评测凭据、激活、使用和配置回退限隔离夹具。现有规则 `[]`。 |
| OBS-AUDIT-M2-01 | 计数口径已关闭，M3 下发及 R01 开工验证均为完整 35 项。 |
| V020-M0 数据及训练 | 反馈排序默认 OFF。M0 本地反馈为零，Dev 无净增益，项目／时间留出证据仍不足，在线训练未启用。 |
| V020-M2 受控补参 | 两项 LLM 开关默认 OFF。离线 Mock 验证候选约束、预算、超时等。真实调用、收益和成本边界开放。 |
| 模型评估 | Cross-Encoder 权重和 1024 维模型的评估按裁决延后至 V0.2.1 再议。 |

质量及观察项依据为 [V0.1.7 签认目标及预算](../../V0.1.7/evidence/指标目标与性能预算_统筹签认.md)、[V0.1.9 终审](../../V0.1.9/03_终审与版本准入.md)、[V020-M0](../rounds/V020-M0_R01_整包放行_统筹裁决.md)、[V020-M1](../rounds/V020-M1_R01_整包放行_统筹裁决.md)、[V020-M2](../rounds/V020-M2_R01_整包放行_统筹裁决.md)。
