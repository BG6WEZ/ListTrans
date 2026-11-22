"""Replay one M2 arm through authenticated K/BGE HTTP routes using Train/Dev."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from m2_r01_capture import EQUIVALENCES, EVIDENCE, ROOT, frozen_inputs, stamp
from app import acceptance_calibration
import m0_r01_replay as m0
from m0_r01_benchmark import make_workbook, response_json

SCOPE = "m0-golden.xlsx"
GUARDS = ("GT-0674", "GT-0677", "GT-0678")
PATHS = ("app/matcher.py", "app/server.py", "app/acceptance_calibration.py",
         "app/calibration_assets/v019_m2_policy.json",
         EQUIVALENCES.relative_to(ROOT).as_posix())


def binding(row, candidate):
    return {**{k: candidate[k] for k in ("code", "name", "specification", "unit", "category", "brand")},
            "qty": row["original_qty"], "qty_ratio": 1.0}


def probe_http(client, headers, rows):
    def post(path, payload):
        return client.post(path, headers=headers, json=payload)

    def export(row):
        return post("/api/export", {"items": [row], "scope_filename": SCOPE}).status_code

    def restore(row):
        resp = post("/api/session/annotate", {"items": [row], "scope_filename": SCOPE})
        restored, = response_json(resp)["items"]
        return restored["decision_status"]

    def claim(row, bind):
        return {**row, "bindings": [bind], "matched_code": bind["code"],
                "decision_status": "auto_accepted", "is_exact": True,
                "acceptance_calibration": {"confidence": 1, "outcome": "accepted"}}

    guards = {}
    for gid in GUARDS:
        row = rows[gid]
        forged = claim(row, binding(row, row["top_candidates"][0]))
        guards[gid] = {"status": row["decision_status"], "direct_export": export(row),
                       "client_claim_export": export(forged), "client_claim_restore": restore(forged)}

    # The frozen protective sample has a live, manually selectable target.
    row = rows[GUARDS[0]]
    bind = binding(row, row["top_candidates"][0])
    claimed = claim(row, bind)
    manual = {k: v for k, v in claimed.items() if k.startswith("original_") or k in ("bindings", "professional")}
    manual["scope_filename"] = SCOPE
    confirmed = post("/api/binding/submit", manual).status_code
    after_confirm = {"export": export(claimed), "restore": restore(claimed)}
    changed = claim(row, {**bind, "qty": bind["qty"] + 1})
    changed_name = {**claimed, "original_name": claimed["original_name"] + "复核"}
    changed_qty_result, changed_name_result = export(changed), export(changed_name)
    rejected = post("/api/binding/reject", manual).status_code
    after_reject = {"export": export(claimed), "restore": restore(claimed)}
    positive = {}
    for gid, accepted in rows.items():
        if accepted["decision_status"] == "auto_accepted":
            edited = claim(accepted, {**accepted["bindings"][0], "qty": accepted["original_qty"] + 1})
            positive[gid] = {"export": export(accepted), "restore": restore(accepted), "edited_export": export(edited)}
    return {
        "protected_unknown": guards, "confirmed_status": confirmed, "after_confirm": after_confirm,
        "changed_qty_after_confirmation_export": changed_qty_result,
        "changed_row_after_confirmation_export": changed_name_result,
        "rejected_status": rejected, "after_reject": after_reject, "automatic": positive,
    }


def run(mode, arm, output):
    os.environ["LISTTRANS_V019_M1_RERANK"] = "off"
    os.environ["LISTTRANS_V019_M2_CALIBRATION"] = arm
    sources = {p: m0.replay.sha(ROOT / p) for p in PATHS}
    _, partitions, items = frozen_inputs()
    ids = {item["id"] for item in items}
    single_detail, raw_upload, batch_detail = {}, {}, {}
    model_features = {}
    probes = {}
    active = ""
    save, phase = m0.replay.save_json, m0.replay.phase
    actor = None
    assess = acceptance_calibration.assess_match

    def observe_features(row, query, match, policy, **kwargs):
        result = assess(row, query, match, policy, **kwargs)
        if active == "batch_http":
            model_features[row["original_code"]] = result["features"]
        return result

    @contextmanager
    def observe_phase(report, key):
        nonlocal active
        active = key
        with phase(report, key):
            yield
        active = ""
        if key == "batch_http":
            probes.update(probe_http(*actor, raw_upload))

    def match(client, headers, item):
        if item["id"] not in ids:
            raise RuntimeError("Non-Train/Dev single row")
        start = perf_counter()
        response = client.post("/api/match", headers=headers, json={
            "name": item["name"], "text": item["specification"], "professional": item["professional"],
            "unit": item["unit"], "category": item["supply_category"], "brand": item.get("brand") or "",
        })
        elapsed = (perf_counter() - start) * 1000
        result = response_json(response)
        if active == "single_http":
            single_detail[item["id"]] = result.get("acceptance_calibration")
        return {"ms": elapsed, "top_code": next((c["code"] for c in result["top_candidates"]), None),
                "is_exact": result["is_exact"], "matched_code": result["matched_code"],
                "decision_status": result.get("decision_status"),
                "bindings": [b["code"] for b in result.get("bindings", [])]}

    def upload(client, headers, subset):
        nonlocal actor
        batch_ids = [it["id"] for it in subset]
        if not set(batch_ids) <= ids:
            raise RuntimeError("Non-Train/Dev upload row")
        workbook = make_workbook(subset)
        start = perf_counter()
        up = response_json(client.post("/api/upload", headers=headers, files={
            "file": (SCOPE, workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        }))
        state = response_json(client.get("/api/upload/status", headers=headers, params={"task_id": up["task_id"]}))
        elapsed = (perf_counter() - start) * 1000
        if state["status"] != "completed" or len(state["result"]) != len(subset):
            raise RuntimeError("Upload failed or dropped rows")
        out = {}
        for row in state["result"]:
            gid = row["original_code"]
            if gid in out or not row["source_row_id"] or not row["recommendation_snapshot_id"]:
                raise RuntimeError("Upload row identity or evidence missing")
            out[gid] = {"decision_status": row["decision_status"], "bindings": [b["code"] for b in row["bindings"]],
                        "top_code": next((c["code"] for c in row["top_candidates"]), None),
                        "served_domain": row["professional"], "is_exact": row["is_exact"]}
            if active == "batch_http":
                if gid in GUARDS or row["decision_status"] == "auto_accepted":
                    raw_upload[gid] = row
                batch_detail[gid] = row.get("acceptance_calibration")
        if set(out) != set(batch_ids):
            raise RuntimeError("Upload changed source row identity")
        actor = client, headers
        return out, {"ms": elapsed, "rows": len(subset), "ids": batch_ids}

    def complete(path, value):
        if any(m0.replay.sha(ROOT / p) != sha for p, sha in sources.items()):
            raise RuntimeError("Source changed during replay")
        if set(single_detail) != ids or set(batch_detail) != ids:
            raise RuntimeError("Replay lost split rows")
        if any((detail is not None) != (arm == "on") for detail in (*single_detail.values(), *batch_detail.values())):
            raise RuntimeError("Calibration not applied according to arm")
        if arm == "on":
            capture_path = EVIDENCE / f"m2_r01_{mode}_capture.json"
            captured = m0.replay.read_json(capture_path)["rows"]
            expected_features = {gid: tuple(row["features"]) for gid, row in captured.items()}
            if expected_features != model_features:
                raise RuntimeError("Final upload model features changed after fitting")
            value["fitting_features_reproduced"] = {
                "rows": len(model_features), "capture_file_sha256": m0.replay.sha(capture_path),
                "feature_matrix_sha256": stamp(model_features),
            }
        value.update(calibration_arm=arm, calibration_source_sha256=sources, file_probes=probes,
                     split_id_sha256={k: stamp(v) for k, v in partitions.items()},
                     acceptance_details={"match": single_detail, "upload": batch_detail})
        save(path, value)

    with (patch.object(acceptance_calibration, "assess_match", observe_features),
          patch.multiple(m0.replay, send_match=match, send_upload=upload, save_json=complete, phase=observe_phase)):
        m0.run(mode, "legacy", output)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("keyword", "vector"), required=True)
    p.add_argument("--arm", choices=("off", "on"), required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    run(args.mode, args.arm, args.out)
