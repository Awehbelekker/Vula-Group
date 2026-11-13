"""Choices that don't change the price (migration 267): asked for, matched to the product's own
values, kept with the cart and order line, shown wherever the line is.

13 Nov 2026 (Kelp Gear, board bags made to order): size is a variant with its own price; colour,
trim, nose and tail style are choices on the same price. Before this, an order could only say
"9'6"" — the colour the customer asked for went nowhere."""
from unittest.mock import AsyncMock, patch

import pytest

from core.skills.commerce_assistant import CommerceAssistantSkill
from tests.fake_supabase import FakeSupabase
from vula.commerce import service

T = "kelp-boardbags"
CHOICES = [
    {"name": "Primary Colour", "values": ["Olive Green", "Navy Blue", "Charcoal", "Black"]},
    {"name": "Trim Colour", "values": ["Beige", "Black"]},
    {"name": "Nose Style", "values": ["Round", "Pinched Generous"]},
    {"name": "Tail Style", "values": ["Standard Short Tail", "Wide Fish Tail", "Longboard Thruster no fin slot"]},
]
BAG = {"id": "p-bag", "tenant_id": T, "name": "Kelp Classic Day Bag", "slug": "kelpclassicdaybag",
       "price_cents": 120_000, "choices": CHOICES, "options": None}


# ── matching what the customer said ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("said, expect", [
    ("olive green, black trim, round nose, fish tail",
     {"Primary Colour": "Olive Green", "Trim Colour": "Black", "Nose Style": "Round", "Tail Style": "Wide Fish Tail"}),
    ("navy with beige trim, pinched nose and a fish tail",
     {"Primary Colour": "Navy Blue", "Trim Colour": "Beige", "Nose Style": "Pinched Generous", "Tail Style": "Wide Fish Tail"}),
    ("Primary Colour: Black, Trim Colour: Beige, Nose Style: Round, Tail Style: Standard Short Tail",
     {"Primary Colour": "Black", "Trim Colour": "Beige", "Nose Style": "Round", "Tail Style": "Standard Short Tail"}),
    ({"colour": "olive green", "trim": "black", "nose": "round", "tail": "wide fish tail"},
     {"Primary Colour": "Olive Green", "Trim Colour": "Black", "Nose Style": "Round", "Tail Style": "Wide Fish Tail"}),
])
def test_what_the_customer_said_is_matched_to_the_products_own_values(said, expect):
    assert service.resolve_choices(BAG, said) == (expect, [])


def test_a_word_that_fits_two_choices_is_asked_about_not_guessed():
    chosen, missing = service.resolve_choices(BAG, "black")
    assert chosen == {} and missing == ["Primary Colour", "Trim Colour", "Nose Style", "Tail Style"]


def test_a_value_the_product_doesnt_have_is_never_taken():
    chosen, missing = service.resolve_choices(BAG, "hot pink, square nose")
    assert chosen == {} and "Primary Colour" in missing and "Nose Style" in missing


def test_what_was_said_so_far_is_kept_and_only_the_rest_is_missing():
    chosen, missing = service.resolve_choices(BAG, "9'6 olive green")
    assert chosen == {"Primary Colour": "Olive Green"}
    assert missing == ["Trim Colour", "Nose Style", "Tail Style"]


def test_a_product_without_choices_needs_none():
    assert service.resolve_choices({"name": "Hat"}, "anything") == ({}, [])


def test_choices_are_cleaned_before_they_are_saved():
    got = service.clean_product_choices([
        {"name": "  Colour ", "values": ["Red", " red ", "Red", "", "Blue"]},
        {"name": "colour", "values": ["dup name"]},
        {"name": "", "values": ["no name"]}, {"name": "Empty", "values": []}, "junk"])
    assert got == [{"name": "Colour", "values": ["Red", "red", "Blue"]}]


# ── cart and order lines ────────────────────────────────────────────────────────────────────

def _db():
    return FakeSupabase({"commerce_products": [dict(BAG)], "commerce_cart_items": [],
                         "commerce_product_variants": []})


@pytest.mark.asyncio
async def test_two_bags_in_different_colours_are_two_lines_and_the_same_one_adds_up():
    db = _db()
    olive = {"Primary Colour": "Olive Green", "Trim Colour": "Black"}
    navy = {"Primary Colour": "Navy Blue", "Trim Colour": "Black"}
    with patch.object(service, "_client", return_value=db):
        await service.add_to_cart(T, "cart-1", "p-bag", 1, choices=olive)
        await service.add_to_cart(T, "cart-1", "p-bag", 1, choices=navy)
        await service.add_to_cart(T, "cart-1", "p-bag", 2, choices=olive)
    rows = sorted(db.rows["commerce_cart_items"], key=lambda r: r["choices"]["Primary Colour"])
    assert [(r["choices"]["Primary Colour"], r["quantity"]) for r in rows] == [("Navy Blue", 1), ("Olive Green", 3)]


