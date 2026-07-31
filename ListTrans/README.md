# 华康洁净物料清单智能匹配工具 (Huakang Clean Material List Matching Tool)

本工具旨在为洁净室工程（涉及暖通、装饰、强电、弱电、给排水、医气等专业）提供高效率、高精度的物料清单智能匹配服务。系统能够将用户上传的原始物料清单自动与企业标准物料数据库进行比对匹配，并生成标准化 Excel 报告。

---

## 🌟 V0.0.6 核心特性 (最新升级)

> **V0.0.6 是"大模型混合增强 + 询价预处理清洗"的重要版本**。在 V0.0.5 30域 Schema 与硬约束打分体系之上，本版集成了通义千问 LLM 混合增强匹配与精准 Query 预处理清洗分片引擎，进一步提升复杂、非标及施工描述干扰清单的匹配召回率与准确度。

### V0.0.6 新增能力速览

1. **Query 询价描述预处理与杂质噪声切分 (`query_preprocessor.py`)**
   * **文本分片**：自动切分物料描述主块与“工作内容/施工部位/调试/全费用”等施工说明噪声块，消除冗余干扰。
   * **二级键值解析**：精准提取 `1.名称` / `2.规格型号` 等标号键值结构。
   * **HVAC 专有名词映射**：涵盖 MAU、PAU、RAU、FAU、FCU、RCU、DOAS、VAV、RTU、HRU、ERU、HPU、FPU、AHU 等 14 类空调机组的专业中英文映射识别。
2. **通义千问 LLM 混合增强引擎 (`llm_client.py`)**
   * 支持通过 DashScope API 接入 `qwen-plus` / `qwen-turbo` 大模型。
   * **智能缓存与防刷**：内置 SQLite `llm_cache` 结果持久化缓存，支持信号量并发控制 (Max 3) 与熔断降级 (Circuit Breaker)。
   * **动态控制 API**：提供 `/api/llm/status` 实时监控接口与 `/api/llm/toggle` 动态开关接口。
3. **数据管理与系统重置 API 增强**
   * `GET /api/db/materials`：物料标准库多条件与分页查询接口。
   * `DELETE /api/system/reset`：一键清空系统运行数据（草稿会话、临时上传文件及绑定日志）。
4. **离线治理工具与自动化测试集**
   * `scripts/batch_llm_structure.py`：离线大模型批处理物料结构化工具。
   * `tests/test_query_preprocessor.py` 与 `tests/test_llm_integration.py` 单元测试保护。

---

## 🌟 V0.0.5 历史特性

> **V0.0.5 是"结构化改造 + 可回归度量"的关键版本**。在 V0.0.4 安全与会话体系之上，本版完成了方案 [final_structuring_master_plan.md](./doc/final_structuring_master_plan.md) 提出的 30 域结构化 Schema、配置驱动 Hard Gates、单向缺失惩罚、异步局部重算、金标回归、以及利川清单专项优化。金标 326 条实测 Top-1 **96.32%** / Top-3 **97.55%** / Must **100%**。

---

## 🛠️ 技术栈

* **前端**：HTML5 + Vanilla CSS + Vanilla JavaScript (纯原生，带中央 Token 拦截钩子)
* **后端**：FastAPI + Uvicorn + Pydantic V2 + ThreadPoolExecutor
* **数据库**：双 `SQLite 3` 独立数据库文件（内置 FTS5 全文索引）
  * `app/materials.db`：保存全局企业标准物料明细、LLM 缓存及全文倒排检索。
  * `app/auth.db`：保存用户账号、RBAC 角色权限体系，以及自动暂存草稿和历史命名进度。
* **计算与自然语言处理**：SentenceTransformers (PyTorch-based) + Numpy + FAISS-CPU (可选) + 通义千问 LLM API Client
* **Excel 解析**：openpyxl + pandas

---

## 🚀 快速开始

### 方式一：本地一键启动 (Windows)

若在 Windows 下直接运行：
1. 确保安装了 Python 3.9 ~ 3.11 版本。
2. 复制配置模板：将根目录下的 `.env.example` 复制为 `.env` 并按需调整密钥和初始管理员密码（大模型接入需配置 `DASHSCOPE_API_KEY`）。
3. 安装依赖项：
   ```bash
   pip install -r requirements.txt
   ```
