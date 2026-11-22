# V019-M0 / R01 复验和回退

在 ListTrans 根目录执行。实测使用本机 Windows 及仓库已有的 `venv`（Python 3.12.10）。评测复制物料库、配置和向量缓存到新的仓外临时目录，核对 SHA256，使用现有离线 BGE。评测进程禁止非回环网络与付费 LLM，逐组顺序运行。签认性能取生产接口路径的 P50/P95 和进程峰值工作集。

当前 Windows 工具沙箱内的最小 `asyncio.run(asyncio.sleep(0))` 自检及 TestClient 初始化停滞。实际 HTTP 回放在本机沙箱外执行，沿用仓外路径隔离和断网护栏。此处的 HTTP 计时来自实际 FastAPI 路由的 TestClient 请求，外部网络传输不计入预算。

## 开工及收工对账

```powershell
$ev = 'doc/V0.1.9/evidence'
$py = '.\venv\Scripts\python.exe'
$env:TESTING = '1'
$env:LLM_ENABLED = 'false'
$env:AUTH_DEV_MODE = 'false'
$env:PYTHON_DOTENV_DISABLED = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m0_r01_isolation_before.json"
& $py -B -X utf8 "$ev/m0_r01_verify_assets.py" --out "$ev/m0_r01_archive_precheck.json"
# 在回归和评测全部结束后执行
& $py -B -X utf8 tests/verify_isolation.py --out "$ev/m0_r01_isolation_post_rerun.json"
& $py -B -X utf8 "$ev/m0_r01_verify_assets.py" --out "$ev/m0_r01_asset_guard_final.json"
```

复跑时选用新输出文件名，保留已有证据。恢复验证在临时目录解包 88 份文件，验证逐份 SHA256，并编译检查本轮触及的两份回退源码。V0.1.9 关闭评分实验即可使用 V0.1.8 分值与排序。旧版源码恢复按开工清单中的归档执行。[开工清单](m0_r01_dispatch_baseline.json)

## Train/Dev 配对回放

```powershell
$env:PYTHONUNBUFFERED = '1'
foreach ($mode in @('keyword','vector')) {
    foreach ($profile in @('legacy','calibrated','dedup')) {
        $extra = @()
        if ($profile -eq 'dedup') { $extra = @('--rank-only') }
        & $py -B -X utf8 "$ev/m0_r01_replay.py" --mode $mode --profile $profile @extra --out "$ev/m0_r01_${mode}_${profile}_final.json"
        Write-Output "$mode $profile EXIT=$LASTEXITCODE"
    }
}
foreach ($mode in @('keyword','vector')) {
    & $py -B -X utf8 "$ev/m0_r01_v017_reference.py" --mode $mode --out "$ev/m0_r01_v017_$mode.json"
    Write-Output "V017 $mode EXIT=$LASTEXITCODE"
}
& $py -B -X utf8 "$ev/m0_r01_compare.py" --out "$ev/m0_r01_comparison.json"
foreach ($mode in @('keyword','vector')) {
    foreach ($version in @('v017','v018')) {
        & $py -B -X utf8 "$ev/m0_r01_focus.py" --version $version --mode $mode --out "$ev/m0_r01_focus_${mode}_${version}.json"
    }
}
```

`legacy` 为 V0.1.8 全 OFF 主对照。两组候选使用同一属性去重和高分压缩。`dedup` 保留场景权重，`calibrated` 按专业调整型号、参数和名称权重。消融组仅重放排名。主对照和校准组各执行 1,060 条排名、1,060 次单条请求及 22 次上传，其中 21 个完整 50 行批次计入预算。V0.1.7 回放使用核过 77 项 SHA256 的旧版源码、同一数据与索引，执行排名和上传以定位前版差异。

物理参数的同义字段只占一个权重。已计入型号的相同字段、完整组成已计分的复合线缆列、已进入区间评分的派生尺寸以及已计入品牌的同义字段，均不再额外累计。型号精确、名称或分类全等、参数全等与原文全等继续作为解释证据。未解析但完全相同的规格可用规格文本贡献替换原规格相似度。条件式分类惩罚、独立品牌信号及各域硬约束保留。

