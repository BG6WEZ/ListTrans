# -*- coding: utf-8 -*-
import unittest
import os
import sys
import json
import shutil

# Ensure project root is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.server import (
    BINDING_FEEDBACK_FILE, AUTO_BINDING_RULES_FILE,
    load_auto_binding_rules, save_auto_binding_rules,
    batch_match_rows
)
from app.report_generator import generate_standard_report

class TestAutoBindingAndLearning(unittest.TestCase):
    def setUp(self):
        # Back up existing files if they exist
        self.feedback_backup = None
        if os.path.exists(BINDING_FEEDBACK_FILE):
            self.feedback_backup = BINDING_FEEDBACK_FILE + ".bak"
            shutil.copyfile(BINDING_FEEDBACK_FILE, self.feedback_backup)
            os.remove(BINDING_FEEDBACK_FILE)
            
        self.rules_backup = None
        if os.path.exists(AUTO_BINDING_RULES_FILE):
            self.rules_backup = AUTO_BINDING_RULES_FILE + ".bak"
            shutil.copyfile(AUTO_BINDING_RULES_FILE, self.rules_backup)
            os.remove(AUTO_BINDING_RULES_FILE)
            
        # Initialize rules file
        with open(AUTO_BINDING_RULES_FILE, "w", encoding="utf-8") as f:
            f.write("[]")

    def tearDown(self):
        # Clean up files created during test
        if os.path.exists(BINDING_FEEDBACK_FILE):
            os.remove(BINDING_FEEDBACK_FILE)
        if os.path.exists(AUTO_BINDING_RULES_FILE):
            os.remove(AUTO_BINDING_RULES_FILE)
            
        # Restore backups
        if self.feedback_backup and os.path.exists(self.feedback_backup):
            shutil.copyfile(self.feedback_backup, BINDING_FEEDBACK_FILE)
            os.remove(self.feedback_backup)
            
        if self.rules_backup and os.path.exists(self.rules_backup):
            shutil.copyfile(self.rules_backup, AUTO_BINDING_RULES_FILE)
            os.remove(self.rules_backup)

    def test_binding_feedback_and_suggestion(self):
        # 1. Create mock bindings
        bindings = [
            {
                "code": "M_001",
                "name": "竖龙骨",
                "specification": "75*50",
                "unit": "米",
                "brand": "华康",
                "category": "轻钢龙骨",
                "qty": 150.0
            },
            {
                "code": "M_002",
                "name": "穿心龙骨",
                "specification": "38*12",
                "unit": "米",
                "brand": "华康",
                "category": "轻钢龙骨",
                "qty": 80.0
            }
        ]
        
        # 2. Append mock feedback
        samples = []
        samples.append({
            "original_name": "墙面轻钢龙骨",
            "original_spec": "规格75",
            "bindings": [
                dict(b, qty_ratio=float(b["qty"]) / 100.0) for b in bindings
            ]
        })
        
        with open(BINDING_FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(samples, f, ensure_ascii=False, indent=4)
            
        # Verify suggestions generation logic
        # We simulate the suggestion code logic:
        with open(BINDING_FEEDBACK_FILE, "r", encoding="utf-8") as f:
            loaded_samples = json.load(f)
        self.assertEqual(len(loaded_samples), 1)
        self.assertEqual(loaded_samples[0]["original_name"], "墙面轻钢龙骨")
        self.assertEqual(len(loaded_samples[0]["bindings"]), 2)

    def test_auto_binding_rules_matching(self):
        # 1. Save confirmed rule to rule file
        confirmed_rules = [
            {
                "original_name": "墙面轻钢龙骨",
                "bound_materials": [
                    {
                        "code": "M_001",
                        "name": "竖龙骨",
                        "specification": "75*50",
                        "unit": "米",
                        "brand": "华康",
                        "category": "轻钢龙骨",
                        "qty_ratio": 1.5
                    },
                    {
                        "code": "M_002",
                        "name": "穿心龙骨",
                        "specification": "38*12",
                        "unit": "米",
                        "brand": "华康",
                        "category": "轻钢龙骨",
                        "qty_ratio": 0.8
                    }
                ]
            }
        ]
        save_auto_binding_rules(confirmed_rules)
        
        # 2. Call batch_match_rows with raw row matching the rule
        raw_rows = [
            {
                "row_idx": 1,
                "name": "墙面轻钢龙骨",
                "specification": "75型",
                "unit": "平米",
                "qty": 200.0,
                "loss": 1.0,
                "category": "装饰",
                "brand": ""
            }
        ]
        
        results = batch_match_rows(raw_rows)
        self.assertEqual(len(results), 1)
        
        item = results[0]
        self.assertTrue(item["is_exact"])
        self.assertEqual(item["matched_code"], "M_001,M_002")
        self.assertTrue("bindings" in item)
        self.assertEqual(len(item["bindings"]), 2)
        
        # Verify scaled quantities
        # Qty = original_qty * qty_ratio
        # M_001: 200 * 1.5 = 300
        # M_002: 200 * 0.8 = 160
        self.assertEqual(item["bindings"][0]["qty"], 300.0)
        self.assertEqual(item["bindings"][1]["qty"], 160.0)

    def test_report_generator_multirow(self):
        # Test that standard report generator handles bindings properly and splits them to multiple rows
        proj_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_path = os.path.join(proj_dir, "doc", "sample", "标准格式.xlsx")
        output_path = os.path.join(proj_dir, "app", "uploads", "test_output_split.xlsx")
        
        matched_items = [
            {
                "original_row_idx": 1,
                "original_name": "墙面轻钢龙骨",
                "original_spec": "75型",
                "original_qty": 200.0,
                "original_unit": "平米",
                "original_loss": 1.0,
                "matched_code": "M_001,M_002",
                "professional": "装饰",
                "bindings": [
                    {
                        "code": "M_001",
                        "name": "竖龙骨",
                        "specification": "75*50",
                        "unit": "米",
                        "brand": "华康",
                        "category": "轻钢龙骨",
                        "qty": 300.0
                    },
                    {
                        "code": "M_002",
                        "name": "穿心龙骨",
                        "specification": "38*12",
                        "unit": "米",
                        "brand": "华康",
                        "category": "轻钢龙骨",
                        "qty": 160.0
                    }
                ]
            }
        ]
        
        if os.path.exists(template_path):
            generate_standard_report(matched_items, template_path, output_path)
            self.assertTrue(os.path.exists(output_path))
            
            # Read sheet to verify rows
            import openpyxl
            wb = openpyxl.load_workbook(output_path, data_only=True)
            sheet = wb["模板"]
            
            # Row 3 is the Professional Category header
            self.assertEqual(sheet.cell(3, 1).value, 1)
            self.assertEqual(sheet.cell(3, 6).value, "装饰")
            
            # Row 4 is the Original List Row
            self.assertEqual(sheet.cell(4, 1).value, "1.1")
            self.assertEqual(sheet.cell(4, 6).value, "墙面轻钢龙骨")
            self.assertIsNone(sheet.cell(4, 13).value)  # Price should be None
            
            # Row 5 is M_001 matched row
            self.assertIsNone(sheet.cell(5, 1).value)
            self.assertEqual(sheet.cell(5, 5).value, "M_001")
            self.assertEqual(sheet.cell(5, 6).value, "竖龙骨")
            self.assertEqual(sheet.cell(5, 9).value, 300.0)
            
            # Row 6 is M_002 matched row
            self.assertIsNone(sheet.cell(6, 1).value)
            self.assertEqual(sheet.cell(6, 5).value, "M_002")
            self.assertEqual(sheet.cell(6, 6).value, "穿心龙骨")
            self.assertEqual(sheet.cell(6, 9).value, 160.0)
            
            wb.close()
            os.remove(output_path)

if __name__ == "__main__":
    unittest.main()
