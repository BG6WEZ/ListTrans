# -*- coding: utf-8 -*-
"""
V015-M3 / R01 硬约束反例测试（真实 HTTP 集成层）。

与 tests/test_m3_hard_constraints.py（纯函数层）互补：本模块经 FastAPI TestClient
走真实 Bearer 鉴权与真实 HTTP 端点（/api/binding/submit、/api/export、/api/match、
/api/batch_match），验证推荐/规则接受/人工确认/导出四链路复用同一套硬约束语义：

1. 人工确认：七道门反例（304/316L、米/平方米、非防爆/防爆、不带泵/带泵、
   parsed_params 真实品牌值冲突、真跨专业、DN 口径）一律 409 binding_hard_conflict；
   医气别名、同品牌、304 别名、15/15.0 数值等等价正例必须 200，证明不泛化误杀；
2. 导出：确认后物料漂移致硬冲突 → 422 export_blocked 且 detail.row.conflicts
   带原因串；合法确认行导出 200；
3. 推荐链路（真实 HTTP）：强制 +100 不破 0.3 cap、动态排除双归零、
   服务端第二遍内存系列 +5 不复活 hard_blocked 且受各自 max_score 收口、
   top1 硬冲突经 parsed_params 同源复核后 rejected 且不预绑定。

全部在 conftest 隔离根内运行：物料库/账号库/会话库/规则文件/反馈日志/动态规则
缓存均隔离，AUTH_DEV_MODE=false，不调付费 LLM、不下载模型、不做任何 git/线上操作。
动态规则一律经 LLMRuleManager._active_rules_cache 类缓存注入，不写库、不调 LLM。
"""
import io
import json
import os
import unittest
from unittest.mock import patch

import openpyxl
from fastapi.testclient import TestClient

from app.db_manager import get_db_connection, init_db
from app.auth_manager import (
    init_auth_db, issue_token_for_user, get_user_by_username,
    get_auth_connection, get_password_hash,
)
from app.session_manager import init_session_db
from app.binding_decisions import clear_binding_decisions
from app.llm_rule_engine import LLMRuleManager
from app import server as srv


def _ensure_user(username, password, role="employee"):
    if get_user_by_username(username):
        return
    conn = get_auth_connection()
    conn.execute(
        "INSERT INTO users (username, password_hash, name, role, is_active, must_change_password, account_epoch) VALUES (?,?,?,?,1,0,?)",
        (username, get_password_hash(password), username, role, f"ep-{username}"),
    )
    conn.commit()
    conn.close()


class M3HttpBase(unittest.TestCase):
    """与 M2 同型的真实 HTTP 隔离骨架；种子 helper 额外写入 parsed_params 列。"""

    _seeded_codes = set()

    @classmethod
    def setUpClass(cls):
        init_db()
        init_auth_db()
        init_session_db()
        cls.client = TestClient(srv.app)
        _ensure_user("m3tester", "Test@12345", role="employee")
        cls.user = get_user_by_username("m3tester")
        cls.token = issue_token_for_user(cls.user)
        cls.auth = {"Authorization": f"Bearer {cls.token}"}
        _ensure_user("m3admin", "AdminTest@12345", role="admin")
        cls._admin_actor = get_user_by_username("m3admin")
        issue_token_for_user(cls._admin_actor)

    @classmethod
    def _track_seed(cls, code):
        cls._seeded_codes.add(code)

    @classmethod
    def _clear_seeded_materials(cls):
        from app.db_manager import _search_materials_keyword_cached
        conn = get_db_connection()
        if cls._seeded_codes:
            marks = ",".join("?" * len(cls._seeded_codes))
            codes = tuple(cls._seeded_codes)
            conn.execute(f"DELETE FROM materials WHERE code IN ({marks})", codes)
            if conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='materials_fts'"
            ).fetchone():
                conn.execute(f"DELETE FROM materials_fts WHERE code IN ({marks})", codes)
            conn.commit()
            cls._seeded_codes.clear()
        conn.close()
        _search_materials_keyword_cached.cache_clear()

    def setUp(self):
        self._clear_seeded_materials()
        clear_binding_decisions()
        LLMRuleManager.clear_active_cache()
        # 隔离自动规则文件与反馈日志（与 M2 同型）
        self._rules_backup = None
        if os.path.exists(srv.AUTO_BINDING_RULES_FILE):
            with open(srv.AUTO_BINDING_RULES_FILE, "r", encoding="utf-8") as f:
                self._rules_backup = f.read()
        os.makedirs(os.path.dirname(srv.AUTO_BINDING_RULES_FILE), exist_ok=True)
        with open(srv.AUTO_BINDING_RULES_FILE, "w", encoding="utf-8") as f:
            f.write("[]")
        srv.save_auto_binding_rules([], actor_user=self._admin_actor)
        if os.path.exists(srv.BINDING_FEEDBACK_FILE):
            os.remove(srv.BINDING_FEEDBACK_FILE)

    def tearDown(self):
        LLMRuleManager.clear_active_cache()
        self._clear_seeded_materials()
        srv.save_auto_binding_rules([], actor_user=self._admin_actor)
        if self._rules_backup is not None:
            with open(srv.AUTO_BINDING_RULES_FILE, "w", encoding="utf-8") as f:
                f.write(self._rules_backup)

    # ---- 种子 / 请求体 helpers ----

    def _seed(self, code, name, spec, unit, category,
              brand="", description="", parsed_params=""):
        """插入带 parsed_params 的物料并同步 FTS；code 全部登记以便用例后清理。"""
        self._track_seed(code)
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO materials "
            "(code, category, name, specification, unit, brand, description, search_tokens, "
            "parsed_params, parsed_schema_version) VALUES (?,?,?,?,?,?,?,?,?,2)",
            (code, category, name, spec, unit, brand, description,
             f"{name} {spec} {category} {brand} {description}", parsed_params),
        )
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='materials_fts'"
        )
        if cur.fetchone():
            from app.preprocessor import fts_tokenize
            cur.execute("DELETE FROM materials_fts WHERE code=?", (code,))
            cur.execute(
                "INSERT INTO materials_fts "
                "(code, category, name, specification, brand, description) VALUES (?,?,?,?,?,?)",
                (code, *(fts_tokenize(v) for v in (category, name, spec, brand, description))),
            )
        conn.commit()
        conn.close()

    def _row(self, **kw):
        row = {
            "row_idx": 0,
            "name": "通用材料",
            "specification": "",
            "qty": 10,
            "unit": "台",
            "loss": 0,
            "category": "暖通",
            "brand": "",
            "professional": "暖通",
            "code": "",
        }
        row.update(kw)
        return row

    def _bind(self, code, **kw):
        base = {
            "code": code, "name": "物料", "specification": "", "unit": "台",
            "category": "暖通", "brand": "", "qty": 10,
        }
        base.update(kw)
        return base

    def _confirm_payload(self, row, bindings, scope="m3http.xlsx"):
        return {
            "original_name": row["name"],
            "original_spec": row["specification"],
            "original_qty": row["qty"],
            "bindings": bindings,
            "professional": row["professional"],
            "scope_filename": scope,
            "original_unit": row["unit"],
            "original_loss": row["loss"],
            "original_category": row["category"],
            "original_brand": row["brand"],
            "original_code": row["code"],
            "original_row_idx": row.get("row_idx", 0),
        }

    def _export_item(self, row, bindings, status="manual_confirmed"):
        return {
            "original_row_idx": row.get("row_idx", 0),
            "original_name": row["name"],
            "original_spec": row["specification"],
            "original_qty": row["qty"],
            "original_unit": row["unit"],
            "original_loss": row["loss"],
            "original_category": row["category"],
            "original_brand": row["brand"],
            "professional": row["professional"],
            "original_code": row["code"],
            "matched_code": ",".join(b["code"] for b in bindings),
            "matched_name": bindings[0].get("name", "") if bindings else "",
            "matched_category": "",
            "matched_spec": "",
            "matched_unit": "",
            "matched_brand": "",
            "bindings": bindings,
            "decision_status": status,
            "decision_source": "manual" if status == "manual_confirmed" else "matcher",
        }

    def _post_confirm(self, row, code, **bind_kw):
        binds = [self._bind(code, **bind_kw)]
        return self.client.post(
            "/api/binding/submit", json=self._confirm_payload(row, binds), headers=self.auth,
        )

    @staticmethod
    def _dynamic_rule(matching_rules, rule_id="m3h-rule"):
        """active_rules 缓存条目；scope_target 空串表示全量命中。"""
        return {
            "rule_id": rule_id,
            "scope_level": "category",
            "scope_target": "",
            "parsed_dsl": {"matching_rules": matching_rules},
        }


# ---------------------------------------------------------------------------
# 1. 人工确认端点：七道门硬冲突一律 409，等价正例 200（真实 HTTP + 真实 Bearer）
# ---------------------------------------------------------------------------

