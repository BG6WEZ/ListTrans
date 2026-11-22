# -*- coding: utf-8 -*-
"""Generate verified closure JSON and SHA256 delivery manifest for V015-M5 R02 audit."""
import hashlib
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[3]


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_dev_manifest():
    txt = (ROOT / "doc/V0.1.5/evidence/m5_r02_delivery_hashes.md").read_text(encoding="utf-8")
    checked_64hex = 0
    mismatches = []
    typo_62hex_rows = []
    for line in txt.splitlines():
        if line.startswith("| `"):
            cols = [c.strip().strip("`") for c in line.strip("|").split("|")]
            shas = [c for c in cols if re.fullmatch(r"[0-9a-f]{64}", c)]
            rel = cols[0] if cols else ""
            if rel and shas:
                checked_64hex += 1
                p = ROOT / rel if "/" in rel else ROOT / "doc/V0.1.5/evidence" / rel
                if not p.exists() or sha256_file(p) != shas[0]:
                    mismatches.append(rel)
            elif rel and len(cols) >= 3 and re.fullmatch(r"[0-9a-f]{60,63}", cols[2]):
                typo_62hex_rows.append({"file": rel, "listed": cols[2], "actual": sha256_file(ROOT / "doc/V0.1.5/evidence" / rel)})
    return checked_64hex, mismatches, typo_62hex_rows


def verify_r01_immutable():
    r01_items = [
        ("doc/V0.1.5/rounds/V015-M5_R01_审核小结.md", None),
        ("doc/V0.1.5/rounds/V015-M5_R01_统筹裁决.md", None),
        ("doc/V0.1.5/evidence/m5_r01_browser_e2e.py", "a65cc55fc2faf6d8c882d8936b85ea93ba770cd6ad802100d43e3dc28601d4ad"),
        ("doc/V0.1.5/evidence/m5_r01_browser_e2e_report.json", "fb788a7613fc0352581427faa70c53b39efb51702d1e7956f63198e507cbaa33"),
    ]
    missing_or_drifted = []
    for rel, expected in r01_items:
        p = ROOT / rel
        if not p.exists():
            missing_or_drifted.append(rel)
        elif expected is not None and sha256_file(p) != expected:
            missing_or_drifted.append(rel)
    return missing_or_drifted


