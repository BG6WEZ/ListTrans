# 实测证据

当前最终状态（2026-11-16）：[M5 R04正式放行、V0.1.5重新终审通过](../rounds/V015-M5_R04_统筹裁决.md)，PRG-F11第一次真实部署前保持开放，后续版本仍锁定，未批准生产发布。所有旧证据/结论保留原字节，活动文档最新摘要另行绑定。

## V0.1.5整改后版本终审（统筹）

- [终审编排脚本](v015_final_review.py)、[源/配置/本地模型与固定证据基线](v015_final_baseline.json)：R04交付33/33、R03冻结物36/36、业务快照一致。
- 统筹实际[96P/0W抽样日志](v015_final_sample.log)、[命令与EXIT=0](v015_final_sample_command.json)；[verify前](v015_final_verify_before.json)、[verify后](v015_final_verify_after.json)均PASS/0。本轮不重复跑全量/浏览器/镜像，独立审核324P等见原报告，不记为统筹重跑。
- [终审收尾](v015_final_closure.json)、[本次统筹精确SHA256](v015_final_delivery_hashes.md)；[正式版本终审](../03_终审与版本准入.md)记录范围、开放门禁、回滚/数据限制和下一版本锁定。

当前（2026-11-16）：R03独立审核PASS报告原文保留；统筹终审新增A-M5-06/07两项P1，[M5不放行/版本终审不通过、唯一下发R04](../rounds/V015-M5_R03_统筹裁决.md)。旧证据与结论不覆写。

## V015-M5 R03统筹终审抽查

- [36项交付与业务快照初验](m5_r03_coordinator_final_before.json)、[统筹实际抽查脚本](m5_r03_coordinator_final_check.py)、[两项新反例](m5_r03_coordinator_final_probes.json)：旧缓存备份移动失败误删未备份文件、DB反向恢复失败仍可继续导入。
- [首次受长路径影响的记录](m5_r03_coordinator_final_probes_attempt1.json)保留，第二组当次未复现，不计成功；缩短临时目录/task_id后两项确认。
- [收尾保护](m5_r03_coordinator_final_closure.json)、[本次统筹交付SHA256](m5_r03_coordinator_final_delivery_hashes.md)。独立审核310P等为报告引用，本轮未重跑全量/浏览器/镜像；只执行新增隔离终审探针。

当前（2026-11-16）：[M5 R02审核退回，唯一下发R03](../rounds/V015-M5_R02_统筹裁决.md)，03～05关闭、01/02 P1继续开放。统筹仅抽查证据、提交窗口源码与只读哈希/快照；下方旧状态为历史。

## V015-M5 R02审核接收与R03统筹下发

- [R02独立审核](../rounds/V015-M5_R02_审核小结.md)、[四项反例](m5_r02_audit_probes.json)、[审核收尾](m5_r02_audit_closure.json)、[原审核清单](m5_r02_audit_delivery_hashes.md)保持原字节，286P等为独立审核实测，非统筹重跑。
- [统筹只读核对脚本](m5_r03_coordinator_check.py)、[下发前基线](m5_r03_coordinator_before.json)、[收尾核对](m5_r03_coordinator_closure.json)、[统筹下发SHA256](m5_r03_coordinator_delivery_hashes.md)记录当前来源及活动文档。
- 开发两份快照清单62位抄录错误及真实64位摘要已在[R03裁决](../rounds/V015-M5_R02_统筹裁决.md)和统筹前置JSON追加勘误；原开发清单未覆写，快照文件无变化。UI注入按钮证据限制亦见裁决。

最新状态（2026-11-16）：[M5 R01审核退回/R02下发](../rounds/V015-M5_R01_统筹裁决.md)，独立269P/0W及五项异常反例；A-M5-01～05开放。M4未上线条件性裁决、PRG-F11首次真实部署前和后续版本锁定保持。旧证据只绑定各轮来源，不因活动文档更新静默重签；下文旧状态保留原事实。

## V015-M5 R01独立审核（退回整改）

- [初验与冻结哈希](m5_r01_audit_baseline.json)、[真实改动diff](m5_r01_audit_changes.diff)、[运行编排](m5_r01_audit_run.py)。
- [独立269P/0W日志](m5_r01_audit_full_retry.log)、[命令/耗时/EXIT=0](m5_r01_audit_full_retry_command.json)；[首次沙箱中止记录](m5_r01_audit_sandbox_interruption.json)与[m5_r01_audit_full.log](m5_r01_audit_full.log)分开保留，不记通过。
- [独立异常探针](m5_r01_audit_probes.py)、[5项反例结果](m5_r01_audit_probes.json)；[verify前](m5_r01_audit_verify_before.json)、[verify后](m5_r01_audit_verify_after.json)、[收尾保护](m5_r01_audit_closure.json)、[审核交付SHA256](m5_r01_audit_delivery_hashes.md)。
- 浏览器/镜像/独立三规则采集为开发报告已核对及查代码，本轮未独立重跑；缺口见审核A-M5-04/05。开发R01证据保持原字节。

## V015-M5 R01统筹下发（尚无开发/审核实测）

- [只读核对脚本](m5_r01_dispatch_check.py)、[下发前基线](m5_r01_dispatch_baseline.json)：HEAD、70项来源及业务快照、旧清单3份活动文档预期差异；未执行应用/测试/真实轮换。
- [收尾校验](m5_r01_dispatch_closure.json)、[统筹下发交付SHA256](m5_r01_dispatch_delivery_hashes.md)：记录本次状态文档与下发文件；不覆写M4旧清单或旧结论。
- V-M4-02补证：[授权方说明](m4_r03_vm402_owner_disposition.md)、[开发核验脚本](m4_r03_vm402_verify.py)、[开发11/11报告](m4_r03_vm402_verify.json)与[审核/统筹条件性裁决](../rounds/V015-M4_R03_V-M4-02_审核与统筹裁决.md)；既有审核重跑90秒超时保留，不宣称独立重跑通过。

