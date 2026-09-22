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

from vula.email_imap import service as email_service
from vula.email_imap.service import (
    _extract_text_body, _fetch_thread, _imap_since, _search, summarize_correspondence,
)


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


# ── _imap_since / _search date-range narrowing (2026-09-22) ─────────────────────────

def test_imap_since_converts_iso_date_to_imap_format():
    assert _imap_since("2026-08-01") == "01-Aug-2026"


def test_imap_since_fails_open_on_bad_input():
    assert _imap_since(None) is None
    assert _imap_since("not-a-date") is None
    assert _imap_since("") is None


def _search_imap_mock(search_ids: list[bytes]):
    m = MagicMock()
    m.search.return_value = ("OK", [b" ".join(search_ids)] if search_ids else [b""])
    m.fetch.return_value = ("OK", [None])
    return m


def test_search_since_is_anded_into_the_text_criteria():
    mock_imap = _search_imap_mock([])
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap):
        _search({"imap_host": "x", "email": "a@b.com", "password": "p"}, "jackhammer", 10,
                since="2026-08-01")
    args = mock_imap.search.call_args_list[0][0]
    assert args[1] == "SINCE" and args[2] == "01-Aug-2026" and args[3] == "TEXT"


def test_search_since_narrows_the_all_fallback_too():
    """Without `since`, a TEXT miss falls back to an unbounded ALL. With `since` set, it should
    fall back to a SINCE-bounded search instead of dropping the date bound entirely."""
    mock_imap = _search_imap_mock([])
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap):
        _search({"imap_host": "x", "email": "a@b.com", "password": "p"}, "jackhammer", 10,
                since="2026-08-01")
    fallback_args = mock_imap.search.call_args_list[1][0]
    assert fallback_args[1:] == ("SINCE", "01-Aug-2026")


def test_search_without_since_keeps_unbounded_all_fallback():
    mock_imap = _search_imap_mock([])
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap):
        _search({"imap_host": "x", "email": "a@b.com", "password": "p"}, "jackhammer", 10)
    fallback_args = mock_imap.search.call_args_list[1][0]
    assert fallback_args[1:] == ("ALL",)


# ── rate limiting: per-account lock + short-TTL cache on the public wrappers (2026-09-22) ──
#
# find_document's mailbox fallback + a narrowing-question round-trip can call search()/
# fetch_thread() more than once for the same mailbox in quick succession — _imap_login opens a
# fresh connection every call with no pooling, so an identical repeat call within the cache
# window should be served from cache rather than opening a second IMAP session.

@pytest.fixture(autouse=True)
def _clear_search_cache():
    email_service._search_cache.clear()
    email_service._search_locks.clear()
    yield
    email_service._search_cache.clear()
    email_service._search_locks.clear()


@pytest.mark.asyncio
async def test_identical_search_within_ttl_is_served_from_cache():
    mock_imap = _search_imap_mock([])
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap) as mock_login:
        await email_service.search({"email": "a@b.com"}, "jackhammer", 10)
        await email_service.search({"email": "a@b.com"}, "jackhammer", 10)
    assert mock_login.call_count == 1


@pytest.mark.asyncio
async def test_different_query_is_not_served_from_a_stale_cache_entry():
    mock_imap = _search_imap_mock([])
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap) as mock_login:
        await email_service.search({"email": "a@b.com"}, "jackhammer", 10)
        await email_service.search({"email": "a@b.com"}, "porterfield", 10)
    assert mock_login.call_count == 2


@pytest.mark.asyncio
async def test_different_accounts_never_share_a_cache_entry():
    mock_imap = _search_imap_mock([])
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap) as mock_login:
        await email_service.search({"email": "a@b.com"}, "jackhammer", 10)
        await email_service.search({"email": "judy@digg-ct.co.za"}, "jackhammer", 10)
    assert mock_login.call_count == 2


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
        out, total = _fetch_thread({"imap_host": "x", "email": "a@b.com", "password": "p"}, "nobody", 20)
    assert out == []
    assert total == 0


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


