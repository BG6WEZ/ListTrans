# 武汉华康世纪洁净科技股份有限公司 · 物料清单匹配系统 V0.1.3
## 《数据飞轮（Data Flywheel）技术实现方案与架构设计》独立评审报告

> **被评审对象**：[data_flywheel_technical_proposal.md](file:///c:/Users/wangd/project/ListTrans/doc/V0.1.3/data_flywheel_technical_proposal.md)
> **评审方式**：方案条目与代码实际状态逐项交叉核验（覆盖 `app/`、`config/`、`web/`、`tests/`）
> **评审结论**：**不建议直接进入实施，需修订**
> **版本说明**：本文档所述 V0.1.3 为数据飞轮版本（注意历史上 V0.1.3 与 V0.1.2 版本号曾互换，不可与物料库改版方案混淆）

---

## 目录
- [零、总体判断](#零总体判断)
- [一、事实性错误（方案假设与代码不符）](#一事实性错误方案假设与代码不符)
- [二、硬技术错误](#二硬技术错误)
- [三、致命架构缺环：飞轮只有“进”没有“验”](#三致命架构缺环飞轮只有进没有验)
- [四、数据模型问题](#四数据模型问题)
- [五、算法问题](#五算法问题)
- [六、方案完全遗漏的既有系统](#六方案完全遗漏的既有系统)
- [七、其他缺失项](#七其他缺失项)
- [八、工期评估](#八工期评估)
- [九、修订建议清单（按优先级）](#九修订建议清单按优先级)
- [十、评审结论与放行条件](#十评审结论与放行条件)

---

## 零、总体判断

方案的**问题定位准确**（反馈数据零散、缺乏自动聚合、缺少审批与热加载闭环），**三级知识沉淀的思路方向正确**，整体架构图与数据流描述清晰，具备作为顶层设计的价值。

但作为**可执行的技术实施方案**，存在以下阻塞性问题：

| 类别 | 数量 | 严重度 |
| :--- | :--- | :--- |
| 事实性错误（假设与代码不符） | 6 处 | 高 |
| 硬技术错误（方案不可运行） | 1 处 | 高 |
| 致命架构缺环（缺少效果验证闭环） | 1 处 | **最高** |
| 数据模型缺陷 | 7 项 | 中—高 |
| 算法设计缺陷 | 4 项 | 中—高 |
| 遗漏的既有系统 | 1 套 | 高 |
| 其他缺失项 | 4 项 | 中 |

---

## 一、事实性错误（方案假设与代码不符）

### 1.1 `config/llm_rules.json` 不存在（严重）

方案 6.1 通道② 与第二节流程图（第 74 行）均基于"文件型 LLM 规则库 `config/llm_rules.json`"。

**实际情况**：该文件不存在。LLM 规则存储于 **`app/materials.db` 的 `llm_rules` 表**，建表见 [llm_rule_engine.py](file:///c:/Users/wangd/project/ListTrans/app/llm_rule_engine.py#L64-L87)，且**已存在一套完整的规则审批体系**：

- 状态机：`draft → pending_approval → approved / rejected / deprecated / disabled`
- 审计表：`rule_audit_logs`（[llm_rule_engine.py](file:///c:/Users/wangd/project/ListTrans/app/llm_rule_engine.py#L90-L101)）
- 热重载入口：[clear_active_cache()](file:///c:/Users/wangd/project/ListTrans/app/llm_rule_engine.py#L549-L552)，**不是** `reload_*` 系列函数
- 管理 API：已在 [rule_api.py](file:///c:/Users/wangd/project/ListTrans/app/rule_api.py) 的 `/api/llm-rules` 前缀下

方案 3.2 设计的 `feedback_rule_suggestions` 表（含 `status` / `reviewer_id` / `reviewed_at`）与既有 `llm_rules` 表**高度同构**。若按方案原样实施，将产生**第二套并行的规则审批体系**，审批入口、状态机、审计日志全部重复。

> 错误溯源：`README.md:153` 提到 `config/llm_rules.json`，属陈旧文档描述；方案沿用了这一错误。同理 `config/llm_providers.json` 也不存在。

### 1.2 独立 `feedback.db` 是错误选择

方案 3 开头写"或纳入主库表结构"，但 9.1 又专门要求给 `feedback.db` 启用 WAL。

**实际情况**：`materials.db` **早已启用 WAL**，见 [db_manager.py](file:///c:/Users/wangd/project/ListTrans/app/db_manager.py#L26-L28) 与 [db_manager.py](file:///c:/Users/wangd/project/ListTrans/app/db_manager.py#L41-L43)（磁盘上 `materials.db-wal` / `-shm` 已存在，可佐证生效）。`llm_rule_engine.py`、`auth_manager.py` 亦同此模式。

**独立库的代价**：
1. 挖掘引擎无法 JOIN `materials` 表 —— 而 5.1 节的聚合恰恰需要标准物料名/分类
2. 无法复用既有 `rule_audit_logs` 审计表
3. 多一套连接管理与 PRAGMA 配置（项目内已有 `llm_client.py:86/143/160`、`vector_index.py:403` 未设 WAL 的裸连接，属同类历史债）
4. 新增孤儿库风险（项目内已存在无任何代码引用的 `app/sessions.db`）

**结论**：应明确**并入 `materials.db`**，9.1 节关于 WAL 的表述应改为"复用既有 WAL 配置"。

### 1.3 `/api/feedback/suggestions` 路径已被占用

方案第七节规划 `GET /api/feedback/suggestions`（权限：管理员）。

**实际情况**：该路径已存在于 [server.py](file:///c:/Users/wangd/project/ListTrans/app/server.py#L1522-L1527)：
- 权限为 `require_employee`（**非管理员**）
- 返回结构为 `{suggestions: [{suggested_typo, suggested_correction, confidence_count}]}`
- 前端渲染逻辑在 [app.js](file:///c:/Users/wangd/project/ListTrans/web/app.js#L2064-L2103)

改为管理员权限 + 新数据结构会**直接打断现有前端**。方案第七节未提任何迁移或兼容策略。建议新接口统一走 `/api/flywheel/*` 前缀。

### 1.4 热重载调用链不完整

方案 6.1 通道① 称"调用 `reload_preprocessor_configs()` 和 `reload_normalizer_configs()` 内存热重载"。

**实际情况**：

| 函数 | 定义位置 | 被调用情况 |
| :--- | :--- | :--- |
| `reload_preprocessor_configs` | [preprocessor.py:61](file:///c:/Users/wangd/project/ListTrans/app/preprocessor.py#L61-L67) | 已在 `/api/config/save` 调用 |
| `reload_matcher_configs` | [matcher.py:77](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L77-L83) | 已在 `/api/config/save` 调用 |
| `reload_normalizer_configs` | [normalizer.py:165](file:///c:/Users/wangd/project/ListTrans/app/normalizer.py#L165-L172) | **全项目零调用** |
| `reload_extractor_configs` | [domain_extractors.py:790](file:///c:/Users/wangd/project/ListTrans/app/domain_extractors.py#L790-L794) | **全项目零调用** |
| `reload_router_configs` | [domain_router.py:291](file:///c:/Users/wangd/project/ListTrans/app/domain_router.py#L291-L296) | **全项目零调用** |

唯一调用点是 [server.py:1272-1273](file:///c:/Users/wangd/project/ListTrans/app/server.py#L1272-L1273)（`/api/config/save`），只调用了 preprocessor + matcher 两个。

**这意味着当前系统存在一个既有缺陷**：保存 `alias_dict.json` 后，`preprocessor.alias_dict` 虽已更新，但真正执行别名替换的 `normalizer.py` 预编译正则 `alias_rx` / `alias_map`（[normalizer.py:45](file:///c:/Users/wangd/project/ListTrans/app/normalizer.py#L45)、[normalizer.py:120-121](file:///c:/Users/wangd/project/ListTrans/app/normalizer.py#L120-L121)）**不会刷新，需重启进程才生效**。

方案把这个既有 bug 当成了可用能力。此外：
- **多 worker 部署下，内存热重载只作用于当前进程**。`LLMRuleManager._active_rules_cache`（[llm_rule_engine.py:546](file:///c:/Users/wangd/project/ListTrans/app/llm_rule_engine.py#L546)）为进程内缓存，审批后无法跨进程失效。方案第九节"并发保障"完全未涉及。

### 1.5 "Top-3" 与代码实际不符

方案全篇（3.1 `auto_candidates_json`、4 节行为表、6.1 通道③）按 Top3 描述。

**实际情况**：[matcher.py:1826](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L1826) 返回 `scored_candidates[:10]`（注释明确写 "Output Top 10 for detailed view"）；前端 [renderCandidates](file:///c:/Users/wangd/project/ListTrans/web/app.js#L1336-L1401) 直接遍历全部，无截断。`index.html:245` 的"推荐匹配候选 (Top 3)"只是**标题文字**。

`auto_candidates_json` 究竟落库几条、存哪些字段必须明确 —— 直接影响存储容量（见 4 节）。

### 1.6 四类 `action_type` 在现有前端无法区分

方案第四节表格假设 `PICK_CANDIDATE`（点候选卡）与 `MANUAL_REPLACE`（全库搜索替换）是两个可区分的入口。

**实际情况**：两者**共用同一个函数** [applyMatchCandidate](file:///c:/Users/wangd/project/ListTrans/web/app.js#L1404-L1452)：
- 候选卡片点击 → `app.js:1395` 调 `applyMatchCandidate(cand)`
- 全库搜索"套用"按钮 → `app.js:1511` 也调 `applyMatchCandidate(...)`

且该函数语义是**追加绑定 / toggle 解绑**（`app.js:1412-1424` 已存在则 splice 解绑，`app.js:1434-1442` 否则 push），**项目里根本不存在"替换"这个操作**（与 `index.html:246` 文案"点击卡片即可直接套用并覆盖该物料"也矛盾）。

**连带影响**：方案 3.1 的 `final_target_code` 是**单值字段**，无法表达一对多绑定结果。

另：`CONFIRM_TOP1` 目前的载体是 [app.js:565-624](file:///c:/Users/wangd/project/ListTrans/web/app.js#L565-L624) 的**匿名内联回调**（无具名函数，无法外部复用），且它用 `selectedItem.is_exact = true`（`app.js:590`）兼作"已确认"标记 —— 该字段本是匹配置信标志（`score >= 75` 阈值，[matcher.py:1819](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L1819)），语义已被污染，并连带影响 `updateProgress()`（`app.js:1858-1863`）的统计口径。

---

## 二、硬技术错误

### 2.1 `navigator.sendBeacon` 无法携带 `Authorization` 头

方案第四节"性能保障"建议"前端上报使用 `navigator.sendBeacon` 或异步 `fetch`"。

**问题**：项目鉴权依赖[全局 fetch 拦截器注入 Bearer token](file:///c:/Users/wangd/project/ListTrans/web/app.js#L16-L26)。`sendBeacon` **不支持自定义 HTTP header**，请求将因缺少 `Authorization` 而被 401 拒绝。

**修正**：改为 `fetch(url, { method: 'POST', keepalive: true, ... })`，既能走现有拦截器注入 token，又能在页面卸载时保持请求。

### 2.2 前端无法获取 `user_id`

方案 3.1 要求上报 `user_id`（NOT NULL）与 `user_name`。

**实际情况**：
- JWT payload 只有 `{sub: username, role}`（[server.py:205](file:///c:/Users/wangd/project/ListTrans/app/server.py#L205)）
- login 响应体（`server.py:230-242`）与 `/api/auth/me`（`server.py:244-258`）**都不下发 `id`**
- 前端 localStorage 只有 `token / username / name / position / role`（[login.html:87-91](file:///c:/Users/wangd/project/ListTrans/web/login.html#L87-L91)）
- 而服务端内部草稿/会话是按 `current_user["id"]` 存的（`server.py:262`、`server.py:274`）

**且由前端上报 user_id 本身不安全（可篡改）**。应由后端从 token 反查后自行落库。但 `/api/binding/submit`（`server.py:1302`）与 `/api/match`（`server.py:1170`）当前签名里都**没有 `current_user`**（权限挂在装饰器 `dependencies=[...]` 上），需先改造签名为 `current_user: dict = Depends(require_employee)`。

---

## 三、致命架构缺环：飞轮只有"进"没有"验"

**这是本方案最严重的问题。**

方案的采纳链路是：`挖掘建议 → 管理员点击采纳 → 写回配置 → 热重载生效`（见 2 节流程图 `F2 → G1 → G3` 与 6.1）。

**全链路没有任何"采纳前影响评估"环节。**

一条别名映射或 `must_not_contain` 硬门禁被采纳后**直接热加载生效**。若该规则有害：

```
错误规则生效 → 污染后续全部匹配结果 → 用户产生更多纠错操作
    → 挖掘引擎从被污染的数据中挖出更多错误规则 → 再次采纳 → ...
```

这是**自我强化的负反馈回路**，而非"飞轮"。方案 1.2 提出的"高质量数据回流"目标无法保证。

### 3.1 项目已有现成的回归基础设施，方案未接入

- `tests/eval_golden_set.py`（黄金集评测）
- `tests/fixtures/golden_baseline.json` / `golden_last_run.json` / `golden_set_v1.json` / `ground_truth_500.json`
- `tests/regression_runner.py`（回归执行器）
- `app/test_engine.py`
- `doc/golden_set/黄金测试集与自动化回归评估建立说明.md`

**采纳链路必须强制接入**：

```
管理员点击采纳
  → 沙箱环境应用候选规则（不影响生产内存态）
  → 自动执行黄金集回归
  → 看板展示指标 Δ（准确率 / Top1 命中率 / 召回率变化）
  → 指标不退化 → 允许提交生效
  → 指标退化 → 阻断并给出退化用例明细
  → 生效后保留变更快照，支持一键回滚
```

### 3.2 通道③ 存在数据泄漏（Data Leakage）

方案 6.1 通道③ 让飞轮**自动扩充 Golden 评测集**（流程图 `E3`），同时又用 Golden 集评测飞轮产出的规则效果 —— **训练数据与评测数据同源**，指标必然虚高，评测失去意义。

**修正**：飞轮自动生成的用例必须
1. 打独立来源标记（如 `source = 'flywheel_auto'`）
2. 与人工构建的基线集**物理隔离**
3. 经人工抽检确认后，才允许并入 `golden_baseline.json`

---

## 四、数据模型问题

针对方案 3.1 `feedback_records` 与 3.2 `feedback_rule_suggestions` 表设计：

| # | 问题 | 说明 | 建议 |
| :--- | :--- | :--- | :--- |
| 1 | **无幂等键** | `keepalive` fetch 在网络抖动/页面卸载时可能重发，重复记录会直接虚增 `support_count`，污染门禁判定 | 增加 `client_event_id TEXT UNIQUE`，服务端 `INSERT OR IGNORE` |
| 2 | **无归一化列** | 5.1 要求按"清洗后的 raw_name"分组，但表中只存 `raw_name`。每次挖掘重跑 normalizer 成本高，且**配置变更后历史数据的分组结果会漂移**，导致 Consistency 不可复现 | 落库时同步写入 `raw_name_norm` |
| 3 | **无算法/配置版本快照** | 规则热重载后，历史样本的 `auto_top1_*` 不再代表当前系统行为。挖掘引擎基于**过期预测轨迹**统计，会重复产出已修复问题的建议 | 增加 `algo_version` / `config_version` 字段，挖掘时按版本窗口筛选 |
| 4 | **无终态语义** | 用户可能反复修改同一行（`CONFIRM_TOP1` 后又改成 `MANUAL_REPLACE`）。中间态会被当作正样本采集，形成矛盾数据 | 增加 `superseded INTEGER DEFAULT 0` 标记，或挖掘时按 `(session_id, row_idx)` 只取时间最新的终态记录 |
| 5 | **`final_target_code` 单值** | 无法表达一对多绑定（现有 `applyMatchCandidate` 是追加绑定，`matched_code` 实际是逗号拼接串，见 `app.js:1445`） | 改为 `final_targets_json TEXT`，保留 `code + qty_ratio` 列表 |
| 6 | **`is_processed` 与 Consistency 语义矛盾** | Consistency 的分母需要该原始词的**历史全量**替换次数；而 `is_processed` 是增量处理语义。仅扫描未处理记录会得出错误的一致性比率 | 两阶段：增量扫描仅用于**发现候选 key**，再对候选 key 做**全量统计** |
| 7 | **容量未测算** | 每清单行一条记录，含候选 JSON。若按实际 Top10 落库，单条约 2–4 KB；一次 2000 行清单即产生约 8 MB。高频使用下 SQLite 单表将快速膨胀，且 3.1 的四个索引进一步放大写放大 | 明确只存 Top3 精简字段（`code` / `name` / `score`）；或候选独立分表；并制定归档策略 |

**补充**：`feedback_rule_suggestions` 表缺少
- `distinct_users`（门禁必需，见 5.2）
- `discipline` 默认值 `'通用'` 与既有 `llm_rules.discipline` 取值空间未对齐
- 缺少与既有 `llm_rules` 的关联外键（采纳后生成的规则 ID），无法追溯"建议 → 规则"链路

---

## 五、算法问题

### 5.1 别名挖掘的核心难点被绕过了（最关键）

方案 5.1 按 `(清洗后的 raw_name, final_target_name)` 分组，产出的是**整名 → 整名**映射。

**问题**：`alias_dict` 在 [normalizer.py:40-43](file:///c:/Users/wangd/project/ListTrans/app/normalizer.py#L40-L43) 是按 key 长度倒序进行**全局子串替换**（`re.escape` + `IGNORECASE`）。把整条规格描述当 key 写入：
1. 基本不会命中（真实清单文本极少完全一致）
2. 一旦命中则有污染全库的风险

旧实现的 `raw_clean[:20]` 硬截断（[feedback_train.py:72](file:///c:/Users/wangd/project/ListTrans/app/feedback_train.py#L72)）正是这个难点的产物 —— 它会生成 `"1.类型：不锈钢止回阀\n2.材质："` 这类**完全不可用的 key**。而现有前端埋点上报的 `raw_text` 传的实际是 `original_spec`（规格全文，见 `app.js:1428`），不是名称，进一步放大了问题。

**方案没有解决这个难点，只是换了一套门禁阈值。**

**必须补充的算法**：
1. 词粒度 diff / 最小编辑片段提取（从 `raw_name_norm` 与 `final_target_name` 中定位真正差异的**词片段**）
2. 候选 key 长度与词性约束（拒绝超长、拒绝纯数字/单位串）
3. **环检测与冲突检测**（写入前校验）

**前置数据治理**：当前 `config/alias_dict.json`（470 条）已存在
- **循环映射**：`"Y型过滤器" → "铜质Y型过滤器"`（第 449 行）与 `"铜质Y型过滤器" → "Y型过滤器"`（第 345 行）互相指向；`"医疗设备带" ↔ "设备带"`（第 234 / 56 行）同理
- **重复键且值冲突**（JSON 解析静默丢弃前者）：`"医用静音型排风机"`（第 300 / 469 行）、`"防爆型医用静音型排风机"`（第 301 / 471 行）
- **恒等映射**（纯冗余）：第 80、253、256、257 行
- **疑似数据污染**：第 141 行 `"铝塑板" → "不锈钢压边条"`、第 176 行 `"电力电缆头" → "排风机控制盒"`、第 402 行 `"UPVC球阀" → "WDZC-BVR 6"`
- 值中含换行符：第 197–198 行

**飞轮上线前应先完成一次别名词典去重与环检测**，否则会在已污染的基础上继续叠加。

### 5.2 门禁阈值形同虚设

方案 5.1 第 3 点：`Support ≥ 2 且 Consistency ≥ 80%`。

**问题**：当 Support = 2 时，Consistency 的可能取值只有 `1.0` 或 `0.5` 两种，80% 门禁**等价于"必须两次都一致"**。实际效果是：**同一个人连续做 2 次相同误操作即可生成待审规则**。

方案完全缺少 `distinct_users` 约束，无法区分"多人共识"与"单人惯性误操作"。

**建议门禁**：

```
support_count >= 3
  AND distinct_users >= 2
  AND consistency >= 0.8
```

高置信档（默认勾选）建议：`support >= 8 AND distinct_users >= 3 AND consistency >= 0.95`。

### 5.3 负向约束挖掘（5.2 节）只有一句话，但风险最高

`must_not_contain` 是**硬门禁**（见 [llm_rule_engine.py:467](file:///c:/Users/wangd/project/ListTrans/app/llm_rule_engine.py#L467)），误采纳会**直接把正确物料从召回结果中排除**，是全方案破坏力最大的动作。

但方案 5.2 全节仅一句自然语言描述，缺失：
- 算法（"铝"这个关键词如何从候选物料文本中被自动定位提取？）
- Scope 界定（规则作用于全局？category？discipline？`llm_rules.scope_level` 有 `category` 等取值需对齐）
- 与既有 `boost_rules.json` / `tolerance_rules.json` / `weight_rules.json` 的优先级与冲突关系
- 误杀检测（该词是否在其他正确物料中也出现？）

**建议**：Phase 1 **不实施此通道**；或降级为"仅产出分析报告供人工参考，不产出可一键采纳的规则"。

### 5.4 Hard Negative 质量未过滤

方案 6.1 通道③ 直接用"系统当时错误推荐但被用户驳回的首选项"作为 Hard Negative。

**问题**：若用户是因**规格差异**换选（同名不同规格，如 DN80 vs DN100），`auto_top1` 与 `final_target` 的文本几乎一致。这类样本作为对比学习的 negative 是**噪声**，会损害向量模型表征。

**修正**：按 `auto_top1` 与 `final_target` 的文本相似度设上限阈值过滤；或按 `action_type` 区分（`PARAM_ADJUST` 类样本不产出三元组）。

---

## 六、方案完全遗漏的既有系统

### 6.1 多物料绑定学习（第二条已上线的飞轮）

系统中已存在一条**完整运行的反馈—挖掘—采纳链路**，方案完全未提及：

| 环节 | 位置 |
| :--- | :--- |
| 采集 | `POST /api/binding/submit`（[server.py:1301-1342](file:///c:/Users/wangd/project/ListTrans/app/server.py#L1301-L1342)） |
| 存储 | `app/binding_feedback.json` |
| 挖掘 | `GET /api/binding/suggestions`（[server.py:1344-1400](file:///c:/Users/wangd/project/ListTrans/app/server.py#L1344-L1400)） |
| 采纳 | `POST /api/binding/rules/confirm`（`server.py:1402-1438`） |
| 生效 | `config/auto_binding_rules.json` → `batch_match_rows()`（`server.py:918-919`、`1041-1079`） |
| 测试 | `tests/test_auto_binding.py` |

方案的三级沉淀通道（alias_dict / llm_rules / 训练资产）中**没有它的位置**。若按方案实施，系统将同时存在**三套并行的反馈体系**（JSON 版绑定飞轮 + JSON 版纠错样本 + DB 版新飞轮）。

**建议**：将绑定学习纳为 `suggestion_type = 'BINDING_RULE'`，统一进 `feedback_rule_suggestions` 的状态机与审批看板。

### 6.2 该绑定看板目前已损坏，方案未识别

- [index.html:402-424](file:///c:/Users/wangd/project/ListTrans/web/index.html#L402-L424) 的 Card 5.5 **HTML 结构残缺**：`<table>` 内出现游离 `<tr>` 与无起始标签的 `</tbody>`
- **`binding-suggestions-tbody` 与 `binding-rules-tbody` 两个元素根本不存在**
- 而 [app.js:2108-2117](file:///c:/Users/wangd/project/ListTrans/web/app.js#L2108-L2117) 仍在 `getElementById` 取这两个 id，随后对 `null` 赋 `innerHTML` 必抛异常，被 `app.js:2193` 的 catch **静默吞掉**

**结论：绑定规则的采纳/删除功能目前在 UI 上完全不可用**（`config/auto_binding_rules.json` 为空数组，与此吻合）。这是方案实施前必须修复的阻塞项。

### 6.3 既有历史数据具备复用价值，方案未提迁移

`app/feedback_samples.json` 现有 **114 条样本（106 条负样本 + 8 条正样本）**，其 `auto_code` 字段恰好保留了"系统当时错误推荐的首选项"，可直接作为方案 6.1 通道③ 对比学习三元组的**初始种子数据**。

注意：这些记录**无时间戳、无用户身份**，迁移后只能作为匿名历史数据，不可参与 `distinct_users` 门禁计算。

### 6.4 既有基础设施可直接复用（方案重复建设）

| 方案拟新建 | 既有可复用 |
| :--- | :--- |
| `feedback_rule_suggestions` 状态机 | `llm_rules.status` 状态机（[llm_rule_engine.py:754-816](file:///c:/Users/wangd/project/ListTrans/app/llm_rule_engine.py#L754-L816)） |
| 规则审批 API | `rule_api.py` `/api/llm-rules/audit`（`require_manager`） |
| 审计追溯（9.3 节） | `log_audit_event`（已用于 `server.py:1428`、`1447`） |
| `feedback.db` WAL | `materials.db` 已启用 WAL |

---

## 七、其他缺失项

### 7.1 无回滚机制（高）
`/api/config/save` 是**整文件覆盖写、无备份、无并发锁、无 schema 校验**（[server.py:1269-1270](file:///c:/Users/wangd/project/ListTrans/app/server.py#L1269-L1270)）。飞轮自动写回 `alias_dict.json` 后**无法撤销**。必须增加变更快照表与一键回滚。

### 7.2 挖掘任务调度归属未定（中）
方案第三节标题写"离线/定时数据挖掘引擎"，但第七节只提供了 `POST /api/feedback/miner/run` 手动触发接口。**定时调度的技术选型缺失**（APScheduler？`lifespan` 内 asyncio task？外部 cron？），且未说明：
- 多 worker 下如何避免重复执行（需分布式锁或单实例约束）
- 长任务是否阻塞事件循环（`get_feedback_suggestions` 现有实现存在 N+1 查库问题，见 `feedback_train.py:57-63`）

### 7.3 权限未收紧（中—高）
- 现有 `/api/binding/rules/confirm` 与 `/api/binding/rules/delete` 会**直接写/删全局生效的配置文件**，却只需最低的 `require_employee` 权限（越权隐患）。对比 `/api/config/save` 要求 `require_admin`、`/api/config/load` 要求 `require_manager`，权限设计明显不一致。
- `/api/feedback/suggestions` 与 `/api/binding/suggestions` 当前对全体员工暴露全公司纠错数据。
- 注意 `AUTH_DEV_MODE=true` 时无 token 会被直接伪装为 admin（[auth_middleware.py:7,15-16](file:///c:/Users/wangd/project/ListTrans/app/auth_middleware.py#L7)），生产部署需确认该开关状态。

**建议**：飞轮全部写操作接口统一 `require_admin`，全部接入 `log_audit_event`。

### 7.4 无数据保留与治理策略（中）
方案 9.3 只提审计合规，缺少：
- 保留期限 / 归档 / 清理策略（结合 4 节容量问题）
- `DELETE /api/system/reset` 当前会清空 `binding_feedback.json` 但**不清 `feedback_samples.json`**，新表需明确纳入或排除
- `.gitignore` 未忽略 `feedback_samples.json` / `binding_feedback.json`，运行时数据目前被纳入版本控制；迁移到 `.db` 后可由 `.gitignore:11-17`（`app/*.db`）自动覆盖

### 7.5 无统一 schema 迁移机制（中）
项目**无 Alembic 等迁移框架**，`materials_metadata.schema_version` 只写不读（`db_manager.py:789`），schema 失配的兜底是 **DROP + Excel 全量重导**（`db_manager.py:78-80`、`90`）—— 若飞轮表建在 `materials.db` 中，需确认不会被该兜底逻辑波及。唯一可参考的幂等列迁移范式见 `auth_manager.py:74-90`（基于 `PRAGMA table_info`）。

---

## 八、工期评估

方案第八节给出 3 天（1 + 1.5 + 0.5）。**严重低估。**

依据：
1. 仅"从长规格描述文本中提炼可用别名片段"（5.1 的真正难点）就不是半天量级的工作
2. 采纳前黄金集回归验证（第三节缺环）为方案未计入的新增工作量
3. 实施前需要一个 **Phase 0** 修复三个阻塞性既有缺陷（`reload_normalizer_configs` 未接线、绑定看板 DOM 残缺、`user_id` 不可得）
4. 前端需先重构 `applyMatchCandidate` 以拆分行为类型，涉及绑定/解绑核心逻辑，需回归测试

建议按"阶段可独立验收"重排，不给绝对天数承诺，改为以检查点门禁放行。

---

## 九、修订建议清单（按优先级）

### Phase 0 — 前置缺陷修复（阻塞项，必须先做）

1. 在 `/api/config/save` 补齐 `reload_normalizer_configs()` / `reload_extractor_configs()` / `reload_router_configs()` 调用，修复"改别名词典不生效"的既有缺陷
2. 修复 [index.html](file:///c:/Users/wangd/project/ListTrans/web/index.html#L402-L424) Card 5.5 的 DOM 结构残缺，恢复绑定规则看板可用性
3. 用户身份贯通：login / `/api/auth/me` 下发用户 id，**或**改由后端从 token 反查注入（**推荐后者**，避免前端篡改）；同时为 `/api/binding/submit`、`/api/match` 补 `current_user` 参数
4. `config/alias_dict.json` 数据治理：去重复键、消除循环映射、清理恒等映射、清查疑似污染条目

### Phase 1 — 采集层（可独立验收）

5. **表并入 `materials.db`**，删除独立 `feedback.db` 设计；9.1 改为复用既有 WAL
6. `feedback_records` 补字段：`raw_name_norm`、`algo_version`、`config_version`、`client_event_id (UNIQUE)`、`superseded`；`final_target_code` 改 `final_targets_json`
7. 新 API 统一走 `/api/flywheel/*` 前缀，避免与既有 `/api/feedback/suggestions` 冲突；写操作统一 `require_admin` 并接 `log_audit_event`
8. 前端上报改 `fetch(keepalive: true)`（禁用 sendBeacon）；先拆分 `applyMatchCandidate` 以区分 `PICK_CANDIDATE` / `MANUAL_REPLACE`
9. 明确 `auto_candidates_json` 只存 Top3 精简字段（`code` / `name` / `score`），控制存储膨胀
10. 迁移 `feedback_samples.json` 现有 114 条历史样本（标记为匿名历史数据）

**检查点 1**：操作流水精准入库，幂等生效（重复上报不增计数），`user_id` 由后端可靠注入

### Phase 2 — 挖掘与审批（核心）

11. 别名挖掘补齐词粒度 diff / 最小编辑片段提取算法 + 写入前环检测与冲突检测
12. 门禁改为 `support >= 3 AND distinct_users >= 2 AND consistency >= 0.8`；`feedback_rule_suggestions` 补 `distinct_users` 字段
13. suggestion 表对齐/复用 `llm_rules` 状态机与 `rule_audit_logs`；补"建议 → 生成规则 ID"关联字段
14. 绑定学习纳入 `suggestion_type = 'BINDING_RULE'`，收敛为单一体系
15. **采纳链路强制接入黄金集回归验证 + 变更快照表 + 一键回滚**（补齐第三节缺环）
16. 明确多 worker 下的跨进程缓存失效方案（或明确约束单实例部署）
17. 明确挖掘任务调度选型与防重复执行机制

**检查点 2**：模拟多用户纠错数据，验证门禁正确拦截单人误操作；验证有害规则被回归门禁阻断；验证一键回滚

### Phase 3 — 数据资产（可延后）

18. 负向约束挖掘 —— **先只产出分析报告，不产出可一键采纳的规则**
19. Hard Negative 按相似度阈值过滤后再产出三元组
20. Golden 集扩充：自动用例打 `source = 'flywheel_auto'` 标记并与人工基线物理隔离，经人工抽检后方可并入基线

**检查点 3**：全链路闭环 + 评测数据无泄漏证明 + V0.1.3 验收报告

---

## 十、评审结论与放行条件

**结论：方案方向正确，但当前版本不具备实施条件，需修订后重新评审。**

### 必须修订项（P0，不修订不予放行）

| # | 修订内容 | 对应章节 |
| :--- | :--- | :--- |
| 1 | 删除 `config/llm_rules.json` 相关设计，改为对接 `materials.db` 的 `llm_rules` 表 + `LLMRuleManager.clear_active_cache()` | 1.1 |
| 2 | 明确表并入 `materials.db`，删除独立 `feedback.db` 与重复 WAL 设计 | 1.2 |
| 3 | API 路径避免与既有 `/api/feedback/suggestions` 冲突 | 1.3 |
| 4 | 补齐完整热重载调用链，并说明多 worker 场景 | 1.4 |
| 5 | 移除 `navigator.sendBeacon` 建议，改 `fetch(keepalive)` | 2.1 |
| 6 | 明确 `user_id` 由后端注入，不由前端上报 | 2.2 |
| 7 | **增加"采纳前黄金集回归验证 + 回滚"环节** | 三 |
| 8 | 解决 Golden 集数据泄漏 | 3.2 |
| 9 | 补齐别名片段提取算法（不能只给整名映射） | 5.1 |
| 10 | 门禁增加 `distinct_users` 约束 | 5.2 |
| 11 | 纳入既有多物料绑定学习体系，避免三套并行 | 6.1 |
| 12 | 增加 Phase 0 前置缺陷修复阶段 | 九 |

### 建议修订项（P1）

- 数据模型 7 项补充（第四节表格）
- `must_not_contain` 通道降级或延后（5.3）
- Hard Negative 噪声过滤（5.4）
- 权限统一收紧至 `require_admin` + 审计（7.3）
- 数据保留与归档策略（7.4）
- 挖掘任务调度选型（7.2）
- 工期重排为检查点门禁制（八）

---

> **评审说明**：本报告仅作技术评审结论，未对生产代码做任何修改。方案修订完成后可提交复评。