@pytest.mark.asyncio
async def test_a_line_without_choices_writes_no_choices_field():
    """Before migration 267 the column isn't there: a shop without choices must not send it."""
    db = _db()
    with patch.object(service, "_client", return_value=db):
        await service.add_to_cart(T, "cart-1", "p-bag", 1)
    assert "choices" not in db.rows["commerce_cart_items"][0]


def test_the_order_line_names_the_size_and_every_choice():
    item = {"commerce_products": {"name": "Kelp Classic Day Bag"},
            "commerce_product_variants": {"option_values": {"Board Size": "9'6\""}},
            "choices": {"Primary Colour": "Olive Green", "Tail Style": "Wide Fish Tail"}}
    assert service._order_item_name(item) == \
        "Kelp Classic Day Bag — Board Size: 9'6\", Primary Colour: Olive Green, Tail Style: Wide Fish Tail"
    assert service._order_item_name({"commerce_products": {"name": "Hat"}}) == "Hat"


# ── the shop chat ───────────────────────────────────────────────────────────────────────────

CTX = {"tenant_id": T, "session_id": "+27821112222", "customer_phone": "+27821112222"}


def _chat(product=BAG):
    return (patch("core.skills.commerce_assistant.service.get_product_by_slug", new=AsyncMock(return_value=product)),
            patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value={"id": "c1"})),
            patch("core.skills.commerce_assistant.service.add_to_cart", new=AsyncMock()))


@pytest.mark.asyncio
async def test_the_chat_asks_for_the_missing_choices_and_adds_nothing():
    a, b, c = _chat()
    with a, b, c as add:
        out = await CommerceAssistantSkill()._dispatch_tool(
            "add_to_cart", {"product": "kelpclassicdaybag", "quantity": 1, "choices": "olive green"}, CTX)
    add.assert_not_awaited()
    assert out["needs_selection"] is True and out["chosen_so_far"] == {"Primary Colour": "Olive Green"}
    assert set(out["choices"]) == {"Trim Colour", "Nose Style", "Tail Style"}
    assert out["choices"]["Nose Style"] == ["Round", "Pinched Generous"]


@pytest.mark.asyncio
async def test_with_every_choice_the_line_is_added_with_them():
    a, b, c = _chat()
    with a, b, c as add:
        out = await CommerceAssistantSkill()._dispatch_tool(
            "add_to_cart", {"product": "kelpclassicdaybag", "quantity": 1,
                            "choices": "olive green, beige trim, round nose, fish tail"}, CTX)
    chosen = {"Primary Colour": "Olive Green", "Trim Colour": "Beige", "Nose Style": "Round",
              "Tail Style": "Wide Fish Tail"}
    add.assert_awaited_once_with(T, "c1", "p-bag", 1.0, variant_id=None, choices=chosen)
    assert out["added"].startswith("Kelp Classic Day Bag (Primary Colour: Olive Green, Trim Colour: Beige")
    assert out["unit_price"] == "R1200.00"


@pytest.mark.asyncio
async def test_the_product_list_shows_the_choices():
    with patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[dict(BAG)])):
        out = await CommerceAssistantSkill()._dispatch_tool("list_products", {}, CTX)
    items = out if isinstance(out, list) else out.get("products") or out.get("items") or []
    bag = next(p for p in items if p.get("name") == "Kelp Classic Day Bag")
    assert bag["choices"]["Tail Style"][1] == "Wide Fish Tail"


# ── the website cart ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_website_cart_refuses_a_line_missing_a_choice_but_sync_never_drops_one():
    from vula.api import commerce as api
    with patch.object(service, "get_product", new=AsyncMock(return_value=dict(BAG))):
        with pytest.raises(ValueError) as e:
            await api._web_choices(T, "p-bag", {"Primary Colour": "Olive Green"})
        assert "Trim Colour" in str(e.value)
        kept = await api._web_choices(T, "p-bag", {"Primary Colour": "Olive Green", "Trim Colour": "pink"},
                                      require=False)
    assert kept == {"Primary Colour": "Olive Green"}                 # an unknown value is dropped


# ── the website import saves which options change the price, and the choices ────────────────

@pytest.mark.asyncio
async def test_imported_rows_carry_the_priced_option_names_and_the_choices():
    from vula.api import commerce as api
    db = FakeSupabase({"commerce_products": [], "commerce_categories": []})
    with patch.object(service, "_client", return_value=db), \
         patch.object(service, "list_products", new=AsyncMock(return_value=[])):
        await api.admin_import_products(T, {"rows": [
            {"name": "Kelp Classic Day Bag", "slug": "kelpclassicdaybag", "price_cents": 120_000,
             "options": ["Board Size"], "choices": CHOICES},
            {"name": "Trucker Hat", "slug": "hat", "price_cents": 50_000}]})
    rows = {r["slug"]: r for r in db.rows["commerce_products"]}
    assert rows["kelpclassicdaybag"]["options"] == ["Board Size"]
    assert rows["kelpclassicdaybag"]["choices"][0]["name"] == "Primary Colour"
    assert "options" not in rows["hat"] and "choices" not in rows["hat"]
