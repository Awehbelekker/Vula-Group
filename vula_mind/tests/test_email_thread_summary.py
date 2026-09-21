"""Tests for the email_thread_summary feature (2026-09-18): summarizing actual email CONTENT
(not just filed attachments) for a supplier/sender/topic, added after a real request — "look
at all the mail from a supplier and summarize what needs to be done" — found no correct tool to
reach for. find_document (vula.commerce.service.find_filed_document) answers "what documents do
we have"; this answers "what's actually been said" by reading the emails themselves.

Three layers tested:
  1. _extract_text_body — real MIME parsing, including the HTML-only fallback (2026-09-18: a
     real, common shape for a supplier notification email; the old text/plain-only extraction
     silently returned an empty body for these).
  2. summarize_correspondence — the shared implementation (vula.email_imap.service), mocking
     fetch_thread and the LLM call.
  3. Both tools' delegation (email_admin.py and commerce_admin.py — duplicated the same way as
     find_document, since a commerce-mode tenant's owner never reaches email_admin at all).
"""
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.email_imap.service import _extract_text_body, _fetch_thread, summarize_correspondence


# ── _extract_text_body ───────────────────────────────────────────────────────────

def test_plain_text_body_extracted():
    m = EmailMessage()
    m.set_content("Please see the attached invoice for R4,565.50.")
    assert "R4,565.50" in _extract_text_body(m)


def test_html_only_body_falls_back_to_converted_text():
    """2026-09-18: a real, common shape — an HTML-only order-confirmation/invoice-notification
    email with no text/plain part at all. The old extraction (text/plain only) silently
    returned '' for these; a summary built on that would confidently omit real content."""
    m = EmailMessage()
    m.set_content("<html><body><p>Your order for a <b>Jackhammer</b> rental is confirmed, "
                  "total R850.00.</p></body></html>", subtype="html")
    body = _extract_text_body(m)
    assert "Jackhammer" in body
    assert "R850.00" in body
    assert "<html>" not in body and "<b>" not in body


def test_multipart_alternative_prefers_plain_text_over_html():
    m = EmailMessage()
    m.set_content("Plain version: order confirmed.")
    m.add_alternative("<html><body>HTML version: order confirmed.</body></html>", subtype="html")
    body = _extract_text_body(m)
    assert "Plain version" in body


def test_no_body_at_all_returns_empty_string():
    m = EmailMessage()
    m["Subject"] = "No body"
    assert _extract_text_body(m) == ""


def test_attachment_part_is_never_read_as_the_body():
    m = EmailMessage()
    m.set_content("Real body text.")
    m.add_attachment(b"not-the-body", maintype="text", subtype="plain", filename="notes.txt")
    body = _extract_text_body(m)
    assert "Real body text" in body
    assert "not-the-body" not in body


# ── _fetch_thread ─────────────────────────────────────────────────────────────────

def _imap_mock(search_ids: list[bytes], messages: dict[bytes, EmailMessage]):
    m = MagicMock()
    m.search.return_value = ("OK", [b" ".join(search_ids)] if search_ids else [b""])

    def _fetch(uid, spec):
        msg = messages.get(uid)
        if not msg:
            return ("OK", [None])
        return ("OK", [(b"1 (BODY[])", msg.as_bytes())])
    m.fetch.side_effect = _fetch
    return m


def test_fetch_thread_no_match_returns_empty_not_the_whole_inbox():
    """Deliberately different from _search: no ALL fallback. An empty match means 'nothing
    from/about that' — summarizing the whole inbox instead would be wrong and wasteful."""
    with patch("vula.email_imap.service._imap_login", return_value=_imap_mock([], {})):
        out = _fetch_thread({"imap_host": "x", "email": "a@b.com", "password": "p"}, "nobody", 20)
    assert out == []


def test_fetch_thread_uses_from_search_for_an_email_address():
    mock_imap = _imap_mock([], {})
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap):
        _fetch_thread({"imap_host": "x", "email": "a@b.com", "password": "p"},
                      "supplier@jackhammer.co.za", 20)
    assert mock_imap.search.call_args_list[0][0][1] == "FROM"


def test_fetch_thread_falls_back_to_text_search_for_a_topic():
    mock_imap = _imap_mock([], {})
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap):
        _fetch_thread({"imap_host": "x", "email": "a@b.com", "password": "p"}, "jackhammer", 20)
    assert mock_imap.search.call_args_list[0][0][1] == "TEXT"


def test_fetch_thread_returns_full_bodies_newest_first():
    msg1, msg2 = EmailMessage(), EmailMessage()
    msg1["From"], msg1["Subject"], msg1["Date"] = "supplier@x.co.za", "Quote", "Mon"
    msg1.set_content("First email body.")
    msg2["From"], msg2["Subject"], msg2["Date"] = "supplier@x.co.za", "Invoice", "Tue"
    msg2.set_content("Second email body.")
    mock_imap = _imap_mock([b"1", b"2"], {b"1": msg1, b"2": msg2})
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap):
        out = _fetch_thread({"imap_host": "x", "email": "a@b.com", "password": "p"}, "supplier", 20)
    assert len(out) == 2
    assert out[0]["subject"] == "Invoice"  # newest (uid 2) first
    assert "Second email body" in out[0]["body"]
    assert out[1]["subject"] == "Quote"


