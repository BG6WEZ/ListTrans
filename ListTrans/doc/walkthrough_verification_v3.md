# 华康洁净物料匹配系统 · Walkthrough 报告核验与代码质量评估 (V3)

> 评估日期：2026-07-21（第 3 轮复核）
> 核验对象：[walkthrough.md](file:///c:/Users/wangd/project/ListTrans/walkthrough.md)
> 评估依据：[final_structuring_master_plan.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/final_structuring_master_plan.md)
> 前序评估：[code_quality_review_vs_master_plan.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/code_quality_review_vs_master_plan.md) / [code_quality_review_v2.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/code_quality_review_v2.md)
> 评估方式：静态代码审查 + 本机复跑回归测试

---

## 一、Walkthrough 报告核验结论：**基本属实**

### ✅ 完全属实（5 大类改造均已落地）

**1. 匹配算法三项优化真实到位**
- **相对公差 `has_conflict`**：[matcher.py#L110-122](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L110-L122) 已改为读取 `tolerance_rules` 相对公差判定，公式与报告一致：
  `diff_ratio = abs(q_v-c_v)/max(q_v,c_v,1e-5)`，替代了旧的 `>1e-5` 绝对差硬判。
- **消除 Flag 干扰**：[matcher.py#L465-467](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L465-L467) 的 `all_attrs_in_tolerance` 增加了 `if (k in tolerance_rules or k == "尺寸" or k in ["芯数","截面积"])` 前置过滤，Flags 不再干扰。
- **清理硬编码加分**：Grep 全项目 `核心风量匹配|管道口径匹配|电缆规格匹配` **零匹配**，`+0.08/+0.12` 硬编码加分已彻底删除。

**2. 特征提取两项去污真实**
- **无冒号 K:V 提取**：[preprocessor.py#L215-223](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L215-L223) 新增 `concat_match` 正则处理 "机外余压1100pa" / "DN100" 型无冒号字符串。
- **设备名过滤属性词**：[preprocessor.py#L407-410](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L407-L410) 新增 `exclude_property_words` 白名单，命中"风量/功率/口径/材质"等参数词时退避到 `category_name`。

**3. 服务层三项升级真实**
- **行级版本列**：[db_manager.py#L26-35](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L26-L35) 存在 `ALTER TABLE materials ADD COLUMN parsed_schema_version INTEGER DEFAULT 0` 的自愈迁移；[L59-70](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L59-L70) 新建表也带此列。
- **`/api/db/schema/stats` 路由**：[server.py#L1056-1065](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/server.py#L1056-L1065) 已存在，返回各域数据分布及 Must 字段覆盖率；对应的 [get_db_schema_stats](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L540-L606) 已实现。
- **增量异步重解析**：[reparse_materials_db_partial](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L608-L680) 已实现，支持 code / category / `parsed_schema_version < 2` 三种粒度局部重算，与 [server.py `/api/db/reparse`](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/server.py#L1067-L1096) 路由联通。

**4. 字典扩容真实（甚至超过报告描述）**
- [material_dict.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/material_dict.json) 扩到 **44 条**（新增 304L/316L/UPVC管/无缝不锈钢管 等）。
- [flags_dict.json](file:///c:/Users/wangd/project/ListTrans/ListTrans/config/flags_dict.json) 扩到 **18 条**（新增 变频电机/防爆电机/阻燃/耐火/无卤/屏蔽）。

**5. 金标回归指标本机复跑一致**

我在本机运行 `python tests/regression_runner.py`，输出：
```
Total Evaluated: 197
Top-1 Accuracy: 92.89%
Top-3 Coverage: 94.42%
Must Fields Extraction Rate: 100.00%
Must Fields Accuracy: 97.14%
```

与 [baseline_metrics_v0.0.4.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/baseline_metrics_v0.0.4.md) 记录**完全一致**。

---

## 二、有出入的 3 处

### 1. 集成测试数字不对（细节偏差）

Walkthrough 声称 "Ran 11 tests OK"，实测运行结果：
```
Ran 14 tests in 0.377s
OK
```
**14 个测试通过，不是 11**。测试**全部通过属实**，仅数字略过时，无实质影响。

### 2. `ground_truth_500.json` 名不副实（结构缺口）

- 文件命名声称 500 条，实际只有 **197 条**（Python 校验：`len(data) == 197`）
- 其中真正带 `must_attrs` 非空的仅 **65 条**（33%）
- 方案 §4.3 Phase 0 明确要求 **500 条金标测试集**，当前只完成了 **39.4%**

**含义**：虽然 Top-1 92.89% 数字漂亮，但样本量不足会放大统计噪声；`Must 字段准确率 97.14%` 分母偏小（约 100 个字段样本），代表性有限。

### 3. "远超 85% 目标"表述乐观，未证明"相对提升 ≥30%"

方案 §1 的量化目标包含 3 条：
- Top-1 精确匹配率 **相对提升 ≥30%** ← ❓ 未证明
- Top-3 命中率 ≥90% ← ✅ 达标 (94.42%)
- 结构化率 ≥85% ← ✅ 达标 (97.14%)

**"相对提升"需要与改造前基线对照**，而项目里**没有保留改造前版本的对照跑分**。当前 92.89% 只能证明"绝对达标"，不能证明相对提升 30%。

---

## 三、上一轮遗留问题的最新修复清单

### ✅ 本轮新修复（4 项）

| 上一轮遗留问题 | 本轮修复证据 |
|---|---|
| `has_conflict` 用 `abs(diff)>1e-5` 判定为冲突，语义错误 | [matcher.py#L116-121](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L116-L121) 改为 `diff_ratio > tolerance` |
| `all_attrs_in_tolerance` 被 Flags 干扰 | [matcher.py#L465-467](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py#L465-L467) 只对数值/尺寸/电缆类字段计算 |
| 硬编码 `+0.08 / +0.08 / +0.12` 加分 | 已彻底删除，Grep 无匹配 |
| Phase 0 基线（`baseline_metrics_v0.0.4.md` + 金标集） | 均已建立（金标 197 条 < 500 条） |
| Phase 5 `/api/db/schema/stats` | [server.py#L1056-1065](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/server.py#L1056-L1065) 已实现 |
| Phase 6 字典变更**局部**重算 | [reparse_materials_db_partial](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L608-L680) 已支持 |
| `parsed_schema_version` 行级列 | [db_manager.py#L26-35](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/db_manager.py#L26-L35) 已加 + 自愈迁移 |
| 无冒号拼接属性无法提取 | [preprocessor.py#L215-223](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L215-L223) `concat_match` 已加 |
| 设备名误抽属性词 | [preprocessor.py#L407-410](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L407-L410) 白名单退避已加 |

### ❌ 仍未修复（6 项）

| 问题 | 现状 |
|---|---|
| **金标集扩到 500 条** | 现有 197 条，缺口 60.6% |
| **Top-1 相对提升 ≥30% 的对照基线** | 无历史版本对比数据 |
| `unit_norm.json` 缺 length / DN / mass 类换算 | 仅 airflow/pressure/power/voltage 4 类 |
| 字典距 §3.1 "≥95% 覆盖率" 要求仍差量级 | 无自动化覆盖率验证脚本 |
| `domain_extractors/` 按域拆子文件（fan.py/damper.py/…） | 仍是单一 [domain_extractors.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_extractors.py) |
| Bug #7 型号识别过窄 | [preprocessor.py#L88-L123 `extract_model`](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L88-L123) 仍是 6 条通用正则 |
| Bug #8 功率/尺寸贪心 | [preprocessor.py#L330-340](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/preprocessor.py#L330-L340) 兜底仍首匹配 |

---

## 四、V1 → V2 → V3 评分演进

| 维度 | V1 | V2 | V3 | 备注 |
|---|:---:|:---:|:---:|---|
| 架构骨架落地 | 4/5 | 4/5 | 4/5 | 稳定 |
| 配置驱动闭环 | 2/5 | 4/5 | 4/5 | Hard Gates 配置化早已完成 |
| Bug 修复彻底度 | 3/5 | 4/5 | **4.5/5** | ↑ `has_conflict` 修复 + 硬编码加分清理 |
| 可观测/可回归 | 1/5 | 1/5 | **4/5** | ↑ 金标集 + baseline 报告 + schema stats + partial reparse **全落地** |
| 代码卫生 | 3/5 | 3/5 | **3.5/5** | ↑ 老硬编码加分已删；型号/功率贪心仍存 |
| **综合** | **13/25 = 52%** | **16/25 = 64%** | **20/25 = 80%** | **+28% 累计提升** |

---

## 五、是否达到方案预期？

**结论：绝大部分达成，但仍有 3 项深水区未闭环。**

| 方案 §1 目标 | 状态 | 说明 |
|---|:---:|---|
| Top-1 相对提升 ≥30% | ❓ 未证明 | 缺改造前对照基线 |
| Top-3 命中率 ≥90% | ✅ 达标 | 实测 94.42% |
| 结构化率 ≥85% | ✅ 超标 | 实测 97.14% |
| 前端零改动 | ✅ 达标 | attrs/model 契约保留 + 别名兼容层 |

**方案 §3.1 前置门槛**：
- 字典基线覆盖率 ≥95% —— ❌ 未验证
- 结构化率口径定标 —— 🟡 已定义但 Must 字段样本量不足

---

## 六、下一优先动作（3 项）

1. **补齐金标集**：从 197 条扩到 500 条，且保证每条至少 2 个 `must_attrs`，让 `Must 字段准确率` 的分母达到 1000 以上。
2. **建立"改造前对照跑分"**：git 拉回改造前版本或制作模拟版本，对同一批金标做对照跑，输出 `Top-1 相对提升率` 数字，才能证明 §1 相对提升目标。
3. **补齐深水区**：
   - 型号识别按域拆分（`domain_extractors/fan.py` 定制风机型号族正则等）
   - 功率抽取按 Key 语义拆分为"电机功率/加热功率/制冷输入功率"多字段
   - 字典扩容至方案 §3.1 "95% 覆盖率" 要求

---

## 七、一句话总结

> **这一轮是本项目最实质的一次进步——把"可度量、可回归、可运维"这三块拼图补齐了。**
> 报告 [walkthrough.md](file:///c:/Users/wangd/project/ListTrans/walkthrough.md) 描述基本属实，代码确实落地；
> 但报告在"达标"这一表述上略偏乐观（用绝对值 92.89% 覆盖了方案要求的"相对提升 30%"）。
> 距离方案 V1.0 完整达成还差 3 项工作。

---

> 本次评估未修改任何代码。
