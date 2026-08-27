"""Tests for _log_meeting's 2026-08-27 extension: any photos this rep sent recently (site
photos, building signage, etc.) are automatically embedded into the same PDF as real ![](url)
markdown, so a site visit can be "photos then voice note" or "voice note then photos" in any
order — the rep never has to separately attach anything. Each picked-up photo is marked
fields.attached_to_meeting so a LATER, unrelated meeting log never re-attaches it."""
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


class _PhotoClient:
    """Supports the contact lookup (no match), the visit-photo select chain, and the
    mark-attached update chain used by _log_meeting."""

    def __init__(self, photo_rows):
        self._photo_rows = photo_rows
        self.updates = []

    def table(self, name):
        self._table = name
        return self

    def select(self, *a):
        return self

    def eq(self, *a):
        return self

    def ilike(self, *a):
        return self

    def limit(self, *a):
        return self

    def gte(self, *a):
        return self

    def order(self, *a):
        return self

    def update(self, body):
        self._pending_update = body
        return self

    def execute(self):
        if getattr(self, "_pending_update", None) is not None:
            self.updates.append(dict(self._pending_update))
            self._pending_update = None
            return SimpleNamespace(data=[{"id": "ok"}])
        if self._table == "vula_filed_documents":
            return SimpleNamespace(data=self._photo_rows)
        return SimpleNamespace(data=[])


@pytest.fixture
def skill():
    return CommerceAdminSkill()


@pytest.mark.asyncio
async def test_recent_photo_embedded_and_marked_attached(skill):
    extraction = {"summary": "Site visit to the Peters building.", "attendees": [],
                  "action_items": [], "next_meeting_hint": None}
    filed_row = {"id": "meeting-1"}
    photo_rows = [{"id": "photo-1", "file_url": "https://storage/photo1.jpg",
                  "mime": "image/jpeg", "fields": {}}]
    client = _PhotoClient(photo_rows)

    with (
        patch.object(ca.service, "_client", return_value=client),
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(extraction))),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value=filed_row)),
        patch("core.skills.draft_admin.draft_letter", new=AsyncMock(return_value={"sent_via_whatsapp": True})) as mock_draft,
    ):
        result = await skill._log_meeting(TID, {"notes": "Visited the Peters building today."}, CTX)

    assert result["photos_attached"] == 1
    kwargs = mock_draft.call_args.kwargs
    assert "photo1.jpg" in kwargs["extra_markdown"]
    assert "![Photo]" in kwargs["extra_markdown"]
    # marked attached against the real filed meeting-note id, not left for a later log to reuse
    assert client.updates == [{"fields": {"attached_to_meeting": "meeting-1"}}]


@pytest.mark.asyncio
async def test_no_recent_photos_omits_photos_attached_and_passes_no_extra_markdown(skill):
    extraction = {"summary": "Quick call.", "attendees": [], "action_items": [],
                  "next_meeting_hint": None}
    filed_row = {"id": "meeting-2"}
    client = _PhotoClient([])

    with (
        patch.object(ca.service, "_client", return_value=client),
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(extraction))),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value=filed_row)),
        patch("core.skills.draft_admin.draft_letter", new=AsyncMock(return_value={"sent_via_whatsapp": True})) as mock_draft,
    ):
        result = await skill._log_meeting(TID, {"notes": "Quick phone call, no site visit."}, CTX)

    assert "photos_attached" not in result
    assert mock_draft.call_args.kwargs["extra_markdown"] is None
    assert client.updates == []


@pytest.mark.asyncio
async def test_already_attached_photo_is_not_picked_up_again(skill):
    """A photo already folded into an earlier meeting log (fields.attached_to_meeting set)
    must never be re-attached to a later, unrelated one."""
    extraction = {"summary": "Follow-up call.", "attendees": [], "action_items": [],
                  "next_meeting_hint": None}
    filed_row = {"id": "meeting-3"}
    # The select() itself doesn't filter server-side in this fake client — the real filtering
    # happens in Python against fields.attached_to_meeting, exactly as _log_meeting does it.
    photo_rows = [{"id": "photo-1", "file_url": "https://storage/photo1.jpg",
                  "mime": "image/jpeg", "fields": {"attached_to_meeting": "meeting-1"}}]
    client = _PhotoClient(photo_rows)

    with (
        patch.object(ca.service, "_client", return_value=client),
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(extraction))),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value=filed_row)),
        patch("core.skills.draft_admin.draft_letter", new=AsyncMock(return_value={"sent_via_whatsapp": True})) as mock_draft,
    ):
        result = await skill._log_meeting(TID, {"notes": "Following up on that call."}, CTX)

    assert "photos_attached" not in result
    assert mock_draft.call_args.kwargs["extra_markdown"] is None
    assert client.updates == []


@pytest.mark.asyncio
async def test_non_image_document_is_not_picked_up_as_a_visit_photo(skill):
    extraction = {"summary": "Meeting.", "attendees": [], "action_items": [],
                  "next_meeting_hint": None}
    filed_row = {"id": "meeting-4"}
    photo_rows = [{"id": "doc-1", "file_url": "https://storage/quote.pdf",
                  "mime": "application/pdf", "fields": {}}]
    client = _PhotoClient(photo_rows)

    with (
        patch.object(ca.service, "_client", return_value=client),
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(extraction))),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value=filed_row)),
        patch("core.skills.draft_admin.draft_letter", new=AsyncMock(return_value={"sent_via_whatsapp": True})) as mock_draft,
    ):
        result = await skill._log_meeting(TID, {"notes": "Discussed the quote."}, CTX)

    assert "photos_attached" not in result
    assert mock_draft.call_args.kwargs["extra_markdown"] is None


@pytest.mark.asyncio
async def test_multiple_photos_all_embedded_and_all_marked(skill):
    extraction = {"summary": "Full site survey.", "attendees": [], "action_items": [],
                  "next_meeting_hint": None}
    filed_row = {"id": "meeting-5"}
    photo_rows = [
        {"id": "p1", "file_url": "https://storage/front.jpg", "mime": "image/jpeg", "fields": {}},
        {"id": "p2", "file_url": "https://storage/back.jpg", "mime": "image/jpeg", "fields": {}},
        {"id": "p3", "file_url": "https://storage/roof.jpg", "mime": "image/jpeg", "fields": {}},
    ]
    client = _PhotoClient(photo_rows)

    with (
        patch.object(ca.service, "_client", return_value=client),
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(extraction))),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value=filed_row)),
        patch("core.skills.draft_admin.draft_letter", new=AsyncMock(return_value={"sent_via_whatsapp": True})) as mock_draft,
    ):
        result = await skill._log_meeting(TID, {"notes": "Front, back, and roof survey."}, CTX)

    assert result["photos_attached"] == 3
    md = mock_draft.call_args.kwargs["extra_markdown"]
    assert "front.jpg" in md and "back.jpg" in md and "roof.jpg" in md
    assert len(client.updates) == 3
    assert all(u["fields"]["attached_to_meeting"] == "meeting-5" for u in client.updates)
