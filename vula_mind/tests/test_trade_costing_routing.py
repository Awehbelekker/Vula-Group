"""16 Nov 2026: "research the build-up for a 220 mm one-brick wall" on DIGG's WhatsApp went to
web search and came back from general knowledge — on a knowledge-mode line the team's messages
never reached the business assistant, which has the costing tools. Costing questions (and the
short replies that follow them) now go there, where the business has costing by trade on."""
from unittest.mock import AsyncMock

import pytest

from vula import open_chat
from vula.api import whatsapp
from vula.commerce.trade_checklists import is_costing_request

T, PHONE = "digg-demo", "27640000010"


@pytest.mark.parametrize("text", [
    "research the build-up for a 220 mm one-brick wall",
    "what goes into a m2 of plaster",
    "how much to build a 6 x 4 m wall, plastered both sides, not painted?",
    "How much will it cost to build a boundary wall",
    "estimate the cost of a 6x4 brick wall",
    "price for tiling 20 m2",
    "what would a concrete slab of 30 m2 cost",
    "compare the Bokaap estimate with what we paid",
    "any suggested changes?",
])
def test_costing_questions_are_recognised(text):
    assert is_costing_request(text)


@pytest.mark.parametrize("text", [
    "what is our fee estimate for HPC",
    "how much did we pay Cashbuild last month",
    "what does SANS 10400 say about wall thickness",
    "estimate when the drawings will be done",
    "send the invoice to Judy",
    "which projects are running",
    "",
])
def test_other_questions_are_not(text):
    assert not is_costing_request(text)


@pytest.fixture()
def line(monkeypatch):
    whatsapp._TRADE_FOLLOWUP.clear()
    state = {"on": True, "role": "owner"}
    monkeypatch.setattr("vula.features.on", lambda t, name: state["on"] and name == "trade_costing")
    monkeypatch.setattr(whatsapp, "_caller_identity", lambda t, p: ("Judy", state["role"]))
    admin = AsyncMock(return_value=True)
    monkeypatch.setattr(whatsapp, "_run_commerce_admin", admin)
    return state, admin


async def test_a_team_members_costing_question_goes_to_the_business_assistant(line):
    state, admin = line
    assert await whatsapp._trade_costing_turn(PHONE, "research the build-up for a 220 mm one-brick wall", T)
    admin.assert_awaited_once_with(PHONE, "research the build-up for a 220 mm one-brick wall", T)


async def test_the_short_reply_that_follows_goes_there_too_then_it_lets_go(line):
    state, admin = line
    assert await whatsapp._trade_costing_turn(PHONE, "how much to build a 6 x 4 m wall?", T)
    assert await whatsapp._trade_costing_turn(PHONE, "220 mm, plastered both sides", T)
    assert await whatsapp._trade_costing_turn(PHONE, "yes", T)
    assert admin.await_count == 3
    # a long, different question isn't swallowed — and ends the follow-up
    long_q = "what does the NHBRC require for the foundations on a site with clay soil and a slope"
    assert not await whatsapp._trade_costing_turn(PHONE, long_q, T)
    assert not await whatsapp._trade_costing_turn(PHONE, "yes", T)
    assert admin.await_count == 3


async def test_not_without_the_switch_and_not_for_someone_outside_the_team(line):
    state, admin = line
    state["on"] = False
    assert not await whatsapp._trade_costing_turn(PHONE, "what goes into a m2 of plaster", T)
    state["on"], state["role"] = True, None
    assert not await whatsapp._trade_costing_turn(PHONE, "what goes into a m2 of plaster", T)
    admin.assert_not_awaited()


async def test_an_ordinary_question_stays_with_the_knowledge_assistant(line):
    state, admin = line
    assert not await whatsapp._trade_costing_turn(PHONE, "what does SANS 10400 say about wall thickness", T)
    admin.assert_not_awaited()


async def test_the_business_assistant_failing_falls_through(line):
    state, admin = line
    admin.return_value = False
    assert not await whatsapp._trade_costing_turn(PHONE, "what goes into a m2 of plaster", T)
    assert not whatsapp._TRADE_FOLLOWUP


def test_ask_anything_sends_costing_questions_to_the_business_assistant_where_switched_on(monkeypatch):
    monkeypatch.setattr("vula.features.on", lambda t, name: t == T and name == "trade_costing")
    q = "how much to build a 6 x 4 m wall, plastered both sides?"
    assert open_chat.needs_records(q, tenant_id=T)
    assert not open_chat.needs_records(q, tenant_id="a-shop")
    assert not open_chat.needs_records(q)
    assert not open_chat.needs_records("explain how a cavity wall works", tenant_id=T)
