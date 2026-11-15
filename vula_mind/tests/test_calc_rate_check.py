"""14 Nov 2026 (DIGG): brick rates of R2,760.48 / R47,692.16 / R10,166.85 were given, then
R345.06 / R554.56 / R677.79, and the reply named "the 'lookup_rate' tool… tool results".
Every rand figure in a calculation reply must now be a rate the library returned, a calculate()
result or a figure from the question; tool names are put in plain words."""
from unittest.mock import patch

import pytest

from core.skills.base import plain_tool_words
from core.skills.calculations import RATE_CHECK_NOTE, CalculationsSkill
from core.skills.base import SkillInput

RATES = [{"description": "Half brick walls", "unit": "m2", "rate": 345.06, "source": "qs"},
         {"description": "One brick walls", "unit": "m2", "rate": 554.56, "source": "qs"}]


async def _run(answer, *, look_up=True, calcs=()):
    skill = CalculationsSkill()

    async def fake_loop(self, history, question, context, tenant_id):
        if look_up:
            self._dispatch("lookup_rate", {"description": "brick wall"}, tenant_id)
        for expr in calcs:
            self._dispatch("calculate", {"expression": expr}, tenant_id)
        return answer

    with patch("vula.api.qs.search_rates", return_value=RATES), \
            patch.object(CalculationsSkill, "_agent_loop", fake_loop), \
            patch("vula.ingestion.pipeline.VulaIngestionPipeline.query", side_effect=RuntimeError("no kb")):
        return await skill(SkillInput(question="What are the brick wall rates?", tenant_id="digg-demo"))


@pytest.mark.asyncio
async def test_rates_from_the_library_stay():
    out = await _run("Half brick walls: R 345.06 per m2.\nOne brick walls: R554.56 per m2.")
    assert "R 345.06" in out.answer and "R554.56" in out.answer and RATE_CHECK_NOTE not in out.answer


@pytest.mark.asyncio
async def test_an_invented_rate_is_left_out_even_with_no_calculation():
    out = await _run("Half brick walls: R 345.06 per m2.\nOne brick walls: R 47,692.16 per m2.")
    assert "47,692.16" not in out.answer and "R 345.06" in out.answer
    assert RATE_CHECK_NOTE in out.answer


@pytest.mark.asyncio
async def test_an_invented_rate_with_no_lookup_at_all_is_left_out():
    out = await _run("Half brick walls cost R 2,760.48 per m2.", look_up=False)
    assert "2,760.48" not in out.answer and RATE_CHECK_NOTE in out.answer


@pytest.mark.asyncio
async def test_a_calculated_total_stays():
    out = await _run("20 m2 of half brick wall: 20 × R345.06 = R6,901.20.", calcs=["20 * 345.06"])
    assert "R6,901.20" in out.answer and RATE_CHECK_NOTE not in out.answer


@pytest.mark.asyncio
async def test_tool_names_never_reach_the_reply():
    out = await _run('I obtained the rates from the "lookup_rate" tool, which is a part of the '
                     'tool results. Half brick walls: R 345.06 per m2.')
    assert "lookup_rate" not in out.answer and "tool results" not in out.answer
    assert "from your rate library" in out.answer


def test_plain_tool_words():
    assert plain_tool_words("I used the `find_document` tool.", {"find_document"}) == "I used your records."
    assert plain_tool_words('per the "calculate" tool', {"calculate"},
                            {"calculate": "a calculation"}) == "per a calculation"
    # an ordinary word that happens to be a tool name is left alone
    assert plain_tool_words("Let me calculate that.", {"calculate"}) == "Let me calculate that."
    assert plain_tool_words("", {"x_y"}) == ""


def test_where_a_rate_came_from_in_words():
    from core.skills.calculations import where_from
    assert where_from({"description": "x", "rate": 1}) == "your rate library"
    assert where_from({"learned": True, "source": "Learned from 1 boq, last 2026-10-26",
                       "projects": ["HPC Bokaap"], "latest_on": "2026-10-26",
                       "observations": 1}) == "your HPC Bokaap BOQ, 26 Oct 2026"
    assert where_from({"learned": True, "source": "Learned from 2 quotes, 2 invoices, last 2026-10-12",
                       "projects": ["Belladonna"], "latest_on": "2026-10-12", "observations": 4}) == \
        "4 of your documents (2 quotes, 2 invoices; Belladonna), latest 12 Oct 2026"


@pytest.mark.asyncio
async def test_the_model_is_given_the_source_with_each_rate():
    skill = CalculationsSkill()
    learned = [{"description": "One brick walls", "unit": "m2", "rate": 554.56, "learned": True,
                "source": "Learned from 1 boq, last 2026-10-26", "projects": ["HPC Bokaap"],
                "latest_on": "2026-10-26", "observations": 1}]
    with patch("vula.api.qs.search_rates", return_value=learned):
        out = skill._dispatch("lookup_rate", {"description": "brick wall"}, "digg-demo")
    assert out["rates"][0]["where"] == "your HPC Bokaap BOQ, 26 Oct 2026"
    assert "never present a line total" in skill._system_prompt()
