"""core/skills/commerce_admin.py::draft_followup_email + vula/commerce/mail_router.py's new
create_tenant_draft (2026-09-15).

Two independent changes, both in this tool:

1. It was hardcoded to Gmail only. A tenant with a working IMAP or Microsoft mailbox connected
   instead (confirmed the more common case, see mail_router.py's own docstring) got a dead-end
   "connect Google" error despite already having mail connected. Both backend capabilities
   already existed independently and were already proven in production by their own skills
   (email_imap.service.save_draft, used by email_admin.py; microsoft.service.mail_create_draft,
   used by microsoft_admin.py) — neither had ever been reached from a tenant-agnostic "whichever
   mailbox they actually have" caller, the role mail_router.send_tenant_email already plays for
   sending. create_tenant_draft is that draft-side twin.

2. It's now two calls, not one: the first proposes three tone options (formal/warm/brief) and
   saves nothing; the second — once the rep has picked one or asked for a tweak — saves EXACTLY
   the confirmed text. The finalize step deliberately never regenerates (see
   _draft_followup_email's own docstring for why that matters), so _save_followup_draft (the
   actual backend-fallback logic) is tested directly against fixed subject/body, independent of
   the tone-options generation step.
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


# ── draft_followup_email — step 1: propose tone options ─────────────────────────────────

@pytest.fixture
def skill():
    return CommerceAdminSkill()


def _tone_json_response():
    import json
    payload = {
        "formal": {"subject": "Following up on our meeting", "body": "Dear Client, ..."},
        "warm": {"subject": "Great chatting today!", "body": "Hi there, thanks so much..."},
        "brief": {"subject": "Next steps", "body": "Thanks for today. Next: ..."},
    }
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    return resp


@pytest.mark.asyncio
async def test_missing_recipient_errors_before_generating_anything(skill):
    result = await skill._draft_followup_email(TID, {"meeting_notes": "notes"}, CTX)
    assert "error" in result


@pytest.mark.asyncio
async def test_missing_notes_and_no_chosen_text_errors(skill):
    result = await skill._draft_followup_email(TID, {"to_email": "client@example.com"}, CTX)
    assert "error" in result


@pytest.mark.asyncio
async def test_step1_returns_three_tone_options_and_saves_nothing(skill):
    save_mock = AsyncMock()
    with (
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("m", "k", "b"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_tone_json_response())),
        patch.object(skill, "_save_followup_draft", save_mock),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "meeting_notes": "Discussed the new order."}, CTX)

    assert "drafted" not in result
    assert set(result["tone_options"]) == {"formal", "warm", "brief"}
    assert result["tone_options"]["warm"]["subject"] == "Great chatting today!"
    assert "instruction_to_assistant" in result
    save_mock.assert_not_called()


@pytest.mark.asyncio
async def test_step1_uses_subject_hint_when_the_model_omits_one(skill):
    import json
    payload = {"formal": {"subject": "", "body": "Dear Client, ..."}}
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    with (
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("m", "k", "b"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=resp)),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "meeting_notes": "notes",
                 "subject": "Re: pricing"}, CTX)
    assert result["tone_options"]["formal"]["subject"] == "Re: pricing"


@pytest.mark.asyncio
async def test_step1_fails_open_with_a_clear_error_when_generation_breaks(skill):
    with (
        patch.object(ca, "resolve_generation_route", new=AsyncMock(side_effect=RuntimeError("router down"))),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "meeting_notes": "notes"}, CTX)
    assert "error" in result


# ── draft_followup_email — step 2: finalize exactly what was confirmed ──────────────────

@pytest.mark.asyncio
async def test_step2_saves_the_exact_chosen_text_via_gmail(skill):
    with patch("vula.google.service.gmail_create_draft", new=AsyncMock(return_value={"id": "g1"})):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "chosen_subject": "Great chatting today!",
                 "chosen_body": "Hi there, thanks so much..."}, CTX)
    assert result["drafted"] is True
    assert "Gmail" in result["note"]


@pytest.mark.asyncio
async def test_step2_never_calls_the_llm(skill):
    """The whole point of splitting propose/finalize — no regeneration, ever, on step 2."""
    with (
        patch("vula.google.service.gmail_create_draft", new=AsyncMock(return_value={"id": "g1"})),
        patch("litellm.acompletion", new=AsyncMock(side_effect=AssertionError("must not be called"))),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "chosen_subject": "s",
                 "chosen_body": "exact confirmed text"}, CTX)
    assert result["drafted"] is True


@pytest.mark.asyncio
async def test_step2_falls_back_to_imap_when_google_not_connected(skill):
    from vula.google.service import GoogleNotConnected
    with (
        patch("vula.google.service.gmail_create_draft", new=AsyncMock(side_effect=GoogleNotConnected())),
        patch("vula.email_imap.credentials.get_email_creds", return_value={"email": "a@b.com"}),
        patch("vula.email_imap.service.save_draft",
              new=AsyncMock(return_value={"saved_to": "Drafts", "to": "client@example.com"})),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "chosen_subject": "s",
                 "chosen_body": "confirmed text"}, CTX)
    assert result["drafted"] is True
    assert "Drafts folder" in result["note"]


@pytest.mark.asyncio
async def test_step2_falls_back_to_microsoft_when_google_not_connected(skill):
    from vula.google.service import GoogleNotConnected
    with (
        patch("vula.google.service.gmail_create_draft", new=AsyncMock(side_effect=GoogleNotConnected())),
        patch("vula.email_imap.credentials.get_email_creds", return_value=None),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value="tok")),
        patch("vula.microsoft.service.mail_create_draft",
              new=AsyncMock(return_value={"draft_id": "d1", "to": "client@example.com"})),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "chosen_subject": "s",
                 "chosen_body": "confirmed text"}, CTX)
    assert result["drafted"] is True
    assert "Outlook" in result["note"]


@pytest.mark.asyncio
async def test_step2_clear_error_when_nothing_is_connected_at_all(skill):
    from vula.google.service import GoogleNotConnected
    with (
        patch("vula.google.service.gmail_create_draft", new=AsyncMock(side_effect=GoogleNotConnected())),
        patch("vula.email_imap.credentials.get_email_creds", return_value=None),
        patch("vula.microsoft.credentials.get_access_token", new=AsyncMock(return_value=None)),
    ):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "chosen_subject": "s",
                 "chosen_body": "confirmed text"}, CTX)
    assert "error" in result
    assert "connect" in result["error"].lower()


@pytest.mark.asyncio
async def test_step2_a_real_gmail_error_other_than_not_connected_still_surfaces(skill):
    """A GoogleNotConnected must fall through to the other backends, but a DIFFERENT Gmail
    failure (e.g. an API error while actually connected) must still be reported, not silently
    swallowed into a fallback attempt."""
    with patch("vula.google.service.gmail_create_draft",
              new=AsyncMock(side_effect=RuntimeError("Gmail API quota exceeded"))):
        result = await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "chosen_subject": "s",
                 "chosen_body": "confirmed text"}, CTX)
    assert "error" in result
    assert "quota" in result["error"]


@pytest.mark.asyncio
async def test_step2_defaults_subject_when_none_given(skill):
    with patch("vula.google.service.gmail_create_draft", new=AsyncMock(return_value={"id": "g1"})) as m:
        await skill._draft_followup_email(
            TID, {"to_email": "client@example.com", "chosen_body": "confirmed text"}, CTX)
    assert m.call_args[0][2]  # a non-empty subject was passed through
