"""create_order converts the cart it charged, and every preview uses the same delivery fee.

2026-09-25 review: `service.clear_cart` had no callers, so a WhatsApp customer's cart (one
long-lived "active" cart per phone number) still held the previous order's items — the next
order re-charged them and a repeated "yes" placed a duplicate. Separately, view_cart /
review_order showed the cart's snapshotted R80 delivery while create_order charged the
tenant's configured fee / free-delivery threshold, so the preview total could differ from
the amount charged.
"""
from unittest.mock import patch

import pytest

from vula.commerce import service

TENANT = "off-the-hook"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self._db, self._table = db, table
        self._op, self._payload, self._filters = "select", None, []

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def gte(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def _match(self, row):
        return all(row.get(c) == v for c, v in self._filters)

    def execute(self):
        rows = self._db.store.setdefault(self._table, [])
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            rows.extend(dict(p) for p in payload)
            return _Result(list(payload))
        if self._op == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self._payload)
            return _Result(hit)
        if self._op == "delete":
            keep = [r for r in rows if not self._match(r)]
            gone = len(rows) - len(keep)
            self._db.store[self._table] = keep
            return _Result([{}] * gone)
        return _Result([])


class _RpcCall:
    def __init__(self, v):
        self._v = v

    def execute(self):
        return _Result(self._v)


class _FakeDB:
    def __init__(self):
        self.store = {}
        self._n = 0

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params=None):
        if name == "next_document_number":
            self._n += 1
            return _RpcCall(self._n)
        return _RpcCall(True)  # reserve_* stock: untracked -> always available


@pytest.mark.asyncio
async def test_create_order_converts_and_empties_the_cart():
    db = _FakeDB()
    db.store["commerce_carts"] = [{"id": "cart1", "status": "active"}]
    db.store["commerce_cart_items"] = [
        {"id": "i1", "cart_id": "cart1", "product_id": "p1", "quantity": 1, "unit_price_cents": 10000},
    ]
    cart = {"id": "cart1", "delivery_cents": 0, "commerce_cart_items": list(db.store["commerce_cart_items"])}
    with patch("vula.commerce.service._client", return_value=db), \
         patch("vula.commerce.order_workflow.get_order_settings", return_value={}):
        await service.create_order(TENANT, cart, {
            "customer_name": "Jane", "customer_phone": "0821234567", "delivery_address": "1 Main Rd"})

    assert db.store["commerce_cart_items"] == []
    assert db.store["commerce_carts"][0]["status"] == "converted"


@pytest.mark.asyncio
async def test_cart_clear_failure_never_fails_the_order():
    db = _FakeDB()
    cart = {"id": "cart1", "delivery_cents": 0, "commerce_cart_items": [
        {"product_id": "p1", "quantity": 1, "unit_price_cents": 10000}]}

    async def _boom(_cart_id):
        raise RuntimeError("db down")

    with patch("vula.commerce.service._client", return_value=db), \
         patch("vula.commerce.service.clear_cart", side_effect=_boom), \
         patch("vula.commerce.order_workflow.get_order_settings", return_value={}):
        order = await service.create_order(TENANT, cart, {
            "customer_name": "Jane", "customer_phone": "0821234567", "delivery_address": "1 Main Rd"})
    assert order["total_cents"] == 10000


def test_delivery_fee_uses_tenant_rules_over_cart_snapshot():
    cart = {"delivery_cents": 8000}
    with patch("vula.commerce.order_workflow.get_order_settings",
               return_value={"delivery_fee_cents": 5000, "free_delivery_over_cents": 50000}):
        assert service.delivery_fee_cents(TENANT, cart, 10000) == 5000
        assert service.delivery_fee_cents(TENANT, cart, 50000) == 0
    with patch("vula.commerce.order_workflow.get_order_settings", return_value={}):
        assert service.delivery_fee_cents(TENANT, cart, 10000) == 8000
