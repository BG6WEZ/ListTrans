"""Read-only frozen-input/business verification and supplemental evidence manifest."""
from pathlib import Path
import hashlib
import json
import re
import runpy
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.5/evidence"
DOCS = ["doc/README.md", "doc/00_项目管理/版本路线图.md", "doc/00_项目管理/进度总表.md",
        "doc/00_项目管理/问题与版本归属总台账.md", "doc/V0.1.5/01_实施方案与任务包.md",
        "doc/V0.1.5/02_任务台账.md", "doc/V0.1.5/03_终审与版本准入.md",
        "doc/V0.1.5/rounds/README.md", "doc/V0.1.5/evidence/README.md",
        "doc/V0.1.5/rounds/V015-M4_R03_V-M4-01_审核补证.md",
        "doc/V0.1.5/rounds/V015-M4_R03_V-M4-01_统筹补充裁决.md"]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    baseline = json.loads((EV / "m4_r03_vm401_audit_baseline.json").read_text(encoding="utf-8"))
    decision = json.loads((EV / "m4_r03_vm401_audit_decision.json").read_text(encoding="utf-8"))
    provided = json.loads((EV / "m4_r03_vm401_prod_image_verify.json").read_text(encoding="utf-8"))
    changed = [p for p, h in baseline["protected_hashes"].items() if not (ROOT / p).is_file() or sha(ROOT / p) != h]
    unexpected = [p for p in changed if p not in DOCS]
    frozen_source_changes = [p for p, h in provided["frozen_source_binding"]["source_hashes"].items() if sha(ROOT / p) != h]
    if "tests/verify_isolation.py" in unexpected:
        raise RuntimeError("Snapshot helper changed; inspect first")
    before_bytes = (EV / "m4_r03_vm401_audit_snapshot_before.json").read_bytes()
    snap = runpy.run_path(str(ROOT / "tests/verify_isolation.py"))["collect_snapshot"]()
    equal = snap == json.loads(before_bytes)
    after_bytes = before_bytes if equal else json.dumps(snap, indent=1, sort_keys=True).encode("utf-8")
    (EV / "m4_r03_vm401_audit_snapshot_after.json").write_bytes(after_bytes)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report = {"operation": "V-M4-01 supplement independent audit; implementation remains frozen",
              "head": head, "head_unchanged": head == baseline["head"], "V-M4-01": decision["verdict"],
              "V-M4-02": "PENDING_VERIFY", "F11": "OPEN", "M4": "PENDING_VERIFY",
              "image_id": decision["image_id"], "frozen_source_files": len(provided["frozen_source_binding"]["source_hashes"]),
              "frozen_source_changes": frozen_source_changes, "expected_active_document_updates": changed,
              "unexpected_protected_input_changes": unexpected,
              "initial_manifest_checks": baseline["manifest_checks"],
              "business_snapshot_equal": equal, "before_sha256": hashlib.sha256(before_bytes).hexdigest(),
              "after_sha256": hashlib.sha256(after_bytes).hexdigest(),
              "host_pytest_rerun": False, "host_application_or_real_credential_operations": False,
              "classification_decision": "Public CA/dependency code/fixed public test materials explicitly accounted; raw scanner retained"}

    # Remove only this audit's single large exported image, after binding config/rootfs.
    candidates = list(Path(tempfile.gettempdir()).glob("m4_vm401_independent_*/image.tar"))
    cleanup = []
    raw = json.loads((EV / "m4_r03_vm401_audit_result.json").read_text(encoding="utf-8"))
    if len(candidates) == 1 and decision["verdict"] == "PASS":
        p = candidates[0].resolve()
        owned_dir = p.parent
        assert owned_dir.parent == Path(tempfile.gettempdir()).resolve()
        assert owned_dir.name.startswith("m4_vm401_independent_") and p.name == "image.tar"
        with tarfile.open(p) as archive:
            manifest = json.load(archive.extractfile("manifest.json"))
            configs = [archive.extractfile(m["Config"]).read() for m in manifest]
            matched = [c for c in configs if hashlib.sha256(c).hexdigest() == raw["inspection"]["config_sha256"]]
            assert len(matched) == 1
            assert json.loads(matched[0])["rootfs"]["diff_ids"] == [x["diff_id"] for x in raw["inspection"]["layers"]]
        p.unlink()
        if not any(owned_dir.iterdir()):
            owned_dir.rmdir()
        cleanup.append({"path": str(p), "result": "removed own verified temporary export only; supplied image retained"})
    report["temporary_export_cleanup"] = cleanup
    closure = EV / "m4_r03_vm401_audit_closure.json"
    manifest_path = EV / "m4_r03_vm401_audit_delivery_hashes.md"

    def write():
        closure.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        files = sorted(set(DOCS + [str(p.relative_to(ROOT)).replace("\\", "/") for p in EV.glob("m4_r03_vm401_*")
                                  if p.is_file() and p != manifest_path]))
        lines = ["# V015-M4 R03 / V-M4-01 独立审核补证交付SHA256", "",
                 "V-M4-01通过；仅V-M4-02待验证，F11开放、M4不放行。", "",
                 "本清单绑定新补证和活动状态文档，不覆写原R03清单/结论；清单不含自身。", "",
                 "HEAD: `" + head + "`", "",
                 "| 文件 | SHA256 |", "|---|---|"]
        lines += ["| `" + p + "` | `" + sha(ROOT / p) + "` |" for p in files]
        manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return len(files)

    write()
    broken = []
    for doc in DOCS:
        p = ROOT / doc
        for target in re.findall(r"\]\(([^)]+)\)", p.read_text(encoding="utf-8-sig")):
            if "vm401" not in target and "V-M4-01_" not in target:
                continue
            if not (p.parent / target.split("#")[0]).is_file():
                broken.append({"document": doc, "target": target})
    report["broken_supplement_links"] = broken
    passed = not unexpected and not frozen_source_changes and equal and head == baseline["head"] and not broken and decision["verdict"] == "PASS"
    report["integrity_verdict"] = "PASS" if passed else "FAIL"
    count = write()
    print(json.dumps({"integrity": report["integrity_verdict"], "V-M4-01": report["V-M4-01"],
                      "V-M4-02": report["V-M4-02"], "source_changes": frozen_source_changes,
                      "unexpected_changes": unexpected, "snapshot_equal": equal,
                      "broken_links": broken, "delivery_files": count}, ensure_ascii=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
