"""Order progress on WhatsApp (vula/commerce/order_updates.py): packing / on its way / delivered
reach the customer with a photo and a button; a rating tap is recorded once, for that customer."""
import pytest

import vula.api.whatsapp as wa
from vula.commerce import order_updates, order_slip, service, web_basket

TID = "seedleme"
ORDER = {"id": "o-1", "display_id": "SEE-00012", "customer_name": "Thandi Mokoena",
         "customer_phone": "27821234567", "status": "paid", "delivery_slot": "Thu 10:00–12:00",
         "commerce_order_items": [{"product_id": "p-1", "product_name": "Round Drop-In Basin"}]}


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.payload, self.filters = db, table, "select", None, {}

    def select(self, *_a, **_k):
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        self.db.calls.append((self.table, self.op, self.payload, dict(self.filters)))
        if self.op != "select":
            return type("R", (), {"data": [self.payload]})()
        rows = [r for r in self.db.rows.get(self.table, [])
                if all(r.get(k) == v for k, v in self.filters.items())]
        return type("R", (), {"data": rows})()


class _DB:
    def __init__(self, rows=None):
        self.rows, self.calls = rows or {}, []

    def table(self, name):
        return _Q(self, name)


@pytest.fixture
def sent(monkeypatch):
    db = _DB({"commerce_products": [{"id": "p-1", "image_url": "https://cdn.shopify.com/basin.jpg"}],
              "commerce_orders": [{**ORDER, "tenant_id": TID, "status": "delivered", "review_rating": None}]})
    out = {"db": db, "buttons": [], "cta": [], "templates": [], "replies": [], "ok": True}

    async def resolve(tid):
        return {"phone_id": "1387582947771270", "token": "t"}

    async def buttons(creds, number, body, btns, image_url=None):
        out["buttons"].append((number, body, btns, image_url))
        return out["ok"]

    async def cta(creds, number, body, label, url, image_url=None):
        out["cta"].append((number, body, label, url, image_url))
        return out["ok"]

    async def template(tid, phone, name, params, **kw):
        out["templates"].append((name, params))
        return True

    async def reply(phone, text, tid=None, **kw):
        out["replies"].append(text)
        return True

    monkeypatch.setattr(service, "_client", lambda: db)
    monkeypatch.setattr(wa, "_resolve_wa", resolve)
    monkeypatch.setattr(wa, "_send_wa_buttons", buttons)
    monkeypatch.setattr(wa, "_send_wa_cta_url", cta)
    monkeypatch.setattr(wa, "send_template", template)
    monkeypatch.setattr(wa, "_send_reply", reply)
    monkeypatch.setattr(web_basket, "public_base", lambda tid: "https://shop.seedleme.com")
    monkeypatch.setattr("vula.api.tenants.display_name", lambda tid: "SeedleMe")
    return out


def test_message_per_step():
    assert order_updates.message(ORDER, "packing", "SeedleMe") == \
        "Hi Thandi! 📦 We're packing your order SEE-00012 now.\n\n— SeedleMe"
    on_way = order_updates.message(ORDER, "dispatched", "SeedleMe")
    assert "on its way" in on_way and "Expected: Thu 10:00–12:00." in on_way
    assert "How was it?" in order_updates.message(ORDER, "delivered", "SeedleMe")
    assert order_updates.message(ORDER, "paid", "SeedleMe") is None


@pytest.mark.asyncio
async def test_dispatched_sends_photo_and_view_order_button(sent):
    assert await order_updates.notify(TID, ORDER, "dispatched") is True
    number, body, label, url, image = sent["cta"][0]
    assert label == "View order" and "on its way" in body
    assert url == order_slip.urls("o-1", TID)["page"] and url.startswith("https://shop.seedleme.com/receipt/")
    assert image == "https://cdn.shopify.com/basin.jpg?width=800"
    assert not sent["buttons"] and not sent["templates"]
    # only "delivered" marks the review as asked
    assert not [c for c in sent["db"].calls if c[1] == "update"]


@pytest.mark.asyncio
async def test_delivered_asks_for_a_rating_with_buttons(sent):
    assert await order_updates.notify(TID, ORDER, "delivered") is True
    _, body, btns, image = sent["buttons"][0]
    assert [b["id"] for b in btns] == ["rate_o-1_5", "rate_o-1_4", "rate_o-1_2"]
    assert image and "How was it?" in body
    upd = [c for c in sent["db"].calls if c[0] == "commerce_orders" and c[1] == "update"]
    assert upd and "review_requested_at" in upd[0][2] and upd[0][3] == {"id": "o-1", "tenant_id": TID}


@pytest.mark.asyncio
async def test_outside_the_window_falls_back_to_the_template(sent):
    sent["ok"] = False                     # Meta refuses free-form outside 24h
    assert await order_updates.notify(TID, ORDER, "packing") is True
    name, params = sent["templates"][0]
    assert name.endswith("_order_update")
    assert params == ["Thandi Mokoena", "SEE-00012", "📦 We're packing your order SEE-00012 now."]


@pytest.mark.asyncio
async def test_no_phone_sends_nothing(sent):
    assert await order_updates.notify(TID, {**ORDER, "customer_phone": None}, "packing") is False
    assert not sent["cta"] and not sent["templates"]


@pytest.mark.asyncio
async def test_rating_tap_recorded_for_the_orders_own_customer(sent):
    await wa._handle_commerce_interactive("27821234567", "rate_o-1_5", "😍 Loved it", "m1", TID)
    inserts = [c for c in sent["db"].calls if c[0] == "commerce_reviews" and c[1] == "insert"]
    assert len(inserts) == 1 and inserts[0][2]["rating"] == 5 and inserts[0][2]["order_id"] == "o-1"
    assert "5 stars" in sent["replies"][-1]


@pytest.mark.asyncio
async def test_rating_tap_from_another_phone_is_ignored(sent):
    await wa._handle_commerce_interactive("27839999999", "rate_o-1_2", "😕 Not great", "m2", TID)
    assert not [c for c in sent["db"].calls if c[0] == "commerce_reviews"]


@pytest.mark.asyncio
async def test_rating_only_once(sent):
    assert await wa._record_rating("27821234567", TID, {**ORDER, "review_rating": 4}, 5) is True
    assert not [c for c in sent["db"].calls if c[0] == "commerce_reviews"]
    assert "already got your rating" in sent["replies"][-1]


@pytest.mark.asyncio
async def test_admin_status_change_tells_the_customer(monkeypatch):
    import core.skills.commerce_admin as ca
    monkeypatch.setattr(ca, "_emit", lambda **kw: None)

    async def list_orders(tid, **kw):
        return [{"id": "o-1", "display_id": "SEE-00012", "status": "paid"}]

    async def update_order_status(order_id, status):
        return None

    async def get_order(order_id):
        return {**ORDER, "status": "dispatched"}

    told = []

    async def notify(tid, order, status):
        told.append(status)
        return True

    monkeypatch.setattr(ca.service, "list_orders", list_orders)
    monkeypatch.setattr(ca.service, "update_order_status", update_order_status)
    monkeypatch.setattr(ca.service, "get_order", get_order)
    monkeypatch.setattr(order_updates, "notify", notify)
    res = await ca.CommerceAdminSkill()._update_order_status(TID, "SEE-00012", "dispatched")
    assert told == ["dispatched"]
    assert res["reply_verbatim"].endswith("📲 The customer has been told on WhatsApp.")

