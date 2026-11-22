# -*- coding: utf-8 -*-
"""V015-M5 / R03 / A-M5-04 evidence-grade production image rebuild and verification.

Rebuilds the production image from the FINAL R03-remediated workspace source inside a
temporary sanitized build context (the real workspace is never modified and the synthetic
canaries exist only inside that temporary context), keeping every R02 assertion intact:

  1. Per-layer digests: the compressed blob SHA256 (bound to the blobs/sha256/<digest>
     object names) AND the uncompressed DiffID SHA256 are computed for every layer and
     compared, in order, against docker inspect RootFS.Layers.
  2. Full source binding: SHA256 of Dockerfile / .dockerignore / requirements.txt, every
     app/*.py module, every web/** asset and the exact 16 allowed config files, compared
     byte-for-byte against the files actually present inside the image layers (whiteouts
     applied to the cumulative view).
  3. Secret scanning that actually populates AND asserts hits: the 10 synthetic canary
     path+content markers plus high-confidence secret token regular expressions are
     streamed across every regular file of every layer with a tail buffer so cross-chunk
     matches cannot be missed. All raw hits keep matched-text/context evidence and are
     tiered by path ownership: app-owned paths are zero-tolerance, site-packages hits must
     map data-driven to an installed distribution (PEP 376 RECORD or dist-info/top_level.txt,
     mapping source recorded per hit) and receive a deterministic triage class, and anything
     unattributable stays review_required and fails closed. The raw broad-pattern count is
     reported honestly even though public libraries trigger benign pattern coincidences.
  4. Image Env values AND docker history are fully recorded and scanned for canary
     markers, token regexes and plaintext secret assignments (not just Env key names).
  5. The single-worker requirement is proven from /proc/[0-9]*/cmdline inside the running
     container (exact "--workers 1" cmdline, exactly one uvicorn process).
  6. The 10 synthetic canaries are injected only into the temporary build context, with a
     persisted injection manifest and pre-build existence assertions so that zero-hit
     results are never vacuous.

Merged retained coverage from the accepted VM401 / R01 / R02 procedures: fail-closed
rejection when SECRET_KEY is missing, full isolated HTTP auth / forced password change /
old-token invalidation / password rotation flows, admin health and metrics, read-only
rootfs with tmpfs hardening, a --network none offline self-check, in-container pip check
and key dependency import probes, and itemized source classification for every public
dependency.

The R01/R02 images, scripts and reports are never overwritten or re-interpreted; all
outputs are new m5_r03_* evidence files. No real secrets, credentials or personnel data
are used.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "doc" / "V0.1.5" / "evidence"
IMAGE_TAG = "listtrans:m5-r03-prod"
REPORT_PATH = EV / "m5_r03_prod_image_verify.json"
BUILD_LOG_PATH = EV / "m5_r03_prod_image_build.log"

# 10 synthetic sensitive canaries: written ONLY into the temporary build context.
CANARIES: dict[str, str] = {
    ".env": "SYNTHETIC_ENV_CANARY",
    ".env.production": "SYNTHETIC_ENV_PROD_CANARY",
    "app/auth.db": "SYNTHETIC_AUTH_CANARY",
    "app/auth.db-wal": "SYNTHETIC_AUTH_WAL_CANARY",
    "app/auth.db.backup": "SYNTHETIC_AUTH_BACKUP_CANARY",
    "data/audit_disabled_materials.csv": "SYNTHETIC_BUSINESS_DATA_CANARY",
    "doc/personnel.xls": "SYNTHETIC_PERSONNEL_CANARY",
    "scripts/cloud_users_snapshot.json": "SYNTHETIC_USER_SNAPSHOT_CANARY",
    "config/runtime_credentials.json": "SYNTHETIC_CREDENTIAL_CANARY",
    "config/unlisted_runtime.json": "SYNTHETIC_UNLISTED_CONFIG_CANARY",
}

ALLOWED_CONFIGS = [
    "config/alias_dict.json",
    "config/auto_binding_rules.json",
    "config/boost_rules.json",
    "config/category_domain_map.json",
    "config/connection_dict.json",
    "config/domain_schema.json",
    "config/flags_dict.json",
    "config/llm_config.json",
    "config/llm_prompts.json",
    "config/material_dict.json",
    "config/model_config.json",
    "config/stop_words.json",
    "config/tolerance_rules.json",
    "config/unit_norm.json",
    "config/unit_rules.json",
    "config/weight_rules.json",
]

# High-confidence token formats only: near-zero false-positive risk against application
# source. Bare substrings (such as the R01 bare "sk-") are intentionally not used.
SECRET_PATTERNS: list[tuple[str, "re.Pattern[bytes]"]] = [
    ("pem_private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")),
    ("openai_style_sk_token", re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}")),
    ("aws_access_key_id", re.compile(rb"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("google_api_key", re.compile(rb"AIza[0-9A-Za-z_\-]{35}")),
    ("slack_token", re.compile(rb"xox[baprs]-[0-9A-Za-z-]{10,}")),
]

# Plaintext assignment forms checked against the small Env/history metadata blob.
ENV_HISTORY_LEAK_STRINGS = [
    *CANARIES.values(),
    "OPENAI_API_KEY=",
    "DASHSCOPE_API_KEY=",
    "DEEPSEEK_API_KEY=",
    "SECRET_KEY=",
    "ADMIN_PASSWORD=",
]

CANARY_BYTE_MARKERS = [v.encode("utf-8") for v in CANARIES.values()]
_GZIP_MAGIC = b"\x1f\x8b"
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# Content the project itself puts into the image (Dockerfile COPY destinations).
APP_OWNED_PREFIXES = ("app/", "web/", "config/")
# Third-party public distributions installed by pip; broad-pattern hits here are allowed
# only when they map to an installed distribution and receive a deterministic triage class.
SITE_PACKAGES_PREFIX = "usr/local/lib/python3.11/site-packages/"


def _normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.lower())


def _dist_dir_name(dist_dir: str) -> str:
    """Normalized distribution name parsed from a *.dist-info / *.egg-info directory."""
    base = dist_dir
    for suffix in (".dist-info", ".egg-info"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return _normalize_dist_name(base.split("-", 1)[0])


def _triage_class(path: str, pattern: str, matched_text: str) -> str:
    """Rule-based explanation for a broad-pattern hit inside site-packages.

    Anything not covered by an evidence-backed rule stays "review_required" and fails the
    gate closed, so a new kind of vendor hit can never be silently waved through.
    """
    low = path.lower()
    if low.endswith((".so", ".dll", ".dylib")):
        return "native_binary_symbol_coincidence"
    if pattern == "pem_private_key":
        if low.endswith(".dist-info/metadata") or low.endswith(".egg-info/metadata") or low.endswith("metadata"):
            return "package_metadata_text"
        return "pem_header_string_literal"
    if pattern == "openai_style_sk_token" and ("sk-" in matched_text and "_" in matched_text):
        return "identifier_or_css_class_coincidence"
    return "review_required"


# --------------------------------------------------------------------------- utilities


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


class _HashReader:
    """Binary stream wrapper that hashes every byte actually consumed."""

    def __init__(self, underlying: Any):
        self._u = underlying
        self.sha256 = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._u.read() if size is None or size < 0 else self._u.read(size)
        if chunk:
            self.sha256.update(chunk)
            self.bytes_read += len(chunk)
        return chunk

    def close(self) -> Any:
        return self._u.close()


class _PrefixedReader:
    """Re-attaches bytes already consumed for magic-byte detection."""

    def __init__(self, prefix: bytes, rest: Any):
        self._prefix = prefix
        self._rest = rest

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            out = self._prefix + self._rest.read()
            self._prefix = b""
            return out
        if not self._prefix:
            return self._rest.read(size)
        take = self._prefix[:size]
        self._prefix = self._prefix[size:]
        if len(take) < size:
            return take + self._rest.read(size - len(take))
        return take

    def close(self) -> Any:
        return self._rest.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def _http_request(method: str, url: str, payload: dict | None = None, headers: dict | None = None):
    hdrs = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw
    except Exception as exc:  # connection refused, timeout, ...
        return 0, str(exc)


def _is_forbidden_app_relpath(app_rel: str) -> bool:
    """Whether a path under /app/ (relative to /app/) violates image exclusion rules."""
    low = app_rel.lower()
    name = low.rsplit("/", 1)[-1]
    if name.startswith(".env"):
        return True
    if any(
        low.endswith(ext)
        for ext in (".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3", ".backup", ".csv", ".xls", ".xlsx")
    ):
        return True
    if ".db." in name:
        return True
    if any(tok in low for tok in ("cloud_users_snapshot", "runtime_credentials", "secret", "password", "credential")):
        return True
    if low.startswith("app/feedback_") and low.endswith(".json"):
        return True
    if low == "app/binding_feedback.json":
        return True
    if low.startswith("config/") and app_rel not in ALLOWED_CONFIGS:
        return True
    if low.startswith(("doc/", "scripts/", "data/", "tests/", "logs/", "model_offline/", "app/uploads/", "app/vector_cache/")):
        return True
    return False


def _is_suspicious_global_name(norm_path: str) -> bool:
    """Candidate sensitive filenames anywhere in image layers (separate /app/ strict gate)."""
    low = norm_path.lower()
    name = low.rsplit("/", 1)[-1]
    if name.startswith(".env"):
        return True
    if any(low.endswith(ext) for ext in (".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3", ".backup", ".csv", ".xls", ".xlsx")):
        return True
    if ".db." in name:
        return True
    if "cloud_users_snapshot" in low or "runtime_credentials" in low:
        return True
    return False


def _match_evidence(data: bytes, match: "re.Match[bytes]") -> dict[str, str]:
    start, end = match.start(), match.end()
    context = data[max(0, start - 40) : min(len(data), end + 40)]
    return {
        "matched_text": match.group()[:80].decode("utf-8", "replace"),
        "context": context.decode("utf-8", "replace"),
    }


def _scan_bytes(data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    marker_hits = {m.decode("utf-8") for m in CANARY_BYTE_MARKERS if m in data}
    pattern_hits: list[dict[str, Any]] = []
    for label, rx in SECRET_PATTERNS:
        match = rx.search(data)
        if match:
            pattern_hits.append({"pattern": label, **_match_evidence(data, match)})
    return sorted(marker_hits), pattern_hits


def _stream_scan(file_obj: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Streaming marker + regex scan with a tail buffer so matches spanning chunks are caught.

    One evidence row per (file, pattern): the first match is kept with its matched text and
    a surrounding context window. Every byte of every file is still scanned exactly once.
    """
    if file_obj is None:
        return [], []
    marker_hits: set[str] = set()
    pattern_hits: list[dict[str, Any]] = []
    seen_patterns: set[str] = set()
    carry = max(128, max(len(m) for m in CANARY_BYTE_MARKERS))
    tail = b""
    while True:
        chunk = file_obj.read(1024 * 1024)
        if not chunk:
            break
        buf = tail + chunk
        for m in CANARY_BYTE_MARKERS:
            if m in buf:
                marker_hits.add(m.decode("utf-8"))
        for label, rx in SECRET_PATTERNS:
            if label in seen_patterns:
                continue
            match = rx.search(buf)
            if match:
                seen_patterns.add(label)
                start, end = match.start(), match.end()
                pattern_hits.append(
                    {
                        "pattern": label,
                        "matched_text": match.group()[:80].decode("utf-8", "replace"),
                        "context": buf[max(0, start - 40) : min(len(buf), end + 40)].decode("utf-8", "replace"),
                    }
                )
        tail = buf[-carry:] if len(buf) >= carry else buf
    return sorted(marker_hits), pattern_hits


