# ListTrans V0.2.0 RC 运行与恢复手册

当前工作区为 **V0.2.0 发布候选（RC）／待统筹终审，未获生产发布授权**。本手册用于隔离预验收和准备运行材料。资产校验、支持范围和待验证项目见[发布候选清单](doc/V0.2.0/evidence/v020_release_candidate_manifest.md)。

## 准备本机预验收

1. 使用已核验的 Windows Python 3.12 venv 和对应[运行依赖锁](doc/V0.1.7/evidence/m3_r01_runtime_windows_py312.lock)。运行 `python -m pip check`，核对已安装版本。容器基础环境为 Linux Python 3.11，需要按目标平台独立锁定依赖及 wheel SHA256。
2. 在仓外受控副本中准备应用、网页、16 份基准配置、3 份运行时评测资产、隔离 SQLite 库和 `model_offline/bge-small-zh-v1.5`。模型需保留原有目录结构。该模型产生 512 维向量。SQLite 与向量缓存应属于同一代目录，缓存不兼容时按索引流程重建。
3. 将 `MATERIALS_DB_PATH`、`AUTH_DB_PATH`、`CONFIG_DIR`、`VECTOR_CACHE_DIR`、`UPLOAD_DIR`、`OUTPUT_DIR`、`LOG_DIR`、`BINDING_FEEDBACK_FILE`、`FEEDBACK_SAMPLES_FILE` 和 `GOLDEN_DETAIL_FILE` 指向隔离位置。为不可变素材保留只读输入。设置 `AUTH_DEV_MODE=false`、`PYTHON_DOTENV_DISABLED=1`、`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`。10 项受控开关值见[候选清单](doc/V0.2.0/evidence/v020_release_candidate_manifest.md)。
4. 非测试启动使用 `APP_ENV=production`、`TESTING=0`、全新强随机 `SECRET_KEY`。空账号库首次启动使用专门签发的强 `ADMIN_PASSWORD`，`ADMIN_USERNAME` 可自定义。通过受控环境注入秘密，设置 `ENABLE_DOCS=false`，`ALLOWED_ORIGINS` 填精确允许的源。自动化回归使用 `TESTING=1` 和测试夹具账号，按[复验方法](doc/V0.2.0/evidence/m3_r01_reproduce.md)运行前后隔离核验。

在隔离副本根目录以**单 Worker**启动本机预验收，避免进程之间的索引、规则状态和 SQLite 写入分离。

```powershell
python -m uvicorn app.server:app --host 127.0.0.1 --port 18080 --workers 1
```

启动后先核对 `GET /api/auth/me` 未登录返回 `401`，再校验隔离账号登录、角色分配和后台健康信息。用脱敏 `.xlsx` 清单试做一次上传、人工确认、草稿恢复和导出。核对未确认行的 `422` 与确认后的 Excel 下载。离线模型加载失败时检查目录、模型日志和索引状态，关键词 FTS5 可继续提供候选。

## 容器候选核验

仓库 `Dockerfile` 的 pip 安装步骤依赖外部仓库，`docker-compose.yml` 仍使用历史 V0.1.4 标签并将主机应用目录覆盖到容器。`ENV-V017-01` 要求在目标 Linux 环境使用本地已校验基础镜像与目标平台 wheelhouse 完成断网重建、逐层白名单检查和无网络脱敏启动。完成验收并取得授权后，分发按 digest 锁定的镜像归档，显式映射目录和配置副本。

- 镜像包括白名单内源码、网页、基准配置及运行评测资产。模型、SQLite、缓存、日志、上传、输出与秘密由运行时隔离挂载或注入。交付时逐层核对 `tests/`、`doc/`、业务库及其 WAL/SHM、`.env`、个人信息和明文密钥的排除记录。
- 使用启动命令核对 `--workers 1`。验收时不发布端口，在断网容器内走本机回环健康、鉴权和脱敏评测。检查内置基准配置加载、隔离测试目录写入及退出后无业务资产变化。

## 备份、恢复与回滚

1. 进入维护窗口，停止新增写入并等待在途任务退出。用 SQLite 在线备份机制，或经确认的停机快照，分别备份物料库和账号库。同时保存配置、规则快照、同代向量缓存、应用构建和模型标识。记录 SHA256、目录 revision、解析 schema 和向量模型／维度。
2. 在隔离副本恢复并逐项核对 SQLite 完整性、物料和 FTS 行数及抽样查询、缓存映射、向量条数和 revision。对于旧目录 revision 缺失或旧格式平铺缓存，按兼容检查加载。目录变化后验证新缓存与目录同代。用实际上传、人工确认、草稿恢复、`200/422` 导出检验恢复后的服务。
3. 目录发布失败时检查旧 SQLite／FTS、进程内向量、磁盘缓存及配置是否还在同代。若系统进入持久写阻断，保留现场和恢复材料，修复后由授权负责人调用 `POST /api/admin/recovery/verify-clear` 做一致性核验。核验通过后再恢复导入、重解析和索引更新。
4. 飞轮规则发布采用 `eval_id` 绑定本次内容。回退通过 `POST /api/flywheel/rollback` 使用当前栈顶 `snapshot_version`。回退后复核规则配置、运行加载与待处理行。应用版本回退先验证旧代码与恢复副本 schema 兼容，并保留已轮换秘密和 Token 撤销记录。

## 首次真实部署前凭据门禁

`PRG-F11` 仍开放。授权运维应轮换适用的历史远程登录凭据、JWT `SECRET_KEY` 和曾使用弱口令的账号凭据。账号重置使用唯一临时口令并完成首次改密。验证旧凭据被拒绝、新凭据能通过相应连接及 `/api/auth/me`，留存包含对象代号、日期、拒绝及通过结果、授权人和证据引用的脱敏记录。回滚期间继续使用新的凭据与撤销状态。