def test_fetch_thread_tries_from_as_a_last_resort_for_a_bare_name():
    """2026-09-22: 'Richard' (not an email address) used to only ever get a TEXT search — if
    that also misses, IMAP FROM matching is a substring match against the whole From: header
    (including display name), so a supplementary FROM attempt can still rescue a real sender
    whose name never appears in the subject/body text itself."""
    mock_imap = _imap_mock([], {})  # every search call returns no matches
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap):
        _fetch_thread({"imap_host": "x", "email": "a@b.com", "password": "p"}, "Richard", 20)
    criteria_used = [c[0][1] for c in mock_imap.search.call_args_list]
    assert criteria_used == ["TEXT", "FROM"]


def test_fetch_thread_skips_the_from_last_resort_once_text_already_hit():
    """Strictly additive — must not fire (or change anything) when TEXT already matched."""
    mock_imap = _imap_mock([b"1"], {})
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap):
        _fetch_thread({"imap_host": "x", "email": "a@b.com", "password": "p"}, "jackhammer", 20)
    criteria_used = [c[0][1] for c in mock_imap.search.call_args_list]
    assert criteria_used == ["TEXT"]


def test_fetch_thread_since_is_anded_into_the_search_criteria():
    mock_imap = _imap_mock([], {})
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap):
        _fetch_thread({"imap_host": "x", "email": "a@b.com", "password": "p"}, "jackhammer", 20,
                      since="2026-08-01")
    args = mock_imap.search.call_args_list[0][0]
    assert args[1] == "SINCE" and args[2] == "01-Aug-2026"
    assert args[3] == "TEXT"


def test_fetch_thread_returns_full_bodies_newest_first():
    msg1, msg2 = EmailMessage(), EmailMessage()
    msg1["From"], msg1["Subject"], msg1["Date"] = "supplier@x.co.za", "Quote", "Mon"
    msg1.set_content("First email body.")
    msg2["From"], msg2["Subject"], msg2["Date"] = "supplier@x.co.za", "Invoice", "Tue"
    msg2.set_content("Second email body.")
    mock_imap = _imap_mock([b"1", b"2"], {b"1": msg1, b"2": msg2})
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap):
        out, total = _fetch_thread({"imap_host": "x", "email": "a@b.com", "password": "p"}, "supplier", 20)
    assert len(out) == 2
    assert total == 2
    assert out[0]["subject"] == "Invoice"  # newest (uid 2) first
    assert "Second email body" in out[0]["body"]
    assert out[1]["subject"] == "Quote"


def test_fetch_thread_reports_total_matched_before_truncation():
    msgs = {}
    for n in range(1, 4):
        m = EmailMessage()
        m["From"], m["Subject"], m["Date"] = "supplier@x.co.za", f"Email {n}", f"Day {n}"
        m.set_content(f"Body {n}")
        msgs[str(n).encode()] = m
    mock_imap = _imap_mock([b"1", b"2", b"3"], msgs)
    with patch("vula.email_imap.service._imap_login", return_value=mock_imap):
        out, total = _fetch_thread({"imap_host": "x", "email": "a@b.com", "password": "p"},
                                   "supplier", limit=2)
    assert len(out) == 2
    assert total == 3


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
    with patch("vula.email_imap.service.fetch_thread", new=AsyncMock(return_value=([], 0))):
        res = await summarize_correspondence({}, "nonexistent supplier")
    assert "matches" not in res and "summary" not in res
    assert res["status"] == "not_found_live"
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
        patch("vula.email_imap.service.fetch_thread", new=AsyncMock(return_value=(emails, 2))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("local-model", None, "http://local"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(summary_text))) as mock_llm,
    ):
        res = await summarize_correspondence({}, "jackhammer")

    assert res["status"] == "found"
    assert res["summary"] == summary_text
    assert res["emails_covered"] == 2
    assert res["truncated"] is False
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
        patch("vula.email_imap.service.fetch_thread", new=AsyncMock(return_value=(emails, 1))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("local-model", None, "http://local"))),
        patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("model down"))),
    ):
        res = await summarize_correspondence({}, "jackhammer")
    assert "error" in res


