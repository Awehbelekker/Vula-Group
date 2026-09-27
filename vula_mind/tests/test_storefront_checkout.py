"""Storefront checkout: the cart is spent only once the Yoco checkout exists, and a gateway
failure cancels the just-created order and releases its stock (code review 2026-09-27)."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from vula.api import commerce as commerce_api

CART = {"id": "cart1", "commerce_cart_items": [{"product_id": "p1", "quantity": 1, "unit_price_cents": 5000}]}
ORDER = {"id": "o1", "display_id": "OTH-1", "total_cents": 5000}


def _body():
    return commerce_api.CheckoutRequest(session_id="web-abc123", customer_name="Sam",
                                        customer_phone="27820000001", delivery_address="1 Main Rd")


def _run(post):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = post
    svc = commerce_api.service
    with patch("vula.api.tenants.is_active", return_value=True), \
         patch("vula.api.tenants.store_url", return_value="https://shop.test"), \
         patch.object(svc, "get_or_create_cart", new=AsyncMock(return_value=CART)), \
         patch.object(svc, "create_order", new=AsyncMock(return_value=ORDER)) as create, \
         patch.object(svc, "update_order_status", new=AsyncMock()) as status, \
         patch.object(svc, "apply_order_stock", new=AsyncMock()) as stock, \
         patch.object(svc, "clear_cart", new=AsyncMock()) as clear, \
         patch("vula.api.yoco._get_tenant_yoco_creds", new=AsyncMock(return_value={"secret_key": "sk"})), \
         patch("vula.commerce.service.send_order_invoice", new=AsyncMock()), \
         patch.object(commerce_api.httpx, "AsyncClient", return_value=client):
        import asyncio
        try:
            result, err = asyncio.run(commerce_api.create_checkout("shop", _body())), None
        except HTTPException as e:
            result, err = None, e
    return result, err, create, status, stock, clear


def test_success_clears_the_cart_only_after_the_checkout_exists():
    resp = MagicMock(is_success=True)
    resp.json.return_value = {"id": "co_1", "redirectUrl": "https://pay"}
    result, err, create, status, stock, clear = _run(AsyncMock(return_value=resp))
    assert err is None and result["redirect_url"] == "https://pay"
    assert create.await_args.kwargs == {"clear_cart_after": False}
    assert create.await_args.args[2]["payment_method"] == "online"
    status.assert_awaited_once_with("o1", "pending_payment", yoco_checkout_id="co_1")
    clear.assert_awaited_once_with("cart1")
    stock.assert_not_awaited()


@pytest.mark.parametrize("post", [
    AsyncMock(return_value=MagicMock(is_success=False, text="bad gateway")),
    AsyncMock(side_effect=httpx.ConnectError("down")),
])
def test_gateway_failure_keeps_the_cart_and_releases_the_order(post):
    _, err, _, status, stock, clear = _run(post)
    assert err is not None and err.status_code == 502
    clear.assert_not_awaited()                              # customer can just retry
    status.assert_awaited_once_with("o1", "cancelled")
    stock.assert_awaited_once_with("o1", restore=True)


def test_expiry_catches_storefront_yoco_orders_without_a_payment_method():
    import asyncio
    from vula.commerce import service
    rows = [{"id": "a", "payment_method": None, "yoco_checkout_id": "co_1"},   # storefront Yoco
            {"id": "b", "payment_method": "online", "yoco_checkout_id": None},
            {"id": "c", "payment_method": "eft", "yoco_checkout_id": None},     # left alone
            {"id": "d", "payment_method": None, "yoco_checkout_id": None}]     # left alone
    db = MagicMock()
    q = db.table.return_value
    for m in ("select", "eq", "lt", "limit", "update"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=rows)
    restore = AsyncMock()
    with patch.object(service, "_client", return_value=db), patch.object(service, "apply_order_stock", restore):
        n = asyncio.run(service.expire_abandoned_online_orders("shop"))
    assert n == 2
    assert [c.args[0] for c in restore.await_args_list] == ["a", "b"]
