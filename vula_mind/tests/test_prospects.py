"""Master › Prospects (vula/prospects.py, migration 268): a business scanned before it's a client.
Its website is read without saving anything, and the report is built from the scan's own counts —
never a model's reading. 13 Nov 2026 (Ian: scan any client's website and socials and pitch)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from vula import prospects
from vula.commerce import service

ME = {"email": "ops@vula.example", "user_id": "u-master"}


def _shop_scan(**over):
    st = {"url": "https://www.kelpgearshop.com", "platform": "website", "found_by": "sitemap",
          "facts": {"name": "Kelp Gear", "phone": "0719363070", "socials": "https://www.instagram.com/kelp_boardbags_ct_sa"},
          "coverage": {"products": {"found": 6, "with_price": 6, "with_image": 5, "with_description": 4},
                       "policies": {"terms": "u1", "privacy": "u2", "returns": "u3", "shipping": None},
                       "contact": {"phone": True, "email": True, "address": True, "hours": False, "socials": True},
                       "pages": {"found": 24, "read": 24, "used": 15, "how": "sitemap"}},
          "products_preview": [
              {"name": "Kelp Classic Day Bag", "price_min_cents": 120_000, "price_max_cents": 240_000, "options": 15,
               "photos": 9, "has_description": True, "choices": ["Primary Colour"]},
              {"name": "Trucker Hat", "price_min_cents": 50_000, "price_max_cents": 50_000, "options": 3,
               "photos": 2, "has_description": True, "choices": []}],
          "pages_preview": [{"url": "https://www.kelpgearshop.com/about", "title": "About", "words": 120}]}
    st.update(over)
    return st


def _services_scan():
    return {"url": "https://shopeazy.co", "facts": {"name": "ShopEazy",
                                                    "socials": "https://x.com/shopeazyza, https://instagram.com/shopeazyza"},
            "coverage": {"products": {"found": 0}, "policies": {"terms": "a", "privacy": "b", "returns": "c", "shipping": "d"},
                         "contact": {"phone": False, "email": True, "address": False, "hours": False, "socials": True},
                         "pages": {"found": 60, "read": 60, "used": 59, "how": "sitemap"}}}


# ── the report ──────────────────────────────────────────────────────────────────────────────

def test_a_shops_report_is_built_from_the_scans_own_counts():
    rep = prospects.build_report(_shop_scan(), {"accent_color": "#123456", "logo_url": "https://x/logo.png"},
                                 ["instagram.com/kelp_boardbags_ct_sa"])
    assert rep["kind"] == "shop" and rep["name"] == "Kelp Gear" and rep["site"] == "www.kelpgearshop.com"
    assert "15 pages with content (of 24 found, from the sitemap)" in rep["found"]
    assert "6 products, 6 with a price" in rep["found"]
    assert "2 sold in sizes or options, 1 at different prices (e.g. Kelp Classic Day Bag: R1 200–R2 400)" in rep["found"]
    assert "1 of 6 products without a photo" in rep["gaps"] and "2 of 6 products without a description" in rep["gaps"]
    assert "No delivery or shipping policy on the website" in rep["gaps"]
    assert "Not on the website: opening hours" in rep["gaps"]
    assert rep["socials"] == ["https://instagram.com/kelp_boardbags_ct_sa"]          # the site's copy is the same page
    assert rep["counts"] == {"pages": 15, "products": 6, "policies": 3, "contact": 4}
    assert rep["brand"]["accent_color"] == "#123456"
    assert "every size at its own price" in rep["offer"][0]
    assert rep["pitch"].startswith("Kelp Gear: what Vula found on www.kelpgearshop.com")


def test_a_business_without_products_gets_the_assistant_pitch():
    rep = prospects.build_report(_services_scan())
    assert rep["kind"] == "services"
    assert rep["offer"][0].startswith("A WhatsApp assistant that answers prospects and customers 24/7 from their own 59 pages")
    assert rep["socials"] == ["https://x.com/shopeazyza", "https://instagram.com/shopeazyza"]
    assert not any("product" in g for g in rep["gaps"])


def test_a_site_built_in_the_browser_or_unreadable_says_so():
    rep = prospects.build_report(_shop_scan(note="This website builds its pages in the browser, so Vula could only read 1 page of it."))
    assert any(g.startswith("This website builds its pages in the browser") for g in rep["gaps"])
    rep = prospects.build_report({"url": "https://down.example", "error": "HTTP 503"})
    assert rep["gaps"] == ["The website couldn't be read: HTTP 503"]


def test_nothing_in_the_report_is_more_than_the_scan_found():
    """No product, page or figure appears that wasn't in the scan."""
    rep = prospects.build_report(_shop_scan(products_preview=[], coverage={"products": {"found": 0}, "pages": {}}))
    assert rep["products"] == [] and rep["counts"]["products"] == 0 and not rep["found"]


