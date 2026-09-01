"""Tests for core.skills.base.unverified_prices and its wiring into commerce_admin.run() —
a deterministic backstop against a confidently-stated, unfounded price. Real incident, gerflor,
2026-08-31: "ZAR 129.90 per square meter for the Mipolam Concept" appeared nowhere in what
lookup_business_info actually returned, and the adversarial verifier (a fuzzy LLM pass, with
that same text as grounding context) still passed it as accepted — a confirmed false negative.
See tests/test_commerce_admin_kb_tool.py for the tool-selection half of this incident.
"""
from unittest.mock import patch

import pytest

from core.skills.base import unverified_prices, SkillInput
from core.skills.commerce_admin import CommerceAdminSkill

TID = "gerflor"
GROUNDING = {"lookup_business_info", "competitor_check"}


# ── unverified_prices ─────────────────────────────────────────────────────────────

def test_flags_the_real_fabricated_price():
    answer = "Gerflor lists prices like ZAR 129.90 per square meter for the Mipolam Concept."
    sources = [{"name": "lookup_business_info",
               "text": "Mipolam 180 R 198.00, Mipolam Astro R 280.00, Mipolam Affinity R 315.00"}]
    assert unverified_prices(answer, sources, GROUNDING) == ["R 129.90"]


def test_does_not_flag_a_correctly_grounded_price():
    answer = "Mipolam 180 is R198.00 per square meter."
    sources = [{"name": "lookup_business_info", "text": "Mipolam 180 R 198.00"}]
    assert unverified_prices(answer, sources, GROUNDING) == []


def test_ignores_structured_tool_sources_outside_the_grounding_set():
    # sales_summary is already ground truth by construction — no free-text extraction risk,
    # and flagging it would just be noise.
    answer = "Today's sales were R450.00 from 3 orders."
    sources = [{"name": "sales_summary", "text": '{"total_cents": 45000}'}]
    assert unverified_prices(answer, sources, GROUNDING) == []


def test_no_sources_means_nothing_to_check_against():
    answer = "That's R129.90 per square meter."
    assert unverified_prices(answer, [], GROUNDING) == []


def test_no_prices_in_answer_returns_empty():
    answer = "We stock several ranges including Mipolam and Taralay."
    sources = [{"name": "lookup_business_info", "text": "no numbers here at all"}]
    assert unverified_prices(answer, sources, GROUNDING) == []


def test_ignores_spacing_and_comma_formatting_differences():
    # Same real number, different formatting between the source and the stated answer.
    answer = "That's R1,234.50."
    sources = [{"name": "lookup_business_info", "text": "Price: R 1234.50 each"}]
    assert unverified_prices(answer, sources, GROUNDING) == []


# ── wiring into commerce_admin.run() ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_replaces_answer_when_price_is_unverified():
    skill = CommerceAdminSkill()

    async def fake_agent_loop(system_msg, history, question, ctx, tools, sources=None):
        if sources is not None:
            sources.append({"type": "tool", "name": "lookup_business_info",
                            "text": "Mipolam 180 R 198.00"})
        return "Gerflor lists prices like ZAR 129.90 per square meter for the Mipolam Concept."

    with patch.object(skill, "_agent_loop", new=fake_agent_loop):
        inp = SkillInput(question="check decotrader costs", tenant_id=TID,
                         conversation_history="", metadata={"customer_phone": "27645755210"})
        out = await skill.run(inp)

    assert "129.90" not in out.answer
    assert "couldn't confirm" in out.answer.lower()


@pytest.mark.asyncio
async def test_run_keeps_answer_when_price_is_verified():
    skill = CommerceAdminSkill()

    async def fake_agent_loop(system_msg, history, question, ctx, tools, sources=None):
        if sources is not None:
            sources.append({"type": "tool", "name": "lookup_business_info",
                            "text": "Mipolam 180 R 198.00"})
        return "Mipolam 180 is R198.00 per square meter."

    with patch.object(skill, "_agent_loop", new=fake_agent_loop):
        inp = SkillInput(question="what's the price of Mipolam 180", tenant_id=TID,
                         conversation_history="", metadata={"customer_phone": "27645755210"})
        out = await skill.run(inp)

    assert "R198.00" in out.answer


@pytest.mark.asyncio
async def test_run_does_not_flag_structured_tool_answers():
    skill = CommerceAdminSkill()

    async def fake_agent_loop(system_msg, history, question, ctx, tools, sources=None):
        if sources is not None:
            sources.append({"type": "tool", "name": "sales_summary", "text": '{"total_cents": 45000}'})
        return "Today's sales were R450.00 from 3 orders."

    with patch.object(skill, "_agent_loop", new=fake_agent_loop):
        inp = SkillInput(question="what were today's sales", tenant_id=TID,
                         conversation_history="", metadata={"customer_phone": "27645755210"})
        out = await skill.run(inp)

    assert "R450.00" in out.answer
