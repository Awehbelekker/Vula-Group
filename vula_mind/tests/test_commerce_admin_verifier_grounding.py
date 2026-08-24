"""Tests for the 2026-08-24 fix: commerce_admin.py's verification_policy="adversarial" ran
"blind" because SkillOutput.sources was never populated — core/verification.py's checker only
builds grounding context from sources whose type contains "kb" or equals "tool", and nothing
ever set either. run() now threads a mutable sources list through _agent_loop so every
dispatched tool result reaches the adversarial checker.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.skills.base import SkillInput
from core.skills.commerce_admin import CommerceAdminSkill

TID = "test-tenant"


def _resp(content=None, tool_calls=None):
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    return resp


def _tool_call(call_id, name, args_json):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=args_json))


async def _fake_route(*a, **kw):
    return ("openrouter/test", "k", None)


@pytest.mark.asyncio
async def test_agent_loop_populates_sources_list_when_given_one():
    import core.skills.commerce_admin as ca

    call_count = {"n": 0}

    async def fake_completion(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _resp(tool_calls=[_tool_call("c1", "stock_status", "{}")])
        return _resp(content="Stock is fine.")

    with (
        patch.object(ca, "resolve_generation_route", new=_fake_route),
        patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)),
        patch("litellm.acompletion", new=fake_completion),
        patch.object(CommerceAdminSkill, "_dispatch_tool",
                     new=lambda self, name, args, ctx: _fake_dispatch()),
    ):
        skill = CommerceAdminSkill()
        sources = []
        answer = await skill._agent_loop("system", "", "how's stock", {"tenant_id": TID},
                                         tools=None, sources=sources)

    assert answer == "Stock is fine."
    assert len(sources) == 1
    assert sources[0]["type"] == "tool"
    assert sources[0]["name"] == "stock_status"


async def _fake_dispatch():
    return {"products": [{"name": "Hake", "stock_quantity": 20}]}


@pytest.mark.asyncio
async def test_agent_loop_without_sources_param_is_unaffected():
    """Every pre-existing direct call to _agent_loop (no `sources` kwarg) must keep working
    exactly as before — sources defaults to None and nothing is collected."""
    import core.skills.commerce_admin as ca

    async def fake_completion(*a, **kw):
        return _resp(content="plain answer, no tools")

    with (
        patch.object(ca, "resolve_generation_route", new=_fake_route),
        patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)),
        patch("litellm.acompletion", new=fake_completion),
    ):
        skill = CommerceAdminSkill()
        answer = await skill._agent_loop("system", "", "hi", {"tenant_id": TID}, tools=None)

    assert answer == "plain answer, no tools"


@pytest.mark.asyncio
async def test_run_populates_skilloutput_sources_end_to_end():
    call_count = {"n": 0}

    async def fake_completion(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _resp(tool_calls=[_tool_call("c1", "sales_summary", '{"period": "today"}')])
        return _resp(content="You made R500 today.")

    async def fake_dispatch(self, name, args, ctx):
        return {"period": "today", "revenue": "R500.00", "paid_orders": 3}

    import core.skills.commerce_admin as ca
    with (
        patch.object(ca, "resolve_generation_route", new=_fake_route),
        patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)),
        patch("litellm.acompletion", new=fake_completion),
        patch.object(CommerceAdminSkill, "_dispatch_tool", new=fake_dispatch),
        patch("core.verification.apply", new=lambda *a, **kw: None),  # isolate: verifier tested separately
    ):
        skill = CommerceAdminSkill()
        out = await skill.run(SkillInput(question="how did we do today", tenant_id=TID))

    assert out.answer == "You made R500 today."
    assert len(out.sources) == 1
    assert out.sources[0]["type"] == "tool"
    assert "R500" in out.sources[0]["text"]


def test_verification_apply_now_grounds_on_tool_sources():
    """core/verification.py's own filter must accept type=='tool', not just 'kb'-ish types —
    otherwise populating SkillOutput.sources here changes nothing for the checker."""
    import asyncio
    from core import verification

    class _FakeSkill:
        name = "commerce_admin"
        verification_policy = "adversarial"

    class _FakeInp:
        tenant_id = TID
        question = "how did we do today"

    class _FakeResult:
        error = None
        answer = "You made R500 today."
        confidence = 0.8
        verification = None
        sources = [{"type": "tool", "name": "sales_summary", "text": '{"revenue": "R500.00"}'}]

    captured = {}

    async def fake_check(question, answer, context=""):
        captured["context"] = context
        return {"verdict": "pass", "defects": [], "checker_ms": 5}

    with patch.object(verification, "adversarial_check", new=fake_check):
        asyncio.run(verification.apply(_FakeSkill(), _FakeInp(), _FakeResult()))

    assert "R500.00" in captured["context"]