class TestM3ConfirmHardConflictGate(M3HttpBase):

    def _assert_409(self, resp, reason_part):
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["error"], "binding_hard_conflict")
        self.assertEqual(
            detail["message"], "绑定目标与清单行存在硬冲突，不允许人工确认"
        )
        reasons = [c["reason"] for c in detail["conflicts"]]
        self.assertTrue(any(reason_part in r for r in reasons), reasons)

    def test_304_vs_316l_grade_blocked(self):
        # 门 5：304/316L 枚举硬隔离，候选牌号经 parsed_params 真实读取
        self._seed(
            "M3H-G316", "不锈钢管", "316L 不锈钢管", "米", "暖通",
            parsed_params=json.dumps({"attrs": {"材质": "不锈钢316L"}}, ensure_ascii=False),
        )
        row = self._row(name="不锈钢管", specification="304", unit="米")
        resp = self._post_confirm(row, "M3H-G316", name="不锈钢管", unit="米")
        self._assert_409(resp, "不锈钢牌号冲突硬隔离")

    def test_meter_vs_square_meter_blocked(self):
        # 门 3：米（长度）vs 平方米（面积）跨物理维度，不得因子串包含判兼容
        self._seed("M3H-AREA", "装饰板材", "通用板材", "平方米", "暖通")
        row = self._row(name="镀锌钢管", specification="DN100", unit="米")
        resp = self._post_confirm(row, "M3H-AREA", name="装饰板材", unit="平方米")
        self._assert_409(resp, "单位不可换算阻断")

    def test_non_ex_vs_ex_blocked(self):
        # 门 7：清单显式“非防爆” vs 候选显式防爆，双向互斥硬隔离
        self._seed("M3H-EX", "排风机", "防爆型排风机", "台", "暖通", description="防爆风机")
        row = self._row(name="排风机", specification="非防爆", unit="台")
        resp = self._post_confirm(row, "M3H-EX", name="排风机", unit="台")
        self._assert_409(resp, "非防爆要求vs防爆硬隔离")

    def test_no_pump_vs_pump_blocked(self):
        # 门 6：明确“不带提升泵” vs 候选带泵，否定三态硬隔离
        self._seed("M3H-PUMP", "风机盘管", "卧式暗装", "台", "暖通", description="冷凝水提升泵")
        row = self._row(name="风机盘管", specification="不带提升泵", unit="台")
        resp = self._post_confirm(row, "M3H-PUMP", name="风机盘管", unit="台")
        self._assert_409(resp, "提升泵否定冲突硬隔离")

    def test_brand_conflict_via_parsed_params_blocked(self):
        # 门 2：品牌列为空时回退 parsed_params.attrs.品牌 真实字符串，冲突仍阻断
        self._seed(
            "M3H-BRND", "通用装置", "标准型", "台", "暖通", brand="",
            parsed_params=json.dumps({"attrs": {"品牌": "起帆"}}, ensure_ascii=False),
        )
        row = self._row(name="通用装置", brand="远东", unit="台")
        resp = self._post_confirm(row, "M3H-BRND", name="通用装置", unit="台")
        self._assert_409(resp, "品牌冲突阻断")

    def test_cross_professional_blocked(self):
        # 门 1：真跨专业（强电 vs 暖通）拦截，别名归一不代表泛化放行
        self._seed("M3H-PROF", "排风机", "通用排风设备", "台", "暖通")
        row = self._row(name="排风机", professional="强电", category="强电", unit="台")
        resp = self._post_confirm(row, "M3H-PROF", name="排风机", unit="台", category="暖通")
        self._assert_409(resp, "专业冲突阻断")

    def test_dn_caliber_conflict_blocked(self):
        # 门 4：DN 口径分字段比较，DN15 vs dn_val=20 阻断
        self._seed(
            "M3H-DN20", "镀锌钢管", "DN20", "米", "暖通",
            parsed_params=json.dumps({"attrs": {"dn_val": 20}}, ensure_ascii=False),
        )
        row = self._row(name="镀锌钢管", specification="DN15", unit="米")
        resp = self._post_confirm(row, "M3H-DN20", name="镀锌钢管", unit="米")
        self._assert_409(resp, "管径口径冲突阻断")

    # ---- 等价正例：证明硬约束不泛化误杀（全部必须 200）----

    def test_medical_gas_alias_pass(self):
        # 医用气体=医气 别名归一后相容
        self._seed("M3H-MED", "气体终端", "氧气终端", "个", "医气")
        row = self._row(
            name="气体终端", specification="氧气终端", unit="个",
            category="医气", professional="医用气体",
        )
        resp = self._post_confirm(row, "M3H-MED", name="气体终端", unit="个", category="医气")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["decision"]["decision_status"], "manual_confirmed")

    def test_same_brand_via_parsed_params_pass(self):
        # 同品牌（候选值来自 parsed_params）相容
        self._seed(
            "M3H-BOK", "通用装置", "标准型", "台", "暖通",
            parsed_params=json.dumps({"attrs": {"品牌": "远东"}}, ensure_ascii=False),
        )
        row = self._row(name="通用装置", brand="远东", unit="台")
        resp = self._post_confirm(row, "M3H-BOK", name="通用装置", unit="台")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_304_alias_normalization_pass(self):
        # 304 别名归一后同牌号相容
        self._seed(
            "M3H-G304", "不锈钢管", "304 不锈钢管", "米", "暖通",
            parsed_params=json.dumps({"attrs": {"材质": "304"}}, ensure_ascii=False),
        )
        row = self._row(name="不锈钢管", specification="304", unit="米")
        resp = self._post_confirm(row, "M3H-G304", name="不锈钢管", unit="米")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_dn_15_vs_15_dot_0_numeric_pass(self):
        # 15 与 15.0 按数值比较，int/float 类型差异不构成冲突
        self._seed(
            "M3H-DN15", "镀锌钢管", "DN15", "米", "暖通",
            parsed_params=json.dumps({"attrs": {"dn_val": 15.0}}, ensure_ascii=False),
        )
        row = self._row(name="镀锌钢管", specification="DN15", unit="米")
        resp = self._post_confirm(row, "M3H-DN15", name="镀锌钢管", unit="米")
        self.assertEqual(resp.status_code, 200, resp.text)


# ---------------------------------------------------------------------------
# 2. 导出端点：确认后物料漂移致硬冲突 → 422 export_blocked；合法行 200
# ---------------------------------------------------------------------------

