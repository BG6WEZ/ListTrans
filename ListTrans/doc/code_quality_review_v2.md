# 华康洁净物料匹配系统 · 代码质量与方案达成度评估报告 (V2)

> 评估日期：2026-07-20（第 2 轮复核）
> 评估依据：[final_structuring_master_plan.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/final_structuring_master_plan.md)（V1.0 Final - Revised）
> 参照上一轮：[code_quality_review_vs_master_plan.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/code_quality_review_vs_master_plan.md)
> 评估方式：静态代码审查（未运行、未改动任何代码）

---

## 一、上次问题清单的修复进度

### ✅ 已修复（7 项）

| 上次问题 | 现状证据 |
|---|---|
| **域内字段权重被忽略、只做等权平均** | [matcher.py#L455-459](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L455-L459) 已改为 `sum(scores*weights)/sum(weights)` 加权平均，且逐字段权重从 `domain_config["weights"]` 读取（[L370-371](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L370-L371) 等） |
| **Hard Gates 未配置化、被硬编码在 Python** | [matcher.py#L560-604](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L560-L604) 已从 `domain_config["hard_gates"]` 读取 `conflict_caps` / `deviation_caps` / `ratio_penalties` 三类规则，并抽出 [has_conflict](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L110-L118) / [check_dimension_conflict](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L91-L108) 通用工具 |
| **Category Overwrite 仅限 4 个高危品类** | [domain_router.py#L36-L41](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_router.py#L36-L41) 已放开：`overwrite_rules` 现对**所有品类**执行，与方案 §3.2.1 一致 |
| **电缆分流布尔优先级 Bug (`and`/`or` 未加括号)** | [domain_router.py#L50](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_router.py#L50) 已加括号：`("*" not in spec_clean and "×" not in spec_clean) and (any(kw…))` |
| **`lru_cache` 浅拷贝导致缓存污染** | [preprocessor.py#L360-L362](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L360-L362) 改为 `copy.deepcopy(res.get("attrs", {}))` |
| **`防火 → 耐高温` 语义污染** | [flags_dict.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/flags_dict.json) 现已把 `防火` 独立成一个 flag，不再等价 `耐高温` |
| **老式 `pipe_conflict -0.40 / cable_conflict -0.40` 与新硬门槛"双重扣分"** | [matcher.py#L532-L558](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L532-L558) 已移除对 `pipe_conflict / cable_conflict` 的直接触发调用，改由配置化 Hard Gates 统一封顶 |

### 🟡 部分修复（4 项）

| 问题 | 进展 | 剩余缺口 |
|---|---|---|
| **单位量级换算类目不全** | 新增 [normalizer.py#L86-L115 `convert_de_to_dn`](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/normalizer.py#L86-L115) 处理 De→DN；`domain_extractors.py` 抽取时对"管径/DN/口径"字段调用 [convert_de_to_dn](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_extractors.py#L153-L156) | [unit_norm.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/unit_norm.json) 仍只有 airflow/pressure/power/voltage 4 类，无 length/mass；DN 类未独立成 unit_class |
| **6 份字典规模不足** | material_dict 22→34 条，flags_dict 5→12 条，connection_dict 12 条 | 距 §3.1 "覆盖率 ≥95%" 仍差一个量级；无自动化统计脚本验证覆盖率 |
| **IR-1 属性名带单位污染 K:V 抽取** | [normalizer.py#L71-L73](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/normalizer.py#L71-L73) 新增 `header_unit_pattern` 剥离 20 个常见 Key 后附带的单位 | 白名单式硬编码，可扩展性差；对未列入名单的键（如"扬程m"）无效 |
| **双端 `professional/category` 不对称** | 因 `domain_router` 优先看正文强特征而非分类，实际不对称影响下降 | 但 [matcher.py#L225](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L225) 查询侧仍传 `professional` 到 `extract_params` 的 `category_hint`；候选侧传 `c_category`，参数入口仍不完全对齐 |

### ❌ 未修复（8 项）

| 问题 | 现状 |
|---|---|
| **Phase 0 基线（`baseline_metrics_v0.0.4.md` / `ground_truth_500.json`）** | [tests/](file:///c:/Users/wangd/project/ListTrans/ListTrans/tests) 仍只有 benchmark/integration_test/test_auto_binding 三个文件，无金标集，无基线报告 |
| **Phase 5 `/api/db/schema/stats` + admin 图表** | [server.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/server.py) 全文只有 `/api/db/metadata`、`/api/db/upload`、`/api/db/reparse` 三个 db 路由 |
| **Phase 6 字典变更**局部**重算** | 现有 [reparse_materials_db_asymmetric](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L382-L522) 仍是全量重算，无局部队列 |
| **`parsed_schema_version` 行级列** | 只写在 [db_manager.py#L503](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L503) 的 metadata 表，materials 主表未加列 |
| **`domain_extractors/` 按域拆子文件** | 仍是单一 [domain_extractors.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_extractors.py)，电缆的 `findall(\d+*\d+)` 硬编码在通用 extractor 里 |
| **Bug #7 型号识别过窄** | [preprocessor.py#L88-L123 `extract_model`](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L88-L123) 仍是 6 条通用正则，未拆到域抽取器 |
| **Bug #8 功率/尺寸贪心** | [preprocessor.py#L329-L340](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L329-L340) 兜底仍是首匹配"功率\|电机功率\|风机功率\|单机功率" |
| **打分层残留硬编码 +0.08/+0.08/+0.12 加分** | [matcher.py#L510-530](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L510-L530) 的"核心风量匹配 / 管道口径匹配 / 电缆规格匹配" 依旧硬写在代码里，与配置化 `weights` 平行存在，容易与新 weights 打架 |

### ⚠️ 新增/次生问题（2 项）

- **加权平均实现的口径与旧 `all_attrs_in_tolerance` 冲突**：[matcher.py#L461-L465](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L461-L465) 用 `s < 0.8` 判断"是否全部在公差内"，但 flags 组的 `flags_score` 也会被算进来——若查询有 3 个 flag 只命中 2 个，`0.67 < 0.8` 就误判整个项目"不达标"，反而丢失 `attrs_exact +0.10` 加分。
- **`has_conflict` 对数值 `abs(diff)>1e-5` 判定为冲突**：[matcher.py#L117](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L117) 意味着 `风量 8000` 与 `风量 8001` 也会触发 `deviation_caps`，与"偏差比例"语义相悖，会**误封顶得分**。

---

## 二、总修复率

| 分类 | 数量 | 已修复 | 部分修复 | 未修复 | 完成度 |
|---|:---:|:---:|:---:|:---:|:---:|
| 方法论层面（§3） | 8 | 4 | 3 | 1 | **~63%** |
| 软件改造 RFC（§4） | 7 | 1 | 1 | 5 | **~21%** |
| 8 大 Bug 修复 | 8 | 5 | 2 | 1 | **~75%** |
| 上次代码卫生问题 | 8 | 3 | 1 | 4 | **~44%** |
| **加权总计** | **31** | **13** | **7** | **11** | **约 53%**（比上一轮估算 30% 有实质提升） |

---

## 三、是否达到方案预期？

**结论：仍未达到。**

方案 §1 的量化目标：
- 结构化率 ≥85%（Must 字段全部抽出）
- Top1 相对提升 ≥30%
- Top3 命中率 ≥90%

现状阻塞：

1. **没有 Phase 0 基线也没有金标集**，Top1/Top3 指标**根本无法度量**，改造效果只能靠肉眼观察 [structured_report_preview.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/structured_report_preview.md)（且该文件里"实验室专用排风机 → 机外余压1100pa: 机外余压1100pa" 类的抽取错误依然可见，未修）。
2. **数值冲突判定过严**（`>1e-5` 就算冲突）会让新引入的 `conflict_caps` 大规模误封顶，反而可能**降低 Top1 精度**。
3. **`+0.08 / +0.12` 硬编码加分**与新加权体系并存，权重实际含义无法预测，无法调参。
4. **字典覆盖率与基线远未达标**，方案 §3.1 明确要求"未达 95% 覆盖率不得开发核心"—— 这条前置门槛并未通过就已进入实现阶段。

### 评分对比

| 维度 | V1 评分 | V2 评分 | Δ |
|---|:---:|:---:|:---:|
| 架构骨架落地 | 4/5 | 4/5 | 持平 |
| 配置驱动闭环 | 2/5 | **4/5** | ↑ Hard Gates + 权重都已读取 |
| Bug 修复彻底度 | 3/5 | **4/5** | ↑ 5→7 项完全修复 |
| 可观测/可回归 | 1/5 | 1/5 | 持平（Phase 0/5 依旧缺失）|
| 代码卫生 | 3/5 | 3/5 | 持平（旧硬编码加分尚未清理，新引入误判逻辑）|
| **综合** | **13/25 = 52%** | **16/25 = 64%** | **+12%** |

---

## 四、建议的下一优先动作

1. **立刻建 Phase 0**：`tests/fixtures/ground_truth_500.json` + `baseline_metrics_v0.0.4.md`，否则所有优化都是盲改。
2. 修 [matcher.py#L117](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L117) 的 `has_conflict`：数值应按 `diff_ratio > tolerance` 判定，而非绝对差 `>1e-5`。
3. 删除 [matcher.py#L510-530](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L510-L530) 硬编码的 `+0.08` 系列加分（已被 domain weights 覆盖）。
4. 补 `parsed_schema_version` 列 + 局部 reparse 队列。
5. 字典扩容 → 触发全量 reparse → 跑金标集回归。

---

> 一句话：**框架已经足够，但"跑得起来、量得清楚"仍未完成**。
> 本次评估未修改任何代码。
