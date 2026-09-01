"""Guards on the escalate-and-learn loop, from a real production audit (2026-09-01).

vula_learned_answers held exactly two rows in production, both off-the-hook, and BOTH were wrong:

  Q: "What is in the family fish box?"  A: "Yes I can do"
      -> the helper replying about something else entirely
  Q: "Do you deliver to Timbuktu"       A: "Respond to Richard Downing via WhatsApp business
                                            and say delivery will be Monday 10:00-12:00"
      -> the helper instructing Vula, not answering, and naming a real customer

Both were live and being served. Probing production, "do you deliver to Milnerton" returned the
Timbuktu answer: Jaccard 3/5 = 0.6, over the old 0.5 bar, with the place name — the only word
that decides the answer — being the only difference.

These tests reproduce both incidents and lock the guards that stop them.
"""
from unittest.mock import MagicMock, patch

from vula import escalation as esc


def _db_with(rows):
    """Mock whose .eq("status","approved") chain yields `rows` (post-migration-150 shape)."""
    table = MagicMock()
    table.select.return_value.eq.return_value.order.return_value.limit.return_value \
        .eq.return_value.execute.return_value = MagicMock(data=rows)
    db = MagicMock()
    db.table.return_value = table
    return db


def _approved(question, answer):
    return {"question": question, "answer": answer, "status": "approved"}


# ── the Milnerton/Timbuktu incident ─────────────────────────────────────────────

def test_different_place_never_matches_a_stored_answer():
    """THE incident: a Milnerton customer must not get the Timbuktu answer."""
    rows = [_approved("Do you deliver to Timbuktu", "Delivery is Monday 10:00-12:00")]
    with patch.object(esc, "_client", lambda: _db_with(rows)):
        assert esc.find_learned_answer("off-the-hook", "do you deliver to Milnerton") is None


def test_same_question_still_matches():
    """The guard must not break the feature it protects."""
    rows = [_approved("Do you deliver to Timbuktu", "Delivery is Monday 10:00-12:00")]
    with patch.object(esc, "_client", lambda: _db_with(rows)):
        got = esc.find_learned_answer("off-the-hook", "Do you deliver to Timbuktu?")
    assert got == "Delivery is Monday 10:00-12:00"


def test_different_product_never_matches():
    rows = [_approved("What is the price of hake", "R160 per kg")]
    with patch.object(esc, "_client", lambda: _db_with(rows)):
        assert esc.find_learned_answer("off-the-hook", "What is the price of prawns") is None


def test_different_quantity_never_matches():
    rows = [_approved("Can I order 5kg of prawns", "Yes, 24 hours notice")]
    with patch.object(esc, "_client", lambda: _db_with(rows)):
        assert esc.find_learned_answer("off-the-hook", "Can I order 100kg of prawns") is None


def test_wording_differences_in_common_words_still_match():
    """'what is in the box' vs 'whats in the box' is the same question."""
    rows = [_approved("What is in the family fish box", "Hake, calamari and prawns")]
    with patch.object(esc, "_client", lambda: _db_with(rows)):
        got = esc.find_learned_answer("off-the-hook", "Whats in the family fish box?")
    assert got == "Hake, calamari and prawns"


def test_unrelated_question_does_not_match():
    rows = [_approved("Do you deliver to Timbuktu", "Monday 10:00-12:00")]
    with patch.object(esc, "_client", lambda: _db_with(rows)):
        assert esc.find_learned_answer("off-the-hook", "What are your opening hours") is None


# ── approval gate ───────────────────────────────────────────────────────────────

def test_pending_answers_are_never_served():
    """Only approved rows come back from the query; nothing unreviewed reaches a customer."""
    with patch.object(esc, "_client", lambda: _db_with([])):
        assert esc.find_learned_answer("off-the-hook", "Do you deliver to Timbuktu") is None


def test_fails_closed_before_migration_150():
    """Without the status column we must serve nothing rather than serve everything."""
    table = MagicMock()
    table.select.return_value.eq.return_value.order.return_value.limit.return_value \
        .eq.side_effect = Exception("column status does not exist")
    db = MagicMock()
    db.table.return_value = table
    with patch.object(esc, "_client", lambda: db):
        assert esc.find_learned_answer("off-the-hook", "anything at all") is None


# ── the "instruction to Vula" incident ──────────────────────────────────────────

def test_detects_the_real_richard_downing_instruction():
    assert esc.reply_is_instruction_to_vula(
        "Respond to Richard Downing via WhatsApp business and say delivery will be on Monday "
        "between 10:00 - 12:00") is True


def test_detects_common_instruction_shapes():
    for text in [
        "Tell them we're closed on Sunday",
        "Please reply to her and say we can do it",
        "Let the customer know it's R160",
        "Say that we deliver on Mondays",
        "Sê vir hom ons is toe",
    ]:
        assert esc.reply_is_instruction_to_vula(text) is True, text


def test_real_answers_are_not_flagged_as_instructions():
    for text in [
        "We deliver to Milnerton on Mondays between 10 and 12",
        "The family box has hake, calamari and prawns",
        "R160 per kg",
        "Yes we can do that",
        "Ons lewer Maandae af",
    ]:
        assert esc.reply_is_instruction_to_vula(text) is False, text


def test_instruction_reply_is_relayed_but_never_learned():
    """The helper meant the customer to hear something — relay it, but don't store a directive
    as the canonical answer for everyone else."""
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.eq.return_value \
        .execute.return_value = MagicMock(data=[{"id": "e1"}])
    esc_row = {"id": "e1", "tenant_id": "off-the-hook", "customer_phone": "27786537562",
               "question": "Do you deliver to Timbuktu"}
    with patch.object(esc, "_client", lambda: db):
        info = esc.answer_escalation(
            esc_row, "Respond to Richard Downing via WhatsApp business and say delivery is Monday")
    assert info is not None, "the customer must still get a reply"
    assert info["learned_id"] is None, "a directive must never be learned"
    assert not any(c.args and c.args[0] == "vula_learned_answers"
                   for c in db.table.call_args_list), "no learned-answer insert should happen"


def test_a_real_answer_is_learned_as_pending():
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.eq.return_value \
        .execute.return_value = MagicMock(data=[{"id": "e1"}])
    esc_row = {"id": "e1", "tenant_id": "off-the-hook", "customer_phone": "27786537562",
               "question": "What is in the family fish box"}
    with patch.object(esc, "_client", lambda: db):
        info = esc.answer_escalation(esc_row, "Hake, calamari and prawns — feeds four")
    assert info["learned_id"], "a genuine answer should be captured for review"
    inserted = db.table.return_value.insert.call_args[0][0]
    assert inserted["status"] == "pending", "must not be usable until the owner approves"


def test_contact_details_are_redacted_before_storing():
    """A stored answer gets replayed to OTHER customers — it must not carry someone's number."""
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.eq.return_value \
        .execute.return_value = MagicMock(data=[{"id": "e1"}])
    esc_row = {"id": "e1", "tenant_id": "off-the-hook", "customer_phone": "27786537562",
               "question": "Who do I call about my order"}
    with patch.object(esc, "_client", lambda: db):
        esc.answer_escalation(esc_row, "Call Sam on 082 123 4567 or mail sam@shop.co.za")
    stored = db.table.return_value.insert.call_args[0][0]["answer"]
    assert "082 123 4567" not in stored and "sam@shop.co.za" not in stored
    assert "[phone]" in stored and "[email]" in stored
