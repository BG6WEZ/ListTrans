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
from app.db_manager import init_db
from app.auth_manager import init_auth_db, get_auth_connection, get_password_hash, get_user_by_username, issue_token_for_user

# A-M0-05: 合成物料种子（与规则中的 M_001/M_002 对应），仅写入隔离 MATERIALS_DB_PATH
_SYNTHETIC_MATERIALS = [
    ("M_001", "轻钢龙骨", "竖龙骨", "75*50", "米", "华康", "竖龙骨 75*50", "竖龙骨 75 50 轻钢龙骨"),
    ("M_002", "轻钢龙骨", "穿心龙骨", "38*12", "米", "华康", "穿心龙骨 38*12", "穿心龙骨 38 12 轻钢龙骨"),
]

class TestAutoBindingAndLearning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A-M0-05: batch_match_rows 的 first pass 会对每行无条件执行 match_item，其
        # 召回/补全依赖 materials 与 materials_fts 表。经生产入口 init_db() 初始化
        # 隔离 MATERIALS_DB_PATH（已存在且非空时仅自检迁移；空库时建真实 Schema），
        # 随后写入合成物料种子；不依赖业务库已有数据、测试顺序或其他模块副作用。
        # unittest.TestCase 不接收 pytest fixture，初始化必须放在运行期钩子而非模块导入期。
        init_db()
        init_auth_db()
        conn = get_auth_connection()
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch) VALUES (?, ?, ?, 'admin', 1, 0, ?)",
            ("ab_admin", get_password_hash("AbAdmin@12345"), "ab_admin", "ep-ab-admin"),
        )
        conn.commit()
        conn.close()
        cls.admin_actor = get_user_by_username("ab_admin")
        issue_token_for_user(cls.admin_actor)
        cls._seed_synthetic_materials()

    @classmethod
    def tearDownClass(cls):
        cls._remove_synthetic_materials()

    @staticmethod
    def _materials_db_path():
        db_path = os.environ.get("MATERIALS_DB_PATH")
        if not db_path:
            raise unittest.SkipTest(
                "MATERIALS_DB_PATH 未注入：必须在 tests/conftest.py 隔离环境下运行，禁止写业务库。"
            )
        return db_path

    @classmethod
    def _fts_available(cls, cur):
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='materials_fts'"
        )
        return cur.fetchone() is not None

    @classmethod
    def _seed_synthetic_materials(cls):
        import sqlite3
        conn = sqlite3.connect(cls._materials_db_path())
        try:
            cur = conn.cursor()
            for row in _SYNTHETIC_MATERIALS:
                cur.execute(
                    "INSERT OR REPLACE INTO materials "
                    "(code, category, name, specification, unit, brand, description, search_tokens) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
                if cls._fts_available(cur):
                    cur.execute(
                        "INSERT OR REPLACE INTO materials_fts "
                        "(code, category, name, specification, brand, description) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (row[0], row[1], row[2], row[3], row[5], row[6]),
                    )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def _remove_synthetic_materials(cls):
        import sqlite3
        conn = sqlite3.connect(cls._materials_db_path())
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM materials WHERE code IN ('M_001', 'M_002')")
            if cls._fts_available(cur):
                cur.execute("DELETE FROM materials_fts WHERE code IN ('M_001', 'M_002')")
            conn.commit()
        finally:
            conn.close()

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
        with open(AUTO_BINDING_RULES_FILE, "w", encoding="utf-8") as _f:
            json.dump(confirmed_rules, _f, ensure_ascii=False, indent=4)
        
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
        self.assertEqual(item["decision_status"], "recommended")
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
        # A-M0-02: 输出必须落在 conftest 注入的隔离 UPLOAD_DIR，禁止硬编码 app/uploads
        upload_dir = os.environ.get("UPLOAD_DIR")
        self.assertTrue(
            upload_dir,
            "UPLOAD_DIR 未注入：必须在 tests/conftest.py 隔离环境下运行，禁止写业务 uploads。",
        )
        os.makedirs(upload_dir, exist_ok=True)
        output_path = os.path.join(upload_dir, "test_output_split.xlsx")

        # ASSET-M1-01：业务模板（.gitignore 忽略，本机可能缺失）缺失时，用可复现
        # 生成器在隔离目录内生成脱敏模板，真实执行导出链路；不再以缺模板跳过必做验收。
        generated_template = None
        if not os.path.exists(template_path):
            from fixtures.gen_standard_template import generate_standard_template
            generated_template = os.path.join(upload_dir, "gen_standard_template.xlsx")
            generate_standard_template(generated_template, with_history_rows=True)
            template_path = generated_template
        
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

        wb = None
        try:
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

            # ASSET-M1-01：原文/来源字段贯通断言（V015-M1 F19 导出契约）
            # 原文行：原始单位(列H)、损耗(列J)、来源备注(列N)、专业(列O/P)
            self.assertEqual(sheet.cell(4, 8).value, "平米")
            self.assertEqual(sheet.cell(4, 10).value, 1.0)
            self.assertEqual(sheet.cell(4, 14).value, "标段一")
            self.assertEqual(sheet.cell(4, 15).value, "装饰")
            # 绑定行：品牌(列K)、专业(列O)
            self.assertEqual(sheet.cell(5, 11).value, "华康")
            self.assertEqual(sheet.cell(5, 14).value, "标段一")
            self.assertEqual(sheet.cell(5, 15).value, "装饰")

            # ASSET-M1-01：历史价格继承断言（生成器模板行3含 HIST_001/12.5，
            # 行4含 HIST_002/8.0；导出后行3起被重写，历史价格行应被清除）
            self.assertNotEqual(sheet.cell(5, 5).value, "HIST_001")
            self.assertNotEqual(sheet.cell(6, 5).value, "HIST_002")
            self.assertEqual(sheet.max_row, 6)
        finally:
            # A-M0-02: 无论断言成败都关闭工作簿并清理隔离输出，不依赖测试末尾的普通删除
            if wb is not None:
                wb.close()
            if os.path.exists(output_path):
                os.remove(output_path)
            if generated_template and os.path.exists(generated_template):
                os.remove(generated_template)

if __name__ == "__main__":
    unittest.main()
