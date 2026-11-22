"""Recheck M2's signed inventory and record the M3 delivery by exact bytes."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
PREFIX = "m3_r01_"
EDITS = [
    ".dockerignore", "Dockerfile", "app/flywheel_sandbox.py", "app/rule_governance.py", "app/rule_api.py",
    "app/report_generator.py", "app/server.py", "app/vector_index.py", "tests/conftest.py",
    "tests/eval_golden_set.py", "tests/test_flywheel_sandbox.py", "doc/V0.1.6/02_任务台账.md",
]
ADDITIONS = [
    "app/runtime_eval_assets.py", "app/sandbox_evaluator.py", "app/eval_assets/.gitattributes",
    "app/eval_assets/golden_runtime_v1.json", "app/eval_assets/report_template_v1.json",
    "app/eval_assets/smoke_materials_v1.json", "tests/test_v016_m3_runtime_assets.py",
    "doc/V0.1.6/rounds/V016-M3_R01_开发小结.md",
]


def digest(path):
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def compare_business(baseline):
    return {
        "files": {name: digest(ROOT / name) for name in baseline["files"]},
        "trees": {
            name: {
                "__exists__": (ROOT / name).is_dir(),
                "files": {p.relative_to(ROOT / name).as_posix(): digest(p)
                          for p in sorted((ROOT / name).rglob("*")) if p.is_file()},
            }
            for name in baseline["trees"]
        },
    }


def main():
    baseline = read_json(EVIDENCE / "coord_m2_r01/state_final.json")
    start = read_json(EVIDENCE / "m3_r01_baseline.json")
    m2_assets = read_json(EVIDENCE / "coord_m2_r01/evidence_binding.json")["assets"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == baseline["head"] == start["head"]
    business = compare_business(baseline["business"])
    assert business == baseline["business"] == start["business"]
    assert all(digest(ROOT / name) == expected for name, expected in m2_assets.items())
    assert all(digest(ROOT / ".local-archive/v016_m3_r01_pre_backup" / name) == expected
               for name, expected in start["backup"].items())
    protected_changes = sorted(name for name, expected in baseline["protected"].items()
                               if digest(ROOT / name) != expected)
    assert set(protected_changes) <= set(EDITS), protected_changes
    assert all(digest(ROOT / name) != start["backup"][name] for name in EDITS)

    layer = read_json(EVIDENCE / "m3_r01_image_layers.json")
    for layer_info in layer["layers"].values():
        for name, expected in layer_info["app_files"].items():
            assert digest(ROOT / name) == expected, f"Build input changed: {name}"
    assert layer["source_hashes_match"] and not layer["violations"]
    report = {
        "head": head, "business_unchanged_vs_m2": True, "business": business,
        "m2_protected_items": len(baseline["protected"]), "expected_protected_edits": protected_changes,
        "pre_backup_matches": len(start["backup"]), "m2_model_and_fixture_hashes_match": len(m2_assets),
        "image_id": layer["image_id"], "image_source_matches_delivery": True,
    }
    (EVIDENCE / "m3_r01_outer_snapshot_after.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    paths = set(EDITS + ADDITIONS)
    paths.update(p.relative_to(ROOT).as_posix() for p in EVIDENCE.glob(PREFIX + "*")
                 if p.is_file() and p.name != "m3_r01_delivery_hashes.md")
    rows = ["# V016-M3 R01 交付 SHA256", "", f"基线 HEAD `{head}` 与 M2 统筹冻结工作区。", "",
            "| 路径 | 字节 | SHA256 |", "|---|---:|---|"]
    for name in sorted(paths):
        p = ROOT / name
        rows.append(f"| `{name}` | {p.stat().st_size} | `{digest(p)}` |")
    (EVIDENCE / "m3_r01_delivery_hashes.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"Frozen {len(paths)} files. M2 protected inventory, model assets and business state verified.")


if __name__ == "__main__":
    main()
