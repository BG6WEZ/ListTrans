# 数据飞轮技术方案 Rev 3.0 第三轮独立评审报告

> **被评审对象**：`doc/V0.1.3/data_flywheel_technical_proposal.md`（V0.1.3 Rev 3.0 实施终版）
> **评审基准**：第一轮报告（12 项 P0 / 7 项 P1）、第二轮报告（10 项 P0 / 14 项 P1）
> **评审方式**：文档逻辑审阅 + 全量代码事实交叉核验
> **评审日期**：2026-08-20

---

## 零、总体判断与开工结论

### 结论：**有条件批准开工。Phase 0 与 Phase 1 可立即动工；Phase 2 需先补 3 项设计缺口再启动。**

Rev 3.0 是一次质量显著跃升的修订。第二轮报告提出的 **10 项 P0 已闭环 10 项**，14 项 P1 已闭环 12 项。方案的数据模型、采集链路、门禁口径、回滚语义已达到可实施精度。

但**"子进程沙箱 + 定向用例子集秒级回归"这条核心主防线，在 Rev 3.0 中仍不可实施**。原因不是设计思路错误（思路是正确的），而是它依赖 3 个当前代码不具备、且方案未安排任务去建设的能力。这 3 项属于 Phase 2 的前置阻塞项，不影响 Phase 0/1 开工。

| 阶段 | 开工判定 | 说明 |
| :--- | :--- | :--- |
| **Phase 0** | ✅ **可立即开工** | 6 项任务全部有明确代码落点，无设计缺口 |
| **Phase 1** | ✅ **可立即开工** | 表结构、幂等逻辑、鉴权注入均已就绪；仅需修正 1 处 SQL 缺陷（P0-1） |
| **Phase 2** | ⚠️ **暂缓，先补设计** | 沙箱回归的 3 项前置能力缺失（P0-2/3/4） |
| **Phase 3** | ✅ 设计可接受 | `lifespan` 调度已核实可行 |

---

## 一、第二轮 P0 闭环情况核验（10/10 已闭环）

| # | 第二轮 P0 问题 | Rev 3.0 处置 | 判定 |
| :-- | :--- | :--- | :--- |
| 1 | 沙箱须明确子进程隔离，说明 `CONFIG_DIR` 注入 | 7.2 节明确子进程 + 临时目录注入；Phase 0 第 2 项安排 3 个模块改造 | ✅ 闭环（但注入范围不全，见 P0-2） |
| 2 | 采纳流程改异步 + 进度轮询，删"秒级生效" | 新增 `GET /api/flywheel/eval/{task_id}` 轮询接口；全量回归标注 ~150 秒异步 | ✅ 闭环 |
| 3 | 门禁改双层 | 7.2 节双层门禁：定向子集零退化（主）+ 全量 `tolerance=0.5`（辅） | ✅ 思路闭环（可行性见 P0-3） |
| 4 | 6.1 LCS 补中文分词方案 | 6.2 节改为领域词表正向最大匹配，复用 4 个既有词表 | ✅ 闭环（词表容量不足，见 P1-1） |
| 5 | 修正门禁自相矛盾，为字母缩写开通道 | 6.2 节第 3 条明确"缩写通道放行 AP/AL/ACP-P/PAF" | ✅ 闭环 |
| 6 | 通道①③ 原子写 + 文件锁 + 写后校验 | 7.1 表格 + 十一节第 2 条：`tempfile` + `os.replace` + 写后 `json.loads` 校验 | ✅ 闭环 |
| 7 | 补齐回滚完整语义 | 8.2 节 LIFO 栈式回滚伪代码，含 suggestion 状态联动 `rolled_back`、`llm_rules` 置 deprecated | ✅ 闭环 |
| 8 | `superseded` 服务端维护 + 组合索引 | 5.2 节事务内 SQL；`idx_fwr_session_row` 组合索引已建 | ✅ 闭环（SQL 有缺陷，见 P0-1） |
| 9 | 第六节显式描述两阶段挖掘 | 6.1 节独立成节，增量发现 + 全量统计 | ✅ 闭环 |
| 10 | 补定时调度选型与时机 | Phase 3 第 1 项 + 十一节第 3 条：`lifespan` 内 asyncio 周期任务，每日 02:00 | ✅ 闭环（已核实 `server.py:58-92` 确有 `lifespan`） |

