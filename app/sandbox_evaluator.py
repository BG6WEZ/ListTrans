"""Run production matching and binding against versioned evaluation samples."""
import os
import gc
import sys
import json
import time
import argparse
import collections
import hashlib
import math


def _sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _evaluation_snapshot(items, chunk_size, golden_digest):
    """随结果记录只读物料、金标及离线模型/向量通道与预算标识。"""
    from app.vector_index import get_cache_dir, index_manager
    from app.runtime_eval_assets import runtime_model_state

    db_path = os.environ.get("MATERIALS_DB_PATH", "")
    cache_path = os.path.join(get_cache_dir(), "materials_embeddings.npy")
    materials_digest = ""
    if db_path and os.path.isfile(db_path):
        try:
            from app.rule_governance import compute_materials_catalog_digest
            materials_digest = compute_materials_catalog_digest(db_path=db_path)
        except Exception:
            materials_digest = _sha256_file(db_path)
    model_state = runtime_model_state()
    return {
        "model_version": model_state["model_version"],
        "materials_db_sha256": materials_digest,
        "golden_set_sha256": golden_digest,
        "golden_set_item_count": len(items),
        "vector_channel": {
            **model_state,
            "cache_embeddings_sha256": _sha256_file(cache_path) if model_state["enabled"] and os.path.isfile(cache_path) else "",
            "active_material_codes": [m.get("code") for m in index_manager.index_to_material] if model_state["enabled"] else [],
        },
        "budget": {"chunk_size": chunk_size, "top_k": 10},
    }

# 16G 内存机器：限制 BLAS/OMP 线程数，避免 numpy 矩阵运算开出多份线程私有缓冲区
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "2")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

TARGETS = {"hit@1": 85.0, "recall@3": 90.0, "recall@5": 93.0, "mrr": 0.88}


def _sample_skip_reason(item):
    if not isinstance(item, dict):
        return "invalid_sample"
    codes = item.get("target_codes")
    if not isinstance(codes, list) or not codes or any(not isinstance(c, str) or not c.strip() for c in codes):
        return "no_valid_target_codes"
    if (not isinstance(item.get("name"), str) or not item["name"].strip()
            or not isinstance(item.get("specification"), str)
            or not isinstance(item.get("professional"), str) or not item.get("id")):
        return "invalid_sample_fields"
    if any(item.get(key) is not None and not isinstance(item[key], str)
           for key in ("supply_category", "unit", "brand", "raw_name", "raw_spec", "target_name")):
        return "invalid_sample_fields"
    if item.get("target_ref") and (not isinstance(item["target_ref"], list)
            or any(not isinstance(ref, dict) for ref in item["target_ref"])):
        return "invalid_sample_fields"
    try:
        if not math.isfinite(float(item.get("qty") or 1.0)) or float(item.get("qty") or 1.0) <= 0:
            return "invalid_sample_fields"
    except (TypeError, ValueError):
        return "invalid_sample_fields"
    return ""


