# -*- coding: utf-8 -*-
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from app.auth_middleware import require_employee, require_manager, require_admin, get_current_user
from app.llm_rule_engine import (
    LLMRuleManager, LLMProviderManager, DynamicRuleInterpreter, RuleHitStatsTracker
)

from app.runtime_jobs import http_job, rule_jobs

router = APIRouter(prefix="/api/llm-rules", tags=["llm-rules"])

class RuleGenerateRequest(BaseModel):
    scope_level: str = Field("category", pattern="^(universal|discipline|category)$")
    scope_target: Optional[str] = ""
    discipline: Optional[str] = ""
    description: str = Field(..., min_length=2)
    sample_text: Optional[str] = ""

class RuleTestRequest(BaseModel):
    parsed_dsl: Dict[str, Any]
    sample_text: str
    candidate_sample: Optional[str] = ""
    scope_level: Optional[str] = None
    scope_target: Optional[str] = None
    professional: Optional[str] = None
    material_category: Optional[str] = None
    sample_name: Optional[str] = None

class RuleAuditRequest(BaseModel):
    action: str = Field(..., pattern="^(approve|reject|deprecate)$")
    comment: Optional[str] = ""
    expected_revision: Optional[int] = None
    expected_digest: Optional[str] = None

class RulePublishRequest(BaseModel):
    expected_revision: Optional[int] = None
    expected_digest: Optional[str] = None
    eval_id: Optional[str] = None
    neutral_reason: Optional[str] = None

class RuleToggleRequest(BaseModel):
    reason: Optional[str] = ""
    eval_id: Optional[str] = None
    neutral_reason: Optional[str] = None

def _raise_rule_error(res: Dict[str, Any], default_msg: str):
    code = int(res.get("status_code") or 400)
    if res.get("error_code"):
        detail: Any = {
            "code": res["error_code"],
            "message": res.get("error", default_msg),
        }
        if isinstance(res.get("details"), dict) and res["details"]:
            detail.update(res["details"])
        raise HTTPException(status_code=code, detail=detail)
    raise HTTPException(status_code=code, detail=res.get("error", default_msg))

@router.post("/generate")
@http_job(rule_jobs, timeout=120)
def generate_rule(req: RuleGenerateRequest, current_user: dict = Depends(require_employee)):
    """Generate Rule DSL JSON from natural language description using priority LLM router."""
    res = LLMRuleManager.generate_rule_from_prompt(
        description=req.description,
        scope_level=req.scope_level,
        scope_target=req.scope_target,
        discipline=req.discipline,
        sample_text=req.sample_text
    )
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error", "生成规则失败。"))
    return res

@router.post("/test")
@http_job(rule_jobs, timeout=120)
def test_rule(req: RuleTestRequest, current_user: dict = Depends(require_employee)):
    """Test rule DSL against sample raw text with optional RuleContext evaluation."""
    res = LLMRuleManager.test_rule_execution(
        dsl_json=req.parsed_dsl,
        sample_text=req.sample_text,
        candidate_sample=req.candidate_sample,
        scope_level=req.scope_level,
        scope_target=req.scope_target,
        professional=req.professional,
        material_category=req.material_category,
        sample_name=req.sample_name,
    )
    return res

# Register static and dedicated evaluation GET paths before /{rule_id}.
@router.get("/stats")
def get_rule_stats(current_user: dict = Depends(require_employee)):
    """Retrieve rule hit statistics and leaderboard (registered before /{rule_id})."""
    return LLMRuleManager.get_rule_stats()

@router.get("/eval/{eval_id}")
def get_rule_eval_status(eval_id: str, current_user: dict = Depends(require_employee)):
    """Retrieve sandbox evaluation status for an LLM DSL rule (registered before /{rule_id})."""
    from app.flywheel_sandbox import get_eval_task_status_for_user

    st = get_eval_task_status_for_user(eval_id, current_user)
    if not st:
        raise HTTPException(status_code=404, detail="评测任务不存在。")
    return st

@router.get("")
def list_rules(
    status: Optional[str] = None,
    discipline: Optional[str] = None,
    current_user: dict = Depends(require_employee)
):
    """Retrieve rule list filtered by status and discipline."""
    return LLMRuleManager.list_rules(status=status, discipline=discipline)

@router.post("")
def save_rule_draft(rule_data: Dict[str, Any], current_user: dict = Depends(require_employee)):
    """Save or update a rule draft (author forced from server-verified user)."""
    res = LLMRuleManager.save_rule_draft(rule_data, current_user)
    if not res.get("success"):
        _raise_rule_error(res, "保存失败。")
    return res

