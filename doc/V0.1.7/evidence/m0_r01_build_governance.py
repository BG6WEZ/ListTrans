"""Reconcile local frozen inputs and build versioned governance labels. No app imports."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "tests/fixtures/golden_set_v1.json"
LEGACY = ROOT / "tests/fixtures/ground_truth_500.json"
CATALOG = ROOT / "app/materials.db"
FIELDS = ("name", "specification", "unit", "brand", "category", "description")
DISABLE = re.compile(r"[（(](?:禁用|停用|无效)(\d{6})?[）)]\s*$")
CLASSES = ("code_remapped", "equivalent_candidates", "disabled_or_retired", "no_substitute",
           "pending_business_review")
# This is a frozen-snapshot date, so a replay in a later month cannot silently change labels.
AS_OF = date(2026, 11, 11)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, payload):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def relative(path):
    return path.relative_to(ROOT).as_posix()


def core(item):
    return item["label_type"] == "1to1" and not item["ambiguous"] and not item["qc_suspect"]


def norm(value):
    return unicodedata.normalize("NFC", str(value) if value is not None else "").strip()


def match_text(value):
    return "".join(unicodedata.normalize("NFKC", norm(value)).split()).casefold()


def stop_marker(value):
    return DISABLE.search(norm(value))


def identity(row):
    return (match_text(DISABLE.sub("", norm(row["name"]))),
            match_text(DISABLE.sub("", norm(row["specification"]))))


def read_catalog():
    sidecars = [CATALOG.with_name(CATALOG.name + suffix) for suffix in ("-wal", "-journal")]
    if any(p.exists() and p.stat().st_size for p in sidecars):
        raise ValueError("Frozen catalog must be checkpointed before immutable reads")
    with sqlite3.connect(CATALOG.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        materials = [dict(row) for row in connection.execute(
            "SELECT code, name, specification, unit, brand, category, description, parsed_params "
            "FROM materials ORDER BY code")]
        fts_rows = connection.execute("SELECT count(*) FROM materials_fts").fetchone()[0]
    return materials, fts_rows


def params(value):
    if not norm(value):
        return ""
    return json.dumps(json.loads(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def duplicate_groups(materials):
    groups = defaultdict(list)
    for row in materials:
        groups[(row["name"], row["specification"])].append(row)
    report, equivalents = [], {}
    columns = ("unit", "brand", "category", "description", "parsed_params")
    for rows in groups.values():
        if len(rows) < 2:
            continue
        partitions = defaultdict(list)
        for row in rows:
            key = tuple(norm(row[field]) for field in columns[:-1]) + (params(row["parsed_params"]),)
            partitions[key].append(row["code"])
        eligible = [codes for key, codes in partitions.items()
                    if len(codes) > 1 and key[0] and key[2] and rows[0]["name"] and rows[0]["specification"]]
        for codes in eligible:
            equivalents.update({code: codes for code in codes})
        report.append({
            "codes": [row["code"] for row in rows],
            "differing_fields": [field for i, field in enumerate(columns)
                                 if len({key[i] for key in partitions}) > 1],
            "attribute_partitions": list(partitions.values()),
            "equivalent_partitions": eligible,
        })
    return report, equivalents


HEADERS = {
    "code": ("物料编码", "物料编号"),
    "name": ("物料名称",),
    "specification": ("规格/型号",),
    "unit": ("计量单位",),
    "brand": ("常用品牌",),
    "category": ("物料分类", "所属分类编号"),
    "description": ("描述",),
    "marked_new": ("是否新增编码", "物料档案是否新增"),
}


def xlsx_rows(sheet):
    # Some ERP exports declare dimension A1:G5 while storing tens of thousands of rows.
    sheet.reset_dimensions()
    rows = enumerate(sheet.values, 1)
    for number, values in rows:
        headers = [norm(value).rstrip("*") for value in values]
        positions = {field: next((i for i, value in enumerate(headers) if value in names), None)
                     for field, names in HEADERS.items()}
        if all(positions[field] is not None for field in ("code", "name", "specification")):
            break
        if number == 15:
            raise ValueError(f"Unknown material header on sheet {sheet.title}")
    else:
        raise ValueError(f"Missing material header on sheet {sheet.title}")

    # The water/gas revision workbook prefixes its data rows with a batch number.
    first = next(rows, None)
    if first is None:
        return
    number, values = first
    offset = int(sheet.title == "第二批" and isinstance(values[0], (int, float)) and
                 positions["code"] == 1 and positions["name"] == 2)
    for row_number, cells in _prepend(first, rows):
        parsed = {}
        for field, position in positions.items():
            index = -1 if position is None else position + offset
            parsed[field] = norm(cells[index]) if 0 <= index < len(cells) else ""
        if parsed["code"] and parsed["name"]:
            yield row_number, parsed


def _prepend(first, rest):
    yield first
    yield from rest


def report_by_domain(items, active):
    counts = defaultdict(Counter)
    for item in items:
        codes = item["target_codes"]
        missing = sum(code not in active for code in codes)
        for domain in ("all", item["professional"]):
            counts[domain].update({
                "rows": 1,
                "missing_any": int(missing > 0),
                "missing_all": int(missing == len(codes)),
                "all_in_catalog": int(missing == 0),
                "targets": len(codes),
                "missing_targets": missing,
            })
    return dict(counts)


def local_sources(active, missing):
    witnesses, reconciliation, marked_new = defaultdict(list), {}, defaultdict(list)
    inputs = sorted((ROOT / "data").glob("audit_disabled_materials_*.csv"))
    inputs += sorted((ROOT / "doc/物料库").rglob("*.xlsx"))
    for path in inputs:
        rel = relative(path)
        counters, codes = {}, set()
        if path.suffix == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                counters["csv"] = source_counts(
                    enumerate(csv.DictReader(stream), 2), rel, "csv", active, missing,
                    witnesses, marked_new, codes)
        else:
            workbook = load_workbook(path, read_only=True, data_only=True)
            try:
                for sheet in workbook:
                    counters[sheet.title] = source_counts(
                        xlsx_rows(sheet), rel, sheet.title, active, missing, witnesses, marked_new, codes)
            finally:
                workbook.close()
        reconciliation[rel] = {"sha256": sha(path), "distinct_codes": len(codes),
                               "distinct_active_codes": len(codes & active),
                               "distinct_missing_golden_codes": len(codes & missing),
                               "sheets": counters}
    return witnesses, reconciliation, marked_new


def source_counts(rows, rel, sheet, active, missing, witnesses, marked_new, codes):
    summary = Counter(rows=0, active_rows=0, missing_golden_code_rows=0, disable_marked_rows=0,
                      audit_disabled_rows=0, audit_expired_rows=0)
    for number, row in rows:
        code = row["code"]
        codes.add(code)
        marker = stop_marker(row["name"]) or stop_marker(row["specification"]) or stop_marker(code)
        audit_disabled = row.get("is_disabled") == "True"
        audit_expired = row.get("is_expired") == "True"
        eligible_marker = bool(marker and (not marker[1] or
                               date.fromisoformat(f"20{marker[1][:2]}-{marker[1][2:4]}-{marker[1][4:]}") <= AS_OF))
        summary.update({"rows": 1, "active_rows": int(code in active),
                        "missing_golden_code_rows": int(code in missing), "disable_marked_rows": int(bool(marker)),
                        "audit_disabled_rows": int(audit_disabled), "audit_expired_rows": int(audit_expired)})
        location = [rel, sheet, number]
        if code in missing:
            witnesses[code].append({
                "row": row, "location": location, "disabled_marker_effective": eligible_marker,
                "audit_disabled_and_expired": audit_disabled and audit_expired,
            })
        if code in active and row.get("marked_new") == "是" and not marker:
            marked_new[code].append((row, location))
    return dict(summary)


def confirmed_fields(old, current):
    fields = ("unit", "brand", "category", "description")
    return bool(norm(old["unit"]) and norm(old["category"]) and
                all(norm(old.get(field)) == norm(current.get(field)) for field in fields))


def classify(code, ref, index, by_name, by_spec, witnesses, marked_new, equivalents):
    clean_ref = {"name": ref.get("db_name") or ref.get("list_name"),
                 "specification": ref.get("db_spec") or ref.get("list_spec")}
    key = identity(clean_ref)
    history = witnesses.get(code, [])
    matching = [entry for entry in history if identity(entry["row"]) == key and key[0]]
    retired = [entry["location"] for entry in matching
               if entry["disabled_marker_effective"] or entry["audit_disabled_and_expired"]]
    plausible = set(index.get(key, ()))
    # A same-name row with a different spec or a same-spec row with a related name is a
    # reason to request review, even though it cannot establish equivalence.
    plausible.update(by_name.get(key[0], ()))
    if key[1]:
        plausible.update(candidate for candidate, name in by_spec.get(key[1], ())
                         if min(len(name), len(key[0])) >= 3 and (name in key[0] or key[0] in name))
    exact = index.get(key, {})
    eligible = sorted(c for c, row in exact.items()
                      if key[0] and key[1] and not any(stop_marker(row[f]) for f in ("name", "specification"))
                      and matching and all(confirmed_fields(entry["row"], row) for entry in matching
                                           if norm(entry["row"]["unit"]) and norm(entry["row"]["category"]))
                      and any(confirmed_fields(entry["row"], row) for entry in matching))
    # A revision flag establishes which matching candidate was introduced. A unique identical
    # active row without a revision witness remains pending.
    revised = [loc for row, loc in marked_new.get(eligible[0], [])
               if identity(row) == key and confirmed_fields(row, exact[eligible[0]])] if len(eligible) == 1 else []
    approved = []
    if len(exact) == 1 and revised and retired:
        status, approved, reason = "code_remapped", eligible, "unique_identical_new_revision_and_retirement"
    elif (len(eligible) > 1 and len(exact) == len(eligible) and
          set(eligible).issubset(equivalents.get(eligible[0], ()))):
        status, approved, reason = "equivalent_candidates", eligible, "current_equivalent_partition_matches_history"
    elif retired and key[0] and key[1] and not plausible:
        status, reason = "disabled_or_retired", "retirement_evidence_and_no_candidate_in_offline_comparison"
    else:
        status, reason = "pending_business_review", ("candidate_requires_confirmation" if plausible
                                                     else "insufficient_evidence_for_no_substitute")
    return {"code": code, "status": status, "reason": reason, "approved_codes": approved,
            "history_locations": [entry["location"] for entry in history],
            "matching_history_locations": [entry["location"] for entry in matching],
            "retirement_locations": retired, "revision_locations": revised,
            "exact_text_codes": sorted(exact), "attribute_confirmed_codes": eligible,
            "plausible_candidate_count": len(plausible)}


def query_signature(item):
    return (match_text(item["name"]), match_text(item["specification"]))


def shingle(text):
    return {text[i:i + 3] for i in range(len(text) - 2)}


def split_items(items):
    """Group entire project/sheet units and cross-project exact/near Query duplicates."""
    parent = {item["id"]: item["id"] for item in items}

    def find(key):
        while key != parent[key]:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def link(a, b):
        a, b = sorted((find(a), find(b)))
        parent[b] = a

    sheets, identical, template_names = {}, {}, defaultdict(list)
    for item in sorted(items, key=lambda x: x["id"]):
        item_id = item["id"]
        sheet = item["project"], item["source_sheet"]
        link(item_id, sheets.setdefault(sheet, item_id))
        name, spec = query_signature(item)
        link(item_id, identical.setdefault((name, spec), item_id))
        if len(spec) >= 12:
            template_names[name].append((item_id, shingle(spec)))
    near_edges = []
    for group in template_names.values():
        for (a, left), (b, right) in combinations(group, 2):
            if len(left & right) * 100 >= 88 * len(left | right):
                link(a, b)
                near_edges.append([a, b])

    grouped = defaultdict(list)
    for item in items:
        grouped[find(item["id"])].append(item)
    if len(grouped) < 3:
        raise ValueError("Sheet and query clusters leave fewer than three independent components")
    labels, group_report = {}, {}
    sizes = Counter(train=0, dev=0, test=0)
    ratios = {"train": .70, "dev": .15, "test": .15}
    seeded_tie = lambda value: hashlib.sha256(f"V017-M0-R01:{value}".encode()).hexdigest()
    groups = sorted(grouped.items(), key=lambda pair: (-len(pair[1]), seeded_tie(pair[0])))
    for component, members in groups:
        split = max(ratios, key=lambda name: ratios[name] * len(items) - sizes[name])
        sizes[split] += len(members)
        labels.update({item["id"]: split for item in members})
        group_report[component] = {"split": split, "count": len(members),
                                   "sheets": sorted({(i["project"], i["source_sheet"]) for i in members})}
    assert all(sizes.values()), "Every split must be populated"
    projects = defaultdict(set)
    for i in items:
        projects[i["project"]].add(labels[i["id"]])
    return labels, {
        "seed": "V017-M0-R01", "desired_ratios": ratios, "sizes": dict(sizes),
        "projects": {p: sorted(v) for p, v in projects.items()},
        "components": group_report, "near_edges": sorted(near_edges),
        "domains": {s: dict(Counter(i["professional"] for i in items if labels[i["id"]] == s)) for s in ratios},
    }


def pairing_sources():
    result = {}
    for path in sorted((ROOT / "doc/真集").glob("*.xlsx")):
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            result[relative(path)] = {"sha256": sha(path), "sheets": workbook.sheetnames}
        finally:
            workbook.close()
    return result


def build():
    materials, fts_rows = read_catalog()
    active = {m["code"] for m in materials}
    if len(active) != len(materials):
        raise ValueError("Multiple material rows for the same code")
    index, by_name, by_spec = defaultdict(dict), defaultdict(set), defaultdict(list)
    for material in materials:
        name, spec = identity(material)
        index[name, spec][material["code"]] = material
        by_name[name].add(material["code"])
        by_spec[spec].append((material["code"], name))
    data = read_json(GOLDEN)
    items = data["items"]
    assert len({item["id"] for item in items}) == len(items)
    legacy = [{"target_codes": [i["ground_truth_code"]], "professional": i["category"]} for i in read_json(LEGACY)]
    missing = {c for i in items for c in i["target_codes"] if c not in active}
    witnesses, sources, marked_new = local_sources(active, missing)
    duplicates, equivalents = duplicate_groups(materials)
    splits, split_report = split_items(items)
    labels, ledger, mismatch_counts = {}, {}, Counter({key: 0 for key in CLASSES})
    for item in items:
        codes = item["target_codes"]
        refs = {ref["code"]: ref for ref in item["target_ref"]}
        decisions = [classify(c, refs.get(c, {}), index, by_name, by_spec, witnesses, marked_new, equivalents)
                     for c in codes if c not in active]
        targets = {c: equivalents.get(c, [c]) for c in codes if c in active}
        targets.update({d["code"]: sorted({alt for c in d["approved_codes"]
                                          for alt in equivalents.get(c, [c])})
                        for d in decisions if d["approved_codes"]})
        statuses = {d["status"] for d in decisions}
        if "pending_business_review" in statuses or (targets and len(targets) != len(codes)):
            status = "pending_business_review"
        elif targets:
            status = next(iter(statuses)) if len(statuses) == 1 else "answerable"
        elif "no_substitute" in statuses:
            status = "no_substitute"
        else:
            status = "disabled_or_retired"
        if decisions:
            ledger[item["id"]] = {"sample_status": status, "core": core(item),
                                  "domain": item["professional"], "missing_targets": decisions}
            for d in decisions:
                mismatch_counts[d["status"]] += 1
        labels[item["id"]] = {"split": splits[item["id"]], "status": status, "target_alternatives": targets}
    frozen = {relative(p): sha(p) for p in (CATALOG, GOLDEN, LEGACY)}
    sample_status_counts = Counter(row["sample_status"] for row in ledger.values())
    core_status_counts = Counter(row["sample_status"] for row in ledger.values() if row["core"])
    report = {
        "inputs": frozen, "as_of": AS_OF.isoformat(), "catalog_rows": len(materials), "fts_rows": fts_rows,
        "catalog_categories": dict(Counter(row["category"] or "" for row in materials)),
        "golden_build_time": data["meta"]["build_time"],
        "golden_original_catalog_rows": data["meta"]["material_db_snapshot"]["rows"],
        "label_types": dict(Counter(i["label_type"] for i in items)),
        "ambiguous": sum(i["ambiguous"] for i in items), "qc_suspect": sum(i["qc_suspect"] for i in items),
        "full": report_by_domain(items, active),
        "core": report_by_domain([i for i in items if core(i)], active),
        "legacy_326": report_by_domain(legacy, active),
        "source_reconciliation": sources, "pairing_sources": pairing_sources(),
        "missing_distinct_codes": len(missing), "missing_target_classes": dict(mismatch_counts),
        "missing_sample_classes": {k: sample_status_counts[k] for k in CLASSES},
        "missing_core_sample_classes": {k: core_status_counts[k] for k in CLASSES},
        "mismatch_ledger": ledger,
        "pending_by_domain": dict(Counter(row["domain"] for row in ledger.values()
                                          if row["sample_status"] == "pending_business_review")),
        "duplicate_groups": duplicates, "splits": split_report,
    }
    return {"version": "golden-governance-v1", "inputs": frozen, "samples": labels,
            "equivalent_code_groups": equivalents}, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.asset.resolve() == args.report.resolve() or args.asset.exists() or args.report.exists():
        parser.error("Choose two distinct new output paths")
    asset, report = build()
    save_json(args.asset, asset)
    save_json(args.report, report)
    print(json.dumps({key: report[key] for key in ("catalog_rows", "fts_rows", "missing_distinct_codes",
          "missing_target_classes", "missing_sample_classes", "missing_core_sample_classes")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
