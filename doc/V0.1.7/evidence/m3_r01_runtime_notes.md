# V017-M3 R01 运行治理复核说明

## 运行边界

应用继续使用单个 Uvicorn worker。后台和重型 HTTP 操作按用途限制运行与排队容量，超限返回 `429`。原物料导入互斥和恢复写阻断继续返回 `409`。容量保留到执行线程实际退出，可防止超时后继续堆积后台工作。响应超时或取消后，由下一协作检查点终止待处理部分。SQLite 提交与 M5 已经进入提交的导入操作保持各自的原子提交语义。Python 线程内的单次原生调用或 I/O 依赖自身返回或超时，评测子进程可终止并回收。

| 用途 | 运行线程 | 排队额度 | 时间预算 |
|---|---:|---:|---|
| 单条与批量 JSON 匹配 | 4 | 8 | 单条、重算 120 秒，批量 300 秒 |
| Excel 清单上传匹配 | 2 | 4 | 请求接入 900 秒，行处理按既有每行预算收紧 |
| 物料导入、重解析和手动向量重建 | 1 | 0 | 导入沿用 600 秒提交仲裁，重解析和重建 900 秒 |
| 导出和清理 | 2 | 4 | Excel 导出 300 秒，其他 120 秒 |
| 规则及管理重操作 | 2 | 4 | 120 秒 |
| 手动和定时挖掘 | 1 | 1 | 300 秒 |
| 沙箱评测 | 1 | 3 | 总 660 秒，单子进程 300 秒 |
| 异步 LLM 补参 | 1 | 2 | 600 秒，保留每批 50 条上限 |

清单、导入和重解析由本人通过 `POST /api/upload/cancel/{task_id}` 发起取消。规则评测使用 `POST /api/llm-rules/eval/{eval_id}/cancel` 或 `POST /api/flywheel/suggestions/eval/{task_id}/cancel`，保留相应角色和本人检查。上传和评测终态缓存默认 1,800 秒，分别限制为 50 和 100 个记录。活跃记录继续可查。评测发布凭据仍由 SQLite 持久化并走现有有效期和发布门禁。

## 保留和清理

管理员调用 `POST /api/admin/runtime/cleanup`。默认 `dry_run=true`，使用 `dry_run=false` 才实际清理。运行和审计日志记录操作结果。上传及导出按程序生成的文件名识别，日志只清轮转文件，隔离目录同时验证目录名与应用标识。业务库、账号库、向量缓存及其恢复材料、配置、文档、模型、数据和备份目录均受保护。`BACKUP_DIR` 可登记额外备份根。符号链接、Windows junction 与活跃文件或目录排除。文件数量约束保留最近 60 秒内的文件，保证新生成的下载可开始。

| 环境变量 | 默认值 | 作用 |
|---|---:|---|
| `LISTTRANS_TASK_TTL_SECONDS` | 1800 | 终态内存记录秒数 |
| `LISTTRANS_FILE_TTL_SECONDS` | 86400 | 运行文件保留秒数 |
| `LISTTRANS_RUNTIME_FILE_LIMIT` | 200 | 每类非活跃运行产物保留上限 |
| `LISTTRANS_LOG_MAX_BYTES` | 10485760 | 单个运行日志轮转字节数 |
| `LISTTRANS_LOG_BACKUPS` | 5 | 轮转日志保留份数 |

## 依赖及离线部署

Windows Python 3.12 的运行依赖闭包 73 项，测试闭包 79 项。递归检查声明的版本、适用的环境标记和 extras，并运行 `pip check`。环境唯一额外分发包为安装工具 `pip`。应用直接使用的 Starlette、Pydantic 和 bcrypt 补列为运行依赖。清单还绑定 38 份离线模型文件、16 份配置和 4 份运行评测资产的 SHA256。项目没有可供核验的 wheelhouse，本机 pip wheel 缓存也为空。[依赖与资产明细](m3_r01_dependency_assets.json) · [运行锁](m3_r01_runtime_windows_py312.lock) · [测试锁](m3_r01_test_windows_py312.lock)

Windows 锁用于复核当前测试环境。容器采用 Linux Python 3.11，离线部署需在相同 Python 小版本和目标平台准备 wheelhouse，锁定其 wheel 哈希，再以 `--no-index --find-links` 安装。模型及配置要与部署资产清单一致。该轮证据来自既有 Windows venv 和本机离线资产，Linux 离线重装属于验证缺口。
