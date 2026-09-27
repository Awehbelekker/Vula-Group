"""Gateway notifications are only trusted when verified, and only for an amount that covers
what's owed (2026-09-25 review).

Before: PayFast accepted an ITN with no signature at all, Ozow/Peach/iKhokha did no
verification, and no provider's amount was compared with the invoice/order — so a forged
POST to /v1/payments/webhook/{tenant}/{provider} could mark an invoice or order paid.
"""
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest

from vula import payments
from vula.api import payments as payments_api


# ── Provider verification ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_payfast_rejects_missing_signature():
    pf = payments.PayFast()
    form = {"m_payment_id": "inv-1", "payment_status": "COMPLETE", "amount_gross": "100.00"}
    assert await pf.verify_webhook({"passphrase": "pp"}, {}, b"", form) is None


@pytest.mark.asyncio
async def test_payfast_accepts_valid_signature_and_reports_amount():
    pf = payments.PayFast()
    form = {"m_payment_id": "inv-1", "payment_status": "COMPLETE", "amount_gross": "100.00"}
    form["signature"] = pf._sig(dict(form), "pp")
    res = await pf.verify_webhook({"passphrase": "pp"}, {}, b"", form)
    assert res["paid"] is True and res["reference"] == "inv-1" and res["amount_cents"] == 10000


@pytest.mark.asyncio
async def test_payfast_rejects_tampered_amount():
    pf = payments.PayFast()
    form = {"m_payment_id": "inv-1", "payment_status": "COMPLETE", "amount_gross": "100.00"}
    form["signature"] = pf._sig(dict(form), "pp")
    form["amount_gross"] = "1000.00"
    assert await pf.verify_webhook({"passphrase": "pp"}, {}, b"", form) is None


def _ozow_notice(key, **over):
    d = {"SiteCode": "SITE1", "TransactionId": "t1", "TransactionReference": "OTH-1001",
         "Amount": "250.00", "Status": "Complete", "CurrencyCode": "ZAR", "IsTest": "false",
         "StatusMessage": ""}
    d.update(over)
    d["Hash"] = payments.Ozow()._hash([d.get(f) or "" for f in payments.Ozow._NOTIFY_FIELDS], key)
    return d


@pytest.mark.asyncio
async def test_ozow_unverified_notice_rejected():
    oz = payments.Ozow()
    forged = {"TransactionReference": "OTH-1001", "Status": "Complete", "Amount": "250.00"}
    assert await oz.verify_webhook({"private_key": "k", "site_code": "SITE1"}, {}, b"", forged) is None
    # A correctly-hashed notice without our private key configured is still refused.
    assert await oz.verify_webhook({}, {}, b"", _ozow_notice("k")) is None


@pytest.mark.asyncio
async def test_ozow_verified_notice_accepted():
    res = await payments.Ozow().verify_webhook(
        {"private_key": "k", "site_code": "SITE1"}, {}, b"", _ozow_notice("k"))
    assert res["paid"] is True and res["amount_cents"] == 25000


@pytest.mark.asyncio
async def test_peach_requires_signature():
    pe = payments.Peach()
    data = {"merchantTransactionId": "OTH-1", "result.code": "000.000.000", "amount": "10.00"}
    assert await pe.verify_webhook({"webhook_secret": "s"}, {}, b"", dict(data)) is None
    data["signature"] = pe._signature(data, "s")
    res = await pe.verify_webhook({"webhook_secret": "s"}, {}, b"", data)
    assert res["paid"] is True and res["amount_cents"] == 1000


@pytest.mark.asyncio
async def test_ikhokha_requires_signature():
    ik = payments.IKhokha()
    body = json.dumps({"status": "SUCCESS", "externalTransactionID": "OTH-1", "amount": 1000}).encode()
    assert await ik.verify_webhook({"app_secret": "s"}, {}, body, {}) is None
    path = "/v1/payments/webhook/t/ikhokha"
    sign = hmac.new(b"s", (path + body.decode()).encode(), hashlib.sha256).hexdigest()
    res = await ik.verify_webhook({"app_secret": "s"}, {"IK-SIGN": sign, "x-vula-path": path}, body, {})
    assert res["paid"] is True and res["amount_cents"] == 1000


# ── Route: amount + idempotency ──────────────────────────────────────────────

class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.patch = db, table, [], None

    def select(self, *_a):
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def eq(self, c, v):
        self.filters.append((c, v))
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        rows = [r for r in self.db.get(self.table, []) if all(r.get(c) == v for c, v in self.filters)]
        if self.patch is not None:
            for r in rows:
                r.update(self.patch)
        return _Res(rows)


class _DB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables, name)


class _Req:
    def __init__(self):
        self.headers = {"content-type": "application/json"}

        class _U:
            path = "/v1/payments/webhook/t1/payfast"
        self.url = _U()

    async def body(self):
        return b"{}"


async def _run(db, verified):
    prov = AsyncMock()
    prov.verify_webhook = AsyncMock(return_value=verified)
    upd = AsyncMock(return_value={"id": "inv-1"})
    notify = AsyncMock()
    with patch.object(payments, "get_provider", return_value=prov), \
         patch.object(payments, "_client", return_value=_DB({})), \
         patch("vula.commerce.service._client", return_value=db), \
         patch("vula.commerce.service.update_invoice_status", upd), \
         patch("vula.api.yoco._notify_order_paid", notify):
        await payments_api.payment_webhook("t1", "payfast", _Req())
    return upd, notify


@pytest.mark.asyncio
async def test_invoice_underpayment_not_marked_paid():
    db = _DB({"commerce_invoices": [{"id": "inv-1", "tenant_id": "t1", "status": "sent",
                                      "total_cents": 50000, "total_paid_cents": 0}]})
    upd, _ = await _run(db, {"reference": "inv-1", "paid": True, "amount_cents": 100})
    upd.assert_not_awaited()


@pytest.mark.asyncio
async def test_invoice_full_payment_marked_paid_once():
    db = _DB({"commerce_invoices": [{"id": "inv-1", "tenant_id": "t1", "status": "sent",
                                      "total_cents": 50000, "total_paid_cents": 0}]})
    upd, _ = await _run(db, {"reference": "inv-1", "paid": True, "amount_cents": 50000})
    upd.assert_awaited_once()
    db.tables["commerce_invoices"][0]["status"] = "paid"
    upd2, _ = await _run(db, {"reference": "inv-1", "paid": True, "amount_cents": 50000})
    upd2.assert_not_awaited()


@pytest.mark.asyncio
async def test_order_missing_amount_not_marked_paid():
    db = _DB({"commerce_invoices": [], "commerce_orders": [
        {"id": "o1", "display_id": "OTH-1", "tenant_id": "t1", "status": "pending_payment", "total_cents": 9000}]})
    _, notify = await _run(db, {"reference": "OTH-1", "paid": True, "amount_cents": None})
    notify.assert_not_awaited()
    assert db.tables["commerce_orders"][0]["status"] == "pending_payment"


@pytest.mark.asyncio
async def test_order_paid_in_full_marked_and_notified():
    db = _DB({"commerce_invoices": [], "commerce_orders": [
        {"id": "o1", "display_id": "OTH-1", "tenant_id": "t1", "status": "pending_payment", "total_cents": 9000}]})
    _, notify = await _run(db, {"reference": "OTH-1", "paid": True, "amount_cents": 9000})
    notify.assert_awaited_once()
    assert db.tables["commerce_orders"][0]["status"] == "paid"
