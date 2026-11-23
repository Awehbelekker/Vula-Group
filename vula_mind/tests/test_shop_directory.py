"""The shop directory (vula/commerce/shop_directory.py, 24 Nov 2026): a shop is on it only when its
owner asked, it is active, it has something to sell and its address answers; what a card shows is the
business's own words, escaped; and only an owner or manager can change the listing."""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from config import settings
from tests.fake_supabase import FakeSupabase
from vula.api import menu_page as mp, tenants
from vula.commerce import service, shop_directory as sd, web_basket as wb

PRODUCT = {"id": "p1", "name": "Round Basin", "price_cents": 125050, "image_url": "https://cdn.example/b.jpg"}


def _row(tid, btype="retail", listed=True, active=True, about="", town="", **feats):
    d = {"listed": listed, "about": about, "town": town}
    return {"id": tid, "tenant_id": tid, "display_name": tid.title(), "business_type": btype, "active": active,
            "features": {"directory": d, **feats} if listed is not None else dict(feats)}


@pytest.fixture
def world(monkeypatch):
    rows = [_row("seedleme", about="Basins & taps <b>for</b> bathrooms", town="Cape Town"),
            _row("off_the_hook", "food", about="Fresh fish", town="Hout Bay"),
            _row("quiet", listed=None),                                   # never asked
            _row("unlisted", listed=False),
            _row("paused", active=False),
            _row("empty"),                                                # nothing to sell
            _row("notup")]                                                # address not answering
    db = FakeSupabase({"vula_tenant_config": rows, "vula_merchant_audit": []})
    monkeypatch.setattr(settings, "shop_base_domain", "shop.vula-ai.com", raising=False)
    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(tenants, "_client", lambda: db)
    tenants._CACHE.clear()
    sd._CACHE.update(at=0.0, rows=None)
    stock = {r["tenant_id"]: [PRODUCT] for r in rows}
    stock["empty"] = []
    down = {"notup"}

    async def products(tid, category=None):
        return stock.get(tid, [])

    async def inv(tid):
        return {"trading_as": {"seedleme": "SeedleMe {{BODY}}", "off_the_hook": "Off the Hook"}.get(tid),
                "accent_color": "0a7d5c", "logo_url": "https://cdn.example/logo.png" if tid == "seedleme" else ""}

    async def check(host, tid):
        return tid not in down

    monkeypatch.setattr(wb, "shop_products", products)
    monkeypatch.setattr(wb, "check_shop_host", check)
    monkeypatch.setattr(wb, "_host_ready", lambda host, tid: tid not in down)
    monkeypatch.setattr(service, "get_invoice_settings", inv)
    return SimpleNamespace(db=db, stock=stock, down=down)


def run(c):
    return asyncio.run(c)


def _req(host):
    return SimpleNamespace(headers={"host": host, "user-agent": "Mozilla"})


def test_where_the_directory_answers(world, monkeypatch):
    assert sd.host() == "directory.shop.vula-ai.com" and sd.url() == "https://directory.shop.vula-ai.com/"
    for h in ("directory.shop.vula-ai.com", "Shop.Vula-AI.com:443", "www.shop.vula-ai.com"):
        assert sd.is_directory_host(h)
    for h in ("seedleme.shop.vula-ai.com", "api.vula-ai.com", "directory.shop.vula-ai.com.evil.com", ""):
        assert not sd.is_directory_host(h)
    assert wb.free_shop_host("directory") == "" and wb.tenant_for_shop_host("directory.shop.vula-ai.com") is None
    monkeypatch.setattr(settings, "shop_base_domain", "", raising=False)
    assert sd.host() == "" and not sd.is_directory_host("directory.shop.vula-ai.com")


def test_only_shops_that_asked_are_active_have_stock_and_answer(world):
    got = run(sd.entries())
    assert [e["tenant"] for e in got] == ["off_the_hook", "seedleme"]            # by name
    fish, basins = got
    assert fish["url"] == "https://off-the-hook.shop.vula-ai.com/" and fish["category"] == "Food & drink"
    assert basins["category"] == "Shops" and basins["town"] == "Cape Town" and basins["products"] == 1
    assert basins["accent"] == "#0a7d5c" and basins["image"].startswith("https://")


def test_a_shop_appears_and_leaves_as_its_state_changes(world):
    world.stock["empty"] = [PRODUCT]
    world.down.clear()
    assert {e["tenant"] for e in run(sd.entries(fresh=True))} == {"off_the_hook", "seedleme", "empty", "notup"}
    world.stock["seedleme"] = []
    assert "seedleme" not in {e["tenant"] for e in run(sd.entries(fresh=True))}


