"""The 9 Nov 2026 go-live audit's money findings (docs/golive-audit-2026-11-09.md), each as the
case that went wrong before the fix.

S6  a business's own Yoco secret could sign "paid" for another business's order; no amount check
S7  a PayFast ITN for a business with no passphrase could be forged (no PayFast confirmation)
H4  PayFast/Ozow/Paystack/Peach/iKhokha order payments never reached the ledger
M1  an order's invoice dropped the discount and added VAT on top, and stayed unpaid
M2  typing "yes" ran the model's new arguments, not the preview's
H3  money and messaging tools that acted on the first call
H5  invoice edits took the browser's totals; status could go from sent/paid back to draft
"""
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import ledger, service

TID, OTHER = "shop-a", "shop-b"


# ── a small in-memory table store: select / update / eq / neq / limit / order ──────────────

class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.patch = db, name, [], None

    def select(self, *_a):
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def eq(self, c, v):
        self.filters.append(lambda r, c=c, v=v: r.get(c) == v)
        return self

    def neq(self, c, v):
        self.filters.append(lambda r, c=c, v=v: r.get(c) != v)
        return self

    def gt(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        rows = [r for r in self.db.tables.get(self.name, []) if all(f(r) for f in self.filters)]
        if self.patch is not None:
            for r in rows:
                r.update(self.patch)
            self.db.writes.append((self.name, dict(self.patch), len(rows)))
        return SimpleNamespace(data=[dict(r) for r in rows])


class _DB:
    def __init__(self, **tables):
        self.tables, self.writes = tables, []

    def table(self, name):
        return _Q(self, name)


@pytest.fixture
def books(monkeypatch):
    posted = []
    monkeypatch.setattr(ledger, "post_order_paid", lambda tid, order: posted.append(("order_paid", order["id"])))
    monkeypatch.setattr(ledger, "post_invoice_paid", lambda tid, inv: posted.append(("invoice_paid", inv["id"])))
    return posted


def _order(**kw):
    return {"id": "o1", "tenant_id": TID, "display_id": "SHA-0001", "status": "pending_payment",
            "total_cents": 9000, "customer_phone": "27820000001", "customer_name": "Jane", **kw}


# ── S6: Yoco ─────────────────────────────────────────────────────────────────────────────────

class _Req:
    def __init__(self, payload, secret="sek"):
        self._raw = json.dumps(payload).encode()
        self.headers = {"yoco-signature": hmac.new(secret.encode(), self._raw, hashlib.sha256).hexdigest()}

    async def body(self):
        return self._raw

    async def json(self):
        return json.loads(self._raw)


async def _yoco(db, payload, monkeypatch):
    from vula.api import yoco
    notify = AsyncMock()
    stock = AsyncMock()
    inv_paid = AsyncMock()
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(yoco.settings, "yoco_webhook_secret", "sek")
    monkeypatch.setattr(yoco.settings, "debug", False)
    # Every business's Yoco secret is "sek" here: shop-a signs its own payloads with its own key.
    monkeypatch.setattr(yoco, "_get_tenant_yoco_creds", AsyncMock(return_value={"webhook_secret": "sek"}))
    monkeypatch.setattr(yoco, "_notify_order_paid", notify)
    monkeypatch.setattr(service, "apply_order_stock", stock)
    monkeypatch.setattr(service, "update_invoice_status", inv_paid)
    await yoco.yoco_webhook(_Req(payload))
    return notify, stock, inv_paid


def _paid_event(order_id="o1", tenant=TID, amount=9000, currency="ZAR", kind="payment.succeeded"):
    return {"type": kind, "payload": {"amount": amount, "currency": currency,
                                      "metadata": {"order_id": order_id, "tenant_id": tenant}}}


@pytest.mark.asyncio
async def test_s6_a_business_cannot_sign_paid_for_another_businesss_order(monkeypatch, books):
    db = _DB(commerce_orders=[_order(tenant_id=OTHER)])
    notify, _, _ = await _yoco(db, _paid_event(tenant=TID), monkeypatch)
    assert db.tables["commerce_orders"][0]["status"] == "pending_payment"
    notify.assert_not_awaited()
    assert books == []


@pytest.mark.asyncio
async def test_s6_nor_cancel_it(monkeypatch, books):
    db = _DB(commerce_orders=[_order(tenant_id=OTHER)])
    _, stock, _ = await _yoco(db, _paid_event(tenant=TID, kind="checkout.expired"), monkeypatch)
    assert db.tables["commerce_orders"][0]["status"] == "pending_payment"
    stock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("amount,currency", [(100, "ZAR"), (None, "ZAR"), (9000, "USD")])
async def test_s6_an_underpaid_or_foreign_currency_event_does_not_mark_the_order_paid(amount, currency, monkeypatch, books):
    db = _DB(commerce_orders=[_order()])
    notify, _, _ = await _yoco(db, _paid_event(amount=amount, currency=currency), monkeypatch)
    assert db.tables["commerce_orders"][0]["status"] == "pending_payment"
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_s6_a_full_payment_marks_paid_once_posts_once_and_tells_the_customer_once(monkeypatch, books):
    db = _DB(commerce_orders=[_order()])
    notify, _, _ = await _yoco(db, _paid_event(), monkeypatch)
    notify2, _, _ = await _yoco(db, _paid_event(kind="checkout.completed"), monkeypatch)
    assert db.tables["commerce_orders"][0]["status"] == "paid"
    assert notify.await_count + notify2.await_count == 1
    assert books == [("order_paid", "o1")]


@pytest.mark.asyncio
async def test_s6_only_one_of_two_simultaneous_events_moves_the_order(books, monkeypatch):
    """Both events read pending_payment before either writes; only one conditional write wins."""
    db = _DB(commerce_orders=[_order()])
    monkeypatch.setattr(service, "_client", lambda: db)
    first = await service.mark_order_paid(TID, "o1", "card")
    second = await service.mark_order_paid(TID, "o1", "card")
    assert first and second is None and books == [("order_paid", "o1")]
    assert await service.mark_order_paid(OTHER, "o1") is None


@pytest.mark.asyncio
async def test_s6_an_invoice_link_underpaid_is_not_marked_paid(monkeypatch, books):
    db = _DB(commerce_invoices=[{"id": "inv-1", "tenant_id": TID, "status": "sent",
                                 "total_cents": 50000, "total_paid_cents": 0}])
    payload = {"type": "payment.succeeded", "payload": {"amount": 100, "currency": "ZAR",
                                                         "metadata": {"invoice_id": "inv-1", "tenant_id": TID}}}
    _, _, inv_paid = await _yoco(db, payload, monkeypatch)
    inv_paid.assert_not_awaited()


# ── S7 + H4: the generic gateway webhook ─────────────────────────────────────────────────────

class _GReq:
    headers = {"content-type": "application/x-www-form-urlencoded"}
    url = SimpleNamespace(path=f"/v1/payments/webhook/{TID}/payfast")
    client = SimpleNamespace(host="197.97.145.144")

    def __init__(self, form):
        self._form = form

    async def body(self):
        return b"x"

    async def form(self):
        return self._form


async def _gateway(db, form, monkeypatch, confirmed=True, provider="payfast"):
    from vula import payments
    from vula.api import payments as api
    from vula.api import yoco
    prov = SimpleNamespace(verify_webhook=AsyncMock(return_value={
        "reference": "SHA-0001", "paid": True, "amount_cents": 9000}))
    creds_db = _DB(vula_payment_providers=[{"tenant_id": TID, "provider": provider, "mode": "live",
                                            "credentials": {"merchant_id": "10001234"}}])
    validate = AsyncMock(return_value=confirmed)
    notify = AsyncMock()
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(payments, "_client", lambda: creds_db)
    monkeypatch.setattr(payments, "_decrypt_creds", lambda c: c or {})
    monkeypatch.setattr(payments, "get_provider", lambda name: prov)
    monkeypatch.setattr(payments, "payfast_server_confirms", validate)
    monkeypatch.setattr("vula.payfast_source.allowed", AsyncMock(return_value=True))
    monkeypatch.setattr("vula.tap.api.try_handle_itn", AsyncMock(return_value=None))
    monkeypatch.setattr(yoco, "_notify_order_paid", notify)
    await api.payment_webhook(TID, provider, _GReq(form))
    return notify, validate


@pytest.mark.asyncio
async def test_s7_a_payfast_itn_payfast_doesnt_confirm_is_ignored(monkeypatch, books):
    db = _DB(commerce_invoices=[], commerce_orders=[_order()])
    notify, validate = await _gateway(db, {"merchant_id": "10001234", "payment_status": "COMPLETE"},
                                      monkeypatch, confirmed=False)
    validate.assert_awaited_once()
    assert db.tables["commerce_orders"][0]["status"] == "pending_payment" and books == []
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_s7_a_payfast_itn_for_another_merchant_account_is_ignored(monkeypatch, books):
    db = _DB(commerce_invoices=[], commerce_orders=[_order()])
    notify, validate = await _gateway(db, {"merchant_id": "99999999", "payment_status": "COMPLETE"}, monkeypatch)
    validate.assert_not_awaited()
    assert db.tables["commerce_orders"][0]["status"] == "pending_payment"


@pytest.mark.asyncio
async def test_h4_a_confirmed_gateway_payment_is_in_the_books(monkeypatch, books):
    db = _DB(commerce_invoices=[], commerce_orders=[_order()])
    notify, _ = await _gateway(db, {"merchant_id": "10001234", "payment_status": "COMPLETE"}, monkeypatch)
    assert db.tables["commerce_orders"][0]["status"] == "paid"
    assert books == [("order_paid", "o1")]
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_h4_other_gateways_post_to_the_books_too(monkeypatch, books):
    db = _DB(commerce_invoices=[], commerce_orders=[_order()])
    await _gateway(db, {}, monkeypatch, provider="ozow")
    assert books == [("order_paid", "o1")]


# ── M1: an order's invoice says what the order charged ───────────────────────────────────────

ORDER_WITH_CODE = {
    "id": "o9", "tenant_id": TID, "display_id": "SHA-0009", "customer_name": "Jane",
    "customer_phone": "27820000001", "subtotal_cents": 100000, "discount_code": "WELCOME10",
    "discount_cents": 10000, "delivery_cents": 5000, "total_cents": 95000,
    "commerce_order_items": [{"product_id": "p1", "product_name": "Yellowtail", "quantity": 2,
                              "unit_price_cents": 50000}],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("settings_row", [{}, {"prices_include_vat": False, "vat_registered": True},
                                          {"vat_registered": False}])
async def test_m1_the_orders_invoice_totals_what_the_customer_paid(settings_row, monkeypatch):
    made = {}

    async def create(tid, data):
        made.update(data)
        s = data.get("prices_include_vat", settings_row.get("prices_include_vat", False))
        rate = 15.0 if settings_row.get("vat_registered", True) else 0.0
        *_, total, _ = service._compute_totals(data["line_items"], rate, s)
        return {"id": "inv-9", "invoice_number": "SHA-INV-00009", "total_cents": total}
    monkeypatch.setattr(service, "get_order", AsyncMock(return_value=dict(ORDER_WITH_CODE)))
    monkeypatch.setattr(service, "get_invoice_settings", AsyncMock(return_value=settings_row))
    monkeypatch.setattr(service, "create_invoice", create)
    monkeypatch.setattr(service, "update_invoice_status", AsyncMock(return_value={}))
    with patch("vula.commerce.pdf.merge_branding", return_value={}), \
         patch("vula.commerce.pdf.render_invoice_pdf", return_value=b"%PDF"), \
         patch("vula.api.whatsapp._send_invoice_document", new=AsyncMock(return_value=True)):
        inv = await service.send_order_invoice(TID, "o9", with_pay_link=False)
    assert inv["total_cents"] == 95000                          # was 120750 (or 105000 inclusive)
    assert any(l["description"] == "Discount (WELCOME10)" for l in made["line_items"])


@pytest.mark.asyncio
async def test_m1_an_invoice_that_wouldnt_match_the_order_is_never_sent(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(service, "get_order", AsyncMock(return_value={**ORDER_WITH_CODE, "total_cents": 1}))
    monkeypatch.setattr(service, "get_invoice_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "create_invoice", create)
    assert await service.send_order_invoice(TID, "o9") is None
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_m1_paying_the_order_settles_its_invoice_without_booking_the_sale_twice(monkeypatch, books):
    from vula.commerce.order_deposit import DEPOSIT_NOTE
    db = _DB(commerce_orders=[_order()], commerce_invoices=[
        {"id": "inv-1", "tenant_id": TID, "order_id": "o1", "status": "sent", "total_cents": 9000, "notes": ""},
        {"id": "inv-d", "tenant_id": TID, "order_id": "o1", "status": "sent", "total_cents": 3000,
         "notes": f"{DEPOSIT_NOTE} SHA-0001"}])
    monkeypatch.setattr(service, "_client", lambda: db)
    await service.mark_order_paid(TID, "o1")
    inv = {i["id"]: i for i in db.tables["commerce_invoices"]}
    assert inv["inv-1"]["status"] == "paid" and inv["inv-1"]["total_paid_cents"] == 9000
    assert inv["inv-d"]["status"] == "sent"                     # a deposit invoice is its own
    assert books == [("order_paid", "o1")]                      # no invoice_paid on top


# ── M2 + H3: the owner's "yes" ───────────────────────────────────────────────────────────────

def _llm_calls(name, args_json):
    from tests.test_known_bad_transcripts import _resp, _tool_call
    n = {"i": 0}

    async def completion(*a, **kw):
        n["i"] += 1
        return _resp(tool_calls=[_tool_call("c1", name, args_json)]) if n["i"] == 1 else _resp(content="Done")
    return completion


@pytest.mark.asyncio
async def test_m2_yes_runs_the_previewed_amount_not_the_models_new_one(monkeypatch):
    import core.skills.commerce_admin as ca
    from tests.test_known_bad_transcripts import _fake_route
    seen = []

    async def dispatch(self, name, args, ctx):
        seen.append(dict(args))
        return {"sent": True}
    monkeypatch.setattr(ca, "_claim_preview", lambda tid, phone, tool: {"customer": "Sam", "amount_rands": 1200})
    with patch.object(ca, "resolve_generation_route", new=_fake_route), \
         patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)), \
         patch.object(ca.CommerceAdminSkill, "_dispatch_tool", new=dispatch), \
         patch("litellm.acompletion", new=_llm_calls("payment_link",
                                                     '{"customer": "Sam", "amount_rands": 12000, "confirm": true}')):
        await ca.CommerceAdminSkill()._agent_loop("system", "", "yes", {"tenant_id": TID, "phone": "27820000001"},
                                                  tools=None)
    assert seen == [{"customer": "Sam", "amount_rands": 1200, "confirm": True}]


@pytest.mark.parametrize("tool", ["order_payment", "create_manual_order", "create_discount_code",
                                  "update_order_status", "staff_notice", "staff_poll", "cancel_booking"])
def test_h3_these_tools_wait_for_a_preview(tool):
    import core.skills.commerce_admin as ca
    assert tool in ca._PREVIEW_FIRST


@pytest.mark.asyncio
async def test_h3_order_payment_with_confirm_on_the_first_call_only_previews(monkeypatch):
    import core.skills.commerce_admin as ca
    from tests.test_known_bad_transcripts import _fake_route
    seen = []

    async def dispatch(self, name, args, ctx):
        seen.append(dict(args))
        return {"preview": True, "message": "Confirm"} if not args.get("confirm") else {"sent": True}
    monkeypatch.setattr(ca, "_claim_preview", lambda tid, phone, tool: None)
    with patch.object(ca, "resolve_generation_route", new=_fake_route), \
         patch.object(ca, "escalate_to_cloud", return_value=("openrouter/test", "k", None)), \
         patch.object(ca.CommerceAdminSkill, "_dispatch_tool", new=dispatch), \
         patch("litellm.acompletion", new=_llm_calls("order_payment",
                                                     '{"order_number": "SHA-0001", "balance": true, "confirm": true}')):
        with pytest.raises(ca.ConfirmationRequired):
            await ca.CommerceAdminSkill()._agent_loop("system", "", "send Jane the balance",
                                                      {"tenant_id": TID, "phone": "27820000001"}, tools=None)
    assert seen == [{"order_number": "SHA-0001", "balance": True}]


# ── H5: invoice edits ────────────────────────────────────────────────────────────────────────

def _app():
    from fastapi.testclient import TestClient
    from vula.api.server import app
    return TestClient(app)


def _inv(**kw):
    return {"id": "inv-1", "tenant_id": TID, "doc_type": "invoice", "invoice_number": "SHA-INV-00001",
            "status": "draft", "vat_rate": 15, **kw}


# A R1,200 VAT-inclusive payment-link draft as create_invoice stores it.
INCL_LINES = [{"description": "Payment", "quantity": 1, "unit_price_cents": 120000, "total_cents": 120000}]
INCL = _inv(line_items=INCL_LINES, subtotal_cents=104348, vat_cents=15652, total_cents=120000, discount_cents=0)


def test_h5_editing_a_vat_inclusive_draft_keeps_its_total(monkeypatch):
    from vula.api import commerce
    db = _DB(commerce_invoices=[dict(INCL)])
    monkeypatch.setattr(commerce.service, "_client", lambda: db)
    monkeypatch.setattr(commerce.service, "get_invoice", AsyncMock(return_value=dict(INCL)))
    monkeypatch.setattr(commerce.service, "get_invoice_settings", AsyncMock(return_value={}))
    # What the dashboard sends: its own VAT-exclusive sums (R1,380).
    r = _app().patch(f"/v1/commerce/{TID}/admin/invoices/inv-1", json={
        "customer_name": "Sam", "line_items": INCL_LINES, "vat_rate": 15, "subtotal_cents": 120000,
        "vat_cents": 18000, "total_cents": 138000, "discount_cents": 0, "discount_pct": 0})
    assert r.status_code == 200, r.text
    saved = db.tables["commerce_invoices"][0]
    assert saved["total_cents"] == 120000 and saved["vat_cents"] == 15652


def test_h5_a_browser_cannot_set_an_invoices_total(monkeypatch):
    from vula.api import commerce
    lines = [{"description": "Tiling", "quantity": 2, "unit_price_cents": 50000, "total_cents": 100000}]
    cur = _inv(line_items=lines, subtotal_cents=100000, vat_cents=15000, total_cents=115000, discount_cents=0)
    db = _DB(commerce_invoices=[dict(cur)])
    monkeypatch.setattr(commerce.service, "_client", lambda: db)
    monkeypatch.setattr(commerce.service, "get_invoice", AsyncMock(return_value=dict(cur)))
    r = _app().patch(f"/v1/commerce/{TID}/admin/invoices/inv-1", json={"line_items": lines, "total_cents": 1})
    assert r.status_code == 200
    assert db.tables["commerce_invoices"][0]["total_cents"] == 115000


@pytest.mark.parametrize("cur_status,new_status", [("sent", "draft"), ("paid", "draft"), ("paid", "sent"),
                                                   ("overdue", "cancelled"), ("part_paid", "void")])
def test_h5_an_issued_invoice_cannot_go_back(cur_status, new_status, monkeypatch):
    from vula.api import commerce
    db = _DB(commerce_invoices=[_inv(status=cur_status)])
    status = AsyncMock()
    monkeypatch.setattr(commerce.service, "_client", lambda: db)
    monkeypatch.setattr(commerce.service, "get_invoice", AsyncMock(return_value=_inv(status=cur_status)))
    monkeypatch.setattr(commerce.service, "update_invoice_status", status)
    r = _app().patch(f"/v1/commerce/{TID}/admin/invoices/inv-1", json={"status": new_status})
    assert r.status_code == 409 and "credit note" in r.json()["detail"]
    status.assert_not_awaited()


def test_h5_marking_a_paid_invoice_paid_again_books_nothing(monkeypatch):
    from vula.api import commerce
    status = AsyncMock()
    monkeypatch.setattr(commerce.service, "_client", lambda: _DB(commerce_invoices=[]))
    monkeypatch.setattr(commerce.service, "get_invoice", AsyncMock(return_value=_inv(status="paid")))
    monkeypatch.setattr(commerce.service, "update_invoice_status", status)
    r = _app().patch(f"/v1/commerce/{TID}/admin/invoices/inv-1", json={"status": "paid"})
    assert r.status_code == 200
    status.assert_not_awaited()


def test_h5_a_draft_still_moves_forward(monkeypatch):
    from vula.api import commerce
    status = AsyncMock(return_value={"id": "inv-1", "status": "sent"})
    monkeypatch.setattr(commerce.service, "_client", lambda: _DB(commerce_invoices=[]))
    monkeypatch.setattr(commerce.service, "get_invoice", AsyncMock(return_value=_inv(status="draft")))
    monkeypatch.setattr(commerce.service, "update_invoice_status", status)
    r = _app().patch(f"/v1/commerce/{TID}/admin/invoices/inv-1", json={"status": "sent"})
    assert r.status_code == 200
    status.assert_awaited_once_with(TID, "inv-1", "sent")
