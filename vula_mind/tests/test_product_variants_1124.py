"""24 Nov 2026: sizes and colours made in one go, a photo per colour, SKU/barcode/cost on a
product. Made-up data."""
import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from vula.api import commerce
from vula.commerce import product_variants as pv
from vula.commerce import service

T = "boutique"


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "commerce_products": [
            {"id": "p1", "tenant_id": T, "name": "Linen shirt", "price_cents": 45000, "options": None,
             "images": ["https://img.example/red.jpg", "https://img.example/blue.jpg"], "barcode": "600111"},
            {"id": "p2", "tenant_id": T, "name": "Cap", "price_cents": 15000},
            {"id": "px", "tenant_id": "other", "name": "Not ours", "price_cents": 100},
        ],
        "commerce_product_variants": [
            {"id": "v0", "tenant_id": T, "product_id": "p1", "option_values": {"colour": "RED", "size": "m"},
             "price_cents": 50000, "stock_quantity": 3, "sort_order": 1, "archived": False, "barcode": "600222"},
        ],
        "commerce_categories": [],
    })
    monkeypatch.setattr(service, "_client", lambda: d)
    return d


SHIRT = [{"name": "Colour", "values": "Red, Blue, red "}, {"name": "Size", "values": ["S", "M", "L", ""]}]


# ── options as typed ──────────────────────────────────────────────────────────

def test_options_are_cleaned_not_cut():
    opts = pv.clean_options(SHIRT)
    assert opts == [{"name": "Colour", "values": ["Red", "Blue"]}, {"name": "Size", "values": ["S", "M", "L"]}]
    assert len(pv.combinations(opts)) == 6


@pytest.mark.parametrize("bad,says", [
    ([{"name": "", "values": "S, M"}], "name"),
    ([{"name": "Size", "values": ""}], "at least one"),
    ([{"name": "Size", "values": "S"}, {"name": "size", "values": "M"}], "twice"),
    ([{"name": n, "values": "a"} for n in "ABCD"], "most a product"),
    ([{"name": "Size", "values": ",".join(str(i) for i in range(31))}], "most one option"),
    ([{"name": "A", "values": ",".join(str(i) for i in range(11))},
      {"name": "B", "values": ",".join(str(i) for i in range(11))}], "121 versions"),
])
def test_too_much_or_too_little_is_refused_in_words(bad, says):
    with pytest.raises(pv.VariantError) as e:
        pv.clean_options(bad)
    assert says in str(e.value)


# ── making them ───────────────────────────────────────────────────────────────

def test_generate_makes_the_missing_ones_and_keeps_the_rest(db):
    out = pv.generate(T, "p1", SHIRT)
    assert out["created"] == 5 and out["kept"] == 1
    rows = [v for v in db.rows["commerce_product_variants"] if v["product_id"] == "p1"]
    assert len(rows) == 6
    kept = [v for v in rows if v["id"] == "v0"][0]
    assert kept["price_cents"] == 50000 and kept["stock_quantity"] == 3      # untouched
    new = [v for v in rows if v["id"] != "v0"]
    assert all(v.get("price_cents") is None for v in new)                    # the product's price
    assert db.rows["commerce_products"][0]["options"] == ["Colour", "Size"]


def test_generate_twice_makes_nothing_new(db):
    pv.generate(T, "p1", SHIRT)
    again = pv.generate(T, "p1", SHIRT)
    assert again["created"] == 0 and again["kept"] == 6


def test_a_version_outside_the_new_options_is_listed_never_deleted(db):
    out = pv.generate(T, "p1", [{"name": "Colour", "values": "Blue"}, {"name": "Size", "values": "S"}])
    assert [x["id"] for x in out["not_in_options"]] == ["v0"]
    assert any(v["id"] == "v0" for v in db.rows["commerce_product_variants"])


def test_stock_and_price_given_go_on_the_new_ones(db):
    pv.generate(T, "p2", [{"name": "Size", "values": "S, M"}], price_cents=16000, stock_quantity="4")
    caps = [v for v in db.rows["commerce_product_variants"] if v["product_id"] == "p2"]
    assert {v["price_cents"] for v in caps} == {16000} and {v["stock_quantity"] for v in caps} == {4.0}


def test_another_business_product_is_not_found(db):
    with pytest.raises(LookupError):
        pv.generate(T, "px", SHIRT)


# ── a photo per colour ────────────────────────────────────────────────────────

