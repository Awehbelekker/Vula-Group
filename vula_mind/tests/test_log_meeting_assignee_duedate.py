"""Tests for _log_meeting's 2026-08-18 extension: per-action-item assignee + due-date inference,
added after discussing what happens when a rep sits in a meeting with two or more people —
action items previously had no "who owns this" or "when is it due" even when the transcript
stated both explicitly. Due dates are resolved deterministically (dateutil), never computed by
the LLM itself (a known weak spot) — the model only ever lifts the raw phrase verbatim.
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.skills.commerce_admin as ca
from core.skills.commerce_admin import CommerceAdminSkill

TID = "digg-demo"
CTX = {"tenant_id": TID, "phone": "27821234567", "caller_name": "Judy", "caller_role": None}


def _llm_response(payload: dict):
    import json
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    return resp


class _CapturingClient:
    """No contact match; captures every insert() call's rows for inspection."""
    def __init__(self):
        self.inserted_rows = []

    def table(self, *a): return self
    def select(self, *a): return self
    def eq(self, *a): return self
    def ilike(self, *a): return self
    def limit(self, *a): return self

    def insert(self, rows):
        self.inserted_rows.extend(rows if isinstance(rows, list) else [rows])
        return self

    def execute(self): return SimpleNamespace(data=[])


@pytest.fixture
def skill():
    return CommerceAdminSkill()


async def _run(skill, extraction, notes="Meeting notes here.", client=None):
    client = client or _CapturingClient()
    with (
        patch.object(ca.service, "_client", return_value=client),
        patch.object(ca, "resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=AsyncMock(return_value=_llm_response(extraction))),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value={"id": "doc-1"})),
        patch("core.skills.draft_admin.draft_letter", new=AsyncMock(return_value={"sent_via_whatsapp": True})),
    ):
        result = await skill._log_meeting(TID, {"notes": notes}, CTX)
    return result, client


@pytest.mark.asyncio
async def test_assignee_folded_into_reminder_text(skill):
    extraction = {"summary": "Discussed scope.", "attendees": ["Judy", "Peter"],
                  "action_items": [{"text": "send the drawings", "assignee": "Peter",
                                    "due_phrase": None}],
                  "next_meeting_hint": None}
    result, client = await _run(skill, extraction)

    assert result["action_items"] == ["Peter: send the drawings"]
    assert client.inserted_rows[0]["text"] == "Peter: send the drawings"


@pytest.mark.asyncio
async def test_clear_weekday_due_phrase_sets_due_at(skill):
    extraction = {"summary": "Discussed scope.", "attendees": ["Peter"],
                  "action_items": [{"text": "send the drawings", "assignee": "Peter",
                                    "due_phrase": "Friday"}],
                  "next_meeting_hint": None}
    result, client = await _run(skill, extraction)

    assert result["action_items"] == ["Peter: send the drawings (Friday)"]
    due_at = client.inserted_rows[0]["due_at"]
    assert due_at is not None
    parsed = datetime.fromisoformat(due_at)
    assert parsed.weekday() == 4  # Friday


@pytest.mark.asyncio
async def test_vague_due_phrase_leaves_due_at_null(skill):
    """'soon' has no real date signal — better an undated-but-visible reminder than a guess."""
    extraction = {"summary": "Discussed scope.", "attendees": [],
                  "action_items": [{"text": "follow up", "assignee": None, "due_phrase": "soon"}],
                  "next_meeting_hint": None}
    result, client = await _run(skill, extraction)

    assert result["action_items"] == ["follow up (soon)"]
    assert client.inserted_rows[0]["due_at"] is None


@pytest.mark.asyncio
async def test_no_assignee_or_due_phrase_unchanged_from_before(skill):
    extraction = {"summary": "Site visit.", "attendees": [],
                  "action_items": [{"text": "check on materials", "assignee": None, "due_phrase": None}],
                  "next_meeting_hint": None}
    result, client = await _run(skill, extraction)

    assert result["action_items"] == ["check on materials"]
    assert client.inserted_rows[0]["text"] == "check on materials"
    assert client.inserted_rows[0]["due_at"] is None


@pytest.mark.asyncio
async def test_multiple_action_items_different_assignees():
    """The actual multi-person-meeting scenario this was built for."""
    skill = CommerceAdminSkill()
    extraction = {
        "summary": "Site meeting with two client reps.",
        "attendees": ["Judy", "Peter", "Sarah"],
        "action_items": [
            {"text": "send the drawings", "assignee": "Peter", "due_phrase": "Friday"},
            {"text": "confirm the budget", "assignee": "Sarah", "due_phrase": None},
            {"text": "follow up with the contractor", "assignee": None, "due_phrase": "next week"},
        ],
        "next_meeting_hint": None,
    }
    result, client = await _run(skill, extraction)

    assert result["action_items"] == [
        "Peter: send the drawings (Friday)",
        "Sarah: confirm the budget",
        "follow up with the contractor (next week)",
    ]
    assert len(client.inserted_rows) == 3
    assert client.inserted_rows[0]["due_at"] is not None   # Friday — real signal
    assert client.inserted_rows[1]["due_at"] is None       # no due phrase at all
    assert client.inserted_rows[2]["due_at"] is None       # "next week" — no specific day


@pytest.mark.asyncio
async def test_plain_string_action_items_still_tolerated(skill):
    """Backward compatibility: the model occasionally still returns plain strings."""
    extraction = {"summary": "Quick call.", "attendees": [],
                  "action_items": ["send the quote"], "next_meeting_hint": None}
    result, client = await _run(skill, extraction)

    assert result["action_items"] == ["send the quote"]
    assert client.inserted_rows[0]["text"] == "send the quote"
    assert client.inserted_rows[0]["due_at"] is None
