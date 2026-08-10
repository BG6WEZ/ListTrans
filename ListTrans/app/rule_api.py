# -*- coding: utf-8 -*-
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from app.auth_middleware import require_employee, require_manager, require_admin, get_current_user
from app.llm_rule_engine import (
    LLMRuleManager, LLMProviderManager, DynamicRuleInterpreter, RuleHitStatsTracker
)

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

class RuleAuditRequest(BaseModel):
    action: str = Field(..., pattern="^(approve|reject|deprecate)$")
    comment: Optional[str] = ""

@router.post("/generate")
async def generate_rule(req: RuleGenerateRequest, current_user: dict = Depends(require_employee)):
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
async def test_rule(req: RuleTestRequest, current_user: dict = Depends(require_employee)):
    """Test rule DSL against sample raw text."""
    res = LLMRuleManager.test_rule_execution(
        dsl_json=req.parsed_dsl,
        sample_text=req.sample_text,
        candidate_sample=req.candidate_sample
    )
    return res

@router.get("")
async def list_rules(
    status: Optional[str] = None,
    discipline: Optional[str] = None,
    current_user: dict = Depends(require_employee)
):
    """Retrieve rule list filtered by status and discipline."""
    return LLMRuleManager.list_rules(status=status, discipline=discipline)

@router.post("")
async def save_rule_draft(rule_data: Dict[str, Any], current_user: dict = Depends(require_employee)):
    """Save or update a rule draft."""
    res = LLMRuleManager.save_rule_draft(rule_data, current_user)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "保存失败。"))
    return res

@router.post("/{rule_id}/submit")
async def submit_rule(rule_id: str, current_user: dict = Depends(require_employee)):
    """Submit a draft rule for manager/director audit."""
    res = LLMRuleManager.submit_rule(rule_id, current_user)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "提交失败。"))
    return res

@router.post("/{rule_id}/audit")
async def audit_rule(rule_id: str, req: RuleAuditRequest, current_user: dict = Depends(require_manager)):
    """Manager/Director audit endpoint (approve, reject, or deprecate)."""
    res = LLMRuleManager.audit_rule(
        rule_id=rule_id,
        action=req.action,
        comment=req.comment or "",
        user_info=current_user
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "审核失败。"))
    return res

@router.get("/stats")
async def get_rule_stats(current_user: dict = Depends(require_employee)):
    """Retrieve rule hit statistics and leaderboard."""
    return LLMRuleManager.get_rule_stats()