def test_one_photo_for_every_red_one(db):
    pv.generate(T, "p1", SHIRT)
    out = pv.set_option_photo(T, "p1", "colour", "red", "https://img.example/red.jpg")
    assert out["updated"] == 3
    reds = [v for v in db.rows["commerce_product_variants"]
            if v["product_id"] == "p1" and str(v["option_values"].get("Colour") or v["option_values"].get("colour")).lower() == "red"]
    assert {v["image_url"] for v in reds} == {"https://img.example/red.jpg"}
    blues = [v for v in db.rows["commerce_product_variants"] if (v["option_values"].get("Colour") or "") == "Blue"]
    assert all(not v.get("image_url") for v in blues)


@pytest.mark.parametrize("url", ["http://img.example/a.jpg", "javascript:alert(1)", "https://x/a b.jpg"])
def test_a_photo_must_be_https(db, url):
    with pytest.raises(pv.VariantError):
        pv.set_option_photo(T, "p1", "Colour", "Red", url)


def test_a_value_no_version_has_says_so(db):
    with pytest.raises(pv.VariantError) as e:
        pv.set_option_photo(T, "p1", "Colour", "Green", "https://img.example/g.jpg")
    assert "Green" in str(e.value)


# ── SKU, barcode, cost ────────────────────────────────────────────────────────

def test_a_barcode_is_on_one_thing_only(db):
    assert pv.barcode_owner(T, "600111", product_id="p2") == "Linen shirt"
    assert pv.barcode_owner(T, "600111", product_id="p1") is None              # its own
    assert "Linen shirt" in pv.barcode_owner(T, "600222", product_id="p2")    # a variant's
    assert pv.barcode_owner(T, "600222", variant_id="v0") is None
    assert pv.barcode_owner(T, "999", product_id="p2") is None


def test_a_taken_barcode_is_refused_before_saving(db):
    with pytest.raises(HTTPException) as e:
        commerce._check_codes(T, {"barcode": " 600111 "}, product_id="p2")
    assert e.value.status_code == 409 and "Linen shirt" in e.value.detail


@pytest.mark.parametrize("data,ok", [
    ({"cost_cents": "1250"}, {"cost_cents": 1250}),
    ({"cost_cents": ""}, {"cost_cents": None}),
    ({"sku": "  SH-RED  M "}, {"sku": "SH-RED M"}),
    ({"sku": ""}, {"sku": None}),
])
def test_codes_are_tidied(db, data, ok):
    commerce._check_codes(T, data, product_id="p2")
    assert data == ok


@pytest.mark.parametrize("data", [{"cost_cents": -5}, {"cost_cents": "R12"}, {"sku": "x" * 65}])
def test_bad_codes_are_refused(db, data):
    with pytest.raises(HTTPException) as e:
        commerce._check_codes(T, data, product_id="p2")
    assert e.value.status_code == 400


# ── adding a product with everything at once ──────────────────────────────────

@pytest.mark.asyncio
async def test_a_new_product_with_sizes_and_codes(db):
    out = await commerce.admin_create_product(T, {
        "name": "Canvas tote", "price_cents": 22000, "sku": "TOTE", "cost_cents": 9000,
        "images": ["https://img.example/tote.jpg"], "stock_quantity": 5,
        "options": [{"name": "Colour", "values": "Natural, Black"}]})
    prod = [p for p in db.rows["commerce_products"] if p["name"] == "Canvas tote"][0]
    assert prod["sku"] == "TOTE" and prod["cost_cents"] == 9000
    assert prod["image_url"] == "https://img.example/tote.jpg" and prod["stock_quantity"] is None
    vs = [v for v in db.rows["commerce_product_variants"] if v["product_id"] == prod["id"]]
    assert len(vs) == 2 and {v["stock_quantity"] for v in vs} == {5.0}
    assert len(out["variants"]) == 2


@pytest.mark.asyncio
async def test_bad_options_refuse_the_whole_add(db):
    before = len(db.rows["commerce_products"])
    with pytest.raises(HTTPException) as e:
        await commerce.admin_create_product(T, {"name": "Mug", "price_cents": 9000,
                                                "options": [{"name": "Size", "values": ""}]})
    assert e.value.status_code == 400 and len(db.rows["commerce_products"]) == before


@pytest.mark.asyncio
async def test_a_new_product_with_a_taken_barcode_is_refused(db):
    with pytest.raises(HTTPException) as e:
        await commerce.admin_create_product(T, {"name": "Mug", "price_cents": 9000, "barcode": "600111"})
    assert e.value.status_code == 409
