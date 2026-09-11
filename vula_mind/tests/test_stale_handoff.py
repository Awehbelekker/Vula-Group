"""Human handoff (an owner pausing the bot on a WhatsApp thread) had no expiry — if the owner
got distracted, the customer could be left permanently ghosted with no signal to anyone
(2026-09-11, pre-go-live brief item #5). Covers find_stale_paused_sessions, the data layer
the scheduler loop (server.py::_stale_handoff_scheduler_loop) uses to decide who to notify +
auto-resume — job_config registration is covered in test_job_config.py, and the scheduler
LOOP itself isn't unit-tested (infinite loop, real sleeps), matching this codebase's existing
convention for its other background loops (see test_job_config.py's own docstring)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from vula.commerce import service as commerce_service


def _mock_db_returning(rows):
    mock_table = MagicMock()
    chain = mock_table.select.return_value.eq.return_value.eq.return_value.lt.return_value
    chain.limit.return_value.execute.return_value = MagicMock(data=rows)
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    return mock_db


@pytest.mark.asyncio
async def test_find_stale_paused_sessions_returns_rows():
    row = {"id": "s1", "tenant_id": "off-the-hook", "paused": True,
          "customer_phone": "27821234567", "customer_name": "Jane",
          "last_at": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()}
    with patch("vula.commerce.service._client", return_value=_mock_db_returning([row])):
        rows = await commerce_service.find_stale_paused_sessions("off-the-hook")
    assert rows == [row]


@pytest.mark.asyncio
async def test_find_stale_paused_sessions_returns_empty_on_db_error():
    mock_db = MagicMock()
    mock_db.table.side_effect = RuntimeError("db down")
    with patch("vula.commerce.service._client", return_value=mock_db):
        assert await commerce_service.find_stale_paused_sessions("off-the-hook") == []


@pytest.mark.asyncio
async def test_find_stale_paused_sessions_accepts_a_custom_threshold():
    # Confirms the call succeeds with a non-default threshold — the actual cutoff filtering
    # happens DB-side (`.lt("last_at", cutoff)`), which this mock doesn't simulate.
    with patch("vula.commerce.service._client", return_value=_mock_db_returning([])):
        rows = await commerce_service.find_stale_paused_sessions("off-the-hook", hours=0.5)
    assert rows == []
