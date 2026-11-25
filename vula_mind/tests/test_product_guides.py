"""Product guides (vula/commerce/product_guides.py, migration 310, 25 Nov 2026): a spec sheet on a
product is cleaned in code; a guide is a secret link to the business's own products, white-labelled,
never indexed; prices only when the guide shows them, from integer cents; a turned-off or renewed
link stops working; another business's products can't go into a guide."""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from tests.fake_supabase import FakeSupabase
from vula.api import product_guides as api
from vula.commerce import product_guides as pg, service, web_basket as wb


def P(pid, name, cat="vinyl", tenant="acme", **kw):
    return {"id": pid, "tenant_id": tenant, "name": name, "category": cat, "price_cents": 45900,
            "status": "active", "in_stock": True, **kw}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "commerce_products": [
            P("p1", "Taralay Impression", specs={"grade": "Grade 43 · Heavy Commercial",
                                                 "rows": [["Width", "2 m"], ["Colours", "Oak, Ash, Slate"]]}),
            P("p2", "Creation 55", cat="lvt", sale_price_cents=39900, sold_by="m2",
              options=[{"name": "Colour", "values": ["Sand", "Smoke"]}]),
            P("p3", "Hidden sample board", status="unlisted"),
            P("p4", "Old range", archived=True),
            P("p5", "Ticket", product_type="ticket"),
            P("x1", "Someone else's", tenant="other"),
        ],
        "commerce_categories": [{"tenant_id": "acme", "key": "lvt", "label": "LVT planks", "sort_order": 0},
                                {"tenant_id": "acme", "key": "vinyl", "label": "Sheet vinyl", "sort_order": 1}],
        "commerce_product_guides": [], "vula_merchant_audit": [],
    })
    monkeypatch.setattr(service, "_client", lambda: d)
    monkeypatch.setattr(wb, "public_base", lambda t: "https://acme.shop.example")
    monkeypatch.setattr(wb, "wa_number", lambda t: "27820000000")

    async def inv(t):
        return {"trading_as": "Acme <Floors>", "accent_color": "1F5E5A", "logo_url": "javascript:alert(1)"}
    monkeypatch.setattr(service, "get_invoice_settings", inv)
    from vula.api import tenants
    monkeypatch.setattr(tenants, "is_active", lambda t: t != "paused")
    return d


def run(c):
    return asyncio.run(c)


def test_spec_sheet_is_cleaned_in_code():
    s = pg.clean_specs({"grade": "  Grade 33  ", "benefits": "• Stain resistant\n- Stain resistant\nUV stable",
                        "rows": "Width: 4 m\nBacking: Felt back: woven", "link": "https://www.gerflor.co.za/x"})
    assert s == {"grade": "Grade 33", "benefits": ["Stain resistant", "UV stable"],
                 "rows": [["Width", "4 m"], ["Backing", "Felt back: woven"]], "link": "https://www.gerflor.co.za/x"}
    assert pg.clean_specs({"grade": "", "rows": []}) is None and pg.clean_specs(None) is None
    for bad in ({"rows": "Width 4 m"}, {"rows": [["Width", ""]]}, {"rows": "Width: 4\nwidth: 5"},
                {"link": "http://x.com"}, {"link": "javascript:alert(1)"},
                {"benefits": [f"b{i}" for i in range(9)]}, {"rows": [[f"L{i}", "v"] for i in range(31)]}, "text"):
        with pytest.raises(pg.GuideError):
            pg.clean_specs(bad)


def test_colours_from_options_else_the_spec_line():
    assert pg.colours({"options": [{"name": "Size", "values": ["1"]}, {"name": "Colours", "values": "Sand, Smoke"}]}) == ["Sand", "Smoke"]
    assert pg.colours({"specs": {"rows": [["Colour", "Oak · Ash; Slate"]]}}) == ["Oak", "Ash", "Slate"]
    assert pg.colours({}) == []


