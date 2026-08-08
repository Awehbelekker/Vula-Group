"""Tests for the stock-oversell fix (migration 122, 2026-08 audit follow-up).

Before this fix, `create_order` never checked availability — stock was only
decremented later at payment confirmation via a clamp-to-zero RPC, so two
concurrent checkouts for the last unit of a product could both succeed with
no error to either customer. These tests exercise `create_order`'s new
reserve-at-creation-time behaviour: successful reservation decrements stock
and marks the order `stock_adjusted`; insufficient stock raises
`OutOfStockError` and creates no order; a multi-item cart where a later item
fails restores the earlier item's reservation rather than leaving it
stranded; and untracked stock (NULL stock_quantity) is always allowed
through.
"""
from unittest.mock import patch

import pytest

from vula.commerce import service

TENANT = "off-the-hook"


class _Result:
    def __init__(self, data):
        self.data = data


class _RpcCall:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return _Result(self._result)


class _Query:
    """Minimal fake postgrest query supporting only what create_order needs
    for orders/order_items inserts (no availability logic here — that lives
    entirely in the fake rpc(), mirroring where migration 122 puts it in
    production: a DB-side conditional UPDATE, not app code)."""
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._op = "select"
        self._payload = None
        self._filters = []

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def gte(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        rows = self._store.setdefault(self._table, [])
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for p in payload:
                rows.append(dict(p))
            return _Result(list(payload))
        return _Result([])  # broadcast-attribution lookup etc. — always empty in these tests


class _FakeSupabase:
    """Tracks product/variant stock directly so the fake reserve_* rpcs can
    enforce the same "only succeed if enough stock, NULL = unlimited" rule
    as the real SQL functions in migration 122."""
    def __init__(self, products=None, variants=None):
        self.store = {}
        self.products = {p["id"]: dict(p) for p in (products or [])}
        self.variants = {v["id"]: dict(v) for v in (variants or [])}
        self.reserve_calls = []

    def table(self, name):
        return _Query(self.store, name)

    def rpc(self, name, params=None):
        params = params or {}
        if name == "next_document_number":
            counters = self.store.setdefault("_counters", {})
            key = (params["p_tenant_id"], params["p_counter_key"])
            counters[key] = counters.get(key, 0) + 1
            return _RpcCall(counters[key])
        if name == "reserve_product_stock":
            self.reserve_calls.append(("product", params["p_product_id"], params["p_qty"]))
            p = self.products[params["p_product_id"]]
            qty = p.get("stock_quantity")
            if qty is None:
                return _RpcCall(True)
            if qty >= params["p_qty"]:
                p["stock_quantity"] = qty - params["p_qty"]
                return _RpcCall(True)
            return _RpcCall(False)
        if name == "reserve_variant_stock":
            self.reserve_calls.append(("variant", params["p_variant_id"], params["p_qty"]))
            v = self.variants[params["p_variant_id"]]
            qty = v.get("stock_quantity")
            if qty is None:
                return _RpcCall(True)
            if qty >= params["p_qty"]:
                v["stock_quantity"] = qty - params["p_qty"]
                return _RpcCall(True)
            return _RpcCall(False)
        raise NotImplementedError(f"fake rpc not implemented: {name}")


def _cart(items):
    return {"id": "cart1", "commerce_cart_items": items}


def _checkout_data(**overrides):
    data = {
        "customer_name": "Jane Client",
        "customer_phone": "0821234567",
        "delivery_address": "12 Main Rd",
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_create_order_reserves_stock_and_marks_adjusted():
    products = [{"id": "p1", "stock_quantity": 5}]
    fake = _FakeSupabase(products=products)
    items = [{"product_id": "p1", "quantity": 2, "unit_price_cents": 15000,
              "commerce_products": {"name": "Yellowtail 1kg"}}]
    with patch("vula.commerce.service._client", return_value=fake), \
         patch("vula.commerce.service.update_product_stock"), \
         patch("vula.commerce.service.update_variant_stock"):
        order = await service.create_order(TENANT, _cart(items), _checkout_data())

    assert fake.products["p1"]["stock_quantity"] == 3
    assert order["stock_adjusted"] is True
    assert fake.store["commerce_orders"][0]["subtotal_cents"] == 30000


@pytest.mark.asyncio
async def test_create_order_raises_out_of_stock_and_creates_no_order():
    products = [{"id": "p1", "stock_quantity": 1}]
    fake = _FakeSupabase(products=products)
    items = [{"product_id": "p1", "quantity": 5, "unit_price_cents": 15000,
              "commerce_products": {"name": "Yellowtail 1kg"}}]
    with patch("vula.commerce.service._client", return_value=fake), \
         patch("vula.commerce.service.update_product_stock"), \
         patch("vula.commerce.service.update_variant_stock"):
        with pytest.raises(service.OutOfStockError) as exc:
            await service.create_order(TENANT, _cart(items), _checkout_data())

    assert "Yellowtail 1kg" in str(exc.value)
    # stock untouched, no order/order_items rows written
    assert fake.products["p1"]["stock_quantity"] == 1
    assert fake.store.get("commerce_orders", []) == []


@pytest.mark.asyncio
async def test_partial_reservation_rolled_back_when_second_item_unavailable():
    products = [
        {"id": "p1", "stock_quantity": 5},
        {"id": "p2", "stock_quantity": 0},
    ]
    fake = _FakeSupabase(products=products)
    items = [
        {"product_id": "p1", "quantity": 2, "unit_price_cents": 10000,
         "commerce_products": {"name": "Hake"}},
        {"product_id": "p2", "quantity": 1, "unit_price_cents": 5000,
         "commerce_products": {"name": "Prawns"}},
    ]
    restored = []

    async def _fake_update_product_stock(tenant_id, product_id, delta):
        restored.append((product_id, delta))
        p = fake.products[product_id]
        p["stock_quantity"] = (p.get("stock_quantity") or 0) - delta

    with patch("vula.commerce.service._client", return_value=fake), \
         patch("vula.commerce.service.update_product_stock", side_effect=_fake_update_product_stock), \
         patch("vula.commerce.service.update_variant_stock"):
        with pytest.raises(service.OutOfStockError) as exc:
            await service.create_order(TENANT, _cart(items), _checkout_data())

    assert "Prawns" in str(exc.value)
    # p1's reservation (decrement of 2) must have been compensated back to 5.
    assert fake.products["p1"]["stock_quantity"] == 5
    assert ("p1", -2) in restored
    assert fake.store.get("commerce_orders", []) == []


@pytest.mark.asyncio
async def test_untracked_stock_null_is_always_allowed():
    products = [{"id": "p1", "stock_quantity": None}]
    fake = _FakeSupabase(products=products)
    items = [{"product_id": "p1", "quantity": 100, "unit_price_cents": 5000,
              "commerce_products": {"name": "Made to order platter"}}]
    with patch("vula.commerce.service._client", return_value=fake), \
         patch("vula.commerce.service.update_product_stock"), \
         patch("vula.commerce.service.update_variant_stock"):
        order = await service.create_order(TENANT, _cart(items), _checkout_data())

    assert order["stock_adjusted"] is True
    assert fake.products["p1"]["stock_quantity"] is None


@pytest.mark.asyncio
async def test_variant_reservation_uses_variant_id_not_product_id():
    variants = [{"id": "v1", "stock_quantity": 3}]
    fake = _FakeSupabase(variants=variants)
    items = [{"product_id": "p1", "variant_id": "v1", "quantity": 2, "unit_price_cents": 8000,
              "commerce_products": {"name": "T-Shirt"}}]
    with patch("vula.commerce.service._client", return_value=fake), \
         patch("vula.commerce.service.update_product_stock"), \
         patch("vula.commerce.service.update_variant_stock"):
        order = await service.create_order(TENANT, _cart(items), _checkout_data())

    assert fake.variants["v1"]["stock_quantity"] == 1
    assert order["stock_adjusted"] is True
    assert ("variant", "v1", 2) in fake.reserve_calls
