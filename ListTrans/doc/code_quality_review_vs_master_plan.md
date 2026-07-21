# 华康洁净物料匹配系统 · 代码质量与方案达成度评估报告

> 评估日期：2026-07-20
> 评估依据：[final_structuring_master_plan.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/final_structuring_master_plan.md)（V1.0 Final - Revised）
> 评估范围：`ListTrans/app/`、`ListTrans/config/`、`ListTrans/tests/`、`ListTrans/doc/` 现存实现
> 评估方式：静态代码审查（未运行、未改动任何代码）

---

## 一、总体判断

**方案完成度：约 70%。骨架已按方案落地，但深水区（配置化 Hard Gates、Baseline 度量、Schema Stats、增量重算）多处偷工，且多个原始 Bug 修复不彻底或引入新的隐性缺陷。**

- ✅ IR-1/IR-2/IR-3 三层结构已成形，30 域 Schema、385→30 分类映射、6 份字典、异步 re-parse 双表原子切换等**主脉络存在**。
- ⚠️ 但**"配置化"承诺没有闭环**：Hard Gates 仍硬编码在 [matcher.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py)；`domain_schema.json` 里的 `hard_gates` 字段**根本没被读取**。
- ❌ Phase 0（基线 500 条 ground truth 与基线指标文档）、Phase 5（`/api/db/schema/stats`）、Phase 6（字典变更触发的局部重算）**完全缺失**。
- ⚠️ 部分 Bug 修复只做了一半，甚至在新旧规则并存下出现**双重扣分**风险。

---

## 二、逐章达成情况

### 第三章 结构化方法论

