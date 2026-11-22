"""V021-M3 experimental CPU reranking with an isolated, local-only model worker."""

import atexit
from datetime import date
import hashlib
import math
import multiprocessing
import os
from pathlib import Path
import sys
from threading import Lock
from time import monotonic


SWITCH = "LISTTRANS_V021_M3_RERANKER"
MODEL_DIR = Path(__file__).resolve().parents[1] / "model_offline" / "bge-reranker-base"
MODEL_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"
ASSET_SHA256 = {
    ".gitattributes": "8ae7b365d432d1158942502ae33bd7b566db27b95424ea11675066b58d80fa65",
    "README.md": "b4223c8c1e95538f8c79168afc501f8b2da06c63221f013b79b28af78b59366d",
    "config.json": "289adf7ada1eb6b4afa7589a48a032d45a076cf2e46dcdb3b4cabc33be14f708",
    "model.safetensors": "ced967c45fd1902eb92716c9ceeca7c95a936770ea9db611f5a841b926e33fbd",
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
    "special_tokens_map.json": "d5469a60db23249c7f8945013d78df30b44b6bf686c6bb4740f4223f77b1b535",
    "tokenizer.json": "9eb652ac4e40cc093272bbbe0f55d521cf67570060227109b5cdc20945a4489e",
    "tokenizer_config.json": "a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b",
}
TOP_K = 20
MAX_LENGTH = 128
MAX_TEXT_CHARS = 512
MAX_SCORE_SHIFT = 3.0
CPU_THREADS = 4
LOAD_TIMEOUT_SECONDS = 45.0
INFERENCE_TIMEOUT_SECONDS = 2.0
_QUERY_FIELDS = ("name", "specification", "unit")
_CANDIDATE_FIELDS = ("name", "specification", "unit")
_SOCKET_EVENTS = {
    "socket.connect", "socket.sendto", "socket.getaddrinfo", "socket.gethostbyname",
    "socket.gethostbyaddr", "socket.getnameinfo",
}


class OfflineFailure(Exception):
    """A stable, data-free failure code safe to include in diagnostics."""


def _verify_assets(directory):
    if not directory.is_dir():
        raise OfflineFailure("model_directory_missing")
    if directory.is_symlink() or {p.name for p in directory.iterdir()} - ASSET_SHA256.keys() - {".cache"}:
        raise OfflineFailure("unexpected_model_asset")
    for name, expected in ASSET_SHA256.items():
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise OfflineFailure("model_asset_missing")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise OfflineFailure("model_hash_mismatch")


def _reject_network(event, args):
    if event in _SOCKET_EVENTS:
        raise OfflineFailure("network_blocked")


def _load_local_model(directory):
    _verify_assets(directory)
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(CPU_THREADS)
    torch.set_num_interop_threads(1)
    local = {"local_files_only": True, "trust_remote_code": False}
    tokenizer = AutoTokenizer.from_pretrained(str(directory), use_fast=True, **local)
    model = AutoModelForSequenceClassification.from_pretrained(
        str(directory), use_safetensors=True, **local,
    ).to("cpu").eval()

    def predict(query, texts):
        batch = tokenizer([query] * len(texts), texts, padding=True, truncation=True,
                          max_length=MAX_LENGTH, return_tensors="pt")
        with torch.inference_mode():
            logits = model(**batch).logits.squeeze(-1)
            if logits.shape != (len(texts),) or not torch.isfinite(logits).all().item():
                raise OfflineFailure("invalid_prediction")
            return logits.sigmoid().tolist()

    return predict


def _worker(connection, directory):
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      HF_HUB_DISABLE_PROGRESS_BARS="1", TOKENIZERS_PARALLELISM="false")
    sys.addaudithook(_reject_network)
    phase = "load"
    try:
        predict = _load_local_model(directory)
        connection.send(("ready", None))
        phase = "inference"
        while True:
            query, texts = connection.recv()
            connection.send(("ok", predict(query, texts)))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as error:
        code = str(error) if isinstance(error, OfflineFailure) else f"{phase}_error"
        try:
            connection.send(("failure", code))
        except (EOFError, OSError):
            pass
    finally:
        connection.close()


