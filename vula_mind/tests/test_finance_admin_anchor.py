"""Tests for core/skills/finance_admin.py's accuracy anchor check (2026-08 audit).

Unlike calculations.py (deterministic anchor) or commerce_admin.py's mutating tools
(post-write readback), finance_admin previously had no check that its prose reply actually
matched what its own tools returned — a money-reporting skill with zero anchoring. These tests
pin: a correctly-anchored reply keeps full confidence; a reply that states a figure the tools
never returned drops confidence and gets a visible caveat.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.skills.base import SkillInput
from core.skills.finance_admin import FinanceAdminSkill

TENANT = "digg-demo"


def _resp(content="", tool_calls=None):
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    return resp


def _tool_call(call_id, name, args_json):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=args_json))


async def _fake_route(*a, **kw):
    return ("openrouter/test", "k", None)


@pytest.mark.asyncio
async def test_correctly_anchored_reply_keeps_full_confidence():
    call_count = {"n": 0}

    async def _fake_completion(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _resp(tool_calls=[_tool_call("c1", "project_spend", '{"project": "HPC"}')])
        return _resp(content="HPC has spent R18,500.00 so far, R6,500 remaining of budget.")

    dispatch_result = {"project": "HPC Bokaap", "money_in": 25000.0, "money_out": 18500.0,
                        "net": 6500.0, "budget": 25000.0, "remaining": 6500.0, "transactions": 4}

    with (
        patch("core.skills.finance_admin.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=_fake_completion),
        patch.object(FinanceAdminSkill, "_dispatch", return_value=dispatch_result),
    ):
        out = await FinanceAdminSkill().run(SkillInput(question="how much on HPC?", tenant_id=TENANT))

    assert out.confidence == 0.8
    assert "⚠️" not in out.answer


@pytest.mark.asyncio
async def test_misreported_figure_drops_confidence_and_adds_caveat():
    call_count = {"n": 0}

    async def _fake_completion(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _resp(tool_calls=[_tool_call("c1", "project_spend", '{"project": "HPC"}')])
        # Model states a figure (R99,999) that has nothing to do with what the tool returned.
        return _resp(content="HPC has spent R99,999.00 so far.")

    dispatch_result = {"project": "HPC Bokaap", "money_in": 25000.0, "money_out": 18500.0,
                        "net": 6500.0, "budget": 25000.0, "remaining": 6500.0, "transactions": 4}

    with (
        patch("core.skills.finance_admin.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=_fake_completion),
        patch.object(FinanceAdminSkill, "_dispatch", return_value=dispatch_result),
    ):
        out = await FinanceAdminSkill().run(SkillInput(question="how much on HPC?", tenant_id=TENANT))

    assert out.confidence == 0.45
    assert "⚠️" in out.answer
    assert "couldn't be matched" in out.answer


@pytest.mark.asyncio
async def test_no_tools_called_skips_verification_entirely():
    """If nothing was fetched (e.g. the model asked a clarifying question instead), there's
    nothing to anchor against — must not falsely flag a low-confidence caveat."""
    async def _fake_completion(*a, **kw):
        return _resp(content="Which project did you mean?")

    with (
        patch("core.skills.finance_admin.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=_fake_completion),
    ):
        out = await FinanceAdminSkill().run(SkillInput(question="how much did we spend?", tenant_id=TENANT))

    assert out.confidence == 0.8
    assert "⚠️" not in out.answer


def test_extract_candidates_includes_both_raw_and_cents_forms():
    skill = FinanceAdminSkill()
    candidates = skill._extract_candidates({"money_out": 18500.0})
    assert 18500.0 in candidates
    assert 185.0 in candidates  # /100 form, defensive of a *_cents convention


def test_verify_answer_ignores_small_incidental_numbers():
    skill = FinanceAdminSkill()
    skill._verified = [18500.0]
    # "3" (project count) isn't a money figure and must not force a false mismatch.
    anchored, unmatched = skill._verify_answer("You have 3 projects; HPC spent R18,500.00.")
    assert anchored is True
    assert unmatched == []