## V015-M4 R03 / V-M4-01完整镜像补证

- 开发提供：[完整构建脚本](m4_r03_vm401_prod_image_verify.py)、[构建日志](m4_r03_vm401_prod_image_build.log)、[采集结果](m4_r03_vm401_prod_image_verify.json)。此三项保留原字节。
- 独立核验：[初验与冻结哈希](m4_r03_vm401_audit_baseline.json)、[审核源码](m4_r03_vm401_audit.py)、[11层摘要/扫描/真实容器鉴权](m4_r03_vm401_audit_result.json)、[日志](m4_r03_vm401_audit.log)。原始广义候选报告保留，最终状态须结合来源分类，不能只读原始未分类FAIL或开发PASS。
- 来源分类：[149项公开CA及24项依赖源码/字节码](m4_r03_vm401_fixture_provenance.json)、[ecdsa官方公开wheel源码对照](m4_r03_vm401_public_fixture_verify.json)、[最终逐项门禁裁定PASS](m4_r03_vm401_audit_decision.json)。两份测试字节码含公开固定私钥样例，未冒充“私钥样式文本为零”。
- [收尾与业务快照](m4_r03_vm401_audit_closure.json)、[本轮交付SHA256](m4_r03_vm401_audit_delivery_hashes.md)。旧审核/开发实测保留；活动台账进度更新由本轮清单绑定，代码与业务快照不变。

## V015-M4 R03独立审核

- [基线/73项交付与32份备份](m4_r03_audit_baseline.json)、[50项旧审核与范围核对](m4_r03_audit_prior_evidence_check.json)、[完整净diff](m4_r03_audit_r02_to_r03.diff)、[原断言保护](m4_r03_audit_test_assertions.json)。
- [独立命令/退出码](m4_r03_audit_summary.json)、[259项全量](m4_r03_audit_full.log)、[14项M4](m4_r03_audit_m4.log)、[99项M3](m4_r03_audit_m3.log)、[58项组合](m4_r03_audit_required.log)、[30项边界](m4_r03_audit_boundaries.log)、[R02/R03反例与正例](m4_r03_audit_probes.json)。
- [Chrome六步](m4_r03_audit_browser/m4_r03_browser_e2e.json)、[真实三规则](m4_r03_audit_gate_reports.json)、[逐例零差异](m4_r03_audit_gate_compare.json)、[双Dockercanary/16配置](m4_r03_audit_docker_probes.json)、[真实Dockerfile离线构建失败](m4_r03_audit_docker_full_offline.json)。scratch局部验证不是完整安全镜像通过证明，实际轮换仍缺外部证据。
- [隔离后检](m4_r03_audit_verify_after.json)、[业务快照/输入冻结收尾](m4_r03_audit_closure.json)、[审核与统筹交付SHA256](m4_r03_audit_delivery_hashes.md)。开发及旧审核证据未覆写。

## V015-M4（R03 留痕）

- 改动前基线与交付清单：[R03改动前基线哈希（32项）](m4_r03_pre_baseline_hashes.md) · [R03交付SHA256清单](m4_r03_delivery_hashes.md)
- 一键顺序复验：[m4_r03_rerun.ps1](m4_r03_rerun.ps1) · 语法/告警编译 [m4_r03_py_compile.log](m4_r03_py_compile.log)（`EXIT=0`）
- 专项与对比探针：[m4_r03_probes.py](m4_r03_probes.py) → [m4_r03_probes.json](m4_r03_probes.json) / [日志](m4_r03_probes.log)（8/8 通过，含 R02 退回代码重放对照与 `A-M4-05` 全分支）
- Docker 排除与离线门：[m4_r03_docker_probe.py](m4_r03_docker_probe.py) → [m4_r03_docker_probes.json](m4_r03_docker_probes.json) / [日志](m4_r03_docker_check.log)（10 canary 全排除 + 16 允许配置全保留；完整 `Dockerfile` `--network=none` `exit=1`，未用 `v0.1.4` 覆盖层替代）
- M4 / 跨包 / 全量测试：[M4 14P日志](m4_r03_pytest.log) · [M3 99P日志](m4_r03_m3_regression.log) · [M0～M2 58P日志](m4_r03_m0_m2_regression.log) · [沙盒边界 30P日志](m4_r03_sandbox_regression.log) · [全量 259P 日志](m4_r03_full_pytest.log) · [AST旧断言零丢失](m4_r03_test_assertions_check.json)（`old_assertions_missing=0`）
- Chrome CDP 端到端与离线三规则：[m4_r03_browser_e2e.json](m4_r03_browser_e2e.json)（[图1](m4_r03_01_forced_pwd_change.png) / [图2](m4_r03_02_employee_forbidden.png) / [图3](m4_r03_03_dual_approval_and_isolation.png)） · [m4_r03_3rule_check.json](m4_r03_3rule_check.json)（[门禁报告](m4_r03_gate_reports.json)）
- 隔离与快照不变性：[m4_r03_isolation_verify_before.json](m4_r03_isolation_verify_before.json) / [m4_r03_isolation_verify.json](m4_r03_isolation_verify.json)（`PASS`/`0`） · [前快照](m4_r03_snapshot_before.json) / [后快照](m4_r03_snapshot_after.json) / [差异比对](m4_r03_snapshot_diff.txt)（`5768bdca...5c7e`，`diffs=[]`）

