"""Apply Coordinator status transitions for V016-M4 R01 release and V0.1.6 Version Final Audit PASS."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent

updates = {
    "doc/README.md": [
        (
            "当前（2026-11-18）：",
            "当前（2026-11-18）：**V016-M0～M4全部审核通过并由统筹正式放行，V0.1.6已批准实现验收范围正式终审通过**；本版F05/F06/F07/F08/F27/F28全部关闭，F20与R-M3-01完成M4阶段诊断；见[M4/终审裁决](V0.1.6/rounds/V016-M4_R01_统筹裁决.md)、[终审与准入](V0.1.6/03_终审与版本准入.md)及[任务台账](V0.1.6/02_任务台账.md)。V015终审保持；V0.1.7～V0.2.0继续锁定（尚待另行准入与V017-M0下发），PRG-F11开放，未批准生产发布。",
        ),
    ],
    "doc/00_项目管理/版本路线图.md": [
        (
            "当前执行状态（2026-11-18）：",
            "当前执行状态（2026-11-18）：V015终审保持；**V016-M0～M4全部审核通过并由统筹放行，V0.1.6已批准实现验收范围正式终审通过**（F05/F06/F07/F08/F27/F28全关闭，F20/R-M3-01完成M4阶段诊断，正式镜像`9517cc2a…`与双断网容器各51项通过）。见[终审裁决](../V0.1.6/rounds/V016-M4_R01_统筹裁决.md)与[V0.1.6终审记录](../V0.1.6/03_终审与版本准入.md)。V0.1.7～V0.2.0继续锁定（尚待用户指令及统筹另行准入并下发V017-M0），PRG-F11首次真实部署前约束保持，未批准发布。上表为初始状态，实时见[进度总表](进度总表.md)。",
        ),
    ],
    "doc/00_项目管理/进度总表.md": [
        (
            "更新：2026-11-18",
            "更新：2026-11-18（V016-M4 R01正式放行，V0.1.6正式终审通过；V0.1.7～V0.2.0保持锁定）；维护人：统筹AI。真实状态只以本表和版本终审记录为准，历史“100%闭环”声明不自动继承。",
        ),
        (
            "- 当前版本：",
            "- 当前版本：**V0.1.6 正式终审通过（限已批准实现验收范围，2026-11-18）**；V0.1.5 终审保持；V0.1.7～V0.2.0 继续锁定，生产发布未批准。",
        ),
        (
            "- 当前任务包：",
            "- 当前任务包：**无执行中的开发包**；V016-M0～M4 全部审核通过并正式放行（[M4裁决/终审](../V0.1.6/rounds/V016-M4_R01_统筹裁决.md)）；下一候选首包 `V017-M0` 尚未下发。",
        ),
        (
            "- 当前执行角色：",
            "- 当前执行角色：本聊天仅统筹；已完成 [M4放行与V0.1.6终审](../V0.1.6/rounds/V016-M4_R01_统筹裁决.md) 及 [03_终审与版本准入](../V0.1.6/03_终审与版本准入.md)。统筹完成121/122交付与6/6改前备件核对、只读diff审查、78项跨包终审抽样回归（`78 passed in 138.58s, EXIT=0`，含`test_v0_0_8_llm_rules` 4/4通过）、`verify_isolation.py` PASS/0、只读SQLite零污染及35项外层资产零变化核验；并接收独立审核16反例/16专项/4冷启动/373全量(748.07s)/6基线零差/ENV-M4-01三态/12层57文件零违规/双断网容器各51项HTTP闭环证据；AUDIT-SIDE-04、ENV-M4-01已签认关闭，OBS-AUDIT-M4-01/P3非阻断登记。",
        ),
        (
            "- 下个动作：",
            "- 下个动作：等待用户明确指令决定是否另行开展 `V0.1.7` 版本准入并下发 `V017-M0`（数据基准冻结、编码统一与缓存/并发基础设施）。V0.1.6 承接的 F05/F06/F07/F08/F27/F28 及 OBS-M3-01/C-M4-07 均已关闭，F20/R-M3-01 完成 M4 阶段诊断（策略闭合仍归 V019-M0/M2）；PRG-F11 保持开放，未批准生产发布。",
        ),
        (
            "| V0.1.6 |",
            "| V0.1.6 | M0～M4全部审核通过并正式放行，F05～F08/F27/F28关闭，F20/R-M3-01完成阶段诊断 | 通过（2026-11-18，限已批准实现范围） | 否（暂锁定，待用户指令及统筹另行准入并下发V017-M0） | 正式镜像`9517cc2a…`与双容器各51项通过；PRG-F11开放，未批准发布 |",
        ),
    ],
    "doc/00_项目管理/问题与版本归属总台账.md": [
        (
            "当前统筹裁决（2026-11-18）：",
            "当前统筹裁决（2026-11-18）：[M4 R01正式放行、V0.1.6正式终审通过](../V0.1.6/rounds/V016-M4_R01_统筹裁决.md)（[正式终审](../V0.1.6/03_终审与版本准入.md)）。V0.1.6承接的 **F05/F06/F07/F08/F27/F28 与 OBS-M3-01/C-M4-07 全部关闭**；**F20 与 R-M3-01 完成 V016-M4 阶段诊断里程碑**（最终策略闭合仍归 V019-M0/M2）；AUDIT-SIDE-01～04 与 ENV-M4-01 流程/环境事件均签认关闭，新增 OBS-AUDIT-M4-01 / P3 非阻断观察项；已批准实现验收范围内 P0/P1 为 0。V015 终审保持，PRG-F11 首次真实部署前门禁保持开放；V0.1.7～V0.2.0 继续锁定（尚待统筹另行准入与下发 V017-M0）。下方较早“当前/未准入”等为历史事实，不覆盖本节。",
        ),
        (
            "| F07 |",
            "| F07 | P1 | V015-M1/LEGACY-01局部止血；V016-M2发布门禁/M4完整闭环 | 发布门禁与闭环 | M2发布强制门禁与M4完整闭环、LIFO栈顶校验与防泄漏导出全部通过独立审核及统筹核验，正式全量关闭（持续回归） | [M2统筹裁决](../V0.1.6/rounds/V016-M2_R01_统筹裁决.md)、[M4统筹裁决/终审](../V0.1.6/rounds/V016-M4_R01_统筹裁决.md) |",
        ),
        (
            "| F20 |",
            "| F20 | P2 | V015-M2止血；V016-M4诊断；V019-M0/M2闭合 | 排序与接受分离 | M2止血与V016-M4五阶段轨迹/candidate_recall诊断阶段通过；后续置信度校准仍开放归V019-M0/M2 | [M2放行边界](../V0.1.5/rounds/V015-M2_R01_统筹裁决.md)、[M4统筹裁决](../V0.1.6/rounds/V016-M4_R01_统筹裁决.md) |",
        ),
    ],
    "doc/V0.1.6/01_实施方案与任务包.md": [
        (
            "当前状态（2026-11-18）：",
            "当前状态（2026-11-18）：**M0～M4全部审核通过并由统筹正式放行；V0.1.6已批准实现验收范围正式终审通过**，见[M4裁决/终审](rounds/V016-M4_R01_统筹裁决.md)与[终审记录](03_终审与版本准入.md)。本版F05/F06/F07/F08/F27/F28及OBS-M3-01/C-M4-07全部关闭，F20/R-M3-01完成M4阶段诊断；V0.1.7～V0.2.0继续锁定（尚待另行准入与V017-M0下发），PRG-F11首次真实部署前强制门禁保持开放，未批准生产发布。",
        ),
        (
            "- 当前授权：2026-11-18唯一下发[R01正式任务](rounds/V016-M4_R01_统筹下发.md)",
            "- 当前结论：2026-11-18接收[R01独立审核](rounds/V016-M4_R01_审核小结.md)，[统筹放行与版本终审](rounds/V016-M4_R01_统筹裁决.md)，F07完整闭环与LIFO回滚、OBS-M3-01/C-M4-07正式关闭，F20/R-M3-01完成M4阶段诊断；正式镜像`9517cc2a…`与双断网容器各51步全闭环及M0～M4保护持续回归。AUDIT-SIDE-04、ENV-M4-01签认关闭，OBS-AUDIT-M4-01/P3非阻断开放。",
        ),
    ],
    "doc/V0.1.6/02_任务台账.md": [
        (
            "2026-11-18 当前统筹裁决：",
            "2026-11-18 当前统筹裁决：**M4 R01整包正式放行，V0.1.6正式终审通过（限已批准实现验收范围）**。见[M4裁决/终审](rounds/V016-M4_R01_统筹裁决.md)与[终审记录](03_终审与版本准入.md)。本版F05/F06/F07/F08/F27/F28与OBS-M3-01/C-M4-07全部关闭，F20/R-M3-01完成M4阶段诊断；AUDIT-SIDE-01～04与ENV-M4-01均签认关闭，外层业务快照与M2/M3签认基线零差异，OBS-AUDIT-M4-01/P3非阻断开放。V0.1.7～V0.2.0继续锁定（尚待另行准入与V017-M0下发），PRG-F11开放，未批准生产发布。",
        ),
        (
            "| V016-M4 |",
            "| V016-M4 | 闭环与阶段诊断验收 | R01审核通过，统筹整包放行（V0.1.6终审通过） | [R01开发小结](rounds/V016-M4_R01_开发小结.md) | [R01通过](rounds/V016-M4_R01_审核小结.md) | [统筹裁决/终审](rounds/V016-M4_R01_统筹裁决.md)；F07完整闭环与OBS-M3-01关闭、F20/R-M3-01阶段诊断达成；统筹78项通过+隔离PASS/业务零变，独立审核16反例/16专项/4冷启动/373全量/6基线零差/双容器各51项/12层57文件零违规；OBS-AUDIT-M4-01/P3开放 |",
        ),
        (
            "| F07/F28完整治理、F08 |",
            "| F07/F28完整治理、F08 | M2发布门禁、M3 F08正式镜像资产与F28静态路由、M4 F07完整闭环与LIFO回滚均已审核通过并由统筹放行正式关闭，持续回归 |",
        ),
        (
            "| R-M3-01 / P2 |",
            "| R-M3-01 / P2 | V016-M4阶段诊断已完成（GT-0674/0677/0678 均top10_hit/T=98.6/W=98.5/margin=0.1/recommended/导出拦截422）；V019-M2策略闭合期限与当前保护不变 |",
        ),
        (
            "| OBS-M3-01 / C-M4-07 |",
            "| OBS-M3-01 / C-M4-07 | M3 R01统筹抽查 | P2当前版本必改 | M4 R01独立审核与统筹抽查确认关闭 | [M3裁决](rounds/V016-M3_R01_统筹裁决.md)第4节、[M4裁决](rounds/V016-M4_R01_统筹裁决.md)及`evidence/coord_m4_r01/targeted_pytest_local.log`；`flywheel_sandbox`向candidate私有`VECTOR_CACHE_DIR`复制同快照4缓存文件、`test_v0_0_8_llm_rules`轮询对齐`range(240)`(120s)并先断言终态再发布；空缓存冷启动4/4通过（开发43.31s/审核39.13s）、统筹78项含该文件138.58s全过 | 不延期；保持同快照只读复制（严禁共享可写目录） |",
        ),
        (
            "| AUDIT-SIDE-04 |",
            "| AUDIT-SIDE-04 | M4 R01审核 | 审核侧流程事件，非产品缺陷 | 统筹签认处置关闭；开发双根373与审核重跑373结论采信 | [R01审核](rounds/V016-M4_R01_审核小结.md)第5节、[M4裁决](rounds/V016-M4_R01_统筹裁决.md)及`evidence/audit_m4_r01/audit_events.log`；默认根首跑373点F/s/x/E=0但收尾411文件rmtree被`[safe-delete]`(阈值50)拦截致exit=1；系统Temp原生`--basetemp`重跑得`373 passed in 748.07s, EXIT=0` | 不改产品代码(121/122+台账)、不触业务库(35项零变化)；首跑`audit_m4_full.log`留痕不作通过依据，Windows重跑显式使用仓库外原生`--basetemp` |",
        ),
        (
            "| OBS-AUDIT-M4-01 |",
            "| OBS-AUDIT-M4-01 | M4 R01审核静态观察 | P3低优先级健壮性，非阻断 | 统筹接收开放，转后续规则配置治理顺带加守卫 | [R01审核](rounds/V016-M4_R01_审核小结.md)第2节及[M4裁决](rounds/V016-M4_R01_统筹裁决.md)；`app/rule_governance.py:468-470`未过滤空字符串`src`，极端手工配置下`server.py:3524`导出防泄漏`\"\" in raw_name`恒真过度过滤 | 当前工作区无该文件、触发方向fail-closed不泄漏错绑；不阻塞M4放行或V0.1.6终审，后续配置治理补`if src:`守卫 |",
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

# 1. Insert top row in 进度总表.md 统筹裁决日志
progress_name = "doc/00_项目管理/进度总表.md"
needle_p = "## 统筹裁决日志\n\n| 日期 | 裁决 | 依据/影响 |\n|---|---|---|"
assert needle_p in prepared[progress_name]
log_row = "| 2026-11-18 | M4 R01正式放行，V0.1.6正式终审通过 | [M4裁决/终审](../V0.1.6/rounds/V016-M4_R01_统筹裁决.md)、[终审记录](../V0.1.6/03_终审与版本准入.md)：F07全量与OBS-M3-01关闭、F20/R-M3-01阶段诊断达成（策略闭合仍归V019-M0/M2）；统筹78项通过+隔离PASS/业务零变；独立审核16反例/16专项/4冷启动/373全量/6基线零差/ENV-M4-01三态/12层57文件零违规/双容器各51项HTTP；AUDIT-SIDE-04签认关闭，OBS-AUDIT-M4-01/P3登记；V0.1.7～V0.2.0保持锁定，PRG-F11开放，未发布 |"
prepared[progress_name] = prepared[progress_name].replace(
    needle_p, needle_p + "\n" + log_row, 1
)
changes.append(
    {"file": progress_name, "old": "(insertion at top of 统筹裁决日志)", "new": log_row}
)

# 2. Insert V0.1.6 closure summary section at top of 问题与版本归属总台账.md (before V0.1.5 section)
ledger_all_name = "doc/00_项目管理/问题与版本归属总台账.md"
anchor_v015 = "最终当前状态（2026-11-16）："
assert anchor_v015 in prepared[ledger_all_name]
v016_block = (
    "### V0.1.6 正式终审收口清单（2026-11-18，不覆盖下方 V0.1.5 历史）\n\n"
    "| 项目 | 级别 | 最终本版结论 | 后续范围/保护 |\n"
    "|---|---|---|---|\n"
    "| F05 | P1 | V016-M0 R03 范围关闭，已放行 | 保持身份/事件/投递契约及离线护栏回归 |\n"
    "| F06 / F27 | P1 / P2 | V016-M1 R01 范围关闭，已放行 | 保持聚合SQL/可信谓词与统一RuleContext |\n"
    "| F07 / F28 | P1 / P2 | V016-M2/M3/M4 全部审核通过并统筹放行，正式全量关闭 | 保持三源门禁/静态路由/LIFO栈顶回滚/Scoped导出过滤回归 |\n"
    "| F08 | P1 | V016-M3 R01 范围关闭，M4 镜像重建与双容器51步复验保持 | 严禁镜像带入tests/doc/DB/.env |\n"
    "| OBS-M3-01 / C-M4-07 | P2 | V016-M4 R01 审核与统筹抽查确认关闭 | 同快照只读向量缓存复制+120s轮询先断言终态 |\n"
    "| F20 / R-M3-01 | P2 | V016-M4 阶段轨迹诊断与candidate_recall完成（3例防爆unknown维持recommended与422拦截） | 最终置信度校准与策略闭合维持V019-M0/M2原期限开放 |\n"
    "| AUDIT-SIDE-01～04 / ENV-M4-01 | 流程/环境 | 非产品缺陷，均已签认处置关闭（原始损失/废弃日志永久留痕） | Windows显式仓库外原生`--basetemp`、全套10路径env注入、禁覆写他方输出 |\n"
    "| OBS-AUDIT-M4-01 | P3 | 审核静态观察开放：load_scoped_aliases 空字符串 src 守卫 | 当前无该文件、方向fail-closed，留待后续规则治理顺带补 `if src:` |\n"
    "| PRG-F11 | P1 | V0.1.5 条件边界保持，未随V0.1.6关闭 | 首次真实部署前强制完成凭据轮换与独立核验，未批准生产发布 |\n\n"
)
prepared[ledger_all_name] = prepared[ledger_all_name].replace(
    anchor_v015, v016_block + anchor_v015, 1
)
changes.append(
    {"file": ledger_all_name, "old": "(insert V0.1.6 closure block before 2026-11-16 block)", "new": "V0.1.6 closure table added"}
)

# 3. Append ENV-M4-01 row to 02_任务台账.md right after AUDIT-SIDE-04
ledger_v016 = "doc/V0.1.6/02_任务台账.md"
l_lines = prepared[ledger_v016].splitlines()
a4_idx = next(i for i, ln in enumerate(l_lines) if ln.startswith("| AUDIT-SIDE-04 |"))
env_row = "| ENV-M4-01 | M4 R01开发自验 | 开发侧环境记录，非产品缺陷 | 独立审核三态复现确认并由统筹签认关闭 | [R01开发](rounds/V016-M4_R01_开发小结.md)、[R01审核](rounds/V016-M4_R01_审核小结.md)第5节、`evidence/audit_m4_r01/audit_m4_env_repro.json`；仓库内basetemp使`python -c`子进程dotenv祖先发现`.env`，仓库外或禁用dotenv均fail-closed拒绝缺SECRET_KEY（零密钥打印） | 认证逻辑`auth_manager.py:147-153`本身正确，未修改实现或降断言；后续固定使用仓库外原生路径+`PYTHON_DOTENV_DISABLED=1` |"
l_lines.insert(a4_idx + 1, env_row)
prepared[ledger_v016] = "\n".join(l_lines) + "\n"
changes.append(
    {"file": ledger_v016, "old": "(insertion after AUDIT-SIDE-04)", "new": env_row}
)

# 4. Update 03_终审与版本准入.md (replace top '未开展终审' header AND append V0.1.6 formal final audit section)
final_name = "doc/V0.1.6/03_终审与版本准入.md"
final_raw = (ROOT / final_name).read_text(encoding="utf-8-sig")
old_header = """# V0.1.6 终审与版本准入

