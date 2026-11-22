"""Isolated HTTP, rule governance and workbook checks for the first BOM family."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from app import server
from app.audit_manager import init_audit_tables
from app.auth_manager import issue_token_for_user
from app.db_manager import get_db_connection
from app.flywheel_db import init_flywheel_tables
from app.flywheel_sandbox import _inject_candidate_rule_into_sandbox
from app.llm_rule_engine import init_rule_engine_db
from app.rule_governance import compute_rule_digest
from app.sandbox_evaluator import _evaluate_binding_pipeline, _select_sandbox_subsets
from test_binding_decisions import M2Base, _seed_material


NAME = "304薄壁不锈钢管分段组合"
CATEGORY = "不锈钢管"
SPEC = "DN25 D25.4 304"
BARE, INSULATED = "2.17.05.0628", "2.17.05.0479"
SCOPE = "bom-synthetic.xlsx"


class TestBom(M2Base):
    def setUp(self):
        super().setUp()
        init_flywheel_tables()
        init_rule_engine_db()
        init_audit_tables()
        for code, name, spec in (
            (BARE, "304薄壁不锈钢管", "DN25，D25.4*1.0"),
            (INSULATED, "304薄壁不锈钢管（保温）", "DN25，D25.4×1.0"),
            ("2.17.05.0631", "304薄壁不锈钢管", "DN50，D50.8*1.2"),
            ("2.17.05.0482", "304薄壁不锈钢管（保温）", "DN50，D50.8×1.2"),
        ):
            _seed_material(code, name, spec, "m", CATEGORY)
        self.row = self._row(name=NAME, category=CATEGORY, specification=SPEC, unit="千米", qty=2, code="BOM-25")
        self.targets = self._targets(BARE, INSULATED)
        self.rule, self.normalized = self._policy(SPEC, self.targets)
        self.rules_file = Path(server._get_auto_binding_rules_path())
        self.rules_file.write_text(json.dumps([self.rule], ensure_ascii=False), encoding="utf-8")

    def _targets(self, *codes):
        return [
            {"code": code, "unit": "米", "qty_ratio": share, "name": "", "specification": "", "qty": 0,
             "brand": "", "category": ""}
            for code, share in zip(codes, (.6, .4), strict=True)
        ]

    def _policy(self, spec, targets):
        _, normalized = server._build_legacy_binding_spec_and_key(
            NAME, targets, original_spec=spec, professional="暖通",
        )
        return dict(original_name=NAME, original_spec=spec, professional="暖通",
                    bound_materials=normalized["bound_materials"], bom=normalized["bom"]), normalized

    def _post(self, path, payload):
        return self.client.post(path, json=payload, headers=self.auth)

    def _upload(self):
        workbook = Workbook()
        ws = workbook.active
        ws.title = "暖通"
        ws.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量", "甲供材品类", "品牌"])
        ws.append(["BOM-25", NAME, SPEC, "千米", 2, CATEGORY, ""])
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        uploaded = self.client.post("/api/upload", headers=self.auth, files={
            "file": (SCOPE, buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        })
        assert uploaded.status_code == 200, uploaded.text
        status = self.client.get("/api/upload/status", headers=self.auth, params={"task_id": uploaded.json()["task_id"]})
        assert status.json()["status"] == "completed", status.text
        return status.json()["result"][0]

    def test_match_upload_full_manual_draft_restore_and_excel(self):
        item = self._upload()
        assert item["decision_status"] == "auto_accepted", item["decision_reason"]
        assert item["source_row_id"] and item["recommendation_snapshot_id"]
        assert [(b["code"], b["unit"], b["qty"], b["qty_ratio"]) for b in item["bindings"]] == [
            (BARE, "m", 1200, 600), (INSULATED, "m", 800, 400),
        ]
        single = self._post("/api/match", dict(name=NAME, text=SPEC, professional="暖通",
                                                category=CATEGORY, unit="km", qty=2))
        assert single.status_code == 200 and single.json()["bindings"] == item["bindings"], single.text
        batch_payload = dict(name=NAME, specification=SPEC, professional="暖通", category=CATEGORY, unit="km")
        batch = self._post("/api/batch_match", {"rows": [dict(batch_payload, qty=2), batch_payload]})
        matched, incomplete = batch.json()["results"]
        assert matched["bindings"] == item["bindings"]
        assert incomplete["decision_status"] == "recommended" and incomplete["bindings"] == []
        qty_row = server._raw_item_from_export(item)
        confirm = self._confirm_payload({**qty_row, "row_idx": item["original_row_idx"]}, item["bindings"], SCOPE)
        confirmed = self._post("/api/binding/submit", confirm)
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["decision"]["decision_status"] == "manual_confirmed"
        save = self._post("/api/session/draft", dict(original_filename=SCOPE, professional="暖通",
                                                     items_json=json.dumps([item], ensure_ascii=False)))
        assert save.status_code == 200, save.text
        saved = self.client.get("/api/session/draft", headers=self.auth).json()["draft"]
        restored, = json.loads(saved["items_json"])
        assert restored["decision_status"] == "manual_confirmed"
        assert restored["binding_fingerprint"] == confirmed.json()["binding_fingerprint"]
        assert restored["row_fingerprint"] == confirmed.json()["row_fingerprint"]
        assert restored["bindings"] == confirmed.json()["bindings"]
        exported = self._post("/api/export", dict(items=[restored], scope_filename=SCOPE))
        assert exported.status_code == 200, exported.text
        book = load_workbook(BytesIO(exported.content))
        try:
            cells = {r[4].value: r for r in book.active.iter_rows(min_row=2) if r[1].value == "材料费"}
            assert set(cells) == {"BOM-25", BARE, INSULATED}
            assert [c.value for c in cells["BOM-25"][4:9]] == ["BOM-25", NAME, SPEC, "千米", 2]
            for binding in restored["bindings"]:
                line = cells[binding["code"]]
                assert [c.value for c in line[4:9]] == [
                    binding[key] for key in ("code", "name", "specification", "unit", "qty")
                ]
                assert line[8].comment.text == f"绑定比例：{binding['qty_ratio']}"
        finally:
            book.close()
        assert self._post("/api/binding/invalidate", confirm).status_code == 200
        late = self.client.get("/api/session/draft", headers=self.auth).json()["draft"]
        assert json.loads(late["items_json"])[0]["decision_status"] == "recommended"
        assert self._post("/api/export", dict(items=[restored], scope_filename=SCOPE)).status_code == 422

    def test_collection_quantity_and_unit_mismatches_fail_all_gates(self):
        item, = server.batch_match_rows([self.row])
        assert item["decision_status"] == "auto_accepted", item["decision_reason"]
        a, b = item["bindings"]
        for bindings in ([a], [a, b, a], [a, a], [a, dict(b, code="not-in-catalog")],
                         [a, dict(b, qty=2)], [a, dict(b, qty_ratio=0)], [a, dict(b, qty_ratio=-1)],
                         [a, dict(b, unit="m2")], [a, dict(b, unit="km")], [a, dict(b, unit="")]):
            bad = {**item, "bindings": bindings, "decision_status": "auto_accepted"}
            assert self._post("/api/binding/submit", self._confirm_payload(self.row, bindings, SCOPE)).status_code == 409
            assert self._post("/api/session/annotate", {"items": [bad], "scope_filename": SCOPE}).json()["items"][0][
                "decision_status"] == "recommended"
            assert self._post("/api/export", {"items": [bad], "scope_filename": SCOPE}).status_code == 422
        bool_bindings = [a, dict(b, qty_ratio=True)]
        assert self._post("/api/binding/submit", self._confirm_payload(self.row, bool_bindings, SCOPE)).status_code == 422
        confirm = self._confirm_payload(self.row, item["bindings"], SCOPE)
        confirm.pop("professional")
        item.pop("professional")
        assert self._post("/api/binding/submit", confirm).status_code == 409
        assert self._post("/api/export", {"items": [item], "scope_filename": SCOPE}).status_code == 422
        restored, = self._post("/api/session/annotate", {"items": [item]}).json()["items"]
        assert restored["decision_status"] == "recommended" and "暖通" in restored["decision_reason"]

    def test_context_is_explicit_and_unit_synonyms_and_dn50_are_supported(self):
        for changes, reason in (
            ({"specification": "DN25"}, "外径"), ({"specification": "DN25 D25.4 316L"}, "304"),
            ({"specification": "DN250 D25.4 304"}, "仅支持"), ({"category": ""}, "原始品类"),
            ({"professional": "给排水"}, "暖通"), ({"unit": "m2"}, "长度单位"),
            ({"unit": "无"}, "长度单位"), ({"qty": 0}, "工程量"),
        ):
            item, = server.batch_match_rows([{**self.row, **changes}])
            assert item["decision_status"] == "recommended" and item["bindings"] == []
            assert reason in item["decision_reason"]
        rule50, _ = self._policy("DN50 D50.8 304", self._targets("2.17.05.0631", "2.17.05.0482"))
        self.rules_file.write_text(json.dumps([self.rule, rule50], ensure_ascii=False), encoding="utf-8")
        item, = server.batch_match_rows([{**self.row, "unit": "米", "qty": 5, "specification": "DN50 D50.8 304"}])
        assert item["decision_status"] == "auto_accepted"
        assert [(b["qty"], b["qty_ratio"]) for b in item["bindings"]] == [(3, .6), (2, .4)]

    def test_catalog_drift_and_dynamic_hard_conflict_block_whole_group(self):
        item, = server.batch_match_rows([self.row])
        assert item["decision_status"] == "auto_accepted", item["decision_reason"]
        confirm = self._confirm_payload(self.row, item["bindings"], SCOPE)
        assert self._post("/api/binding/submit", confirm).status_code == 200
        rule = {"rule_id": "bom-forbid-insulation", "priority": 50, "scope_level": "discipline",
                "scope_target": "暖通", "parsed_dsl": {"matching_rules": {"must_not_contain": ["保温"]}}}
        from app.llm_rule_engine import LLMRuleManager

        with patch.object(LLMRuleManager, "_active_rules_cache", [rule]):
            blocked, = server.batch_match_rows([self.row])
            assert blocked["decision_status"] == "rejected" and blocked["bindings"] == []
            assert "动态规则硬排除" in blocked["decision_reason"]
            assert self._post("/api/export", {"items": [item], "scope_filename": SCOPE}).status_code == 422
        conn = get_db_connection()
        try:
            conn.execute("UPDATE materials SET specification='DN25，D42*1.0' WHERE code=?", (INSULATED,))
            conn.commit()
            assert self._post("/api/binding/submit", confirm).status_code == 409
            conn.execute("DELETE FROM materials WHERE code=?", (INSULATED,))
            conn.commit()
        finally:
            conn.close()
        blocked, = server.batch_match_rows([self.row])
        assert "已失效" in blocked["decision_reason"] and not blocked["bindings"]
        assert self._post("/api/export", {"items": [item], "scope_filename": SCOPE}).status_code == 422

    def test_old_bom_cannot_activate_and_sandbox_checks_independent_quantity_labels(self):
        old = {key: value for key, value in self.rule.items() if key != "bom"}
        self.rules_file.write_text(json.dumps([old], ensure_ascii=False), encoding="utf-8")
        item, = server.batch_match_rows([self.row])
        assert "旧规则" in item["decision_reason"] and item["bindings"] == []
        auth = {"Authorization": f"Bearer {issue_token_for_user(self._admin_actor)}"}
        request = dict(original_name=NAME, original_spec=SPEC, professional="暖通", bindings=self.targets)
        assert self.client.post("/api/binding/rules/confirm", json=request, headers=auth).status_code != 200
        assert json.loads(self.rules_file.read_text(encoding="utf-8")) == [old]
        with TemporaryDirectory() as directory:
            _inject_candidate_rule_into_sandbox(rule_meta=self.normalized, candidate_config_dir=directory,
                                                candidate_db_path="")
            activated, = json.loads((Path(directory) / "auto_binding_rules.json").read_text(encoding="utf-8"))
        assert activated == self.rule
        assert compute_rule_digest(self.normalized) != compute_rule_digest({**self.normalized, "bom": {"version": 2}})
        self.rules_file.write_text(json.dumps([activated], ensure_ascii=False), encoding="utf-8")
        gold = {"id": "BOM-25", "name": NAME, "professional": "暖通", "specification": SPEC, "unit": "km",
                "qty": 2, "supply_category": CATEGORY, "label_type": "1toN", "target_codes": [BARE, INSULATED],
                "bom_expected": [dict(code=BARE, unit="米", qty=1200, qty_ratio=600),
                                 dict(code=INSULATED, unit="m", qty=800, qty_ratio=400)]}
        assert _select_sandbox_subsets([gold], self.normalized)[0] == [gold]
        assert _select_sandbox_subsets([{**gold, "bom_expected": gold["bom_expected"][:1]}], self.normalized)[0] == []
        evaluation, = _evaluate_binding_pipeline([gold])
        assert evaluation["qty_ratios_ok"] and evaluation["codes_match_gt"] and evaluation["export_eligible"]
        wrong = deepcopy(gold)
        wrong["bom_expected"][1]["qty"] = 801
        evaluation, = _evaluate_binding_pipeline([wrong])
        assert not evaluation["qty_ratios_ok"] and not evaluation["export_eligible"]
