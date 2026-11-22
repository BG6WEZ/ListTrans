"""Run one frozen M1 profile through matching, /api/match and /api/upload."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from time import perf_counter_ns
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from app import candidate_reranker
import m0_r01_replay as m0
from m1_r01_capture import stamp


def run(mode, profile, output):
    fit = m0.replay.read_json(EVIDENCE / "m1_r01_offline_comparison.json")
    os.environ["LISTTRANS_V019_M1_RERANK"] = "off" if profile == "legacy" else profile
    rerank = candidate_reranker.rerank
    save_json = m0.replay.save_json
    diagnostic_rows = m0.replay.diagnostic_rows
    timings = []
    active_detail = []
    ranked_digest = None
    phase = m0.replay.phase
    source_hashes = {p: m0.replay.sha(ROOT / p) for p in (
        "app/matcher.py", "app/server.py", "app/candidate_reranker.py",
    )}
    model_hash = m0.replay.sha(candidate_reranker.MODEL_PATH)
    if model_hash != fit["model_sha256"]:
        raise RuntimeError("Reranker model changed after Dev screening")

    def time_rerank(*args, **kwargs):
        start = perf_counter_ns()
        detail = rerank(*args, **kwargs)
        if active_detail:
            timings.append((perf_counter_ns() - start) / 1000)
            if detail:
                active_detail[0] += detail["moved_count"]
        return detail

    def observe_ranking(items, governance):
        nonlocal ranked_digest
        active_detail.append(0)
        records, elapsed = diagnostic_rows(items, governance)
        ranked_digest = stamp({gid: row["top10"] for gid, row in records.items()})
        return records, elapsed

    def stop_rank_timing(*args, **kwargs):
        cm = phase(*args, **kwargs)
        if args[1] not in ("single_http", "batch_http"):
            return cm
        return untimed_http_phase(cm)

    @contextmanager
    def untimed_http_phase(cm):
        with patch.object(candidate_reranker, "rerank", rerank), cm:
            yield

    def complete(path, value):
        expected = fit["modes"][mode][profile]
        ref = m0.replay.read_json(EVIDENCE / f"m0_r01_{mode}_legacy_final.json")
        if ranked_digest != expected["top10_by_id_sha256"] or value["recall_order_sha256"] != ref["recall_order_sha256"]:
            raise RuntimeError("Live experiment changed the frozen pool or disagrees with offline ranking")
        if any(m0.replay.sha(ROOT / p) != h for p, h in source_hashes.items()) or model_hash != m0.replay.sha(candidate_reranker.MODEL_PATH):
            raise RuntimeError("Runtime source/model changed during measurement")
        if profile != "legacy" and len(timings) != 1060:
            raise RuntimeError("Expected one CPU timing per diagnostic row")
        cpu = sorted(timings)
        value.update({
            "source_sha256": source_hashes, "rerank_profile": profile, "model_sha256": model_hash,
            "model_bytes": candidate_reranker.MODEL_PATH.stat().st_size, "live_top10_sha256": ranked_digest,
            "rerank_cpu": {"n": len(cpu), "p50_us": cpu[(len(cpu)-1)//2] if cpu else None,
                           "p95_us": cpu[(len(cpu)*95+99)//100-1] if cpu else None,
                           "total_ms": sum(cpu) / 1000, "moved_slots": active_detail[0]},
        })
        save_json(path, value)

    with patch.object(candidate_reranker, "rerank", time_rerank), patch.multiple(
        m0.replay, save_json=complete, diagnostic_rows=observe_ranking, phase=stop_rank_timing,
    ):
        m0.run(mode, "legacy", output)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("keyword", "vector"), required=True)
    p.add_argument("--profile", choices=("legacy", *candidate_reranker.OPTIONS), required=True)
    p.add_argument("--out", type=Path, required=True)
    options = p.parse_args()
    run(options.mode, options.profile, options.out)
