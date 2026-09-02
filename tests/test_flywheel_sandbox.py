# -*- coding: utf-8 -*-
"""
Tests for Subprocess Sandbox Isolation and Regression Double-Gate (tests/test_flywheel_sandbox.py).
Validates:
1. Harmful rule injection is strictly intercepted (passed=False, degraded_from_top1 or degraded_count).
2. Beneficial rule injection passes regression gate (passed=True, improved_count > 0).
3. Empty targeted set is strictly intercepted without silent bypass (passed=False).
"""
import time
import pytest
from app.flywheel_sandbox import start_sandbox_evaluation, get_eval_task_status

def _wait_for_task(task_id: str, timeout: float = 120.0):
    start_t = time.time()
    while time.time() - start_t < timeout:
        status = get_eval_task_status(task_id)
        if status and status["status"] in ("completed", "failed"):
            return status
        time.sleep(0.5)
    raise TimeoutError(f"Sandbox task {task_id} timed out after {timeout}s")

def test_sandbox_harmful_rule_rejected():
    """Injecting a harmful alias rule ('排风机' -> '电缆') must be rejected by sandbox regression gate."""
    harmful_suggestion = {
        "suggestion_type": "ALIAS",
        "source_pattern": "排风机",
        "target_value": "低压交联聚乙烯绝缘聚氯乙烯护套电力电缆"
    }
    task_id = start_sandbox_evaluation(harmful_suggestion)
    result = _wait_for_task(task_id)
    
    assert result["status"] == "completed"
    assert result["passed"] is False, "Harmful rule must NOT pass sandbox gate"
    report = result["report"]
    assert report["targeted_count"] > 0
    assert report["degraded_count"] > 0 or report["degraded_from_top1"] > 0 or report["improved_count"] == 0
    assert "门禁拦截" in report["reason"]

def test_sandbox_beneficial_rule_accepted():
    """Injecting a beneficial binding rule ('医用静音型排风机' -> bound material) must pass sandbox gate."""
    beneficial_suggestion = {
        "suggestion_type": "BINDING",
        "source_pattern": "医用静音型排风机",
        "target_value": [
            {"code": "3.50.29.1369", "name": "医用离心式排风机(箱）", "spec": "风量：1000m³/h，机外余压250pa，镀锌钢板", "weight": 1.0}
        ]
    }
    task_id = start_sandbox_evaluation(beneficial_suggestion)
    result = _wait_for_task(task_id)
    
    assert result["status"] == "completed"
    assert result["passed"] is True, f"Beneficial rule failed: {result}"
    report = result["report"]
    assert report["targeted_count"] > 0
    assert report["degraded_from_top1"] == 0

def test_sandbox_empty_keyword_rejected():
    """A rule with a keyword that matches 0 golden set cases must return passed=False without silent bypass."""
    empty_suggestion = {
        "suggestion_type": "ALIAS",
        "source_pattern": "NONEXISTENT_KEYWORD_XYZ999",
        "target_value": "测试不存在的物料"
    }
    task_id = start_sandbox_evaluation(empty_suggestion)
    result = _wait_for_task(task_id)
    
    assert result["status"] == "completed"
    assert result["passed"] is False, "Empty targeted set must return passed=False"
    report = result["report"]
    assert report["targeted_count"] == 0
    assert "定向子集为空" in report["reason"]

def test_sandbox_neutral_rule_passes_with_flag():
    """Injecting a neutral/steady-state rule (improved=0, degraded=0) must pass gate with steady-state reason."""
    neutral_suggestion = {
        "suggestion_type": "ALIAS",
        "source_pattern": "磷酸脱氧无缝紫铜管",
        "target_value": "磷酸脱氧无缝紫铜管"
    }
    task_id = start_sandbox_evaluation(neutral_suggestion)
    result = _wait_for_task(task_id)
    
    assert result["status"] == "completed"
    assert result["passed"] is True, f"Neutral rule must pass sandbox gate: {result}"
    report = result["report"]
    assert report["targeted_count"] > 0
    assert report["degraded_from_top1"] == 0
    assert report["degraded_count"] == 0
    assert "稳态无退化" in report["reason"] or "通过" in report["reason"]

