"""Read-only V0.1.7 admission & V017-M0 R01 dispatch checks; writes only new V017 coordinator evidence.

Run with fixed venv: this_file.py before|after. No app imports or tests.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc/V0.1.7/evidence"
V016_COORD = ROOT / "doc/V0.1.6/evidence/coord_m4_r01"

DOCS = [
    "doc/README.md",
    "doc/00_项目管理/版本路线图.md",
    "doc/00_项目管理/进度总表.md",
    "doc/00_项目管理/问题与版本归属总台账.md",
    "doc/V0.1.6/03_终审与版本准入.md",
    "doc/V0.1.7/01_实施方案与任务包.md",
    "doc/V0.1.7/02_任务台账.md",
    "doc/V0.1.7/03_终审与版本准入.md",
    "doc/V0.1.7/rounds/V017-M0_R01_统筹下发.md",
]

ALLOWED_V016_DELIVERY_CHANGES = set(DOCS[:5])


def sha(path: Path):
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def current_business_snapshot():
    spec = importlib.util.spec_from_file_location(
        "coord_snapshot", ROOT / ".local-archive/business_snapshot.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.snapshot()


def main(phase: str) -> int:
    target = EV / f"m0_r01_dispatch_{phase}.json"
    if phase not in ("before", "after") or target.exists():
        raise SystemExit("Unknown phase or existing evidence: refusing overwrite")

    state_final = json.loads((V016_COORD / "state_final.json").read_text(encoding="utf-8"))
    binding = json.loads((V016_COORD / "evidence_binding.json").read_text(encoding="utf-8"))
    delivery = json.loads((V016_COORD / "coordinator_delivery.json").read_text(encoding="utf-8"))

    protected_mismatches = [
        p for p, h in state_final["protected"].items() if sha(ROOT / p) != h
    ]
    asset_mismatches = [
        p for p, h in binding["assets"].items() if sha(ROOT / p) != h
    ]
    eval_asset_mismatches = [
        p
        for p, meta in binding["runtime_eval_assets"].items()
        if sha(ROOT / p) != meta["sha256"]
    ]
    delivery_changed = [
        p
        for p, meta in delivery["files"].items()
        if sha(ROOT / p) != meta["sha256"]
    ]
    unexpected_delivery_changes = (
        delivery_changed
        if phase == "before"
        else [p for p in delivery_changed if p not in ALLOWED_V016_DELIVERY_CHANGES]
    )

    business_now = current_business_snapshot()
    business_equal = business_now == state_final["business"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()

    result = {
        "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "role": "coordinator",
        "phase": phase,
        "head": head,
        "head_matches_v016_final": head == delivery["head"],
        "v016_protected_checked": len(state_final["protected"]),
        "v016_protected_mismatches": protected_mismatches,
        "offline_assets_checked": len(binding["assets"]),
        "offline_assets_mismatches": asset_mismatches,
        "eval_assets_checked": len(binding["runtime_eval_assets"]),
        "eval_assets_mismatches": eval_asset_mismatches,
        "v016_delivery_checked": len(delivery["files"]),
        "v016_delivery_changed": delivery_changed,
        "unexpected_v016_delivery_changes": unexpected_delivery_changes,
        "business_snapshot_equal": business_equal,
        "document_hashes": {p: sha(ROOT / p) for p in DOCS},
        "v016_delivery_manifest_sha256": sha(V016_COORD / "coordinator_delivery.json"),
        "tests_rerun": False,
        "production_operations": False,
    }

    okay = (
        result["head_matches_v016_final"]
        and not protected_mismatches
        and not asset_mismatches
        and not eval_asset_mismatches
        and not unexpected_delivery_changes
        and business_equal
    )

    if phase == "after":
        before = json.loads((EV / "m0_r01_dispatch_before.json").read_text(encoding="utf-8"))
        result["before_verdict"] = before["integrity_verdict"]
        result["v016_delivery_manifest_unchanged"] = (
            before["v016_delivery_manifest_sha256"] == result["v016_delivery_manifest_sha256"]
        )
        result["documents_missing"] = [p for p in DOCS if not (ROOT / p).is_file()]
        checked_links = 0
        broken = []
        planned = {
            target.resolve(),
            (EV / "m0_r01_dispatch_hashes.md").resolve(),
        }
        for rel in DOCS:
            fp = ROOT / rel
            text = fp.read_text(encoding="utf-8")
            for m in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
                raw_link = m.group(1)
                link_path = raw_link.split("#", 1)[0].strip()
                if not link_path or re.match(r"^[a-zA-Z]+://", link_path):
                    continue
                resolved = (fp.parent / link_path).resolve()
                checked_links += 1
                if not resolved.exists() and resolved not in planned:
                    broken.append({"doc": rel, "link": raw_link})
        result["checked_relative_links"] = checked_links
        result["broken_links"] = broken
        okay = (
            okay
            and before["integrity_verdict"] == "PASS"
            and result["v016_delivery_manifest_unchanged"]
            and not result["documents_missing"]
            and not broken
        )

    result["integrity_verdict"] = "PASS" if okay else "FAIL"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if phase == "after" and okay:
        files = DOCS + [
            "doc/V0.1.7/evidence/" + name
            for name in (
                "m0_r01_dispatch_check.py",
                "m0_r01_dispatch_before.json",
                "m0_r01_dispatch_after.json",
            )
        ]
        lines = [
            "# V017-M0 R01 统筹准入与下发 SHA256",
            "",
            "2026-11-18；仅管理文档与只读核验，未开发/未审核本包。清单不包含自身。",
            "",
            "| 文件 | 字节数 | SHA256 |",
            "|---|---:|---|",
        ]
        lines += [
            f"| `{p}` | {(ROOT / p).stat().st_size} | `{sha(ROOT / p)}` |"
            for p in files
        ]
        (EV / "m0_r01_dispatch_hashes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "document_hashes"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if okay else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
