"""Tests for logprob-confidence escalation rolled out to architecture_planning.py,
standards_lookup.py, and draft_admin.py (2026-08-24 chat-accuracy audit, Phase 7).

Same mechanism as test_skill_confidence_escalation.py (finance_admin/commerce_admin, wired the
same day as this audit) — these three had zero adoption despite architecture_planning answering
life-safety-adjacent construction questions and draft_admin generating real client documents.
calculations.py is deliberately excluded (has its own adequate safe_eval anchor-check instead).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.architecture_planning import ArchitecturePlanningSkill
from core.skills.base import SkillInput
from core.skills.draft_admin import DraftAdminSkill
from core.skills.standards_lookup import StandardsLookupSkill

TENANT = "digg-demo"


def _resp_with_logprob(avg_logprob, content, tool_calls=None):
    token = MagicMock(logprob=avg_logprob)
    logprobs = MagicMock(content=[token])
    message = SimpleNamespace(content=content, tool_calls=tool_calls, logprobs=logprobs)
    choice = MagicMock(logprobs=logprobs, message=message)
    return type("R", (), {"choices": [choice]})()


def _pipeline_mock(chunks):
    mock_pipeline = MagicMock()
    mock_pipeline.query = AsyncMock(return_value=chunks)
    return mock_pipeline


@pytest.mark.asyncio
async def test_architecture_planning_escalates_on_low_confidence():
    call_count = {"n": 0}

    async def _fake_completion(*a, **kw):
        call_count["n"] += 1
        assert kw.get("logprobs") is True
        if call_count["n"] == 1:
            return _resp_with_logprob(-3.0, "maybe 5% retention? not sure")
        return _resp_with_logprob(-0.05, "Retention is 5% per the JBCC contract.")

    escalated = {}

    def _fake_escalate(reason, run_id=None, task_type=None):
        escalated["reason"] = reason
        escalated["task_type"] = task_type
        return ("openrouter/cloud-model", "sk-test", None)

    chunks = [{"filename": "boq.pdf", "text": "Retention: 5%", "score": 0.9}]
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=_pipeline_mock(chunks)),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.skills.architecture_planning.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
        patch("core.llm_router.escalate_to_cloud", side_effect=_fake_escalate),
    ):
        out = await ArchitecturePlanningSkill().run(
            SkillInput(question="what's the retention?", tenant_id=TENANT))

    assert escalated["reason"] == "local_unreliable"
    assert escalated["task_type"] == "architecture_planning"
    assert call_count["n"] == 2
    assert "5%" in out.answer


@pytest.mark.asyncio
async def test_architecture_planning_skips_escalation_when_confident():
    async def _fake_completion(*a, **kw):
        assert kw.get("logprobs") is True
        return _resp_with_logprob(-0.05, "Retention is 5%.")

    mock_escalate = MagicMock()
    chunks = [{"filename": "boq.pdf", "text": "Retention: 5%", "score": 0.9}]
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=_pipeline_mock(chunks)),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.skills.architecture_planning.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
        patch("core.llm_router.escalate_to_cloud", new=mock_escalate),
    ):
        await ArchitecturePlanningSkill().run(SkillInput(question="what's the retention?", tenant_id=TENANT))

    mock_escalate.assert_not_called()


@pytest.mark.asyncio
async def test_standards_lookup_escalates_on_low_confidence():
    call_count = {"n": 0}

    async def _fake_completion(*a, **kw):
        call_count["n"] += 1
        assert kw.get("logprobs") is True
        if call_count["n"] == 1:
            return _resp_with_logprob(-3.0, "maybe something about fire escapes?")
        return _resp_with_logprob(-0.05, "Per SANS 10400-T, fire escape width is 1000mm.")

    escalated = {}

    def _fake_escalate(reason, run_id=None, task_type=None):
        escalated["reason"] = reason
        escalated["task_type"] = task_type
        return ("openrouter/cloud-model", "sk-test", None)

    chunks = [{"filename": "sans10400.pdf", "text": "Fire escape width: 1000mm", "score": 0.9}]
    with (
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=_pipeline_mock(chunks)),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.skills.standards_lookup.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
        patch("core.llm_router.escalate_to_cloud", side_effect=_fake_escalate),
    ):
        out = await StandardsLookupSkill().run(
            SkillInput(question="fire escape width?", tenant_id=TENANT))

    assert escalated["reason"] == "local_unreliable"
    assert escalated["task_type"] == "standards_lookup"
    assert call_count["n"] == 2
    assert "1000mm" in out.answer


@pytest.mark.asyncio
async def test_draft_admin_escalates_on_low_confidence():
    call_count = {"n": 0}

    async def _fake_completion(*a, **kw):
        call_count["n"] += 1
        assert kw.get("logprobs") is True
        if call_count["n"] == 1:
            return _resp_with_logprob(-3.0, "maybe I can help with that?", tool_calls=None)
        return _resp_with_logprob(-0.05, "Sure — what should the letter say?", tool_calls=None)

    escalated = {}

    def _fake_escalate(reason, run_id=None, task_type=None):
        escalated["reason"] = reason
        escalated["task_type"] = task_type
        return ("openrouter/cloud-model", "sk-test", None)

    with (
        patch("core.skills.draft_admin.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.llm_router.escalate_to_cloud", side_effect=_fake_escalate),
    ):
        answer = await DraftAdminSkill()._loop("", "can you help me?", TENANT, "27821234567")

    assert escalated["reason"] == "local_unreliable"
    assert escalated["task_type"] == "draft_admin"
    assert answer == "Sure — what should the letter say?"


@pytest.mark.asyncio
async def test_draft_admin_skips_escalation_when_confident():
    async def _fake_completion(*a, **kw):
        assert kw.get("logprobs") is True
        return _resp_with_logprob(-0.05, "Sure — what should the letter say?", tool_calls=None)

    mock_escalate = MagicMock()
    with (
        patch("core.skills.draft_admin.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.llm_router.escalate_to_cloud", new=mock_escalate),
    ):
        await DraftAdminSkill()._loop("", "can you help me?", TENANT, "27821234567")

    mock_escalate.assert_not_called()