- R02独立审核：[小结](../rounds/V015-M4_R02_审核小结.md)、[交付/备份基线](m4_r02_audit_baseline.json)、[净diff](m4_r02_audit_r01_to_r02.diff)、[命令/退出码](m4_r02_audit_summary.json)、[258项全量](m4_r02_audit_full.log)、[旧断言对照](m4_r02_audit_test_assertions.json)。
- R02独立反例：[探针源码](m4_r02_audit_probes.py)、[原四项与新增停用回归结果](m4_r02_audit_probes.json)。A-M4-05证明普通同步使停用0→1/原密码403→200，R01函数对照保持0/403；EXIT=0只代表观察完成。
- R02独立集成：[浏览器6步](m4_r02_audit_browser/m4_r02_browser_e2e.json)、[真实三规则](m4_r02_audit_gate_reports.json)、[逐例零差异](m4_r02_audit_gate_compare.json)、[集成退出码](m4_r02_audit_integration_summary.json)、[离线双Docker canary与16配置保留](m4_r02_audit_docker_probes.json)、[真实Dockerfile离线构建失败](m4_r02_audit_docker_full_offline.json)。历史应用镜像覆盖层不作完整安全镜像证明。
- R02收尾：[隔离后检](m4_r02_audit_verify_after.json)、[业务快照/冻结输入/哈希核验](m4_r02_audit_closure.json)、[本轮精确交付SHA256](m4_r02_audit_delivery_hashes.md)。旧轮证据及开发实测未覆写。

- M4 R02开发自验（待独立审核）：[开发小结](../rounds/V015-M4_R02_开发小结.md)、[R02改动前基线哈希](m4_r02_pre_baseline_hashes.md)、[R02交付SHA256清单](m4_r02_delivery_hashes.md)、[一键复验脚本](m4_r02_rerun.ps1)（全步 `EXIT=0`：[258项全量](m4_r02_full_pytest.log) 258P/0F/0S/64W、[13项M4](m4_r02_pytest.log)、[99项M3](m4_r02_m3_regression.log)、[58项M0～M2](m4_r02_m0_m2_regression.log)、[30项边界](m4_r02_sandbox_regression.log)、[旧断言保留AST核对0缺失](m4_r02_test_assertions_check.json)）。
- M4 R02专项探针/容器/浏览器/三规则：[R02 SQLite/HTTP/跨进程探针](m4_r02_probes.json)（7/7 通过）、[Docker诊断与覆盖层检查](m4_r02_docker_probes.json)（双合成 canary `0/9`入层；`v0.1.4` 覆盖层 `workers_1=true`/`uvicorn_process_count=1`/缺密钥 `exit=3`；真实 Dockerfile `--network=none` pip 阶段 `exit=1` 待验证）、[真实Chrome E2E](m4_r02_browser_e2e.json)（6/6 通过，含 [改密图](m4_r02_01_forced_pwd_change.png) / [越权图](m4_r02_02_employee_forbidden.png) / [双审发布图](m4_r02_03_dual_approval_and_isolation.png)）、[真实三规则报告](m4_r02_gate_reports.json)（[对比diff=[]](m4_r02_3rule_check.json)）、[隔离前/后核验](m4_r02_isolation_verify.json)（均 PASS/0）、[外层快照diff](m4_r02_snapshot_diff.txt)（SHA256恒为 `5768bdca...5c7e`，`diffs=[]`）、[F11轮换操作与核验指南（R02更新，外部待验证）](m4_r02_f11_rotation_runbook.md)。

- M4 R01独立审核：[审核小结](../rounds/V015-M4_R01_审核小结.md)、[基线](m4_r01_audit_baseline.json)、[命令/退出码](m4_r01_audit_summary.json)、[255项全量](m4_r01_audit_full.log)、[10项M4](m4_r01_audit_m4.log)、[99项M3](m4_r01_audit_m3.log)、[58项组合](m4_r01_audit_required.log)、[30项边界](m4_r01_audit_boundaries.log)。
- M4 R01反例：[Token/身份/临时口令探针源码](m4_r01_audit_probes.py)、[结构化结果](m4_r01_audit_probes.json)、[Docker离线诊断源码](m4_r01_audit_docker_probe.py)、[实际上下文/层canary结果](m4_r01_audit_docker_probes.json)。探针EXIT=0表示完成观察，存在不符合验收的行为；诊断层不是完整生产镜像。
- M4 R01独立集成：[真实浏览器结果](m4_r01_audit_browser/m4_r01_browser_e2e.json)、[截图](m4_r01_audit_browser/m4_r01_browser_e2e.png)、[真实三规则](m4_r01_audit_gate_reports.json)、[对M3 R04逐例零差异](m4_r01_audit_gate_compare.json)、[集成耗时/退出码](m4_r01_audit_integration_summary.json)。审核时Docker已可用；完整生产依赖镜像和外部实际轮换仍未核验。
- M4 R01收尾：[旧断言对照](m4_r01_audit_test_assertions.json)、[隔离后检](m4_r01_audit_verify_after.json)、[快照/保护输入/开发交付哈希核验](m4_r01_audit_closure.json)、[本轮精确交付SHA256](m4_r01_audit_delivery_hashes.md)。开发证据与旧轮结论未覆写。

