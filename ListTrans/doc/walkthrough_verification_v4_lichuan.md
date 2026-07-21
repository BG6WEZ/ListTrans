# 华康洁净物料匹配系统 · 利川清单专项 Walkthrough 核验报告 (V4)

> 评估日期：2026-07-21（第 4 轮复核）
> 核验对象：用户提供的《华康洁净物料匹配系统回归与利川发包清单验证报告 (Walkthrough)》
> 评估依据：[final_structuring_master_plan.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/final_structuring_master_plan.md)
> 前序评估：[code_quality_review_vs_master_plan.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/code_quality_review_vs_master_plan.md) / [code_quality_review_v2.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/code_quality_review_v2.md) / [walkthrough_verification_v3.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/walkthrough_verification_v3.md)
> 评估方式：静态代码审查 + 本机复跑金标回归

---

## 一、核心结论

**代码改造部分完全属实；但报告里的"利川清单验证数据"无法在项目里找到独立复现路径，需要额外证据。**

- ✅ 4 项 walkthrough 声称的代码改造，Grep + Read 后**均可在代码库中定位到实施证据**
- ✅ 金标回归指标 **实测再次提升**：Top-1 **92.89% → 96.32%**，Top-3 **94.42% → 97.55%**（v0.0.4→v0.0.5）
- ⚠️ **利川清单 44.87% / 78.26% 等表格数据**：仓库中无 `lichuan_runner.py` 或类似脚本，doc/ 目录无对应结果文件，**无法就地复现**
- ⚠️ 报告称"751 行有效物料"，实测 [利川发包清单汇总 - 剔除配电箱和机组.xlsx](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/利川发包清单汇总%20-%20剔除配电箱和机组.xlsx) 全表 **868 行**、13 张（不含"编制说明""汇总表""多联机特安清单"等表则约 700-750 行），量级近似但缺失清洗规则说明

---

## 二、Walkthrough 声称改动的逐项核验

### ✅ 1. FTS 检索分词与同义词扩展 —— 属实

