# Phase 3: 并发吞吐、架构解耦与基线留存方案 (POC & Implementation Plan)

- **编制日期**：2026-11-11
- **当前状态**：Phase 3 准备与基线留存中

---

## 一、 跟踪项闭环与承接方案（R1, R3, R6, R7, P2-1）

| 跟踪项 | 级别 | 核心问题 | Phase 3 落地解决措施 |
| :--- | :--- | :--- | :--- |
| **P2-1** | 中危 | 数据库迁移失败被静默吞 | **已率先修复**：`init_auth_db()` 捕获异常后记录 error 并直接 `raise RuntimeError` 阻断启动，杜绝无唯一索引裸奔。 |
| **P2-2** | 低危 | Excel 导入临时工号未打标 | **已率先修复**：`import_users_from_excel()` 对空工号插入的 `EMP_TEMP_{phone}` 同步置 `need_emp_no_setup=1`。 |
| **P2-3** | 低危 | profile 端点双重 close | **已率先修复**：移除 except 分支中的重复 `conn.close()`，统一由 `finally: conn.close()` 安全收口。 |
| **R1** | 中危 | `vector_index` 检索与重建索引无锁竞态 | 在 `VectorIndex` 引入细粒度读写锁（`threading.RLock` 保护整个 `search` 与 `build_index`），且在后台重建索引期间采用 **Shadow Swap（影子索引原子替换）** 机制，确保检索 0 竞态且不被重建阻塞。 |
| **R3** | 低危 | `server.py:687` 重复赋空字典 `upload_tasks` | 删除第 687 行重复声明；保留 614 行唯一定义并增加 `_upload_tasks_lock = threading.Lock()` 全程保护字典读写。 |
| **R6** | 实施注意 | `get_db_cursor` 长连接语义与事件循环防阻 | 规范所有 SQLite 读写均移入线程池 Worker（每个 Worker 线程复用各自的 `thread-local` 独立连接），主事件循环禁止同步执行 DB 访问。 |
| **R7** | 验收风险 | M3 多线程提速受 Python GIL 限制 | **POC 方案先测后定**：先编写 POC 脚本在 Docker 环境对比单线程 vs 4 线程 vs 4 进程在真实物料打分上的吞吐（items/sec），实测加速比后锁定基线，必要时支持进程池切换。 |

---

## 二、 M1 ~ M5 落地技术设计

### 1. M1: 主事件循环 0 阻塞改造
- 将 `server.py` 中 `async_process_upload_task` 的 Excel 解析抽离为同步纯函数 `_parse_and_prepare_upload_excel`，入口立即通过 `await loop.run_in_executor(executor, ...)` 卸载。
- 将 `api_batch_match`、单项匹配端点及后台每日飞轮挖矿 `run_mining()` 全部纳入 `run_in_executor`。

### 2. M3: 批量匹配两阶段真并发架构
- **Stage 1 (向量批量编码)**：单线程集中批编码 `encode_batch(batch_queries)`，充分发挥 PyTorch 底层 C++ 矩阵并行。
- **Stage 2 (候选检索与并行打分)**：`match_jobs` 提交给专用线程池 Worker 并发召回并打分，按原顺序收集结果，原子锁更新匹配进度。

### 3. M4: `score_candidate` 解耦为 7 阶段纯函数管道
- 引入强类型 `ScoringContext`（默认值防 `UnboundLocalError`）。
- 保持二元组返回 `(score, reasons)`，对外签名 100% 兼容。
- 采用当前正在跑的 `eval_golden_set.py`（1067 条）基线数据做逐条回归对比，确保分值与命中结果零退化。

### 4. M5: 全仓连接收拢、分类缓存与静默异常治理
- 全仓统一使用 `get_db_cursor` / `contextlib.closing`。
- `server.py` 设立 `_material_categories_cache` 内存缓存，避免翻页全表扫描。
- 修复 `vector_index.py:340` 路径错误，治理裸 `except: pass`。
