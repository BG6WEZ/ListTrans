# -*- coding: utf-8 -*-
"""V018-M0 R01 保护与契约回归测试。

覆盖：
1. 标准编码直达（精确码、未知码、项目码/位号隔离、命名空间重叠、硬约束不绕过）
2. 产品型号与位号/箱号区分、SS316L 误抽修复、领域路由与置信度
3. 多字段结构化召回（同名不同规格、DN/外径分离、材质冲突、未知/低置信域跨域召回）
4. 默认开关关闭复现旧路径、db_revision 缓存失效与独立副本契约
"""
import json
import os
import sqlite3
import unittest

from app import db_manager, matcher, structured_recall
from app.domain_extractors import ConfigDrivenExtractor
from app.domain_router import route_domain, route_domain_with_confidence
from app.preprocessor import extract_device_tag, extract_model
from app.query_preprocessor import extract_query_params, prepare_query


def _insert_material(code, name, spec, category="通用", unit="个", brand="", parsed_params=None):
    with db_manager.get_db_cursor() as cur:
        pp = json.dumps(parsed_params, ensure_ascii=False) if isinstance(parsed_params, dict) else (parsed_params or "")
        cur.execute(
            """INSERT OR REPLACE INTO materials
               (code, category, name, specification, unit, brand, description, search_tokens, parsed_params)
               VALUES (?, ?, ?, ?, ?, ?, '', ?, ?)""",
            (code, category, name, spec, unit, brand, f"{name} {spec}", pp),
        )
        db_manager._refresh_fts(cur.connection, codes=[code])
        db_manager._commit_materials_write(cur.connection)
    db_manager._query_by_code.cache_clear()
    db_manager._keyword_rows.cache_clear()


