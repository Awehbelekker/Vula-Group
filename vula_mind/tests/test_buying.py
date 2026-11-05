"""Vula buys for a business (5 Nov 2026): only the business's own shops, only prices printed on the
shop's page, limits and approvers checked in code, nothing bought without a yes, the receipt matched
afterwards. Made-up business, shops, people and prices throughout; no network.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from vula.commerce import buying as b

T, OTHER = "seaview-demo", "oakstreet-demo"
OWNER, STAFF = "27820000001", "27820000002"


class _Q:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.f, self.like, self.patch, self.row, self.up, self.n, self.sort = [], [], None, None, None, None, None

    def select(self, *a, **k): return self
    def eq(self, k, v): self.f.append((k, v)); return self
    def ilike(self, k, v): self.like.append((k, v.strip("%").lower())); return self
    def order(self, col, desc=False): self.sort = (col, desc); return self
    def limit(self, n): self.n = n; return self
    def insert(self, row): self.row = row; return self
    def update(self, patch): self.patch = patch; return self
    def upsert(self, row, on_conflict=None): self.up = (row, on_conflict.split(",")); return self

    def _hit(self, r):
        return (all(r.get(k) == v for k, v in self.f)
                and all(w in str(r.get(k) or "").lower() for k, w in self.like))

    def execute(self):
        rows = self.db.setdefault(self.name, [])
        if self.row is not None:
            new = {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), **self.row}
            rows.append(new)
            return SimpleNamespace(data=[dict(new)])
        if self.up:
            row, keys = self.up
            hit = next((r for r in rows if all(r.get(k) == row.get(k) for k in keys)), None)
            hit.update(row) if hit else rows.append(dict(row))
            return SimpleNamespace(data=[row])
        hit = [r for r in rows if self._hit(r)]
        if self.patch is not None:
            for r in hit:
                r.update(self.patch)
        if self.sort:
            hit = sorted(hit, key=lambda r: str(r.get(self.sort[0]) or ""), reverse=self.sort[1])
        return SimpleNamespace(data=[dict(r) for r in hit][: self.n or None])


class _DB:
    def __init__(self, **t): self.t = t
    def table(self, name): return _Q(self.t, name)


PAGES = {
    "https://inkshop.example.co.za/pg545": ("Canon PG-545 Black Ink Cartridge R 389.00 In stock",
                                            {"product": "Canon PG-545 Black", "price": "R389.00", "in_stock": True}),
    "https://paperco.example.co.za/canon-545": ("Canon PG-545 black cartridge now R349,99 incl VAT",
                                                {"product": "Canon PG-545", "price": "R349.99", "in_stock": True}),
    "https://paperco.example.co.za/made-up": ("Canon PG-545 cartridge, call for price",
                                              {"product": "Canon PG-545", "price": "R199.00", "in_stock": True}),
    "https://elsewhere.example.com/pg545": ("Canon PG-545 R99.00", {"product": "Canon PG-545", "price": "R99.00"}),
}


@pytest.fixture()
def world(monkeypatch):
    db = _DB(
        vula_buying_settings=[{"tenant_id": T, "enabled": True, "per_purchase_limit_cents": 150000,
                               "monthly_limit_cents": 300000, "who_can_ask": "team", "approver_phones": []}],
        vula_buying_shops=[
            {"id": "s-ink", "tenant_id": T, "name": "Ink Shop", "domain": "inkshop.example.co.za",
             "how": "checkout_link", "active": True},
            {"id": "s-paper", "tenant_id": T, "name": "Paper Co", "domain": "paperco.example.co.za",
             "how": "checkout_link", "active": True},
            {"id": "s-tile", "tenant_id": T, "name": "Tile Co", "how": "account", "supplier_id": "sup-1",
             "active": True},
            {"id": "s-other", "tenant_id": OTHER, "name": "Elsewhere", "domain": "elsewhere.example.com",
             "how": "checkout_link", "active": True}],
        commerce_suppliers=[{"id": "sup-1", "tenant_id": T, "name": "Tile Co", "contact_email": "orders@tileco.example"}],
        vula_price_observations=[{"tenant_id": T, "description": "Canon PG-545 black ink", "supplier": "Ink Shop",
                                  "unit_price_cents": 41000, "observed_on": "2026-09-12"}],
        vula_purchases=[], commerce_expenses=[], commerce_bank_transactions=[], commerce_purchase_orders=[])
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    monkeypatch.setattr("vula.integrations.notify._members", lambda t: [
        {"whatsapp": OWNER, "name": "Sam", "role": "owner"},
        {"whatsapp": STAFF, "name": "Thandi", "role": "staff"}] if t == T else [])
    monkeypatch.setattr("vula.integrations.notify._fallback_phone", lambda t: None)
    monkeypatch.setattr("vula.commerce.expenses.known_projects", lambda t: ["Main Road", "Overheads"])
    searched = []

    async def search(q, limit=4):
        searched.append(q)
        if "inkshop" in q:
            return [{"url": "https://inkshop.example.co.za/pg545", "title": "PG-545"},
                    {"url": "https://elsewhere.example.com/pg545", "title": "cheap elsewhere"}]
        if "paperco" in q:
            return [{"url": "https://paperco.example.co.za/canon-545", "title": "PG-545"},
                    {"url": "https://paperco.example.co.za/made-up", "title": "PG-545"}]
        return []

    async def page(url):
        return PAGES.get(url, ("", {}))[0]

    async def extract(text, want, url):
        return dict(PAGES[url][1])

    monkeypatch.setattr(b, "_search", search)
    monkeypatch.setattr(b, "_page_text", page)
    monkeypatch.setattr(b, "_extract", extract)
    sent, asked = [], []

    async def reply(to, text, tenant_id=None, idem_key=None, **k):
        sent.append((to, text))
        return True
    monkeypatch.setattr("vula.api.whatsapp._send_reply", reply)

    async def create_approval(tid, entity_type, entity_id, title, approvers, requested_by="", meta=None, **k):
        asked.append((entity_type, entity_id, title, [a["phone"] for a in approvers]))
        return {"id": "appr-1"}
    monkeypatch.setattr("vula.commerce.approvals.create_approval", create_approval)
    return SimpleNamespace(db=db, searched=searched, sent=sent, asked=asked)


def _find(world, phone=STAFF):
    return asyncio.run(b.find(T, "Canon PG-545 black ink", by_phone=phone, by_name="Thandi"))


def test_only_the_business_shops_are_searched_and_only_printed_prices_are_shown(world):
    res = _find(world)
    assert all("site:inkshop" in q or "site:paperco" in q for q in world.searched)
    online = [o for o in res["offers"] if o["how"] == "checkout_link"]
    assert [(o["shop"], o["unit_price_cents"]) for o in online] == [("Paper Co", 34999), ("Ink Shop", 38900)]
    assert all("elsewhere" not in (o.get("url") or "") for o in res["offers"])      # off the list
    assert not any(o["unit_price_cents"] == 19900 for o in res["offers"])          # not on the page
    text = b.describe_offers(res)
    assert "R349.99" in text and "You last paid R410.00 at Ink Shop" in text
    assert "Tile Co (order on account)" in text and "https://paperco.example.co.za/canon-545" in text


def test_buying_is_off_until_switched_on_and_needs_a_limit(world):
    world.db.t["vula_buying_settings"].clear()
    assert b.enabled(T) is False
    with pytest.raises(ValueError, match="limit per purchase"):
        b.save_settings(T, {"enabled": True}, by="Sam")
    cfg = b.save_settings(T, {"enabled": True, "per_purchase_limit": "R1,500", "monthly_limit": 3000}, by="Sam")
    assert cfg["enabled"] and cfg["per_purchase_limit_cents"] == 150000 and cfg["monthly_limit_cents"] == 300000
    with pytest.raises(ValueError):
        b.save_settings(T, {"who_can_ask": "anyone"})


def test_shops_need_a_real_website_or_one_of_your_suppliers(world):
    shop = b.add_shop(T, "Office Mart", domain="https://www.OfficeMart.example.co.za/ink?x=1")
    assert shop["domain"] == "officemart.example.co.za"
    with pytest.raises(ValueError):
        b.add_shop(T, "Bad", domain="not a site")
    with pytest.raises(ValueError, match="suppliers"):
        b.add_shop(T, "Nobody", how="account", supplier_id="sup-x")
    with pytest.raises(ValueError, match="already"):
        b.add_shop(T, "ink shop", domain="inkshop.example.co.za")
    b.remove_shop(T, shop["id"])
    assert all(s["id"] != shop["id"] for s in b.shops(T))


def test_who_may_ask(world):
    cfg = b.settings_for(T)
    assert b.may_ask(T, STAFF, "staff", cfg)
    assert b.may_ask(T, OWNER, None, cfg)                       # the owner, even with no team role
    assert not b.may_ask(T, "27820000009", "sales_rep", cfg)
    cfg["who_can_ask"] = "managers"
    assert not b.may_ask(T, STAFF, "staff", cfg) and b.may_ask(T, "27820000003", "manager", cfg)


def test_limits_are_checked_in_code(world):
    _find(world)
    with pytest.raises(ValueError, match="over the limit of R1,500.00 per purchase"):
        b.prepare(T, STAFF, 1, 5)                                # 5 × R349.99
    world.db.t["vula_purchases"].append({"tenant_id": T, "status": "paid", "total_cents": 280000,
                                         "approved_at": datetime.now(timezone.utc).isoformat()})
    with pytest.raises(ValueError, match="monthly limit of R3,000.00"):
        b.prepare(T, STAFF, 1, 1)
    world.db.t["vula_purchases"][-1]["status"] = "cancelled"
    with pytest.raises(ValueError, match="from 1 to"):
        b.prepare(T, STAFF, 9, 1)
    with pytest.raises(ValueError, match="no project"):
        b.prepare(T, STAFF, 1, 1, project="Atlantis")
    p = b.prepare(T, STAFF, 1, 2, project="main road")
    assert p["total_cents"] == 69998 and p["project"] == "Main Road"


def test_a_staff_request_goes_to_the_owner_and_nothing_is_bought(world):
    _find(world)
    reply = asyncio.run(b.submit(T, b.prepare(T, STAFF, 1, 2), phone=STAFF, name="Thandi"))
    assert "Sent to Sam to approve" in reply
    kind, pid, title, people = world.asked[0]
    assert kind == "purchase" and people == [OWNER] and "2 × Canon PG-545 from Paper Co" in title
    pur = b.get(T, pid)
    assert pur["status"] == "pending_approval" and pur["approval_id"] == "appr-1"
    assert not any(r["status"] == "draft" for r in world.db.t["vula_purchases"])


def test_the_owner_s_own_yes_sends_the_link_after_the_price_is_read_again(world):
    _find(world, phone=OWNER)
    reply = asyncio.run(b.submit(T, b.prepare(T, OWNER, 1, 1), phone=OWNER, name="Sam"))
    assert world.asked == []
    assert "Google Pay" in reply and "https://paperco.example.co.za/canon-545" in reply
    pur = next(r for r in world.db.t["vula_purchases"] if r["status"] == "approved")
    assert pur["approved_by"] == "Sam" and pur["total_cents"] == 34999


def test_a_price_rise_on_approval_means_ask_again(world):
    _find(world)
    asyncio.run(b.submit(T, b.prepare(T, STAFF, 1, 1), phone=STAFF, name="Thandi"))
    pid = world.asked[0][1]
    PAGES["https://paperco.example.co.za/canon-545"] = ("Canon PG-545 R399.00",
                                                        {"product": "Canon PG-545", "price": "R399.00"})
    try:
        from vula.commerce import approvals
        out = asyncio.run(approvals._BOOKING_ACTIONS["purchase"](
            {"tenant_id": T, "entity_id": pid}, {"approver_name": "Sam"}))
    finally:
        PAGES["https://paperco.example.co.za/canon-545"] = ("Canon PG-545 black cartridge now R349,99 incl VAT",
                                                            {"product": "Canon PG-545", "price": "R349.99", "in_stock": True})
    assert "Not bought" in out and "R399.00" in out
    assert b.get(T, pid)["status"] == "cancelled"


def test_a_supplier_on_account_gets_a_purchase_order(world, monkeypatch):
    made = []

    async def create_po(tid, body):
        po = {"id": str(uuid.uuid4()), "tenant_id": tid, "status": "draft", **body}
        world.db.t["commerce_purchase_orders"].append(po)
        made.append(body)
        return po

    async def send_po(tid, po_id, channel):
        for po in world.db.t["commerce_purchase_orders"]:
            if po["id"] == po_id:
                po["status"] = "sent"
        return {"sent_via": [channel]}
    monkeypatch.setattr("vula.api.commerce.admin_create_purchase_order", create_po)
    monkeypatch.setattr("vula.commerce.purchase_orders.send_purchase_order", send_po)
    res = _find(world, phone=OWNER)
    option = next(i for i, o in enumerate(res["offers"], 1) if o["how"] == "account")
    reply = asyncio.run(b.submit(T, b.prepare(T, OWNER, option, 3), phone=OWNER, name="Sam"))
    assert "sent to Tile Co by email" in reply and made[0]["supplier_id"] == "sup-1"
    assert any(r["status"] == "ordered" and r["po_id"] for r in world.db.t["vula_purchases"])


def test_the_receipt_is_matched_and_booked_to_the_project(world):
    _find(world, phone=OWNER)
    asyncio.run(b.submit(T, b.prepare(T, OWNER, 1, 2, project="Main Road"), phone=OWNER, name="Sam"))
    world.db.t["commerce_expenses"].append({"id": str(uuid.uuid4()), "tenant_id": T, "supplier": "Paper Co (Pty) Ltd",
                                            "amount_cents": 74998, "status": "paid", "project": None,
                                            "created_at": datetime.now(timezone.utc).isoformat()})
    res = asyncio.run(b.follow_up())
    assert res["matched"] == 1
    pur = next(r for r in world.db.t["vula_purchases"] if r.get("expense_id"))
    assert pur["status"] == "paid" and world.db.t["commerce_expenses"][0]["project"] == "Main Road"


def test_no_receipt_after_three_days_reminds_once(world):
    _find(world, phone=OWNER)
    asyncio.run(b.submit(T, b.prepare(T, OWNER, 1, 1), phone=OWNER, name="Sam"))
    pur = next(r for r in world.db.t["vula_purchases"] if r["status"] == "approved")
    pur["approved_at"] = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    assert asyncio.run(b.follow_up())["reminded"] == 1
    assert "Forward the receipt" in world.sent[-1][1] and world.sent[-1][0] == OWNER
    assert asyncio.run(b.follow_up())["reminded"] == 0


def test_another_business_never_sees_these_shops_or_settings(world):
    assert b.enabled(OTHER) is False
    assert [s["name"] for s in b.shops(OTHER)] == ["Elsewhere"]
    assert b.latest_draft(OTHER, STAFF) is None
    _find(world)
    assert b.latest_draft(OTHER, STAFF) is None


def test_the_tools_appear_only_when_buying_is_on_and_preview_first(world, monkeypatch):
    from core.skills import commerce_admin as ca
    monkeypatch.setattr("vula.api.tenants.enabled_modules", lambda t: [])
    names = lambda tid: {t["function"]["name"] for t in ca._tools_for(tid, role="owner")}  # noqa: E731
    assert {"find_best_price", "request_purchase"} <= names(T)
    assert "find_best_price" not in names(OTHER)
    skill = ca.CommerceAdminSkill()
    ctx = {"phone": STAFF, "caller_role": "staff", "caller_name": "Thandi"}
    found = asyncio.run(skill._find_best_price(T, {"item": "Canon PG-545 black ink"}, ctx))
    assert found["options"] >= 2 and "R349.99" in found["reply"]
    prev = asyncio.run(skill._request_purchase(T, {"option": 1, "quantity": 1}, ctx))
    assert prev["preview"] and "approver" in prev["message"] and world.asked == []
    done = asyncio.run(skill._request_purchase(T, {"option": 1, "quantity": 1, "confirm": True}, ctx))
    assert world.asked and "Sent to Sam" in str(done)
    rep = asyncio.run(skill._find_best_price(T, {"item": "ink"}, {"phone": "27820000009", "caller_role": "sales_rep"}))
    assert "error" in rep


def test_a_rejection_marks_the_purchase_rejected(world):
    _find(world)
    asyncio.run(b.submit(T, b.prepare(T, STAFF, 1, 1), phone=STAFF, name="Thandi"))
    pid = world.asked[0][1]
    b.reject(T, pid, "Sam", "not now")
    assert b.get(T, pid)["status"] == "rejected"
