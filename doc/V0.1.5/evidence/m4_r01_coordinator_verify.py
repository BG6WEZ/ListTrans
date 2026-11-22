r"""Read-only verification of M4 R01 issuance; writes only its evidence reports.

Command: .\venv\Scripts\python.exe doc/V0.1.5/evidence/m4_r01_coordinator_verify.py
No application imports, application tests, deployment or Git mutation.
"""
from pathlib import Path
import hashlib
import json
import re
import runpy
import subprocess


ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "doc/V0.1.5/evidence"
PREFIX = "m4_r01_coordinator_"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = json.loads((EVIDENCE / (PREFIX + "baseline.json")).read_text(encoding="utf-8"))
    changes = [p for p, expected in baseline["protected_file_hashes"].items()
               if not (ROOT / p).is_file() or digest(ROOT / p) != expected]
    if "tests/verify_isolation.py" in changes:
        raise RuntimeError("Snapshot helper changed; inspect before running it")
    # run_path only defines stdlib helpers; __main__ and application imports do not run.
    snapshot = runpy.run_path(str(ROOT / "tests/verify_isolation.py"))["collect_snapshot"]()
    old_bytes = (EVIDENCE / (PREFIX + "snapshot_before.json")).read_bytes()
    old_snapshot = json.loads(old_bytes)
    snapshot_equal = snapshot == old_snapshot
    stable = [row for row in baseline["m3_r04_delivery_checks"]
              if row["actual"] == row["expected"]]
    stable_changes = [row["path"] for row in stable
                      if digest(ROOT / row["path"]) != row["actual"]]
    backup_changes = [row["path"] for row in baseline["backup_checks"]
                      if digest(ROOT / row["path"]) != row["expected"]]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    docs = [
        "doc/README.md", "doc/00_项目管理/版本路线图.md",
        "doc/00_项目管理/进度总表.md", "doc/00_项目管理/问题与版本归属总台账.md",
        "doc/V0.1.5/01_实施方案与任务包.md", "doc/V0.1.5/02_任务台账.md",
        "doc/V0.1.5/03_终审与版本准入.md", "doc/V0.1.5/rounds/README.md",
        "doc/V0.1.5/evidence/README.md", "doc/V0.1.5/rounds/V015-M4_R01_统筹下发.md",
    ]
    closure = {
        "operation": "M4 R01 coordinator issuance only",
        "command": r".\venv\Scripts\python.exe doc/V0.1.5/evidence/m4_r01_coordinator_verify.py",
        "application_tests_run": False,
        "head": head, "head_unchanged": head == baseline["head"],
        "protected_files_checked": len(baseline["protected_file_hashes"]),
        "protected_file_changes": changes,
        "m3_fixed_delivery_checked": len(stable), "m3_fixed_delivery_changes": stable_changes,
        "m3_mutable_documents_previously_updated": [r["path"] for r in baseline["m3_r04_delivery_checks"]
                                                     if r["actual"] != r["expected"]],
        "backups_checked": len(baseline["backup_checks"]), "backup_changes": backup_changes,
        "business_snapshot_object_equal": snapshot_equal,
        "business_snapshot_sha256": hashlib.sha256(old_bytes).hexdigest() if snapshot_equal else None,
        "m4_implementation_status": "issued; awaiting development delivery and independent audit",
        "future_packages": "M5 unissued; V0.1.6-V0.2.0 locked; no final review or release",
    }
    closure_path = EVIDENCE / (PREFIX + "closure.json")
    manifest_path = EVIDENCE / (PREFIX + "delivery_hashes.md")
    artifacts = docs + [str(p.relative_to(ROOT)).replace("\\", "/") for p in sorted(EVIDENCE.glob(PREFIX + "*"))
                        if p != manifest_path and p != closure_path]
    artifacts += [str(closure_path.relative_to(ROOT)).replace("\\", "/")]

    def write_reports():
        closure_path.write_text(json.dumps(closure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# V015-M4 R01 统筹下发交付SHA256", "",
                 "仅任务下发与只读核验，不是M4开发或审核通过证明。", "",
                 "HEAD: `" + head + "`；本轮未执行Git写操作。", "",
                 "核验命令：`.\\venv\\Scripts\\python.exe doc/V0.1.5/evidence/m4_r01_coordinator_verify.py`。", "",
                 "清单不包含自身，原轮次证据保持不变。", "",
                 "| 文件 | SHA256 |", "|---|---|"]
        lines += ["| `" + p + "` | `" + digest(ROOT / p) + "` |" for p in artifacts]
        manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    write_reports()
    broken = []
    link_count = 0
    for name in docs:
        path = ROOT / name
        for link in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8-sig")):
            if "m4_r01" not in link.lower() and "V015-M4_R01" not in link:
                continue
            link_count += 1
            if not (path.parent / link.split("#")[0]).is_file():
                broken.append({"document": name, "target": link})
    closure["new_task_links_checked"] = link_count
    closure["broken_new_task_links"] = broken
    passed = (head == baseline["head"] and not changes and not stable_changes
              and not backup_changes and snapshot_equal and not broken)
    closure["verification"] = "PASS" if passed else "FAIL"
    write_reports()
    print(json.dumps({"verification": closure["verification"], "protected_files": len(baseline["protected_file_hashes"]),
                      "fixed_m3_delivery": len(stable), "backups": len(baseline["backup_checks"]),
                      "snapshot_equal": snapshot_equal, "links_checked": link_count,
                      "broken_links": len(broken), "delivery_files": len(artifacts)}, ensure_ascii=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
