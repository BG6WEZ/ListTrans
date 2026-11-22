# V019-M1 / R01 复验方法

在 ListTrans 根目录执行。使用已有 Windows `venv` 和离线 BGE。仓外原生临时目录内运行应用、账号库、配置、上传和向量缓存。录制、HTTP 和实验规则回放使用现有离线护栏，本轮 Windows 回环使用沙箱外的本机 Python。

## 冻结候选和拟合

```powershell
$ev = 'doc/V0.1.9/evidence'
$py = '.\venv\Scripts\python.exe'
$env:TESTING = '1'
$env:LLM_ENABLED = 'false'
$env:AUTH_DEV_MODE = 'false'
$env:PYTHON_DOTENV_DISABLED = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m1_r01_isolation_before.json"
foreach ($mode in @('keyword','vector')) {
    & $py -B -X utf8 "$ev/m1_r01_capture.py" --mode $mode --out "$ev/m1_r01_${mode}_frozen.json.gz"
}
# 模型已存在时，将 --model 和 --out 指向新路径，再比对 SHA256。
& $py -B -X utf8 "$ev/m1_r01_fit_select.py" --model app/rerank_candidates/v019_m1_linear.json --out "$ev/m1_r01_offline_comparison.json"
```

冻结文件只包含 Train/Dev 行、候选编码、排序和数值特征。两模式逐条核对候选召回顺序摘要及分片 M0 排名。三分片记录 ID 摘要，Test 160 延续留出。

每个模式用 Train 的可回答核心样本拟合逻辑回归，其中同一查询同时有可移动的正确项与负项才参与拟合。正、负样本各占该查询训练权重的一半。标准化器和回归均在训练折拟合，按规范化查询分组做 4 折交叉验证，在 `C=0.03/0.3/3.0` 中以验证折配对全排序 MRR 选取正则强度。随机种子为 1901。模型 JSON 折叠标准化参数，运行时只需标准库计算。

首轮使用的 Windows 进程时钟粒度不足，短耗时出现零值。改用高分辨率计时器在 CPU 环境重跑，模型产物 SHA256 与首轮相同。[首轮留痕](m1_r01_offline_comparison_initial.json) · [模型复现](m1_r01_model_reproduction.json)

16 项特征覆盖业务分、旧排名、语义值及通道、型号与关键词召回、名称及规格词元重叠、M0 型号/原文/公差及冲突理由、类型化字段匹配/差异/未知和型号相等。类型化区间无有效点值时不当作等值。候选编码和标签仅用于离线对账与训练标记。

`linear-20/50` 使用模型顺序。`blend-20/50` 采用 70% 原业务分与 30% 模型值作为排序键。重排分独立于业务分和自动接受。硬阻断、零分、有不可逆封顶、原候选段之外的候选位置固定，标准编码直达整行沿用原顺序。可移动候选仅在现有 75 分判定线的同侧交换。上传仍按既有规则与批内系列逻辑处理。

Dev 按 Hit@1、MRR@10、Hit@3、全排序 MRR 顺序选型，平局优先融合及较短深度。每个模式用胜出策略同时跑 20/50 的 HTTP 对照。四套离线方案均报告 CPU 环境下的内核耗时，HTTP 组另测实际入口内包含取特征的重排耗时。旧评分以及 M0 去重、校准两组分别列在比较报告中。

## 单条与批量对照

```powershell
$plan = Get-Content -Raw -Encoding UTF8 "$ev/m1_r01_offline_comparison.json" | ConvertFrom-Json
foreach ($mode in @('keyword','vector')) {
    foreach ($arm in (@('legacy') + @($plan.http_selection.$mode.http_profiles))) {
        & $py -B -X utf8 "$ev/m1_r01_http.py" --mode $mode --profile $arm --out "$ev/m1_r01_${mode}_${arm}_http.json"
        Write-Output "$mode $arm EXIT=$LASTEXITCODE"
    }
}
& $py -B -X utf8 "$ev/m1_r01_summarize.py" --out "$ev/m1_r01_http_comparison.json"
```

各组独立进程顺序运行。单条计分样本 1,060，批量计时使用 21 次完整 50 行上传，另测 1 次尾批。HTTP P50/P95 和工作集与 V0.1.7 签认门槛对照，重排计时探针仅挂在排名回放。排名指标及决策比例给出 Wilson 区间，Dev 排名差给出 2,000 次同样本 bootstrap 区间。给排水在这两个分片没有可回答分母，继续列为留出阶段验收。

## 回归、规则门禁和回退

```powershell
$env:LISTTRANS_V018_M0_RECALL = '0'
$env:LISTTRANS_V018_M1_RECALL = '0'
$env:LISTTRANS_V018_M2_RECALL = '0'
$env:LISTTRANS_V019_M0_SCORING = 'legacy'
$env:LISTTRANS_V019_M1_RERANK = 'off'
$env:ENABLE_SERIES_RERUN = 'false'
$suite = Join-Path ([IO.Path]::GetTempPath()) ('v019_m1_suite_' + [guid]::NewGuid().ToString('N'))
$env:LISTTRANS_TEST_ROOT = $suite
& $py -B -X utf8 -m pytest tests -q -ra -p no:cacheprovider --basetemp (Join-Path $suite 'pytest') *> "$ev/m1_r01_pytest.log"
Write-Output "pytest EXIT=$LASTEXITCODE"
& $py -B -X utf8 doc/V0.1.6/evidence/m4_r01_3rule_gate.py --out "$ev/m1_r01_gate_legacy.json"
foreach ($arm in @('linear-20','linear-50','blend-20','blend-50')) {
    & $py -B -X utf8 "$ev/m1_r01_gate.py" --profile $arm --out "$ev/m1_r01_gate_$arm.json"
    Write-Output "$arm gate EXIT=$LASTEXITCODE"
}
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m1_r01_isolation_after.json"
& $py -B -X utf8 "$ev/m1_r01_asset_guard.py" --out "$ev/m1_r01_asset_guard.json"
```

回退时清除 `LISTTRANS_V019_M1_RERANK` 或设为 `off`。两模式对照逐项复现 M0 排名、决策与套装指标。源码回退脚本在仓外还原并核对已放行的 M0 matcher 摘要。模型文件缺失或无法加载时也使用原排序。

本地无离线 Cross-Encoder 权重，未执行 Cross-Encoder 分支。复跑使用新输出路径，保留既有证据。
