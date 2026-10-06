"""HPC profit on WhatsApp (DIGG, 6 Oct): the answer is sent as job costing wrote it, says what
the figures include, and the follow-ups ("Is this before the tax benefits", "Sorry after") go
back to the admin agent instead of memory_recall / email_admin."""
from unittest.mock import patch

import pytest

from core.skills import commerce_admin as ca
from core.skills.base import looks_like_follow_up
from vula.commerce import job_costing

HPC = {"project": "HPC Bokaap", "first": "2026-06-25", "last": "2026-09-29",
       "received_cents": 230071014, "cost_cents": 192208346, "fee_pct": 10.0,
       "target_received_cents": 211429181, "fee_earned_cents": 37862668,
       "fee_target_cents": 19220835, "fee_shortfall_cents": 0,
       "overhead_share_cents": 23642351, "profit_cents": 14220317, "status": "on track",
       "trades": [{"trade": "Labour", "cents": 60000000}], "unallocated_trade_cents": 0}
RES = {"projects": [HPC], "overheads_cents": 23642351, "overhead_rate_pct": 12.3,
       "fees_earned_cents": 37862668, "fees_target_cents": 19220835,
       "unallocated_project_spend_cents": 0}


@pytest.mark.parametrize("registered,phrase", [
    (False, "VAT on supplier costs is counted as cost"), (True, "amounts exclude VAT")])
def test_profit_text_says_what_the_figures_include(registered, phrase):
    with patch.object(job_costing, "costing", return_value=RES), \
            patch("vula.commerce.accounting.is_vat_registered", return_value=registered):
        text = job_costing.project_profit("digg-demo", "HPC")["text"]
    assert "R2,300,710.14" in text and "R236,423.51" in text and "R142,203.17" in text
    assert "before income tax" in text and phrase in text


@pytest.mark.asyncio
async def test_profit_reply_is_sent_as_the_tool_wrote_it():
    skill = ca.CommerceAdminSkill()
    with patch.object(job_costing, "costing", return_value=RES), \
            patch("vula.commerce.accounting.is_vat_registered", return_value=False):
        out = await skill._dispatch_tool("project_profit", {"project": "HPC"},
                                         {"tenant_id": "digg-demo"})
    assert ca.checked_reply(out) == out["text"]


@pytest.mark.parametrize("text", ["Is this before the tax benefits", "So what would it be before?",
                                  "Sorry after"])
def test_short_questions_about_the_last_answer_are_follow_ups(text):
    assert looks_like_follow_up(text)


@pytest.mark.parametrize("text", ["Check my email", "Send the HPC invoice to Judy",
                                  "What should I charge per m2 for tiling?"])
def test_new_requests_are_not_follow_ups(text):
    assert not looks_like_follow_up(text)
