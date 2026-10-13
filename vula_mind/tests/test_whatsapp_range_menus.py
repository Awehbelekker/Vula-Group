"""Tap-to-order on WhatsApp for a shop whose ranges come in colours (2026-10-13, SeedleMe):
category → range (photo, colours) → how many → cart → Checkout / Keep shopping. Every step is an
exact id from the tap — no model picks the product or the price."""
import pytest

from vula.api import whatsapp
from vula.commerce import product_families as pf

COLOURS = ["Black", "Burnt Orange", "Charcoal", "Choc Plum", "Green", "Grey", "Ivory", "Sandstone",
           "Terracotta", "Nguni Pattern Grey", "Bright Blue"]


def _catalogue():
    rows = [{"id": f"cb-{i}", "slug": f"cb-{i}", "name": f"Countertop Butler Concrete Basin – {c} – 31 x 31 x 12cm",
             "price_cents": 149900, "category": "basin", "image_url": f"https://cdn/cb{i}.jpg"}
            for i, c in enumerate(COLOURS)]
    rows += [{"id": f"r{i}", "slug": f"r{i}", "name": f"Range {chr(65 + i)} Concrete Sink – Grey – {40 + i} x 12cm",
              "price_cents": 159900, "category": "basin", "image_url": None} for i in range(10)]
    rows.append({"id": "bath", "slug": "bath", "name": "Amazon Green Bespoke Bath 175 x 80 x 50cm",
                 "price_cents": 2700000, "category": "basin", "image_url": "https://cdn/bath.jpg"})
    return rows


@pytest.fixture()
def wa(monkeypatch):
    sent = {"lists": [], "buttons": [], "images": [], "text": [], "cart": [], "assistant": []}
    cart = {"id": "cart-1", "commerce_cart_items": []}

    async def creds(tid):
        return {"token": "t", "phone_id": "p"}

    async def lst(creds, number, header, body, footer, button, sections):
        sent["lists"].append({"header": header, "body": body, "rows": sections[0]["rows"]})
        return True

    async def buttons(creds, number, body, btns):
        sent["buttons"].append({"body": body, "ids": [b["id"] for b in btns]})
        return True

    async def image(creds, number, url, caption=""):
        sent["images"].append((url, caption))
        return True

    async def reply(phone, text, tenant_id=None, **k):
        sent["text"].append(text)

    async def products(tid, category=None, **k):
        return [p for p in _catalogue() if category in (None, p["category"])]

    async def get_cart(tid, sid, phone=None):
        return cart

    async def add_to_cart(tid, cart_id, pid, qty, variant_id=None):
        p = next(x for x in _catalogue() if x["id"] == pid)
        sent["cart"].append((pid, qty))
        cart["commerce_cart_items"].append({"quantity": qty, "unit_price_cents": p["price_cents"],
                                            "commerce_products": {"name": p["name"]}})

    async def assistant(phone, text, msg_id, tid):
        sent["assistant"].append(text)

    monkeypatch.setattr(whatsapp, "_resolve_wa", creds)
    monkeypatch.setattr(whatsapp, "_send_wa_list", lst)
    monkeypatch.setattr(whatsapp, "_send_wa_buttons", buttons)
    monkeypatch.setattr(whatsapp, "_send_wa_image", image)
    monkeypatch.setattr(whatsapp, "_send_reply", reply)
    monkeypatch.setattr(whatsapp, "_handle_commerce_message", assistant)
    monkeypatch.setattr("vula.commerce.service.list_products", products)
    monkeypatch.setattr("vula.commerce.service.get_or_create_cart", get_cart)
    monkeypatch.setattr("vula.commerce.service.add_to_cart", add_to_cart)
    return sent


def test_a_range_row_is_its_name_and_the_size_goes_underneath():
    assert whatsapp._range_row_text("Round Concave Concrete Basin – 42 x 14cm") == ("Round Concave Basin", "42 x 14cm")
    assert whatsapp._range_row_text("Gaia's Ark 53 x 30 x 11.5cm") == ("Gaia's Ark", "53 x 30 x 11.5cm")


