# -*- coding: utf-8 -*-
"""V015-M2：绑定状态契约、人工确认、导出门禁与旧草稿兼容测试。

全部在 conftest 隔离根内运行：物料库/账号库/配置/上传/输出均隔离，
AUTH_DEV_MODE=false，真实 Bearer Token 鉴权，不调付费 LLM、不下载模型。
"""
import io
import json
import os
import unittest

from fastapi.testclient import TestClient

from app.db_manager import get_db_connection, init_db
from app.auth_manager import (
    init_auth_db, issue_token_for_user, get_user_by_username,
    get_auth_connection, get_password_hash,
)
from app.session_manager import init_session_db
from app.binding_decisions import (
    clear_binding_decisions, binding_fingerprint, normalize_bindings,
    row_content_fingerprint,
)
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


def _seed_material(code, name, spec, unit, category, brand=""):
    M2Base._track_seed(code)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO materials "
        "(code, name, specification, unit, category, brand, description, search_tokens) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (code, name, spec, unit, category, brand, "", f"{name} {spec} {category} {brand}"),
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
            (code, *(fts_tokenize(v) for v in (category, name, spec, brand, ""))),
        )
    conn.commit()
    conn.close()


class M2Base(unittest.TestCase):
    _seeded_codes = set()

    @classmethod
    def setUpClass(cls):
        init_db()
        init_auth_db()
        init_session_db()
        cls.client = TestClient(srv.app)
        _ensure_user("m2tester", "Test@12345", role="employee")
        cls.user = get_user_by_username("m2tester")
        cls.token = issue_token_for_user(cls.user)
        cls.auth = {"Authorization": f"Bearer {cls.token}"}
        _ensure_user("m2admin", "AdminTest@12345", role="admin")
        cls._admin_actor = get_user_by_username("m2admin")
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
        # 隔离规则文件与反馈日志
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
        self._clear_seeded_materials()
        srv.save_auto_binding_rules([], actor_user=self._admin_actor)
        if self._rules_backup is not None:
            with open(srv.AUTO_BINDING_RULES_FILE, "w", encoding="utf-8") as f:
                f.write(self._rules_backup)

    # ---- helpers ----
    def _row(self, **kw):
        row = {
            "row_idx": 0,
            "name": "镀锌钢管",
            "specification": "DN100",
            "qty": 200,
            "unit": "米",
            "loss": 0,
            "category": "暖通",
            "brand": "",
            "professional": "暖通",
            "code": "",
        }
        row.update(kw)
        return row

    def _export_item(self, row, bindings, status="recommended"):
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

    def _confirm_payload(self, row, bindings, scope="test.xlsx"):
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