class OfflineWorker:
    def __init__(self, directory):
        self.directory = directory
        self.lock = Lock()
        self.process = None
        self.connection = None
        self.failure = None

    def _receive(self, expected, deadline, timeout_code):
        if not self.connection.poll(max(0, deadline - monotonic())):
            raise OfflineFailure(timeout_code)
        status, data = self.connection.recv()
        if status != expected:
            raise OfflineFailure(data if status == "failure" else "worker_protocol_error")
        return data

    def _start(self):
        ctx = multiprocessing.get_context("spawn")
        self.connection, worker_end = ctx.Pipe()
        try:
            self.process = ctx.Process(target=_worker, args=(worker_end, self.directory), daemon=True)
            deadline = monotonic() + LOAD_TIMEOUT_SECONDS
            self.process.start()
        finally:
            worker_end.close()
        self._receive("ready", deadline, "load_timeout")

    def _stop(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        process, self.process = self.process, None
        if process is not None and process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.2)
                if process.is_alive():
                    process.kill()
            process.join(timeout=0.2)
            if not process.is_alive():
                process.close()

    def close(self):
        with self.lock:
            self._stop()

    def predict(self, query, texts):
        if not self.lock.acquire(blocking=False):
            raise OfflineFailure("worker_busy")
        try:
            if self.failure:
                raise OfflineFailure(self.failure)
            if self.process is None:
                self._start()
            deadline = monotonic() + INFERENCE_TIMEOUT_SECONDS
            self.connection.send((query, texts))
            scores = self._receive("ok", deadline, "inference_timeout")
            if len(scores) != len(texts) or any(not math.isfinite(score) or not 0 <= score <= 1
                                                for score in scores):
                raise OfflineFailure("invalid_prediction")
            return scores
        except Exception as error:
            self.failure = str(error) if isinstance(error, OfflineFailure) else "model_worker_error"
            self._stop()
            raise OfflineFailure(self.failure) from error
        finally:
            self.lock.release()


_MODEL = OfflineWorker(MODEL_DIR)
atexit.register(_MODEL.close)


def _text(values, fields):
    parts = [str(values.get(field) or "").strip() for field in fields]
    return " ".join(filter(None, parts))[:MAX_TEXT_CHARS]


def _active(material):
    from app.db_manager import parse_disable_annotation

    for field in ("code", "name", "specification", "category", "description"):
        marker, end, _ = parse_disable_annotation(str(material.get(field) or ""))
        if marker and (end is None or end <= date.today()):
            return False
    return True


def _movable_prefix(query, candidates, capped_codes):
    from app.acceptance_calibration import SAFETY_FIELDS, _physical_evidence
    from app.binding_decisions import evaluate_hard_conflicts, fetch_live_material_map

    row = {"original_name": query.get("original_name", query.get("name")),
           "original_spec": query.get("original_spec", query.get("specification")),
           "original_unit": query.get("original_unit", query.get("unit")),
           "original_brand": query.get("original_brand", query.get("brand")),
           "professional": query.get("professional")}
    prefix = candidates[:TOP_K]
    live = fetch_live_material_map([candidate["code"] for candidate in prefix])
    movable = []
    for candidate in prefix:
        code, score = candidate["code"], candidate["score"]
        material = live.get(code)
        if (candidate.get("hard_blocked") or code in capped_codes or candidate.get("max_score") != 1
                or not math.isfinite(score) or score <= 0 or material is None or not _active(material)):
            break
        comparisons = _physical_evidence(row, query, material)["comparisons"]
        if (any(field["requested_unverified"] or
                (field["field"] in SAFETY_FIELDS and field["state"] != "equal") for field in comparisons)
                or evaluate_hard_conflicts(row, material)):
            break
        movable.append(candidate)
    return movable


def rerank(query, candidates, capped_codes):
    """Reorder a movable prefix in place only after a complete, valid prediction."""
    if len(candidates) < 2 or any("exact_code" in (c.get("recall_sources") or ()) for c in candidates):
        return {"status": "skipped", "reason": "direct_code_or_short_pool"}
    try:
        movable = _movable_prefix(query, candidates, capped_codes)
        if len(movable) < 2 or movable[0]["score"] <= movable[-1]["score"]:
            return {"status": "skipped", "reason": "no_movable_range"}
        query_text = _text(query, _QUERY_FIELDS)
        texts = [_text(c, _CANDIDATE_FIELDS) for c in movable]
        scores = _MODEL.predict(query_text, texts)
        low, high = movable[-1]["score"], movable[0]["score"]
        adjusted = [
            {**candidate, "score": min(high, max(low, candidate["score"] + MAX_SCORE_SHIFT * (2 * score - 1)))}
            for candidate, score in zip(movable, scores, strict=True)
        ]
        adjusted.sort(key=lambda candidate: candidate["score"], reverse=True)
    except Exception as error:
        reason = str(error) if isinstance(error, OfflineFailure) else "rerank_error"
        return {"status": "degraded", "reason": reason}
    candidates[:len(adjusted)] = adjusted
    return {"status": "applied", "candidate_count": len(adjusted),
            "moved_count": sum(a["code"] != b["code"] for a, b in zip(movable, adjusted, strict=True))}
