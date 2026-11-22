"""Apply only coordinator status transitions; preserve historical rounds."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
updates = {
'doc/README.md': [
('当前（2026-11-17）：', '当前（2026-11-18）：**V016-M0/M1已整包正式放行，F06/F27本包范围关闭；唯一下发 V016-M2 / R01（统一规则发布与强制门禁），待开发**，见[M1裁决](V0.1.6/rounds/V016-M1_R01_统筹裁决.md)、[M2正式任务](V0.1.6/rounds/V016-M2_R01_统筹下发.md)及[任务台账](V0.1.6/02_任务台账.md)。V015终审保持；V0.1.6未终审，M3/M4未下发，V0.1.7～V0.2.0继续锁定，PRG-F11开放，未批准生产发布。')],
'doc/00_项目管理/版本路线图.md': [
('当前执行状态（2026-11-17）：', '当前执行状态（2026-11-18）：V015已批准实现范围终审保持；**M0/M1整包放行，F06/F27本包关闭，唯一下发[V016-M2 / R01](../V0.1.6/rounds/V016-M2_R01_统筹下发.md)**。见[M1裁决](../V0.1.6/rounds/V016-M1_R01_统筹裁决.md)。V0.1.6未终审，M3/M4未下发，V0.1.7～V0.2.0继续锁定。PRG-F11首次真实部署前约束保持，未批准发布。上表为初始状态，实时见[进度总表](进度总表.md)。')],
'doc/V0.1.6/01_实施方案与任务包.md': [
('当前状态（2026-11-17）：', '当前状态（2026-11-18）：**M0/M1整包正式放行，F06/F27本包范围关闭；唯一下发 V016-M2 / R01，待开发**。见[M1裁决](rounds/V016-M1_R01_统筹裁决.md)和[M2正式任务](rounds/V016-M2_R01_统筹下发.md)。M3/M4未下发，V0.1.6未终审，V0.1.7～V0.2.0继续锁定，PRG-F11首次真实部署前强制门禁保持开放。'),
('- 当前授权：2026-11-17', '- 当前结论：2026-11-18接收[R01独立审核](rounds/V016-M1_R01_审核小结.md)，[统筹整包放行](rounds/V016-M1_R01_统筹裁决.md)，F06/F27本包范围关闭；M1-C01～C06及M0保护持续回归。AUDIT-SIDE-01已签认处置，原始JSON不可恢复事实保留。')],
'doc/V0.1.6/02_任务台账.md': [
('2026-11-17 当前统筹裁决：', '2026-11-18 当前统筹裁决：**M1 R01整包正式放行，F06/F27本包范围关闭；唯一下发 V016-M2 / R01，待开发**。见[M1裁决](rounds/V016-M1_R01_统筹裁决.md)和[M2正式任务](rounds/V016-M2_R01_统筹下发.md)。M0及既有关闭项保持；AUDIT-SIDE-01流程处置签认关闭，原始JSON不可恢复事实永久留痕。F07/F28完整治理与F08仍开放；M3/M4未下发，V0.1.6未终审，V0.1.7～V0.2.0继续锁定，PRG-F11开放。'),
('| V016-M1 |', '| V016-M1 | 挖掘SQL与规则上下文 | R01审核通过，统筹整包放行 | [R01开发小结](rounds/V016-M1_R01_开发小结.md) | [R01通过](rounds/V016-M1_R01_审核小结.md) | [统筹裁决](rounds/V016-M1_R01_统筹裁决.md)；F06/F27本包关闭；统筹49/49，独立审核7反例/9专项/347全量/三规则零差分别留证 |'),
('| V016-M2 |', '| V016-M2 | 统一规则发布与强制门禁 | R01已下发，待开发 | 未产生 | 未产生 | [正式任务](rounds/V016-M2_R01_统筹下发.md)；仅F07/F28发布治理，M2-C01～C08及验收矩阵锁定 |'),
('| F06/F27 |', '| F06/F27 | M1 R01独立审核与统筹抽查通过，本包范围正式关闭，持续回归 |'),
('| F07/F28完整治理、F08 |', '| F07/F28完整治理、F08 | M2发布/评测治理已下发未关闭；F07完整闭环归M4，F28路由及F08镜像资产归M3，M3/M4仍未下发 |'),
('| AUDIT-SIDE-01 |', '| AUDIT-SIDE-01 | M1 R01审核 | 流程留痕，非阻断 | 统筹签认处置关闭，原始证据损失永久留痕 | [R01审核](rounds/V016-M1_R01_审核小结.md)第5节及[统筹裁决](rounds/V016-M1_R01_统筹裁决.md)第4节；原JSON不可恢复，现存为审核再生成证据 | 不伪造恢复；采信可绑定脚本/双方日志及统筹复跑；M2起独立输出路径强制验收 |')],
'doc/00_项目管理/进度总表.md': [
('更新：2026-11-17', '更新：2026-11-18（M1 R01整包放行、唯一下发M2 R01；保留主线外临时方案登记）；维护人：统筹AI。真实状态只以本表和版本终审记录为准，历史“100%闭环”声明不自动继承。'),
('- 当前任务包：', '- 当前任务包：**V016-M2 / R01 已下发，待开发；M0/M1整包放行**；本版M3/M4未下发，不并行开展。'),
('- 当前执行角色：', '- 当前执行角色：本聊天仅统筹；[M1收口](../V0.1.6/rounds/V016-M1_R01_统筹裁决.md)及[M2正式任务](../V0.1.6/rounds/V016-M2_R01_统筹下发.md)。统筹另跑49项通过、15个代码/测试哈希一致；审核7反例/9专项/347全量/三规则零差分别留证。33项清单两项已解释差异：审核台账更新与AUDIT-SIDE-01报告覆写。'),
('- 下个动作：', '- 下个动作：开发仅实施M2三来源统一发布门、内容/基线/有效期绑定与真实绑定评测；M0/M1及离线护栏保持。F05/F06/F27本包范围关闭；F07/F28完整闭环、F08仍开放。PRG-F11开放，V0.1.6未终审，V0.1.7～V0.2.0继续锁定，未发布。'),
('| V0.1.6 |', '| V0.1.6 | M0/M1审核通过并整包正式放行；M2 R01已下发待开发 | 未开展 | 否 | 仅M2可执行；M3/M4未下发 |')],
'doc/00_项目管理/问题与版本归属总台账.md': [
('当前统筹裁决（2026-11-17）：', '当前统筹裁决（2026-11-18）：[M1 R01整包放行](../V0.1.6/rounds/V016-M1_R01_统筹裁决.md)，F06/F27本包范围关闭，唯一下发[M2 R01](../V0.1.6/rounds/V016-M2_R01_统筹下发.md)。M0及既有关闭项保持；AUDIT-SIDE-01流程处置签认关闭、原始JSON不可恢复事实保留。F07/F28发布治理已承接未关闭，F07完整闭环归M4、F28路由及F08归M3，M3/M4尚未下发。V0.1.6未终审、V015终审保持，PRG-F11开放；V0.1.7～V0.2.0继续锁定。下方较早“当前/未准入”等为历史事实，不覆盖本节。'),
('| F06 |', '| F06 | P1 | V016-M1 | 聚合SQL | R01独立审核通过，统筹本包范围关闭，持续回归 | [审核](../V0.1.6/rounds/V016-M1_R01_审核小结.md)、[放行](../V0.1.6/rounds/V016-M1_R01_统筹裁决.md) |'),
('| F07 |', '| F07 | P1 | V015-M1/LEGACY-01局部止血；V016-M2/M4完整闭环 | 发布门禁与闭环 | V015局部门禁保持；M2发布/真实绑定评测已下发未关闭，完整闭环M4未下发 | [M2正式任务](../V0.1.6/rounds/V016-M2_R01_统筹下发.md)、[旧局部边界](../V0.1.5/rounds/V015-M1_R02_统筹裁决.md) |'),
('| F27 |', '| F27 | P2 | V016-M1 | RuleContext | R01统一作用域及错误可见范围独立审核通过，统筹本包关闭 | [审核](../V0.1.6/rounds/V016-M1_R01_审核小结.md)、[放行](../V0.1.6/rounds/V016-M1_R01_统筹裁决.md) |'),
('| F28 |', '| F28 | P2 | V016-M2/M3 | 发布统一与路由 | M2发布治理已下发未关闭；M3静态路由仍未下发 | [M2正式任务](../V0.1.6/rounds/V016-M2_R01_统筹下发.md) |')],
}
prepared = {}
changes = []
for name, replacements in updates.items():
    p = ROOT / name
    lines = p.read_text(encoding='utf-8-sig').splitlines()
    for prefix, new in replacements:
        indexes = [i for i,line in enumerate(lines) if line.startswith(prefix)]
        if len(indexes) != 1:
            raise RuntimeError(f'{name}: expected unique prefix {prefix!r}, found {len(indexes)}')
        i = indexes[0]
        changes.append({'file':name,'old':lines[i],'new':new})
        lines[i] = new
    prepared[name] = '\n'.join(lines)+'\n'
plan = 'doc/V0.1.6/01_实施方案与任务包.md'
needle = '## V016-M2：统一规则发布与强制门禁\n\n- 前置：V016-M1审核通过且统筹下发本包。'
assert needle in prepared[plan]
prepared[plan] = prepared[plan].replace(needle, needle+'\n- 当前授权：2026-11-18唯一下发[R01正式任务](rounds/V016-M2_R01_统筹下发.md)，待开发；M2-C01～C08、24小时有效期、进程300秒/任务660秒超时及验收矩阵以任务书为准。F07/F28发布治理承接但整体未关闭。',1)
progress = 'doc/00_项目管理/进度总表.md'
needle = '## 统筹裁决日志\n\n| 日期 | 裁决 | 依据/影响 |\n|---|---|---|'
assert needle in prepared[progress]
prepared[progress] = prepared[progress].replace(needle,needle+'\n| 2026-11-18 | M1 R01整包放行，唯一下发M2 R01 | [M1裁决](../V0.1.6/rounds/V016-M1_R01_统筹裁决.md)、[M2任务](../V0.1.6/rounds/V016-M2_R01_统筹下发.md)：F06/F27本包关闭；统筹49项/隔离PASS/业务零变，独立审核347全量及三规则分别留证；AUDIT-SIDE-01签认处置关闭、原始JSON不可恢复留痕；M3/M4及后续版本仍锁定，未终审未发布 |',1)
final_name = 'doc/V0.1.6/03_终审与版本准入.md'
final_text = (ROOT/final_name).read_text(encoding='utf-8-sig')
assert '## 2026-11-18 M1 R01' not in final_text
prepared[final_name] = final_text.rstrip()+'\n\n## 2026-11-18 M1 R01正式放行与M2下发（非版本终审）\n\n[M1统筹裁决](rounds/V016-M1_R01_统筹裁决.md)接收独立审核通过，F06/F27在本包范围关闭、M1整包放行；唯一下发[M2 R01](rounds/V016-M2_R01_统筹下发.md)。统筹另跑49项通过、收尾隔离PASS且外层业务不变；审核347全量/7反例/三规则证据分别保留。AUDIT-SIDE-01原始JSON不可恢复事实保留，以可绑定日志/脚本及审核再生成报告签认，不重写原审核。F07/F28/F08仍开放，M3/M4未下发；本版本未终审，V0.1.7～V0.2.0继续锁定，PRG-F11开放，无发布授权。前述较早下发与未关闭状态为当轮历史，不覆盖本节。\n'
for name, text in prepared.items():
    (ROOT/name).write_text(text, encoding='utf-8')
(OUT/'management_changes.json').write_text(json.dumps({'files':list(prepared),'line_changes':changes},ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Updated {len(prepared)} management files; {len(changes)} exact line transitions plus explicit additions.')
