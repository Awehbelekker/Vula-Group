"""Tests for _handle_native_order (vula/api/whatsapp.py) — the receiving side of WhatsApp's
native product-catalog checkout. See tests/test_meta_catalog_sync.py for the sending side
(scripts/sync_meta_catalog.py). Real webhook payload shape verified against a working
implementation example, 2026-08-25 (Meta's own docs don't publish a full JSON example):
{"type": "order", "order": {"catalog_id": ..., "product_items": [{"product_retailer_id",
"quantity", "item_price", "currency"}], "text": "..."}}.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TID = "off-the-hook"
PHONE = "27821234567"


def _mock_product_lookup_chain(rows):
    m = MagicMock()
    m.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .limit.return_value.execute.return_value = MagicMock(data=rows)
    return m


@pytest.mark.asyncio
async def test_native_order_seeds_cart_and_asks_for_delivery_address():
    from vula.api.whatsapp import _handle_native_order

    order = {"catalog_id": "cat1", "product_items": [
        {"product_retailer_id": "hake-fillets", "quantity": 2, "item_price": 160.0, "currency": "ZAR"},
    ]}

    sent = {}
    async def fake_send_reply(phone, message, tenant_id=""):
        sent["message"] = message
        return True

    add_to_cart_mock = AsyncMock()

    with (
        patch("vula.commerce.service.get_or_create_cart", new=AsyncMock(
            return_value={"id": "cart1"})),
        patch("vula.commerce.service.add_to_cart", new=add_to_cart_mock),
        patch("vula.commerce.service._client", _mock_product_lookup_chain(
            [{"id": "prod1", "name": "Hake Fillets"}])),
        patch("vula.api.whatsapp._send_reply", new=fake_send_reply),
    ):
        await _handle_native_order(PHONE, order, TID)

    # Real product, real cart, real price — add_to_cart re-prices from the live product row,
    # never trusting the webhook's own item_price.
    add_to_cart_mock.assert_awaited_once_with(TID, "cart1", "prod1", 2.0)
    assert "2x Hake Fillets" in sent["message"]
    assert "delivery address" in sent["message"].lower()


@pytest.mark.asyncio
async def test_native_order_reports_unmatched_products_without_crashing():
    from vula.api.whatsapp import _handle_native_order

    order = {"product_items": [
        {"product_retailer_id": "discontinued-item", "quantity": 1},
    ]}
    sent = {}
    async def fake_send_reply(phone, message, tenant_id=""):
        sent["message"] = message
        return True

    with (
        patch("vula.commerce.service.get_or_create_cart", new=AsyncMock(return_value={"id": "cart1"})),
        patch("vula.commerce.service.add_to_cart", new=AsyncMock()),
        patch("vula.commerce.service._client", _mock_product_lookup_chain([])),  # no match
        patch("vula.api.whatsapp._send_reply", new=fake_send_reply),
    ):
        await _handle_native_order(PHONE, order, TID)

    assert "couldn't match" in sent["message"].lower()
    assert "delivery address" not in sent["message"].lower()  # nothing real got added


@pytest.mark.asyncio
async def test_native_order_includes_customer_note():
    from vula.api.whatsapp import _handle_native_order

    order = {"product_items": [{"product_retailer_id": "hake-fillets", "quantity": 1}],
             "text": "Please deliver before noon"}
    sent = {}
    async def fake_send_reply(phone, message, tenant_id=""):
        sent["message"] = message
        return True

    with (
        patch("vula.commerce.service.get_or_create_cart", new=AsyncMock(return_value={"id": "cart1"})),
        patch("vula.commerce.service.add_to_cart", new=AsyncMock()),
        patch("vula.commerce.service._client", _mock_product_lookup_chain(
            [{"id": "prod1", "name": "Hake Fillets"}])),
        patch("vula.api.whatsapp._send_reply", new=fake_send_reply),
    ):
        await _handle_native_order(PHONE, order, TID)

    assert "Please deliver before noon" in sent["message"]


@pytest.mark.asyncio
async def test_native_order_with_no_product_items_does_nothing():
    from vula.api.whatsapp import _handle_native_order

    with patch("vula.api.whatsapp._send_reply") as mock_send:
        await _handle_native_order(PHONE, {"product_items": []}, TID)

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_native_order_cart_creation_failure_does_not_raise():
    from vula.api.whatsapp import _handle_native_order

    order = {"product_items": [{"product_retailer_id": "hake-fillets", "quantity": 1}]}
    with patch("vula.commerce.service.get_or_create_cart",
              new=AsyncMock(side_effect=RuntimeError("db down"))):
        await _handle_native_order(PHONE, order, TID)  # must not raise


# ── 2026-09-08: a real 0 must never become a silent 1 ────────────────────────────

@pytest.mark.asyncio
async def test_a_zero_quantity_line_is_skipped_not_defaulted_to_one():
    """`item.get('quantity') or 1` used to treat an explicit 0 (a malformed/edited-cart line
    from Meta) the same as a genuinely missing field, seeding the cart with a unit the payload
    never actually asked for."""
    from vula.api.whatsapp import _handle_native_order

    order = {"product_items": [
        {"product_retailer_id": "hake-fillets", "quantity": 0},
        {"product_retailer_id": "chicken-thighs", "quantity": 2},
    ]}
    add_to_cart_mock = AsyncMock()
    sent = {}
    async def fake_send_reply(phone, message, tenant_id=""):
        sent["message"] = message
        return True

    with (
        patch("vula.commerce.service.get_or_create_cart", new=AsyncMock(
            return_value={"id": "cart1"})),
        patch("vula.commerce.service.add_to_cart", new=add_to_cart_mock),
        patch("vula.commerce.service._client", _mock_product_lookup_chain(
            [{"id": "prod2", "name": "Chicken Thighs"}])),
        patch("vula.api.whatsapp._send_reply", new=fake_send_reply),
    ):
        await _handle_native_order(PHONE, order, TID)

    add_to_cart_mock.assert_awaited_once_with(TID, "cart1", "prod2", 2.0)
    assert "hake" not in sent["message"].lower()
    assert "2x Chicken Thighs" in sent["message"]


@pytest.mark.asyncio
async def test_a_missing_quantity_still_defaults_to_one():
    """A genuinely absent quantity field (unlike an explicit 0) still means one unit."""
    from vula.api.whatsapp import _handle_native_order

    order = {"product_items": [{"product_retailer_id": "hake-fillets"}]}
    add_to_cart_mock = AsyncMock()
    with (
        patch("vula.commerce.service.get_or_create_cart", new=AsyncMock(
            return_value={"id": "cart1"})),
        patch("vula.commerce.service.add_to_cart", new=add_to_cart_mock),
        patch("vula.commerce.service._client", _mock_product_lookup_chain(
            [{"id": "prod1", "name": "Hake Fillets"}])),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()),
    ):
        await _handle_native_order(PHONE, order, TID)

    add_to_cart_mock.assert_awaited_once_with(TID, "cart1", "prod1", 1.0)
