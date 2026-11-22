# -*- coding: utf-8 -*-
"""V017-M1 / R01 专项回归：类型化字段、白名单、DN/De、牌号、位号、统计去虚高。

覆盖 M1-C01~C04 的验收矩阵核心断言，与既有测试互补。
"""
import json
import os
import unittest


class TestM1TypedFieldsAndWhitelist(unittest.TestCase):
    """M1-C01：统一类型化字段结构与字段白名单。"""

    def test_extract_params_outputs_typed_attrs_and_schema_v3(self):
        from app.preprocessor import extract_params
        r = extract_params("无缝钢管 DN100 304不锈钢 壁厚4.5mm", "无缝钢管")
        self.assertEqual(r.get("schema_version"), 3)
        self.assertIn("typed_attrs", r)
        self.assertIn("unwhitelisted_attrs", r)
        self.assertIn("domain", r)
        # typed_attrs 每条含必填元信息
        for k, v in r["typed_attrs"].items():
            for field in ("value", "status", "source", "confidence"):
                self.assertIn(field, v, f"typed_attrs[{k}] 缺 {field}")

    def test_query_outputs_typed_attrs(self):
        from app.query_preprocessor import extract_query_params
        q = extract_query_params("无缝钢管", "DN100 304不锈钢 壁厚4.5mm", "给排水")
        self.assertEqual(q.get("schema_version"), 3)
        self.assertIn("typed_attrs", q)
        self.assertIn("unwhitelisted_attrs", q)

    def test_noise_key_isolated_to_unwhitelisted(self):
        from app.preprocessor import extract_params
        # 施工噪声键应被隔离，不进入 attrs
        r = extract_params("风管 材质:镀锌, 现场焊接, 打胶平整", "风管")
        self.assertNotIn("现场焊接", r["attrs"])
        self.assertNotIn("打胶平整", r["attrs"])

    def test_whitelist_key_kept(self):
        from app.typed_fields import is_whitelisted_key
        self.assertTrue(is_whitelisted_key("管径"))
        self.assertTrue(is_whitelisted_key("材质"))
        self.assertTrue(is_whitelisted_key("风量"))
        self.assertTrue(is_whitelisted_key("管径_min"))
        self.assertFalse(is_whitelisted_key("施工说明"))


class TestM1DNDeSeparation(unittest.TestCase):
    """M1-C02：DN 与 De/Φ/外径彻底分离。"""

    def test_de_goes_to_outer_diameter_not_dn(self):
        from app.preprocessor import extract_params
        r = extract_params("PPR管 De32 壁厚2mm", "PPR管")
        self.assertNotIn("dn_val", r["attrs"])
        self.assertNotIn("管径", r["attrs"])
        self.assertIn("外径", r["attrs"])
        self.assertEqual(r["attrs"]["外径"], 32.0)

    def test_dn_goes_to_nominal_diameter(self):
        from app.preprocessor import extract_params
        r = extract_params("PPR管 DN32 壁厚2mm", "PPR管")
        self.assertIn("dn_val", r["attrs"])
        self.assertEqual(r["attrs"]["dn_val"], 32)

    def test_phi_goes_to_outer_diameter(self):
        from app.preprocessor import extract_params
        r = extract_params("不锈钢管 Φ50 壁厚3mm", "不锈钢管")
        self.assertNotIn("dn_val", r["attrs"])
        self.assertIn("外径", r["attrs"])
        self.assertEqual(r["attrs"]["外径"], 50.0)

    def test_dn_and_od_cooccurrence_keep_both(self):
        # A-M1-02 回归：DN 与外径共现（DN50(φ57*3.5)）时，DN 不再被误判为外径，
        # 管径与外径应同时保留、不再串值丢弃。
        from app.domain_extractors import ConfigDrivenExtractor
        e = ConfigDrivenExtractor("pipe_steel")
        r = e.extract("规格:DN50(φ57*3.5) 材质:304")
        self.assertIn("管径", r)
        self.assertEqual(r["管径"], 50.0)
        self.assertIn("外径", r)
        self.assertEqual(r["外径"], 57.0)

    def test_dn_cooccurrence_valve_water_no_false_od(self):
        # A-M1-02 回归：valve_water 域 DN 共现时不得误写 外径=50.0 / de_val=50.0。
        from app.domain_extractors import ConfigDrivenExtractor
        e = ConfigDrivenExtractor("valve_water")
        r = e.extract("规格:DN50(φ57*3.5) 材质:304")
        self.assertIn("管径", r)
        self.assertEqual(r["管径"], 50.0)
        self.assertNotIn("de_val", r)
        self.assertNotIn("外径", r)