class TestDecisionContract(M2Base):
    def test_zero_score_or_no_candidate_not_prebound(self):
        """0 分/无答案不预绑定：无候选行 bindings 为空且为 recommended。"""
        out = srv.batch_match_rows([self._row(name="完全不存在的东西xyz", specification="QQQ999")])
        r = out[0]
        self.assertEqual(r["decision_status"], "recommended")
        self.assertEqual(r["bindings"], [])
        self.assertIn("不预绑定", r["decision_reason"])

    def test_matcher_recommendation_is_not_exportable(self):
        """matcher 推荐候选默认仅 recommended，不是可导出状态。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        out = srv.batch_match_rows([self._row()])
        r = out[0]
        self.assertNotEqual(r["decision_status"], "auto_accepted")
        self.assertIn(r["decision_status"], ("recommended", "rejected"))

    def _seed_rules_fixture(self, rules):
        import json as _json
        with open(srv._get_auto_binding_rules_path(), "w", encoding="utf-8") as _f:
            _json.dump(rules, _f, ensure_ascii=False, indent=4)

    def test_name_only_rule_dn100_not_auto_accepted_by_dn15(self):
        """名称-only 旧规则：DN100 不得因 DN15 同名规则被自动接受。"""
        _seed_material("PIPE15", "镀锌钢管", "DN15", "米", "暖通")
        self._seed_rules_fixture([{
            "original_name": "镀锌钢管",
            "bound_materials": [{"code": "PIPE15", "specification": "DN15", "qty_ratio": 1.0}],
        }])
        out = srv.batch_match_rows([self._row(specification="DN100")])
        r = out[0]
        self.assertEqual(r["decision_status"], "recommended")
        self.assertNotEqual(r["decision_status"], "auto_accepted")

    def test_constrained_rule_auto_accepts_with_valid_code(self):
        """带规格约束且逐项匹配 + 编码有效 → 自动接受。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        self._seed_rules_fixture([{
            "original_name": "镀锌钢管",
            "original_spec": "DN100",
            "bound_materials": [{"code": "PIPE100", "specification": "DN100", "qty_ratio": 1.0}],
        }])
        out = srv.batch_match_rows([self._row(specification="DN100")])
        r = out[0]
        self.assertEqual(r["decision_status"], "auto_accepted")
        self.assertEqual(r["matched_code"], "PIPE100")

    def test_rule_with_invalid_code_downgrades_to_recommended(self):
        """规则目标编码失效 → 降推荐，不自动接受。"""
        self._seed_rules_fixture([{
            "original_name": "镀锌钢管",
            "original_spec": "DN100",
            "bound_materials": [{"code": "GHOST_CODE", "specification": "DN100", "qty_ratio": 1.0}],
        }])
        out = srv.batch_match_rows([self._row(specification="DN100")])
        r = out[0]
        self.assertEqual(r["decision_status"], "recommended")
        self.assertIn("GHOST_CODE", r["decision_reason"])

    def test_one_to_many_qty_ratio_preserved(self):
        """一对多数量/比例操作保留：200×1.5=300、200×0.8=160。"""
        _seed_material("M_001", "镀锌钢管", "DN100", "米", "暖通")
        _seed_material("M_002", "镀锌钢管配件", "DN100", "米", "暖通")
        self._seed_rules_fixture([{
            "original_name": "镀锌钢管",
            "original_spec": "DN100",
            "bound_materials": [
                {"code": "M_001", "specification": "DN100", "qty_ratio": 1.5},
                {"code": "M_002", "specification": "DN100", "qty_ratio": 0.8},
            ],
        }])
        out = srv.batch_match_rows([self._row(specification="DN100", qty=200)])
        r = out[0]
        self.assertEqual(r["decision_status"], "auto_accepted")
        self.assertEqual(r["matched_code"], "M_001,M_002")
        self.assertEqual(len(r["bindings"]), 2)
        self.assertAlmostEqual(r["bindings"][0]["qty"], 300.0)
        self.assertAlmostEqual(r["bindings"][1]["qty"], 160.0)
        self.assertAlmostEqual(r["bindings"][0]["qty_ratio"], 1.5)
        self.assertAlmostEqual(r["bindings"][1]["qty_ratio"], 0.8)

    def test_equivalent_numeric_types_share_fingerprints(self):
        row_int = self._export_item(self._row(), [])
        row_float = dict(row_int, original_qty=200.0, original_loss=0.0)
        self.assertEqual(row_content_fingerprint(row_int), row_content_fingerprint(row_float))
        missing_ratio = [{"code": "M_001", "qty": 300}]
        explicit_ratio = [{"code": "M_001", "qty": 300.0, "qty_ratio": 1.5}]
        self.assertEqual(
            binding_fingerprint(normalize_bindings(missing_ratio, 200)),
            binding_fingerprint(explicit_ratio),
        )


