# 线上缺陷根因分析：上传清单比对崩溃 `UnboundLocalError: q_fan_vol`

| 项 | 内容 |
| :--- | :--- |
| 缺陷日期 / 分析日期 | 2026-09-02 |
| 现象 | 上传清单时「比对处理失败: cannot access local variable 'q_fan_vol' where it is not associated with a value」 |
| 缺陷类型 | `UnboundLocalError`（局部变量未绑定） |
| 崩溃位置 | [app/matcher.py:1441](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L1441)，函数 `score_candidate` |
| 发现方式 | 本地运行 V0.1.3 实际上传清单比对时触发 |
| 根因归类 | **V0.1.3 开发引入的回归**（git blame 坐实，引用块为未提交新代码） |
| 状态 | 仅定位与取证，**未做任何代码修改** |

---

## 一、完整报错堆栈（已复现）

复现脚本：`scratch/repro_q_fan_vol.py`
原始日志：`doc/V0.1.3/20260902_上传清单比对失败_q_fan_vol_traceback.log`

```text
CRASHED: UnboundLocalError - cannot access local variable 'q_fan_vol' where it is not associated with a value

Traceback (most recent call last):
  File "C:\Users\wangd\project\ListTrans\scratch\repro_q_fan_vol.py", line 43, in <module>
    score, reasons = score_candidate(query_info, candidate, semantic_score=0.99)
  File "c:\Users\wangd\project\ListTrans\app\matcher.py", line 1441, in score_candidate
    if q_fan_vol is not None and c_attrs.get("风量") is not None:
       ^^^^^^^^^
UnboundLocalError: cannot access local variable 'q_fan_vol' where it is not associated with a value
```

---

## 二、根因（变量作用域错配）

`q_fan_vol` 的**赋值**与**引用**处在两个不同的条件分支里：

**赋值点** —— [matcher.py:1247-1252](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L1247-L1252)：

```python
if is_q_fan and is_c_fan:          # ← 必须“查询是风机 且 候选也是风机”
    base_score += 0.20
    reasons.append("通风机品类匹配")
    # a. Air volume interval matching
    q_fan_vol = q_attrs.get("风量")   # ← q_fan_vol 仅在此块内赋值
```

**引用点** —— [matcher.py:1434-1447](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L1434-L1447)：

```python
if is_q_fan:                       # ← 只要求“查询是风机”，不要求候选是风机
    ...
    if q_fan_vol is not None and c_attrs.get("风量") is not None:   # 1441：访问 q_fan_vol
        ...
```

当 **`is_q_fan=True` 但 `is_c_fan=False`**（查询是排风机，但召回的候选不是通风机品类）时：
- 第 1247 行的块被整体跳过 → `q_fan_vol` 从未赋值；
- 第 1434 行 `if is_q_fan:` 仍然成立 → 第 1441 行访问 `q_fan_vol` → **`UnboundLocalError`**。

Python 中只要函数体内某处对变量有赋值，该变量即被视为整个函数的局部变量；在赋值前的任何分支引用它都会抛 `UnboundLocalError`。

---

## 三、为什么同函数里的 `q_fan_press` 不报错（对照证据）

同为风机变量，`q_fan_press` 在**函数顶部做了预初始化**，因此安全：

[matcher.py:997-1001](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L997-L1001)：

```python
q_is_ex = False
c_is_ex = False
c_is_non_ex = False
q_fan_press = None      # ← 有顶部预初始化
is_q_fan = False        # ← 有顶部预初始化
# 注意：此处唯独缺少 q_fan_vol = None
```

即：开发者在顶部初始化了 `q_fan_press`、`is_q_fan` 等变量，**遗漏了 `q_fan_vol`**。这是一处典型的「同类型变量初始化清单漏项」。

---

## 四、触发条件（完整链路）

复现需同时满足：

1. 查询物料被判定为风机：`is_q_fan=True`
   （名称含 排风机/送风机/静音型排风机/离心风机/轴流风机/通风机，或 `domain=="fan"` 等，见 [matcher.py:1236](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L1236)）
2. 召回候选**不是**风机：`is_c_fan=False`
   （候选名称不含「离心风机/通风机」、category 不含「通风机」、编码不以 `3.50.29.` 开头，见 [matcher.py:1237](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L1237)）
   → 跳过 1247 块，`q_fan_vol` 未赋值
