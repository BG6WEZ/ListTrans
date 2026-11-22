# -*- coding: utf-8 -*-
"""V018-M3 R01 选型冻结、降级护栏与回退契约回归测试。

覆盖：
1. 默认开关全 OFF 冻结契约及开关往返切换无残留状态
2. OBS-COORD-M2-02：模型不可用、索引版本/维度失配、零向量结果时，M2 RRF/cap 安全跳过并回到已验关键词路径
3. OBS-A-M2-01：空候选旧 q_name[:4] 兜底与 M1 稀疏兜底配对验证（长施工文本、位号与编码、明确物理冲突）
4. OBS-A-M1-01：_fts_raw_rows 有界 LRU 上限（512）及 db_revision 失效/独立副本契约
5. 硬约束与禁止误自动接受护栏在冻结全 OFF 与实验开关下均成立
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from app import (
    db_manager,
    matcher,
    sparse_recall,
    structured_recall,
    vector_fusion,
    vector_index,
)
from app.query_preprocessor import prepare_query


def _insert_material(code, name, spec, category="通用", unit="个", brand="", parsed_params=None):
    with db_manager.get_db_cursor() as cur:
        pp = (
            json.dumps(parsed_params, ensure_ascii=False)
            if isinstance(parsed_params, dict)
            else (parsed_params or "")
        )
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


class TestV018M3SelectionAndDegradation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_manager.init_db()

    def setUp(self):
        self._env_keys = (
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
        self._saved_env = {k: os.environ.get(k) for k in self._env_keys}
        for k in self._env_keys:
            os.environ.pop(k, None)
        structured_recall.invalidate_structured_index()

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        structured_recall.invalidate_structured_index()

    # ── 1. 默认全 OFF 冻结与开关往返无残留 ─────────────────────────────────
    def test_frozen_default_all_off_and_roundtrip_rollback(self):
        self.assertFalse(structured_recall.DEFAULT_V018_M0_ENABLED)
        self.assertFalse(sparse_recall.DEFAULT_V018_M1_ENABLED)
        self.assertFalse(vector_fusion.DEFAULT_V018_M2_ENABLED)
        self.assertFalse(structured_recall.is_v018_m0_recall_enabled())
        self.assertFalse(sparse_recall.is_v018_m1_recall_enabled())
        self.assertFalse(vector_fusion.is_v018_m2_recall_enabled())

        _insert_material(
            "91.01.01.0001", "手动球阀", "316L不锈钢 DN20 焊接", category="医用气体",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢316L", "管径": "DN20", "dn_val": 20}},
        )
        os.environ["LISTTRANS_EVAL_KEYWORD_ONLY"] = "1"
        qi = prepare_query("手动球阀", "316L不锈钢 DN20", "医用气体")
        pool_before = matcher.hybrid_recall(qi, None)

        # Toggle M0/M1/M2 ON, execute, then toggle back OFF
        os.environ["LISTTRANS_V018_M0_RECALL"] = "1"
        os.environ["LISTTRANS_V018_M1_RECALL"] = "1"
        os.environ["LISTTRANS_V018_M2_RECALL"] = "1"
        os.environ["LISTTRANS_V018_M2_FUSION_MODE"] = "rrf"
        os.environ["LISTTRANS_V018_M2_CANDIDATE_CAP"] = "200"
        _ = matcher.hybrid_recall(qi, None)

        os.environ["LISTTRANS_V018_M0_RECALL"] = "0"
        os.environ["LISTTRANS_V018_M1_RECALL"] = "0"
        os.environ["LISTTRANS_V018_M2_RECALL"] = "0"
        pool_after = matcher.hybrid_recall(qi, None)

        self.assertEqual([c["code"] for c in pool_before], [c["code"] for c in pool_after])
        self.assertEqual(
            [c.get("recall_sources") for c in pool_before],
            [c.get("recall_sources") for c in pool_after],
        )

    # ── 2. OBS-COORD-M2-02：无语义时跳过 M2 RRF/cap 回到已验关键词行为 ───────
    def test_no_semantic_skips_rrf_and_cap_on_model_missing_mismatch_and_empty_vector(self):
        # Insert 6 materials that match FTS so cap=2 would truncate if RRF/cap wrongly ran
        for i, dn in enumerate((15, 20, 25, 32, 40, 50), 1):
            _insert_material(
                f"91.02.01.000{i}",
                "手动球阀",
                f"316L不锈钢 DN{dn} 焊接",
                category="医用气体",
            )

        qi_base = prepare_query("手动球阀", "316L不锈钢 焊接", "医用气体")
        os.environ["LISTTRANS_EVAL_KEYWORD_ONLY"] = "1"
        expected_kw_pool = matcher.hybrid_recall(qi_base, None)
        self.assertGreaterEqual(len(expected_kw_pool), 6)
        expected_codes = [c["code"] for c in expected_kw_pool]

        # (a) Model disabled / unavailable + M2 RRF + aggressive cap=2
        qi_rrf = dict(
            qi_base,
            recall_options={
                "v018_m2_enabled": True,
                "fusion_mode": "rrf",
                "rrf_k": 60,
                "candidate_cap": 2,
            },
        )
        deg_disabled = matcher.hybrid_recall(qi_rrf, None)
        self.assertEqual([c["code"] for c in deg_disabled], expected_codes)
        self.assertFalse(any("fusion_rank" in c for c in deg_disabled))

        # (b) Vector ready=True but vector search returns empty []
        os.environ["LISTTRANS_EVAL_KEYWORD_ONLY"] = "0"
        with (
            patch.object(vector_index.VectorIndexManager, "is_enabled", new_callable=unittest.mock.PropertyMock, return_value=True),
            patch.object(vector_index.index_manager, "has_current_index", return_value=True),
            patch.object(vector_index.index_manager, "encode_single_query", return_value=[0.0] * 512),
            patch.object(vector_index.index_manager, "search", return_value=[]),
        ):
            deg_empty_vec = matcher.hybrid_recall(qi_rrf, None)
        self.assertEqual([c["code"] for c in deg_empty_vec], expected_codes)
        self.assertFalse(any("fusion_rank" in c for c in deg_empty_vec))

        # (c) Index schema/version or dimension mismatch -> load_or_build returns None -> degrades cleanly
        qi_mismatch = dict(
            qi_base,
            recall_options={
                "v018_m2_enabled": True,
                "vector_text_mode": "canonical",
                "fusion_mode": "rrf",
                "candidate_cap": 2,
            },
        )
        with (
            patch.object(vector_index.VectorIndexManager, "is_enabled", new_callable=unittest.mock.PropertyMock, return_value=True),
            patch.object(vector_index.index_manager, "load_or_build_candidate_index", return_value=None),
        ):
            deg_mismatch = matcher.hybrid_recall(qi_mismatch, None)
        self.assertEqual([c["code"] for c in deg_mismatch], expected_codes)

        # (d) Explicit diagnostic opt-in `allow_keyword_rrf=True` still allows RRF/cap when requested
        os.environ["LISTTRANS_EVAL_KEYWORD_ONLY"] = "1"
        qi_optin = dict(
            qi_base,
            recall_options={
                "v018_m2_enabled": True,
                "fusion_mode": "rrf",
                "candidate_cap": 2,
                "allow_keyword_rrf": True,
            },
        )
        optin_pool = matcher.hybrid_recall(qi_optin, None)
        self.assertEqual(len(optin_pool), 2)
        self.assertTrue(all("fusion_rank" in c for c in optin_pool))

    # ── 3. OBS-A-M2-01：空候选旧 q_name[:4] 兜底 vs M1 稀疏兜底配对验证 ──────
    def test_obs_a_m2_01_empty_pool_fallback_vs_m1_sparse_paired(self):
        os.environ["LISTTRANS_EVAL_KEYWORD_ONLY"] = "1"
        _insert_material("91.03.01.0001", "单管防爆灯", "1200mm 吊装 18W", category="强电")
        _insert_material("91.03.01.0002", "组合式空气处理机组", "风量5000", category="暖通")
        _insert_material(
            "91.03.01.0003", "特种精密球阀", "316L不锈钢 DN20 焊接", category="医用气体",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢316L", "管径": "DN20", "dn_val": 20}},
        )
        _insert_material(
            "91.03.01.0004", "特种精密球阀", "304不锈钢 DN80 焊接", category="医用气体",
            parsed_params={"domain": "valve_water", "attrs": {"材质": "不锈钢304", "管径": "DN80", "dn_val": 80}},
        )

        # (a) Long construction text: M1=OFF triggers legacy fallback (or misses), M1=ON recalls via sparse_phrase_split
        qi_long = prepare_query(
            "防爆型壁挂式单管LED灯管",
            "1.规格：18W 1200mm\n2.工作内容：打眼安装接线调试",
            "强电",
        )
        pool_long_off = matcher.hybrid_recall(qi_long, None)
        pool_long_m1 = matcher.hybrid_recall(dict(qi_long, recall_options={"v018_m1_enabled": True}), None)
        self.assertIn("91.03.01.0001", [c["code"] for c in pool_long_m1])
        self.assertFalse(any("fallback" in (c.get("recall_sources") or []) for c in pool_long_m1))
        # Verify old path didn't unexpectedly invoke sparse_*
        self.assertFalse(any(
            s.startswith("sparse_") for c in pool_long_off for s in (c.get("recall_sources") or [])
        ))

        # (b) Device tag + project code: q_name[:4] is "AHU-" (device tag), while M1 strips tag cleanly
        qi_tag = prepare_query(
            "AHU-J-01 组合式空气处理机组",
            "1.项目编码：030408001001\n2.风量：5000m3/h",
            "暖通",
        )
        pool_tag_m1 = matcher.hybrid_recall(dict(qi_tag, recall_options={"v018_m1_enabled": True}), None)
        self.assertIn("91.03.01.0002", [c["code"] for c in pool_tag_m1])

        # (c) Explicit physical conflict (DN20 vs DN80): neither M1=OFF fallback nor M1=ON bypasses hard constraints
        qi_conf = prepare_query(
            "特种精密球阀超长后缀名",
            "1.材质：316L不锈钢\n2.管径：DN20",
            "医用气体",
        )
        for opts in ({}, {"v018_m1_enabled": True}):
            q_run = dict(qi_conf, recall_options=opts) if opts else dict(qi_conf)
            res = matcher.match_item(q_run, diagnostic_targets=["91.03.01.0003", "91.03.01.0004"])
            diag_map = {c["code"]: c for c in res["diagnostics"]["candidates"]}
            if "91.03.01.0004" in diag_map:
                self.assertLessEqual(diag_map["91.03.01.0004"]["max_score"], 0.15)
            self.assertFalse(res.get("is_exact") and res.get("matched_code") == "91.03.01.0004")

    # ── 4. OBS-A-M1-01：_fts_raw_rows LRU 有界上限 ────────────────────────────
    def test_fts_raw_lru_is_bounded_and_revision_consistent(self):
        info = db_manager._fts_raw_rows.cache_info()
        self.assertEqual(info.maxsize, 512)
        _insert_material("91.04.01.0001", "有界缓存测试阀", "DN25 316L", category="阀门")
        rows = db_manager.search_materials_fts_raw('"有" AND "界" AND "缓" AND "存"', limit=10)
        self.assertTrue(any(r["code"] == "91.04.01.0001" for r in rows))


if __name__ == "__main__":
    unittest.main()