def evaluate(items, chunk_size=200):
    """分块流式评测。

    16G 内存机器上必须分块：一次性预处理 + 一次性向量化 1000+ 条会与
    47750x512 的物料向量矩阵（约 93MB float32，运算时的临时矩阵可达数百 MB）
    叠加，容易触发 OOM。此处按 chunk_size 分块，块内用完即释放。
    """
    from app.matcher import match_item
    from app.query_preprocessor import prepare_query
    from app.vector_index import index_manager

    rows = []
    t0 = time.time()
    total = len(items)

    for start in range(0, total, chunk_size):
        chunk = items[start:start + chunk_size]

        # V015-M1：评测与生产 batch_match_rows 共用统一 prepare_query 契约，
        # 规范名称/清洗文本/专业/品类/单位/品牌来自同一函数，消除"评测读
        # attrs['名称']、生产 get('name') 恒回退原文"的双轨差异。
        prepped = []
        valid_chunk = []
        for it in chunk:
            skip_reason = _sample_skip_reason(it)
            if skip_reason:
                rows.append({"id": it.get("id") if isinstance(it, dict) else None,
                             "professional": it.get("professional", "") if isinstance(it, dict) else "",
                             "rank": 0, "top": [], "skipped": True, "skip_reason": skip_reason})
                continue
            prof = it["professional"]
            sup = (it.get("supply_category") or "").strip()
            if sup in ("无", "/", "-"):
                sup = ""
            try:
                prepped.append(prepare_query(
                    it["name"], it["specification"], prof,
                    category=sup, brand=it.get("brand", "") or "", unit=it.get("unit", "") or "",
                    preserve_original=False,
                ))
                valid_chunk.append(it)
            except Exception as exc:
                rows.append({"id": it["id"], "professional": prof, "rank": 0, "top": [],
                             "target_codes": it["target_codes"], "error": f"{type(exc).__name__}: {exc}"})

        vectors = [None] * len(prepped)
        if index_manager.is_enabled and prepped:
            texts = [f"{p['name']} {p['specification']}".strip() for p in prepped]
            embs = index_manager.encode_queries(texts)
            if embs is not None:
                for i, e in enumerate(embs):
                    vectors[i] = e
            del embs, texts

        for i, it in enumerate(valid_chunk):
            gt = set(it["target_codes"])
            try:
                res = match_item(dict(prepped[i]), vectors[i], diagnostic_targets=it["target_codes"])
                cands = res.get("top_candidates", []) or []
            except Exception as e:
                rows.append({"id": it["id"], "professional": it["professional"], "rank": 0,
                             "target_codes": it["target_codes"], "error": str(e), "top": []})
                continue

            rank = 0
            for pos, c in enumerate(cands, 1):
                if c.get("code") in gt:
                    rank = pos
                    break
            # V015-M1 R02 / LEGACY-01：除展示用 top5 外，补 `candidates` 保留
            # top_candidates 全量（match_item 输出 top10）的**原始未舍入合分**。
            # 沙箱门禁需据原始得分计算逐例正确目标得分 T 与领先间隔 margin=T-W，
            # 不得依赖 round(...,2) 的两位展示分或 top5 截断集（统筹裁决保护契约 §3/§4）。
            raw_cands = []
            for c in cands:
                try:
                    sc = float(c.get("score", 0.0))
                except (TypeError, ValueError):
                    sc = float("nan")
                raw_cands.append({"code": c.get("code"), "score": sc})
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
                "candidates": raw_cands,
                "diagnostics": res.get("diagnostics", {}),
            })
            del res, cands

        del prepped, vectors
        gc.collect()
        print(f"  ... {min(start + chunk_size, total)}/{total}  ({time.time() - t0:.1f}s)")

    return rows, time.time() - t0


def metrics(rows):
    skipped = sum(bool(r.get("skipped")) for r in rows)
    rows = [r for r in rows if not r.get("skipped")]
    traces = [t for r in rows for t in (r.get("diagnostics") or {}).get("targets", [])]
    channels = collections.Counter(source for t in traces if t.get("recalled")
                                   for source in t.get("recall_sources", []))
    target_count = sum(len(r.get("target_codes") or []) for r in rows)
    stage_counts = {state + "_count": sum(t.get("state") == state for t in traces)
                    for state in ("not_in_catalog", "not_recalled", "hard_blocked", "ranked_gt10", "top10_hit")}
    extra = {"candidate_recall": round(sum(bool(t.get("recalled")) for t in traces) / target_count * 100, 2) if target_count else 0.0,
             "candidate_recall_numerator": sum(bool(t.get("recalled")) for t in traces),
             "candidate_recall_denominator": target_count, "channel_contributions": dict(channels),
             "diagnostic_failed_targets_count": target_count - len(traces),
             "failed_samples_count": sum(bool(r.get("error")) for r in rows),
             "skipped_samples_count": skipped, **stage_counts}
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
            **extra,
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
        **extra,
    }


