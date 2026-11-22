# -*- coding: utf-8 -*-
"""
Tests for Subprocess Sandbox Isolation and Regression Double-Gate (tests/test_flywheel_sandbox.py).
Validates:
1. Harmful rule injection is strictly intercepted (passed=False, degraded_from_top1 or degraded_count).
2. Beneficial rule injection passes regression gate (passed=True, improved_count > 0).
3. Empty targeted set is strictly intercepted without silent bypass (passed=False).

V015-M1 / LEGACY-01：沙箱评测链路依赖物料库召回。隔离测试库默认为空时
rank 恒 0，门禁以"稳态无退化"放行有害规则（R04 诊断）。本模块通过
sandbox_materials_v1.json 合成脱敏物料（金标目标物料 + 有害别名目标 + 中性
对照）播种隔离库，使基线 rank 可观察、候选/基线配置差异真实生效。
"""
import os
import json
import hashlib
import time
import copy
import pytest
from app.flywheel_sandbox import (
    _compare_evaluation_snapshots,
    _compare_sandbox_rows,
    get_eval_task_status,
    start_sandbox_evaluation,
)

SANDBOX_MATERIALS_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "sandbox_materials_v1.json"
)


@pytest.fixture(scope="module", autouse=True)
def seed_isolated_materials():
    """把合成脱敏物料播种进本会话隔离物料库（沙箱子进程经 MATERIALS_DB_PATH 读取）。

    - 物料来源：tests/fixtures/sandbox_materials_v1.json（版本化合成数据，
      由 golden_set_v1.json 的 target_ref 合成 + 有害别名目标电缆 + 中性对照），
      不触及生产 materials.db 与正式金标。
    - 落库走生产导入入口 import_materials_from_excel（经 pandas 合成临时 XLSX），
      与真实上传链路一致，自动建 FTS 索引与 parsed_params。
    """
    if not os.path.exists(SANDBOX_MATERIALS_FIXTURE):
        pytest.skip(f"合成物料 fixture 缺失: {SANDBOX_MATERIALS_FIXTURE}")
    with open(SANDBOX_MATERIALS_FIXTURE, encoding="utf-8") as f:
        fixture = json.load(f)
    materials = fixture["materials"]
    assert materials, "合成物料 fixture 不能为空"

    import pandas as pd
    from app.db_manager import import_materials_from_excel, get_all_materials

    upload_dir = os.getenv("UPLOAD_DIR", "")
    assert upload_dir, "隔离环境未注入 UPLOAD_DIR"
    xlsx_path = os.path.join(upload_dir, "sandbox_materials_v1.xlsx")
    df = pd.DataFrame([
        {
            "物料分类": m["category"],
            "物料编码": m["code"],
            "物料名称": m["name"],
            "规格/型号": m["spec"],
            "计量单位": "台",
            "常用品牌": "",
            "描述": "",
        }
        for m in materials
    ])
    # 第 0 行留空、第 1 行为真实表头（导入入口按 header=1 读取，同 integration_test 模式）
    df.to_excel(xlsx_path, index=False, startrow=1)

    meta = import_materials_from_excel(xlsx_path, mode="replace", filename="sandbox_materials_v1.xlsx")
    seeded = get_all_materials(limit=10 ** 6)
    assert meta.get("imported_rows", 0) > 0
    assert len(seeded) == len(materials), f"播种行数不符: {len(seeded)} != {len(materials)}"

    # 证据要素：fixture 快照哈希 + 离线模型标识 + 目标码覆盖
    with open(SANDBOX_MATERIALS_FIXTURE, "rb") as f:
        fixture_sha = hashlib.sha256(f.read()).hexdigest()
    from app.vector_index import index_manager
    yield {
        "fixture_sha256": fixture_sha,
        "seeded_rows": len(seeded),
        "vector_model": getattr(index_manager, "model_name", "") or "unknown",
        "vector_enabled": bool(index_manager.is_enabled),
        "codes": {m["code"] for m in materials},
    }


def _wait_for_task(task_id: str, timeout: float = 120.0):
    start_t = time.time()
    while time.time() - start_t < timeout:
        status = get_eval_task_status(task_id)
        if status and status["status"] in ("completed", "failed"):
            return status
        time.sleep(0.5)
    raise TimeoutError(f"Sandbox task {task_id} timed out after {timeout}s")


