"""Regression test, 2026-08-26: commerce_expenses.receipt_url was always NULL — a scanned
receipt's expense claim is created BEFORE the storage upload (later in the same request)
produces a real durable URL, and nothing ever linked the two back together. Confirmed live
against real gerflor claims (4 real receipts, all receipt_url=None). _backfill_receipt_url()
closes that gap.
"""
from unittest.mock import MagicMock, patch

import pytest

import vula.api.whatsapp as wa

TID = "gerflor"


@pytest.mark.asyncio
async def test_backfill_updates_receipt_url_when_filed_row_has_one():
    mock_client = MagicMock()
    with patch(
        "vula.commerce.service._client", return_value=mock_client
    ):
        await wa._backfill_receipt_url(TID, "img:abc123", {"id": "doc1", "file_url": "https://example.com/x.jpg"})

    mock_client.table.assert_called_with("commerce_expenses")
    update_call = mock_client.table.return_value.update
    update_call.assert_called_once_with({"receipt_url": "https://example.com/x.jpg"})
    chain = update_call.return_value
    chain.eq.assert_any_call("tenant_id", TID)


@pytest.mark.asyncio
async def test_backfill_noop_when_filed_row_missing():
    mock_client = MagicMock()
    with patch(
        "vula.commerce.service._client", return_value=mock_client
    ):
        await wa._backfill_receipt_url(TID, "img:abc123", None)
    mock_client.table.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_noop_when_filed_row_has_no_url():
    mock_client = MagicMock()
    with patch(
        "vula.commerce.service._client", return_value=mock_client
    ):
        await wa._backfill_receipt_url(TID, "img:abc123", {"id": "doc1", "file_url": None})
    mock_client.table.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_never_raises_on_db_error():
    mock_client = MagicMock()
    mock_client.table.side_effect = RuntimeError("db down")
    with patch(
        "vula.commerce.service._client", return_value=mock_client
    ):
        await wa._backfill_receipt_url(TID, "img:abc123", {"file_url": "https://example.com/x.jpg"})
    # no exception raised — best-effort