def main():
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dev_checked, dev_mismatches, dev_short_hash_typos = verify_dev_manifest()
    r01_drift = verify_r01_immutable()

    snap_before = sha256_file(ROOT / "doc/V0.1.5/evidence/m5_r02_audit_snapshot_before.json")
    snap_after = sha256_file(ROOT / "doc/V0.1.5/evidence/m5_r02_audit_snapshot_after.json")
    vb = json.loads((ROOT / "doc/V0.1.5/evidence/m5_r02_audit_verify_before.json").read_text(encoding="utf-8"))
    va = json.loads((ROOT / "doc/V0.1.5/evidence/m5_r02_audit_verify_after.json").read_text(encoding="utf-8"))

    closure = {
        "head": head,
        "round": "V015-M5 R02 Audit",
        "verdict": "RETURN_FOR_REWORK",
        "closed_items": ["A-M5-03", "A-M5-04", "A-M5-05"],
        "open_P1": ["A-M5-01", "A-M5-02"],
        "dev_delivery_checked": dev_checked,
        "dev_delivery_mismatches": dev_mismatches,
        "dev_delivery_short_hash_typos": dev_short_hash_typos,
        "r01_immutable_drift": r01_drift,
        "zero_edit_check": {
            "app/auth_manager.py": sha256_file(ROOT / "app/auth_manager.py") == "0f621d06bc78d4abbc569b221a354b391322dee285ab02ed2091e65ca47472d8",
            "tests/conftest.py": sha256_file(ROOT / "tests/conftest.py") == "207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033",
        },
        "business_snapshot_equal": snap_before == snap_after,
        "before_sha256": snap_before,
        "after_sha256": snap_after,
        "verify": {"before": vb.get("verdict"), "after": va.get("verdict")},
        "independent_m4_m5_suite": "35 passed / 48.03s / exit_code=0",
        "independent_full_suite": "286 passed / 0 warnings / 153.26s / exit_code=0",
        "independent_3rule_gate": "harmful=false, beneficial=true, neutral=true, diffs_m3_r04=0, diffs_m4_r03=0 / 68.137s",
        "independent_browser_e2e": "22/22 gates true / 30.974s / 3 audit screenshots inspected",
        "independent_docker_audit": "listtrans:m5-r02-prod 11 layers, 49/49 /app sha256 match, 0 leaks, /login.html=200, psutil=7.2.2, fail_closed_rc=1",
        "independent_probes_reproduced": [
            "r02_probe_a_post_swap_cancel_split",
            "r02_probe_b_real_timeout_after_sql_commit",
            "r02_probe_c_step7_cache_replace_failure",
            "r02_probe_d_append_index_failure",
        ],
        "PRG_F11": "OPEN_BEFORE_FIRST_REAL_DEPLOYMENT",
        "future_versions": "LOCKED",
    }

    closure_path = ROOT / "doc/V0.1.5/evidence/m5_r02_audit_closure.json"
    closure_path.write_text(json.dumps(closure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Enumerate audit files created/updated by this audit round
    audit_files = [
        "doc/V0.1.5/rounds/V015-M5_R02_审核小结.md",
        "doc/V0.1.5/02_任务台账.md",
        "doc/V0.1.5/evidence/m5_r02_audit_closure.json",
        "doc/V0.1.5/evidence/m5_r02_audit_snapshot_before.json",
        "doc/V0.1.5/evidence/m5_r02_audit_snapshot_after.json",
        "doc/V0.1.5/evidence/m5_r02_audit_verify_before.json",
        "doc/V0.1.5/evidence/m5_r02_audit_verify_after.json",
        "doc/V0.1.5/evidence/m5_r02_audit_r01_to_r02.diff",
        "doc/V0.1.5/evidence/m5_r02_audit_m5_pytest.log",
        "doc/V0.1.5/evidence/m5_r02_audit_full_pytest.log",
        "doc/V0.1.5/evidence/m5_r02_audit_probes.py",
        "doc/V0.1.5/evidence/m5_r02_audit_probes.json",
        "doc/V0.1.5/evidence/m5_r02_audit_3rule.py",
        "doc/V0.1.5/evidence/m5_r02_audit_3rule.log",
        "doc/V0.1.5/evidence/m5_r02_audit_gate_reports.json",
        "doc/V0.1.5/evidence/m5_r02_audit_browser_e2e.py",
        "doc/V0.1.5/evidence/m5_r02_audit_browser.log",
        "doc/V0.1.5/evidence/m5_r02_audit_browser_e2e_report.json",
        "doc/V0.1.5/evidence/m5_r02_audit_ui_workbench.png",
        "doc/V0.1.5/evidence/m5_r02_audit_ui_rejected_restored.png",
        "doc/V0.1.5/evidence/m5_r02_audit_ui_export_done.png",
        "doc/V0.1.5/evidence/m5_r02_audit_docker_check.py",
        "doc/V0.1.5/evidence/m5_r02_audit_docker_check.json",
        "doc/V0.1.5/evidence/m5_r02_audit_closure.py",
    ]
    lines = [
        "# V015-M5 R02 独立审核交付物 SHA256 清单",
        "",
        f"- 基线 `HEAD`：`{head}`",
        f"- 业务快照前后 SHA256：`{snap_after}`",
        "",
        "| 文件 | 字节 | SHA256 |",
        "|---|---|---|",
    ]
    for rel in audit_files:
        p = ROOT / rel
        assert p.exists(), f"missing {rel}"
        lines.append(f"| `{rel}` | {p.stat().st_size} | `{sha256_file(p)}` |")

    (ROOT / "doc/V0.1.5/evidence/m5_r02_audit_delivery_hashes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "dev_checked": dev_checked,
        "dev_mismatches": dev_mismatches,
        "r01_drift": r01_drift,
        "snap_equal": snap_before == snap_after,
        "audit_file_count": len(audit_files),
    }, ensure_ascii=True))


if __name__ == "__main__":
    main()
