# -*- coding: utf-8 -*-
import os
import sys
import json
import time

# Ensure project root is in sys.path
sys.path.append(r"c:\Users\wangd\project\ListTrans\ListTrans")

from app.matcher import match_item
from app.preprocessor import extract_params

def run_regression():
    from app.db_manager import init_db
    from app.vector_index import index_manager
    init_db()
    index_manager.load_cache()
    
    fixtures_dir = r"c:\Users\wangd\project\ListTrans\ListTrans\tests\fixtures"
    ground_truth_path = os.path.join(fixtures_dir, "ground_truth_500.json")
    if not os.path.exists(ground_truth_path):
        print(f"Error: Ground truth file not found at {ground_truth_path}")
        return

    with open(ground_truth_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    print(f"Loaded {len(dataset)} items for regression testing.")

    total = len(dataset)
    top1_hits = 0
    top3_hits = 0
    
    # Must attribute match details
    total_must_fields = 0
    extracted_must_fields = 0
    correct_must_fields = 0

    start_time = time.time()
    
    for idx, item in enumerate(dataset):
        name = item["name"]
        spec = item["specification"]
        cat = item["category"]
        gt_code = item["ground_truth_code"]
        gt_must = item.get("must_attrs", {})
        
        # 1. Evaluate Matching
        match_query = {
            "name": name,
            "specification": spec,
            "category": cat
        }
        
        try:
            match_res = match_item(match_query)
            top_candidates = match_res.get("top_candidates", [])
            
            top_codes = [c.get("code") for c in top_candidates[:3]]
            if top_codes:
                if top_codes[0] == gt_code:
                    top1_hits += 1
                if gt_code in top_codes:
                    top3_hits += 1
        except Exception as e:
            # print(f"Match error for item {idx}: {e}")
            pass
            
        # 2. Evaluate Must Attribute Extraction
        try:
            parsed = extract_params(spec, cat)
            attrs = parsed.get("attrs", {})
            
            for k, expected_val in gt_must.items():
                total_must_fields += 1
                if k in attrs:
                    extracted_must_fields += 1
                    # Cast both to string for comparison to avoid float representation differences
                    if str(attrs[k]).strip() == str(expected_val).strip():
                        correct_must_fields += 1
        except Exception as e:
            # print(f"Extract error for item {idx}: {e}")
            pass

    elapsed = time.time() - start_time
    
    # Compute metrics
    top1_rate = (top1_hits / total) * 100.0 if total > 0 else 0.0
    top3_rate = (top3_hits / total) * 100.0 if total > 0 else 0.0
    
    structure_extraction_rate = (extracted_must_fields / total_must_fields) * 100.0 if total_must_fields > 0 else 0.0
    extraction_accuracy_rate = (correct_must_fields / extracted_must_fields) * 100.0 if extracted_must_fields > 0 else 0.0
    overall_structure_rate = (correct_must_fields / total_must_fields) * 100.0 if total_must_fields > 0 else 0.0

    print("=================== REGRESSION RESULTS ===================")
    print(f"Total Evaluated: {total}")
    print(f"Elapsed Time: {elapsed:.2f} seconds ({ (elapsed/total)*1000 if total > 0 else 0:.2f} ms/row)")
    print(f"Top-1 Accuracy: {top1_rate:.2f}%")
    print(f"Top-3 Coverage: {top3_rate:.2f}%")
    print(f"Must Fields Extraction Rate: {structure_extraction_rate:.2f}%")
    print(f"Must Fields Accuracy: {extraction_accuracy_rate:.2f}%")
    print(f"Overall Structuring Rate (Correct/Expected): {overall_structure_rate:.2f}%")
    print("=========================================================")

    # Write report to baseline_metrics_v0.0.5.md
    report_path = r"c:\Users\wangd\project\ListTrans\ListTrans\doc\baseline_metrics_v0.0.5.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"""# 华康洁净物料匹配系统 · 回归指标度量报告 (v0.0.5)

> 报告生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}
> 测试集大小：{total} 行金标数据

## 一、核心指标摘要

| 指标 | 达成值 | 目标值 | 状态 |
|---|---|---|---|
| **Top-1 匹配准确率** | {top1_rate:.2f}% | ≥ 85.0% | {'✅ 达标' if top1_rate >= 85.0 else '❌ 未达标'} |
| **Top-3 匹配覆盖率** | {top3_rate:.2f}% | ≥ 90.0% | {'✅ 达标' if top3_rate >= 90.0 else '❌ 未达标'} |
| **Must 字段结构化率** | {overall_structure_rate:.2f}% | ≥ 85.0% | {'✅ 达标' if overall_structure_rate >= 85.0 else '❌ 未达标'} |

## 二、详细性能与度量分析

- **总耗时**：{elapsed:.2f} 秒
- **单行平均耗时**：{ (elapsed/total)*1000 if total > 0 else 0:.2f} 毫秒
- **Must 字段覆盖率** (提取出/期望总数)：{structure_extraction_rate:.2f}%
- **Must 字段准确率** (提取正确/提取出)：{extraction_accuracy_rate:.2f}%
""")
    print(f"Report written to {report_path}")

if __name__ == "__main__":
    run_regression()
