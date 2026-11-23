"""Stage signed read-only inputs and execute isolated checks inside the audited RC image."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
V017 = ROOT / "doc/V0.1.7/evidence"
THIS = Path(__file__).resolve().relative_to(ROOT)
FINAL = "doc/V0.2.1/evidence/v021_final_coord_verify.json"
CALIBRATION = {
    "v019_m2_policy.json": "63c78ad224cfd4ccac6d31473b6c008ab4a897e0cb038ed1099cbe25eeb66981",
    "v019_m2_equivalences.json": "a6062d0b3a465bb8f64eefc7c36c8d54aaae192bb6e5b1efeb0c45f565928120",
}
BUDGETS = {
    "keyword": (30, 300, 1750, 10000, 768),
    "vector": (90, 375, 6000, 12500, 2048),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def json_at(path):
    return json.loads(path.read_bytes())


def stage_inputs(destination):
    before = json_at(V017 / "m0_r01_before.json")
    governance_path = "tests/fixtures/golden_governance_v1.json"
    governance = json_at(ROOT / governance_path)
    expected = {
        key: digest for key, digest in before["protected_sha256"].items()
        if key.startswith("model_offline/bge-small-zh-v1.5/")
    }
    expected.update(governance["inputs"])
    dispatch = json_at(ROOT / "doc/V0.2.1/evidence/m3_r01_dispatch_baseline.json")
    expected[governance_path] = dispatch["effective_baseline_sha256"][governance_path]
    for folder in ("config", "app/vector_cache"):
        expected.update({f"{folder}/{name}": digest
                         for name, digest in before["business"]["trees"][folder]["files"].items()})
    scripts = ["m0_r01_before.json", "m0_r01_benchmark.py", "m0_r01_build_governance.py", "m3_r01_load.py"]
    support = [FINAL, THIS.as_posix(), *(f"doc/V0.1.7/evidence/{name}" for name in scripts)]
    if not expected or any(sha(ROOT / name) != digest for name, digest in expected.items()):
        raise ValueError("Signed workload has drifted")
    destination.mkdir(parents=True, exist_ok=False)
    for name in (*expected, *support):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    print(json.dumps({"signed_input_count": len(expected), "support_files": len(support)}))


def quantile(samples, percent):
    return sorted(samples)[math.ceil(len(samples) * percent) - 1]


def summarize_performance(bench, root, mode):
    import resource

    raw = bench.run(SimpleNamespace(root=root, mode=mode,
                                    governance=ROOT / "tests/fixtures/golden_governance_v1.json", batch_size=50))
    singles = [row["ms"] for row in raw["single"].values()]
    full = [row["ms"] for row in raw["uploads"] if row["rows"] == 50]
    assert len(singles) == len(raw["batch"]) == len(raw["diagnostics"]) == 1220
    assert len(full) == 24 and len(raw["uploads"]) == 25
    assert not raw["index"].get("faiss", False)
    limits = dict(zip(("single_p50_ms", "single_p95_ms", "upload50_p50_ms", "upload50_p95_ms",
                       "peak_working_set_mib"), BUDGETS[mode], strict=True))
    measurements = (*[quantile(singles, p) for p in (.50, .95)],
                    *[quantile(full, p) for p in (.50, .95)],
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    observed = dict(zip(limits, measurements, strict=True))
    return {
        "scope": "V017 signed workload on local Docker", "mode": mode, "single_count": len(singles),
        "complete_uploads": len(full), "tail_rows": raw["uploads"][-1]["rows"], "index": raw["index"],
        "semantic_rows": raw["semantic_rows"], "hardware": raw["hardware"], "phases": raw["phases"],
        "observed": observed, "signed_budget": limits,
        "budget_pass": {key: observed[key] <= limit for key, limit in limits.items()},
        "single_ms": singles, "full_upload_ms": full, "peak_source": "Linux ru_maxrss, KiB divided by 1024",
    }


def calibration_probe():
    from app import acceptance_calibration as cal
    from app.match_assessment import assess_initial_match

    observed = {name: sha(cal.ASSETS / name) for name in sorted(CALIBRATION)}
    if observed != CALIBRATION or {p.name for p in cal.ASSETS.iterdir()} != CALIBRATION.keys():
        raise ValueError("Container calibration assets differ from the signed pair")
    original = cal.ASSETS
    with TemporaryDirectory(prefix="v021-cal-") as directory:
        results = {}
        for asset in observed:
            cal.ASSETS = Path(directory)
            for name in observed:
                content = (original / name).read_bytes()
                (cal.ASSETS / name).write_bytes(content + b"\n" if name == asset else content)
            cal.load_policy.cache_clear()
            policy = cal.load_policy(require_frozen=True)
            top = {"code": "SYNTH", "name": "样例物料", "specification": "示例", "unit": "个", "score": 95}
            row = {"original_name": top["name"], "original_spec": top["specification"], "original_unit": top["unit"]}
            match = {"top_candidates": [top], "_initial_match_rank": {
                "top_code": top["code"], "ranking_score": 95, "margin": 12, "uncapped": True,
            }}
            initial = assess_initial_match(row, {}, match, {"bindings": [top], "decision_status": "recommended"},
                                           "matcher", policy, {top["code"]: top}, 1)
            assert policy is None and initial["tier"] == "B" and initial["priority_review_unavailable"]
            results[asset] = {"policy_rejected": True, "tier": initial["tier"], "priority_unavailable": True}
        cal.ASSETS = original
        cal.load_policy.cache_clear()
        assert cal.load_policy(require_frozen=True) is not None
    return {"image_calibration_sha256": observed, "tampered_copies": results}


def smoke(bench, root, mode):
    bench.stage(root, mode, json_at(V017 / "m0_r01_before.json"))
    os.environ.update(TESTING="0", APP_ENV="production", ENABLE_DOCS="false",
                      SECRET_KEY=secrets.token_urlsafe(48), ADMIN_USERNAME="rc-smoke",
                      ADMIN_PASSWORD=secrets.token_urlsafe(32) + "!R7")
    sys.addaudithook(bench.block_external_network)
    from fastapi.testclient import TestClient
    from app.server import app
    from app.vector_index import index_manager

    materials, _ = bench.read_catalog()
    with TestClient(app) as client:
        index = bench.checked_index(mode, index_manager, materials)
        anonymous = client.get("/api/auth/me").status_code
        response = client.post("/api/auth/login", json={"username": os.environ["ADMIN_USERNAME"],
                                                        "password": os.environ["ADMIN_PASSWORD"]})
        headers = {"Authorization": "Bearer " + bench.response_json(response)["access_token"]}
        authenticated = client.get("/api/auth/me", headers=headers).status_code
        health = client.get("/api/admin/health", headers=headers).status_code
        assert (anonymous, authenticated, health) == (401, 200, 200)
        example = {"id": "SMOKE", "name": "不锈钢法兰", "specification": "DN50", "professional": "暖通",
                   "unit": "个", "supply_category": ""}
        bench.send_match(client, headers, example)
        rows, _ = bench.send_upload(client, headers, [example])
        assert set(rows) == {"SMOKE"}
        return {"mode": mode, "python": sys.version.split()[0], "index": index, "login": 200,
                "anonymous": anonymous, "auth_me": authenticated, "health": health,
                "single_and_upload": 200, **calibration_probe()}


def main(args):
    if args.stage:
        stage_inputs(args.stage)
        return 0
    sys.path.insert(0, str(V017))
    import m0_r01_benchmark as bench

    os.environ.update(json_at(ROOT / FINAL)["default_switches_frozen_off"])
    if not sys.platform.startswith("linux") or sys.version_info[:2] != (3, 11):
        raise RuntimeError("Use the audited Linux Python 3.11 image")
    with TemporaryDirectory(prefix="v021-signed-") as directory:
        root = Path(directory) / "isolated"
        if args.run == "perf":
            result = summarize_performance(bench, root, args.mode)
        elif args.run == "concurrent":
            from m3_r01_load import run

            result = run(root)
            result["budget_pass"] = {
                "health_p95": result["probes"]["health"]["p95_ms"] <= 450,
                "status_p95": result["probes"]["status"]["p95_ms"] <= 175,
                "match_queue_wait_p95": result["queues"]["match"]["wait"]["p95_ms"] <= 23000,
                "each_probe_below_2000": result["probe_gate_passed"],
            }
        else:
            result = smoke(bench, root, args.mode)
    print("V021_GATE_JSON " + json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    return 0 if all(result.get("budget_pass", {"smoke": True}).values()) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--stage", type=Path)
    command.add_argument("--run", choices=("smoke", "perf", "concurrent"))
    parser.add_argument("--mode", choices=("keyword", "vector"), default="keyword")
    raise SystemExit(main(parser.parse_args()))