class TestManualConfirm(M2Base):
    def test_confirm_valid_binding_returns_server_decision(self):
        """合法人工确认：服务端返回 manual_confirmed 与指纹凭据。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        row = self._row()
        binds = [{"code": "PIPE100", "name": "镀锌钢管", "specification": "DN100",
                  "unit": "米", "category": "暖通", "brand": "", "qty": 200}]
        resp = self.client.post("/api/binding/submit", json=self._confirm_payload(row, binds), headers=self.auth)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["decision"]["decision_status"], "manual_confirmed")
        self.assertTrue(data["decision"]["row_fingerprint"])
        self.assertTrue(data["decision"]["binding_fingerprint"])

    def test_confirm_invalid_code_rejected(self):
        """失效编码不得人工确认。"""
        row = self._row()
        binds = [{"code": "GHOST", "name": "x", "specification": "DN100",
                  "unit": "米", "category": "暖通", "brand": "", "qty": 200}]
        resp = self.client.post("/api/binding/submit", json=self._confirm_payload(row, binds), headers=self.auth)
        self.assertEqual(resp.status_code, 409)
        self.assertIn("GHOST", json.dumps(resp.json(), ensure_ascii=False))

    def test_confirm_hard_conflict_rejected(self):
        """硬冲突（专业不符）不得靠人工确认绕过。"""
        _seed_material("CABLE", "电力电缆", "YJV-4x25", "米", "强电")
        row = self._row(professional="暖通", category="暖通")
        binds = [{"code": "CABLE", "name": "电力电缆", "specification": "YJV-4x25",
                  "unit": "米", "category": "强电", "brand": "", "qty": 200}]
        resp = self.client.post("/api/binding/submit", json=self._confirm_payload(row, binds), headers=self.auth)
        self.assertEqual(resp.status_code, 409)
        self.assertIn("硬冲突", json.dumps(resp.json(), ensure_ascii=False))


class TestExportGate(M2Base):
    def _auto_rule(self):
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        with open(srv._get_auto_binding_rules_path(), "w", encoding="utf-8") as _f:
            json.dump([{
                "original_name": "镀锌钢管", "original_spec": "DN100",
                "bound_materials": [{"code": "PIPE100", "specification": "DN100", "qty_ratio": 1.5}],
            }], _f, ensure_ascii=False, indent=4)

    def test_auto_accepted_export_valid(self):
        self._auto_rule()
        row = self._row()
        item = self._export_item(row, [{"code": "PIPE100", "name": "镀锌钢管",
            "specification": "DN100", "unit": "米", "category": "暖通", "brand": "",
            "qty": 300, "qty_ratio": 1.5}], status="auto_accepted")
        resp = self.client.post("/api/export", json={"items": [item]}, headers=self.auth)
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_auto_accepted_qty_tamper_blocked(self):
        self._auto_rule()
        row = self._row()
        item = self._export_item(row, [{"code": "PIPE100", "name": "镀锌钢管",
            "specification": "DN100", "unit": "米", "category": "暖通", "brand": "",
            "qty": 999, "qty_ratio": 1.5}], status="auto_accepted")
        resp = self.client.post("/api/export", json={"items": [item]}, headers=self.auth)
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("数量/比例", json.dumps(resp.json(), ensure_ascii=False))

    def test_auto_accepted_original_qty_edit_requires_confirmation(self):
        self._auto_rule()
        row = self._row()
        item = self._export_item(row, [{"code": "PIPE100", "name": "镀锌钢管",
            "specification": "DN100", "unit": "米", "category": "暖通", "brand": "",
            "qty": 300, "qty_ratio": 1.5}], status="auto_accepted")
        item["original_qty"] = 300
        resp = self.client.post("/api/export", json={"items": [item]}, headers=self.auth)
        self.assertEqual(resp.status_code, 422, resp.text)

    def _confirm_then_export(self, row, binds, scope="test.xlsx", mutate=None, expect=200):
        c = self.client.post("/api/binding/submit", json=self._confirm_payload(row, binds, scope), headers=self.auth)
        self.assertEqual(c.status_code, 200, c.text)
        item = self._export_item(row, binds, status="manual_confirmed")
        if mutate:
            mutate(item)
        resp = self.client.post("/api/export", json={"items": [item], "scope_filename": scope}, headers=self.auth)
        self.assertEqual(resp.status_code, expect, resp.text)
        return resp

    def test_unconfirmed_export_blocked(self):
        """未确认行直调导出被拒（422），不默选 top1。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        row = self._row()
        binds = [{"code": "PIPE100", "name": "镀锌钢管", "specification": "DN100",
                  "unit": "米", "category": "暖通", "brand": "", "qty": 200}]
        item = self._export_item(row, binds, status="recommended")
        resp = self.client.post("/api/export", json={"items": [item], "scope_filename": "test.xlsx"}, headers=self.auth)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("未确认", json.dumps(resp.json(), ensure_ascii=False))

    def test_forged_status_without_server_record_blocked(self):
        """伪造 manual_confirmed 但无服务端记录 → 拒绝。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        row = self._row()
        binds = [{"code": "PIPE100", "name": "镀锌钢管", "specification": "DN100",
                  "unit": "米", "category": "暖通", "brand": "", "qty": 200}]
        item = self._export_item(row, binds, status="manual_confirmed")
        item["is_exact"] = True
        resp = self.client.post("/api/export", json={"items": [item], "scope_filename": "test.xlsx"}, headers=self.auth)
        self.assertEqual(resp.status_code, 422)

    def test_confirm_then_export_then_xlsx_reread(self):
        """真实 HTTP：人工确认→导出→XLSX 重读，多行一对多数量正确。"""
        _seed_material("M_001", "镀锌钢管", "DN100", "米", "暖通")
        _seed_material("M_002", "镀锌钢管配件", "DN100", "米", "暖通")
        row = self._row(qty=200)
        binds = [
            {"code": "M_001", "name": "镀锌钢管", "specification": "DN100", "unit": "米", "category": "暖通", "brand": "", "qty": 300.0, "qty_ratio": 1.5},
            {"code": "M_002", "name": "镀锌钢管配件", "specification": "DN100", "unit": "米", "category": "暖通", "brand": "", "qty": 160.0, "qty_ratio": 0.8},
        ]
        resp = self._confirm_then_export(row, binds, expect=200)
        self.assertEqual(resp.headers["content-type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        codes, qtys = [], []
        for r in range(2, ws.max_row + 1):
            v = ws.cell(row=r, column=5).value
            if v in ("M_001", "M_002"):
                codes.append(str(v).strip())
                qtys.append(ws.cell(row=r, column=9).value)
        self.assertEqual(codes, ["M_001", "M_002"])
        self.assertEqual(qtys, [300.0, 160.0])
        self.assertEqual(ws.cell(4, 4).value, "暖通")
        self.assertEqual(ws.cell(4, 6).value, "镀锌钢管")
        self.assertEqual(ws.cell(4, 7).value, "DN100")
        self.assertEqual(ws.cell(4, 8).value, "米")
        self.assertEqual(ws.cell(4, 9).value, 200)
        self.assertEqual(ws.cell(4, 15).value, "暖通")
        self.assertIsNone(ws.cell(5, 11).value)
        self.assertEqual(ws.cell(5, 15).value, "暖通")
        wb.close()

    def test_batch_match_confirm_export_xlsx_reread(self):
        """真实 HTTP：batch_match 推荐→显式确认→导出→XLSX 重读。"""
        _seed_material("M_001", "镀锌钢管", "DN100", "米", "暖通", brand="华康")
        scope = "http-chain.xlsx"
        match = self.client.post("/api/batch_match", json={
            "rows": [{"id": 8, "text": "镀锌钢管 DN100", "unit": "米", "qty": 2,
                      "category": "暖通", "brand": "华康", "professional": "暖通"}],
        }, headers=self.auth)
        self.assertEqual(match.status_code, 200, match.text)
        item = match.json()["results"][0]
        self.assertEqual(item["decision_status"], "recommended", json.dumps(item, ensure_ascii=False))
        self.assertEqual(len(item["bindings"]), 1, json.dumps(item, ensure_ascii=False))
        binding = dict(item["bindings"][0], qty=3.0, qty_ratio=1.5)
        confirm_payload = {
            "original_name": item["original_name"],
            "original_spec": item["original_spec"],
            "original_qty": item["original_qty"],
            "original_unit": item["original_unit"],
            "original_loss": item["original_loss"],
            "original_category": item["original_category"],
            "original_brand": item["original_brand"],
            "original_code": item["original_code"],
            "original_row_idx": item["original_row_idx"],
            "professional": item["professional"],
            "scope_filename": scope,
            "bindings": [binding],
        }
        confirmed = self.client.post("/api/binding/submit", json=confirm_payload, headers=self.auth)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        item["bindings"] = [binding]
        item.update(confirmed.json()["decision"])
        exported = self.client.post(
            "/api/export", json={"items": [item], "scope_filename": scope}, headers=self.auth,
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(exported.content), data_only=True)
        ws = wb.active
        self.assertEqual(ws.cell(5, 5).value, "M_001")
        self.assertEqual(ws.cell(5, 9).value, 3.0)
        self.assertEqual(ws.cell(4, 6).value, "镀锌钢管 DN100")
        self.assertEqual(ws.cell(4, 9).value, 2)
        self.assertEqual(ws.cell(5, 11).value, "华康")
        self.assertEqual(ws.cell(5, 15).value, "暖通")
        wb.close()

    def test_edit_after_confirm_invalidates_export(self):
        """确认后修改行内容（规格）→ 行指纹失配 → 导出拒绝。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        row = self._row()
        binds = [{"code": "PIPE100", "name": "镀锌钢管", "specification": "DN100",
                  "unit": "米", "category": "暖通", "brand": "", "qty": 200}]
        self._confirm_then_export(row, binds, mutate=lambda it: it.update(original_spec="DN150"), expect=422)

    def test_qty_edit_after_confirm_invalidates_export(self):
        """确认后修改绑定数量 → 绑定指纹失配 → 导出拒绝。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        row = self._row()
        binds = [{"code": "PIPE100", "name": "镀锌钢管", "specification": "DN100",
                  "unit": "米", "category": "暖通", "brand": "", "qty": 200}]

        def bump(it):
            it["bindings"][0]["qty"] = 999.0
        self._confirm_then_export(row, binds, mutate=bump, expect=422)

    def test_code_invalidated_after_confirm_blocked(self):
        """确认后物料编码失效（删除）→ 导出拒绝。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        row = self._row()
        binds = [{"code": "PIPE100", "name": "镀锌钢管", "specification": "DN100",
                  "unit": "米", "category": "暖通", "brand": "", "qty": 200}]
        c = self.client.post("/api/binding/submit", json=self._confirm_payload(row, binds), headers=self.auth)
        self.assertEqual(c.status_code, 200)
        conn = get_db_connection()
        conn.execute("DELETE FROM materials WHERE code='PIPE100'")
        conn.commit()
        conn.close()
        item = self._export_item(row, binds, status="manual_confirmed")
        resp = self.client.post("/api/export", json={"items": [item], "scope_filename": "test.xlsx"}, headers=self.auth)
        self.assertEqual(resp.status_code, 422)

    def test_reject_overrides_auto_rule_and_reports_row(self):
        """人工拒绝不得被同名自动规则绕过，恢复与导出均保留拒绝。"""
        self._auto_rule()
        row = self._row()
        binds = [{"code": "PIPE100", "name": "镀锌钢管", "specification": "DN100",
                  "unit": "米", "category": "暖通", "brand": "", "qty": 300,
                  "qty_ratio": 1.5}]
        rejected = self.client.post(
            "/api/binding/reject", json=self._confirm_payload(row, binds), headers=self.auth,
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["decision"]["decision_status"], "rejected")

        item = self._export_item(row, binds, status="auto_accepted")
        exported = self.client.post(
            "/api/export", json={"items": [item], "scope_filename": "test.xlsx"}, headers=self.auth,
        )
        self.assertEqual(exported.status_code, 422, exported.text)
        detail = exported.json()["detail"]
        self.assertEqual(detail["error"], "export_blocked")
        self.assertEqual(detail["row"]["original_row_idx"], 0)
        self.assertEqual(detail["row"]["original_name"], "镀锌钢管")
        self.assertEqual(detail["row"]["original_spec"], "DN100")
        self.assertIn("人工拒绝", detail["row"]["reason"])

        restored = self.client.post(
            "/api/session/annotate",
            json={"items": [item], "scope_filename": "test.xlsx"}, headers=self.auth,
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["items"][0]["decision_status"], "rejected")
        self.assertEqual(restored.json()["items"][0]["decision_source"], "manual_reject")


