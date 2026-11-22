# V020-M1 R01 复验

从 ListTrans 根目录使用原生 Windows Python venv。应用回环需要原生运行权限。仓外隔离根由测试创建，依赖和向量模型使用本机资产。顺序运行专项、规则门禁、全量和收工检查，等上一步退出后再开启下一步。

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
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m1_r01_isolation_before.json"

$target = @(
  'tests/test_v020_m1_bom.py',
  'tests/test_v020_m0_feedback_ranker.py',
  'tests/test_binding_decisions.py',
  'tests/test_auto_binding.py',
  'tests/test_m3_hard_constraints.py',
  'tests/test_m3_hard_constraints_http.py',
  'tests/test_v019_m1_rerank.py',
  'tests/test_v019_m2_calibration.py',
  'tests/test_v019_m3_review.py',
  'tests/test_v017_m2_catalog_revision.py',
  'tests/test_v016_m2_publish_gate.py'
)
$env:LISTTRANS_TEST_ROOT = Join-Path ([IO.Path]::GetTempPath()) ('v020_m1_targeted_' + [guid]::NewGuid().ToString('N'))
& $py -B -X utf8 -m pytest @target -vv -ra -p no:cacheprovider --basetemp (Join-Path $env:LISTTRANS_TEST_ROOT 'pytest') *> "$ev/m1_r01_pytest_targeted.log"
& $py -B -X utf8 "$ev/m0_r01_replay.py" --mode rules --arm off --out "$ev/m1_r01_rule_gate.json" *> "$ev/m1_r01_rule_gate.log"

$env:LISTTRANS_TEST_ROOT = Join-Path ([IO.Path]::GetTempPath()) ('v020_m1_full_' + [guid]::NewGuid().ToString('N'))
& $py -B -X utf8 -m pytest tests -vv -ra -p no:cacheprovider --basetemp (Join-Path $env:LISTTRANS_TEST_ROOT 'pytest') *> "$ev/m1_r01_pytest_full.log"
& $py -B -X utf8 "$ev/m1_r01_governed_replay.py" --out "$ev/m1_r01_governed_replay.json" *> "$ev/m1_r01_governed_replay.log"
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m1_r01_isolation_after.json"
& $py -B -X utf8 "$ev/m1_r01_guard.py" --out "$ev/m1_r01_asset_guard.json" --rollback "$ev/m1_r01_rollback.patch"
```

提交证据原件应保留。重复验证时用新的输出路径，或在独立工作副本按默认路径运行。规则门禁复用已放行的 OFF 回放器，仅含 Train/Dev 投影。目录只读核验用 `app/materials.db` 的 SQLite `mode=ro&immutable=1`，按[目录证据](m1_r01_catalog_evidence.json)的四个编码读出名称、品类、规格和单位，并对整库文件重算 SHA256。

本轮下发 JSON 的资产快照字段为空。资产守卫使用已放行且摘要可追溯的 M0 收工快照，与 M1 开工、收工和当前工作区快照逐项比较。[下发基线](m1_r01_dispatch_baseline.json) · [M0 收工快照](m0_r01_isolation_after.json)
