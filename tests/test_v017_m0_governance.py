import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "doc/V0.1.7/evidence/m0_r01_build_governance.py"
SPEC = importlib.util.spec_from_file_location("v017_governance_builder", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_equivalence_separates_brand_unit_category_description_and_parsed_params():
    material = {"name": "样例阀", "specification": "DN100", "unit": "个", "brand": "甲",
                "category": "样例分类", "description": "样例说明", "parsed_params": '{"DN":100,"阀型":"甲"}'}
    rows = [
        dict(material, code="A"),
        dict(material, code="B", parsed_params='{"阀型":"甲", "DN":100}'),
        dict(material, code="C", brand="乙"),
        dict(material, code="D", unit="套"),
        dict(material, code="E", category="其他分类"),
        dict(material, code="F", description="其他说明"),
        dict(material, code="G", parsed_params='{"DN":50}'),
    ]
    groups, equivalence = builder.duplicate_groups(rows)
    assert groups[0]["codes"] == list("ABCDEFG")
    assert groups[0]["differing_fields"] == ["unit", "brand", "category", "description", "parsed_params"]
    assert equivalence == {"A": ["A", "B"], "B": ["A", "B"]}


def test_frozen_assignment_covers_same_sheet_and_near_query_edges():
    items = builder.read_json(builder.GOLDEN)["items"]
    data = builder.read_json(ROOT / "tests/fixtures/golden_governance_v1.json")
    assignments, details = builder.split_items(items)
    assert assignments == {k: v["split"] for k, v in data["samples"].items()}
    assert builder.split_items(list(reversed(items)))[0] == assignments
    assert set(assignments.values()) == {"train", "dev", "test"}
    assert len(assignments) == len(items)
    by_sheet, by_query = {}, {}
    for item in items:
        split = assignments[item["id"]]
        assert by_sheet.setdefault((item["project"], item["source_sheet"]), split) == split
        assert by_query.setdefault(builder.query_signature(item), split) == split
    assert details["near_edges"]
    assert all(assignments[a] == assignments[b] for a, b in details["near_edges"])


def test_uncertain_candidate_is_pending_and_receives_no_approved_code():
    old = {"db_name": "样例阀", "db_spec": "DN100"}
    current = {"name": "样例阀", "specification": "DN100", "unit": "个", "brand": "",
               "description": "", "category": "阀"}
    key = builder.identity(current)
    result = builder.classify("OLD", old, {key: {"NEW": current}}, {key[0]: {"NEW"}}, {},
                              {}, {}, {})
    assert result["status"] == "pending_business_review"
    assert result["approved_codes"] == []
    assert result["exact_text_codes"] == ["NEW"]
