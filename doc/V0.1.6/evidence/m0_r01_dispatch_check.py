"""Read-only admission checks; writes only new V016 coordinator evidence.

Run with the fixed venv: this_file.py before|after. No app imports or tests.
"""
import hashlib
import json
import re
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.6/evidence"
OLD = ROOT / "doc/V0.1.5/evidence"
DOCS = [
    "doc/README.md", "doc/00_项目管理/版本路线图.md",
    "doc/00_项目管理/进度总表.md", "doc/00_项目管理/问题与版本归属总台账.md",
    "doc/V0.1.5/03_终审与版本准入.md", "doc/V0.1.6/01_实施方案与任务包.md",
    "doc/V0.1.6/02_任务台账.md", "doc/V0.1.6/03_终审与版本准入.md",
    "doc/V0.1.6/rounds/README.md", "doc/V0.1.6/evidence/README.md",
    "doc/V0.1.6/rounds/V016-M0_R01_统筹下发.md",
]


def sha(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(phase):
    target = EV / f"m0_r01_dispatch_{phase}.json"
    if phase not in ("before", "after") or target.exists():
        raise SystemExit("Unknown phase or existing evidence: refusing overwrite")
    baseline = json.loads((OLD / "v015_final_baseline.json").read_bytes())
    manifest = dict(re.findall(r"^\| `([^`]+)` \| `([a-f0-9]{64})`", (OLD / "v015_final_delivery_hashes.md").read_text(encoding="utf-8"), re.M))
    groups = {name: {"checked": len(baseline[name]), "mismatches": [p for p, h in baseline[name].items() if sha(ROOT / p) != h]}
              for name in ("source_hashes", "model_hashes", "fixed_evidence_hashes")}
    changed = [p for p, h in manifest.items() if sha(ROOT / p) != h]
    # Only these current-status documents from the old manifest may be updated.
    unexpected = changed if phase == "before" else [p for p in changed if p not in DOCS[:5]]
    reference = (OLD / "v015_final_snapshot_after.json").read_bytes()
    snapshot = runpy.run_path(str(ROOT / "tests/verify_isolation.py"))["collect_snapshot"]()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    result = dict(date="2026-11-17", role="coordinator", phase=phase, head=head,
                  previous_delivery_checked=len(manifest), previous_delivery_changed=changed,
                  unexpected_previous_delivery_changes=unexpected, protected=groups,
                  business_snapshot_equal=snapshot == json.loads(reference),
                  reference_snapshot_sha256=hashlib.sha256(reference).hexdigest(),
                  document_hashes={p: sha(ROOT / p) for p in DOCS},
                  previous_manifest_sha256=sha(OLD / "v015_final_delivery_hashes.md"),
                  tests_rerun=False, production_operations=False)
    okay = (head == baseline["head"] and len(manifest) == 22 and not unexpected
            and all(not group["mismatches"] for group in groups.values())
            and result["business_snapshot_equal"] and bool(baseline["model_hashes"]))
    if phase == "after":
        before = json.loads((EV / "m0_r01_dispatch_before.json").read_bytes())
        result["before_verdict"] = before["integrity_verdict"]
        resolution = json.loads((EV / "m0_r01_dispatch_baseline_resolution.json").read_bytes())
        reviewed = resolution["reviewed_preexisting_changes"]
        resolved = (resolution["decision"] == "ACCEPT_CURRENT_STATUS_DOCUMENTS_WITH_RECORDED_DIFFERENCES"
                    and set(reviewed) == set(before["unexpected_previous_delivery_changes"])
                    and all(before["document_hashes"].get(p) == h for p, h in reviewed.items())
                    and all(not group["mismatches"] for group in before["protected"].values())
                    and before["business_snapshot_equal"] and before["head"] == baseline["head"])
        result["preexisting_document_differences_accepted"] = resolved
        result["previous_manifest_unchanged"] = before["previous_manifest_sha256"] == result["previous_manifest_sha256"]
        result["documents_missing"] = [p for p in DOCS if not (ROOT / p).is_file()]
        result["broken_links"] = []
        for rel in DOCS:
            for link in re.findall(r"\]\(([^)]+)\)", (ROOT / rel).read_text(encoding="utf-8")):
                resolved_link = ((ROOT / rel).parent / link.split("#", 1)[0]).resolve()
                planned = {target.resolve(), (EV / "m0_r01_dispatch_hashes.md").resolve()}
                if not link.startswith(("http:", "https:", "#")) and not resolved_link.is_file() and resolved_link not in planned:
                    result["broken_links"].append({"file": rel, "target": link})
        okay = (okay and (before["integrity_verdict"] == "PASS" or resolved) and result["previous_manifest_unchanged"]
                and not result["documents_missing"] and not result["broken_links"])
    result["integrity_verdict"] = "PASS" if okay else "FAIL"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if phase == "after" and okay:
        files = DOCS + ["doc/V0.1.6/evidence/" + name for name in (
            "m0_r01_dispatch_check.py", "m0_r01_dispatch_before.json", "m0_r01_dispatch_after.json",
            "m0_r01_dispatch_baseline_resolution.json")]
        lines = ["# V016-M0 R01 统筹准入与下发 SHA256", "", "2026-11-17；仅管理文档与只读核验，未开发/未审核本包。清单不包含自身。", "",
                 "| 文件 | 字节数 | SHA256 |", "|---|---:|---|"]
        lines += [f"| `{p}` | {(ROOT / p).stat().st_size} | `{sha(ROOT / p)}` |" for p in files]
        (EV / "m0_r01_dispatch_hashes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "document_hashes"}, ensure_ascii=True))
    return 0 if okay else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
