"""Hand-off gaps from the EZContact brief (2026-10-22, PR 2).

- A customer asking for a person always reaches one: no stored answer, no bot reply in between.
- Two weak replies in a row hand the chat to a person.
- Who took a chat over is recorded and shown in the inbox list.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula import escalation as esc


# ── "speak to a person", per language ────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Can I speak to someone please",                     # en
    "I want to talk to a real person",
    "can someone call me back",
    "Human please",
    "Kan ek met iemand praat? Ek wil praat met iemand",   # af
    "Kan iemand my bel asseblief",
    "Ngifuna ukukhuluma nomuntu",                         # isiZulu
    "Ndifuna ukuthetha nomntu",                           # isiXhosa
    "Ke batla ho bua le motho",                           # Sesotho
])
def test_asking_for_a_person_is_recognised(text):
    assert esc.wants_human(text)


@pytest.mark.parametrize("text", [
    "Do you deliver to Bellville?", "2kg hake please", "Who is your best supplier?",
    "Can someone deliver tomorrow?", "Thanks, speak soon", "I talked to my wife, we'll take 3",
])
def test_ordinary_messages_are_not(text):
    assert not esc.wants_human(text)


def test_afrikaans_no_answer_reply_escalates():
    assert esc.should_escalate("Ek weet nie, jammer.", 0.9)


@pytest.fixture()
def helper(monkeypatch):
    from vula.api import whatsapp as wa
    wa._WEAK_STREAK.clear()
    sent = []
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, **k: sent.append((p, m)) or True))
    monkeypatch.setattr(esc, "find_learned_answer", AsyncMock(return_value="Stored answer"))
    monkeypatch.setattr(esc, "customer_context", AsyncMock(return_value=""))
    created = []

    def _create(tid, phone, text):
        created.append(text)
        return {"id": "e1", "helper_phone": "27821112222", "helper_name": "Staci",
                "deduped": len(created) > 1}

    monkeypatch.setattr(esc, "create_escalation", _create)
    return wa, sent, created


@pytest.mark.asyncio
async def test_a_customer_asking_for_a_person_reaches_the_helper(helper):
    wa, sent, created = helper
    reply = await wa._maybe_escalate_and_learn(
        "off-the-hook", "27820001111", "Can I speak to someone please",
        "Sure! What would you like to order?")
    assert "asked someone from the team" in reply           # not the stored answer, not the bot
    assert sent and sent[0][0] == "27821112222" and "asked to speak to a person" in sent[0][1]
    # Asking twice pings once.
    await wa._maybe_escalate_and_learn("off-the-hook", "27820001111", "speak to a person!!", "Hi")
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_the_team_asking_for_a_person_is_not_a_handoff(helper):
    wa, sent, created = helper
    reply = await wa._maybe_escalate_and_learn(
        "digg-demo", "27827077080", "speak to someone at Builders about the order",
        "Here's their number.", caller_role="owner")
    assert reply == "Here's their number."     # Vula's own answer, no hand-off
    assert not created and not sent


# ── two weak replies in a row ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_weak_replies_in_a_row_hand_over(helper, monkeypatch):
    wa, sent, created = helper
    monkeypatch.setattr(esc, "find_learned_answer", AsyncMock(return_value=None))
    first = await wa._maybe_escalate_and_learn(
        "off-the-hook", "27820001111", "price of kob?", "Kob is fresh today.", streak_confidence=0.4)
    assert first == "Kob is fresh today." and not sent              # one weak reply: keep going
    second = await wa._maybe_escalate_and_learn(
        "off-the-hook", "27820001111", "and yellowtail?", "Yellowtail is lovely.", streak_confidence=0.4)
    assert "check with the team" in second
    assert "couldn't give this customer a good answer twice" in sent[0][1]


@pytest.mark.asyncio
async def test_a_good_reply_resets_the_run(helper, monkeypatch):
    wa, sent, created = helper
    monkeypatch.setattr(esc, "find_learned_answer", AsyncMock(return_value=None))
    for conf in (0.4, 0.8, 0.4):
        reply = await wa._maybe_escalate_and_learn(
            "off-the-hook", "27820001111", "kob?", "Kob is R120/kg.", streak_confidence=conf)
        assert reply == "Kob is R120/kg."
    assert not sent


# ── who took over ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_taking_over_records_who():
    from vula.commerce import service
    q = MagicMock()
    for m in ("update", "eq"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=[{"id": "s1", "paused": True}])
    db = MagicMock()
    db.table.return_value = q
    with patch.object(service, "_client", return_value=db):
        await service.set_session_paused("oth", "s1", True, paused_by="Thandi")
        assert q.update.call_args.args[0]["paused_by"] == "Thandi"
        await service.set_session_paused("oth", "s1", False)
        assert q.update.call_args.args[0]["paused_by"] is None


@pytest.mark.asyncio
async def test_takeover_name_prefers_the_team_name():
    from vula.api import commerce
    req = MagicMock()
    req.headers = {"authorization": "Bearer x"}
    with patch("vula.api.tenant_auth.member_scope",
               AsyncMock(return_value={"member": {"name": "Thandi"}, "email": "t@oth.co.za"})):
        assert await commerce._takeover_name(req, "oth") == "Thandi"
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value={"email": "t@oth.co.za"})):
        assert await commerce._takeover_name(req, "oth") == "t@oth.co.za"
    with patch("vula.api.tenant_auth.member_scope", AsyncMock(side_effect=RuntimeError)):
        assert await commerce._takeover_name(req, "oth") is None
