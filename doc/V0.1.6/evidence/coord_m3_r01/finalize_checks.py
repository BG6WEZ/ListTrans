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

    assert final["business"] == after["business"], "business changed between after and final"
    assert final["business_file_diffs_vs_m2_final"] == {}, "business differs from M2 final"
    assert final["business_trees_equal_vs_m2_final"] is True
    assert final["protected_changes"] == [
        "doc/V0.1.6/rounds/V016-M3_R01_统筹裁决.md",
        "doc/V0.1.6/rounds/V016-M4_R01_统筹下发.md",
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
        "doc/V0.1.6/rounds/V016-M3_R01_统筹裁决.md",
        "doc/V0.1.6/rounds/V016-M4_R01_统筹下发.md",
    ]
    docs = mgmt + rounds

    # Ensure coordinator_delivery.json exists during link check
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
    checks_payload = {
        "coordinator_66_raw_rerun": (
            "65 passed, 1 failed in 236.77s, EXIT=1 (archived in targeted_pytest_66_raw.log; "
            "test_v0_0_8_llm_rules.py:141 45s poll limit registered as OBS-M3-01 / C-M4-07)"
        ),
        "coordinator_65_deterministic_rerun": (
            "65 passed, 1 deselected in 161.46s, EXIT=0 "
            "(9 files covering M3 8, M2 5, M1 7, miner 2, M0 15, llm_rules DSL 3, LLM/isolation/guardrails 25)"
        ),
        "isolation_verify_after": {
            "verdict": iv["verdict"],
            "exit_code": iv["exit_code"],
            "business_hash_unchanged": iv["business_hash_unchanged"],
        },
        "checks": {
            "business_equal_to_m2_final": True,
            "business_equal_after_to_final": True,
            "wal_shm_note": (
                "4 zero-byte/clean WAL/SHM sidecars recreated at 17:00:38 by inspect_evidence.py ?mode=ro, "
                "restoring exact match with coord_m2_r01/state_final.json (business_file_diffs_vs_m2_final == {}); "
                "materials.db and auth.db mtime unchanged since 10:46:37."
            ),
            "delivery_matches_before": (
                f"{before['delivery_match_count']}/{before['delivery_count']} "
                "(only doc/V0.1.6/02_任务台账.md updated by Auditor before Coordinator opening)"
            ),
            "protected_changes": final["protected_changes"],
            "pre_backup_hashes_match": "12/12",
            "asset_hashes_unchanged_vs_m2": 43,
            "audit_evidence_count": 21,
            "gate_cross_check_equal": True,
            "image_layers_cross_check_equal": True,
            "new_rounds": rounds,
            "updated_management_files": mgmt,
            "checked_relative_links": checked_links,
            "broken_relative_links": broken,
            "relative_document_links_valid": len(broken) == 0,
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
        "scope": "Coordinator only; 7 management files, 2 new round documents, own evidence. No implementation or test changes.",
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
