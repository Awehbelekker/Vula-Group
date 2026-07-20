"""Tests for the follow-up-detection heuristic (vula/email_imap/sync.py:_needs_reply).
A real complaint: spam/phishing mail was showing up in Judy's follow-up digest because
request-y phrasing ("please confirm", "kindly advise") is exactly how phishing/promo copy
reads too. Added a List-Unsubscribe header check (bulk-mail senders are required to include
it) and a spam-phrase override that beats a request-keyword match."""
from vula.email_imap.sync import _needs_reply


def test_genuine_business_request_is_flagged():
    em = {"from": "client@example.com", "subject": "Quote needed",
          "body": "Hi, could you please send me a quote for the tiling job?"}
    assert _needs_reply(em, "digg-ct.co.za") == "request"


def test_own_domain_is_never_flagged():
    em = {"from": "colleague@digg-ct.co.za", "subject": "please confirm",
          "body": "could you confirm the site visit time?"}
    assert _needs_reply(em, "digg-ct.co.za") is None


def test_bulk_sender_pattern_is_not_flagged():
    em = {"from": "no-reply@supplier.com", "subject": "please confirm your order",
          "body": "could you confirm your recent order?"}
    assert _needs_reply(em, "digg-ct.co.za") is None


def test_list_unsubscribe_header_marks_bulk_mail_even_with_request_phrasing():
    em = {"from": "deals@retailer.co.za", "subject": "please confirm you want these deals",
          "body": "could you let me know if you'd like to keep receiving offers?", "bulk": True}
    assert _needs_reply(em, "digg-ct.co.za") is None


def test_phishing_phrasing_overrides_request_keyword_match():
    em = {"from": "security@bank-alert.com",
          "subject": "URGENT: verify your account",
          "body": "Please confirm your details now, your account has been suspended."}
    assert _needs_reply(em, "digg-ct.co.za") is None


def test_scam_lottery_email_is_not_flagged():
    em = {"from": "claims@prize-dept.com", "subject": "Congratulations you have won",
          "body": "Kindly advise your bank details to claim your prize, wire transfer required."}
    assert _needs_reply(em, "digg-ct.co.za") is None


def test_schedule_request_is_flagged():
    em = {"from": "client@example.com", "subject": "Site visit",
          "body": "When can we schedule a site visit this week?"}
    assert _needs_reply(em, "digg-ct.co.za") == "schedule"
