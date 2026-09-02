"""Two real bugs from the Gerflor transcript of 2026-09-02.

    [10:25] Gerflor Western Cape: Please remind me to contact Danielle in two weeks. <maps link>
    [10:28] GerFlor Cape Town:    I couldn't set a reminder to contact Danielle in two weeks...
    [10:28] Gerflor Western Cape: Get company details and set reminder
    [10:29] GerFlor Cape Town:    You've got 2 receipts I need the purpose for: ...
    [10:29] Gerflor Western Cape: Get company details and set reminder
    [10:29] GerFlor Cape Town:    You've got 2 receipts I need the purpose for: ...

1. "in two weeks" could not be resolved — the date parser understood digits but not number
   words, so the identical request in words failed where "in 2 weeks" worked. People speak in
   words, especially in a voice note.

2. The rep asked the same thing twice and got the identical receipts prompt back both times:
   with receipts pending, ANY unparseable message was swallowed and replaced by the prompt, so
   their actual request never reached the agent. The only way out of the conversation was to
   answer about the receipts. This is the "stuck in a loop" reported earlier.
"""
import pytest

from vula.clickup.service import _resolve_relative_date
from vula.api.whatsapp import _looks_like_purpose_attempt


# ── 1. spoken dates ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "in two weeks",            # the exact phrase from the transcript
    "in two weeks time",
    "in a fortnight",
    "in a couple of weeks",
])
def test_two_weeks_spoken_resolves_the_same_as_digits(phrase):
    assert _resolve_relative_date(phrase) == _resolve_relative_date("in 2 weeks")


@pytest.mark.parametrize("words,digits", [
    ("in three days", "in 3 days"),
    ("in one week", "in 1 week"),
    ("in ten days", "in 10 days"),
    ("in a few days", "in 3 days"),
])
def test_number_words_match_their_digit_equivalents(words, digits):
    assert _resolve_relative_date(words) == _resolve_relative_date(digits)


def test_digits_still_work_unchanged():
    for p in ("in 2 weeks", "in 3 days", "tomorrow", "today", "next Friday"):
        assert _resolve_relative_date(p) is not None


def test_unrecognised_phrasing_still_returns_none():
    """Returning None lets the caller try ISO parsing — inventing a date would be worse."""
    for p in ("sometime soon", "when you get a chance", "", "next quarter"):
        assert _resolve_relative_date(p) is None


# ── 2. the pending-receipt loop ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Get company details and set reminder",     # the exact message from the transcript
    "Get company details and set reminder",
    "what's the price of Mipolam 180?",
    "Please send the quote to Danielle",
    "remind me to call her on Friday",
    "How many rolls do we have in stock?",
    "can you check the pricing structure",
])
def test_a_real_request_is_never_swallowed_by_the_receipts_prompt(text):
    assert _looks_like_purpose_attempt(text, {}) is False, \
        "this message must reach the agent, not be answered with the receipts listing"


@pytest.mark.parametrize("text", [
    "fuel",
    "client lunch",
    "petrol",
])
def test_a_genuine_purpose_answer_is_still_taken(text):
    assert _looks_like_purpose_attempt(text, {}) is True


def test_an_indexed_reply_is_always_treated_as_an_attempt():
    """Even a partial one — "1 fuel" with two pending — is clearly about the receipts."""
    assert _looks_like_purpose_attempt("1 fuel", {1: "fuel"}) is True


def test_a_long_message_is_not_a_purpose():
    long_msg = ("I was thinking about the Stellenbosch job and whether we should be quoting "
                "the Taraflex or the Mipolam for that specification")
    assert _looks_like_purpose_attempt(long_msg, {}) is False


def test_an_empty_message_is_not_an_attempt():
    assert _looks_like_purpose_attempt("", {}) is False
    assert _looks_like_purpose_attempt("   ", {}) is False
