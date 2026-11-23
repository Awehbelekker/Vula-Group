"""Every shop's free address (23 Nov 2026): <business>.<shop_base_domain> on one wildcard domain.
A new shop has a real web address with nothing to set up; its own address still wins; a link only
ever points at an address that answers; and the shop page says what a search engine needs."""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from config import settings
from vula.api import menu_page as mp
from vula.commerce import shop_domain as sd, web_basket as wb

TENANTS = {"seedleme": {"tenant_id": "seedleme", "domains": []},
           "off_the_hook": {"tenant_id": "off_the_hook", "domains": ["shop.offthehook.co.za"]},
           "digg-demo": {"tenant_id": "digg-demo", "domains": []}}
PRODUCTS = [{"id": "p1", "name": "Round Basin", "price_cents": 125050, "image_url": "https://cdn.example/b.jpg",
             "description": "A basin for the bathroom."}]


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(settings, "shop_base_domain", "shop.vula-ai.com", raising=False)
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, **k: TENANTS.get(tid, {}))
    monkeypatch.setattr("vula.api.tenants.tenant_for_host",
                        lambda h: next((t for t, c in TENANTS.items() if h in c["domains"]), None))
    live = {"seedleme.shop.vula-ai.com"}
    stock = {"seedleme": PRODUCTS, "off_the_hook": PRODUCTS, "digg-demo": []}

    async def products(tid, category=None):
        return stock.get(tid, [])

    async def check(host, tid):
        return host in live

    monkeypatch.setattr(wb, "shop_products", products)
    monkeypatch.setattr(wb, "check_shop_host", check)
    monkeypatch.setattr(wb, "_host_ready", lambda host, tid: host in live)
    monkeypatch.setattr("vula.commerce.shop_stats.record", lambda *a, **k: None)
    return SimpleNamespace(live=live, stock=stock)


def _req(host):
    return SimpleNamespace(headers={"host": host, "user-agent": "Mozilla"})


def test_no_base_domain_means_no_free_addresses(monkeypatch):
    monkeypatch.setattr(settings, "shop_base_domain", "", raising=False)
    assert wb.free_shop_host("seedleme") == "" and wb.tenant_for_shop_host("seedleme.shop.vula-ai.com") is None


def test_a_business_gets_its_own_name_and_only_its_own(world):
    assert wb.free_shop_host("seedleme") == "seedleme.shop.vula-ai.com"
    assert wb.free_shop_host("off_the_hook") == "off-the-hook.shop.vula-ai.com"      # a web-safe name
    assert wb.tenant_for_shop_host("Seedleme.Shop.Vula-AI.com:443") == "seedleme"
    assert wb.tenant_for_shop_host("off-the-hook.shop.vula-ai.com") == "off_the_hook"
    for nobody in ("nosuchshop.shop.vula-ai.com", "www.shop.vula-ai.com", "api.shop.vula-ai.com",
                   "a.b.shop.vula-ai.com", "shop.vula-ai.com", "seedleme.shop.vula-ai.com.evil.com",
                   "seedleme-shop.vula-ai.com", "-x.shop.vula-ai.com"):
        assert wb.tenant_for_shop_host(nobody) is None, nobody
    for bad in ("", "www", "Bad Name!", "x" * 80, "a.b"):
        assert wb.free_shop_host(bad) == ""


def test_links_use_the_free_address_only_once_it_answers_and_the_own_address_wins(world):
    assert wb.shop_host("seedleme") == "seedleme.shop.vula-ai.com"
    assert wb.shop_url("seedleme") == "https://seedleme.shop.vula-ai.com/"
    assert wb.shop_host("off_the_hook") == ""                       # neither address answers yet
    assert "/menu/off_the_hook" in wb.shop_url("off_the_hook")      # so links stay on Vula's
    world.live.add("off-the-hook.shop.vula-ai.com")
    assert wb.shop_host("off_the_hook") == "off-the-hook.shop.vula-ai.com"
    world.live.add("shop.offthehook.co.za")
    assert wb.shop_host("off_the_hook") == "shop.offthehook.co.za"  # its own address wins


def test_the_free_address_serves_the_shop_and_tells_search_engines_about_it(world):
    page = asyncio.run(mp.shop_home(_req("seedleme.shop.vula-ai.com"))).body.decode()
    assert '<link rel="canonical" href="https://seedleme.shop.vula-ai.com/">' in page
    assert '<meta name="description" content="Seedleme — order online and pay on WhatsApp. Round Basin.">' in page
    ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))
    offer = ld["hasOfferCatalog"]["itemListElement"][0]
    assert ld["@type"] == "Store" and ld["url"] == "https://seedleme.shop.vula-ai.com/"
    assert (offer["price"], offer["priceCurrency"], offer["itemOffered"]["name"]) == ("1250.50", "ZAR", "Round Basin")
    assert "<noscript><ul><li>Round Basin — R1250.50</li></ul></noscript>" in page
    assert asyncio.run(mp.shop_ping(_req("seedleme.shop.vula-ai.com"))).body == b'{"shop":"seedleme"}'


def test_the_copy_on_vulas_server_points_at_the_shops_address(world):
    page = asyncio.run(mp.photo_menu("seedleme", _req("api.vula-ai.com"))).body.decode()
    assert '<link rel="canonical" href="https://seedleme.shop.vula-ai.com/">' in page
    world.live.clear()
    assert 'rel="canonical"' not in asyncio.run(mp.photo_menu("seedleme", _req("api.vula-ai.com"))).body.decode()


def test_a_business_with_nothing_to_sell_has_no_page_on_its_free_address(world):
    for host in ("digg-demo.shop.vula-ai.com", "nosuchshop.shop.vula-ai.com", "api.vula-ai.com"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(mp.shop_home(_req(host)))
        assert exc.value.status_code == 404


def test_robots_and_sitemap_exist_only_on_a_shops_address(world):
    robots = asyncio.run(mp.shop_robots(_req("seedleme.shop.vula-ai.com"))).body.decode()
    assert "Sitemap: https://seedleme.shop.vula-ai.com/sitemap.xml" in robots and "Disallow: /receipt/" in robots
    assert "<loc>https://seedleme.shop.vula-ai.com/</loc>" in asyncio.run(
        mp.shop_sitemap(_req("seedleme.shop.vula-ai.com"))).body.decode()
    for fn in (mp.shop_robots, mp.shop_sitemap):
        with pytest.raises(HTTPException):
            asyncio.run(fn(_req("api.vula-ai.com")))


def test_product_names_cannot_break_out_of_the_page(world):
    world.stock["seedleme"] = [{"id": "p1", "name": 'Evil"></script><script>alert(1)</script>{{DATA}}',
                                "price_cents": 100, "image_url": None}]
    page = asyncio.run(mp.shop_home(_req("seedleme.shop.vula-ai.com"))).body.decode()
    head = page.split("</head>")[0]
    assert "<script>alert(1)" not in head and head.count("<script") == 1      # only the structured data
    assert "<script>alert(1)" not in page.split("<noscript>")[1].split("</noscript>")[0]
    json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))


def test_settings_shows_the_free_address_and_whether_it_is_up(world):
    assert asyncio.run(sd.free_address("seedleme")) == {
        "host": "seedleme.shop.vula-ai.com", "url": "https://seedleme.shop.vula-ai.com/", "live": True}
    assert asyncio.run(sd.free_address("off_the_hook"))["live"] is False
    assert asyncio.run(sd.free_address("digg-demo")) is None              # nothing to sell
