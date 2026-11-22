# V015-M0 R03 统筹裁决交付哈希

- 日期：2026-11-13（Asia/Shanghai）；基线：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`；未提交、未push。
- 本轮精确范围：下表12个文件及本清单自身，共13个文件。本清单不记录自指哈希。
- 只修改管理文档和新增统筹证据；未修改应用/测试实现、历史开发/审核小结、已有证据或交付清单。
- 抽查证据在本轮管理更新前采集：R03清单25/28相符，差异仅为审核后维护的台账及两个索引；当前状态以下列哈希绑定。
- 实测：系统Python专项18 passed/0；venv verify PASS/0、6/6路径ok；两个子进程均由外层业务快照包住，差异[]。全量未重跑，保留审核的7 failed/2 errors，R04已下发。

| SHA256 | 文件 |
|---|---|
| 97e21b40e868f58fbf54bfc6d276444cdbce56e1585f3d4cd83516ae0c605ab3 | doc/00_项目管理/进度总表.md |
| b5b94690549f8c22f14946c8b88e17a0ef71bd27e0784f50a1fa08438ebbc253 | doc/00_项目管理/问题与版本归属总台账.md |
| 40012d0c130a6ca2b459bbdc5a6866d8aabe22040e47004e1ead2a687f56a593 | doc/V0.1.5/01_实施方案与任务包.md |
| d5c4e22bd14f68d52f82407852b9a63875f9d474da2f0119b935f270a12e178b | doc/V0.1.5/02_任务台账.md |
| 930a4f9a7e8da5bced65acbdd320b0da653e7bd8fba24feef9873099271c4b5c | doc/V0.1.5/03_终审与版本准入.md |
| d3610c5379f610eb2408139606e78185fccae0e90a52e44f1e6bb6b4cc95117f | doc/V0.1.5/rounds/V015-M0_R03_统筹裁决.md |
| 89df0188bd2add6ba88af2b7061ca35f050029bb5532099310c6bb8e2d379b2f | doc/V0.1.5/evidence/README.md |
| 4c075aac300f5b560a5a5a9d4a04b9aee31d9cb5b710c44114f814669e4aeaf3 | doc/V0.1.5/rounds/README.md |
| 03fb4d2ab4eca86ed07f1e97a18fc683680626ff337b5de00a68b95c9530c473 | doc/V0.1.5/evidence/m0_r03_coordinator_check.json |
| e419f1fc306afaac13204dac7c27ebb6be3226d151427829e2d4d567220bbf5c | doc/V0.1.5/evidence/m0_r03_coordinator_regression_smoke.log |
| e13721ec5b6ce724489ef59fac238d10a2ffce9c7be510d6e14e0e11e9cba6d0 | doc/V0.1.5/evidence/m0_r03_coordinator_venv_verify.json |
| 4689085fdbf5bcb7993ef8ea76962930947b413976750e07b5ba9ec81f01b74f | doc/V0.1.5/evidence/m0_r03_coordinator_venv_verify.log |
