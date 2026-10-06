"""Fixes from the 21–25 Sep WhatsApp chats (off-the-hook, digg-demo), each on the real wording."""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.base import leaked_tool_output
from core.skills.commerce_assistant import (REPEAT_REPLY, CommerceAssistantSkill,
                                            _repeats_last_reply)
from vula.escalation import should_escalate


# ── 1. A promised follow-up is an escalation ────────────────────────────────
@pytest.mark.parametrize("reply", [
    "I'll find out the price of Atlantic Mackerel for you and someone will get back to you.",
    "The team will be in touch shortly about your quote.",
    "Let me check with the shop and confirm.",
])
def test_promised_follow_up_escalates(reply):
    assert should_escalate(reply, 0.9)


@pytest.mark.parametrize("reply", [
    "Order OTH-12 confirmed — we'll contact you when it's out for delivery.",
    "Your cart: Atlantic Mackerel Fillets (1kg) R80.00, total R160.00.",
])
def test_ordinary_replies_do_not_escalate(reply):
    assert not should_escalate(reply, 0.9)


# ── 2. Never send the same reply twice running ──────────────────────────────
MAURITIUS = ("We do not deliver to Mauritius. We deliver to these areas: Table View, Sunningdale, "
             "West Beach, Parklands, Flamingo Vlei, Sunset Beach, Big Bay, Bloubergstrand.")
HISTORY = (f"Customer (3 min ago): Delivery fee to Mauritus ?\nAssistant (3 min ago): {MAURITIUS}\n"
           "Customer (1 min ago): You deliver within the Country and not outside South Africa")


def test_repeat_is_detected():
    assert _repeats_last_reply(MAURITIUS, HISTORY)
    assert _repeats_last_reply(MAURITIUS.replace("Bloubergstrand.", "Bloubergstrand"), HISTORY)
    assert not _repeats_last_reply("Yes — we deliver within Cape Town only, not the rest of South Africa.", HISTORY)
    assert not _repeats_last_reply(MAURITIUS, "")


@pytest.mark.asyncio
async def test_run_swaps_a_repeat_for_an_offer_of_the_team(monkeypatch):
    from core.skills import commerce_assistant as ca
    from core.skills.base import SkillInput
    skill = CommerceAssistantSkill()
    monkeypatch.setattr(ca, "_is_booking_focused", lambda t: False)
    monkeypatch.setattr(ca, "_tenant_has_bookings", lambda t: False)
    monkeypatch.setattr(skill, "_retrieve_kb", AsyncMock(return_value=("", [])))
    monkeypatch.setattr(skill, "_agent_loop", AsyncMock(return_value=MAURITIUS))
    out = await skill.run(SkillInput(question="Okay", tenant_id="off-the-hook", conversation_history=HISTORY,
                                     metadata={}))
    assert out.answer == REPEAT_REPLY


# ── 3. A bare JSON reply is tool output ─────────────────────────────────────
@pytest.mark.parametrize("text", [
    '{"status": "not_found_live", "message": "No emails found matching \'jackhammer\'."}',
    '{"type": "text", "text": "Sorry, I couldn\'t add the note. The tool says it couldn\'t find the task."}',
    '```json\n[{"id": 1}]\n```',
])
def test_bare_json_reply_is_caught(text):
    assert leaked_tool_output(text)


@pytest.mark.parametrize("text", ["{name} is a placeholder you can fill in", "", "{}x"])
def test_braces_in_prose_are_fine(text):
    assert not leaked_tool_output(text)


# ── 4. A closest-match product is named as such ─────────────────────────────
_FILLETS = {"id": "p1", "name": "Atlantic Mackerel Fillets", "price_cents": 8000, "sold_by": "kg"}


async def _add(args):
    with patch("core.skills.commerce_assistant.service.get_product_by_slug", new=AsyncMock(return_value=None)), \
         patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[_FILLETS])), \
         patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value={"id": "c1"})), \
         patch("core.skills.commerce_assistant.service.add_to_cart", new=AsyncMock()):
        return await CommerceAssistantSkill()._exec_add_to_cart("off-the-hook", "s1", "2782", args)


@pytest.mark.asyncio
async def test_closest_match_is_flagged():
    out = await _add({"product": "Atlantic Mackerel", "quantity": 1})
    assert out["added"] == "Atlantic Mackerel Fillets"
    assert "asked for 'Atlantic Mackerel'" in out["note"]


@pytest.mark.asyncio
async def test_exact_product_and_quantity_carry_no_note():
    out = await _add({"product": "atlantic mackerel fillet", "quantity": 2})
    assert "note" not in out