def test_sandbox_harmful_rule_rejected(seed_isolated_materials):
    """Injecting a harmful alias rule ('排风机' -> '电缆') must be rejected by sandbox regression gate."""
    harmful_suggestion = {
        "suggestion_type": "ALIAS",
        "source_pattern": "排风机",
        "target_value": "低压交联聚乙烯绝缘聚氯乙烯护套电力电缆"
    }
    task_id = start_sandbox_evaluation(harmful_suggestion)
    result = _wait_for_task(task_id, timeout=300.0)

    assert result["status"] == "completed"
    assert result["passed"] is False, (
        "Harmful rule must NOT pass sandbox gate: "
        f"{json.dumps(result.get('report'), ensure_ascii=False)}"
    )
    report = result["report"]
    assert report["targeted_count"] > 0
    assert report["degraded_count"] > 0 or report["degraded_from_top1"] > 0 or report["improved_count"] == 0
    assert "门禁拦截" in report["reason"]


def test_sandbox_beneficial_rule_accepted(seed_isolated_materials):
    """Injecting a scoped constrained beneficial binding rule passes M2 gate with improved benefit."""
    beneficial_suggestion = {
        "suggestion_type": "BINDING",
        "discipline": "暖通",
        "original_spec": "风量：1000m3/h",
        "source_pattern": "医用静音型排风机",
        "target_value": [
            {
                "code": "3.50.29.1369",
                "name": "医用离心式排风机(箱）",
                "specification": "风量：1000m³/h，机外余压250pa，镀锌钢板",
                "unit": "台",
                "qty_ratio": 1.0,
            }
        ],
    }
    task_id = start_sandbox_evaluation(beneficial_suggestion)
    result = _wait_for_task(task_id, timeout=300.0)

    assert result["status"] == "completed"
    assert result["passed"] is True, f"Beneficial rule failed: {result}"
    assert result["safety_passed"] is True
    assert result["benefit"] == "improved"
    report = result["report"]
    assert report["targeted_count"] > 0
    assert report["guard_count"] > 0
    assert report["degraded_from_top1"] == 0
    assert report["binding_degraded_count"] == 0
    assert report["binding_improved_count"] >= 1
    assert report["rule_execution_hits"] >= 1


def test_sandbox_unconstrained_wrong_binding_rejected(seed_isolated_materials):
    """Unconstrained binding hijacks other airflows in real batch_match_rows and must fail closed."""
    wrong_binding = {
        "suggestion_type": "BINDING",
        "discipline": "暖通",
        "source_pattern": "医用静音型排风机",
        "target_value": [
            {
                "code": "3.50.29.1369",
                "name": "医用离心式排风机(箱）",
                "specification": "风量：1000m³/h，机外余压250pa，镀锌钢板",
                "unit": "台",
                "qty_ratio": 1.0,
            }
        ],
    }
    task_id = start_sandbox_evaluation(wrong_binding)
    result = _wait_for_task(task_id, timeout=300.0)

    assert result["status"] == "completed"
    assert result["passed"] is False
    assert result["safety_passed"] is False
    assert result["benefit"] == "harmful"
    assert result["report"]["binding_degraded_count"] > 0


def test_sandbox_empty_keyword_rejected(seed_isolated_materials):
    """A rule with a keyword that matches 0 golden set cases must return passed=False without silent bypass."""
    empty_suggestion = {
        "suggestion_type": "ALIAS",
        "source_pattern": "NONEXISTENT_KEYWORD_XYZ999",
        "target_value": "测试不存在的物料"
    }
    task_id = start_sandbox_evaluation(empty_suggestion)
    result = _wait_for_task(task_id, timeout=300.0)

    assert result["status"] == "completed"
    assert result["passed"] is False, "Empty targeted set must return passed=False"
    report = result["report"]
    assert report["targeted_count"] == 0
    assert "定向子集为空" in report["reason"]


