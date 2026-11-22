# -*- coding: utf-8 -*-
"""
华康洁净物料匹配库 - 离线 LLM 物料结构化治理脚本

用法示例:
  # 1. Dry run 测试前 5 条
  python scripts/batch_llm_structure.py --limit 5 --dry-run

  # 2. 对风机域 (fan) 批处理结构化 50 条物料
  python scripts/batch_llm_structure.py --domain fan --limit 50

  # 3. 对所有缺失型号(model)的物料全量结构化治理
  python scripts/batch_llm_structure.py --missing-model-only
"""

import os
import sys
import json
import time
from contextlib import closing
import argparse
import logging

# Ensure UTF-8 stdout encoding on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is in PYTHONPATH
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.llm_client import llm_client
from app.domain_router import route_domain
from app.preprocessor import extract_device_name
from app.normalizer import clean_text_standard
from app.domain_extractors import domain_schema

from app.db_manager import get_db_connection, update_material_params

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("batch_llm_structure")

def run_batch_structuring(args):
    if not llm_client.is_enabled():
        logger.error("LLM client is NOT enabled! Please check LLM_ENABLED and DASHSCOPE_API_KEY in .env file.")
        return

    # Keep the original values for the optimistic check when each batch is written.
    with closing(get_db_connection()) as conn:
        all_rows = conn.execute("""
            SELECT code, name, specification, category, parsed_params,
                   COALESCE(specification, '') || ' ' || COALESCE(description, '') AS catalog_spec
            FROM materials
        """).fetchall()

    targets = []
    for row in all_rows:
        code = row["code"]
        name = row["name"] or ""
        spec = row["specification"] or ""
        cat = row["category"] or ""
        full_spec = f"{name} {spec}".strip()
        
        parsed = {}
        if row["parsed_params"]:
            try:
                parsed = json.loads(row["parsed_params"])
            except Exception:
                parsed = {}

        has_model = bool(parsed.get("model"))
        valid_attrs = [v for k, v in parsed.get("attrs", {}).items() if v and v is not False]
        has_valid_attrs = len(valid_attrs) >= 2

        if args.missing_model_only and has_model:
            continue
        if args.empty_attrs_only and has_valid_attrs:
            continue
            
        name_ext = extract_device_name(spec, cat)
        text_clean = clean_text_standard(spec)
        domain = route_domain(cat, name_ext, text_clean, cat)

        if args.domain and domain != args.domain:
            continue

        targets.append({
            "code": code,
            "name": name,
            "specification": spec,
            "category": cat,
            "full_spec": full_spec,
            "domain": domain,
            "parsed": parsed,
            "previous_parsed": row["parsed_params"],
            "catalog_spec": row["catalog_spec"],
        })

    total_targets = len(targets)
    if args.limit and args.limit > 0:
        targets = targets[:args.limit]

    logger.info(f"Target selection complete: Found {total_targets} total candidate materials. Processing batch of {len(targets)} items.")

    if len(targets) == 0:
        logger.info("No materials match the criteria. Exiting.")
        return

    success_cnt = 0
    fail_cnt = 0
    start_time = time.time()

    pending_updates = []
    for idx, item in enumerate(targets, 1):
        if llm_client.check_circuit_breaker():
            logger.error("Circuit breaker is OPEN. Stopping batch execution.")
            break

        code = item["code"]
        full_spec = item["full_spec"]
        domain = item["domain"]
        parsed = item["parsed"]

        domain_config = domain_schema.get(domain, {})
        must_keys = [f["key"] for f in domain_config.get("must", [])]
        should_keys = [f["key"] for f in domain_config.get("should", [])]
        flags = domain_config.get("flags", [])

        if args.dry_run:
            logger.info(f"[{idx}/{len(targets)}] DRY-RUN Processing code={code}, domain={domain}, spec='{full_spec[:40]}...'")

        llm_result = llm_client.extract_params_by_llm(full_spec, domain, must_keys, should_keys, flags)
        
        if llm_result:
            model = llm_result.get("model", "")
            attrs = llm_result.get("attrs", {})

            if args.dry_run:
                logger.info(f"  -> Extracted Model: '{model}'")
                logger.info(f"  -> Extracted Attrs: {json.dumps(attrs, ensure_ascii=False)}")
                success_cnt += 1
            else:
                # Merge into parsed_params
                if not parsed.get("model") and model:
                    parsed["model"] = model
                if "attrs" not in parsed or not isinstance(parsed["attrs"], dict):
                    parsed["attrs"] = {}
                
                for k, v in attrs.items():
                    parsed["attrs"][k] = v

                # Standardize aliases
                if "机外余压" in parsed["attrs"]:
                    parsed["attrs"]["余压"] = parsed["attrs"]["机外余压"]
                if "管径" in parsed["attrs"]:
                    parsed["attrs"]["口径"] = parsed["attrs"]["管径"]

                pending_updates.append((
                    code, item["previous_parsed"], json.dumps(parsed, ensure_ascii=False), item["catalog_spec"],
                ))

                if len(pending_updates) >= args.batch_size:
                    count = update_material_params(pending_updates)
                    success_cnt += count
                    fail_cnt += len(pending_updates) - count
                    pending_updates.clear()
                    logger.info(f"[{idx}/{len(targets)}] Committed {count} updates to database.")
        else:
            fail_cnt += 1
            logger.warning(f"[{idx}/{len(targets)}] Failed/Skipped LLM extraction for code={code}")

        if args.delay > 0:
            time.sleep(args.delay)

    if pending_updates:
        count = update_material_params(pending_updates)
        success_cnt += count
        fail_cnt += len(pending_updates) - count

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info(f"Batch LLM Structuring Finished!")
    logger.info(f"Processed: {len(targets)} | Success: {success_cnt} | Failed: {fail_cnt}")
    logger.info(f"Elapsed Time: {elapsed:.2f}s | Avg Time per Item: {elapsed/max(1, len(targets)):.2f}s")
    logger.info(f"LLM Stats: {json.dumps(llm_client.get_stats(), ensure_ascii=False, indent=2)}")
    logger.info("=" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch LLM Material Data Structuring Script")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of items to process (0 for all)")
    parser.add_argument("--domain", type=str, default="", help="Filter by specific domain name (e.g. fan, damper, valve_water)")
    parser.add_argument("--missing-model-only", action="store_true", help="Only process items missing a model")
    parser.add_argument("--empty-attrs-only", action="store_true", help="Only process items with 0 or 1 valid attrs")
    parser.add_argument("--batch-size", type=int, default=20, help="Database commit batch size")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay in seconds between API calls")
    parser.add_argument("--dry-run", action="store_true", help="Perform extraction without modifying database")

    args = parser.parse_args()
    run_batch_structuring(args)
