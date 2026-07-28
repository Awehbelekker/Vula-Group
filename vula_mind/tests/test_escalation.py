"""Tests for vula/escalation.py's customer-sentiment signal — closes a real staff-facing
gap identified in the 2026-07-27 comms-quality audit: escalation only ever looked at the
BOT's own confidence/uncertainty, never at whether the CUSTOMER's own message read upset.
A confidently-wrong-or-unhelpful reply to an angry customer never pulled a human in.

Also covers find_stale_open_escalations/mark_stale_notified (2026-07-28) — the data layer
behind generalizing proactive re-engagement past commerce-only tenants."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from vula import escalation as esc


# ── customer_seems_frustrated ───────────────────────────────────────────────────

def test_detects_explicit_complaint_words():
    assert esc.customer_seems_frustrated("This is ridiculous, worst service ever")
    assert esc.customer_seems_frustrated("this is a scam, I want a refund")
    assert esc.customer_seems_frustrated("Dis totale mors, ek is woedend")


def test_detects_shouting():
    assert esc.customer_seems_frustrated("WHY HAS NOBODY ANSWERED ME YET")
    assert esc.customer_seems_frustrated("THIS IS NOT OK I NEED HELP NOW")


def test_short_caps_not_flagged_as_shouting():
    # Order numbers, "OK", "ETA" etc. shouldn't trip the shouting heuristic.
    assert not esc.customer_seems_frustrated("OK")
    assert not esc.customer_seems_frustrated("OTH-00042")
    assert not esc.customer_seems_frustrated("ETA?")


def test_detects_repeated_punctuation():
    assert esc.customer_seems_frustrated("where is my order???")
    assert esc.customer_seems_frustrated("hello!!!")


def test_normal_messages_not_flagged():
    assert not esc.customer_seems_frustrated("Hi, what's the price of hake?")
    assert not esc.customer_seems_frustrated("Can I get a quote for delivery please")
    assert not esc.customer_seems_frustrated("Thanks so much!")


def test_empty_or_none_not_flagged():
    assert not esc.customer_seems_frustrated("")
    assert not esc.customer_seems_frustrated(None)
    assert not esc.customer_seems_frustrated("   ")


# ── should_escalate with customer_text ──────────────────────────────────────────

def test_escalates_on_frustrated_customer_even_with_confident_reply():
    assert esc.should_escalate(
        "Sure, here's the info.", confidence=0.9,
        customer_text="This is ridiculous, worst service ever!!!",
    ) is True


def test_does_not_escalate_on_normal_message_with_confident_reply():
    assert esc.should_escalate(
        "Sure, here's the info.", confidence=0.9,
        customer_text="what's the price of hake?",
    ) is False


def test_existing_confidence_behavior_unchanged_without_customer_text():
    assert esc.should_escalate("Sure, here's the info.", confidence=0.9) is False
    assert esc.should_escalate("Sure, here's the info.", confidence=0.1) is True


def test_no_answer_phrase_still_escalates_regardless_of_customer_text():
    assert esc.should_escalate(
        "I don't know that one.", confidence=0.9, customer_text="what's the price of hake?",
    ) is True


# ── find_stale_open_escalations / mark_stale_notified ───────────────────────────

def _mock_db_returning(rows):
    mock_table = MagicMock()
    chain = mock_table.select.return_value.eq.return_value.eq.return_value \
        .lt.return_value.gte.return_value.is_.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=rows)
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    return mock_db, mock_table


def test_find_stale_open_escalations_returns_rows():
    row = {"id": "e1", "tenant_id": "off-the-hook", "question": "do you deliver to Milnerton?",
          "status": "open", "helper_phone": "27821112222",
          "created_at": (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()}
    mock_db, mock_table = _mock_db_returning([row])
    with patch("vula.escalation._client", return_value=mock_db):
        result = esc.find_stale_open_escalations("off-the-hook", hours=6.0)
    assert result == [row]
    mock_table.select.return_value.eq.assert_called_with("tenant_id", "off-the-hook")


def test_find_stale_open_escalations_returns_empty_on_error():
    with patch("vula.escalation._client", side_effect=RuntimeError("down")):
        assert esc.find_stale_open_escalations("off-the-hook") == []


def test_mark_stale_notified_updates_row():
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    with patch("vula.escalation._client", return_value=mock_db):
        esc.mark_stale_notified("e1")
    mock_table.update.assert_called_once()
    patch_dict = mock_table.update.call_args[0][0]
    assert "stale_notified_at" in patch_dict
    mock_table.update.return_value.eq.assert_called_with("id", "e1")


def test_mark_stale_notified_never_raises_on_error():
    with patch("vula.escalation._client", side_effect=RuntimeError("down")):
        esc.mark_stale_notified("e1")  # must not raise
