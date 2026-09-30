"""Owner corrections become permanent (2026-09-30): an explicit correction to a CONFIDENT answer
is captured, and an approved answer is used whenever the question matches — not only when the
fresh reply admits it doesn't know."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vula.api import whatsapp as wa


@pytest.mark.parametrize("text,captured", [
    ("No, we close at 4pm on Saturdays", True),
    ("That's wrong — the slip rating is R10", True),
    ("Actually the delivery fee is R80 for Table View", True),
    ("No, send me the Affinity sheet please", False),     # a new request, not a correction
    ("Thanks, that's great", False),                       # plain ack after a confident reply
])
@pytest.mark.asyncio
async def test_explicit_corrections_are_captured_after_a_confident_reply(monkeypatch, text, captured):
    msgs = [SimpleNamespace(role="user", text="What time do we close on Saturday?"),
            SimpleNamespace(role="assistant", text="We close at 1pm on Saturdays.")]
    monkeypatch.setattr("vula.chat.history.get_db", lambda: SimpleNamespace(get=lambda *a, **k: msgs))
    capture = []
    monkeypatch.setattr("vula.escalation.capture_owner_correction",
                        lambda tid, q, c: capture.append((q, c)) or "lid")
    monkeypatch.setattr(wa, "_get_tenant_wa_creds", AsyncMock(return_value=None))
    await wa._maybe_capture_owner_correction("digg-demo", "2782", "t", text)
    assert bool(capture) is captured
    if captured:
        assert capture[0][0] == "What time do we close on Saturday?"


@pytest.mark.asyncio
async def test_an_approved_answer_replaces_a_confident_reply(monkeypatch):
    monkeypatch.setattr("vula.escalation.find_learned_answer",
                        AsyncMock(return_value="We close at 4pm on Saturdays."))
    out = await wa._maybe_escalate_and_learn("digg-demo", "2782", "What time do we close on Saturday?",
                                             "We close at 1pm on Saturdays.", 0.9, caller_role="customer")
    assert out == "We close at 4pm on Saturdays."


@pytest.mark.asyncio
async def test_live_data_questions_never_use_a_stored_answer(monkeypatch):
    find = AsyncMock(return_value="stale")
    monkeypatch.setattr("vula.escalation.find_learned_answer", find)
    out = await wa._maybe_escalate_and_learn("oth", "2782", "Is there stock of hake?",
                                             "Yes, 12kg of hake.", 0.9, caller_role="customer")
    assert out == "Yes, 12kg of hake." and find.await_count == 0