- 当前结论：未开展终审。
- 终审人：统筹AI（待执行）。
- 交付基线/独立抽查/证据：尚未产生。
- 本版任务审核：尚未完成。
- 未闭合问题：参考问题总台账；不得默认归零。
- 准入裁决：**尚未批准进入V0.1.7。**
- 发布状态：未发布。

本文件不是通过证明。仅统筹AI完成真实终审后可以填写结论、依据、数据/模型/配置版本、回滚核验和下一版首个下发任务包；历史结论追加留痕。"""

new_header = """# V0.1.6 终审与版本准入

- 当前结论：**2026-11-18 正式终审通过（限已批准的开发/实现验收范围），V016-M4 R01 正式放行。**
- 终审人：统筹AI（本聊天，仅统筹）。
- 交付基线/独立抽查/证据：HEAD `e964fb16848de9dece8e729e801b61c7d81dd341` + 未提交工作区（121/122 交付与 6/6 改前备件核对一致，唯一变化为审核登记后的台账）；正式镜像 `listtrans:v016-m4-r01-local` / `sha256:9517cc2a941cf9eebb662f2c833cdf88046709131a98380410b3b8b7a877e1b3`；统筹实测跨包 **78 passed / 0 warnings in 138.58s, EXIT=0** 及前后业务零变化，见[M4放行与终审裁决](rounds/V016-M4_R01_统筹裁决.md)、[evidence_binding.json](evidence/coord_m4_r01/evidence_binding.json)与[targeted_pytest_local.log](evidence/coord_m4_r01/targeted_pytest_local.log)。
- 本版任务审核：M0（R03）、M1（R01）、M2（R01）、M3（R01）、M4（R01）共 5 个任务包全部经独立审核通过并由统筹正式放行；早期 M0 R02 不放行记录保留历史。
- 未闭合问题：已批准实现验收范围内未闭合 **P0/P1 为 0**（F05/F06/F07/F08/F27/F28 与 OBS-M3-01/C-M4-07 全部关闭）；F20 与 R-M3-01 完成 M4 阶段诊断（最终策略闭合仍归 V019-M0/M2）；OBS-AUDIT-M4-01（P3 空 src 守卫）非阻断开放；PRG-F11 首次真实部署前强制门禁继续开放。
- 准入裁决：**本次不自动启动 V0.1.7；V0.1.7～V0.2.0 继续维持锁定，下一候选首包 V017-M0 尚未下发**（须用户明确指令并由统筹另行执行 V0.1.7 准入裁决与首包下发）。
- 发布状态：未发布（未批准生产发布）。