[db_manager.py `search_materials_keyword`#L124-L200](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L124-L200) 已实现：

```python
synonym_map = {
    "网线": ["网线", "双绞线", "对线", "网络线"],
    "双绞线": ["网线", "双绞线", "对线", "网络线"],
    "配电箱": ["配电箱", "配电柜"],
    "配电柜": ["配电箱", "配电柜"],
    "开关": ["开关", "功能件", "面板"],
    "按钮": ["按钮", "呼叫", "开关"]
}
```

并且构建 `(A AND B) OR (C AND D)` 结构的 FTS 子句（[L165-173](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L165-L173)），单字 token 的 `("线" OR "缆")` / `("箱" OR "柜")` 特殊映射也在 [L180-183](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L180-L183)。**Walkthrough §1 完全属实。**

### ✅ 2. 同义词得分加持 + generic 域降级 —— 属实

- **文本匹配度归一至 0.95**：[matcher.py#L322-328](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L322-L328) 定义了 `weak_cable_syns = ["网线", "双绞线", "对线", "网络线"]`，两侧命名都命中同义词表时 `name_sim = 0.95`
- **`+0.12` 加分**：[matcher.py#L526-535](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L526-L535) 名称完全一致或满足弱电缆同义词关系时 `base_score += 0.12`
- **generic 域强制降级到 scene_c**：[matcher.py#L497-503](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L497-L503)：
  ```python
  if q_model and c_model:
      scene = "scene_a"
  elif has_any_attr and domain != "generic":  # ← 关键补丁
      scene = "scene_b"
  else:
      scene = "scene_c"
  ```

**Walkthrough §2 §3 完全属实。**

### ✅ 3. 规格段特征提取器升级 —— 属实

[preprocessor.py `extract_device_name`#L400-L423](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L400-L423)：
- 新增 `spec_match = re.search(r'(?:材质、规格|材质规格|设备名称)\s*[:：]\s*([^\n\r，,；;]+)', text)` 抽取 "PSP钢塑复合管" 类字段（L401）
- 黑名单从上一版 `["风量","流量",…]` 扩到 `["…","安装","部位","介质","工作"]`（L419）

**Walkthrough §4 完全属实。**

---

## 三、金标回归指标 — 本机实测复跑

我在本机运行 `python tests/regression_runner.py`：

```
Total Evaluated: 326
Elapsed Time: 54.59 seconds (167.47 ms/row)
Top-1 Accuracy: 96.32%
Top-3 Coverage: 97.55%
Must Fields Extraction Rate: 100.00%
Must Fields Accuracy: 100.00%
Overall Structuring Rate (Correct/Expected): 100.00%
```

与 [baseline_metrics_v0.0.5.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/baseline_metrics_v0.0.5.md) 完全一致。**相比 v0.0.4 的提升为：**

| 指标 | v0.0.4 | v0.0.5 | Δ |
|---|:---:|:---:|:---:|
| 金标集样本 | 197 | **326** | +129 |
| Must 字段样本 | 65 | **146** | +81 |
| Top-1 准确率 | 92.89% | **96.32%** | +3.43% |
| Top-3 覆盖率 | 94.42% | **97.55%** | +3.13% |
| Must 字段准确率 | 97.14% | **100.00%** | +2.86% |

金标集扩容至 326 条（距离方案要求 500 条尚差 174 条），且指标继续走高，**说明改造真实有效，非过拟合**。

---

## 四、利川清单验证数据的**可复现性问题**

### ⚠️ 4.1 找不到独立运行脚本

- Glob `**/lichuan*` / `**/利川*` 只找到 doc/ 下的两份 Excel，**没有 Python 脚本**
- Grep 全项目 `"44.87"`、`"78.26"`、`"751"` **零匹配**（除了 walkthrough 本身）
- 项目文档目录里也**没有 `lichuan_report.md` 或 `benchmark_lichuan_v0.0.5.md`**
- `tests/benchmark.py` 里跑的是通用 4 个模拟 query，不涉及利川清单

**含义**：报告里"整体自动绑定率 44.87%"、9 张 sheet 的匹配率明细，无法在当前仓库状态下 push-button 重现。可能是走了一次性脚本或前端手工上传，没有沉淀到代码库。

### ⚠️ 4.2 行数与 sheet 数不完全对齐

Walkthrough 声称："13 张 Sheet (共计 751 行有效物料)"

实测 [利川发包清单汇总 - 剔除配电箱和机组.xlsx](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/利川发包清单汇总%20-%20剔除配电箱和机组.xlsx)：

| 项目 | 数值 |
|---|---:|
| 全 sheet 数 | 15 |
| 全行数（含表头/汇总/编制说明）| 868 |
| 剔除 3 张非物料 sheet（编制说明/汇总表/多联机特安清单）后 | 800 |
| walkthrough 表格里 9 张 sheet 相加 | 717 |
| walkthrough 声称 | 751 |

数字量级近似，但没有清洗规则可以精确 reproduce 到 751。建议在下轮跑的时候把清洗脚本+结果 dump 到 `doc/lichuan_report_v0.0.5.md`。

### ⚠️ 4.3 特定 sheet 匹配率反常

- **暖通工程发包 62.44%**：暖通域是本项目投入最多结构化配置的（有 fan/damper/silencer/plenum/ahu 5 个 domain schema），照理应该 >70%
- **强电 -1.06% 回退**：报告称"轻微波动"未展开原因，可能是同义词扩展 `("箱"OR"柜")` 反而放大了强电柜类的 FTS 召回噪声
- **自控 6.90%、弱电 16.92%** 仍然极低：说明弱电/自控专业的结构化改造还没有实质进入本轮优化范围

---

## 五、上一轮 (V3) 遗留问题的修复复核

### ✅ 本轮新修复（1 项）

| V3 遗留问题 | V4 修复证据 |
|---|---|
| 金标集扩到 500 条 | 🟡 197 → **326**（62.5%）继续在做，但未到 500 |

### 🟡 部分修复（1 项）

| V3 遗留问题 | 现状 |
|---|---|
| Top-1 相对提升 ≥30% 对照基线 | 目前有 v0.0.4→v0.0.5 的相对增量（+3.43%），若把最早未改造版本视为基线，报告称"35%→44.87%"约为 **+28.2%** 相对提升，但**利川清单数据不可复现**，此结论证据链未闭合 |

### ❌ 仍未修复（5 项）

| 问题 | 现状 |
|---|---|
| `unit_norm.json` 缺 length / DN / mass 类换算 | 仍是 4 类 |
| 字典 §3.1 "≥95% 覆盖率" 无自动化校验 | 无脚本 |
| `domain_extractors/` 按域拆子文件 | 仍是单文件 |
| Bug #7 型号识别过窄 | 6 条通用正则未拆到域 |
| Bug #8 功率贪心 | 首匹配未按 Key 语义拆分 |

---

## 六、新增/次生风险点（3 项）

### 1. 同义词表**硬编码**在 Python 里

[db_manager.py#L148-155](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L148-L155) 的 `synonym_map` 和 [matcher.py#L323](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L323) 的 `weak_cable_syns` 都是**硬编码 dict**，不走 `config/` 下的 JSON 字典。

- 违反方案 §3.5 "6 份受控字典"原则
- 每加一组同义词就要改代码，不满足运维要求
- `synonym_map` 与 `flags_dict.json` / `material_dict.json` 存在职责重叠

**建议**：迁移到 `config/synonym_dict.json`，与其它 6 份字典统一管理。

### 2. `+0.12` 硬编码加分**又回来了**

上一轮好不容易删掉的 [matcher.py#L510-530](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L510-L530) 硬编码 `+0.08/+0.12`，本轮又新增了 [matcher.py#L535](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L535) 的 `base_score += 0.12`（同义词命中加分）。

- 是配置化 `boost_rules.json` 的重复项，容易调参打架
- 建议改为 `pos_rules.get("name_syn_match", 0.12)` 从 [boost_rules.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/boost_rules.json) 读取

### 3. `weak_cable_syns` 在 matcher.py 重复定义两次

[L323](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L323) 和 [L531](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L531) 相同的列表被写了两遍，是明显的复制粘贴。**代码卫生轻微下降。**

---

## 七、V1→V2→V3→V4 评分演进

| 维度 | V1 | V2 | V3 | V4 | 备注 |
|---|:---:|:---:|:---:|:---:|---|
| 架构骨架落地 | 4/5 | 4/5 | 4/5 | 4/5 | 稳定 |
| 配置驱动闭环 | 2/5 | 4/5 | 4/5 | **3.5/5** | ↓ 硬编码同义词表与 +0.12 加分回归 |
| Bug 修复彻底度 | 3/5 | 4/5 | 4.5/5 | 4.5/5 | 持平 |
| 可观测/可回归 | 1/5 | 1/5 | 4/5 | **4/5** | 金标 197→326，利川清单结果未沉淀到仓库 |
| 代码卫生 | 3/5 | 3/5 | 3.5/5 | **3.5/5** | 持平（新增复制粘贴常量抵消部分改进）|
| **综合** | **13/25 = 52%** | **16/25 = 64%** | **20/25 = 80%** | **19.5/25 = 78%** | ⚠️ 首次评分**回退 2%** |

**评分首次回退的原因**：功能上有实质进步（金标 +129 条、利川清单专项优化），但配置化架构出现**逆向坍缩**（同义词硬编码 + 硬编码 +0.12 加分），代码卫生开始出现小裂缝。

---

## 八、方案预期达成情况

| 方案 §1 目标 | V3 | V4 | 备注 |
|---|:---:|:---:|---|
| Top-1 相对提升 ≥30% | ❓ | ⚠️ 部分证明 | walkthrough 声称 35%→44.87%（+28.2%）但不可复现；金标集内部 v0.0.4→v0.0.5 相对 +3.7% |
| Top-3 命中率 ≥90% | ✅ 94.42% | ✅ 97.55% | 稳定超标 |
| 结构化率 ≥85% | ✅ 97.14% | ✅ **100.00%** | 完美达标 |
| 前端零改动 | ✅ | ✅ | 稳定 |

**综合结论：核心指标已经"绝对达标"，但方案要求的"利川清单实盘验证"证据链**尚未沉淀到代码库**，仍需一次可复现的正式跑分留档。**

---

## 九、下一步建议

1. **把利川清单跑分脚本化并入库**：新增 `tests/lichuan_runner.py`，输出 `doc/lichuan_report_v0.0.5.md`，让 44.87% 这个数字可复现。
2. **同义词表配置化**：把 `synonym_map` 抽到 `config/synonym_dict.json`，`weak_cable_syns` 也一起进去。
3. **同义词加分配置化**：`+0.12` 迁移到 `boost_rules.json`。
4. **金标集继续扩到 500**：从 326 补 174 条，重点补弱电/自控/强电（这三个域仍在低分区）。
5. **修 [matcher.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py) 中 `weak_cable_syns` 的 2 处重复定义**：抽成模块级常量。

---

## 十、一句话总结

> **本轮利川专项优化是"功能层的胜利、架构层的小退步"**。
> Walkthrough 声称的 4 项代码改造 100% 属实；金标集实测 Top-1 已经飙到 **96.32%**（v0.0.5）；
> 但 "44.87%" 这个业务侧最亮眼的数字 **不可复现**——工程上依然存在遗憾。
> **距离方案 V1.0 完整达成还差 3 项工作，其中"利川清单证据链"和"同义词配置化"是本轮新增的短板。**

---

> 本次评估未修改任何代码。
