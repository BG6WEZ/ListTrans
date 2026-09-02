# -*- coding: utf-8 -*-
"""
黄金测试集自动化回归评测 (Golden Set Evaluator)

用法:
  python tests/eval_golden_set.py                     # 全量评测核心集
  python tests/eval_golden_set.py --limit 200         # 分层抽样快速评测
  python tests/eval_golden_set.py --chunk-size 100    # 内存紧张时减小分块（默认 200）
  python tests/eval_golden_set.py --include-1ton      # 同时评测 1toN 条目
  python tests/eval_golden_set.py --baseline          # 将本次结果写为基线
  python tests/eval_golden_set.py --gate              # 与基线比对，回退则退出码 1

指标定义:
  Hit@1         Top-1 命中率（首选物料即正确答案）
  Recall@3/5/10 前 K 个候选包含正确答案的比例
  MRR           平均倒数排名（未命中记 0）
  miss@10       Top-10 完全未命中比例，用于区分"召回漏"与"排序错"

内存说明:
  本机 16G 内存，物料向量矩阵约 47750x512。评测采用分块流式处理，
  单进程峰值控制在 2G 以内；如仍紧张可用 --chunk-size 50 进一步降低。
"""
import os
import io
import gc
import sys
import json
import time
import argparse
import collections

# 16G 内存机器：限制 BLAS/OMP 线程数，避免 numpy 矩阵运算开出多份线程私有缓冲区
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "2")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

GOLDEN_PATH = os.path.join(PROJECT_ROOT, "tests", "fixtures", "golden_set_v1.json")
BASELINE_PATH = os.path.join(PROJECT_ROOT, "tests", "fixtures", "golden_baseline.json")
DETAIL_PATH = os.path.join(PROJECT_ROOT, "tests", "fixtures", "golden_last_run.json")

TARGETS = {"hit@1": 85.0, "recall@3": 90.0, "recall@5": 93.0, "mrr": 0.88}


def evaluate(items, chunk_size=200):
    """分块流式评测。

    16G 内存机器上必须分块：一次性预处理 + 一次性向量化 1000+ 条会与
    47750x512 的物料向量矩阵（约 93MB float32，运算时的临时矩阵可达数百 MB）
    叠加，容易触发 OOM。此处按 chunk_size 分块，块内用完即释放。
    """
    from app.matcher import match_item
    from app.query_preprocessor import extract_query_params
    from app.vector_index import index_manager

    rows = []
    t0 = time.time()
    total = len(items)

    for start in range(0, total, chunk_size):
        chunk = items[start:start + chunk_size]

        # 与生产流程 batch_match_rows 完全对齐：先 extract_query_params 清洗，
        # 再用清洗后的 name/spec 构造 query_info，并按块批量向量化。
        prepped = []
        for it in chunk:
            prof = it["professional"]
            parsed = extract_query_params(it["name"], it["specification"], prof)
            real_name = parsed.get("name") or (parsed.get("attrs", {}).get("名称") if isinstance(parsed, dict) else None) or it["name"]
            clean_spec = parsed.get("clean_text") or parsed.get("spec") or it["specification"]
            sup = (it.get("supply_category") or "").strip()
            if sup in ("无", "/", "-"):
                sup = ""
            prepped.append({
                "name": real_name,
                "specification": clean_spec,
                # 与生产 batch_match_rows L1000 一致：甲供材品类 -> 专业 -> 名称
                "category": sup or prof or real_name,
                "brand": "",
                "professional": prof,
                "parsed_spec": parsed,
            })

        vectors = [None] * len(prepped)
        if index_manager.is_enabled and prepped:
            texts = [f"{p['name']} {p['specification']}".strip() for p in prepped]
            embs = index_manager.encode_queries(texts)
            if embs is not None:
                for i, e in enumerate(embs):
                    vectors[i] = e
            del embs, texts

        for i, it in enumerate(chunk):
            gt = set(it["target_codes"])
            try:
                res = match_item(dict(prepped[i]), vectors[i])
                cands = res.get("top_candidates", []) or []
            except Exception as e:
                rows.append({"id": it["id"], "professional": it["professional"], "rank": 0,
                             "error": str(e), "top": []})
                continue

            rank = 0
            for pos, c in enumerate(cands, 1):
                if c.get("code") in gt:
                    rank = pos
                    break
            rows.append({
                "id": it["id"],
                "professional": it["professional"],
                "name": it["name"],
                "clean_name": prepped[i]["name"],
                "specification": it["specification"][:200],
                "target_codes": it["target_codes"],
                "target_name": it["target_ref"][0]["db_name"] if it.get("target_ref") else "",
                "target_spec": str(it["target_ref"][0]["db_spec"])[:80] if it.get("target_ref") else "",
                "rank": rank,
                "weight": it.get("freq", 1),
                "top": [{"code": c.get("code"), "name": c.get("name"),
                         "spec": str(c.get("specification"))[:60],
                         "score": round(float(c.get("score", 0)), 2)} for c in cands[:5]],
            })
            del res, cands

        del prepped, vectors
        gc.collect()
        print(f"  ... {min(start + chunk_size, total)}/{total}  ({time.time() - t0:.1f}s)")

    return rows, time.time() - t0