4. 双击运行根目录下的 [start.bat](file:///Users/tlyer/Project/ListTrans/start.bat)。
5. 系统会自动启动 Web 服务并调用默认浏览器打开本地页面：`http://127.0.0.1:18080`。
6. 使用默认管理员账号进行登录测试：
   * **用户名**：`admin`
   * **密码**：`AdminPassword@2026`

### 方式二：Docker 容器化部署

如果你希望使用 Docker 隔离环境或在 Linux 服务器上部署：
1. 启动容器：
   ```bash
   docker-compose up -d --build
   ```
2. 容器将运行在 `18080` 端口上，访问：`http://localhost:18080`。

---

## 🧪 自动化测试

项目提供了单元测试及基准回归测试方案。
在项目根目录下执行以下命令运行测试：
```bash
# 运行单元测试
python -m pytest tests/

# 运行金标基准回归测试
python tests/regression_runner.py
```

---

## 📁 目录结构 (V0.0.6)

```
ListTrans/
├── app/                      # 后端接口及逻辑实现
│   ├── admin_api.py          # 管理员用户管理 API 路由
│   ├── auth_manager.py       # 登录、bcrypt 加密、JWT 签发及防爆破锁定
│   ├── auth_middleware.py    # API 鉴权拦截器与角色限制
│   ├── db_manager.py         # SQLite 标准物料库导入及 FTS5 全文索引
│   ├── domain_extractors.py  # 领域特征抽取器
│   ├── domain_router.py     # 30 域路由映射引擎
│   ├── llm_client.py         # 通义千问 LLM 客户端封装与 caching 熔断机制
│   ├── matcher.py            # 混合打分与匹配决策引擎
│   ├── preprocessor.py       # 文本清洗与参数提取
│   ├── query_preprocessor.py # 询价描述预处理与施工杂质分片提取
│   ├── report_generator.py   # Excel 报表精准样式复制回写模块
│   ├── server.py             # FastAPI 服务主入口与安全响应头
│   ├── session_manager.py    # 自动草稿与历史版本保存数据库逻辑
│   ├── binding_feedback.json # 用户历史绑定反馈记录
│   ├── auth.db               # [运行时自动生成] 用户及工作会话数据库
│   └── materials.db          # [运行时自动生成] 标准企业物料明细库 (含 llm_cache)
├── config/                   # 系统配置文件与字典
│   ├── llm_config.json       # LLM 评分与抽取阈值配置
│   ├── llm_prompts.json      # LLM 提示词模板
│   ├── domain_schema.json    # 30 域 Schema 定义
│   └── alias_dict.json       # 同义词别名字典
├── doc/                      # 方案设计与技术规范文档
│   ├── technical_solution.md # 系统技术方案说明书 (V0.0.6)
│   ├── Data Portrait.md      # 物料库数字画像分析报告
│   ├── baseline_metrics_v0.0.5.md # 基准回归评测数据
│   └── sample/               # 模板文件样例（原始清单、标准格式、物料明细）
├── logs/                     # 运行及安全日志归档文件夹
│   ├── app_runtime.log       # 系统运行业务日志
│   └── security.log          # 登录防爆破与敏感操作审计日志
├── scripts/                  # 批处理与数据治理脚本
│   ├── batch_llm_structure.py# 大模型批处理物料库结构化工具
│   └── export_structured_db.py # 结构化物料库导出工具
├── tests/                    # 单元测试与基准回归模块
│   ├── test_llm_integration.py
│   ├── test_query_preprocessor.py
│   └── regression_runner.py
├── web/                      # 前端网页静态资源
│   ├── index.html            # 智能匹配工作台主页
│   ├── index.css             # 交互设计与高档玻璃微光样式
│   ├── app.js                # 前端交互与 API 调度 (含全局 Token 注入)
│   ├── login.html            # 登录界面
│   ├── login.css             # 登录界面样式
│   ├── admin.html            # 管理员用户控制后台
│   └── logo500.png           # 企业 Logo 品牌徽标
├── requirements.txt          # Python 生产依赖清单
├── .env.example              # 环境变量配置模板
├── .gitignore                # Git 忽略配置
└── start.bat                 # Windows 快速启动脚本
```
