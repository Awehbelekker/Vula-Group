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


# ── the loop came BACK (2026-09-02, second Gerflor transcript) ──────────────────
#     [12:59] rep:  Please remind me to open a WhatsApp group for the two numbers
#     [12:59] Vula: I've set a reminder... 📅
#     [13:36] rep:  Tell me taralay impressions
#     [13:36] Vula: You've got 2 receipts I need the purpose for: ...
#
# "tell" was simply not in my request-verb allowlist. Any verb not thought of re-traps the
# user, so guessing at verbs was the wrong shape of solution. What actually settles it is
# whether Vula JUST ASKED — which needs no vocabulary at all.

import vula.api.whatsapp as _wa


@pytest.fixture(autouse=True)
def _clear_prompt_memory():
    _wa._purpose_prompted_at.clear()
    yield
    _wa._purpose_prompted_at.clear()


PHONE = "27645755210"


@pytest.mark.parametrize("text", [
    "Tell me taralay impressions",          # the exact message that re-broke it
    "Tell me about the Mipolam range",
    "Give me the price list",
    "Look up Winelands Flooring",
    "Research this company",
    "Open a WhatsApp group for those two",
    "Explain the zone pricing",
])
def test_a_request_long_after_the_question_is_never_intercepted(text):
    """No prompt was sent recently, so nothing here can be a receipt answer."""
    assert _wa._recently_asked_about_purpose(PHONE) is False
    assert not (_wa._recently_asked_about_purpose(PHONE)
                and _wa._looks_like_purpose_attempt(text, {}))


def test_a_purpose_reply_right_after_the_question_is_still_taken():
    _wa._note_purpose_prompt(PHONE)
    assert _wa._recently_asked_about_purpose(PHONE) is True
    for answer in ("fuel", "client lunch", "printer ink for the office"):
        assert _wa._looks_like_purpose_attempt(answer, {}) is True


def test_the_window_expires():
    import time
    _wa._note_purpose_prompt(PHONE)
    _wa._purpose_prompted_at[PHONE] = time.monotonic() - (_wa._PURPOSE_PROMPT_WINDOW_S + 1)
    assert _wa._recently_asked_about_purpose(PHONE) is False


def test_the_window_is_per_person():
    _wa._note_purpose_prompt(PHONE)
    assert _wa._recently_asked_about_purpose("27000000000") is False


def test_a_restart_fails_toward_letting_messages_through():
    """In-memory by design: forgetting we asked must never trap somebody."""
    _wa._purpose_prompted_at.clear()          # simulates a fresh process
    assert _wa._recently_asked_about_purpose(PHONE) is False
