"""The shop and its receipts on the business's own address, and a faster browse (2026-10-13,
Ian: "the link shows Vula and it takes away from the client's brand"; "go ahead with the
receipt slip and speed work")."""
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vula.commerce import order_slip, web_basket

SEEDLEME = {"tenant_id": "seedleme", "domains": ["admin.seedleme.com", "shop.seedleme.com"]}


@pytest.fixture()
def branded(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, **k: SEEDLEME if tid == "seedleme" else {})
    monkeypatch.setattr("vula.api.tenants.tenant_for_host",
                        lambda h: "seedleme" if h.split(":")[0] == "shop.seedleme.com" else None)
    from config import settings
    monkeypatch.setattr(settings, "public_base_url", "https://vula-group-production.up.railway.app")
    import time
    monkeypatch.setitem(web_basket._READY, "shop.seedleme.com", (time.monotonic(), True))   # domain is live


def test_links_use_the_shops_own_address(branded):
    assert web_basket.shop_host("seedleme") == "shop.seedleme.com"
    assert web_basket.shop_url("seedleme", "#r-abc") == "https://shop.seedleme.com/#r-abc"
    links = order_slip.urls("o-1", "seedleme")
    assert links["page"].startswith("https://shop.seedleme.com/receipt/")
    assert links["image"].endswith("/slip.png") and "railway" not in links["image"]


def test_a_shop_without_its_own_address_keeps_vulas(branded):
    assert web_basket.shop_url("off-the-hook") == "https://vula-group-production.up.railway.app/menu/off-the-hook"


def test_the_shops_address_serves_its_shop_and_no_other_host_does(branded, monkeypatch):
    from vula.api import menu_page
    monkeypatch.setattr("vula.commerce.service.list_products", AsyncMock(return_value=[
        {"id": "bath", "category": "bath", "name": "Silverland Black Concrete Bath 175 x 80 x 50cm",
         "price_cents": 1589900, "image_url": "https://cdn.shopify.com/s/files/bath.jpg?v=1"}]))
    monkeypatch.setattr("vula.commerce.service.get_invoice_settings", AsyncMock(return_value={"trading_as": "SeedleMe"}))
    monkeypatch.setattr(web_basket, "wa_number", lambda tid: "27713128891")
    app = FastAPI()
    app.include_router(menu_page.router)
    c = TestClient(app)
    page = c.get("/", headers={"host": "shop.seedleme.com"})
    assert page.status_code == 200 and "Silverland Black" in page.text and "<title>SeedleMe" in page.text
    assert "bath.jpg?v=1&amp;width=900" in page.text or "bath.jpg?v=1&width=900" in page.text
    assert '"basket": "/menu/seedleme/basket"' in page.text
    assert c.get("/", headers={"host": "vula-group-production.up.railway.app"}).status_code == 404


def test_shopify_photos_are_fetched_phone_sized():
    assert web_basket.sized("https://cdn.shopify.com/s/files/1/a.jpg?v=17", 800) == \
        "https://cdn.shopify.com/s/files/1/a.jpg?v=17&width=800"
    assert web_basket.sized("https://cdn.shopify.com/a.jpg?width=4000&v=2", 600) == \
        "https://cdn.shopify.com/a.jpg?v=2&width=600"
    assert web_basket.sized("https://other.example/a.jpg", 800) == "https://other.example/a.jpg"
    assert web_basket.sized(None) is None


@pytest.mark.asyncio
async def test_a_browse_reads_the_catalogue_once_a_minute(monkeypatch):
    calls = []

    async def products(tid, category=None, **k):
        calls.append((tid, category))
        return [{"id": "x"}]
    monkeypatch.setattr("vula.commerce.service.list_products", products)
    web_basket._PRODUCTS.clear()
    for _ in range(4):
        assert await web_basket.shop_products("seedleme") == [{"id": "x"}]
    await web_basket.shop_products("seedleme", "basin")
    assert calls == [("seedleme", None), ("seedleme", "basin")]
    monkeypatch.setattr(web_basket, "PRODUCTS_TTL_S", 0.0)
    await web_basket.shop_products("seedleme")
    assert len(calls) == 3


def test_the_receipt_prints_out_full_screen_with_back_to_chat(branded, monkeypatch):
    from vula.api import menu_page
    order = {"id": "o-1", "tenant_id": "seedleme", "display_id": "SEE-00012", "status": "paid",
             "total_cents": 1589900, "subtotal_cents": 1589900, "updated_at": "2026-10-13T12:31:00+00:00",
             "commerce_order_items": [{"product_name": "Silverland Black Concrete Bath", "quantity": 1,
                                       "total_cents": 1589900}]}
    monkeypatch.setattr("vula.commerce.service.get_order", AsyncMock(return_value=order))
    monkeypatch.setattr("vula.commerce.service.get_invoice_settings", AsyncMock(return_value={"trading_as": "SeedleMe"}))
    monkeypatch.setattr("vula.commerce.accounting.is_vat_registered", lambda tid: False)
    monkeypatch.setattr(web_basket, "wa_number", lambda tid: "27713128891")
    app = FastAPI()
    app.include_router(menu_page.router)
    html = TestClient(app).get(f"/receipt/{order_slip.token_for('o-1')}").text
    assert 'class="printer"' in html and "@keyframes feed" in html
    assert "Replay" not in html and "Save slip" not in html        # the slip is already in the chat
    assert 'href="https://wa.me/27713128891">Back to chat' in html
    assert 'href="https://shop.seedleme.com/">Shop again' in html
    assert "Vula" not in html


@pytest.mark.asyncio
async def test_until_the_domain_works_links_stay_on_vulas_address_and_switch_by_themselves(branded, monkeypatch):
    """Ian, 13 Oct: no extra onboarding step — the link is hidden behind the button anyway, and
    Vula moves to the shop's own address by itself once it's live."""
    web_basket._READY.pop("shop.seedleme.com", None)
    checks = []

    async def check(host, tid):
        checks.append((host, tid))
        return True
    monkeypatch.setattr(web_basket, "check_shop_host", check)
    assert web_basket.shop_url("seedleme") == "https://vula-group-production.up.railway.app/menu/seedleme"
    import asyncio
    await asyncio.sleep(0)
    assert checks == [("shop.seedleme.com", "seedleme")]


@pytest.mark.asyncio
async def test_a_domain_is_live_when_it_answers_as_that_shop(monkeypatch):
    from unittest.mock import MagicMock, patch
    def client_for(status, body):
        resp = MagicMock(status_code=status)
        resp.json.return_value = body
        c = AsyncMock()
        c.__aenter__ = AsyncMock(return_value=c)
        c.__aexit__ = AsyncMock(return_value=None)
        c.get = AsyncMock(return_value=resp)
        return c
    with patch("httpx.AsyncClient", return_value=client_for(200, {"shop": "seedleme"})):
        assert await web_basket.check_shop_host("shop.seedleme.com", "seedleme") is True
    with patch("httpx.AsyncClient", return_value=client_for(200, {"shop": None})):
        assert await web_basket.check_shop_host("shop.seedleme.com", "seedleme") is False
    assert web_basket._READY["shop.seedleme.com"][1] is False


def test_the_ping_names_the_shop_for_its_own_host_only(branded):
    from vula.api import menu_page
    app = FastAPI()
    app.include_router(menu_page.router)
    c = TestClient(app)
    assert c.get("/shop-ping", headers={"host": "shop.seedleme.com"}).json() == {"shop": "seedleme"}
    assert c.get("/shop-ping", headers={"host": "example.com"}).json() == {"shop": None}
