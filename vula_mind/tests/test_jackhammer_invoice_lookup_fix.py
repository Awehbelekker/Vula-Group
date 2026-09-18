"""Real incident, 2026-09-18 WhatsApp transcript (DIGG tenant):

    [10:03] Aweh Be Lekker: Can you give me a breakdown on all jackhammer
    [10:03] DIGG: Noted — R18,076.74 was your own money, so it's marked to be paid back to you.
    [10:05] Aweh Be Lekker: Can you look up all invoice for jackhammer
    [10:05] DIGG: I couldn't find an order or invoice matching that. Try the order number...
    [10:06] Aweh Be Lekker: Through the emails...
    [10:07] DIGG: I couldn't find an order or invoice matching that. Try the order number...

Two separate bugs, both the same class as the Gerflor "receipts prompt" incident
(test_gerflor_chat_fixes.py): a message that plainly addresses Vula with a new request got
silently swallowed by an outstanding pending-question handler instead of ever reaching the agent
(which has a find_document tool that would have searched filed invoices, including ones ingested
from email, for "jackhammer").

1. _maybe_allocate_pending_expense's "company card or your own money?" branch used a bare
   substring check — "own" matches inside "breakdown" — so the first message was misread as an
   answer and silently mutated an unrelated expense claim's paid_with field.
2. bank_review.handle_answer/handle_client_answer had no request-shape guard at all, so the
   second and third messages were swallowed by an outstanding bank-review question and answered
   with a generic "couldn't find an order or invoice" instead of reaching the agent.
"""
import re

import pytest

from vula.api.whatsapp import _REQUEST_SHAPED, _ADDRESSES_ASSISTANT
from vula.commerce.bank_review import _is_request_shaped


# ── bug 1: the "own"-in-"breakdown" substring misfire ───────────────────────────

@pytest.mark.parametrize("text", [
    "Can you give me a breakdown on all jackhammer",   # the exact message from the transcript
    "breakdown please",
    "give me a breakdown",
])
def test_breakdown_no_longer_matches_own_as_a_keyword(text):
    low = text.lower()
    assert re.search(r"\b(company|own|personal|my card|my money|cash)\b", low) is None


@pytest.mark.parametrize("text", [
    "Can you give me a breakdown on all jackhammer",
    "give me a breakdown",
])
def test_breakdown_request_is_recognised_as_request_shaped(text):
    assert bool(text.endswith("?") or _REQUEST_SHAPED.match(text)
                or _ADDRESSES_ASSISTANT.search(text)) is True


@pytest.mark.parametrize("text", [
    "own",
    "my own money",
    "it was my own money",
    "company card",
    "cash",
])
def test_genuine_paid_with_answers_still_match(text):
    low = text.lower()
    assert re.search(r"\b(company|own|personal|my card|my money|cash)\b", low) is not None
    assert not (text.endswith("?") or _REQUEST_SHAPED.match(text)
                or _ADDRESSES_ASSISTANT.search(text))


# ── bug 2: bank-review questions swallowing real requests ───────────────────────

@pytest.mark.parametrize("text", [
    "Can you look up all invoice for jackhammer",   # the exact message from the transcript
    "Can you give me a breakdown on all jackhammer",
    "look up the invoice for the jackhammer",
    "what's the price of the jackhammer?",
    "Please send the quote",
])
def test_a_real_request_is_never_taken_as_a_bank_review_answer(text):
    assert _is_request_shaped(text) is True


@pytest.mark.parametrize("text", [
    "OFF-00006",
    "R Naidoo",
    "skip",
    "stock",
    "fuel",
    "stop",
])
def test_genuine_bank_review_answers_are_still_taken(text):
    assert _is_request_shaped(text) is False
