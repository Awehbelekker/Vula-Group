"""Yoco webhook: fail closed without a secret, idempotent, resolves create_pay_link references,
and releases reserved stock when a payment fails (2026-09-25 review)."""
import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from vula.api import yoco


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, tables, name):
        self.tables, self.name, self.filters = tables, name, []

    def select(self, *_a):
        return self

    def eq(self, c, v):
        self.filters.append((c, v))
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        return _Res([r for r in self.tables.get(self.name, []) if all(r.get(c) == v for c, v in self.filters)])


class _DB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, n):
        return _Q(self.tables, n)


class _Req:
    def __init__(self, payload, headers=None):
        self._raw = json.dumps(payload).encode()
        self.headers = headers or {}

    async def body(self):
        return self._raw

    async def json(self):
        return json.loads(self._raw)


def _signed(payload, secret="sek"):
    raw = json.dumps(payload).encode()
    return _Req(payload, {"yoco-signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()})


async def _call(req, db, secret="sek", debug=False):
    upd_order = AsyncMock()
    upd_inv = AsyncMock()
    notify = AsyncMock()
    stock = AsyncMock()
    with patch.object(yoco.settings, "yoco_webhook_secret", secret), \
         patch.object(yoco.settings, "debug", debug), \
         patch.object(yoco, "_get_tenant_yoco_creds", AsyncMock(return_value=None)), \
         patch.object(yoco.commerce, "_client", return_value=db), \
         patch.object(yoco.commerce, "update_order_status", upd_order), \
         patch.object(yoco.commerce, "update_invoice_status", upd_inv), \
         patch.object(yoco.commerce, "apply_order_stock", stock), \
         patch.object(yoco, "_notify_order_paid", notify), \
         patch.object(yoco.settings, "n8n_webhook_base", ""):
        await yoco.yoco_webhook(req)
    return upd_order, upd_inv, notify, stock


@pytest.mark.asyncio
async def test_no_secret_anywhere_fails_closed_in_production():
    payload = {"type": "payment.succeeded", "payload": {"metadata": {"order_id": "o1"}}}
    with pytest.raises(HTTPException) as exc:
        await _call(_Req(payload), _DB({}), secret="")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_bad_signature_rejected():
    payload = {"type": "payment.succeeded", "payload": {"metadata": {"order_id": "o1"}}}
    with pytest.raises(HTTPException):
        await _call(_Req(payload, {"yoco-signature": "nope"}), _DB({}))


@pytest.mark.asyncio
async def test_standard_webhooks_signature_accepted():
    payload = {"type": "payment.succeeded", "payload": {"amount": 100, "metadata": {"order_id": "o1"}}}
    raw = json.dumps(payload).encode()
    key = b"k3y"
    secret = "whsec_" + base64.b64encode(key).decode()
    sig = base64.b64encode(hmac.new(key, b"msg_1.1700000000." + raw, hashlib.sha256).digest()).decode()
    req = _Req(payload, {"webhook-id": "msg_1", "webhook-timestamp": "1700000000",
                         "webhook-signature": f"v1,{sig}"})
    db = _DB({"commerce_orders": [{"id": "o1", "status": "pending_payment"}]})
    upd, _, notify, _ = await _call(req, db, secret=secret)
    upd.assert_awaited_once_with("o1", "paid")
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_success_event_notifies_once():
    payload = {"type": "checkout.completed", "payload": {"amount": 100, "metadata": {"order_id": "o1"}}}
    db = _DB({"commerce_orders": [{"id": "o1", "status": "paid"}]})
    upd, _, notify, _ = await _call(_signed(payload), db)
    upd.assert_not_awaited()
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_pay_link_reference_resolves_to_order():
    payload = {"type": "payment.succeeded",
               "payload": {"amount": 9000, "metadata": {"reference": "OTH-1001", "tenant_id": "t1"}}}
    db = _DB({"commerce_orders": [{"id": "o1", "tenant_id": "t1", "display_id": "OTH-1001",
                                   "status": "pending_payment", "customer_phone": "27820000000",
                                   "customer_name": "Jane"}]})
    upd, _, notify, _ = await _call(_signed(payload), db)
    upd.assert_awaited_once_with("o1", "paid")
    assert notify.await_args.kwargs["customer_phone"] == "27820000000"


@pytest.mark.asyncio
async def test_pay_link_reference_resolves_to_invoice():
    payload = {"type": "payment.succeeded",
               "payload": {"amount": 9000, "metadata": {"reference": "inv-1", "tenant_id": "t1"}}}
    db = _DB({"commerce_invoices": [{"id": "inv-1", "tenant_id": "t1", "status": "sent"}]})
    _, upd_inv, _, _ = await _call(_signed(payload), db)
    upd_inv.assert_awaited_once_with("t1", "inv-1", "paid")


@pytest.mark.asyncio
async def test_failed_payment_cancels_and_releases_stock():
    payload = {"type": "payment.failed", "payload": {"metadata": {"order_id": "o1"}}}
    db = _DB({"commerce_orders": [{"id": "o1", "status": "pending_payment"}]})
    upd, _, _, stock = await _call(_signed(payload), db)
    upd.assert_awaited_once_with("o1", "cancelled")
    stock.assert_awaited_once_with("o1", restore=True)


@pytest.mark.asyncio
async def test_expired_event_never_cancels_a_paid_order():
    payload = {"type": "checkout.expired", "payload": {"metadata": {"order_id": "o1"}}}
    db = _DB({"commerce_orders": [{"id": "o1", "status": "paid"}]})
    upd, _, _, stock = await _call(_signed(payload), db)
    upd.assert_not_awaited()
    stock.assert_not_awaited()
