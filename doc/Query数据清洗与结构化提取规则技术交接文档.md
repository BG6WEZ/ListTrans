# Query端数据清洗与结构化提取规则技术交接文档

> **文档版本**：v1.0 (生产交付标准版)  
> **编写日期**：2026-10-20  
> **适用范围**：工程清单原始数据清洗、文本规整、30 域路由分类、结构化参数抽取与物料匹配适配层  
> **代码基线**：`app/query_preprocessor.py`, `app/domain_router.py`, `app/domain_extractors.py`, `app/normalizer.py`  
> **配置基线**：`config/domain_schema.json`, `config/category_domain_map.json`, `config/unit_norm.json`  

---

## 目录

- [一、 系统定位与总体处理链路](#一-系统定位与总体处理链路)
  - [1.1 业务背景与问题痛点](#11-业务背景与问题痛点)
  - [1.2 Query 端总体处理流水线架构](#12-query-端总体处理流水线架构)
- [二、 IR-0 扩展数据清洗与硬过滤规则 (10 组精确规则)](#二-ir-0-扩展数据清洗与硬过滤规则-10-组精确规则)
  - [2.1 过滤接口定义](#21-过滤接口定义)
  - [2.2 10 组精确过滤规则明细与判定机制](#22-10-组精确过滤规则明细与判定机制)
  - [2.3 异常拦截与过滤日志规范](#23-异常拦截与过滤日志规范)
- [三、 IR-1 文本规整与防误伤策略](#三-ir-1-文本规整与防误伤策略)
  - [3.1 字符集与标点标准化](#31-字符集与标点标准化)
  - [3.2 序号剥离与单位白名单防误伤机制](#32-序号剥离与单位白名单防误伤机制)
  - [3.3 名称与规格轻量解耦机制](#33-名称与规格轻量解耦机制)
  - [3.4 干扰字段切块与噪音屏蔽](#34-干扰字段切块与噪音屏蔽)
  - [3.5 KV 块属性解析器 v1.2 (含括号单位捕获)](#35-kv-块属性解析器-v12-含括号单位捕获)
- [四、 30 域三级加权路由决策引擎 (QueryDomainRouter)](#四-30-域三级加权路由决策引擎-querydomainrouter)
  - [4.1 三级路由架构设计](#41-三级路由架构设计)
  - [4.2 Tier 1 强特征词重叠匹配规则](#42-tier-1-强特征词重叠匹配规则)
  - [4.3 Tier 2 名称核心词受控字典匹配](#43-tier-2-名称核心词受控字典匹配)
  - [4.4 Tier 3 Sheet 名称与专业 Hint 概率向量融合](#44-tier-3-sheet-名称与专业-hint-概率向量融合)
  - [4.5 完整 30 域映射矩阵表](#45-完整-30-域映射矩阵表)
- [五、 IR-2 分域结构化参数抽取规则与核心算法](#五-ir-2-分域结构化参数抽取规则与核心算法)
  - [5.1 配置驱动抽取引擎 (ConfigDrivenExtractor)](#51-配置驱动抽取引擎-configdrivenextractor)
  - [5.2 复合数值解析器 (parse_numeric_complex)](#52-复合数值解析器-parse_numeric_complex)
  - [5.3 暖通空调与设备类 (HVAC) 专有提取规则](#53-暖通空调与设备类-hvac-专有提取规则)
  - [5.4 电气强电与布线类 (Electrical) 专有提取规则](#54-电气强电与布线类-electrical-专有提取规则)
  - [5.5 给排水与工业管道类 (Plumbing & Piping) 专有提取规则](#55-给排水与工业管道类-plumbing--piping-专有提取规则)
  - [5.6 医用气体与特种设备类 (Medical Gas) 专有提取规则](#56-医用气体与特种设备类-medical-gas-专有提取规则)
  - [5.7 装饰洁净与建筑材料类 (Decoration & Cleanroom) 专有提取规则](#57-装饰洁净与建筑材料类-decoration--cleanroom-专有提取规则)
  - [5.8 无规格清单反向补全机制 (NameSpecExtractor)](#58-无规格清单反向补全机制-namespecextractor)
- [六、 物理量强制归一化与几何特征计算](#六-物理量强制归一化与几何特征计算)
  - [6.1 单位与量级强制换算标准](#61-单位与量级强制换算标准)
  - [6.2 材质与过滤等级归一化](#62-材质与过滤等级归一化)
  - [6.3 几何尺寸与衍生特征计算](#63-几何尺寸与衍生特征计算)
  - [6.4 三维公差箱体解析 (3D Tolerance Box)](#64-三维公差箱体解析-3d-tolerance-box)
  - [6.5 动态 LLM 规则引擎集成](#65-动态-llm-规则引擎集成)
- [七、 核心接口调用规范与数据输入输出结构](#七-核心接口调用规范与数据输入输出结构)
  - [7.1 主入口函数签名与调用时序](#71-主入口函数签名与调用时序)
  - [7.2 输入与输出 Schema 示例](#72-输入与输出-schema-示例)
- [八、 维护与扩展指引 (开发交接指南)](#八-维护与扩展指引-开发交接指南)
  - [8.1 如何新增或调整过滤规则](#81-如何新增或调整过滤规则)
  - [8.2 如何扩充新业务 Domain 或 Schema 字段](#82-如何扩充新业务-domain-或-schema-字段)
  - [8.3 常见避坑点与排错清单](#83-常见避坑点与排错清单)
  - [8.4 自动化测试执行与回归验证](#84-自动化测试执行与回归验证)

---

## 一、 系统定位与总体处理链路

### 1.1 业务背景与问题痛点

在工程清单物料匹配（ListTrans）系统中，用户上传的 Query 端原始清单具有以下特征：
1. **异构性强**：来自数百个不同的医院、洁净厂房与商业安装项目，Excel 格式不一，包含跨 Sheet 二次表头、小计合计、设计说明、合并单元格断片等。
2. **文字粘连与噪音严重**：大量规格文本中充斥着“工作内容：管道除锈、刷漆”、“施工部位：地下室一层”、“重新重复名称”等施工及造价噪音。
3. **规格分布两极化**：
   - 约 **61.13%** 含有长文本、多行 `Key: Value` 的详细项目特征；
   - 约 **38.87%** 为专项材料清单，其“规格”列完全为空或仅有几个字符，关键尺寸材质隐式嵌入在“名称”列中（如 `同透PVC卷材 2.0mm厚`）。
4. **单位与写法非标**：乘号混用（`*`、`x`、`X`、`×`）、全角半角混杂、管径英公制混写（`4寸`、`De110`、`DN100`）、前缀序号易误伤（如 `2.5mm²` 若被误当序号剥离成 `mm²` 会造成致命匹配错误）。

本模块的目标是**在物料打分匹配前，将粗糙、异构、多噪的原始清单行转换为标准、纯净、结构化的 30 域特征 JSON**。

### 1.2 Query 端总体处理流水线架构

```
                     原始工程清单行 (Excel / 表单)
               [Name, Specification, Unit, Code, SheetName]
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 阶段一：IR-0 扩展行级硬过滤 (is_valid_query_item)                         │
│ 10 组精确过滤规则：剔除全空行、小计合计、二次表头、前言说明、落款、纯算式等 │
└─────────────────────────────────┬──────────────────────────────────────┘
                                   │ PASS (VALID_ITEM)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 阶段二：IR-1 文本规整与解耦 (clean_query_text_ir1 & decouple)           │
│ 1. 全半角转换、标点归一、乘号规整 (X/x/× -> *)                          │
│ 2. 序号剥离 + 单位白名单保护 (严防 2.5mm²、70℃ 误剥离)                  │
│ 3. 名称/规格轻量解耦 (剥离名称中嵌入的 DN/De/截面/尺寸填入规格)          │
│ 4. 施工噪音切片隔离 (slice_query_sections 屏蔽工作内容等噪音)            │
│ 5. KV 块解析器 v1.2 (非贪婪捕获括号单位，如 "制冷量(kW): 120")         │
└─────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 阶段三：30 域三级加权路由引擎 (QueryDomainRouter)                         │
│ [Tier 1] 强特征词正文/名称精准重叠匹配 (置信度 1.0)                      │
│ [Tier 2] 名称核心词字典树匹配 (置信度 0.85)                            │
│ [Tier 3] Sheet 名称 / 专业 Hint 先验概率向量融合 (置信度 0.60)           │
└─────────────────────────────────┬──────────────────────────────────────┘
                                   │ 确定 Domain (如 "fan", "ahu", "cable_power")
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 阶段四：IR-2 分域结构化抽取与反向补全                                  │
│ 1. ConfigDrivenExtractor (基于 domain_schema.json 提取 Must/Should)     │
│ 2. NameSpecExtractor (对 38.87% 空规格从名称反向解析尺寸/厚度/材质)      │
│ 3. 专用域深度特征挖掘 (HVAC机组代号、电缆复合芯线、电气导管、风阀形式) │
└─────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 阶段五：物理量归一化、公差几何推导与动态规则集成                       │
│ 1. 单位量级统一 (CFM->m³/h, kPa->Pa, HP/W->kW, 4寸->DN100)             │
│ 2. 材质标准化 (316L->不锈钢316L, 优质镀锌->镀锌钢板)                   │
│ 3. 几何参数衍生 (长/宽 -> 半周长/截面积, 3D 公差箱体提取)               │
│ 4. LLMRuleManager 动态 DSL 规则热注入与覆写                            │
└─────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
                 标准化 Query IR-2 JSON 结构体 -> matcher.py
```

---

## 二、 IR-0 扩展数据清洗与硬过滤规则 (10 组精确规则)

### 2.1 过滤接口定义

源码文件：`app/query_preprocessor.py`  
函数签名：
```python
def is_valid_query_item(
    name: Any, 
    spec: Any, 
    unit: Any = "", 
    code: Any = "", 
    row_index: int = 0
) -> Tuple[bool, str]:
    """
    IR-0 扩展硬过滤规则检查
    返回值: (is_valid: bool, rule_reason: str)
    若 is_valid 为 True，rule_reason 为 "VALID_ITEM"；
    若为 False，rule_reason 为下述 10 组精确原因码之一。
    """
```

### 2.2 10 组精确过滤规则明细与判定机制

| 规则编码 (`rule_reason`) | 规则中文描述 | 触发判定条件与核心逻辑 | 典型拦截样本 |
| :--- | :--- | :--- | :--- |
| **`R1_EMPTY_ROW`** | 全空行 / 孤立 None 行 | `not name_str and not spec_str` | Excel 底部空行、读取产生的 `(None, None)` |
| **`R2_TOTAL_ROW`** | 汇总 / 小计 / 合计行 | `name_str in ["小计", "合计", "总计", "合计：", "小计：", "总计："]` | `小计`、`合计：`、`总计` |
| **`R3_REPEAT_HEADER_ROW`** | 跨 Sheet 二次重复表头 | `name_str in ["名称", "*名称", "项目名称", "构件名称", "材料(设备)名称", "材料名称", "设备名称"]` | 跨分页打印残留的 `*名称`、`项目名称` |
| **`R4_PREFACE_NOTE_ROW`** | 施工前言与设计说明行 | 包含：`"编制依据"`, `"图纸版本号"`, `"图纸号"`, `"计量依据"`, `"设计说明"` | `设计说明：按施工规范施工`、`编制依据：招标文件` |
| **`R5_OUTLINE_TITLE_ROW`** | 大纲章节导航标题行 | 正则匹配：`^[一二三四五六七八九十百]+[、\.]` 或 `^（[一二三四五六七八九十百]+）` 或 `^\(?[0-9\.]+\)?\s+` 或包含 `["系统", "工程", "分部", "标段", "部分", "阶段"]`；且 **`not spec_str and (not unit_str or is_outline_pattern) and not code_str`** | `一、暖通空调工程`、`1.1 洁净手术部装饰`、`强电标段` |
| **`R6_LONG_TEXT_NOTE_ROW`** | 单单元格纯说明长文本 | `len(name_str) > 100 and not unit_str and not spec_str` | 某行单元格写入了 150 字的综合合同发包条款且无参数 |
| **`R7_FOOTER_NOTE_ROW`** | 表尾落款与签字行 | 包含：`"编制人:"`, `"编制人："`, `"审核人:"`, `"审核人："`, `"编制日期:"`, `"编制日期："`, `"制表人:"`, `"制表人："` | `编制人：张三`、`审核人: 李四`、`编制日期：2026-06` |
| **`R8_FORMULA_CALC_ROW`** | 纯算式计算行 | `not name_str` 且存在运算符 `['+', '-', '*', '/', '=']` 且满足正则 `^[0-9\+\-\*\/\(\)\.\=\s]+$` | 名称为空，规格列为 `10+20*1.5=40`、`12.5*3` |
| **`R9_MERGED_CELL_ORPHAN_ROW`** | 合并单元格拆散孤立值 | `not name_str and not code_str and len(spec_str) < 3 and not unit_str` | 合并单元格拆散后遗留的孤立数字 `12`、`0` |
| **`R10_DIVIDER_ROW`** | 表格修饰分割线 | `name_str.startswith("===")` 或 `spec_str.startswith("===")` 或 `name_str.startswith("---")` 或 `spec_str.startswith("---")` | `===================`、`-------------------` |

### 2.3 异常拦截与过滤日志规范

在执行批量清单预处理时，过滤系统应遵守以下准则：
1. **行号追踪**：调用时传入 `row_index`，一旦判定无效，记录 `(row_index, item_name, rule_reason)`。
2. **零误伤原则**：`R5_OUTLINE_TITLE_ROW` 必须严格校验 `not spec_str`。若某清单项名称为“一、排风系统离心风机”，但规格列写有“风量5000m³/h”，**严禁拦截**，必须作为正常清单放行。

---

## 三、 IR-1 文本规整与防误伤策略

### 3.1 字符集与标点标准化

源码文件：`app/normalizer.py` 与 `app/query_preprocessor.py`

1. **全角转半角 (`to_half_width`)**：
   - 全角空格 `\u3000` 映射为 ASCII 空格 `\u0020`；
   - 字符编码在 `0xFF01` 至 `0xFF5E` 范围内的字符统一减去 `0xfee0`，将全角字母、数字、括号转化为半角；
   - 过滤 `\u0000-\u001f` 内的不可见控制字符（保留 `\n` 与 `\r`）。
2. **工程标点统一**：
   ```python
   t = t.replace("：", ":").replace("；", ";").replace("（", "(").replace("）", ")").replace("，", ",")
   ```
3. **尺寸乘号统一**：
   使用正则 `r'([0-9])\s*[xX×]\s*([0-9])'` 替换为 `r'\1*\2'`。例如 `600X600`、`300×300` 统一归一化为 `600*600`、`300*300`。

### 3.2 序号剥离与单位白名单防误伤机制

**业务痛点**：  
工程清单项目特征通常形如 `1. 材质: 304` 或 `1.1 配电箱安装`。若使用粗暴正则 `^\d+(\.\d+)?` 剥离前缀，会导致：
- `2.5mm² 铜芯电线` $\rightarrow$ 被切为 `mm² 铜芯电线`（丢失核心截面！）；
- `70℃常开防火阀` $\rightarrow$ 被切为 `℃常开防火阀`（丢失核心动作温度！）；
- `4.304 不锈钢管` $\rightarrow$ 被切为 `304 不锈钢管`（型号误变！）。

**实现方案 (`clean_query_text_ir1`)**：
引入**单位白名单保护判定**：
```python
# 单位保护白名单
whitelist_units = [
    "MM", "MM2", "MM²", "M2", "M²", "KW", "W", "A", "V", 
    "DN", "DE", "PA", "KPA", "MPA", "℃", "°C", "*", "寸", "P", "R"
]

# 序号正则匹配 (通常为 1~2 级数字编号)
m = re.match(r'^(\d{1,2}(?:\.\d{1,2})?)[\.．、\s]\s*(.*)$', line_str)
if m:
    rest = m.group(2).strip()
    rest_upper = rest.upper()
    # 检查紧跟在数字后面的字符是否命中白名单
    is_whitelisted = any(rest_upper.startswith(u) for u in whitelist_units)
    # 仅在非白名单且后接中文或字母时执行剥离
    if not is_whitelisted and (not rest or re.match(r'^[一-龥a-zA-Z_]', rest)):
        line_str = rest
```

### 3.3 名称与规格轻量解耦机制

**函数实现**：`decouple_name_and_spec(name: str, spec: str) -> Tuple[str, str]`  
**处理目标**：解决约 3.78% 的“规格嵌在名称列”的脏数据问题。

**解耦正则表达式**：
```python
spec_pattern = r'((?:DN|DE|De|Ф|Φ)\d+|\d+\s*\*\s*\d+(?:\s*\*\s*\d+)?|\b[A-Z]{2,}-\d+\*\d+|\d+(?:\.\d+)?\s*(?:mm2|mm²|平方))'
```
**解耦与回填逻辑**：
1. 若 `name` 中检索到该模式（如 `无缝钢管 DN100` 或 `防滑地砖 600*600`）：
   - 提取出参数 `DN100` 或 `600*600`；
   - 将 `name` 中的该部分剔除并清理多余符号，还原标准名称为 `无缝钢管`、`防滑地砖`；
   - 将提取出的参数拼入 `spec` 的最顶行：`DN100\n原规格`。

### 3.4 干扰字段切块与噪音屏蔽

**函数实现**：`slice_query_sections(raw_spec: str) -> Tuple[str, str]`  
**屏蔽的噪音关键词集合 (`NOISE_SECTION_KEYWORDS`)**：
```python
NOISE_SECTION_KEYWORDS = [
    "工作内容", "施工内容", "施工部位", "敷设方式", "安装方式", "包含完成该项",
    "单价暂按", "暂估", "全费用", "根据实际", "包工包料", "配合", "试压", "清理基层",
    "检验试验", "冲洗", "调试", "吹扫", "成品保护", "管道及管件安装", "此为成品安装价格",
    "重新重复名称", "备注"
]
```
**切分机制**：
对规格文本逐行扫描，一旦检测到某行以工作内容、施工内容、重新重复名称等关键字开头，状态机切换为 `in_noise = True`，后续所有行归入 `noise_block`，前面纯净的技术参数归入 `material_block`。

### 3.5 KV 块属性解析器 v1.2 (含括号单位捕获)

**函数实现**：`parse_query_kv_blocks(material_block: str) -> Dict[str, Any]`  
针对 99.6% 的 `Key: Value` 多行清单，采用非贪婪前缀与括号单位捕获正则：
```python
match = re.match(
    r'^(?:\d+[\.\、\s]|（\d+）|\(\d+\)|[①-⑩])?\s*([\u4e00-\u9fa5A-Za-z0-9_]+?)(?:\s*[（\(]([^）\)]*)[）\)])?\s*[:：]\s*(.*)$', 
    line
)
```
- `group(1)`：捕获属性键名（如 `风量`、`制冷量`、`机外余压`）；
- `group(2)`：捕获键名括号内标明的单位（如 `m³/h`、`kW`、`Pa`）；
- `group(3)`：捕获具体属性值（如 `5000`、`120`、`250`）。
- **生成结构**：若含有括号单位，生成 `{"value": v, "unit": unit_in_paren}`，供后续单位量级归一化直接调用。

---

## 四、 30 域三级加权路由决策引擎 (QueryDomainRouter)

### 4.1 三级路由架构设计

源码文件：`app/domain_router.py`  
函数入口：`route_domain(category: str, name: str, specification: str, professional_hint: str = "") -> str`

三级加权路由严格遵循**置信度从高到低逐级落焦**原则，严禁模糊粗暴兜底。

```
                    Query 文本 (Category, Name, Spec)
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tier 1: 强特征词正文/名称精准覆盖 (Confidence: 1.0)               │
│ • 优先匹配 config/category_domain_map.json 中的 overwrite_rules │
│ • 强特征设备/器件：消声器、静压箱、YJV/WDZ、H13/H14/HEPA、      │
│   14 类 HVAC 机组代号 (MAU/AHU/FCU/PAU)、JDG/KBG 电气导管等     │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ 未命中
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tier 2: 名称核心词受控字典匹配 (Confidence: 0.85)                │
│ • 电缆细分：截面/芯数 -> cable_power vs cable_control vs wire    │
│ • 管道细分：不锈钢管/紫铜管/塑料管/无缝钢管                     │
│ • 阀门细分：暖通风阀(damper) vs 医气阀 vs 给水阀(valve_water)    │
│ • 其他品类字典：开关柜、桥架、灯具、洁具、洁净板、装饰面层等     │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ 未命中
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tier 3: Sheet 名称/专业 Hint 概率向量融合 (Confidence: 0.60)     │
│ • compute_sheet_hint_vector: 投影至暖通/电气/水/医气/装饰 5 维   │
│ • 结合正文次要特征词 (管/阀/线/箱) 综合选出概率最高的 Domain     │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ 最终兜底
                                  ▼
                             generic (通用)
```

### 4.2 Tier 1 强特征词重叠匹配规则

1. **暖通调节风阀 (`damper`)**：
   - 触发特征词：`"调节风阀"`, `"多叶调节"`, `"防火阀"`, `"排烟防火阀"`, `"止回风阀"`, `"密闭风阀"`, `"70℃"`, `"280℃"`, `"多叶排烟口"` 等。
2. **消声与静压箱 (`silencer` / `plenum`)**：
   - 消声器：`"消声器"`, `"消声弯头"`, `"消音器"`, `"消声百叶"` $\rightarrow$ `silencer`；
   - 静压箱：`"静压箱"`, `"消声静压箱"`, `"集风箱"`, `"稳压箱"` $\rightarrow$ `plenum`。
3. **暖通机组类 (`ahu`)**：
   - 中文特征：`全新风空气处理机组`, `组合式空气处理机组`, `风机盘管机组`, `多联机`, `VRV`, `VRF`, `净化循环机组` 等；
   - 英文代号（前后加边界保护）：`\b(MAU|PAU|RAU|FAU|FCU|RCU|DOAS|VAV|RTU|AHU|HRV|ERV|DXU|CRU|CAV)\b`。
4. **高效空气过滤设备 (`filter_ffu`)**：
   - 特征词：`高效过滤器`, `中效过滤器`, `H13`, `H14`, `HEPA`, `FFU`。
5. **强电线缆 (`cable_power`)**：
   - 特征词：`YJV22`, `YJV`, `WDZ-YJY` 等（且不含“控制”、“KVV”）。
6. **电气导管 (`conduit`)**：
   - 包含 `JDG`, `KBG`, `FPC`, `KPC`, `MT`, `CP` 或 `SC/RC` 且在电气上下文环境。
7. **不间断电源 (`ups`)**：
   - 包含 `UPS`, `不间断电源`, `蓄电池屏`, `蓄电池柜`。

### 4.3 Tier 2 名称核心词受控字典匹配

当 Tier 1 未命中时，根据名称/品类词的层次结构进行二级精细化分流：
- **电线电缆细分**：
  - 含有 `KVV`, `KVVR`, `RVVP` $\rightarrow$ `cable_control` (控制电缆)；
  - 无乘号且包含 `BV`, `BVR`, `BYJ`, `电线` $\rightarrow$ `wire` (电线)；
  - 默认多芯电力电缆 $\rightarrow$ `cable_power`。
- **管道细分**：
  - 包含 `不锈钢管`, `304管`, `316L管` $\rightarrow$ `pipe_ss`；
  - 包含 `紫铜管`, `脱脂铜管`, `冷媒铜管` $\rightarrow$ `pipe_copper`；
  - 包含 `PVC管`, `PPR管`, `塑料管`, `UPVC` $\rightarrow$ `pipe_plastic`；
  - 包含 `无缝钢管`, `镀锌钢管`, `焊接钢管` $\rightarrow$ `pipe_steel`。
- **阀门细分**：
  - 包含 `风`, `防火`, `排烟`, `密闭`, `半周长` $\rightarrow$ `damper`；
  - 包含 `医气`, `氧气`, `负压` $\rightarrow$ `valve_medical_gas`；
  - 默认水用阀门 $\rightarrow$ `valve_water`。

### 4.4 Tier 3 Sheet 名称与专业 Hint 概率向量融合

**函数实现**：`compute_sheet_hint_vector(professional_hint: str) -> Dict[str, float]`  
对 Sheet 名称（如“附件三-净化空调水系统标段”）进行先验分析，生成五维概率分布向量：
- 暖通向量 (`hvac`)：扫描 `["暖通", "空调", "通风", "净化"]`；
- 电气向量 (`elec`)：扫描 `["电气", "强电", "弱电", "动力", "照明", "智能化"]`；
- 给排水向量 (`plumb`)：扫描 `["给排水", "给水", "排水", "水专", "纯水"]`；
- 医气动力向量 (`gas`)：扫描 `["医气", "气体", "动力气体"]`；
- 装饰建筑向量 (`deco`)：扫描 `["装饰", "土建", "建材", "装修"]`。

当概率阈值 $> 0.3$ 时，结合 Query 文本中的泛指名词（如“管”、“阀”、“箱”）推导出最终精准域。

### 4.5 完整 30 域映射矩阵表

| 序号 | 路由 Domain Key | Schema 对应标准域 | 中文领域名称 | 核心路由特征词 |
|:---:|:---|:---|:---|:---|
| 1 | `fan_hvac` | `fan` | 通风机 | 风机、离心风机、轴流风机、风量+余压 |
| 2 | `valve_hvac` | `damper` | 暖通阀门/风管 | 防火阀、排烟阀、调节阀、风阀、风管、风道 |
| 3 | `cable_power` | `cable_power` | 电力电缆 | YJV、YJV22、WDZN-YJV、多芯动力线缆 |
| 4 | `cable_control` | `cable_control` | 控制电缆 | KVV、KVVP、KVVR、RVVP |
| 5 | `wire` | `wire` | 单芯电线 | BV、BVR、BYJ、BYJR、单芯布电线 |
| 6 | `silencer` | `silencer` | 消声设备 | 消声器、消声弯头、消声百叶、微穿孔 |
| 7 | `air_filter` | `filter_ffu` | 空气过滤与FFU | 高效过滤器、初效过滤器、中效过滤器、H13、FFU |
| 8 | `header_steam` | `manifold` | 汽水集配器 | 分水器、集水器、集配水器、分集水器 |
| 9 | `plenum` | `plenum` | 静压箱 | 静压箱、消声静压箱、集风箱、稳压箱 |
| 10 | `chiller` | `chiller` | 冷热水机组 | 冷水机组、冷热水机组、螺杆机、离心机组 |
| 11 | `air_terminal` | `air_terminal` | 风口末端 | 散流器、双层百叶、旋流风口、风口 |
| 12 | `door_special` | `door` | 特种门/医用门 | 气密门、防射线门、自动趟门、洁净门 |
| 13 | `ahu` | `ahu` | 空气处理机组 | 组合式空调机组、AHU、PAU、MAU、风机盘管FCU、多联机 |
| 14 | `med_gas` | `medical_gas` | 医用气体终端设备 | 医气设备带、气体终端、氧气管道附件 |
| 15 | `pipe_stainless`| `pipe_ss` | 不锈钢管 | 304不锈钢管、316L管道、不锈钢无缝管 |
| 16 | `pipe_water` | `pipe_plastic` | 塑料/非金属管 | PPR管、PVC-U管、PE管、给水塑料管 |
| 17 | `pipe_steel` | `pipe_steel` | 钢管/无缝钢管 | 无缝钢管、焊接钢管、镀锌钢管 |
| 18 | `pipe_copper` | `pipe_copper` | 铜管 | 紫铜管、脱脂紫铜管、冷媒铜管 |
| 19 | `valve_water` | `valve_water` | 给排水阀门 | 闸阀、蝶阀、止回阀、球阀、截止阀 |
| 20 | `valve_medical_gas`| `valve_medical_gas`| 医用气体阀门 | 医用脱脂球阀、医气减压阀、气体截止阀 |
| 21 | `distribution_box`| `switchgear` | 配电箱柜 | 配电箱、照明箱、动力柜、控制箱、AL/AP/ALE |
| 22 | `bridge_raceway` | `cable_tray` | 桥架线槽 | 槽式桥架、梯级桥架、托盘式桥架、网格桥架 |
| 23 | `conduit_elec` | `conduit` | 电气导管/线管 | JDG、KBG、SC、RC、薄壁钢导管、穿线管 |
| 24 | `clean_room` | `purification` | 洁净材料/板材 | 彩钢板、手工岩棉板、玻镁板、洁净天棚 |
| 25 | `lighting` | `lighting` | 照明灯具 | 面板灯、泪珠灯、平板灯、应急照明灯、LED |
| 26 | `switch_socket`| `switchgear` | 开关插座 | 五孔插座、86型开关、防水插座、单联双控 |
| 27 | `floor_tile` | `decoration` | 瓷砖/地面 | 同透PVC地板、防滑地砖、踢脚线、环氧自流平 |
| 28 | `wall_paint` | `decoration` | 涂料饰面 | 无机涂料、乳胶漆、防霉涂料、墙面漆 |
| 29 | `ceiling_board` | `decoration` | 吊顶天棚 | 铝扣板、石膏板、矿棉板、格栅吊顶 |
| 30 | `generic_civil` | `generic` | 通用/其他 | 无法命中以上 29 域时的兜底大类 |

---

## 五、 IR-2 分域结构化参数抽取规则与核心算法

### 5.1 配置驱动抽取引擎 (ConfigDrivenExtractor)

源码文件：`app/domain_extractors.py`  
类定义：`class ConfigDrivenExtractor`

抽取流程：
1. 实例化：`extractor = ConfigDrivenExtractor(domain)`，自动载入 `config/domain_schema.json` 中该域的 `must`、`should`、`flags` 定义。
2. 逐字段提取：
   - **正则优先**：若 schema 中声明了 `extract_regex`，优先以此正则捕获；
   - **别名 K:V 扫描**：若未命中，自动以该字段的 `key` 及 `aliases` 构建 `(?:^|[,;:\s])别名\s*[:：]\s*([^\s,;]{1,40})` 进行回退检索；
   - **数值转换**：对 `float` 与 `int` 类型调用 `parse_numeric_complex` 处理；
   - **枚举匹配**：对 `enum` 类型严格匹配 `options` 选项词；
3. 布尔标记识别：遍历 `flags` 数组（如 `防爆`, `防腐`, `变频`, `静音`），在正文中命中即置为 `True`。

### 5.2 复合数值解析器 (parse_numeric_complex)

**函数实现**：`parse_numeric_complex(text: str) -> Dict[str, Any]`  
解决工程清单中广泛存在的“非单值数值”问题：
1. **不等式识别**：匹配 `>=, <=, >, <, ≥, ≤`，输出 `{"op": ">=", "base_val": 500.0, "val": 500.0}`。
2. **范围区间识别**：匹配 `[-~—至到及或]`，例如 `1000~2000m3/h`，输出：
   ```python
   {"min_val": 1000.0, "max_val": 2000.0, "val": 1500.0} # 均值作为基准
   ```
3. **约数清洗**：清洗 `约`, `左右`, `~` 前缀或后缀。
4. **单位提取与缩放**：末尾单位提取后调用 `get_unit_conversion(unit)` 计算换算乘数 `mult`。

### 5.3 暖通空调与设备类 (HVAC) 专有提取规则

源码文件：`app/query_preprocessor.py` 中的 `extract_query_hvac_specific_params`

#### 1. 空调机组代码提取 (`extract_hvac_unit_type`)
建立 18 组机组标准代号与关键字映射表（`HVAC_UNIT_MAP`）：
- `全新风空气处理机组` $\leftarrow$ `["MAU", "全新风空气处理机组", "100%全新风"]`
- `回风循环空气处理机组` $\leftarrow$ `["RAU", "回风循环空气处理机组"]`
- `组合式空气处理机组` $\leftarrow$ `["AHU", "组合式空气处理机组", "空气处理机组"]`
- `风机盘管机组` $\leftarrow$ `["FCU", "风机盘管机组", "卡式风机盘管", "卧式风机盘管"]`
- `新风预处理机组` $\leftarrow$ `["PAU", "PAU-N", "净化新风机组", "新风预处理机组"]`
- `净化循环机组` $\leftarrow$ `["RCU", "循环空气处理机组", "自取新风机组", "AHU-J"]`
- `多联式空调室内机` $\leftarrow$ `["多联机室内机", "VRV室内机", "VRF室内机"]`
- `离心风机（箱）` $\leftarrow$ `["静音型排风机", "医用静音型排风机", "离心排风机"]`
- `风机过滤单元` $\leftarrow$ `["FFU", "风机过滤单元FFU"]`

#### 2. 空调安装形式与循环模式推理
- **安装形式**：包含 `["吊装", "吊顶安装", "吊顶"]` 置为 `安装形式 = "吊装"`；包含 `["落地安装", "落地", "地装"]` 置为 `安装形式 = "落地"`。
- **自取新风判定**：若 `送风量 == 新风量` 或包含 `"自取新风"`，标记 `自取新风 = True`，机组类型补正为 `净化循环机组`。
- **风机盘管结构**：
  - 包含 `["卡式", "卡式四吹", "四面出风"]` 置为 `结构形式 = "卡式"`；
  - 包含 `["卧式暗装", "暗装"]` 置为 `结构形式 = "卧式暗装"`；
  - 包含 `["自带提升泵", "提升泵", "冷凝水提升泵"]` 标记 `带冷凝水提升泵 = "带冷凝水提升泵"`。

#### 3. 防火阀控制模式与动作温度
- **控制方式**：
  - 包含 `["电动", "DV24V", "DC24V", "执行机构", "执行器"]` 判定为 `控制方式 = "电动"`；
  - 包含 `["常开", "常闭", "熔断", "70℃", "280℃"]` 判定为 `控制方式 = "常开熔断"`。
- **熔断温度**：正则提取 `(\d+)\s*[℃度c]` $\rightarrow$ `熔断温度 = 70` 或 `280`。
- **截面形状**：匹配 `W*H` 判定为 `矩形`，匹配 `φ/Φ/DN` 判定为 `圆形`。

#### 4. 通风管道 (`extract_query_ductwork_params`)
- **板材厚度**：正则 `(?:板材厚度|钢板厚度|壁厚|厚度|δ)[:：]?\s*(\d+(?:\.\d+)?)\s*(?:mm)?` $\rightarrow$ `0.75`, `1.0`, `1.2` 等。
- **连接方式**：支持提取 `角钢法兰`、`共板法兰`、`插条`、`咬口`、`焊接`。

#### 5. 静压箱与集风箱 (`extract_query_plenum_params`)
- 提取 3D 尺寸：`r'(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)'`；
- 衍生提取 `长`, `宽`, `高`, `半周长`。

### 5.4 电气强电与布线类 (Electrical) 专有提取规则

源码文件：`app/query_preprocessor.py` 中的 `extract_query_cable_params`

#### 1. 线缆型号族长前缀贪婪匹配
严格按照字符长度倒序排列，优先匹配全称：
- 低烟无卤耐火系列：`WDZN-YJV22`, `WDZN-YJV`, `WDZN-BYJ`, `WDZN-KYJV`
- 阻燃系列：`WDZB-YJV22`, `WDZA-YJV`, `WDZC-YJY`, `ZR-YJV`, `ZR-BV`
- 基础系列：`YJV22`, `YJV`, `KVV`, `RVVP`, `BYJ`, `BV`, `BVR`
- **型号族映射**：提取型号后，将其归类至 `BYJ`, `BV`, `YJV`, `YJV22`, `KVV` 等型号族。

#### 2. 复合芯线与截面积解耦算法
工程中电缆多为复合芯线（如 `4*50+1*25`），算法拆解为：
```python
comp_match = re.search(r'(\d+)\s*\*\s*(\d+(?:\.\d+)?)\s*\+\s*(\d+)\s*\*\s*(\d+(?:\.\d+)?)', t_clean)
if comp_match:
    attrs["芯数"] = f"{comp_match.group(1)}+{comp_match.group(3)}"      # "4+1"
    attrs["截面积"] = f"{sec1}+{sec2}"                                 # "50+25"
    attrs["主芯数"] = int(comp_match.group(1))                         # 4
    attrs["主截面"] = float(comp_match.group(2))                       # 50.0
    attrs["零芯数"] = int(comp_match.group(3))                         # 1
    attrs["零截面"] = float(comp_match.group(4))                       # 25.0
```

#### 3. 电气配电箱柜分类 (`BOX_PREFIX_RULES`)
通过箱号前缀识别箱柜类型：
- `ACP-P` $\rightarrow$ 水泵控制箱
- `ACP` $\rightarrow$ 风机控制箱
- `ALE` $\rightarrow$ 应急照明配电箱
- `AL` $\rightarrow$ 普通照明配电箱
- `AP` $\rightarrow$ 动力配电箱
- `AT` $\rightarrow$ 双电源切换箱
- `AX` $\rightarrow$ 检修插座箱

#### 4. 电气导管业务规则 (`extract_conduit_params`)
- **管材代号映射**：
  - `JDG` $\rightarrow$ 套接紧定式镀锌薄壁钢导管
  - `KBG` $\rightarrow$ 套接扣压式薄壁钢导管
  - `SC` $\rightarrow$ 热镀锌焊接厚壁钢管
  - `RC` $\rightarrow$ 水煤气钢管
  - `PC` $\rightarrow$ 硬质阻燃PVC塑料管
- **SC/RC 管道公称直径转外径表**：
  SC/RC 规格数字代表 DN，转换为外径（OD）：
  `DN15->21.3mm`, `DN20->26.9mm`, `DN25->33.7mm`, `DN32->42.4mm`, `DN40->48.3mm`, `DN50->60.3mm`, `DN65->76.1mm`, `DN80->88.9mm`, `DN100->114.3mm`。
- **敷设方式代号**：
  `WC` (暗敷在墙内), `CC` (暗敷在顶板内), `FC` (暗敷在地面内), `CE` (沿顶棚明敷), `WE` (沿墙面明敷)。

### 5.5 给排水与工业管道类 (Plumbing & Piping) 专有提取规则

源码文件：`app/normalizer.py` 与 `app/domain_extractors.py`

#### 1. De 外径转 DN 公称直径标准表 (`convert_de_to_dn`)
给水塑料管常用 De 表示外径，根据国家规范换算为对应 DN：
| 塑料管外径 De | 换算对应公称通径 DN |
|:---:|:---:|
| De16 | DN10 |
| De20 | DN15 |
| De25 | DN20 |
| De32 | DN25 |
| De40 | DN32 |
| De50 | DN40 |
| De63 | DN50 |
| De75 | DN65 |
| De90 | DN80 |
| De110 | DN100 |
| De160 | DN150 |
| De200 / De225 | DN200 |
| De250 | DN250 |
| De315 | DN300 |

#### 2. JIS 英寸口径映射规则
针对日标或传统习惯中“几寸管”映射为标准公称通径：
- `1/2寸` $\rightarrow$ DN15 | `3/4寸` $\rightarrow$ DN20 | `1寸` $\rightarrow$ DN25 | `1.2寸` $\rightarrow$ DN32 | `1.5寸` $\rightarrow$ DN40
- `2寸` $\rightarrow$ DN50 | `2.5寸` $\rightarrow$ DN65 | `3寸` $\rightarrow$ DN80 | `4寸` $\rightarrow$ DN100 | `6寸` $\rightarrow$ DN150

### 5.6 医用气体与特种设备类 (Medical Gas) 专有提取规则

源码文件：`app/query_preprocessor.py` 中的 `canonicalize_query_attrs`

#### 1. 气体介质标准化词典
- `氧气` $\leftarrow$ `["氧气", "o2"]`
- `负压` $\leftarrow$ `["负压", "吸引", "真空", "vac"]`
- `空压` $\leftarrow$ `["空压", "压缩空气", "空气", "air"]`
- `CO2` $\leftarrow$ `["co2", "二氧化碳"]`
- `N2O` $\leftarrow$ `["n2o", "笑气"]`
- `氮气` $\leftarrow$ `["氮气", "n2"]`

#### 2. 医用铜管脱脂特性
医用气体铜管必须严格区分脱脂等级：
- 文本中包含 `"脱脂"` $\rightarrow$ `材质 = "脱脂紫铜"`；
- 未包含 `"脱脂"` $\rightarrow$ `材质 = "紫铜"`。

### 5.7 装饰洁净与建筑材料类 (Decoration & Cleanroom) 专有提取规则

源码文件：`app/domain_extractors.py` 中的 `extract_decoration_params`

1. **2D 尺寸提取**：从 `600*600`、`1200*2400` 中提取 `长`、`宽`、`尺寸_2d`。
2. **板材厚度提取**：匹配 `0.6mm`、`12mm`、`板厚50mm` 存入 `厚度`。
3. **洁净板结构**：识别 `手工` vs `机制`、芯材（`岩棉`、`玻镁`、`铝蜂窝`）。
4. **门扇类型**：提取 `单开`、`双开`、`子母门`。

### 5.8 无规格清单反向补全机制 (NameSpecExtractor)

源码文件：`app/query_preprocessor.py` 中的 `extract_from_name_spec_fallback`  
**适用场景**：针对占 **38.87%** 的空规格或极短规格清单项（特别是专项材料发包包）。

当 `spec` 文本长度 $< 3$ 或抽取结果字段数 $\le 1$ 时，触发反向补全：
1. **反向抽尺寸 (2D/3D)**：从名称中提取 `600*600` 或 `1000*3150*300`，推导出长、宽、高、面积及半周长；
2. **反向抽厚度**：匹配 `(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:厚|卷材)`，如 `同透PVC卷材 2.0mm厚` $\rightarrow$ `厚度 = 2.0`；
3. **反向抽管径**：匹配 `DN\d+` 或 `De\d+`，如 `无缝钢管 DN100` $\rightarrow$ `管径 = "DN100"`, `dn_val = 100`；
4. **反向抽材质**：匹配 `316L`, `304`, `不锈钢`, `PVC`, `PPR`, `PPH`, `镀锌钢板`。

---

## 六、 物理量强制归一化与几何特征计算

### 6.1 单位与量级强制换算标准

源码文件：`app/query_preprocessor.py` 中的 `normalize_unit_scales` 与 `app/domain_extractors.py`

| 物理量类型 | 原始输入常见单位 | 标准存储单位 | 换算乘数 / 公式 |
|:---|:---|:---:|:---|
| **风量 (Air Volume)** | CFM (美制)<br>l/s (升/秒)<br>CMH / m³/h | **`m³/h`** | $\text{CFM} \times 1.699$<br>$\text{l/s} \times 3.6$<br>$\text{m³/h} \times 1.0$ |
| **压力 (Pressure)** | kPa (千帕)<br>MPa (兆帕)<br>mmH2O (毫米水柱)<br>Pa (帕斯卡) | **`Pa`** | $\text{kPa} \times 1000$<br>$\text{MPa} \times 1000000$<br>$\text{mmH2O} \times 9.80665$<br>$\text{Pa} \times 1.0$ |
| **功率/冷量 (Power)** | W (瓦)<br>HP (匹/马力)<br>kcal/h (千卡)<br>kW (千瓦) | **`kW`** | $\text{W} \times 0.001$<br>$\text{HP} \times 0.735$<br>$\text{kcal/h} \times 0.001163$<br>$\text{kW} \times 1.0$ |
| **管径 (Pipe Size)** | 4寸 (日标英制)<br>De110 (塑料管外径)<br>DN100 (公称通径) | **`DN (mm)`** | JIS 映射表 (4寸 $\rightarrow$ 100.0)<br>De 映射表 (De110 $\rightarrow$ 100.0)<br>数值直接取整 |
| **电缆截面 (Cable Area)** | mm² / 平方 | **`mm²`** | 浮点数格式化为一位小数 (如 `2.5`, `185.0`) |

### 6.2 材质与过滤等级归一化

#### 1. 材质名称归一 (`canonicalize_query_attrs`)
- `316L`、`S31603` $\rightarrow$ `不锈钢316L`
- `304`、`S30408`、`06Cr19Ni10` $\rightarrow$ `不锈钢304`
- `优质镀锌钢板`、`镀锌板` $\rightarrow$ `镀锌钢板`
- `脱脂铜管`、`脱脂紫铜` $\rightarrow$ `脱脂紫铜`

#### 2. 空调与洁净过滤等级归一
- `G1` ~ `G4`、`初效` $\rightarrow$ `初效`
- `F5` ~ `F9`、`中效` $\rightarrow$ `中效`
- `H10` ~ `H12`、`亚高效` $\rightarrow$ `亚高效`
- `H13` ~ `H14`、`高效`、`HEPA` $\rightarrow$ `高效`
- `U15` ~ `U17`、`超高效` $\rightarrow$ `超高效`

### 6.3 几何尺寸与衍生特征计算

在 `extract_query_params` 流程末端，根据几何规律补全派生计算属性：
1. **半周长派生**：若存在 `长` 与 `宽`，自动计算 `半周长 = 长 + 宽`（阀门与风管工程量核心匹配依据）。
2. **截面积派生**：`面积 = (长 / 1000.0) * (宽 / 1000.0)`（单位：m²）。
3. **尺寸长宽高拆解**：若仅有 `尺寸 = "1000*300*400"`，自动解析为 `长=1000.0`, `宽=300.0`, `高=400.0`, `半周长=1300.0`。

### 6.4 三维公差箱体解析 (3D Tolerance Box)

**函数实现**：`extract_tolerance_3d_box(text: str)`  
针对带施工公差的设备或箱体描述，如 `(1500±150)*(1500±150)*(900±150)mm`：
- 解析出名义尺寸：`长 = 1500.0`, `宽 = 1500.0`, `高 = 900.0`；
- 解析出区间极值：
  - `长_min = 1350.0`, `长_max = 1650.0`
  - `宽_min = 1350.0`, `宽_max = 1650.0`
  - `高_min = 750.0`, `高_max = 1050.0`
- 标记 `尺寸_类型 = "3d_tolerance_box"`。

### 6.5 动态 LLM 规则引擎集成

在硬编码规则处理完成后，系统自动载入通过管理后台（`app/llm_rule_engine.py`）动态生成的业务 DSL 规则：
```python
active_rules = LLMRuleManager.get_active_rules()
# 校验学科 (Discipline) 或品类 (Category) 作用域匹配
# 执行动态提取规则 DynamicRuleInterpreter.apply_extraction_rules(...)
# 覆写或补充 normalized_attrs 中的字段
```
这保证了在不重启系统、不发版代码的情况下，能够热修复特定工程项目的生僻物料提取逻辑。

---

## 七、 核心接口调用规范与数据输入输出结构

### 7.1 主入口函数签名与调用时序

源码文件：`app/query_preprocessor.py`  
**核心处理入口**：
```python
def extract_query_params(
    name: str, 
    specification: str, 
    category: str
) -> Dict[str, Any]:
    """
    Query 端数据结构化提取统一主入口
    
    参数:
        name: 原始清单名称（如 "无缝钢管 DN100"）
        specification: 原始清单规格型号/特征（多行长文本）
        category: 原始清单分类/标段名称（如 "给排水"、"暖通"）
        
    返回:
        字典对象，包含 domain, attrs, model, clean_text, noise_text, kv_dict
    """
```

### 7.2 输入与输出 Schema 示例

#### 示例 1：详细多行暖通设备输入
**输入数据**：
- `name` = `"全新风空气处理机组"`
- `specification` = `"""1.规格: MAU-01
2.风量(m³/h): 5000
3.机外余压(Pa): 250
4.制冷量(kW): 120
5.过滤段: 初效+中效
6.工作内容: 管道切割、机组安装调试
7.重新重复名称: 全新风空气处理机组"""`
- `category` = `"暖通空调"`

**输出结构体 (`extract_query_params` 返回值)**：
```json
{
  "domain": "ahu",
  "model": "MAU-01",
  "clean_text": "1.规格: MAU-01\n2.风量(m³/h): 5000\n3.机外余压(Pa): 250\n4.制冷量(kW): 120\n5.过滤段: 初效+中效",
  "noise_text": "6.工作内容: 管道切割、机组安装调试\n7.重新重复名称: 全新风空气处理机组",
  "kv_dict": {
    "规格": "MAU-01",
    "风量": {"value": "5000", "unit": "m³/h"},
    "机外余压": {"value": "250", "unit": "Pa"},
    "制冷量": {"value": "120", "unit": "kW"},
    "过滤段": "初效+中效"
  },
  "attrs": {
    "名称": "全新风空气处理机组",
    "机组类型": "全新风空气处理机组",
    "风量": 5000.0,
    "机外余压": 250.0,
    "制冷量": 120.0,
    "过滤段": ["初效", "中效"],
    "过滤等级": "初效"
  }
}
```

#### 示例 2：空规格专项材料输入（反向补全）
**输入数据**：
- `name` = `"同透PVC卷材 2.0mm厚"`
- `specification` = `""`
- `category` = `"装饰工程"`

**输出结构体**：
```json
{
  "domain": "decoration",
  "model": "",
  "clean_text": "",
  "noise_text": "",
  "kv_dict": {},
  "attrs": {
    "名称": "同透PVC卷材",
    "材质": "PVC",
    "厚度": 2.0,
    "板材厚度": 2.0
  }
}
```

---

## 八、 维护与扩展指引 (开发交接指南)

### 8.1 如何新增或调整过滤规则

若现场遇到新的非工程清单干扰行（如某些工程特有的附注声明）：
1. 打开 `app/query_preprocessor.py`；
2. 找到 `is_valid_query_item` 函数；
3. 新增 `Rule 11` 拦截分支，确保返回特定的错误编码（如 `R11_CUSTOM_DISCLAIMER`）；
4. **注意**：新增规则必须补充单元测试到 `tests/test_query_cleaning_pipeline.py`，验证不会误杀正常清单项。

### 8.2 如何扩充新业务 Domain 或 Schema 字段

若系统需要接入全新设备品类（如“医用吊塔”）：
1. **修改 Schema 定义**：在 `config/domain_schema.json` 中增加该域（如 `"medical_pendant"`），声明其 `must`、`should`、`flags` 正则表达式；
2. **增加路由映射**：
   - 打开 `config/category_domain_map.json`，在 `overwrite_rules` 中添加触发特征词；
   - 在 `app/domain_router.py` 的 `DOMAIN_SCHEMA_MAP` 注册别名；
   - 在 Tier 1 或 Tier 2 中加入名称关键词；
3. **编写特异性抽取规则**：若有复杂的专用参数（如旋转角度、承重、气电接口数），在 `app/domain_extractors.py` 中编写 `extract_pendant_params` 并在 `ConfigDrivenExtractor` 中分发。

### 8.3 常见避坑点与排错清单

1. **切勿随意删除单位白名单**：
   `clean_query_text_ir1` 中的 `whitelist_units` 保护着类似 `2.5mm²`、`70℃`、`4寸` 等核心参数。若调整白名单，必须运行测试集验证。
2. **冒号与全角符号顺序**：
   必须先调用 `to_half_width` 将 `：` 统一为 `:`，否则后续所有的 KV 正则捕获将无法匹配中文全角冒号。
3. **De 与 DN 的转换时机**：
   De 转 DN 仅适用于塑料及复合给水管（`PPR`, `PVC`, `PE`）。金属钢管通常标称直接为 DN，若误对钢管应用 De 转换可能导致口径漂移。
4. **多芯电缆零截面缺失**：
   若遇到 `3*120+2*70`，务必检查 `零芯数` 与 `零截面` 是否正确拆分为 `2` 和 `70.0`。
5. **Should 字段打分策略对齐**：
   Query 端未提取到 Should 字段属于普遍现象（用户只提供了简写规格）。在 `matcher.py` 中已对齐“有则加分，无则不扣分”原则，严禁因 Query 端缺失 Should 字段而进行惩罚性扣分。

### 8.4 自动化测试执行与回归验证

每次修改清洗或提取规则后，必须在终端执行回归测试验证：

```powershell
# 运行 Query 端专用清洗与提取测试套件
python -m unittest tests/test_query_cleaning_pipeline.py

# 运行完整测试套件
pytest tests/
```

**验收标准**：
- `test_query_cleaning_pipeline.py` 必须 100% 通过（全部 `OK`）；
- 核心指标满足：IR-0 过滤准度 100%，30 域路由成功率 $\ge 95\%$，非空规格 Must 字段提取率 $\ge 90\%$。