class TestM1MaterialGrade(unittest.TestCase):
    """M1-C02：材质牌号 304 vs 316L 严格区分，泛称不锈钢 unknown 宽容。"""

    def test_grade_normalization(self):
        from app.matcher import normalize_material_grade
        self.assertEqual(normalize_material_grade("304"), "不锈钢304")
        self.assertEqual(normalize_material_grade("316l"), "不锈钢316L")
        self.assertEqual(normalize_material_grade("不锈钢"), "不锈钢")

    def test_304_vs_316l_conflict(self):
        from app.matcher import normalize_material_grade, KNOWN_MATERIAL_GRADES
        q = normalize_material_grade("304")
        c = normalize_material_grade("316l")
        self.assertIn(q, KNOWN_MATERIAL_GRADES)
        self.assertIn(c, KNOWN_MATERIAL_GRADES)
        self.assertNotEqual(q, c)

    def test_generic_stainless_not_graded(self):
        from app.matcher import normalize_material_grade, KNOWN_MATERIAL_GRADES
        self.assertNotIn(normalize_material_grade("不锈钢"), KNOWN_MATERIAL_GRADES)


class TestM1DeviceTagSeparation(unittest.TestCase):
    """M1-C02：产品型号与工程设备位号/箱号分离。"""

    def test_tag_detection(self):
        from app.preprocessor import is_device_tag
        self.assertTrue(is_device_tag("AHU-J-01"))
        self.assertTrue(is_device_tag("PAU-D301"))
        self.assertTrue(is_device_tag("AP-1"))
        self.assertTrue(is_device_tag("AL-2"))

    def test_model_not_tag(self):
        from app.preprocessor import is_device_tag
        self.assertFalse(is_device_tag("RAU-Q-D301"))
        self.assertFalse(is_device_tag("DN100"))

    def test_extract_model_skips_tag(self):
        from app.preprocessor import extract_model, extract_device_tag
        self.assertEqual(extract_model("组合式空调机组 AHU-J-01 风量2000"), "")
        self.assertEqual(extract_device_tag("组合式空调机组 AHU-J-01 风量2000"), "AHU-J-01")
        self.assertEqual(extract_model("回风机组 RAU-Q-D301"), "RAU-Q-D301")


