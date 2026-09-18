"""Tests confirming the ClickUp/OneDrive status endpoints surface the new sync-health columns
(migration 169) — go-live readiness pass, Phase 3.2. Before this, /status/{tenant_id} only ever
returned OAuth connection state, so a tenant whose background sync had been silently failing for
days still saw "Connected" with nothing to indicate the sync itself was broken."""
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_clickup_status_includes_sync_health_fields():
    from vula.api import clickup

    row = {"tenant_id": "digg-demo", "team_id": "t1", "list_ids": {"default": "l1"},
           "status": "connected", "connected_at": "2026-09-01T00:00:00Z",
           "last_synced_at": "2026-09-18T10:00:00Z", "last_sync_status": "error",
           "last_sync_error": "clickup API down"}
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[row])
    with patch("vula.api.clickup._client", return_value=mock_db):
        result = await clickup.status("digg-demo")

    assert result["last_sync_status"] == "error"
    assert result["last_sync_error"] == "clickup API down"
    select_cols = mock_db.table.return_value.select.call_args[0][0]
    assert "last_synced_at" in select_cols and "last_sync_status" in select_cols


@pytest.mark.asyncio
async def test_microsoft_status_includes_sync_health_fields():
    from vula.api import microsoft

    row = {"tenant_id": "digg-demo", "email": "owner@digg.co.za", "status": "connected",
           "connected_at": "2026-09-01T00:00:00Z", "last_synced_at": "2026-09-18T10:00:00Z",
           "last_sync_status": "ok", "last_sync_error": None}
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[row])
    with patch("vula.api.microsoft._client", return_value=mock_db):
        result = await microsoft.status("digg-demo")

    assert result["last_sync_status"] == "ok"
    select_cols = mock_db.table.return_value.select.call_args[0][0]
    assert "last_synced_at" in select_cols and "last_sync_status" in select_cols


@pytest.mark.asyncio
async def test_clickup_status_not_connected_when_no_row():
    from vula.api import clickup
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    with patch("vula.api.clickup._client", return_value=mock_db):
        result = await clickup.status("digg-demo")
    assert result == {"tenant_id": "digg-demo", "status": "not_connected"}
