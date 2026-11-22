"""Apply only coordinator status transitions for V016-M2 R01 approval & V016-M3 R01 dispatch."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent

updates = {
    'doc/README.md': [
        (
            '当前（2026-11-18）：',
            '当前（2026-11-18）：**V016-M0/M1/M2已整包正式放行，F05/F06/F27及F07/F28发布门禁治理本包范围关闭；唯一下发 V016-M3 / R01（正式镜像内的评测资产），待开发**，见[M2裁决](V0.1.6/rounds/V016-M2_R01_统筹裁决.md)、[M3正式任务](V0.1.6/rounds/V016-M3_R01_统筹下发.md)及[任务台账](V0.1.6/02_任务台账.md)。V015终审保持；V0.1.6未终审，M4未下发，V0.1.7～V0.2.0继续锁定，PRG-F11开放，未批准生产发布。',
        ),
    ],
    'doc/00_项目管理/版本路线图.md': [
        (
            '当前执行状态（2026-11-18）：',
            '当前执行状态（2026-11-18）：V015已批准实现范围终审保持；**M0/M1/M2整包放行，F05/F06/F27及F07/F28发布门禁治理本包关闭，唯一下发[V016-M3 / R01](../V0.1.6/rounds/V016-M3_R01_统筹下发.md)**。见[M2裁决](../V0.1.6/rounds/V016-M2_R01_统筹裁决.md)。V0.1.6未终审，M4未下发，V0.1.7～V0.2.0继续锁定。PRG-F11首次真实部署前约束保持，未批准发布。上表为初始状态，实时见[进度总表](进度总表.md)。',
        ),
    ],
    'doc/00_项目管理/进度总表.md': [
        (
            '更新：2026-11-18',
            '更新：2026-11-18（M2 R01整包放行、唯一下发M3 R01；保留主线外临时方案登记）；维护人：统筹AI。真实状态只以本表和版本终审记录为准，历史“100%闭环”声明不自动继承。',
        ),
        (
            '- 当前任务包：',
            '- 当前任务包：**V016-M3 / R01 已下发，待开发；M0/M1/M2整包放行**；本版M4未下发，不并行开展。',
        ),
        (
            '- 当前执行角色：',
            '- 当前执行角色：本聊天仅统筹；[M2收口](../V0.1.6/rounds/V016-M2_R01_统筹裁决.md)及[M3正式任务](../V0.1.6/rounds/V016-M3_R01_统筹下发.md)。统筹另跑58项通过（EXIT=0）、24/25交付哈希与19/19改前备件一致、43项资产零变；审核12反例/三规则零差+绑定真跑/Chrome DOM/双根353全量分别留证。25项清单唯一差异为审核按协议更新台账；AUDIT-SIDE-02签认处置关闭，逻辑干净新快照已确立为M3起基线。',
        ),
        (
            '- 下个动作：',
            '- 下个动作：开发仅实施M3正式运行评测模块、脱敏资产打包、模型降级/缺资产fail-closed及静态路由收口；M0/M1/M2及离线护栏保持。F05/F06/F27本包范围关闭，F07/F28发布门禁治理关闭；F07完整闭环归M4，F08与F28路由归M3。PRG-F11开放，V0.1.6未终审，V0.1.7～V0.2.0继续锁定，未发布。',
        ),
        (
            '| V0.1.6 |',
            '| V0.1.6 | M0/M1/M2审核通过并整包正式放行；M3 R01已下发待开发 | 未开展 | 否 | 仅M3可执行；M4未下发 |',
        ),
    ],
    'doc/00_项目管理/问题与版本归属总台账.md': [
        (
            '当前统筹裁决（2026-11-18）：',
            '当前统筹裁决（2026-11-18）：[M2 R01整包放行](../V0.1.6/rounds/V016-M2_R01_统筹裁决.md)，F07发布门禁/真实绑定评测及F28三源统一发布治理本包范围关闭，唯一下发[M3 R01](../V0.1.6/rounds/V016-M3_R01_统筹下发.md)。M0/M1既有关闭项保持；AUDIT-SIDE-01/02流程处置签认关闭，原始证据/字节哈希不可恢复事实保留，逻辑清理后新业务快照已确立为M3起基线。F07完整闭环归M4（未下发）；F08与F28静态路由归M3（已下发）。V0.1.6未终审、V015终审保持，PRG-F11开放；V0.1.7～V0.2.0继续锁定。下方较早“当前/未准入”等为历史事实，不覆盖本节。',
        ),
        (
            '| F07 |',
            '| F07 | P1 | V015-M1/LEGACY-01局部止血；V016-M2发布门禁/M4完整闭环 | 发布门禁与闭环 | M2发布强制门禁与真实绑定评测独立审核及统筹通过并本包关闭；完整闭环与阶段诊断归M4（未下发） | [M2统筹裁决](../V0.1.6/rounds/V016-M2_R01_统筹裁决.md)、[M2审核](../V0.1.6/rounds/V016-M2_R01_审核小结.md) |',
        ),
        (
            '| F08 |',
            '| F08 | P1 | V016-M3 | 正式镜像资产 | M3 R01已下发待开发/独立审核 | [M3正式任务](../V0.1.6/rounds/V016-M3_R01_统筹下发.md) |',
        ),
        (
            '| F28 |',
            '| F28 | P2 | V016-M2/M3 | 发布统一与路由 | M2三源统一发布治理已放行关闭；M3静态路由与镜像收口已下发待开发 | [M2统筹裁决](../V0.1.6/rounds/V016-M2_R01_统筹裁决.md)、[M3正式任务](../V0.1.6/rounds/V016-M3_R01_统筹下发.md) |',
        ),
    ],
    'doc/V0.1.6/01_实施方案与任务包.md': [
        (
            '当前状态（2026-11-18）：',
            '当前状态（2026-11-18）：**M0/M1/M2整包正式放行，F05/F06/F27及F07/F28发布门禁治理本包范围关闭；唯一下发 V016-M3 / R01，待开发**。见[M2裁决](rounds/V016-M2_R01_统筹裁决.md)和[M3正式任务](rounds/V016-M3_R01_统筹下发.md)。M4未下发，V0.1.6未终审，V0.1.7～V0.2.0继续锁定，PRG-F11首次真实部署前强制门禁保持开放。',
        ),
        (
            '- 当前授权：2026-11-18唯一下发[R01正式任务]',
            '- 当前结论：2026-11-18接收[R01独立审核](rounds/V016-M2_R01_审核小结.md)，[统筹整包放行](rounds/V016-M2_R01_统筹裁决.md)，F07发布强制门禁与F28发布治理本包范围关闭；M2-C01～C08及M0/M1保护持续回归。AUDIT-SIDE-02已签认处置，逻辑干净快照确立为M3起基线；F07完整闭环归M4、F28路由归M3。',
        ),
    ],
    'doc/V0.1.6/02_任务台账.md': [
        (
            '2026-11-18 当前统筹裁决：',
            '2026-11-18 当前统筹裁决：**M2 R01整包正式放行，F07发布门禁/F28发布治理本包范围关闭；唯一下发 V016-M3 / R01，待开发**。见[M2裁决](rounds/V016-M2_R01_统筹裁决.md)和[M3正式任务](rounds/V016-M3_R01_统筹下发.md)。M0/M1及既有关闭项保持；AUDIT-SIDE-01/02流程处置签认关闭，原始证据/字节哈希不可恢复事实永久留痕，逻辑干净快照已确立为M3起基线。F07完整闭环归M4（未下发），F08与F28静态路由归M3（已下发）；V0.1.6未终审，V0.1.7～V0.2.0继续锁定，PRG-F11开放。',
        ),
        (
            '| V016-M2 |',
            '| V016-M2 | 统一规则发布与强制门禁 | R01审核通过，统筹整包放行 | [R01开发小结](rounds/V016-M2_R01_开发小结.md) | [R01通过](rounds/V016-M2_R01_审核小结.md) | [统筹裁决](rounds/V016-M2_R01_统筹裁决.md)；F07/F28发布治理关闭；统筹58/58，独立审核12反例/三规则零差+绑定真跑/Chrome DOM/双根353全量 |',
        ),
        (
            '| V016-M3 |',
            '| V016-M3 | 正式镜像内的评测资产 | R01已下发，待开发 | 未产生 | 未产生 | [正式任务](rounds/V016-M3_R01_统筹下发.md)；承接F08与F28路由收口，M3-C01～C06及验收矩阵锁定 |',
        ),
        (
            '| F07/F28完整治理、F08 |',
            '| F07/F28完整治理、F08 | M2发布门禁与三源治理范围已通过放行关闭；F08与F28静态路由归M3已下发待开发；F07完整闭环归M4仍未下发 |',
        ),
        (
            '| AUDIT-SIDE-02 |',
            '| AUDIT-SIDE-02 | M2 R01审核 | 审核侧流程事件，非产品缺陷 | 统筹签认处置关闭，原始字节哈希不可恢复留痕，新业务库基线已签认 | [R01审核](rounds/V016-M2_R01_审核小结.md)第5节、[统筹裁决](rounds/V016-M2_R01_统筹裁决.md)第4节及`evidence/audit_m2_r01/audit_events.log`；探针行精确删除且逻辑完整(materials 26863/users 186/rule_eval_runs 0)，导入迁移与checkpoint致字节哈希变化 | 不伪造恢复、不宣称完全恢复；采信24/25交付哈希与双根353结论；确立当前逻辑干净快照为M3起基线，M3起tests外脚本强制全套env注入 |',
        ),
    ],
}

prepared = {}
changes = []
for name, replacements in updates.items():
    p = ROOT / name
    lines = p.read_text(encoding='utf-8-sig').splitlines()
    for prefix, new in replacements:
        indexes = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        if len(indexes) != 1:
            raise RuntimeError(f'{name}: expected unique prefix {prefix!r}, found {len(indexes)}')
        i = indexes[0]
        changes.append({'file': name, 'old': lines[i], 'new': new})
        lines[i] = new
    prepared[name] = '\n'.join(lines) + '\n'

plan = 'doc/V0.1.6/01_实施方案与任务包.md'
needle = '## V016-M3：正式镜像内的评测资产\n\n- 前置：V016-M2审核通过且统筹下发本包。'
assert needle in prepared[plan]
m3_auth_line = '- 当前授权：2026-11-18唯一下发[R01正式任务](rounds/V016-M3_R01_统筹下发.md)，待开发；M3-C01～C06及验收矩阵以任务书为准，承接F08与F28静态路由收口。'
prepared[plan] = prepared[plan].replace(needle, needle + '\n' + m3_auth_line, 1)
changes.append({'file': plan, 'old': '(insertion after V016-M3 前置)', 'new': m3_auth_line})

progress = 'doc/00_项目管理/进度总表.md'
needle_p = '## 统筹裁决日志\n\n| 日期 | 裁决 | 依据/影响 |\n|---|---|---|'
assert needle_p in prepared[progress]
log_row = '| 2026-11-18 | M2 R01整包放行，唯一下发M3 R01 | [M2裁决](../V0.1.6/rounds/V016-M2_R01_统筹裁决.md)、[M3任务](../V0.1.6/rounds/V016-M3_R01_统筹下发.md)：F07发布门禁/F28发布治理本包关闭；统筹58项/隔离PASS/业务零变，独立审核12反例/三规则零差+绑定真跑/Chrome DOM/双根353全量；AUDIT-SIDE-02签认处置关闭、逻辑干净新快照确立为M3起基线；M4及后续版本仍锁定，未终审未发布 |'
prepared[progress] = prepared[progress].replace(needle_p, needle_p + '\n' + log_row, 1)
changes.append({'file': progress, 'old': '(insertion at top of 统筹裁决日志)', 'new': log_row})

final_name = 'doc/V0.1.6/03_终审与版本准入.md'
final_text = (ROOT / final_name).read_text(encoding='utf-8-sig')
assert '## 2026-11-18 M2 R01' not in final_text
append_sec = (
    '\n\n## 2026-11-18 M2 R01正式放行与M3下发（非版本终审）\n\n'
    '[M2统筹裁决](rounds/V016-M2_R01_统筹裁决.md)接收独立审核通过，F07发布门禁/真实绑定评测与F28三源统一发布治理在本包范围关闭、M2整包放行；唯一下发[M3 R01](rounds/V016-M3_R01_统筹下发.md)。'
    '统筹另跑58项通过、收尾隔离PASS且外层业务相对开场不变；审核12反例/三规则零差+绑定真跑/Chrome DOM/双根353全量证据分别保留。'
    'AUDIT-SIDE-02审核侧流程事件已逻辑清理并经统筹签认关闭，原始字节哈希不可恢复事实永久留痕，确立当前逻辑干净快照为M3起新受控对比基线，不重写原审核。'
    'F07完整闭环归M4（未下发），F08与F28静态路由归M3（已下发）；本版本未终审，V0.1.7～V0.2.0继续锁定，PRG-F11开放，无发布授权。前述较早下发与未关闭状态为当轮历史，不覆盖本节。\n'
)
prepared[final_name] = final_text.rstrip() + append_sec
changes.append({'file': final_name, 'old': '(append section)', 'new': append_sec.strip()})

for name, text in prepared.items():
    (ROOT / name).write_text(text, encoding='utf-8')

(OUT / 'management_changes.json').write_text(
    json.dumps({'files': list(prepared), 'line_changes': changes}, ensure_ascii=False, indent=2),
    encoding='utf-8',
)
print(f'Updated {len(prepared)} management files; {len(changes)} recorded transitions.')
