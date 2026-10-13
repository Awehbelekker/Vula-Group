"""A paid WhatsApp order ends like a Tap to Pay payment: a till-slip picture in the chat with a
"View receipt" button (2026-10-13, Ian: "like the slip that prints out on the tap and pay")."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vula.commerce import order_slip

ORDER = {"id": "o-1", "tenant_id": "seedleme", "display_id": "SEE-00012", "status": "paid",
         "customer_phone": "27645755210", "delivery_address": "5 Korhaan Close",
         "subtotal_cents": 629700, "delivery_cents": 45000, "total_cents": 674700,
         "payment_method": "yoco", "updated_at": "2026-10-13T12:31:00+00:00",
         "commerce_order_items": [
             {"product_name": "Aquatina Blue Concrete Basin 47 x 23.5 x 12cm", "quantity": 2,
              "unit_price_cents": 209900, "total_cents": 419800},
             {"product_name": "Countertop Butler Concrete Basin – Charcoal – 31 x 31 x 12cm",
              "quantity": 1, "unit_price_cents": 209900}]}
SHOP = {"name": "SeedleMe", "accent": "#2f6f5e", "vat_registered": True, "vat_number": "4123456789"}


def test_the_link_cannot_be_guessed_or_altered():
    tok = order_slip.token_for("o-1")
    assert order_slip.order_id_from(tok) == "o-1"
    body, mac = tok.split(".")
    assert order_slip.order_id_from(order_slip.token_for("o-2").split(".")[0] + "." + mac) is None
    assert order_slip.order_id_from("rubbish") is None


def test_the_slip_shows_items_totals_and_vat_but_no_phone_or_address():
    d = order_slip.slip_data(ORDER, SHOP)
    assert d["items"] == [{"qty": 2, "name": "Aquatina Blue Concrete Basin 47 x 23.5 x 12cm", "cents": 419800},
                          {"qty": 1, "name": "Countertop Butler Concrete Basin – Charcoal – 31 x 31 x 12cm",
                           "cents": 209900}]
    assert (d["total_cents"], d["vat_cents"], d["paid_at"], d["method"]) == (674700, 88004, "13 Oct 2026, 14:31", "Card (Yoco)")
    assert "27645755210" not in str(d) and "Korhaan" not in str(d)
    assert order_slip.slip_data(ORDER, {**SHOP, "vat_registered": False})["vat_cents"] is None


def test_the_slip_is_a_picture():
    png = order_slip.render_png(order_slip.slip_data(ORDER, SHOP))
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000


@pytest.mark.asyncio
async def test_an_unpaid_order_has_no_slip(monkeypatch):
    monkeypatch.setattr("vula.commerce.service.get_order", AsyncMock(return_value={**ORDER, "status": "pending_payment"}))
    assert await order_slip.load("o-1") is None


def _client(monkeypatch, order=ORDER):
    from vula.api import menu_page
    monkeypatch.setattr("vula.commerce.service.get_order", AsyncMock(return_value=order))
    monkeypatch.setattr("vula.commerce.service.get_invoice_settings",
                        AsyncMock(return_value={"trading_as": "SeedleMe", "accent_color": "#2f6f5e"}))
    monkeypatch.setattr("vula.commerce.accounting.is_vat_registered", lambda tid: False)
    app = FastAPI()
    app.include_router(menu_page.router)
    return TestClient(app)


def test_the_receipt_page_and_picture(monkeypatch):
    c = _client(monkeypatch)
    tok = order_slip.token_for("o-1")
    page = c.get(f"/receipt/{tok}")
    assert page.status_code == 200 and "SEE-00012" in page.text and f'src="{tok}/slip.png"' in page.text
    img = c.get(f"/receipt/{tok}/slip.png")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert c.get("/receipt/not-a-token").status_code == 404


@pytest.mark.asyncio
async def test_a_paid_order_gets_the_slip_with_a_view_receipt_button(monkeypatch):
    from vula.api import yoco
    sent = []

    async def cta(creds, number, body, label, url, image_url=None):
        sent.append((number, label, url, image_url, body))
        return True
    monkeypatch.setattr("vula.api.whatsapp._send_wa_cta_url", cta)
    monkeypatch.setattr("vula.api.whatsapp._get_tenant_wa_creds", AsyncMock(return_value={"token": "t", "phone_id": "p"}))
    monkeypatch.setattr("vula.commerce.service.apply_order_stock", AsyncMock())
    monkeypatch.setattr("vula.commerce.service.get_order", AsyncMock(return_value=ORDER))
    monkeypatch.setattr(yoco.settings, "public_base_url", "https://api.example")
    monkeypatch.setattr(yoco, "_tenant_team", lambda tid: [])
    with patch("vula.commerce.order_workflow.get_order_settings", return_value={}), \
         patch("vula.commerce.order_workflow.dispatch_order", AsyncMock()):
        await yoco._notify_order_paid("seedleme", "SEE-00012", "o-1", "27645755210", "Ian", 674700)
    number, label, url, image, body = sent[0]
    tok = order_slip.token_for("o-1")
    assert (number, label) == ("27645755210", "View receipt")
    assert url == f"https://api.example/receipt/{tok}" and image == f"https://api.example/receipt/{tok}/slip.png"
    assert "SEE-00012" in body and "R6747.00" in body
