# Query端数据清洗及结构化功能优化完成报告

根据 [query_cleaning_and_structuring_plan.md](file:///c:/Users/wangd/project/ListTrans/ListTrans/doc/query_structuring_archive/query_cleaning_and_structuring_plan.md) v1.2 完美收敛版方案要求，已全面完成 Query 端数据清洗、文本规整、30 域路由与分域抽取引擎的重构与优化。

---

## 核心优化成果

### 1. IR-0 10 组扩展硬过滤机制 (`is_valid_query_item`)
在 [query_preprocessor.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/query_preprocessor.py) 中实现了完整的 10 组精确行过滤逻辑：
- `R1_EMPTY_ROW`: 过滤全空行/孤立 None 行。
- `R2_TOTAL_ROW`: 过滤“小计”、“合计”、“总计”行。
- `R3_REPEAT_HEADER_ROW`: 过滤跨 Sheet 重复表头行（如“名称”、“项目名称”、“构件名称”）。
- `R4_PREFACE_NOTE_ROW`: 过滤“编制依据”、“图纸号”、“设计说明”等前言说明行。
- `R5_OUTLINE_TITLE_ROW`: 过滤无规格、无单位的大纲章节导航标题行。
- `R6_LONG_TEXT_NOTE_ROW`: 过滤长度 > 100 且无工程参数的段落说明行。
- `R7_FOOTER_NOTE_ROW`: 过滤“编制人:”、“审核人:”、“编制日期:”等落款行。
- `R8_FORMULA_CALC_ROW`: 过滤无名称且规格为纯算式的计算行。
- `R9_MERGED_CELL_ORPHAN_ROW`: 过滤因合并单元格拆散产生的孤立数值行。
- `R10_DIVIDER_ROW`: 过滤包含 `===` 或 `---` 的表样分割线。

### 2. IR-1 文本规整与防误伤策略
- **单位白名单前缀剥离保护** (`clean_query_text_ir1`)：对紧跟 `mm`、`mm²`、`A`、`kW`、`DN`、`m²`、`*`、`℃` 等单位与规格标记的数值（如 `2.5mm²`、`70℃常开防火阀`）实施白名单保护，严禁剔除前缀；仅对后接空格或纯中文的纯序号执行剥离。
- **名称与规格轻量解耦** (`decouple_name_and_spec`)：针对混入名称中的规格（如 `无缝钢管 DN100`、`防滑地砖 600*600`），正则表达式剥离规格后填入 Spec 域，名称还原为标准中文词。
- **属性级噪音屏蔽**：屏蔽出现频率最高的 `工作内容`、`施工内容`、`重新重复名称`、`备注` 等干扰 Key。

### 3. 30 域路由引擎 (`QueryDomainRouter`)
在 [domain_router.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/domain_router.py) 中重构了 **Three-Tier Weighted Router**：
- **Tier 1 (置信度 1.0)**：强特征词正文/名称精准重叠匹配（如 `消声器`、`YJV`、`H13`、`AHU` 等）。
- **Tier 2 (置信度 0.85)**：名称核心词受控字典树匹配。
- **Tier 3 (置信度 0.60)**：Sheet 名称与专业 Hint 先验概率向量融合 (`compute_sheet_hint_vector`)，结合次要特征选出最高概率 Domain。
- **完整 30 域对齐**：完整支持 30 域 schema 映射。

### 4. KV Line Parser v1.2 & 无规格补全
- **KV Line Parser v1.2**：修复贪婪匹配 Bug，增加 Key 末尾括号单位捕获 `(?:\s*[（\(]([^）\)]*)[）\)])?`，独立提取为 `unit` 属性。
- **无规格反向补全 (`NameSpecExtractor`)**：当 Spec 为空或极短时，激活 `extract_from_name_spec_fallback`，从名称中反向提取尺寸、厚度、管径、材质参数，补充结构化属性。
- **单位与量级强制归一化**：自动归一风量 (`m³/h`)、压力 (`Pa`)、功率 (`kW`)、管径 (`DN mm` / JIS 英寸映射) 等物理维度。

### 5. 匹配打分与 Should 字段对齐
在 [matcher.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py) 中与后端“有则加分，无则不扣分”策略严格保持一致，去除了针对 Missing Should 字段的无差别扣分，避免规格不全记录被系统性打压。

---

## 验证与测试结果

### 自动化单元测试
在 [test_query_cleaning_pipeline.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/tests/test_query_cleaning_pipeline.py) 和已有测试套件中，**34 项单元测试全部通过** (34/34 OK)。

- **IR-0 10 组过滤规则测试**：`PASS`
- **IR-1 白名单与解耦测试**：`PASS` (`2.5mm²`、`70℃` 无误伤；`4.304` 型号材质无误剥离)
- **30 域路由与概率向量测试**：`PASS` (30 域正确分发)
- **KV Parser v1.2 & NameSpecExtractor 测试**：`PASS`
