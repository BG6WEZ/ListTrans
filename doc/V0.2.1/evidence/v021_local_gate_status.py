"""Bind the local preproduction verdict to the audited image, measurements and account DB."""

import json
from pathlib import Path

from v021_preproduction_context import ROOT, sha


EVIDENCE = Path(__file__).resolve().parent


def load(name, *, optional=False):
    path = EVIDENCE / name
    if optional and not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def check_business():
    frozen = load("m3_r01_dispatch_baseline.json")["business_snapshot"]
    for name, digest in frozen["files"].items():
        path = ROOT / name
        actual = sha(path) if path.is_file() else None
        assert actual == digest, name
    count = sum(digest is not None for digest in frozen["files"].values())
    for folder, snapshot in frozen["trees"].items():
        path = ROOT / folder
        observed = {item.relative_to(path).as_posix(): sha(item) for item in path.rglob("*") if item.is_file()}
        assert path.exists() == snapshot["__exists__"] and observed == snapshot["files"], folder
        count += len(observed)
    assert count == 33
    return count


def main():
    image = load("v021_offline_image.json")
    assert image["exit_code"] == 0 and image["platform"] == "linux/amd64"
    smoke = {mode: load(f"v021_linux_smoke_{mode}.json") for mode in ("keyword", "vector")}
    environment = all(
        run["python"].startswith("3.11.") and (run["anonymous"], run["auth_me"], run["health"]) == (401, 200, 200)
        and len(run["image_calibration_sha256"]) == len(run["tampered_copies"]) == 2
        and all(item["policy_rejected"] and item["priority_unavailable"] and item["tier"] == "B"
                for item in run["tampered_copies"].values())
        for run in smoke.values()
    ) and smoke["vector"]["index"]["dimension"] == 512 and not smoke["keyword"]["index"]["enabled"]
    credentials = load("v021_prg_f11_local_actual.json", optional=True)
    prg = bool(credentials and credentials["exit_code"] == 0 and credentials["image_id"] == image["image_id"]
               and credentials["password_rotation"]["old_password"] == 400
               and credentials["password_rotation"]["new_token"] == 200
               and credentials["signing_key_rotation"]["old_signature"] == 401
               and credentials["signing_key_rotation"]["new_token"] == 200)
    performance = {}
    for mode in ("keyword", "vector"):
        runs = []
        for number in (1, 2, 3):
            name = f"v021_linux_perf_{mode}_{number}.json"
            raw = load(name, optional=True)
            if raw is None:
                continue
            assert raw["mode"] == mode and raw["single_count"] == 1220 and raw["complete_uploads"] == 24
            # The kernel high-water mark and live RSS sampler differ slightly on Linux.
            # Apply the unchanged ceiling to the more conservative of the two readings.
            observed = raw["observed"].copy()
            sampled = max(item["sampled_peak_rss_mib"] for item in raw["phases"].values())
            observed["peak_working_set_mib"] = max(observed["peak_working_set_mib"], sampled)
            runs.append({"round": number, "observed": observed, "limits": raw["signed_budget"],
                         "budget_pass": {key: value <= raw["signed_budget"][key] for key, value in observed.items()},
                         "evidence": name})
        performance[mode] = runs
    concurrent = load("v021_linux_concurrent_keyword_1.json", optional=True)
    accepted_perf = bool(
        all(len(runs) == 3 and all(all(run["budget_pass"].values()) for run in runs)
            for runs in performance.values())
        and concurrent and all(concurrent["budget_pass"].values())
    )
    verdict = {
        "scope": "user-designated local Docker Desktop Linux target only",
        "image_id": image["image_id"], "git_head": image["source"]["head"],
        "local_mandatory_gates_passed": {"PRG-F11": prg, "ENV-V017-01": environment, "A-M3-01": accepted_perf},
        "auth_database_sha256": credentials["auth_database_sha256"] if prg else None,
        "business_assets_unchanged": check_business(),
        "performance": performance, "concurrent_budget_pass": concurrent["budget_pass"] if concurrent else None,
        "production_release_authorized": False, "remote_push_authorized": False,
    }
    (EVIDENCE / "v021_local_gate_status.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    print(verdict["local_mandatory_gates_passed"])


if __name__ == "__main__":
    main()
