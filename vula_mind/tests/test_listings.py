"""Property listings for real-estate businesses (vula/listings/service.py, vula/api/listings.py,
migration 254): prices are parsed to integer cents server-side, the public view never leaks a
hidden address or a draft, every query is scoped to the tenant, and an enquiry reaches the
listing's agent and the team."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from vula.api import listings as api
from vula.listings import service as ls


# ── A tiny in-memory stand-in for the Supabase client ────────────────────────
class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.vals, self.filters = db, table, "select", None, []

    def select(self, *_a): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a): return self

    def insert(self, row):
        self.op, self.vals = "insert", row
        return self

    def update(self, row):
        self.op, self.vals = "update", row
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, c, v):
        self.filters.append(lambda r, c=c, v=v: r.get(c) == v)
        return self

    def in_(self, c, vs):
        self.filters.append(lambda r, c=c, vs=tuple(vs): r.get(c) in vs)
        return self

    def gte(self, c, v):
        if c != "created_at":
            self.filters.append(lambda r, c=c, v=v: r.get(c) is not None and r.get(c) >= v)
        return self

    def lte(self, c, v):
        self.filters.append(lambda r, c=c, v=v: r.get(c) is not None and r.get(c) <= v)
        return self

    def ilike(self, c, pattern):
        needle = pattern.strip("%").lower()
        self.filters.append(lambda r, c=c: needle in (r.get(c) or "").lower())
        return self

    def execute(self):
        rows = self.db.rows.setdefault(self.table, [])
        match = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "insert":
            row = {"id": f"{self.table}-{len(rows) + 1}", **self.vals}
            rows.append(row)
            return SimpleNamespace(data=[dict(row)])
        if self.op == "update":
            for r in match:
                r.update(self.vals)
            return SimpleNamespace(data=[dict(r) for r in match])
        if self.op == "delete":
            self.db.rows[self.table] = [r for r in rows if r not in match]
            return SimpleNamespace(data=match)
        return SimpleNamespace(data=[dict(r) for r in match])


@pytest.fixture
def env(monkeypatch):
    db = SimpleNamespace(rows={})
    db.table = lambda name: _Q(db, name)
    monkeypatch.setattr(ls, "_client", lambda: db)
    sent = []

    async def send(to, text, tenant_id="", **k):
        sent.append((to, text))
        return True
    monkeypatch.setattr("vula.api.whatsapp._send_reply", send)
    from vula.integrations import notify
    monkeypatch.setattr(notify, "recipients_for", lambda tid, ev: ["27820000001"])

    async def push(*a, **k):
        return None
    monkeypatch.setattr("vula.dash_push.push_event", push)
    return SimpleNamespace(db=db, sent=sent)


def _req(ip="1.2.3.4"):
    return SimpleNamespace(headers={"x-forwarded-for": ip}, client=None)


# ── Prices ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("typed,cents", [
    ("R2.4m", 240_000_000),
    ("R 1 250 000", 125_000_000),
    ("1,250,000", 125_000_000),
    ("8 500", 850_000),
    ("850k", 85_000_000),
    (2400000, 240_000_000),
    ("", None),
    (None, None),
    ("two million", None),
    ("2,4", None),            # decimal comma or thousands? never guess
])
def test_prices_are_parsed_to_cents_without_guessing(typed, cents):
    assert ls.parse_rands_to_cents(typed) == cents


def test_format_price():
    assert ls.format_price({"price_cents": 245_000_000, "listing_type": "sale"}) == "R2 450 000"
    assert ls.format_price({"price_cents": 1_250_000, "listing_type": "rent"}) == "R12 500 pm"
    assert ls.format_price({"price_cents": 1, "price_on_request": True}) == "Price on request"


def test_bad_listing_input_is_refused_with_a_reason():
    with pytest.raises(ValueError, match="title"):
        ls.clean_property({"title": "  "})
    with pytest.raises(ValueError, match="price"):
        ls.clean_property({"title": "House", "price": "lots"})
    with pytest.raises(ValueError, match="listing_type"):
        ls.clean_property({"title": "House", "listing_type": "auction"})
    row = ls.clean_property({"title": "House", "price": "R2.4m", "features": "Pool, Solar,",
                             "property_type": "Mansion", "bedrooms": "3.5", "bogus": 1})
    assert row["price_cents"] == 240_000_000 and row["features"] == ["Pool", "Solar"]
    assert row["property_type"] == "other" and row["bedrooms"] == 3.5 and "bogus" not in row


# ── Listings ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_create_reads_back_and_gives_unique_slugs(env):
    a = await ls.upsert_property("estate-a", {"title": "3 Bed House in Durbanville", "price": "R2.4m"})
    b = await ls.upsert_property("estate-a", {"title": "3 Bed House in Durbanville"})
    other = await ls.upsert_property("estate-b", {"title": "3 Bed House in Durbanville"})
    assert a["slug"] == "3-bed-house-in-durbanville" and b["slug"] == "3-bed-house-in-durbanville-2"
    assert other["slug"] == "3-bed-house-in-durbanville"          # slugs are per business
    assert a["price_cents"] == 240_000_000


@pytest.mark.asyncio
async def test_an_agent_from_another_business_cant_be_attached(env):
    theirs = await ls.upsert_agent("estate-b", {"name": "Someone Else"})
    with pytest.raises(ValueError, match="agent"):
        await ls.upsert_property("estate-a", {"title": "Flat", "agent_id": theirs["id"]})


@pytest.mark.asyncio
async def test_public_view_hides_drafts_addresses_and_other_tenants(env):
    agent = await ls.upsert_agent("estate-a", {"name": "Lindi", "whatsapp": "0821112222"})
    await ls.upsert_property("estate-a", {"title": "Live", "status": "active", "address": "12 Oak St",
                                          "latitude": -33.8, "agent_id": agent["id"], "price": 1_500_000})
    await ls.upsert_property("estate-a", {"title": "Draft", "status": "draft"})
    await ls.upsert_property("estate-a", {"title": "Shown", "status": "active", "address": "1 Main Rd",
                                          "show_address": True, "price_on_request": True, "price": 9})
    await ls.upsert_property("estate-a", {"title": "Gone", "status": "sold"})
    await ls.upsert_property("estate-b", {"title": "Not ours", "status": "active"})

    rows = await ls.public_properties("estate-a")
    by_title = {r["title"]: r for r in rows}
    assert set(by_title) == {"Live", "Shown"}
    assert "address" not in by_title["Live"] and "latitude" not in by_title["Live"]
    assert by_title["Live"]["agent"]["name"] == "Lindi"
    assert by_title["Shown"]["address"] == "1 Main Rd" and by_title["Shown"]["price_cents"] is None
    assert {r["title"] for r in await ls.public_properties("estate-a", include_sold=True)} == {"Live", "Shown", "Gone"}
    assert await ls.public_property("estate-a", "draft") is None


@pytest.mark.asyncio
async def test_public_filters(env):
    for title, kind, price, beds, suburb in [
        ("Cheap flat", "rent", 800_000, 1, "Sea Point"),
        ("Family home", "sale", 300_000_000, 4, "Durbanville"),
        ("Starter home", "sale", 150_000_000, 2, "Bellville"),
    ]:
        await ls.upsert_property("estate-a", {"title": title, "status": "active", "listing_type": kind,
                                              "price_cents": price, "bedrooms": beds, "suburb": suburb})
    res = await api.public_list("estate-a", type="sale", max_price="R2m", beds=None, suburb=None,
                                property_type=None, min_price=None, featured=False, include_sold=False)
    assert [p["title"] for p in res["properties"]] == ["Starter home"]
    res = await api.public_list("estate-a", type=None, beds=3, suburb="durban", property_type=None,
                                min_price=None, max_price=None, featured=False, include_sold=False)
    assert [p["title"] for p in res["properties"]] == ["Family home"]
    with pytest.raises(HTTPException) as bad:
        await api.public_list("estate-a", min_price="cheap", type=None, max_price=None, beds=None,
                              suburb=None, property_type=None, featured=False, include_sold=False)
    assert bad.value.status_code == 422


# ── Enquiries ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_an_enquiry_is_stored_and_reaches_the_agent_and_team(env):
    agent = await ls.upsert_agent("estate-a", {"name": "Lindi", "whatsapp": "082 111 2222"})
    await ls.upsert_property("estate-a", {"title": "Family home", "status": "active",
                                          "price_cents": 245_000_000, "agent_id": agent["id"]})
    r = await api.create_enquiry("estate-a", api.EnquiryIn(
        name=" Thandi ", whatsapp="083 555 1234", property_slug="family-home",
        message="Can I view on Saturday?", wants_viewing=True), _req())
    assert r == {"ok": True}
    stored = env.db.rows["vula_property_enquiries"][0]
    assert stored["tenant_id"] == "estate-a" and stored["whatsapp"] == "27835551234"
    assert stored["wants_viewing"] is True and stored["property_id"]
    recipients = {to for to, _ in env.sent}
    assert recipients == {"27820000001", "27821112222"}            # team + the listing's agent
    text = env.sent[0][1]
    assert "Family home" in text and "R2 450 000" in text and "https://wa.me/27835551234" in text


@pytest.mark.asyncio
async def test_enquiry_guards(env):
    await ls.upsert_property("estate-a", {"title": "Draft", "status": "draft"})
    assert await api.create_enquiry("estate-a", api.EnquiryIn(name="x", whatsapp="0821234567",
                                                              website="spam.biz"), _req()) == {"ok": True}
    assert not env.db.rows.get("vula_property_enquiries")         # honeypot: quietly dropped
    for body, code in [
        (api.EnquiryIn(name="A"), 422),                                  # no way to reach them
        (api.EnquiryIn(name="A", whatsapp="123"), 422),
        (api.EnquiryIn(name="A", email="not-an-email"), 422),
        (api.EnquiryIn(name="A", email="a@b.co", property_slug="draft"), 404),   # drafts aren't public
    ]:
        with pytest.raises(HTTPException) as e:
            await api.create_enquiry("estate-a", body, _req())
        assert e.value.status_code == code


# ── Business type wiring ──────────────────────────────────────────────────────
def test_real_estate_business_type():
    from vula.api import tenants
    from vula.api.onboarding import _map_business_type
    from vula.commerce import business_profile, starter_kb
    assert _map_business_type("Estate agency in Cape Town") == "real_estate"
    assert _map_business_type("Property sales agent") == "real_estate"
    assert _map_business_type("Letting agent") == "real_estate"
    assert _map_business_type("Flooring sales rep for Gerflor") == "rep"     # unchanged
    # existing kinds of business don't drift into real estate
    assert _map_business_type("Property maintenance and renovations") == "trades"
    assert _map_business_type("Property development and construction") == "trades"
    assert _map_business_type("Seafood retail") == "food"
    preset = tenants.BUSINESS_TYPES["real_estate"]
    assert "listings" in preset["modules"] and "bookings" in preset["modules"]
    assert tenants.valid_modules(preset["modules"]) == preset["modules"]
    assert {q["key"] for q in business_profile.questions("real_estate")} >= {"areas", "viewings"}
    assert starter_kb.STARTER_SLOTS["real_estate"]
