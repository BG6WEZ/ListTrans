# Query 端（原始工程清单）独立清洗与结构化改造方案

## 概述与背景

通过对 `doc/真集` 中 5 大专业（装饰、强电、弱电、给排水、动力气体）原始 Excel 清单结构以及 `tests/fixtures/ground_truth_500.json` 326 条金标数据的深度诊断，**实测现场回归 Top-1 准确率仅为 47.24%**。

其核心症结在于：**现有的参数提取和域路由逻辑是基于标准物料库（DB 端）规范、干净的短文本设计的**。而原始清单（Query 端）文本极脏，混杂了大量的多行编号（`1.` `2.` `3.`）、施工工作内容（“切管、套丝、水压试验”）、商务暂估价说明、敷设方式等杂质，导致：
1. **跨行 KV 粘连与吞噬**：抓取上一行键值时把下一行的核心参数（如材质 `304`）连同编号一起当作值吞掉，导致属性丢失。
2. **非标表述无法识别**：如 `截面10mm2`、`三芯及以上×截面35mm²以内` 无法被按 DB 模式写的正则抓出 `截面积` 和 `芯数`。
3. **同义词与数值格式与物料库断层**：原清单写 `S30408`/`紫铜管`/`4*25+1*16`，未能规范归一为物料库匹配要求的 `不锈钢304`/`铜`/`25.0+16.0`。

本方案提出**“DB 端与 Query 端彻底解耦”**的独立清洗与结构化架构，建立专属的 **Query 预处理流水线**。

---

## 结合真集原始清单的具体分析

真集中原始清单的真实形态举例：

```text
-- [例 1: 给排水/纯水工程] --
1.名称：卫生级球阀
2.规格：DN15
3.焊接方法：螺纹连接
4.304不锈钢材质
5.工作内容：切管、套丝、阀门连接、水压试验

-- [例 2: 强电工程电缆头] --
1.名称：电力电缆终端头
2.规格、型号：截面10mm2
3.材质、类型：铜芯电缆、干包式
4.工作内容：定位、量尺寸、锯断、剥保护层...
```

针对上述特征，Query 端清洗需要解决 4 个关键解构动作：
1. **段落剥离 (Section Slicing)**：切除 `工作内容`、`敷设方式`、`暂估价`、`甲供说明` 等无关段落。
2. **多行 KV 块化 (Block KV Parser)**：按行/编号拆解，防止跨行文本吞噬。
3. **Query 专属容错正则 (Query Extractor)**：支持 `截面10mm2`、`1槽位` 等口语化/工程化抓取。
4. **属性规范化 (Value Canonicalizer)**：将 `S30408` 映射为 `不锈钢304`，将 `紫铜管` 映射为 `铜`，电缆截面统一格式化为 `25.0+16.0`。

---

## 拟修改的架构与代码变动

### 组件 1: Query 专属清洗与解构引擎 (New Module: `app/query_preprocessor.py`)

创建全新的 Query 端预处理模块，负责上传清单特有的文本规整与参数提取：

#### [NEW] [query_preprocessor.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/query_preprocessor.py)
* **`slice_query_sections(raw_spec: str) -> Dict[str, str]`**：
  - 将原始文本切分为 `material_block`（物料特征块）与 `noise_block`（施工工作内容/暂估价/敷设方式/甲供说明）。
* **`parse_query_kv_blocks(material_block: str) -> Dict[str, str]`**：
  - 基于 `1.` `2.` `3.` 编号或换行符逐块解析 `名称`、`规格`、`材质`、`型号`，避免正则表达式跨行乱抓。
* **`extract_query_params(name: str, spec: str, category: str) -> Dict[str, Any]`**：
  - 调用 Query 专属解构逻辑，抓取 `must` 与 `should` 参数。
* **`canonicalize_query_attrs(domain: str, attrs: Dict[str, Any]) -> Dict[str, Any]`**：
  - 执行同义词和数值规范化（如 `S30408` -> `不锈钢304`，`紫铜管` -> `铜`，截面浮点标准化）。

---

### 组件 2: 前端上传与在线匹配接口挂载

#### [MODIFY] [server.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/server.py)
- 在 `/api/upload` (上传清单) 和 `/api/match` / `/api/batch_match` (清单匹配) 处，调用 `query_preprocessor.py` 中的 `extract_query_params`，替代以往直接调用的通用 `extract_params`。

#### [MODIFY] [matcher.py](file:///c:/Users/wangd/project/ListTrans/ListTrans/app/matcher.py)
- 确保 `match_item()` 在接收 Query 查询对象时，自动识别并优先使用 Query 端经过规范化（Canonicalized）后的结构化属性进行 Hard Gates 校验与相似度比对。

---

## 验证与回归计划

### 1. 自动化回归测试 (Automated Regression)
运行回归脚本评测金标 326 条数据：
```bash
python tests/regression_runner.py
```
**预期目标**：
- **Top-1 匹配准确率**：从目前的 **47.24%** 提升至 **≥ 88%**（阶段一） / **≥ 92%**（阶段二）。
- **Must 字段结构化准确率**：从 **74.18%** 提升至 **≥ 95%**。

### 2. 单元测试 (Unit Testing)
新建 `tests/test_query_preprocessor.py` 覆盖真集中 63 个典型 Fail 样例的切片与提取逻辑。

---

## 用户审核与确认事项

> [!IMPORTANT]
> 1. 本方案完全**遵循您的要求**：在获得您的明确批准前，**未修改任何项目源代码**。
> 2. 方案采取**单向解耦机制**（新建 Query 预处理通道），**完全不会影响**已运行的标准物料库（DB 端）结构和索引。