def test_sandbox_neutral_rule_passes_with_flag(seed_isolated_materials):
    """Injecting a neutral/steady-state rule (improved=0, degraded=0) must pass gate with steady-state reason."""
    neutral_suggestion = {
        "suggestion_type": "ALIAS",
        "source_pattern": "磷酸脱氧无缝紫铜管",
        "target_value": "磷酸脱氧无缝紫铜管"
    }
    task_id = start_sandbox_evaluation(neutral_suggestion)
    result = _wait_for_task(task_id, timeout=300.0)

    assert result["status"] == "completed"
    assert result["passed"] is True, f"Neutral rule must pass sandbox gate: {result}"
    report = result["report"]
    assert report["targeted_count"] > 0
    assert report["degraded_from_top1"] == 0
    assert report["degraded_count"] == 0
    assert "稳态无退化" in report["reason"] or "通过" in report["reason"]


def test_sandbox_fixture_rank_observable(seed_isolated_materials, monkeypatch):
    """LEGACY-01 收口证据：基线 rank 可观察（非全 0），有害别名与中性对照差异真实生效。

    直接调用真实评测入口 evaluate()（沙箱子进程同一链路），断言：
    - 排风机相关用例存在 rank>0（目标物料可被召回排序）；
    - 有害别名注入后同一用例 rank 恶化（基线 top1 -> 候选非 top1 或 0）。
    """
    import io
    import sys
    # This direct replay isolates the lexical effect of an alias. Import now also
    # builds vectors, so select the keyword path explicitly. The preceding sandbox
    # tests still exercise the full subprocess gate with the real vector model.
    monkeypatch.setenv("LISTTRANS_EVAL_KEYWORD_ONLY", "1")
    test_dir = os.path.dirname(__file__)
    # eval_golden_set 在模块级把 sys.stdout 替换为 UTF-8 wrapper（Windows 控制台
    # GBK 兼容），该 wrapper 析构时会关闭其包装的底层流；在 pytest 下 sys.stdout
    # 是捕获流（TemporaryFile），被其关闭后 pytest 后续写捕获流即报
    # "I/O operation on closed file"。导入前临时换用独立缓冲，导入后恢复。
    prev_stdout = sys.stdout
    try:
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        sys.path.insert(0, test_dir)
        import eval_golden_set as _egs
        evaluate = _egs.evaluate
    finally:
        sys.stdout = prev_stdout
        sys.path.pop(0)

    golden_path = os.path.join(os.path.dirname(__file__), "fixtures", "golden_set_v1.json")
    with open(golden_path, encoding="utf-8") as f:
        data = json.load(f)
    items = [
        x for x in data["items"]
        if x["label_type"] == "1to1" and not x["ambiguous"] and not x.get("qc_suspect")
        and "排风机" in x.get("name", "").lower()
    ]
    assert items, "金标中排风机用例缺失"

    # 中性对照子集（磷酸脱氧无缝紫铜管），用于证明配置差异只影响目标子集
    neutral_items = [
        x for x in data["items"]
        if x["label_type"] == "1to1" and not x["ambiguous"] and not x.get("qc_suspect")
        and "紫铜管" in x.get("name", "").lower()
    ]

    base_rows, _ = evaluate(items, chunk_size=50)
    base_rank_map = {r["id"]: r["rank"] for r in base_rows}
    hit = sum(1 for r in base_rows if r["rank"] > 0)
    top1 = sum(1 for r in base_rows if r["rank"] == 1)
    assert top1 > 0, f"基线应有 top1 命中（fixture 未生效? rows={base_rows[:2]})"

    neutral_rows, _ = evaluate(neutral_items, chunk_size=50)
    neutral_hit = sum(1 for r in neutral_rows if r["rank"] > 0)
    assert neutral_hit > 0, "中性对照子集应存在 rank>0"

    # 有害别名注入：排风机 -> 电缆（与 sandbox harmful 测试同款规则）。
    # 别名在 normalizer.clean_text_standard 内生效，经 reload_normalizer_configs 重载。
    from app.normalizer import reload_normalizer_configs
    alias_path = os.path.join(os.getenv("CONFIG_DIR", ""), "alias_dict.json")
    with open(alias_path, encoding="utf-8") as f:
        alias_data = json.load(f)
    original_alias_data = dict(alias_data)
    try:
        alias_data["排风机"] = "低压交联聚乙烯绝缘聚氯乙烯护套电力电缆"
        with open(alias_path, "w", encoding="utf-8") as f:
            json.dump(alias_data, f, ensure_ascii=False, indent=4)
        reload_normalizer_configs()

        cand_rows, _ = evaluate(items, chunk_size=50)
        cand_rank_map = {r["id"]: r["rank"] for r in cand_rows}
        degraded_from_top1 = sum(
            1 for cid, br in base_rank_map.items() if br == 1 and cand_rank_map.get(cid, 0) != 1
        )
        assert degraded_from_top1 > 0, (
            "有害别名应导致 top1 恶化（配置差异未生效? "
            f"base_top1={top1}, hit={hit})"
        )

        # 中性子集不受有害别名影响（rank 保持，证明差异只作用于目标子集）
        neutral_after, _ = evaluate(neutral_items, chunk_size=50)
        neutral_after_map = {r["id"]: r["rank"] for r in neutral_after}
        unaffected = sum(
            1 for r in neutral_rows
            if neutral_after_map.get(r["id"], 0) == r["rank"]
        )
        assert unaffected >= neutral_hit, (
            f"中性对照 rank 不应恶化: unaffected={unaffected}, hit={neutral_hit}"
        )
    finally:
        # 还原隔离配置快照中的别名表并重载
        with open(alias_path, "w", encoding="utf-8") as f:
            json.dump(original_alias_data, f, ensure_ascii=False, indent=4)
        reload_normalizer_configs()