| 方案条目 | 实现情况 | 证据 / 问题 |
|---|:---:|---|
| IR-1 归一（全半角/大小写/别名/单位标签） | ✅ | [normalizer.py#L39-L72](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/normalizer.py#L39-L72) |
| IR-1 单位**量级换算**（kPa→Pa、CFM→m³/h 等） | 🟡 部分 | 未在 IR-1 完成，只在 [domain_extractors.py `parse_numeric_complex`](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_extractors.py#L32-L104) 抽取时换算；[unit_norm.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/unit_norm.json) 已就绪，方向对但边界易漏（例如 `mmH2O` 会被单位正则 `[a-zA-Z³/㎡²]+$` 匹到但 `l/s` 因含 `/` 未必稳定） |
| IR-2 分域抽取（配置化正则） | ✅ | [ConfigDrivenExtractor.extract](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_extractors.py#L117-L211) 可从 [domain_schema.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/domain_schema.json) 读取 `extract_regex` 动态实例化 |
| IR-3 双轨（在线禁向量补齐 / 离线反填） | 🟡 | 在线侧确实未做向量反填；**离线侧的最近邻反填未见实现**（`vector_index.py` 只做召回，无 auto-refill 逻辑） |
| 3.2.1 Category Overwrite（**第一优先级**） | ⚠️ | [domain_router.py#L36-L43](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_router.py#L36-L43) 只对 `high_risk_categories` 列表内 4 个品类触发覆写，而方案要求**任何品类**当正文出现强特征都要覆写 |
| 3.2.2 标准单位换算 | 🟡 | 只有 airflow / pressure / power / voltage 四类，方案要求的**管径 DN 换算**、`kcal/h`→kW 等在 [unit_norm.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/unit_norm.json) 有 power 但**没有 DN/length 类** |
| 3.2.3 非标数值（范围/不等式/约数） | ✅ | [parse_numeric_complex](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_extractors.py#L32-L104) 完成，且在 [matcher.py 打分](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L314-L355) 消费了 `_min/_max/_op/_base` 子字段 |
| 3.4 二级子类分流（wire/cable_power/cable_control） | ⚠️ | [domain_router.py#L47-L55](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_router.py#L47-L55) **运算符优先级 Bug**：`"*" not in spec and "×" not in spec or any(...)` 中 `and`/`or` 混用未加括号，会把带 `*` 但含 `BV` 的多芯电缆错分到 wire |
| 3.5 六份字典 | 🟡 | 6 个 JSON 均在 [config/](file:///c:/Users/wangd/project/ListTrans/ListTrans/config)，但 [flags_dict.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/flags_dict.json) 仅 5 条、[material_dict.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/material_dict.json) 仅 22 条，**远低于方案 §3.1 "覆盖率 ≥ 95%" 的基线要求**；`connection_dict.json` 未看到丰富条目，`unit_norm.json` 也缺 DN 类 |
| 3.6.1 域内字段权重 | 🟡 | Schema 里定义了 `weights`，但 [matcher.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L289-L424) 打分对**所有域**采用等权平均 `attr_score / valid_attrs_count`，**没有读取 `weights` 做加权** |
| 3.6.2 单向非对称缺失惩罚 | ✅ | [matcher.py#L413-L420](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L413-L420) 只在 "查询有、候选缺" 时扣分，Must -0.15、Should -0.05，符合方案 |
| 3.6.3 硬门槛**配置化** | ❌ | 关键背叛：`domain_schema.json` 里的 `hard_gates.conflict_caps/deviation_caps` **从未被读取**。[matcher.py#L552-L625](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L552-L625) 把风量 500/200、DN 冲突、门窗 10% 等**全部硬编码**在 Python 里 |

### 第四章 软件改造 RFC

| 交付项 | 状态 | 说明 |
|---|:---:|---|
| API 契约保留（attrs 仍 dict、model 仍 str） | ✅ | |
| **前端别名兼容层** | 🟡 | [preprocessor.py#L362-L368](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L362-L368) 仅冗余映射 3 个字段（余压/口径/过滤段），远不足以覆盖旧前端历史键名 |
| 模块解耦（normalizer / domain_router / domain_extractors） | ✅ | 目录/文件与方案 §4.2 一致 |
| `domain_extractors/` 分域子模块 | ❌ | 方案要求 `domain_extractors/{fan.py, damper.py, ...}` 每域独立文件，实际是**单一 [domain_extractors.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_extractors.py)** 全部通过配置驱动 —— 好处是够简，坏处是**特殊逻辑（如电缆 `*` 复合解析）被塞到通用 extractor 里**，扩展性差 |
| `parsed_schema_version` 列 | ❌ | 只写在 metadata 表 [db_manager.py#L503](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L503)，`materials` 主表**没有增列**，无法按行级识别陈旧解析 |
| `/api/db/reparse` 异步重算 | ✅ | [server.py#L1049-L1070](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/server.py#L1049-L1070) + [reparse_materials_db_asymmetric](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L382-L522) 有临时表 + Exclusive Lock + 原子改名 + FAISS 热重载 —— **架构对，但实现有隐患**：一次性读取 46k 行到内存并批量写入，未按方案 §4.3 Phase 4 "每批 1000 条" 处理 |
| Phase 0 基线（`baseline_metrics_v0.0.4.md` / `ground_truth_500.json`） | ❌ | doc 目录未见基线指标文件，`tests/fixtures/` 目录不存在 |
| Phase 5 `/api/db/schema/stats` + admin 图表 | ❌ | 全线缺失 |
| Phase 6 字典变更的**局部**异步重算 | ❌ | 现有 reparse 是**全量**，无局部触发通路 |

### 第二章 8 大 Bug 修复复核

| Bug | 修复状态 | 复核结论 |
|---|:---:|---|
| #1 `not category_hint` 短路 | ✅ | 已改由 `domain_router` 联合判定 |
| #2 `float("3140m³/h")` 静默失败 | ✅ | [parse_numeric_complex](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_extractors.py) 剥单位后转数值 |
| #3 3D 尺寸污染线缆芯数 | 🟡 | 非电缆域会 `pop("芯数","截面积")`；但电缆域仍用 `findall(\d+*\d+)` 贪吃，在 `WDZ-YJY 3*185+2*95` 会得到 `3+2 / 185+95`，作为**字符串**存入，[matcher.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L488-L490) 又只做字面等值比较，语义比较能力实际下降 |
| #4 `common_attrs` 只比交集 | ✅ | 改为查询侧驱动的**单向缺失惩罚** |
| #5 导入时"一次冻结" | 🟡 | 有 reparse，但缺 `parsed_schema_version` 行列 & 增量触发 |
| #6 双端 `professional/category` 不对称 | ⚠️ | 候选侧走 [route_domain(c_category,...)](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L284)；查询侧 `extract_params(q_spec, professional)` 用的是专业名而非 `route_domain` 结果。**双端仍存在不对称**（专业名≠域名） |
| #7 型号识别过窄 | ❌ | [extract_model](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L88-L123) 仍是 6 条通用正则，未拆到域抽取器，方案 §2.7 承诺没兑现 |
| #8 功率/尺寸贪心 | ⚠️ | [preprocessor.py#L329-L339](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L329-L339) 兜底逻辑保留贪心 `功率|电机功率|风机功率|单机功率` 首匹配，多类功率场景仍会覆盖 |

---

## 三、代码质量突出问题

1. **新旧规则并存导致双重扣分**
   [matcher.py#L502-L517](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L502-L517) 保留了老的 `pipe_conflict -0.40` / `cable_conflict -0.40`，随后 [L552-L570](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L552-L570) 又执行 `min(score, 0.30)` 的硬门槛，同一冲突被**先减 40 分再封顶 30 分**。方案 §3.6.3 明确要求"硬门槛封顶"取代旧线性扣分。

2. **打分体系里的"隐性硬编码"**
   - [matcher.py#L471-L491](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L471-L491) 的"核心风量匹配 +0.08 / 管道口径 +0.08 / 电缆规格 +0.12" 与配置化权重系统平行存在。
   - `tolerance_rules.json` 与 `domain_schema.json.hard_gates` 语义重叠，同一参数在两处调参会互相打架。

3. **枚举匹配顺序敏感**
   [ConfigDrivenExtractor.extract](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_extractors.py#L161-L166) 用 `opt.lower() in text.lower()` 判断，`options` 列表顺序决定命中，"不锈钢"会先于"不锈钢304L"命中，材质会退化。fallback [`if "材质" not in attrs`](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_extractors.py#L189-L193) 顺序相反，行为不一致。

4. **`lru_cache` 缓存边界风险**
   [preprocessor.py `_extract_params_cached`](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L277) 用 `@lru_cache(maxsize=100000)` 缓存**可变 dict 的深层引用**。[extract_params](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L359-L360) 用了 `.copy()` 一层，但对 `attrs` 内嵌 list 仍是浅拷贝，前端一旦回写就会污染缓存。

5. **字典规模不足**
   - [material_dict.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/material_dict.json) 仅 22 条同义词
   - [flags_dict.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/flags_dict.json) 5 条，且把 `"防火": "耐高温"` 语义等价，与 damper 域 `flags: ["防火", ...]` 相冲突（IR-1 一归一，防火阀反而丢标签）

6. **文本 IR-1 会破坏正则匹配**
   [normalizer.py 单位替换](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/normalizer.py#L67-L69) 会把 `KW`→`kW`；随后 [domain_schema.json fan.功率 regex](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/domain_schema.json#L45) 里 `(?:kW|W|HP)` 又依赖大小写，加了 `IGNORECASE` 会 OK；但 [`(?:m³/h|m3/h|CMH|l/s|CFM)`](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/domain_schema.json#L9) 在 IGNORECASE 下 `l/s` 也匹 `L/S`，无害；整体幸运，但语义耦合度过高。

7. **无自动测试基线**
   `tests/` 只有 [benchmark.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/tests/benchmark.py) / [integration_test.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/tests/integration_test.py) / [test_auto_binding.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/tests/test_auto_binding.py)，无 500 条金标测试集，无法验证方案 §1 目标"Top1 相对提升 ≥30%、Top3 ≥ 90%"。

8. **[structured_report_preview.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/structured_report_preview.md) 暴露的真实抽取质量堪忧**
   - `实验室专用排风机（箱） 风量：8000m³/h，机外余压1100pa，玻璃钢` 抽出的属性是 `机外余压1100pa: 机外余压1100pa`（把整个 K:V 当成一个键存了），说明 [extract_general_params/parse_sub_parameters](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L193-L218) 拆分逻辑还没把"字典 key 中的单位后缀"清理干净。
   - `医用脱脂无缝不锈钢管 DN32*2.5,D38` 被路由到 `pipe_copper`，暴露分类映射覆盖率不足。

---

## 四、评分与结论

| 维度 | 评分 | 说明 |
|---|:---:|---|
| **架构骨架落地** | 4/5 | 分层清晰，配置外置，异步重算+原子切换到位 |
| **配置驱动闭环** | 2/5 | Hard Gates / Weights **没被真正读取**，与硬编码并存 |
| **Bug 修复彻底度** | 3/5 | 5 个真修，2 个半修，1 个未修（#7） |
| **可观测/可回归** | 1/5 | 无 baseline、无 schema stats、无金标集 |
| **代码卫生** | 3/5 | 老逻辑未清理、缓存/枚举顺序等隐性坑较多 |

**当前系统"已接近方案 V1.0 的形，但还没有它的神"**：想真正跑到方案预期的 ≥85% 结构化率 / Top3 ≥90%，至少还要完成以下 5 件事（按优先级）：

1. 把 [matcher.py#L552-L625](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L552-L625) 的硬门槛改为**从 `domain_schema.hard_gates` 动态读取**，并移除与之重复的老式 `pipe_conflict / cable_conflict / power` 线性扣分。
2. 修 [domain_router.py#L47-L55](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_router.py#L47-L55) 的 wire 分流布尔优先级 Bug，并把 Category Overwrite 从"仅高危品类"放开到"所有品类"。
3. 建立 `tests/fixtures/ground_truth_500.json` + Phase 0 基线脚本，先量化再改造。
4. 补齐 `parsed_schema_version` 列 + 按行触发的**局部**增量 re-parse（对应 Phase 6）。
5. 扩充 [material_dict.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/material_dict.json) / [flags_dict.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/flags_dict.json) / [connection_dict.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/connection_dict.json) 至方案 §3.1 承诺的 **95% 覆盖率**，并处理好 `防火 ≠ 耐高温` 这类语义污染。

---

> 本次评估未修改任何代码。