class TestM3ExportHardConflictGate(M3HttpBase):

    SCOPE = "m3http.xlsx"

    def _confirm_binds(self, row, binds):
        resp = self.client.post(
            "/api/binding/submit", json=self._confirm_payload(row, binds, self.SCOPE),
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp

    def _post_export(self, row, binds):
        item = self._export_item(row, binds)
        return self.client.post(
            "/api/export", json={"items": [item], "scope_filename": self.SCOPE},
            headers=self.auth,
        )

    def test_unit_drift_after_confirm_blocks_export(self):
        # 确认后物料库单位漂移为平方米：新鲜连接复核必须 fail-closed（422 带原因串）
        self._seed("M3H-E1", "镀锌钢管", "DN100", "米", "暖通")
        row = self._row(name="镀锌钢管", specification="DN100", unit="米")
        binds = [self._bind("M3H-E1", name="镀锌钢管", specification="DN100",
                            unit="米", category="暖通")]
        self._confirm_binds(row, binds)

        conn = get_db_connection()
        conn.execute("UPDATE materials SET unit='平方米' WHERE code='M3H-E1'")
        conn.commit()
        conn.close()

        resp = self._post_export(row, binds)
        self.assertEqual(resp.status_code, 422, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["error"], "export_blocked")
        self.assertEqual(detail["row"]["original_row_idx"], 0)
        reasons = [c["reason"] for c in detail["row"]["conflicts"]]
        self.assertIn("单位不可换算阻断", reasons)

    def test_grade_drift_via_parsed_params_blocks_export(self):
        # 确认后 parsed_params 材质漂移 304→316L：导出同源复核 422
        self._seed(
            "M3H-E2", "不锈钢管", "304 不锈钢管", "米", "暖通",
            parsed_params=json.dumps({"attrs": {"材质": "304"}}, ensure_ascii=False),
        )
        row = self._row(name="不锈钢管", specification="304", unit="米")
        binds = [self._bind("M3H-E2", name="不锈钢管", specification="304 不锈钢管",
                            unit="米", category="暖通")]
        self._confirm_binds(row, binds)

        conn = get_db_connection()
        conn.execute(
            "UPDATE materials SET parsed_params=? WHERE code='M3H-E2'",
            (json.dumps({"attrs": {"材质": "不锈钢316L"}}, ensure_ascii=False),),
        )
        conn.commit()
        conn.close()

        resp = self._post_export(row, binds)
        self.assertEqual(resp.status_code, 422, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["error"], "export_blocked")
        reasons = [c["reason"] for c in detail["row"]["conflicts"]]
        self.assertIn("不锈钢牌号冲突硬隔离(304/316L)", reasons)

    def test_confirmed_valid_export_200(self):
        # 正例对照：无漂移的合法人工确认行必须可导出，证明门禁不误伤
        self._seed("M3H-E3", "镀锌钢管", "DN100", "米", "暖通")
        row = self._row(name="镀锌钢管", specification="DN100", unit="米")
        binds = [self._bind("M3H-E3", name="镀锌钢管", specification="DN100",
                            unit="米", category="暖通")]
        self._confirm_binds(row, binds)
        resp = self._post_export(row, binds)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("spreadsheetml", resp.headers["content-type"])


# ---------------------------------------------------------------------------
# 3. 推荐链路真实 HTTP：cap/动态排除/系列 boost/top1 同源硬冲突复核
# ---------------------------------------------------------------------------

class TestM3HttpMatcherGuards(M3HttpBase):

    @staticmethod
    def _cap_candidate():
        # 与纯函数层同型夹具：芯数 3 vs 查询 4 触发 0.3 封顶；code 不入库，
        # match_item 的 enrich SELECT 不命中即保留 monkeypatch 原值
        return {
            "code": "M3H-CAP-01",
            "name": "电力电缆",
            "specification": "YJV-3*25",
            "category": "强电",
            "unit": "米",
            "brand": "",
            "description": "",
            "parsed_params": json.dumps(
                {"attrs": {"芯数": "3", "截面积": "25"}}, ensure_ascii=False),
            "semantic_score": 0.0,
        }

    def test_force_boost_100_cannot_break_cap_over_http(self):
        # 真实 /api/match：先封 0.3，再动态强制 +100，分数仍不得超过 30
        LLMRuleManager._active_rules_cache = [
            self._dynamic_rule({"score_boost_keywords": {"YJV": 100.0}}, rule_id="m3h-boost"),
        ]
        payload = {
            "name": "电力电缆", "text": "YJV-4*25", "category": "强电",
            "professional": "强电", "unit": "米",
        }
        with patch("app.matcher.hybrid_recall", return_value=[self._cap_candidate()]):
            resp = self.client.post("/api/match", json=payload, headers=self.auth)
        self.assertEqual(resp.status_code, 200, resp.text)
        top = resp.json()["top_candidates"][0]
        self.assertFalse(top["hard_blocked"])
        self.assertAlmostEqual(top["max_score"], 0.3, places=6)
        self.assertLessEqual(top["score"], 30.0 + 1e-9)

    def test_dynamic_exclusion_double_zero_over_http(self):
        # 真实 /api/match：动态排除词命中即 hard_blocked + 双归零，不可自动接受
        LLMRuleManager._active_rules_cache = [
            self._dynamic_rule({"must_not_contain": ["YJV"]}, rule_id="m3h-excl"),
        ]
        payload = {
            "name": "电力电缆", "text": "YJV-4*25", "category": "强电",
            "professional": "强电", "unit": "米",
        }
        with patch("app.matcher.hybrid_recall", return_value=[self._cap_candidate()]):
            resp = self.client.post("/api/match", json=payload, headers=self.auth)
        self.assertEqual(resp.status_code, 200, resp.text)
        top = resp.json()["top_candidates"][0]
        self.assertTrue(top["hard_blocked"])
        self.assertEqual(top["max_score"], 0.0)
        self.assertEqual(top["score"], 0.0)

    @staticmethod
    def _anchor_res():
        # 锚点行：is_exact=True，top1 型号 ABC-100 → 服务端 confirmed_series 收集前缀 abc
        return {
            "is_exact": True,
            "matched_code": "M3H-ANCHOR",
            "top_candidates": [{
                "code": "M3H-ANCHOR",
                "name": "锚点装置",
                "specification": "ABC-100 锚点装置",
                "category": "其它",
                "unit": "台",
                "brand": "",
                "description": "",
                "parsed_params": json.dumps({"model": "ABC-100", "attrs": {}}, ensure_ascii=False),
                "score": 90.0,
                "max_score": 1.0,
                "hard_blocked": False,
                "match_reasons": ["名称与规格完全一致"],
            }],
        }

    @staticmethod
    def _target_res():
        # 被测行：blocked 候选刻意同系列（ABC 前缀）且仅因 hard_blocked 被排除；
        # 正常候选 ABC-999 基础分 24.6 低于自身 cap 85，+5 分差可见
        common = {"name": "通用装置", "category": "其它", "unit": "台",
                  "brand": "", "description": ""}
        blocked = dict(common, **{
            "code": "M3H-SER-EX",
            "specification": "ABC-900 EXCL专用装置",
            "parsed_params": json.dumps({"model": "ABC-900", "attrs": {}}, ensure_ascii=False),
            "score": 0.0,
            "max_score": 0.0,
            "hard_blocked": True,
            "match_reasons": ["包含排除词 [EXCL]"],
        })
        normal = dict(common, **{
            "code": "M3H-SER-OK",
            "specification": "ABC-999 普通装置",
            "parsed_params": json.dumps({"model": "ABC-999", "attrs": {}}, ensure_ascii=False),
            "score": 24.6,
            "max_score": 0.85,
            "hard_blocked": False,
            "match_reasons": ["型号高度相似", "品类名称一致"],
        })
        return {"is_exact": False, "matched_code": None, "top_candidates": [blocked, normal]}

    def _match_item_side_effect(self, q_info, q_vector=None, *args, **kwargs):
        if "锚点装置" in str(q_info.get("name", "")):
            return self._anchor_res()
        return self._target_res()

    @staticmethod
    def _batch_rows(with_anchor):
        rows = []
        if with_anchor:
            rows.append({"id": 1, "text": "锚点装置 ABC-100", "unit": "台", "qty": 1,
                         "category": "其它", "professional": "暖通"})
        rows.append({"id": 2, "text": "被测装置 ABC-200", "unit": "台", "qty": 1,
                     "category": "其它", "professional": "暖通"})
        return rows

    def test_series_boost_never_revives_hard_blocked_over_http(self):
        # 真实 /api/batch_match 双运行：带锚点（confirmed_series=[abc]）vs 单跑（无系列）。
        # 覆盖 server 第二遍内存 boost：blocked 跳过 +5 保持双零；正常候选 +5 受 cap 收口。
        with patch("app.server.match_item", side_effect=self._match_item_side_effect):
            boosted = self.client.post(
                "/api/batch_match", json={"rows": self._batch_rows(with_anchor=True)},
                headers=self.auth,
            )
        self.assertEqual(boosted.status_code, 200, boosted.text)
        with patch("app.server.match_item", side_effect=self._match_item_side_effect):
            baseline = self.client.post(
                "/api/batch_match", json={"rows": self._batch_rows(with_anchor=False)},
                headers=self.auth,
            )
        self.assertEqual(baseline.status_code, 200, baseline.text)

        b_row = next(r for r in boosted.json()["results"] if r["original_row_idx"] == 2)
        base_row = next(r for r in baseline.json()["results"] if r["original_row_idx"] == 2)
        b_cands = {c["code"]: c for c in b_row["top_candidates"]}
        base_cands = {c["code"]: c for c in base_row["top_candidates"]}

        # blocked 候选：系列前置条件全满足（ABC 前缀），hard_blocked 守卫跳过 +5，双零不变
        blocked = b_cands["M3H-SER-EX"]
        self.assertTrue(blocked["hard_blocked"])
        self.assertEqual(blocked["score"], 0.0)
        self.assertEqual(blocked["max_score"], 0.0)
        self.assertNotIn("系列优先", blocked["match_reasons"])

        # 正常候选：确吃 +5（24.6→29.6），且不突破自身 0.85 不可逆上限
        self.assertAlmostEqual(
            b_cands["M3H-SER-OK"]["score"] - base_cands["M3H-SER-OK"]["score"],
            5.0, places=2,
        )
        self.assertLessEqual(
            b_cands["M3H-SER-OK"]["score"],
            b_cands["M3H-SER-OK"]["max_score"] * 100.0 + 1e-9,
        )

        # 全员无一突破各自上限；目标行仅为推荐，不产生可导出状态
        for r in (b_row, base_row):
            for c in r["top_candidates"]:
                self.assertLessEqual(c["score"], c["max_score"] * 100.0 + 1e-9)
            self.assertEqual(r["decision_status"], "recommended")

    def test_top1_hard_conflict_rejected_no_prebind_over_http(self):
        # top1 品牌冲突（候选品牌列空、真实值在 parsed_params）：服务端同源复核
        # 必须 rejected 且 bindings 为空，不预绑定
        def fake_match(q_info, q_vector=None, *args, **kwargs):
            cand = {
                "code": "M3H-TOP1",
                "name": "通用装置",
                "specification": "标准型",
                "category": "其它",
                "unit": "台",
                "brand": "",
                "description": "",
                "parsed_params": json.dumps(
                    {"attrs": {"品牌": "起帆"}}, ensure_ascii=False),
                "score": 50.0,
                "max_score": 1.0,
                "hard_blocked": False,
                "match_reasons": ["品类名称一致"],
            }
            return {"is_exact": False, "matched_code": None, "top_candidates": [cand]}

        rows = [{"id": 7, "text": "通用装置", "unit": "台", "qty": 1,
                 "category": "其它", "brand": "远东", "professional": "暖通"}]
        with patch("app.server.match_item", side_effect=fake_match):
            resp = self.client.post("/api/batch_match", json={"rows": rows}, headers=self.auth)
        self.assertEqual(resp.status_code, 200, resp.text)
        result = resp.json()["results"][0]
        self.assertEqual(result["decision_status"], "rejected")
        self.assertIn("品牌冲突阻断", result["decision_reason"])
        self.assertEqual(result["bindings"], [])


# ---------------------------------------------------------------------------
# 4. V015-M3 R02 审计复现四项 P1 端到端 HTTP 闭环（含恢复降级与 XLSX 重读）
# ---------------------------------------------------------------------------

class TestM3R02AuditCounterexamplesHttp(M3HttpBase):
    """覆盖 /api/match、/api/batch_match 约束规则、/api/binding/submit、/api/session/annotate、/api/export。"""

    SCOPE = "m3r02http.xlsx"

    def _write_rules_fixture(self, rules):
        with open(srv._get_auto_binding_rules_path(), "w", encoding="utf-8") as _f:
            json.dump(rules, _f, ensure_ascii=False, indent=4)

    def _set_constrained_rule(self, original_name, original_spec, professional, bound_mat, qty_ratio=1.0):
        self._write_rules_fixture([{
            "original_name": original_name,
            "original_spec": original_spec,
            "professional": professional,
            "bound_materials": [dict(bound_mat, qty_ratio=qty_ratio)],
        }])

    def _post_export(self, row, binds, status="manual_confirmed", decision_source="manual"):
        item = self._export_item(row, binds, status=status)
        item["decision_source"] = decision_source
        return self.client.post(
            "/api/export",
            json={"items": [item], "scope_filename": self.SCOPE},
            headers=self.auth,
        )

    def _post_annotate(self, row, binds, status="manual_confirmed", decision_source="manual"):
        item = self._export_item(row, binds, status=status)
        item["decision_source"] = decision_source
        return self.client.post(
            "/api/session/annotate",
            json={"items": [item], "scope_filename": self.SCOPE},
            headers=self.auth,
        )

    def test_r02_a_m3_01_unit_multiplier_km_to_m_and_control(self):
        self._seed(
            "AUDIT-UNIT-KM", "镀锌钢管", "DN15", "m", "暖通",
            parsed_params=json.dumps({"attrs": {"dn_val": 15}}, ensure_ascii=False),
        )
        bound_mat = {
            "code": "AUDIT-UNIT-KM", "name": "镀锌钢管", "specification": "DN15",
            "unit": "m", "brand": "", "category": "暖通",
        }

        # 1) 约束规则模式：即使 rule 自带 qty_ratio=1 或 qty_ratio=1000，非 1 倍率不得静默 auto_accepted
        for ratio in (1.0, 1000.0):
            self._set_constrained_rule("镀锌钢管 DN15", "DN15", "暖通", bound_mat, qty_ratio=ratio)
            batch_resp = self.client.post(
                "/api/batch_match",
                json={"rows": [{
                    "id": 1, "text": "镀锌钢管 DN15", "unit": "km", "qty": 10,
                    "category": "暖通", "professional": "暖通",
                }]},
                headers=self.auth,
            )
            self.assertEqual(batch_resp.status_code, 200, batch_resp.text)
            res = batch_resp.json()["results"][0]
            self.assertEqual(res["decision_status"], "rejected")
            self.assertIn("单位非1倍率", res["decision_reason"])

        row_km = self._row(name="镀锌钢管", specification="DN15", unit="km", qty=10)
        # 2) 原数照搬 (qty=10, qty_ratio=1) 人工确认 -> 409，导出 -> 422
        raw_binds = [self._bind(
            "AUDIT-UNIT-KM", name="镀锌钢管", specification="DN15",
            unit="m", category="暖通", qty=10, qty_ratio=1,
        )]
        c_raw = self.client.post(
            "/api/binding/submit", json=self._confirm_payload(row_km, raw_binds, self.SCOPE), headers=self.auth
        )
        self.assertEqual(c_raw.status_code, 409, c_raw.text)
        self.assertEqual(c_raw.json()["detail"]["error"], "binding_hard_conflict")
        self.assertEqual(self._post_export(row_km, raw_binds).status_code, 422)

        # 3) 显式换算后确认 (qty=10000, qty_ratio=1000, unit="m") -> 200，恢复维持确认，导出 200 且 XLSX 数量为 10000
        conv_binds = [self._bind(
            "AUDIT-UNIT-KM", name="镀锌钢管", specification="DN15",
            unit="m", category="暖通", qty=10000, qty_ratio=1000,
        )]
        c_ok = self.client.post(
            "/api/binding/submit", json=self._confirm_payload(row_km, conv_binds, self.SCOPE), headers=self.auth
        )
        self.assertEqual(c_ok.status_code, 200, c_ok.text)
        ann_ok = self._post_annotate(row_km, conv_binds)
        self.assertEqual(ann_ok.status_code, 200, ann_ok.text)
        self.assertEqual(ann_ok.json()["downgraded"], 0)
        self.assertEqual(ann_ok.json()["items"][0]["decision_status"], "manual_confirmed")

        exp_ok = self._post_export(row_km, conv_binds)
        self.assertEqual(exp_ok.status_code, 200, exp_ok.text)
        wb = openpyxl.load_workbook(io.BytesIO(exp_ok.content), data_only=True)
        ws_rows = list(wb.active.iter_rows(values_only=True))
        self.assertEqual(ws_rows[3][7], "km")
        self.assertEqual(float(ws_rows[3][8]), 10.0)
        self.assertEqual(ws_rows[4][4], "AUDIT-UNIT-KM")
        self.assertEqual(ws_rows[4][7], "m")
        self.assertEqual(float(ws_rows[4][8]), 10000.0)

        # 4) 确认后物料库 unit 从 m 漂移为 km：草稿恢复降级且导出 422
        conn = get_db_connection()
        conn.execute("UPDATE materials SET unit='km' WHERE code='AUDIT-UNIT-KM'")
        conn.commit()
        conn.close()
        ann_drift = self._post_annotate(row_km, conv_binds)
        self.assertEqual(ann_drift.status_code, 200, ann_drift.text)
        self.assertEqual(ann_drift.json()["items"][0]["decision_status"], "recommended")
        self.assertGreaterEqual(ann_drift.json()["downgraded"], 1)
        exp_drift = self._post_export(row_km, conv_binds)
        self.assertEqual(exp_drift.status_code, 422, exp_drift.text)

        # 5) 1 倍率正例对照 (10 米 -> 10 m)：约束规则 auto_accepted -> 导出 200，确认 200 -> 导出 200
        self._seed(
            "AUDIT-UNIT-CTRL", "镀锌钢管", "DN15", "m", "暖通",
            parsed_params=json.dumps({"attrs": {"dn_val": 15}}, ensure_ascii=False),
        )
        ctrl_mat = {
            "code": "AUDIT-UNIT-CTRL", "name": "镀锌钢管", "specification": "DN15",
            "unit": "m", "brand": "", "category": "暖通",
        }
        self._set_constrained_rule("镀锌钢管 DN15", "DN15", "暖通", ctrl_mat, qty_ratio=1.0)
        batch_ctrl = self.client.post(
            "/api/batch_match",
            json={"rows": [{
                "id": 2, "text": "镀锌钢管 DN15", "unit": "米", "qty": 10,
                "category": "暖通", "professional": "暖通",
            }]},
            headers=self.auth,
        )
        self.assertEqual(batch_ctrl.status_code, 200, batch_ctrl.text)
        ctrl_res = batch_ctrl.json()["results"][0]
        self.assertEqual(ctrl_res["decision_status"], "auto_accepted")
        exp_auto = self.client.post(
            "/api/export", json={"items": [ctrl_res], "scope_filename": self.SCOPE}, headers=self.auth
        )
        self.assertEqual(exp_auto.status_code, 200, exp_auto.text)

    def test_r02_a_m3_02_dynamic_exclusion_all_chains_and_restore(self):
        self._seed("AUDIT-DYNAMIC", "镀锌钢管", "DN15 304 EXCL专用", "米", "暖通")
        self._seed("AUDIT-DYNAMIC-MISS", "镀锌钢管", "DN15 普通", "米", "暖通")

        # 无动态规则时先确认 AUDIT-DYNAMIC 一次，随后再启用动态排除规则，检验恢复降级+导出 422
        row = self._row(name="镀锌钢管", specification="DN15 304", unit="米")
        binds_excl = [self._bind("AUDIT-DYNAMIC", name="镀锌钢管", specification="DN15 304 EXCL专用", unit="米")]
        c_pre = self.client.post(
            "/api/binding/submit", json=self._confirm_payload(row, binds_excl, self.SCOPE), headers=self.auth
        )
        self.assertEqual(c_pre.status_code, 200, c_pre.text)

        # 启用动态规则：must_not_contain=["EXCL"] & must_contain=["304"]
        LLMRuleManager._active_rules_cache = [
            self._dynamic_rule({"must_not_contain": ["EXCL"], "must_contain": ["304"]}, rule_id="r02h-dyn")
        ]

        # /api/match 推荐链路双归零不可逆
        cand_stub = {
            "code": "AUDIT-DYNAMIC", "name": "镀锌钢管", "specification": "DN15 304 EXCL专用",
            "category": "暖通", "unit": "米", "brand": "", "description": "", "parsed_params": "", "semantic_score": 0.0,
        }
        with patch("app.matcher.hybrid_recall", return_value=[cand_stub]):
            m_resp = self.client.post(
                "/api/match",
                json={"name": "镀锌钢管", "text": "DN15 304", "category": "暖通", "professional": "暖通", "unit": "米"},
                headers=self.auth,
            )
        self.assertEqual(m_resp.status_code, 200, m_resp.text)
        top = m_resp.json()["top_candidates"][0]
        self.assertTrue(top["hard_blocked"])
        self.assertEqual(top["score"], 0.0)
        self.assertEqual(top["max_score"], 0.0)

        # 草稿恢复因新规则生效降为 recommended；导出被 422 阻断
        ann = self._post_annotate(row, binds_excl)
        self.assertEqual(ann.status_code, 200, ann.text)
        self.assertEqual(ann.json()["items"][0]["decision_status"], "recommended")
        self.assertGreaterEqual(ann.json()["downgraded"], 1)
        exp = self._post_export(row, binds_excl)
        self.assertEqual(exp.status_code, 422, exp.text)
        self.assertIn("动态规则硬排除", str(exp.json()["detail"]))

        # 重新人工确认 (EXCL 和 缺失304) -> 409；约束规则 /api/batch_match -> rejected
        for code, spec, expected_substr in (
            ("AUDIT-DYNAMIC", "DN15 304 EXCL专用", "包含排除词 [EXCL]"),
            ("AUDIT-DYNAMIC-MISS", "DN15 普通", "缺少必含词 [304]"),
        ):
            binds = [self._bind(code, name="镀锌钢管", specification=spec, unit="米")]
            sub = self.client.post(
                "/api/binding/submit", json=self._confirm_payload(row, binds, self.SCOPE), headers=self.auth
            )
            self.assertEqual(sub.status_code, 409, sub.text)
            self.assertIn(expected_substr, str(sub.json()["detail"]))

            self._set_constrained_rule(
                "镀锌钢管 DN15 304", "DN15 304", "暖通",
                {"code": code, "name": "镀锌钢管", "specification": spec, "unit": "米", "brand": "", "category": "暖通"},
            )
            bm = self.client.post(
                "/api/batch_match",
                json={"rows": [{"id": 3, "text": "镀锌钢管 DN15 304", "unit": "米", "qty": 10, "category": "暖通", "professional": "暖通"}]},
                headers=self.auth,
            )
            self.assertEqual(bm.status_code, 200, bm.text)
            bm_res = bm.json()["results"][0]
            self.assertEqual(bm_res["decision_status"], "rejected")
            self.assertIn("动态规则硬排除", bm_res["decision_reason"])
            self.assertIn(expected_substr, bm_res["decision_reason"])

    def test_r02_a_m3_03_empty_and_bad_parsed_params_dn_and_grade(self):
        self._seed("AUDIT-DN-EMPTY", "镀锌钢管", "DN20", "米", "暖通", parsed_params="")
        self._seed("AUDIT-DN-BAD", "镀锌钢管", "DN20", "米", "暖通", parsed_params="{badjson")
        self._seed(
            "AUDIT-DN-CONTROL", "镀锌钢管", "DN20", "米", "暖通",
            parsed_params=json.dumps({"attrs": {"dn_val": 20}}, ensure_ascii=False),
        )
        self._seed("AUDIT-DN-OK", "镀锌钢管", "DN15", "米", "暖通", parsed_params="")

        row_dn = self._row(name="镀锌钢管", specification="DN15", unit="米")
        for code in ("AUDIT-DN-EMPTY", "AUDIT-DN-BAD", "AUDIT-DN-CONTROL"):
            binds = [self._bind(code, name="镀锌钢管", specification="DN20", unit="米")]
            self.assertEqual(
                self.client.post(
                    "/api/binding/submit", json=self._confirm_payload(row_dn, binds, self.SCOPE), headers=self.auth
                ).status_code,
                409,
            )
            self.assertEqual(self._post_export(row_dn, binds).status_code, 422)

            self._set_constrained_rule(
                "镀锌钢管 DN15", "DN15", "暖通",
                {"code": code, "name": "镀锌钢管", "specification": "DN20", "unit": "米", "brand": "", "category": "暖通"},
            )
            bm = self.client.post(
                "/api/batch_match",
                json={"rows": [{"id": 4, "text": "镀锌钢管 DN15", "unit": "米", "qty": 10, "category": "暖通", "professional": "暖通"}]},
                headers=self.auth,
            )
            bm_res = bm.json()["results"][0]
            self.assertEqual(bm_res["decision_status"], "rejected")
            self.assertIn("管径口径冲突阻断", bm_res["decision_reason"])

        # 同规格空缓存正例 200 + 导出 200
        ok_dn_binds = [self._bind("AUDIT-DN-OK", name="镀锌钢管", specification="DN15", unit="米")]
        self.assertEqual(
            self.client.post(
                "/api/binding/submit", json=self._confirm_payload(row_dn, ok_dn_binds, self.SCOPE), headers=self.auth
            ).status_code,
            200,
        )
        self.assertEqual(self._post_export(row_dn, ok_dn_binds).status_code, 200)

        # 304 vs 316L 空缓存反例与 06Cr19Ni10 正例对照
        self._seed("AUDIT-GRADE-EMPTY", "不锈钢管", "316L 不锈钢管", "米", "暖通", parsed_params="")
        self._seed("AUDIT-GRADE-OK", "不锈钢管", "06Cr19Ni10 不锈钢管", "米", "暖通", parsed_params="")
        row_grade = self._row(name="不锈钢管", specification="304", unit="米")
        bad_g_binds = [self._bind("AUDIT-GRADE-EMPTY", name="不锈钢管", specification="316L 不锈钢管", unit="米")]
        sub_g = self.client.post(
            "/api/binding/submit", json=self._confirm_payload(row_grade, bad_g_binds, self.SCOPE), headers=self.auth
        )
        self.assertEqual(sub_g.status_code, 409, sub_g.text)
        self.assertIn("不锈钢牌号冲突硬隔离(304/316L)", str(sub_g.json()["detail"]))
        self.assertEqual(self._post_export(row_grade, bad_g_binds).status_code, 422)

        self._set_constrained_rule(
            "不锈钢管 304", "304", "暖通",
            {"code": "AUDIT-GRADE-EMPTY", "name": "不锈钢管", "specification": "316L 不锈钢管", "unit": "米", "brand": "", "category": "暖通"},
        )
        bm_g = self.client.post(
            "/api/batch_match",
            json={"rows": [{"id": 5, "text": "不锈钢管 304", "unit": "米", "qty": 10, "category": "暖通", "professional": "暖通"}]},
            headers=self.auth,
        )
        self.assertEqual(bm_g.json()["results"][0]["decision_status"], "rejected")
        self.assertIn("不锈钢牌号冲突硬隔离(304/316L)", bm_g.json()["results"][0]["decision_reason"])

        ok_g_binds = [self._bind("AUDIT-GRADE-OK", name="不锈钢管", specification="06Cr19Ni10 不锈钢管", unit="米")]
        self.assertEqual(
            self.client.post(
                "/api/binding/submit", json=self._confirm_payload(row_grade, ok_g_binds, self.SCOPE), headers=self.auth
            ).status_code,
            200,
        )
        self.assertEqual(self._post_export(row_grade, ok_g_binds).status_code, 200)

    def test_r02_a_m3_04_pump_bidirectional_and_drift_restore(self):
        self._seed("AUDIT-PUMP-REVERSE", "风机盘管", "不带提升泵", "台", "暖通", parsed_params="")
        self._seed("AUDIT-PUMP-POS", "风机盘管", "带提升泵", "台", "暖通", parsed_params="")

        # /api/match 推荐层反向（要求带泵 vs 不带泵）压到 0.15 cap
        stub_neg = {
            "code": "AUDIT-PUMP-REVERSE", "name": "风机盘管", "specification": "不带提升泵",
            "category": "暖通", "unit": "台", "brand": "", "description": "", "parsed_params": "", "semantic_score": 0.0,
        }
        with patch("app.matcher.hybrid_recall", return_value=[stub_neg]):
            m_rev = self.client.post(
                "/api/match",
                json={"name": "风机盘管", "text": "带提升泵", "category": "暖通", "professional": "暖通", "unit": "台"},
                headers=self.auth,
            )
        self.assertEqual(m_rev.status_code, 200, m_rev.text)
        top_rev = m_rev.json()["top_candidates"][0]
        self.assertFalse(top_rev["hard_blocked"])
        self.assertAlmostEqual(top_rev["max_score"], 0.15, places=6)
        self.assertLessEqual(top_rev["score"], 15.0 + 1e-6)

        # 确认 409 + 规则 rejected + 导出 422
        row_req_pump = self._row(name="风机盘管", specification="带提升泵", unit="台")
        neg_binds = [self._bind("AUDIT-PUMP-REVERSE", name="风机盘管", specification="不带提升泵", unit="台")]
        sub_rev = self.client.post(
            "/api/binding/submit", json=self._confirm_payload(row_req_pump, neg_binds, self.SCOPE), headers=self.auth
        )
        self.assertEqual(sub_rev.status_code, 409, sub_rev.text)
        self.assertIn("提升泵要求冲突硬隔离(带泵/不带泵)", str(sub_rev.json()["detail"]))
        self.assertEqual(self._post_export(row_req_pump, neg_binds).status_code, 422)

        self._set_constrained_rule(
            "风机盘管 带提升泵", "带提升泵", "暖通",
            {"code": "AUDIT-PUMP-REVERSE", "name": "风机盘管", "specification": "不带提升泵", "unit": "台", "brand": "", "category": "暖通"},
        )
        bm_rev = self.client.post(
            "/api/batch_match",
            json={"rows": [{"id": 6, "text": "风机盘管 带提升泵", "unit": "台", "qty": 10, "category": "暖通", "professional": "暖通"}]},
            headers=self.auth,
        )
        self.assertEqual(bm_rev.json()["results"][0]["decision_status"], "rejected")
        self.assertIn("提升泵要求冲突硬隔离(带泵/不带泵)", bm_rev.json()["results"][0]["decision_reason"])

        # 正向对照 + DB 规格漂移为无泵后的恢复降级 & 导出 422
        pos_binds = [self._bind("AUDIT-PUMP-POS", name="风机盘管", specification="带提升泵", unit="台")]
        self.assertEqual(
            self.client.post(
                "/api/binding/submit", json=self._confirm_payload(row_req_pump, pos_binds, self.SCOPE), headers=self.auth
            ).status_code,
            200,
        )
        self.assertEqual(self._post_export(row_req_pump, pos_binds).status_code, 200)

        conn = get_db_connection()
        conn.execute("UPDATE materials SET specification='无泵' WHERE code='AUDIT-PUMP-POS'")
        conn.commit()
        conn.close()
        ann_p_drift = self._post_annotate(row_req_pump, pos_binds)
        self.assertEqual(ann_p_drift.status_code, 200, ann_p_drift.text)
        self.assertEqual(ann_p_drift.json()["items"][0]["decision_status"], "recommended")
        self.assertGreaterEqual(ann_p_drift.json()["downgraded"], 1)
        exp_p_drift = self._post_export(row_req_pump, pos_binds)
        self.assertEqual(exp_p_drift.status_code, 422, exp_p_drift.text)
        self.assertIn("提升泵要求冲突硬隔离(带泵/不带泵)", str(exp_p_drift.json()["detail"]))

        # 正向（不带提升泵 查询 vs 带提升泵 候选）：/api/match 同等压到 0.15 cap，确认 409
        stub_pos = {
            "code": "AUDIT-PUMP-FWD", "name": "风机盘管", "specification": "带提升泵",
            "category": "暖通", "unit": "台", "brand": "", "description": "", "parsed_params": "", "semantic_score": 0.0,
        }
        with patch("app.matcher.hybrid_recall", return_value=[stub_pos]):
            m_fwd = self.client.post(
                "/api/match",
                json={"name": "风机盘管", "text": "不带提升泵", "category": "暖通", "professional": "暖通", "unit": "台"},
                headers=self.auth,
            )
        self.assertEqual(m_fwd.status_code, 200, m_fwd.text)
        top_fwd = m_fwd.json()["top_candidates"][0]
        self.assertFalse(top_fwd["hard_blocked"])
        self.assertAlmostEqual(top_fwd["max_score"], 0.15, places=6)
        self.assertLessEqual(top_fwd["score"], 15.0 + 1e-6)
        row_no_pump = self._row(name="风机盘管", specification="不带提升泵", unit="台")
        self._seed("AUDIT-PUMP-FWD", "风机盘管", "带提升泵", "台", "暖通", parsed_params="")
        fwd_binds = [self._bind("AUDIT-PUMP-FWD", name="风机盘管", specification="带提升泵", unit="台")]
        sub_fwd = self.client.post(
            "/api/binding/submit", json=self._confirm_payload(row_no_pump, fwd_binds, self.SCOPE), headers=self.auth
        )
        self.assertEqual(sub_fwd.status_code, 409, sub_fwd.text)
        self.assertIn("提升泵否定冲突硬隔离(不带泵/带泵)", str(sub_fwd.json()["detail"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# 6. V015-M3 R03：A-M3-01 / A-M3-02 / A-M3-05 审计反例端到端 HTTP 闭环验证
# ---------------------------------------------------------------------------

class TestM3R03AuditClosureHttp(M3HttpBase):
    """将 R02 审核反例全部固化为真实 FastAPI HTTP 闭环断言，并验证修复后恢复与正例保留。"""

    def _seed_llm_rule(
        self,
        rule_id: str,
        scope_level: str,
        scope_target: str,
        rule_obj: dict,
        priority: int = 1,
        status: str = "published",
    ):
        from app.llm_rule_engine import init_rule_engine_db, get_db_conn, compute_rule_content_digest

        init_rule_engine_db()
        rule_json_str = json.dumps(rule_obj, ensure_ascii=False)
        digest = compute_rule_content_digest(scope_level, scope_target, scope_target, priority, rule_json_str)
        conn = get_db_conn()
        try:
            conn.execute("DELETE FROM llm_rules WHERE rule_id = ?", (rule_id,))
            conn.execute(
                """
                INSERT INTO llm_rules (
                    rule_id, rule_name, scope_level, scope_target, discipline,
                    rule_json, author_id, author_name, approver_id, approver_name,
                    publisher_id, publisher_name, revision, content_digest,
                    approved_revision, approved_digest, priority, status
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 'emp_author', 2, 'mgr_approver', 3, 'adm_publisher', 1, ?, 1, ?, ?, ?)
                """,
                (
                    rule_id,
                    rule_id,
                    scope_level,
                    scope_target,
                    scope_target,
                    rule_json_str,
                    digest,
                    digest,
                    priority,
                    status,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        LLMRuleManager.clear_active_cache()

    def _clear_llm_rules(self):
        from app.llm_rule_engine import init_rule_engine_db, get_db_conn

        init_rule_engine_db()
        conn = get_db_conn()
        try:
            conn.execute("DELETE FROM llm_rules")
            conn.commit()
        finally:
            conn.close()
        LLMRuleManager.clear_active_cache()

    def test_r03_a_m3_01_empty_target_unit_drift_and_legacy_records_blocked(self):
        import io
        import openpyxl

        self._seed("CONC-M", "镀锌钢管", "DN100", "m", "暖通")
        row = self._row(
            name="镀锌钢管", specification="DN100", qty=10, unit="km",
            professional="暖通", category="暖通",
        )

        # 1) 缺 target unit 或 unit="" 的非1倍率确认 -> 一律拒绝 (缺键 422 / 空串 409)
        for bad_unit_kw, expected_codes in (({}, (409, 422)), ({"unit": ""}, (409,))):
            bad_bind = {
                "code": "CONC-M", "name": "镀锌钢管", "specification": "DN100",
                "category": "暖通", "brand": "", "qty": 10000.0, "qty_ratio": 1000.0,
            }
            bad_bind.update(bad_unit_kw)
            resp_bad = self.client.post(
                "/api/binding/submit",
                json=self._confirm_payload(row, [bad_bind], scope="r03_u1.xlsx"),
                headers=self.auth,
            )
            self.assertIn(resp_bad.status_code, expected_codes, resp_bad.text)

        # 2) 显式 target unit="m" 确认 -> 200，导出 XLSX 读回明确写出 10 km -> 10000 m
        good_bind = self._bind(
            "CONC-M", name="镀锌钢管", specification="DN100",
            unit="m", category="暖通", qty=10000.0, qty_ratio=1000.0,
        )
        ok_confirm = self.client.post(
            "/api/binding/submit",
            json=self._confirm_payload(row, [good_bind], scope="r03_u1.xlsx"),
            headers=self.auth,
        )
        self.assertEqual(ok_confirm.status_code, 200, ok_confirm.text)

        item = self._export_item(row, [good_bind], status="manual_confirmed")
        ok_exp = self.client.post(
            "/api/export", json={"items": [item], "scope_filename": "r03_u1.xlsx"}, headers=self.auth
        )
        self.assertEqual(ok_exp.status_code, 200, ok_exp.text)
        wb = openpyxl.load_workbook(io.BytesIO(ok_exp.content), data_only=True)
        ws = wb.active
        self.assertEqual(ws.cell(4, 8).value, "km")
        self.assertEqual(ws.cell(4, 9).value, 10)
        self.assertEqual(ws.cell(5, 8).value, "m")
        self.assertEqual(ws.cell(5, 9).value, 10000)
        wb.close()

        # 3) 确认后物料库 unit 从 "m" 漂移成 "km"：保留 m / 改写 km / 删除 unit 全部阻断
        conn = get_db_connection()
        conn.execute("UPDATE materials SET unit = 'km' WHERE code = 'CONC-M'")
        conn.commit()
        conn.close()

        drift_variants = [
            good_bind,
            dict(good_bind, unit="km"),
            {k: v for k, v in good_bind.items() if k != "unit"},
        ]
        for mutated_bind in drift_variants:
            mutated_item = self._export_item(row, [mutated_bind], status="manual_confirmed")
            rest = self.client.post(
                "/api/session/annotate",
                json={"items": [mutated_item], "scope_filename": "r03_u1.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(rest.status_code, 200, rest.text)
            self.assertEqual(rest.json()["items"][0]["decision_status"], "recommended", rest.text)
            self.assertGreaterEqual(rest.json()["downgraded"], 1)

            exp_blk = self.client.post(
                "/api/export",
                json={"items": [mutated_item], "scope_filename": "r03_u1.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(exp_blk.status_code, 422, exp_blk.text)

        # 4) 库恢复回 "m"，但客户端删掉或篡改 target unit -> 仍阻断；还原 unit="m" 后恢复 200
        conn = get_db_connection()
        conn.execute("UPDATE materials SET unit = 'm' WHERE code = 'CONC-M'")
        conn.commit()
        conn.close()
        for bad_client_bind in (
            {k: v for k, v in good_bind.items() if k != "unit"},
            dict(good_bind, unit=""),
            dict(good_bind, unit="km"),
        ):
            bad_item = self._export_item(row, [bad_client_bind], status="manual_confirmed")
            rest_bad = self.client.post(
                "/api/session/annotate",
                json={"items": [bad_item], "scope_filename": "r03_u1.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(rest_bad.json()["items"][0]["decision_status"], "recommended")
            exp_bad = self.client.post(
                "/api/export",
                json={"items": [bad_item], "scope_filename": "r03_u1.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(exp_bad.status_code, 422, exp_bad.text)

        exp_recovered = self.client.post(
            "/api/export", json={"items": [item], "scope_filename": "r03_u1.xlsx"}, headers=self.auth
        )
        self.assertEqual(exp_recovered.status_code, 200, exp_recovered.text)

        # 5) 直接向库写入遗留无权威单位证据的历史确认记录 -> 恢复降推荐 + 导出 422
        from app.auth_manager import get_auth_connection
        from app.binding_decisions import row_content_fingerprint, make_scope_key

        legacy_row_fp = row_content_fingerprint(row)
        scope_key = make_scope_key("r03_legacy.xlsx", row["professional"])
        legacy_payload = json.dumps({
            "row": row,
            "bindings": [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0}],
        }, ensure_ascii=False)
        aconn = get_auth_connection()
        try:
            aconn.execute(
                """
                INSERT INTO binding_decisions (
                    user_id, scope_key, row_fingerprint, binding_fingerprint,
                    decision, source, reason, matched_codes, payload_json
                ) VALUES (1, ?, ?, ?, 'manual_confirmed', 'manual', 'legacy', 'CONC-M', ?)
                """,
                (
                    scope_key,
                    legacy_row_fp,
                    self.client.post(
                        "/api/session/annotate",
                        json={"items": [self._export_item(row, [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0}], status="recommended")], "scope_filename": "r03_legacy.xlsx"},
                        headers=self.auth,
                    ).json()["items"][0]["binding_fingerprint"],
                    legacy_payload,
                ),
            )
            aconn.commit()
        finally:
            aconn.close()

        for legacy_binds in (
            [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": ""}],
            [good_bind],
        ):
            legacy_item = self._export_item(row, legacy_binds, status="manual_confirmed")
            rest_legacy = self.client.post(
                "/api/session/annotate",
                json={"items": [legacy_item], "scope_filename": "r03_legacy.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(rest_legacy.json()["items"][0]["decision_status"], "recommended", rest_legacy.text)
            self.assertGreaterEqual(rest_legacy.json()["downgraded"], 1)
            exp_legacy = self.client.post(
                "/api/export",
                json={"items": [legacy_item], "scope_filename": "r03_legacy.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(exp_legacy.status_code, 422, exp_legacy.text)

    def test_r03_a_m3_02_rule_db_failure_fails_closed_on_real_http_endpoints_and_recovers(self):
        from app.llm_rule_engine import init_rule_engine_db, get_db_conn

        init_rule_engine_db()
        self._seed("OK-PIPE", "镀锌钢管", "DN100 常规", "米", "暖通")
        self._seed("EX-PIPE", "镀锌钢管", "DN100 禁止词", "米", "暖通")
        row = self._row(
            name="镀锌钢管", specification="DN100", qty=10, unit="米",
            professional="暖通", category="暖通",
        )
        ok_bind = self._bind(
            "OK-PIPE", name="镀锌钢管", specification="DN100 常规",
            unit="米", category="暖通", qty=10, qty_ratio=1.0,
        )
        ex_bind = self._bind(
            "EX-PIPE", name="镀锌钢管", specification="DN100 禁止词",
            unit="米", category="暖通", qty=10, qty_ratio=1.0,
        )

        # 配置带约束自动绑定规则，验证规则库故障时不会被自动接受放行
        with open(srv._get_auto_binding_rules_path(), "w", encoding="utf-8") as _f:
            json.dump([{
                "original_name": "镀锌钢管",
                "original_spec": "镀锌钢管",
                "original_unit": "米",
                "bound_materials": [{"code": "OK-PIPE", "specification": "DN100 常规", "qty_ratio": 1.0}],
            }], _f, ensure_ascii=False, indent=4)

        schema_broken = False
        try:
            # 1) 真实写库 approved 规则生效：EX-PIPE 被 409，OK-PIPE 200 确认
            self._seed_llm_rule(
                "r03-excl-rule", "discipline", "暖通",
                {"matching_rules": {"must_not_contain": ["禁止词"]}},
            )
            r_ex = self.client.post(
                "/api/binding/submit",
                json=self._confirm_payload(row, [ex_bind], scope="r03_m2.xlsx"),
                headers=self.auth,
            )
            self.assertEqual(r_ex.status_code, 409, r_ex.text)

            r_ok_pre = self.client.post(
                "/api/binding/submit",
                json=self._confirm_payload(row, [ok_bind], scope="r03_m2.xlsx"),
                headers=self.auth,
            )
            self.assertEqual(r_ok_pre.status_code, 200, r_ok_pre.text)

            # 2) 制造真实 SQL 读取失败（破坏 priority 列），清空缓存 -> 全链路 fail-closed
            conn = get_db_conn()
            conn.execute("ALTER TABLE llm_rules RENAME COLUMN priority TO priority_broken")
            conn.commit()
            conn.close()
            schema_broken = True
            LLMRuleManager.clear_active_cache()

            m_resp = self.client.post(
                "/api/match",
                json={"text": "镀锌钢管 DN100", "name": "镀锌钢管", "professional": "暖通", "category": "暖通", "unit": "米"},
                headers=self.auth,
            )
            self.assertEqual(m_resp.status_code, 200, m_resp.text)
            cands = m_resp.json().get("top_candidates", [])
            self.assertTrue(cands, m_resp.text)
            self.assertTrue(all(c["score"] == 0.0 and c.get("hard_blocked") for c in cands), cands)

            bm_resp = self.client.post(
                "/api/batch_match",
                json={"rows": [{"id": 0, "text": "镀锌钢管", "unit": "米", "qty": 10, "category": "暖通", "professional": "暖通"}]},
                headers=self.auth,
            )
            self.assertEqual(bm_resp.status_code, 200, bm_resp.text)
            bm_row = bm_resp.json()["results"][0]
            self.assertEqual(bm_row["decision_status"], "rejected", bm_resp.text)
            self.assertIn("动态规则不可用", bm_row["decision_reason"], bm_resp.text)
            self.assertTrue(
                all(c["score"] == 0.0 and c.get("hard_blocked") for c in bm_row["top_candidates"]),
                bm_resp.text,
            )

            c_fail = self.client.post(
                "/api/binding/submit",
                json=self._confirm_payload(row, [ok_bind], scope="r03_m2_new.xlsx"),
                headers=self.auth,
            )
            self.assertEqual(c_fail.status_code, 409, c_fail.text)
            self.assertIn("动态规则不可用", c_fail.text)

            item_confirmed = self._export_item(row, [ok_bind], status="manual_confirmed")
            rest_fail = self.client.post(
                "/api/session/annotate",
                json={"items": [item_confirmed], "scope_filename": "r03_m2.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(rest_fail.status_code, 200, rest_fail.text)
            self.assertEqual(rest_fail.json()["items"][0]["decision_status"], "recommended", rest_fail.text)
            self.assertGreaterEqual(rest_fail.json()["downgraded"], 1)

            exp_fail = self.client.post(
                "/api/export",
                json={"items": [item_confirmed], "scope_filename": "r03_m2.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(exp_fail.status_code, 422, exp_fail.text)

            # 3) 修复数据库列结构 -> 自动恢复可确认、可恢复、可导出
            conn = get_db_conn()
            conn.execute("ALTER TABLE llm_rules RENAME COLUMN priority_broken TO priority")
            conn.commit()
            conn.close()
            schema_broken = False
            LLMRuleManager.clear_active_cache()

            rest_ok = self.client.post(
                "/api/session/annotate",
                json={"items": [item_confirmed], "scope_filename": "r03_m2.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(rest_ok.json()["items"][0]["decision_status"], "manual_confirmed", rest_ok.text)
            exp_ok = self.client.post(
                "/api/export",
                json={"items": [item_confirmed], "scope_filename": "r03_m2.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(exp_ok.status_code, 200, exp_ok.text)
        finally:
            if schema_broken:
                conn = get_db_conn()
                try:
                    conn.execute("ALTER TABLE llm_rules RENAME COLUMN priority_broken TO priority")
                    conn.commit()
                finally:
                    conn.close()
            self._clear_llm_rules()

    def test_r03_a_m3_05_plumbing_rule_does_not_leak_to_hvac_http_e2e_and_universal_works(self):
        self._seed("FAN-HVAC", "涡轮排风机R03", "FX-100 禁止特征", "台", "暖通")
        self._seed("PUMP-PLUMB", "高扬程泵R03", "Q=10 禁止特征", "台", "给排水")

        with open(srv._get_auto_binding_rules_path(), "w", encoding="utf-8") as _f:
            json.dump([
                {
                    "original_name": "涡轮排风机R03",
                    "original_spec": "涡轮排风机R03",
                    "original_unit": "台",
                    "bound_materials": [{"code": "FAN-HVAC", "specification": "FX-100 禁止特征", "qty_ratio": 1.0}],
                },
                {
                    "original_name": "高扬程泵R03",
                    "original_spec": "高扬程泵R03",
                    "original_unit": "台",
                    "bound_materials": [{"code": "PUMP-PLUMB", "specification": "Q=10 禁止特征", "qty_ratio": 1.0}],
                },
            ], _f, ensure_ascii=False, indent=4)

        hvac_row = self._row(
            name="涡轮排风机R03", specification="FX-100", qty=2, unit="台",
            professional="暖通", category="暖通",
        )
        hvac_bind = self._bind(
            "FAN-HVAC", name="涡轮排风机R03", specification="FX-100 禁止特征",
            unit="台", category="暖通", qty=2, qty_ratio=1.0,
        )
        plumb_row = self._row(
            name="高扬程泵R03", specification="Q=10", qty=1, unit="台",
            professional="给排水", category="给排水",
        )
        plumb_bind = self._bind(
            "PUMP-PLUMB", name="高扬程泵R03", specification="Q=10 禁止特征",
            unit="台", category="给排水", qty=1, qty_ratio=1.0,
        )

        try:
            # 给排水专用规则不得影响暖通行
            self._seed_llm_rule(
                "r03-plumb-excl", "discipline", "给排水",
                {"matching_rules": {"must_not_contain": ["禁止特征"]}},
            )

            bm_hvac = self.client.post(
                "/api/batch_match",
                json={"rows": [{"id": 0, "text": "涡轮排风机R03", "unit": "台", "qty": 2, "category": "暖通", "professional": "暖通"}]},
                headers=self.auth,
            )
            self.assertEqual(bm_hvac.status_code, 200, bm_hvac.text)
            self.assertEqual(bm_hvac.json()["results"][0]["decision_status"], "auto_accepted", bm_hvac.text)

            c_hvac = self.client.post(
                "/api/binding/submit",
                json=self._confirm_payload(hvac_row, [hvac_bind], scope="r03_scope.xlsx"),
                headers=self.auth,
            )
            self.assertEqual(c_hvac.status_code, 200, c_hvac.text)

            exp_hvac = self.client.post(
                "/api/export",
                json={"items": [self._export_item(hvac_row, [hvac_bind], status="manual_confirmed")], "scope_filename": "r03_scope.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(exp_hvac.status_code, 200, exp_hvac.text)

            # 同规则对目标专业“给排水”四链路全部有效拦截
            bm_plumb = self.client.post(
                "/api/batch_match",
                json={"rows": [{"id": 0, "text": "高扬程泵R03", "unit": "台", "qty": 1, "category": "给排水", "professional": "给排水"}]},
                headers=self.auth,
            )
            bm_p_row = bm_plumb.json()["results"][0]
            self.assertEqual(bm_p_row["decision_status"], "rejected", bm_plumb.text)
            self.assertIn("动态规则", bm_p_row["decision_reason"], bm_plumb.text)
            self.assertTrue(
                all(
                    c["score"] == 0.0 and c.get("hard_blocked")
                    for c in bm_p_row["top_candidates"]
                    if c["code"] == "PUMP-PLUMB" or "禁止特征" in c.get("specification", "")
                ),
                bm_plumb.text,
            )

            c_plumb = self.client.post(
                "/api/binding/submit",
                json=self._confirm_payload(plumb_row, [plumb_bind], scope="r03_scope.xlsx"),
                headers=self.auth,
            )
            self.assertEqual(c_plumb.status_code, 409, c_plumb.text)

            exp_plumb = self.client.post(
                "/api/export",
                json={"items": [self._export_item(plumb_row, [plumb_bind], status="auto_accepted")], "scope_filename": "r03_scope.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(exp_plumb.status_code, 422, exp_plumb.text)

            # 切换为显式通用规则("通用") -> 暖通同样被拦截
            self._seed_llm_rule(
                "r03-plumb-excl", "discipline", "通用",
                {"matching_rules": {"must_not_contain": ["禁止特征"]}},
            )
            c_hvac_univ = self.client.post(
                "/api/binding/submit",
                json=self._confirm_payload(hvac_row, [hvac_bind], scope="r03_scope_u.xlsx"),
                headers=self.auth,
            )
            self.assertEqual(c_hvac_univ.status_code, 409, c_hvac_univ.text)
            bm_hvac_univ = self.client.post(
                "/api/batch_match",
                json={"rows": [{"id": 0, "text": "涡轮排风机R03", "unit": "台", "qty": 2, "category": "暖通", "professional": "暖通"}]},
                headers=self.auth,
            )
            bm_hu_row = bm_hvac_univ.json()["results"][0]
            self.assertEqual(bm_hu_row["decision_status"], "rejected", bm_hvac_univ.text)
            self.assertIn("动态规则", bm_hu_row["decision_reason"], bm_hvac_univ.text)
            self.assertTrue(
                all(
                    c["score"] == 0.0 and c.get("hard_blocked")
                    for c in bm_hu_row["top_candidates"]
                    if c["code"] == "FAN-HVAC" or "禁止特征" in c.get("specification", "")
                ),
                bm_hvac_univ.text,
            )
            exp_hvac_univ = self.client.post(
                "/api/export",
                json={"items": [self._export_item(hvac_row, [hvac_bind], status="manual_confirmed")], "scope_filename": "r03_scope.xlsx"},
                headers=self.auth,
            )
            self.assertEqual(exp_hvac_univ.status_code, 422, exp_hvac_univ.text)
        finally:
            self._clear_llm_rules()


# ---------------------------------------------------------------------------
# 7. V015-M3 R04：A-M3-01 历史非空客户端单位/残缺/伪造证据端到端 HTTP 闭环验证
# ---------------------------------------------------------------------------

class TestM3R04LegacyUnitEvidenceHttp(M3HttpBase):
    """复现并封闭历史仅带客户端 unit（无服务端权威 confirmed_* 上下文）绕过恢复与导出的 P1 链路。"""

    def _insert_raw_confirmed_decision(self, scope_name: str, row: dict, saved_bindings: list, codes: str = "CONC-M"):
        from app.auth_manager import get_auth_connection
        from app.binding_decisions import row_content_fingerprint, binding_fingerprint, make_scope_key

        scope_key = make_scope_key(scope_name, row.get("professional") or "暖通")
        row_fp = row_content_fingerprint(row)
        bind_fp = binding_fingerprint(saved_bindings)
        payload = json.dumps({"row": row, "bindings": saved_bindings}, ensure_ascii=False)
        aconn = get_auth_connection()
        try:
            aconn.execute(
                "DELETE FROM binding_decisions WHERE user_id = 1 AND scope_key = ? AND row_fingerprint = ?",
                (scope_key, row_fp),
            )
            aconn.execute(
                """
                INSERT INTO binding_decisions (
                    user_id, scope_key, row_fingerprint, binding_fingerprint,
                    decision, source, reason, matched_codes, payload_json
                ) VALUES (1, ?, ?, ?, 'manual_confirmed', 'manual', 'legacy_or_partial', ?, ?)
                """,
                (scope_key, row_fp, bind_fp, codes, payload),
            )
            aconn.commit()
        finally:
            aconn.close()

    def test_r04_legacy_nonempty_client_unit_and_partial_evidence_blocked_until_reconfirmed(self):
        import io
        import openpyxl

        self._seed("CONC-M", "镀锌钢管", "DN100", "m", "暖通")
        row = self._row(
            name="镀锌钢管", specification="DN100", qty=10, unit="km",
            professional="暖通", category="暖通",
        )
        scope = "r04_legacy_nonempty.xlsx"

        # 1) 构造 R01 风格历史确认记录：仅有客户端 unit="km"，无任何 confirmed_* 来源证据
        legacy_nonempty_bind = {
            "code": "CONC-M", "name": "镀锌钢管", "specification": "DN100",
            "unit": "km", "category": "暖通", "qty": 10000.0, "qty_ratio": 1000.0,
        }
        self._insert_raw_confirmed_decision(scope, row, [legacy_nonempty_bind])

        # 在目标单位为 "m"（漂移前）与漂移成 "km"（与历史客户端 unit 同名）两种条件下均不得放行；
        # 请求中篡改 unit 或伪造 confirmed_* 亦不可救活旧确认。
        req_attack_variants = (
            [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "km"}],
            [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m"}],
            [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": ""}],
            [{
                "code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "km",
                "confirmed_unit": "km", "confirmed_orig_unit": "km",
                "confirmed_orig_qty": 10.0, "confirmed_unit_mult": 1.0,
                "confirmed_unit_source": "server_live_material", "confirmed_unit_version": "r04-v1",
            }],
        )
        for live_db_unit in ("m", "km"):
            conn = get_db_connection()
            try:
                conn.execute("UPDATE materials SET unit=? WHERE code='CONC-M'", (live_db_unit,))
                conn.commit()
            finally:
                conn.close()

            for req_binds in req_attack_variants:
                item = self._export_item(row, req_binds, status="manual_confirmed")
                rest = self.client.post(
                    "/api/session/annotate",
                    json={"items": [item], "scope_filename": scope},
                    headers=self.auth,
                )
                self.assertEqual(rest.status_code, 200, rest.text)
                self.assertEqual(rest.json()["items"][0]["decision_status"], "recommended", (live_db_unit, req_binds, rest.text))
                self.assertGreaterEqual(rest.json()["downgraded"], 1)

                exp = self.client.post(
                    "/api/export",
                    json={"items": [item], "scope_filename": scope},
                    headers=self.auth,
                )
                self.assertEqual(exp.status_code, 422, (live_db_unit, req_binds, exp.text))

        # 2) 历史空/缺 unit 以及“仅部分 confirmed_* / 伪造 version”数据库记录也一律阻断
        conn = get_db_connection()
        try:
            conn.execute("UPDATE materials SET unit='m' WHERE code='CONC-M'")
            conn.commit()
        finally:
            conn.close()

        for case_scope, bad_saved in (
            ("r04_empty.xlsx", [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": ""}]),
            ("r04_partial1.xlsx", [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m", "confirmed_unit": "m"}]),
            ("r04_partial2.xlsx", [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km"}]),
            ("r04_badver.xlsx", [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m", "confirmed_unit": "m", "confirmed_orig_unit": "km", "confirmed_unit_mult": 1000.0, "confirmed_unit_version": "fake-v0"}]),
        ):
            self._insert_raw_confirmed_decision(case_scope, row, bad_saved)
            req_good_item = self._export_item(
                row,
                [{"code": "CONC-M", "qty": 10000.0, "qty_ratio": 1000.0, "unit": "m"}],
                status="manual_confirmed",
            )
            rest_bad = self.client.post(
                "/api/session/annotate",
                json={"items": [req_good_item], "scope_filename": case_scope},
                headers=self.auth,
            )
            self.assertEqual(rest_bad.json()["items"][0]["decision_status"], "recommended", (case_scope, rest_bad.text))
            self.assertGreaterEqual(rest_bad.json()["downgraded"], 1)
            exp_bad = self.client.post(
                "/api/export",
                json={"items": [req_good_item], "scope_filename": case_scope},
                headers=self.auth,
            )
            self.assertEqual(exp_bad.status_code, 422, (case_scope, exp_bad.text))

        # 3) 对原历史记录按当前实时物料显式重新确认 -> 持久化权威上下文，恢复保持确认并可导出正确 XLSX
        good_bind = self._bind(
            "CONC-M", name="镀锌钢管", specification="DN100",
            unit="m", category="暖通", qty=10000.0, qty_ratio=1000.0,
        )
        sub_ok = self.client.post(
            "/api/binding/submit",
            json=self._confirm_payload(row, [good_bind], scope=scope),
            headers=self.auth,
        )
        self.assertEqual(sub_ok.status_code, 200, sub_ok.text)

        valid_item = self._export_item(row, [good_bind], status="manual_confirmed")
        rest_ok = self.client.post(
            "/api/session/annotate",
            json={"items": [valid_item], "scope_filename": scope},
            headers=self.auth,
        )
        self.assertEqual(rest_ok.status_code, 200, rest_ok.text)
        self.assertEqual(rest_ok.json()["items"][0]["decision_status"], "manual_confirmed")
        self.assertEqual(rest_ok.json()["downgraded"], 0)

        exp_ok = self.client.post(
            "/api/export",
            json={"items": [valid_item], "scope_filename": scope},
            headers=self.auth,
        )
        self.assertEqual(exp_ok.status_code, 200, exp_ok.text)
        wb = openpyxl.load_workbook(io.BytesIO(exp_ok.content), data_only=True)
        try:
            ws_rows = list(wb.active.iter_rows(values_only=True))
            self.assertEqual(ws_rows[3][7], "km")
            self.assertEqual(float(ws_rows[3][8]), 10.0)
            self.assertEqual(ws_rows[4][4], "CONC-M")
            self.assertEqual(ws_rows[4][7], "m")
            self.assertEqual(float(ws_rows[4][8]), 10000.0)
        finally:
            wb.close()

        # 4) 重新确认后再次发生目标单位漂移 (m -> km) -> 恢复降级且导出 422
        conn = get_db_connection()
        try:
            conn.execute("UPDATE materials SET unit='km' WHERE code='CONC-M'")
            conn.commit()
        finally:
            conn.close()
        rest_drift = self.client.post(
            "/api/session/annotate",
            json={"items": [valid_item], "scope_filename": scope},
            headers=self.auth,
        )
        self.assertEqual(rest_drift.json()["items"][0]["decision_status"], "recommended", rest_drift.text)
        self.assertGreaterEqual(rest_drift.json()["downgraded"], 1)
        exp_drift = self.client.post(
            "/api/export",
            json={"items": [valid_item], "scope_filename": scope},
            headers=self.auth,
        )
        self.assertEqual(exp_drift.status_code, 422, exp_drift.text)

        # 5) 合法 1 倍率一对多系数正例对照 (200 米 -> 300 m, qty_ratio=1.5) -> 正常导出与 XLSX 重读
        self._seed("CONC-ONE", "镀锌钢管", "DN100", "m", "暖通")
        row_one = self._row(
            name="镀锌钢管", specification="DN100", qty=200, unit="米",
            professional="暖通", category="暖通",
        )
        one_bind = self._bind(
            "CONC-ONE", name="镀锌钢管", specification="DN100",
            unit="m", category="暖通", qty=300.0, qty_ratio=1.5,
        )
        sub_one = self.client.post(
            "/api/binding/submit",
            json=self._confirm_payload(row_one, [one_bind], scope="r04_one.xlsx"),
            headers=self.auth,
        )
        self.assertEqual(sub_one.status_code, 200, sub_one.text)
        one_item = self._export_item(row_one, [one_bind], status="manual_confirmed")
        rest_one = self.client.post(
            "/api/session/annotate",
            json={"items": [one_item], "scope_filename": "r04_one.xlsx"},
            headers=self.auth,
        )
        self.assertEqual(rest_one.json()["items"][0]["decision_status"], "manual_confirmed")
        self.assertEqual(rest_one.json()["downgraded"], 0)
        exp_one = self.client.post(
            "/api/export",
            json={"items": [one_item], "scope_filename": "r04_one.xlsx"},
            headers=self.auth,
        )
        self.assertEqual(exp_one.status_code, 200, exp_one.text)
        wb_one = openpyxl.load_workbook(io.BytesIO(exp_one.content), data_only=True)
        try:
            ws_one = list(wb_one.active.iter_rows(values_only=True))
            self.assertEqual(ws_one[3][7], "米")
            self.assertEqual(float(ws_one[3][8]), 200.0)
            self.assertEqual(ws_one[4][4], "CONC-ONE")
            self.assertEqual(ws_one[4][7], "m")
            self.assertEqual(float(ws_one[4][8]), 300.0)
        finally:
            wb_one.close()
