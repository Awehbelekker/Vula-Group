"""Tests for logprob-confidence escalation rolled out to finance_admin.py and
commerce_admin.py (2026-08 accuracy audit follow-up).

The escalation path itself (compute_confidence + looks_unreliable + escalate_to_cloud) was
wired up earlier the same day, but only reached reasoning.py and commerce_assistant.py —
commerce_admin.py (real financial/stock mutations) and finance_admin.py (money reporting) had
zero adoption. These tests pin that a low-confidence local final answer now escalates to cloud
in both, even when the text itself looks superficially fine.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import core.skills.commerce_admin as ca
from core.skills.base import SkillInput
from core.skills.commerce_admin import CommerceAdminSkill
from core.skills.finance_admin import FinanceAdminSkill

TENANT = "off-the-hook"


def _resp_with_logprob(avg_logprob, content, tool_calls=None):
    token = MagicMock(logprob=avg_logprob)
    logprobs = MagicMock(content=[token])
    message = SimpleNamespace(content=content, tool_calls=tool_calls, logprobs=logprobs)
    choice = MagicMock(logprobs=logprobs, message=message)
    return type("R", (), {"choices": [choice]})()


@pytest.mark.asyncio
async def test_finance_admin_escalates_on_low_confidence():
    call_count = {"n": 0}

    async def _fake_completion(*a, **kw):
        call_count["n"] += 1
        assert kw.get("logprobs") is True
        if call_count["n"] == 1:
            return _resp_with_logprob(-3.0, "maybe R18,000? not sure")  # low confidence
        return _resp_with_logprob(-0.05, "R18,500.00 spent on HPC")  # cloud: confident

    escalated = {}

    def _fake_escalate(reason, run_id=None, task_type=None):
        escalated["reason"] = reason
        escalated["task_type"] = task_type
        return ("openrouter/cloud-model", "sk-test", None)

    with (
        patch("core.skills.finance_admin.resolve_generation_route",
              return_value=("ollama/test", None, "http://localhost:11434")),
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.llm_router.escalate_to_cloud", side_effect=_fake_escalate),
    ):
        out = await FinanceAdminSkill().run(SkillInput(question="how much on HPC?", tenant_id=TENANT))

    assert escalated["reason"] == "local_unreliable"
    assert escalated["task_type"] == "finance_admin"
    assert call_count["n"] == 2
    assert out.answer.startswith("R18,500.00 spent on HPC")


@pytest.mark.asyncio
async def test_commerce_admin_escalates_on_low_confidence_when_no_cloud_key_for_toolcalling():
    """The admin loop force-escalates to cloud UNCONDITIONALLY for tool-calling reliability
    (a pre-existing, separate mechanism) — the new confidence check only has something to do
    in the no-cloud-key fallback case, where the loop is genuinely running on ollama/."""
    call_count = {"n": 0}

    def _fake_escalate(reason, run_id=None, task_type=None):
        # First call (admin_agent_toolcalling, pre-existing): no cloud key configured.
        if reason == "admin_agent_toolcalling":
            return None
        # Second call (local_unreliable, the new one under test): cloud key available.
        return ("openrouter/cloud-model", "sk-test", None)

    async def _fake_completion(*a, **kw):
        call_count["n"] += 1
        assert kw.get("logprobs") is True
        if call_count["n"] == 1:
            return _resp_with_logprob(-3.0, "maybe 5 units left?", tool_calls=None)
        return _resp_with_logprob(-0.05, "You have 12 units of hake left.", tool_calls=None)

    with (
        patch.object(ca, "resolve_generation_route", return_value=("ollama/test", None, "http://localhost:11434")),
        patch.object(ca, "escalate_to_cloud", side_effect=_fake_escalate),
        patch("litellm.acompletion", new=_fake_completion),
    ):
        answer = await CommerceAdminSkill()._agent_loop("system", "", "how much hake left?", {"tenant_id": TENANT})

    assert call_count["n"] == 2
    assert answer == "You have 12 units of hake left."


@pytest.mark.asyncio
async def test_commerce_admin_skips_confidence_check_when_already_on_cloud():
    """When the pre-existing force-escalation succeeds, model is already cloud — the new
    check must not fire a second, redundant escalation attempt."""
    async def _fake_completion(*a, **kw):
        assert kw.get("logprobs") is True
        return _resp_with_logprob(-3.0, "a low-confidence-shaped answer", tool_calls=None)

    escalate_calls = []

    def _fake_escalate(reason, run_id=None, task_type=None):
        escalate_calls.append(reason)
        return ("openrouter/cloud-model", "sk-test", None)  # force-escalation succeeds

    with (
        patch.object(ca, "resolve_generation_route", return_value=("ollama/test", None, "http://localhost:11434")),
        patch.object(ca, "escalate_to_cloud", side_effect=_fake_escalate),
        patch("litellm.acompletion", new=_fake_completion),
    ):
        await CommerceAdminSkill()._agent_loop("system", "", "q", {"tenant_id": TENANT})

    # Only the pre-existing force-escalation fired — model was already cloud, so
    # model.startswith("ollama/") is False and the new check is skipped entirely.
    assert escalate_calls == ["admin_agent_toolcalling"]
