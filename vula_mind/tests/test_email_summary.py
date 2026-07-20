"""Tests for the follow-up email skim-summary (vula/email_imap/sync.py:_summarize_followup,
_track_followup) — an AI read of tone/urgency plus a one-sentence summary, computed only for
emails that already passed _needs_reply (a small subset), stored on vula_email_followups
(migration 092) so the dashboard's Follow-ups list can show it instead of a raw preview."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.email_imap.sync import (
    _summarize_followup,
    _track_followup,
    backfill_followup_summaries,
)


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
        await _track_followup(mock_db, "digg-demo", "acct1", em, "schedule")

    mock_table.upsert.assert_called_once()
    row = mock_table.upsert.call_args[0][0]
    assert row["summary"] == "Wants a call this week."
    assert row["urgency"] == "normal"
    assert row["tone"] == "friendly"
    assert row["sender_name"] == "Jane Client"


@pytest.mark.asyncio
async def test_track_followup_pings_immediately_when_urgent():
    content = '{"summary": "Angry about a missed deadline.", "urgency": "high", "tone": "angry"}'
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    em = {"uid": 9, "from": "client@example.com", "subject": "Where is my order?!",
          "body": "This is now three days late, I need an answer today.", "when": "2026-07-20T00:00:00Z"}

    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm(content))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("test-model", "key", "base"))),
        patch("vula.integrations.notify.notify_team", new=AsyncMock(return_value=1)) as mock_notify,
    ):
        await _track_followup(mock_db, "off-the-hook", "acct1", em, "request")

    mock_notify.assert_awaited_once()
    args, _ = mock_notify.call_args
    assert args[0] == "off-the-hook"
    assert args[1] == "followup_digest"
    assert "🔥" in args[2]


@pytest.mark.asyncio
async def test_track_followup_normal_urgency_does_not_ping():
    content = '{"summary": "Routine question about pricing.", "urgency": "normal", "tone": "neutral"}'
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    em = {"uid": 10, "from": "client@example.com", "subject": "Pricing",
          "body": "What's the cost for a dozen?", "when": "2026-07-20T00:00:00Z"}

    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm(content))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("test-model", "key", "base"))),
        patch("vula.integrations.notify.notify_team", new=AsyncMock(return_value=1)) as mock_notify,
    ):
        await _track_followup(mock_db, "off-the-hook", "acct1", em, "request")

    mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_summaries_updates_null_rows():
    content = '{"summary": "Needs a quote for tiling.", "urgency": "normal", "tone": "polite"}'
    stuck_row = {"id": "f1", "subject": "Quote?", "preview": "Could you send a quote?",
                "sender": "client@example.com", "sender_name": "Client"}
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.is_.return_value \
        .limit.return_value.execute.return_value = MagicMock(data=[stuck_row])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with (
        patch("vula.email_imap.sync._client", return_value=mock_db),
        patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm(content))),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("test-model", "key", "base"))),
    ):
        n = await backfill_followup_summaries("digg-demo")

    assert n == 1
    mock_table.update.assert_called_once()
    updated = mock_table.update.call_args[0][0]
    assert updated["summary"] == "Needs a quote for tiling."


@pytest.mark.asyncio
async def test_backfill_summaries_returns_zero_when_nothing_stuck():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.is_.return_value \
        .limit.return_value.execute.return_value = MagicMock(data=[])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.email_imap.sync._client", return_value=mock_db):
        n = await backfill_followup_summaries("digg-demo")

    assert n == 0
    mock_table.update.assert_not_called()
