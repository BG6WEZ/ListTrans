# V015-M4 R03 / V-M4-02 授权方真实状态说明与本地隔离核验报告

- **任务包 / 轮次**：`V015-M4` / `R03` / 遗留项 `V-M4-02`（对应台账 `F11`）
- **代码基线（R03 冻结 HEAD）**：`e964fb16848de9dece8e729e801b61c7d81dd341`
- **核验模式**：`read_only_workspace_audit_and_isolated_v014_v015_rehearsal`（只读工作区基线稽核 + 独立临时目录多进程隔离演练）
- **实证机读报告**：`doc/V0.1.5/evidence/m4_r03_vm402_verify.json`
- **状态边界**：本文档仅陈述 V0.1.5 当前未上线的真实客观状态与本地隔离验证结果，**不宣称 `M4` 已放行或 `F11` 已关闭**；是否采信并解除 `M4` 阻塞以**审核 AI 独立核验**与**统筹 AI 裁决**为准。

---

## 一、授权方（项目所有者）关于 V0.1.5 非上线状态的客观陈述

根据统筹补充裁决（`doc/V0.1.5/rounds/V015-M4_R03_V-M4-01_统筹补充裁决.md`）中关于“未配置/未启用/不适用对象应有明确证据，不应虚构轮换”的要求，授权方就 `V0.1.5` 当前阶段说明如下：

1. **V0.1.5 尚未执行线上部署**：当前全部开发与验证均在本地工作区及隔离临时目录中完成，不存在正在运行的 V0.1.5 线上生产服务，未向远程云主机部署，未操作云控制台 SSH，也未启用真实对外 LLM API 计费调用。
2. **禁止在无授权窗口下篡改本地冻结资产**：依据 V015-M4 审计基线与业务隔离门禁要求，开发阶段严禁改动工作区 `.env`、`app/auth.db`、`app/materials.db`、`app/`、`scripts/` 及 `V-M4-01` 已交付文档；因此在未获统筹“解除本地数据冻结/开启实库迁移窗口”批准前，保持工作区 `.env` 与 V0.1.4 15 列 `app/auth.db` 原样冻结是唯一合规路径。
3. **四类凭据如实申报**：本阶段未伪造任何外部云主机或第三方的“实际生产轮换截图”；全部可程序化验证的轮换、JWT 失效、强制改密与 fail-closed 防线均已在独立隔离目录中通过跨进程真实 HTTP 接口复现并录得 `11/11` 通过证据；尚未发生真实上线动作的外部环境对象已如实标注为**未配置 / 离线禁用 / 上线前置门禁（Pre-Release Gate）强制约束**。

---

## 二、F11 四类凭据对象逐项核验与处置清单（已脱敏）

严格对照 `doc/V0.1.5/evidence/m4_r02_f11_rotation_runbook.md` 定义的 4 类凭据对象：

### 1. 会话签名密钥 `SECRET_KEY`（`.env` / 部署环境变量）

- **当前工作区事实（只读核验）**：
  - 工作区 `.env` 保持与审计保护基线一致未改动：`sha256 = 889a6bd2ebf47943ed0cd174bddb337dcc21dd9be95ec2ec738a9d5ab788598b`，共 9 个变量名（`SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ENABLE_DOCS`, `ALLOWED_ORIGINS`, `LLM_ENABLED`, `DASHSCOPE_API_KEY`），无任何密钥值读取或回写。
- **隔离实证（`m4_r03_vm402_verify.json`）**：
  - 子进程 Phase A 使用临时随机 `old_secret_1` 签发管理员 JWT 并请求 `/api/auth/me` 返回 `200`；
  - 子进程 Phase B 轮换注入临时随机 `new_secret_2` 重启服务后，旧 `SECRET_KEY` 签发的 JWT 请求 `/api/auth/me` 被拒绝返回 `401`，新登录签发 JWT 返回 `200`；
  - 未提供 `SECRET_KEY` 且处于生产检查环境（`TESTING!=1`）时服务 fail-closed 启动失败（`missing_secret_key_fail_closed: true`）。
- **处置与上线前置约束**：V0.1.5 本地冻结期间不改动工作区 `.env`；进入未来实际生产部署前（Pre-Release Gate），必须在目标运行环境重新生成高熵 `SECRET_KEY` 并重启全部实例使旧会话全量失效。

### 2. 持久化管理员 / 员工账号凭据（`app/auth.db` `users` 表）