# —— R02 / LEGACY-01：纯数据边界回归；不替代上方真实双子进程对照 ——
def _row(case_id="case-a", rank=1, scores=None):
    if scores is None:
        scores = {f"{case_id}-target": 80.0, f"{case_id}-other": 60.0}
    return {
        "id": case_id,
        "name": f"合成用例 {case_id}",
        "rank": rank,
        "target_codes": [f"{case_id}-target"],
        "candidates": [{"code": code, "score": score} for code, score in scores.items()],
    }


def _violation_types(report):
    return {v["type"] for v in report["violations"]}


def test_compare_target_score_decrease_rejected_without_rank_change():
    passed, report = _compare_sandbox_rows(
        [_row()], [_row(scores={"case-a-target": 79.0, "case-a-other": 59.0})]
    )
    assert not passed
    assert report["degraded_from_top1"] == 0
    assert "target_score_decreased" in _violation_types(report)


def test_compare_margin_shrink_rejected_with_unchanged_target_score():
    passed, report = _compare_sandbox_rows(
        [_row()], [_row(scores={"case-a-target": 80.0, "case-a-other": 70.0})]
    )
    assert not passed
    violation = next(v for v in report["violations"] if v["type"] == "margin_shrunk")
    assert violation["base_T"] == violation["cand_T"] == 80.0
    assert violation["base_margin"] == 20.0
    assert violation["cand_margin"] == 10.0


def test_compare_per_case_violation_not_offset_by_other_improvement():
    base = [
        _row(),
        _row(case_id="case-b", rank=3, scores={"case-b-target": 50.0, "case-b-other": 60.0}),
    ]
    cand = [
        _row(scores={"case-a-target": 70.0, "case-a-other": 60.0}),
        _row(case_id="case-b", rank=1, scores={"case-b-target": 70.0, "case-b-other": 60.0}),
    ]
    passed, report = _compare_sandbox_rows(base, cand)
    assert not passed
    assert report["improved_count"] == 1
    assert any(
        v["id"] == "case-a" and v["type"] == "target_score_decreased"
        for v in report["violations"]
    )


def test_compare_original_rank_gates_preserved():
    base = [
        _row(),
        _row(case_id="case-b", rank=2),
        _row(case_id="case-c", rank=3),
    ]
    cand = [
        _row(rank=2, scores={"case-a-other": 90.0, "case-a-target": 80.0}),
        _row(case_id="case-b", rank=3),
        _row(case_id="case-c", rank=4),
    ]
    passed, report = _compare_sandbox_rows(base, cand)
    assert not passed
    assert report["degraded_from_top1"] == 1
    assert report["degraded_count"] == 2


