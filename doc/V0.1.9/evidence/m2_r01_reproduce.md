# V019-M2 / R01 复验方法

在 ListTrans 根目录使用现有 Windows venv 和离线 BGE。应用写入走仓外原生临时目录，各组独立进程。回放使用真实 Bearer 鉴权、应用路由和本机回环；现有审计钩子拦截外部网络。计时组顺序运行。

## 隔离与拟合

```powershell
$ev = 'doc/V0.1.9/evidence'
$py = '.\venv\Scripts\python.exe'
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
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m2_r01_isolation_before.json"
# 首次生成。已交付的等价编码表可直接用于复跑。
& $py -B -X utf8 "$ev/m2_r01_capture.py" --write-equivalences
foreach ($mode in @('keyword','vector')) {
    & $py -B -X utf8 "$ev/m2_r01_capture.py" --mode $mode --out "$ev/m2_r01_${mode}_capture.json"
}
# 复跑拟合时为模型与结果指定新的输出路径。
& $py -B -X utf8 "$ev/m2_r01_fit.py" --model app/calibration_assets/v019_m2_policy.json --out "$ev/m2_r01_fit_report.json"
```

每模式捕获 1,060 条上传行的无标签特征，含 Train 842 和 Dev 218。回归仅纳入每模式 Train 的 606 条可判定核心行，其中 529 条可回答、77 条负例。Dev 每模式同口径 160 条，其中 122 条可回答、38 条负例。质检排除、套装及待判定标签保留在真实 HTTP 行级统计。Test 160 仅核对分片 ID 摘要；模型、阈值及 M2 路由回放均使用 Train/Dev。

拟合用标准化逻辑回归，固定 `C=0.3`、随机种子 1902。两模式共享系数。Train 评估弱证据拒绝阈值并排除会新增拒绝正确 Top1 的候选。Dev 优先比较负例显式拒绝数，平局保留 Train 可安全拒绝的弱证据行，再取较低阈值。最终选 40 分，覆盖两条 30 分且有关键字段冲突的 Train 行。零误绑的非空接受候选按 Dev 覆盖率排序，然后比较两模式较小的接受数、区分间隔和置信度阈值。当前没有通过联合门槛的非空自动接受档，模型保留 `threshold=null`。全扫描、固定间隔 Dev 曲线和 Wilson 区间落盘。自动接受分母为零时精确率和对应区间均为 null。[拟合复现](m2_r01_model_reproduction.json)

## 真实 HTTP 配对与规则门禁

```powershell
foreach ($mode in @('keyword','vector')) {
    foreach ($arm in @('off','on')) {
        $stem = "$ev/m2_r01_${mode}_${arm}_http"
        & $py -B -X utf8 "$ev/m2_r01_http.py" --mode $mode --arm $arm --out "$stem.json" *> "$stem.log"
        Write-Output "$mode $arm EXIT=$LASTEXITCODE"
    }
}
& $py -B -X utf8 doc/V0.1.6/evidence/m4_r01_3rule_gate.py --out "$ev/m2_r01_gate_off.json"
& $py -B -X utf8 "$ev/m2_r01_gate.py" --out "$ev/m2_r01_gate_on.json"
& $py -B -X utf8 "$ev/m2_r01_compare.py" --out "$ev/m2_r01_comparison.json"
```

计时覆盖 1,060 次单条和 21 份完整 50 行上传。另执行一次尾批和三个防爆 unknown 的直接导出、客户端状态声明、草稿恢复、人工确认与拒绝。导出和草稿探针在计时后运行。评分、召回、重排和批内系列开关与 M1 默认档对齐。关键词 OFF 在安装最终阈值前完成，使用相同服务端源码且分支未读取策略文件。ON 上传逐行核对实际模型特征等于拟合捕获的特征。OFF 门禁沿用历史回归入口以核对 18 组冻结分数。ON 规则门禁只接收 Train/Dev 子集。固定切片可回答 651 条和负例 115 条用于错误预绑定指标。

## 全量回归与退回 M1

```powershell
$suite = Join-Path ([IO.Path]::GetTempPath()) ('v019_m2_suite_' + [guid]::NewGuid().ToString('N'))
$env:LISTTRANS_TEST_ROOT = $suite
& $py -B -X utf8 -m pytest tests -q -ra -p no:cacheprovider --basetemp (Join-Path $suite 'pytest') *> "$ev/m2_r01_pytest.log"
Write-Output "pytest EXIT=$LASTEXITCODE"
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m2_r01_isolation_after.json"
& $py -B -X utf8 "$ev/m2_r01_asset_guard.py" --out "$ev/m2_r01_asset_guard.json"
```

运行时回退为清除 `LISTTRANS_V019_M2_CALIBRATION` 或设为 `off`。源码回退由脚本在仓外副本应用反向补丁，按 M1 放行单校验 `matcher.py`、`server.py`、`sandbox_evaluator.py` 和 `flywheel_sandbox.py` 的 SHA256。所有复跑使用新输出路径。
