# V020-M3 R01 复验方法

使用已具备离线依赖和模型的 Windows venv。使用本机原生仓外临时根。应用测试使用回环，外网访问由测试护栏或性能脚本审计钩子阻断。每段结束后再开始下一段。完整回归期间避免其它测试、镜像构建及高负载任务。

## 全量回归与 OFF 三规则

在独立工作副本中使用下发基线及候选脚本。在已交付候选上复验时，先在**工作副本**对三份文档逆用补丁，恢复开工字节。新证据保留。脚本检查 HEAD、200 项 SHA256、35 项快照、72 份归档内容和 `auto_binding_rules.json`。

```powershell
git -c core.autocrlf=false apply -p2 --reverse --check --whitespace=nowarn doc/V0.2.0/evidence/m3_r01_rollback.patch
git -c core.autocrlf=false apply -p2 --reverse --whitespace=nowarn doc/V0.2.0/evidence/m3_r01_rollback.patch

$py = '.\venv\Scripts\python.exe'
$ev = 'doc/V0.2.0/evidence'
& $py -B -X utf8 "$ev/m3_r01_run.py" --out "$ev/m3_r01_audit_serial"
```

两个步段都锁定全部 10 项开关、关闭 dotenv 与开发态鉴权、设置 `TESTING=1` 并清空外部 LLM API key。全量由 `tests/conftest.py` 在应用导入前隔离运行路径。日志保留每条用例、失败及最慢耗时，摘要给出 JUnit 总时长及 `test_flywheel_end_to_end_pipeline` 单项耗时。OFF 规则门禁只读取 Train/Dev 1,060 条，复计 18 组历史评分、5 类安全、40 次约束绑定和 3 条防爆 `recommended`／`422`。回归中的主动启用分支限隔离夹具和 Mock。

## 性能、隔离 BOM 门禁与离线资产

等全量、三规则和尾部隔离结束后再运行。每段使用新目录，保留原始证据。

```powershell
& $py -B -X utf8 "$ev/m3_r01_run.py" --followup --out "$ev/m3_r01_audit_followup"
```

- 每模式独立进程。复用 V0.1.7 HTTP 路径、查询样本结构及冻结快照装载方式。预热 5 次单条和 1 份 5 行簿。按冻结清单顺序等距抽取 1,000 条 Train/Dev 行，测 20 份 50 行簿，再从同一集合等距选 200 次单条。实际执行顺序为预热、单条、上传。采用 nearest-rank P50/P95。峰值取进程全阶段工作集高水位。每份上传检查完成状态、行数、来源与推荐凭据，真实 512 维模式验证向量映射、编码及检索。出站钩子和同步／异步 LLM 传输哨兵记录尝试数。性能 JSON 给出全部样本 ID、单次耗时、预算与差异。性能退出码 `2` 表示有预算超限。抽样结果仅适用于所测环境和负载。
- BOM 复用 M1 仓外夹具，走实际 `eval`、确认凭据并拒绝篡改后的参数、加载规则、量纲换算和配置字节回退。目录、索引、规则 LIFO 及人工确认/恢复用例的完整定位见[矩阵](m3_r01_review_matrix.md)。
- 离线清单使用 SQLite 只读不可变视图，先检查 WAL/journal，再取 schema 哈希、revision 与行数。账号 schema 取隔离库。模式执行后再核验全部冻结资产。Docker 探针仅访问本机 Windows 命名管道，Linux 镜像动作须在本地守护进程和经核验的离线依赖就绪后另行复验。

对首测超限的单条路径，等待上述批次全部退出后，在新进程和新隔离根用同一组 200 条、相同顺序及预热重测。保留首测和重复结果。重复测量限定单条请求，上传及工作集预算仍以首测的完整负载为准。

```powershell
& $py -B -X utf8 "$ev/m3_r01_run.py" --repeat-single keyword vector --out "$ev/m3_r01_audit_repeat"
```

## 交付与文档回退

`m3_r01_finalize.py` 从原串行前置快照取三份文档，在仓外副本生成补丁、逆向应用并重新计算 SHA256，同时比较 200 项基线与全部隔离快照。交付日志中的本机用户目录前缀使用 `%USERPROFILE%` 表示。独立审核可用上述 `git apply` 命令复验，再对照[开工记录](m3_r01_serial/m3_r01_preflight.json)中的原始哈希。[交付哈希](m3_r01_delivery_sha256.json)以开发交付时点为准，审核依协议推进台账会改变台账哈希。
