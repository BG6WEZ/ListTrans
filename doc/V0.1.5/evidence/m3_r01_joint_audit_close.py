"""Read-only closing verification plus exact reviewer artifact manifest."""
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess

EV = Path(__file__).resolve().parent
ROOT = EV.parents[2]
h = runpy.run_path(str(EV / "m3_r01_joint_audit_runner.py"))
main = json.loads((EV / "m3_r01_joint_audit2_summary.json").read_text(encoding="utf-8"))
extra = json.loads((EV / "m3_r01_joint_audit2_extra_summary.json").read_text(encoding="utf-8"))
probes = json.loads((EV / "m3_r01_joint_audit2_rule_probes.json").read_text(encoding="utf-8"))
snap = h["snapshot"]("after_rules")
current_hashes = h["hashes"]()
live_doc_paths = {
    "doc/V0.1.5/02_任务台账.md", "doc/V0.1.5/rounds/README.md", "doc/V0.1.5/evidence/README.md",
}
unexpected = [r for r in current_hashes if not r["matches"] and r["file"].replace("\\", "/") not in live_doc_paths]
assert not unexpected, unexpected
assert snap["sha256"] == main["snapshot_before"]["sha256"]
assert all(not value for value in snap["golden_paths"].values())
docs = [
    "doc/README.md", "doc/00_项目管理/版本路线图.md", "doc/00_项目管理/进度总表.md",
    "doc/00_项目管理/问题与版本归属总台账.md", "doc/V0.1.5/01_实施方案与任务包.md",
    "doc/V0.1.5/02_任务台账.md", "doc/V0.1.5/03_终审与版本准入.md",
    "doc/V0.1.5/rounds/README.md", "doc/V0.1.5/evidence/README.md",
    "doc/V0.1.5/rounds/V015-M3_R01_审核小结.md", "doc/V0.1.5/rounds/V015-M3_R01_统筹裁决.md",
]
for doc in docs[-2:]:
    p = ROOT / doc
    for target in re.findall(r"\]\(([^)]+)\)", p.read_text(encoding="utf-8")):
        if "://" not in target and not target.startswith("#"):
            # The closing JSON is written immediately below.
            if "m3_r01_joint_audit_closure.json" in target:
                continue
            assert (p.parent / target.split("#")[0]).resolve().is_file(), (doc, target)

closure = {
    "role": "user-authorized reviewer/coordinator, no implementation changes",
    "baseline": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "verdict": "退回整改", "next_package": "V015-M3 R02 only",
    "initial_delivery_hashes": {"matched": sum(r["matches"] for r in main["hashes_before"]), "total": len(main["hashes_before"])},
    "stable_hashes_after": {"matched": sum(r["matches"] for r in current_hashes),
                            "expected_live_document_changes": [r for r in current_hashes if not r["matches"]],
                            "unexpected_changes": unexpected},
    "snapshot_after_rules": snap,
    "all_snapshot_sha256": [main["snapshot_before"]["sha256"], main["snapshot_after"]["sha256"],
                            extra["snapshot_before_probes"]["sha256"], extra["snapshot_post_probes"]["sha256"], snap["sha256"]],
    "counterexample_matrix": [{"case": r["label"], "matcher": r["matcher"],
                              "confirm_http": r["confirm_status"], "manual_export_http": r["export_status"],
                              "rule_status": r["batch"]["decision_status"], "rule_export_http": r["auto_export_status"]}
                             for r in probes["results"]],
    "execution": {"main_runner": "venv/Scripts/python.exe -u doc/V0.1.5/evidence/m3_r01_joint_audit_runner.py",
                  "extra_runner": "venv/Scripts/python.exe -u doc/V0.1.5/evidence/m3_r01_joint_audit_finalize.py",
                  "rule_probes": "venv/Scripts/python.exe -u doc/V0.1.5/evidence/m3_r01_joint_audit_rule_probes.py",
                  "rule_probe_exit_observed": 0,
                  "rule_probe_log_encoding": "cp936, retained as original bytes; JSON evidence uses UTF-8",
                  "sandbox_attempts": "interrupted; stack showed asyncio socketpair accept blocking; not counted as passes",
                  "completed_execution_environment": "auto-reviewed require_escalated; all writable app paths still isolated; offline model; LLM false"},
    "risks": {"P1": ["A-M3-01", "A-M3-02", "A-M3-03", "A-M3-04"], "P2": ["R-M3-01"],
              "version_final_review": "not started", "M4_M5": "not issued", "V016_to_V020": "locked"},
}
(EV / "m3_r01_joint_audit_closure.json").write_text(json.dumps(closure, ensure_ascii=False, indent=2), encoding="utf-8")
manifest = EV / "m3_r01_joint_audit_delivery_hashes.md"
artifacts = sorted([p for p in EV.glob("m3_r01_joint*") if p.is_file() and p != manifest])
files = [ROOT / p for p in docs] + artifacts
assert len(files) == len(set(files))
lines = ["# V015-M3 R01 审核/统筹交付哈希", "", "日期：2026-11-14（Asia/Shanghai）。HEAD `" + closure["baseline"] + "`，未提交。",
         "", "本轮仅修改11个管理文档并新增下列审核证据/脚本，不改业务与正式测试实现。旧开发交付与未知来源审核日志保留。",
         "初始开发交付62/62哈希相符；收尾59项稳定文件相符，原清单中3个活文档随审核维护而改变，详细差异见closure。",
         "首轮受限环境中止不计通过；正式完成的检查为audit2汇总及其引用日志。反例观察脚本退出0不等于业务验收通过。",
         "本清单不做自引用哈希。所有文件路径相对项目根。", "", "| 文件 | SHA256 |", "|---|---|"]
for p in files:
    lines.append(f"| {p.relative_to(ROOT).as_posix()} | {hashlib.sha256(p.read_bytes()).hexdigest()} |")
manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({"initial_hashes": closure["initial_delivery_hashes"], "stable_matches_after": closure["stable_hashes_after"]["matched"],
                  "unexpected_changes": unexpected, "snapshot": snap["sha256"], "delivered_files": len(files), "documents": len(docs)}, ensure_ascii=False))
