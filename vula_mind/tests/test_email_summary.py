"""Tests for the follow-up email skim-summary (vula/email_imap/sync.py:_summarize_followup,
_track_followup) — an AI read of tone/urgency plus a one-sentence summary, computed only for
emails that already passed _needs_reply (a small subset), stored on vula_email_followups
(migration 092) so the dashboard's Follow-ups list can show it instead of a raw preview."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.email_imap.sync import _summarize_followup, _track_followup


def _mock_llm(content: str):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    return resp


@pytest.mark.asyncio
async def test_summarize_followup_parses_json_response():
    content = ('{"summary": "Client asks for a quote on the tiling job.", '
              '"urgency": "high", "tone": "frustrated"}')
    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm(content))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("test-model", "key", "base"))),
    ):
        result = await _summarize_followup("Quote?", "Please send me a quote urgently.")

    assert result["summary"] == "Client asks for a quote on the tiling job."
    assert result["urgency"] == "high"
    assert result["tone"] == "frustrated"


@pytest.mark.asyncio
async def test_summarize_followup_invalid_urgency_defaults_to_normal():
    content = '{"summary": "Asking about timelines.", "urgency": "critical!!", "tone": "neutral"}'
    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm(content))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("test-model", "key", "base"))),
    ):
        result = await _summarize_followup("Timelines", "When will this be done?")

    assert result["urgency"] == "normal"


@pytest.mark.asyncio
async def test_summarize_followup_returns_empty_on_llm_failure():
    with (
        patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("down"))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("test-model", "key", "base"))),
    ):
        result = await _summarize_followup("Subject", "Body")

    assert result == {}


@pytest.mark.asyncio
async def test_summarize_followup_empty_text_short_circuits():
    with patch("litellm.acompletion", new=AsyncMock()) as mock_call:
        result = await _summarize_followup("", "")
    assert result == {}
    mock_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_track_followup_upserts_summary_fields():
    content = '{"summary": "Wants a call this week.", "urgency": "normal", "tone": "friendly"}'
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    em = {"uid": 5, "from": '"Jane Client" <jane@example.com>', "subject": "Call?",
          "body": "Could we set up a call this week?", "when": "2026-07-20T00:00:00Z"}

    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm(content))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("test-model", "key", "base"))),
    ):
        await _track_followup(mock_db, "digg-demo", em, "schedule")

    mock_table.upsert.assert_called_once()
    row = mock_table.upsert.call_args[0][0]
    assert row["summary"] == "Wants a call this week."
    assert row["urgency"] == "normal"
    assert row["tone"] == "friendly"
    assert row["sender_name"] == "Jane Client"
