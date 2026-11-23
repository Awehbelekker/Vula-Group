"""Sell › Reviews, the Google review link, the WhatsApp catalogue from the business's own screen and
Sell › Sales reports (23 Nov 2026). Ratings customers give on WhatsApp after delivery were kept
but shown nowhere; the public rating route took a rating from anyone for any business; only
Master could send the catalogue. Made-up data."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.fake_supabase import FakeSupabase
from vula.api import commerce, server
from vula.api.tenant_auth import scope_allows
from vula.commerce import reviews, service

T = "deli"
LINK = "https://g.page/r/CdeliReview/review"


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "vula_tenant_config": [{"tenant_id": T, "google_review_url": None}],
        "commerce_orders": [
            {"id": "o1", "tenant_id": T, "display_id": "DEL-00001", "customer_phone": "+27 82 555 0101",
             "customer_name": "Thandi", "review_rating": None},
            {"id": "o2", "tenant_id": T, "display_id": "DEL-00002", "customer_phone": "27825550102",
             "customer_name": "Sipho", "review_rating": 4},
            {"id": "x1", "tenant_id": "other", "display_id": "OTH-1", "customer_phone": "27825550101",
             "review_rating": None},
        ],
        "commerce_reviews": [],
    })
    monkeypatch.setattr(service, "_client", lambda: d)
    service._rating_cache.clear()
    return d


# ── the link ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,want", [
    ("https://g.page/r/Cabc/review", "https://g.page/r/Cabc/review"),
    ("g.page/r/Cabc/review", "https://g.page/r/Cabc/review"),
    ("https://search.google.com/local/writereview?placeid=ChIJ1", "https://search.google.com/local/writereview?placeid=ChIJ1"),
    ("https://maps.app.goo.gl/AbC123", "https://maps.app.goo.gl/AbC123"),
    ("", ""),
    ("http://g.page/r/Cabc/review", None),               # not https
    ("https://g.page.evil.com/r/x", None),               # looks like Google, isn't
    ("https://evil.com/?u=https://g.page/r/x", None),
    ("https://user:pw@g.page/r/x", None),
    ("https://g.page/r/x y", None),
    ("https://g.page/" + "a" * 600, None),
])
def test_only_a_google_link_is_kept(url, want):
    assert reviews.clean_review_url(url) == want


def test_the_link_is_saved_read_back_and_cleared(db):
    assert reviews.set_google_link(T, LINK) == LINK
    assert reviews.google_link(T) == LINK
    assert reviews.set_google_link(T, "") == ""
    assert reviews.google_link(T) == ""
    with pytest.raises(ValueError):
        reviews.set_google_link(T, "https://example.com/review")
    assert reviews.google_link(T) == ""


def test_a_happy_customer_gets_the_link_an_unhappy_one_a_follow_up():
    five = reviews.thanks_text(5, LINK)
    assert "5 stars" in five and LINK in five
    assert LINK in reviews.thanks_text(4, LINK)
    three = reviews.thanks_text(3, LINK)
    assert LINK not in three and "follow up" in three
    assert reviews.thanks_text(5, "") == "Thank you so much for the 5 stars! 🌟 See you next order."


# ── what the business sees ───────────────────────────────────────────────────

def test_every_rating_is_counted_past_the_thousand_row_cap(db):
    db.max_rows = 1000
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    db.rows["commerce_reviews"] = [
        {"id": f"r{i:05d}", "tenant_id": T, "order_id": "o2" if i == 0 else None, "rating": 5 if i % 2 else 3,
         "created_at": (now - timedelta(minutes=i)).isoformat()} for i in range(1500)
    ] + [{"id": "z", "tenant_id": "other", "rating": 1, "created_at": now.isoformat()}]
    s = reviews.summary(T, limit=50)
    assert s["count"] == 1500 and s["stars"]["5"] == 750 and s["stars"]["3"] == 750 and s["stars"]["1"] == 0
    assert s["average"] == 4.0 and s["low"] == 750
    assert len(s["reviews"]) == 50 and s["reviews"][0]["order_number"] == "DEL-00002"


def test_the_route_lists_and_saves(db):
    db.rows["commerce_reviews"] = [{"id": "r1", "tenant_id": T, "rating": 4, "created_at": "2026-11-20T10:00:00+00:00"}]
    c = TestClient(server.app)
    r = c.get(f"/v1/commerce/{T}/admin/reviews")
    assert r.status_code == 200 and r.json()["average"] == 4.0 and r.json()["google_review_url"] == ""
    assert c.post(f"/v1/commerce/{T}/admin/reviews/settings", json={"google_review_url": "https://bit.ly/x"}).status_code == 400
    r = c.post(f"/v1/commerce/{T}/admin/reviews/settings", json={"google_review_url": LINK})
    assert r.status_code == 200 and r.json()["google_review_url"] == LINK
    assert any(a.get("action") == "google_review_link_set" for a in db.rows.get("vula_merchant_audit", []))


# ── the public rating route ──────────────────────────────────────────────────

def test_a_rating_needs_the_order_and_its_own_customer(db):
    c = TestClient(server.app)
    assert c.post(f"/v1/commerce/{T}/reviews", json={"rating": 5}).status_code == 400
    assert c.post(f"/v1/commerce/{T}/reviews", json={"rating": 1, "order_id": "o1",
                                                     "customer_phone": "0829999999"}).status_code == 404
    assert c.post(f"/v1/commerce/{T}/reviews", json={"rating": 1, "order_id": "x1",          # another business's order
                                                     "customer_phone": "0825550101"}).status_code == 404
    assert c.post(f"/v1/commerce/{T}/reviews", json={"rating": 2, "order_id": "o2",
                                                     "customer_phone": "0825550102"}).status_code == 409
    assert db.rows["commerce_reviews"] == []
    r = c.post(f"/v1/commerce/{T}/reviews", json={"rating": 5, "order_id": "o1", "customer_phone": "082 555 0101",
                                                  "customer_name": "Someone else", "comment": "Lovely"})
    assert r.status_code == 200
    row, = db.rows["commerce_reviews"]
    assert (row["rating"], row["customer_name"], row["comment"], row["tenant_id"]) == (5, "Thandi", "Lovely", T)
    assert [o for o in db.rows["commerce_orders"] if o["id"] == "o1"][0]["review_rating"] == 5
    assert c.post(f"/v1/commerce/{T}/reviews", json={"rating": 1, "order_id": "o1",
                                                     "customer_phone": "0825550101"}).status_code == 409


# ── the rating on WhatsApp ───────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("stars,gets_link", [(5, True), (4, True), (3, False)])
async def test_the_whatsapp_thank_you_carries_the_link_for_4_and_5(db, monkeypatch, stars, gets_link):
    from vula.api import whatsapp as wa
    db.rows["vula_tenant_config"][0]["google_review_url"] = LINK
    sent = []

    async def reply(phone, text, tid=None, **kw):
        sent.append(text)
        return True

    async def team(*a, **k):
        return None

    monkeypatch.setattr(wa, "_send_reply", reply)
    monkeypatch.setattr("vula.integrations.notify.notify_team", team)
    order = dict(db.rows["commerce_orders"][0])
    assert await wa._record_rating("27825550101", T, order, stars) is True
    assert (LINK in sent[0]) is gets_link
    assert db.rows["commerce_reviews"][0]["rating"] == stars


# ── who may do what ──────────────────────────────────────────────────────────

def _first_rule(scope, path, method="GET"):
    """As the server's guard does: the first rule that matches decides."""
    for methods, rx, tabs in server._SECTION_RULES:
        if (methods is None or method in methods) and rx.match(path):
            return scope_allows(scope, tabs)
    return True