@router.post("/{rule_id}/submit")
def submit_rule(rule_id: str, current_user: dict = Depends(require_employee)):
    """Submit an author's draft or rejected rule for manager/director/admin audit."""
    res = LLMRuleManager.submit_rule(rule_id, current_user)
    if not res.get("success"):
        _raise_rule_error(res, "提交失败。")
    return res

@router.post("/{rule_id}/withdraw")
def withdraw_rule(rule_id: str, current_user: dict = Depends(require_employee)):
    """Author withdraws a pending_approval rule back to draft before editing."""
    res = LLMRuleManager.withdraw_rule(rule_id, current_user)
    if not res.get("success"):
        _raise_rule_error(res, "撤回失败。")
    return res

@router.post("/{rule_id}/audit")
def audit_rule(rule_id: str, req: RuleAuditRequest, current_user: dict = Depends(require_manager)):
    """Manager/Director/Admin audit endpoint (approve->approved_pending_publish, reject, or deprecate)."""
    res = LLMRuleManager.audit_rule(
        rule_id=rule_id,
        action=req.action,
        comment=req.comment or "",
        user_info=current_user,
        expected_revision=req.expected_revision,
        expected_digest=req.expected_digest,
    )
    if not res.get("success"):
        _raise_rule_error(res, "审核失败。")
    return res

@router.post("/{rule_id}/eval")
@http_job(rule_jobs, timeout=120)
def start_rule_eval(rule_id: str, current_user: dict = Depends(require_employee)):
    """Start sandbox evaluation for an LLM DSL rule and return eval_id/task_id."""
    res = LLMRuleManager.start_rule_evaluation(rule_id, current_user)
    if not res.get("success"):
        _raise_rule_error(res, "启动沙箱评测失败。")
    return res

@router.post("/{rule_id}/publish")
@http_job(rule_jobs, timeout=120)
def publish_rule(
    rule_id: str,
    req: Optional[RulePublishRequest] = None,
    current_user: dict = Depends(require_admin),
):
    """Admin-only activation endpoint promoting approved_pending_publish -> published."""
    res = LLMRuleManager.publish_rule(
        rule_id=rule_id,
        user_info=current_user,
        expected_revision=req.expected_revision if req else None,
        expected_digest=req.expected_digest if req else None,
        eval_id=req.eval_id if req else None,
        neutral_reason=req.neutral_reason if req else None,
    )
    if not res.get("success"):
        _raise_rule_error(res, "发布失败。")
    return res

@router.get("/{rule_id}")
def get_rule(rule_id: str, current_user: dict = Depends(require_employee)):
    """Retrieve single rule detail with parsed DSL."""
    rule = LLMRuleManager.get_rule_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在。")
    return rule

@router.put("/{rule_id}")
def update_rule(rule_id: str, rule_data: Dict[str, Any], current_user: dict = Depends(require_employee)):
    """Update an existing rule DSL, parameters, and configuration (clears approval and reverts to draft)."""
    res = LLMRuleManager.update_rule(rule_id, rule_data, current_user)
    if not res.get("success"):
        _raise_rule_error(res, "修改失败。")
    return res

@router.post("/{rule_id}/toggle")
@http_job(rule_jobs, timeout=120)
def toggle_rule(
    rule_id: str,
    req: Optional[RuleToggleRequest] = None,
    current_user: dict = Depends(require_admin),
):
    """Admin-only toggle rule published/disabled status (停用/启用)."""
    res = LLMRuleManager.toggle_rule_status(
        rule_id,
        current_user,
        reason=req.reason if req and req.reason else "",
        eval_id=req.eval_id if req else None,
        neutral_reason=req.neutral_reason if req else None,
    )
    if not res.get("success"):
        _raise_rule_error(res, "操作失败。")
    return res

@router.delete("/{rule_id}")
def delete_rule(rule_id: str, current_user: dict = Depends(require_employee)):
    """Delete an author's unpublished draft/rejected rule."""
    res = LLMRuleManager.delete_rule(rule_id, current_user)
    if not res.get("success"):
        _raise_rule_error(res, "删除失败。")
    return res


@router.post("/eval/{eval_id}/cancel")
def cancel_rule_eval(eval_id: str, current_user: dict = Depends(require_employee)):
    from app.flywheel_sandbox import cancel_eval_task

    result = cancel_eval_task(eval_id, current_user)
    if result is None:
        raise HTTPException(404, "评测任务不存在。")
    if not result:
        raise HTTPException(409, "评测任务已结束。")
    return {"cancel_requested": True}