def test_only_social_links_are_kept_once_each():
    assert prospects.clean_socials(["instagram.com/kelp", "https://www.instagram.com/kelp/", "example.com",
                                    "https://tiktok.com/@kelp, https://fb.com/kelp"]) == \
        ["https://instagram.com/kelp", "https://tiktok.com/@kelp", "https://fb.com/kelp"]


# ── storage and the scan ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    d = FakeSupabase({"vula_prospects": []})
    with patch.object(service, "_client", return_value=d):
        yield d


@pytest.mark.asyncio
async def test_a_scan_is_stored_and_listed_without_its_report(db):
    row = prospects.create("kelpgearshop.com", "Kelp Gear", ["instagram.com/kelp"], "ops@vula.example")
    assert row["url"] == "https://kelpgearshop.com" and row["status"] == "scanning"
    with patch.object(prospects, "scan", AsyncMock(return_value={"kind": "shop", "counts": {"products": 6}, "pitch": "x"})) as sc:
        await prospects.run(row["id"])
    sc.assert_awaited_once_with("https://kelpgearshop.com", ["https://instagram.com/kelp"], "Kelp Gear")
    got = prospects.get(row["id"])
    assert got["status"] == "done" and got["report"]["pitch"] == "x"
    listed = prospects.listing()
    assert listed[0]["counts"] == {"products": 6} and "report" not in listed[0]


@pytest.mark.asyncio
async def test_a_failed_scan_says_why(db):
    row = prospects.create("down.example")
    with patch.object(prospects, "scan", AsyncMock(side_effect=RuntimeError("HTTP 503"))):
        await prospects.run(row["id"])
    got = prospects.get(row["id"])
    assert got["status"] == "failed" and "HTTP 503" in got["error"]


def test_a_scan_lost_to_a_restart_shows_as_failed(db):
    row = prospects.create("slow.example")
    db.rows["vula_prospects"][0]["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    got = prospects.get(row["id"])
    assert got["status"] == "failed" and "Scan again" in got["error"]


def test_a_non_address_is_refused(db):
    with pytest.raises(ValueError):
        prospects.create("not a website")


# ── Master routes ───────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_master_scans_links_and_deletes_a_prospect_and_each_step_is_audited(db):
    from vula.api import master
    with patch.object(prospects, "start") as start, patch.object(master, "audit") as audit:
        out = await master.master_scan_prospect({"url": "shopeazy.co", "name": "ShopEazy"}, ME)
        pid = out["prospect"]["id"]
        start.assert_called_once_with(pid)
        assert (await master.master_prospects())["prospects"][0]["name"] == "ShopEazy"
        linked = await master.master_link_prospect(pid, {"tenant_id": "ShopEazy"}, ME)
        assert linked["prospect"]["tenant_id"] == "ShopEazy"
        assert (await master.master_delete_prospect(pid, ME)) == {"deleted": pid}
    assert [c.args[1] for c in audit.call_args_list] == ["prospect_scanned", "prospect_became_tenant", "prospect_deleted"]


@pytest.mark.asyncio
async def test_master_gets_a_clear_answer_for_a_bad_address_or_unknown_prospect(db):
    from vula.api import master
    with pytest.raises(HTTPException) as e:
        await master.master_scan_prospect({"url": "nope"}, ME)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        await master.master_prospect("missing")
    assert e.value.status_code == 404
