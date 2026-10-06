"""Payment links from WhatsApp + the tenant's own pay page (stub-gap brief, capability 1).

"Send Sam a payment link for R1,200": an invoice for exactly R1,200, the business's branded pay
page (card through its own gateway, plus its EFT details), WhatsApped to the customer only after
the owner confirms. The gateway's signed webhook marks it paid, posts the ledger and tells the
owner. Nothing here invents a figure — every amount is read back from the saved invoice.
"""
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import pay_page

TID = "digg-demo"
INV = {"id": "11111111-2222-3333-4444-555555555555", "tenant_id": TID, "doc_type": "invoice",
       "direction": "outbound", "status": "sent", "invoice_number": "DIG-INV-00042",
       "customer_name": "Sam Botha", "customer_phone": "27821112222",
       "total_cents": 120000, "total_paid_cents": 0}
BRAND = {"name": "DIGG <Architecture>", "accent_color": "#1E1E1E", "logo_url": None}


# ── the page ───────────────────────────────────────────────────────────────────

def test_page_shows_the_amount_card_and_eft_from_the_tenant():
    html = pay_page.render(BRAND, INV, "FNB 62845 · Branch 250655", "https://pay.yoco.com/x")
    assert "R1 200.00" in html and "DIG-INV-00042" in html
    assert "https://pay.yoco.com/x" in html and "FNB 62845" in html
    assert "DIGG &lt;Architecture&gt;" in html and "<Architecture>" not in html   # escaped


def test_a_paid_invoice_says_paid_and_offers_nothing():
    html = pay_page.render(BRAND, {**INV, "status": "paid"}, "FNB", "https://pay.yoco.com/x")
    assert "Paid — thank you" in html and "pay.yoco.com" not in html


def test_coming_back_from_the_gateway_never_claims_the_money_arrived():
    html = pay_page.render(BRAND, INV, None, "https://pay.yoco.com/x", result="success")
    assert "being confirmed" in html and "Paid" not in html


@pytest.mark.asyncio
async def test_the_gateway_returns_the_customer_to_the_tenants_page_not_off_the_hook():
    seen = {}

    async def fake_link(tenant_id, **kw):
        seen.update(kw)
        return type("L", (), {"url": "https://pay.example/abc", "provider": "payfast", "raw": {}})()
    with patch("vula.payments.create_pay_link", new=fake_link), \
            patch("vula.payments.default_provider_row", return_value={"provider": "payfast"}), \
            patch("vula.commerce.service._client"):
        url, provider = await pay_page.gateway_link(TID, INV)
    assert url == "https://pay.example/abc" and provider == "payfast"
    assert seen["success_url"].endswith(f"/v1/commerce/{TID}/pay/{INV['id']}?result=success")
    assert "offthehook" not in seen["cancel_url"]
    assert seen["amount_cents"] == 120000


@pytest.mark.asyncio
async def test_no_gateway_is_said_out_loud():
    with patch("vula.payments.create_pay_link", new=AsyncMock(return_value=None)), \
            patch("vula.payments.default_provider_row", return_value=None):
        with pytest.raises(pay_page.NoGateway):
            await pay_page.gateway_link(TID, INV)


def test_the_pay_page_route_rejects_a_supplier_bill_or_quote(monkeypatch):
    from fastapi.testclient import TestClient
    from vula.api import commerce
    from vula.api.server import app
    for bad in ({**INV, "direction": "inbound"}, {**INV, "doc_type": "quote"}):
        monkeypatch.setattr(commerce.service, "get_invoice", AsyncMock(return_value=bad))
        r = TestClient(app).get(f"/v1/commerce/{TID}/pay/{INV['id']}")
        assert r.status_code == 404


# ── the WhatsApp tool ────────────────────────────────────────────────────────

@pytest.fixture()
def tool(monkeypatch):
    from core.skills import commerce_admin as ca
    from vula.api import commerce
    monkeypatch.setattr(commerce, "_aggregate_customers", AsyncMock(return_value={
        "a": {"name": "Sam Botha", "phone": "27821112222"},
        "b": {"name": "Thabo Mokoena", "phone": "27823334444"}}))
    monkeypatch.setattr(pay_page, "has_gateway", AsyncMock(return_value=True))
    monkeypatch.setattr("vula.commerce.order_workflow.get_order_settings",
                        lambda t: {"eft_details": "FNB 62845"})
    return ca.CommerceAdminSkill()


