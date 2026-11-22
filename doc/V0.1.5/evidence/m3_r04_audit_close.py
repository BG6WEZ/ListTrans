"""Final reviewer/coordinator delivery binding; no application implementation writes."""
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess

EV=Path(__file__).resolve().parent
ROOT=EV.parents[2]
h=runpy.run_path(str(EV/"m3_r04_audit_runner.py"))
main=json.loads((EV/"m3_r04_audit_summary.json").read_text(encoding="utf-8"))
extra=json.loads((EV/"m3_r04_audit_extra_summary.json").read_text(encoding="utf-8"))
hashes=h["hashes"]()
live_docs={"doc/V0.1.5/02_任务台账.md","doc/V0.1.5/rounds/README.md","doc/V0.1.5/evidence/README.md"}
unexpected=[r for r in hashes if not r["matches"] and r["file"] not in live_docs]
assert not unexpected,unexpected
snap=h["snapshot"]("closure")
snapshots=[main["snapshot_before"],main["snapshot_after"],extra["snapshot_before"],extra["snapshot_after"],snap]
assert all(s["sha256"]=="5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e" for s in snapshots)
assert all(not any(s["golden_paths"].values()) for s in snapshots)
backups=[]
for line in (EV/"m3_r04_delivery_hashes.md").read_text(encoding="utf-8").splitlines():
    if not line.startswith("| "):
        continue
    cells=[c.strip().strip("`") for c in line.split("|")[1:-1]]
    if len(cells)==4 and re.fullmatch(r"[0-9a-f]{64}",cells[1]) and re.fullmatch(r"[0-9a-f]{64}",cells[2]):
        old=ROOT/".local-archive/m3_r04_pre_backup"/cells[0]
        backups.append(dict(file=cells[0],expected=cells[1],actual=h["sha"](old),matches=h["sha"](old)==cells[1]))
assert len(backups)==4 and all(b["matches"] for b in backups),backups
changed={r["file"] for r in main["backups"] if r["changed"]}
protected_r03=[]
for line in (EV/"m3_r03_delivery_hashes.md").read_text(encoding="utf-8").splitlines():
    if not line.startswith("| "):
        continue
    cells=[c.strip().strip("`") for c in line.split("|")[1:-1]]
    hs=re.findall(r"\b[0-9a-f]{64}\b"," ".join(cells[1:]))
    if not hs or cells[0] in changed|live_docs:
        continue
    p=ROOT/cells[0] if "/" in cells[0] else EV/cells[0]
    protected_r03.append(dict(file=p.relative_to(ROOT).as_posix(),expected=hs[-1],actual=h["sha"](p),matches=h["sha"](p)==hs[-1]))
assert all(r["matches"] for r in protected_r03),[r for r in protected_r03 if not r["matches"]]
old_closure=json.loads((EV/"m3_r03_audit_closure.json").read_text(encoding="utf-8"))
protected_inputs=[]
for old in old_closure["protected_R02_checks"]:
    p=ROOT/old["file"]
    protected_inputs.append(dict(file=old["file"],expected=old["expected"],actual=h["sha"](p),matches=h["sha"](p)==old["expected"]))
assert all(r["matches"] for r in protected_inputs)
legacy=json.loads((EV/"m3_r04_audit_legacy_replay.json").read_text(encoding="utf-8"))
probes=json.loads((EV/"m3_r04_audit_probes.json").read_text(encoding="utf-8"))
assert legacy["restore"]["items"][0]["decision_status"]=="recommended" and legacy["export_http"]==422
assert len(probes["matrix"])==13 and all(r["restore"]["body"]["items"][0]["decision_status"]=="recommended" and r["export"]["http"]==422 for r in probes["matrix"])
assert probes["reconfirmed"]["export"]["http"]==200 and probes["one_to_many_ratio"]["export"]["http"]==200
docs=["doc/README.md","doc/00_项目管理/版本路线图.md","doc/00_项目管理/进度总表.md",
      "doc/00_项目管理/问题与版本归属总台账.md","doc/V0.1.5/01_实施方案与任务包.md","doc/V0.1.5/02_任务台账.md",
      "doc/V0.1.5/03_终审与版本准入.md","doc/V0.1.5/rounds/README.md","doc/V0.1.5/evidence/README.md",
      "doc/V0.1.5/rounds/V015-M3_R04_审核小结.md","doc/V0.1.5/rounds/V015-M3_R04_统筹裁决.md"]
