"""Offline transport and real route/catalog checks for advisory-only review."""
from contextlib import ExitStack, closing
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

from openpyxl import Workbook

from app import llm_decision as decision, server
from app.audit_manager import init_audit_tables
from app.db_manager import _set_db_revision, get_db_connection, get_db_revision
from app.flywheel_db import init_flywheel_tables
from app.llm_rule_engine import init_rule_engine_db
from test_binding_decisions import M2Base, _seed_material


A, B = "LLM-M2-A", "LLM-M2-B"
NAME, SPEC = "校准专用样件", "QAL-412X 配电箱 400×300×200"
BRAND = "校准品牌"
PROBE_IDS = {"GT-0674", "GT-0677", "GT-0678"}


class Stub:
    def __init__(self):
        self.calls = []
        self.reply = json.dumps({"decision": "recommend", "code": B})
        self.callback = None

    def __call__(self, system, payload, *, model, timeout):
        self.calls.append((system, json.loads(payload), model, timeout))
        if self.callback is not None:
            self.callback()
        return self.reply


class TestOfflineDecision(M2Base):
    def setUp(self):
        super().setUp()
        init_audit_tables()
        init_flywheel_tables()
        init_rule_engine_db()
        _seed_material(A, NAME, SPEC, "个", "强电")
        _seed_material(B, NAME, SPEC, "个", "强电", brand=BRAND)
        self.stub = Stub()
        self.now = time.monotonic()
        self.engine = decision.DecisionEngine(self.stub, model="offline-stub-v1", clock=lambda: self.now)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            "LISTTRANS_V020_M2_LLM_DECISION": "on", "LLM_ENABLED": "true",
            "DASHSCOPE_API_KEY": "", "DEEPSEEK_API_KEY": "",
        }))
        self.stack.enter_context(patch.object(decision, "offline_engine", self.engine))
        self.stack.enter_context(patch("app.llm_client.QwenLLMClient._call_qwen_api",
                                       side_effect=AssertionError("legacy external path is forbidden")))
        self.candidates = [self._material(A), self._material(B, score=61)]
        self.match = self.stack.enter_context(patch.object(server, "match_item", side_effect=self._result))
        self.base = self._row(name=NAME, specification=SPEC, category="强电", professional="强电",
                              unit="个", qty=1, brand=BRAND)

    def _material(self, code, **changes):
        return {"code": code, "name": NAME, "specification": SPEC, "unit": "个", "category": "强电",
                "brand": "", "description": "", "parsed_params": "", "score": 65,
                "hard_blocked": False, "max_score": 1., "recall_sources": ["fts"], **changes}

    def _result(self, *_args, **_kwargs):
        return {"matched_code": "", "is_exact": False, "top_candidates": [dict(c) for c in self.candidates]}

    def _call(self, **changes):
        payload = {"name": NAME, "text": SPEC, "category": "强电", "unit": "个", "professional": "强电",
                   "brand": BRAND, **changes}
        response = self.client.post("/api/match", headers=self.auth, json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    def _status(self, item, code):
        response = self.client.post("/api/export", headers=self.auth, json={
            "items": [item], "scope_filename": "offline-review.xlsx",
        })
        assert response.status_code == code, response.text

    def test_off_invalid_global_gate_or_provider_missing_are_exact_baseline(self):
        with patch.dict(os.environ, {"LLM_ENABLED": "false", "LISTTRANS_V020_M2_LLM_DECISION": "off"}):
            expected = self._call()
        for env in ({"LLM_ENABLED": "false"}, {"LISTTRANS_V020_M2_LLM_DECISION": "OFF"},
                    {"LISTTRANS_V020_M2_LLM_DECISION": "bogus"}, {"TESTING": "0"}):
            with patch.dict(os.environ, env):
                assert self._call() == expected
        with patch.object(decision, "offline_engine", None):
            assert self._call() == expected
        assert self.stub.calls == []

    def test_advice_remains_separate_from_binding_and_requires_real_manual_confirmation(self):
        observed = self._call()
        assert observed["llm_decision"] == {
            "decision": "recommend", "suggested_code": B, "reason": "offline_suggestion",
        }
        assert observed["decision_status"] == "recommended"
        assert observed["bindings"] == [] and observed["matched_code"] == "" and not observed["is_exact"]
        assert [x["code"] for x in observed["top_candidates"]] == [A, B]
        request = self.stub.calls[0]
        assert request[2] == "offline-stub-v1" and 0 < request[3] <= decision.TIMEOUT_SECONDS
        assert [x["code"] for x in request[1]["candidates"]] == [B]
        material = {**self._material(B), "qty": 1, "qty_ratio": 1}
        item = self._export_item(self.base, [material], "auto_accepted")
        item["llm_decision"] = observed["llm_decision"]
        self._status(item, 422)
        assert self.client.post("/api/binding/submit", headers=self.auth, json=self._confirm_payload(
            self.base, [material], "offline-review.xlsx",
        )).status_code == 200
        self._status(item, 200)

    def test_guards_skip_high_direct_cap_rule_bom_and_untrusted_overrides(self):
        alternatives = [dict(c) for c in self.candidates]
        for overrides in ({"score": 99}, {"recall_sources": ["fts", "exact_code"]},
                          {"hard_blocked": True}, {"max_score": .4}):
            self.candidates[0] = {**alternatives[0], **overrides}
            assert "llm_decision" not in self._call()
        self.candidates[:] = alternatives
        _seed_material("2.99.01.0001", NAME, SPEC, "个", "强电")
        assert "llm_decision" not in self._call(text=f"{SPEC} 物料编码：2.99.01.0001")
        assert "llm_decision" not in self._call(parsed_spec={"attrs": {"管径": 150}})
        assert "llm_decision" not in self._call(name="304薄壁不锈钢管分段组合", text="DN25 D25.4 304")
        workbook_rule = {"original_name": NAME, "professional": "强电", "original_spec": "QAL-412X 配电箱",
                         "bound_materials": [{"code": A, "specification": SPEC, "unit": "个", "qty_ratio": 1}]}
        with patch.object(server, "load_auto_binding_rules", return_value=[workbook_rule]):
            assert "llm_decision" not in self._call()
            row, = server.batch_match_rows([self.base])
            assert row["decision_status"] == "auto_accepted" and "llm_decision" not in row
        assert self.stub.calls == []

    def test_local_missing_and_no_candidates_and_typed_abstentions(self):
        answer = self._call(text="")
        assert answer["llm_decision"]["decision"] == "need_manual_params"
        assert "核心规格" in answer["llm_decision"]["missing_fields"] and not answer["bindings"]
        assert len(self.stub.calls) == 0
        original, self.candidates = self.candidates, []
        assert self._call()["llm_decision"] == {"decision": "no_answer", "reason": "no_eligible_catalog_candidate"}
        self.candidates = original
        for outcome in ("no_answer", "need_manual_params"):
            self.stub.reply = json.dumps({"decision": outcome})
            result = self._call(name=f"{NAME} {outcome}")
            assert result["llm_decision"] == {"decision": outcome, "reason": "offline_abstention"}
            assert not result["bindings"] and result["decision_status"] == "recommended"

    def test_indistinguishable_catalog_entries_require_manual_parameters_without_model(self):
        result = self._call(brand="")
        assert result["llm_decision"] == {"decision": "need_manual_params", "reason": "ambiguous_catalog_candidates",
                                          "missing_fields": ["物料区分参数"]}
        assert result["decision_status"] == "recommended" and not result["bindings"]
        assert self.stub.calls == []

    def test_invalid_json_forgery_and_out_of_pool_live_code_open_circuit_then_recover(self):
        _seed_material("ACTIVE-OUTSIDE", NAME, SPEC, "个", "强电")
        for raw in ('not-json', '{"decision":"recommend","code":"BOGUS"}',
                    '{"decision":"recommend","code":"ACTIVE-OUTSIDE"}'):
            self.stub.reply = raw
            result = self._call()
            assert result["llm_decision"] == {"decision": "fallback", "reason": "invalid_response"}
            assert result.get("decision_status") != "auto_accepted"
        assert self._call()["llm_decision"]["reason"] == "circuit_open"
        assert len(self.stub.calls) == 3
        self.now += decision.COOLDOWN_SECONDS + 1
        self.stub.reply = '{"decision":"recommend","code":"LLM-M2-B"}'
        assert self._call()["llm_decision"]["suggested_code"] == B
        for raw in ('{"decision":"recommend","code":"LLM-M2-B","bindings":[]}',
                    '{"decision":"recommend","decision":"no_answer"}',
                    '{"decision":"auto_accepted","code":"LLM-M2-B"}'):
            self.stub.reply = raw
            self.now += decision.CACHE_TTL_SECONDS + 1
            assert self._call()["llm_decision"]["reason"] == "invalid_response"

    def test_version_revision_and_live_candidate_evidence_invalidate_cache(self):
        baseline = get_db_revision()
        def revision(value):
            with closing(get_db_connection()) as conn, conn:
                _set_db_revision(conn, value)
        self.addCleanup(revision, baseline)
        self._call()
        self._call(name=f" {NAME} ")
        assert len(self.stub.calls) == 1
        revision(baseline + 1)
        self._call()
        with patch.object(decision, "POLICY_VERSION", "test-policy"):
            self._call()
        with patch.object(decision, "PROMPT_VERSION", "test-prompt"):
            self._call()
        self.engine.model = "offline-stub-v2"
        self._call()
        assert len(self.stub.calls) == 5
        self.stub.callback = lambda: revision(baseline + 2)
        assert self._call(name=NAME + "并发目录")["llm_decision"]["reason"] == "stale_evidence"
        self.stub.callback = None
        assert self._call()["llm_decision"]["suggested_code"] == B
        with closing(get_db_connection()) as conn, conn:
            conn.execute("DELETE FROM materials WHERE code=?", (B,))
        assert self._call()["llm_decision"]["reason"] == "invalid_response"
        assert all(c["code"] != B for c in self.stub.calls[-1][1]["candidates"])

    def test_timeout_does_not_queue_or_publish_late_result_and_slot_recovers(self):
        waiting, started = threading.Event(), threading.Event()
        def hold():
            started.set()
            waiting.wait(3)
        self.stub.callback = hold
        with patch.object(decision, "TIMEOUT_SECONDS", .075):
            try:
                first = self._call()
                assert started.is_set()
                assert first["llm_decision"]["reason"] == "timeout"
                assert self._call(name=NAME + "并发")["llm_decision"]["reason"] == "busy"
                assert len(self.stub.calls) == 1
            finally:
                self.stub.callback = None
                waiting.set()
            expiry = time.monotonic() + 1
            while time.monotonic() < expiry and not self.engine._worker.acquire(blocking=False):
                time.sleep(.005)
            assert time.monotonic() < expiry
            self.engine._worker.release()
            assert self._call()["llm_decision"]["suggested_code"] == B
            assert len(self.stub.calls) == 2

    def _upload(self, names):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "强电"
        sheet.append(["项目编码", "项目名称", "项目特征描述", "计量单位", "工程量", "甲供材品类", "品牌"])
        for index, name in enumerate(names):
            sheet.append([f"OFFLINE-{index}", name, SPEC, "个", 1, "强电", BRAND])
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        response = self.client.post("/api/upload", headers=self.auth, files={
            "file": ("offline-review.xlsx", buffer.getvalue(),
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        })
        assert response.status_code == 200, response.text
        status = self.client.get("/api/upload/status", headers=self.auth, params={"task_id": response.json()["task_id"]})
        assert status.json()["status"] == "completed", status.text
        return status.json()["result"]

    def test_request_and_actual_upload_only_spend_two_calls_and_cache_hits_are_free(self):
        names = [f"{NAME} {i}" for i in range(4)]
        output = self._upload(names + [names[0]])
        assert [row["llm_decision"].get("suggested_code") for row in output] == [B, B, None, None, B]
        assert [row["llm_decision"]["reason"] for row in output[2:4]] == ["budget_exhausted"] * 2
        assert len(self.stub.calls) == 2
        payload = {"rows": [{"text": SPEC, "category": "强电", "professional": "强电", "unit": "个", "brand": f"批{i}"}
                            for i in range(4)]}
        self.stub.reply = '{"decision":"no_answer"}'
        result = self.client.post("/api/batch_match", headers=self.auth, json=payload)
        assert result.status_code == 200
        assert [row["llm_decision"]["reason"] for row in result.json()["results"]] == [
            "offline_abstention", "offline_abstention", "budget_exhausted", "budget_exhausted",
        ]
        assert len(self.stub.calls) == 4

    def test_independent_hard_constraints_on_current_catalog_filter_model_choices(self):
        from app.llm_rule_engine import LLMRuleManager

        cases = (
            ("不锈钢管", "DN25 不锈钢304", "DN32 不锈钢304", "米", "给排水"),
            ("PPR给水管", "De20", "De25", "米", "给排水"),
            ("不锈钢管", "DN25 不锈钢304", "DN25 不锈钢316L", "米", "给排水"),
            ("轴流排风机", "风量3000m³/h 非防爆", "风量3000m³/h 防爆", "台", "暖通"),
            ("风机盘管", "FP-68 不带提升泵", "FP-68 带提升泵", "台", "暖通"),
        )
        for index, (name, spec, wrong, unit, prof) in enumerate(cases):
            code = f"BAD-LIVE-{index}"
            _seed_material(code, name, wrong, unit, prof)
            self.candidates[:] = [self._material(code)]
            result = self._call(name=name, text=spec, unit=unit, category=prof, professional=prof)
            assert "llm_decision" not in result, (spec, wrong, result.get("llm_decision"))
        self.candidates[:] = [self._material(B)]
        _seed_material(B, NAME, SPEC, "m2", "强电")
        assert "llm_decision" not in self._call(unit="m")
        dynamic = {"rule_id": "offline-exclude", "scope_level": "category", "scope_target": "",
                   "parsed_dsl": {"matching_rules": {"must_not_contain": [A]}}}
        self.candidates[:] = [self._material(A)]
        with patch.object(LLMRuleManager, "_active_rules_cache", [dynamic]):
            assert "llm_decision" not in self._call()
        assert self.stub.calls == []
        self.candidates[:] = [self._material(A), self._material(B, score=61)]
        # The rank result claims B is compatible. The fresh database says otherwise.
        assert self._call()["llm_decision"]["reason"] == "invalid_response"
        assert [c["code"] for c in self.stub.calls[-1][1]["candidates"]] == [A]

    def test_protected_golden_unknowns_remain_manual_at_high_scores(self):
        evidence = Path(__file__).resolve().parents[1] / "doc/V0.2.0/evidence"
        with patch.object(sys, "path", [str(evidence), *sys.path]):
            from m0_r01_data import read_projection
            items, _ = read_projection()
        fixtures_path = Path(__file__).resolve().parent / "fixtures/sandbox_materials_v1.json"
        materials = {r["code"]: r for r in json.loads(fixtures_path.read_text(encoding="utf-8"))["materials"]}
        observed = set()
        for item in items:
            if item["id"] not in PROBE_IDS:
                continue
            observed.add(item["id"])
            code, = item["target_codes"]
            m = materials[code]
            _seed_material(code, m["name"], m["spec"], "台", m["category"])
            self.candidates[:] = [self._material(code, name=m["name"], specification=m["spec"], unit="台", score=98.6)]
            raw = self._row(name=item["name"], specification=item["specification"], unit=item["unit"],
                            professional=item["professional"], category=item["supply_category"])
            match, = server.batch_match_rows([raw])
            assert match["llm_decision"]["decision"] == "need_manual_params"
            assert "防爆" in match["llm_decision"]["missing_fields"]
            assert match["decision_status"] == "recommended" and not match["bindings"]
            self._status(match, 422)
        assert observed == PROBE_IDS and not self.stub.calls