本文件当前段保留统筹实际正式终审结论与边界；下方各包阶段放行记录保留各自历史事实，不覆写历史。"""

assert old_header in final_raw, "Expected top header not found in 03_终审与版本准入.md"
assert "## 2026-11-18 M4 R01正式放行" not in final_raw

append_v016_final = """

## 2026-11-18 M4 R01正式放行与V0.1.6版本正式终审通过

依据[V016-M4 R01统筹裁决](rounds/V016-M4_R01_统筹裁决.md)。独立审核[M4审核小结](rounds/V016-M4_R01_审核小结.md)实测自编反例 16/16、专项 16/16（45.08s）、空缓存冷启动 4/4（39.13s）、原生`--basetemp`全量 373/373（748.07s, EXIT=0）、6 历史基线三规则全 0 diff 与`GT-0674/0677/0678`全`recommended`、ENV-M4-01 三态一致、正式镜像 12 层 57 应用文件零违规及双断网容器各 51 项 HTTP 闭环（零 5xx）；统筹另独立完成 121/122 交付与 6/6 改前备件核对、只读源码 diff 审查、只读 SQLite 零污染确认、双方门禁/镜像/容器报告交叉核对，并实跑跨包 11 文件 **78 passed / 0 warnings in 138.58s, EXIT=0**（含`test_v0_0_8_llm_rules` 4/4 通过）、`verify_isolation.py` **PASS/0** 与 35 项外层业务快照零变化。未将 373 全量或容器重跑记为统筹亲自重跑。