| 查询专业 | 型号倍率 | 参数倍率 | 名称与分类倍率 | 权重依据 |
|---|---:|---:|---:|---|
| 强电、暖通 | 0.80 | 1.25 | 1.00 | 同型号下突出性能或线缆规格 |
| 弱电 | 0.90 | 1.20 | 1.00 | 保持较强型号证据并提高物理字段权重 |
| 装饰 | 0.70 | 1.10 | 1.25 | 突出材料类别与制式，保留尺寸区分 |
| 医用气体、给排水、纯水 | 0.85 | 1.20 | 1.00 | 提高口径、连接形式与材质的权重 |

倍率按场景原权重总量归一。两组实验的 90 分以上原始分单调压缩，实际并列沿用既有的规格和尺寸排序键。上限与阻断仍优先于分数换算、动态加分和同批系列加分。

比较脚本对 Recall、Hit 和绑定比例使用 Wilson 95% 区间，对 Hit 和 MRR 的同样本差值使用 2,000 次配对 bootstrap。同一项目的样本可能相关，区间用于描述本批回放的不确定性。Test 160 留出，三个 V0.1.8 召回开关在回放中显式设为 OFF。

## 回归和规则门禁

首次全量回归发现 `score_candidate` 未沿用明细接口包装。恢复包装后，复验该历史用例及 M0 专项共 14 项通过。首次旧评分门禁在修复写入期间检测到运行指纹变化并拦截，之后使用同一份最终源码重跑全量与旧评分门禁。初跑证据保留。[全量初跑](m0_r01_pytest_initial.log) · [旧评分门禁初跑](m0_r01_gate_legacy_initial.log)

六组性能和排名回放先于包装修复。资产核对用 SHA256 验证基准计时源码和交付源码只相差这一个包装函数，并扫描应用中对包装函数的引用。`/api/match`、`/api/upload` 与金标排名回放使用的匹配入口均未引用该包装。复验报告保留计时源码与交付源码的各自摘要。

校准组门禁和 V0.1.7 关键词对照的首次评测已落盘，进程在 Windows 清理临时 SQLite 文件时退出 1。调整临时目录回收后，两项重跑均退出 0。V0.1.7 关键词重跑除耗时外逐项相同。[首次校准门禁日志](m0_r01_gate_calibrated_initial.log) · [V0.1.7 关键词重跑](m0_r01_v017_keyword_rerun.json)

```powershell
$env:LISTTRANS_V018_M0_RECALL = '0'
$env:LISTTRANS_V018_M1_RECALL = '0'
$env:LISTTRANS_V018_M2_RECALL = '0'
$env:LISTTRANS_V019_M0_SCORING = 'legacy'
$env:ENABLE_SERIES_RERUN = 'false'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('v019_m0_suite_' + [guid]::NewGuid().ToString('N'))
$env:LISTTRANS_TEST_ROOT = $testRoot
& $py -B -X utf8 -m pytest tests -q -ra -p no:cacheprovider --basetemp (Join-Path $testRoot 'pytest') *> "$ev/m0_r01_pytest_final.log"
Write-Output "pytest EXIT=$LASTEXITCODE"
& $py -B -X utf8 doc/V0.1.6/evidence/m4_r01_3rule_gate.py --out "$ev/m0_r01_gate_legacy.json"
Write-Output "legacy gate EXIT=$LASTEXITCODE"
& $py -B -X utf8 "$ev/m0_r01_candidate_gate.py" --profile calibrated --out "$ev/m0_r01_gate_calibrated.json"
Write-Output "calibrated gate EXIT=$LASTEXITCODE"
```

恢复默认评分可清除 `LISTTRANS_V019_M0_SCORING`，或设为 `legacy`。`dedup` 和 `calibrated` 只在显式选择时生效。所有分支继续使用原确认和导出契约。精确标准编码命中的行沿用旧版排序，目录 revision 和等价编码口径继续使用冻结资产。
