# 武汉华康世纪物料清单匹配系统 V0.1.2
## 物料库改版清洗与匹配规则升级改造技术方案（修订执行版）

> **版本**：V0.1.2（根据独立评审报告完成全面修订）  
> **文档状态**：已修订待执行（Ready for Implementation）  
> **责任领域**：数据治理 / 领域路由 / 清洗规则 / 特征提取 / 多通道召回 / 打分与硬门禁引擎  
> **依据报告**：
> - [doc/V0.1.2/material_library_revamp_analysis_report.md](file:///c:/Users/wangd/project/ListTrans/doc/V0.1.2/material_library_revamp_analysis_report.md)  
> - [doc/V0.1.2/material_library_revamp_review_report.md](file:///c:/Users/wangd/project/ListTrans/doc/V0.1.2/material_library_revamp_review_report.md)  
> **目标物料库**：[doc/物料库/物料明细20260817（部分改版）.xlsx](file:///c:/Users/wangd/project/ListTrans/doc/物料库/物料明细20260817（部分改版）.xlsx)  
> **评测基线**：[tests/fixtures/golden_baseline.json](file:///c:/Users/wangd/project/ListTrans/tests/fixtures/golden_baseline.json)

---

## 目录
- [一、方案概述与建设目标](#一方案概述与建设目标)
- [二、总体技术架构与数据流全景图](#二总体技术架构与数据流全景图)
- [三、模块一：数据治理与全字段禁用精准拦截引擎](#三模块一数据治理与全字段禁用精准拦截引擎)
- [四、模块二：抽取层净化与领域路由前置修复](#四模块二抽取层净化与领域路由前置修复)
- [五、模块三：几何特征抽取与复合双区间解析引擎](#五模块三几何特征抽取与复合双区间解析引擎)
- [六、模块四：多通道召回与分词索引扩充](#六模块四多通道召回与分词索引扩充)
- [七、模块五：级距宽度归一化打分引擎与最近邻兜底](#七模块五级距宽度归一化打分引擎与最近邻兜底)
- [八、模块六：Schema 门禁解耦与业务单位规范](#八模块六schema-门禁解耦与业务单位规范)
- [九、涉及文件与代码修改清单](#九涉及文件与代码修改清单)
- [十、实施阶段规划与里程碑](#十实施阶段规划与里程碑)
- [十一、回归评测、真实基线验收标准与回滚预案](#十一回归评测真实基线验收标准与回滚预案)

---

## 一、方案概述与建设目标

针对物料库改版及存量缺陷集中暴露（10,281条禁用物料穿透、半周长因领域路由误判导致全候选100分、双区间消声器无法区分、三维容差正则失效等），V0.1.2 版本提供成套系统性技术解决方案，确保系统零故障平稳过渡并显著提升真实匹配精度。

### 1.1 核心建设指标
1. **禁用物料拦截率 100%（0误杀 0漏杀）**：
   - 精准拦截 `name`（10,281条）、`code`（673条）、`spec`（1条）中的全部 **10,955 条** 禁用物料。
   - 保护 `[3.61.19.0001] PP废液收集车` 等正常物料，入库标准活跃物料精准为 **39,420 条**。
   - 导出拦截审计 CSV 供业务复核，并对库内 37 行重复编码进行去重治理。
2. **根因级修复“全候选满分”缺陷**：
   - 修复 `extract_device_name` 与 `route_domain`，确保半周长风阀精准路由至 `damper` 领域。
   - 消除 8 个半周长候选同得 100 分的无区分度现象，目标半周长命中率达到 100%。
3. **消声器“半周长 × 长度”双区间与三维容差解析率 100%**：
   - 支持全角冒号 `半周长：` 与 `*长度：` 双区间笛卡尔特征解析（覆盖 96 条消声器物料）。
   - 完整支持 `(A±ΔA)*(B±ΔB)*(C±ΔC)` 三维容差解析（覆盖 279 条物料）。
4. **真实黄金测试集指标显著跃升**：
   - 半周长/容差品类专项 `Hit@1 ≥ 80%`。
   - 暖通专业 `Hit@1 ≥ 45%`（现状 ~28.5%）。
   - 全量评测（1067用例）`Hit@1 ≥ 30%`（现状 22.31%），`Recall@3 ≥ 45%`（现状 35.61%），`MRR ≥ 0.400`（现状 0.307）。

---

## 二、总体技术架构与数据流全景图

```mermaid
flowchart TD
    subgraph Layer1["1. 数据治理与入库层 (Data Ingestion Layer)"]
        A1[读取 20260817 物料 Excel 50375行] --> A2{全字段语义共现扫描: 禁用|停用|无效}
        A2 -->|命中 10955条| A3[归档并导出禁用审计 CSV]
        A2 -->|有效物料 39420条| A4[去重校验: 治理 37行重复code]
        A4 --> A5[(SQLite materials.db)]
        A4 --> A6[(SQLite materials_fts FTS5)]
        A4 --> A7[(BGE-small-zh-v1.5 512维向量索引)]
    end

    subgraph Layer2["2. 抽取层净化与领域路由 (Extraction & Routing Layer)"]
        B1[输入文本] --> B2[设备名称提取: 剥离半周长/长度/风量/面积等标签词]
        B2 --> B3[领域路由: 优先信任标准品类 category -> 纠正为 damper/silencer]
        B1 --> B4[参数提取: 修复区间上界误抽为厚度缺陷]
    end

    subgraph Layer3["3. 几何衍生与双区间特征抽取 (Feature Engineering)"]
        C1[清单端: 矩形尺寸 L*W] --> C2[计算衍生属性: 半周长 L+W]
        C3[物料端: 半周长:A~B * 长度:C~D] --> C4[双区间解析: 提取半周长及长度上下界]
        C5[物料端: 三维容差 L±150*W±150*H±150] --> C6[三维解析: 提取长宽高上下界]
        C7[名称双写: 保留原名 + 生成归一化别名]
    end

    subgraph Layer4["4. 多通道召回与分词扩展 (Hybrid Recall Layer)"]
        D1[通道一: 型号/品类精准召回]
        D2[通道二: FTS5 倒排索引 - 扩充半周长/尺寸段 Token]
        D3[通道三: 512维向量语义召回 - 39420条全量刷新缓存]
        D1 & D2 & D3 --> D4[合并去重 Top 150 候选集]
    end

    subgraph Layer5["5. 级距打分与 Schema 门禁 (Scoring & Ranking Layer)"]
        D4 --> E1[级距宽度归一化打分器 + 最近邻兜底]
        D4 --> E2[双区间复合加权打分 + 三维容差打分]
        D4 --> E3[Schema 动态门禁: fan 余压降级 should]
        E1 & E2 & E3 --> E4[置信度重排 Top 1~Top 3 候选]
    end

    Layer1 --> Layer2 --> Layer3 --> Layer4 --> Layer5
```

---

## 三、模块一：数据治理与全字段禁用精准拦截引擎

### 3.1 现状缺陷与改造要点
- **现状缺陷**：原代码仅过滤 `code` 列（673条），漏过 `name` 列中 **10,281 条** 禁用标记物料。
- **高危正则纠正**：废弃 `(?:26\d{4}|废弃)` 等可能导致裸数字误匹配和误杀正常在用物料（如 `PP废液收集车`）的危险规则，收紧为精准语义共现模式。

### 3.2 核心代码实现（[app/db_manager.py](file:///c:/Users/wangd/project/ListTrans/app/db_manager.py)）

```python
import re
import pandas as pd
from typing import Tuple, List, Dict

# 精准语义禁用正则：实测命中 10,955 条，漏杀 0 条，误杀 0 条
DISABLE_KEYWORD_PATTERN = re.compile(r'禁用|停用|无效')

def check_row_disabled(row: pd.Series) -> Tuple[bool, str]:
    """
    全字段联合扫描物料记录是否已被禁用/淘汰。
    扫描字段：code, name, category, specification, description
    """
    fields = [
        ("code", str(row.get("code", ""))),
        ("name", str(row.get("name", ""))),
        ("category", str(row.get("category", ""))),
        ("specification", str(row.get("specification", ""))),
        ("description", str(row.get("description", "")))
    ]
    for field_name, val in fields:
        val_clean = val.strip()
        if DISABLE_KEYWORD_PATTERN.search(val_clean):
            return True, f"字段[{field_name}]命中禁用标记: {val_clean}"
    return False, ""
```

### 3.3 审计输出与重复编码治理
1. **禁用物料审计 CSV 导出**：
   导入时将拦截的 10,955 条物料及命中原因自动保存至 `data/audit_disabled_materials_YYYYMMDD.csv`，便于业务部门核验。
2. **重复编码（37行）治理**：
   在写入 SQLite 前，对相同 `code` 的记录根据最新规则去重（保留有效信息最全的记录），并输出去重日志，防止主键冲突或非预期覆盖。
3. **元数据持久化**：
   更新 `materials_metadata`：`total_rows = 50375`、`imported_rows = 39420`、`disabled_rows = 10955`。

---

## 四、模块二：抽取层净化与领域路由前置修复

### 4.1 根因级问题与修复目标
评审实测证实：设备名残留“半周长：”导致 `route_domain` 误判为 `valve_water`，绕过了风阀尺寸门禁，导致全部候选同得 100 分。必须在此处做彻底的前置治理。

### 4.2 设备名称提取净化（[app/query_preprocessor.py](file:///c:/Users/wangd/project/ListTrans/app/query_preprocessor.py)）
在 `extract_device_name` 中增加参数标签词清洗逻辑：

```python
PARAM_LABEL_WORDS = [
    r'半周长\s*[：:]?', r'周长\s*[：:]?', r'长度\s*[：:]?', r'宽度\s*[：:]?',
    r'高度\s*[：:]?', r'风量\s*[：:]?', r'余压\s*[：:]?', r'静压\s*[：:]?',
    r'面积\s*[：:]?', r'厚度\s*[：:]?'
]
PARAM_LABEL_PATTERN = re.compile('|'.join(PARAM_LABEL_WORDS), re.IGNORECASE)

def clean_extracted_device_name(name: str) -> str:
    """剥离残留在设备名称中的参数引导词与孤立标点"""
    cleaned = PARAM_LABEL_PATTERN.sub('', name)
    cleaned = re.sub(r'[\s~～\-—：:]+$', '', cleaned)
    return cleaned.strip()
```

### 4.3 领域路由优先信任 Category（[app/domain_router.py](file:///c:/Users/wangd/project/ListTrans/app/domain_router.py)）
重构 `route_domain` 逻辑：当传入的物料 `category` 或名称已明确命中风阀关键字（如“调节风阀”、“多叶调节阀”、“防火阀”、“止回风阀”）时，**强行锚定为 `damper` 领域**，绝不被后续残留词干扰误判为水阀 `valve_water`。

### 4.4 清理 `extract_params` 误抽“厚度”缺陷
修复属性提取器中将 `1~500mm` 误抽取为 `厚度=500.0` 的正则捕获组漏洞，保证区间数值仅进入 `半周长_min/max`，不产生脏属性。

---

## 五、模块三：几何特征抽取与复合双区间解析引擎

### 5.1 清单端几何衍生计算
在 [app/query_preprocessor.py](file:///c:/Users/wangd/project/ListTrans/app/query_preprocessor.py) 与 [app/domain_extractors.py](file:///c:/Users/wangd/project/ListTrans/app/domain_extractors.py) 中：
- 当清单抽取到矩形尺寸 $L \times W$（如 `630*400`）：
  ```python
  attrs["尺寸"] = "630*400"
  attrs["长"] = float(L)
  attrs["宽"] = float(W)
  attrs["半周长"] = float(L + W)  # 1030.0
  # 面积属性优先级下调，仅作辅助标记
  attrs["面积"] = (float(L) / 1000.0) * (float(W) / 1000.0)
  ```

### 5.2 物料端半周长与消声器“半周长 × 长度”双区间解析
兼容全角冒号 `：` 与消声器复合规格（覆盖 340 条半周长物料，含 96 条消声器）：

```python
def extract_half_perimeter_and_length(text: str) -> Dict[str, Any]:
    res = {}
    # 1. 提取半周长（兼容全角/半角冒号）
    hp_m = re.search(r'半周长\s*[：:]?\s*(\d+)\s*[~～\-—至到]\s*(\d+)\s*(?:mm|m)?', text)
    if hp_m:
        res["半周长_min"] = float(hp_m.group(1))
        res["半周长_max"] = float(hp_m.group(2))
        res["半周长"] = (res["半周长_min"] + res["半周长_max"]) / 2.0
    
    # 2. 提取消声器第二维：长度区间
    len_m = re.search(r'长度\s*[：:]?\s*(\d+)\s*[~～\-—至到]\s*(\d+)\s*(?:mm|m)?', text)
    if len_m:
        res["长度_min"] = float(len_m.group(1))
        res["长度_max"] = float(len_m.group(2))
        res["长"] = (res["长度_min"] + res["长度_max"]) / 2.0
        
    return res
```

### 5.3 三维容差区间解析器（[app/domain_extractors.py](file:///c:/Users/wangd/project/ListTrans/app/domain_extractors.py)）
支持静压箱、器械柜等 279 条物料的三维容差提取：

```python
def extract_tolerance_3d_box(text: str) -> Optional[Dict[str, Any]]:
    """
    解析：（1500±150）*（1500±150）*（900±150）mm 或 1500±150*1500±150*900±150
    """
    pattern = r'[（\(]?\s*(\d+)\s*±\s*(\d+)\s*[）\)]?\s*[*x×]\s*[（\(]?\s*(\d+)\s*±\s*(\d+)\s*[）\)]?\s*[*x×]\s*[（\(]?\s*(\d+)\s*±\s*(\d+)\s*[）\)]?'
    m = re.search(pattern, text)
    if m:
        l_b, l_t = float(m.group(1)), float(m.group(2))
        w_b, w_t = float(m.group(3)), float(m.group(4))
        h_b, h_t = float(m.group(5)), float(m.group(6))
        return {
            "长_min": l_b - l_t, "长_max": l_b + l_t, "长_base": l_b,
            "宽_min": w_b - w_t, "宽_max": w_b + w_t, "宽_base": w_b,
            "高_min": h_b - h_t, "高_max": h_b + h_t, "高_base": h_b,
            "尺寸_类型": "3d_tolerance_box"
        }
    return None
```

### 5.4 名称“双写别名”策略（保护 1,965 条有效在用“医用”物料）
在 [app/normalizer.py](file:///c:/Users/wangd/project/ListTrans/app/normalizer.py) 中，**不破坏性修改原始物料名称**，而是生成 `normalized_alias`（剥离修饰词后）作为辅助检索和匹配通道，确保在用医用物料与普通物料不发生混淆。

---

## 六、模块四：多通道召回与分词索引扩充

### 6.1 FTS5 倒排索引 Token 扩充
1. **物料端 Token 生成**：
   对含有半周长/长度区间的物料，在 `search_tokens` 中注入 `半周长 半周长1001 1001~1500` 等级距特征标签。
2. **查询端 Query 扩展**：
   当清单为 `多叶调节风阀 630*400` 时，自动衍生构建：
   `("多叶调节风阀" AND ("630" OR "400" OR "半周长" OR "1030"))`
   确保 FTS5 通道 100% 稳定召回目标级距候选。

### 6.2 向量索引（Embedding）全量重构（[app/vector_index.py](file:///c:/Users/wangd/project/ListTrans/app/vector_index.py)）
在纯净化入库完成后，调用 BGE-small-zh-v1.5 模型对全部 **39,420 条** 活跃物料生成 512 维向量缓存 `vector_cache_bge-small-zh-v1.5.npy`。

---

## 七、模块五：级距宽度归一化打分引擎与最近邻兜底

### 7.1 级距宽度归一化数学模型
采用级距自身宽度（而非绝对边界）进行误差归一化，彻底消除除零风险与极小值畸变：

```python
def score_point_vs_interval(point_val: float, min_val: float, max_val: float) -> float:
    """
    点值与区间的吻合度得分 (0.0 ~ 1.0)
    - 落在区间内: 1.0 (业务闭区间语义兼容)
    - 越界: 按偏离距离占区间宽度的比例平滑衰减
    """
    if min_val <= point_val <= max_val:
        return 1.0
    width = max(max_val - min_val, 1.0)
    dist = (min_val - point_val) if point_val < min_val else (point_val - max_val)
    return max(0.0, 1.0 - dist / width)
```

### 7.2 级距空档（跳段）最近邻兜底策略
当清单输入（如 $v=4200$）落入物料库未覆盖的级距空档（如 $3501\sim4000$ 与 $6001\sim6500$ 之间）：
- 若所有候选得分均 `< 0.3`，打分器自动选取**物理距离最近的区间**（即 $3501\sim4000$）作为候选，并输出提示：`"级距空档，选取最近邻候选，需人工复核"`，避免 Top-1 退化为随机乱选。

### 7.3 消声器双区间综合打分
消声器打分同时计算半周长与长度两个维度：
$$\text{Score}_{\text{dim}} = 0.6 \times \text{Score}_{\text{半周长}} + 0.4 \times \text{Score}_{\text{长度}}$$
确保半周长相同时，能够精准按长度级距区分 Top-1。

---

## 八、模块六：Schema 门禁解耦与业务单位规范

### 8.1 严守业务原则，修正单位映射表
根据《物料编码瘦身方案》业务原则第 3 条明确规定（“门不能按㎡”），在 [app/matcher.py](file:///c:/Users/wangd/project/ListTrans/app/matcher.py) 中：
1. **坚决删除** `("樘", "㎡")` 映射，防火门/洁净门按“材质 + 开启方式（单开/双开/子母）”进行匹配；
2. **限定作用域**：`("项", "批")`、`("项", "套")` 仅对管件/辅材类品类生效，不进行全局穿透。

### 8.2 Schema 门禁动态放宽（[config/domain_schema.json](file:///c:/Users/wangd/project/ListTrans/config/domain_schema.json)）
- **风机类（fan）**：将 `机外余压`、`静压` 从 `must` 降级为 `should`。当物料精简未标余压时不扣减缺失分；双方均有余压时按区间比对。
- **风阀（damper）与消声器（silencer）**：`must` 中尺寸比对支持长宽直接匹配、半周长级距匹配与双区间复合匹配。

---

## 九、涉及文件与代码修改清单

| 序号 | 目标文件 | 变更性质 | 核心修改说明 |
| :--- | :--- | :--- | :--- |
| 1 | [app/db_manager.py](file:///c:/Users/wangd/project/ListTrans/app/db_manager.py) | **MODIFY** | 全字段精准禁用过滤；拦截 10,955 条，保留 39,420 条；去重 37 行重复 code；导出审计 CSV |
| 2 | [app/query_preprocessor.py](file:///c:/Users/wangd/project/ListTrans/app/query_preprocessor.py) | **MODIFY** | `extract_device_name` 剥离参数标签词；长宽转半周长衍生计算；FTS 分词扩展 |
| 3 | [app/domain_router.py](file:///c:/Users/wangd/project/ListTrans/app/domain_router.py) | **MODIFY** | 优先信任 `category`，纠正风阀路由误判为 `valve_water` 的致命缺陷 |
| 4 | [app/domain_extractors.py](file:///c:/Users/wangd/project/ListTrans/app/domain_extractors.py) | **MODIFY** | 半周长全角冒号支持；消声器双区间解析；三维容差区间解析；修复厚度误抽 |
| 5 | [app/normalizer.py](file:///c:/Users/wangd/project/ListTrans/app/normalizer.py) | **MODIFY** | 名称双写别名生成，保护 1,965 条有效在用“医用”物料 |
| 6 | [app/matcher.py](file:///c:/Users/wangd/project/ListTrans/app/matcher.py) | **MODIFY** | 级距宽度归一化打分；最近邻空档兜底；双区间综合打分；删除 `樘↔㎡` 单位映射 |
| 7 | [config/domain_schema.json](file:///c:/Users/wangd/project/ListTrans/config/domain_schema.json) | **MODIFY** | 风机类 `机外余压` 调整为 should；扩展 damper/silencer 尺寸门禁 |
| 8 | [app/vector_index.py](file:///c:/Users/wangd/project/ListTrans/app/vector_index.py) | **MODIFY** | 纯净 39,420 条物料全量向量索引刷新 |
| 9 | [tests/eval_golden_set.py](file:///c:/Users/wangd/project/ListTrans/tests/eval_golden_set.py) | **MODIFY** | 对接真实 golden_baseline.json 进行自动化回归校验 |

---

## 十、实施阶段规划与里程碑

```
阶段 1: 数据入库治理与禁用精准拦截 (预计 0.5 天)
  ├─ [1.1] 实现 db_manager.py 全字段精准正则过滤与 37 行重复 code 去重
  ├─ [1.2] 执行物料库重新导入，校验库内精准为 39,420 条，导出禁用审计 CSV
  └─ [门禁 1]: SQL 验证库内 0 条 "禁用/停用/无效" 标记

阶段 2: 领域路由修复与特征抽取重构 (预计 1.0 天)
  ├─ [2.1] 修复 query_preprocessor.py 剥离参数标签词，修复 extract_params 厚度误抽
  ├─ [2.2] 重构 domain_router.py，确保风阀 100% 路由至 damper
  ├─ [2.3] 实现 domain_extractors.py 全角冒号半周长、消声器双区间、三维容差抽取
  ├─ [2.4] 实现 normalizer.py 名称双写别名机制
  └─ [门禁 2]: 单元测试验证半周长/容差/消声器抽取率 100%，路由准确率 100%

阶段 3: 打分引擎升级、Schema 门禁适配与向量重建 (预计 1.0 天)
  ├─ [3.1] 实现 matcher.py 级距宽度归一化打分器、最近邻空档兜底与双区间加权
  ├─ [3.2] 清理 matcher.py 单位映射（删除 樘↔㎡），更新 domain_schema.json
  ├─ [3.3] 全量重新生成 39,420 条物料的 512 维 BGE 向量索引
  └─ [门禁 3]: 单专业沙箱测试，8 个半周长候选打分区分度完全正常，满分问题消除

阶段 4: 黄金测试集自动化全量回归与验收 (预计 0.5 天)
  ├─ [4.1] 运行 tests/eval_golden_set.py 全量评测 (1067 用例)
  ├─ [4.2] 验证专项与全量指标均达标
  └─ [门禁 4]: 固化 V0.1.2 Baseline，出具版本验收报告与 Walkthrough
```

---

## 十一、回归评测、真实基线验收标准与回滚预案

### 11.1 真实基线验收指标（基于 [tests/fixtures/golden_baseline.json](file:///c:/Users/wangd/project/ListTrans/tests/fixtures/golden_baseline.json)）

| 评测维度 | 真实基线现状（1067用例） | V0.1.2 验收目标 | 验收判定规则 |
| :--- | :--- | :--- | :--- |
| **禁用物料拦截率** | 6.14% (673/10955) | **100.0%** (10955/10955) | 硬门禁：库内 0 条禁用词物料，误杀 0 条 |
| **半周长/容差专项 Hit@1** | < 15.0%（满分错配严重） | **≥ 80.0%** | 本次改造核心直接验收项 |
| **暖通专业整体 Hit@1** | ~28.5% | **≥ 45.0%** | 提升 ≥ 16.5 个百分点 |
| **全量 Top-1 准确率 (Hit@1)** | **22.31%** | **≥ 30.0%** | 全量提升 ≥ 7.69 个百分点 |
| **全量 Top-3 召回率 (Recall@3)**| **35.61%** | **≥ 45.0%** | 全量提升 ≥ 9.39 个百分点 |
| **全量 MRR** | **0.307** | **≥ 0.400** | 提升 ≥ 0.093 |
| **严重落网率 (miss@10)** | **48.92%** | **≤ 40.0%** | 下降 ≥ 8.92 个百分点 |

### 11.2 风险评估与回滚预案
1. **自动备份**：实施前备份 `app/materials.db` 为 `app/materials.db.v0.1.2.bak`。
2. **一键回滚指令**：
   ```powershell
   Copy-Item app\materials.db.v0.1.2.bak app\materials.db -Force
   git checkout app/ config/
   ```
   10 秒内恢复至 V0.1.2 初始环境。

---

> **结语**：  
> 本技术方案已全面吸纳独立评审报告的所有关键意见，彻底排除了裸数字误杀、路由误判致打分失效、单位违背业务原则等重大工程隐患。方案技术路径清晰，验证标准客观，可立即进入阶段 1 实施。