| 任务包 | 接受的审核/统筹依据 | 本版裁定 |
|---|---|---|
| M0 / R03 | [R03审核](rounds/V016-M0_R03_审核小结.md)、[R03统筹放行](rounds/V016-M0_R03_统筹裁决.md) | 正式通过；F05（身份/事件/投递契约）、AUDIT-01～07 与 C-M0-08/09 闭环；保留 R02 退回历史 |
| M1 / R01 | [R01审核](rounds/V016-M1_R01_审核小结.md)、[R01统筹放行](rounds/V016-M1_R01_统筹裁决.md) | 正式通过；F06（聚合SQL/可信读者）与 F27（RuleContext）闭环；AUDIT-SIDE-01 已签认处置 |
| M2 / R01 | [R01审核](rounds/V016-M2_R01_审核小结.md)、[R01统筹放行](rounds/V016-M2_R01_统筹裁决.md) | 正式通过；F07 发布强制门禁、三源统一治理与绑定真实评测闭环；AUDIT-SIDE-02 新业务快照基线签认 |
| M3 / R01 | [R01审核](rounds/V016-M3_R01_审核小结.md)、[R01统筹放行](rounds/V016-M3_R01_统筹裁决.md) | 正式通过；F08 正式镜像评测资产与 F28（含路由优先级）正式关闭；AUDIT-SIDE-03 签认关闭 |
| M4 / R01 | [R01审核](rounds/V016-M4_R01_审核小结.md)、[R01统筹放行与终审](rounds/V016-M4_R01_统筹裁决.md) | 正式通过；F07 完整闭环、OBS-M3-01/C-M4-07 正式关闭，F20/R-M3-01 达成 M4 阶段诊断，容器全闭环通过 |