def test_the_page_escapes_what_businesses_wrote(world):
    rows = run(sd.entries())
    page = sd.page(rows, sd.url())
    assert "Basins &amp; taps &lt;b&gt;for&lt;/b&gt; bathrooms" in page and "<b>for</b>" not in page
    assert "{{" not in page                                    # a name can't fill one of the page's slots
    assert page.count('<li class="card"') == 2 and "2 shops" in page
    assert 'href="https://off-the-hook.shop.vula-ai.com/"' in page
    ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))
    assert [i["url"] for i in ld["itemListElement"]] == [e["url"] for e in rows]
    assert '<link rel="canonical" href="https://directory.shop.vula-ai.com/">' in page
    assert "No shops are listed yet" in sd.page([], sd.url())


def test_an_image_or_logo_that_is_not_https_is_left_out(world):
    e = dict(run(sd.entries())[0], image="javascript:alert(1)", logo="http://plain.example/l.png")
    card = sd._card(e)
    assert "javascript:" not in card and "plain.example" not in card and 'class="lg ini"' in card


def test_served_on_the_directory_address_with_robots_and_sitemap(world):
    res = run(mp.shop_home(_req("directory.shop.vula-ai.com")))
    assert res.status_code == 200 and b"Shops on Vula" in res.body and b"Off the Hook" in res.body
    robots = run(mp.shop_robots(_req("directory.shop.vula-ai.com")))
    assert b"Sitemap: https://directory.shop.vula-ai.com/sitemap.xml" in robots.body
    site = run(mp.shop_sitemap(_req("directory.shop.vula-ai.com"))).body.decode()
    assert site.count("<loc>") == 3 and "https://seedleme.shop.vula-ai.com/" in site
    with pytest.raises(HTTPException) as nf:
        run(mp.shop_home(_req("api.vula-ai.com")))
    assert nf.value.status_code == 404


def test_listing_is_saved_read_back_and_keeps_the_other_switches(world):
    world.db.rows["vula_tenant_config"].append(_row("newshop", listed=None, trade_costing=True))
    world.stock["newshop"] = [PRODUCT]
    before = run(sd.status("newshop"))
    assert before["listed"] is False and before["showing"] is False and before["why_not"] == "Your shop isn't listed."
    out = run(sd.save("newshop", listed=True, about="  Hand-made\n soap ", town="Paarl", actor={"email": "o@x.co.za"}))
    assert out["listed"] and out["showing"] and out["about"] == "Hand-made soap" and out["town"] == "Paarl"
    assert out["shop_url"] == "https://newshop.shop.vula-ai.com/"
    row = next(r for r in world.db.rows["vula_tenant_config"] if r["tenant_id"] == "newshop")
    assert row["features"]["trade_costing"] is True
    assert world.db.rows["vula_merchant_audit"][-1]["action"] == "shop_directory_listing"
    assert "newshop" in {e["tenant"] for e in run(sd.entries())}              # the kept page was cleared
    off = run(sd.save("newshop", listed=False, about="Hand-made soap", town="Paarl"))
    assert off["listed"] is False and "newshop" not in {e["tenant"] for e in run(sd.entries())}


def test_why_a_listed_shop_is_not_showing(world):
    assert "no products" in run(sd.status("empty"))["why_not"]
    assert "paused" in run(sd.status("paused"))["why_not"]
    assert "isn't answering" in run(sd.status("notup"))["why_not"]
    assert run(sd.status("seedleme"))["showing"] is True


def test_refused_listings(world):
    for bad in ("Visit www.elsewhere.co.za", "see https://x.y", "better at othershop.com"):
        with pytest.raises(sd.DirectoryError):
            run(sd.save("seedleme", listed=True, about=bad))
    with pytest.raises(sd.DirectoryError):
        run(sd.save("seedleme", listed="yes"))
    with pytest.raises(sd.DirectoryError):
        run(sd.save("nobody", listed=True))
    assert len(sd.clean("x" * 500, "y" * 90)["about"]) == sd.ABOUT_MAX
    assert run(sd.status("seedleme"))["about"].startswith("Basins")            # nothing changed


def test_only_an_owner_or_manager_may_change_the_listing():
    from vula.api import server
    rule = next(r for r in server._SECTION_RULES if "shop-domain" in r[1].pattern)
    assert rule[1].match("/v1/commerce/seedleme/admin/shop-directory") and rule[2] == frozenset()
    assert "PUT" in rule[0] and "GET" not in rule[0]
