# V016-M4 R01 验证入口与证据口径

Windows 主机使用项目 `venv/Scripts/python.exe`。正式自验由 `m4_r01_validation.py fixed standard cold gate verify` 串行执行，逐项记录命令、原生绝对隔离根、退出码、Asia/Shanghai 起始时间和耗时到各项 `.run.json`。`fixed`/`cold` 使用系统临时目录中的固定根；`standard` 清除 `LISTTRANS_TEST_ROOT`，使用 conftest 默认 `mkdtemp`。全部启用 `PYTHON_DOTENV_DISABLED=1`、`LLM_ENABLED=false`、`TESTING=1` 和离线模型开关。测试中专门验证生产启动拒绝的子进程沿用既有测试的 `TESTING=0` 探针，其十项可写路径仍为隔离路径。

任何 `tests/` 外的入口在导入应用前注入十项路径并断言属于专属隔离根；纯标准库的清单、镜像扫描、进程驱动器不导入应用。模型、黄金集、真实账号和业务库均不作为可写 fixture。

| 检查 | 入口与正式输出 |
|---|---|
| 改前基线与备件 | `m4_r01_inventory.py before` → `m4_r01_baseline.json`；逐项绑定 M3 冻结的 463 项文件，改前备件在 `.local-archive/v016_m4_r01_pre_backup/` |
| 改前行为复现 | `m4_r01_pre_reproduction.py` → `m4_r01_pre_reproduction.json`；从备件提取原函数，真实复现隔离快照越级回滚；导出过滤为受控数据行探针，完整可信身份链另由 HTTP 闭环验证 |
| M4 专项 | `pytest tests/test_v016_m4_diagnostics.py tests/test_v016_m4_lifecycle.py -v -p no:cacheprovider` → `m4_r01_special_final.log`，12 项；包含真实睡眠子进程的超时终止与回收，其他异常用受控输出注入 |
| 五阶段矩阵 | `m4_r01_stage_probe.py` → `m4_r01_stage_matrix.json`；复用已通过的受控召回/得分 fixture，明确不是实际金标收益证明 |
| 故障分支 DB 留痕 | `m4_r01_failure_branches.py` → `m4_r01_failure_branches.json`；保留非零退出/缺失输出/坏 JSON/真实子进程超时及保护集/绑定样本异常的实际任务状态与 `rule_eval_runs` 字段 |
| 双根全量 | `m4_r01_validation.py fixed standard` → `m4_r01_full_fixed_native.*`、`m4_r01_full_standard.*`；保留全部 361 项既有测试并增加 12 项 M4 回归 |
| 空缓存独立历史测试 | `m4_r01_validation.py cold` → `m4_r01_cold_llm_rules.*`；原生新根启动，运行 `tests/test_v0_0_8_llm_rules.py`，轮询 120s 并先断言终态 |
| 三规则与专项诊断 | `m4_r01_validation.py gate` → `m4_r01_gate_reports.json` / `m4_r01_3rule_gate.*`；三规则对 M3/M2/M0 R03 与 V015 M3 R04/M4 R03/M5 R04 六基线逐例比较，另测带约束有益规则和真实空定向集，并保留各评测 DB 记录；输出 GT-0674/0677/0678 完整轨迹和真实导出门禁 422 |
| 清洁主机树闭环 | `m4_r01_runtime_host.py` → `m4_r01_runtime_host_db_verified.log` / `.json`；仅复制正式白名单运行文件，不携带 tests/doc；含越级回滚前后 DB 完整行对比 |
| 正式镜像重建 | `docker build --pull=false --progress=plain -t listtrans:v016-m4-r01-local .` → `m4_r01_docker_build_final.log`；没有推送、发布或业务迁移 |
| 最终镜像全层扫描 | `m3_r01_image_audit.py listtrans:v016-m4-r01-local` → `m4_r01_image_layers_final.json`；复用只读扫描器，输出仅写新文件，不覆写 M3 |
| 断网容器闭环 | `m4_r01_container.py keyword --suffix _verified` / `vector --suffix _verified` → `m4_r01_docker_keyword_verified.*` / `m4_r01_docker_vector_verified.*`；统一 `--rm --init --network none -i`，无 tests/doc 挂载，向量路径仅只读挂载既有 `model_offline`；两个模式各 51 次记录的 HTTP 调用，绑定实际 stdin 脚本 SHA256，含逐字段 DB 与逐层状态恢复 |
| R-M3-01 独立专项入口 | `m4_r01_r_m3_01_probe.py` → `m4_r01_r_m3_01_diagnostics.json`；原三例均 T=98.6、W=98.5、margin≈0.1、unknown 不硬阻断、recommended 且实际导出门禁 422；三规则入口另在同一基线重新执行此诊断 |
| 隔离与交付冻结 | 前置 `tests/verify_isolation.py --out .../m4_r01_isolation_before.json`；收尾 `m4_r01_validation.py verify`；最后 `m4_r01_inventory.py after` 复核外层业务快照、旧证据、改前备件与 43 项模型/fixture，并生成最终 SHA256 清单 |