- M4 R01统筹下发：[基线与保护文件SHA256](m4_r01_coordinator_baseline.json)、[业务快照](m4_r01_coordinator_snapshot_before.json)、[收尾核验](m4_r01_coordinator_closure.json)、[本轮交付SHA256](m4_r01_coordinator_delivery_hashes.md)。本轮未运行应用/测试；R04的245P仅引用此前独立审核，不作本次重跑声明。
- M4 R01开发自验（待独立审核）：[一键复验脚本](m4_r01_rerun.ps1)（255 passed / 0 failed、M4 10P、M3 99P、M0～M2 58P、沙盒边界 30P）、[真实Chrome E2E](m4_r01_browser_e2e.json)（[截图](m4_r01_browser_e2e.png)）、[三规则门禁](m4_r01_gate_reports.json)（[对比diff=[]](m4_r01_m3_r04_gate_compare.json)）、[隔离前/后核验](m4_r01_venv_verify_after.json)、[外层快照前/后](m4_r01_outer_snapshot_after.json)（零变化）、[Docker离线日志](m4_r01_docker_check.log)（待验证）、[F11外部轮换手册](m4_r01_f11_rotation_runbook.md)（待验证）、[开发交付SHA256清单](m4_r01_delivery_hashes.md)。
- M3 R04审核/整包放行仍以[R04审核](../rounds/V015-M3_R04_审核小结.md)、[统筹裁决](../rounds/V015-M3_R04_统筹裁决.md)为准；本轮不改旧轮结论或实测证据。

- R04独立复验：[汇总](m3_r04_audit_summary.json)、[99项M3](m3_r04_audit_m3.log)、[58项组合](m3_r04_audit_required.log)、[30项边界](m3_r04_audit_boundaries.log)、[真实三规则](m3_r04_audit_gate_reports.json)、[245项全量](m3_r04_audit_full.log)、[相对R03逐例零差异](m3_r04_audit_r03_compare.json)。
- R04关键闭合：[旧格式来源重放→当前recommended/422](m3_r04_audit_legacy_replay.json)、[13组负例/可信格式与重确认/XLSX正例](m3_r04_audit_probes.json)、[净diff](m3_r04_audit_r03_to_r04.diff)。
- R04收尾：[保护输入/快照/哈希核验](m3_r04_audit_closure.json)、[本轮文件SHA256](m3_r04_audit_delivery_hashes.md)。本轮未覆写旧开发/审核证据，旧函数补充重放不替代完整服务/模型验收。

- R03独立复验：[汇总](m3_r03_audit_summary.json)、[95项M3](m3_r03_audit_m3.log)、[58项组合](m3_r03_audit_required.log)、[30项边界](m3_r03_audit_boundaries.log)、[真实三规则](m3_r03_audit_gate_reports.json)、[241项全量](m3_r03_audit_full.log)、[三规则零分差](m3_r03_audit_r02_compare.json)。
- R03兼容/异常反例：[结构化观察](m3_r03_audit_probes.json)、[旧格式来源与当前HTTP/XLSX重放](m3_r03_audit_legacy_replay.json)；补充旧函数重放仅用于fixture来源，不替代已执行完整服务/模型检查。
- R03收尾：[保护项/快照/基线](m3_r03_audit_closure.json)、[本轮文件SHA256](m3_r03_audit_delivery_hashes.md)。本轮未覆写旧开发/审核证据。

- R02独立复验：[汇总](m3_r02_audit_summary.json)、[87项M3](m3_r02_audit_m3.log)、[58项组合](m3_r02_audit_required.log)、[30项边界](m3_r02_audit_boundaries.log)、[三规则报告](m3_r02_audit_gate_reports.json)、[全量233项](m3_r02_audit_full.log)。
- R02审核反例：[13组结构化观察](m3_r02_audit_probes.json)、[日志](m3_r02_audit_probes.log)、[R01内容备份→R02净diff](m3_r02_audit_r01_to_r02.diff)、[三规则分数零差异](m3_r02_audit_r01_compare.json)。观察脚本EXIT=0不表示错误行为通过验收。
- R02收尾：[快照与基线绑定](m3_r02_audit_closure.json)、[本轮文件SHA256](m3_r02_audit_delivery_hashes.md)。未覆写开发或R01证据。

当前统筹状态（2026-11-14）：M0～M2均已放行，仅下发M3 R01。[M2统筹抽查](m2_r01_coordinator_check.json)绑定当前35/38哈希及审核关键报告/业务快照；[V8语法补验](m2_r01_coordinator_js_syntax.json)仅证明当前app.js可解析，真实浏览器仍由UI-M5-01承接；[本轮交付哈希](m2_r01_coordinator_delivery_hashes.md)列明精确文件范围。HTTP/模型/全量运行数据引用独立审核，不宣称统筹重跑；下列历史状态不覆写。

记录命令、环境、基线、哈希、退出码及结果；不得包含密钥、个人信息或未脱敏业务清单。原始大型数据保留在受控本机路径，只记录授权的快照标识。

## V015-M0