closure=dict(date="2026-11-15 Asia/Shanghai",baseline=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
             verdict="通过",coordinator="V015-M3 formally released; M4/M5 not issued",open_package_blockers=[],
             closed=["A-M3-01","A-M3-02","A-M3-03","A-M3-04","A-M3-05"],
             deferred_P2=["R-M3-01","UI-M5-01","COMPAT-M5-01","DEP-M5-01"],
             initial_delivery=dict(matched=sum(r["matches"] for r in main["hashes_before"]),total=len(main["hashes_before"])),
             stable_delivery_matches=sum(r["matches"] for r in hashes),expected_document_changes=[r for r in hashes if not r["matches"]],
             unexpected_changes=unexpected,backup_checks=backups,protected_R03_checks=protected_r03,protected_inputs=protected_inputs,
             snapshots=snapshots,execution=dict(main=main["steps"],extra=extra["steps"],strict_compile="m3_r04_audit_compile.log EXIT=0"),
             evidence=dict(legacy_restore="recommended",legacy_export=422,negative_matrix="13/13 rejected",reconfirmed_export=200,
                           original_unit_qty=["km",10],target_unit_qty=["m",10000]),
             compatibility_decision="R03 server-only core evidence format accepted with self-consistency checks; legacy client unit alone rejected; optional tags must match if present",
             boundaries="No app/test implementation or git writes; no real migration, later package execution, version final approval or production release")
(EV/"m3_r04_audit_closure.json").write_text(json.dumps(closure,ensure_ascii=False,indent=2),encoding="utf-8")
for doc in docs[-2:]:
    p=ROOT/doc
    for link in re.findall(r"\]\(([^)]+)\)",p.read_text(encoding="utf-8")):
        if "://" not in link and not link.startswith("#"):
            assert (p.parent/link.split("#")[0]).resolve().is_file(),(doc,link)
manifest=EV/"m3_r04_audit_delivery_hashes.md"
files=[ROOT/p for p in docs]+sorted(p for p in EV.glob("m3_r04_audit*") if p.is_file() and p!=manifest)
lines=["# V015-M3 R04 审核/统筹交付文件SHA256","",
       "日期：2026-11-15（Asia/Shanghai）；HEAD `"+closure["baseline"]+"`，未执行Git写操作。",
       "审核通过，M3整包放行；M4/M5未下发，版本终审未开展、未发布。",
       "本轮仅维护11个管理文档并新增审核证据/脚本，不修改业务或正式测试实现。",
       "开发交付初始27/27相符，收尾24个稳定文件相符，3个活文档因审核维护而变化；4份备份相符。",
       "保护输入、旧格式拒绝、13组负例、重确认正例与业务快照核验见closure。原记录未覆写。",
       "本清单不做自引用哈希；路径相对项目根。","","| 文件 | SHA256 |","|---|---|"]
for p in files:
    lines.append(f"| {p.relative_to(ROOT).as_posix()} | {hashlib.sha256(p.read_bytes()).hexdigest()} |")
manifest.write_text("\n".join(lines)+"\n",encoding="utf-8")
print(json.dumps(dict(initial=closure["initial_delivery"],stable_matches=closure["stable_delivery_matches"],backups=len(backups),
                      protected_R03=len(protected_r03),protected_inputs=len(protected_inputs),unexpected_changes=unexpected,
                      snapshot=snap["sha256"],manifest_files=len(files)),ensure_ascii=False))
