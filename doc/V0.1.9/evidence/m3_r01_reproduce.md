# V019-M3 / R01 复验方法

在 ListTrans 根目录使用现有 Windows Python venv、Node 和 Chrome。应用服务、账号、测试物料库、Chrome 配置、会话、上传、下载使用仓外原生临时目录。浏览器仅连接本机回环，外部 URL 由 CDP 和名称解析设置拦截。服务端回放另设 Python 审计钩子拦截外部网络。

```powershell
$py = '.\venv\Scripts\python.exe'
$ev = 'doc/V0.1.9/evidence'
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
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m3_r01_isolation_before.json"
```

## Chrome、规则和 F23

```powershell
& $py -B -X utf8 "$ev/m3_r01_browser.py" --baseline .local-archive/v019_m3_r01_start --out "$ev/m3_r01_browser.json"
& $py -B -X utf8 "$ev/m3_r01_cancel.py" --out "$ev/m3_r01_cancel.json"
& $py -B -X utf8 "$ev/m3_r01_quality.py" --kind m2-on --mode vector --out "$ev/m3_r01_m2_on.json"
$load = Join-Path ([IO.Path]::GetTempPath()) ('v019_m3_load_' + [guid]::NewGuid().ToString('N'))
& $py -B -X utf8 doc/V0.1.7/evidence/m3_r01_load.py --root $load --out "$ev/m3_r01_load.json"
foreach ($mode in @('off', 'on')) {
    & $py -B -X utf8 "$ev/m3_r01_rule_gate.py" --mode $mode --out "$ev/m3_r01_gate_${mode}.json"
}
$suite = Join-Path ([IO.Path]::GetTempPath()) ('v019_m3_suite_' + [guid]::NewGuid().ToString('N'))
$env:LISTTRANS_TEST_ROOT = $suite
$env:PYTHONUNBUFFERED = '1'
& $py -B -X utf8 -m pytest tests -vv -ra -p no:cacheprovider --basetemp (Join-Path $suite 'pytest') *> "$ev/m3_r01_pytest.log"
```

浏览器对照用同一个隔离后端、同一制表夹具、同一操作者脚本、同一视口。基线静态目录从 M2 放行字节复原，运行前逐文件核对 8 个 SHA256。三次配对交错顺序，各复核两个独立源行，计时从首个复核动作到实际 XLSX 下载。每个配对内按唯一业务行计修改率。耗时为真实 DOM 操作回放，包括服务端请求和下载，可以复现操作流程的成本。延迟数据不包含真人阅读与判断停顿。

取消用实网传输的本机 Uvicorn HTTP，发出上传后从取消路由请求协作停止。超时探针仅对单个测试任务设定 35 ms worker 测试期限，依旧运行应用原有检查点、状态更新及资源释放。两例核对无结果、零在途、文件租用和实际上传无残留，随后真实上传成功恢复。

## 一次性留出评估

所有功能修改完成、交互和规则验证通过后，执行冻结。默认关键词和 BGE 分别只运行一次。Test 160 从各次完整 1,220 条的实测中切片；每模式用单独的独占 claim 文件约束执行次数。完成后用同一 SHA256 校验实现、依赖和数据，终验后只写证据与小结。

```powershell
& $py -B -X utf8 "$ev/m3_r01_guard.py" --prepare-rollback .local-archive/v019_m3_r01_start
& $py -B -X utf8 "$ev/m3_r01_guard.py" --freeze
foreach ($mode in @('keyword', 'vector')) {
    & $py -B -X utf8 "$ev/m3_r01_quality.py" --kind final --mode $mode --out "$ev/m3_r01_final_${mode}.json"
}
& $py -B -X utf8 "$ev/m3_r01_summarize.py" --out "$ev/m3_r01_comparison.json"
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m3_r01_isolation_after.json"
& $py -B -X utf8 "$ev/m3_r01_guard.py" --after "$ev/m3_r01_asset_guard.json"
```

已生成的终验 claim 与结果保留。独立核对基线时可在仓外副本对六份源码应用回退补丁，再按放行 SHA256 校验。
