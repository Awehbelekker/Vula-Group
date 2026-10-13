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
    sent = {"lists": [], "buttons": [], "images": [], "text": [], "cart": [], "assistant": [], "product_list": []}
    cart = {"id": "cart-1", "commerce_cart_items": []}

    async def creds(tid):
        return {"token": "t", "phone_id": "p"}

    async def lst(creds, number, header, body, footer, button, sections):
        sent["lists"].append({"header": header, "body": body, "rows": sections[0]["rows"]})
        return True

    async def buttons(creds, number, body, btns, image_url=None):
        sent["buttons"].append({"body": body, "ids": [b["id"] for b in btns], "image": image_url})
        return True

    async def product_list(creds, number, catalog_id, header, body, sections):
        sent["product_list"].append({"catalog": catalog_id, "sections": sections})
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
    monkeypatch.setattr(whatsapp, "_send_wa_product_list", product_list)
    monkeypatch.setattr(whatsapp, "_meta_catalog_id", lambda tid: "")
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
    assert wa["buttons"][0]["image"] == "https://cdn/cb2.jpg"                      # that colour's own photo
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


# ── Typing to search (2026-10-13, Ian: "can we have a search option?") ─────────────────────────

async def _search(text, session=None):
    return await whatsapp._maybe_send_search_results("27645755210", text, "seedleme", session)


@pytest.mark.asyncio
async def test_a_search_with_one_match_opens_that_range(wa):
    assert await _search("Do you have countertop butler basins?") is True
    assert wa["images"][0][0] == "https://cdn/cb0.jpg" and wa["lists"][0]["rows"][0]["title"] == "Black"


@pytest.mark.asyncio
async def test_a_colour_search_opens_the_whole_range(wa):
    assert await _search("ivory countertop butler") is True
    assert len(wa["lists"][0]["rows"]) == 10                     # 9 colours + "More colours…"


@pytest.mark.asyncio
async def test_many_matches_come_back_as_a_tappable_list(wa):
    assert await _search("grey concrete sink") is True
    lst = wa["lists"][0]
    assert len(lst["rows"]) == 9 and all(r["id"].startswith("rng_") for r in lst["rows"])
    assert "Showing 9 of 10" in lst["body"]


@pytest.mark.asyncio
async def test_orders_delivery_questions_and_no_matches_go_to_the_assistant(wa):
    for text in ("How long does delivery take?", "I'd like 2 of those", "checkout please",
                 "5 Korhaan Close, Joostenbergvlakte", "do you have shower doors"):
        assert await _search(text) is False, text
    assert wa["lists"] == [] and wa["images"] == []


@pytest.mark.asyncio
async def test_an_answer_to_the_assistants_question_is_not_a_search(wa, monkeypatch):
    async def recent(tid, sid, limit=1, max_age_hours=None):
        return [{"role": "assistant", "content": "Lovely — which colour would you like?"}]
    monkeypatch.setattr("vula.commerce.service.get_recent_messages", recent)
    assert await _search("Charcoal", session={"id": "s1"}) is False


@pytest.mark.asyncio
async def test_a_shop_without_colour_ranges_is_left_to_the_assistant(wa, monkeypatch):
    async def fish(tid, category=None, **k):
        return [{"id": "f1", "slug": "hake", "name": "Hake Fillets 1kg", "price_cents": 22000, "category": "fresh_fish"}]
    monkeypatch.setattr("vula.commerce.service.list_products", fish)
    assert await whatsapp._maybe_send_search_results("27820000000", "hake", "off-the-hook") is False


@pytest.mark.asyncio
async def test_with_whatsapps_catalogue_connected_results_are_product_cards(wa, monkeypatch):
    monkeypatch.setattr(whatsapp, "_meta_catalog_id", lambda tid: "cat123")
    assert await _search("grey concrete sink") is True
    pl = wa["product_list"][0]
    assert pl["catalog"] == "cat123" and wa["lists"] == []
    assert [len(sec["product_items"]) for sec in pl["sections"]] == [10]
    assert pl["sections"][0]["product_items"][0] == {"product_retailer_id": "r0"}
