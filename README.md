# 华康洁净物料清单智能匹配工具 (Huakang Clean Material List Matching Tool)

本工具旨在为洁净室工程（涉及暖通、装饰、强电、弱电、给排水、医气等专业）提供高效率、高精度的物料清单智能匹配服务。系统能够将用户上传的原始物料清单自动与企业标准物料数据库进行比对匹配，并生成标准化 Excel 报告。

## 🌟 核心特性

1. **双路混合召回引擎**
   * **关键词召回**：基于 SQLite FTS5 的中文分词优化（单字空格分词化），保障中文工业型号与规格的精确、高召回检索。
   * **语义向量召回**：使用 `paraphrase-multilingual-MiniLM-L12-v2` 预训练模型（384维）提取文本特征。
   * **双轨高可用**：优先使用 `faiss-cpu` 索引检索，若环境兼容性问题导致加载失败，自动无缝降级为 `Numpy` 矩阵相似度检索。

2. **多专业自适应打分与阶梯公差过滤**
   * 支持针对暖通、电气、给排水等多专业的核心参数（风量、功率、DN管径等）正则提取。
   * 自适应评估场景权重（含型号/无型号/纯文本），套用对应比重进行综合打分。
   * 采用阶梯公差计分机制（在允许偏离度内得满分，超出线性衰减，防止特殊非标物料遗漏）。

3. **非破坏性 Excel 导出回写**
   * 读取原始 Excel 清单模板进行精确定位更新。
   * **无损写入**：保留其他专业的数据行、顶部表头、以及底部的合计公式行，避免覆盖历史数据。
   * 完整套用模板行样式，自动根据匹配物料编码填充历史库单价。

4. **三色状态看板与交互式自学习**
   * 前端呈现绿色（置信度 $\ge 85$）、黄色（待校对 $40 \le \text{Score} < 85$）、红色（未匹配 $\text{Score} < 40$）三色标识。
   * 支持人工二次检索与参数快速补全，并实时重新匹配候选列表。
   * 自动捕获用户的**物料绑定行为**并推荐一键入库为规则。后续同类物料将实现 100% 自动精确绑定。

---

## 🛠️ 技术栈

* **前端**：HTML5 + Vanilla CSS + Vanilla JavaScript (纯原生，无复杂脚手架)
* **后端**：FastAPI + Uvicorn + Pydantic + ThreadPoolExecutor
* **数据库**：SQLite 3 (内置 FTS5 检索虚拟表)
* **计算与自然语言处理**：SentenceTransformers (PyTorch-based) + Numpy + FAISS (可选)
* **Excel 解析**：openpyxl + pandas

---

## 🚀 快速开始

### 方式一：本地一键启动 (Windows)

若在 Windows 下直接运行：
1. 确保安装了 Python 3.11 或以上版本。
2. 安装依赖项：
   ```bash
   pip install -r requirements.txt
   ```
3. 双击运行根目录下的 [start.bat](file:///c:/Users/wangd/project/ListTrans/start.bat)。
4. 系统会自动启动 Uvicorn 服务并调用默认浏览器打开本地页面：`http://127.0.0.1:8000`。

### 方式二：Docker 容器化部署

如果你希望使用 Docker 隔离环境或在 Linux 服务器上部署：
1. 启动容器：
   ```bash
   docker-compose up -d --build
   ```
2. 容器将运行在 `18080` 端口上，访问：`http://localhost:18080`。
3. 容器中自带的模型、上传数据和生成报表将保存在宿主机映射的挂载卷中。

---

## 🧪 自动化测试

项目提供了单元测试来保障自动绑定规则和匹配缩放逻辑的正确性。
在项目根目录下执行以下命令运行测试：
```bash
python -m unittest tests/test_auto_binding.py
```

---

## 📁 目录结构

```
ListTrans/
├── app/                      # 后端接口及逻辑实现
│   ├── uploads/              # Excel 上传和生成文件临时存放区
│   ├── server.py             # FastAPI 服务主入口
│   ├── report_generator.py   # Excel 报表精准回写模块
│   ├── db_manager.py         # SQLite 数据库连接及初始化
│   ├── preprocessor.py       # 文本清洗与参数提取
│   ├── matcher.py            # 混合打分与匹配决策引擎
│   └── binding_feedback.json # 用户历史绑定反馈记录
├── config/                   # 自学习匹配规则及其他配置
│   └── auto_binding_rules.json
├── doc/                      # 方案设计与技术规范文档
├── tests/                    # 单元测试模块
│   └── test_auto_binding.py  # 自学习规则测试
├── web/                      # 前端网页静态资源
│   ├── index.html            # 主界面
│   ├── index.css             # 交互设计与样式
│   └── app.js                # 前端交互与 API 调度
├── Dockerfile                # Docker 镜像构建配置
├── docker-compose.yml        # Docker 服务编排
├── requirements.txt          # Python 依赖清单
└── start.bat                 # Windows 快速启动脚本
```
