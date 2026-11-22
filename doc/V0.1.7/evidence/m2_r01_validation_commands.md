# V017-M2 R01 复验方法和过程记录

## 环境

使用仓库 `venv` 的 Windows Python 3.12。每次 pytest 使用新的 `LISTTRANS_TEST_ROOT` 和仓外 Windows 原生 `--basetemp`。`tests/conftest.py` 在业务模块导入前配置物料和规则库、账号库、配置、向量缓存、上传、输出、日志和反馈路径，并启用外部网络及真实 LLM 阻断。三规则脚本和 M0 评测脚本各自为全部 10 个路径创建独立根，在导入前设置 `LLM_ENABLED=false`、`TESTING=1`，向量模型只从本地读取。

默认 Windows 沙箱的最小 `socket.socketpair()` 探针停在套接字创建，TestClient 全量初试随之停在首个 HTTP 检查。默认沙箱还出现过 FAISS 原生加载异常 `0xc0000008`。自动审批允许本机回环复验，pytest 的业务路径、真实 LLM 和外连护栏继续启用。两个最终全量日志使用不同临时根。

## 命令

下列 PowerShell 命令适用于两个独立的 pytest 运行，每次重新生成 `$tag`。三规则和隔离核验的 `--out` 应指向新的证据文件。

```powershell
$tag = [guid]::NewGuid().ToString('N').Substring(0, 8)
$env:LISTTRANS_TEST_ROOT = Join-Path $env:TEMP "lt017m2-test-$tag"
$pytestRoot = Join-Path $env:TEMP "lt017m2-pytest-$tag"
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:PYTHON_DOTENV_DISABLED = '1'
.\venv\Scripts\python.exe -B -X utf8 -m pytest -p no:cacheprovider -vv --tb=short -o faulthandler_timeout=120 --basetemp $pytestRoot
.\venv\Scripts\python.exe -B -X utf8 tests/verify_isolation.py --out (Join-Path $env:TEMP "lt017m2-isolation-$tag.json")
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.6/evidence/m4_r01_3rule_gate.py --out (Join-Path $env:TEMP "lt017m2-gate-$tag.json")
```

双模式回放沿用 M0 的冻结数据、金标分类、统计器和真实 HTTP 路径。输出聚合指标，用放行裁决规定的 `with_equivalence` 口径比较 9 个专业。`test` 划分用于复验，参数和标签没有调整。

```powershell
$tag = [guid]::NewGuid().ToString('N').Substring(0, 8)
$replay = Join-Path $env:TEMP "lt017m2-quality-$tag"
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.7/evidence/m0_r01_benchmark.py --mode keyword --root "$replay-keyword" --out "$replay-keyword.json"
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.7/evidence/m0_r01_benchmark.py --mode vector --root "$replay-vector" --out "$replay-vector.json"
.\venv\Scripts\python.exe -B -X utf8 doc/V0.1.7/evidence/m0_r01_summarize.py --keyword "$replay-keyword.json" --vector "$replay-vector.json" --out "$replay-metrics.json"
```

## 过程修正

- M2 初次专项 8 通过、1 失败。同一工作簿内的既有去重按完整度选行，测试改为三次独立导入同码规格，专项复跑 9 通过。随后加入 SQLite 提交阶段故障用例。
- 首轮 M5 恢复专项发现 `save_cache()` 先报版本不匹配，掩盖了已有的持久恢复写阻断。已优先判定恢复写阻断。
- 全量初试发现目录导入建立真实向量后，直接调用 `evaluate()` 的旧别名观测用例会被语义召回补足。该用例显式指定原先实际使用的关键词通道，保留四项实体断言及真实双子进程门禁，测试结束恢复原隔离配置。[两项失败记录](m2_r01_pytest_preliminary.log)
- 最终全量在两个独立根各通过 405 项，均 `EXIT=0`。[根 A](m2_r01_pytest_verified_a.log) · [根 B](m2_r01_pytest_verified_b.log)
- 6 个冻结基线各跑 3 项规则，18 组分数差为 0。`GT-0674`、`GT-0677`、`GT-0678` 均为 `recommended`。[最终三规则门禁](m2_r01_gate_verified.json)
