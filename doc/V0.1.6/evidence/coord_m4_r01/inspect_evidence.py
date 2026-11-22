"""Bind M4 R01 evidence, pre-backups, frozen/runtime assets, containers/layers, and read-only DB state."""
import collections
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


def summarize_container(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    calls = data.get("calls") or []
    status_counts = dict(sorted(collections.Counter(c[2] for c in calls).items()))
    has_5xx = any(c[2] >= 500 for c in calls)
    dbs = data.get("database_states") or {}
    before_rej = dbs.get("before_rejected_rollback")
    after_rej = dbs.get("after_rejected_rollback")
    after_bind_rb = dbs.get("after_binding_rollback") or {}
    after_alias_rb = dbs.get("after_alias_rollback") or {}
    ev_summary = {}
    for sid, ev in (data.get("evaluations") or {}).items():
        rep = ev.get("report") or {}
        vec = ((rep.get("snapshot") or {}).get("candidate") or {}).get("vector_channel") or {}
        ev_summary[sid] = {
            "status": ev.get("status"),
            "safety_passed": ev.get("safety_passed"),
            "candidate_recall": (rep.get("candidate_metrics") or {}).get("candidate_recall"),
            "candidate_diagnostics_count": len(rep.get("candidate_diagnostics") or []),
            "vector_enabled": vec.get("enabled"),
            "vector_dim": vec.get("dim"),
            "model_version": vec.get("model_version"),
        }
    return {
        "bytes": path.stat().st_size,
        "sha256": sha(path),
        "no_tests_doc": data.get("no_tests_doc"),
        "catalog_rows": data.get("catalog_rows"),
        "call_count": len(calls),
        "status_counts": {str(k): v for k, v in status_counts.items()},
        "has_5xx": has_5xx,
        "lifo_409": data.get("lifo_409"),
        "restored_config_db_memory_fingerprint": data.get("restored_config_db_memory_fingerprint"),
        "xlsx_bytes": data.get("xlsx_bytes"),
        "exports": data.get("exports"),
        "rejected_rollback_db_unchanged": (before_rej == after_rej) and (before_rej is not None),
        "after_binding_rollback_active": [r.get("is_active") for r in after_bind_rb.get("snapshots", [])],
        "after_binding_rollback_statuses": [r.get("status") for r in after_bind_rb.get("suggestions", [])],
        "after_alias_rollback_active": [r.get("is_active") for r in after_alias_rb.get("snapshots", [])],
        "after_alias_rollback_statuses": [r.get("status") for r in after_alias_rb.get("suggestions", [])],
        "fingerprints_before_eq_after_alias_rb": (
            (data.get("fingerprints") or {}).get("before")
            == (data.get("fingerprints") or {}).get("rollback_alias")
        ),
        "fingerprints_alias_eq_after_binding_rb": (
            (data.get("fingerprints") or {}).get("alias")
            == (data.get("fingerprints") or {}).get("rollback_binding")
        ),
        "evaluations": ev_summary,
        "runner": data.get("runner"),
    }


def main():
    # 1. Pre-baseline 6 backup checks vs m4_r01_baseline.json and coord_m3_r01/state_final.json + unified diff
    baseline = json.loads(
        (ROOT / "doc/V0.1.6/evidence/m4_r01_baseline.json").read_text(encoding="utf-8-sig")
    )
    m3_final = json.loads(
        (ROOT / "doc/V0.1.6/evidence/coord_m3_r01/state_final.json").read_text(
            encoding="utf-8"
        )
    )
    m3_protected = m3_final.get("protected", {})

    backup_map = baseline.get("backup", {})
    pre_checks = []
    diffs = []
    for rel, expected_sha in sorted(backup_map.items()):
        bak_path = ROOT / ".local-archive/v016_m4_r01_pre_backup" / rel
        bak_sha = sha(bak_path) if bak_path.is_file() else None
        m3_sha = m3_protected.get(rel)
        if rel == "doc/V0.1.6/02_任务台账.md":
            m3_sha = (
                json.loads(
                    (ROOT / "doc/V0.1.6/evidence/coord_m3_r01/coordinator_delivery.json").read_text(
                        encoding="utf-8"
                    )
                )["files"][rel]["sha256"]
            )
        pre_checks.append(
            {
                "path": rel,
                "baseline_declared": expected_sha,
                "backup_actual": bak_sha,
                "m3_final_sha256": m3_sha,
                "matches_baseline": bak_sha == expected_sha,
                "matches_m3_final": (bak_sha == m3_sha) if m3_sha is not None else None,
            }
        )
        cur_path = ROOT / rel
        if bak_path.is_file() and cur_path.is_file() and rel != "doc/V0.1.6/02_任务台账.md":
            a = bak_path.read_text(encoding="utf-8-sig").splitlines()
            b = cur_path.read_text(encoding="utf-8-sig").splitlines()
            diffs.extend(difflib.unified_diff(a, b, fromfile="pre/" + rel, tofile=rel, n=3))

    for new_rel in (
        "tests/test_v016_m4_diagnostics.py",
        "tests/test_v016_m4_lifecycle.py",
    ):
        np = ROOT / new_rel
        if np.is_file():
            b = np.read_text(encoding="utf-8-sig").splitlines()
            diffs.extend(difflib.unified_diff([], b, fromfile="/dev/null", tofile=new_rel, n=3))

    (OUT / "m4_only_review.diff").write_text(
        "\n".join(diffs) + ("\n" if diffs else ""), encoding="utf-8"
    )

    # Compare pre_backup-based diff hunks for the 5 modified files with m4_r01_implementation.diff
    mod_hunks_rebuilt = {}
    for rel in (
        "app/matcher.py",
        "app/sandbox_evaluator.py",
        "app/flywheel_sandbox.py",
        "app/server.py",
        "tests/test_v0_0_8_llm_rules.py",
    ):
        a = (ROOT / ".local-archive/v016_m4_r01_pre_backup" / rel).read_text(encoding="utf-8-sig").splitlines()
        b = (ROOT / rel).read_text(encoding="utf-8-sig").splitlines()
        hunks = [line for line in difflib.unified_diff(a, b, fromfile="a/" + rel, tofile="b/" + rel, n=3) if line.startswith("@@")]
        mod_hunks_rebuilt[rel] = len(hunks)

    impl_diff_text = (ROOT / "doc/V0.1.6/evidence/m4_r01_implementation.diff").read_text(encoding="utf-8")
    impl_diff_files = [line[4:] for line in impl_diff_text.splitlines() if line.startswith("+++ ")]

    # 2. Frozen offline model/fixture assets (43 items vs M3) + app/eval_assets (4 files vs M3)
    m3_binding = json.loads(
        (ROOT / "doc/V0.1.6/evidence/coord_m3_r01/evidence_binding.json").read_text(
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
        for k in set(m3_binding.get("assets", {})) | set(assets)
        if m3_binding.get("assets", {}).get(k) != assets.get(k)
    ]

    runtime_eval_assets = {
        p.relative_to(ROOT).as_posix(): {"bytes": p.stat().st_size, "sha256": sha(p)}
        for p in sorted((ROOT / "app/eval_assets").iterdir())
        if p.is_file()
    }
    eval_asset_mismatches = [
        k
        for k in set(m3_binding.get("runtime_eval_assets", {})) | set(runtime_eval_assets)
        if (m3_binding.get("runtime_eval_assets", {}).get(k) or {}).get("sha256")
        != (runtime_eval_assets.get(k) or {}).get("sha256")
    ]

    # 3. Audit M4 R01 evidence inventory (26 in audit_m4_r01 + 6 container audit files + 2 round reports)
    audit_dir = ROOT / "doc/V0.1.6/evidence/audit_m4_r01"
    audit_evid = {
        p.relative_to(ROOT).as_posix(): {"bytes": p.stat().st_size, "sha256": sha(p)}
        for p in sorted(audit_dir.iterdir())
        if p.is_file()
    }
    container_audit_files = {
        p.relative_to(ROOT).as_posix(): {"bytes": p.stat().st_size, "sha256": sha(p)}
        for p in sorted((ROOT / "doc/V0.1.6/evidence").glob("m4_r01_docker_*_audit.*"))
        if p.is_file()
    }
    round_docs = {
        rel: {"bytes": (ROOT / rel).stat().st_size, "sha256": sha(ROOT / rel)}
        for rel in (
            "doc/V0.1.6/rounds/V016-M4_R01_开发小结.md",
            "doc/V0.1.6/rounds/V016-M4_R01_审核小结.md",
        )
    }

    # 4. Cross-check developer gate report vs auditor gate report + R-M3-01 standalone
    dev_gate = json.loads(
        (ROOT / "doc/V0.1.6/evidence/m4_r01_gate_reports.json").read_text(encoding="utf-8")
    )
    aud_gate = json.loads(
        (audit_dir / "audit_m4_gate_reports.json").read_text(encoding="utf-8")
    )
    comparisons_equal = dev_gate.get("comparisons") == aud_gate.get("comparisons")
    probes_equal = dev_gate.get("probe_gt_decisions") == aud_gate.get("probe_gt_decisions")
    rm3_gate_equal = dev_gate.get("r_m3_01_diagnostics") == aud_gate.get("r_m3_01_diagnostics")
    rule_verdicts = {}
    for rname in ("empty", "harmful", "beneficial", "beneficial_constrained", "neutral"):
        dr = dev_gate["rules"][rname]
        ar = aud_gate["rules"][rname]
        rule_verdicts[rname] = {
            "dev_passed": dr.get("passed"),
            "aud_passed": ar.get("passed"),
            "dev_benefit": dr.get("benefit"),
            "aud_benefit": ar.get("benefit"),
            "binding_degraded_count": (dr.get("report") or {}).get("binding_degraded_count"),
            "binding_improved_count": (dr.get("report") or {}).get("binding_improved_count"),
            "candidate_recall": ((dr.get("report") or {}).get("candidate_metrics") or {}).get("candidate_recall"),
            "equal_summary": (
                dr.get("passed") == ar.get("passed")
                and dr.get("safety_passed") == ar.get("safety_passed")
                and dr.get("benefit") == ar.get("benefit")
                and (dr.get("report") or {}).get("binding_degraded_count") == (ar.get("report") or {}).get("binding_degraded_count")
                and (dr.get("report") or {}).get("binding_improved_count") == (ar.get("report") or {}).get("binding_improved_count")
            ),
        }

    gt_summary = {}
    for gid in ("GT-0674", "GT-0677", "GT-0678"):
        gd = aud_gate["r_m3_01_diagnostics"][gid]
        pd_row = aud_gate["probe_gt_decisions"][gid]
        t0 = gd["targets"][0]
        gt_summary[gid] = {
            "state": t0["state"],
            "full_rank": t0["full_rank"],
            "hard_blocked": t0["hard_blocked"],
            "T": t0["T"],
            "W": t0["W"],
            "margin": round(t0["margin"], 4),
            "decision_status": gd["decision"]["decision_status"],
            "probe_decision_status": pd_row["decision_status"],
            "export_http_status": gd["export_gate"]["http_status"],
        }

    rm3_diag = json.loads(
        (ROOT / "doc/V0.1.6/evidence/m4_r01_r_m3_01_diagnostics.json").read_text(encoding="utf-8")
    )
    rm3_summary = {}
    for gid in ("GT-0674", "GT-0677", "GT-0678"):
        gd = rm3_diag[gid]
        t0 = gd["targets"][0]
        rm3_summary[gid] = {
            "state": t0["state"],
            "full_rank": t0["full_rank"],
            "T": t0["T"],
            "W": t0["W"],
            "margin": round(t0["margin"], 4),
            "decision_status": gd["decision"]["decision_status"],
            "export_status": gd["export_gate"]["http_status"],
        }

    # 5. Cross-check developer image layers vs auditor image layers
    dev_layers = json.loads(
        (ROOT / "doc/V0.1.6/evidence/m4_r01_image_layers_final.json").read_text(encoding="utf-8")
    )
    aud_layers = json.loads(
        (audit_dir / "audit_m4_image_layers.json").read_text(encoding="utf-8")
    )
    layer_app_files = {}
    for layer_info in aud_layers.get("layers", {}).values():
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
        "rootfs_diff_ids_equal": dev_layers.get("rootfs_diff_ids") == aud_layers.get("rootfs_diff_ids"),
        "application_files": aud_layers.get("application_files"),
        "application_files_count_actual": len(layer_app_files),
        "layers_equal": dev_layers.get("layers") == aud_layers.get("layers"),
        "violations": aud_layers.get("violations"),
        "source_hashes_match": aud_layers.get("source_hashes_match"),
        "cmd": aud_layers.get("cmd"),
        "layer_workspace_mismatches": layer_workspace_mismatches,
    }

    # 6. Container cross-check (dev verified vs auditor _audit)
    containers = {
        name: summarize_container(ROOT / rel)
        for name, rel in (
            ("dev_keyword", "doc/V0.1.6/evidence/m4_r01_docker_keyword_verified.json"),
            ("dev_vector", "doc/V0.1.6/evidence/m4_r01_docker_vector_verified.json"),
            ("aud_keyword", "doc/V0.1.6/evidence/m4_r01_docker_keyword_audit.json"),
            ("aud_vector", "doc/V0.1.6/evidence/m4_r01_docker_vector_audit.json"),
        )
    }

    # 7. Audit 16 counterexample probes & failure branches & stage matrix & score contract & env probe
    aud_probes = json.loads((audit_dir / "audit_m4_probes.json").read_text(encoding="utf-8"))
    failure_branches = json.loads(
        (ROOT / "doc/V0.1.6/evidence/m4_r01_failure_branches.json").read_text(encoding="utf-8")
    )
    stage_matrix = json.loads(
        (ROOT / "doc/V0.1.6/evidence/m4_r01_stage_matrix.json").read_text(encoding="utf-8")
    )
    score_contract = json.loads(
        (ROOT / "doc/V0.1.6/evidence/m4_r01_score_contract.json").read_text(encoding="utf-8")
    )
    env_audit = json.loads((audit_dir / "audit_m4_env_repro.json").read_text(encoding="utf-8"))

    # 8. Read-only SQLite logical & zero-pollution verification
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
            mat_counts[tbl] = conn_m.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
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
            auth_counts[tbl] = conn_a.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
        residual_audit_users = conn_a.execute(
            "SELECT COUNT(*) FROM users WHERE username LIKE 'm2_%' OR username LIKE 'm3_%' OR username LIKE 'm4_%' OR account_epoch LIKE 'audit%'"
        ).fetchone()[0]
    finally:
        conn_a.close()

    result = {
        "pre_backup_checks": pre_checks,
        "pre_backup_match_baseline_count": sum(1 for c in pre_checks if c["matches_baseline"]),
        "pre_backup_total": len(pre_checks),
        "rebuilt_mod_hunk_counts": mod_hunks_rebuilt,
        "impl_diff_files": impl_diff_files,
        "assets": assets,
        "assets_total": len(assets),
        "asset_mismatches_vs_m3": asset_mismatches,
        "runtime_eval_assets": runtime_eval_assets,
        "eval_asset_mismatches_vs_m3": eval_asset_mismatches,
        "audit_dir_evidence": audit_evid,
        "audit_dir_evidence_count": len(audit_evid),
        "container_audit_files": container_audit_files,
        "container_audit_files_count": len(container_audit_files),
        "round_docs": round_docs,
        "gate_cross_check": {
            "comparisons_equal": comparisons_equal,
            "comparisons": aud_gate.get("comparisons"),
            "probe_gt_decisions_equal": probes_equal,
            "r_m3_01_diagnostics_equal": rm3_gate_equal,
            "gt_summary": gt_summary,
            "rule_verdicts": rule_verdicts,
        },
        "r_m3_01_standalone_summary": rm3_summary,
        "r_m3_01_matches_gate": rm3_summary == {
            k: {
                "state": v["state"],
                "full_rank": v["full_rank"],
                "T": v["T"],
                "W": v["W"],
                "margin": v["margin"],
                "decision_status": v["decision_status"],
                "export_status": v["export_http_status"],
            }
            for k, v in gt_summary.items()
        },
        "image_cross_check": image_cross_check,
        "containers": containers,
        "auditor_probes": {
            "total": aud_probes.get("total"),
            "passed": aud_probes.get("passed"),
            "failed": aud_probes.get("failed"),
        },
        "failure_branches_summary": {
            k: {
                "regression_assertions_passed": v.get("regression_assertions_passed"),
                "state_status": (v.get("state") or {}).get("status"),
                "state_passed": (v.get("state") or {}).get("passed"),
                "state_safety_passed": (v.get("state") or {}).get("safety_passed"),
                "state_error_code": (v.get("state") or {}).get("error_code"),
                "db_status": (v.get("database") or {}).get("status"),
                "db_failed_samples_count": (v.get("database") or {}).get("failed_samples_count"),
                "db_skipped_samples_count": (v.get("database") or {}).get("skipped_samples_count"),
            }
            for k, v in failure_branches.items()
        },
        "stage_matrix_metrics": stage_matrix.get("stage_metrics"),
        "score_contract": score_contract,
        "env_m4_01_audit_repro": env_audit.get("audit_assertions") | {"all_pass": env_audit.get("all_pass")},
        "readonly_business_db_check": {
            "materials_db_counts": mat_counts,
            "auth_db_counts": auth_counts,
            "residual_audit_users": residual_audit_users,
        },
    }

    (OUT / "evidence_binding.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("assets", "audit_dir_evidence", "gate_cross_check")
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
