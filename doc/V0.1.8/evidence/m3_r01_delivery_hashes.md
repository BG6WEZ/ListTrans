# V018-M3 / R01 交付文件 SHA256 清单

- 基线：Git HEAD `e964fb16848de9dece8e729e801b61c7d81dd341` + `m2_r01_coord_release.json`（166 项有效基线）
- 较 M2 有效基线未变动：164 份；本包修改既有文件：2 份（`app/matcher.py`、`app/db_manager.py`）；新增测试/评测脚本：3 份；关键证据：5 份。
- 冻结链核验：`m3_r01_candidate_freeze.json` 摘要 `bcf422fe38fa3390ef1bb94aa15a3f5edfb5be36087d666212a72298f6f004bf` 与留出报告绑定一致，冻结后代码/测试 0 变动。

| 文件 | 类别 | SHA256 |
|---|---|---|
| `app/matcher.py` | 修改 | `e5770958399ef537f4c6d0fda873285b57476b5a893ceb4712c3c873afb68dac` |
| `app/db_manager.py` | 修改 | `fa792357e25843b591ceb55c32acda62f01216f9e6d2e42c4a9df4940c9b543f` |
| `tests/test_v018_m3_selection_degradation.py` | 新增 | `14d7f652c628a8ebc09eede7f016b70eb66966e86c29d2af5d2fbf7a90aa4a69` |
| `doc/V0.1.8/evidence/m3_r01_dev_selection.py` | 新增 | `9dc499616be71a7f2a47371b39a9f9b0945174bc82f77dd5eae180fcbecef6fa` |
| `doc/V0.1.8/evidence/m3_r01_holdout_eval.py` | 新增 | `1cddcdd5c71774f688afcbb7b24cf1be743f8bde6cb2df453088b44b55dc2587` |
| `doc/V0.1.8/evidence/m3_r01_dev_selection_summary.json` | 证据 | `36644ef65e2eb5cfd6096111dcb0851113509fd69f2dcd8b44e4688f345fd57b` |
| `doc/V0.1.8/evidence/m3_r01_candidate_freeze.json` | 证据 | `bcf422fe38fa3390ef1bb94aa15a3f5edfb5be36087d666212a72298f6f004bf` |
| `doc/V0.1.8/evidence/m3_r01_holdout_report.json` | 证据 | `911487dc0473206f8256422f229f4f2d615ed9a05c64a1fbb342faf14a53536e` |
| `doc/V0.1.8/evidence/m3_r01_gate_final.json` | 证据 | `4b036be0ef7803ca96498630d4d1e73e9cf8f5fe96aa4e3908ec362ee8aa248a` |
| `doc/V0.1.8/evidence/m3_r01_pytest_frozen.log` | 证据 | `1902ee7e2d527093c0608557618b975128c16c4a78c0f9ba0099eec5612fe91f` |

## 辅助隔离与初跑日志摘要

- `doc/V0.1.8/evidence/m3_r01_isolation_before.json`: `4ca08463961bb051d1b28977dc417fc54fe905d446694ba7536246b956d82ce0`
- `doc/V0.1.8/evidence/m3_r01_isolation_after.json`: `0dc7c0ed998c50cb33cec4c67befc944e91772b101dc398d83eb2190f2d723cd`
- `doc/V0.1.8/evidence/m3_r01_asset_guard.json`: `94e7a589b728069b2aff12c9808515c05d371028b72e95f12ff6cdca3a255e89`
- `doc/V0.1.8/evidence/m3_r01_pytest_preliminary.log`: `b94773ceb2ae62acc9df6f62b868e5cfd973b915a030b0cd1c9983010557f9f1`

## 复现命令（仓外隔离根，`TESTING=1`、`LLM_ENABLED=false`）

```powershell
# 1. 前/后隔离核验
.\venv\Scripts\python.exe -B -X utf8 tests/verify_isolation.py --out doc/V0.1.8/evidence/m3_r01_isolation_before.json
# 2. Dev 阶段 10 组同条件选型与降级复测（严格排除 Test 160）
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.8/evidence/m3_r01_dev_selection.py --out doc/V0.1.8/evidence/m3_r01_dev_selection_summary.json
# 3. 冻结后一次性 Test 160 / 全量 1,220 留出验收（校验 m3_r01_candidate_freeze.json 冻结链）
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.8/evidence/m3_r01_holdout_eval.py --freeze doc/V0.1.8/evidence/m3_r01_candidate_freeze.json --out doc/V0.1.8/evidence/m3_r01_holdout_report.json
# 4. 三规则治理门禁（6 基线 x 3 规则 = 18 组 + GT-0674/0677/0678）
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.6/evidence/m4_r01_3rule_gate.py --out doc/V0.1.8/evidence/m3_r01_gate_final.json
# 5. 全量 pytest（仓外独立临时根，434 passed）
$tmp = Join-Path $env:TEMP ('v018_m3_' + [guid]::NewGuid().ToString('N').Substring(0,8)); New-Item -ItemType Directory -Force -Path $tmp | Out-Null; $env:LISTTRANS_TEST_ROOT=$tmp; $env:TESTING='1'; $env:LLM_ENABLED='false'; $env:AUTH_DEV_MODE='false'; $env:PYTHON_DOTENV_DISABLED='1'; $env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'; .\venv\Scripts\python.exe -B -X utf8 -m pytest tests -q -ra -p no:cacheprovider --basetemp (Join-Path $tmp 'pytest')
```
