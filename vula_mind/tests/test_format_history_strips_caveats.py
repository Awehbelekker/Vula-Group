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