- R01 交付哈希清单：[m0_delivery_hashes.md](m0_delivery_hashes.md)
- R01 隔离复验：[m0_isolation_verify.json](m0_isolation_verify.json)、冒烟 [m0_smoke_pytest.log](m0_smoke_pytest.log)、全量 [m0_full_pytest.log](m0_full_pytest.log)
- R01 审核独立复验：[m0_r01_audit_verify.json](m0_r01_audit_verify.json)
- R02 交付哈希清单：[m0_r02_delivery_hashes.md](m0_r02_delivery_hashes.md)
- R02 隔离复验：[m0_r02_isolation_verify.json](m0_r02_isolation_verify.json)、冒烟 [m0_r02_smoke_pytest.log](m0_r02_smoke_pytest.log)、全量 [m0_r02_full_pytest.log](m0_r02_full_pytest.log)（41 passed / 4 skipped）
- R02 审核独立复验：[m0_r02_audit_verify.json](m0_r02_audit_verify.json)
- R02 统筹负向抽查：[探针源码](m0_r02_coordinator_probe.py) / [复现结果](m0_r02_coordinator_probe.json)（复现两项护栏P1；非验收通过）；[统筹交付哈希](m0_r02_coordinator_delivery_hashes.md)
- R03 交付哈希清单：[m0_r03_delivery_hashes.md](m0_r03_delivery_hashes.md)
- R03 隔离复验（系统 Python，缺依赖）：[m0_r03_isolation_verify.json](m0_r03_isolation_verify.json)（PENDING/2）、冒烟 [m0_r03_smoke_pytest.log](m0_r03_smoke_pytest.log)、全量 [m0_r03_full_pytest.log](m0_r03_full_pytest.log)（54 passed / 4 skipped）、负向回归 [m0_r03_guardrail_regression.log](m0_r03_guardrail_regression.log)（13 passed）、[统筹探针重定向复跑](m0_r03_coordinator_probe_rerun.json)（缺陷不可复现）
- R03 venv 补环境（ENV-M0-01）：[安装日志](m0_r03_venv_pip_install.log) / [freeze](m0_r03_venv_pip_freeze.txt)、[venv verify](m0_r03_venv_verify.json)（PASS/0）、[venv 全量 pytest](m0_r03_venv_full_pytest.log)（7 failed/68 passed/1 skipped/2 errors，含失败如实留痕）、[逐模块汇总](m0_r03_venv_single_summary.txt) 与 `m0_r03_venv_single_*.log`、[顺序耦合复现](m0_r03_venv_order_coupling.log)、[末次 verify](m0_r03_venv_verify_final.json)（PASS/0，业务零变化）
- R03 审核独立复验：[审核复验汇总](m0_r03_audit_verify.json)、系统 Python verify [m0_r03_audit_sys_verify.json](m0_r03_audit_sys_verify.json)（PENDING/2）、统筹探针重定向复跑 [m0_r03_audit_probe_rerun.json](m0_r03_audit_probe_rerun.json)、venv verify [m0_r03_audit_venv_verify.json](m0_r03_audit_venv_verify.json)（PASS/0，外层快照零变化）
- R03 统筹独立抽查：[汇总与外层快照](m0_r03_coordinator_check.json)、[18项专项日志](m0_r03_coordinator_regression_smoke.log)、[venv verify结果](m0_r03_coordinator_venv_verify.json) / [日志](m0_r03_coordinator_venv_verify.log)（PASS/0，6/6路径ok）；[本轮交付哈希](m0_r03_coordinator_delivery_hashes.md)。全量失败未重跑或抹除，已下发同包R04。
- R04 交付哈希清单：[m0_r04_delivery_hashes.md](m0_r04_delivery_hashes.md)
- R04 隔离复验与全量实测：[verify PASS/0](m0_r04_precheck_verify.json)、[业务资产快照 before](m0_r04_assets_before.json) / [after](m0_r04_assets_after.json)（23 文件零差异）、[venv 全量 pytest](m0_r04_venv_full.log)（1 failed=LEGACY-01保留/76 passed/1 skipped/1 warning，exit 1）
- R04 LEGACY-01 诊断：[m0_r04_legacy01_diagnose.json](m0_r04_legacy01_diagnose.json)（金标命中 25 条、rank 恒 0 → 门禁稳态放行，根因为测试前置条件缺失，回交统筹）
- R04 审核独立复验：[审核复验汇总](m0_r04_audit_verify.json)、venv verify [m0_r04_audit_venv_verify.json](m0_r04_audit_venv_verify.json)（PASS/0）、LEGACY-01 诊断复跑 [m0_r04_audit_legacy01_diagnose.json](m0_r04_audit_legacy01_diagnose.json)（外层快照零差异）
- R04 统筹抽查：[汇总与外层业务快照](m0_r04_coordinator_check.json)、[18项专项日志](m0_r04_coordinator_regression_smoke.log)、[venv verify](m0_r04_coordinator_venv_verify.json) / [日志](m0_r04_coordinator_venv_verify.log)（PASS/0，6/6路径ok）；[本轮交付哈希](m0_r04_coordinator_delivery_hashes.md)。M0隔离包放行，仅下发M1；全量原失败/skip继续记录，承接和期限见[R04统筹裁决](../rounds/V015-M0_R04_统筹裁决.md)。
- 退役测试场景盘点：[m0_legacy_test_inventory.md](m0_legacy_test_inventory.md)

## V015-M1

当前统筹状态：M1整包已放行，LEGACY-01局部关闭，仅下发M2 R01。[R02统筹抽查](m1_r02_coordinator_check.json)记录当前32/35交付哈希、真实报告绑定及合成比较分支探针；业务资产无变化。[统筹交付哈希](m1_r02_coordinator_delivery_hashes.md)绑定本轮精确文件范围。真实模型/全量运行数据引用R02审核独立实测，不作为统筹重跑声明；原历史记录保留。

