"""Verify audit inputs remain frozen; write only audit closure/manifest/snapshot evidence."""
from pathlib import Path
import hashlib
import json
import re
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.5/evidence"
BASELINE = json.loads((EV / "m4_r01_audit_baseline.json").read_text(encoding="utf-8"))
DOCS = ["doc/README.md", "doc/00_项目管理/版本路线图.md", "doc/00_项目管理/进度总表.md",
        "doc/00_项目管理/问题与版本归属总台账.md", "doc/V0.1.5/01_实施方案与任务包.md",
        "doc/V0.1.5/02_任务台账.md", "doc/V0.1.5/03_终审与版本准入.md",
        "doc/V0.1.5/rounds/README.md", "doc/V0.1.5/evidence/README.md",
        "doc/V0.1.5/rounds/V015-M4_R01_审核小结.md", "doc/V0.1.5/rounds/V015-M4_R01_统筹裁决.md"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    changes = [p for p, h in BASELINE["protected_hashes"].items() if not (ROOT / p).exists() or sha(ROOT / p) != h]
    unexpected = [p for p in changes if p not in DOCS]
    delivery = [{"path": x["path"], "expected": x["actual"], "actual": sha(ROOT / x["path"])}
                for x in BASELINE["delivery_checks"]]
    delivery_changes = [x["path"] for x in delivery if x["actual"] != x["expected"]]
    unexpected_delivery = [p for p in delivery_changes if p not in DOCS]
    if "tests/verify_isolation.py" in changes:
        raise RuntimeError("Snapshot helper changed; inspect before executing")
    before_bytes = (EV / "m4_r01_audit_snapshot_before.json").read_bytes()
    before = json.loads(before_bytes)
    after = runpy.run_path(str(ROOT / "tests/verify_isolation.py"))["collect_snapshot"]()
    snapshot_equal = before == after
    after_bytes = before_bytes if snapshot_equal else json.dumps(after, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
    (EV / "m4_r01_audit_snapshot_after.json").write_bytes(after_bytes)
    verifies = {}
    for label in ("before", "after"):
        v = json.loads((EV / ("m4_r01_audit_verify_" + label + ".json")).read_text(encoding="utf-8-sig"))
        verifies[label] = {k: v.get(k) for k in ("verdict", "exit_code", "business_hash_unchanged", "business_changes", "hard_errors")}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    goldens = {p: (ROOT / p).exists() for p in ("tests/fixtures/golden_last_run.json", "data/golden_last_run.json")}
    closure = {
        "role": "independent auditor and coordinator; no implementation changes",
        "audit_conclusion": "RETURN_FOR_REWORK", "head": head,
        "head_unchanged": head == BASELINE["head"], "protected_inputs_checked": len(BASELINE["protected_hashes"]),
        "expected_active_document_changes": changes, "unexpected_protected_changes": unexpected,
        "development_delivery_checked": len(delivery), "development_delivery_checks": delivery,
        "expected_mutable_delivery_documents": delivery_changes, "unexpected_development_delivery_changes": unexpected_delivery,
        "business_snapshot_equal": snapshot_equal, "business_snapshot_before_sha256": hashlib.sha256(before_bytes).hexdigest(),
        "business_snapshot_after_sha256": hashlib.sha256(after_bytes).hexdigest(), "verify": verifies, "golden_last_run_exists": goldens,
        "open_findings": {"A-M4-01": "P1", "A-M4-02": "P2/current acceptance blocker", "A-M4-03": "P2/current acceptance blocker", "A-M4-04": "P1"},
        "pending": ["full production dependency image build/layer validation", "authorized actual credential rotation evidence"],
        "docker_scope": "two actual offline scratch builds using synthetic canaries only; not production image validation",
        "next_package": "V015-M4 R02 only; M5 and future versions locked",
    }
    closure_path = EV / "m4_r01_audit_closure.json"
    manifest_path = EV / "m4_r01_audit_delivery_hashes.md"

    def write():
        closure_path.write_text(json.dumps(closure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        artifacts = sorted(set(DOCS + [str(p.relative_to(ROOT)).replace("\\", "/") for p in EV.rglob("*")
                                      if p.is_file() and "m4_r01_audit_" in str(p.relative_to(EV))
                                      and p != manifest_path and "__pycache__" not in p.parts]))
        lines = ["# V015-M4 R01 独立审核与统筹裁决交付SHA256", "",
                 "结论：退回整改；仅下发同包R02，不放行M4/M5或后续版本。", "",
                 "HEAD: `" + head + "`。本轮未改实现/开发测试，未执行Git写操作。", "",
                 "命令：`.\\venv\\Scripts\\python.exe doc/V0.1.5/evidence/m4_r01_audit_close.py`。", "",
                 "清单不含自身；Docker tar仅含合成canary，不含真实账号/秘密。", "",
                 "| 文件 | SHA256 |", "|---|---|"]
        lines += ["| `" + p + "` | `" + sha(ROOT / p) + "` |" for p in artifacts]
        manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return len(artifacts)

    write()
    links = []
    for doc in DOCS:
        p = ROOT / doc
        for target in re.findall(r"\]\(([^)]+)\)", p.read_text(encoding="utf-8-sig")):
            if "m4_r01" not in target.lower() and "V015-M4_R01" not in target:
                continue
            if not (p.parent / target.split("#")[0]).is_file():
                links.append({"document": doc, "missing_target": target})
    closure["broken_current_task_links"] = links
    passed = not unexpected and not unexpected_delivery and snapshot_equal and head == BASELINE["head"] and not links
    passed = passed and all(v["verdict"] == "PASS" and v["exit_code"] == 0 for v in verifies.values()) and not any(goldens.values())
    closure["integrity_verdict"] = "PASS" if passed else "FAIL"
    total = write()
    print(json.dumps({"integrity": closure["integrity_verdict"], "audit": closure["audit_conclusion"],
                      "unexpected_source_changes": unexpected, "delivery_changed": delivery_changes,
                      "snapshot_equal": snapshot_equal, "broken_links": links, "artifacts": total}, ensure_ascii=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
