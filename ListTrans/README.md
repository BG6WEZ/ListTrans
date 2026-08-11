# 华康洁净物料清单智能匹配工具 (Huakang Clean Material List Matching Tool)

本工具旨在为洁净室工程（涉及暖通、装饰、强电、弱电、给排水、医气等专业）提供高效率、高精度的物料清单智能匹配服务。系统能够将用户上传的原始物料清单自动与企业标准物料数据库进行比对匹配，并生成标准化 Excel 报告。

---

## 🌟 V0.0.8 核心特性 (最新升级)

> **V0.0.8 是"全员组织权限体系落地、多厂商大模型优先路由与全局使用指南升级"的重大版本**。在原有 30 域特征打分与多专业工程清洗引擎基础之上，本版全面落地了企业级人员组织架构与安全登录体系，上线了可视化多模型优先调度中心与全功能使用说明看板。

### V0.0.8 新增能力速览

1. **企业全员账号体系与手机号认证**
   * **全员建账与默认密码**：无缝对接企业人员清单（179位员工），全员采用各自**手机号**作为登录账号，统一初始化密码为 `hkjj@12345`。
   * **岗位权限自动适配**：依据岗位职称（如公司总监 -> `总监`，经理/主管 -> `经理`，工程师 -> `普通员工`）自动配置系统角色权限。
   * **工作台用户画像展示**：工作台右上角升级为展示员工真实**「岗位」**与**「姓名」**，并支持无感异步同步。

2. **用户管理模块与企业架构完全对齐 (`admin.html`)**
   * **表头严格对齐**：完整支持 `姓名` | `1级部门` | `2级部门` | `3级部门` | `工号` | `职位` | `手机号 (登录账号)` | `角色` | `状态` | `最后登录` | `操作` 11 列标准字段。
   * **全量浏览与分页控制器**：新增底部翻页栏（支持 20 / 50 / 100 / 全部），结合粘性表头（Sticky Header）与滚动容器，顺畅浏览 180 位员工。
   * **实时多维检索与一键同步**：支持按姓名、部门、工号、职位、手机号实时快速搜索，并提供「🔄 一键同步人员清单」按钮。

3. **大模型服务与优先调度配置 (LLM Priority Router)**
   * **多模型统一纳管**：支持通义千问（Qwen）、DeepSeek 等多厂家大模型节点的在线配置、修改与启停。
   * **优先级队列与容灾熔断**：配置优先级（Priority 1~100）调度队列与超时降级机制，支持监控累计调用、降级次数与平均响应延迟。
   * **后台 Tab 双面板架构**：管理员后台直观分为「👥 用户与权限管理」与「🤖 大模型服务配置」两大 Tab。

4. **顶部导航栏新增「📖 使用说明」看板**
   * **4 步标准作业全流程**：手机号登录 -> 上传原始清单 -> 50/50 智能比对复核 -> 规范化回写导出。
   * **深度特性与常见问题 (FAQ)**：包含核心算法决胜机制、3分钟自动暂存、多版本协同以及非标物料参数补充技巧。

5. **全链路防缓存加固与离线化**
   * 服务端中间件与页面 Meta 注入 `no-cache, no-store, must-revalidate`，接口携带时间戳参数，杜绝 304 缓存旧数据滞留。
   * 集成离线 `paraphrase-multilingual-MiniLM-L12-v2` 语义向量引擎与 FTS5 倒排索引，保证毫秒级本地计算。

---

## 🌟 V0.0.7 历史特性

> **V0.0.7 是"多专业工程数据清洗规则库与打分决策引擎全面升级"的重要版本**。在 V0.0.6 大模型与预处理引擎基础之上，针对工程实际中的**暖通、医气、配管、线缆**四大核心专业进行了深度数据清洗与匹配决策规则优化，大幅提升非标清单的自动匹配率与首位准确率。

---

## 🌟 V0.0.6 历史特性

> **V0.0.6 是"大模型混合增强 + 询价预处理清洗"的重要版本**。在 V0.0.5 30域 Schema 与硬约束打分体系之上，本版集成了通义千问 LLM 混合增强匹配与精准 Query 预处理清洗分片引擎，进一步提升复杂、非标及施工描述干扰清单的匹配召回率与准确度。

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

## 📁 目录结构 (V0.0.8)

```
ListTrans/
├── app/                      # 后端接口及业务逻辑实现
│   ├── admin_api.py          # 管理员用户管理与多厂商大模型节点调度 API
│   ├── auth_manager.py       # 登录、bcrypt 加密、全员批量初始化及防爆破锁定
│   ├── auth_middleware.py    # API 鉴权拦截器与 RBAC 角色限制
│   ├── db_manager.py         # SQLite 标准物料库导入及 FTS5 全文索引
│   ├── domain_extractors.py  # 领域特征抽取器 (30 域 Schema 多专业清洗引擎)
│   ├── domain_router.py     # 30 域路由映射引擎
│   ├── llm_client.py         # 大模型优先路由调度引擎与多模型容灾降级
│   ├── rule_engine.py        # LLM 自然语言规则生成、验证与持久化引擎
│   ├── rule_api.py           # LLM 规则生成与审核工作台 API
│   ├── matcher.py            # 混合打分与匹配决策引擎 (容差排序与壁厚/尺寸决胜)
│   ├── preprocessor.py       # 文本清洗与参数提取
│   ├── query_preprocessor.py # 询价描述预处理与施工杂质分片提取
│   ├── report_generator.py   # Excel 报表精准样式复制回写模块
│   ├── server.py             # FastAPI 服务主入口与防缓存安全响应头
│   ├── session_manager.py    # 自动草稿与历史版本保存数据库逻辑
│   ├── auth.db               # 用户权限及工作会话数据库
│   └── materials.db          # 标准企业物料明细库 (4.5万+ 条数据)
├── config/                   # 系统配置文件与字典
│   ├── llm_providers.json    # 多厂商大模型节点优先调度配置
│   ├── llm_rules.json        # 生产运行规则持久化库
│   ├── domain_schema.json    # 30 域 Schema 定义
│   ├── tolerance_rules.json  # 属性容差规则配置
│   └── alias_dict.json       # 同义词别名字典
├── doc/                      # 方案设计与技术规范文档
│   ├── technical_solution.md # 系统技术方案说明书 (V0.0.8)
│   ├── Data Portrait.md      # 物料库数字画像分析报告
│   ├── 人员清单.xls          # 企业全员组织架构人员清单数据
│   └── baseline_metrics_v0.0.5.md # 基准回归评测数据
├── web/                      # 前端单页面应用
│   ├── index.html            # 智能工作台与全功能看板 (含使用说明)
│   ├── login.html            # 手机号安全登录界面
│   ├── admin.html            # 系统管理后台 (用户管理 + 大模型配置双 Tab)
│   ├── app.js                # 工作台核心交互与状态流转脚本
│   └── index.css             # 前端设计系统与暗黑主题样式
```
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