- R01 改动前基线：[m1_r01_pre_baseline_hashes.md](m1_r01_pre_baseline_hashes.md)
- R01 交付哈希清单：[m1_r01_delivery_hashes.md](m1_r01_delivery_hashes.md)
- R01 复验实测：venv verify [m1_r01_venv_verify.json](m1_r01_venv_verify.json) 与末次 [m1_r01_verify_after.json](m1_r01_verify_after.json)（均 PASS/0）、guardrail+冒烟 [m1_r01_guardrail_smoke.log](m1_r01_guardrail_smoke.log)（18 passed）、venv 全量 [m1_r01_full_pytest.log](m1_r01_full_pytest.log)（1 failed=LEGACY-01 harmful 保留/89 passed/0 skipped/3 warnings，exit 1）
- R01 外层业务快照：[before](m1_r01_outer_snapshot_before.json) / [after](m1_r01_outer_snapshot_after.json)（字节级零变化）
- R01 LEGACY-01 收口：[证据汇总](m1_r01_legacy01_evidence.json)、[GT-0313 分步打分诊断日志](m1_r01_diag_score_breakdown.log)（harmful 门禁仍放行，rank-only 门禁设计缺陷回交统筹）
- R01 审核独立复验：[审核复验汇总](m1_r01_audit_verify.json)、venv verify [前](m1_r01_audit_venv_verify.json) / [后](m1_r01_audit_verify_after.json)（均 PASS/0，外层快照零变化，golden_last_run 不存在）
- R01 统筹抽查：[代码/脚本哈希与运行限制](m1_r01_coordinator_check.json)（15/15代码脚本匹配；组合运行90秒超时，未计通过；之后业务快照与审核一致）、[A-M1-01证据哈希勘误](m1_r01_evidence_hash_errata.md)、[统筹交付哈希](m1_r01_coordinator_delivery_hashes.md)。[R02局部门禁下发](../rounds/V015-M1_R01_统筹裁决.md)，M2仍未下发。
- R02 交付哈希清单：[m1_r02_delivery_hashes.md](m1_r02_delivery_hashes.md)；[开发小结](../rounds/V015-M1_R02_开发小结.md)（开发自验完成，待独立审核，未预设LEGACY-01关闭）。
- R02 真实离线双子进程三规则：[完整报告](m1_r02_gate_reports.json)、[末次带耗时/退出码日志](m1_r02_gate_reports_capture_closed.log)（57.816s，EXIT=0；harmful报告`passed=false`，由目标分/margin保护拒绝，beneficial/neutral报告`passed=true`）。首次留证脚本[失败日志](m1_r02_gate_reports_capture.log)（EXIT=1）及早先[成功摘要](m1_r02_gate_reports_capture_final.log)均保留，前者不计通过。
- R02 边界与真实沙箱：[比较/完整性/快照/子进程30项](m1_r02_compare_boundaries_closed.log)（30 passed/5 deselected/3 warnings/7.94s/EXIT=0）、[真实沙箱5项](m1_r02_sandbox_final.log)（5 passed/30 deselected/3 warnings/74.45s/EXIT=0）；中间断点日志按原文件名保留，未覆写。
- R02 护栏、契约与真实导出：[m1_r02_required_regressions.log](m1_r02_required_regressions.log)（M0护栏18+Query契约11+auto_binding真实XLSX导出3，共32 passed/0 skipped/1 warning/11.25s/EXIT=0，含openpyxl重读）。
- R02 全量留痕：[首轮](m1_r02_full_pytest.log)（1 failed/119 passed/0 skipped/3 warnings/98.28s/EXIT=1，空隔离库前置失败，不计通过）、[播种后端到端专项](m1_r02_end2end_seeded.log)（1 passed/24.42s/EXIT=0）、[最终固定venv全量](m1_r02_full_pytest_final.log)（120 passed/0 failed/0 skipped/0 errors/5 warnings/96.51s/EXIT=0）。5个warning实例仍属3类COMPAT-M5-01。
- R02 隔离与资产闭环：[venv verify](m1_r02_venv_verify.json)、[末次verify](m1_r02_verify_after.json) / [带退出码日志](m1_r02_verify_after.log)（PASS/0）、外层业务快照[before](m1_r02_outer_snapshot_before.json) / [after](m1_r02_outer_snapshot_after.json)（字节一致）；末次留证后复核业务资产零变化，`tests/fixtures/golden_last_run.json`保持不存在。
- R02 审核独立复验：[审核复验汇总](m1_r02_audit_verify.json)、[真实双子进程三规则报告](m1_r02_audit_gate_reports.json)（59.625s/EXIT=0，harmful拒绝、beneficial/neutral通过）、venv verify [前](m1_r02_audit_venv_verify_before.json) / [后](m1_r02_audit_venv_verify_after.json)（均PASS/0）、外层业务快照 [before](m1_r02_audit_outer_snapshot_before.json) / [after](m1_r02_audit_outer_snapshot_after.json)（SHA256均为`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`，差异`[]`，`golden_last_run.json`不存在）。

## V015-M2

当前R01开发交付与独立审核均已完成，待统筹裁决；M3～M5未下发。本机无独立JS语法或浏览器运行验证环境（作为残余边界如实记录）。