# ----------------------------------------------------------------- all-layer inspection


def _inspect_image_tar(tar_path: Path, expected_app_hashes: dict[str, str], inspect_diff_ids: list[str]) -> dict[str, Any]:
    with tarfile.open(tar_path, mode="r:") as outer:
        manifest = json.loads(outer.extractfile("manifest.json").read().decode("utf-8"))
        layers = manifest[0]["Layers"]

        layer_reports: list[dict[str, Any]] = []
        canary_path_hits = {k: False for k in CANARIES}
        canary_content_hits: list[dict[str, Any]] = []
        secret_pattern_hits: list[dict[str, Any]] = []
        app_forbidden_hits: list[dict[str, Any]] = []
        outside_app_pattern_hits: list[dict[str, Any]] = []
        app_layer_file_hashes: dict[str, str] = {}
        cumulative_app_files: set[str] = set()
        blob_digest_matches: list[bool] = []
        diff_id_matches: list[bool] = []
        # Data-driven map of installed distributions, built from dist-info/egg-info inside the tar.
        dist_info_dirs: set[str] = set()
        top_level_map: dict[str, str] = {}
        # PEP 376 RECORD-derived maps: exact installed path -> dist, and first path segment
        # (import package / bundled directory) -> dist. Wheels without top_level.txt (modern
        # hatchling/meson/maturin builds such as cryptography, rsa, scipy, scikit-learn) are
        # still attributable exclusively through their RECORD manifest.
        record_path_map: dict[str, str] = {}
        record_top_level_map: dict[str, str] = {}

        for idx, layer_blob in enumerate(layers):
            member = outer.getmember(layer_blob)
            raw_stream = outer.extractfile(member)
            blob_hasher = _HashReader(raw_stream)

            magic = blob_hasher.read(4)
            restored = _PrefixedReader(magic, blob_hasher)
            if magic[:2] == _GZIP_MAGIC:
                compression = "gzip"
                inner_stream = gzip.GzipFile(fileobj=restored)
            elif magic[:4] == _ZSTD_MAGIC:
                # Fail closed: the standard library cannot independently recompute a zstd DiffID.
                raise RuntimeError(f"Layer {idx} uses zstd compression; DiffID cannot be verified with stdlib-only tooling")
            else:
                compression = "plain"
                inner_stream = restored
            diff_hasher = _HashReader(inner_stream)

            file_count = 0
            app_files_in_layer: list[str] = []
            layer_marker_hits: list[dict[str, Any]] = []
            layer_pattern_hits: list[dict[str, Any]] = []

            with tarfile.open(fileobj=diff_hasher, mode="r|*") as lyr:
                for item in lyr:
                    norm = item.name.lstrip("./")
                    if norm.startswith(SITE_PACKAGES_PREFIX):
                        first = norm[len(SITE_PACKAGES_PREFIX) :].split("/", 1)[0]
                        if first.endswith((".dist-info", ".egg-info")):
                            dist_info_dirs.add(first)
                    if norm.startswith("app/") and "/.wh." in norm:
                        removed = norm.replace("/.wh.", "/")
                        cumulative_app_files.discard(removed)
                        app_layer_file_hashes.pop(removed, None)
                    elif norm == "app/.wh..wh..opq":
                        cumulative_app_files.clear()
                        app_layer_file_hashes.clear()

                    if not item.isfile():
                        continue
                    file_count += 1

                    for cname in CANARIES:
                        if norm == f"app/{cname}" or norm == cname:
                            canary_path_hits[cname] = True

                    if norm.startswith("app/"):
                        app_rel = norm[len("app/") :]
                        cumulative_app_files.add(norm)
                        app_files_in_layer.append(norm)
                        if _is_forbidden_app_relpath(app_rel):
                            app_forbidden_hits.append({"layer_index": idx, "path": norm})
                    elif _is_suspicious_global_name(norm):
                        outside_app_pattern_hits.append({"layer_index": idx, "path": norm})

                    fobj = lyr.extractfile(item)
                    if (
                        norm.startswith(SITE_PACKAGES_PREFIX)
                        and norm.split("/")[-1] == "top_level.txt"
                        and norm.split("/")[-2].endswith((".dist-info", ".egg-info"))
                    ):
                        # Consume the stream once: record the import->distribution map, then scan it.
                        tl_bytes = fobj.read() if fobj is not None else b""
                        dist_dir = norm.split("/")[-2]
                        for pkg in tl_bytes.decode("utf-8", "replace").split():
                            top_level_map.setdefault(pkg.strip(), dist_dir)
                        m_hits, p_hits = _scan_bytes(tl_bytes)
                    elif (
                        norm.startswith(SITE_PACKAGES_PREFIX)
                        and norm.split("/")[-1] == "RECORD"
                        and norm.split("/")[-2].endswith((".dist-info", ".egg-info"))
                    ):
                        # PEP 376 installed-file manifest: every CSV row's first column is a
                        # site-packages-relative path owned by this distribution.
                        rec_bytes = fobj.read() if fobj is not None else b""
                        dist_dir = norm.split("/")[-2]
                        rec_text = rec_bytes.decode("utf-8", "replace")
                        for row in csv.reader(io.StringIO(rec_text)):
                            if not row or not row[0].strip():
                                continue
                            rec_path = row[0].strip()
                            record_path_map.setdefault(rec_path, dist_dir)
                            first_seg = rec_path.split("/", 1)[0]
                            if not first_seg.endswith((".dist-info", ".egg-info")):
                                record_top_level_map.setdefault(first_seg, dist_dir)
                        m_hits, p_hits = _scan_bytes(rec_bytes)
                    elif norm in expected_app_hashes:
                        data = fobj.read() if fobj is not None else b""
                        app_layer_file_hashes[norm] = _sha256_bytes(data)
                        m_hits, p_hits = _scan_bytes(data)
                    else:
                        m_hits, p_hits = _stream_scan(fobj)
                    for mk in m_hits:
                        hit = {"layer_index": idx, "path": norm, "marker": mk}
                        canary_content_hits.append(hit)
                        layer_marker_hits.append(hit)
                    for ph in p_hits:
                        hit = {
                            "layer_index": idx,
                            "path": norm,
                            "pattern": ph["pattern"],
                            "matched_text": ph.get("matched_text"),
                            "context": ph.get("context"),
                        }
                        secret_pattern_hits.append(hit)
                        layer_pattern_hits.append(hit)

            blob_digest = blob_hasher.sha256.hexdigest()
            diff_id = diff_hasher.sha256.hexdigest()
            blob_name = layer_blob.rsplit("/", 1)[-1]
            blob_name_is_digest = bool(re.fullmatch(r"[0-9a-f]{64}", blob_name))
            blob_matches_name = (blob_name == blob_digest) if blob_name_is_digest else False
            blob_digest_matches.append(blob_matches_name)
            expected_diff = inspect_diff_ids[idx] if idx < len(inspect_diff_ids) else None
            diff_matches = expected_diff == f"sha256:{diff_id}"
            diff_id_matches.append(diff_matches)

            layer_reports.append(
                {
                    "layer_index": idx,
                    "blob": layer_blob,
                    "compression": compression,
                    "blob_bytes": blob_hasher.bytes_read,
                    "blob_sha256_computed": blob_digest,
                    "blob_name_is_content_digest": blob_name_is_digest,
                    "blob_digest_matches_name": blob_matches_name,
                    "diff_id_computed": f"sha256:{diff_id}",
                    "diff_id_from_inspect": expected_diff,
                    "diff_id_matches_inspect": diff_matches,
                    "uncompressed_tar_bytes": diff_hasher.bytes_read,
                    "file_count": file_count,
                    "app_file_count": len(app_files_in_layer),
                    "app_files_sample": app_files_in_layer[:25],
                    "canary_content_hits": layer_marker_hits,
                    "secret_pattern_hits": layer_pattern_hits,
                }
            )

    config_files_in_image = sorted(p for p in cumulative_app_files if p.startswith("app/config/"))
    expected_image_configs = sorted("app/" + cfg for cfg in ALLOWED_CONFIGS)
    hash_mismatches = [
        {"path": path, "expected_sha256": exp, "actual_sha256": app_layer_file_hashes.get(path)}
        for path, exp in sorted(expected_app_hashes.items())
        if app_layer_file_hashes.get(path) != exp
    ]

    # ------------------------------------------------------------ secret-hit triage
    # Broad-pattern hits are never deleted or suppressed. Each hit is attributed by path
    # ownership: project-owned paths are zero-tolerance; site-packages hits must map to an
    # installed distribution (data-driven via dist-info/top_level.txt) and receive a
    # deterministic triage class. Anything unattributable stays "review_required" and fails
    # the gate closed, so a new kind of vendor hit can never be silently waved through.
    app_owned_secret_hits: list[dict[str, Any]] = []
    triaged_site_packages_hits: list[dict[str, Any]] = []
    unmappable_secret_hits: list[dict[str, Any]] = []

    def _map_to_distribution(norm_path: str) -> tuple[str, str] | None:
        rel = norm_path[len(SITE_PACKAGES_PREFIX) :]
        first = rel.split("/", 1)[0]
        if first in dist_info_dirs:
            return first, "dist_info_dir"
        if rel in record_path_map:
            return record_path_map[rel], "record_exact_path"
        if first in top_level_map:
            return top_level_map[first], "top_level_txt"
        if first in record_top_level_map:
            return record_top_level_map[first], "record_top_level"
        # Byte-compiled artifacts are generated at install time from a source file RECORD lists.
        if "/__pycache__/" in rel and rel.endswith(".pyc"):
            dir_part, pyc_name = rel.rsplit("/__pycache__/", 1)
            source_rel = f"{dir_part}/{pyc_name.split('.', 1)[0]}.py"
            if source_rel in record_path_map:
                return record_path_map[source_rel], "record_pyc_source"
        # Prefix fallback for files living directly inside a *.dist-info / *.egg-info directory.
        for dist_dir in sorted(dist_info_dirs):
            if rel.startswith(dist_dir + "/"):
                return dist_dir, "dist_info_prefix"
        return None

    site_packages_hit_total = 0
    for hit in secret_pattern_hits:
        norm_path = hit["path"]
        if norm_path.startswith(APP_OWNED_PREFIXES):
            app_owned_secret_hits.append(hit)
            continue
        if norm_path.startswith(SITE_PACKAGES_PREFIX):
            site_packages_hit_total += 1
            mapping = _map_to_distribution(norm_path)
            if mapping is None:
                unmappable_secret_hits.append({**hit, "reason": "no_installed_distribution_match"})
                continue
            dist_dir, mapping_source = mapping
            triaged_site_packages_hits.append(
                {
                    **hit,
                    "mapped_distribution_dir": dist_dir,
                    "mapped_distribution": _dist_dir_name(dist_dir),
                    "mapping_source": mapping_source,
                    "triage_class": _triage_class(norm_path, hit["pattern"], hit.get("matched_text") or ""),
                }
            )
        else:
            unmappable_secret_hits.append({**hit, "reason": "outside_app_owned_and_site_packages"})

    review_required_hits = [
        row for row in triaged_site_packages_hits if row["triage_class"] == "review_required"
    ]
    triage_class_counts: dict[str, int] = {}
    mapping_source_counts: dict[str, int] = {}
    for row in triaged_site_packages_hits:
        triage_class_counts[row["triage_class"]] = triage_class_counts.get(row["triage_class"], 0) + 1
        mapping_source_counts[row["mapping_source"]] = mapping_source_counts.get(row["mapping_source"], 0) + 1

    return {
        "total_layers_scanned": len(layers),
        "inspect_rootfs_layer_count": len(inspect_diff_ids),
        "layer_count_matches_inspect": len(layers) == len(inspect_diff_ids),
        "layers": layer_reports,
        "every_layer_blob_digest_matches_name": all(blob_digest_matches) and any(blob_digest_matches),
        "every_layer_diff_id_matches_inspect": all(diff_id_matches) and len(diff_id_matches) == len(inspect_diff_ids),
        "canary_path_hits": canary_path_hits,
        "all_10_canaries_excluded": not any(canary_path_hits.values()),
        "canary_content_hits_across_all_layers": canary_content_hits,
        "zero_canary_content_in_all_layers": len(canary_content_hits) == 0,
        "secret_pattern_hits_across_all_layers": secret_pattern_hits,
        # Raw broad-pattern count is reported honestly (third-party libraries trigger benign
        # pattern coincidences); it is no longer a pass/fail gate on its own.
        "zero_secret_pattern_hits_in_all_layers": len(secret_pattern_hits) == 0,
        "secret_pattern_hit_count_raw": len(secret_pattern_hits),
        "app_owned_secret_pattern_hits": app_owned_secret_hits,
        "zero_app_owned_secret_pattern_hits": len(app_owned_secret_hits) == 0,
        "site_packages_secret_hits_triaged": triaged_site_packages_hits,
        "site_packages_triage_class_counts": triage_class_counts,
        "site_packages_mapping_source_counts": mapping_source_counts,
        "site_packages_secret_hit_count": site_packages_hit_total,
        "all_site_packages_hits_mapped_to_installed_distribution": (
            site_packages_hit_total == len(triaged_site_packages_hits)
        ),
        "unmappable_secret_hits": unmappable_secret_hits,
        "review_required_triage_hits": review_required_hits,
        "zero_review_required_triage_findings": (
            len(review_required_hits) == 0 and len(unmappable_secret_hits) == 0
        ),
        "distribution_attribution_tables": {
            "dist_info_dirs_discovered": len(dist_info_dirs),
            "top_level_txt_entries": len(top_level_map),
            "record_manifest_path_entries": len(record_path_map),
            "record_manifest_top_level_entries": len(record_top_level_map),
        },
        "app_forbidden_hits": app_forbidden_hits,
        "zero_forbidden_files_under_app": len(app_forbidden_hits) == 0,
        "outside_app_pattern_hits_count": len(outside_app_pattern_hits),
        "outside_app_pattern_hits_sample": outside_app_pattern_hits[:20],
        "config_files_in_image": config_files_in_image,
        "exact_16_allowed_configs_in_image": config_files_in_image == expected_image_configs,
        "expected_app_files_checked": len(expected_app_hashes),
        "app_source_sha256_mismatches": hash_mismatches,
        "all_app_source_hashes_match": len(hash_mismatches) == 0,
    }


