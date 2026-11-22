"""M3 review edits and authoritative workbook fields."""
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from test_binding_decisions import M2Base, _seed_material


class TestReviewEdits(M2Base):
    def setUp(self):
        super().setUp()
        _seed_material("M3-PIPE", "镀锌钢管", "DN100", "米", "暖通")
        _seed_material("M3-ACCESSORY", "镀锌钢管配件", "DN100", "米", "暖通")

    def _post(self, route, payload):
        return self.client.post(route, json=payload, headers=self.auth)

    def _review(self, scope):
        row = self._row()
        bindings = [
            {"code": code, "name": "请求标签", "specification": "请求规格", "unit": "米",
             "category": "", "brand": "", "qty": qty, "qty_ratio": ratio}
            for code, qty, ratio in (("M3-PIPE", 300, 1.5), ("M3-ACCESSORY", 160, 0.8))
        ]
        return self._confirm_payload(row, bindings, scope), self._export_item(row, bindings)

    def test_invalidate_blocks_reverted_fingerprints_until_reconfirmed(self):
        scope = "m3-revert.xlsx"
        confirm, item = self._review(scope)
        file = {"items": [item], "scope_filename": scope}
        assert self._post("/api/binding/submit", confirm).status_code == 200
        assert self._post("/api/export", file).status_code == 200
        assert self._post("/api/binding/invalidate", confirm).status_code == 200
        assert self._post("/api/binding/invalidate", confirm).status_code == 200
        assert self._post("/api/export", file).status_code == 422
        restored = self._post("/api/session/annotate", file).json()["items"][0]
        assert restored["decision_status"] == "recommended"
        assert restored["decision_source"] == "manual_edit"
        assert self._post("/api/binding/submit", confirm).status_code == 200
        assert self._post("/api/export", file).status_code == 200
        assert self._post("/api/binding/reject", confirm).status_code == 200
        assert self._post("/api/binding/invalidate", confirm).status_code == 200
        assert self._post("/api/export", file).status_code == 422
        assert self._post("/api/session/annotate", file).json()["items"][0]["decision_status"] == "rejected"

    def test_confirm_restore_and_export_share_canonical_cells_and_ratios(self):
        scope = "m3-fields.xlsx"
        confirm, item = self._review(scope)
        file = {"items": [item], "scope_filename": scope}
        broken = {**confirm, "bindings": [{**confirm["bindings"][0], "qty_ratio": 1.25}]}
        assert self._post("/api/binding/submit", broken).status_code == 409
        response = self._post("/api/binding/submit", confirm)
        assert response.status_code == 200
        canonical = response.json()["bindings"]
        restored = self._post("/api/session/annotate", file).json()["items"][0]
        assert restored["bindings"] == canonical
        exported = self._post("/api/export", file)
        assert exported.status_code == 200
        workbook = load_workbook(BytesIO(exported.content))
        try:
            sheet = workbook.active
            cells = {row[4].value: row for row in sheet.iter_rows(min_row=2) if row[4].value}
            for binding in canonical:
                line = cells[binding["code"]]
                assert [cell.value for cell in line[4:9]] == [
                    binding["code"], binding["name"], binding["specification"], binding["unit"], binding["qty"],
                ]
                assert line[8].comment.text == f"绑定比例：{binding['qty_ratio']}"
        finally:
            workbook.close()


def test_workbench_five_states_use_shared_filter_and_no_exact_promotion():
    web = Path(__file__).resolve().parents[1] / "web"
    markup = (web / "index.html").read_text(encoding="utf-8")
    script = (web / "app.js").read_text(encoding="utf-8")
    for state in ("auto_accepted", "manual_confirmed", "recommended", "needs_parameters", "rejected"):
        assert f'data-filter="{state}"' in markup
    assert 'id="reject-match-btn"' in markup
    assert "items.filter(matchesWorkbenchFilters)" in script
    assert "hasReviewApproval(item)" in script
    assert "data.is_exact" not in script
