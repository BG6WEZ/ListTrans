# -*- coding: utf-8 -*-
"""V018-M1 R01 名称别名治理与稀疏通道保护测试。

覆盖：
1. 默认开关关闭（LISTTRANS_V018_M1_RECALL=0）复现 M0 放行路径；冻结 config/alias_dict.json 未改写
2. 同品类过度具体化映射治理（控制线/气体终端/光纤/插座）与跨品类防护（AP 仅限强电、黑名单保持）
3. 名称混入设备位号/箱号/数量前缀清洗
4. 长 AND 拆分、长施工文本、短名与同名异规格召回，避免 q_name[:4] 截断
5. 渠道名、渠道内 rank（channel_ranks）、加入原因（recall_reasons）及候选量预算控制
6. 硬约束不绕过、db_revision 缓存失效与独立副本、M0+M1 组合交互
"""
import json
import os
import unittest

from app import db_manager, matcher, sparse_recall, structured_recall
from app.query_preprocessor import prepare_query


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
    db_manager.search_materials_fts_raw.cache_clear()


class TestV018M1SparseRecall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_manager.init_db()

    def setUp(self):
        self._prev_m0 = os.environ.get("LISTTRANS_V018_M0_RECALL")
        self._prev_m1 = os.environ.get("LISTTRANS_V018_M1_RECALL")
        os.environ.pop("LISTTRANS_V018_M0_RECALL", None)
        os.environ.pop("LISTTRANS_V018_M1_RECALL", None)
        structured_recall.invalidate_structured_index()

    def tearDown(self):
        for k, v in (("LISTTRANS_V018_M0_RECALL", self._prev_m0), ("LISTTRANS_V018_M1_RECALL", self._prev_m1)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        structured_recall.invalidate_structured_index()

    def test_default_toggle_off_reproduces_m0_released_path(self):
        self.assertFalse(sparse_recall.DEFAULT_V018_M1_ENABLED)
        self.assertFalse(sparse_recall.is_v018_m1_recall_enabled())

        _insert_material("89.01.01.0001", "CO2气体终端", "国标系列 铜+塑料压盖底座", category="医用气体")
        qi = prepare_query("医用气体终端", "1.名称：医用气体终端\n2.介质：CO2", "医用气体")
        pool_off = matcher.hybrid_recall(qi, None)
        self.assertFalse(any(
            s.startswith("sparse_") for c in pool_off for s in (c.get("recall_sources") or [])
        ))

        qi_on = dict(qi, recall_options={"v018_m1_enabled": True})
        pool_on = matcher.hybrid_recall(qi_on, None)
        codes_on = {c["code"]: c for c in pool_on}
        self.assertIn("89.01.01.0001", codes_on)
        cand = codes_on["89.01.01.0001"]
        self.assertTrue(any(s.startswith("sparse_") for s in cand["recall_sources"]))
        self.assertTrue(cand.get("channel_ranks"))
        self.assertTrue(cand.get("recall_reasons"))

    def test_over_specific_aliases_and_discipline_scoped_abbreviations(self):
        # 1) 控制线 with WDZC-RYYP 4*0.75 must recall WDZC-RYYP 4*0.75, not get locked to WDZB-RYY 7*1
        _insert_material("89.02.01.0001", "阻燃C级屏蔽软电缆", "WDZC-RYYP 4*0.75", category="弱电")
        qi_c = prepare_query("控制线", "1.名称：控制线\n2.规格：WDZC-RYYP-4*0.75mm²", "弱电")
        cands_c, meta_c = sparse_recall.recall_sparse_candidates(qi_c, qi_c["parsed_spec"])
        self.assertTrue(meta_c["name_info"]["was_over_specific_suppressed"])
        self.assertIn("89.02.01.0001", [c["code"] for c in cands_c])

        # 2) AP abbreviation: expands in 强电, must NOT expand in 医用气体 ("304 AP脱脂不锈钢气管")
        qi_gas = {"original_name": "304 AP脱脂不锈钢气管", "name": "304 AP脱脂不锈钢气管", "professional": "医用气体"}
        ni_gas = sparse_recall.canonicalize_sparse_query_name(qi_gas)
        self.assertNotIn("动力配电箱", ni_gas["canonical_name"])

        qi_elec = {"original_name": "AP 配电箱", "name": "AP 配电箱", "professional": "强电"}
        ni_elec = sparse_recall.canonicalize_sparse_query_name(qi_elec)
        self.assertIn("动力配电箱", ni_elec["canonical_name"])

    def test_device_tag_and_long_construction_text_phrase_split_no_first4_truncation(self):
        # Long construction name + tag prefix where q_name[:4] would fail
        _insert_material("89.03.01.0001", "单管防爆灯", "1200mm 吊装 18W", category="强电")
        _insert_material("89.03.01.0002", "手动截止阀", "铜 DN8 焊接 介质：气体", category="医用气体")
        _insert_material("89.03.01.0003", "组合式空气处理机组", "风量5000", category="暖通")

        # 1) Long construction text
        qi_long = prepare_query("防爆型壁挂式单管LED灯管", "1.规格：18W 1200mm\n2.工作内容：打眼安装", "强电")
        cands_long, _ = sparse_recall.recall_sparse_candidates(qi_long, qi_long["parsed_spec"])
        self.assertIn("89.03.01.0001", [c["code"] for c in cands_long])

        # 2) Long compound name with alias + material/DN
        qi_v = prepare_query("医用铜制维修阀", "1.材质：铜\n2.管径：DN8\n3.连接：焊接", "医用气体")
        cands_v, _ = sparse_recall.recall_sparse_candidates(qi_v, qi_v["parsed_spec"])
        self.assertIn("89.03.01.0002", [c["code"] for c in cands_v])

        # 3) Embedded device tag AHU-J-01 stripped cleanly
        qi_tag = prepare_query("AHU-J-01 组合式空调机组", "风量5000", "暖通")
        ni_tag = sparse_recall.canonicalize_sparse_query_name(qi_tag)
        self.assertNotIn("AHU-J-01", ni_tag["canonical_name"])
        cands_tag, _ = sparse_recall.recall_sparse_candidates(qi_tag, qi_tag["parsed_spec"])
        self.assertIn("89.03.01.0003", [c["code"] for c in cands_tag])

    def test_same_name_different_spec_hard_constraints_and_budget(self):
        _insert_material(
            "89.04.01.0001", "手动球阀", "316L不锈钢 DN15 焊接", category="阀门",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢316L", "管径": "DN15", "dn_val": 15}},
        )
        _insert_material(
            "89.04.01.0002", "手动球阀", "304不锈钢 DN50 焊接", category="阀门",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢304", "管径": "DN50", "dn_val": 50}},
        )
        qi = prepare_query("不锈钢总控球阀", "1.材质：SS316L\n2.管径：DN15", "医用气体")
        qi["recall_options"] = {"v018_m1_enabled": True, "sparse_limit": 20}
        res = matcher.match_item(qi, diagnostic_targets=["89.04.01.0001", "89.04.01.0002"])
        diag_cands = {c["code"]: c for c in res["diagnostics"]["candidates"]}
        self.assertIn("89.04.01.0001", diag_cands)
        # Verify provenance & channel_ranks propagated to match_item output
        self.assertIsInstance(diag_cands["89.04.01.0001"].get("channel_ranks"), dict)
        self.assertTrue(diag_cands["89.04.01.0001"]["channel_ranks"])
        # Conflicting DN50+304 candidate (if recalled) must remain capped and never auto-accepted
        if "89.04.01.0002" in diag_cands:
            self.assertLessEqual(diag_cands["89.04.01.0002"]["max_score"], 0.15)
        self.assertFalse(res.get("is_exact") and res.get("matched_code") == "89.04.01.0002")

        # Strict candidate budget check
        cands_lim, _ = sparse_recall.recall_sparse_candidates(qi, qi["parsed_spec"], limit=10)
        self.assertLessEqual(len(cands_lim), 10)

    def test_fts_raw_revision_invalidation_detached_copy_and_m0_m1_combo(self):
        _insert_material(
            "89.05.01.0001", "8芯单模室内光缆", "8芯单模室内光缆", category="弱电",
            parsed_params={"domain": "weak_current", "attrs": {}},
        )
        r1 = db_manager.search_materials_fts_raw('"8" AND "芯" AND "光" AND "缆"', limit=10)
        self.assertTrue(any(r["code"] == "89.05.01.0001" for r in r1))
        # Detached copy check
        r1[0]["name"] = "MUTATED_M1"
        r2 = db_manager.search_materials_fts_raw('"8" AND "芯" AND "光" AND "缆"', limit=10)
        self.assertNotEqual(r2[0]["name"], "MUTATED_M1")

        # Revision invalidation on new insert
        _insert_material("89.05.01.0002", "8芯单模室内光缆新", "8芯单模室内光缆 修订版", category="弱电")
        r3 = db_manager.search_materials_fts_raw('"8" AND "芯" AND "光" AND "缆"', limit=10)
        self.assertTrue(any(r["code"] == "89.05.01.0002" for r in r3))

        # M0 + M1 combined interaction
        qi_combo = prepare_query("光纤", "1.名称：光纤\n2.规格：8芯单模室内光缆\n3.标准编码：89.05.01.0001", "弱电")
        qi_combo["recall_options"] = {"v018_m0_enabled": True, "v018_m1_enabled": True}
        pool_combo = matcher.hybrid_recall(qi_combo, None)
        by_code = {c["code"]: c for c in pool_combo}
        self.assertIn("89.05.01.0001", by_code)
        self.assertIn("exact_code", by_code["89.05.01.0001"]["recall_sources"])
        self.assertTrue(any(s.startswith("sparse_") for s in by_code["89.05.01.0001"]["recall_sources"]))


if __name__ == "__main__":
    unittest.main()