class TestM1SchemaStats(unittest.TestCase):
    """M1-C04：填充率去虚高（generic 无 Must 不记 100%）。"""

    def test_generic_no_must_rate_none(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from unittest import mock
        import app.db_manager as dbm

        # 用临时内存库 + monkeypatch get_db_connection，验证去虚高逻辑（不触碰业务库）
        db_path = str(Path(tempfile.mkdtemp(prefix="m1_stats_")) / "materials.db")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""CREATE TABLE materials (
            code TEXT PRIMARY KEY, category TEXT, name TEXT, specification TEXT,
            unit TEXT, brand TEXT, description TEXT, search_tokens TEXT,
            parsed_params TEXT, parsed_schema_version INTEGER)""")
        cur.execute("INSERT INTO materials VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("G1", "其他", "杂项", "无规格", "", "", "", "", "", 2))
        conn.commit()
        conn.close()

        def fake_conn():
            import sqlite3 as s
            c = s.connect(db_path)
            c.row_factory = s.Row
            return c

        with mock.patch.object(dbm, "get_db_connection", side_effect=fake_conn):
            stats = dbm.get_db_schema_stats()

        for dom, info in stats["domains"].items():
            if not info.get("has_must"):
                self.assertIsNone(info["must_fill_rate"], f"{dom} 无 must 域应 must_fill_rate=None")

    def test_whitelist_filtered_count_merges_unwhitelisted(self):
        # A-M1-04 回归：whitelist_filtered_count 需合并 attrs 残留非白名单键
        # 与顶层 unwhitelisted_attrs，避免 v3 库恒为 0。
        import sqlite3
        import tempfile
        from pathlib import Path
        from unittest import mock
        import app.db_manager as dbm

        db_path = str(Path(tempfile.mkdtemp(prefix="m1_stats_wl_")) / "materials.db")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""CREATE TABLE materials (
            code TEXT PRIMARY KEY, category TEXT, name TEXT, specification TEXT,
            unit TEXT, brand TEXT, description TEXT, search_tokens TEXT,
            parsed_params TEXT, parsed_schema_version INTEGER)""")
        # 顶层 unwhitelisted_attrs 含 2 个噪声键，attrs 含 1 个残留非白名单键
        parsed = json.dumps({
            "attrs": {"材质": "不锈钢", "施工说明": "x"},
            "unwhitelisted_attrs": {"现场焊接": "x", "打胶": "x"},
            "schema_version": 3,
        }, ensure_ascii=False)
        cur.execute("INSERT INTO materials VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("G2", "给水管道", "不锈钢管", "DN50", "", "", "", "", parsed, 3))
        conn.commit()
        conn.close()

        def fake_conn():
            import sqlite3 as s
            c = s.connect(db_path)
            c.row_factory = s.Row
            return c

        with mock.patch.object(dbm, "get_db_connection", side_effect=fake_conn):
            stats = dbm.get_db_schema_stats()

        doms = stats["domains"]
        total_filtered = sum(d.get("whitelist_filtered_count", 0) for d in doms.values())
        # attrs 残留非白名单键 1 + 顶层 unwhitelisted_attrs 2 = 3
        self.assertEqual(total_filtered, 3, f"whitelist_filtered_count 应为 3，实为 {total_filtered}")

    def test_flags_fill_rate_present(self):
        # A-M1-04 回归：有 flags 字段的域应返回 flags_fill_rate（此前已计算未写入 entry）。
        import sqlite3
        import tempfile
        from pathlib import Path
        from unittest import mock
        import app.db_manager as dbm
        from app.domain_router import route_domain
        from app.domain_extractors import domain_schema

        db_path = str(Path(tempfile.mkdtemp(prefix="m1_stats_flag_")) / "materials.db")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""CREATE TABLE materials (
            code TEXT PRIMARY KEY, category TEXT, name TEXT, specification TEXT,
            unit TEXT, brand TEXT, description TEXT, search_tokens TEXT,
            parsed_params TEXT, parsed_schema_version INTEGER)""")
        # 选一个有 flags 的域：fan（含 防爆 等 flags）
        has_flags_domain = None
        for d, cfg in domain_schema.items():
            if isinstance(cfg, dict) and cfg.get("flags"):
                has_flags_domain = d
                break
        if has_flags_domain is None:
            self.skipTest("domain_schema 无 flags 域，跳过")
        parsed = json.dumps({"attrs": {"防爆": True}, "schema_version": 3}, ensure_ascii=False)
        cur.execute("INSERT INTO materials VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("G3", "风机", "排风机", "防爆", "", "", "", "", parsed, 3))
        conn.commit()
        conn.close()

        def fake_conn():
            import sqlite3 as s
            c = s.connect(db_path)
            c.row_factory = s.Row
            return c

        with mock.patch.object(dbm, "get_db_connection", side_effect=fake_conn):
            stats = dbm.get_db_schema_stats()

        # 该行 route_domain 到的域若有 flags 配置，entry 应含 flags_fill_rate 键
        from app.domain_router import route_domain as rd
        routed = rd("风机", "排风机", "防爆")
        entry = stats["domains"].get(routed)
        if entry is not None:
            self.assertIn("flags_fill_rate", entry, f"域 {routed} 应含 flags_fill_rate")


if __name__ == "__main__":
    unittest.main()