# ------------------------------------------------------------- dependency classification


def _normalize_pkg_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.split("[", 1)[0].strip().lower())


def _classify_dependencies(requirements_text: str, pip_freeze_lines: list[str]) -> tuple[dict[str, str], list[dict[str, str]]]:
    declared: dict[str, str] = {}
    for raw in requirements_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        token = re.split(r"[<>=!~\[; :]", line, 1)[0].strip()
        if token:
            declared[_normalize_pkg_name(token)] = line

    rows: list[dict[str, str]] = []
    for line in pip_freeze_lines:
        line = line.strip()
        if "==" not in line:
            continue
        name, version = line.split("==", 1)
        norm = _normalize_pkg_name(name)
        if norm == "torch":
            source_class = "explicit_pytorch_cpu_index"
            origin = "Dockerfile explicit RUN: pip install torch --index-url https://download.pytorch.org/whl/cpu (public PyTorch CPU wheel index)"
        elif norm in declared:
            source_class = "direct_requirements_txt"
            origin = "Aliyun PyPI mirror https://mirrors.aliyun.com/pypi/simple/ (upstream: public https://pypi.org) via pip install -r requirements.txt"
        else:
            source_class = "transitive"
            origin = "Transitive dependency resolved from Aliyun PyPI mirror (upstream: public https://pypi.org)"
        rows.append(
            {
                "name": name,
                "version": version.strip(),
                "normalized_name": norm,
                "source_class": source_class,
                "origin": origin,
                "requirements_spec": declared.get(norm),
            }
        )
    return declared, rows


