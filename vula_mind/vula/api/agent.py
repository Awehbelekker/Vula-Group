"""
vula/api/agent.py

The Vula multi-agent endpoint. Exposes the full HRM → skills → ThinKMesh
loop as a single API. Unlike /query (simple RAG), this routes the question
to the right skill(s), runs parallel branches, and merges.

Endpoints:
  POST /v1/agent/run         — run the multi-agent loop on a question
  GET  /v1/agent/skills      — list available skills
  GET  /v1/agent/stats       — reflection store statistics
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field, field_validator

from config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agent"])

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_auth(api_key: str | None = Security(_api_key_header)) -> None:
    import secrets as _s
    if settings.api_key and (not api_key or not _s.compare_digest(api_key, settings.api_key)):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


class AgentRequest(BaseModel):
    tenant_id: str
    question: str = Field(..., min_length=2, max_length=2000)
    conversation_history: str = ""
    use_llm_scoring: bool = False

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_\-]{1,64}$", v):
            raise ValueError("Invalid tenant_id")
        return v


class AgentResponse(BaseModel):
    answer: str
    skill_used: str
    complexity: int
    merge_strategy: str
    branch_count: int
    confidence: float
    latency_ms: int
    sources: list
    branches: list
    task_id: str
    tenant_id: str


@router.post("/agent/run", response_model=AgentResponse,
             dependencies=[Depends(_require_auth)])
async def run_agent(body: AgentRequest) -> AgentResponse:
    """Run the full multi-agent loop: HRM routing → parallel skills → merge."""
    from core.agent_runner import get_agent_runner

    runner = get_agent_runner()
    result = await runner.run(
        question=body.question,
        tenant_id=body.tenant_id,
        conversation_history=body.conversation_history,
        use_llm_scoring=body.use_llm_scoring,
    )

    return AgentResponse(
        answer=result.final_answer,
        skill_used=result.skill_used,
        complexity=result.complexity,
        merge_strategy=result.merge_strategy,
        branch_count=result.branch_count,
        confidence=result.confidence,
        latency_ms=result.latency_ms,
        sources=result.sources,
        branches=result.branches,
        task_id=result.task_id,
        tenant_id=body.tenant_id,
    )


@router.get("/agent/skills", dependencies=[Depends(_require_auth)])
async def list_skills() -> dict:
    """List all available agent skills."""
    from core.skills.loader import available_skills, _FALLBACK_ALIASES
    return {
        "implemented": available_skills(),
        "aliased": _FALLBACK_ALIASES,
    }


@router.get("/agent/stats", dependencies=[Depends(_require_auth)])
async def agent_stats() -> dict:
    """Return reflection store statistics — how the agent is learning."""
    try:
        from core.memory.reflection import ReflectionAgent
        return ReflectionAgent().get_stats()
    except Exception as exc:
        return {"error": str(exc), "total_reflections": 0}
