"""Final source/protected-input freeze and complete reviewer artifact manifest."""
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess

EV=Path(__file__).resolve().parent
ROOT=EV.parents[2]
h=runpy.run_path(str(EV/"m3_r03_audit_runner.py"))
main=json.loads((EV/"m3_r03_audit_summary.json").read_text(encoding="utf-8"))
extra=json.loads((EV/"m3_r03_audit_extra_summary.json").read_text(encoding="utf-8"))
hashes=h["hashes"]()
live_docs={"doc/V0.1.5/02_任务台账.md","doc/V0.1.5/rounds/README.md","doc/V0.1.5/evidence/README.md"}
unexpected=[r for r in hashes if not r["matches"] and r["file"] not in live_docs]
assert not unexpected,unexpected
snap=h["snapshot"]("closure")
snapshots=[main["snapshot_before"],main["snapshot_after"],extra["snapshot_before"],extra["snapshot_after"],snap]
assert all(s["sha256"]=="5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e" for s in snapshots)
assert all(not any(s["golden_paths"].values()) for s in snapshots)
backups=[]
for line in (EV/"m3_r03_delivery_hashes.md").read_text(encoding="utf-8").splitlines():
    if not line.startswith("| "):
        continue
    cells=[c.strip().strip("`") for c in line.split("|")[1:-1]]
    if len(cells)==4 and re.fullmatch(r"[0-9a-f]{64}",cells[1]) and re.fullmatch(r"[0-9a-f]{64}",cells[2]):
        old=ROOT/".local-archive/m3_r03_pre_backup"/cells[0]
        backups.append(dict(file=cells[0],expected=cells[1],actual=h["sha"](old),matches=h["sha"](old)==cells[1]))
assert len(backups)==9 and all(b["matches"] for b in backups),backups
changed={r["file"] for r in main["backups"] if r["changed"]}
protected=[]
for line in (EV/"m3_r02_delivery_hashes.md").read_text(encoding="utf-8").splitlines():
    if not line.startswith("| "):
        continue
    cells=[c.strip().strip("`") for c in line.split("|")[1:-1]]
    hs=re.findall(r"\b[0-9a-f]{64}\b"," ".join(cells[1:]))
    if not hs or cells[0] in changed|live_docs:
        continue
    p=ROOT/cells[0] if "/" in cells[0] else EV/cells[0]
    protected.append(dict(file=p.relative_to(ROOT).as_posix(),expected=hs[-1],actual=h["sha"](p),matches=h["sha"](p)==hs[-1]))
assert all(r["matches"] for r in protected),[r for r in protected if not r["matches"]]
docs=["doc/README.md","doc/00_项目管理/版本路线图.md","doc/00_项目管理/进度总表.md",
      "doc/00_项目管理/问题与版本归属总台账.md","doc/V0.1.5/01_实施方案与任务包.md","doc/V0.1.5/02_任务台账.md",
      "doc/V0.1.5/03_终审与版本准入.md","doc/V0.1.5/rounds/README.md","doc/V0.1.5/evidence/README.md",
      "doc/V0.1.5/rounds/V015-M3_R03_审核小结.md","doc/V0.1.5/rounds/V015-M3_R03_统筹裁决.md"]
closure=dict(date="2026-11-15 Asia/Shanghai",baseline=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
             verdict="退回整改",next_package="V015-M3 R04 only",open_P1=["A-M3-01 legacy client-unit provenance"],
             closed=["A-M3-02","A-M3-03","A-M3-04","A-M3-05"],deferred_P2="R-M3-01 unchanged",
             initial_delivery=dict(matched=sum(r["matches"] for r in main["hashes_before"]),total=len(main["hashes_before"])),
             stable_delivery_matches=sum(r["matches"] for r in hashes),expected_document_changes=[r for r in hashes if not r["matches"]],
             unexpected_changes=unexpected,backup_checks=backups,protected_R02_checks=protected,snapshots=snapshots,
             execution=dict(main=main["steps"],extra=extra["steps"],strict_compile="m3_r03_audit_compile.log EXIT=0",
                            legacy_replay="m3_r03_audit_legacy_replay.log; observed EXIT=0; supplemental old-format provenance plus current HTTP/XLSX",
                            environment="approved local HTTP outside socketpair-limited sandbox; app write paths fully isolated; offline model; LLM false"),
             boundaries="Only reviewer evidence and management documents changed; no app/test implementation or git writes; later packages not executed")
(EV/"m3_r03_audit_closure.json").write_text(json.dumps(closure,ensure_ascii=False,indent=2),encoding="utf-8")
for doc in docs[-2:]:
    p=ROOT/doc
    for link in re.findall(r"\]\(([^)]+)\)",p.read_text(encoding="utf-8")):
        if "://" not in link and not link.startswith("#"):
            assert (p.parent/link.split("#")[0]).resolve().is_file(),(doc,link)
manifest=EV/"m3_r03_audit_delivery_hashes.md"
files=[ROOT/p for p in docs]+sorted(p for p in EV.glob("m3_r03_audit*") if p.is_file() and p!=manifest)
lines=["# V015-M3 R03 审核/统筹交付文件SHA256","",
       "日期：2026-11-15（Asia/Shanghai）；HEAD `"+closure["baseline"]+"`，未执行Git写操作。",
       "本轮仅维护11个管理文档并新增审核证据/脚本，不修改业务或正式测试实现。",
       "开发交付初始32/32相符；收尾29个稳定文件相符，3个活文档随审核维护而变化；9份R02备份相符。",
       "另核验R02未涉及的保护文件（含金标、配置、隔离入口、回归与历史证据），结果见closure。",
       "241项全量通过不代替本包验收；历史非空客户端单位记录残余P1阻断。原开发/审核证据保留。",
       "本清单不做自引用哈希；路径相对项目根。","","| 文件 | SHA256 |","|---|---|"]
for p in files:
    lines.append(f"| {p.relative_to(ROOT).as_posix()} | {hashlib.sha256(p.read_bytes()).hexdigest()} |")
manifest.write_text("\n".join(lines)+"\n",encoding="utf-8")
print(json.dumps(dict(initial=closure["initial_delivery"],stable_matches=closure["stable_delivery_matches"],backups=len(backups),
                      protected=len(protected),unexpected_changes=unexpected,snapshot=snap["sha256"],manifest_files=len(files)),ensure_ascii=False))
