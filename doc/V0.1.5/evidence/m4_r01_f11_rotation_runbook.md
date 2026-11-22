# V015-M4 / F11 外部凭据轮换与部署验证运行手册（待授权方实施与核验）

> **边界声明**：本手册由开发AI落盘于 `V015-M4 / R01`，仅规定凭据轮换步骤、验收标准与回滚边界；真实生产凭据轮换、SSH密钥绑定、远端部署与线上验证须由**用户授权方**操作并出具独立脱敏核验凭据。本仓库及本文件不包含任何真实密钥、口令、IP或私钥内容。

## 1. 需轮换的凭据清单（代码脱敏后仍须外部处置）

1. **应用会话签名密钥 (`SECRET_KEY`)**
   - 轮换目标：废弃历史上曾默认使用的签名密钥默认值或占位符，生成至少 32 字节（64 位十六进制或等效高熵随机串）的独立生产 `SECRET_KEY`。
   - 生效结果：所有旧签名 JWT 立即失效，生产端不允许降级回默认弱密钥（否则进程在导入阶段 `fail-closed` 退出）。
2. **管理员与初始口令 (`ADMIN_PASSWORD` / 历史默认弱密码)**
   - 轮换目标：部署环境变量 `ADMIN_PASSWORD` 设置高强度独立随机口令（禁止历史默认弱口令或常见简单密码）；存量库中弱默认密码账号由 `init_auth_db()` 保守迁移标记 `must_change_password=1`，强制首次登录后修改。
   - 生效结果：旧默认密码与旧会话全部失效。
3. **云端服务器部署凭据（`DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_SSH_KEY_PATH` / `DEPLOY_KNOWN_HOSTS`）**
   - 轮换目标：停用历史上写死在脚本中的 SSH 口令登录方式，在目标云主机轮换 SSH 密钥与登录密码，撤销旧密码登录或绑定专用 `ed25519` 部署私钥与 `known_hosts` 指纹校验；所有脚本严格通过环境变量注入（详见 `deploy_guide.md`）。
4. **模型/第三方 API Key 与数据库凭据**
   - 轮换目标：核查目标主机 `.env` 中的所有 LLM Provider API Key 与云端数据库凭据并完成轮换；禁止将真实 `.env`、`*.db`、`*.db-wal`、`*.db-shm`、`*.pem`、`*.key`、`scripts/cloud_users_snapshot.json` 打包进 Docker 构建上下文或镜像层。

## 2. Docker 镜像层与生产启动核验门禁（Docker 守护进程在线后执行）

1. 运行 `docker build --no-cache -t listtrans:v015-m4-check .` 验证构建仅复制白名单目录（`app/*.py`、`web/`、`config/`、`requirements.txt`）。
2. 运行镜像内容检查确认以下敏感对象零出现：
   - `.env` / `.env.*`（仅允许模板 `.env.example`）
   - `*.db` / `*.db-wal` / `*.db-shm` / `*.db-journal`
   - `scripts/`、`doc/`、`tests/`、`*cloud_users_snapshot*`、`*.pem`、`*.key`
3. 验证生产容器在缺失或弱 `SECRET_KEY` / `ADMIN_PASSWORD` 时启动即安全拒绝（非零退出），在配置强环境变量后单 worker（`--workers 1`）正常启动并完成 `/api/auth/login` -> `must_change_password` -> `/api/auth/profile` -> 重新登录完整闭环。

## 3. 外部授权方核验与签收标准

只有授权方在实际运行环境完成以下步骤并提供脱敏核验记录（严禁在日志或文档中回填明文密钥/IP）后，**统筹AI** 方可在审核关闭基础上准予关闭 `F11`：

- [ ] 生产服务器 `.env` 已写入全新高熵 `SECRET_KEY` 与 `ADMIN_PASSWORD`，历史默认值已废弃；
- [ ] 云端主机 SSH 凭据及已暴露 API Key 已完成外部轮换并验证旧凭据登录被拒；
- [ ] 容器部署目标已完成真实 `docker build` 与镜像层扫描，无 `.env`、`*.db*`、私钥、人员清单或快照残留；
- [ ] 回滚边界确认：如遇发布异常，仅回退容器镜像或配置版本，不得恢复已撤销的旧默认口令与弱签名密钥。