- **当前工作区事实（只读核验）**：
  - 工作区 `app/auth.db` 完整保留 V0.1.4 冻结基线未改动：`sha256 = b0eb34acdf3625b131f9d155bc6bb927de403aae0e8518d6f0ca27f92ca0ac80`；
  - `users` 表保持 V0.1.4 的 15 列结构（无 `token_version`、`account_epoch`、`must_change_password` 列），总计 186 个账号（`admin: 3`, `director: 3`, `manager: 23`, `employee: 157`），全程只读连接且未残留 `-wal` / `-shm` 副文件。
- **隔离实证（`m4_r03_vm402_verify.json`）**：
  - 在隔离临时库注入合成 V0.1.4 四角色（`admin` / `director` / `manager` / `employee`）15 列账号并连续两次执行 `init_auth_db()` 验证迁移幂等（`migrated_user_count: 4`）；弱口令员工自动标记 `must_change_password = 1` 且访问规则接口被拦截返回 `403`；
  - 证明仅修改环境变量 `ADMIN_PASSWORD` 不会篡改或覆盖既有数据库管理员口令（使用仅在环境变量中修改的新 `ADMIN_PASSWORD` 登录返回 `400`，原持久化口令仍返回 `200`）；
  - 持久化管理员密码轮换（`PUT /api/auth/password`）：返回 `200`（`relogin_required: true`），轮换后旧密码登录返回 `400`、旧 JWT 访问 `/api/auth/me` 与 `/api/llm-rules` 均返回 `401`，新密码登录与访问均返回 `200`；新子进程重启后持久化验证保持 `400 / 401 / 200`；
  - 管理员重置员工密码（`PUT /api/admin/users/{id}/password`）：返回 `200` 并生成唯一临时密码，旧密码 `400`、重置前旧 JWT `401`、临时密码未改密时访问业务接口 `403`；员工完成强制改密（`PUT /api/auth/password` 返回 `200`）后临时密码与旧 JWT 失效（`400 / 401`），新密码登录 `must_change_password=false` 且规则接口恢复 `200`。
- **处置与上线前置约束**：本地工作区 `app/auth.db` 保持冻结；若后续版本发布复用历史业务用户库或启用真实环境迁移，上线前须先执行历史全量管理员密码轮换与员工首登强制改密督导。

### 3. 云服务器部署 SSH / 登录凭据（`scripts/_deploy_env.py` 及部署脚本）

- **当前工作区事实（只读核验）**：
  - 检查 `scripts/_deploy_env.py`、`scripts/deploy_cloud.py`、`scripts/deploy_to_cloud_safe.py`、`scripts/check_cloud_status.py`、`scripts/probe_servers.py` 共 5 个部署脚本，全部与审计保护基线哈希一致（`all_scripts_match_audit_baseline: true`）；
  - 当前未执行 V0.1.5 云端部署，且未配置任何远程部署环境变量（`env_vars_configured_for_v015: false`, `remote_deployment_enabled: false`）。本项在当前未上线状态下**不适用外部实际 SSH 密钥轮换（N/A）**。
- **隔离实证（`m4_r03_vm402_verify.json`）**：
  - 验证 `scripts/_deploy_env.py` 及 `scripts/probe_servers.py` 在未设置环境变量时严格 fail-closed 报错拒绝运行（`ssh_load_failed_closed: true`、`probe_targets_failed_closed: true`），确认代码仓库内不存在任何默认或硬编码主机 IP / 登录口令。
- **处置与上线前置约束**：未来实际执行云端发布前（Pre-Release Gate），如启用过外部 SSH 凭据，须先在云服务商控制台轮换并仅通过部署时环境变量注入。

### 4. 大模型 API Key（`DASHSCOPE_API_KEY` / `app/materials.db` `llm_providers`）

- **当前工作区事实（只读核验）**：
  - `app/materials.db` 保持冻结未改动：`sha256 = 9b7be8c2bd8f420f25b12a5394e8470cc2910dd363da875eafd04c1385b78927`；仅只读核验 `llm_providers` 表非敏感元数据（`qwen-max-default` `is_active=1`, `qwen-plus-fallback` `is_active=1`, `deepseek-v3-fallback` `is_active=0`），未读取 `api_key_enc` 字段；
  - V0.1.5 开发与核验全程遵守不触发真实付费外部 LLM 的红线（`external_llm_calls_in_v015_verification: false`）。本项在当前离线模式下如实判定为**离线禁用 / 冻结未改**。
