# 实测证据

## V019-M3 / R01

- 五态复核、显式拒绝和编辑失效，Chrome 真实 DOM、草稿恢复和 XLSX 重读。[开发小结](../rounds/V019-M3_R01_开发小结.md) · [配对和导出](m3_r01_browser.json) · [DOM 截图](m3_r01_review_dom.png)
- F23 18 重请求、取消及超时回收，M2 ON BGE 单条复测。[并发](m3_r01_load.json) · [预算](m3_r01_load_exit.json) · [取消与超时](m3_r01_cancel.json) · [单条复测](m3_r01_m2_on.json)
- 冻结后一次性全量 1,220 和 Test 160，逐专业对账。[冻结](m3_r01_pre_holdout_freeze.json) · [对账](m3_r01_comparison.json) · [复验方法](m3_r01_reproduce.md)
- 规则门禁、全量回归与回退。[门禁退出码](m3_r01_gate_exits.json) · [全量日志](m3_r01_pytest.log) · [隔离和资产](m3_r01_asset_guard.json) · [交付哈希](m3_r01_delivery_sha256.json)

## V019-M2 / R01

- 匹配分与接受置信度联合校准，Train/Dev 策略筛选及 Wilson 区间。[开发小结](../rounds/V019-M2_R01_开发小结.md) · [阈值曲线](m2_r01_fit_report.json) · [模型复现](m2_r01_model_reproduction.json)
- 关键词与离线 BGE 四组真实 HTTP 配对，单条、上传、导出及人工决策复核。[对账](m2_r01_comparison.json) · [复验方法](m2_r01_reproduce.md)
- 原建议退化保护、规则门禁、回归与业务资产回退。[门禁初检](m2_r01_gate_on_initial.json) · [门禁退出码](m2_r01_gate_exits.json) · [全量日志](m2_r01_pytest.log) · [资产与回退](m2_r01_asset_guard.json) · [交付哈希](m2_r01_delivery_sha256.json)
- 独立审核及统筹整包放行证据（174/178 基线、45/46 交付+台账更新、144 项仓外专项回归、35 项快照零差、OFF/ON 门禁与三项观察裁决、M3 191 项含前端与 Dockerfile 基线）。[M2 审核小结](../rounds/V019-M2_R01_审核小结.md) · [M2 统筹放行证据](m2_r01_coord_release.json) · [M2 放行裁决](../rounds/V019-M2_R01_整包放行_统筹裁决.md) · [M3 下发任务书](../rounds/V019-M3_R01_统筹下发.md)

## V019-M1 / R01

- 冻结关键词与离线 BGE 候选，Train 拟合及 Dev 四档离线选型。[离线比较](m1_r01_offline_comparison.json) · [关键词候选](m1_r01_keyword_frozen.json.gz) · [BGE 候选](m1_r01_vector_frozen.json.gz) · [模型复现](m1_r01_model_reproduction.json)
- 六组 HTTP 预算、下游绑定逐条对账及关停复现。[HTTP 比较](m1_r01_http_comparison.json) · [复验方法](m1_r01_reproduce.md)
- 全量回归、五种配置规则门禁、隔离回退及冻结文件摘要。[全量日志](m1_r01_pytest.log) · [门禁退出码](m1_r01_gate_exits.json) · [资产与回退](m1_r01_asset_guard.json) · [交付哈希](m1_r01_delivery_sha256.json)

## V019-M0 / R01

V019-M0 / R01 的开工清单绑定 V0.1.8 冻结基线、业务快照及源码归档。开发小结记录本轮取舍和待复核差异。[开工清单](m0_r01_dispatch_baseline.json) · [开发小结](../rounds/V019-M0_R01_开发小结.md)

- 关键词与离线 BGE 六组 Train/Dev 回放、V0.1.7 同分片对照、分域指标和逐条绑定对账。[汇总](m0_r01_comparison.json) · [复验命令与口径](m0_r01_reproduce.md)
- 最终代码全量回归以及旧评分和校准评分的规则门禁。[全量日志](m0_r01_pytest_final.log) · [旧评分](m0_r01_gate_legacy.json) · [校准评分](m0_r01_gate_calibrated.json)
- 35 项业务快照对账、88 份源码回退验证及逐文件交付哈希。[隔离](m0_r01_isolation_post_rerun.json) · [资产与回退](m0_r01_asset_guard_final.json) · [交付哈希](m0_r01_delivery_sha256.json)
- 统筹独立复核与整包放行证据（172/174 基线、44/45 交付+台账更新、54 项仓外专项回归、前后隔离与 35 项快照零差、三项观察裁决与离线模型盘点）。[M0 统筹放行证据](m0_r01_coord_release.json) · [M0 放行裁决](../rounds/V019-M0_R01_整包放行_统筹裁决.md) · [M1 下发任务书](../rounds/V019-M1_R01_统筹下发.md)
- V019-M1 / R01 冻结候选 Top-20/50 线性与融合重排离线/HTTP 评测、五组门禁、454 项全量回归、独立审核及统筹整包放行证据（174/175 基线、41/42 交付+台账更新、90 项仓外专项回归、35 项快照零差、三项观察裁决）。[M1 开发小结](../rounds/V019-M1_R01_开发小结.md) · [M1 审核小结](../rounds/V019-M1_R01_审核小结.md) · [M1 统筹放行证据](m1_r01_coord_release.json) · [M1 放行裁决](../rounds/V019-M1_R01_整包放行_统筹裁决.md) · [M2 下发任务书](../rounds/V019-M2_R01_统筹下发.md)