class TestV018M0StructuredRecall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_manager.init_db()

    def setUp(self):
        self._prev_flag = os.environ.get("LISTTRANS_V018_M0_RECALL")
        os.environ.pop("LISTTRANS_V018_M0_RECALL", None)
        structured_recall.invalidate_structured_index()

    def tearDown(self):
        if self._prev_flag is None:
            os.environ.pop("LISTTRANS_V018_M0_RECALL", None)
        else:
            os.environ["LISTTRANS_V018_M0_RECALL"] = self._prev_flag
        structured_recall.invalidate_structured_index()

    # ── 1. 默认开关与旧路径复现 ──────────────────────────────────────────────
    def test_default_toggle_is_off_and_preserves_legacy_path(self):
        self.assertFalse(structured_recall.DEFAULT_V018_M0_ENABLED)
        self.assertFalse(structured_recall.is_v018_m0_recall_enabled())

        _insert_material(
            "88.01.02.0001",
            "特种精密隔离阀",
            "316L不锈钢 DN25 焊接",
            category="医用气体",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢316L", "dn_val": 25, "管径": "DN25"}},
        )
        qi = prepare_query("完全不相关名称", "标准编码：88.01.02.0001", "医用气体")
        # Default OFF: exact_code must NOT fire
        pool_off = matcher.hybrid_recall(qi, None)
        self.assertFalse(any("exact_code" in (c.get("recall_sources") or []) for c in pool_off))

        # Explicit ON: exact_code fires and lands first
        qi_on = dict(qi, recall_options={"v018_m0_enabled": True})
        pool_on = matcher.hybrid_recall(qi_on, None)
        self.assertTrue(pool_on)
        self.assertEqual(pool_on[0]["code"], "88.01.02.0001")
        self.assertIn("exact_code", pool_on[0]["recall_sources"])

    # ── 2. 标准编码直达：精确码、未知码、项目码/位号隔离、命名空间重叠 ─────────
    def test_identifier_classification_namespaces_and_overlaps(self):
        qi = {
            "name": "组合式空调机组 AHU-J-01",
            "specification": "项目编码:030408001001 标准编码:88.01.02.0002 型号:RAU-Q-D301",
            "original_code": "GT-0099",
            "project_code": "88.01.02.0003",  # standard-like format placed inside project_code
            "standard_code": "030408001002",  # BOQ 12-digit project code placed inside standard_code
            "professional": "暖通",
        }
        parsed = extract_query_params(qi["name"], qi["specification"], qi["professional"])
        ident = structured_recall.classify_query_identifiers(qi, parsed)

        std_codes = [c for c, _ in ident["standard_codes"]]
        prj_codes = [c for c, _ in ident["project_codes"]]
        self.assertIn("88.01.02.0002", std_codes)
        self.assertNotIn("030408001001", std_codes)
        self.assertNotIn("030408001002", std_codes)
        self.assertNotIn("GT-0099", std_codes)
        self.assertNotIn("AHU-J-01", std_codes)
        self.assertIn("030408001001", prj_codes)
        self.assertIn("030408001002", prj_codes)
        self.assertIn("GT-0099", prj_codes)
        self.assertIn("AHU-J-01", ident["device_tags"])
        self.assertIn("RAU-Q-D301", ident["product_models"])
        self.assertNotIn("AHU-J-01", ident["product_models"])
        # Namespace overlaps explicitly recorded
        overlap_reasons = {o["reason"] for o in ident["namespace_overlaps"]}
        self.assertIn("project_code_in_standard_field", overlap_reasons)
        self.assertIn("standard_format_in_project_code", overlap_reasons)

    def test_unknown_standard_code_does_not_fabricate_candidate(self):
        qi = prepare_query("不锈钢球阀", "标准编码：99.98.97.9654 DN20", "医用气体")
        hits, meta = structured_recall.recall_by_standard_code(qi, qi["parsed_spec"])
        self.assertEqual(hits, [])
        self.assertIn("99.98.97.9654", meta["unknown_codes"])

    def test_exact_code_candidate_still_enforces_hard_constraints(self):
        # 1) DN100 vs DN25 conflict: exact_code hit must be capped (max_score <= 0.15) and never auto-accepted
        _insert_material(
            "88.02.03.0010",
            "不锈钢球阀",
            "材质:不锈钢304 管径:DN100",
            category="阀门",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢304", "管径": "DN100", "dn_val": 100}},
        )
        qi = prepare_query("不锈钢球阀", "1.名称：不锈钢球阀\n2.标准编码：88.02.03.0010\n3.材质：316L不锈钢\n4.管径：DN25", "给排水")
        qi["recall_options"] = {"v018_m0_enabled": True}
        res = matcher.match_item(qi, diagnostic_targets=["88.02.03.0010"])
        cands = {c["code"]: c for c in res["diagnostics"]["candidates"]}
        self.assertIn("88.02.03.0010", cands)
        target = cands["88.02.03.0010"]
        self.assertIn("exact_code", target["recall_sources"])
        self.assertLessEqual(target["max_score"], 0.15)
        self.assertLessEqual(target["score"], 15.0)
        self.assertFalse(res.get("is_exact"), "DN-conflicting exact_code hit must never auto-accept")

        # 2) Brand conflict: exact_code hit must be hard_blocked=True, max_score=0.0, is_exact=False
        _insert_material(
            "88.02.03.0011",
            "不锈钢球阀",
            "材质:不锈钢316L 管径:DN25",
            category="阀门",
            brand="施耐德",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢316L", "管径": "DN25", "dn_val": 25}},
        )
        qi_b = prepare_query("不锈钢球阀", "1.名称：不锈钢球阀\n2.标准编码：88.02.03.0011\n3.管径：DN25", "给排水", brand="西门子")
        qi_b["recall_options"] = {"v018_m0_enabled": True}
        res_b = matcher.match_item(qi_b, diagnostic_targets=["88.02.03.0011"])
        cands_b = {c["code"]: c for c in res_b["diagnostics"]["candidates"]}
        self.assertIn("88.02.03.0011", cands_b)
        target_b = cands_b["88.02.03.0011"]
        self.assertIn("exact_code", target_b["recall_sources"])
        self.assertTrue(target_b["hard_blocked"])
        self.assertEqual(target_b["max_score"], 0.0)
        self.assertFalse(res_b.get("is_exact"))

    # ── 3. SS316L 误抽修复与领域路由/置信度 ──────────────────────────────────
    def test_ss316l_not_misextracted_as_pressure_or_model(self):
        spec = '1.类型:不锈钢总控球阀\n2.规格、压力等级:SS316L材质，接口形式：1/2"卡套，适用高纯气体BA级'
        parsed = extract_query_params("不锈钢总空球阀", spec, "医用气体")
        attrs = parsed["attrs"]
        self.assertEqual(attrs.get("材质"), "不锈钢316L")
        self.assertNotIn("压力", attrs)
        self.assertNotIn("型号", attrs)
        self.assertEqual(parsed.get("model"), "")
        self.assertEqual(extract_model(spec), "")

    def test_domain_routing_fixes_and_confidence_tiers(self):
        # Tier 1/2 high-confidence fixes from V017-M1 backlog
        self.assertEqual(
            route_domain("医用气体", "UPVC管", "1.名称:UPVC管\n2.介质：其他空调（风管机）冷凝水管\n3.规格:De32", "医用气体"),
            "pipe_plastic",
        )
        self.assertEqual(
            route_domain("自控", "控制线", "1.名称：控制线\n2.规格：WDZ-RYYP-4*0.75mm²\n3.工作内容：穿引线、扫管", "自控"),
            "cable_control",
        )
        self.assertEqual(
            route_domain("弱电", "交换机", "1.名称：交换机\n2.规格：8端口\n3.工作内容：接线、本体安装", "弱电"),
            "weak_current",
        )
        self.assertEqual(
            route_domain("强电", "LED防爆灯", "1.名称：LED防爆灯\n2.规格：25-40w\n3.工作内容：接线、安装", "强电"),
            "lighting",
        )
        self.assertEqual(
            route_domain("装饰", "不锈钢包边（防火门）", "1.面层：1.2mm不锈钢板", "装饰"),
            "decoration",
        )
        self.assertEqual(
            route_domain("设备", "洁净工作台", "型号：CJV1000-Y", "设备"),
            "purification",
        )

        # Confidence tiers
        dom1, conf1, _ = route_domain_with_confidence("暖通", "常开防火阀", "70℃", "暖通")
        self.assertEqual(dom1, "damper")
        self.assertGreaterEqual(conf1, 0.85)

        dom_unk, conf_unk, tier_unk = route_domain_with_confidence("", "未知特制构件XYZ", "规格定制", "")
        self.assertEqual(dom_unk, "generic")
        self.assertLess(conf_unk, structured_recall.LOW_CONFIDENCE_DOMAIN_THRESHOLD)
        self.assertEqual(tier_unk, "fallback_generic")

    # ── 4. 多字段结构化召回：同名不同规格、DN/OD分离、未知域跨域 ──────────────
    def test_structured_recall_distinguishes_dn_od_and_material_and_allows_unknown_domain(self):
        _insert_material(
            "88.03.01.0001",
            "手动精密阀",
            "316L不锈钢 DN15 焊接",
            category="医用气体",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢316L", "管径": "DN15", "dn_val": 15}},
        )
        _insert_material(
            "88.03.01.0002",
            "手动精密阀",
            "304不锈钢 DN15 焊接",
            category="医用气体",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢304", "管径": "DN15", "dn_val": 15}},
        )
        _insert_material(
            "88.03.01.0003",
            "塑料冷凝管",
            "UPVC De32 粘接",
            category="给排水",
            parsed_params={"domain": "pipe_plastic", "attrs": {"材质": "UPVC", "外径": 32.0, "de_val": 32.0}},
        )

        # Query with unknown domain ("generic") + 316L + DN15 -> must allow cross-domain and find 88.03.01.0001, NOT 304
        qi_unk = prepare_query("特种构件", "材质:SS316L DN15", "")
        hits, meta = structured_recall.recall_by_structured_fields(qi_unk, qi_unk["parsed_spec"])
        self.assertTrue(meta["cross_domain_allowed"])
        hit_codes = [h["code"] for h in hits]
        self.assertIn("88.03.01.0001", hit_codes)
        self.assertNotIn("88.03.01.0002", hit_codes)

        # Query with explicit De32 (OD) -> recalls OD=32 material, does not confuse with DN
        qi_od = prepare_query("未知管件", "UPVC De32", "")
        hits_od, meta_od = structured_recall.recall_by_structured_fields(qi_od, qi_od["parsed_spec"])
        self.assertEqual(meta_od["query_features"]["dn"], None)
        self.assertEqual(meta_od["query_features"]["od"], 32)
        self.assertIn("88.03.01.0003", [h["code"] for h in hits_od])

    # ── 5. db_revision 自动失效与无共享可变对象契约 ───────────────────────────
    def test_structured_index_tracks_db_revision_and_returns_fresh_copies(self):
        snap1 = structured_recall.get_structured_index()
        rev1 = snap1.db_revision
        self.assertNotIn("88.09.09.0001", snap1.materials)

        _insert_material(
            "88.09.09.0001",
            "新版本校验阀",
            "316L不锈钢 DN40 型号:RAU-Q-D301",
            category="阀门",
            parsed_params={"domain": "valve_water", "model": "RAU-Q-D301", "attrs": {"材质": "不锈钢316L", "dn_val": 40}},
        )
        snap2 = structured_recall.get_structured_index()
        self.assertGreater(snap2.db_revision, rev1)
        self.assertIn("88.09.09.0001", snap2.materials)

        # Verify fresh shallow copies (mutating returned dict must not pollute snapshot)
        qi = prepare_query("新版本校验阀", "型号:RAU-Q-D301", "给排水")
        h1, _ = structured_recall.recall_by_structured_fields(qi, qi["parsed_spec"])
        self.assertTrue(h1)
        h1[0]["name"] = "MUTATED"
        h2, _ = structured_recall.recall_by_structured_fields(qi, qi["parsed_spec"])
        self.assertNotEqual(h2[0]["name"], "MUTATED")


if __name__ == "__main__":
    unittest.main()