@pytest.mark.asyncio
async def test_a_category_lists_ranges_with_colour_counts_nine_a_page(wa):
    await whatsapp._handle_commerce_interactive("27645755210", "cat_basin", "Basin", "m1", "seedleme")
    rows = wa["lists"][0]["rows"]
    assert rows[0]["title"] == "Countertop Butler Basin"
    assert rows[0]["description"] == "31 x 31 x 12cm · 11 colours · R1,499.00"
    assert len(rows) == 10 and rows[-1] == {"id": "catp_basin:1", "title": "More…",
                                            "description": "3 more in this category"}
    await whatsapp._handle_commerce_interactive("27645755210", "catp_basin:1", "More…", "m2", "seedleme")
    assert [r["title"] for r in wa["lists"][1]["rows"]][-1] == "Amazon Green Bath"   # a range of one


@pytest.mark.asyncio
async def test_a_range_shows_its_photo_then_its_colours(wa):
    rid = pf.group(_catalogue())[0]["id"]
    await whatsapp._handle_commerce_interactive("27645755210", f"rng_{rid}", "Countertop Butler Basin", "m1", "seedleme")
    assert wa["images"] == [("https://cdn/cb0.jpg",
                             "Countertop Butler Concrete Basin – 31 x 31 x 12cm\nR1,499.00 · 11 colours")]
    rows = wa["lists"][0]["rows"]
    assert rows[0] == {"id": "col_cb-0", "title": "Black", "description": "R1,499.00"}
    assert rows[-1] == {"id": f"rngp_{rid}:1", "title": "More colours…", "description": "2 more"}
    await whatsapp._handle_commerce_interactive("27645755210", f"rngp_{rid}:1", "More colours…", "m2", "seedleme")
    assert len(wa["images"]) == 1                                   # the photo isn't sent again
    assert [r["title"] for r in wa["lists"][1]["rows"]] == ["Sandstone", "Terracotta"]


@pytest.mark.asyncio
async def test_colour_then_quantity_puts_exactly_that_in_the_cart(wa):
    await whatsapp._handle_commerce_interactive("27645755210", "col_cb-2", "Charcoal", "m1", "seedleme")
    assert wa["buttons"][0]["ids"] == ["qty_cb-2_1", "qty_cb-2_2", "qty_cb-2_3"]
    assert "Countertop Butler Concrete Basin – Charcoal – 31 x 31 x 12cm" in wa["buttons"][0]["body"]
    await whatsapp._handle_commerce_interactive("27645755210", "qty_cb-2_2", "2", "m2", "seedleme")
    assert wa["cart"] == [("cb-2", 2.0)]
    cart = wa["buttons"][1]
    assert cart["ids"] == ["cart_checkout", "cart_more"]
    assert "2 × Countertop Butler Concrete Basin – Charcoal – 31 x 31 x 12cm — R2,998.00" in cart["body"]


@pytest.mark.asyncio
async def test_checkout_goes_to_the_assistants_checkout(wa):
    await whatsapp._handle_commerce_interactive("27645755210", "cart_checkout", "Checkout", "m1", "seedleme")
    assert wa["assistant"] == ["I'd like to check out now"]


@pytest.mark.asyncio
async def test_a_shop_without_colour_ranges_keeps_its_product_list(wa, monkeypatch):
    async def fish(tid, category=None, **k):
        return [{"id": "f1", "slug": "hake", "name": "Hake Fillets 1kg", "price_cents": 22000, "category": "fresh_fish"}]
    monkeypatch.setattr("vula.commerce.service.list_products", fish)
    await whatsapp._handle_commerce_interactive("27820000000", "cat_fresh_fish", "Fresh Fish", "m1", "off-the-hook")
    assert wa["lists"][0]["rows"][0]["id"] == "prod_f1"
