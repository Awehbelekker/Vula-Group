"""Rival products for reps (vula/commerce/product_rivals.py, migration 315, 26 Nov 2026): a product's
competing products are the business's own sales knowledge — a team guide shows them and finds a
product by a rival's name; a client guide never reads them; the public product routes never carry
cost, supplier or reorder fields."""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from tests.fake_supabase import FakeSupabase
from vula.api import product_guides as api
from vula.commerce import product_guides as pg, product_rivals as pr, service, web_basket as wb


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "commerce_products": [
            {"id": "p1", "tenant_id": "acme", "name": "Carlton", "category": "carpet", "status": "active",
             "price_cents": 22360, "in_stock": True, "cost_cents": 9900, "default_supplier_id": "s1",
             "specs": {"grade": "Heavy residential"}},
            {"id": "x1", "tenant_id": "other", "name": "Their carpet", "status": "active", "price_cents": 1},
        ],
        "commerce_product_rivals": [], "commerce_categories": [], "commerce_product_guides": [],
        "vula_merchant_audit": [],
    })
    monkeypatch.setattr(service, "_client", lambda: d)
    monkeypatch.setattr(wb, "public_base", lambda t: "https://acme.shop.example")
    monkeypatch.setattr(wb, "wa_number", lambda t: "27820000000")

    async def inv(t):
        return {"trading_as": "Acme Floors", "accent_color": "#1F5E5A"}
    monkeypatch.setattr(service, "get_invoice_settings", inv)
    from vula.api import tenants
    monkeypatch.setattr(tenants, "is_active", lambda t: True)
    return d


def _page(token):
    res = asyncio.run(api.guide_page(token, SimpleNamespace(headers={"user-agent": "Mozilla"})))
    html = res.body.decode()
    data = json.loads(re.search(r'<script id="g" type="application/json">(.*?)</script>', html, re.S).group(1))
    return html, data


def test_the_list_is_cleaned_in_code():
    assert pr.clean("Belgotex: Conqueror\n- Nouwens: Harbour (closest colour match)\nKingston\nbelgotex: conqueror") == [
        {"brand": "Belgotex", "name": "Conqueror", "note": ""},
        {"brand": "Nouwens", "name": "Harbour", "note": "closest colour match"},
        {"brand": "", "name": "Kingston", "note": ""}]
    assert pr.clean("") == [] and pr.clean(None) == []
    for bad in ("Belgotex:", "Belgotex: see www.x.co.za", [f"r{i}" for i in range(31)], 5):
        with pytest.raises(pr.RivalError):
            pr.clean(bad)


def test_only_for_the_business_own_product_and_read_back(db):
    back = pr.replace("acme", "p1", "Belgotex: Conqueror\nNouwens: Harbour", actor={"email": "o@acme"})
    assert [pr.label(r) for r in back] == ["Belgotex Conqueror", "Nouwens Harbour"]
    assert pr.replace("acme", "p1", "Belgotex: Influence") == [{"brand": "Belgotex", "name": "Influence", "note": ""}]
    with pytest.raises(pr.RivalError):
        pr.replace("acme", "x1", "Belgotex: Conqueror")              # another business's product
    assert pr.for_products("acme", ["p1", "x1"]) == {"p1": [{"brand": "Belgotex", "name": "Influence", "note": ""}]}
    assert pr.replace("acme", "p1", "") == [] and pr.for_products("acme", ["p1"]) == {}


def test_a_client_guide_never_shows_rivals_a_team_guide_does(db):
    pr.replace("acme", "p1", "Belgotex: Conqueror")
    client = pg.create("acme", {"title": "For Riverside"})
    assert client["audience"] in (None, "client")                   # the column's default on the real database
    html, data = _page(client["url"].rsplit("/", 1)[1])
    assert "Conqueror" not in html and data["team"] is False and "rivals" not in data["items"][0]

    team = pg.create("acme", {"title": "Reps' field guide", "audience": "team"})
    assert team["audience"] == "team"
    html, data = _page(team["url"].rsplit("/", 1)[1])
    assert data["team"] is True and data["items"][0]["rivals"] == [{"brand": "Belgotex", "name": "Conqueror", "note": ""}]
    assert "For your team only" in html
    with pytest.raises(pg.GuideError):
        pg.create("acme", {"title": "x", "audience": "everyone"})
    pg.update("acme", team["id"], {"audience": "client"})
    _, data = _page(team["url"].rsplit("/", 1)[1])
    assert data["team"] is False and "rivals" not in data["items"][0]


def test_public_product_routes_carry_no_cost_or_supplier(db):
    from vula.api import commerce
    row = {"id": "p1", "name": "Carlton", "price_cents": 22360, "cost_cents": 9900, "unit_cost": 99,
           "default_supplier_id": "s1", "reorder_threshold": 5, "reorder_qty": 10,
           "variants": [{"id": "v1", "price_cents": 1, "cost_cents": 50}]}
    out = service.public_product(row)
    assert not set(out) & service.PRIVATE_PRODUCT_FIELDS and out["price_cents"] == 22360
    assert "cost_cents" not in out["variants"][0] and out["variants"][0]["price_cents"] == 1
    assert "cost_cents" in row                                     # the row itself is left alone

    async def listed(*a, **k):
        return [dict(row)]
    service_list = service.list_products
    try:
        service.list_products = listed
        got = asyncio.run(commerce.list_products("acme", category=None, in_stock_only=True, sort=None))
    finally:
        service.list_products = service_list
    assert not set(got["products"][0]) & service.PRIVATE_PRODUCT_FIELDS