def by_professional(rows):
    g = collections.defaultdict(list)
    for r in rows:
        g[r["professional"]].append(r)
    return {k: metrics(v) for k, v in sorted(g.items(), key=lambda x: -len(x[1]))}


def _active_material_codes():
    from app.db_manager import get_db_connection
    conn = get_db_connection()
    try:
        return {str(r[0]) for r in conn.execute("SELECT code FROM materials").fetchall() if r[0]}
    finally:
        conn.close()


def _bom_fixture_expected(item):
    from app.bom import Expansion, positive

    expected = item.get("bom_expected")
    targets = set(item.get("target_codes") or ())
    if (item.get("label_type") != "1toN" or not isinstance(expected, list)
            or len(expected) != len(targets) or len(targets) != 2
            or any(not isinstance(row, dict) or not isinstance(row.get("code"), str)
                   or not isinstance(row.get("unit"), str) or not row["unit"].strip()
                   or not positive(row.get("qty")) or not positive(row.get("qty_ratio")) for row in expected)
            or {row["code"] for row in expected} != targets):
        return None
    return Expansion(expected, {}, "")


def _select_sandbox_subsets(all_items, rule_meta, guard_limit=40, *, with_stats=False):
    """Select targeted subset + disjoint stratified global guard subset for M2 sandbox evaluation."""
    from app.rule_context import DYNAMIC_UNIVERSAL_TOKENS, RuleContext, canonicalize_professional

    original_items = all_items
    all_items = [x for x in all_items if not _sample_skip_reason(x)]
    clean_1to1 = [
        x for x in all_items
        if x.get("label_type") == "1to1" and not x.get("ambiguous") and not x.get("qc_suspect")
    ]
    clean_all = [
        x for x in all_items
        if x.get("label_type") in ("1to1", "1toN") and not x.get("ambiguous") and not x.get("qc_suspect")
    ]
    rule_type = str(rule_meta.get("rule_type") or "ALIAS").upper()
    discipline = str(rule_meta.get("discipline") or "").strip()
    canon_disc = canonicalize_professional(discipline) if discipline else ""
    active_codes = _active_material_codes()

    targeted = []
    if rule_type == "ALIAS":
        kw = str(rule_meta.get("source_pattern") or "").strip().lower()
        if kw:
            for x in clean_1to1:
                if canon_disc and canon_disc.lower() not in DYNAMIC_UNIVERSAL_TOKENS and canon_disc != "其它":
                    if canonicalize_professional(x.get("professional", "")) != canon_disc:
                        continue
                hit_kw = (
                    kw in x.get("name", "").lower()
                    or kw in x.get("specification", "").lower()
                    or kw in x.get("target_name", "").lower()
                    or any(kw in ref.get("db_name", "").lower() for ref in x.get("target_ref", []))
                    or kw in x.get("raw_name", "").lower()
                    or kw in x.get("raw_spec", "").lower()
                )
                if hit_kw:
                    targeted.append(x)
    elif rule_type in ("BINDING", "BINDING_RULE"):
        orig_name = str(rule_meta.get("original_name") or rule_meta.get("source_pattern") or "").strip().lower()
        if rule_meta.get("bom"):
            targeted = [item for item in clean_all
                        if item["name"].strip().lower() == orig_name
                        and item["specification"].strip() == rule_meta.get("original_spec")
                        and canonicalize_professional(item.get("professional", "")) == canon_disc
                        and _bom_fixture_expected(item) is not None]
        elif orig_name:
            for x in clean_all:
                if x.get("name", "").strip().lower() == orig_name or orig_name in x.get("name", "").lower():
                    targeted.append(x)
    elif rule_type == "LLM_DSL":
        scope_level = str(rule_meta.get("scope_level") or "category").strip()
        scope_target = str(rule_meta.get("scope_target") or "").strip()
        cand_pool = [x for x in clean_1to1 if set(x.get("target_codes") or []) & active_codes]
        if not cand_pool:
            cand_pool = list(clean_1to1)
        for x in cand_pool:
            ctx = RuleContext.from_fields(
                professional=x.get("professional", ""),
                material_category=x.get("supply_category", "") or "",
                name=x.get("name", ""),
                raw_name=x.get("raw_name", "") or x.get("name", ""),
                stage="matching",
            )
            if ctx.matches_rule({
                "scope_level": scope_level,
                "scope_target": scope_target,
                "discipline": discipline,
            }):
                targeted.append(x)
        if len(targeted) > 60:
            targeted = targeted[:60]

    targeted_ids = {x.get("id") for x in targeted}
    guard_candidates = [
        x for x in clean_1to1
        if x.get("id") not in targeted_ids and (set(x.get("target_codes") or []) & active_codes)
    ]
    buckets = collections.OrderedDict()
    for x in sorted(guard_candidates, key=lambda it: (str(it.get("professional") or ""), str(it.get("id") or ""))):
        buckets.setdefault(canonicalize_professional(x.get("professional") or "") or "其它", []).append(x)

    guard = []
    if guard_limit > 0 and buckets:
        bucket_lists = list(buckets.values())
        idx = 0
        while len(guard) < guard_limit and any(bucket_lists):
            for bl in bucket_lists:
                if bl and len(guard) < guard_limit:
                    guard.append(bl.pop(0))
            bucket_lists = [bl for bl in bucket_lists if bl]
            idx += 1

    if with_stats:
        selected = {id(x) for x in targeted + guard}
        skipped = [{"id": x.get("id") if isinstance(x, dict) else None,
                    "reason": _sample_skip_reason(x) or "filtered_or_not_selected"}
                   for x in original_items if id(x) not in selected]
        return targeted, guard, {"skipped_samples_count": len(skipped), "skipped_samples": skipped}
    return targeted, guard