- **隔离实证（`m4_r03_vm402_verify.json`）**：
  - 在设置 `LLM_ENABLED=false`、`TESTING=0` 的隔离运行期子进程中验证 `llm_client.is_enabled()` 返回 `false`（`llm_is_enabled_under_offline_flag: false`），确认默认离线配置下不会发起外网 LLM 请求。
- **处置与上线前置约束**：如历史 `.env` 或 `app/materials.db` 中曾存在明文/加密外部 API Key，应在未来首次开启线上 `LLM_ENABLED=true` 或发布前于服务商控制台作废旧 Key、重签新 Key 并重新录入或配置。

---

## 三、自动化核验结果汇总（11/11 PASS）

执行命令：`.\venv\Scripts\python.exe doc/V0.1.5/evidence/m4_r03_vm402_verify.py`（退出码 `0`）

| 序号 | 检查项（`m4_r03_vm402_verify.json -> checks`） | 结果 |
| --- | --- | --- |
| 1 | `workspace_env_unchanged` | `true` |
| 2 | `workspace_auth_db_v014_frozen` | `true` |
| 3 | `deploy_scripts_match_baseline` | `true` |
| 4 | `v014_to_v015_migration_idempotent_4_roles` | `true` |
| 5 | `secret_key_rotation_invalidates_old_jwt` | `true` |
| 6 | `admin_password_env_does_not_mutate_existing_db` | `true` |
| 7 | `persistent_admin_rotation_Verified_400_401_200` | `true` |
| 8 | `employee_reset_and_forced_change_verified_400_401_403_200` | `true` |
| 9 | `ssh_deploy_fails_closed_when_unconfigured` | `true` |
| 10 | `llm_disabled_under_offline_flag` | `true` |
| 11 | `business_snapshot_and_vm401_delivery_intact` | `true` |

### 完整性与基线保护零篡改实证

- **业务快照一致性**：`business_snapshot_equal = true`
  - `snapshot_before_sha256`：`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`
  - `snapshot_after_sha256`：`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`
- **基线保护清单（`m4_r03_vm401_audit_baseline.json`）**：`protected_checked = 284`，`protected_mismatches = []`（其中 V-M4-01 补充审核已合法更新的 9 份活跃状态文档全部纳入 `m4_r03_vm401_audit_delivery_hashes.md` 校验）
- **V-M4-01 交付清单（`m4_r03_vm401_audit_delivery_hashes.md`）**：`vm401_delivery_checked = 28`，`vm401_delivery_mismatches = []`

---

## 四、本轮交付产物与 SHA256 清单

以下均为本轮新增核验与说明产物，R03 冻结代码、`V-M4-01` 产物及业务数据未作任何变更：

| 文件路径 | 大小 (Bytes) | SHA256 |
| --- | ---: | --- |
| `doc/V0.1.5/evidence/m4_r03_vm402_verify.py` | 31246 | `7c5b548f788e2b62b681c7891fef8110aceadf8ee9ccdad95d647b971695c6e7` |
| `doc/V0.1.5/evidence/m4_r03_vm402_verify.json` | 5999 | `6ff10006e7975adfe783c9b2b9440e62e3277cc7fd86028cd2f43f1a539abd79` |
| `doc/V0.1.5/evidence/m4_r03_vm402_owner_disposition.md` | 本文件 | 见运行后落盘哈希回执 |

---

## 五、提请审核 AI 与统筹 AI 裁决事项

当前 V0.1.5 尚未上线，本轮已完成 `V-M4-02`（`F11`）全部可本地自动复现的隔离轮换实证与非上线客观状态固化。提请审核 AI 独立复核上述产物，并由统筹 AI 裁决以下二选一路径：

1. **推荐路径（非上线客观态验收 + 上线前置门禁约束）**：认可 V0.1.5 当前未上线的客观事实与 `11/11` 本地隔离轮换/fail-closed 证据，准予解除 `M4` 阻塞；同时将真实环境 `.env`（`SECRET_KEY` / `DASHSCOPE_API_KEY`）、线上持久化账号及外部 SSH 凭据的实际操作绑定为**未来上线前强制前置门禁（Pre-Release Gate）**（对应 `V-M5` 及后续发布检查项）。
2. **备选路径（若统筹坚持须在本地工作区完成实库落盘轮换）**：须由统筹明确下发一次**受控“本地业务快照重签/更新窗口”**，授权备份并对工作区 `.env` 与 `app/auth.db` 执行本地化迁移、凭据轮换及管理员重置，随后重新签发基线快照供复验。
