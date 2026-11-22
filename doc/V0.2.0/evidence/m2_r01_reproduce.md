# V020-M2 R01 复验

从 ListTrans 根目录使用已有 Windows venv，按顺序执行。HTTP 测试需要原生本机回环权限。测试和规则回放在仓外临时根隔离业务库、账号库、规则库、配置、向量缓存、上传和输出。只读使用已有本地依赖与模型资产。全量单进程，等专项和规则门禁退出后再启动，避免同时执行其它测试或高负载任务。

以下为交付文件名。在独立工作副本执行，保留原交付证据。守卫读取同目录下刚生成的隔离和规则报告，输出文件须为新路径。每一步检查退出码，非零时先保留日志并排查。

```powershell
$py = '.\venv\Scripts\python.exe'
$ev = 'doc/V0.2.0/evidence'
$env:TESTING = '1'
$env:LLM_ENABLED = 'false'
$env:AUTH_DEV_MODE = 'false'
$env:PYTHON_DOTENV_DISABLED = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:DASHSCOPE_API_KEY = ''
$env:DEEPSEEK_API_KEY = ''
$env:LISTTRANS_V018_M0_RECALL = '0'
$env:LISTTRANS_V018_M1_RECALL = '0'
$env:LISTTRANS_V018_M2_RECALL = '0'
$env:LISTTRANS_V019_M0_SCORING = 'legacy'
$env:LISTTRANS_V019_M1_RERANK = 'off'
$env:LISTTRANS_V019_M2_CALIBRATION = 'off'
$env:ENABLE_SERIES_RERUN = 'false'
$env:LISTTRANS_V020_M0_RANKER = 'off'
$env:LISTTRANS_V020_M2_LLM_DECISION = 'off'
$env:PYTHONUNBUFFERED = '1'
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m2_r01_isolation_before.json"

$target = @(
  'tests/test_v020_m2_llm_decision.py',
  'tests/test_v020_m1_bom.py',
  'tests/test_v020_m0_feedback_ranker.py',
  'tests/test_binding_decisions.py',
  'tests/test_m3_hard_constraints.py',
  'tests/test_m3_hard_constraints_http.py',
  'tests/test_v019_m2_calibration.py',
  'tests/test_v017_m2_catalog_revision.py'
)
$env:LISTTRANS_TEST_ROOT = Join-Path ([IO.Path]::GetTempPath()) ('v020_m2_targeted_' + [guid]::NewGuid().ToString('N'))
& $py -B -X utf8 -m pytest @target -vv -ra -p no:cacheprovider --basetemp (Join-Path $env:LISTTRANS_TEST_ROOT 'pytest') *> "$ev/m2_r01_pytest_targeted.log"
& $py -B -X utf8 "$ev/m0_r01_replay.py" --mode rules --arm off --out "$ev/m2_r01_rule_gate.json" *> "$ev/m2_r01_rule_gate.log"

$env:LISTTRANS_TEST_ROOT = Join-Path ([IO.Path]::GetTempPath()) ('v020_m2_full_' + [guid]::NewGuid().ToString('N'))
& $py -B -X utf8 -m pytest tests -vv -ra -p no:cacheprovider --basetemp (Join-Path $env:LISTTRANS_TEST_ROOT 'pytest') *> "$ev/m2_r01_pytest_full.log"
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m2_r01_isolation_after.json"
& $py -B -X utf8 "$ev/m2_r01_guard.py" --out "$ev/m2_r01_asset_guard.json" --rollback "$ev/m2_r01_rollback.patch"
```

M2 专项只在本进程内打开 Mock 分支，另设哨兵阻断旧客户端的实际传输。三规则门禁沿用已经放行的 OFF 回放器，读取 1,060 条 Train/Dev 投影。三条防爆保护样本同时在该门禁与启用 Mock 的专项中验证。Test 160 未用于调参。

资产守卫以 M2 下发完整快照与前后隔离输出核对 35 项资产。下发 JSON 的计数字段为 18，按同一 JSON 的 `business_snapshot_sha256` 展开实为 35 项，逐项比对以完整快照为准，保留原计数字段供核对。恢复时先从 72 项受控归档提取 `app/server.py`，逆用已放行的 M1 回退补丁恢复至 M2 开工字节。确认其 SHA256 与 M2 下发吻合后，在仓外副本生成并实际应用 M2 回退补丁，逐字节校验 `app/server.py`、检查两份新增源码和测试文件已移除。其余 195 项基线独立复算 SHA256。
