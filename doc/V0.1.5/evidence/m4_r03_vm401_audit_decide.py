"""Classify every broad-scanner candidate using independent recorded provenance.
Retains raw scanner observations; never relabels public sample bytes as absent.
"""
from pathlib import Path, PurePosixPath
import hashlib
import json

EV = Path(__file__).resolve().parent
ROOT = EV.parents[2]
raw = json.loads((EV / "m4_r03_vm401_audit_result.json").read_text(encoding="utf-8"))
proof = json.loads((EV / "m4_r03_vm401_fixture_provenance.json").read_text(encoding="utf-8"))
public = json.loads((EV / "m4_r03_vm401_public_fixture_verify.json").read_text(encoding="utf-8"))
provided = json.loads((EV / "m4_r03_vm401_prod_image_verify.json").read_text(encoding="utf-8"))
private_paths = {x["path"] for x in raw["inspection"]["private_key_material_hits_all_layers"]}
expected_paths = {str(PurePosixPath(x["source"]).parent / "__pycache__" / (PurePosixPath(x["source"]).stem + ".cpython-311.pyc")).lstrip("/")
                  for x in proof["tests"]}
classified_paths = {x["path"] for x in proof["ca_candidates"] + proof["module_candidates"]}
unknown_paths = {x["path"] for x in raw["inspection"]["unexplained_outside_candidates"]}
classifications = {
    "exact_private_marker_paths_identified": private_paths == expected_paths and len(private_paths) == 2,
    "public_ecdsa_source_verified": proof["version"] == "0.19.2" and public["verdict"] == "PASS"
        and len(public["source_checks"]) == 2 and all(x["matches_image_source"] for x in public["source_checks"])
        and all(x["source_matches_wheel_record"] and x["compiled_samples_equal_source_constants"] for x in proof["tests"]),
    "all_outside_candidates_classified": classified_paths == unknown_paths,
    "system_ca_public": all(x["public_ca_certificate"] for x in proof["ca_candidates"]),
    "credential_named_modules_are_distribution_code": all(x["source_matches_wheel_record"] and x["compiled_module_matches_source"] for x in proof["module_candidates"]),
    "workspace_source_still_frozen": all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h
        for p, h in provided["frozen_source_binding"]["source_hashes"].items()),
}
technical = {k: v for k, v in raw["checks"].items() if k not in ("private_key_material_absent", "global_candidates_accounted")}
verdict = "PASS" if all(technical.values()) and all(classifications.values()) else "PENDING_VERIFY"
report = {"gate": "V-M4-01", "verdict": verdict, "image_id": raw["image_id"],
          "technical_checks": technical, "candidate_classification_checks": classifications,
          "classification_summary": {"public_dependency_csv_files": raw["inspection"]["dependency_test_csv_count"],
              "public_certifi_ca_bundles": raw["inspection"]["public_ca_bundle_count"],
              "system_public_ca_paths": len(proof["ca_candidates"]),
              "credential_named_code_files": len(proof["module_candidates"]),
              "public_test_bytecode_files_with_key_samples": len(private_paths)},
          "raw_scanner_report_preserved": "m4_r03_vm401_audit_result.json",
          "scope": "No project/business/operations credentials found. Public dependency fixture material is explicitly classified, not claimed absent.",
          "production_credential_rotation_verified": False, "V-M4-02": "PENDING_VERIFY", "F11": "OPEN", "M4": "PENDING_VERIFY"}
(EV / "m4_r03_vm401_audit_decision.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report))
