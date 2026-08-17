"""Tests for _log_meeting's 2026-08-17 extension: it now also renders and sends a PDF meeting
summary (reusing draft_letter's existing generation/render/send pipeline as-is) and surfaces an
explicit next-meeting mention as a note for the model to relay — never auto-booking it."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill

TID = "digg-demo"
CTX = {"tenant_id": TID, "phone": "27821234567", "caller_name": "Judy", "caller_role": None}


def _llm_response(payload: dict):
    resp = MagicMock()
    import json
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    return resp


class _NoContactClient:
    """No contact match — commerce_contacts lookup returns nothing."""
    def table(self, *a): return self
    def select(self, *a): return self
    def eq(self, *a): return self
    def ilike(self, *a): return self
    def limit(self, *a): return self
    def insert(self, *a): return self
    def execute(self): return SimpleNamespace(data=[])


@pytest.fixture
def skill():
    return CommerceAdminSkill()


@pytest.mark.asyncio
async def test_log_meeting_sends_pdf_summary(skill):
    extraction = {"summary": "Discussed office door film installation.",
                  "attendees": ["Judy", "Solucent rep"],
                  "action_items": ["Confirm 50% deposit"], "next_meeting_hint": None}
    filed_row = {"id": "doc-1"}
    pdf_result = {"sent_via_whatsapp": True, "draft_id": "d1"}

    with (
        patch.object(ca.service, "_client", return_value=_NoContactClient()),
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(extraction))),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value=filed_row)),
        patch("core.skills.draft_admin.draft_letter", new=AsyncMock(return_value=pdf_result)) as mock_draft,
    ):
        result = await skill._log_meeting(TID, {"notes": "We discussed the film installation..."}, CTX)

    assert result["logged"] is True
    assert result["pdf_sent"] is True
    mock_draft.assert_awaited_once()
    call_args = mock_draft.call_args.args
    assert call_args[0]["document_type"] == "site_meeting_minutes"
    assert "Confirm 50% deposit" in call_args[0]["brief"]
    assert call_args[1] == TID
    assert call_args[2] == "27821234567"


@pytest.mark.asyncio
async def test_log_meeting_pdf_failure_does_not_block_log(skill):
    """PDF generation is best-effort — a failure there must not undo the already-successful
    filing + reminders."""
    extraction = {"summary": "Site visit notes.", "attendees": [], "action_items": [],
                  "next_meeting_hint": None}
    filed_row = {"id": "doc-1"}

    with (
        patch.object(ca.service, "_client", return_value=_NoContactClient()),
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(extraction))),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value=filed_row)),
        patch("core.skills.draft_admin.draft_letter", new=AsyncMock(side_effect=RuntimeError("render failed"))),
    ):
        result = await skill._log_meeting(TID, {"notes": "Site visit went well."}, CTX)

    assert result["logged"] is True
    assert result["pdf_sent"] is False


@pytest.mark.asyncio
async def test_log_meeting_surfaces_next_meeting_hint_as_note():
    skill = CommerceAdminSkill()
    extraction = {"summary": "Discussed scope.", "attendees": ["Judy"], "action_items": [],
                  "next_meeting_hint": "same time next Tuesday"}
    filed_row = {"id": "doc-1"}
    pdf_result = {"sent_via_whatsapp": True}

    with (
        patch.object(ca.service, "_client", return_value=_NoContactClient()),
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(extraction))),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value=filed_row)),
        patch("core.skills.draft_admin.draft_letter", new=AsyncMock(return_value=pdf_result)),
    ):
        result = await skill._log_meeting(TID, {"notes": "Let's meet again same time next Tuesday."}, CTX)

    assert result["next_meeting_hint"] == "same time next Tuesday"
    assert "note" in result and "next Tuesday" in result["note"]
    assert "create_booking" in result["note"]


@pytest.mark.asyncio
async def test_log_meeting_no_next_meeting_hint_omits_note():
    skill = CommerceAdminSkill()
    extraction = {"summary": "Discussed scope.", "attendees": [], "action_items": [],
                  "next_meeting_hint": None}
    filed_row = {"id": "doc-1"}

    with (
        patch.object(ca.service, "_client", return_value=_NoContactClient()),
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(extraction))),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value=filed_row)),
        patch("core.skills.draft_admin.draft_letter", new=AsyncMock(return_value={"sent_via_whatsapp": True})),
    ):
        result = await skill._log_meeting(TID, {"notes": "Discussed scope only."}, CTX)

    assert "next_meeting_hint" not in result
    assert "note" not in result


@pytest.mark.asyncio
async def test_log_meeting_passes_contact_name_to_pdf():
    skill = CommerceAdminSkill()
    extraction = {"summary": "Client update.", "attendees": [], "action_items": [],
                  "next_meeting_hint": None}
    filed_row = {"id": "doc-1"}

    class _WithContactClient:
        def table(self, *a): return self
        def select(self, *a): return self
        def eq(self, *a): return self
        def ilike(self, *a): return self
        def limit(self, *a): return self
        def execute(self): return SimpleNamespace(data=[{"phone": "27829999999", "name": "Solucent (Pty) Ltd"}])

    with (
        patch.object(ca.service, "_client", return_value=_WithContactClient()),
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(extraction))),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value=filed_row)),
        patch("core.skills.draft_admin.draft_letter", new=AsyncMock(return_value={"sent_via_whatsapp": True})) as mock_draft,
    ):
        await skill._log_meeting(TID, {"notes": "Client update.", "contact_name_or_phone": "Solucent"}, CTX)

    assert mock_draft.call_args.args[0]["client_name"] == "Solucent (Pty) Ltd"