def _evaluate_binding_pipeline(items, is_guard=False):
    """Execute real production batch_match_rows + binding conflict/qty/export sanity checks."""
    from app import server as srv
    from app.binding_decisions import (
        STATUS_AUTO_ACCEPTED,
        STATUS_REJECTED,
        evaluate_hard_conflicts,
        validate_bindings_against_row,
    )

    if not items:
        return []

    raw_rows = []
    for idx, it in enumerate(items, start=1):
        sup = (it.get("supply_category") or "").strip()
        if sup in ("无", "/", "-"):
            sup = ""
        qty = float(it.get("qty") or 1.0)
        raw_rows.append({
            "row_idx": idx,
            "code": str(it.get("id") or ""),
            "name": it.get("name", ""),
            "specification": it.get("specification", ""),
            "unit": it.get("unit", "台") or "台",
            "qty": qty,
            "loss": 1.0,
            "category": sup,
            "brand": it.get("brand", "") or "",
            "professional": it.get("professional", "暖通"),
        })

    matched_rows = srv.batch_match_rows(raw_rows)
    out = []
    for it, raw_row, res_row in zip(items, raw_rows, matched_rows):
        gt_codes = [str(c).strip() for c in (it.get("target_codes") or []) if str(c).strip()]
        gt_set = set(gt_codes)
        bindings = res_row.get("bindings") or []
        norm_bindings = []
        qty_ratios_ok = True
        orig_qty = float(raw_row["qty"] or 0.0)
        for b in bindings:
            code = str(b.get("code") or "").strip()
            b_qty = float(b.get("qty") or 0.0)
            ratio = b.get("qty_ratio")
            if ratio is None:
                qty_ratios_ok = False
                ratio_val = 1.0
            else:
                ratio_val = float(ratio)
                expected_qty = orig_qty * ratio_val
                if ratio_val <= 0 or abs(b_qty - expected_qty) > 1e-6 * max(1.0, abs(expected_qty)):
                    qty_ratios_ok = False
            norm_bindings.append({
                "code": code,
                "name": str(b.get("name") or ""),
                "unit": str(b.get("unit") or ""),
                "qty": b_qty,
                "qty_ratio": ratio_val,
            })

        bound_codes = [b["code"] for b in norm_bindings if b["code"]]
        bound_set = set(bound_codes)
        row_context = srv._build_row_item_from_raw(raw_row)
        bom = srv._current_bom(row_context)
        if bom is None:
            val = validate_bindings_against_row(row_context, bindings, allow_unit_conversion=False)
        else:
            reason = bom.mismatch(bindings)
            if bindings:
                fixture = _bom_fixture_expected(it)
                discrepancy = fixture.mismatch(bindings) if fixture else "BOM 评测缺少独立集合与数量金标"
                if discrepancy:
                    reason = reason or discrepancy
                    qty_ratios_ok = False
            val = {"conflicts": [{"code": "", "reason": reason}] if reason and bindings else []}
        invalid_codes = list(val.get("invalid_codes") or [])
        conflicts = list(val.get("conflicts") or [])
        dec_status = str(res_row.get("decision_status") or "")
        dec_source = str(res_row.get("decision_source") or "")
        codes_match_gt = bool(gt_set) and (
            (gt_set == bound_set) if it.get("label_type") == "1toN" else bool(bound_set & gt_set)
        )
        if bom is not None:
            codes_match_gt = it.get("label_type") == "1toN" and codes_match_gt and not reason
        export_eligible = (
            dec_status == STATUS_AUTO_ACCEPTED
            and not invalid_codes
            and not conflicts
            and qty_ratios_ok
            and bool(bound_codes)
        )
        report_row = {
            "id": it.get("id"),
            "is_guard": bool(is_guard),
            "label_type": it.get("label_type", "1to1"),
            "professional": it.get("professional", ""),
            "target_codes": gt_codes,
            "decision_status": dec_status,
            "decision_source": dec_source,
            "decision_reason": str(res_row.get("decision_reason") or ""),
            "rule_applied": dec_source == "auto_binding_rule",
            "matched_code": str(res_row.get("matched_code") or ""),
            "bindings": norm_bindings,
            "invalid_codes": invalid_codes,
            "conflicts": conflicts,
            "qty_ratios_ok": qty_ratios_ok,
            "codes_match_gt": codes_match_gt,
            "export_eligible": export_eligible,
            "rejected": dec_status == STATUS_REJECTED,
        }
        if res_row.get("acceptance_calibration") is not None:
            # Preserve the original matcher proposal as regression evidence for
            # rules even when calibration clears the actual response bindings.
            top = next(iter(res_row.get("top_candidates") or ()), {})
            has_candidate = float(top.get("score") or 0.0) > 0
            conflict = evaluate_hard_conflicts(row_context, top) if has_candidate else None
            proposal = str(top.get("code") or "").strip() if has_candidate and not conflict else ""
            report_row["prior_matcher_proposal"] = {
                "matched_code": proposal,
                "codes_match_gt": bool(proposal) and (
                    gt_set == {proposal} if it.get("label_type") == "1toN" else proposal in gt_set
                ),
                "rejected": conflict is not None,
                "decision_reason": conflict or "",
            }
        out.append(report_row)
    return out


