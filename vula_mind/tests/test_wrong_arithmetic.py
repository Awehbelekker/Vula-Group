"""Deterministic arithmetic backstop — from a real Gerflor quote (2026-08-31).

The model wrote "11.8 × 18.2 = 215.56" (correct: 214.76) and then "215.56 × 198.00 =
R42,731.08" — a total that doesn't follow from even its own wrong intermediate (the correct
chain gives R42,522.48). Nothing checked it, so a wrong price reached a real customer.

commerce_admin had no calculation tool at all, and prompt instructions demonstrably don't fix
this — the model was already told to use tools and did the sums in its head anyway. Same
deterministic-backstop pattern as unverified_prices(): recompute what was actually claimed.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.base import wrong_arithmetic
from core.skills.commerce_admin import CommerceAdminSkill, KNOWLEDGE_TOOLS


# ── the real incident ───────────────────────────────────────────────────────────

def test_catches_the_real_gerflor_multiplication_error():
    bad = wrong_arithmetic("The area is 11.8 x 18.2 = 215.56 m².")
    assert len(bad) == 1
    assert bad[0]["stated"] == 215.56
    assert bad[0]["actual"] == 214.76


def test_catches_the_real_gerflor_total_error():
    bad = wrong_arithmetic("So 215.56 × 198.00 = R42,731.08 for the job.")
    assert len(bad) == 1
    assert bad[0]["actual"] == 42680.88


def test_catches_both_errors_in_one_answer():
    answer = ("Area: 11.8 × 18.2 = 215.56 m²\n"
              "Total: 215.56 × 198.00 = R42,731.08")
    assert len(wrong_arithmetic(answer)) == 2


def test_the_correct_chain_passes_clean():
    answer = ("Area: 11.8 × 18.2 = 214.76 m²\n"
              "Total: 214.76 × 198.00 = R42,522.48")
    assert wrong_arithmetic(answer) == []


# ── formats and operators ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "2 x 3 = 6", "2 × 3 = 6", "2 * 3 = 6",
    "R1,000.00 + R250.00 = R1,250.00",
    "1000 - 250 = 750",
    "100 / 4 = 25",
])
def test_correct_arithmetic_in_many_formats_is_not_flagged(text):
    assert wrong_arithmetic(text) == []


@pytest.mark.parametrize("text,actual", [
    ("2 x 3 = 7", 6.0),
    ("R1,000.00 + R250.00 = R1,300.00", 1250.0),
    ("1000 - 250 = 800", 750.0),
    ("100 / 4 = 30", 25.0),
])
def test_wrong_arithmetic_in_many_formats_is_caught(text, actual):
    bad = wrong_arithmetic(text)
    assert len(bad) == 1 and bad[0]["actual"] == actual


def test_display_rounding_is_tolerated():
    """1/3 style rounding must not be reported as an error."""
    assert wrong_arithmetic("10 / 3 = 3.33") == []
    assert wrong_arithmetic("214.755 x 1 = 214.76") == []


def test_division_by_zero_is_skipped_not_crashed():
    assert wrong_arithmetic("5 / 0 = 0") == []


def test_prose_with_no_equations_is_clean():
    assert wrong_arithmetic("We have 12 rolls in stock at R198 per square metre.") == []
    assert wrong_arithmetic("") == []


# ── the calculate tool ──────────────────────────────────────────────────────────

def test_calculate_tool_is_registered():
    names = [t["function"]["name"] for t in KNOWLEDGE_TOOLS]
    assert "calculate" in names


def test_calculate_returns_the_exact_product():
    out = CommerceAdminSkill()._calculate({"expression": "11.8 * 18.2"})
    assert out["result"] == 214.76, "the exact figure the model got wrong in production"


def test_calculate_handles_the_full_real_chain():
    skill = CommerceAdminSkill()
    area = skill._calculate({"expression": "11.8 * 18.2"})["result"]
    total = skill._calculate({"expression": f"{area} * 198.00"})["result"]
    assert total == 42522.48


def test_calculate_formats_money_readably():
    assert CommerceAdminSkill()._calculate({"expression": "214.76 * 198"})["formatted"] == "42,522.48"


def test_calculate_rejects_empty_and_unsafe_expressions():
    skill = CommerceAdminSkill()
    assert "error" in skill._calculate({"expression": ""})
    assert "error" in skill._calculate({"expression": "__import__('os').system('ls')"})
    assert "error" in skill._calculate({"expression": "open('/etc/passwd').read()"})


# ── wiring into run() ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_appends_a_correction_when_the_maths_is_wrong():
    skill = CommerceAdminSkill()
    from core.skills.base import SkillInput
    inp = SkillInput(question="quote for 11.8 x 18.2 at R198", tenant_id="gerflor",
                     conversation_history="", metadata={})
    with patch.object(skill, "_agent_loop",
                      AsyncMock(return_value="Area is 11.8 x 18.2 = 215.56 m².")):
        out = await skill.run(inp)
    assert "correct my own maths" in out.answer
    assert "214.76" in out.answer
    assert out.confidence == 0.3


@pytest.mark.asyncio
async def test_run_leaves_correct_maths_untouched():
    skill = CommerceAdminSkill()
    from core.skills.base import SkillInput
    inp = SkillInput(question="quote", tenant_id="gerflor", conversation_history="", metadata={})
    good = "Area is 11.8 x 18.2 = 214.76 m²."
    with patch.object(skill, "_agent_loop", AsyncMock(return_value=good)):
        out = await skill.run(inp)
    assert out.answer == good
    assert out.confidence == 0.8