- R01 基线与交付：[改动前工作区哈希](m2_r01_pre_baseline_hashes.md)、[精确交付文件清单/SHA256](m2_r01_delivery_hashes.md)、[开发小结](../rounds/V015-M2_R01_开发小结.md)。历史未提交工作及业务资产保留，改动前哈希不等同于内容备份。
- R01 护栏、契约、真实XLSX与决策回归：[m2_r01_required_regressions.log](m2_r01_required_regressions.log)（M0护栏18+Query契约11+auto_binding真实XLSX 3+M2决策26，共58 passed/0 failed/0 skipped/16 warnings/14.26s/EXIT=0；含真实HTTP匹配→确认→导出→XLSX重读）。
- R01 M1比较边界：[m2_r01_compare_boundaries.log](m2_r01_compare_boundaries.log)（30 passed/0 failed/5 deselected/3 warnings/8.25s/EXIT=0）。
- R01 真实离线双子进程三规则：[完整报告](m2_r01_gate_reports.json)、[摘要/耗时日志](m2_r01_gate_reports_capture.log)（54.411s，后台命令真实EXIT=0；harmful报告`passed=false`，beneficial/neutral报告`passed=true`；两个金标明细路径均不存在）。
- R01 固定venv全量：[m2_r01_full_pytest.log](m2_r01_full_pytest.log)（146 passed/0 failed/0 skipped/0 errors/20 warnings/115.80s/EXIT=0）。告警为vector更名1次、Pydantic `.dict()`弃用15次、pandas两处`fillna`各2次；未屏蔽或升级依赖。
- R01 隔离与资产闭环：venv verify [前](m2_r01_venv_verify_before.json) / [后](m2_r01_venv_verify_after.json)及[末次日志](m2_r01_venv_verify_after.log)（PASS/0，pending/hard errors/business changes均为空）；外层业务快照 [before](m2_r01_outer_snapshot_before.json) / [after](m2_r01_outer_snapshot_after.json)及[after日志](m2_r01_outer_snapshot_after.log)（SHA256均`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`，逐项差异为空）。末次复核`tests/fixtures/golden_last_run.json`与三规则隔离`GOLDEN_DETAIL_FILE`均不存在。
- R01 审核独立复验：[审核复验汇总](m2_r01_audit_verify.json)、[真实双子进程三规则报告](m2_r01_audit_gate_reports.json)（53.920s/EXIT=0，harmful拒绝、beneficial/neutral通过）、venv verify [前](m2_r01_audit_venv_verify_before.json) / [后](m2_r01_audit_venv_verify_after.json)（均PASS/0）、外层业务快照 [before](m2_r01_audit_outer_snapshot_before.json) / [after](m2_r01_audit_outer_snapshot_after.json)（SHA256均为`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`，差异`[]`，两个`golden_last_run.json`路径均不存在）。

## V015-M3

R01审核退回，当前仅下发M3 R02；M4～M5未下发。原开发证据保留，以下新增审核证据单独绑定。

- R01本轮审核：[独立复验汇总](m3_r01_joint_audit2_summary.json)、[原216项全量](m3_r01_joint_audit2_full.log)、[真实离线三规则](m3_r01_joint_audit2_gate_reports.json)；70/58/30项、verify前后及快照路径见汇总。
- R01新增反例：[确认/导出与XLSX观察](m3_r01_joint_audit2_probes.json)、[精确规则接受及导出观察](m3_r01_joint_audit2_rule_probes.json)。4项P1详见[审核小结](../rounds/V015-M3_R01_审核小结.md)，观察脚本EXIT=0不表示业务验收通过。
- R01近同分：[独立三例诊断](m3_r01_joint_audit2_rm3_diag.log)、[M2→当前前后对照](m3_r01_joint_audit2_compare.json)；策略去向与防护见[统筹裁决](../rounds/V015-M3_R01_统筹裁决.md)。
- 审核收尾：[快照/交付绑定](m3_r01_joint_audit_closure.json)、[本轮文件与SHA256](m3_r01_joint_audit_delivery_hashes.md)。受限环境中止记录 `m3_r01_joint_audit_*` / `m3_r01_joint_audit2_probes.log` 不计通过；早先已有的 `m3_r01_audit_*` 保留但本轮不据此验收。

