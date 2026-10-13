"""SeedleMe lists every colour as its own product (325 rows, ~94 ranges). Grouping them back into
ranges with colours is what a colour question, a WhatsApp menu and the native catalogue need."""
from vula.commerce import product_families as pf


def _p(name, cents=149900, slug=None):
    return {"name": name, "price_cents": cents, "slug": slug or name.lower(), "category": "basin",
            "image_url": f"https://cdn/{abs(hash(name)) % 999}.jpg"}


def test_a_colour_is_read_from_the_name_longest_first():
    assert pf.colour_of("Countertop Butler Concrete Basin – Nguni Pattern Grey – 31 x 31 x 12cm") == "Nguni Pattern Grey"
    assert pf.colour_of("Burnt Orange Round Bespoke Concrete Sink 30 x 30 x 13cm") == "Burnt Orange"
    assert pf.colour_of("Solid Square Concrete Shower Pan 90 x 90 x 13,5") is None


def test_one_range_whatever_the_shop_called_each_colour():
    rs = pf.group([_p("Gaia's Ark Black 53 x 30 x 11.5cm", 209900),
                   _p("Gaia's Ark Ivory Concrete Sink 53 x 30 x 11.5cm", 209900),
                   _p("Gaia's Ark Choc Plum Bespoke Sink 53 x 30 x 11.5cm", 209900),
                   _p("Charcoal Round Bespoke Concrete Sink 30x30x13cm", 139900),
                   _p("Black Round Bespoke Concrete Sink 30 x 30 x 13cm", 139900)])
    assert [(r["title"], [c["colour"] for c in r["colours"]]) for r in rs] == [
        ("Gaia's Ark 53 x 30 x 11.5cm", ["Black", "Choc Plum", "Ivory"]),
        ("Round Bespoke Concrete Sink 30 x 30 x 13cm", ["Black", "Charcoal"])]


def test_a_finish_that_costs_more_and_a_different_size_are_their_own_ranges():
    rs = pf.group([_p("Countertop Butler Concrete Basin – Black – 31 x 31 x 12cm"),
                   _p("Countertop Butler Concrete Basin – Ivory – 31 x 31 x 12cm"),
                   _p("Countertop Butler Concrete Basin – Black – 36 x 36 x 12cm", 159900),
                   _p("Gaia's Ark Black 53 x 30 x 11.5cm", 209900),
                   _p("Gaia's Ark Black Namibian Dream Bespoke Concrete Sink 53 x 30 x 11.5cm", 309900)])
    assert [r["title"] for r in rs] == [
        "Countertop Butler Concrete Basin – 31 x 31 x 12cm",
        "Countertop Butler Concrete Basin – Black – 36 x 36 x 12cm",     # one colour: its own name
        "Gaia's Ark Black 53 x 30 x 11.5cm",
        "Gaia's Ark Black Namibian Dream Bespoke Concrete Sink 53 x 30 x 11.5cm"]


def test_ranges_have_stable_ids_prices_and_a_photo():
    items = [_p("Oval Concrete Basin – Black – 50 x 38 x 13cm", 101900),
             _p("Oval Concrete Basin – Sandstone – 50 x 38 x 13cm", 189900)]
    r = pf.group(items)[0]
    assert r["id"] == pf.group(list(reversed(items)))[0]["id"] and len(r["id"]) == 10
    assert pf.price_label(r) == "from R1,019.00" and r["image_url"].startswith("https://cdn/")
    assert pf.find_range(items, r["id"])["title"] == "Oval Concrete Basin – 50 x 38 x 13cm"


def test_made_to_order_and_odd_spacing_do_not_split_a_range():
    rs = pf.group([_p("Large Blue Double Concrete Kitchen Butler Basin 80 x 40 x 27cm (50mm Outlet)( Made to Order )"),
                   _p("Large Ivory Double Concrete Kitchen Butler basin 80 x 40 x 27cm (50mm Outlet)")])
    assert len(rs) == 1 and [c["colour"] for c in rs[0]["colours"]] == ["Blue", "Ivory"]
