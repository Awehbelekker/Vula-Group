"""Tests for the 2026-08-24 chat-accuracy audit's finance_admin.py fixes:
  - budget_status with no project used to silently return an arbitrary single (highest-spend)
    project instead of a genuine aggregate — confirmed real bug, fixed here.
  - _extract_candidates was missing the *100 hedge direction (only /100 existed).
  - _money_shaped_numbers excludes percentages and plausible bare years from the anchor check.
  - sources population (verifier grounding) and the post-tool decline guard.
  - preferred_language threading (previously never read at all).
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


# ── budget_status no-project aggregate fix ────────────────────────────────────────

def test_budget_status_with_no_project_returns_real_aggregate_not_arbitrary_project():
    skill = FinanceAdminSkill()
    fake_full = {
        "projects": [
            {"project": "HPC", "in": 30000.0, "out": 28000.0, "net": 2000.0,
             "budget": 30000.0, "remaining": 2000.0, "count": 10},
            {"project": "Bokaap", "in": 10000.0, "out": 4000.0, "net": 6000.0,
             "budget": 10000.0, "remaining": 6000.0, "count": 3},
        ],
        "transactions": [], "total_in": 40000.0, "total_out": 32000.0,
    }
    with patch("vula.integrations.finances.finance_summary", return_value=fake_full):
        result = skill._dispatch("budget_status", {}, TENANT)

    assert result["scope"] == "all_projects"
    assert result["project_count"] == 2
    assert result["total_budget"] == 40000.0
    assert result["total_spent"] == 32000.0
    assert result["total_remaining"] == 8000.0
    # The old bug: silently returning ONLY the highest-spend project (HPC) as if it were "the"
    # answer. The fix must include both projects, not just HPC.
    names = {p["project"] for p in result["per_project"]}
    assert names == {"HPC", "Bokaap"}


def test_budget_status_with_a_project_still_returns_single_project_shape():
    """Regression: the aggregate branch must only fire when no project is given — a real
    single-project request keeps its existing shape."""
    skill = FinanceAdminSkill()
    fake_full = {"projects": [{"project": "HPC", "in": 1, "out": 1, "net": 0,
                                "budget": 1, "remaining": 0, "count": 1}]}
    fake_one = {"projects": [{"project": "HPC", "in": 30000.0, "out": 28000.0, "net": 2000.0,
                               "budget": 30000.0, "remaining": 2000.0, "count": 10}]}
    with patch("vula.integrations.finances.finance_summary", side_effect=[fake_full, fake_one]):
        result = skill._dispatch("budget_status", {"project": "HPC"}, TENANT)

    assert "scope" not in result
    assert result["project"] == "HPC"
    assert result["money_out"] == 28000.0


# ── _extract_candidates *100 hedge ────────────────────────────────────────────────

def test_extract_candidates_includes_times_100_form():
    skill = FinanceAdminSkill()
    candidates = skill._extract_candidates({"money_out": 185.0})
    assert 185.0 in candidates
    assert 1.85 in candidates    # /100 form
    assert 18500.0 in candidates  # *100 form — previously missing entirely


# ── _money_shaped_numbers: percentage and year exclusion ─────────────────────────

def test_money_shaped_numbers_excludes_percentages():
    nums = FinanceAdminSkill._money_shaped_numbers("You've used 40% of the budget, R18,500 spent.")
    assert 40.0 not in nums
    assert 18500.0 in nums


def test_money_shaped_numbers_excludes_bare_years():
    nums = FinanceAdminSkill._money_shaped_numbers("As of 2026 you've spent R18,500 on HPC.")
    assert 2026.0 not in nums
    assert 18500.0 in nums


def test_money_shaped_numbers_keeps_a_year_like_amount_when_currency_prefixed():
    nums = FinanceAdminSkill._money_shaped_numbers("The invoice total was R2026.00.")
    assert 2026.0 in nums


def test_verify_answer_no_longer_flags_percentage_or_year_as_unmatched():
    skill = FinanceAdminSkill()
    skill._verified = [18500.0]
    anchored, unmatched = skill._verify_answer(
        "As of 2026, you've used 40% of the budget — R18,500.00 spent so far.")
    assert anchored is True
    assert unmatched == []


# ── sources population (verifier grounding) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_run_populates_sources_from_dispatched_tools():
    call_count = {"n": 0}

    async def _fake_completion(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _resp(tool_calls=[_tool_call("c1", "project_spend", '{"project": "HPC"}')])
        return _resp(content="HPC has spent R18,500.00 so far.")

    dispatch_result = {"project": "HPC", "money_in": 25000.0, "money_out": 18500.0,
                        "net": 6500.0, "budget": 25000.0, "remaining": 6500.0, "transactions": 4}

    with (
        patch("core.skills.finance_admin.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=_fake_completion),
        patch.object(FinanceAdminSkill, "_dispatch", return_value=dispatch_result),
    ):
        out = await FinanceAdminSkill().run(SkillInput(question="how much on HPC?", tenant_id=TENANT))

    assert len(out.sources) == 1
    assert out.sources[0]["type"] == "tool"
    assert "18500" in out.sources[0]["text"]


# ── post-tool decline guard ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_tools_not_found_declines_instead_of_letting_model_answer_freely():
    async def _fake_completion(*a, **kw):
        # Model calls a tool that comes back not-found, then tries to answer anyway.
        return _resp(tool_calls=[_tool_call("c1", "supplier_lookup", '{"query": "Ghost Supplier"}')])

    not_found_result = {"query": "Ghost Supplier", "found": False}

    with (
        patch("core.skills.finance_admin.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=_fake_completion),
        patch.object(FinanceAdminSkill, "_dispatch", return_value=not_found_result),
    ):
        out = await FinanceAdminSkill().run(
            SkillInput(question="what have we paid Ghost Supplier?", tenant_id=TENANT))

    assert "couldn't find" in out.answer.lower()
    assert out.confidence <= 0.3


@pytest.mark.asyncio
async def test_at_least_one_found_result_does_not_trigger_decline():
    call_count = {"n": 0}

    async def _fake_completion(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _resp(tool_calls=[_tool_call("c1", "project_spend", '{"project": "HPC"}')])
        return _resp(content="HPC has spent R18,500.00 so far.")

    dispatch_result = {"project": "HPC", "money_in": 25000.0, "money_out": 18500.0,
                        "net": 6500.0, "budget": 25000.0, "remaining": 6500.0, "transactions": 4}

    with (
        patch("core.skills.finance_admin.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=_fake_completion),
        patch.object(FinanceAdminSkill, "_dispatch", return_value=dispatch_result),
    ):
        out = await FinanceAdminSkill().run(SkillInput(question="how much on HPC?", tenant_id=TENANT))

    assert "couldn't find" not in out.answer.lower()
    assert out.confidence == 0.8


# ── language threading ─────────────────────────────────────────────────────────────

def test_system_prompt_threads_preferred_language():
    skill = FinanceAdminSkill()
    prompt_en = skill._system("")
    prompt_af = skill._system("af")
    # behaviour_preamble's generic CONVERSATION_RULES always mentions "Afrikaans" as one of the
    # supported languages regardless — the real signal is the SPECIFIC per-language instruction
    # block, only added when preferred_language is actually passed through.
    assert "Reply in Afrikaans by default" in prompt_af
    assert "Reply in Afrikaans by default" not in prompt_en


@pytest.mark.asyncio
async def test_run_reads_preferred_language_from_metadata():
    """Previously finance_admin never read inp.metadata at all for language — confirmed real
    gap, worse than commerce_admin's (which at least computed it and forgot one branch)."""
    captured = {}

    async def _fake_completion(*a, **kw):
        captured["system"] = kw["messages"][0]["content"]
        return _resp(content="Sure.")

    with (
        patch("core.skills.finance_admin.resolve_generation_route", new=_fake_route),
        patch("litellm.acompletion", new=_fake_completion),
    ):
        await FinanceAdminSkill().run(
            SkillInput(question="hoeveel het ons bestee?", tenant_id=TENANT,
                      metadata={"preferred_language": "af"}))

    assert "Afrikaans" in captured["system"]