@pytest.mark.asyncio
async def test_preview_first_nothing_created_or_sent(tool, monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr("vula.commerce.service.create_invoice", create)
    out = await tool._payment_link(TID, {"customer_name": "Sam", "amount_rands": 1200})
    assert out["preview"] and out["amount"] == "R1,200.00" and out["customer"] == "Sam Botha"
    assert out["pays_by"] == "card or EFT"
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_creates_the_exact_amount_and_sends_the_tenants_page(tool, monkeypatch):
    from vula.commerce import service
    made = {}

    async def create(tid, data):
        made.update(data)
        return dict(INV, status="draft")
    monkeypatch.setattr(service, "create_invoice", create)
    monkeypatch.setattr(service, "update_invoice_status", AsyncMock(return_value=INV))
    monkeypatch.setattr(service, "get_invoice", AsyncMock(return_value=INV))
    monkeypatch.setattr(pay_page, "gateway_link", AsyncMock(return_value=("https://pay", "yoco")))
    monkeypatch.setattr("vula.api.tenants.display_name", lambda t: "DIGG")
    with patch("vula.api.whatsapp._send_reply", AsyncMock(return_value=True)) as send:
        out = await tool._payment_link(TID, {"customer_name": "Sam", "amount_rands": 1200,
                                             "description": "Deposit", "confirm": True})
    assert made["prices_include_vat"] is True
    assert made["line_items"] == [{"description": "Deposit", "quantity": 1, "unit_price_cents": 120000}]
    to, msg = send.await_args.args[0], send.await_args.args[1]
    assert to == "27821112222" and f"/v1/commerce/{TID}/pay/{INV['id']}" in msg
    assert "R1,200.00" in msg and "DIG-INV-00042" in msg
    assert out["sent"] and out["amount"] == "R1,200.00" and out["status"] == "sent"


@pytest.mark.asyncio
async def test_two_customers_called_sam_means_ask(tool, monkeypatch):
    from vula.api import commerce
    monkeypatch.setattr(commerce, "_aggregate_customers", AsyncMock(return_value={
        "a": {"name": "Sam Botha", "phone": "27821112222"},
        "b": {"name": "Sam Naidoo", "phone": "27825556666"}}))
    out = await tool._payment_link(TID, {"customer_name": "Sam", "amount_rands": 1200})
    assert out["status"] == "need_info" and "Sam Naidoo" in out["message"]


@pytest.mark.asyncio
async def test_no_gateway_and_no_eft_fails_loudly(tool, monkeypatch):
    monkeypatch.setattr(pay_page, "has_gateway", AsyncMock(return_value=False))
    monkeypatch.setattr("vula.commerce.order_workflow.get_order_settings", lambda t: {})
    out = await tool._payment_link(TID, {"customer_name": "Sam", "amount_rands": 1200})
    assert "Settings › Payments" in out["error"]


@pytest.mark.asyncio
async def test_undeliverable_whatsapp_is_reported_with_the_link(tool, monkeypatch):
    from vula.commerce import service
    monkeypatch.setattr(service, "create_invoice", AsyncMock(return_value=dict(INV, status="draft")))
    monkeypatch.setattr(service, "update_invoice_status", AsyncMock())
    monkeypatch.setattr(service, "get_invoice", AsyncMock(return_value=dict(INV, status="draft")))
    monkeypatch.setattr(pay_page, "gateway_link", AsyncMock(return_value=("https://pay", "yoco")))
    monkeypatch.setattr("vula.api.tenants.display_name", lambda t: "DIGG")
    with patch("vula.api.whatsapp._send_reply", AsyncMock(return_value=False)):
        out = await tool._payment_link(TID, {"customer_name": "Sam", "amount_rands": 1200,
                                             "confirm": True})
    assert not out["sent"] and "Forward the link yourself" in out["message"]
    service.update_invoice_status.assert_not_awaited()


# ── paid → owner told ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_is_told_once_when_the_customer_pays(monkeypatch):
    monkeypatch.setattr("vula.commerce.service.get_invoice",
                        AsyncMock(return_value=dict(INV, status="paid")))
    monkeypatch.setattr("vula.commerce.approvals.tenant_admin_approvers",
                        AsyncMock(return_value=[{"phone": "27645755210"}]))
    with patch("vula.api.whatsapp._send_reply", AsyncMock(return_value=True)) as send:
        assert await pay_page.notify_paid(TID, INV["id"], "payfast") == 1
    msg, kw = send.await_args.args[1], send.await_args.kwargs
    assert "Sam Botha" in msg and "DIG-INV-00042" in msg and "R1 200.00" in msg
    assert kw["idem_key"] == f"invoice_paid:{INV['id']}"


@pytest.mark.asyncio
async def test_not_paid_means_no_message(monkeypatch):
    monkeypatch.setattr("vula.commerce.service.get_invoice", AsyncMock(return_value=INV))
    with patch("vula.api.whatsapp._send_reply", AsyncMock()) as send:
        assert await pay_page.notify_paid(TID, INV["id"], "payfast") == 0
    send.assert_not_awaited()