def test_compare_beneficial_and_neutral_rows():
    base = [
        _row(),
        _row(case_id="case-b", rank=2, scores={"case-b-other": 90.0, "case-b-target": 70.0}),
    ]
    cand = [
        _row(scores={"case-a-target": 85.0, "case-a-other": 60.0}),
        _row(case_id="case-b", rank=1, scores={"case-b-other": 60.0, "case-b-target": 70.0}),
    ]
    passed, report = _compare_sandbox_rows(base, cand)
    assert passed
    assert report["improved_count"] == 1

    passed, report = _compare_sandbox_rows(base, copy.deepcopy(base))
    assert passed
    assert "稳态无退化" in report["reason"]


def test_compare_only_fixed_absolute_epsilon_allowed():
    within = [_row(scores={"case-a-target": 80.0 - 1e-6, "case-a-other": 60.0})]
    passed, _ = _compare_sandbox_rows([_row()], within)
    assert passed

    beyond = [_row(scores={"case-a-target": 80.0 - 2e-6, "case-a-other": 60.0})]
    passed, report = _compare_sandbox_rows([_row()], beyond)
    assert not passed
    assert "target_score_decreased" in _violation_types(report)


def test_compare_comparable_score_details_retained():
    passed, report = _compare_sandbox_rows([_row()], [_row()])
    assert passed
    detail = next(d for d in report["details"] if d["type"] == "score_protection")
    assert detail["id"] == "case-a"
    assert detail["baseline"]["target_codes"] == ["case-a-target"]
    assert detail["baseline"]["T"] == 80.0
    assert detail["baseline"]["W"] == 60.0
    assert detail["baseline"]["margin"] == 20.0


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("field,expected", [
    ("target_codes", "missing_target_codes"),
    ("rank", "missing_rank"),
    ("error", "eval_error"),
])
def test_compare_missing_required_fields_rejected(side, field, expected):
    base, cand = [_row()], [_row()]
    row = base[0] if side == "baseline" else cand[0]
    if field == "error":
        row[field] = "synthetic evaluation failure"
    else:
        row.pop(field)
    passed, report = _compare_sandbox_rows(base, cand)
    assert not passed
    assert any(
        v["type"] == expected and v.get("side", side) == side
        for v in report["violations"]
    )


@pytest.mark.parametrize("invalid_score", [float("nan"), float("inf"), float("-inf"), "not-a-number", None])
def test_compare_non_finite_or_missing_scores_rejected(invalid_score):
    passed, report = _compare_sandbox_rows(
        [_row()], [_row(scores={"case-a-target": invalid_score, "case-a-other": 60.0})]
    )
    assert not passed
    assert "non_finite_or_missing_score" in _violation_types(report)


def test_compare_non_numeric_rank_rejected():
    passed, report = _compare_sandbox_rows([_row()], [_row(rank="invalid")])
    assert not passed
    assert "non_numeric_rank" in _violation_types(report)


@pytest.mark.parametrize("duplicate_side", ["baseline", "candidate"])
def test_compare_duplicate_sample_ids_rejected(duplicate_side):
    base, cand = [_row()], [_row()]
    (base if duplicate_side == "baseline" else cand).append(_row())
    passed, report = _compare_sandbox_rows(base, cand)
    assert not passed
    assert "样本 ID 重复" in "".join(report["integrity_errors"])


def test_compare_missing_or_mismatched_samples_rejected():
    passed, report = _compare_sandbox_rows([_row(), _row(case_id="case-b")], [_row()])
    assert not passed
    assert "ID 集合不一致" in "".join(report["integrity_errors"])

    passed, report = _compare_sandbox_rows([], [_row()])
    assert not passed
    assert "ID 集合不一致" in "".join(report["integrity_errors"])
    assert "sample_missing_in_baseline" in _violation_types(report)

    missing_id = _row()
    missing_id.pop("id")
    passed, report = _compare_sandbox_rows([missing_id], [missing_id])
    assert not passed
    assert "缺 id" in "".join(report["integrity_errors"])


def test_compare_unverifiable_baseline_rejected():
    passed, report = _compare_sandbox_rows([_row(rank=0)], [_row(rank=0)])
    assert not passed
    assert "没有可验证的正确 Top1 基线" in "".join(report["integrity_errors"])


