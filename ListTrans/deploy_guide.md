# 华康洁净物料清单匹配工具 V0.0.7 Windows 服务器部署指南

本指南详细描述了如何在 Windows Server 服务器上基于 Docker 容器化部署**华康洁净物料清单匹配工具 V0.0.7**。

---

## 📋 1. 前置依赖准备

在部署前，请确保目标 Windows 服务器已安装并配置好以下软件环境：

1. **Docker Desktop for Windows** 或 **Docker Engine**：
   - 需确保运行在 **Linux 容器模式 (Linux containers)** 下。
   - 确认 Docker 正在运行。可以在 CMD/PowerShell 中执行命令进行检查：
     ```cmd
     docker --version
     ```
2. **Docker Compose v2**：
   - 确认已安装 `docker-compose`：
     ```cmd
     docker compose version
     ```

---

## 📂 2. 部署文件清单与目录结构

请在服务器的磁盘中（建议选择 `C:\ListTrans` 或 `D:\ListTrans`）创建一个干净的安装目录，并将以下打包好的文件与目录复制到其中：

```text
ListTrans/
├── material-matcher-v0.0.7.tar  # 打包生成的 Docker 镜像归档
├── docker-compose.yml           # 容器服务定义文件
├── .env.example                 # 环境变量模板文件（复制并重命名为 .env）
├── model_offline/               # [重要] 离线 NLP 算法模型目录
│   └── paraphrase-multilingual-MiniLM-L12-v2/
├── config/                      # 系统提示词及映射配置文件目录
│   ├── alias_dict.json
│   ├── domain_schema.json
│   ├── llm_config.json
│   └── llm_prompts.json
└── doc/
    └── sample/
        └── 物料明细.xlsx         # [重要] 系统初始化时所需的物料明细 Excel 模板
```

> [!IMPORTANT]
> **离线模型** (`model_offline/`) 和 **初始化物料模板** (`doc/sample/物料明细.xlsx`) 是程序能够离线正确运行的核心，必须包含在上述目录结构中。

---

## ⚙️ 3. 配置环境变量

1. 将部署目录下的 `.env.example` 复制一份并重命名为 `.env`。
2. 使用文本编辑器打开 `.env` 并按需调整参数，常见配置项如下：
   ```env
   # JWT 鉴权密匙（可以使用默认生成的值，或自行使用 openssl 生成）
   SECRET_KEY=941a8a25c1103f6f3a388f7c9e0d16eb222a1b32df511ef5e69dfad1f30a597a
   ALGORITHM=HS256
   ACCESS_TOKEN_EXPIRE_MINUTES=480

   # 默认初始管理员账号（系统在首次启动没有用户时会自动创建该账户）
   ADMIN_USERNAME=admin
   ADMIN_PASSWORD=admin       # 已修改为要求的简易密码

   # CORS 跨域配置
   ENABLE_DOCS=true
   ALLOWED_ORIGINS=*

   # 大模型接入配置（根据需求配置是否启用大模型及 API Key）
   LLM_ENABLED=false
   DASHSCOPE_API_KEY=sk-placeholder
   ```

---

## 🚀 4. 导入镜像与启动服务

打开 Windows CMD 或 PowerShell 并切换至您的部署目录下（例如 `C:\ListTrans`），依次执行以下步骤：

### 步骤 A：载入 Docker 镜像
使用 Docker load 命令将打包好的镜像归档载入本地 Docker 镜像库：
```cmd
docker load -i material-matcher-v0.0.7.tar
```
*导入完成后，运行 `docker images`，应能看到 `material-matcher:v0.0.7` 镜像已在列表中。*

### 步骤 B：启动容器
运行 docker-compose 启动多容器守护进程：
```cmd
docker compose up -d
```
*容器会自动启动并映射 `18080` 端口。同时它会在您的本地 `ListTrans` 目录下挂载 `logs/` 日志文件夹以及 `app/` 下的数据库文件。*

---

## 🔍 5. 状态检查与日志查看

1. **检查容器运行状态**：
   ```cmd
   docker compose ps
   ```
   *状态应显示为 `Up` 正常运行中。*
2. **查看实时运行日志**：
   ```cmd
   docker compose logs -f matcher-app
   ```
   *您应当看到类似如下的日志，代表服务已成功启动并成功加载了离线算法模型：*
   ```text
   INFO:     Started server process [1]
   INFO:     Waiting for application startup.
   Loading offline sentence-transformers model from /app/model_offline/paraphrase-multilingual-MiniLM-L12-v2...
   INFO:     Application startup complete.
   INFO:     Uvicorn running on http://0.0.0.0:18080 (Press CTRL+C to quit)
   ```

---

## 🎯 6. 验证与访问

服务成功启动后，使用浏览器访问服务器的 IP 与映射端口：

- **访问地址**：`http://服务器IP:18080/` 或 `http://127.0.0.1:18080/`
- **系统管理员默认账号**：
  - **用户名**：`admin`
  - **密码**：`admin`
- **物料库初始化验证**：
  - 登录后点击“物料明细库”选项卡，应能看到由 `doc/sample/物料明细.xlsx` 自动解析导入并生成的物料数据库数字画像及所有明细记录。
