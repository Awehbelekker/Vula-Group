"""vula/kb_maintenance.learning_weekly — the knowledge-question report, once a week, one line."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from vula import kb_maintenance as km

NOW = datetime(2026, 10, 25, 8, tzinfo=timezone.utc)
REP = {"tenants": {"off-the-hook": {"questions": 4, "answered": 3, "asked_which": 1, "picked": 1,
                                    "nothing_found": 0, "said_wrong": 1, "said_right": 0}}}


@pytest.mark.asyncio
async def test_not_due_within_a_week():
    with patch.object(km, "_last_done", return_value=NOW - timedelta(days=3)), \
         patch.object(km, "run_job", new=AsyncMock()) as run:
        assert await km.learning_weekly(NOW) is None
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_due_runs_and_sends_one_line():
    with patch.object(km, "_last_done", return_value=NOW - timedelta(days=8)), \
         patch.object(km, "run_job", new=AsyncMock(return_value=REP)), \
         patch("vula.integrations.platform_support.alert_operator", new=AsyncMock(return_value=True)) as alert:
        assert await km.learning_weekly(NOW) == REP
    text = alert.await_args.args[0]
    assert "off-the-hook: 4 questions, 3 answered (1 called wrong)" in text and "\n" not in text


@pytest.mark.asyncio
async def test_an_empty_week_sends_nothing():
    with patch.object(km, "_last_done", return_value=None), \
         patch.object(km, "run_job", new=AsyncMock(return_value={"tenants": {}})), \
         patch("vula.integrations.platform_support.alert_operator", new=AsyncMock()) as alert:
        await km.learning_weekly(NOW)
    alert.assert_not_awaited()
