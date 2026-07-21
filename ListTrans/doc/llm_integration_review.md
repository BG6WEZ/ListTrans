# 华康洁净物料匹配系统 · 千问 LLM 集成方案评估报告 (V0.0.5)

> 评估日期：2026-07-21
> 评估对象：[llm_integration_strategy.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/llm_integration_strategy.md)
> 系统现状基线：[baseline_metrics_v0.0.5.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/baseline_metrics_v0.0.5.md)（金标 326 条 Top-1 **96.32%** / Top-3 **97.55%** / Must **100%**）
> 评估方式：静态代码审查 + 现有能力对比 + 落地路径推演

---

## 一、结论摘要

**结论：方案架构方向正确，但报告里的**数字预期过于乐观**，且落地细节存在**3 处关键盲区**。**

- ✅ **"混合架构（本地 L1 + LLM L2 兜底）"是正确路径**，绝对优于纯 LLM 方案
- ⚠️ 报告写 "**结构化提取 +15%~20%**、**匹配率 44% → 65%~70%**" 缺依据；当前金标已达 **96.32% / Must 100%**，LLM 能带来的上限远低于报告预期
- ⚠️ 报告只讨论 **API 集成代码**，未涉及**成本核算、缓存策略、prompt 版本化、幻觉治理、失败降级**五大工程化课题
- ⚠️ **Prompt 里没有把 `domain_schema.json` 的 Must/Should/Flags 字段清单注入**，会导致 LLM 抽出的字段和本地 30 域 Schema 不对齐，反而降低数据一致性

---

## 二、报告的"提升空间"数字复核

### 2.1 结构化提取精度：报告预期 15%~20% —— **实际上限不到 3%**

现状（V0.0.5 baseline）：
- Must 字段**结构化率 100%**
- Must 字段**准确率 100%**

⚠️ **报告未对齐 baseline**：金标集里已经 100% 提取成功，LLM 唯一可提升的是**准确率**从 100% 保持到 100%，或者对**未纳入金标的长尾非标物料**（约 46,483 - 326 = 46,157 条）做质量提升。真正的收益空间是"**长尾非标物料的结构化质量**"，不是 15%~20% 的字面提升。

**修正预期**：
| 指标 | V0.0.5 现状 | LLM 预期 | 实际增量 |
|---|:---:|:---:|:---:|
| 金标 Must 字段准确率 | 100% | 保持 100% | 0% |
| 全库长尾物料结构化率（估算）| ~85% | ~93% | **+8%** |
| 全库长尾物料参数准确率（估算）| ~75% | ~90% | **+15%** |

**结论**：报告数字**方向对但口径错**。LLM 主要提升的是"**尚未进入金标集的 46k 条长尾物料**"，而不是已有金标。

### 2.2 自动绑定率：报告称 44% → 65%~70% —— **数字混淆**

- 报告混用了两个 baseline：利川清单实测 **44.87%**（V4 核验中已指出**不可复现**）与金标 **96.32%**
- LLM L2 兜底能提升的是"**得分 60~85 分之间的模糊件**"，这部分在**利川清单口径**下可能占 15%~25%
- 若利川清单 baseline 是 44.87%，LLM 兜底把 15% 的模糊件正确率提升到 70%（保守估计），**理论上限**约为：`44.87% + 15% × 70% ≈ 55%`

**修正预期**：利川清单实盘从 **44.87% → 52%~58%**，不是 65%~70%。

### 2.3 语义判断能力：报告举的例子**至少 2 个已被本地方案覆盖**

