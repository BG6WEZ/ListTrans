# 武汉华康世纪洁净科技股份有限公司 · 物料清单匹配系统 V0.1.3
## 数据飞轮（Data Flywheel）技术实现方案与架构设计（Rev 3.0 实施终版）

> **版本**：V0.1.3 Rev 3.0（全面闭环第二轮独立评审，实施就绪终版）  
> **状态**：已批准进入实施（Approved for Implementation）  
> **核心目标**：打造生产级可用、高安全隔离、双层防退化验证、支持 LIFO 回滚与原子写入的“交互反馈 ➔ 智能聚合 ➔ 子进程沙箱评估 ➔ 规则与模型闭环演进”数据飞轮。

---

## 目录
- [一、背景与核心痛点](#一背景与核心痛点)
- [二、总体架构与闭环全景图](#二总体架构与闭环全景图)
- [三、Phase 0：前置阻塞缺陷修复与数据治理](#三phase-0前置阻塞缺陷修复与数据治理)
- [四、数据模型设计（入库 materials.db）](#四数据模型设计入库-materialsdb)
- [五、前端行为捕获与鉴权上报规范](#五前端行为捕获与鉴权上报规范)
- [六、挖掘引擎算法与安全门禁设计](#六挖掘引擎算法与安全门禁设计)
- [七、四级知识沉淀通道与子进程沙箱双层回归体系](#七四级知识沉淀通道与子进程沙箱双层回归体系)
- [八、管理审批看板与栈式回滚设计](#八管理审批看板与栈式回滚设计)
- [九、API 接口规格定义（/api/flywheel/*）](#九api-接口规格定义apiflywheel)
- [十、实施路线图与阶段检查点（门禁制）](#十实施路线图与阶段检查点门禁制)
- [十一、安全、并发、原子性与调度保障](#十一安全并发原子性与调度保障)

---

## 一、背景与核心痛点

随着物料清单匹配系统在实际工程中的深入应用，一线工程师在【智能工作台】界面的每次纠错、换选、多物料绑定与参数微调，均包含高价值的领域专业先验知识。

### 1.1 既有系统核心缺陷与历史债
1. **反馈数据碎片化且并发脆弱**：旧版存在两套独立的无锁 JSON 文件（`feedback_samples.json` 与 `binding_feedback.json`），并发下存在写丢失与文件截断风险。
2. **缺乏上下文与算法轨迹**：旧版仅记录简单的代码对应关系，丢失了当时召回的候选轨迹、得分、NLP 特征抽取上下文及决策动作。
3. **“有进无验”的致命架构风险**：规则采纳若缺乏严格沙箱验证，有害规则将污染全局匹配，引发自我强化的负反馈回路。
4. **同进程沙箱技术不可行**：系统配置由模块级全局变量与多层 `lru_cache` 固化，在主进程内修改配置做评测会直接污染并发生产流量；且全量黄金集耗时约 157 秒，同步请求必遭网关超时并阻塞事件循环。
5. **既有热重载断链**：修改别名后，底层预编译正则的 `normalizer.py` 缺乏重载调用，导致配置“改了却不热生效”。
6. **多物料绑定看板 DOM 损坏**：前端 HTML 残缺导致取值为 `null` 抛出异常。

### 1.2 V0.1.3 Rev 3.0 建设目标
1. **全量上下文静默采集与服务端幂等落库**：统一并入 `materials.db`，由后端从 JWT Token 注入用户身份，基于 UUID v4 保证幂等，自动维护 `superseded` 终态。
2. **基于领域词表的词片段提取与多人共识门禁**：复用既有词表正向最大匹配提取差异词片段，支持设备英文字母缩写通道，引入 `Distinct Users >= 2` 多人共识门禁。
3. **子进程独立沙箱与双层回归防退化**：通过独立 Python 子进程隔离环境变量 `CONFIG_DIR`，执行**定向用例子集零退化（主，秒级）+ 全量黄金集容差兜底（辅，异步）**。
4. **原子文件写回与 LIFO 栈式安全回滚**：通过 `os.replace` 原子写防截断，支持完整的 Snapshot 快照、建议状态联动与 LIFO 栈式安全回滚。
5. **统一收敛**：别名词典、LLM 规则表（`llm_rules`）、多物料绑定规则（`auto_binding_rules`）、模型微调资产统一纳入飞轮。

---

## 二、总体架构与闭环全景图

```mermaid
flowchart TD
    subgraph 1["1. 前端智能工作台 (Web UI)"]
        A1[用户导入清单并执行匹配] --> A2[呈现精排 Top 候选]
        A2 --> B1["CONFIRM_TOP1 (采纳首选)"]
        A2 --> B2["PICK_CANDIDATE (换选候选)"]
        A2 --> B3["MANUAL_REPLACE (全库搜索套用)"]
        A2 --> B4["PARAM_ADJUST (参数微调)"]
        A2 --> B5["BIND_MULTI (多物料绑定)"]
    end

    subgraph 2["2. 采集与存储层 (materials.db WAL 模式)"]
        B1 & B2 & B3 & B4 & B5 --> C1["异步 fetch (keepalive: true)"]
        C1 --> C2["POST /api/flywheel/record"]
        C2 --> C3["后端从 JWT 注入 user_id，按 UUID v4 幂等插入\n事务内更新同行旧记录 superseded = 1"]
        C3 --> C4[("flywheel_records 流水表\n(含 raw_name_norm, 精简Top3, 终态标记)")]
    end

    subgraph 3["3. 智能挖掘引擎 (Mining Engine - 两阶段扫描)"]
        C4 --> D1["阶段一: 增量扫描 (is_processed=0 & superseded=0) 发现候选 pattern"]
        D1 --> D2["阶段二: 全表统计 Support / Distinct Users / Consistency"]
        D2 --> D3{规则分类与词表提炼}
        D3 -->|领域词表对齐 + 环检测| E1["别名建议 (ALIAS)"]
        D3 -->|1-to-N 组合归纳| E2["多物料绑定建议 (BINDING_RULE)"]
        D3 -->|长尾观察 (Support>=2)| E3["低优先级观察池 (status='observing')"]
        D3 -->|负向冲突统计| E4["负向排除报告 (仅供人工参考)"]
        D3 -->|高置信隔离导出| E5["Golden 评测集扩充 (标记 flywheel_auto)"]
        D3 -->|过滤重合噪声| E6["产出 BGE 微调三元组 (Triplets)"]
    end

    subgraph 4["4. 验证、审批看板与栈式回滚体系"]
        E1 & E2 --> F1["待审规则看板 (/api/flywheel/suggestions)"]
        F1 --> F2["管理员触发【采纳】或【编辑并采纳】"]
        F2 --> F3["启动子进程沙箱隔离评测\n(注入临时 CONFIG_DIR, 全局互斥锁)"]
        F3 --> F4{"双层回归门禁评估"}
        F4 -->|定向子集有退化 或 全量回退>0.5%| G1["❌ 阻断采纳，返回退化明细"]
        F4 -->|定向零退化 且 全量平稳| G2["✅ 写入快照表 (flywheel_config_snapshots)"]
        G2 --> G3["os.replace 原子写回配置文件 / 写 llm_rules"]
        G3 --> G4["调用完整 6 个 reload 与 clear_active_cache 函数"]
        G4 --> G5["更新 suggestion 状态为 accepted (记录 applied_rule_id)"]
        G4 -.->|异常时安全撤销| G6["⏪ LIFO 栈式一键回滚 (/api/flywheel/rollback)"]
    end
```

---

## 三、Phase 0：前置阻塞缺陷修复与数据治理

在飞轮正式开发前，必须完成以下基础设施修复：

1. **热重载链路全贯通与配置重载原子化**：
   在 `/api/config/save` 及飞轮采纳模块中，补齐调用链并增加重载锁：
   - `reload_preprocessor_configs()`
   - `reload_matcher_configs()`
   - `reload_normalizer_configs()`（**核心修复**：刷新 `alias_rx` 预编译正则）
   - `reload_extractor_configs()`
   - `reload_router_configs()`
   - `LLMRuleManager.clear_active_cache()`
   - 改造 `normalizer.py` 等配置重载为**原子引用切换（Atomic Reference Swap）**，杜绝并发匹配读取到半更新正则状态。

2. **支持 `CONFIG_DIR` 环境变量覆盖**：
   改造 `app/preprocessor.py`、`app/normalizer.py`、`app/matcher.py` 中的配置路径，支持 `os.getenv("CONFIG_DIR", default_path)`，为子进程沙箱提供外部配置注入能力。

3. **修复多物料绑定看板 DOM 残缺**：
   重构 [web/index.html](file:///c:/Users/wangd/project/ListTrans/web/index.html#L402-L424) Card 5.5，补齐 `binding-suggestions-tbody` 与 `binding-rules-tbody` 容器，消除 JavaScript 获取 null 异常。

4. **服务端受信任身份注入**：
   采集接口强制从 JWT Token（`current_user = Depends(require_employee)`）中提取 `user_id` 与 `username` 落库，前端禁止上报 `user_id`。

5. **`config/alias_dict.json` 存量数据清洗专项**：
   运行自动化治理脚本：
   - 消除循环映射（如 `"Y型过滤器" ↔ "铜质Y型过滤器"`）。
   - 剔除重复键冲突、剔除恒等映射（A ➔ A）。
   - 清洗疑似污染条目与换行符。

6. **迁移既有历史数据**：
   将 `app/feedback_samples.json` 现有 114 条样本迁入 `flywheel_records`，标记为 `user_id = 'legacy_anonymous'`，作为冷启动种子数据（不计入 `distinct_users` 门禁）。

---

## 四、数据模型设计（入库 `materials.db`）

将所有数据表统一定义在主数据库 `app/materials.db` 中，复用已配置好的 WAL 模式与连接池。

### 4.1 用户反馈流水表：`flywheel_records`

```sql
CREATE TABLE IF NOT EXISTS flywheel_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_event_id TEXT UNIQUE NOT NULL,       -- 客户端生成的 UUID v4，服务端 INSERT OR IGNORE 保证幂等
    session_id TEXT NOT NULL,                  -- 会话ID (清单批次)
    row_idx INTEGER NOT NULL,                  -- 清单原始行号
    discipline TEXT DEFAULT '通用',            -- 专业分类
    
    -- 文本与特征
    raw_name TEXT NOT NULL,                    -- 原始设备/材料名称
    raw_name_norm TEXT NOT NULL,               -- 入库时预先归一化清洗后的名称 (加速挖掘聚类)
    raw_spec TEXT DEFAULT '',                  -- 原始规格描述
    raw_unit TEXT DEFAULT '',                  -- 原始单位
    raw_category TEXT DEFAULT '',              -- 原始分类
    extracted_params TEXT DEFAULT '{}',        -- 当时解析出的特征参数 (JSON)
    
    -- 算法预测轨迹 (精简字段控制容量，仅存 Top3)
    auto_top1_code TEXT DEFAULT '',            -- 首选物料编码
    auto_top1_score REAL DEFAULT 0.0,          -- 首选得分
    auto_top1_name TEXT DEFAULT '',            -- 首选名称
    auto_candidates_json TEXT DEFAULT '[]',    -- Top3 候选精简: [{"code":"","name":"","score":0.0}]
    algo_version TEXT DEFAULT 'v1.0',          -- 当时匹配算法版本
    config_version TEXT DEFAULT '',            -- 当时生效配置快照版本
    
    -- 用户决策
    action_type TEXT NOT NULL,                 -- CONFIRM_TOP1 / PICK_CANDIDATE / MANUAL_REPLACE / PARAM_ADJUST / BIND_MULTI
    final_targets_json TEXT NOT NULL,          -- 选定标准物料: [{"code":"","name":"","spec":"","qty_ratio":1.0}]
    
    -- 状态与审计
    user_id TEXT NOT NULL,                     -- 服务端注入的用户工号/ID
    user_name TEXT DEFAULT '',                 -- 用户姓名
    superseded INTEGER DEFAULT 0,              -- 0: 终态有效, 1: 同一行后续被更新覆盖 (非终态)
    is_processed INTEGER DEFAULT 0,            -- 增量发现标记 (0: 未扫描, 1: 已扫描)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引设计 (删除冗余的 client_event_id 索引，增加关键组合索引)
CREATE INDEX IF NOT EXISTS idx_fwr_session_row ON flywheel_records(session_id, row_idx);
CREATE INDEX IF NOT EXISTS idx_fwr_raw_norm ON flywheel_records(raw_name_norm);
CREATE INDEX IF NOT EXISTS idx_fwr_user_id ON flywheel_records(user_id);
CREATE INDEX IF NOT EXISTS idx_fwr_created_at ON flywheel_records(created_at);
CREATE INDEX IF NOT EXISTS idx_fwr_mining ON flywheel_records(is_processed, superseded);
```

### 4.2 提炼规则建议表：`flywheel_rule_suggestions`

```sql
CREATE TABLE IF NOT EXISTS flywheel_rule_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_type TEXT NOT NULL,             -- ALIAS (别名) / BINDING_RULE (多物料绑定) / LLM_RULE (DSL规则)
    source_pattern TEXT NOT NULL,              -- 触发词/原始模式 (如: "AP-1")
    target_value TEXT NOT NULL,                -- 建议映射值/规则/绑定 JSON
    discipline TEXT DEFAULT '通用',            -- 适用专业 (与 llm_rules 对齐)
    
    -- 统计指标
    support_count INTEGER DEFAULT 1,           -- 累计样本数
    distinct_users INTEGER DEFAULT 1,          -- 独立操作人数 (门禁关键指标)
    consistency REAL DEFAULT 1.0,               -- 一致性比率 (0.0 ~ 1.0)
    sample_ids_json TEXT DEFAULT '[]',         -- 关联的 flywheel_records ID 列表
    
    -- 审批与生效状态
    status TEXT DEFAULT 'pending',             -- pending (待审) / observing (观察池) / accepted (已采纳) / rejected (已驳回) / rolled_back (已回滚)
    applied_rule_id TEXT DEFAULT '',           -- 采纳后生成的规则ID/配置项ID (支持追溯与回滚)
    reviewer_id TEXT DEFAULT '',               -- 审核人
    reviewed_at TIMESTAMP,                     -- 审核时间
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fws_status ON flywheel_rule_suggestions(status);
CREATE INDEX IF NOT EXISTS idx_fws_pattern ON flywheel_rule_suggestions(source_pattern);
```

### 4.3 配置与规则变更快照表：`flywheel_config_snapshots`

```sql
CREATE TABLE IF NOT EXISTS flywheel_config_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_version TEXT NOT NULL UNIQUE,      -- 快照版本号 (如: snap_20260820_142000_001)
    target_type TEXT NOT NULL,                 -- ALIAS / BINDING / LLM_RULE
    config_before TEXT NOT NULL,               -- 变更前全量 JSON
    config_after TEXT NOT NULL,                -- 变更后全量 JSON
    suggestion_id INTEGER,                     -- 关联的 flywheel_rule_suggestions ID
    reason TEXT DEFAULT '',                    -- 触发变更说明 (标注是否经人工编辑)
    operator_id TEXT NOT NULL,                 -- 操作管理员
    targeted_eval_passed INTEGER DEFAULT 1,    -- 定向回归是否通过 (1: 是, 0: 否)
    golden_eval_score REAL DEFAULT 0.0,        -- 全量回归得分 (可选)
    golden_eval_delta REAL DEFAULT 0.0,        -- 全量回归变化量 Δ (可选)
    is_active INTEGER DEFAULT 1,               -- 1: 当前有效 (栈顶), 0: 已被回滚
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_snap_active ON flywheel_config_snapshots(is_active, created_at);
```

---

## 五、前端行为捕获与鉴权上报规范

### 5.1 行为捕获拆分（解决动作混淆）
重构前端 [web/app.js](file:///c:/Users/wangd/project/ListTrans/web/app.js)，将混在一起的 `applyMatchCandidate` 明确拆分为独立语义的动作：

| 动作标识 (`action_type`) | 触发场景 | 采集上下文价值 |
| :--- | :--- | :--- |
| `CONFIRM_TOP1` | 用户点击【确认首选项】且未做任何修改 | **强正样本**：确认系统首选完全正确 |
| `PICK_CANDIDATE` | 用户点击第 2~N 张推荐卡片进行指定 | **排序修正样本**：召回正确但精排偏离 |
| `MANUAL_REPLACE` | 用户通过【全库搜索】弹窗选定物料并套用 | **强纠错样本**：系统未召回，需挖掘别名 |
| `PARAM_ADJUST` | 用户手动补全/修改功率、管径等参数重新匹配 | **特征抽取样本**：正则盲区（Phase 1 仅采集） |
| `BIND_MULTI` | 用户在行上完成 1 对多主辅材绑定 | **BOM 组合样本**：多物料配比 |

### 5.2 可靠上报与服务端终态维护
* **统一使用 `fetch(..., { keepalive: true })`**：自动走全局 fetch 拦截器携带 JWT Token，禁用 `navigator.sendBeacon`。
* **客户端唯一标识**：`client_event_id` 必须由前端通过 `crypto.randomUUID()` 生成（禁止使用确定性拼接）。
* **服务端同一事务内维护终态与幂等**：

```python
# 服务端接收到上报时
with get_db_cursor(commit=True) as cur:
    # 1. 幂等插入
    cur.execute("""
        INSERT OR IGNORE INTO flywheel_records (client_event_id, session_id, row_idx, ...)
        VALUES (?, ?, ?, ...)
    """, (req.client_event_id, req.session_id, req.row_idx, ...))
    
    # 2. 仅当真实插入新记录时，将同会话同行的旧历史记录标记为 superseded
    if cur.rowcount > 0:
        cur.execute("""
            UPDATE flywheel_records SET superseded = 1
            WHERE session_id = ? AND row_idx = ? AND id < last_insert_rowid() AND superseded = 0
        """, (req.session_id, req.row_idx))
```

---

## 六、挖掘引擎算法与安全门禁设计

在 `app/feedback_miner.py` 中实现细粒度挖掘引擎：

### 6.1 两阶段扫描机制（解决一致性分母统计冲突）
1. **阶段一（增量发现）**：扫描 `is_processed = 0 AND superseded = 0` 的记录，快速提取候选 `source_pattern` 集合。扫描完成后将这些记录的 `is_processed` 置为 1。
2. **阶段二（全量精准统计）**：对阶段一产出的每个候选 `source_pattern`，在全表（不限 `is_processed`，但限制 `superseded = 0`）范围内计算指标。

### 6.2 别名词片段提炼算法（基于领域词表正向对齐）
针对中文无分词库（避免字符级 LCS 破碎）的现状，采用**领域词表锚定对齐算法**：
1. **加载领域锚定词表**：复用 `material_dict.json`、`connection_dict.json`、`flags_dict.json` 及清洗后的 `alias_dict.json` 建立领域专业词库。
2. **正向最大匹配分词**：对 `raw_name_norm` 与 `final_target_name` 进行分词对齐，提取未匹配的差异词对 `(src_token, dst_token)`。
3. **合法性与词性门禁**：
   - 拒绝纯数字、拒绝纯单位词（如 `DN50`, `mm`, `台`）。
   - **缩写通道放行**：对纯英文字母或字母加连字符（如 `AP`, `AL`, `ACP-P`, `PAF`）正常放行，作为设备电气缩写候选。
   - 长度约束：$2 \le \text{len} \le 12$。
4. **环路与冲突前置检测**：写入前确保不构成 A ➔ B ➔ A 循环，且不与既有键冲突。

### 6.3 严格统计指标与分母口径白名单

针对不同建议类型，严格限定 Consistency 计算口径：

| 建议类型 | 分子统计动作 (`action_type`) | 分母统计动作 (`action_type` 白名单) |
| :--- | :--- | :--- |
| `ALIAS` | `MANUAL_REPLACE` 映射到当前标准名 | `MANUAL_REPLACE` + `PICK_CANDIDATE` |
| `BINDING_RULE` | `BIND_MULTI` 绑定到完全一致的物料组合 | `BIND_MULTI` 全量出现次数 |

**多维放行门禁**：
* **待审建议门槛（进入 pending）**：
  $$\text{Support} \ge 3 \quad \text{AND} \quad \text{Distinct Users} \ge 2 \quad \text{AND} \quad \text{Consistency} \ge 0.80$$
* **长尾观察池门槛（进入 observing）**：
  $$\text{Support} \ge 2 \quad (\text{看板可见，支持管理员人工提升为待审})$$
* **高置信推荐门槛（看板推荐默认勾选）**：
  $$\text{Support} \ge 8 \quad \text{AND} \quad \text{Distinct Users} \ge 3 \quad \text{AND} \quad \text{Consistency} \ge 0.95$$

---

## 七、四级知识沉淀通道与子进程沙箱双层回归体系

### 7.1 四级知识沉淀通道

| 通道 | 目标沉淀载体 | 写入与原子性保障 |
| :--- | :--- | :--- |
| **通道 ①：别名与同义词库** | `config/alias_dict.json` | 临时文件 + `os.replace()` 原子覆盖，加文件写锁 |
| **通道 ②：LLM 动态规则库** | `materials.db` 的 `llm_rules` 表 | 事务写入，状态机流转为 approved |
| **通道 ③：多物料自动绑定规则** | `config/auto_binding_rules.json` | 临时文件 + `os.replace()` 原子覆盖 |
| **通道 ④：模型训练与评测资产** | 评测集 Excel + 微调三元组 JSONL | 独立目录存储，打上 `source='flywheel_auto'` 标签 |

### 7.2 核心防线：子进程独立沙箱与双层回归体系

针对主进程全局变量污染和 157 秒耗时问题，采用**子进程沙箱隔离 + 双层回归**机制：

```
                    [管理员点击【采纳】或【编辑并采纳】]
                                     │
                                     ▼
                      [获取评测全局互斥锁 (串行化执行)]
                                     │
                                     ▼
                [创建临时沙箱目录 tmp/sandbox_<task_id>/config/]
                [将当前配置 + 待采纳候选规则写入临时目录]
                                     │
                                     ▼
           ┌──────────────────────────────────────────────────┐
           ▼                                                  ▼
   【第一层：定向用例子集回归 (主)】                   【第二层：全量黄金集回归 (辅)】
   • 仅筛选含 source_pattern 的评测用例                • 执行完整 1067 条评测
   • 子进程执行 tests/eval_golden_set.py              • 作为异步后台兜底校验
   • 耗时: < 2 秒 (同步快速返回)                        • 耗时: ~150 秒 (任务轮询)
   • 门禁标准: 该子集内【零退化 (Δ rank <= 0)】         • 门禁标准: 回退 <= 0.5% (--tolerance 0.5)
           │                                                  │
           └────────────────────────┬─────────────────────────┘
                                    │
                         [双层门禁判定结果]
                                    │
           ┌────────────────────────┴────────────────────────┐
           ▼                                                 ▼
      [❌ 存在退化]                                     [✅ 门禁通过]
   • 阻断采纳流程                                    • 事务内生成 Snapshot 快照
   • 弹窗展示退化用例明细与排名下降分析              • 原子覆盖生产配置文件 / llm_rules
                                                     • 触发主进程完整 6 项热重载
                                                     • suggestion.status 置为 accepted
```

---

## 八、管理审批看板与栈式回滚设计

### 8.1 统一管理看板能力
- **飞轮运行大盘**：已采集流水数、正负样本比例、活跃贡献人数、已沉淀规则数。
- **待审与观察池列表**：清晰展示触发词、建议值、支持人次、独立人数、一致性。
- **【采纳】与【编辑并采纳】**：支持管理员对建议值进行微调（记录编辑标记），微调后同样强制触发沙箱回归。
- **异步评测任务中心**：展示评测进度条与退化用例对比明细。

### 8.2 严格的 LIFO 栈式安全回滚机制
为杜绝非线性回滚造成配置冲突丢失，系统强制采用 **LIFO 栈式回滚（只能从最新快照依次向前回滚）**：

```python
def rollback_latest_snapshot(operator_id: str):
    # 1. 查询当前处于栈顶的有效快照 (is_active = 1 且 ID 最大)
    latest_snap = get_latest_active_snapshot()
    if not latest_snap:
        raise HTTPException(status_code=400, detail="无可回滚的快照")
        
    # 2. 按快照中的 config_before 全量原子写回目标文件 / 将 llm_rules 置为 deprecated
    atomic_write_config(latest_snap.target_type, latest_snap.config_before)
    
    # 3. 联动回退建议状态: 将对应 suggestion 的 status 改回 rolled_back
    if latest_snap.suggestion_id:
        update_suggestion_status(latest_snap.suggestion_id, status="rolled_back")
        
    # 4. 标记当前快照 is_active = 0
    mark_snapshot_inactive(latest_snap.id)
    
    # 5. 执行主进程完整热重载并记录审计日志
    trigger_all_reloads()
    log_audit_event(operator_id, "FLYWEEL_ROLLBACK", f"回滚快照 {latest_snap.snapshot_version}")
```

---

## 九、API 接口规格定义（`/api/flywheel/*`）

所有接口统一使用 `/api/flywheel/` 前缀，权限分级管理：

| Method | Endpoint | 权限要求 | 描述 |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/flywheel/record` | `require_employee` | 客户端流水上报（UUID v4 幂等，JWT 注入身份） |
| `GET` | `/api/flywheel/statistics` | `require_manager` | 获取飞轮运行大盘指标 |
| `GET` | `/api/flywheel/suggestions` | `require_manager` | 获取待审列表（支持 status=pending/observing 过滤） |
| `POST` | `/api/flywheel/suggestions/eval` | `require_admin` | 启动子进程沙箱回归（返回 `task_id`） |
| `GET` | `/api/flywheel/eval/{task_id}` | `require_admin` | 轮询沙箱回归进度与用例退化明细 |
| `POST` | `/api/flywheel/suggestions/accept`| `require_admin` | 采纳建议（支持传入 `edited_target_value`，先过回归后写快照生效） |
| `POST` | `/api/flywheel/suggestions/reject`| `require_admin` | 驳回建议 |
| `POST` | `/api/flywheel/miner/run` | `require_admin` | 手动触发一次挖掘分析（非高峰期） |
| `GET` | `/api/flywheel/snapshots` | `require_admin` | 获取配置变更快照栈（按时间倒序） |
| `POST` | `/api/flywheel/rollback` | `require_admin` | 执行最新快照的 LIFO 栈式回滚 |
| `GET` | `/api/flywheel/export/benchmark` | `require_admin` | 导出带 `flywheel_auto` 隔离标记的评测集 Excel |
| `GET` | `/api/flywheel/export/triplets` | `require_admin` | 导出过滤参数噪声后的 BGE 微调三元组 JSONL |

---

## 十、实施路线图与阶段检查点（门禁制）

### Phase 0：前置阻塞修复与数据治理（必须优先完成）
1. 补齐 `reload_normalizer_configs()` 等热重载链路，改造为原子引用切换。
2. 支持 `CONFIG_DIR` 环境变量注入。
3. 修复 `web/index.html` Card 5.5 残缺 DOM 结构。
4. 运行 `config/alias_dict.json` 自动化去重与环检测脚本。
5. 改造 `DELETE /api/system/reset` 逻辑，明确保留规则与快照。
6. 迁移 114 条存量 `feedback_samples.json` 为冷启动种子。
* **[检查点 0 验收]**：
  - 自动化测试：通过 API 修改别名后，无需重启服务，调用 `/api/match` 断言返回的清洗名称即刻生效。
  - 绑定看板 DOM 渲染正常无 JS 报错。

### Phase 1：数据采集、幂等落库与观察池
1. 在 `materials.db` 建立 `flywheel_records` 等 3 张表及组合索引。
2. 后端实现 `/api/flywheel/record`（UUID v4 幂等 + JWT 身份注入 + 事务维护 superseded）。
3. 前端工作台拆分 5 类行为，接入 `fetch(keepalive)` 上报。
* **[检查点 1 验收]**：并发上报流水精准入库，无锁表，重复上报不重复计数，历史旧记录正确标记 `superseded=1`。

### Phase 2：智能挖掘、子进程沙箱双层回归与栈式回滚
1. 实现 `app/feedback_miner.py`（两阶段扫描 + 领域词表分词对齐 + 缩写放行）。
2. 实现子进程沙箱隔离评估模块（`CONFIG_DIR` 注入 + 定向用例子集秒级回归 + 全量异步回归）。
3. 实现原子写配置 + `flywheel_config_snapshots` 记录 + LIFO 栈式回滚。
4. 在 `admin.html` 落地数据飞轮大盘、编辑采纳、评测进度看板与一键回滚。
* **[检查点 2 验收]**：
  - 单人多次连续误操作被门禁正确拦截；
  - 注入有害规则时，定向回归子集精准拦截并阻断采纳；
  - 采纳通过后，执行一键回滚能 100% 恢复配置文件与 suggestion 状态。

### Phase 3：定时调度、资产导出与闭环验收
1. 在 FastAPI `lifespan` 中注册低频后台 asyncio 周期任务（每日凌晨 02:00 执行增量挖掘）。
2. 实现带有隔离标记的评测集与高质量微调三元组导出。
3. 出具 V0.1.3 Rev 3.0 全链路验收报告。
* **[检查点 3 验收]**：全链路无人值守稳定运行，评测集无数据泄漏，正式交付投产。

---

## 十一、安全、并发、原子性与调度保障

1. **子进程评测互斥与内存保护**：
   - 评测任务使用 `threading.Lock` / 后台 Worker 串行化执行，同一时刻全局仅允许 1 个沙箱回归子进程运行（多请求排队或返回 busy）。
   - 评测子进程默认启用分块流式加载（`--chunk-size 50`），实测峰值 RSS 内存约为 **736 MB**，远低于 2GB 限制，定向子集评测实测耗时约 **25~35 秒**（含 SentenceTransformer 模型冷启动及 39k 向量矩阵载入），前端通过轮询异步任务展示进度条与结果。
2. **单 Worker 部署架构约束 (`--workers 1`)**：
   - 生产环境统一保持单 Worker 模式（`uvicorn --workers 1`），以确保 SQLite 连接池（`threading.local`）、动态分词词表与内存缓存的一致性，避免跨进程锁争用与状态漂移。
3. **跨库事务与写入原子性**：
   - 配置文件写入严格采用两阶段写入（`tempfile` 原子写 + `os.replace`），并在 DB 事务中同步更新快照与建议状态；
   - 包含完善的补偿性回退机制（DB 异常时自动将目标配置文件原子还原至 `before_data`），热重载移出 DB 事务块，杜绝长时间持有写锁。
4. **后台定时调度设计**：
   - 在 FastAPI `lifespan` 内部启动后台调度协程，每日凌晨业务低谷期自动执行挖掘分析。
   - 挖掘分析自动避开用户导入高峰，并与沙箱评测共享互斥锁。
5. **数据生命周期与历史分母保全**：
   - 明细流水保留 180 天。
   - 归档时自动在 `flywheel_pattern_aggregates` 汇总表中累加历史 Support 与 Distinct Users 计数值，确保 180 天归档后 Consistency 统计分母不发生漂移突变。
