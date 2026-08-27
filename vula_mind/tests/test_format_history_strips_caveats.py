"""Regression test, 2026-08-26: format_history() previously fed the verification-review caveat
(appended once by core/verification.py, meant for a human reading the message) straight back
into the model's own conversation_history — confirmed live, a customer-facing session where the
model then tried to "resolve" its own past self-doubt annotation out loud WITH THE CUSTOMER
("I noticed the automated review flagged possible issues... could you confirm the customer
received..."). Caveats must still reach whoever's reading the message live; they must never
become part of what the model itself is fed as prior context.
"""
from vula.commerce.service import format_history


def test_format_history_strips_verification_caveat():
    messages = [
        {"role": "user", "content": "Please send the invoice"},
        {"role": "assistant", "content": "Your invoice has been sent.\n\n⚠️ Please double-check "
         "this answer — an automated review flagged possible issues with it. If something's "
         "off, tell me specifically what's wrong and I'll fix it."},
    ]
    result = format_history(messages)
    assert "automated review flagged" not in result
    assert "Your invoice has been sent." in result


def test_format_history_strips_no_document_caveat():
    messages = [
        {"role": "assistant", "content": "Here's what I found.\n\n⚠️ I couldn't find a specific "
         "document on this — worth double-checking anything critical."},
    ]
    result = format_history(messages)
    assert "couldn't find a specific document" not in result
    assert "Here's what I found." in result


def test_format_history_leaves_normal_content_untouched():
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello! How can I help?"},
    ]
    result = format_history(messages)
    assert result == "Customer: Hi\nAssistant: Hello! How can I help?"


# 2026-08-27: real incident, gerflor — a message from ~7 hours earlier in the same session got
# echoed back almost verbatim as the answer to a brand-new, unrelated question, because the
# model had zero signal that it wasn't fresh context. format_history now tags each line with
# its actual age.

def test_format_history_annotates_age():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    messages = [
        {"role": "user", "content": "Old question", "created_at": (now - timedelta(hours=7)).isoformat()},
        {"role": "assistant", "content": "Old answer", "created_at": (now - timedelta(hours=7)).isoformat()},
        {"role": "user", "content": "New question", "created_at": (now - timedelta(seconds=5)).isoformat()},
    ]
    result = format_history(messages)
    assert "Customer (7 hr ago): Old question" in result
    assert "Assistant (7 hr ago): Old answer" in result
    assert "Customer (just now): New question" in result


def test_format_history_missing_created_at_omits_age_tag():
    messages = [{"role": "user", "content": "Hi"}]
    result = format_history(messages)
    assert result == "Customer: Hi"