- "紧急呼叫按钮 vs 紧急按钮" —— V0.0.5 的**同义词表**已覆盖 [db_manager.py#L153](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L153-L154) `"按钮": ["按钮", "呼叫", "开关"]`
- "污水UPVC管 vs UPVC塑料排水管" —— [material_dict.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/material_dict.json) 的 `"UPVC管": "UPVC"` + FAISS 向量召回已能高分匹配
- **"100L 兼容 80L 热水器"这类跨规格兼容判断** —— 这才是 LLM **不可替代**的核心价值

**建议**：报告应聚焦"**跨规格兼容判断**"、"**多模态推理**"、"**非结构化描述归一**"三类**本地方案确实做不了**的场景，而不是列举已被覆盖的例子。

---

## 三、架构方案评估：混合架构 = ✅ 正确方向

### 3.1 与方案 §3.2 IR-3 的天然契合

方案 [final_structuring_master_plan.md §3.2 IR-3](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/final_structuring_master_plan.md#L288-L293) 明确规定：
- **离线热建侧**：允许语义/最近邻反填 Must 字段
- **在线匹配侧**：禁止引入向量搜索（避免延迟）

**LLM 方案与此完全契合**：
| 现有方案层 | LLM 集成位置 |
|---|---|
| IR-3 离线热建侧 | `extract_params_by_qwen`（新增物料入库时异步抽取） |
| IR-3 在线兜底 | `check_match_with_qwen`（Top-1 得分 60~85 时调用） |

### 3.2 双层设计的正确性

| 设计选择 | 评价 | 原因 |
|---|:---:|---|
| **不用 LLM 做召回** | ✅ 正确 | 46k 条超出上下文窗口，且成本无法承受 |
| **本地 FTS + FAISS 召回 Top 5** | ✅ 正确 | 复用现有 [hybrid_recall](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L655-L740)，零改动 |
| **LLM 仅做"多选一"判定** | ✅ 正确 | 输入短、输出短、Token 消耗 < 2000/次 |
| **仅对 [60, 85) 分调用** | ✅ 正确 | 高分件（≥85）已自动绑定；低分件（<60）本身就是垃圾召回，LLM 也救不回来 |

### 3.3 增量入库时的 LLM 结构化

**方向对，但需要与 30 域 Schema 对齐**。当前 prompt 直接让 LLM"提取 attrs 键值对"，而**本地 30 域 Schema 明确规定了每个域的 Must/Should/Flags 字段集合**。若 LLM 抽出的字段名（如"电压 220V"）与 Schema 的字段名（"电压等级"）不一致，会污染 `parsed_params`。

**必要修正**：Prompt 应该**注入目标域的字段清单**：
```python
domain_config = domain_schema.get(domain, {})
must_keys = [f["key"] for f in domain_config.get("must", [])]
should_keys = [f["key"] for f in domain_config.get("should", [])]
flags = domain_config.get("flags", [])

prompt = f"""
目标域: {domain}
必须提取字段（Must）: {must_keys}
可选提取字段（Should）: {should_keys}
布尔特征标签（Flags）: {flags}
...
"""
```

否则 LLM 抽出的结果无法直接消费。

---

## 四、报告未覆盖的 5 大工程化盲区

### 4.1 成本核算缺失

报告称"每周 <1 元人民币"，但没有推算依据。粗算：
- 新增物料 300~500 条/周
- 每条 prompt ~500 tokens + response ~200 tokens
- 千问 turbo 单价（2025 年）：约 `0.3元/百万 输入 tokens` + `0.6元/百万 输出 tokens`
- 单条成本：`500×0.3 + 200×0.6 ≈ 270`（微元）= 0.000270 元
- 周总成本：`500 × 0.000270 ≈ 0.135 元` ✅ 报告口径大致合理

但是 L2 在线兜底部分：
- 利川清单 868 行、假设 25% 触发 L2 = 217 条
- 单条 prompt ~1500 tokens（含 5 个候选 spec）+ response ~50 tokens
- 单条成本：`1500×0.3 + 50×0.6 ≈ 480 微元` = 0.00048 元
- **单张清单**：`217 × 0.00048 ≈ 0.10 元`
- **月均 40 张清单**：`4 元/月`

**结论**：成本极低，但报告没有把利川清单实盘的量级也算进去，需要补充。

### 4.2 缓存策略缺失

- 同一个 spec 反复上传（利川清单里"截止阀 DN15" 出现 5 次），若不做**结果缓存**，LLM 会被调用 5 次
- 建议：对 `L2 LLM 判定结果`按 `(query_hash, top5_codes_hash)` 做 SQLite 持久化缓存
- 命中率保守估计 30%~50%，可再省一半成本

### 4.3 Prompt 版本化 & Prompt Injection 防御

- Prompt 内容会随业务演进不断改动，建议：
  - Prompt 存到 `config/llm_prompts.json`，与代码解耦
  - Prompt 内使用 `{{}}` 占位符，做参数化注入
  - 用户的 `specification` 文本可能包含"忽略上面的指令"这种 prompt injection 尝试，需做输入白名单

### 4.4 幻觉治理

- LLM 可能"编造"一个 `chosen_index = 3` 但实际候选池只有 2 项 —— 报告代码 [L175](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/llm_integration_strategy.md#L175) 有校验 `chosen_idx < len(top_candidates)`，✅
- 但 `chosen_index` 是 `-1` 时的处理路径缺失：应该**回退到本地 Top-1**，并把决策标签打为 `"LLM 弃权"`
- LLM 抽 attrs 时可能编造不存在的字段，建议在 `extract_params_by_qwen` 里做 Schema whitelist 过滤：只保留 `must_keys + should_keys + flags` 里的字段

### 4.5 失败降级 & 熔断

- 报告没有考虑 API 超时（1~3s）、限流（429）、鉴权失败的降级路径
- 建议：
  - `httpx.Timeout(5.0)` + 单次重试
  - 连续失败 > N 次自动熔断 10 分钟
  - 失败时**回退到本地 Top-1**，并在 UI 上标记"LLM 兜底暂不可用，已回退本地结果"

---

## 五、当前系统 vs 引入 LLM 的收益/成本矩阵

| 维度 | 当前系统 (V0.0.5) | 引入 LLM (L2 兜底) | 引入 LLM (增量结构化) |
|---|:---:|:---:|:---:|
| 金标 Top-1 | 96.32% | ~97%（+1%） | 96.32%（不变） |
| 利川清单绑定率 | 44.87% | ~52-58%（+10%） | ~50%（+5%） |
| 长尾物料结构化率 | ~85% | ~85%（不变） | ~93%（+8%） |
| 单次匹配延迟 | 167 ms/行 | ~170 ms/行（高分件不触发） | 167 ms/行 |
| 部分行触发时延迟 | - | +1500 ms | 0（离线） |
| 月运行成本 | 0 | 4~8 元 | 0.5~1 元 |
| 一次性开发工作量 | - | 中（2~3 天） | 中（3~5 天） |
| API 依赖风险 | 无 | 有（需熔断） | 低（离线） |

---

## 六、推荐落地路径（3 步走）

### 阶段 1（1 天，零风险 PoC）
1. 新增 `app/llm_client.py`：只做**通义千问 API 调用封装**，暂不接入生产链路
2. 挑 20 条利川清单低分件手工调用 LLM，比对 LLM 判定 vs 本地 Top-1 vs 真值
3. 输出 `doc/llm_pilot_report.md`：验证"利川清单口径提升 10%"是否真的可达

### 阶段 2（2~3 天，L2 兜底集成）
1. `matcher.match_item` 中判断 Top-1 得分 ∈ [60, 85) 时调用 `check_match_with_qwen`
2. 加 3 层护栏：**结果缓存 + 输入白名单 + 熔断器**
3. LLM 决策结果打上 `"L2 LLM 判定"` 标签写入 `binding_feedback.json`，供后续迭代
4. **prompt 参数化**到 `config/llm_prompts.json`
5. 跑金标回归验证 Top-1 是否稳定或有微增（不能有回退）

### 阶段 3（3~5 天，增量结构化）
1. `db_manager.import_materials_from_excel` 中新增物料时后台调用 `extract_params_by_qwen`
2. Prompt 里**强制注入 30 域 Schema 的 Must/Should/Flags 清单**
3. LLM 抽出的字段做 Schema whitelist 过滤后再入库
4. 单独跑一次全量 `/api/db/reparse` 观察长尾结构化率提升

---

## 七、代码层落地建议

### 7.1 新增文件

```
app/llm_client.py           # 通义千问 API 客户端封装（含熔断、缓存、重试）
config/llm_prompts.json     # 三段 Prompt 模板参数化
config/llm_config.json      # api_key 环境变量名、model、timeout、熔断阈值
```

### 7.2 集成点

| 位置 | 修改内容 |
|---|---|
| [matcher.py#L780](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py) `match_item` 末尾 | 加入 `if 60 <= top_one["score"] < 85 and llm_enabled: check_match_with_qwen(...)` |
| [db_manager.py#L370](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py) `import_materials_from_excel` | 后台线程池对每条新入库物料异步跑 LLM 结构化 |
| [server.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/server.py) | 加 `POST /api/llm/toggle`（管理员开关）、`GET /api/llm/stats`（调用量/命中率） |

### 7.3 环境变量

```dotenv
# .env.example 新增：
DASHSCOPE_API_KEY=sk-xxxx          # 通义千问 API Key
LLM_ENABLED=false                  # 默认关闭，需管理员显式开启
LLM_L2_SCORE_MIN=60                # L2 触发下限
LLM_L2_SCORE_MAX=85                # L2 触发上限
LLM_TIMEOUT_SECONDS=5              # API 超时
LLM_CACHE_TTL_HOURS=168            # 缓存 7 天
```

---

## 八、一句话结论

> **报告方案架构正确、代码模板可用，但数字预期过于乐观，且缺 5 大工程化护栏（成本核算、缓存、prompt 版本化、幻觉治理、熔断降级）。**
>
> **建议按 3 阶段落地：先 PoC 验证"利川清单口径能否真的 +10%"，再决定是否投入生产集成。**
>
> 现有 V0.0.5 系统金标已达 96.32%，LLM 主要价值不在金标提升，而在**长尾结构化质量 (+8%)** 与**利川清单模糊件兜底 (+10%)**。

---

> 本次评估未修改任何代码。