# --------------------------------------------------------------------------- main flow


def main() -> None:
    started = time.perf_counter()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    base_inspect = json.loads(
        subprocess.check_output(["docker", "image", "inspect", "python:3.11-slim"], text=True)
    )[0]

    r02_files = {
        "script": EV / "m5_r02_prod_image_verify.py",
        "report": EV / "m5_r02_prod_image_verify.json",
        "build_log": EV / "m5_r02_prod_image_build.log",
    }
    r02_before_hashes = {k: _sha256_file(p) for k, p in r02_files.items() if p.is_file()}
    r02_img_inspect = json.loads(
        subprocess.check_output(["docker", "image", "inspect", "listtrans:m5-r02-prod"], text=True)
    )[0]
    r02_img_id_before = r02_img_inspect.get("Id")

    # Collect frozen source hashes expected inside /app in the built image.
    expected_app_hashes: dict[str, str] = {"app/requirements.txt": _sha256_file(ROOT / "requirements.txt")}
    source_hashes: dict[str, str] = {
        "Dockerfile": _sha256_file(ROOT / "Dockerfile"),
        ".dockerignore": _sha256_file(ROOT / ".dockerignore"),
        "requirements.txt": expected_app_hashes["app/requirements.txt"],
    }
    app_py_files = sorted((ROOT / "app").glob("*.py"))
    for py in app_py_files:
        rel = f"app/{py.name}"
        h = _sha256_file(py)
        source_hashes[rel] = h
        expected_app_hashes[f"app/{rel}"] = h
    web_files = sorted(p for p in (ROOT / "web").rglob("*") if p.is_file())
    for wf in web_files:
        rel = str(wf.relative_to(ROOT)).replace("\\", "/")
        h = _sha256_file(wf)
        source_hashes[rel] = h
        expected_app_hashes[f"app/{rel}"] = h
    for cfg in ALLOWED_CONFIGS:
        h = _sha256_file(ROOT / cfg)
        source_hashes[cfg] = h
        expected_app_hashes[f"app/{cfg}"] = h

    tmp_dir = Path(tempfile.mkdtemp(prefix="m5_r03_prod_"))
    container_name = f"listtrans-m5-r03-verify-{int(time.time())}"
    temp_context_removed = False

    try:
        # ---- 1. Sanitized temporary build context (real workspace is never written) ----
        prod_ctx = tmp_dir / "prod_ctx"
        prod_ctx.mkdir(parents=True)
        shutil.copy2(ROOT / "Dockerfile", prod_ctx / "Dockerfile")
        shutil.copy2(ROOT / ".dockerignore", prod_ctx / ".dockerignore")
        shutil.copy2(ROOT / "requirements.txt", prod_ctx / "requirements.txt")
        (prod_ctx / "app").mkdir()
        for py in app_py_files:
            shutil.copy2(py, prod_ctx / "app" / py.name)
        shutil.copytree(ROOT / "web", prod_ctx / "web")
        (prod_ctx / "config").mkdir()
        for cfg in ALLOWED_CONFIGS:
            shutil.copy2(ROOT / cfg, prod_ctx / cfg)

        injection_manifest = []
        for cname, cval in CANARIES.items():
            target = prod_ctx / cname
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = f"{cval}\n".encode("utf-8")
            target.write_bytes(payload)
            # Hard guarantee: every canary lives strictly inside the temporary context.
            assert str(target.resolve()).startswith(str(prod_ctx.resolve()))
            injection_manifest.append(
                {"path": cname, "marker": cval, "bytes_written": len(payload), "injected_into": "temporary_build_context_only"}
            )
        # Non-vacuousness: all 10 canaries really exist in the context before building.
        assert all((prod_ctx / cname).is_file() for cname in CANARIES)
        canary_injection_confined = all(item["injected_into"] == "temporary_build_context_only" for item in injection_manifest)

        # ---- 2. Clean online build (no overlay, no scratch substitution) ----
        build_cmd = ["docker", "build", "--pull=false", "--no-cache", "-t", IMAGE_TAG, str(prod_ctx)]
        t0 = time.monotonic()
        with BUILD_LOG_PATH.open("w", encoding="utf-8") as logf:
            build_proc = subprocess.run(build_cmd, stdout=logf, stderr=subprocess.STDOUT, text=True, timeout=1800)
        build_wall = round(time.monotonic() - t0, 3)
        assert build_proc.returncode == 0, f"Production docker build failed with exit={build_proc.returncode}, see {BUILD_LOG_PATH}"

        img_inspect = json.loads(subprocess.check_output(["docker", "image", "inspect", IMAGE_TAG], text=True))[0]
        inspect_diff_ids = list(img_inspect.get("RootFS", {}).get("Layers", []))
        history_raw = subprocess.check_output(
            ["docker", "history", "--no-trunc", "--format", "{{json .}}", IMAGE_TAG], text=True
        )
        history_entries = [json.loads(line) for line in history_raw.splitlines() if line.strip()]
        pip_freeze_out = subprocess.check_output(
            ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", IMAGE_TAG, "-m", "pip", "freeze"],
            text=True,
            timeout=120,
        )
        pip_freeze_lines = [line.strip() for line in pip_freeze_out.splitlines() if line.strip()]
        declared_deps, dependency_rows = _classify_dependencies((ROOT / "requirements.txt").read_text(encoding="utf-8"), pip_freeze_lines)

        # Retained in-container dependency integrity and key import probe (offline).
        dep_probe_cmd = [
            "docker", "run", "--rm", "--network", "none", "--entrypoint", "python", IMAGE_TAG, "-c",
            (
                "import json, subprocess, psutil, xlrd, openpyxl, pandas; "
                "pc = subprocess.run(['python', '-m', 'pip', 'check'], capture_output=True, text=True); "
                "print(json.dumps({'pip_check_rc': pc.returncode, 'pip_check': pc.stdout.strip(), "
                "'psutil': psutil.__version__, 'xlrd': xlrd.__version__, "
                "'openpyxl': openpyxl.__version__, 'pandas': pandas.__version__}))"
            ),
        ]
        dep_probe_res = _run(dep_probe_cmd, timeout=120)
        assert dep_probe_res.returncode == 0, f"In-container dependency probe failed: {dep_probe_res.stderr}\n{dep_probe_res.stdout}"
        dep_probe = json.loads(dep_probe_res.stdout.strip().splitlines()[-1])

        # ---- 3. Full image save and ALL-layer digest / leakage / hash scan ----
        tar_path = tmp_dir / "prod_image.tar"
        save_proc = _run(["docker", "image", "save", "-o", str(tar_path), IMAGE_TAG], timeout=600)
        assert save_proc.returncode == 0, f"docker image save failed: {save_proc.stderr}"
        layer_scan = _inspect_image_tar(tar_path, expected_app_hashes, inspect_diff_ids)

        # ---- 4. Env values + history leak scan (not just Env key names) ----
        config_env = list(img_inspect.get("Config", {}).get("Env", []))
        env_history_blob = json.dumps({"env": config_env, "history": history_entries}, ensure_ascii=False)
        env_history_bytes = env_history_blob.encode("utf-8")
        config_history_leaks = [pat for pat in ENV_HISTORY_LEAK_STRINGS if pat in env_history_blob]
        if re.search(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{12,}", env_history_blob):
            config_history_leaks.append("sk-***")
        env_value_pattern_hits = [label for label, rx in SECRET_PATTERNS if rx.search(env_history_bytes)]
        config_history_leaks.extend(env_value_pattern_hits)

        # ---- 5. Fail-closed: missing SECRET_KEY must abort production startup ----
        fc_proc = _run(
            [
                "docker", "run", "--rm",
                "-e", "TESTING=0",
                "-e", "APP_ENV=production",
                "-e", "LLM_ENABLED=false",
                IMAGE_TAG,
            ],
            timeout=90,
        )
        fc_combined = fc_proc.stdout + fc_proc.stderr
        fail_closed_check = {
            "exit_code": fc_proc.returncode,
            "rejected_nonzero": fc_proc.returncode != 0,
            "fatal_security_error_present": "FATAL SECURITY ERROR" in fc_combined,
            "output_tail": fc_combined[-800:],
        }

        # ---- 6. Hardened isolated runtime container + full HTTP auth flows ----
        host_port = _free_port()
        synth_secret = "Synthetic_M5R03_Prod_Container_Key_2026_Strong_556677"
        synth_admin_pwd = "SyntheticM5R03Admin#2026!"
        synth_emp_new_pwd = "M5R03EmpNew#2026!"
        synth_admin_new_pwd = "M5R03AdminNew#2026!"

        _run(["docker", "rm", "-f", container_name], timeout=30)
        runtime_env = [
            "-e", "TESTING=0",
            "-e", "APP_ENV=production",
            "-e", "LLM_ENABLED=false",
            "-e", "AUTH_DEV_MODE=false",
            "-e", "HF_HUB_OFFLINE=1",
            "-e", "TRANSFORMERS_OFFLINE=1",
            "-e", f"SECRET_KEY={synth_secret}",
            "-e", f"ADMIN_PASSWORD={synth_admin_pwd}",
            "-e", "MATERIALS_DB_PATH=/tmp/materials.db",
            "-e", "AUTH_DB_PATH=/tmp/auth.db",
            "-e", "LOG_DIR=/tmp/logs",
            "-e", "UPLOAD_DIR=/tmp/uploads",
            "-e", "OUTPUT_DIR=/tmp/outputs",
            "-e", "VECTOR_CACHE_DIR=/tmp/vector",
            "-e", "FEEDBACK_SAMPLES_FILE=/tmp/feedback_samples.json",
            "-e", "BINDING_FEEDBACK_FILE=/tmp/binding_feedback.json",
        ]
        run_proc = _run(
            [
                "docker", "run", "-d", "--name", container_name,
                "-p", f"127.0.0.1:{host_port}:18080",
                "--read-only", "--tmpfs", "/tmp:rw,exec,size=512m",
                *runtime_env,
                IMAGE_TAG,
            ],
            timeout=60,
        )
        assert run_proc.returncode == 0, f"Container failed to start: {run_proc.stderr}"

        base_url = f"http://127.0.0.1:{host_port}"
        try:
            deadline = time.time() + 60.0
            login_html_code = 0
            while time.time() < deadline:
                login_html_code, _ = _http_request("GET", f"{base_url}/login.html")
                if login_html_code == 200:
                    break
                time.sleep(0.5)
            assert login_html_code == 200, f"/login.html did not return 200 in time (got {login_html_code})"

            unauth_me_code, _ = _http_request("GET", f"{base_url}/api/auth/me")
            wrong_pwd_code, _ = _http_request(
                "POST", f"{base_url}/api/auth/login",
                payload={"username": "admin", "password": "WrongPassword#000"},
            )

            admin_login_code, admin_login_body = _http_request(
                "POST", f"{base_url}/api/auth/login",
                payload={"username": "admin", "password": synth_admin_pwd},
            )
            admin_tok = admin_login_body["access_token"]
            admin_hdr = {"Authorization": f"Bearer {admin_tok}"}
            admin_me_code, _ = _http_request("GET", f"{base_url}/api/auth/me", headers=admin_hdr)
            admin_rules_code, _ = _http_request("GET", f"{base_url}/api/llm-rules", headers=admin_hdr)

            # Retained admin health / metrics probe over real HTTP.
            health_code, health_body = _http_request("GET", f"{base_url}/api/admin/health", headers=admin_hdr)
            admin_health_check = {
                "status_code": health_code,
                "status": health_body.get("status") if isinstance(health_body, dict) else None,
                "metrics_available": health_body.get("metrics_available") if isinstance(health_body, dict) else None,
                "memory_total_gb": health_body.get("hardware", {}).get("memory", {}).get("total_gb") if isinstance(health_body, dict) else None,
                "disk_total_gb": health_body.get("hardware", {}).get("disk", {}).get("total_gb") if isinstance(health_body, dict) else None,
            }

            emp_create_code, emp_create_body = _http_request(
                "POST", f"{base_url}/api/admin/users",
                payload={
                    "username": "m5r03_emp",
                    "name": "镜像验证员工",
                    "employee_no": "M5R03-E01",
                    "role": "employee",
                },
                headers=admin_hdr,
            )
            emp_temp_pwd = emp_create_body["temp_password"]

            emp_login_code, emp_login_body = _http_request(
                "POST", f"{base_url}/api/auth/login",
                payload={"username": "m5r03_emp", "password": emp_temp_pwd},
            )
            emp_old_tok = emp_login_body["access_token"]
            emp_old_hdr = {"Authorization": f"Bearer {emp_old_tok}"}
            emp_me_before_code, _ = _http_request("GET", f"{base_url}/api/auth/me", headers=emp_old_hdr)
            emp_rules_blocked_code, emp_rules_blocked_body = _http_request(
                "GET", f"{base_url}/api/llm-rules", headers=emp_old_hdr
            )

            emp_profile_code, emp_profile_body = _http_request(
                "PUT", f"{base_url}/api/auth/profile",
                payload={"old_password": emp_temp_pwd, "new_password": synth_emp_new_pwd},
                headers=emp_old_hdr,
            )
            emp_old_tok_after_code, _ = _http_request("GET", f"{base_url}/api/auth/me", headers=emp_old_hdr)
            emp_old_pwd_login_code, _ = _http_request(
                "POST", f"{base_url}/api/auth/login",
                payload={"username": "m5r03_emp", "password": emp_temp_pwd},
            )
            emp_new_login_code, emp_new_login_body = _http_request(
                "POST", f"{base_url}/api/auth/login",
                payload={"username": "m5r03_emp", "password": synth_emp_new_pwd},
            )
            emp_new_hdr = {"Authorization": f"Bearer {emp_new_login_body['access_token']}"}
            emp_me_after_code, _ = _http_request("GET", f"{base_url}/api/auth/me", headers=emp_new_hdr)
            emp_rules_after_code, _ = _http_request("GET", f"{base_url}/api/llm-rules", headers=emp_new_hdr)

            admin_profile_code, _ = _http_request(
                "PUT", f"{base_url}/api/auth/profile",
                payload={"old_password": synth_admin_pwd, "new_password": synth_admin_new_pwd},
                headers=admin_hdr,
            )
            admin_old_tok_after_code, _ = _http_request("GET", f"{base_url}/api/auth/me", headers=admin_hdr)
            admin_old_pwd_login_code, _ = _http_request(
                "POST", f"{base_url}/api/auth/login",
                payload={"username": "admin", "password": synth_admin_pwd},
            )
            admin_new_login_code, admin_new_login_body = _http_request(
                "POST", f"{base_url}/api/auth/login",
                payload={"username": "admin", "password": synth_admin_new_pwd},
            )

            # Single-worker proof from real /proc cmdlines.
            ps_out = subprocess.check_output(
                [
                    "docker", "exec", container_name, "python", "-c",
                    "import glob, pathlib; print('\\n'.join(pathlib.Path(p).read_bytes().replace(b'\\x00', b' ').decode('utf-8', 'replace').strip() for p in sorted(glob.glob('/proc/[0-9]*/cmdline'))))",
                ],
                text=True,
                timeout=20,
            )
            uvicorn_lines = [line.strip() for line in ps_out.splitlines() if "uvicorn" in line]
            cfg_ls_out = subprocess.check_output(
                ["docker", "exec", container_name, "ls", "-1", "/app/config"], text=True, timeout=20
            )
            container_configs = sorted(line.strip() for line in cfg_ls_out.splitlines() if line.strip())
            app_db_out = subprocess.check_output(
                ["docker", "exec", container_name, "find", "/app/app", "-maxdepth", "2", "-name", "*.db*"],
                text=True,
                timeout=20,
            )
            app_dir_dbs = [line.strip() for line in app_db_out.splitlines() if line.strip()]

            isolated_startup_check = {
                "start_exit": run_proc.returncode,
                "port": host_port,
                "read_only_rootfs": True,
                "tmpfs": "/tmp:rw,exec,size=512m",
                "login_html_status": login_html_code,
                "unauth_me_status": unauth_me_code,
                "wrong_password_login_status": wrong_pwd_code,
                "admin_initial_login_status": admin_login_code,
                "admin_initial_must_change_password": admin_login_body.get("must_change_password"),
                "admin_me_status": admin_me_code,
                "admin_llm_rules_status": admin_rules_code,
                "employee_create_status": emp_create_code,
                "employee_create_must_change_password": emp_create_body.get("must_change_password"),
                "employee_temp_password_len": len(emp_temp_pwd),
                "employee_temp_login_status": emp_login_code,
                "employee_temp_must_change_password": emp_login_body.get("must_change_password"),
                "employee_me_before_change_status": emp_me_before_code,
                "employee_llm_rules_before_change_status": emp_rules_blocked_code,
                "employee_llm_rules_before_change_detail": (
                    emp_rules_blocked_body.get("detail") if isinstance(emp_rules_blocked_body, dict) else emp_rules_blocked_body
                ),
                "employee_profile_change_status": emp_profile_code,
                "employee_profile_must_change_password": emp_profile_body.get("must_change_password"),
                "employee_old_token_after_change_status": emp_old_tok_after_code,
                "employee_old_temp_password_login_status": emp_old_pwd_login_code,
                "employee_new_login_status": emp_new_login_code,
                "employee_new_must_change_password": emp_new_login_body.get("must_change_password"),
                "employee_me_after_change_status": emp_me_after_code,
                "employee_llm_rules_after_change_status": emp_rules_after_code,
                "admin_profile_rotate_status": admin_profile_code,
                "admin_old_token_after_rotate_status": admin_old_tok_after_code,
                "admin_old_password_login_status": admin_old_pwd_login_code,
                "admin_new_password_login_status": admin_new_login_code,
                "uvicorn_cmdlines": uvicorn_lines,
                "workers_1_cmd_present": any("--workers 1" in line for line in uvicorn_lines),
                "uvicorn_process_count": len(uvicorn_lines),
                "container_config_files": container_configs,
                "exact_16_container_configs": container_configs == sorted(p.split("/", 1)[1] for p in ALLOWED_CONFIGS),
                "app_dir_db_files": app_dir_dbs,
                "zero_db_files_under_app_dir": len(app_dir_dbs) == 0,
            }
        finally:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=30)

        # ---- 7. --network none + read-only offline self-check ----
        none_cmd = [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,exec,size=512m",
            *runtime_env,
            IMAGE_TAG, "python", "-c",
            (
                "import json; from app.audit_manager import get_server_hardware_metrics; "
                "m = get_server_hardware_metrics(); "
                "assert m['metrics_available'] and m['memory']['total_gb'] > 0; "
                "print(json.dumps({'metrics_available': m['metrics_available'], "
                "'memory_total_gb': m['memory']['total_gb']}))"
            ),
        ]
        none_res = _run(none_cmd, timeout=90)
        network_none_check = {
            "exit_code": none_res.returncode,
            "network": "none",
            "read_only_rootfs": True,
            "stdout_tail": none_res.stdout.strip().splitlines()[-1] if none_res.stdout.strip() else "",
            "stderr_tail": none_res.stderr[-400:],
            "passed": none_res.returncode == 0 and '"metrics_available": true' in none_res.stdout,
        }

        # Verify R02 image and files were never overwritten.
        r02_after_hashes = {k: _sha256_file(p) for k, p in r02_files.items() if p.is_file()}
        r02_img_id_after = json.loads(
            subprocess.check_output(["docker", "image", "inspect", "listtrans:m5-r02-prod"], text=True)
        )[0].get("Id")

        # ---- 8. Boolean gate (22 checks) ----
        all_checks = {
            "build_succeeded": build_proc.returncode == 0,
            "canary_injection_confined_to_temp_context": canary_injection_confined,
            "layer_count_matches_inspect": layer_scan["layer_count_matches_inspect"],
            "every_layer_blob_digest_matches_name": layer_scan["every_layer_blob_digest_matches_name"],
            "every_layer_diff_id_matches_inspect": layer_scan["every_layer_diff_id_matches_inspect"],
            "all_10_canaries_excluded": layer_scan["all_10_canaries_excluded"],
            "zero_canary_content_in_all_layers": layer_scan["zero_canary_content_in_all_layers"],
            "zero_app_owned_secret_pattern_hits": layer_scan["zero_app_owned_secret_pattern_hits"],
            "all_site_packages_hits_mapped_to_installed_distribution": layer_scan[
                "all_site_packages_hits_mapped_to_installed_distribution"
            ],
            "zero_review_required_triage_findings": layer_scan["zero_review_required_triage_findings"],
            "zero_forbidden_files_under_app": layer_scan["zero_forbidden_files_under_app"],
            "exact_16_allowed_configs_in_image": layer_scan["exact_16_allowed_configs_in_image"],
            "all_app_source_hashes_match": layer_scan["all_app_source_hashes_match"],
            "zero_config_or_history_leaks": len(config_history_leaks) == 0,
            "dependency_pip_check_clean": dep_probe.get("pip_check_rc") == 0,
            "dependency_classification_complete": (
                len(dependency_rows) == len(pip_freeze_lines)
                and {r["source_class"] for r in dependency_rows}.issubset(
                    {"direct_requirements_txt", "explicit_pytorch_cpu_index", "transitive"}
                )
            ),
            "fail_closed_missing_secret_key": (
                fail_closed_check["rejected_nonzero"] and fail_closed_check["fatal_security_error_present"]
            ),
            "isolated_startup_and_auth_flows_passed": (
                isolated_startup_check["login_html_status"] == 200
                and isolated_startup_check["unauth_me_status"] == 401
                and isolated_startup_check["wrong_password_login_status"] == 400
                and isolated_startup_check["admin_initial_login_status"] == 200
                and isolated_startup_check["admin_initial_must_change_password"] is False
                and isolated_startup_check["admin_me_status"] == 200
                and isolated_startup_check["admin_llm_rules_status"] == 200
                and isolated_startup_check["employee_create_status"] == 200
                and isolated_startup_check["employee_create_must_change_password"] == 1
                and isolated_startup_check["employee_temp_login_status"] == 200
                and isolated_startup_check["employee_temp_must_change_password"] is True
                and isolated_startup_check["employee_me_before_change_status"] == 200
                and isolated_startup_check["employee_llm_rules_before_change_status"] == 403
                and "MUST_CHANGE_PASSWORD" in str(isolated_startup_check["employee_llm_rules_before_change_detail"])
                and isolated_startup_check["employee_profile_change_status"] == 200
                and isolated_startup_check["employee_profile_must_change_password"] is False
                and isolated_startup_check["employee_old_token_after_change_status"] == 401
                and isolated_startup_check["employee_old_temp_password_login_status"] == 400
                and isolated_startup_check["employee_new_login_status"] == 200
                and isolated_startup_check["employee_new_must_change_password"] is False
                and isolated_startup_check["employee_me_after_change_status"] == 200
                and isolated_startup_check["employee_llm_rules_after_change_status"] == 200
                and isolated_startup_check["admin_profile_rotate_status"] == 200
                and isolated_startup_check["admin_old_token_after_rotate_status"] == 401
                and isolated_startup_check["admin_old_password_login_status"] == 400
                and isolated_startup_check["admin_new_password_login_status"] == 200
            ),
            "admin_health_healthy": (
                admin_health_check["status_code"] == 200
                and admin_health_check["status"] == "healthy"
                and admin_health_check["metrics_available"] is True
                and (admin_health_check["memory_total_gb"] or 0) > 0
                and (admin_health_check["disk_total_gb"] or 0) > 0
            ),
            "single_worker_verified": (
                isolated_startup_check["workers_1_cmd_present"]
                and isolated_startup_check["uvicorn_process_count"] == 1
            ),
            "container_runtime_dir_clean": (
                isolated_startup_check["exact_16_container_configs"]
                and isolated_startup_check["zero_db_files_under_app_dir"]
            ),
            "network_none_readonly_self_check_passed": network_none_check["passed"],
        }

        report = {
            "gate": "A-M5-04-R03",
            "baseline_head": head,
            "ruling_reference": "doc/V0.1.5/rounds/V015-M5_R02_统筹裁决.md (A-M5-04)",
            "audit_reference": "doc/V0.1.5/rounds/V015-M5_R02_审核小结.md",
            "r02_evidence_preserved": {
                "script": "doc/V0.1.5/evidence/m5_r02_prod_image_verify.py",
                "report": "doc/V0.1.5/evidence/m5_r02_prod_image_verify.json",
                "build_log": "doc/V0.1.5/evidence/m5_r02_prod_image_build.log",
                "image_tag": "listtrans:m5-r02-prod",
                "image_id_before": r02_img_id_before,
                "image_id_after": r02_img_id_after,
                "before_hashes": r02_before_hashes,
                "after_hashes": r02_after_hashes,
                "overwritten": (r02_before_hashes != r02_after_hashes) or (r02_img_id_before != r02_img_id_after),
            },
            "secret_scan_note": (
                "Marker/high-confidence-pattern scan only. All canaries are synthetic and no real "
                "workspace secret, credential or personnel datum is read, injected or recorded; zero "
                "hits mean zero marker/pattern occurrences, not a claim that real secrets were hunted. "
                "Every regular file of every layer is still scanned with the full broad pattern set and "
                "every raw hit (with matched text/context evidence) is retained under "
                "all_layer_scan.secret_pattern_hits_across_all_layers. Hits are tiered by path "
                "ownership: project-owned paths (app/, web/, config/) are zero-tolerance; site-packages "
                "hits must map data-driven to an installed distribution (via the PEP 376 RECORD manifest "
                "or dist-info/top_level.txt, with the mapping source recorded per hit) and receive a "
                "deterministic triage class (native_binary_symbol_coincidence, "
                "pem_header_string_literal, package_metadata_text or "
                "identifier_or_css_class_coincidence). Any unattributable or unclassified hit stays "
                "review_required and fails the gate closed. The raw broad-pattern zero-hit boolean is "
                "kept for honesty but is not a pass/fail gate, because public third-party libraries "
                "legitimately contain PEM header string literals, CSS class names and native symbols "
                "that coincide with token shapes."
            ),
            "secret_triage_summary": {
                "raw_broad_pattern_hit_count": layer_scan["secret_pattern_hit_count_raw"],
                "app_owned_hit_count": len(layer_scan["app_owned_secret_pattern_hits"]),
                "site_packages_hit_count": layer_scan["site_packages_secret_hit_count"],
                "site_packages_triage_class_counts": layer_scan["site_packages_triage_class_counts"],
                "site_packages_mapping_source_counts": layer_scan["site_packages_mapping_source_counts"],
                "attribution_tables": layer_scan["distribution_attribution_tables"],
                "unmappable_hit_count": len(layer_scan["unmappable_secret_hits"]),
                "review_required_hit_count": len(layer_scan["review_required_triage_hits"]),
            },
            "frozen_source_binding": {
                "dockerfile_sha256": source_hashes["Dockerfile"],
                "dockerignore_sha256": source_hashes[".dockerignore"],
                "requirements_sha256": source_hashes["requirements.txt"],
                "app_py_count": len(app_py_files),
                "web_file_count": len(web_files),
                "allowed_config_count": len(ALLOWED_CONFIGS),
                "total_source_files_bound": len(source_hashes),
                "expected_app_files_inside_image": len(expected_app_hashes),
                "source_hashes": source_hashes,
            },
            "base_image": {
                "tag": "python:3.11-slim",
                "id": base_inspect.get("Id"),
                "repo_digests": base_inspect.get("RepoDigests", []),
                "source": "Docker Hub official public image (library/python)",
            },
            "supply_chain": {
                "pip_indices_from_dockerfile": [
                    "https://mirrors.aliyun.com/pypi/simple/ (mirror of public pypi.org)",
                    "https://download.pytorch.org/whl/cpu (public PyTorch CPU wheel index, torch only)",
                ],
                "requirements_declared": [
                    {"normalized_name": k, "raw_spec": v} for k, v in sorted(declared_deps.items())
                ],
                "pip_freeze_count": len(pip_freeze_lines),
                "dependency_classification": dependency_rows,
                "in_container_probe": dep_probe,
            },
            "canary_injection_manifest": injection_manifest,
            "build": {
                "command": "docker build --pull=false --no-cache -t listtrans:m5-r03-prod <TMP>/prod_ctx",
                "offline_flag_used": False,
                "runtime_overlay_substituted": False,
                "scratch_substituted": False,
                "exit_code": build_proc.returncode,
                "wall_seconds": build_wall,
                "image_tag": IMAGE_TAG,
                "image_id": img_inspect.get("Id"),
                "rootfs_layers_count": len(inspect_diff_ids),
                "rootfs_diff_ids": inspect_diff_ids,
                "config_cmd": img_inspect.get("Config", {}).get("Cmd"),
                "config_env": config_env,
                "exposed_ports": list((img_inspect.get("Config", {}).get("ExposedPorts") or {}).keys()),
                "build_log": "doc/V0.1.5/evidence/m5_r03_prod_image_build.log",
            },
            "all_layer_scan": layer_scan,
            "config_history_leaks": config_history_leaks,
            "env_value_pattern_hits": env_value_pattern_hits,
            "fail_closed_check": fail_closed_check,
            "isolated_startup_check": isolated_startup_check,
            "admin_health_check": admin_health_check,
            "network_none_readonly_check": network_none_check,
            "checks": all_checks,
            "overall_verdict": "PASS" if all(all_checks.values()) else "FAIL",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }

        # Temp context is removed only after every artifact has been read out of it.
        shutil.rmtree(tmp_dir, ignore_errors=True)
        temp_context_removed = not tmp_dir.exists()
        report["temp_context_cleanup_verified"] = temp_context_removed

        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(
            {
                "verdict": report["overall_verdict"],
                "checks": all_checks,
                "image_id": report["build"]["image_id"],
                "layers": layer_scan["total_layers_scanned"],
                "source_files_bound": len(source_hashes),
                "secret_triage": report["secret_triage_summary"],
                "r02_overwritten": report["r02_evidence_preserved"]["overwritten"],
                "wall_seconds": build_wall,
            },
            ensure_ascii=False,
        ))
        assert report["overall_verdict"] == "PASS", f"A-M5-04-R03 checks failed: {all_checks}"
        assert not report["r02_evidence_preserved"]["overwritten"], "R02 evidence was modified!"
    finally:
        if not temp_context_removed:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