# ── summarize_correspondence ───────────────────────────────────────────────────────

def _llm_response(text: str):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    return resp


@pytest.mark.asyncio
async def test_requires_a_query():
    res = await summarize_correspondence({}, "")
    assert "error" in res


@pytest.mark.asyncio
async def test_no_matching_emails_gives_actionable_message_not_a_guess():
    with patch("vula.email_imap.service.fetch_thread", new=AsyncMock(return_value=[])):
        res = await summarize_correspondence({}, "nonexistent supplier")
    assert "matches" not in res and "summary" not in res
    assert "message" in res


@pytest.mark.asyncio
async def test_fetch_failure_returns_error_not_raise():
    with patch("vula.email_imap.service.fetch_thread", new=AsyncMock(side_effect=RuntimeError("imap down"))):
        res = await summarize_correspondence({}, "jackhammer")
    assert "error" in res


@pytest.mark.asyncio
async def test_summarizes_matching_emails_and_carries_the_verification_caveat():
    emails = [
        {"uid": "2", "from": "supplier@jackhammer.co.za", "subject": "Invoice #245492",
         "date": "2026-09-16", "attachments": ["invoice.pdf"],
         "body": "1x Jackhammer rental, 2 days, R850.00 excl VAT. Please pay within 7 days."},
        {"uid": "1", "from": "supplier@jackhammer.co.za", "subject": "Quote request",
         "date": "2026-09-10", "attachments": [],
         "body": "Can you confirm availability of a jackhammer for next week?"},
    ]
    summary_text = ("Correspondence with Gardens Handiman Centre about a jackhammer rental. "
                    "Status: invoiced R850.00, due within 7 days. Action: pay the invoice.")
    with (
        patch("vula.email_imap.service.fetch_thread", new=AsyncMock(return_value=emails)),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("local-model", None, "http://local"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(summary_text))) as mock_llm,
    ):
        res = await summarize_correspondence({}, "jackhammer")

    assert res["summary"] == summary_text
    assert res["emails_covered"] == 2
    assert res["newest"] == "2026-09-16"
    assert res["oldest"] == "2026-09-10"
    assert "verify" in res["note"].lower()
    # Both emails' actual content reached the prompt, not just headers.
    prompt = mock_llm.call_args.kwargs["messages"][0]["content"]
    assert "R850.00" in prompt and "confirm availability" in prompt
    assert "data to summarize" in prompt.lower()


@pytest.mark.asyncio
async def test_llm_failure_returns_error_not_raise():
    emails = [{"uid": "1", "from": "x@y.com", "subject": "s", "date": "d",
              "attachments": [], "body": "body"}]
    with (
        patch("vula.email_imap.service.fetch_thread", new=AsyncMock(return_value=emails)),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("local-model", None, "http://local"))),
        patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("model down"))),
    ):
        res = await summarize_correspondence({}, "jackhammer")
    assert "error" in res


# ── tool delegation: email_admin.py ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_email_admin_dispatches_to_shared_service_function():
    from core.skills.email_admin import EmailAdminSkill
    skill = EmailAdminSkill()
    creds = {"email": "a@b.com"}
    expected = {"summary": "...", "emails_covered": 3}
    with patch("core.skills.email_admin.service") as mock_service:
        mock_service.summarize_correspondence = AsyncMock(return_value=expected)
        res = await skill._dispatch("email_thread_summary", {"query": "jackhammer"}, "digg-demo", creds)

    mock_service.summarize_correspondence.assert_awaited_once_with(creds, "jackhammer")
    assert res is expected


def test_email_thread_summary_is_a_registered_tool_spec():
    from core.skills.email_admin import TOOL_SPECS
    names = [t["function"]["name"] for t in TOOL_SPECS]
    assert "email_thread_summary" in names


# ── tool delegation: commerce_admin.py (duplicated, same reason as find_document) ──

@pytest.mark.asyncio
async def test_commerce_admin_dispatches_to_shared_service_function_when_mailbox_connected():
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill()
    expected = {"summary": "...", "emails_covered": 2}
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value={"email": "a@b.com"}),
        patch("vula.email_imap.service.summarize_correspondence",
              new=AsyncMock(return_value=expected)) as mock_summarize,
    ):
        res = await skill._email_thread_summary("test-tenant", {"query": "jackhammer"})

    mock_summarize.assert_awaited_once_with({"email": "a@b.com"}, "jackhammer")
    assert res is expected


@pytest.mark.asyncio
async def test_commerce_admin_reports_no_mailbox_connected_plainly():
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill()
    with patch("vula.email_imap.credentials.get_email_creds", return_value=None):
        res = await skill._email_thread_summary("test-tenant", {"query": "jackhammer"})
    assert "error" in res
    assert "connect" in res["error"].lower()


def test_email_thread_summary_is_a_registered_commerce_admin_tool_spec():
    from core.skills.commerce_admin import TOOL_SPECS
    names = [t["function"]["name"] for t in TOOL_SPECS]
    assert "email_thread_summary" in names


def test_email_thread_summary_not_offered_to_sales_rep():
    """Same shop-wide/sensitive-correspondence boundary as find_document."""
    from core.skills.commerce_admin import _REP_TOOL_SPECS
    names = [t["function"]["name"] for t in _REP_TOOL_SPECS]
    assert "email_thread_summary" not in names
