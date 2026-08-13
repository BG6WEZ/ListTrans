# stability_fix_archive

> 本次目录为 **"系统稳定性与安全修复"** 专项工作区，将所有相关文档、报告、方案集中归档，避免 doc 根目录散乱。

## 目录结构

| 文件 | 说明 |
| --- | --- |
| [系统逻辑与稳定性分析报告.md](./系统逻辑与稳定性分析报告.md) | Coding AI 生成的原始分析报告（含 6 阶段流水线梳理、3 大故障点剖析、耗时预估） |
| [系统稳定性分析报告·独立评审与补充方案.md](./系统稳定性分析报告·独立评审与补充方案.md) | 独立评审报告 — 对 Coding AI 报告的属实性核查 + 8 项新发现稳定性隐患 + 复评耗时表 |
| [系统稳定性与安全修复方案.md](./系统稳定性与安全修复方案.md) | **最终修复方案** — 合并两轮审计 + Coding AI 自查 + 独立核查，含修复清单、修复目标、检验标准 |

## 修复清单总览

### P0 — 必须立即修复

| 编号 | 描述 | 状态 |
| :---: | --- | :---: |
| P0-1 | RuleHitStatsTracker 确定性死锁（Lock -> RLock） | 待修复 |
| P0-2 | 无 token 自动降级 admin（移除 fallback） | 待修复 |
| P0-3 | 前端 showLoading 30s Timeout 兜底 | 待修复 |
| P0-4 | 后端 /api/upload 90s 超时护栏 | 待修复 |
| P0-5 | 冷启动模型联网下载卡死（强制离线 + 超时） | 待修复 |

### P1 — 高优先级修复

| 编号 | 描述 | 状态 |
| :---: | --- | :---: |
| P1-1 | 文件上传路径穿越（basename 过滤） | 待修复 |
| P1-2 | /api/system/reset 权限改 require_admin | 待修复 |
| P1-3 | 硬编码 JWT SECRET_KEY（强制 env） | 待修复 |
| P1-4 | auth.db 开启 WAL + busy_timeout | 待修复 |
| P1-5 | upload_tasks TTL 清理机制 | 待修复 |
| P1-6 | hybrid_recall SQLite 连接池化 | 待修复 |
| P1-7 | ThreadPoolExecutor max_workers 10 -> 4 | 待修复 |

### P2 — 中优先级修复

| 编号 | 描述 | 状态 |
| :---: | --- | :---: |
| P2-1 | 5 处 f-string SQL 拼接改参数化 | 待修复 |
| P2-2 | utcnow() 改为 now(timezone.utc) | 待修复 |
| P2-3 | @app.on_event 改为 lifespan | 待修复 |
| P2-4 | print(DEBUG) 改为 logger.debug | 待修复 |
| P2-5 | 数据画像等接口补鉴权 | 待修复 |
| P2-6 | 双轮匹配加开关默认关闭 | 待修复 |

### P3 — 低优先级 / 技术债

| 编号 | 描述 | 状态 |
| :---: | --- | :---: |
| P3-1 | server.py 拆分为多 APIRouter | 待修复 |
| P3-2 | admin_api 重建索引异步化 | 待修复 |
