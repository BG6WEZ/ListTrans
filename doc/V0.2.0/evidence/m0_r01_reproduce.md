# V020-M0 R01 独立复验

从 ListTrans 根目录使用已有 Windows Python venv。原生 Windows 允许 FastAPI 的回环事件循环建立连接。以下命令在仓外原生临时根运行可写步骤，并保持离线护栏。生成类命令要求输出名未占用，可为独立复验指定新文件名。对照汇总要求四个臂的 JSON 和两个输入报告位于同一目录，并使用所示文件名。

```powershell
$py = '.\venv\Scripts\python.exe'
$ev = 'doc/V0.2.0/evidence'
$env:TESTING = '1'
$env:LLM_ENABLED = 'false'
$env:AUTH_DEV_MODE = 'false'
$env:PYTHON_DOTENV_DISABLED = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:LISTTRANS_V018_M0_RECALL = '0'
$env:LISTTRANS_V018_M1_RECALL = '0'
$env:LISTTRANS_V018_M2_RECALL = '0'
$env:LISTTRANS_V019_M0_SCORING = 'legacy'
$env:LISTTRANS_V019_M1_RERANK = 'off'
$env:LISTTRANS_V019_M2_CALIBRATION = 'off'
$env:ENABLE_SERIES_RERUN = 'false'
$env:LISTTRANS_V020_M0_RANKER = 'off'
$env:PYTHONUNBUFFERED = '1'
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m0_r01_isolation_before.json"
& $py -B -X utf8 scripts/v020_feedback_audit.py --snapshot app/materials.db --out "$ev/m0_r01_feedback_sufficiency.json"
& $py -B -X utf8 "$ev/m0_r01_data.py" --out "$ev/m0_r01_hard_negatives.json" --asset "$ev/m0_r01_ranker_asset.json"
```

反馈审计从同一 SQLite 快照校验账号归属、权威源行、初始与目标推荐快照、纠正码、一致性及替代状态。支持快照 WAL 检查、快照前后摘要和事件追溯。可选的 `--reviews` 接受事件摘要到「复核员代号 → 确认物料码」的映射。至少两位复核员一致且与记录标签一致才入选。按事件摘要保留每位复核员投票的摘要和分歧原因。原始复核簿、注册映射与 DB 快照仅在授权的本地隔离区保存。可选的 `--provenance` 接受文档摘要到 `project`、`source`、`collected_at` 三个字段的映射，由可信来源给出稳定项目和清单标识及真实采集时间。文档和事件键可用 `reference()` 从权威快照重算。提供 `--cutoffs TRAIN_END DEV_END` 后，两个截点先冻结，划分依次为 Train、Dev、Holdout。入口拒绝跨集项目、来源清单、文件摘要及标签可用时间重叠，执行失败时保留原因。原始 SQLite 无时区时间遵循 UTC 约定。

排序数据与真实反馈相互独立。金标和治理文件通过结构边界识别行 ID 和 split header，仅反序列化 Train/Dev 标签与查询。拟合只接收 Train 842 的已知可回答单选样本，排除来自完成清单的查询回退，不把等价码标为负例。硬负例按同名、明确结构差异和每查询每种差异的上限采样。对缺少查询正证据的目录对保留审计记录，只有有支持的候选差异进入按查询归一加权的非负最小二乘。权重总预算固定为 5.99 个排序偏好分。实验只改变满足条件的候选顺序。真实业务分数、不可逆 cap、召回池、置信度阈值、直接编码和物理保护行沿用现行路径。Dev 仅作比较。

```powershell
foreach ($mode in @('keyword', 'vector')) {
    $arms = if ($mode -eq 'keyword') { @('off','on') } else { @('on','off') }
    foreach ($arm in $arms) {
        & $py -B -X utf8 "$ev/m0_r01_replay.py" --mode $mode --arm $arm --asset "$ev/m0_r01_ranker_asset.json" --out "$ev/m0_r01_${mode}_${arm}.json" *> "$ev/m0_r01_${mode}_${arm}.log"
    }
}
& $py -B -X utf8 "$ev/m0_r01_compare.py" --out "$ev/m0_r01_comparison.json"
foreach ($arm in @('off','on')) {
    & $py -B -X utf8 "$ev/m0_r01_replay.py" --mode rules --arm $arm --asset "$ev/m0_r01_ranker_asset.json" --out "$ev/m0_r01_gate_$arm.json" *> "$ev/m0_r01_gate_$arm.log"
}
```

每臂新进程使用同一资产、离线护栏和固定样本顺序，逐个顺序执行以减少资源竞争。使用真实应用 `/api/match` 与 XLSX 上传及状态路由，5 条单请求和 1 份 5 行上传预热，随后每臂 1,060 次单条及 22 次上传，其中 21 份 50 行。分位数为 nearest-rank，峰值为进程工作集高水位。同条件门槛以签认预算为准，原全量质量分母另由已冻结的既有流程维护。配对区间按同一工作负载的样本自助重采样，仅用于描述本次样本内差值。规则门禁复用原三规则比对入口，使用 Train/Dev 投影与保护样本，并检查五类安全结论、受约束绑定和导出 422。需核对 OFF 和 ON 的完整防爆探针结果相等。

```powershell
$special = @(
    'tests/test_v020_m0_feedback_ranker.py',
    'tests/test_v019_m1_rerank.py',
    'tests/test_v019_m2_calibration.py',
    'tests/test_v019_m3_review.py',
    'tests/test_v017_m2_catalog_revision.py',
    'tests/test_m3_hard_constraints.py',
    'tests/test_m3_hard_constraints_http.py'
)
$env:LISTTRANS_TEST_ROOT = Join-Path ([IO.Path]::GetTempPath()) ('v020_specialist_' + [guid]::NewGuid().ToString('N'))
& $py -B -X utf8 -m pytest @special -vv -ra -p no:cacheprovider --basetemp (Join-Path $env:LISTTRANS_TEST_ROOT 'pytest') *> "$ev/m0_r01_pytest_specialist.log"
$env:LISTTRANS_TEST_ROOT = Join-Path ([IO.Path]::GetTempPath()) ('v020_full_' + [guid]::NewGuid().ToString('N'))
& $py -B -X utf8 -m pytest tests -vv -ra -p no:cacheprovider --basetemp (Join-Path $env:LISTTRANS_TEST_ROOT 'pytest') *> "$ev/m0_r01_pytest_full.log"
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m0_r01_isolation_after.json"
& $py -B -X utf8 "$ev/m0_r01_guard.py" --out "$ev/m0_r01_asset_guard.json" --rollback "$ev/m0_r01_rollback.patch"
```

实验启用需要精确值 `on`、本地资产路径和预期 SHA256。默认关闭，且缺失或不匹配的资产在装载时回到现行排序。独立回退补丁在仓外副本复演，恢复原 `matcher.py` 摘要并移除新增的应用、审计入口及测试文件。规则库、物料库、向量缓存、金标和发布材料由基线摘要验证。
