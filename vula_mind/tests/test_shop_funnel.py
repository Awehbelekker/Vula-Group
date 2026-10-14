"""The WhatsApp shop's funnel on Home (vula/commerce/shop_stats.py): opened → basket → sent to
WhatsApp → ordered → paid, plus searches — no phone numbers or message text kept."""
import pytest

from vula.commerce import service, shop_stats

TID = "seedleme"


class _Q:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters = {}

    def select(self, *_a, **_k):
        return self

    def insert(self, row):
        self.db.inserted.append((self.table, row))
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def gte(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.table == "commerce_shop_events" and self.db.no_table:
            raise RuntimeError('relation "commerce_shop_events" does not exist')
        return type("R", (), {"data": self.db.rows.get(self.table, [])})()


class _DB:
    def __init__(self, rows=None, no_table=False):
        self.rows, self.inserted, self.no_table = rows or {}, [], no_table

    def table(self, name):
        return _Q(self, name)


def test_search_terms_never_keep_numbers_or_long_text():
    assert shop_stats.clean_term("  Round Basin? ") == "round basin"
    assert shop_stats.clean_term("0821234567") is None
    assert shop_stats.clean_term("12 Long Street, Woodstock") is None
    assert shop_stats.clean_term("x" * 41) is None


def test_link_previews_and_bots_are_not_visits():
    assert shop_stats.is_bot("facebookexternalhit/1.1") and shop_stats.is_bot("WhatsApp/2.23.20 A")
    assert not shop_stats.is_bot("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")


def test_record_writes_one_small_row(monkeypatch):
    db = _DB()
    monkeypatch.setattr(service, "_client", lambda: db)
    shop_stats.record(TID, "search_miss", "Bathtub")
    shop_stats.record(TID, "search", "082 123 4567")        # a number: not kept, not recorded
    shop_stats.record(TID, "nonsense")
    assert db.inserted == [("commerce_shop_events", {"tenant_id": TID, "kind": "search_miss", "term": "bathtub"})]


def test_funnel_counts_and_searches(monkeypatch):
    ev = ([{"kind": "shop_open", "term": None}] * 40 + [{"kind": "basket", "term": None}] * 12
          + [{"kind": "basket_claimed", "term": None}] * 9 + [{"kind": "search", "term": "round basin"}] * 5
          + [{"kind": "search", "term": "butler"}] * 2 + [{"kind": "search_miss", "term": "bathtub"}] * 3)
    orders = [{"status": "paid", "total_cents": 640000, "channel": "whatsapp"},
              {"status": "delivered", "total_cents": 320000, "channel": "web"},
              {"status": "pending_payment", "total_cents": 99000, "channel": "whatsapp"},
              {"status": "paid", "total_cents": 50000, "channel": "manual"}]       # not the shop
    monkeypatch.setattr(service, "_client", lambda: _DB({"commerce_shop_events": ev, "commerce_orders": orders}))
    f = shop_stats.funnel(TID)
    assert [s["count"] for s in f["steps"]] == [40, 12, 9, 3, 2]
    assert f["paid_cents"] == 960000 and f["tracked"] is True
    assert f["top_searches"][0] == {"term": "round basin", "count": 5}
    assert f["not_found"] == [{"term": "bathtub", "count": 3}]


def test_funnel_before_the_migration_still_shows_orders(monkeypatch):
    monkeypatch.setattr(service, "_client", lambda: _DB(
        {"commerce_orders": [{"status": "paid", "total_cents": 100, "channel": "whatsapp"}]}, no_table=True))
    f = shop_stats.funnel(TID)
    assert f["tracked"] is False and f["steps"][-1]["count"] == 1


def test_whatsapp_shops_get_the_card_on_home(monkeypatch):
    from vula.api import tenants
    prof = {"is_rep_business": False, "uses_projects": False, "sells_products": True, "modules": ["orders", "wa_shop"]}
    monkeypatch.setattr(tenants, "tenant_profile", lambda tid: prof)
    cards = tenants.home_default(TID)
    assert cards.index("shop") == cards.index("trend") + 1
    prof["modules"] = ["orders"]
    assert "shop" not in tenants.home_default("off-the-hook")


@pytest.mark.asyncio
async def test_shop_page_counts_a_visit_but_not_a_link_preview(monkeypatch):
    from vula.api import menu_page
    from vula.commerce import web_basket
    seen = []
    monkeypatch.setattr(shop_stats, "record", lambda tid, kind, term=None: seen.append(kind))

    async def products(tid, category=None):
        return []

    async def inv(tid):
        return {}

    monkeypatch.setattr(web_basket, "shop_products", products)
    monkeypatch.setattr(service, "get_invoice_settings", inv)
    monkeypatch.setattr(web_basket, "wa_number", lambda tid: "")

    class Req:
        def __init__(self, ua):
            self.headers = {"user-agent": ua}

    await menu_page.photo_menu(TID, Req("Mozilla/5.0 (Linux; Android 14)"))
    await menu_page.photo_menu(TID, Req("WhatsApp/2.24 A"))
    assert seen == ["shop_open"]
