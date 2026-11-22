# -*- coding: utf-8 -*-
"""V017-M1 / M1-C05：结构化抽取小样本评测。

对 tests/fixtures/structured_extraction_eval_v1.json 实测：
1. 路由正确率（expected_domain 非 None 的样本）
2. 字段抽取 Precision / Recall / F1（precise 样本的 expected_fields）
3. 噪声拒入率（must_not_fields 与 unwhitelisted_attrs）
4. 逐条留痕（每条的实际抽取结果与是否命中，含歧义样本）

用法：
  python tests/eval_structured_extraction.py [--out evidence/m1_r01_structured_eval.json]
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests/fixtures/structured_extraction_eval_v1.json"


def _field_matches(actual_attrs, key, expected):
    """判断单个字段是否命中期望。"""
    if key not in actual_attrs:
        return False
    val = actual_attrs[key]
    if expected is None:
        return True
    if isinstance(expected, dict):
        if expected.get("any"):
            return True
        if expected.get("type") == "number":
            return isinstance(val, (int, float)) and not isinstance(val, bool)
    # 标量精确断言（材质牌号等字符串）
    if isinstance(expected, str):
        return str(val).strip().lower() == expected.strip().lower()
    if isinstance(expected, (int, float)):
        try:
            return float(val) == float(expected)
        except (TypeError, ValueError):
            return False
    return False


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def evaluate():
    from app.query_preprocessor import extract_query_params

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    entries = payload["entries"]

    routing_correct = 0
    routing_evaluated = 0
    # 字段级 TP/FP/FN（跨 precise 样本累计）
    field_tp = field_fp = field_fn = 0
    noise_blocked_total = 0
    noise_present_total = 0

    per_entry = []

    for e in entries:
        parsed = extract_query_params(
            e["name"], e["specification"], e["professional"],
            material_category=e["category"],
        )
        domain = parsed.get("domain", "")
        attrs = parsed.get("attrs", {})
        unwhitelisted = parsed.get("unwhitelisted_attrs", {})
        typed = parsed.get("typed_attrs", {})

        # 路由正确率
        exp_domain = e.get("expected_domain")
        routing_ok = None
        if exp_domain:
            routing_evaluated += 1
            routing_ok = (domain == exp_domain)
            if routing_ok:
                routing_correct += 1

        # 字段 Precision/Recall/F1（precise 样本）
        expected_fields = e.get("expected_fields", {})
        field_hits = 0
        for key, exp in expected_fields.items():
            if _field_matches(attrs, key, exp):
                field_tp += 1
                field_hits += 1
            else:
                # 若该字段被误隔离到 unwhitelisted，也算 FP（噪声键但期望出现）
                if key in unwhitelisted:
                    field_fp += 1
                else:
                    field_fn += 1

        # V017-M1 / M1-C05 修复 A-M1-03：must_not_fields（误抽字段黑名单）也应计入
        # field_fp。旧逻辑仅用于"噪声拒入率"，未把白名单内误抽字段（如 GT-0008 的
        # SS316L 被误抽为 压力=316.0 / 型号=SS316L）计为 FP，导致 Precision 恒 1.0 虚高。
        must_not = e.get("must_not_fields", [])
        noise_blocked = 0
        for nk in must_not:
            noise_present_total += 1
            if nk not in attrs:
                noise_blocked += 1
                noise_blocked_total += 1
            else:
                # 误抽字段出现在 attrs：计入 field_fp（仅 precise 样本，避免污染路由场景样本）
                if expected_fields or e.get("precise"):
                    field_fp += 1

        per_entry.append({
            "id": e["id"],
            "source": e["source"],
            "professional": e["professional"],
            "expected_domain": exp_domain,
            "actual_domain": domain,
            "routing_ok": routing_ok,
            "expected_fields": expected_fields,
            "actual_attrs": attrs,
            "unwhitelisted_attrs": unwhitelisted,
            "typed_attrs_keys": sorted(typed.keys()),
            "must_not_fields": must_not,
            "noise_blocked": noise_blocked if must_not else None,
        })

    precision = field_tp / (field_tp + field_fp) if (field_tp + field_fp) > 0 else None
    recall = field_tp / (field_tp + field_fn) if (field_tp + field_fn) > 0 else None
    f1 = (2 * precision * recall / (precision + recall)) if (precision and recall) else None
    routing_acc = (routing_correct / routing_evaluated) if routing_evaluated else None
    noise_block_rate = (noise_blocked_total / noise_present_total) if noise_present_total else None

    return {
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "total_entries": len(entries),
        "routing_accuracy": routing_acc,
        "routing_evaluated": routing_evaluated,
        "routing_correct": routing_correct,
        "field_precision": precision,
        "field_recall": recall,
        "field_f1": f1,
        "field_tp": field_tp,
        "field_fp": field_fp,
        "field_fn": field_fn,
        "noise_rejection_rate": noise_block_rate,
        "noise_blocked": noise_blocked_total,
        "noise_present": noise_present_total,
        "per_entry": per_entry,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    result = evaluate()
    print(f"路由正确率: {result['routing_accuracy']:.4f} ({result['routing_correct']}/{result['routing_evaluated']})")
    print(f"字段 Precision: {result['field_precision']:.4f} | Recall: {result['field_recall']:.4f} | F1: {result['field_f1']:.4f}")
    print(f"  TP={result['field_tp']} FP={result['field_fp']} FN={result['field_fn']}")
    print(f"噪声拒入率: {result['noise_rejection_rate']:.4f} ({result['noise_blocked']}/{result['noise_present']})")

    # 逐条留痕（仅打印未命中的）
    misses = [p for p in result["per_entry"] if p["routing_ok"] is False or
              any(not _field_matches(p["actual_attrs"], k, v) for k, v in p["expected_fields"].items())]
    for m in misses:
        print(f"  [未命中] {m['id']} 期望域={m['expected_domain']} 实际域={m['actual_domain']} "
              f"期望字段={list(m['expected_fields'].keys())} 实际={m['actual_attrs']}")

    if args.out:
        with args.out.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print(f"已写入 {args.out}")


if __name__ == "__main__":
    main()
