# 华康洁净物料清单智能匹配工具 (Huakang Clean Material List Matching Tool)

本工具旨在为洁净室工程（涉及暖通、装饰、强电、弱电、给排水、医气等专业）提供高效率、高精度的物料清单智能匹配服务。系统能够将用户上传的原始物料清单自动与企业标准物料数据库进行比对匹配，并生成标准化 Excel 报告。

---

## 🌟 V0.0.4 核心特性

1. **双路混合召回引擎**
   * **关键词召回**：基于 SQLite FTS5 的中文分词优化（单字空格分词化），保障中文工业型号与规格的精确、高召回检索。
   * **语义向量召回**：使用 `paraphrase-multilingual-MiniLM-L12-v2` 预训练模型（384维）提取文本特征。
   * **双轨高可用**：优先使用 `faiss-cpu` 索引检索，若环境兼容性问题导致加载失败，自动无缝降级为 `Numpy` 矩阵相似度检索。

2. **多专业自适应打分与阶梯公差过滤**
   * 支持针对暖通、电气、给排水等多专业的核心参数（风量、功率、DN管径等）正则提取。
   * 自适应评估场景权重（含型号/无型号/纯文本），套用对应比重进行综合打分。
   * 采用阶梯公差计分机制（在允许偏离度内得满分，超出线性衰减，防止特殊非标物料遗漏）。

3. **[NEW] 用户身份认证与 RBAC 动态权限管理 (V0.0.4)**
   * **JWT 安全验证**：采用基于 Token 的无状态鉴权。密码使用原生 `bcrypt` 模块进行加盐哈希存储。
   * **精细化角色控制**：
     * `employee` (员工)：使用工作台进行匹配、校对和导出。
     * `manager` (经理)：查看工作台，只读查看系统配置词典。
     * `director` (总监)：具有经理权限，且可在线上传、替换/增量更新物料库。
     * `admin` (管理员)：完整权限，包括增加、修改、停用系统账号，分配角色权限。
   * **动态 UI 渲染**：前端全局 fetch 钩子自动注入 Bearer Token，并在接收到 `401` 时跳转重定向；前端自动根据登录账号角色隐藏无权查看的功能卡片。

4. **[NEW] 双轨工作进度暂存与多版本保存 (V0.0.4)**
   * **草稿静默自动暂存**：用户编辑时，前端每 3 分钟自动保存一次 items 快照。下次登录时若检测到草稿，系统会主动提示“是否恢复上次未完的编辑工作”。
   * **手动多版本备份**：支持在工作台任意阶段命名保存（支持覆盖已存版本），可在【历史记录】中随时一键恢复或永久删除。历史记录按登录账号严格进行物理隔离。

5. **[NEW] 点击劫持防御与暴力破解锁死 (V0.0.4)**
   * **登录限速防御**：限制同一 IP 或同一用户名在 5 分钟内最多尝试 5 次登录，超过则自动锁定该 IP/用户名 15 分钟，所有安全事件写入独立日志 `logs/security.log`。
   * **安全响应头注入**：服务端强制注入 `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY` 等安全防护响应头。

6. **非破坏性 Excel 导出回写**
   * **无损写入**：保留其他专业的数据行、顶部表头、以及底部的合计公式行，避免覆盖历史数据。
   * 完整套用模板行样式，自动根据匹配物料编码填充历史库单价，并对 A 列序号重新进行全局连续排版。

---

## 🛠️ 技术栈

* **前端**：HTML5 + Vanilla CSS + Vanilla JavaScript (纯原生，带中央 Token 拦截钩子)
* **后端**：FastAPI + Uvicorn + Pydantic V2 + ThreadPoolExecutor
* **数据库**：双 `SQLite 3` 独立数据库文件（内置 FTS5 全文索引）
  * `app/materials.db`：保存全局企业标准物料明细及全文倒排检索。
  * `app/auth.db`：保存用户账号、RBAC 角色权限体系，以及自动暂存草稿和历史命名进度。
* **计算与自然语言处理**：SentenceTransformers (PyTorch-based) + Numpy + FAISS-CPU (可选)
* **Excel 解析**：openpyxl + pandas

---

## 🚀 快速开始

### 方式一：本地一键启动 (Windows)

若在 Windows 下直接运行：
1. 确保安装了 Python 3.9 ~ 3.11 版本。
2. 复制配置模板：将根目录下的 `.env.example` 复制为 `.env` 并按需调整密钥和初始管理员密码。
3. 安装依赖项：
   ```bash
   pip install -r requirements.txt
   ```
4. 双击运行根目录下的 [start.bat](file:///Users/tlyer/Project/ListTrans/start.bat)。
5. 系统会自动启动 Web 服务并调用默认浏览器打开本地页面：`http://127.0.0.1:8000`。
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

项目提供了单元测试及 API 测试方案。
在项目根目录下执行以下命令运行测试：
```bash
python -m unittest tests/test_auto_binding.py
```

---

## 📁 目录结构 (V0.0.4)

```
ListTrans/
├── app/                      # 后端接口及逻辑实现
│   ├── admin_api.py          # 管理员用户管理 API 路由
│   ├── auth_manager.py       # 登录、bcrypt 加密、JWT 签发及防爆破锁定
│   ├── auth_middleware.py    # API 鉴权拦截器与角色限制
│   ├── session_manager.py    # 自动草稿与历史版本保存数据库逻辑
│   ├── server.py             # FastAPI 服务主入口与安全响应头
│   ├── report_generator.py   # Excel 报表精准样式复制回写模块
│   ├── db_manager.py         # SQLite 标准物料库导入及 FTS5 全文索引
│   ├── preprocessor.py       # 文本清洗与参数提取
│   ├── matcher.py            # 混合打分与匹配决策引擎
│   ├── binding_feedback.json # 用户历史绑定反馈记录
│   ├── auth.db               # [运行时自动生成] 用户及工作会话数据库
│   └── materials.db          # [运行时自动生成] 标准企业物料明细库
├── doc/                      # 方案设计与技术规范文档
│   ├── technical_solution.md # 系统技术方案说明书 (V0.0.4)
│   └── sample/               # 模板文件样例（原始清单、标准格式、物料明细）
├── logs/                     # 运行及安全日志归档文件夹
│   ├── app_runtime.log       # 系统运行业务日志
│   └── security.log          # 登录防爆破与敏感操作审计日志
├── tests/                    # 单元测试模块
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
