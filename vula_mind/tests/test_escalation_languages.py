"""Gap audit 1.5: escalation in Sepedi and Setswana, and an AI check for the rest.

The phrase lists are starters outside English and Afrikaans (they want a native speaker's
review), so a message in another SA language the lists miss gets one cheap AI check."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vula import escalation as esc


@pytest.mark.parametrize("text", [
    "Ke kgopela go bolela le motho",                # Sepedi
    "Ke nyaka go bolela le molaodi",
    "Ke batla go bua le motho",                     # Setswana
    "Ke kopa go bua le mookamedi",
])
def test_asking_for_a_person(text):
    assert esc.wants_human(text)


@pytest.mark.parametrize("text", ["Ke befetšwe kudu", "ke befetswe", "Tirelo ye mpe",   # Sepedi
                                  "Ke shakgetse", "Ke kgopisegile", "tirelo e e maswe"])  # Setswana
def test_upset(text):
    assert esc.customer_seems_frustrated(text)


@pytest.mark.parametrize("reply", ["Ga ke tsebe, ke maswabi.", "Nka se go thuše ka seo.",  # Sepedi
                                   "Ga ke itse.", "Ke tla botsa batho."])                # Setswana
def test_bot_admitting_it_does_not_know_escalates(reply):
    assert esc.should_escalate(reply, 0.9)


@pytest.mark.parametrize("text", ["Dumela, ke kgopela tlhapi ya 2kg", "Ke batla go reka dijo"])
def test_ordinary_messages_are_not_hand_offs(text):
    assert not esc.wants_human(text) and not esc.customer_seems_frustrated(text)


def test_only_partly_covered_languages_get_the_ai_check(monkeypatch):
    monkeypatch.setattr(esc.settings, "escalation_classifier_enabled", True)
    assert esc.needs_classifier("Ndzi lava ku vulavula na munhu")       # Xitsonga
    assert esc.needs_classifier("Ngifuna ukwazi intengo")               # isiZulu
    assert not esc.needs_classifier("Do you deliver to Bellville?")
    assert not esc.needs_classifier("Ek wil asseblief bestel")
    monkeypatch.setattr(esc.settings, "escalation_classifier_enabled", False)
    assert not esc.needs_classifier("Ndzi lava ku vulavula na munhu")


def _model_says(monkeypatch, content=None, exc=None):
    import core.llm_router as router
    monkeypatch.setattr(router, "resolve_generation_route", AsyncMock(return_value=("ollama/x", None, "b")))

    async def complete(route, **kw):
        if exc:
            raise exc
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))]), route

    monkeypatch.setattr(router, "complete_local_first", complete)


@pytest.mark.asyncio
async def test_classifier_reads_the_models_json(monkeypatch):
    _model_says(monkeypatch, 'Sure: {"wants_person": true, "upset": false}')
    assert await esc.classify_customer_message("x") == {"wants_person": True, "upset": False}
    _model_says(monkeypatch, "I think so")                  # no JSON: neither
    assert await esc.classify_customer_message("x") == {"wants_person": False, "upset": False}


@pytest.mark.asyncio
async def test_classifier_failure_keeps_the_phrase_lists_answer(monkeypatch):
    _model_says(monkeypatch, exc=TimeoutError())
    assert await esc.classify_customer_message("x") is None


@pytest.fixture()
def helper(monkeypatch):
    from vula.api import whatsapp as wa
    wa._WEAK_STREAK.clear()
    sent = []
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, **k: sent.append((p, m)) or True))
    monkeypatch.setattr(esc, "find_learned_answer", AsyncMock(return_value=None))
    monkeypatch.setattr(esc, "customer_context", AsyncMock(return_value=""))
    monkeypatch.setattr(esc, "create_escalation",
                        lambda tid, phone, text: {"id": "e1", "helper_phone": "27821112222", "helper_name": "Staci"})
    monkeypatch.setattr(esc.settings, "escalation_classifier_enabled", True)
    return wa, sent


@pytest.mark.asyncio
async def test_xitsonga_request_for_a_person_reaches_the_team(helper, monkeypatch):
    wa, sent = helper
    classify = AsyncMock(return_value={"wants_person": True, "upset": False})
    monkeypatch.setattr(esc, "classify_customer_message", classify)
    reply = await wa._maybe_escalate_and_learn("off-the-hook", "27820001111",
                                               "Ndzi lava ku vulavula na munhu", "Hi there!")
    assert "asked someone from the team" in reply
    assert sent and sent[0][0] == "27821112222"
    classify.assert_awaited_once()


@pytest.mark.asyncio
async def test_upset_customer_found_by_the_check_is_handed_over(helper, monkeypatch):
    wa, sent = helper
    monkeypatch.setattr(esc, "classify_customer_message",
                        AsyncMock(return_value={"wants_person": False, "upset": True}))
    reply = await wa._maybe_escalate_and_learn("off-the-hook", "27820001111",
                                               "Ndzi lava ku tiva leswaku hikwalaho ka yini", "Sure!")
    assert "check with the team" in reply and sent


@pytest.mark.asyncio
async def test_no_ai_check_for_english_or_for_the_team(helper, monkeypatch):
    wa, sent = helper
    classify = AsyncMock(return_value={"wants_person": True, "upset": True})
    monkeypatch.setattr(esc, "classify_customer_message", classify)
    assert await wa._maybe_escalate_and_learn("t", "27820001111", "Do you deliver?", "Yes!") == "Yes!"
    assert await wa._maybe_escalate_and_learn("t", "27827077080", "Ndzi lava ku vulavula na munhu",
                                              "Done.", caller_role="owner") == "Done."
    classify.assert_not_awaited()
    assert not sent