# ── narrowing question on a truncated, open-ended request (2026-09-22) ──────────────
#
# Real complaint: "all mail from Richard" either silently truncated at the cap with no signal,
# or forced the user to repeat the same request after a flat "couldn't find it". The fix: default
# to a recent-first search (no upfront question), and only ask "how far back?" when that default
# search genuinely comes back with more than it showed AND the caller hasn't already scoped it
# (no `since`, no time-bound phrasing like "this week" already in the query).

@pytest.mark.asyncio
async def test_truncated_open_ended_request_asks_how_far_back_instead_of_a_partial_summary():
    emails = [{"uid": "1", "from": "richard@x.co.za", "subject": "s", "date": "d",
              "attachments": [], "body": "body"}]
    with patch("vula.email_imap.service.fetch_thread", new=AsyncMock(return_value=(emails, 30))):
        res = await summarize_correspondence({}, "Richard")
    assert res["status"] == "need_info"
    assert "how far back" in res["message"].lower()


@pytest.mark.asyncio
async def test_truncated_request_with_since_already_set_does_not_ask_again():
    """The whole point: once `since` narrows the search, don't re-ask even if still truncated —
    the model already scoped it, asking again would be the exact loop being complained about."""
    emails = [{"uid": "1", "from": "richard@x.co.za", "subject": "s", "date": "d",
              "attachments": [], "body": "body"}]
    with (
        patch("vula.email_imap.service.fetch_thread", new=AsyncMock(return_value=(emails, 30))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("local-model", None, "http://local"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response("summary"))),
    ):
        res = await summarize_correspondence({}, "Richard", since="2026-09-01")
    assert res["status"] == "found"
    assert res["truncated"] is True
    assert res["since"] == "2026-09-01"


@pytest.mark.asyncio
async def test_truncated_request_with_time_bound_phrasing_does_not_ask():
    """The query already scopes itself ('this week') — treat that the same as an explicit
    `since`, don't ask a question the user effectively already answered in their own wording."""
    emails = [{"uid": "1", "from": "richard@x.co.za", "subject": "s", "date": "d",
              "attachments": [], "body": "body"}]
    with (
        patch("vula.email_imap.service.fetch_thread", new=AsyncMock(return_value=(emails, 30))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("local-model", None, "http://local"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response("summary"))),
    ):
        res = await summarize_correspondence({}, "mail from Richard this week")
    assert res["status"] == "found"


@pytest.mark.asyncio
async def test_untruncated_open_ended_request_never_asks():
    """Everything fit — no truncation, no question, ever. This is the common case."""
    emails = [{"uid": "1", "from": "richard@x.co.za", "subject": "s", "date": "d",
              "attachments": [], "body": "body"}]
    with (
        patch("vula.email_imap.service.fetch_thread", new=AsyncMock(return_value=(emails, 1))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("local-model", None, "http://local"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response("summary"))),
    ):
        res = await summarize_correspondence({}, "Richard")
    assert res["status"] == "found"
    assert res["truncated"] is False


@pytest.mark.asyncio
async def test_since_is_passed_through_to_fetch_thread():
    with patch("vula.email_imap.service.fetch_thread",
              new=AsyncMock(return_value=([], 0))) as mock_fetch:
        await summarize_correspondence({}, "jackhammer", since="2026-08-01")
    mock_fetch.assert_awaited_once_with({}, "jackhammer", limit=20, since="2026-08-01")


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

    mock_service.summarize_correspondence.assert_awaited_once_with(creds, "jackhammer", since=None)
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

    mock_summarize.assert_awaited_once_with({"email": "a@b.com"}, "jackhammer", since=None)
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