## 调试记录的适用范围

- `m4_r01_special_initial.log`：Windows 沙箱内跑到诊断专项通过后停滞，已中止；不算完整专项通过。`special_local` 中四个失败为开发新测试误将结构化 `error_code` 当中文错误文案查找；修正为同时核对内存与 DB 的结构化字段。最终以 `special_final` 的 12 项为准。
- `m4_r01_full_fixed.log`：在补齐绑定异常计数前主动停止；不算全量结果。
- `m4_r01_full_fixed_final.log`：首次完整 373 项，372 passed / 1 failed；失败为开发把 `--basetemp` 放在仓库内部，既有 `python -c` 认证探针向上发现 `.env`，使“未提供密钥”前提失效。`m4_r01_environment_probe.json` 对照证明仓库内可导入、仓库外/禁用 dotenv 均拒绝；`m4_r01_auth_probe_diagnostic.log` 默认根单项通过。该环境事件记 `ENV-M4-01`，不改认证实现、不删断言，以纠正路径与 dotenv 配置后的双根全量结果为正式交付依据。
- `runtime_host_initial/diagnostic/verified/complete/final` 为脚本构造迭代：公共批量 API 行格式、建议表不存在 `original_spec` 列、未产生异名负例时三元组应为空，以及标准报表缺少 `matched_brand` 必填字段。均未据此调整业务逻辑或降低断言；`runtime_host_delivery` 与初版双容器已通过，后续补强 DB 零篡改断言，正式交付以 `runtime_host_db_verified`、`docker_keyword_verified` 与 `docker_vector_verified` 为准。
- 首次 `docker_build` 使用额外 `--network=none` 改变 BuildKit 依赖层缓存键，离线 pip 无法找到 torch；改用与 M3 相同的正式构建参数命中原依赖缓存。`docker_build_retry` 为补齐绑定异常处理前的中间镜像；最终源码只绑定 `docker_build_final` 和 `image_layers_final`。

真实业务 `.env` 内容、密钥与人员数据不写入证据；上述环境对照只记录退出码与是否出现缺少密钥的错误标识，不记录配置值。
`special_local.log` 的四行合成 pytest 账号 fixture 展示含认证 token 片段，已仅删除 fixture 展示，完整断言与失败结论保留；改前/改后摘要及理由见 `m4_r01_log_redactions.json`。未改写任何 M0～M3 证据。

独立审核复跑三规则时使用 `m4_r01_3rule_gate.py --out <审核目录中的新文件>`；不能直接复用开发输出路径。验证驱动器及专用 JSON 输出拒绝覆盖已存在的交付结果。
首次冻结草稿保存在 `m4_r01_delivery_hashes_initial.md`：清单复核发现 M3 的 protected 集合未包含管理台账，且重定向中的冻结日志尚未写完。已将本轮改前备件对应的台账纳入交付，并在日志落盘后重做无重定向冻结；初次草稿不是审核基线，以最终 `m4_r01_delivery_hashes.md` 为准。

## 新增统计的分母

`candidate_recall` 的单位是有效目标编码：原始召回池中被命中的目标数 / 全部有效目标数；硬阻断目标仍算“曾被召回”，另由阶段状态计数体现阻断。样本求值失败仍留在分母，未观测的目标不计为命中，并单列 `diagnostic_failed_targets_count`；无有效目标/字段无效等跳过项不混入求值分母。`channel_contributions` 统计每个召回渠道命中的目标编码次数，多路命中会同时归入多个渠道，不是互斥比例。选样时过滤或未入定向/保护配额的样本均有跳过清单，避免固定填 0。进程整体失败不伪造已求值样本数，以任务失败状态和明确 `error_code` 表达。

匹配器结构审计见 `m4_r01_score_contract.json`：仅移除新增诊断节点/渠道元数据后比较 AST，证明既有召回选择和评分/排序/阈值未改；此项只补充说明真实 diff，不能替代全量、门禁或容器验收。
