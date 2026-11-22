# V017-M3 R01 复验命令

在仓库根运行。Windows 本机回环用于 TestClient，测试和评测脚本保持业务路径隔离、真实 LLM 关闭及外连阻断。全量 pytest 重复运行两次，每次使用不同的仓外 Windows 原生临时根。全部 10 个应用路径在应用导入前由 `tests/conftest.py` 或独立评测脚本注入，`TESTING=1`，`LLM_ENABLED=false`。

```powershell
$tag = [guid]::NewGuid().ToString('N').Substring(0, 8)
$env:LISTTRANS_TEST_ROOT = Join-Path $env:TEMP "lt017m3-tests-$tag"
$pytestRoot = Join-Path $env:TEMP "lt017m3-pytest-$tag"
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:PYTHON_DOTENV_DISABLED = '1'
.\venv\Scripts\python.exe -B -X utf8 tests/verify_isolation.py --out (Join-Path $env:TEMP "lt017m3-isolation-$tag.json")
.\venv\Scripts\python.exe -B -X utf8 -m pytest -p no:cacheprovider -vv --tb=short -o faulthandler_timeout=120 --basetemp $pytestRoot
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.6/evidence/m4_r01_3rule_gate.py --out (Join-Path $env:TEMP "lt017m3-gate-$tag.json")
.\venv\Scripts\python.exe -B -m pip check
```

等待 pytest 和三规则子进程退出后，单独运行性能采集。负载探针显式发起 15 个批量匹配请求、100 行 Excel 上传、160 行有权导出和 2,500 条合成事件挖掘。健康与任务状态探针采用本轮采集阈值 2,000 ms，队列指标从工作线程实际起点计时。正式预算由统筹在终审核定。

```powershell
$tag = [guid]::NewGuid().ToString('N').Substring(0, 8)
$replay = Join-Path $env:TEMP "lt017m3-replay-$tag"
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.7/evidence/m3_r01_load.py --root "$replay-load" --out "$replay-load.json"
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.7/evidence/m0_r01_benchmark.py --mode keyword --root "$replay-keyword" --out "$replay-keyword.json"
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.7/evidence/m0_r01_benchmark.py --mode vector --root "$replay-vector" --out "$replay-vector.json"
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.7/evidence/m0_r01_summarize.py --keyword "$replay-keyword.json" --vector "$replay-vector.json" --out "$replay-metrics.json"
```

初次负载探针的合成导出凭据缺少标准化后的 `qty_ratio`，导出如预期返回 `422`。为该合成绑定加入比例后，160 行真实导出通过。导出验证代码和阈值保持原值。[负载脚本](m3_r01_load.py)

首轮全量权限测试的旧替身影响了共享线程类，结果为每根 410 通过、2 失败。将替身限定在沙箱执行函数，并按是否实际派发等待替身调用后，两个新根最终各 413 通过。正式负载与双模式回放独立于这些全量测试运行。[首轮日志](m3_r01_pytest_preliminary.log) · [根 A](m3_r01_pytest_frozen_a.log) · [根 B](m3_r01_pytest_frozen_b.log) · [性能日志](m3_r01_performance.log)
