"""Record M4 baselines and freeze exact delivery bytes; never imports app modules."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
EDITS = ["app/matcher.py", "app/sandbox_evaluator.py", "app/flywheel_sandbox.py",
         "app/server.py", "tests/test_v0_0_8_llm_rules.py", "doc/V0.1.6/02_任务台账.md"]
BACKUP = ROOT / ".local-archive/v016_m4_r01_pre_backup"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def business(reference):
    return {"files": {p: digest(ROOT / p) for p in reference["files"]}, "trees": {
        name: {"__exists__": (ROOT / name).is_dir(), "files": {
            p.relative_to(ROOT / name).as_posix(): digest(p)
            for p in sorted((ROOT / name).rglob("*")) if p.is_file()}}
        for name in reference["trees"]}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["before", "after"])
    args = parser.parse_args()
    signed = read(EVIDENCE / "coord_m3_r01/state_final.json")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == signed["head"]
    current_business = business(signed["business"])
    assert current_business == signed["business"], "Outer business snapshot drift"
    changed = [p for p, sha in signed["protected"].items() if digest(ROOT / p) != sha]
    if args.mode == "before":
        assert not changed, changed
        assert not BACKUP.exists(), "Refuse to overwrite previous backup"
        BACKUP.mkdir(parents=True)
        for name in EDITS:
            dest = BACKUP / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
        report = {"head": head, "protected_count": len(signed["protected"]),
                  "protected_mismatches": changed, "business": current_business,
                  "backup": {p: digest(BACKUP / p) for p in EDITS}}
        with (EVIDENCE / "m4_r01_baseline.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    else:
        assert set(changed) <= set(EDITS), changed
        before = read(EVIDENCE / "m4_r01_baseline.json")
        assert all(digest(BACKUP / p) == sha for p, sha in before["backup"].items())
        assets = read(EVIDENCE / "coord_m2_r01/evidence_binding.json")["assets"]
        assert all(digest(ROOT / p) == sha for p, sha in assets.items()), "Frozen model/fixture changed"
        report = {"head": head, "business": current_business, "business_unchanged": True,
                  "changed_protected": changed, "backup_verified": True, "frozen_model_fixture_assets": len(assets),
                  "changed_from_pre_backup": [p for p, sha in before["backup"].items() if digest(ROOT / p) != sha],
                  "m3_signed_state_sha256": digest(EVIDENCE / "coord_m3_r01/state_final.json")}
        (EVIDENCE / "m4_r01_outer_snapshot_after.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        paths = set(changed + EDITS)
        paths.update(p.relative_to(ROOT).as_posix() for p in EVIDENCE.glob("m4_r01_*")
                     if p.is_file() and p.name != "m4_r01_delivery_hashes.md")
        paths.update(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_v016_m4*.py"))
        paths.add("doc/V0.1.6/rounds/V016-M4_R01_开发小结.md")
        rows = ["# V016-M4 R01 交付 SHA256", "", f"HEAD `{head}` + M3 统筹冻结工作区。", "",
                "| 路径 | 字节 | SHA256 |", "|---|---:|---|"]
        for name in sorted(paths):
            path = ROOT / name
            rows.append(f"| `{name}` | {path.stat().st_size} | `{digest(path)}` |")
        (EVIDENCE / "m4_r01_delivery_hashes.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"Frozen {len(paths)} files")
    print(json.dumps({"mode": args.mode, "protected_checked": len(signed["protected"]),
                      "changed": changed, "business_unchanged": True}))


if __name__ == "__main__":
    main()
