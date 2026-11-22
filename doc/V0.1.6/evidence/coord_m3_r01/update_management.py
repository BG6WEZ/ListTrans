"""Apply only coordinator status transitions for V016-M3 R01 approval & V016-M4 R01 dispatch."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent

updates = {
    "doc/README.md": [
        (
            "当前（2026-11-18）：",
            "当前（2026-11-18）：**V016-M0/M1/M2/M3已整包正式放行，F05/F06/F27/F08/F28及F07发布门禁治理本包范围关闭；唯一下发 V016-M4 / R01（闭环与阶段诊断验收），待开发**，见[M3裁决](V0.1.6/rounds/V016-M3_R01_统筹裁决.md)、[M4正式任务](V0.1.6/rounds/V016-M4_R01_统筹下发.md)及[任务台账](V0.1.6/02_任务台账.md)。V015终审保持；V0.1.6未终审，V0.1.7～V0.2.0继续锁定，PRG-F11开放，未批准生产发布。",
        ),
    ],
    "doc/00_项目管理/版本路线图.md": [
        (
            "当前执行状态（2026-11-18）：",
            "当前执行状态（2026-11-18）：V015已批准实现范围终审保持；**M0/M1/M2/M3整包放行，F05/F06/F27/F08/F28及F07发布门禁治理本包关闭，唯一下发[V016-M4 / R01](../V0.1.6/rounds/V016-M4_R01_统筹下发.md)**。见[M3裁决](../V0.1.6/rounds/V016-M3_R01_统筹裁决.md)。V0.1.6未终审，V0.1.7～V0.2.0继续锁定。PRG-F11首次真实部署前约束保持，未批准发布。上表为初始状态，实时见[进度总表](进度总表.md)。",
        ),
    ],
    "doc/00_项目管理/进度总表.md": [
        (
            "更新：2026-11-18",
            "更新：2026-11-18（M3 R01整包放行、唯一下发M4 R01；保留主线外临时方案登记）；维护人：统筹AI。真实状态只以本表和版本终审记录为准，历史“100%闭环”声明不自动继承。",
        ),
        (
            "- 当前任务包：",
            "- 当前任务包：**V016-M4 / R01 已下发，待开发；M0/M1/M2/M3整包放行**（本版收官包，未终审不解锁下一版本）。",
        ),
        (
            "- 当前执行角色：",
            "- 当前执行角色：本聊天仅统筹；[M3收口](../V0.1.6/rounds/V016-M3_R01_统筹裁决.md)及[M4正式任务](../V0.1.6/rounds/V016-M4_R01_统筹下发.md)。统筹65项确定性组合通过（161.46s, EXIT=0；原66项跑中`test_v0_0_8_llm_rules` 45s紧轮询超时登记为`OBS-M3-01 / C-M4-07`纳入M4修复）、隔离verify PASS且业务快照相对M2签认基线零差异、73/74交付与12/12备件一致、43项离线资产零变；独立审核9反例/8专项/361全量/五基线零差/双断网容器各29项/12层57文件零违规分别留证；AUDIT-SIDE-03已定性签认关闭。",
        ),
        (
            "- 下个动作：",
            "- 下个动作：开发仅实施M4候选全阶段轨迹诊断、`candidate_recall`与真实失败跳过统计、`R-M3-01`阶段诊断、沙箱六分支/LIFO栈顶回滚/热加载闭环、`OBS-M3-01 / C-M4-07`及正式镜像重建容器全闭环复验；F05/F06/F27/F08/F28及F07发布门禁关闭，F07完整闭环与F20/R-M3-01阶段诊断归M4。PRG-F11开放，V0.1.6未终审，V0.1.7～V0.2.0继续锁定，未发布。",
        ),
        (
            "| V0.1.6 |",
            "| V0.1.6 | M0/M1/M2/M3审核通过并整包正式放行；M4 R01已下发待开发 | 未开展 | 否 | 仅M4可执行；M4放行后进入版本终审 |",
        ),
    ],
    "doc/00_项目管理/问题与版本归属总台账.md": [
        (
            "当前统筹裁决（2026-11-18）：",
            "当前统筹裁决（2026-11-18）：[M3 R01整包放行](../V0.1.6/rounds/V016-M3_R01_统筹裁决.md)，F08正式镜像资产与F28全量（M2发布治理+M3静态路由收口）正式关闭，唯一下发[M4 R01](../V0.1.6/rounds/V016-M4_R01_统筹下发.md)（F07完整闭环、F20/R-M3-01阶段诊断及OBS-M3-01/C-M4-07）。M0/M1/M2既有关闭项保持；AUDIT-SIDE-01～03流程处置均已签认关闭，外层业务快照相对M2签认基线零差异。V0.1.6未终审、V015终审保持，PRG-F11开放；V0.1.7～V0.2.0继续锁定。下方较早“当前/未准入”等为历史事实，不覆盖本节。",
        ),
        (
            "| F07 |",
            "| F07 | P1 | V015-M1/LEGACY-01局部止血；V016-M2发布门禁/M4完整闭环 | 发布门禁与闭环 | M2发布强制门禁与真实绑定评测本包关闭；M4完整闭环与LIFO栈式回滚R01已下发待开发 | [M2统筹裁决](../V0.1.6/rounds/V016-M2_R01_统筹裁决.md)、[M4正式任务](../V0.1.6/rounds/V016-M4_R01_统筹下发.md) |",
        ),
        (
            "| F08 |",
            "| F08 | P1 | V016-M3 | 正式镜像资产 | R01独立审核与统筹核验通过，本包范围正式关闭（双断网容器各29项/12层零违规），持续回归 | [M3审核](../V0.1.6/rounds/V016-M3_R01_审核小结.md)、[M3统筹裁决](../V0.1.6/rounds/V016-M3_R01_统筹裁决.md) |",
        ),
        (
            "| F20 |",
            "| F20 | P2 | V015-M2止血；V016-M4诊断；V019-M0/M2闭合 | 排序与接受分离 | M2接受状态/导出止血阶段通过；V016-M4阶段轨迹诊断R01已下发待开发；后续置信度校准仍开放 | [M2放行边界](../V0.1.5/rounds/V015-M2_R01_统筹裁决.md)、[M4正式任务](../V0.1.6/rounds/V016-M4_R01_统筹下发.md) |",
        ),
        (
            "| F28 |",
            "| F28 | P2 | V016-M2/M3 | 发布统一与路由 | M2三源统一发布治理与M3静态路由优先及运行态收口均审核通过并经统筹放行关闭，持续回归 | [M2统筹裁决](../V0.1.6/rounds/V016-M2_R01_统筹裁决.md)、[M3统筹裁决](../V0.1.6/rounds/V016-M3_R01_统筹裁决.md) |",
        ),
    ],
    "doc/V0.1.6/01_实施方案与任务包.md": [
        (
            "当前状态（2026-11-18）：",
            "当前状态（2026-11-18）：**M0/M1/M2/M3整包正式放行，F05/F06/F27/F08/F28及F07发布门禁治理本包范围关闭；唯一下发 V016-M4 / R01，待开发**。见[M3裁决](rounds/V016-M3_R01_统筹裁决.md)和[M4正式任务](rounds/V016-M4_R01_统筹下发.md)。V0.1.6未终审，V0.1.7～V0.2.0继续锁定，PRG-F11首次真实部署前强制门禁保持开放。",
        ),
        (
            "- 当前授权：2026-11-18唯一下发[R01正式任务](rounds/V016-M3_R01_统筹下发.md)",
            "- 当前结论：2026-11-18接收[R01独立审核](rounds/V016-M3_R01_审核小结.md)，[统筹整包放行](rounds/V016-M3_R01_统筹裁决.md)，F08正式镜像评测资产与F28（含M2发布治理及M3静态路由收口）正式关闭；M3-C01～C06及M0/M1/M2保护持续回归。AUDIT-SIDE-03已签认处置关闭；OBS-M3-01 / C-M4-07并入M4 R01收口。",
        ),
    ],
    "doc/V0.1.6/02_任务台账.md": [
        (
            "2026-11-18 当前统筹裁决：",
            "2026-11-18 当前统筹裁决：**M3 R01整包正式放行，F08与F28正式关闭；唯一下发 V016-M4 / R01，待开发**。见[M3裁决](rounds/V016-M3_R01_统筹裁决.md)和[M4正式任务](rounds/V016-M4_R01_统筹下发.md)。M0/M1/M2及既有关闭项保持；AUDIT-SIDE-01～03流程处置签认关闭，外层业务快照与M2签认基线零差异。F07完整闭环、F20/R-M3-01阶段诊断及OBS-M3-01/C-M4-07归M4（已下发）；V0.1.6未终审，V0.1.7～V0.2.0继续锁定，PRG-F11开放。",
        ),
        (
            "| V016-M3 |",
            "| V016-M3 | 正式镜像内的评测资产 | R01审核通过，统筹整包放行 | [R01开发小结](rounds/V016-M3_R01_开发小结.md) | [R01通过](rounds/V016-M3_R01_审核小结.md) | [统筹裁决](rounds/V016-M3_R01_统筹裁决.md)；F08与F28正式关闭；统筹65/65确定性组合通过+OBS-M3-01转M4、隔离PASS，独立审核9反例/8专项/361全量/五基线零差/双容器29项/12层零违规 |",
        ),
        (
            "| V016-M4 |",
            "| V016-M4 | 闭环与阶段诊断验收 | R01已下发，待开发 | 未产生 | 未产生 | [正式任务](rounds/V016-M4_R01_统筹下发.md)；承接F07完整闭环复核、F20/R-M3-01阶段诊断与OBS-M3-01/C-M4-07，M4-C01～C06及验收矩阵锁定（含镜像重建闭环） |",
        ),
        (
            "| F07/F28完整治理、F08 |",
            "| F07/F28完整治理、F08 | M2发布门禁治理关闭；M3 F08正式镜像资产与F28静态路由收口通过放行关闭；F07完整闭环与LIFO回滚归M4已下发待开发 |",
        ),
        (
            "| R-M3-01 / P2 |",
            "| R-M3-01 / P2 | 保持推荐/确认导出保护；V016-M4 R01已下发阶段诊断，V019-M2最终闭合期限不变 |",
        ),
        (
            "| AUDIT-SIDE-03 |",
            "| AUDIT-SIDE-03 | M3 R01审核 | 审核侧流程事件，非产品缺陷 | 统筹签认处置关闭，残缺日志废弃留痕，以full_pytest_audit_default.log为准 | [R01审核](rounds/V016-M3_R01_审核小结.md)第5节、[统筹裁决](rounds/V016-M3_R01_统筹裁决.md)第4节及`evidence/audit_m3_r01/audit_events.log`；默认根e2e 88.62s通过、同开发命令重跑全量361/361 EXIT=0 | 不推翻开发全量结论；废弃停在36点的fixed/standard残缺日志；M4起Windows主机禁止向LISTTRANS_TEST_ROOT或--basetemp传入POSIX /tmp路径 |",
        ),
    ],
}

prepared = {}
changes = []
for name, replacements in updates.items():
    p = ROOT / name
    lines = p.read_text(encoding="utf-8-sig").splitlines()
    for prefix, new in replacements:
        indexes = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        if len(indexes) != 1:
            raise RuntimeError(
                f"{name}: expected unique prefix {prefix!r}, found {len(indexes)}"
            )
        i = indexes[0]
        changes.append({"file": name, "old": lines[i], "new": new})
        lines[i] = new
    prepared[name] = "\n".join(lines) + "\n"

# Insert M4 authorization line in 01_实施方案与任务包.md
plan_name = "doc/V0.1.6/01_实施方案与任务包.md"
needle = "## V016-M4：闭环与阶段诊断验收\n\n- 前置：V016-M3审核通过且统筹下发本包。"
assert needle in prepared[plan_name]
m4_auth_line = "- 当前授权：2026-11-18唯一下发[R01正式任务](rounds/V016-M4_R01_统筹下发.md)，待开发；M4-C01～C06及验收矩阵以任务书为准，承接F07完整闭环复核、F20/R-M3-01阶段诊断与OBS-M3-01/C-M4-07。"
prepared[plan_name] = prepared[plan_name].replace(
    needle, needle + "\n" + m4_auth_line, 1
)
changes.append(
    {"file": plan_name, "old": "(insertion after V016-M4 前置)", "new": m4_auth_line}
)

# Insert top row in 进度总表.md 统筹裁决日志
progress_name = "doc/00_项目管理/进度总表.md"
needle_p = "## 统筹裁决日志\n\n| 日期 | 裁决 | 依据/影响 |\n|---|---|---|"
assert needle_p in prepared[progress_name]
log_row = "| 2026-11-18 | M3 R01整包放行，唯一下发M4 R01 | [M3裁决](../V0.1.6/rounds/V016-M3_R01_统筹裁决.md)、[M4任务](../V0.1.6/rounds/V016-M4_R01_统筹下发.md)：F08与F28正式关闭；统筹65/65通过+OBS-M3-01转M4、隔离PASS/业务零污染，独立审核9反例/8专项/361全量/五基线零差/双容器29项/12层零违规；AUDIT-SIDE-03签认关闭；V0.1.7～V0.2.0仍锁定，未终审未发布 |"
prepared[progress_name] = prepared[progress_name].replace(
    needle_p, needle_p + "\n" + log_row, 1
)
changes.append(
    {"file": progress_name, "old": "(insertion at top of 统筹裁决日志)", "new": log_row}
)

# Append OBS-M3-01 / C-M4-07 row to 02_任务台账.md 审核问题与范围裁决 table
ledger_name = "doc/V0.1.6/02_任务台账.md"
needle_obs = "| AUDIT-SIDE-03 |"
obs_row = "| OBS-M3-01 / C-M4-07 | M3 R01统筹抽查 | P2当前版本必改 | 新增开放，并入V016-M4 / R01收口 | [M3裁决](rounds/V016-M3_R01_统筹裁决.md)第4节、[M4正式任务](rounds/V016-M4_R01_统筹下发.md)及`evidence/coord_m3_r01/targeted_pytest_66_raw.log`；`test_v0_0_8_llm_rules`第141行45s紧轮询在1067条金标+双子进程冷建向量缓存下超时（实测66项236.77s/单跑85.28s/预热66.54s，门禁正确拦截409 `eval_not_completed`），同链路在`test_v016_m2_publish_gate` 120s轮询已通过 | 不延期，不阻塞M3放行；M4须复用同快照baseline向量缓存并将该测试轮询对齐120s且先断言终态 |"
ledger_lines = prepared[ledger_name].splitlines()
audit3_idx = next(i for i, ln in enumerate(ledger_lines) if ln.startswith(needle_obs))
ledger_lines.insert(audit3_idx + 1, obs_row)
prepared[ledger_name] = "\n".join(ledger_lines) + "\n"
changes.append(
    {"file": ledger_name, "old": "(insertion after AUDIT-SIDE-03)", "new": obs_row}
)

# Append section to 03_终审与版本准入.md
final_name = "doc/V0.1.6/03_终审与版本准入.md"
final_text = (ROOT / final_name).read_text(encoding="utf-8-sig")
assert "## 2026-11-18 M3 R01" not in final_text
append_sec = (
    "\n\n## 2026-11-18 M3 R01正式放行与M4下发（非版本终审）\n\n"
    "[M3统筹裁决](rounds/V016-M3_R01_统筹裁决.md)接收独立审核通过，F08正式镜像评测资产与F28全量（M2发布治理+M3静态路由收口）正式关闭、M3整包放行；唯一下发收官包[M4 R01](rounds/V016-M4_R01_统筹下发.md)。"
    "统筹65项确定性组合回归通过（161.46s, EXIT=0；原始66项跑中`test_v0_0_8_llm_rules` 45s紧轮询超时已存档`targeted_pytest_66_raw.log`并登记`OBS-M3-01 / C-M4-07`并入M4必改），收尾隔离PASS、外层核心业务快照与M2签认基线零差异（4项0B WAL/SHM系只读打开恢复空句柄）；独立审核9反例/8专项/361全量/五基线零差/断网双容器各29项/12层57文件零违规证据分别保留，审核小结标题“F11/F28”笔误勘误为F08/F28。"
    "AUDIT-SIDE-03审核侧路径构造失误已签认处置关闭，废弃停在36点的残缺日志而以`full_pytest_audit_default.log`为准，不重写原审核。"
    "F07完整闭环、F20/R-M3-01阶段诊断与OBS-M3-01/C-M4-07归M4（已下发）；M4审核放行后方可开启V0.1.6版本终审。V0.1.7～V0.2.0继续锁定，PRG-F11开放，无发布授权。前述较早下发与未关闭状态为当轮历史，不覆盖本节。\n"
)
prepared[final_name] = final_text.rstrip() + append_sec
changes.append(
    {"file": final_name, "old": "(append section)", "new": append_sec.strip()}
)

for name, text in prepared.items():
    (ROOT / name).write_text(text, encoding="utf-8")

(OUT / "management_changes.json").write_text(
    json.dumps(
        {"files": list(prepared), "line_changes": changes}, ensure_ascii=False, indent=2
    ),
    encoding="utf-8",
)
print(
    f"Updated {len(prepared)} management files; {len(changes)} recorded transitions."
)
