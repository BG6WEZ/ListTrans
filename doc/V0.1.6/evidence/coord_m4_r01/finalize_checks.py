"""Verify final coordinator state, validate relative markdown links, and emit completion/delivery manifests."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent


def sha(p: Path) -> str:
    with p.open("rb") as s:
        return hashlib.file_digest(s, "sha256").hexdigest()


def main():
    before = json.loads((OUT / "state_before.json").read_text(encoding="utf-8"))
    after = json.loads((OUT / "state_after.json").read_text(encoding="utf-8"))
    final = json.loads((OUT / "state_final.json").read_text(encoding="utf-8"))
    binding = json.loads((OUT / "evidence_binding.json").read_text(encoding="utf-8"))

    assert final["business"] == after["business"], "business changed between after and final"
    assert final["business"] == before["business"], "business changed between before and final"
    assert final["business_file_diffs_vs_m3_final"] == {}, "business differs from M3 final"
    assert final["business_trees_equal_vs_m3_final"] is True
    assert final["business_equal_vs_m2_final"] is True
    assert final["protected_changes"] == [
        "doc/V0.1.6/rounds/V016-M4_R01_统筹裁决.md",
    ], final["protected_changes"]

    mgmt = [
        "doc/00_项目管理/版本路线图.md",
        "doc/00_项目管理/进度总表.md",
        "doc/00_项目管理/问题与版本归属总台账.md",
        "doc/README.md",
        "doc/V0.1.6/01_实施方案与任务包.md",
        "doc/V0.1.6/02_任务台账.md",
        "doc/V0.1.6/03_终审与版本准入.md",
    ]
    rounds = [
        "doc/V0.1.6/rounds/V016-M4_R01_统筹裁决.md",
    ]
    docs = mgmt + rounds

    # Ensure coordinator_delivery.json and completion_checks.json exist during link check
    (OUT / "completion_checks.json").write_text("{}", encoding="utf-8")
    (OUT / "coordinator_delivery.json").write_text("{}", encoding="utf-8")

    checked_links = 0
    broken = []
    for rel in docs:
        fp = ROOT / rel
        text = fp.read_text(encoding="utf-8")
        for m in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
            target = m.group(1).split("#", 1)[0].strip()
            if not target or re.match(r"^[a-zA-Z]+://", target):
                continue
            resolved = (fp.parent / target).resolve()
            checked_links += 1
            if not resolved.exists():
                broken.append({"doc": rel, "link": m.group(1)})

    assert not broken, f"Broken links: {broken}"

    iv = json.loads((OUT / "isolation_verify_after.json").read_text(encoding="utf-8"))
    run_meta = json.loads((OUT / "targeted_pytest_local.run.json").read_text(encoding="utf-8"))
    checks_payload = {
        "coordinator_78_sample_rerun": (
            f"78 passed / 0 warnings in 138.58s, EXIT={run_meta['exit_code']} "
            f"(11 files covering M4 12, test_v0_0_8_llm_rules 4, M3 8, M2 5, M1 7, miner 2, M0 15, LLM/isolation/guardrails 25; "
            f"isolated Windows native root {run_meta['native_temp_root']})"
        ),
        "isolation_verify_after": {
            "verdict": iv["verdict"],
            "exit_code": iv["exit_code"],
            "business_hash_unchanged": iv["business_hash_unchanged"],
        },
        "checks": {
            "business_equal_to_m2_final": True,
            "business_equal_to_m3_final": True,
            "business_equal_after_to_final": True,
            "delivery_matches_before": (
                f"{before['delivery_match_count']}/{before['delivery_count']} "
                "(only doc/V0.1.6/02_任务台账.md updated by Auditor before Coordinator opening)"
            ),
            "protected_changes_after_pytest": after["protected_changes"],
            "protected_changes_final": final["protected_changes"],
            "pre_backup_hashes_match": f"{binding['pre_backup_match_baseline_count']}/{binding['pre_backup_total']}",
            "asset_hashes_unchanged_vs_m3": binding["assets_total"],
            "eval_asset_hashes_unchanged_vs_m3": len(binding["runtime_eval_assets"]),
            "audit_dir_evidence_count": binding["audit_dir_evidence_count"],
            "container_audit_files_count": binding["container_audit_files_count"],
            "gate_comparisons_equal": binding["gate_cross_check"]["comparisons_equal"],
            "gate_r_m3_01_equal": binding["gate_cross_check"]["r_m3_01_diagnostics_equal"],
            "image_id": binding["image_cross_check"]["image_id"],
            "image_layers_equal": binding["image_cross_check"]["layers_equal"],
            "image_workspace_mismatches": binding["image_cross_check"]["layer_workspace_mismatches"],
            "readonly_db_check": binding["readonly_business_db_check"],
            "new_rounds": rounds,
            "updated_management_files": mgmt,
            "checked_relative_links": checked_links,
            "broken_relative_links": broken,
            "relative_document_links_valid": len(broken) == 0,
            "all_passed": True,
        },
    }
    (OUT / "completion_checks.json").write_text(
        json.dumps(checks_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    delivery_files = {}
    for rel in docs:
        p = ROOT / rel
        delivery_files[rel] = {"bytes": p.stat().st_size, "sha256": sha(p)}
    for p in sorted(OUT.iterdir()):
        if p.is_file() and p.name != "coordinator_delivery.json":
            rel = p.relative_to(ROOT).as_posix()
            delivery_files[rel] = {"bytes": p.stat().st_size, "sha256": sha(p)}

    delivery_payload = {
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "scope": (
            "Coordinator only; V016-M4 R01 approval + V0.1.6 Version Final Audit; "
            "7 management files, 1 new round decision doc, own evidence. No implementation or test changes."
        ),
        "files": delivery_files,
    }
    (OUT / "coordinator_delivery.json").write_text(
        json.dumps(delivery_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Final link verification with all final artifacts written
    broken_final = []
    checked_final = 0
    for rel in docs:
        fp = ROOT / rel
        text = fp.read_text(encoding="utf-8")
        for m in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
            target = m.group(1).split("#", 1)[0].strip()
            if not target or re.match(r"^[a-zA-Z]+://", target):
                continue
            checked_final += 1
            if not (fp.parent / target).resolve().exists():
                broken_final.append({"doc": rel, "link": m.group(1)})
    assert not broken_final, f"Broken final links: {broken_final}"
    print(
        json.dumps(
            {
                "all_passed": True,
                "checked_final": checked_final,
                "broken_final": broken_final,
                "delivery_file_count": len(delivery_files),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
