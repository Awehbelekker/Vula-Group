"""A message suggesting self-harm gets the crisis lines and a person, not the shop assistant."""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from vula import safety


@pytest.mark.parametrize("text", [
    "I want to end my life", "i think i'm going to kill myself", "I don't want to live anymore",
    "honestly I'm suicidal", "I keep hurting myself", "I'd be better off dead",
    "Ek wil nie meer lewe nie", "ek dink aan selfmoord", "ek gaan myself doodmaak",
])
def test_crisis_messages_are_recognised(text):
    assert safety.crisis_signal(text)


@pytest.mark.parametrize("text", [
    "This price is killing me 😂", "I'm dying to try the prawns", "Can I order 2kg hake?",
    "kill the order please, I'll redo it", "my phone died", "dead easy, thanks!",
])
def test_everyday_phrases_are_not(text):
    assert not safety.crisis_signal(text)


def test_the_person_gets_the_crisis_lines_and_the_business_is_told():
    sent = []

    async def send(to, body, tenant_id=None, **kw):
        sent.append((to, body))
    with patch("vula.api.whatsapp._send_reply", send), \
         patch("vula.api.tenants.display_name", return_value="Acme Fish"), \
         patch("vula.escalation._pick_helper", return_value={"whatsapp": "27820000009"}):
        assert asyncio.run(safety.handle("27820000001", "I want to end my life", "t1")) is True
    (to1, reply), (to2, alert) = sent
    assert to1 == "27820000001" and "0800 567 567" in reply and "0861 322 322" in reply and "Acme Fish" in reply
    assert to2 == "27820000009" and "+27820000001" in alert
    assert "end my life" not in alert            # the message itself is never passed on


def test_no_helper_still_answers_the_person():
    with patch("vula.api.whatsapp._send_reply", AsyncMock()) as send, \
         patch("vula.escalation._pick_helper", return_value=None):
        assert asyncio.run(safety.handle("27820000001", "i'm suicidal", "t1")) is True
    assert send.await_count == 1


def test_it_can_be_switched_off(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "crisis_support_enabled", False)
    assert not safety.crisis_signal("I want to end my life")


def test_it_runs_before_any_assistant_reply():
    from vula.api import whatsapp
    src = inspect.getsource(whatsapp._handle_message)
    assert src.index("safety.handle") < src.index("_OPTOUT_RE.match")


@pytest.mark.parametrize("text,kind", [
    ("my husband hits me every night", "abuse"), ("I was raped last week", "abuse"),
    ("hy slaan my", "abuse"), ("I'm addicted to tik and need help", "substance"),
    ("I have a drinking problem", "substance"), ("ek is verslaaf aan drank", "substance"),
])
def test_abuse_and_substance_are_recognised(text, kind):
    assert safety.kind_of(text) == kind


@pytest.mark.parametrize("text", [
    "I'm addicted to your prawns", "the driver hit my car", "can't stop eating these chips",
    "my husband will collect the order",
])
def test_and_their_everyday_lookalikes_are_not(text):
    assert safety.kind_of(text) is None


def test_abuse_gets_the_gbv_line_and_the_business_is_not_told():
    with patch("vula.api.whatsapp._send_reply", AsyncMock()) as send, \
         patch("vula.escalation._pick_helper") as pick:
        assert asyncio.run(safety.handle("27820000001", "my boyfriend beats me", "t1")) is True
    assert send.await_count == 1 and "0800 428 428" in send.await_args.args[1]
    pick.assert_not_called()


def test_substance_gets_the_sadag_substance_line():
    with patch("vula.api.whatsapp._send_reply", AsyncMock()) as send:
        assert asyncio.run(safety.handle("27820000001", "I can't stop drinking", "t1")) is True
    assert "0800 12 13 14" in send.await_args.args[1]


def test_other_sa_languages_go_through_the_ai_check(monkeypatch):
    from vula import escalation as esc
    monkeypatch.setattr(esc, "needs_classifier", lambda t: True)
    with patch.object(esc, "classify_customer_message",
                      AsyncMock(return_value={"wants_person": False, "upset": False, "crisis": True})), \
         patch("vula.api.whatsapp._send_reply", AsyncMock()) as send, \
         patch("vula.escalation._pick_helper", return_value=None):
        assert asyncio.run(safety.handle("27820000001", "ngifuna ukufa", "t1")) is True
    assert "0800 567 567" in send.await_args.args[1]


def test_the_ai_check_runs_once_per_message(monkeypatch):
    from vula import escalation as esc
    esc._CLASSIFY_CACHE.clear()
    calls = []

    async def fake(text, timeout):
        calls.append(text)
        return {"wants_person": True, "upset": False, "crisis": False}
    monkeypatch.setattr(esc, "_classify", fake)
    a = asyncio.run(esc.classify_customer_message("sawubona ngicela umuntu"))
    b = asyncio.run(esc.classify_customer_message("sawubona ngicela umuntu"))
    assert a == b and len(calls) == 1
