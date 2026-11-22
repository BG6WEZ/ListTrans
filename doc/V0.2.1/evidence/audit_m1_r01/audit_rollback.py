# -*- coding: utf-8 -*-
"""Independent auditor re-enactment of the M1 web-only byte-exact rollback.

Does NOT trust the dev rollback JSON. Re-derives everything:
  * HEAD and frozen R2 plan still match the dispatch baseline.
  * Every non-web baseline file already equals its M1 starting hash.
  * The delivered workbench patch hashes to the recorded patch_sha256.
  * In a fresh temp dir, reverse-apply the patch to the CURRENT web files and
    assert the restored bytes equal the M1 starting baseline (CRLF form).
Writes its own verdict to audit_m1_r01/audit_rollback.json.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[4]
EV = ROOT / "doc" / "V0.2.1" / "evidence"
WEB = ("web/app.js", "web/index.css", "web/index.html")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    baseline = json.loads((EV / "m1_r01_dispatch_baseline.json").read_text(encoding="utf-8"))
    dev_rollback = json.loads((EV / "m1_r01_rollback.json").read_text(encoding="utf-8"))
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    frozen = baseline["effective_baseline_sha256"]

    head_ok = head == baseline["head"]
    plan_ok = sha((ROOT / baseline["plan_r2_path"]).read_bytes()) == baseline["plan_r2_sha256"]

    # All non-web baseline files must already be byte-identical to starting state.
    nonweb_violations = []
    for name, expected in frozen.items():
        if name not in WEB and sha((ROOT / name).read_bytes()) != expected:
            nonweb_violations.append(name)

    patch_path = EV / "m1_r01_workbench.patch"
    patch_bytes = patch_path.read_bytes()
    patch_sha_ok = sha(patch_bytes) == dev_rollback["patch_sha256"]

    with TemporaryDirectory(prefix="audit_m1_rollback_") as directory:
        checkout = Path(directory)
        (checkout / "web").mkdir()
        for name in WEB:
            shutil.copyfile(ROOT / name, checkout / name)
        apply = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "apply", "--reverse",
             "--whitespace=nowarn", str(patch_path)],
            cwd=checkout, capture_output=True, text=True,
        )
        apply_ok = apply.returncode == 0
        restored = {name: sha((checkout / name).read_bytes()) for name in WEB}

    expected_web = {name: frozen[name] for name in WEB}
    reverse_byte_identical = restored == expected_web

    verdict = {
        "head": head,
        "head_matches_baseline": head_ok,
        "frozen_plan_matches": plan_ok,
        "nonweb_baseline_violations": nonweb_violations,
        "nonweb_baseline_all_identical": not nonweb_violations,
        "patch_sha256": sha(patch_bytes),
        "patch_sha_matches_dev_record": patch_sha_ok,
        "reverse_apply_ok": apply_ok,
        "apply_stderr": apply.stderr.strip(),
        "restored_web_sha256": restored,
        "expected_m1_start_sha256": expected_web,
        "reverse_byte_identical": reverse_byte_identical,
        "exit_code": 0 if (head_ok and plan_ok and not nonweb_violations
                           and patch_sha_ok and apply_ok and reverse_byte_identical) else 1,
    }
    out = EV / "audit_m1_r01" / "audit_rollback.json"
    out.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return verdict["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
