"""Tests for the ClickUp service's single-task fetch (vula/clickup/service.py:get_task)
and the webhook's procurement dispatch path (vula/api/clickup.py) — the plumbing that
lets a native ClickUp task (no field-ops mirror link) reach the procurement completion
hook. See tests/test_procurement.py for the completion-hook logic itself."""
import hashlib
import hmac
import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 2026-09-01: the webhook now requires a valid ClickUp signature (X-Signature = HMAC-SHA256 of
# the raw body) before it will touch any state — it was previously wide open to anonymous POSTs
# despite updating field-ops status and triggering procurement. These tests exercise what
# happens AFTER authentication, so they now sign their payloads like ClickUp does. The
# rejection paths themselves are covered in tests/test_clickup_webhook_security.py.
_SECRET = "test-webhook-secret"


def _signed_request(payload: dict):
    raw = _json.dumps(payload).encode()
    request = MagicMock()
    request.body = AsyncMock(return_value=raw)
    request.json = AsyncMock(return_value=payload)
    request.headers = {"X-Signature": hmac.new(_SECRET.encode(), raw, hashlib.sha256).hexdigest()}
    return request


def _mock_httpx_get(json_data=None, status_code=200):
    resp = MagicMock(status_code=status_code)
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


@pytest.mark.asyncio
async def test_get_task_returns_none_on_404():
    from vula.clickup.service import get_task
    with (
        patch("vula.clickup.service._creds_or_raise", return_value={"token": "tok"}),
        patch("httpx.AsyncClient", _mock_httpx_get(status_code=404)),
    ):
        assert await get_task("digg-demo", "t404") is None


@pytest.mark.asyncio
async def test_get_task_parses_status_list_and_tags():
    from vula.clickup.service import get_task
    payload = {
        "id": "t1", "name": "Order tiles", "description": "for the kitchen",
        "status": {"status": "Complete"}, "list": {"id": "L1"},
        "tags": [{"name": "Procurement"}], "assignees": [{"username": "Hloni"}],
    }
    with (
        patch("vula.clickup.service._creds_or_raise", return_value={"token": "tok"}),
        patch("httpx.AsyncClient", _mock_httpx_get(json_data=payload)),
    ):
        task = await get_task("digg-demo", "t1")

    assert task["status"] == "complete"
    assert task["list_id"] == "L1"
    assert task["tags"] == ["procurement"]
    assert task["assignees"] == ["Hloni"]


@pytest.mark.asyncio
async def test_webhook_routes_unmapped_task_to_procurement():
    from vula.api.clickup import webhook

    request = _signed_request({
        "task_id": "t1",
        "history_items": [{"after": {"status": "complete"}}],
    })

    task = {"id": "t1", "title": "Order tiles", "status": "complete", "tags": ["procurement"], "list_id": "L1"}

    with (
        patch("vula.integrations.clickup_sync.field_task_for_clickup", return_value=None),
        patch("vula.api.clickup._any_stored_webhook_secret",
              return_value=[("digg-demo", _SECRET)]),
        patch("vula.clickup.service.get_task", new=AsyncMock(return_value=task)),
        patch("vula.integrations.procurement.handle_task_status_change",
              new=AsyncMock(return_value={"id": "row1"})) as mock_handle,
    ):
        result = await webhook(request)

    assert result == {"status": "ok", "tenant_id": "digg-demo", "procurement_logged": True}
    mock_handle.assert_awaited_once_with("digg-demo", task)


@pytest.mark.asyncio
async def test_webhook_still_prefers_field_ops_mapped_task():
    from vula.api.clickup import webhook

    request = _signed_request({
        "task_id": "t2",
        "history_items": [{"after": {"status": "complete"}}],
    })

    with (
        patch("vula.integrations.clickup_sync.field_task_for_clickup",
              return_value={"tenant_id": "off-the-hook", "field_task_id": "f1"}),
        patch("vula.api.clickup._any_stored_webhook_secret",
              return_value=[("off-the-hook", _SECRET)]),
        patch("vula.models.field_ops.get_field_ops_db") as mock_db,
    ):
        result = await webhook(request)

    mock_db.return_value.update_task_status.assert_called_once_with("f1", "complete")
    assert result == {"status": "ok", "field_task": "f1", "new_status": "complete"}
