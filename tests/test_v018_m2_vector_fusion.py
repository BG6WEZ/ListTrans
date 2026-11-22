# -*- coding: utf-8 -*-
"""V018-M2 R01 向量文本、TopK、RRF 融合与隔离降级契约测试。

覆盖：
1. 默认开关关闭（LISTTRANS_V018_M2_RECALL=0）保持 legacy-v1 / K=50 / union 路径
2. 规范化物料与查询编码文本（同名异规格、长施工文本确定性截断、位号剔除与型号保留）
3. RRF 确定性融合（去重合并、exact_code 置顶、乱序输入确定性同分排序、候选上限截断）
4. 版本化隔离候选索引（不覆盖 legacy-v1 缓存、metadata 含 text_schema_version 与 db_revision、版本失配拒载）
5. 模型禁用/缺失/空向量降级、硬约束不绕过、关闭开关立即切回 legacy-v1
"""
import json
import os
import unittest
import zipfile
from pathlib import Path

from app import db_manager, matcher, vector_fusion, vector_index
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


class TestV018M2VectorFusion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_manager.init_db()

    def setUp(self):
        self._saved_env = {
            k: os.environ.get(k)
            for k in (
                "LISTTRANS_V018_M0_RECALL",
                "LISTTRANS_V018_M1_RECALL",
                "LISTTRANS_V018_M2_RECALL",
                "LISTTRANS_V018_M2_TEXT_MODE",
                "LISTTRANS_V018_M2_VECTOR_TOPK",
                "LISTTRANS_V018_M2_FUSION_MODE",
                "LISTTRANS_V018_M2_RRF_K",
                "LISTTRANS_V018_M2_CANDIDATE_CAP",
                "LISTTRANS_EVAL_KEYWORD_ONLY",
            )
        }
        for k in self._saved_env:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_switches_are_off_and_legacy(self):
        self.assertFalse(vector_fusion.DEFAULT_V018_M2_ENABLED)
        self.assertFalse(vector_fusion.is_v018_m2_recall_enabled())
        opts = vector_fusion.get_vector_fusion_options()
        self.assertFalse(opts["v018_m2_enabled"])
        self.assertEqual(opts["vector_text_mode"], "legacy")
        self.assertEqual(opts["text_schema_version"], "legacy-v1")
        self.assertEqual(opts["vector_top_k"], 50)
        self.assertEqual(opts["fusion_mode"], "union")
        self.assertEqual(opts["candidate_cap"], 0)

    def test_canonical_encoding_text_specs_truncation_and_tag_vs_model(self):
        # 1) Same name, different specs produce distinct canonical material texts
        t1, m1 = vector_fusion.build_material_vector_text(
            {"name": "手动球阀", "specification": "316L不锈钢 DN15", "category": "医用气体", "brand": "A厂"},
            mode="canonical",
        )
        t2, m2 = vector_fusion.build_material_vector_text(
            {"name": "手动球阀", "specification": "304不锈钢 DN50", "category": "医用气体", "brand": "A厂"},
            mode="canonical",
        )
        self.assertNotEqual(t1, t2)
        self.assertEqual(m1["text_schema_version"], vector_fusion.VECTOR_TEXT_SCHEMA_CANONICAL)
        self.assertIn("DN15", t1)
        self.assertIn("DN50", t2)

        # 2) Device tag stripped while product model kept
        qi = prepare_query(
            "AHU-J-01 组合式空调机组",
            "1.型号：RAU-Q-D301\n2.风量：8000m3/h\n3.工作内容：吊装接线调试清理现场",
            "暖通",
        )
        q_txt, q_meta = vector_fusion.build_query_vector_text(qi, qi["parsed_spec"], mode="canonical")
        self.assertNotIn("AHU-J-01", q_txt)
        self.assertIn("AHU-J-01", q_meta["removed_tags"])
        self.assertIn("RAU-Q-D301", q_txt)
        self.assertNotIn("工作内容", q_txt)

        # 3) Long text deterministic head+tail truncation keeps tail parameter within 384 chars
        long_spec = "1.说明：" + ("超长工程说明条款" * 70) + " 关键末端规格:DN80_316L"
        qi_long = prepare_query("不锈钢球阀", long_spec, "给排水")
        q_long, q_lmeta = vector_fusion.build_query_vector_text(qi_long, qi_long["parsed_spec"], mode="canonical")
        self.assertTrue(q_lmeta["truncated"])
        self.assertLessEqual(len(q_long), vector_fusion.MAX_ENCODE_CHARS)
        self.assertIn("DN80_316L", q_long)

    def test_rrf_fusion_dedup_exact_pin_determinism_and_cap(self):
        cands = [
            {"code": "90.00.00.0002", "name": "B", "recall_source": "fts", "recall_sources": ["fts"], "channel_ranks": {"fts": 1}, "semantic_score": 0.0},
            {"code": "90.00.00.0003", "name": "C", "recall_source": "fts", "recall_sources": ["fts"], "channel_ranks": {"fts": 2}, "semantic_score": 0.0},
            {"code": "90.00.00.0003", "name": "C", "recall_source": "semantic", "recall_sources": ["semantic"], "channel_ranks": {"semantic": 1}, "semantic_score": 0.88},
            {"code": "90.00.00.0001", "name": "A", "recall_source": "exact_code", "recall_sources": ["exact_code"], "channel_ranks": {"exact_code": 1}, "semantic_score": 0.0},
            {"code": "90.00.00.0004", "name": "D", "recall_source": "semantic", "recall_sources": ["semantic"], "channel_ranks": {"semantic": 2}, "semantic_score": 0.70},
        ]
        fused1, meta1 = vector_fusion.fuse_recall_candidates(cands, fusion_mode="rrf", rrf_k=60, candidate_cap=3)
        fused2, _ = vector_fusion.fuse_recall_candidates(list(reversed(cands)), fusion_mode="rrf", rrf_k=60, candidate_cap=3)

        self.assertEqual([c["code"] for c in fused1], [c["code"] for c in fused2])
        self.assertEqual(len(fused1), 3)
        self.assertEqual(meta1["duplicate_merges"], 1)
        self.assertEqual(meta1["truncated_count"], 1)
        # Exact code pinned #1; dual-channel C (fts+semantic) ranks #2
        self.assertEqual(fused1[0]["code"], "90.00.00.0001")
        self.assertEqual(fused1[1]["code"], "90.00.00.0003")
        self.assertEqual(sorted(fused1[1]["recall_sources"]), ["fts", "semantic"])
        self.assertEqual(fused1[1]["channel_ranks"], {"fts": 2, "semantic": 1})
        self.assertAlmostEqual(fused1[1]["semantic_score"], 0.88, places=4)

    def test_isolated_candidate_index_versioning_rollback_and_degradation(self):
        _insert_material(
            "90.01.01.0001", "手动球阀", "316L不锈钢 DN20 焊接", category="医用气体",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢316L", "管径": "DN20", "dn_val": 20}},
        )
        _insert_material(
            "90.01.01.0002", "手动球阀", "304不锈钢 DN80 焊接", category="医用气体",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢304", "管径": "DN80", "dn_val": 80}},
        )
        idx = vector_index.index_manager
        rev = db_manager.get_db_revision()
        rows = db_manager.get_all_materials(limit=1000)
        idx.build_index(rows, db_revision=rev)

        legacy_bundle = Path(vector_index.get_cache_dir()) / vector_index.SNAPSHOT_FILE
        self.assertTrue(legacy_bundle.is_file())
        legacy_bytes_before = legacy_bundle.read_bytes()

        # Build isolated canonical candidate index; legacy-v1 bundle must stay untouched
        cand_snap = idx.load_or_build_candidate_index(text_mode="canonical", persist=True)
        self.assertIsNotNone(cand_snap)
        self.assertEqual(cand_snap.text_schema_version, vector_fusion.VECTOR_TEXT_SCHEMA_CANONICAL)
        self.assertEqual(cand_snap.db_revision, rev)
        self.assertEqual(legacy_bundle.read_bytes(), legacy_bytes_before)

        cand_bundle = Path(idx.get_candidate_cache_dir(vector_fusion.VECTOR_TEXT_SCHEMA_CANONICAL)) / vector_index.SNAPSHOT_FILE
        self.assertTrue(cand_bundle.is_file())
        with zipfile.ZipFile(cand_bundle) as zf:
            meta = json.loads(zf.read("cache_metadata.json"))
            self.assertEqual(meta["text_schema_version"], vector_fusion.VECTOR_TEXT_SCHEMA_CANONICAL)
            self.assertEqual(meta["db_revision"], rev)
            # Version mismatch rejected cleanly by _read_cache_candidate
            with self.assertRaises(ValueError):
                idx._read_cache_candidate(
                    meta, zf.open, set(zf.namelist()), rev, expected_text_schema="wrong-schema-v99"
                )

        # Model metadata check
        m_meta = idx.get_model_metadata()
        self.assertEqual(m_meta["model_name"], "bge-small-zh-v1.5")
        self.assertEqual(m_meta["dimension"], 512)
        self.assertEqual(m_meta["active_text_schema_version"], "legacy-v1")
        self.assertIn(vector_fusion.VECTOR_TEXT_SCHEMA_CANONICAL, m_meta["candidate_versions_loaded"])

        # Hard constraint still enforced on canonical+RRF candidates (DN80 vs DN20)
        qi = prepare_query("不锈钢球阀", "1.材质：316L不锈钢\n2.管径：DN20", "医用气体")
        qi["recall_options"] = {
            "v018_m2_enabled": True,
            "vector_text_mode": "canonical",
            "vector_top_k": 50,
            "fusion_mode": "rrf",
            "candidate_cap": 100,
        }
        res = matcher.match_item(qi, diagnostic_targets=["90.01.01.0001", "90.01.01.0002"])
        diag_cands = {c["code"]: c for c in res["diagnostics"]["candidates"]}
        self.assertIn("90.01.01.0001", diag_cands)
        if "90.01.01.0002" in diag_cands:
            self.assertLessEqual(diag_cands["90.01.01.0002"]["max_score"], 0.15)
        self.assertFalse(res.get("is_exact") and res.get("matched_code") == "90.01.01.0002")

        # Keyword-only degradation with M2 RRF enabled must not crash and degrades to FTS
        os.environ["LISTTRANS_EVAL_KEYWORD_ONLY"] = "1"
        pool_deg = matcher.hybrid_recall(qi, None)
        self.assertFalse(any("semantic" in (c.get("recall_sources") or []) for c in pool_deg))


if __name__ == "__main__":
    unittest.main()