class TestDraftRestore(M2Base):
    def test_legacy_draft_downgraded(self):
        """旧草稿 is_exact/bindings 无服务端确认 → 恢复降 recommended。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        legacy = [{
            "original_row_idx": 0, "original_name": "镀锌钢管", "original_spec": "DN100",
            "original_qty": 200, "original_unit": "米", "original_loss": 0,
            "original_category": "暖通", "original_brand": "", "professional": "暖通", "original_code": "",
            "matched_code": "PIPE100", "is_exact": True,
            "bindings": [{"code": "PIPE100", "qty": 200}],
        }]
        resp = self.client.post("/api/session/annotate",
                                json={"items": legacy, "scope_filename": "old.xlsx"}, headers=self.auth)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["items"][0]["decision_status"], "recommended")
        self.assertEqual(data["downgraded"], 1)

    def test_confirmed_draft_restored(self):
        """有服务端人工确认的草稿恢复保留 manual_confirmed。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        row = self._row()
        binds = [{"code": "PIPE100", "name": "镀锌钢管", "specification": "DN100",
                  "unit": "米", "category": "暖通", "brand": "", "qty": 200}]
        c = self.client.post("/api/binding/submit", json=self._confirm_payload(row, binds, scope="s.xlsx"), headers=self.auth)
        self.assertEqual(c.status_code, 200)
        item = self._export_item(row, binds, status="manual_confirmed")
        resp = self.client.post("/api/session/annotate",
                                json={"items": [item], "scope_filename": "s.xlsx"}, headers=self.auth)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["items"][0]["decision_status"], "manual_confirmed")
        self.assertEqual(data["downgraded"], 0)

    def test_confirmed_draft_with_invalid_code_downgraded(self):
        """已确认目标编码失效后，恢复立即降级而非显示人工确认。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        row = self._row()
        binds = [{"code": "PIPE100", "name": "镀锌钢管", "specification": "DN100",
                  "unit": "米", "category": "暖通", "brand": "", "qty": 200}]
        confirmed = self.client.post(
            "/api/binding/submit", json=self._confirm_payload(row, binds, scope="s.xlsx"),
            headers=self.auth,
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        conn = get_db_connection()
        conn.execute("DELETE FROM materials WHERE code='PIPE100'")
        conn.commit()
        conn.close()
        item = self._export_item(row, binds, status="manual_confirmed")
        restored = self.client.post(
            "/api/session/annotate", json={"items": [item], "scope_filename": "s.xlsx"},
            headers=self.auth,
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        result = restored.json()["items"][0]
        self.assertEqual(result["decision_status"], "recommended")
        self.assertIn("编码已失效", result["decision_reason"])
        self.assertEqual(restored.json()["downgraded"], 1)

    def test_auto_rule_draft_with_tampered_qty_downgraded(self):
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        with open(srv._get_auto_binding_rules_path(), "w", encoding="utf-8") as _f:
            json.dump([{
                "original_name": "镀锌钢管", "original_spec": "DN100",
                "bound_materials": [{"code": "PIPE100", "specification": "DN100", "qty_ratio": 1.5}],
            }], _f, ensure_ascii=False, indent=4)
        item = self._export_item(self._row(), [{"code": "PIPE100", "qty": 999}],
                                status="auto_accepted")
        resp = self.client.post("/api/session/annotate", json={"items": [item]}, headers=self.auth)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["items"][0]["decision_status"], "recommended")
        self.assertEqual(resp.json()["downgraded"], 1)

    def test_legacy_draft_cannot_bypass_export(self):
        """旧草稿（伪造 is_exact+matched_code）直调导出仍被拒。"""
        _seed_material("PIPE100", "镀锌钢管", "DN100", "米", "暖通")
        row = self._row()
        binds = [{"code": "PIPE100", "name": "镀锌钢管", "specification": "DN100",
                  "unit": "米", "category": "暖通", "brand": "", "qty": 200}]
        item = self._export_item(row, binds, status="manual_confirmed")
        item["is_exact"] = True
        resp = self.client.post("/api/export", json={"items": [item], "scope_filename": "old.xlsx"}, headers=self.auth)
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
