# Query端（原始工程清单）数据清洗及结构化方案报告 (v1.2 完美收敛版)

> **编写与修订日期**：2026-07-24  
> **方案状态**：**技术方案设计稿（待批准）**，无用户批准前不得进行任何代码修改  
> **前置输入**：
> - [query_data_portrait.md](./query_data_portrait.md) (Query 端数据画像 v1.1 精确版)
> - [query_data_portrait_review.md](./query_data_portrait_review.md) (数据画像评审报告)
> - [query_cleaning_and_structuring_plan_review.md](./query_cleaning_and_structuring_plan_review.md) (方案评审报告)
> - [final_structuring_master_plan.md](./final_structuring_master_plan.md) (后端物料库结构化主方案 V1.0 Final)
>
> **目标**：建立与后端 30 域 Schema 完全对称的 Query 端预处理与结构化提取流水线，消除清洗噪音与属性歧义，实现 **域路由成功率 $\ge 95\%$**、**Must 字段抽取率（非空规格下）$\ge 90\%$**、**Top-1 精确匹配率相对提升 $\ge 30\%$**、**Top-3 综合命中率 $\ge 90\%$**。

---

## 目录

- [第一章 数据前提与数据清洗层方案 (IR-0 & IR-1)](#第一章-数据前提与数据清洗层方案-ir-0--ir-1)
- [第二章 完整 30 域路由矩阵设计 (QueryDomainRouter)](#第二章-完整-30-域路由矩阵设计-querydomainrouter)
- [第三章 Query端分域结构化抽取引擎 (IR-2 Engine)](#第三章-query端分域结构化抽取引擎-ir-2-engine)
- [第四章 双端 Schema 对齐与匹配打分适配器](#第四章-双端-schema-对齐与匹配打分适配器)
- [第五章 数据链路与系统集成落盘设计](#第五章-数据链路与系统集成落盘设计)
- [第六章 实施计划与多维效果验证方案](#第六章-实施计划与多维效果验证方案)

---

# 第一章 数据前提与数据清洗层方案 (IR-0 & IR-1)

## 1.1 数据前提与统计口径对齐 (Data Baseline)

为确保实施阶段“目标可达、规则完全覆盖”，本方案基于 [query_data_portrait.md](./query_data_portrait.md) v1.1 精确版及评审结论确定以下统一数据前提：

* **原始扫描数据**：24 个 Excel 文件（161 个 Sheet），共 17,767 个单元格行，其中**非空原始行数为 15,173 行**。
* **清洗后有效清单条目**：经过 IR-0 扩展规则过滤后，得到 **12,715 条有效 Query 清单项**。
* **规格填充率**：相对 12,715 条有效清单，**非空规格条数为 7,773 条（占比 61.13%）**；**空/极短规格条数为 4,942 条（占比 38.87%）**。
* **非空规格结构化特征**：在 **7,773 条非空规格** 中，**换行符 `\n` 覆盖率达 99.60%**，**冒号 `:` 覆盖率达 99.60%**，**项目列表编号覆盖率达 99.78%**。

```
Raw Excel (17,767 行 / 非空 15,173 行)
   │
   ▼ 【IR-0 扩展过滤 (10条规则)】 ──► 剔除 2,458 行非清单干扰行
   │
   ▼ 12,715 条有效清单
   │
   ├─► 61.13% (非空规格 7,773 条)  ──► 【IR-2 多行 KV 抽取器 (正则/规则池)】
   └─► 38.87% (空/极短规格 4,942 条) ──► 【NameSpecExtractor 名称反向补全】
```

## 1.2 IR-0 扩展过滤规则 (10 组精确规则)

为确保剔除 ~2,458 行非清单节点且结果完全可复现，设置 10 组扩展硬过滤规则：

```python
# IR-0 扩展过滤规则 (10 组规则伪代码)
def is_valid_query_item(name, spec, unit, code, row_index):
    name_str = str(name).strip() if pd.notna(name) else ""
    spec_str = str(spec).strip() if pd.notna(spec) else ""
    unit_str = str(unit).strip() if pd.notna(unit) else ""
    code_str = str(code).strip() if pd.notna(code) else ""
    
    # 规则 1: 全空行 / openpyxl 孤立全 None 行
    if not name_str and not spec_str:
        return False, "R1_EMPTY_ROW"
        
    # 规则 2: 汇总 / 小计 / 合计行
    if name_str in ["小计", "合计", "总计", "合计：", "小计："]:
        return False, "R2_TOTAL_ROW"
        
    # 规则 3: 跨 Sheet 重复出现二次表头行
    if name_str in ["名称", "*名称", "项目名称", "构件名称", "材料(设备)名称"]:
        return False, "R3_REPEAT_HEADER_ROW"
        
    # 规则 4: 施工与前言说明文本行
    if any(k in name_str for k in ["编制依据", "图纸版本号", "图纸号", "计量依据", "设计说明"]):
        return False, "R4_PREFACE_NOTE_ROW"
        
    # 规则 5: 大纲章节导航标题行 (无规格且无单位/工程量)
    if (re.match(r'^[一二三四五六七八九十]+\s*[、\.]', name_str) or re.match(r'^\d+\.\d+\s+', name_str)) and not unit_str and not spec_str:
        return False, "R5_OUTLINE_TITLE_ROW"
        
    # 规则 6: 单单元格纯段落说明行 (长度 > 100 且无工程参数)
    if len(name_str) > 100 and not unit_str and not spec_str:
        return False, "R6_LONG_TEXT_NOTE_ROW"
        
    # 规则 7: 表尾落款行 (编制人 / 审核人 / 编制日期)
    if any(k in name_str for k in ["编制人:", "审核人:", "编制日期:", "制表人:"]):
        return False, "R7_FOOTER_NOTE_ROW"
        
    # 规则 8: 纯工程量计算公式行 (名称为空，Spec 为纯算式或数字)
    if not name_str and re.match(r'^[0-9\+\-\*\/\(\)\.\=]+$', spec_str):
        return False, "R8_FORMULA_CALC_ROW"
        
    # 规则 9: 拆散合并单元格导致的无名称孤立数值行
    if not name_str and not code_str and len(spec_str) < 3 and not unit_str:
        return False, "R9_MERGED_CELL_ORPHAN_ROW"
        
    # 规则 10: 特殊表样分割线 (包含 "=====" 或 "-----")
    if name_str.startswith("===") or spec_str.startswith("==="):
        return False, "R10_DIVIDER_ROW"
        
    return True, "VALID_ITEM"
```

## 1.3 IR-1 文本规整与防误伤策略

1. **字符集规范化**：
   * 全角字符转半角：`：` $\rightarrow$ `:`, `；` $\rightarrow$ `;`, `（）` $\rightarrow$ `()`, `，` $\rightarrow$ `,`
   * 尺寸与乘号规范化：将 `X`, `x`, `×`, `*` 统一规整为标准乘号 `*`。
2. **前缀序号剥离（含单位白名单防误伤）**：
   * **白名单保护**：当数字后紧跟 `mm` / `A` / `kW` / `DN` / `m²` / `*` / `℃` 时，**严禁剥离**（如 `2.5mm²`、`70℃常开防火阀`）。
   * **安全剥离**：仅对后接空格或纯中文的纯序号执行剥离（如 `1.1 配电箱` $\rightarrow$ `配电箱`）。
3. **名称与规格轻量解耦 (Sub-Step)**：
   * 针对 ~3% 名称混入规格的记录（如 `无缝钢管 DN100`），正则表达式匹配到规格后提取填入 Spec 域，名称还原为 `无缝钢管`。
4. **属性级干扰噪音全面屏蔽**：
   * 彻底屏蔽出现 7,469 次的 **`工作内容`**、5,747 次的 **`重新重复名称`** 与 571 次的 **`备注`**，仅保留物理/电气/性能属性。

---

# 第二章 完整 30 域路由矩阵设计 (QueryDomainRouter)

为兑现“双端 Schema 完全对称”原则，Query 端建立包含 **完整 30 个 Domain** 的路由矩阵，摒弃模糊兜底。

## 2.1 三级加权路由算法 (Three-Tier Weighted Router)

```
Query Item (Name, Spec, Unit, SheetName)
   │
   ▼
[Tier 1] 规格正文与名称强特征重叠匹配 (High-Priority Keywords)
   ├─ 命中强特征（如 "消声器" / "YJV" / "H13"） ──► 直接确定目标 Domain (置信度 1.0)
   │
   ▼ (未命中 Tier 1)
[Tier 2] 名称核心词字典树匹配 (Head Noun Match)
   ├─ 匹配到名称受控字典 ──────────────────────────► 确定目标 Domain (置信度 0.85)
   │
   ▼ (未命中 Tier 2)
[Tier 3] Sheet 名称 / 专业 Hint 先验概率向量融合 (Probabilistic Hint Vector)
   ├─ 计算 Sheet 先验概率向量, 例: {hvac:0.4, elec:0.4, deco:0.2}
   └─ 融合名称次要特征词选出最高概率 Domain (置信度 0.60)
```

## 2.2 完整 30 域路由映射矩阵 (Full 30 Domain Matrix)

| 序号 | 域标识 (`domain`) | 域名中文名称 | 核心路由特征词（规格正文 / 名称） | 物料库对齐 30 域 Schema |
|---:|:---|:---|:---|:---|
| 1 | `fan_hvac` | 通风机域 | 包含 `风机`、`离心风机`、`轴流风机`、`风量`+`余压` | 通风机 |
| 2 | `valve_hvac` | 暖通阀门域 | 包含 `防火阀`、`调节阀`、`排烟阀`、`风阀` | 调节阀、防火阀 |
| 3 | `cable_power` | 电力电缆域 | 包含 `电缆`、`YJV`、`YJV22`、`WDZ` + 多芯 `4*` | 电气装备用电线电缆 |
| 4 | `cable_control` | 控制电缆域 | 包含 `控制电缆`、`KVV`、`KVVR`、`RVVP` | 电气装备用电线电缆(控制) |
| 5 | `wire` | 电线域 | 包含 `电线`、`BV`、`BVR`、`BYJ`（单芯/小截面） | 电气装备用电线电缆(电线) |
| 6 | `silencer` | 消声器域 | 包含 `消声器`、`消声弯头`、`阻抗复合`、`微穿孔` | 消声器 |
| 7 | `air_filter` | 净化过滤设备域 | 包含 `高效过滤器`、`中效过滤器`、`H13`、`H14` | 净化过滤设备 |
| 8 | `header_steam` | 汽水集配器域 | 包含 `集配水器`、`分水器`、`集水器`、`缸体直径` | 汽水集配器 |
| 9 | `plenum` | 静压箱域 | 包含 `静压箱`、`消声静压箱` | 静压箱 |
| 10 | `chiller` | 冷热水机组域 | 包含 `冷水机组`、`冷热水机组`、`制冷量`+`kW` | 冷热水机组 |
| 11 | `air_terminal` | 风口/终端域 | 包含 `风口`、`散流器`、`百叶`、`网孔板风口` | 调节阀(漂移)、风口 |
| 12 | `door_special` | 特种门域 | 包含 `气密门`、`自动趟门`、`防火门`、`医用门` | 特种门、钢门窗 |
| 13 | `ahu` | 洁卫型空调机组域| 包含 `AHU`、`组合式空调机组`、`洁卫型空调` | 医用洁卫型空调机组 |
| 14 | `med_gas` | 医用气体设备域 | 包含 `医气`、`气体终端`、`氧气`、`负压吸引` | 医用气体设备 |
| 15 | `pipe_stainless` | 不锈钢管域 | 包含 `不锈钢管`、`304`、`316L` + `DN` | 不锈钢管 |
| 16 | `pipe_water` | 给排水管道域 | 包含 `PPR`、`PVC-U`、`衬塑钢管`、`排水管` | 塑料管、钢管 |
| 17 | `valve_water` | 给水阀门域 | 包含 `闸阀`、`蝶阀`、`球阀`、`截止阀` + `PN` | 蝶阀、闸阀、球阀 |
| 18 | `distribution_box`| 配电箱域 | 包含 `配电箱`、`照明箱`、`控制箱`、`配电柜` | 配电箱、开关柜 |
| 19 | `clean_room` | 净化彩钢板/面域 | 包含 `彩钢板`、`手工板`、`岩棉板`、`同透PVC` | 净化装饰材料 |
| 20 | `lighting` | 灯具照明域 | 包含 `平板灯`、`泪珠灯`、`应急灯`、`LED` | 灯具 |
| 21 | `switch_socket` | 开关插座域 | 包含 `插座`、`开关`、`五孔`、`86型` | 开关插座 |
| 22 | `bridge_raceway` | 桥架线槽域 | 包含 `桥架`、`槽式`、`梯级`、`网格` | 槽式桥架、梯级桥架 |
| 23 | `conduit_elec` | 电气配管域 | 包含 `JDG`、`KBG`、`PVC配管`、`穿线管` | 电气配管 |
| 24 | `floor_tile` | 瓷砖/地面域 | 包含 `地砖`、`釉面砖`、`防滑砖`、`踢脚线` | 块料面层/地砖 |
| 25 | `wall_paint` | 涂料饰面域 | 包含 `无机涂料`、`乳胶漆`、`防腐漆` | 刷喷涂料/墙面 |
| 26 | `ceiling_board` | 吊顶天棚域 | 包含 `铝扣板`、`石膏板`、`矿棉板`、`窗帘盒` | 吊顶天棚材料 |
| 27 | `sanitary_ware` | 洁具水暖域 | 包含 `水龙头`、`洗手盆`、`拖布池`、`地漏` | 卫生洁具 |
| 28 | `sealant` | 密封胶/辅材域 | 包含 `密封胶`、`玻璃胶`、`打胶`、`结构胶` | 密封防火胶 |
| 29 | `insulation` | 保温材料域 | 包含 `橡塑`、`玻璃棉`、`岩棉保温` | 保温材料 |
| 30 | `generic_civil` | 通用土建装饰域 | 仅在以上 29 域均未命中时的综合兜底 | 综合土建饰面 |

---

# 第三章 Query端分域结构化抽取引擎 (IR-2 Engine)

在路由至目标 30 域之一后，启动对应的 **IR-2 分域抽取引擎**。

## 3.1 修复 Bug 后的 KV 属性切割器 (KV Line Parser v1.2)

修正贪婪匹配 Bug，并将 Key 末尾的单位括号捕获为独立的单位字段 `unit`：

```python
# KV Line Parser v1.2 伪代码
def parse_raw_spec_to_kv(spec_text):
    kv_dict = {}
    lines = [line.strip() for line in spec_text.split('\n') if line.strip()]
    for line in lines:
        # 正则优化：非贪婪 match + 捕获组 group(2) 提取括号内单位 (如 "制冷量(kW): 120")
        match = re.match(r'^(?:\d+[\.\、\s]|（\d+）|\(\d+\)|[①-⑩])?\s*([\u4e00-\u9fa5A-Za-z0-9_]+?)(?:\s*[（\(]([^）\)]*)[）\)])?\s*[:：]\s*(.*)$', line)
        if match:
            k = match.group(1).strip()
            unit_in_paren = match.group(2).strip() if match.group(2) else ""
            v = match.group(3).strip()
            # 剔除末尾误截数字
            k = re.sub(r'^\d+', '', k)
            if k in ["工作内容", "名称", "备注", "其他"]:
                continue
            # 若含有括号单位，保存结构化字典字典 {value, unit}，否则存字符串 v
            kv_dict[k] = {"value": v, "unit": unit_in_paren} if unit_in_paren else v
        else:
            kv_dict.update(fallback_regex_extract(line))
    return kv_dict
```

## 3.2 单位与量级强制归一化 (Unit Scale Conversion)

对提取出的物理量强制进行单位换算并存为 `float` / `int`：

| 物理维度 | 统一标准单位 | 换算逻辑 (Conversion Scaling) |
| :--- | :--- | :--- |
| **风量 (Air Volume)** | `m³/h` | `CFM` $\times 1.699$ \| `l/s` $\times 3.6$ \| `m³/h` $\times 1.0$ |
| **压力/余压 (Pressure)**| `Pa` | `kPa` $\times 1000$ \| `MPa` $\times 1000000$ \| `mmH2O` $\times 9.80665$ |
| **功率/冷量 (Power)** | `kW` | `W` $\times 0.001$ \| `HP` (匹) $\times 0.735$ \| `kcal/h` $\times 0.001163$ |
| **公称通径 (Pipe DN)** | `DN (mm)` | `DN100` $\rightarrow 100.0$ \| `4寸` $\rightarrow 100.0$ *(JIS 映射)* |
| **几何尺寸 (Dimension)**| `mm` | 2D: `500*300` $\rightarrow W=500.0, H=300.0$ \| 3D: $W, H, L$ |
| **线缆截面 (Cable Area)**| `mm²` | `4*6+1*4` $\rightarrow$ 主芯$=4$, 主截面$=6.0$, 零芯$=1$, 零截面$=4.0$ |

## 3.3 无规格清单（38.87% 空规格）反向补全机制

针对占比 38.87%（4,942 条）规格为空或极短的记录（特别是专项材料发包包）：

1. 当 `Spec` 为空时，激活 **`NameSpecExtractor`**。
2. 从名称（如 `同透PVC卷材 2.0mm`）中提取参数：
   * 尺寸 $\rightarrow$ `width/height`
   * 厚度 $\rightarrow$ `thickness=2.0`
   * 材质 $\rightarrow$ `material="PVC"`
3. 将提取参数反填进 JSON 对象，确保参与结构化打分。

---

# 第四章 双端 Schema 对齐与匹配打分适配器

与 [final_structuring_master_plan.md](./final_structuring_master_plan.md) 后端打分机制保持完全对齐：

1. **Must 核心字段硬门槛 (Hard Gate)**：物理参数严重偏离时（如 DN25 匹配到 DN100），执行硬门槛阻断 (-0.40 分)。
2. **Should 辅助字段“有则加分，无则不扣”**：对齐后端主方案的软加分策略。只在 Query 端明确提供且与后端产生**明确矛盾冲突**时扣分。

---

# 第五章 数据链路与系统集成落盘设计

```
Query Line (Excel / API)
   │
   ▼
[QueryPreprocessPipeline]
   ├── IR-0 行过滤 ──► [FilterLog]
   ├── IR-1 规整
   ├── QueryDomainRouter ──► 确定 30 域之一
   └── IR-2 Engine ──► 输出 Query IR-2 JSON
   │
   ▼
[数据落盘与缓存层]
   ├── 离线持久化 ──► SQLite `query_structured_item` 表 (存 json_data, domain, must_valid)
   └── 在线匹配 ────► 实时输入 matcher.py 运行打分
```

---

# 第六章 实施计划与多维效果验证方案

## 6.1 人工标注真集与评估数据集 (Evaluation Dataset)

* **评测真集来源**：复用项目已有的 [ListTrans/doc/真集/](./真集/) 标注验证文件。
* **数据规模**：1,000 条包含标准物料编码映射的精标注 Query 样本。

## 6.2 多维评估指标口径 (Disaggregated Metrics)

拆分原指标为 4 项具体可衡量的可达目标：

| 评估指标 | 目标值 | 统计口径与计算公式 |
| :--- | :---: | :--- |
| **域路由成功率** | **$\ge 95\%$** | $\frac{\text{准确分发至正确 30 域的 Query 条数}}{\text{评估真集总条数}}$ |
| **Must 字段抽取率 (非空规格)** | **$\ge 90\%$** | $\frac{\text{成功抽取完整 Must 字段的非空 Query 条数}}{\text{非空规格 Query 总条数 (7,773)}}$ |
| **Top-1 精确匹配相对提升** | **$\ge 30\%$** | 相较现有 Baseline 系统 Top-1 精确命中率的相对提升幅度 |
| **Top-3 综合召回率** | **$\ge 90\%$** | $\frac{\text{前 3 个候选物料中包含正确真集编码的条数}}{\text{评估真集总条数}}$ |

---

*以上为 Query 端数据清洗及结构化方案 v1.2 完美收敛版。*
