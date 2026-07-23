"""Tests for broadcast->order revenue attribution — the gap flagged in the marketing
capability audit: the click-tracking funnel (vula/api/links.py, migration 064) stopped at
"clicked", with nothing connecting a click to a resulting order, so campaign ROI couldn't
be computed. Covers vula/commerce/service.py:_attribute_broadcast (last-click, 7-day
window), create_order() wiring it into the inserted row, and the funnel endpoint's
attributed-revenue rollup (vula/api/commerce.py:admin_broadcast_recipients)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.commerce import admin_broadcast_recipients
from vula.commerce.service import _attribute_broadcast, create_order


@pytest.mark.asyncio
async def test_attribute_broadcast_returns_most_recent_click():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.gte.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{"broadcast_id": "b1", "clicked_at": "2026-07-20T00:00:00Z"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.commerce.service._client", return_value=mock_db):
        result = await _attribute_broadcast("off-the-hook", "0821234567")

    assert result == "b1"
    # phone normalised (0xx -> 27xx) before the lookup
    mock_table.select.return_value.eq.assert_any_call("tenant_id", "off-the-hook")


@pytest.mark.asyncio
async def test_attribute_broadcast_returns_none_when_no_recent_click():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.gte.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.commerce.service._client", return_value=mock_db):
        result = await _attribute_broadcast("off-the-hook", "0821234567")

    assert result is None


@pytest.mark.asyncio
async def test_attribute_broadcast_no_phone_short_circuits():
    with patch("vula.commerce.service._client") as mock_client:
        result = await _attribute_broadcast("off-the-hook", "")
    assert result is None
    mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_attribute_broadcast_db_error_returns_none_not_raises():
    with patch("vula.commerce.service._client", side_effect=RuntimeError("db down")):
        result = await _attribute_broadcast("off-the-hook", "0821234567")
    assert result is None


@pytest.mark.asyncio
async def test_create_order_includes_attribution_in_insert():
    mock_table = MagicMock()
    mock_table.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "order1", "display_id": "OTH-00001", "total_cents": 1000}])
    mock_table.select.return_value.eq.return_value.order.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[])   # _next_order_display_id lookup
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    cart = {"id": "cart1", "commerce_cart_items": [
        {"product_id": "p1", "quantity": 1, "unit_price_cents": 1000}]}
    checkout_data = {"customer_phone": "27821234567", "customer_name": "Jane",
                     "delivery_address": "12 Main Rd"}

    with (
        patch("vula.commerce.service._client", return_value=mock_db),
        patch("vula.commerce.service._attribute_broadcast", new=AsyncMock(return_value="b1")),
    ):
        await create_order("off-the-hook", cart, checkout_data)

    insert_call = mock_table.insert.call_args_list[0]
    inserted_order = insert_call[0][0]
    assert inserted_order["attributed_broadcast_id"] == "b1"


@pytest.mark.asyncio
async def test_admin_broadcast_recipients_computes_attributed_revenue():
    orders_table = MagicMock()
    orders_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[
            {"total_cents": 15000, "status": "paid"},
            {"total_cents": 20000, "status": "dispatched"},
            {"total_cents": 5000, "status": "pending_payment"},   # excluded
            {"total_cents": 3000, "status": "cancelled"},          # excluded
        ])
    recipients_table = MagicMock()
    recipients_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[
            {"phone": "27821234567", "status": "read", "error": None, "clicked_at": "2026-07-20T00:00:00Z", "click_count": 1},
        ])

    def _table(name):
        return orders_table if name == "commerce_orders" else recipients_table

    mock_db = MagicMock()
    mock_db.table.side_effect = _table

    with patch("vula.api.commerce.service._client", return_value=mock_db):
        result = await admin_broadcast_recipients("off-the-hook", "b1")

    assert result["funnel"]["attributed_orders"] == 2
    assert result["funnel"]["attributed_revenue_cents"] == 35000


@pytest.mark.asyncio
async def test_admin_broadcast_recipients_defaults_to_zero_on_missing_migration():
    recipients_table = MagicMock()
    recipients_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[])

    def _table(name):
        if name == "commerce_orders":
            raise RuntimeError("column attributed_broadcast_id does not exist")
        return recipients_table

    mock_db = MagicMock()
    mock_db.table.side_effect = _table

    with patch("vula.api.commerce.service._client", return_value=mock_db):
        result = await admin_broadcast_recipients("off-the-hook", "b1")

    assert result["funnel"]["attributed_orders"] == 0
    assert result["funnel"]["attributed_revenue_cents"] == 0
