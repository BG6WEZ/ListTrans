"""Generate and verify the RC documentation rollback and delivery hashes."""
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

from m3_r01_run import EDITABLE, EVIDENCE, ROOT, check_assets, collect_snapshot, read_json, sha256_file, write_json

PATCH = EVIDENCE / "m3_r01_rollback.patch"
GUARD = EVIDENCE / "m3_r01_asset_guard.json"
DELIVERY = EVIDENCE / "m3_r01_delivery_sha256.json"
SERIAL = EVIDENCE / "m3_r01_serial"
FOLLOWUP = EVIDENCE / "m3_r01_followup"
REPEAT = EVIDENCE / "m3_r01_repeat"


def copy_files(source, target):
    for name in EDITABLE:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, path)


def rollback(original, original_hashes):
    if original.parent.is_relative_to(ROOT):
        raise RuntimeError("Original must be from the external preflight copy")
    with TemporaryDirectory(prefix="listtrans_v020_m3_rollback_") as directory:
        temp = Path(directory).resolve()
        if temp.is_relative_to(ROOT):
            raise RuntimeError("Rollback must run outside the workspace")
        for name in EDITABLE:
            if sha256_file(original / name) != original_hashes[name]:
                raise RuntimeError(f"Original SHA256 mismatch: {name}")
        copy_files(original, temp / "old")
        copy_files(ROOT, temp / "new")
        git = ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]
        run = subprocess.run([*git, "diff", "--no-index", "--binary", "--no-ext-diff", "--", "old", "new"],
                             cwd=temp, capture_output=True)
        if run.returncode != 1 or not run.stdout or run.stderr:
            raise RuntimeError(f"Failed to generate rollback diff: EXIT={run.returncode} {run.stderr!r}")
        candidate = temp / PATCH.name
        candidate.write_bytes(run.stdout)
        apply = [*git, "apply", "-p2", "--binary", "--reverse", "--whitespace=nowarn"]
        for options in (["--check"], []):
            subprocess.run([*apply, *options, str(candidate)], cwd=temp / "new", check=True)
        restored = {name: sha256_file(temp / "new" / name) for name in EDITABLE}
        if restored != original_hashes:
            raise RuntimeError("Reverse patch failed byte-exact restoration")
        with candidate.open("rb") as source, PATCH.open("xb") as result:
            shutil.copyfileobj(source, result)
    return {"restore_matches": restored, "rollback_patch_sha256": sha256_file(PATCH),
            "reverse_apply_checked": True, "replay_location": "outside workspace"}


def redact_home_paths(paths):
    home = Path.home()
    spellings = (str(home), home.as_posix(), str(home).replace("\\", "\\\\"))
    pattern = re.compile(b"|".join(re.escape(value.encode("utf-8")) for value in spellings), re.I)
    replacements = 0
    for path in paths:
        raw = path.read_bytes()
        clean, count = pattern.subn(b"%USERPROFILE%", raw)
        if count:
            path.write_bytes(clean)
            replacements += count
    return replacements


def main():
    dispatch_file = EVIDENCE / "m3_r01_dispatch_baseline.json"
    dispatch = read_json(dispatch_file)
    baseline = dispatch["effective_baseline_sha256"]
    preflight = read_json(SERIAL / "m3_r01_preflight.json")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != dispatch["head"] or preflight["dispatch_sha256"] != sha256_file(dispatch_file):
        raise RuntimeError("Dispatch or HEAD changed since preflight")
    differences = sorted(name for name, expected in baseline.items() if sha256_file(ROOT / name) != expected)
    if differences != sorted(set(EDITABLE) & baseline.keys()):
        raise RuntimeError(f"Unexpected scope drift: {differences}")
    checkpoints = {"current": check_assets(dispatch, collect_snapshot())}
    for folder in (SERIAL, FOLLOWUP, REPEAT):
        for bound in ("before", "after"):
            report = read_json(folder / f"m3_r01_isolation_{bound}.json")
            if report["verdict"] != "PASS" or report["exit_code"] != 0:
                raise RuntimeError(f"Isolation did not pass: {folder.name}/{bound}")
            for point in ("before", "after"):
                checkpoints[f"{folder.name}_{bound}_{point}"] = check_assets(dispatch, report[f"business_snapshot_{point}"])
    restoration = rollback(Path(preflight["native_root"].replace("%USERPROFILE%", str(Path.home()))) / "original",
                           preflight["editable_original_sha256"])
    evidence_paths = [p for parent in (SERIAL, FOLLOWUP, REPEAT) for p in parent.rglob("*") if p.is_file()]
    redaction_count = redact_home_paths(evidence_paths)
    write_json(GUARD, {"head": head, "effective_baseline_count": len(baseline),
               "effective_baseline_differences": differences, "unchanged_effective_baseline": len(baseline) - len(differences),
               "local_profile_prefixes_redacted": redaction_count, "checkpoints": checkpoints, **restoration})
    paths = [*(ROOT / name for name in EDITABLE), *EVIDENCE.glob("m3_r01_*.py"),
             *EVIDENCE.glob("m3_r01_*.md"), EVIDENCE / "v020_release_candidate_manifest.md", PATCH, GUARD,
             EVIDENCE.parent / "rounds/V020-M3_R01_开发小结.md", *evidence_paths]
    files = {path.relative_to(ROOT).as_posix(): {"sha256": sha256_file(path), "bytes": path.stat().st_size}
             for path in sorted(paths)}
    content_digest = hashlib.sha256("\n".join(f"{name}  {entry['sha256']}" for name, entry in files.items()).encode()).hexdigest()
    write_json(DELIVERY, {"head": head, "dispatch_sha256": sha256_file(dispatch_file), "files": files,
                          "file_count": len(files), "file_list_sha256": content_digest})


if __name__ == "__main__":
    main()
