# 武汉华康世纪洁净科技股份有限公司 · 物料清单匹配系统 V0.1.3
## 《数据飞轮技术实现方案 Rev 2.0》独立复评报告（第二轮）

> **被评审对象**：[data_flywheel_technical_proposal.md](file:///c:/Users/wangd/project/ListTrans/doc/V0.1.3/data_flywheel_technical_proposal.md)（Rev 2.0 修订版）
> **前置评审**：[data_flywheel_technical_proposal_review.md](file:///c:/Users/wangd/project/ListTrans/doc/V0.1.3/data_flywheel_technical_proposal_review.md)（第一轮，针对 Rev 1.0）
> **评审方式**：Rev 2.0 条目与代码实际状态逐项交叉核验，并重点验证新引入机制（沙箱黄金集回归）的技术可行性
> **复评结论**：**方向已正确，实质性进步；但 7.2 沙箱回归需重新设计后方可实施**
> **版本说明**：本文档所述 V0.1.3 为数据飞轮版本（注意历史上 V0.1.3 与 V0.1.2 版本号曾互换，不可与物料库改版方案混淆）

---

## 目录
- [零、总体判断](#零总体判断)
- [一、阻塞项：7.2 沙箱回归在当前架构下不可实现](#一阻塞项72-沙箱回归在当前架构下不可实现)
- [二、第一轮 P0 闭环情况核对](#二第一轮-p0-闭环情况核对)
- [三、新增问题（Rev 2.0 引入或仍存留）](#三新增问题rev-20-引入或仍存留)
- [四、遗留与新增缺失项](#四遗留与新增缺失项)
- [五、复评结论与放行条件](#五复评结论与放行条件)

---

## 零、总体判断

**Rev 2.0 是一次高质量修订。** 第一轮提出的 12 项 P0 中，**10 项已正确闭环**，包括最关键的几项：

- `config/llm_rules.json` 幻象已纠正为 `materials.db` 的 `llm_rules` 表
- 独立 `feedback.db` 已并入 `materials.db`
- `navigator.sendBeacon` 已明确禁用并说明原因
- `user_id` 已改为服务端从 JWT 注入
- `distinct_users` 多人共识门禁已加入
- 多物料绑定体系已收敛进飞轮
- Phase 0 前置缺陷修复阶段已建立
- Golden 集数据泄漏已隔离

文档结构、数据模型完整度、安全意识相比 Rev 1.0 有显著提升，**方向已正确**。

但 Rev 2.0 新引入的 **"沙箱黄金集回归"这条核心防线，在当前代码基础上不可实现**。方案对它的描述与 `tests/eval_golden_set.py` 的实际形态、耗时、内存占用、隔离能力**全部冲突**。这是 Rev 2.0 唯一的阻塞项，但因为它是全案架构的立足点（第一轮指出的"致命架构缺环"正是靠它填补），严重度最高。

### 问题分布

| 类别 | 数量 | 严重度 |
| :--- | :--- | :--- |
| 阻塞项（核心机制不可实现） | 1 项（含 5 个子问题） | **最高** |
| 新增/存留算法与数据问题 | 8 项 | 中—高 |
| 遗留与新增缺失项 | 7 项 | 中—高 |
| 第一轮 P0 未完全闭环 | 3 项（#7 #9 #11） | 高 |
| 第一轮 P1 反向退化 | 1 项（定时调度消失） | 中 |

---

## 一、阻塞项：7.2 沙箱回归在当前架构下不可实现

方案 7.2 与 8.1 设计的流程为：*管理员点击采纳 → 克隆当前内存配置至沙箱态 → 在沙箱中应用待生效新规则 → 自动执行 `tests/eval_golden_set.py` → 指标退化则阻断，平稳则生成快照并写回热重载*，并在 8.1 声称"若通过则**秒级生效**"。

以下 5 个子问题使该流程无法按方案原样落地。

### 1.1 "秒级生效"与实测 157 秒矛盾（高）

**实测数据**（[golden_baseline.json](file:///c:/Users/wangd/project/ListTrans/tests/fixtures/golden_baseline.json#L104)）：

```
"elapsed_sec": 157.6
"eval_scope": { "count": 1067, "chunk_size": 200 }
```

即 1067 条评测耗时 **157.6 秒**，约 148 ms/条。

**后果**：在一次 HTTP 请求内同步执行 157 秒 —— 网关超时、前端 fetch 超时、且 `match_item` 是**同步阻塞调用**，会阻塞 FastAPI 事件循环，导致整个服务在评测期间无响应。

**修正**：
- 采纳流程必须改为**异步任务 + 进度轮询**（或 SSE 推送）
- 九节 API 中 `/api/flywheel/suggestions/eval` 应返回 `task_id`，另加 `GET /api/flywheel/eval/{task_id}` 查询进度与结果
- 删除 8.1 中"秒级生效"的表述，改为"回归通过后秒级写回并热重载"（写回本身是秒级的，回归不是）

### 1.2 "克隆当前内存配置至沙箱态"技术上做不到（最高）

**配置全部是模块级全局变量，且被多层 `lru_cache` 固化**：

| 位置 | 内容 |
| :--- | :--- |
| [normalizer.py:21-31](file:///c:/Users/wangd/project/ListTrans/app/normalizer.py#L21-L31) | `alias_dict` / `material_dict` / `connection_dict` / `flags_dict` / `unit_rules` 全局变量 + `alias_rx` / `alias_map` 等预编译正则 |
| [matcher.py:85](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L85) | `@lru_cache(maxsize=100000)` |
| [preprocessor.py:125](file:///c:/Users/wangd/project/ListTrans/app/preprocessor.py#L125) | `@lru_cache(maxsize=20000)` |
| [preprocessor.py:309](file:///c:/Users/wangd/project/ListTrans/app/preprocessor.py#L309) | `@lru_cache(maxsize=100000)` |
| [db_manager.py:142](file:///c:/Users/wangd/project/ListTrans/app/db_manager.py#L142) | `@lru_cache(maxsize=50000)` |
| [db_manager.py:150](file:///c:/Users/wangd/project/ListTrans/app/db_manager.py#L150) | `@lru_cache(maxsize=10000)` |

**在同进程内"应用沙箱规则"就等于修改这些全局变量与清空这些缓存 —— 会直接污染正在服务的生产请求。** 并发用户在评测期间会拿到沙箱配置下的匹配结果。这与方案的安全初衷完全相反：**为了防止有害规则污染生产，反而在评测过程中主动污染了生产**。

**唯一可行路径：子进程隔离。**

```
采纳请求
  → 服务端将「当前 config/ 全量」+「候选规则」写入临时目录 (如 tmp/sandbox_<task_id>/config/)
  → subprocess 启动独立 Python 进程，通过环境变量指定 CONFIG_DIR 指向临时目录
  → 子进程内执行评测，结果以 JSON 落盘或 stdout 返回
  → 主进程读取结果做门禁判定
  → 全程不触碰生产进程的任何全局变量与缓存
```

**方案必须显式写明这一点**，否则实施时几乎必然走成"同进程改全局变量"的错误实现。

> 附带前置改造：`normalizer.py:7`、`preprocessor.py`、`matcher.py` 的 `CONFIG_DIR` 目前是硬编码路径推导，需支持环境变量覆盖，子进程注入才可行。

### 1.3 `eval_golden_set.py` 不是可复用库（高）

该脚本是**独立进程入口脚本**，不是可被 import 调用的库：

- `main()` 内部自行执行 `init_db()` 与 `index_manager.load_cache()`（[eval_golden_set.py:209-212](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L209-L212)）
- 缓存不可用时会**重建整个向量索引**（[eval_golden_set.py:212-218](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L212-L218)，注释自述"耗时较长"）
- 门禁结果以 `sys.exit(1)` 表达（[eval_golden_set.py:298-300](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L298-L300)）
- 在模块顶层设置 BLAS/OMP 线程数环境变量与 `sys.stdout` 重定向（[eval_golden_set.py:32-38](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L32-L38)）—— import 进生产进程会产生副作用

方案 7.2 只写"自动执行 tests/eval_golden_set.py"，**未说明是函数调用还是子进程调用**。前者不可行（副作用 + 无法隔离），必须明确为后者（`subprocess` + 退出码/JSON 解析）。

### 1.4 内存冲突风险未评估（中高）

脚本自述（[eval_golden_set.py:19-21](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L19-L21)、[:52-56](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L52-L56)）：

> 本机 16G 内存，物料向量矩阵约 47750×512。评测采用分块流式处理，单进程峰值控制在 2G 以内；如仍紧张可用 `--chunk-size 50` 进一步降低。
> 16G 内存机器上必须分块：一次性预处理 + 一次性向量化 1000+ 条会与 47750×512 的物料向量矩阵（约 93MB float32，运算时的临时矩阵可达数百 MB）叠加，容易触发 OOM。

**问题**：生产 FastAPI 进程**已常驻**该向量矩阵与 sentence-transformers 模型。再启动一个评测子进程需**重新加载模型 + 矩阵**，双份内存占用。

方案第十一节"安全、并发与数据治理保障"完全未评估这一点。

**修正**：需明确
- 评测任务**全局串行化**（同一时刻仅允许一个评测子进程，加互斥锁）
- 或采用降采样评测（`--limit`，脚本已支持按专业分层抽样，见 [eval_golden_set.py:194-205](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L194-L205)）
- 明确部署机内存下限要求

### 1.5 `Δ >= 0` 门禁在当前基线上形同虚设（最高）

**当前基线极低**（[golden_baseline.json](file:///c:/Users/wangd/project/ListTrans/tests/fixtures/golden_baseline.json#L3-L11)）：

| 指标 | 当前基线 | 脚本目标值 |
| :--- | :--- | :--- |
| hit@1 | **20.52%** | 85.0% |
| recall@3 | 34.02% | 90.0% |
| recall@5 | 40.39% | 93.0% |
| mrr | 0.2914 | 0.88 |
| miss@10 | **50.05%** | — |

**问题一：门禁无拦截力。** 单条别名规则对 1067 条评测集的整体指标影响，通常远小于 0.1 个百分点，**低于统计噪声水平**。绝大多数规则（**包括有害的**）都会满足"Δ >= 0"而通过。第一轮指出的"致命架构缺环"表面被填补，实际防线是**空的**。

**问题二：与既有脚本门禁语义冲突。** `eval_golden_set.py` 自带 `--tolerance` 参数，默认允许 **0.5 个百分点回退**（[eval_golden_set.py:176](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L176)、判定逻辑 [:292](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L292)）。方案 7.2 流程图写的是 `Δ < 0 (有退化)` 即阻断，为**零容差**，比既有脚本更严，会因随机抖动/浮点误差误阻断合法规则。两者语义必须统一。

**修正建议 —— 双层门禁**：

| 层级 | 判定范围 | 判定标准 | 执行方式 |
| :--- | :--- | :--- | :--- |
| **定向门禁（主）** | 定位该规则实际触及的评测用例子集（`raw_name_norm` 或 `name`/`specification` 含 `source_pattern` 的用例） | 该子集内**不得有任何用例 rank 变差** | 样本量小，可同步秒级完成 |
| **全局门禁（辅）** | 全量 1067 条 | 沿用脚本 `--tolerance 0.5` | 异步执行，作为兜底 |

定向门禁才是**单条规则应有的评估粒度** —— 既有拦截力（子集内退化立即可见），又能秒级完成（真正实现 8.1 承诺的响应速度）。

---

## 二、第一轮 P0 闭环情况核对

| # | 第一轮 P0 | Rev 2.0 状态 | 说明 |
| :--- | :--- | :--- | :--- |
| 1 | `config/llm_rules.json` 不存在 | ✅ **已闭环** | 7.1 通道② 已改为 `materials.db` 的 `llm_rules` 表 + `LLMRuleManager.clear_active_cache()` |
| 2 | 并入 `materials.db` | ✅ **已闭环** | 第四节标题 + 11.1 均已明确 |
| 3 | API 路径冲突 | ✅ **已闭环** | 已统一改为 `/api/flywheel/*` 前缀 |
| 4 | 热重载链路不完整 | ✅ **已闭环** | 三节第 1 点已补齐 6 个（含 `clear_active_cache()`） |
| 5 | `navigator.sendBeacon` | ✅ **已闭环** | 5.2 已明确禁用并正确说明原因（无法携带 Authorization 头） |
| 6 | `user_id` 后端注入 | ✅ **已闭环** | 三节第 3 点已明确从 JWT 提取，防前端篡改 |
| 7 | 采纳前回归 + 回滚 | ⚠️ **设计已加入，但不可实现** | 见第一节全部 5 个子问题；回滚语义亦不完整，见 4.3 |
| 8 | Golden 集数据泄漏 | ✅ **已闭环** | 7.3 已明确 `source = 'flywheel_auto'` 物理隔离 + 人工抽检 |
| 9 | 别名片段提取算法 | ⚠️ **已加入 LCS，但有硬缺陷** | 见 3.1（中文需分词）、3.2（门禁自相矛盾） |
| 10 | `distinct_users` 门禁 | ✅ **已闭环** | 4.2 表字段 + 6.2 双档阈值均已加入 |
| 11 | 绑定体系收敛 | ⚠️ **records 已收敛，采纳产物未收敛** | `BIND_MULTI` 已纳入采集，但 7.1 通道③ 仍写无锁 JSON，见 4.1 |
| 12 | Phase 0 前置修复 | ✅ **已闭环** | 第三节 + 第十节均已建立，含 4 项修复 |

**闭环率：10 / 12（83%）**

### 第一轮 P1 核对

| 第一轮 P1 | Rev 2.0 状态 |
| :--- | :--- |
| 数据模型 7 项补充 | ✅ 已全部采纳（`client_event_id` / `raw_name_norm` / `algo_version` / `config_version` / `superseded` / `final_targets_json` / Top3 精简） |
| `must_not_contain` 降级 | ✅ 6.3 已降级为"仅出报告，不提供一键采纳" |
| Hard Negative 噪声过滤 | ✅ 7.3 第 2 点已加入重合度过滤 |
| 权限统一收紧 | ✅ 九节已改为 `require_manager` / `require_admin` 分级 |
| 数据保留与归档 | ⚠️ 11.3 提出 180 天，但无技术方案，见 4.7 |
| 挖掘任务调度选型 | ❌ **反向退化 —— 全文已无任何调度描述**，见 4.4 |
| 工期改门禁制 | ✅ 第十节已改为检查点门禁制，删除绝对天数 |

---

## 三、新增问题（Rev 2.0 引入或仍存留）

### 3.1 LCS 算法在中文上不可用（高）

方案 6.1 第 1 点："利用最长公共子序列（LCS）提取差异子串对 `(delta_src, delta_dst)`"。

**问题**：LCS 应用于中文必须**先分词**。项目 [requirements.txt](file:///c:/Users/wangd/project/ListTrans/requirements.txt) 共 12 项依赖，**无 jieba 或任何中文分词库**。

**字符级 LCS 会产出破碎片段**。例如对比"过滤单元"与"高效过滤器"，字符级 LCS 得到公共部分"过滤"，差异对为 `("单元", "器")`。将"单元"作为别名 key 写入后，经 [normalizer.py:40-43](file:///c:/Users/wangd/project/ListTrans/app/normalizer.py#L40-L43) 的**按长度倒序全局子串替换**，会污染所有含"单元"二字的文本（如"空调处理单元"、"单元式空调机"）。

**修正**：
- 引入领域词表分词：可复用既有 `config/material_dict.json`、`connection_dict.json`、`flags_dict.json` 作为词典，做正向最大匹配
- 或改用**词表锚定的片段对齐**（只允许词表中已有的词或整名作为候选 key）
- 无论何种方案，必须明确写入方案文档，不能只写"LCS"

### 3.2 6.1 门禁与方案自身核心用例矛盾（高）

方案 6.1 第 2 点"有效性门禁校验"同时要求：
- 长度约束：`2 <= len <= 12`
- **词性约束：拒绝纯数字、纯英文字母、纯单位词（如 `DN50`, `mm`, `台`）**

**矛盾**：方案 1.1 与 Rev 1.0 反复举的核心示例是 `AP-1 → 动力配电箱`、`PAF → 过滤单元`。而 `config/alias_dict.json` 中的真实合法条目正是：

| 行号 | 键 | 值 |
| :--- | :--- | :--- |
| 第 9-17 行 | `"AP"` | `"动力配电箱"` |
| 第 9-17 行 | `"AL"` | `"照明配电箱"` |
| 第 9-17 行 | `"ACP-P"` | `"水泵控制箱"` |

**这些正是"纯英文字母"或"字母 + 连字符"，会被方案自己的门禁全部拒绝。** 设备缩写（本方案最有价值的挖掘目标之一，1.1 节点名的痛点）将永远无法产出。

**修正**：
- 词性约束改为"拒绝纯数字、拒绝纯单位词"
- 对字母型缩写单独设通道，可要求更高的 `distinct_users`（如 >= 3）与更长的观察窗口
- 保留对 `DN50` 这类"字母+数字规格串"的拒绝（这才是真正需要拦的）

### 3.3 `superseded` 归属不清且与幂等冲突（中高）

方案 5.2 第 3 点："将同一 `(session_id, row_idx)` 的历史旧记录标记 `superseded = 1`"，写在**"客户端防抖与批量上报"**条目下。

**问题一：归属错误。** 旧记录**已在服务端数据库中，前端无法标记**。且 4.1 表结构与第九节 API 列表中**均无任何更新入口**。

**问题二：与幂等机制冲突。** `client_event_id` 走 `INSERT OR IGNORE`。当重放请求被 IGNORE 时（即本次并非真实插入），**不能执行 superseded 更新** —— 否则会把当前有效的终态记录误标为过期，造成数据丢失。

**修正**：明确由服务端在**同一事务内**处理：

```sql
-- 1) 幂等插入
INSERT OR IGNORE INTO flywheel_records (client_event_id, ...) VALUES (?, ...);
-- 2) 仅当 rowcount > 0（真实插入）时执行
UPDATE flywheel_records SET superseded = 1
 WHERE session_id = ? AND row_idx = ? AND id < ? AND superseded = 0;
```

**附带缺失**：`flywheel_records` **缺 `(session_id, row_idx)` 组合索引** —— 上述 UPDATE 与后续"取终态记录"的挖掘查询都依赖它。4.1 现有 4 个索引均不覆盖此场景。

### 3.4 `client_event_id` 由客户端生成的风险（中）

4.1 定义 `client_event_id TEXT UNIQUE NOT NULL`，注释为"客户端生成的 UUID"。

**风险**：若前端实现时用了 `session_id + row_idx` 之类的**确定性拼接**（这是很自然的实现倾向，尤其在配合防抖逻辑时），则**用户对同一行的第二次纠正会被静默 IGNORE 丢弃** —— 而"用户改了又改"恰恰是最有价值的数据信号。

**修正**：
- 方案中明确规定必须使用 `crypto.randomUUID()`（UUID v4），禁止任何确定性拼接
- 服务端对撞键做内容比对：同内容 → 幂等静默；异内容 → 记录告警日志（说明前端 ID 生成有 bug）

**附带冗余**：`UNIQUE` 约束在 SQLite 中已自动创建索引，4.1 第 157 行的 `idx_fwr_client_event` 是**冗余索引**，会增加写放大，应删除。

### 3.5 `raw_name_norm` 只是把配置漂移问题前移（中）

Rev 2.0 正确采纳了第一轮的 `raw_name_norm` 与 `config_version` 建议。但**第六节挖掘算法全文没有使用 `config_version`**。

**问题**：飞轮的本质就是不断修改 `alias_dict`。因此**不同时期入库的 `raw_name_norm` 是在不同配置版本下算出来的**。挖掘时把它们混在同一个桶里做 Consistency 统计，比率不可信 —— 早期未清洗的名称与后期已清洗的名称会被当作两个不同的 pattern，或反之被错误合并。

**修正**（择一）：
- 在第六节明确按 `config_version` 分桶统计，只在同版本窗口内计算 Consistency
- 或规则变更后，异步重算受影响记录的 `raw_name_norm`（需记录哪些记录受影响）
- 至少需在方案中承认该局限并给出取舍说明

### 3.6 `is_processed` 与 Consistency 的矛盾仍未在算法层解决（中）

4.1 第 153 行已将注释改为"增量发现标记（0: 未扫描, 1: 已扫描）"，说明**已理解**第一轮指出的问题。

但**第六节算法全文没有描述两阶段流程**。实施时仍会写成单阶段增量统计，导致 Consistency 分母只统计到增量部分，比率严重偏高。

**修正**：在第六节显式写明两阶段：

```
阶段一（增量发现）：扫描 is_processed = 0 且 superseded = 0 的记录，
                   仅用于「发现候选 source_pattern 集合」，扫描后置 is_processed = 1
阶段二（全量统计）：对阶段一得到的每个候选 pattern，
                   在全表（不限 is_processed）范围内计算
                   Support / Distinct_Users / Consistency
```

### 3.7 `PARAM_ADJUST` 采而不用（中）

5.1 采集了 `PARAM_ADJUST` 行为，4.1 也保留了 `extracted_params` 字段。

但 4.2 的 `suggestion_type` 取值只有 `ALIAS / BINDING_RULE / LLM_RULE`，**第六节也没有任何针对特征抽取盲区的挖掘算法**。

Rev 1.0 原有 `PARAM_EXTRACT` 类型，**Rev 2.0 删掉了消费端却保留了采集端** —— 数据只进不出。

**修正**（择一）：
- 补齐消费路径（`PARAM_EXTRACT` 建议类型 + 正则抽取盲区统计算法）
- 或在方案中明确标注"Phase 1 仅采集 `PARAM_ADJUST`，消费路径延后至 Phase 3"，避免实施时的预期落差

### 3.8 Consistency 分母口径未定义（中）

6.2 定义分母为"该原始触发词的**总纠错次数**"。

**问题**：5.1 定义了 5 类行为，哪些计入"纠错"未说明：

| 行为 | 是否计入分母？ | 争议 |
| :--- | :--- | :--- |
| `CONFIRM_TOP1` | 显然不算 | 无争议 |
| `PICK_CANDIDATE` | **不明** | 召回对、排序偏 —— 算纠错吗？ |
| `MANUAL_REPLACE` | 算 | 无争议 |
| `PARAM_ADJUST` | **不明** | 是特征问题，不是别名问题 |
| `BIND_MULTI` | **不明** | 是 1-to-N 场景，与别名映射语义不同 |

口径不明会导致同一批数据算出不同比率，且 ALIAS 与 BINDING_RULE 两类建议的分母口径本应不同。

**修正**：为每种 `suggestion_type` 显式枚举计入分子/分母的 `action_type` 白名单。

---

## 四、遗留与新增缺失项

### 4.1 采纳产物仍落到无锁 JSON —— 与 1.1 缺陷 #1 自相矛盾（高）

方案 1.1 缺陷 #1 明确批评旧版："单文件无锁读写在多用户并发下存在严重写冲突与损坏风险。"

**但 7.1 的通道①仍写 `config/alias_dict.json`、通道③仍写 `config/auto_binding_rules.json`**，而现有写入实现（[server.py:1269-1270](file:///c:/Users/wangd/project/ListTrans/app/server.py#L1269-L1270)）是**整文件覆盖写、无锁、无原子性、无 schema 校验**。

**快照表能回滚内容，但不能防止写入过程中进程崩溃导致文件截断**：

```
写入 alias_dict.json 中途崩溃
  → 文件被截断为非法 JSON
  → normalizer.py:9-18 的 load_json_config 静默返回 {}（except 仅 print）
  → 全部 470 条别名规则失效
  → 且无任何告警，匹配质量断崖式下降而无人察觉
```

**修正**：通道①③ 必须补充
- 临时文件 + `os.replace()` 原子替换（Windows/Linux 均原子）
- 文件级写锁（或复用飞轮采纳流程的全局互斥锁）
- 写后立即重新解析校验，失败则回滚临时文件
- 加载失败时**必须告警**而非静默返回 `{}`

### 4.2 跨库事务缺失，失败补偿顺序未定义（中高）

采纳链路跨越**两个数据库 + 一个文件系统**：

| 环节 | 落地位置 |
| :--- | :--- |
| 生成快照 | `materials.db` 的 `flywheel_config_snapshots` |
| 更新建议状态 | `materials.db` 的 `flywheel_rule_suggestions` |
| 写配置 | **文件系统** `config/alias_dict.json` |
| 写 LLM 规则 | `materials.db` 的 `llm_rules` |
| 热重载 | **进程内存** |
| 写审计 | **`auth.db`** 的 `audit_logs`（`log_audit_event`） |

**无任何原子性保障**。中途失败会出现：
- "配置已改但无快照" → **无法回滚**（最危险）
- "快照已建但配置未改" → 状态不一致，回滚会写入错误内容
- "配置已改但审计未记" → 合规性缺口（与 11.2 承诺冲突）

**修正**：方案需定义严格的执行顺序与失败补偿：

```
1. 写快照（含 config_before 全量，事务提交）—— 失败则直接终止，无副作用
2. 原子写配置文件 / 写 llm_rules —— 失败则按快照 config_before 恢复
3. 热重载 —— 失败则恢复文件 + 重载回旧配置
4. 更新 suggestion.status = accepted + applied_rule_id
5. 写审计日志（失败仅告警，不回滚业务）
```

### 4.3 回滚设计不完整（中高）

`flywheel_config_snapshots` 仅有 `is_active` 单标记，无法表达三类关键语义：

**（1）`flywheel_rule_suggestions.status` 联动回退缺失**

回滚配置后，若对应建议的 `status` 仍是 `accepted`，则该建议将**永久消失** —— 挖掘引擎不会再产出（已处理），管理员也看不到（非 pending）。有价值的知识被静默丢弃。

需同步回退为 `pending`，或新增 `rolled_back` 状态并在看板可见。

**（2）非线性回滚未约束**

连续采纳 A → B → C 后，若只回滚 B，则 B 的 `config_before` 已不再等于"当时状态"（C 已在其之上）。直接按 `config_before` 恢复会**丢失 C 的变更**。

需明确（择一）：
- 只支持**栈式（LIFO）回滚** —— 必须先回滚 C 才能回滚 B（推荐，实现简单可靠）
- 或 `config_before/after` 存**全量**并支持"重放"（4.3 第 198-199 行注释写的是"变更前全量/增量 JSON"，**含糊**，必须二选一）

**（3）通道②（`llm_rules` 表）的回滚方式未定义**

删除记录？还是置 `status = 'deprecated'`？

**后者才能与既有状态机兼容**（[llm_rule_engine.py:754-816](file:///c:/Users/wangd/project/ListTrans/app/llm_rule_engine.py#L754-L816) 已有 `deprecated` 取值），且保留审计痕迹。方案需明确。

### 4.4 定时调度彻底消失（中）

Rev 1.0 至少在第三节标题写了"离线/定时数据挖掘引擎"。

**Rev 2.0 全文检索"定时 / 调度 / cron / scheduler / APScheduler"，零命中。** 只剩第九节的 `POST /api/flywheel/miner/run` 手动触发接口。

**问题**：
- 飞轮不能依赖管理员记得点按钮 —— 这与 1.2 节"低人工干预"的建设目标冲突
- `requirements.txt` 无 APScheduler，需先确定选型
- 挖掘是 CPU 密集型（含数据库全表统计），需避开业务高峰

**修正**：补充调度选型与执行时机。考虑到当前是单 worker 部署（见 4.6），建议 `lifespan` 内启动低频 asyncio 周期任务（如每日凌晨一次），并与评测互斥锁共享同一把锁。

### 4.5 "编辑并采纳"能力丢失（中）

Rev 1.0 的 6.2 节有【编辑并采纳】按钮。**Rev 2.0 第九节 API 只有 `accept` / `reject`**，8.1 看板描述亦无编辑能力。

**问题**：鉴于 6.1 的片段提取算法必然不完美（尤其存在 3.1 分词缺失、3.2 门禁矛盾两个问题），**人工微调是必要的补偿机制**。

管理员面对"接近正确但需改一个字"的建议时，只能驳回 —— 该知识随即丢失，飞轮产出率骤降，且驳回记录会影响后续 Consistency 统计。

**修正**：保留 `POST /api/flywheel/suggestions/accept` 接受可选的 `edited_target_value` 参数；编辑后的内容同样需走沙箱回归门禁；快照 `reason` 中记录"人工编辑"标记以便区分自动/人工产出质量。

### 4.6 单 worker 下的重载原子性未考虑（中）

11.4 判断"当前单 Worker"是**正确的**：
- [Dockerfile:30](file:///c:/Users/wangd/project/ListTrans/Dockerfile#L30)：`uvicorn app.server:app --host 0.0.0.0 --port 18080`（无 `--workers`）
- [start.bat:24](file:///c:/Users/wangd/project/ListTrans/start.bat#L24)：同上
- [docker-compose.yml](file:///c:/Users/wangd/project/ListTrans/docker-compose.yml) 亦未覆盖启动命令

**但单 worker ≠ 无并发问题。** [normalizer.py:33-53](file:///c:/Users/wangd/project/ListTrans/app/normalizer.py#L33-L53) 的 `compile_configs()` 中，`alias_rx` 与 `alias_map` 是**两次独立的全局赋值**（第 45 行是一次元组解包赋值，但 5 组配置是 5 次独立赋值，且 `unit_rx_list` 是循环 append 构建）。

若重载期间有正在执行的同步 `batch_match_rows`（FastAPI 同步端点在线程池中执行，与事件循环并行），会读到**"新正则 + 旧映射"的半更新状态**，产生错误替换或 KeyError。

**修正**：
- 重载时加 `threading.Lock`，匹配路径读取配置时共享同一把锁（读写锁更优）
- 或构建完整的新配置对象后**单次原子引用切换**（Python 单次赋值是原子的）

### 4.7 其他缺失项

**（1）11.3 与代码不符（中）**

11.3 称 `DELETE /api/system/reset` "明确保留已审核生效的规则与快照，仅清空临时操作流水"。

但现有实现（[server.py:441-470](file:///c:/Users/wangd/project/ListTrans/app/server.py#L441-L470)）**根本不涉及任何 feedback/flywheel 表** —— 它只清 `work_sessions`、`uploads` 目录、`binding_feedback.json`。要实现 11.3 的描述**需要改代码**，而第十节 Phase 列表中**无此任务项**。

**（2）"压缩冷库"无技术方案（中）**

11.3 提出"仅保留流水表最近 180 天明细，超期历史数据自动归档至压缩冷库"，但未说明：
- 归档由谁执行（又是一个缺失的定时任务，见 4.4）
- 归档格式与存储位置
- **归档后 Consistency 的历史分母如何计算** —— 若分母中的旧记录被移走，比率会突变，可能使已稳定的规则重新跌破门禁，或使不合格 pattern 突然达标

建议：归档时同步维护一张 `pattern` 级聚合汇总表（保留 Support / Distinct_Users 累计值），使统计不依赖明细。

**（3）Top 候选数量仍未澄清（低中）**

4.1 注释写"Top3 候选精简列表"，但：
- [matcher.py:1826](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L1826) 返回 10 条
- 前端 [renderCandidates](file:///c:/Users/wangd/project/ListTrans/web/app.js#L1336-L1401) 渲染全部（无截断）
- `web/index.html:245` 标题写"Top 3"
- [eval_golden_set.py:127](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L127) 明细只留 `cands[:5]`

方案未说明是"落库时截取前 3 条"还是"要求后端改为返回 3 条"。实施时会产生歧义，且影响 `PICK_CANDIDATE` 的排序修正样本质量（若用户选的是第 5 名，落库 Top3 就丢失了该信息）。

**（4）门禁可能过严导致产出率过低（中）**

6.2 待审门槛 `Support >= 3 AND Distinct_Users >= 2 AND Consistency >= 0.80`，意味着一个错别字**需被 2 个不同的人共同遇到、累计 3 次**才能进入待审。

在专业细分（暖通/给排水/强电/弱电/装饰）+ 项目制使用（不同项目物料重合度低）的场景下，**长尾词很难满足**。而长尾恰恰是别名词典最需要覆盖的部分。

**建议**：增设**低优先级观察池**（`status = 'observing'`，不可直接采纳，但看板可见、可人工提升为 pending）。门槛设为 `Support >= 2`。避免真实知识被永久挡在门外，同时不放松自动采纳的安全性。

**（5）Phase 0 检查点 0 不可测（低中）**

第十节"[检查点 0 验收]：修改别名后无需重启服务即刻生效" —— 缺少可执行的验证方法。

**建议明确**：改一条别名（如新增 `"测试别名A" → "测试别名B"`）→ 调用 `POST /api/config/save` → 调用 `POST /api/match` 传入含"测试别名A"的文本 → 断言返回的清洗后 name 已变为"测试别名B"。可写成自动化测试用例纳入 `tests/`。

---

## 五、复评结论与放行条件

**结论：方向已正确，Rev 2.0 相比 Rev 1.0 是实质性进步。但 7.2 沙箱回归需重新设计后方可实施。**

第一轮 12 项 P0 已闭环 10 项（83%），核心事实性错误全部修正。剩余问题集中在**新引入机制的工程可行性**上，属于"设计意图正确、实现路径缺失"，修订难度低于第一轮。

### P0（必须修订，不修订不予放行）

| # | 修订内容 | 对应章节 | 依据 |
| :--- | :--- | :--- | :--- |
| 1 | **7.2 沙箱明确为子进程隔离**，禁止同进程修改全局变量；说明 `CONFIG_DIR` 注入方式 | 7.2 | 1.2 |
| 2 | **采纳流程改异步任务 + 进度轮询**，删除"秒级生效"表述（实测 157s） | 7.2 / 8.1 / 九 | 1.1 |
| 3 | **门禁改双层**：定向用例子集零退化（主）+ 全局 `tolerance=0.5`（辅），与既有脚本语义对齐 | 7.2 | 1.5 |
| 4 | **6.1 LCS 补中文分词方案**（可复用 `material_dict.json` 等既有词表） | 6.1 | 3.1 |
| 5 | **修正 6.1 门禁自相矛盾**，为字母型缩写（AP / AL / ACP-P）开通道 | 6.1 | 3.2 |
| 6 | **通道①③ 改原子写 + 文件锁 + 写后校验 + 加载失败告警**，消除与 1.1 缺陷 #1 的自我矛盾 | 7.1 | 4.1 |
| 7 | **补齐回滚完整语义**：suggestion status 联动回退、栈式回滚约束、`llm_rules` 回滚方式 | 4.3 / 8.1 | 4.3 |
| 8 | **明确 `superseded` 由服务端在幂等事务内维护**，补 `(session_id, row_idx)` 组合索引 | 4.1 / 5.2 | 3.3 |
| 9 | **第六节显式描述两阶段挖掘流程**（增量发现 + 全量统计） | 六 | 3.6 |
| 10 | **补定时调度选型与执行时机** | 九 / 十 / 十一 | 4.4 |

### P1（建议修订）

| # | 修订内容 | 依据 |
| :--- | :--- | :--- |
| 1 | 恢复【编辑并采纳】能力（accept 接受 `edited_target_value`） | 4.5 |
| 2 | 定义跨库事务失败补偿顺序 | 4.2 |
| 3 | 在挖掘算法中实际使用 `config_version` 分桶 | 3.5 |
| 4 | `client_event_id` 强制 UUID v4 + 撞键内容比对告警；删除冗余索引 | 3.4 |
| 5 | 为每种 `suggestion_type` 定义 Consistency 分子/分母的 `action_type` 白名单 | 3.8 |
| 6 | `PARAM_ADJUST` 补消费路径，或明确标注延后至 Phase 3 | 3.7 |
| 7 | 配置热重载加锁 / 原子引用切换 | 4.6 |
| 8 | 沙箱评测明确串行化互斥 + 内存约束 + 降采样选项 | 1.4 |
| 9 | `/api/system/reset` 改造纳入 Phase 任务列表 | 4.7(1) |
| 10 | 归档补 pattern 级聚合汇总表，避免统计分母突变 | 4.7(2) |
| 11 | 统一 Top 候选数量口径 | 4.7(3) |
| 12 | 增设低优先级观察池（`status = 'observing'`, `Support >= 2`） | 4.7(4) |
| 13 | 检查点 0 补可执行验证用例 | 4.7(5) |
| 14 | 明确 `eval_golden_set.py` 为子进程调用（非 import） | 1.3 |

---

> **复评说明**：本报告仅作技术评审结论，未对生产代码或方案文档做任何修改。方案完成 P0 修订后可提交第三轮复评。
