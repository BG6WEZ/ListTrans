"""Bind M3 R01 evidence, pre-backups, frozen/runtime assets, containers/layers, and read-only DB state."""
import difflib
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent


def sha(p: Path) -> str:
    with p.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    # 1. Pre-baseline 12 backup checks vs m3_r01_baseline.json and coord_m2_r01/state_final.json + unified diff
    baseline = json.loads(
        (ROOT / "doc/V0.1.6/evidence/m3_r01_baseline.json").read_text(encoding="utf-8-sig")
    )
    m2_final = json.loads(
        (ROOT / "doc/V0.1.6/evidence/coord_m2_r01/state_final.json").read_text(
            encoding="utf-8"
        )
    )
    m2_protected = m2_final.get("protected", {})

    backup_map = baseline.get("backup", {})
    pre_checks = []
    diffs = []
    for rel, expected_sha in sorted(backup_map.items()):
        bak_path = ROOT / ".local-archive/v016_m3_r01_pre_backup" / rel
        bak_sha = sha(bak_path) if bak_path.is_file() else None
        m2_sha = m2_protected.get(rel)
        pre_checks.append(
            {
                "path": rel,
                "baseline_declared": expected_sha,
                "backup_actual": bak_sha,
                "m2_final_protected": m2_sha,
                "matches_baseline": bak_sha == expected_sha,
                "matches_m2_final": (bak_sha == m2_sha) if m2_sha is not None else None,
            }
        )
        cur_path = ROOT / rel
        if bak_path.is_file() and cur_path.is_file() and rel != "doc/V0.1.6/02_任务台账.md":
            a = bak_path.read_text(encoding="utf-8-sig").splitlines()
            b = cur_path.read_text(encoding="utf-8-sig").splitlines()
            diff = list(
                difflib.unified_diff(a, b, fromfile="pre/" + rel, tofile=rel, n=3)
            )
            diffs.extend(diff)

    # Also include newly added M3 runtime files in m3_only_review.diff
    for new_rel in (
        "app/runtime_eval_assets.py",
        "app/sandbox_evaluator.py",
        "tests/test_v016_m3_runtime_assets.py",
    ):
        np = ROOT / new_rel
        if np.is_file():
            b = np.read_text(encoding="utf-8-sig").splitlines()
            diffs.extend(
                difflib.unified_diff([], b, fromfile="/dev/null", tofile=new_rel, n=3)
            )

    (OUT / "m3_only_review.diff").write_text(
        "\n".join(diffs) + ("\n" if diffs else ""), encoding="utf-8"
    )

    # 2. Frozen offline model/fixture assets (43 items vs M2) + new app/eval_assets runtime assets
    m2_binding = json.loads(
        (ROOT / "doc/V0.1.6/evidence/coord_m2_r01/evidence_binding.json").read_text(
            encoding="utf-8"
        )
    )
    assets = {}
    for folder in ("model_offline", "tests/fixtures"):
        for p in sorted((ROOT / folder).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                assets[p.relative_to(ROOT).as_posix()] = sha(p)
    asset_mismatches = [
        k
        for k in set(m2_binding.get("assets", {})) | set(assets)
        if m2_binding.get("assets", {}).get(k) != assets.get(k)
    ]

    runtime_eval_assets = {
        p.relative_to(ROOT).as_posix(): {"bytes": p.stat().st_size, "sha256": sha(p)}
        for p in sorted((ROOT / "app/eval_assets").iterdir())
        if p.is_file()
    }

    # 3. Audit M3 R01 evidence inventory (21 files)
    audit_dir = ROOT / "doc/V0.1.6/evidence/audit_m3_r01"
    audit_evid = {
        p.relative_to(ROOT).as_posix(): {"bytes": p.stat().st_size, "sha256": sha(p)}
        for p in sorted(audit_dir.iterdir())
        if p.is_file()
    }

    # 4. Cross-check developer gate report vs auditor gate report
    dev_gate = json.loads(
        (ROOT / "doc/V0.1.6/evidence/m3_r01_gate_reports.json").read_text(
            encoding="utf-8"
        )
    )
    aud_gate = json.loads(
        (audit_dir / "audit_gate_reports.json").read_text(encoding="utf-8")
    )
    comparisons_equal = dev_gate.get("comparisons") == aud_gate.get("comparisons")
    probes_equal = dev_gate.get("probe_gt_decisions") == aud_gate.get(
        "probe_gt_decisions"
    )

    # 5. Cross-check developer image layers vs auditor image layers
    dev_layers = json.loads(
        (ROOT / "doc/V0.1.6/evidence/m3_r01_image_layers.json").read_text(
            encoding="utf-8"
        )
    )
    aud_layers = json.loads(
        (audit_dir / "audit_image_layers.json").read_text(encoding="utf-8")
    )
    layer_app_files = {}
    for layer_name, layer_info in aud_layers.get("layers", {}).items():
        layer_app_files.update(layer_info.get("app_files", {}))
    layer_workspace_mismatches = [
        rel
        for rel, expected_h in sorted(layer_app_files.items())
        if not (ROOT / rel).is_file() or sha(ROOT / rel) != expected_h
    ]

    image_cross_check = {
        "image_id": aud_layers.get("image_id"),
        "image_id_equal": dev_layers.get("image_id") == aud_layers.get("image_id"),
        "rootfs_diff_ids_count": len(aud_layers.get("rootfs_diff_ids") or []),
        "rootfs_diff_ids_equal": dev_layers.get("rootfs_diff_ids")
        == aud_layers.get("rootfs_diff_ids"),
        "application_files": aud_layers.get("application_files"),
        "application_files_count_actual": len(layer_app_files),
        "layers_equal": dev_layers.get("layers") == aud_layers.get("layers"),
        "violations": aud_layers.get("violations"),
        "source_hashes_match": aud_layers.get("source_hashes_match"),
        "cmd": aud_layers.get("cmd"),
        "layer_workspace_mismatches": layer_workspace_mismatches,
    }

    # 6. Read-only SQLite logical & zero-pollution verification
    mat_uri = f"file:{(ROOT / 'app/materials.db').as_posix()}?mode=ro"
    auth_uri = f"file:{(ROOT / 'app/auth.db').as_posix()}?mode=ro"
    mat_counts = {}
    conn_m = sqlite3.connect(mat_uri, uri=True)
    try:
        for tbl in (
            "materials",
            "materials_fts",
            "materials_metadata",
            "llm_rules",
            "rule_audit_logs",
            "rule_eval_runs",
            "flywheel_rule_suggestions",
            "flywheel_config_snapshots",
            "flywheel_records",
        ):
            mat_counts[tbl] = conn_m.execute(
                f'SELECT COUNT(*) FROM "{tbl}"'
            ).fetchone()[0]
    finally:
        conn_m.close()

    auth_counts = {}
    conn_a = sqlite3.connect(auth_uri, uri=True)
    try:
        for tbl in (
            "users",
            "audit_logs",
            "work_sessions",
            "active_heartbeats",
            "issued_temp_credentials",
            "revoked_tokens",
        ):
            auth_counts[tbl] = conn_a.execute(
                f'SELECT COUNT(*) FROM "{tbl}"'
            ).fetchone()[0]
        residual_audit_users = conn_a.execute(
            "SELECT COUNT(*) FROM users WHERE username LIKE 'm2_%' OR username LIKE 'm3_%' OR account_epoch LIKE 'audit%'"
        ).fetchone()[0]
    finally:
        conn_a.close()

    result = {
        "pre_backup_checks": pre_checks,
        "pre_backup_match_baseline_count": sum(
            1 for c in pre_checks if c["matches_baseline"]
        ),
        "pre_backup_total": len(pre_checks),
        "assets": assets,
        "assets_total": len(assets),
        "asset_mismatches_vs_m2": asset_mismatches,
        "runtime_eval_assets": runtime_eval_assets,
        "audit_evidence": audit_evid,
        "audit_evidence_count": len(audit_evid),
        "gate_cross_check": {
            "comparisons_equal": comparisons_equal,
            "probe_gt_decisions_equal": probes_equal,
            "comparisons": aud_gate.get("comparisons"),
            "probe_gt_decisions": aud_gate.get("probe_gt_decisions"),
        },
        "image_cross_check": image_cross_check,
        "readonly_business_db_check": {
            "materials_db_counts": mat_counts,
            "auth_db_counts": auth_counts,
            "residual_audit_users": residual_audit_users,
        },
        "non_blocking_notes_and_errata": [
            {
                "id": "ERRATA-M3-F08-VS-F11",
                "summary": (
                    "V016-M3_R01_审核小结.md title line and section 6 mention 'F11/F28'; "
                    "M3 formal scope actually covers F08 (P1, runtime evaluation entry & desensitized assets) "
                    "and F28 (P2, static route priority). F11 / PRG-F11 remains open until first real deployment."
                ),
            },
            {
                "id": "AUDIT-SIDE-03",
                "summary": (
                    "Auditor exported POSIX /tmp/... paths to LISTTRANS_TEST_ROOT and --basetemp in Git Bash, "
                    "causing Windows Python path anomalies and two hung runs (full_pytest_fixed.log / full_pytest_standard.log "
                    "at 36 dots). Diagnosed in 4 steps and closed by full_pytest_audit_default.log (361 passed in 1178.71s, EXIT=0)."
                ),
            },
            {
                "id": "NOTE-M3-TARGETED-LOG",
                "summary": (
                    "Developer m3_r01_targeted_pytest.log (113B) contains only the -q summary line ('61 passed in 452.44s'); "
                    "full verbose coverage is provided by m3_r01_full_fixed/standard.log (361 passed), "
                    "audit full_pytest_audit_default.log (361 passed), and coordinator targeted_pytest_local.log (-v, 66 passed)."
                ),
            },
        ],
    }

    (OUT / "evidence_binding.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "pre_backup": f"{result['pre_backup_match_baseline_count']}/{result['pre_backup_total']}",
                "assets_total": result["assets_total"],
                "asset_mismatches_vs_m2": len(asset_mismatches),
                "runtime_eval_assets_count": len(runtime_eval_assets),
                "audit_evidence_count": result["audit_evidence_count"],
                "gate_comparisons_equal": comparisons_equal,
                "gate_probes_equal": probes_equal,
                "image_id": image_cross_check["image_id"],
                "image_layers_count": image_cross_check["rootfs_diff_ids_count"],
                "image_app_files": image_cross_check["application_files"],
                "image_layers_equal": image_cross_check["layers_equal"],
                "image_workspace_mismatches": len(layer_workspace_mismatches),
                "materials": mat_counts.get("materials"),
                "rule_eval_runs": mat_counts.get("rule_eval_runs"),
                "users": auth_counts.get("users"),
                "residual_audit_users": residual_audit_users,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