def test_compare_missing_target_score_rejected():
    passed, report = _compare_sandbox_rows(
        [_row(scores={"case-a-other": 60.0})], [_row(scores={"case-a-other": 60.0})]
    )
    assert not passed
    assert "baseline_target_missing_in_scores" in _violation_types(report)

    passed, report = _compare_sandbox_rows(
        [_row()], [_row(rank=2, scores={"case-a-other": 90.0})]
    )
    assert not passed
    assert "target_score_lost" in _violation_types(report)


@pytest.mark.parametrize("base_scores,cand_scores,expected", [
    ({"case-a-target": 80.0}, {"case-a-target": 80.0, "case-a-other": 60.0}, "noncomparable_new_nontarget"),
    ({"case-a-target": 80.0, "case-a-other": 60.0}, {"case-a-target": 80.0}, "noncomparable_nontarget_lost"),
])
def test_compare_noncomparable_margins_rejected(base_scores, cand_scores, expected):
    passed, report = _compare_sandbox_rows(
        [_row(scores=base_scores)], [_row(scores=cand_scores)]
    )
    assert not passed
    assert expected in _violation_types(report)


def test_compare_no_nontarget_on_both_sides_is_not_silent_regression():
    passed, report = _compare_sandbox_rows(
        [_row(scores={"case-a-target": 80.0})],
        [_row(scores={"case-a-target": 80.0})],
    )
    assert passed
    detail = next(d for d in report["details"] if d["type"] == "score_protection")
    assert detail["baseline"]["margin"] is None
    assert detail["candidate"]["margin"] is None


def test_compare_missing_raw_scores_and_mismatched_targets_rejected():
    rounded_only = _row()
    rounded_only.pop("candidates")
    rounded_only["top"] = [{"code": "case-a-target", "score": 80.0}]
    passed, report = _compare_sandbox_rows([rounded_only], [rounded_only])
    assert not passed
    assert "missing_raw_scores" in _violation_types(report)

    cand = _row()
    cand["target_codes"] = ["another-target"]
    passed, report = _compare_sandbox_rows([_row()], [cand])
    assert not passed
    assert "target_codes_mismatch" in _violation_types(report)


def test_compare_evaluation_snapshots_rejects_missing_or_divergent_channels():
    snapshot = {
        "materials_db_sha256": "materials",
        "golden_set_sha256": "golden",
        "golden_set_item_count": 1,
        "vector_channel": {"model": "offline", "dim": 512},
        "budget": {"chunk_size": 50, "top_k": 10},
    }
    base = {"summary": {"snapshot": snapshot}}
    cand = copy.deepcopy(base)
    assert _compare_evaluation_snapshots(base, cand)["errors"] == []

    cand["summary"]["snapshot"]["vector_channel"]["dim"] = 384
    errors = _compare_evaluation_snapshots(base, cand)["errors"]
    assert any("vector_channel" in e for e in errors)

    cand = copy.deepcopy(base)
    cand["summary"]["snapshot"]["budget"]["top_k"] = 5
    errors = _compare_evaluation_snapshots(base, cand)["errors"]
    assert any("budget" in e for e in errors)

    errors = _compare_evaluation_snapshots({}, base)["errors"]
    assert "缺少运行快照" in "".join(errors)


def test_sandbox_nonzero_subprocess_exit_fails_closed(tmp_path, monkeypatch, seed_isolated_materials):
    """模型替身仅覆盖异常分支：子进程非零退出不得伪造评测通过。"""
    import subprocess
    import app.flywheel_sandbox as sandbox

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(sandbox, "CONFIG_PROD_DIR", str(config_dir))

    def fail_baseline(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 7, stdout="", stderr="synthetic child failure")

    monkeypatch.setattr(sandbox, "_run_eval_process", fail_baseline)
    task_id = start_sandbox_evaluation({
        "suggestion_type": "ALIAS", "source_pattern": "synthetic", "target_value": "synthetic"
    })
    result = _wait_for_task(task_id, timeout=10.0)
    assert result["status"] == "failed"
    assert result["passed"] is False
    assert "rc=7" in result["message"]
    assert "synthetic child failure" in result["message"]