def metrics(rows):
    n = len(rows)
    if n == 0:
        return {
            "total": 0,
            "hit@1": 0.0,
            "recall@3": 0.0,
            "recall@5": 0.0,
            "recall@10": 0.0,
            "mrr": 0.0,
            "miss@10": 0.0,
        }
    hit1 = sum(1 for r in rows if r["rank"] == 1)
    r3 = sum(1 for r in rows if 1 <= r["rank"] <= 3)
    r5 = sum(1 for r in rows if 1 <= r["rank"] <= 5)
    r10 = sum(1 for r in rows if 1 <= r["rank"] <= 10)
    mrr = sum(1.0 / r["rank"] for r in rows if r["rank"] > 0) / n
    return {
        "total": n,
        "hit@1": round(hit1 / n * 100, 2),
        "recall@3": round(r3 / n * 100, 2),
        "recall@5": round(r5 / n * 100, 2),
        "recall@10": round(r10 / n * 100, 2),
        "mrr": round(mrr, 4),
        "miss@10": round((n - r10) / n * 100, 2),
    }


def by_professional(rows):
    g = collections.defaultdict(list)
    for r in rows:
        g[r["professional"]].append(r)
    return {k: metrics(v) for k, v in sorted(g.items(), key=lambda x: -len(x[1]))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--include-1ton", action="store_true")
    ap.add_argument("--include-ambiguous", action="store_true")
    ap.add_argument("--include-qc-suspect", action="store_true")
    ap.add_argument("--professional", default="")
    ap.add_argument("--exclude-source", default="", help="排除指定 source 评测用例 (如 flywheel_auto)")
    ap.add_argument("--filter-keyword", default="", help="按原始名称/规格关键词过滤评测用例")
    ap.add_argument("--case-ids", default="", help="按逗号分隔的 case id 列表过滤")
    ap.add_argument("--output-json", default="", help="将评测结果与明细输出为指定 JSON 文件")
    ap.add_argument("--chunk-size", type=int, default=200,
                    help="分块大小，内存紧张时减小（默认 200）")
    ap.add_argument("--baseline", action="store_true", help="将本次结果写为基线")
    ap.add_argument("--gate", action="store_true", help="与基线比对，回退则退出码 1")
    ap.add_argument("--tolerance", type=float, default=0.5, help="门禁允许的回退百分点")
    args = ap.parse_args()

    with open(GOLDEN_PATH, encoding="utf-8") as f:
        data = json.load(f)
    items = data["items"]
    meta_version = data["meta"]["version"]
    del data
    gc.collect()

    if args.exclude_source:
        exc = set(s.strip() for s in args.exclude_source.split(",") if s.strip())
        items = [x for x in items if x.get("source") not in exc]
    if not args.include_1ton:
        items = [x for x in items if x["label_type"] == "1to1"]
    if not args.include_ambiguous:
        items = [x for x in items if not x["ambiguous"]]
    if not args.include_qc_suspect:
        items = [x for x in items if not x.get("qc_suspect")]
    if args.professional:
        items = [x for x in items if x["professional"] == args.professional]
    if args.filter_keyword:
        kw = args.filter_keyword.strip().lower()
        items = [
            x for x in items
            if kw in x.get("name", "").lower()
            or kw in x.get("specification", "").lower()
            or kw in x.get("target_name", "").lower()
            or any(kw in ref.get("db_name", "").lower() for ref in x.get("target_ref", []))
            or kw in x.get("raw_name", "").lower()
            or kw in x.get("raw_spec", "").lower()
        ]
    if args.case_ids:
        cids = set(c.strip() for c in args.case_ids.split(",") if c.strip())
        items = [x for x in items if str(x.get("id", "")) in cids or str(x.get("idx", "")) in cids]
    if args.limit and args.limit < len(items):
        # 测试集按专业排序存放，直接切片会造成抽样偏差，此处按专业比例分层抽样
        buckets = collections.OrderedDict()
        for x in items:
            buckets.setdefault(x["professional"], []).append(x)
        total = len(items)
        picked = []
        for p, lst in buckets.items():
            k = max(1, round(len(lst) / total * args.limit))
            step = max(1, len(lst) // k)
            picked.extend(lst[::step][:k])
        items = picked[:args.limit]

    print(f"黄金测试集 {meta_version}，本次评测 {len(items)} 条，分块 {args.chunk_size}")

    from app.db_manager import init_db
    from app.vector_index import index_manager
    init_db()
    if not index_manager.load_cache():
        # 缓存与当前模型维度/名称不一致时必须重建，否则向量召回通道静默失效，指标不可信
        print("向量索引缓存不可用，正在重建（首次或换模型后需要，耗时较长）...")
        from app.db_manager import get_all_materials
        index_manager.build_index(get_all_materials(limit=10 ** 9))
        index_manager.save_cache()
        print("向量索引重建完成。")

    rows, elapsed = evaluate(items, chunk_size=args.chunk_size)
    del items
    gc.collect()
    m = metrics(rows)
    prof = by_professional(rows)

    print("\n" + "=" * 66)
    print("整体指标")
    print("=" * 66)
    for k in ("total", "hit@1", "recall@3", "recall@5", "recall@10", "mrr", "miss@10"):
        tv = TARGETS.get(k)
        flag = ""
        if tv is not None:
            flag = "  [达标]" if m[k] >= tv else f"  [未达标 目标{tv}]"
        print(f"  {k:<10}: {m[k]}{flag}")
    print(f"  耗时      : {elapsed:.1f}s  ({elapsed / max(1, len(rows)) * 1000:.0f} ms/条)")

    print("\n" + "=" * 66)
    print("分专业指标")
    print("=" * 66)
    print(f"  {'专业':<10}{'条数':>6}{'Hit@1':>9}{'R@3':>8}{'R@5':>8}{'MRR':>8}")
    for p, pm in prof.items():
        print(f"  {p:<10}{pm['total']:>6}{pm['hit@1']:>9}{pm['recall@3']:>8}"
              f"{pm['recall@5']:>8}{pm['mrr']:>8}")

    print("\n" + "=" * 66)
    print("Top-1 错配 Top15（按真集出现频次排序，优先修这些）")
    print("=" * 66)
    bad = sorted([r for r in rows if r["rank"] != 1], key=lambda x: -x.get("weight", 1))
    for r in bad[:15]:
        top = r["top"][0] if r["top"] else {}
        print(f"  [{r['id']}] {r['professional']} freq={r.get('weight')} rank={r['rank']}")
        print(f"      Q  : {r['name']} | {r['specification'][:70]}")
        print(f"      GT : {r['target_codes']} {r['target_name']}")
        print(f"      系统: {top.get('code')} {top.get('name')} | {top.get('spec')} "
              f"score={top.get('score')}")

    result = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "overall": m,
              "by_professional": prof, "elapsed_sec": round(elapsed, 1),
              "eval_scope": {"count": len(rows), "include_1ton": args.include_1ton,
                             "include_ambiguous": args.include_ambiguous,
                             "include_qc_suspect": args.include_qc_suspect,
                             "professional": args.professional or "ALL",
                             "limit": args.limit, "chunk_size": args.chunk_size}}
    # 流式逐行写盘，避免 json.dump 一次性构造上兆字符串占用内存
    with open(DETAIL_PATH, "w", encoding="utf-8") as f:
        f.write('{"summary": ')
        json.dump(result, f, ensure_ascii=False)
        f.write(', "rows": [\n')
        for i, r in enumerate(rows):
            if i:
                f.write(",\n")
            json.dump(r, f, ensure_ascii=False)
        f.write("\n]}\n")
    print(f"\n明细已写入: {DETAIL_PATH}")

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump({"summary": result, "rows": rows}, f, ensure_ascii=False, indent=2)
        print(f"指定结果文件已写入: {args.output_json}")

    if args.baseline:
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(f"基线已写入: {BASELINE_PATH}")

    if args.gate:
        if not os.path.exists(BASELINE_PATH):
            print("\n[GATE] 无基线文件，请先执行 --baseline")
            sys.exit(1)
        with open(BASELINE_PATH, encoding="utf-8") as f:
            base = json.load(f)["overall"]
        regress = []
        for k in ("hit@1", "recall@3", "recall@5"):
            delta = m[k] - base.get(k, 0)
            mark = "+" if delta >= 0 else ""
            print(f"[GATE] {k}: {base.get(k)} -> {m[k]}  ({mark}{delta:.2f})")
            if delta < -args.tolerance:
                regress.append(k)
        dm = m["mrr"] - base.get("mrr", 0)
        print(f"[GATE] mrr: {base.get('mrr')} -> {m['mrr']}  ({'+' if dm >= 0 else ''}{dm:.4f})")
        if dm < -args.tolerance / 100:
            regress.append("mrr")
        if regress:
            print(f"\n[GATE] 失败：指标回退 -> {regress}")
            sys.exit(1)
        print("\n[GATE] 通过：无指标回退")


if __name__ == "__main__":
    main()
