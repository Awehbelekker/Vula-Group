"""core/agent_runner.py — selective multi-branch reasoning (Master Build Brief section 5b,
2026-09-15).

HRM already plans up to 3 branches + VOTE/SYNTHESIZE merge at complexity==3
(core/hrm/orchestrator.py), but MAX_AGENT_BRANCHES defaults to 1 in prod (confirmed set
explicitly to 1 in Railway) — so that planning was dead code at runtime for every caller.
Rather than raising the cap globally, only the hardest, highest-stakes skills get a 2nd
branch at complexity 3: architecture_planning, reasoning, standards_lookup. Both WhatsApp
paths and the dashboard's main chat (vula/api/chat.py, reusing _rag_reply) already pass an
EXPLICIT max_branches=1 cost cap and must never be affected by this — only the env-default
fallback path (today: /v1/agent/run) can get the extra branch.

Mocks HRMOrchestrator.plan and get_skill so this exercises only the branch-capping logic in
AgentRunner.run, not real routing/LLM calls — matching the direct-function-call testing
style used throughout this session (no FastAPI TestClient, no real skills).
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.agent_runner import AgentRunner
from core.skills.base import SkillOutput
from core.thinkmesh.graph import DeviceRole, GraphStatus, ModelTier, TaskGraph


def _planner(complexity: int, skill_id: str, branch_count: int):
    """A drop-in replacement for HRMOrchestrator.plan that skips real routing and just
    populates graph.branches the way the real planner would for the given complexity."""
    def _plan(self, graph: TaskGraph, use_llm_scoring: bool = False) -> TaskGraph:
        graph.status = GraphStatus.PLANNING
        graph.complexity = complexity
        for i in range(branch_count):
            graph.add_branch(
                device_role=DeviceRole.PRIMARY if i == 0 else DeviceRole.SECONDARY,
                model_tier=ModelTier.REASONER,
                skill_id=skill_id,
                prompt=graph.original_prompt,
            )
        return graph
    return _plan


def _fake_skill():
    """A minimal stand-in skill: no LLM call, instant, always succeeds."""
    return AsyncMock(return_value=SkillOutput(answer="ok", skill_name="x", confidence=0.9))


async def _run(complexity: int, skill_id: str, branch_count: int, **run_kwargs):
    runner = AgentRunner()
    with (
        patch("core.hrm.orchestrator.HRMOrchestrator.plan",
              _planner(complexity, skill_id, branch_count)),
        patch("core.agent_runner.get_skill", return_value=_fake_skill()),
        # Reflection is fire-and-forget via run_in_executor — silence it so tests don't spin
        # up real Supabase/reflection machinery in the background.
        patch("core.memory.reflection.ReflectionAgent"),
    ):
        return await runner.run(question="q", tenant_id="off-the-hook", **run_kwargs)


@pytest.mark.asyncio
async def test_high_stakes_skill_at_complexity_3_gets_two_branches():
    """The actual fix: standards_lookup at complexity 3, no caller override → cap raised
    from the env default (1) to 2, and HRM had planned 3, so 2 survive the truncation."""
    result = await _run(complexity=3, skill_id="standards_lookup", branch_count=3)
    assert result.branch_count == 2


@pytest.mark.asyncio
async def test_architecture_planning_and_reasoning_also_qualify():
    for skill in ("architecture_planning", "reasoning"):
        result = await _run(complexity=3, skill_id=skill, branch_count=3)
        assert result.branch_count == 2, skill


@pytest.mark.asyncio
async def test_non_qualifying_skill_at_complexity_3_stays_capped_at_one():
    """commerce_assistant is real, high-volume, and cost-sensitive — must NOT get a 2nd
    branch just because a question happened to score complexity 3."""
    result = await _run(complexity=3, skill_id="commerce_assistant", branch_count=3)
    assert result.branch_count == 1


@pytest.mark.asyncio
async def test_qualifying_skill_at_lower_complexity_not_raised():
    """The raise is gated on complexity==3, not just the skill name."""
    result = await _run(complexity=2, skill_id="standards_lookup", branch_count=2)
    assert result.branch_count == 1


@pytest.mark.asyncio
async def test_explicit_caller_cap_always_wins_even_for_qualifying_skill():
    """WhatsApp's _rag_reply passes max_branches=1 as a deliberate, commented cost cap —
    this must never be silently overridden, even for standards_lookup at complexity 3."""
    result = await _run(complexity=3, skill_id="standards_lookup", branch_count=3,
                         max_branches=1)
    assert result.branch_count == 1


@pytest.mark.asyncio
async def test_explicit_caller_cap_above_default_also_respected():
    """An explicit cap of 3 (not just 1) should pass straight through untouched — the
    selective raise only ever applies when the caller didn't specify a cap."""
    result = await _run(complexity=3, skill_id="standards_lookup", branch_count=3,
                         max_branches=3)
    assert result.branch_count == 3