def test_a_guide_holds_only_the_business_own_products(db):
    with pytest.raises(pg.GuideError):
        pg.create("acme", {"title": "Offices", "product_ids": ["p1", "x1"]})
    with pytest.raises(pg.GuideError):
        pg.create("acme", {"title": "  "})
    with pytest.raises(pg.GuideError):
        pg.create("acme", {"title": "X", "intro": "see www.cheap.example"})
    g = pg.create("acme", {"title": "Riverside Offices", "client_name": "Riverside Architects",
                           "product_ids": ["p3"], "categories": ["lvt"]}, actor={"email": "rep@acme"})
    assert g["url"].startswith("https://acme.shop.example/guide/") and g["active"] and not g["show_prices"]
    names = [p["name"] for p in pg.chosen("acme", {**g})]
    assert sorted(names) == ["Creation 55", "Hidden sample board"]           # chosen unlisted + the lvt category
    everything = [p["name"] for p in pg.chosen("acme", {"categories": [], "product_ids": []})]
    assert sorted(everything) == ["Creation 55", "Taralay Impression"]       # listed only; no archived, ticket, other tenant
    assert [g2["id"] for g2 in pg.list_guides("other")] == []


def _token(url):
    return url.rsplit("/", 1)[1]


def test_the_page_white_labelled_escaped_and_never_indexed(db):
    g = pg.create("acme", {"title": "Specs </script><b>x</b>", "client_name": "Riverside"})
    res = run(api.guide_page(_token(g["url"]), SimpleNamespace(headers={"user-agent": "Mozilla"})))
    html = res.body.decode()
    assert res.status_code == 200 and "noindex" in res.headers["x-robots-tag"]
    assert "Acme &lt;Floors&gt;" in html and "<Floors>" not in html
    assert "javascript:alert" not in html                                     # a logo must be https
    assert "</script><b>" not in html                                         # the data can't close its script
    data = json.loads(re.search(r'<script id="g" type="application/json">(.*?)</script>', html, re.S).group(1))
    assert data["prices"] is False and all("cents" not in i for i in data["items"])
    assert list(data["cats"]) == ["lvt", "vinyl"] and data["cats"]["lvt"] == "LVT planks"
    t = next(i for i in data["items"] if i["name"] == "Taralay Impression")
    assert t["grade"].startswith("Grade 43") and t["colours"] == ["Oak", "Ash", "Slate"]
    assert ["Colours", "Oak, Ash, Slate"] not in t["rows"]                    # the colour line becomes the picker
    assert data["wa"] == "27820000000"
    assert pg.by_token(_token(g["url"]))["views"] == 1


def test_prices_only_when_shown_from_cents(db):
    g = pg.create("acme", {"title": "Prices", "show_prices": True, "categories": ["lvt"]})
    d = pg.page_data(pg.by_token(_token(g["url"])))
    (c,) = d["items"]
    assert c["cents"] == 39900 and c["was_cents"] == 45900 and c["unit"] == "m²"


def test_turned_off_renewed_deleted_or_paused_is_a_404(db):
    g = pg.create("acme", {"title": "A"})
    tok = _token(g["url"])
    new = pg.renew("acme", g["id"])
    assert pg.by_token(tok) is None and pg.by_token(_token(new["url"]))
    pg.update("acme", g["id"], {"active": False})
    assert pg.by_token(_token(new["url"])) is None
    res = run(api.guide_page(_token(new["url"]), SimpleNamespace(headers={"user-agent": "Mozilla"})))
    assert res.status_code == 404
    assert pg.by_token("short") is None and pg.by_token("../../etc/passwd") is None
    with pytest.raises(pg.GuideError):
        pg.update("other", g["id"], {"title": "mine now"})                  # another business's guide isn't there
    pg.delete("acme", g["id"])
    assert pg.list_guides("acme") == []


def test_stock_message_names_product_colour_and_guide():
    m = pg.stock_message("Acme", "Creation 55", "Sand", "Riverside Offices")
    assert m.splitlines() == ["Hi Acme, please can you check stock for Creation 55.", "Colour: Sand",
                              "Quantity: ", "(From your guide: Riverside Offices)"]
