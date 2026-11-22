"""Bind reviewer evidence, source freeze, backup hashes, and final business snapshot."""
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess

EV=Path(__file__).resolve().parent
ROOT=EV.parents[2]
h=runpy.run_path(str(EV/"m3_r02_audit_runner.py"))
main=json.loads((EV/"m3_r02_audit_summary.json").read_text(encoding="utf-8"))
extra=json.loads((EV/"m3_r02_audit_extra_summary.json").read_text(encoding="utf-8"))
probes=json.loads((EV/"m3_r02_audit_probes.json").read_text(encoding="utf-8"))
hashes=h["hashes"]()
expected_docs={"doc/V0.1.5/02_任务台账.md","doc/V0.1.5/rounds/README.md","doc/V0.1.5/evidence/README.md"}
unexpected=[r for r in hashes if not r["matches"] and r["file"] not in expected_docs]
assert not unexpected,unexpected
snapshot=h["snapshot"]("closure")
snapshots=[main["snapshot_before"],main["snapshot_after"],extra["snapshot_before"],extra["snapshot_after"],snapshot]
assert all(s["sha256"]=="5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e" for s in snapshots)
assert all(not any(s["golden_paths"].values()) for s in snapshots)
backups=[]
for line in (EV/"m3_r02_delivery_hashes.md").read_text(encoding="utf-8").splitlines():
    if not line.startswith("| "):
        continue
    cells=[c.strip().strip("`") for c in line.split("|")[1:-1]]
    if len(cells)!=5 or not re.fullmatch(r"[0-9a-f]{64}",cells[2]):
        continue
    old=ROOT/".local-archive/m3_r02_pre_backup"/cells[0]
    backups.append(dict(file=cells[0],expected=cells[2],actual=h["sha"](old),matches=h["sha"](old)==cells[2]))
assert len(backups)==8 and all(b["matches"] for b in backups),backups
docs=["doc/README.md","doc/00_项目管理/版本路线图.md","doc/00_项目管理/进度总表.md",
      "doc/00_项目管理/问题与版本归属总台账.md","doc/V0.1.5/01_实施方案与任务包.md",
      "doc/V0.1.5/02_任务台账.md","doc/V0.1.5/03_终审与版本准入.md",
      "doc/V0.1.5/rounds/README.md","doc/V0.1.5/evidence/README.md",
      "doc/V0.1.5/rounds/V015-M3_R02_审核小结.md","doc/V0.1.5/rounds/V015-M3_R02_统筹裁决.md"]
closure=dict(baseline=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
             verdict="退回整改",next_package="V015-M3 R03 only",closed=["A-M3-03","A-M3-04"],
             open_P1=["A-M3-01","A-M3-02"],new_blocking_P2=["A-M3-05"],deferred_P2="R-M3-01 unchanged",
             initial_delivery=dict(matched=sum(r["matches"] for r in main["hashes_before"]),total=len(main["hashes_before"])),
             stable_hashes_after=sum(r["matches"] for r in hashes),expected_live_document_changes=[r for r in hashes if not r["matches"]],
             unexpected_changes=unexpected,backup_checks=backups,snapshots=snapshots,
             execution=dict(main=main["steps"],extra=extra["steps"],environment="approved local execution outside socketpair-limited sandbox; writable app paths isolated; offline; LLM false"),
             probe_cases=[r["case"] for r in probes["results"]],
             boundaries="No app/test implementation changes; no git writes, deployment, real data migration, or later packages executed")
(EV/"m3_r02_audit_closure.json").write_text(json.dumps(closure,ensure_ascii=False,indent=2),encoding="utf-8")
for doc in docs[-2:]:
    p=ROOT/doc
    for link in re.findall(r"\]\(([^)]+)\)",p.read_text(encoding="utf-8")):
        if "://" not in link and not link.startswith("#"):
            assert (p.parent/link.split("#")[0]).resolve().is_file(),(doc,link)
manifest=EV/"m3_r02_audit_delivery_hashes.md"
files=[ROOT/p for p in docs]+sorted(p for p in EV.glob("m3_r02_audit*") if p.is_file() and p!=manifest)
lines=["# V015-M3 R02 审核/统筹交付文件SHA256","",
       "日期：2026-11-14（Asia/Shanghai）；HEAD `"+closure["baseline"]+"`。未执行Git写操作。",
       "本轮仅维护11个管理文档并新增审核证据/脚本，不修改业务或正式测试实现。",
       "开发交付初始50/50相符，收尾47个稳定文件相符；3个活文档因审核维护而变化。8个R01完整内容备份全部相符。",
       "233项全量通过不代表本包验收通过；13组观察中2项P1和1项当前验收P2阻断。旧记录全部保留。",
       "本清单不做自引用哈希；路径相对项目根。","","| 文件 | SHA256 |","|---|---|"]
for p in files:
    lines.append(f"| {p.relative_to(ROOT).as_posix()} | {hashlib.sha256(p.read_bytes()).hexdigest()} |")
manifest.write_text("\n".join(lines)+"\n",encoding="utf-8")
print(json.dumps(dict(initial=closure["initial_delivery"],stable_matches=closure["stable_hashes_after"],
                      backups=len(backups),unexpected_changes=unexpected,snapshot=snapshot["sha256"],manifest_files=len(files)),ensure_ascii=False))