3. 该候选打分进入**饱和 tiebreak 分支**：`base_score >= 0.999`（[matcher.py:1413](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L1413)）
4. 且非「名称规格完全一致」（避开 100.0 分支）、非「型号精确匹配」（避开 99.8 分支）→ 进入 else tiebreak（[matcher.py:1418](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L1418)）
5. 执行到 [matcher.py:1441](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L1441) 访问 `q_fan_vol` → 崩溃

**业务含义**：上传的清单里有风机类物料，而向量/关键词召回把某条「非通风机但规格文本高度相似（足以打到 0.999 饱和）」的物料也召回进候选，对它打分时即崩溃。这在风机清单比对中是现实会出现的错召场景，并非极端边角。

---

## 五、版本归因（git blame 坐实）

```
matcher.py:1252  q_fan_vol = q_attrs.get("风量")
  → 13679b85  BG6WEZ 2026-08-18  feat(v0.1.2): 物料库改版...（V0.1.2 提交）
     （赋值端的作用域隐患在 V0.1.2 即存在，但当时无外部引用，不可达）

matcher.py:1441-1453  if q_fan_vol is not None ...（风量/余压 tiebreak 微调块）
  → 00000000  Not Committed Yet 2026-09-02  （V0.1.3 工作区新增，尚未提交）

matcher.py:1434-1440  if is_q_fan: ... q_fan_press ...
  → 13679b85  V0.1.2 提交（只引用已初始化的 q_fan_press，本身安全）
```

**结论**：
- 赋值端的作用域缺陷是 V0.1.2 埋下的隐患；
- 但真正**让该隐患变得可达并崩溃的，是 V0.1.3 新增的 1441-1453 行 tiebreak 微调块**——它在 `if is_q_fan:`（未限定 `is_c_fan`）作用域内引用了仅在 `if is_q_fan and is_c_fan:` 内赋值的 `q_fan_vol`。
- 因此本次线上崩溃应归为 **V0.1.3 开发引入的回归**，且**未被 71 项自动化测试覆盖**（没有「风机查询 × 非风机饱和候选」的用例）。

---

## 六、影响面

- **功能影响**：只要清单中存在触发上述条件的风机行，整行/整批比对在 `score_candidate` 阶段抛异常，前端表现为「比对处理失败」，该行无法产出匹配结果。
- **数据影响**：纯打分阶段异常，不写库、不改配置，**无数据污染风险**；服务进程不中断（请求级异常）。
- **隐蔽性**：仅在「风机查询 + 非风机高相似候选 + 饱和打分」三条件同时成立时触发，常规风机↔风机匹配不受影响，因此常规测试与黄金集（多为同类正样本）难以命中。

---

## 七、修复方向与验证标准（建议，暂不实施）

**修复方向（二选一，推荐前者，与 `q_fan_press` 保持一致）**：

1. 在函数顶部初始化区 [matcher.py:997-1001](file:///c:/Users/wangd/project/ListTrans/app/matcher.py#L997-L1001) 补一行 `q_fan_vol = None`，与 `q_fan_press = None` 对齐。
2. 或将 1441 行引用收敛进与赋值相同的条件（`if is_q_fan and is_c_fan:`），但这会改变 tiebreak 语义，需评估。

**验证标准**：

1. 复现脚本 `scratch/repro_q_fan_vol.py` 修复后输出 `NO CRASH` 且返回正常分数；
2. 新增一条单元测试：风机查询 + 非风机高相似候选，断言不抛异常且 `is_c_fan=False` 路径被覆盖；
3. 全量 `pytest tests/ -q` 保持 71 passed 不回归；
4. 本地实际上传一份含「排风机」的清单，比对成功出结果。

---

## 八、取证产物清单

| 文件 | 说明 |
| :--- | :--- |
| `doc/V0.1.3/20260902_上传清单比对失败_q_fan_vol_traceback.log` | 复现脚本的完整 traceback 原始输出 |
| `scratch/repro_q_fan_vol.py` | 最小复现脚本（可重复执行） |
| 本文档 | 根因、触发条件、版本归因、修复建议与验证标准 |