def validate_isolated_child_paths(output_path, rule_path):
    """Refuse standalone execution before importing any writable app module."""
    from pathlib import Path

    root_name = os.getenv("LISTTRANS_EVAL_ROOT")
    if not root_name or os.getenv("LLM_ENABLED") != "false" or os.getenv("TESTING") != "1":
        raise ValueError("缺少正式沙箱的离线隔离环境。")
    root = Path(root_name).resolve()
    keys = ("MATERIALS_DB_PATH", "AUTH_DB_PATH", "CONFIG_DIR", "VECTOR_CACHE_DIR", "UPLOAD_DIR",
            "OUTPUT_DIR", "LOG_DIR", "BINDING_FEEDBACK_FILE", "FEEDBACK_SAMPLES_FILE", "GOLDEN_DETAIL_FILE")
    paths = [os.getenv(key) for key in keys] + [output_path, rule_path]
    if any(not value or root not in Path(value).resolve().parents for value in paths):
        raise ValueError("正式沙箱写入路径不在本任务隔离目录内。")


def _binding_evaluation_with_errors(items, *, is_guard):
    """Keep per-sample failures observable while retaining the normal batch path."""
    try:
        return _evaluate_binding_pipeline(items, is_guard=is_guard)
    except Exception:
        rows = []
        for item in items:
            try:
                rows.extend(_evaluate_binding_pipeline([item], is_guard=is_guard))
            except Exception as exc:
                rows.append({"id": item["id"], "is_guard": is_guard,
                             "error": f"{type(exc).__name__}: {exc}"})
        return rows