OWNER = {"full": True, "access": []}
ORDERS = {"full": False, "access": ["orders"]}
BOOKINGS = {"full": False, "access": ["bookings"]}
SALES = {"full": False, "access": ["salesreports"]}


def test_reviews_need_orders_customers_or_their_own_tab():
    p = f"/v1/commerce/{T}/admin/reviews"
    assert _first_rule(ORDERS, p) and _first_rule(OWNER, p) and not _first_rule(BOOKINGS, p)


def test_the_link_and_the_catalogue_are_owners_and_managers_only():
    for p in (f"/v1/commerce/{T}/admin/reviews/settings", f"/v1/commerce/{T}/admin/meta-catalog/sync"):
        assert _first_rule(OWNER, p, "POST") and not _first_rule(ORDERS, p, "POST")


def test_sales_reports_open_sales_by_day_and_nothing_else_of_reports():
    assert _first_rule(SALES, f"/v1/commerce/{T}/admin/reports")
    for p in ("reports/pnl", "reports/vat", "reports/statements.pdf", "reports/ledger"):
        assert not _first_rule(SALES, f"/v1/commerce/{T}/admin/{p}")


# ── the WhatsApp catalogue ───────────────────────────────────────────────────

def test_the_business_can_send_its_catalogue_not_too_often(db, monkeypatch):
    from vula.commerce import meta_catalog
    calls = []

    async def sync(tid, catalog_id=None):
        calls.append((tid, catalog_id))
        return {"status": "success", "synced": 12}

    monkeypatch.setattr(meta_catalog, "sync", sync)
    commerce._CATALOG_LAST.clear()
    c = TestClient(server.app)
    r = c.post(f"/v1/commerce/{T}/admin/meta-catalog/sync")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["synced"] == 12
    assert calls == [(T, None)]                                  # never sets a catalogue id
    assert c.post(f"/v1/commerce/{T}/admin/meta-catalog/sync").status_code == 429
    assert len(calls) == 1


def test_no_catalogue_connected_is_said_plainly_and_can_be_retried(db, monkeypatch):
    from vula.commerce import meta_catalog

    async def sync(tid, catalog_id=None):
        return {"status": "no_catalog_id", "message": "x"}

    monkeypatch.setattr(meta_catalog, "sync", sync)
    commerce._CATALOG_LAST.clear()
    c = TestClient(server.app)
    r = c.post(f"/v1/commerce/{T}/admin/meta-catalog/sync").json()
    assert r["ok"] is False and "Ask Vula support" in r["message"]
    assert c.post(f"/v1/commerce/{T}/admin/meta-catalog/sync").status_code == 200
    assert c.get(f"/v1/commerce/{T}/admin/meta-catalog").json() == {"connected": False}
