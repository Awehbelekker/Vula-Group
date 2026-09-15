"""core/skills/commerce_admin.py::draft_followup_email + vula/commerce/mail_router.py's new
create_tenant_draft (2026-09-15).

draft_followup_email (a meeting follow-up to a real customer -- exactly the kind of message
that should always be reviewed before sending, never auto-sent) was hardcoded to Gmail only. A
tenant with a working IMAP or Microsoft mailbox connected instead -- confirmed the more common
case, see mail_router.py's own docstring -- got a dead-end "connect Google" error despite
already having mail connected. Both draft.py-style backend capabilities already existed
independently (email_imap.service.save_draft, microsoft.service.mail_create_draft); this wires
them into the same tenant-agnostic fallback order sending already uses.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill
from vula.commerce.mail_router import create_tenant_draft

TID = "off-the-hook"
CTX = {"tenant_id": TID, "phone": "27821234567", "caller_name": "Staci", "caller_role": None}


# ── create_tenant_draft (mail_router.py) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_uses_imap_when_connected():
    with (
        patch("vula.email_imap.credentials.get_email_creds",
              return_value={"email": "staci@offthehook.co.za"}),
        patch("vula.email_imap.service.save_draft",
              new=AsyncMock(return_value={"saved_to": "Drafts", "to": "x@y.com", "subject": "s"})),
    ):
        result = await create_tenant_draft(TID, "x@y.com", "s", "body")
    assert result["via"] == "imap"
    assert result["saved_to"] == "Drafts"


@pytest.mark.asyncio
async def test_falls_back_to_microsoft_when_no_imap():
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value=None),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.mail_create_draft",
              new=AsyncMock(return_value={"draft_id": "d1", "to": "x@y.com", "subject": "s"})),
    ):
        result = await create_tenant_draft(TID, "x@y.com", "s", "body")
    assert result["via"] == "microsoft"
    assert result["draft_id"] == "d1"


@pytest.mark.asyncio
async def test_falls_back_to_microsoft_when_imap_save_fails():
    """IMAP is connected but the save itself fails (e.g. no Drafts folder found) — must still
    try Microsoft rather than giving up."""
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value={"email": "a@b.com"}),
        patch("vula.email_imap.service.save_draft",
              new=AsyncMock(return_value={"error": "could not find a Drafts folder"})),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.mail_create_draft",
              new=AsyncMock(return_value={"draft_id": "d1", "to": "x@y.com", "subject": "s"})),
    ):
        result = await create_tenant_draft(TID, "x@y.com", "s", "body")
    assert result["via"] == "microsoft"


@pytest.mark.asyncio
async def test_falls_back_to_microsoft_when_imap_raises():
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value={"email": "a@b.com"}),
        patch("vula.email_imap.service.save_draft", new=AsyncMock(side_effect=RuntimeError("imap down"))),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.mail_create_draft",
              new=AsyncMock(return_value={"draft_id": "d1", "to": "x@y.com", "subject": "s"})),
    ):
        result = await create_tenant_draft(TID, "x@y.com", "s", "body")
    assert result["via"] == "microsoft"


@pytest.mark.asyncio
async def test_none_when_nothing_connected():
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value=None),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value=None)),
    ):
        assert await create_tenant_draft(TID, "x@y.com", "s", "body") is None


@pytest.mark.asyncio
async def test_none_when_both_backends_fail():
    with (
        patch("vula.email_imap.credentials.get_email_creds", return_value={"email": "a@b.com"}),
        patch("vula.email_imap.service.save_draft", new=AsyncMock(return_value={"error": "x"})),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.mail_create_draft", new=AsyncMock(return_value={"error": "x"})),
    ):
        assert await create_tenant_draft(TID, "x@y.com", "s", "body") is None


# ── draft_followup_email tool (commerce_admin.py) ────────────────────────────────────────

@pytest.fixture
def skill():
    return CommerceAdminSkill()


def _llm_body_response(text="Great meeting you today, thanks for your time."):
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=text))]
    return resp


@pytest.mark.asyncio
async def test_missing_recipient_errors_before_any_backend_is_tried(skill):
    result = await skill._draft_followup_email(TID, {"meeting_notes": "notes"}, CTX)
    assert "error" in result


@pytest.mark.asyncio
async def test_uses_gmail_when_connected_unchanged_behavior(skill):
    """Existing, already-working Gmail-connected tenants must see no change at all."""
    with (
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("m", "k", "b"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_body_response())),
        patch("vula.google.service.gmail_create_draft", new=AsyncMock(return_value={"id": "g1"})),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "meeting_notes": "notes"}, CTX)
    assert result["drafted"] is True
    assert "Gmail" in result["note"]


@pytest.mark.asyncio
async def test_falls_back_to_imap_when_google_not_connected(skill):
    from vula.google.service import GoogleNotConnected
    with (
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("m", "k", "b"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_body_response())),
        patch("vula.google.service.gmail_create_draft", new=AsyncMock(side_effect=GoogleNotConnected())),
        patch("vula.email_imap.credentials.get_email_creds", return_value={"email": "a@b.com"}),
        patch("vula.email_imap.service.save_draft",
              new=AsyncMock(return_value={"saved_to": "Drafts", "to": "client@example.com"})),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "meeting_notes": "notes"}, CTX)
    assert result["drafted"] is True
    assert "Drafts folder" in result["note"]


@pytest.mark.asyncio
async def test_falls_back_to_microsoft_when_google_not_connected(skill):
    from vula.google.service import GoogleNotConnected
    with (
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("m", "k", "b"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_body_response())),
        patch("vula.google.service.gmail_create_draft", new=AsyncMock(side_effect=GoogleNotConnected())),
        patch("vula.email_imap.credentials.get_email_creds", return_value=None),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.mail_create_draft",
              new=AsyncMock(return_value={"draft_id": "d1", "to": "client@example.com"})),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "meeting_notes": "notes"}, CTX)
    assert result["drafted"] is True
    assert "Outlook" in result["note"]


@pytest.mark.asyncio
async def test_clear_error_when_nothing_is_connected_at_all(skill):
    from vula.google.service import GoogleNotConnected
    with (
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("m", "k", "b"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_body_response())),
        patch("vula.google.service.gmail_create_draft", new=AsyncMock(side_effect=GoogleNotConnected())),
        patch("vula.email_imap.credentials.get_email_creds", return_value=None),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value=None)),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "meeting_notes": "notes"}, CTX)
    assert "error" in result
    assert "connect" in result["error"].lower()


@pytest.mark.asyncio
async def test_a_real_gmail_error_other_than_not_connected_still_surfaces(skill):
    """A GoogleNotConnected must fall through to the other backends, but a DIFFERENT Gmail
    failure (e.g. an API error while actually connected) must still be reported, not silently
    swallowed into a fallback attempt."""
    with (
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("m", "k", "b"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_body_response())),
        patch("vula.google.service.gmail_create_draft",
              new=AsyncMock(side_effect=RuntimeError("Gmail API quota exceeded"))),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "meeting_notes": "notes"}, CTX)
    assert "error" in result
    assert "quota" in result["error"]