def main(*, golden_path=None, detail_path=None, baseline_path=None):
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
    ap.add_argument("--eval-mode", default="match", choices=["match", "sandbox_v2"], help="评测模式")
    ap.add_argument("--rule-json", default="", help="sandbox_v2 模式下注入的规范化规则元数据文件路径")
    ap.add_argument("--guard-limit", type=int, default=40, help="sandbox_v2 全局保护抽样上限")
    args = ap.parse_args()

    from pathlib import Path

    runtime = golden_path is None
    if runtime:
        from app.runtime_eval_assets import load_runtime_golden, load_report_template

        if args.eval_mode != "sandbox_v2" or args.baseline or args.gate:
            ap.error("正式评测入口仅支持 sandbox_v2")
        validate_isolated_child_paths(args.output_json, args.rule_json)
        _, data, golden_digest = load_runtime_golden()
        load_report_template()
        detail_path = os.environ["GOLDEN_DETAIL_FILE"]
    else:
        raw = Path(golden_path).read_bytes()
        data = json.loads(raw)
        golden_digest = hashlib.sha256(raw).hexdigest()
    items = data["items"]
    meta_version = data["meta"]["version"]
    del data
    gc.collect()

    if args.eval_mode == "sandbox_v2":
        from app.db_manager import init_db
        from app.vector_index import index_manager
        from app.normalizer import get_session_alias_hits, reset_session_alias_hits
        from app.llm_rule_engine import RuleHitStatsTracker
        from app.rule_context import canonicalize_professional

        rule_meta = {}
        if args.rule_json and os.path.isfile(args.rule_json):
            with open(args.rule_json, encoding="utf-8") as rf:
                rule_meta = json.load(rf)

        if runtime:
            from app.rule_governance import require_materials_catalog
            require_materials_catalog()
        init_db()
        if index_manager.is_enabled:
            if runtime:
                os.environ["LISTTRANS_EVAL_REQUIRE_VECTOR"] = "1"
            if not index_manager.load_cache():
                from app.db_manager import get_all_materials
                index_manager.build_index(get_all_materials(limit=10 ** 9))
                index_manager.save_cache()
            if runtime and (index_manager.numpy_embeddings is None or not index_manager.index_to_material):
                raise RuntimeError("向量模型可用，但隔离目录未生成有效物料索引。")
        elif runtime:
            os.environ["LISTTRANS_EVAL_KEYWORD_ONLY"] = "1"

        targeted_items, guard_items, selection = _select_sandbox_subsets(
            items, rule_meta, guard_limit=args.guard_limit, with_stats=True
        )
        del items
        gc.collect()

        if runtime and not guard_items:
            raise ValueError("运行黄金集在当前物料库中没有可验证的全局保护样本。")
        if runtime and meta_version == "m3-synthetic-v1":
            active = _active_material_codes()
            if targeted_items and any(not set(item["target_codes"]) <= active for item in targeted_items):
                raise ValueError("内置合成样本的目标编码在当前物料库中缺失。")

        reset_session_alias_hits()
        RuleHitStatsTracker().reset_session_hits()

        targeted_rows, elapsed_t = evaluate(targeted_items, chunk_size=args.chunk_size)
        guard_rows, elapsed_g = evaluate(guard_items, chunk_size=args.chunk_size)

        binding_targeted = _binding_evaluation_with_errors(targeted_items, is_guard=False)
        binding_guard = _binding_evaluation_with_errors(guard_items, is_guard=True)
        decisions = {r["id"]: r for r in binding_targeted + binding_guard}
        for row in targeted_rows + guard_rows:
            decision = decisions.get(row["id"], {})
            row.setdefault("diagnostics", {})["decision"] = {
                key: decision.get(key) for key in ("decision_status", "decision_source", "decision_reason", "export_eligible")}

        rule_type = str(rule_meta.get("rule_type") or "ALIAS").upper()
        rule_execution_hits = 0
        if rule_type == "ALIAS":
            src_pat = str(rule_meta.get("source_pattern") or "").strip().lower()
            r_disc = str(rule_meta.get("discipline") or "").strip()
            canon_r_disc = canonicalize_professional(r_disc) if r_disc else ""
            for (h_disc, h_key), cnt in get_session_alias_hits().items():
                if h_key.lower() == src_pat:
                    if not canon_r_disc or canonicalize_professional(h_disc) == canon_r_disc:
                        rule_execution_hits += int(cnt)
        elif rule_type in ("BINDING", "BINDING_RULE"):
            rule_execution_hits = sum(1 for br in binding_targeted + binding_guard if br["rule_applied"])
        elif rule_type == "LLM_DSL":
            rid = str(rule_meta.get("rule_key") or rule_meta.get("rule_id") or "")
            if rid:
                rule_execution_hits = RuleHitStatsTracker().get_session_hit_count(rid)

        result = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "sandbox_v2",
            "golden_version": meta_version,
            "overall": metrics(targeted_rows),
            "guard_overall": metrics(guard_rows),
            "failed_samples_count": len({r["id"] for r in targeted_rows + guard_rows + binding_targeted + binding_guard if r.get("error")}),
            **selection,
            "elapsed_sec": round(elapsed_t + elapsed_g, 2),
            "targeted_count": len(targeted_rows),
            "guard_count": len(guard_rows),
            "rule_execution_hits": int(rule_execution_hits),
            "binding_checks_count": len(binding_targeted) + len(binding_guard),
            "eval_scope": {
                "count": len(targeted_rows),
                "guard_count": len(guard_rows),
                "chunk_size": args.chunk_size,
            },
            "snapshot": _evaluation_snapshot(targeted_items, args.chunk_size, golden_digest),
        }
        os.makedirs(os.path.dirname(os.path.abspath(detail_path)), exist_ok=True)
        payload = {
            "summary": result,
            "rows": targeted_rows,
            "guard_rows": guard_rows,
            "binding_rows": binding_targeted + binding_guard,
        }
        with open(detail_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        if args.output_json:
            with open(args.output_json, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        return

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
                             "filter_keyword": args.filter_keyword,
                             "case_ids": args.case_ids,
                             "exclude_source": args.exclude_source,
                             "limit": args.limit, "chunk_size": args.chunk_size},
              "snapshot": _evaluation_snapshot(rows, args.chunk_size, golden_digest)}
    # 流式逐行写盘，避免 json.dump 一次性构造上兆字符串占用内存
    os.makedirs(os.path.dirname(os.path.abspath(detail_path)), exist_ok=True)
    with open(detail_path, "w", encoding="utf-8") as f:
        f.write('{"summary": ')
        json.dump(result, f, ensure_ascii=False)
        f.write(', "rows": [\n')
        for i, r in enumerate(rows):
            if i:
                f.write(",\n")
            json.dump(r, f, ensure_ascii=False)
        f.write("\n]}\n")
    print(f"\n明细已写入: {detail_path}")

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump({"summary": result, "rows": rows}, f, ensure_ascii=False, indent=2)
        print(f"指定结果文件已写入: {args.output_json}")

    if args.baseline:
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(f"基线已写入: {baseline_path}")

    if args.gate:
        if not os.path.exists(baseline_path):
            print("\n[GATE] 无基线文件，请先执行 --baseline")
            sys.exit(1)
        with open(baseline_path, encoding="utf-8") as f:
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
