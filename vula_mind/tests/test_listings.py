"""Property listings for real-estate businesses (vula/listings/service.py, vula/api/listings.py,
migration 257): prices are parsed to integer cents server-side, the public view never leaks a
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
                                          "show_address": True, "price_on_request": True, "price": 9,
                                          "latitude": -33.9, "longitude": 18.4})
    await ls.upsert_property("estate-a", {"title": "Gone", "status": "sold"})
    await ls.upsert_property("estate-b", {"title": "Not ours", "status": "active"})

    rows = await ls.public_properties("estate-a")
    by_title = {r["title"]: r for r in rows}
    assert set(by_title) == {"Live", "Shown"}
    assert "address" not in by_title["Live"] and "latitude" not in by_title["Live"]
    assert by_title["Live"]["agent"]["name"] == "Lindi"
    assert by_title["Shown"]["address"] == "1 Main Rd" and by_title["Shown"]["price_cents"] is None
    assert by_title["Shown"]["latitude"] == -33.9                      # pin only with the address
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


# ── WhatsApp tools (core/skills/listing_tools.py) ─────────────────────────────
from core.skills import listing_tools as lt


def test_match_listings_by_ref_title_or_words():
    rows = [{"id": "1", "slug": "family-home", "title": "4 Bed Family Home", "suburb": "Durbanville"},
            {"id": "2", "slug": "sea-point-flat", "title": "2 Bed Apartment", "suburb": "Sea Point"},
            {"id": "3", "slug": "bellville-flat", "title": "2 Bed Apartment", "suburb": "Bellville"}]
    assert [r["id"] for r in lt.match_listings(rows, "family-home")] == ["1"]
    assert [r["id"] for r in lt.match_listings(rows, "the Durbanville house")] == ["1"]
    assert [r["id"] for r in lt.match_listings(rows, "the apartment in sea point")] == ["2"]
    assert {r["id"] for r in lt.match_listings(rows, "2 bed apartment")} == {"2", "3"}   # ambiguous
    assert lt.match_listings(rows, "castle") == []


@pytest.mark.asyncio
async def test_customer_search_details_and_viewing(env):
    agent = await ls.upsert_agent("estate-a", {"name": "Lindi", "whatsapp": "0821112222"})
    await ls.upsert_property("estate-a", {"title": "Family home", "status": "active", "suburb": "Durbanville",
                                          "price_cents": 245_000_000, "bedrooms": 4, "agent_id": agent["id"],
                                          "address": "12 Oak St", "images": ["https://img/1.jpg"]})
    await ls.upsert_property("estate-a", {"title": "Secret draft", "status": "draft", "suburb": "Durbanville"})
    ctx = {"tenant_id": "estate-a", "customer_phone": "27835551234"}

    res = await lt.run_customer_tool("search_listings", {"suburb": "durbanville", "max_price": "R3m"}, ctx)
    assert [r["title"] for r in res["results"]] == ["Family home"]
    assert res["results"][0]["price"] == "R2 450 000"
    assert (await lt.run_customer_tool("search_listings", {"max_price": "lots"}, ctx))["error"]

    det = await lt.run_customer_tool("listing_details", {"listing": "the durbanville home"}, ctx)
    assert det["agent"] == "Lindi" and "address" not in det          # address hidden by default
    assert ctx["media_url"] == "https://img/1.jpg"
    assert "error" in await lt.run_customer_tool("listing_details", {"listing": "secret draft"}, ctx)

    out = await lt.run_customer_tool("request_viewing", {"listing": "family-home", "customer_name": "Thandi",
                                                          "preferred_times": "Saturday morning"}, ctx)
    assert out["sent"] and "Lindi" in out["message"]
    enq = env.db.rows["vula_property_enquiries"][-1]
    assert enq["source"] == "whatsapp" and enq["wants_viewing"] and enq["preferred_times"] == "Saturday morning"
    assert any("Saturday morning" in t for _, t in env.sent)


@pytest.mark.asyncio
async def test_agent_create_and_update_need_a_preview_then_read_back(env):
    await ls.upsert_agent("estate-a", {"name": "Lindi Mokoena"})
    ctx = {"tenant_id": "estate-a", "phone": "27820000001"}
    args = {"title": "3 bed house in Bellville", "listing_type": "sale", "price": "R1.95m",
            "suburb": "Bellville", "bedrooms": 3, "agent": "lindi"}
    prev = await lt.run_admin_tool("create_listing", args, ctx)
    assert prev["preview"] is True and prev["price"] == "R1 950 000" and prev["on_website"] == "yes"
    assert not env.db.rows.get("vula_properties")                    # nothing saved yet
    done = await lt.run_admin_tool("create_listing", {**args, "confirm": True}, ctx)
    assert done["verified"] and "R1 950 000" in done["reply_verbatim"]
    row = env.db.rows["vula_properties"][0]
    assert row["price_cents"] == 195_000_000 and row["status"] == "active" and row["agent_id"]

    prev = await lt.run_admin_tool("update_listing", {"listing": "bellville", "price": "1.85m",
                                                      "status": "under_offer"}, ctx)
    assert prev["preview"] is True
    assert prev["price"] == "R1 950 000 → R1 850 000" and prev["status"] == "active → under offer"
    assert row["price_cents"] == 195_000_000                         # still unchanged
    done = await lt.run_admin_tool("update_listing", {"listing": "bellville", "price": "1.85m",
                                                      "status": "under_offer", "confirm": True}, ctx)
    assert done["verified"] and row["price_cents"] == 185_000_000 and row["status"] == "under_offer"

    bad = await lt.run_admin_tool("create_listing", {"title": "X", "listing_type": "sale", "agent": "nobody"}, ctx)
    assert "No agent called" in bad["error"]


def test_listing_tools_only_for_listings_businesses(monkeypatch):
    from core.skills import commerce_admin as ca
    from vula.api import tenants
    names = lambda tid: {t["function"]["name"] for t in ca._tools_for(tid)}
    profile = {"known": True, "sells_products": False}
    monkeypatch.setattr(tenants, "tenant_profile", lambda tid: profile)
    monkeypatch.setattr(tenants, "uses_projects", lambda tid: False)
    for mods in ([], ["invoices", "crm"], ["products", "orders"]):      # [] = no module config
        monkeypatch.setattr(tenants, "enabled_modules", lambda tid, m=mods: m)
        assert not lt.ADMIN_TOOL_NAMES & names("shop"), mods
    monkeypatch.setattr(tenants, "enabled_modules", lambda tid: ["listings", "crm"])
    assert lt.ADMIN_TOOL_NAMES <= names("estate")
    assert lt.ADMIN_PREVIEW_FIRST <= ca._PREVIEW_FIRST
    # "sold out" must not change a shop's matched tool groups
    assert ca._match_groups("we sold out of hake") is None


@pytest.mark.asyncio
async def test_other_businesses_cant_reach_listing_tools(monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    from core.skills.commerce_assistant import CommerceAssistantSkill
    monkeypatch.setattr(lt, "tenant_has_listings", lambda tid: False)
    r = await CommerceAdminSkill()._dispatch_tool("create_listing", {"title": "x"}, {"tenant_id": "shop"})
    assert "isn't available" in r["error"]
    r = await CommerceAssistantSkill()._dispatch_tool(
        "search_listings", {}, {"tenant_id": "shop", "session_id": "s", "customer_phone": None})
    assert "unknown tool" in r["error"]


@pytest.mark.asyncio
async def test_listing_photos_added_read_back_and_removed_after_preview(env):
    await ls.upsert_property("estate-a", {"title": "Family home", "status": "active", "suburb": "Durbanville"})
    assert lt.LISTING_PHOTO_RE.match("photos Durbanville home").group("name") == "Durbanville home"
    assert lt.LISTING_PHOTO_RE.match("Lovely kitchen!") is None
    r1 = await lt.add_listing_photo("estate-a", "durbanville home", "https://img/1.jpg")
    r2 = await lt.add_listing_photo("estate-a", "family-home", "https://img/2.jpg")
    assert r1["ok"] and "(the cover)" in r1["reply"] and "on the website" in r1["reply"]
    assert r2["ok"] and "photo 2" in r2["reply"]
    assert (await lt.add_listing_photo("estate-a", "castle", "https://img/3.jpg")) == {"no_match": True}
    ctx = {"tenant_id": "estate-a"}
    prev = await lt.run_admin_tool("remove_listing_photo", {"listing": "family home", "photo_number": 1}, ctx)
    assert prev["preview"] and env.db.rows["vula_properties"][0]["images"] == ["https://img/1.jpg", "https://img/2.jpg"]
    done = await lt.run_admin_tool("remove_listing_photo", {"listing": "family home", "photo_number": 1,
                                                            "confirm": True}, ctx)
    assert done["verified"] and env.db.rows["vula_properties"][0]["images"] == ["https://img/2.jpg"]


@pytest.mark.asyncio
async def test_photo_captions_on_other_businesses_fall_through(monkeypatch):
    from vula.api import whatsapp as wa
    monkeypatch.setattr(lt, "tenant_has_listings", lambda tid: False)
    monkeypatch.setattr(wa, "_is_team", lambda tid, p: True)
    # "photo Hake Fillets" on a shop must still reach the product-photo handler
    assert await wa._maybe_listing_photo("off-the-hook", "27820000001", "m1", "photo Hake Fillets", None) is False


@pytest.mark.asyncio
async def test_website_enquiry_is_acknowledged_only_with_an_approved_template(env, monkeypatch):
    from config import settings
    from vula.api import tenants
    sent = []

    async def fake_template(tid, to, name, params, lang=None):
        sent.append((to, name, params))
        return True
    monkeypatch.setattr("vula.api.whatsapp.send_template", fake_template)
    monkeypatch.setattr(tenants, "get_config", lambda tid, **k: {"display_name": "Oak & Stone"})
    await ls.upsert_property("estate-a", {"title": "Family home", "status": "active"})
    body = {"name": "Thandi Nkosi", "whatsapp": "27835551234", "property_slug": "family-home"}

    monkeypatch.setattr(settings, "whatsapp_enquiry_template", "")
    await ls.create_enquiry("estate-a", dict(body))
    assert sent == []                                            # off until a template is approved
    monkeypatch.setattr(settings, "whatsapp_enquiry_template", "enquiry_received")
    await ls.create_enquiry("estate-a", dict(body))
    assert sent == [("27835551234", "enquiry_received", ["Thandi", "Oak & Stone", "Family home"])]
    await ls.create_enquiry("estate-a", {**body, "source": "whatsapp"})      # already chatting
    await ls.create_enquiry("estate-a", {"name": "Ben", "email": "b@x.co"})  # no WhatsApp number
    assert len(sent) == 1