**第二轮 P1 未闭环 2 项**（降级为本轮 P1）：
- `PARAM_ADJUST` 的消费路径：5.1 节标注"Phase 1 仅采集"，但全文再无任何 Phase 消费它，等于永久悬空数据。
- 统一 Top 候选数量口径：4.1 节存 Top3，但 [index.html](file:///c:/Users/wangd/project/ListTrans/web/index.html#L245) 标题写"Top 3"而 [app.js](file:///c:/Users/wangd/project/ListTrans/web/app.js#L1336-L1401) 实际渲染 10 条，仍未对齐。

---

## 二、P0 阻塞项（4 项，必须处理）

### P0-1｜5.2 节幂等 SQL 存在真实缺陷：`last_insert_rowid()` 在 `INSERT OR IGNORE` 后不可靠

方案第 258-262 行的写法：

```sql
UPDATE flywheel_records SET superseded = 1
WHERE session_id = ? AND row_idx = ? AND id < last_insert_rowid() AND superseded = 0
```

**两个问题**：

1. **`cur.rowcount > 0` 的判据不成立**。SQLite 的 `INSERT OR IGNORE` 在唯一键冲突被忽略时，Python `sqlite3` 的 `cursor.rowcount` 返回 **0**；但在部分驱动/版本组合下该值不稳定。更可靠的判据是比较 `cur.lastrowid` 或改用 `INSERT ... ON CONFLICT(client_event_id) DO NOTHING RETURNING id`（SQLite 3.35+ 支持 `RETURNING`，若无返回行即为重复上报）。

2. **`last_insert_rowid()` 是连接级全局状态**。它返回的是该连接上最近一次成功插入的 rowid，不区分表。当前项目 `db_manager.py` 使用 `threading.local` 缓存的长连接（[db_manager.py:14-35](file:///c:/Users/wangd/project/ListTrans/app/db_manager.py#L14-L35)），同一连接上若在本次 `INSERT OR IGNORE` 被忽略（未插入）的情况下调用 `last_insert_rowid()`，得到的是**上一次无关插入的 rowid**，会导致 `id < 某个陈旧值` 的条件误伤或漏标。

**修正要求**：先取插入结果的实际 id，再用该 id 作为参数显式传入：

```python
cur.execute("INSERT OR IGNORE INTO flywheel_records (...) VALUES (...)")
new_id = cur.lastrowid if cur.rowcount == 1 else None
if new_id:
    cur.execute("""UPDATE flywheel_records SET superseded = 1
                   WHERE session_id=? AND row_idx=? AND id<? AND superseded=0""",
                (req.session_id, req.row_idx, new_id))
```

同时方案 5.2 节使用了 `get_db_cursor(commit=True)` 上下文管理器——**该函数在当前代码库中不存在**。`db_manager.py` 只提供 `get_thread_db_connection()`（14 行）与 `get_db_connection()`（37 行），二者均返回裸连接、无上下文管理器、无自动 commit。方案必须明确此为 Phase 0 需新增的基础设施，否则 Phase 1 编码时会直接卡住。

---

### P0-2｜`CONFIG_DIR` 注入范围不完整，沙箱隔离会出现"半污染"

方案 Phase 0 第 2 项只安排改造 3 个文件：`preprocessor.py`、`normalizer.py`、`matcher.py`。

实际全库扫描，读取 `config/` 目录的模块共 **6 个**：

| 文件 | 行号 | 常量 | 方案是否覆盖 |
| :--- | :--- | :--- | :--- |
| [normalizer.py](file:///c:/Users/wangd/project/ListTrans/app/normalizer.py#L7) | 7 | `CONFIG_DIR` | ✅ |
| [preprocessor.py](file:///c:/Users/wangd/project/ListTrans/app/preprocessor.py#L8) | 8 | `CONFIG_DIR` | ✅ |
| [matcher.py](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L73-L75) | 73-75 | 经 `load_config()` 读 config | ✅ |
| [domain_extractors.py](file:///c:/Users/wangd/project/ListTrans/app/domain_extractors.py#L8) | 8 | `CONFIG_DIR` | ❌ **遗漏** |
| [domain_router.py](file:///c:/Users/wangd/project/ListTrans/app/domain_router.py#L7) | 7 | `CONFIG_DIR` | ❌ **遗漏** |
| [vector_index.py](file:///c:/Users/wangd/project/ListTrans/app/vector_index.py#L33) | 33 | `CONFIG_DIR`（读 `model_config.json`） | ❌ **遗漏** |

后果：沙箱子进程启动后，`normalizer` 读临时目录的 `alias_dict.json`，而 `domain_router` / `domain_extractors` 仍读生产目录的配置。这本身不致命（因为待验证的变更只落在 alias/binding），但会造成**沙箱环境与生产环境配置版本不一致**，评测结论的可信度打折。更危险的是 `vector_index.py:33` 若被注入到只含 alias 的临时目录，`model_config.json` 将缺失，回落到默认 `bge-small-zh-v1.5`——一旦生产环境配的是别的模型，沙箱评测就换了模型跑，指标完全不可比。

**修正要求**：Phase 0 第 2 项的改造清单补齐为 6 个文件；并明确沙箱临时目录的构建方式为**全量复制生产 `config/` 目录后再覆盖目标文件**，而非只放单个变更文件。

---

### P0-3｜"定向用例子集回归"缺少最关键的实现前提：`eval_golden_set.py` 不支持按 pattern 筛选

这是本轮最需要正视的一点。方案 7.2 节把"定向用例子集回归"定为**主门禁**，标称耗时 < 2 秒。但核验 [eval_golden_set.py](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L165-L205) 的全部 8 个命令行参数：

```
--limit  --include-1ton  --include-ambiguous  --include-qc-suspect
--professional  --chunk-size  --baseline  --gate
```

**没有任何一个参数能按 `source_pattern` 筛选用例**。`--professional` 只能按专业过滤（电气/给排水等），粒度远远不够；`--limit` 是分层抽样，与定向无关。

更关键的是**耗时假设不成立**。定向子集即使只筛出 5 条用例，脚本 `main()` 仍会无条件执行：
- `init_db()`（[209-211 行](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L209-L211)）
- `index_manager.load_cache()`（212 行）——需加载 **76.9 MB** 的 `materials_embeddings.npy`（已实测文件大小）
- 缓存不可用时触发 `build_index()` 全量重建（214-218 行）——这一步是分钟级甚至十分钟级

也就是说，**子进程冷启动的固定开销（模型加载 + 76.9MB 向量矩阵载入）就远超 2 秒**，与用例条数无关。方案标称的"< 2 秒同步快速返回"在当前架构下无法达成，前端同步等待会超时。

**修正要求（三选一，必须明确写入方案）**：

- **方案 A（推荐）**：Phase 0 增加任务——为 `eval_golden_set.py` 增加 `--case-ids` 与 `--filter-keyword` 参数，并将定向回归也改为**异步任务 + 轮询**，取消"< 2 秒同步返回"的承诺，改为"定向回归约 30-60 秒（含冷启动），全量回归约 150 秒+"。前端统一走 `task_id` 轮询。
- **方案 B**：定向回归不复用 `eval_golden_set.py`，而是在主进程内新写一个轻量校验器，只对 pattern 相关的少量用例调用 `match_item()`——但这就回到了"主进程改配置"的老问题，需要额外设计只读快照对比机制，复杂度高。
- **方案 C**：常驻一个 warm 沙箱子进程（预加载模型与索引），通过 IPC 下发评测任务。可以真正做到秒级，但架构复杂度和内存占用（额外常驻 ~2GB）需评估 16GB 机器是否吃得住。

无论选哪个，**"主门禁 < 2 秒"这个指标必须从方案中移除或修正**，否则 Phase 2 一开工就会发现验收标准无法达成。

---

### P0-4｜定向子集的"零退化"门禁在当前基线水位下会造成大面积误阻断

已核实基线 [golden_baseline.json](file:///c:/Users/wangd/project/ListTrans/tests/fixtures/golden_baseline.json) 实际水位：

```
hit@1 = 20.52%   recall@3 = 34.02%   recall@5 = 40.39%
recall@10 = 49.95%   mrr = 0.2914   miss@10 = 50.05%
```

而脚本 `TARGETS`（[48 行](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L48)）设定的目标是 `hit@1 = 85.0`。**当前系统实际表现与目标相差 64 个百分点，miss@10 高达 50%。**

在这个水位上执行"定向子集内 Δrank <= 0 零退化"门禁，会有两个问题：

1. **一半用例的 rank 本身是"未命中"**（miss@10 = 50.05%）。对这些用例，rank 通常记为 0 或 11+ 的哨兵值。方案未定义"未命中 → 未命中"时 Δrank 如何计算，也未定义"未命中 → 命中第 8 名"算改善还是算什么。这个语义空白会让门禁在实现时行为不可预测。

2. **低水位系统的排名抖动是常态**。当 Top-10 命中率只有 50% 时，任何词典改动都会在大量边缘用例上引起 rank 的随机上下浮动。要求严格"零退化"，实际效果是**几乎所有建议都会被阻断**，飞轮直接停转——这与方案的核心目标相悖。

**修正要求**：
- 明确定义 rank 哨兵值语义（建议：未命中统一记为 `999`，且"未命中 → 未命中"视为 Δ=0 无变化）。
- 门禁不应是绝对零退化，建议改为**净收益判定**：`改善用例数 >= 退化用例数` 且 `不存在原 rank=1 变为未命中的用例`（后者是真正的严重退化，必须硬阻断）。
- 建议在方案中明确记录当前基线水位（20.52% hit@1），并说明飞轮的首要目标正是提升这个水位——避免后续实施者误以为基线已是高水位而设定不切实际的门禁。

---

## 三、P1 建议项（6 项，建议在对应 Phase 前处理）

### P1-1｜领域词表容量不足以支撑正向最大匹配分词

6.2 节要求复用 4 个词表建立领域词库。实测词表规模：

| 词表 | 条目数 |
| :--- | :--- |
| `alias_dict.json` | 470（且含污染，Phase 0 清洗后会更少） |
| `material_dict.json` | 数十条量级 |
| `connection_dict.json` | 数十条量级 |
| `flags_dict.json` | 数十条量级 |

合计约 600 条量级的词表，对物料名称做正向最大匹配，绝大多数长名称会切成大量单字，分词质量堪忧。这不是致命问题（比字符级 LCS 好得多），但建议方案补充：**允许在 Phase 2 引入 `materials` 表的 `name` 字段（47750 条）作为补充领域词源**，通过统计高频 n-gram 扩充词库。同时 `requirements.txt` 中确认无 `jieba`，方案坚持不引入第三方分词是合理的工程选择，但应在文档中说明这是有意取舍及其代价。

### P1-2｜`user_id` 字段类型与实际数据源不匹配

4.1 节定义 `user_id TEXT NOT NULL`。但核验鉴权链路：
- JWT payload 仅含 `{sub, role}`（`sub` 是 username 字符串）
- [auth_middleware.py:37](file:///c:/Users/wangd/project/ListTrans/app/auth_middleware.py#L37) 通过 `get_user_by_username()` 取出完整 user dict
- `users` 表主键是 `id INTEGER AUTOINCREMENT`（[auth_manager.py:57](file:///c:/Users/wangd/project/ListTrans/app/auth_manager.py#L57)）

所以服务端拿到的 `current_user["id"]` 是整数，`current_user["username"]` 是字符串。方案应明确 `user_id` 落库存的是哪个：建议存 `username`（TEXT，稳定可读，且与 `legacy_anonymous` 种子值类型一致），而非整数 id。同时 `user_name` 字段应取 `current_user["name"]`（users 表有 `name` 列）。这个细节不澄清，Phase 1 编码时会随手写成整数 id，与 `'legacy_anonymous'` 种子数据类型冲突。

### P1-3｜`/api/config/save` 的热重载改造需同步生效，方案未点明冲突

Phase 0 第 1 项要求补齐 6 个 reload 调用。核验 [server.py:1271-1272](file:///c:/Users/wangd/project/ListTrans/app/server.py#L1271-L1272)，当前只调了 2 个（`reload_preprocessor_configs`、`reload_matcher_configs`）。方案列出的 6 项中，`reload_normalizer_configs()`（[normalizer.py:165](file:///c:/Users/wangd/project/ListTrans/app/normalizer.py#L165)）、`reload_extractor_configs()`、`reload_router_configs()` 三者**全项目零调用**，确认为死代码。

需注意：`/api/config/save` 当前还是**整文件覆盖写、无锁、无备份**（1268-1269 行）。Phase 0 既然要引入原子写，应把这个既有接口一并改造，否则会出现"飞轮通道走原子写、管理员手改走裸覆盖写"的双标准，两者并发时仍可能截断文件。建议在 Phase 0 第 1 项中明确包含 `/api/config/save` 的原子写改造。

### P1-4｜`reload_matcher_configs()` 会清空 10 万容量的相似度缓存，热重载有性能悬崖

[matcher.py:83](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L83) 中 `reload_matcher_configs()` 会执行 `calculate_text_similarity.cache_clear()`，该 `lru_cache` 容量为 100000（85 行）。每次采纳规则触发热重载，都会让这个缓存全清、后续请求全部冷跑 `difflib.SequenceMatcher`。

在 148 ms/条的当前性能水位下，缓存清空会造成一段可感知的性能下降。建议方案在十一节补一句：**采纳操作安排在业务低谷期执行**，或明确接受此性能抖动。这不是阻塞项，但应有意识。

### P1-5｜`flywheel_pattern_aggregates` 表被引用但未定义

十一节第 4 条提到"在 `flywheel_pattern_aggregates` 汇总表中累加历史计数"，但第四节只定义了 3 张表，没有这一张。虽然它属于 Phase 3 归档功能，但方案既然提了表名，就应给出 DDL，否则实施时又要临时设计。建议补一张最小 DDL（`source_pattern` / `suggestion_type` / `archived_support` / `archived_distinct_users` / `updated_at`）。

### P1-6｜`/api/system/reset` 改造要求过于模糊

Phase 0 第 5 项写"改造 `DELETE /api/system/reset` 逻辑，明确保留规则与快照"。核验 [server.py:441-470](file:///c:/Users/wangd/project/ListTrans/app/server.py#L441-L470)，该接口当前清理 `work_sessions`、uploads 目录、`binding_feedback.json`，**完全不涉及任何 flywheel 表**——也就是说"保留"是默认行为，无需改造。

真正需要决策的是相反的问题：**reset 之后 `flywheel_records` 里指向已删除 session 的流水该如何处理？** 会留下大量 `session_id` 悬空的孤儿记录。建议方案明确：reset 不删 flywheel 流水（历史知识资产应保留），但需在文档中说明 `session_id` 可能失效、不可用于反查会话详情。

---

## 四、已确认无问题的关键点（供实施时放心引用）

以下事实已逐一代码核验，方案表述与代码一致：

1. **`lifespan` 确实存在**且是 `@asynccontextmanager` 形式（[server.py:58-92](file:///c:/Users/wangd/project/ListTrans/app/server.py#L58-L92)），Phase 3 在其中注册后台协程可行。
2. **单 worker 部署**已确认（`Dockerfile:30`、`start.bat:24` 均无 `--workers`），因此 `asyncio.Lock()` 做全局互斥有效。
3. **WAL 模式已启用**（`db_manager.py:26-28`），三张新表并入 `materials.db` 复用连接是正确选择。
4. **`--tolerance` 默认值确为 0.5**（[eval_golden_set.py:176](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py#L176)），门禁判定逻辑在 292-300 行，全量回归门禁标准可直接复用。
5. **`--chunk-size 50` 参数真实存在**（172 行），十一节的内存保护措施可执行。
6. **`llm_rules` 表在 `materials.db` 中**（`llm_rule_engine.py:23-24`），通道② 的落点正确；`clear_active_cache()` 存在于 549-552 行。
7. **`crypto.randomUUID()` 前端可用**，全局 fetch 拦截器（[app.js:1-40](file:///c:/Users/wangd/project/ListTrans/web/app.js#L1-L40)）会自动注入 Authorization 头，5.2 节禁用 `sendBeacon` 改用 `fetch(keepalive)` 的决策正确。
8. **Card 5.5 DOM 确实残缺**（[index.html:402-424](file:///c:/Users/wangd/project/ListTrans/web/index.html#L402-L424) 有游离 `<tr>` 与孤立 `</tbody>`，`binding-suggestions-tbody` 不存在），Phase 0 第 3 项必要且描述准确。
9. **`alias_dict.json` 存量问题真实**：循环映射（`Y型过滤器` 449 行 ↔ `铜质Y型过滤器` 345 行）、重复键（`医用静音型排风机` 300/469 行）、恒等映射（80/253/256/257 行）、含换行符（197-198 行）均已复核，Phase 0 第 5 项必要。
10. **114 条存量样本数量准确**（`feedback_samples.json`，8 正 / 106 负），Phase 0 第 6 项的迁移基数无误。

---

## 五、开工前置清单（Checklist）

### 可立即开工（无阻塞）

- [ ] **Phase 0-1** 补齐 6 个 reload 调用 + 原子引用切换 + `/api/config/save` 原子写改造（含 P1-3）
- [ ] **Phase 0-2** `CONFIG_DIR` 环境变量注入，改造范围扩至 **6 个文件**（P0-2）
- [ ] **Phase 0-3** 修复 `index.html` Card 5.5 DOM
- [ ] **Phase 0-4** 新增 `get_db_cursor()` 上下文管理器基础设施（P0-1 衍生）
- [ ] **Phase 0-5** `alias_dict.json` 清洗脚本
- [ ] **Phase 0-6** 114 条存量样本迁移
- [ ] **Phase 1** 三张表 DDL + `/api/flywheel/record`（幂等 SQL 按 P0-1 修正）+ 前端 5 类行为拆分（`user_id` 口径按 P1-2 定为 username）

### 开工 Phase 2 前必须先定稿

- [ ] **P0-3** 选定定向回归的实现路径（推荐方案 A：加 `--case-ids` 参数 + 全面异步化），并移除"< 2 秒"承诺
- [ ] **P0-4** 定义 rank 哨兵值语义，门禁由"绝对零退化"改为"净收益 + rank=1 掉出硬阻断"
- [ ] **P0-2** 确认沙箱临时目录采用"全量复制 config/ 后覆盖"策略

### 建议但不阻塞

- [ ] P1-1 词表扩充路径写入 Phase 2
- [ ] P1-4 采纳操作低谷期执行的说明
- [ ] P1-5 补 `flywheel_pattern_aggregates` DDL
- [ ] P1-6 明确 reset 与 flywheel 流水的关系
- [ ] 补 `PARAM_ADJUST` 消费路径或明确标注为长期观察数据
- [ ] 统一 Top 候选数量口径（存 Top3 / 显示 Top10 / 文案写 Top3 三者对齐）

---

## 六、结语

Rev 3.0 相比 Rev 1.0 是质的飞跃：从"基于不存在的文件设计"到"每个落点都能在代码中找到"，从"同进程改全局变量"到"子进程隔离 + 原子写 + LIFO 回滚"。数据模型与采集链路已达到可直接编码的精度。

剩余 4 项 P0 集中在同一个区域——**沙箱回归的工程可行性**。它们的共同根源是：方案对 `eval_golden_set.py` 的能力边界和当前系统的真实性能水位（hit@1 仅 20.52%）估计偏乐观。这不影响 Phase 0 / Phase 1 的任何工作，因此**建议立即启动 Phase 0，并在 Phase 0 进行期间同步完成 P0-2/3/4 的设计定稿**，两条线并行，不浪费时间。
