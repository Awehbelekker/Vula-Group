"""The shop's web page takes orders and hands them to WhatsApp (2026-10-13, SeedleMe — Ian:
"the product have long description summary and no selection to order or add to cart and push
back to order in WhatsApp"), and WhatsApp shows photo cards instead of a text-only list
("why can't it be more visual?")."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vula.api import menu_page, whatsapp
from vula.commerce import web_basket

LONG = ("The Aquatina Blue Concrete Basin is a compact, rectangular vessel basin suited to smaller "
        "bathrooms, cloakrooms, or outdoor guest spaces. Its striking blue tone is oxide pigment "
        "mixed directly into the concrete during casting — a permanent part of the basin.")


def _products():
    rows = [{"id": f"aq-{c.lower()}", "slug": f"aq-{c.lower()}", "category": "basin",
             "name": f"Aquatina {c} Concrete Basin 47 x 23.5 x 12cm", "price_cents": 209900,
             "image_url": f"https://cdn/aq-{c.lower()}.jpg", "description": LONG}
            for c in ("Blue", "Burnt Orange", "Charcoal")]
    rows.append({"id": "bath", "slug": "bath", "category": "bath", "price_cents": 2700000,
                 "name": "Amazon Green Bespoke Bath 175 x 80 x 50cm", "image_url": "https://cdn/bath.jpg",
                 "description": "A bath."})
    return rows


# ── The page ──────────────────────────────────────────────────────────────────────────────

def test_a_long_blurb_is_cut_to_its_first_sentence():
    assert menu_page.short_description(LONG) == ("The Aquatina Blue Concrete Basin is a compact, "
                                                 "rectangular vessel basin suited to smaller bathrooms, "
                                                 "cloakrooms, or outdoor guest spaces.")
    assert menu_page.short_description("Short.") == "Short."


def test_one_card_per_range_with_its_colours_each_with_its_own_photo_and_price():
    data = menu_page.shop_data(_products())
    aq, bath = data
    assert aq["title"] == "Aquatina Concrete Basin 47 x 23.5 x 12cm"
    assert [(o["colour"], o["img"], o["cents"]) for o in aq["opts"]] == [
        ("Blue", "https://cdn/aq-blue.jpg", 209900),
        ("Burnt Orange", "https://cdn/aq-burnt orange.jpg", 209900),
        ("Charcoal", "https://cdn/aq-charcoal.jpg", 209900)]
    assert aq["opts"][1]["swatch"] == "#c8662b" and len(aq["short"]) < len(aq["full"])
    assert bath["title"] == "Amazon Green Bespoke Bath 175 x 80 x 50cm" and len(bath["opts"]) == 1


def _client(monkeypatch, number="27713128891"):
    app = FastAPI()
    app.include_router(menu_page.router)
    monkeypatch.setattr("vula.commerce.service.list_products", AsyncMock(return_value=_products()))
    monkeypatch.setattr("vula.commerce.service.get_invoice_settings",
                        AsyncMock(return_value={"trading_as": "SeedleMe", "accent_color": "#2f6f5e"}))
    monkeypatch.setattr(web_basket, "wa_number", lambda tid: number)
    menu_page._RECENT.clear()
    return TestClient(app)


def test_the_page_is_a_shop_with_a_basket_and_no_full_blurbs(monkeypatch):
    html = _client(monkeypatch).get("/menu/seedleme").text
    assert "Add to basket" in html and "Order on WhatsApp" in html and 'type="search"' in html
    data = json.loads(html.split('<script id="shop" type="application/json">')[1].split("</script>")[0])
    assert data["canOrder"] is True and len(data["ranges"]) == 2
    assert data["cats"] == {"basin": "Basin", "bath": "Bath"}


def test_a_shop_without_a_whatsapp_number_shows_no_basket(monkeypatch):
    html = _client(monkeypatch, number="").get("/menu/seedleme").text
    data = json.loads(html.split('<script id="shop" type="application/json">')[1].split("</script>")[0])
    assert data["canOrder"] is False


def test_sending_the_basket_returns_the_whatsapp_link_that_orders_it(monkeypatch):
    create = AsyncMock(return_value={"code": "K7Q2MX", "lines": 2, "subtotal_cents": 629700, "skipped": []})
    monkeypatch.setattr(web_basket, "create", create)
    res = _client(monkeypatch).post("/menu/seedleme/basket",
                                    json={"items": [{"product_id": "aq-blue", "qty": 2}]})
    assert res.status_code == 200
    assert res.json()["wa_url"] == ("https://wa.me/27713128891?text=Hi%21%20I%27d%20like%20to%20order"
                                    "%20basket%20K7Q2MX")
    assert create.call_args.args == ("seedleme", [{"product_id": "aq-blue", "qty": 2}])


def test_the_basket_endpoint_is_throttled(monkeypatch):
    monkeypatch.setattr(web_basket, "create", AsyncMock(return_value={"code": "K7Q2MX"}))
    c = _client(monkeypatch)
    codes = [c.post("/menu/seedleme/basket", json={"items": []}).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]


# ── The basket ────────────────────────────────────────────────────────────────────────────

def test_items_from_the_browser_are_merged_whole_and_capped():
    assert web_basket.clean_items([{"product_id": "a", "qty": 2}, {"product_id": "a", "qty": 1},
                                   {"product_id": "b", "qty": 500}, {"product_id": "c", "qty": 0},
                                   {"product_id": "", "qty": 1}, "junk", {"product_id": "d", "qty": "x"}]) == [
        {"product_id": "a", "qty": 3}, {"product_id": "b", "qty": 99}]
    assert web_basket.clean_items(None) == []


@pytest.mark.asyncio
async def test_a_basket_is_priced_from_the_shop_and_drops_what_isnt_sold(monkeypatch):
    added = []
    monkeypatch.setattr("vula.commerce.service.list_products", AsyncMock(return_value=_products()))
    monkeypatch.setattr("vula.commerce.service.get_or_create_cart",
                        AsyncMock(side_effect=lambda tid, sid, phone=None: {"id": "c1", "sid": sid}))

    async def add(tid, cart_id, pid, qty, variant_id=None):
        added.append((cart_id, pid, qty))
    monkeypatch.setattr("vula.commerce.service.add_to_cart", add)
    monkeypatch.setattr(web_basket, "new_code", lambda: "K7Q2MX")
    res = await web_basket.create("seedleme", [{"product_id": "aq-blue", "qty": 2},
                                               {"product_id": "gone", "qty": 1, "price_cents": 1}])
    assert res == {"code": "K7Q2MX", "lines": 1, "subtotal_cents": 419800, "skipped": ["gone"]}
    assert added == [("c1", "aq-blue", 2.0)]


def test_the_order_message_is_recognised_and_codes_are_unambiguous():
    m = web_basket.BASKET_RE.search(web_basket.order_message("K7Q2MX"))
    assert m and m.group(1) == "K7Q2MX"
    assert all(ch not in web_basket.new_code() for ch in "01IO" for _ in range(20))


@pytest.mark.asyncio
async def test_claiming_moves_the_basket_into_the_whatsapp_cart_once(monkeypatch):
    monkeypatch.setattr(web_basket, "_find", lambda tid, code: {
        "id": "web-cart", "commerce_cart_items": [{"product_id": "aq-blue", "variant_id": None, "quantity": 2}]})
    monkeypatch.setattr("vula.commerce.service.get_or_create_cart",
                        AsyncMock(return_value={"id": "phone-cart"}))
    add, clear = AsyncMock(), AsyncMock()
    monkeypatch.setattr("vula.commerce.service.add_to_cart", add)
    monkeypatch.setattr("vula.commerce.service.clear_cart", clear)
    assert await web_basket.claim("seedleme", "27645755210", "K7Q2MX") == 1
    add.assert_awaited_once_with("seedleme", "phone-cart", "aq-blue", 2.0, variant_id=None)
    clear.assert_awaited_once_with("web-cart")
    monkeypatch.setattr(web_basket, "_find", lambda tid, code: None)
    assert await web_basket.claim("seedleme", "27645755210", "K7Q2MX") is None


# ── WhatsApp ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def sent(monkeypatch):
    out = {"buttons": [], "text": [], "carousel": []}

    async def creds(tid):
        return {"token": "t", "phone_id": "p"}

    async def buttons(creds, number, body, btns, image_url=None):
        out["buttons"].append((body, [b["id"] for b in btns]))
        return True

    async def reply(phone, text, tenant_id=None, **k):
        out["text"].append(text)

    monkeypatch.setattr(whatsapp, "_resolve_wa", creds)
    monkeypatch.setattr(whatsapp, "_send_wa_buttons", buttons)
    monkeypatch.setattr(whatsapp, "_send_reply", reply)
    return out


@pytest.mark.asyncio
async def test_the_basket_message_shows_the_cart_with_checkout(sent, monkeypatch):
    monkeypatch.setattr(web_basket, "claim", AsyncMock(return_value=2))
    monkeypatch.setattr("vula.commerce.service.get_or_create_cart", AsyncMock(return_value={
        "commerce_cart_items": [{"quantity": 2, "unit_price_cents": 209900,
                                 "commerce_products": {"name": "Aquatina Blue Concrete Basin 47 x 23.5 x 12cm"}}]}))
    assert await whatsapp._maybe_claim_web_basket("27645755210", "Hi! I'd like to order basket K7Q2MX", "seedleme")
    body, ids = sent["buttons"][0]
    assert ids == ["cart_checkout", "cart_more"]
    assert "Got your basket — 2 items" in body and "2 × Aquatina Blue" in body and "R4,198.00" in body


@pytest.mark.asyncio
async def test_an_unknown_basket_says_so_and_ordinary_messages_pass(sent, monkeypatch):
    monkeypatch.setattr(web_basket, "claim", AsyncMock(return_value=None))
    monkeypatch.setattr(whatsapp.settings, "public_base_url", "https://api.example")
    buttons = []

    async def cta(creds, number, body, label, url, image_url=None):
        buttons.append((body, label, url))
        return True
    monkeypatch.setattr(whatsapp, "_send_wa_cta_url", cta)
    assert await whatsapp._maybe_claim_web_basket("27645755210", "order basket ZZZZZZ", "seedleme")
    body, label, url = buttons[0]
    assert "couldn't find that basket" in body and "https://" not in body          # the link sits behind the button
    assert (label, url) == ("View products", "https://api.example/menu/seedleme")
    assert not await whatsapp._maybe_claim_web_basket("27645755210", "Do you have round basins?", "seedleme")


def _http(*statuses):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(side_effect=[MagicMock(status_code=s, text="err") for s in statuses])
    return client


@pytest.mark.asyncio
async def test_ranges_go_out_as_photo_cards_that_open_the_colours(monkeypatch):
    from vula.commerce import product_families as pf
    monkeypatch.setattr(whatsapp.settings, "public_base_url", "https://api.example")
    ranges = pf.group(_products())
    client = _http(200)
    with patch("vula.api.whatsapp.httpx.AsyncClient", return_value=client):
        assert await whatsapp._send_range_cards("27645755210", "seedleme", {"token": "t", "phone_id": "p"},
                                                "Basins", ranges)
    payload = client.post.call_args.kwargs["json"]["interactive"]
    assert payload["type"] == "carousel" and len(payload["action"]["cards"]) == 2
    card = payload["action"]["cards"][0]
    assert card["header"] == {"type": "image", "image": {"link": "https://cdn/aq-blue.jpg"}}
    assert card["action"]["buttons"][0]["quick_reply"] == {"id": f"rng_{ranges[0]['id']}", "title": "Choose colour"}
    assert "3 colours · R2,099.00" in card["body"]["text"]


@pytest.mark.asyncio
async def test_when_whatsapp_refuses_reply_buttons_the_cards_link_to_the_shop(monkeypatch):
    from vula.commerce import product_families as pf
    monkeypatch.setattr(whatsapp.settings, "public_base_url", "https://api.example")
    ranges = pf.group(_products())
    client = _http(400, 200)
    with patch("vula.api.whatsapp.httpx.AsyncClient", return_value=client):
        assert await whatsapp._send_range_cards("27645755210", "seedleme", {"token": "t", "phone_id": "p"},
                                                "Basins", ranges)
    card = client.post.call_args.kwargs["json"]["interactive"]["action"]["cards"][0]
    assert card["action"]["parameters"]["url"] == f"https://api.example/menu/seedleme#r-{ranges[0]['id']}"


@pytest.mark.asyncio
async def test_no_cards_when_a_range_has_no_photo_or_whatsapp_takes_neither(monkeypatch):
    from vula.commerce import product_families as pf
    ranges = pf.group(_products())
    ranges[1] = {**ranges[1], "image_url": None}
    assert not await whatsapp._send_range_cards("1", "seedleme", {"token": "t", "phone_id": "p"}, "x", ranges)
    client = _http(400, 400)
    monkeypatch.setattr(whatsapp.settings, "public_base_url", "https://api.example")
    with patch("vula.api.whatsapp.httpx.AsyncClient", return_value=client):
        assert not await whatsapp._send_range_cards("1", "seedleme", {"token": "t", "phone_id": "p"}, "x",
                                                    pf.group(_products()))


# ── One standard shop for every business that sells (2026-10-13, Ian: "Off the Hook would
# benefit of the same process — should we not have a standard?") ─────────────────────────────

OTH = [{"id": "hake", "slug": "hake", "category": "fresh_fish", "sold_by": "kg", "price_cents": 16000,
        "name": "Hake Fillets", "image_url": "https://cdn/hake.jpg", "images": ["https://cdn/hake-2.jpg"]},
       {"id": "prawns", "slug": "prawns", "category": "frozen_seafood", "sold_by": "pack",
        "price_cents": 26000, "name": "Argentinian Pink Prawns (whole) — 1kg", "image_url": "https://cdn/p.jpg"}]


def test_the_shop_is_on_by_switch_or_when_ranges_come_in_colours(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["products", "orders"])
    assert web_basket.shop_enabled("off-the-hook", OTH) is False            # until it's switched on
    assert web_basket.shop_enabled("seedleme", _products()) is True         # colour ranges
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["products", "wa_shop"])
    assert web_basket.shop_enabled("off-the-hook", OTH) is True


def test_new_food_and_retail_shops_get_it_as_standard():
    from vula.api.tenants import BUSINESS_TYPES, MODULES
    assert "wa_shop" in MODULES
    assert "wa_shop" in BUSINESS_TYPES["food"]["modules"] and "wa_shop" in BUSINESS_TYPES["retail"]["modules"]


def test_fish_is_bought_by_the_half_kilo_and_packs_whole():
    hake, prawns = OTH
    assert [web_basket.norm_qty(hake, q) for q in (0.2, 0.5, 1.3, 2, "x", -1)] == [0.5, 0.5, 1.5, 2.0, 0.0, 0.0]
    assert [web_basket.norm_qty(prawns, q) for q in (1, 2.7, 0.5)] == [1.0, 2.0, 0.0]
    assert (web_basket.qty_label(hake, 1.5), web_basket.qty_label(prawns, 2.0)) == ("1.5 kg", "2")


def test_the_page_shows_every_photo_and_per_kg_prices():
    hake, prawns = menu_page.shop_data(OTH)
    assert hake["opts"][0]["imgs"] == ["https://cdn/hake.jpg", "https://cdn/hake-2.jpg"]
    assert hake["opts"][0]["kg"] is True and prawns["opts"][0]["kg"] is False


@pytest.fixture()
def oth_shop(sent, monkeypatch):
    sent["cta"], sent["lists"], sent["cart"] = [], [], []

    async def products(tid, category=None, **k):
        return [p for p in OTH if category in (None, p["category"])]

    async def cta(creds, number, body, label, url, image_url=None):
        sent["cta"].append((label, url, image_url))
        return True

    async def lst(creds, number, header, body, footer, button, sections):
        sent["lists"].append(body)
        return True

    async def add(tid, cart_id, pid, qty, variant_id=None):
        sent["cart"].append((pid, qty))
    monkeypatch.setattr("vula.commerce.service.list_products", products)
    monkeypatch.setattr("vula.commerce.service.get_invoice_settings", AsyncMock(return_value={"trading_as": "Off the Hook"}))
    monkeypatch.setattr("vula.commerce.service.get_or_create_cart", AsyncMock(return_value={"id": "c", "commerce_cart_items": []}))
    monkeypatch.setattr("vula.commerce.service.add_to_cart", add)
    monkeypatch.setattr(whatsapp, "_send_wa_cta_url", cta)
    monkeypatch.setattr(whatsapp, "_get_tenant_wa_creds", AsyncMock(return_value={"token": "t", "phone_id": "p"}))
    monkeypatch.setattr(whatsapp, "_send_wa_list", lst)
    monkeypatch.setattr(whatsapp, "_meta_catalog_id", lambda tid: "")
    monkeypatch.setattr(whatsapp.settings, "public_base_url", "https://api.example")
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["products", "orders", "wa_shop"])
    return sent


@pytest.mark.asyncio
async def test_the_welcome_is_one_photo_message_whose_button_opens_the_shop(oth_shop):
    await whatsapp._send_commerce_welcome("27820000000", "off-the-hook")
    assert oth_shop["cta"] == [("View products", "https://api.example/menu/off-the-hook", "https://cdn/hake.jpg")]
    assert oth_shop["lists"] == []          # one message: the button opens the shop, no menu to tap through


@pytest.mark.asyncio
async def test_with_the_shop_off_the_welcome_is_as_before(oth_shop, monkeypatch):
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda tid: ["products", "orders"])
    await whatsapp._send_commerce_welcome("27820000000", "off-the-hook")
    # The list as before, but no bare link to Vula's server address (2026-10-15): that only
    # shows once the shop has its own address.
    assert oth_shop["cta"] == [] and oth_shop["lists"]
    assert "https://api.example" not in oth_shop["lists"][0]


@pytest.mark.asyncio
async def test_fish_gets_kilo_buttons_and_half_a_kilo_goes_in_the_cart(oth_shop):
    await whatsapp._handle_commerce_interactive("27820000000", "col_hake", "Hake", "m1", "off-the-hook")
    body, ids = oth_shop["buttons"][0]
    assert ids == ["qty_hake_0.5", "qty_hake_1", "qty_hake_2"] and "R160.00 per kg" in body
    await whatsapp._handle_commerce_interactive("27820000000", "qty_hake_0.5", "0.5 kg", "m2", "off-the-hook")
    assert oth_shop["cart"] == [("hake", 0.5)]
    assert "Added 0.5 kg × Hake Fillets" in oth_shop["buttons"][1][0]
