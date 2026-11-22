"""Project frozen Train/Dev and fit one constrained model from Train hard negatives."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
V017 = ROOT / "doc/V0.1.7/evidence"
sys.path[:0] = [str(ROOT), str(V017)]
from m0_r01_benchmark import block_external_network, stage
from m0_r01_build_governance import GOLDEN, core, query_signature, read_catalog, read_json, save_json, sha
from app import feedback_ranker as ranker

GOVERNANCE = ROOT / "tests/fixtures/golden_governance_v1.json"
DATA_COUNTS = {"train": 842, "dev": 218}
GOV_ENTRY = re.compile(rb'    "(GT-[0-9]+)": \{')
GOLD_ENTRY = re.compile(rb'   "id": "(GT-[0-9]+)",')


def read_projection():
    """Use the split header and row IDs only to skip held-out JSON payloads before decoding."""
    labels, items = {}, []
    with GOVERNANCE.open("rb") as stream:
        for line in stream:
            if line.strip() == b'"samples": {':
                break
        for line in stream:
            match = GOV_ENTRY.fullmatch(line.rstrip())
            if not match:
                if line.rstrip() in (b"  }", b"  },"):
                    break
                raise ValueError("unexpected frozen governance framing")
            gid = match[1].decode("ascii")
            header = next(stream)
            split = re.fullmatch(rb'      "split": "(train|dev|test)",', header.rstrip())
            if split is None:
                raise ValueError("missing frozen split header")
            accepted = split[1] in (b"train", b"dev")
            payload = [b"{\n", header] if accepted else None
            for field in stream:
                if field.rstrip() in (b"    },", b"    }"):
                    break
                if accepted:
                    payload.append(field)
            if accepted:
                labels[gid] = json.loads(b"".join([*payload, b"}"]))
    with GOLDEN.open("rb") as stream:
        for line in stream:
            if line.strip() == b'"items": [':
                break
        for line in stream:
            if line.strip() == b"]":
                break
            gid_line = next(stream)
            match = GOLD_ENTRY.fullmatch(gid_line.rstrip())
            if line.rstrip() != b"  {" or match is None:
                raise ValueError("unexpected frozen golden framing")
            gid = match[1].decode("ascii")
            accepted = gid in labels
            payload = [line, gid_line] if accepted else None
            for field in stream:
                if field.rstrip() in (b"  },", b"  }"):
                    break
                if accepted:
                    payload.append(field)
            if accepted:
                items.append(json.loads(b"".join([*payload, b"}"])))
    if (Counter(row["split"] for row in labels.values()) != DATA_COUNTS
            or len(items) != sum(DATA_COUNTS.values()) or {item["id"] for item in items} != set(labels)):
        raise ValueError("Train/Dev must match the frozen 842/218 population")
    return items, labels


def split_report(items, labels):
    subsets = {split: [item for item in items if labels[item["id"]]["split"] == split] for split in DATA_COUNTS}
    train, dev = subsets.values()
    project = lambda rows: {r["project"] for r in rows}
    sheets = lambda rows: {(r["project"], r["source_sheet"]) for r in rows}
    signatures = lambda rows: {query_signature(r) for r in rows}
    shared_projects = sorted(project(train) & project(dev))
    shared_sheets = sorted(sheets(train) & sheets(dev))
    shared_queries = len(signatures(train) & signatures(dev))
    if shared_sheets or shared_queries:
        raise ValueError("frozen Train/Dev shares source sheets or exact query signatures")
    return {
        "frozen_population": DATA_COUNTS, "source_sheet_counts": {k: len(sheets(v)) for k, v in subsets.items()},
        "source_sheet_overlap": shared_sheets, "exact_query_overlap": shared_queries,
        "project_overlap": shared_projects, "row_source_timestamps_available": False,
        "strict_project_time_validation": "unverified",
        "heldout_payloads_decoded": 0, "heldout_rows_used": 0,
        "decision": "diagnostic_only_pending_independent_projects_and_source_timestamps",
    }


def contrast_fields(left, right, domain):
    keys = []
    for key in ranker.differences(left, right):
        if key in ("管径", "外径", "防爆", "提升泵"):
            keys.append(key)
        elif key == "材质" and {left[key], right[key]} == {"304", "316l"}:
            keys.append("304_vs_316L")
        elif key == "型号" and ranker.model_suffix_differs(left[key], right[key]):
            keys.append("型号尾缀")
        elif domain == "装饰" and key in ("厚度", "尺寸"):
            keys.append("装饰/" + key)
        elif domain == "强电" and key in ("额定电流", "电压", "主芯数", "主截面"):
            keys.append("强电/" + key)
    return keys


def build_pairs(train, labels, materials):
    from app.preprocessor import extract_params
    from app.query_preprocessor import prepare_query

    catalog, names = {}, defaultdict(list)
    for material in materials:
        name = ranker.normalized(material["name"])
        if (not name or re.search(r"[（(](?:禁用|停用|无效)", material["name"] or "")
                or re.search(r"[（(](?:禁用|停用|无效)", material["specification"] or "")):
            continue
        catalog[material["code"]] = material
        names[name].append(material["code"])
    parsed = {}

    def candidate_values(code):
        if code not in parsed:
            row = catalog[code]
            spec = row["specification"] or ""
            values = extract_params(f"{row['name']} {spec}", row["category"] or "")
            parsed[code] = ranker.structured_values(row["name"], spec, values)
        return parsed[code]

    pairs = []
    for item in train:
        label = labels[item["id"]]
        if label["split"] != "train":
            raise ValueError("only Train may construct pairs")
        if not core(item) or label["status"] != "answerable" or item["query_source"] == "done_fallback":
            continue
        accepted = set().union(*label["target_alternatives"].values())
        domain = item["professional"]
        query = prepare_query(item["name"], item["specification"], domain, category=item["supply_category"],
                              unit=item["unit"], preserve_original=False)
        query_values = ranker.structured_values(query["name"], query["specification"], query["parsed_spec"])
        quota = Counter()
        for target in sorted(accepted & catalog.keys()):
            positive = candidate_values(target)
            name = ranker.normalized(catalog[target]["name"])
            for other in names[name]:
                if other in accepted:
                    continue
                negative = candidate_values(other)
                contrasts = contrast_fields(positive, negative, domain)
                if not contrasts or all(quota[key] >= 4 for key in contrasts):
                    continue
                quota.update(contrasts)
                good, bad = ranker.features(query_values, positive), ranker.features(query_values, negative)
                delta = [a - b for a, b in zip(good, bad, strict=True)]
                # Keep audit-only pairs whose catalog differences lack positive query evidence.
                fit = any(x > 0 for x in delta) and all(x >= 0 for x in delta)
                pairs.append({"train_id": item["id"], "positive": target, "negative": other,
                              "domain": domain, "contrasts": contrasts, "fit": fit, "delta": delta})
    return pairs


def fit_weights(pairs):
    import numpy as np
    from scipy.optimize import nnls

    counts = Counter(row["train_id"] for row in pairs if row["fit"])
    if not counts:
        raise ValueError("no Train pairs with distinct positive structured evidence")
    vectors = np.array([row["delta"] for row in pairs if row["fit"]], dtype=float)
    pair_weights = np.sqrt([1 / counts[row["train_id"]] for row in pairs if row["fit"]])
    # Per-query weighted, regularized nonnegative pairwise least squares. Fixed six-point budget.
    matrix = np.vstack((vectors * pair_weights[:, None], np.eye(len(ranker.FIELDS))))
    objective = np.concatenate((pair_weights, np.zeros(len(ranker.FIELDS))))
    weights, loss = nnls(matrix, objective)
    weights = 5.99 * weights / weights.sum()
    return [round(float(value), 8) for value in weights], float(loss)


def run(output, asset):
    items, labels = read_projection()
    split = split_report(items, labels)
    with TemporaryDirectory(prefix="listtrans_v020_fit_", ignore_cleanup_errors=True) as directory:
        inputs = stage(Path(directory) / "isolated", "keyword", read_json(V017 / "m0_r01_before.json"))
        sys.addaudithook(block_external_network)
        catalog, _ = read_catalog()
        train = [item for item in items if labels[item["id"]]["split"] == "train"]
        pairs = build_pairs(train, {row["id"]: labels[row["id"]] for row in train}, catalog)
        weights, loss = fit_weights(pairs)
    save_json(asset, {"version": ranker.VERSION, "fields": list(ranker.FIELDS), "weights": weights})
    save_json(output, {
        "split": split, "catalog_sha256": inputs["catalog"], "asset_sha256": sha(asset),
        "train_rows": len(train), "pairs": pairs, "pair_count": len(pairs),
        "by_contrast": dict(Counter(key for row in pairs for key in row["contrasts"])),
        "fit_pairs": sum(row["fit"] for row in pairs), "fit_objective_residual": loss, "weights": weights,
        "fit_query_count": len({row["train_id"] for row in pairs if row["fit"]}),
        "fit_by_contrast": dict(Counter(key for row in pairs if row["fit"] for key in row["contrasts"])),
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--asset", required=True, type=Path)
    args = parser.parse_args()
    run(args.out, args.asset)
