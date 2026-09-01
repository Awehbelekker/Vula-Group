"""The verification caveat is for the reader, never for the model's own memory.

Real Gerflor transcript, 2026-08-26 to 2026-08-31: the caveat was appended to result.answer, the
answer was persisted verbatim to commerce_conversation_messages, and on the NEXT turn the model
read its own caveat back as conversation history and started discussing it with the user:

  "Based on the conversation history, it seems like the customer is inquiring about the status
   of their order. However, I noticed that the automated review flagged possible issues with the
   previous answers, specifically the ones related to the proforma invoice..."

— narrating its internal review machinery instead of answering, and referring to the person it
was talking to in the third person. The caveat also stacked onto nearly every reply in that
session: a warning on everything warns about nothing.
"""
import core.verification as v
from core.verification import strip_caveat


def test_caveat_is_stripped_before_history():
    answer = "The price is R198.00 per square metre." + v._CAVEAT
    assert strip_caveat(answer) == "The price is R198.00 per square metre."
    assert "double-check" not in strip_caveat(answer)


def test_the_older_longer_wording_is_also_stripped():
    """Existing stored history carries the previous phrasing — it must clean up too."""
    old = ("I've saved the contact.\n\n⚠️ Please double-check this answer — an automated review "
           "flagged possible issues with it. If something's off, tell me specifically what's "
           "wrong (e.g. the right invoice/order number or detail) and I'll fix it.")
    assert strip_caveat(old) == "I've saved the contact."


def test_a_clean_answer_is_untouched():
    answer = "Hake Fillets are R160.00 per kg."
    assert strip_caveat(answer) == answer


def test_empty_and_none_are_safe():
    assert strip_caveat("") == ""
    assert strip_caveat(None) is None


def test_the_caveat_stays_short():
    """Caveat fatigue was half the problem — it ran to 200+ chars on every flagged reply."""
    assert len(v._CAVEAT.strip()) < 120, v._CAVEAT


def test_the_caveat_still_invites_a_correction():
    """It must remain actionable, not just a disclaimer."""
    assert "tell me" in v._CAVEAT.lower()


def test_apply_still_shows_the_caveat_to_the_reader():
    """Stripping is for persistence only — the person must still see the warning."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    class _R:
        error = None
        answer = "Some answer."
        sources = []
        confidence = 0.9
        verification = None

    class _S:
        name = "commerce_admin"
        verification_policy = "adversarial"

    class _I:
        question = "q"
        tenant_id = "gerflor"

    result = _R()
    with patch.object(v, "adversarial_check", AsyncMock(return_value={
            "verdict": "fail", "defects": ["x"], "checker_ms": 5})), \
         patch.object(v, "register_outcome", lambda *a, **k: None):
        asyncio.run(v.apply(_S(), _I(), result))

    assert v._CAVEAT_MARKER in result.answer, "the reader still gets the warning"
    assert strip_caveat(result.answer) == "Some answer.", "history gets the clean version"
