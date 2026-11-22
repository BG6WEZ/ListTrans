"""Use a prior materialized Train/Dev projection and reconstruct its frozen labels."""

from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

from m3_r01_baseline import ROOT, read, sha


V017 = ROOT / "doc/V0.1.7/evidence"
V020 = ROOT / "doc/V0.2.0/evidence"
RECONCILIATION = V017 / "m0_r01_reconciliation.json"
COUNTS = {"train": 842, "dev": 218}
SEALED = {ROOT / name for name in read("m3_r01_dispatch_baseline.json")["sealed_assets_git_metadata"]}


def seal_holdout():
    def guard(event, args):
        if event == "open" and isinstance(args[0], (str, bytes)):
            import os

            path = Path(os.fsdecode(args[0]))
            if path.name.lower() in {material.name for material in SEALED}:
                raise PermissionError("Full frozen gold assets are sealed during V021-M3")

    sys.addaudithook(guard)


def queries(path):
    expected = read("m3_r01_protocol_frozen.json")["evaluation"]["inputs"]["projected_queries_sha256"]
    if sha(path) != expected:
        raise ValueError("Materialized queries do not match the prior V021 Train/Dev projection")
    items = json.loads(path.read_bytes())["items"]
    if len({row["id"] for row in items}) != sum(COUNTS.values()) or len(items) != sum(COUNTS.values()):
        raise ValueError("Train/Dev identity mismatch")
    return items


def assignments(items):
    # The pinned file is sorted by key. Read only the byte-addressed component manifest,
    # whose extent ends before the comparative domain and near-query edge metadata.
    with RECONCILIATION.open("rb", buffering=0) as stream:
        stream.seek(1555813)
        if stream.read(len(b'  "splits": {\n    "components": ')) != b'  "splits": {\n    "components": ':
            raise ValueError("Frozen component manifest offset mismatch")
        components = bytearray()
        for line in stream:
            if line == b"    },\n":
                components.extend(b"    }")
                break
            components.extend(line)
    allowed_sheets = {}
    component_counts = {}
    for key, group in json.loads(components).items():
        if group["split"] not in COUNTS:
            continue
        component_counts[key] = group["count"]
        for sheet in group["sheets"]:
            if tuple(sheet) in allowed_sheets:
                raise ValueError("Duplicate Train/Dev sheet in frozen manifest")
            allowed_sheets[tuple(sheet)] = (group["split"], key)
    selected = {item["id"]: allowed_sheets[item["project"], item["source_sheet"]] for item in items}
    if Counter(key for _, key in selected.values()) != component_counts:
        raise ValueError("Projected queries differ from frozen Train/Dev components")
    return {gid: split for gid, (split, _) in selected.items()}


def labels_for(items):
    sys.path.insert(0, str(V017))
    import m0_r01_build_governance as frozen

    splits = assignments(items)
    catalog, _ = frozen.read_catalog()
    if len(catalog) != 26863:
        raise ValueError("Wrong catalog row count")
    active = {material["code"] for material in catalog}
    _, equivalents = frozen.duplicate_groups(catalog)
    missing = {code for row in items for code in row["target_codes"] if code not in active}
    witnesses, _, revised = frozen.local_sources(active, missing)
    indexed, names, specs = defaultdict(dict), defaultdict(set), defaultdict(list)
    for material in catalog:
        name, spec = frozen.identity(material)
        code = material["code"]
        indexed[name, spec][code] = material
        names[name].add(code)
        specs[spec].append((code, name))
    labels = {}
    for row in items:
        gid = row["id"]
        references = {ref["code"]: ref for ref in row["target_ref"]}
        targets = {code: equivalents.get(code, [code]) for code in row["target_codes"] if code in active}
        decisions = [frozen.classify(code, references.get(code, {}), indexed, names, specs,
                                     witnesses, revised, equivalents)
                     for code in row["target_codes"] if code not in active]
        if any(decision["approved_codes"] for decision in decisions):
            raise ValueError("Unexpected remap beyond the signed V017 freeze")
        if not decisions:
            status = "answerable"
        elif targets or any(d["status"] == "pending_business_review" for d in decisions):
            status = "pending_business_review"
        else:
            if any(d["status"] != "disabled_or_retired" for d in decisions):
                raise ValueError("Unexpected frozen negative classification")
            status = "disabled_or_retired"
        labels[gid] = {"split": splits[gid], "status": status, "target_alternatives": targets}

    prior = json.loads((V020 / "m0_r01_vector_off.json").read_bytes())
    if labels.keys() != prior["upload_decisions"].keys():
        raise ValueError("V020 prior guarded replay did not cover the same rows")
    for split in COUNTS:
        cores = Counter(labels[row["id"]]["status"] for row in items
                        if splits[row["id"]] == split and frozen.core(row))
        expected = prior["metrics"][split]["counts"]
        if cores != {"answerable": expected["core_answerable"],
                     "disabled_or_retired": expected["core_negative"],
                     "pending_business_review": expected["core_pending"]}:
            raise ValueError("Independent reconstruction did not match prior frozen-class counts")
    answerable = {row["id"]: labels[row["id"]]["split"] for row in items if frozen.core(row)
                  and labels[row["id"]]["status"] == "answerable"}
    if answerable != {gid: entry["split"] for gid, entry in prior["paired_rank"].items()}:
        raise ValueError("The independently reconstructed ranking cohort differs row by row")
    return labels


def projection(path):
    items = queries(path)
    return items, labels_for(items)