- R01 基线与交付：[改动前工作区哈希](m3_r01_pre_baseline_hashes.md)（表内实为52行，脚注"50"为笔误）、[精确交付文件清单/SHA256](m3_r01_delivery_hashes.md)、[开发小结](../rounds/V015-M3_R01_开发小结.md)、[开发接续交接说明](../rounds/V015-M3_R01_开发交接说明.md)。
- R01 前一开发会话原始留痕（保留不覆写）：[组合回归](m3_r01_required_regressions.log)、[全量](m3_r01_full_pytest.log)、[verify](m3_r01_venv_verify_after.json)、外层快照 [before](m3_r01_outer_snapshot_before.json) / [after](m3_r01_outer_snapshot_after.json)。
- R01 接续会话独立复跑（[执行脚本](m3_r01_rerun.ps1)）：[M3用例](m3_r01_rerun_m3_cases.log)（70 passed/EXIT=0）、[组合回归](m3_r01_rerun_required_regressions.log)（58 passed/EXIT=0）、[30项边界](m3_r01_compare_boundaries.log)（30 passed/5 deselected/EXIT=0）、[真实三规则报告](m3_r01_gate_reports.json) / [日志](m3_r01_gate_reports_capture.log) / [脚本](m3_r01_capture_evidence.py)（56.949s/EXIT=0，harmful拒绝、beneficial/neutral通过）、[固定venv全量](m3_r01_rerun_full_pytest.log)（216 passed/0 failed/0 skipped/0 errors/24 warnings/113.51s/EXIT=0）、verify [前](m3_r01_rerun_venv_verify_before.json) / [后](m3_r01_rerun_venv_verify_after.json)（PASS/0）、外层快照 [before](m3_r01_rerun_outer_snapshot_before.json) / [after](m3_r01_rerun_outer_snapshot_after.json)（均`5768bdca…5c7e`）。
- R01 改动前后对照：[对照结果](m3_r01_pre_post_compare.json) / [脚本](m3_r01_pre_post_compare.py)（M2三规则报告为前）；[分数变化诊断](m3_r01_score_shift_diag.log) / [脚本](m3_r01_score_shift_diag.py)。
- R02 基线、备份与交付：[R02改动前工作区哈希](m3_r02_pre_baseline_hashes.md)、[R02精确交付文件清单/SHA256](m3_r02_delivery_hashes.md)、[开发小结](../rounds/V015-M3_R02_开发小结.md)（R01交付副本保存在 `.local-archive/m3_r02_pre_backup/`）。
- R02 开发全套自验（[执行脚本](m3_r02_rerun.ps1)）：[M3用例](m3_r02_pytest_m3.txt)（87 passed/24 warnings/11.38s/EXIT=0）、[组合回归](m3_r02_pytest_m0_m2_regression.txt)（58 passed/16 warnings/14.03s/EXIT=0）、[30项边界](m3_r02_pytest_sandbox_30.txt)（30 passed/5 deselected/3 warnings/7.78s/EXIT=0）、[真实三规则报告](m3_r02_gate_reports.json) / [日志](m3_r02_3rule_gate.txt) / [脚本](m3_r02_capture_evidence.py)（50.894s/EXIT=0；harmful拒绝、beneficial/neutral通过）、[R01↔R02三规则逐例零差异对照](m3_r02_r01_gate_compare.json)（`score_comparison_diffs=[]`）、[固定venv全量](m3_r02_pytest_full.txt)（233 passed/0 failed/0 skipped/0 errors/43 warnings/103.34s/EXIT=0）、verify [前](m3_r02_venv_verify_before.json) / [后](m3_r02_venv_verify_after.json)（均PASS/0）、外层业务快照 [before](m3_r02_outer_snapshot_before.json) / [after](m3_r02_outer_snapshot_after.json)（均`5768bdca…5c7e`，`golden_last_run.json` 两个路径均 `False`）。
- R03 基线、备份与交付：[R03改动前工作区哈希](m3_r03_pre_baseline_hashes.md)、[R03精确交付文件清单/SHA256](m3_r03_delivery_hashes.md)、[开发小结](../rounds/V015-M3_R03_开发小结.md)（R02交付副本保存在 `.local-archive/m3_r03_pre_backup/`）。
- R03 开发全套自验（[执行脚本](m3_r03_rerun.ps1)）：[py_compile日志](m3_r03_py_compile.log)（EXIT=0）、[M3用例](m3_r03_pytest_m3.txt)（95 passed/40 warnings/11.75s/EXIT=0）、[组合回归](m3_r03_pytest_m0_m2_regression.txt)（58 passed/16 warnings/13.23s/EXIT=0）、[30项边界](m3_r03_pytest_sandbox_30.txt)（30 passed/5 deselected/3 warnings/7.38s/EXIT=0）、[真实三规则报告](m3_r03_gate_reports.json) / [日志](m3_r03_3rule_gate.txt) / [脚本](m3_r03_capture_evidence.py)（48.885s/EXIT=0；harmful拒绝、beneficial/neutral通过）、[R02↔R03三规则逐例零差异对照](m3_r03_r02_gate_compare.json)（`score_comparison_diffs=[]`）、[固定venv全量](m3_r03_pytest_full.txt)（241 passed/0 failed/0 skipped/0 errors/59 warnings/101.26s/EXIT=0）、verify [前](m3_r03_venv_verify_before.json) / [后](m3_r03_venv_verify_after.json)（均PASS/0）、外层业务快照 [before](m3_r03_outer_snapshot_before.json) / [after](m3_r03_outer_snapshot_after.json)（均`5768bdca…5c7e`，`golden_last_run.json` 两个路径均 `False`）。
- R04 基线、备份与交付：[R04改动前工作区哈希](m3_r04_pre_baseline_hashes.md)、[R04精确交付文件清单/SHA256](m3_r04_delivery_hashes.md)、[开发小结](../rounds/V015-M3_R04_开发小结.md)（R03交付副本保存在 `.local-archive/m3_r04_pre_backup/`）。
- R04 开发全套自验（[执行脚本](m3_r04_rerun.ps1)）：[py_compile日志](m3_r04_py_compile.log)（EXIT=0）、[M3用例](m3_r04_pytest_m3.txt)（99 passed/45 warnings/12.84s/EXIT=0）、[组合回归](m3_r04_pytest_m0_m2_regression.txt)（58 passed/16 warnings/13.32s/EXIT=0）、[30项边界](m3_r04_pytest_sandbox_30.txt)（30 passed/5 deselected/3 warnings/7.38s/EXIT=0）、[真实三规则报告](m3_r04_gate_reports.json) / [日志](m3_r04_3rule_gate.txt) / [脚本](m3_r04_capture_evidence.py)（48.875s/EXIT=0；harmful拒绝、beneficial/neutral通过）、[R03↔R04三规则逐例零差异对照](m3_r04_r03_gate_compare.json)（`score_comparison_diffs=[]`）、[固定venv全量](m3_r04_pytest_full.txt)（245 passed/0 failed/0 skipped/0 errors/64 warnings/103.33s/EXIT=0）、verify [前](m3_r04_venv_verify_before.json) / [后](m3_r04_venv_verify_after.json)（均PASS/0）、外层业务快照 [before](m3_r04_outer_snapshot_before.json) / [after](m3_r04_outer_snapshot_after.json)（均`5768bdca…5c7e`，`golden_last_run.json` 两个路径均 `False`）。