- **数据、模型、配置与镜像绑定**：HEAD `e964fb16848de9dece8e729e801b61c7d81dd341` + 未提交工作区（不可仅凭 HEAD 复原）；绑定最终正式镜像 `listtrans:v016-m4-r01-local`（`sha256:9517cc2a941cf9eebb662f2c833cdf88046709131a98380410b3b8b7a877e1b3`，12 层、57 应用文件零失配、`app/eval_assets/` 3 脱敏 JSON）；43 项离线 `bge-small-zh-v1.5` 与测试夹具零变化；外层 35 项业务资产与 M3/M2 签认基线完全一致（`materials.db` `d2ac87ff...`、`auth.db` `8b16c626...`、16 份配置冻结、无真实数据迁移）。收尾核验见 [state_final.json](evidence/coord_m4_r01/state_final.json)、[completion_checks.json](evidence/coord_m4_r01/completion_checks.json) 与 [coordinator_delivery.json](evidence/coord_m4_r01/coordinator_delivery.json)。
- **数据兼容、热加载与回滚核验**：M4 新增 `v016-m4-file-v1` 快照封装保留 `existed` 状态并兼容旧裸内容快照；越级指定非栈顶快照强制 `409 Conflict` 且配置/DB/内存零篡改，按 LIFO 逐级回滚精确恢复 `scoped_aliases.json`（原本不存在则删除）、`auto_binding_rules.json`、`is_active`/`rolled_back` 及 `config_digest`。若将代码回退至 M3 及更早版本，须先按 LIFO 回滚所有新格式激活快照。
- **指标口径**：本版为可信反馈与评测发布闭环功能与安全门禁验收，未改动排序权重、阈值或金标真值；`candidate_recall` 与五阶段分解、失败/跳过真实计数用于阶段归因诊断，不宣称算法命中率提升；`V0.1.7` 数据基准与性能预算指标须在 `V0.1.7` 内由统筹另行签认。
- **准入与发布裁决**：**V0.1.6 已批准实现验收范围正式终审通过；本轮不解锁 V0.1.7～V0.2.0，下一候选首包 V017-M0 尚未下发；PRG-F11 继续开放，未批准生产发布。**
"""

prepared[final_name] = final_raw.replace(old_header, new_header, 1).rstrip() + append_v016_final
changes.append({"file": final_name, "old": "(replace top header + append V0.1.6 formal final audit)", "new": "V0.1.6 正式终审通过"})

for name, text in prepared.items():
    (ROOT / name).write_text(text, encoding="utf-8")

(OUT / "management_changes.json").write_text(
    json.dumps(
        {"files": list(prepared), "line_changes": changes}, ensure_ascii=False, indent=2
    ),
    encoding="utf-8",
)
print(f"Updated {len(prepared)} management files; {len(changes)} recorded transitions.")
