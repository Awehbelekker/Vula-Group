"""Listings as WhatsApp cards, clients' wish lists and matching (vula/listings/sharing.py,
core/skills/listing_tools.py, migration 259): cards are built from the rows (prices formatted
from cents), a client's own search becomes their wish list, a listing is matched only to people
it fits and who haven't had it, and nothing is sent to anyone without an agent confirming."""
from types import SimpleNamespace

import pytest

from core.skills import listing_tools as lt
from tests.test_listings import _Q   # the same in-memory Supabase stand-in
from vula.listings import service as ls
from vula.listings import sharing


@pytest.fixture
def env(monkeypatch):
    db = SimpleNamespace(rows={})
    db.table = lambda name: _Q(db, name)
    monkeypatch.setattr(ls, "_client", lambda: db)
    # Consent has its own tests (tests/test_privacy_audit_1113.py); nobody here opted out.
    monkeypatch.setattr("vula.consent.opted_out", lambda tenant_id, phone: False)
    sent = {"text": [], "buttons": [], "carousel": []}

    async def reply(to, text, tenant_id="", **k):
        sent["text"].append((to, text))
        return True

    async def buttons(creds, number, body, btns, image_url=None):
        sent["buttons"].append((number, body, [b["id"] for b in btns], image_url))
        return True

    async def carousel(creds, number, body, cards):
        sent["carousel"].append((number, body, cards))
        return sent.get("carousel_ok", True)

    async def creds(tid, to=None):
        return {"phone_id": "x", "token": "t"}
    from vula.api import whatsapp as wa
    monkeypatch.setattr(wa, "_send_reply", reply)
    monkeypatch.setattr(wa, "_send_wa_buttons", buttons)
    monkeypatch.setattr(wa, "_send_wa_carousel", carousel)
    monkeypatch.setattr(wa, "_resolve_wa", creds)
    from vula.integrations import notify
    monkeypatch.setattr(notify, "recipients_for", lambda tid, ev: [])

    async def no_push(*a, **k):
        return None
    monkeypatch.setattr("vula.dash_push.push_event", no_push)
    return SimpleNamespace(db=db, sent=sent)


async def _listing(title, **kw):
    base = {"title": title, "status": "active", "listing_type": "sale", "suburb": "Durbanville",
            "bedrooms": 3, "price_cents": 250_000_000, "property_type": "house",
            "images": [f"https://img/{title[:4]}.jpg"]}
    return await ls.upsert_property("estate", {**base, **kw})


# ── Cards and details ─────────────────────────────────────────────────────────
def test_cards_need_photos_and_two_or_more():
    a = {"slug": "a", "title": "A house", "images": ["https://i/a.jpg"], "price_cents": 195_000_000,
         "listing_type": "sale", "bedrooms": 3, "bathrooms": 2.5, "suburb": "Bellville"}
    b = {**a, "slug": "b", "title": "B flat", "listing_type": "rent", "price_cents": 1_250_000}
    no_photo = {**a, "slug": "c", "images": []}
    cards = sharing.cards_for([a, b, no_photo])
    assert [c["id"] for c in cards] == ["lst_v_a", "lst_v_b"]
    assert cards[0]["text"] == "*A house*\nR1 950 000 · 3 bed · 2.5 bath · Bellville"
    assert "R12 500 pm" in cards[1]["text"]
    assert sharing.cards_for([a, no_photo]) == []          # a carousel needs two cards with photos


def test_details_hide_the_address_unless_shown():
    p = {"title": "Home", "price_cents": 100_000_00, "suburb": "Durbanville", "address": "12 Oak St",
         "show_address": False, "features": ["Pool"], "levies_cents": 150_000}
    assert "12 Oak St" not in sharing.details_text(p) and "Durbanville" in sharing.details_text(p)
    assert "12 Oak St" in sharing.details_text({**p, "show_address": True})
    assert "Levies R1 500 pm" in sharing.details_text(p)


# ── Wish lists and matching ───────────────────────────────────────────────────
def test_wish_fits():
    p = {"listing_type": "sale", "price_cents": 250_000_000, "bedrooms": 3, "suburb": "Durbanville",
         "city": "Cape Town", "property_type": "house"}
    assert sharing.wish_fits({}, p)                                       # no constraints
    assert sharing.wish_fits({"max_price_cents": 300_000_000, "min_bedrooms": 3,
                              "suburbs": ["durbanville"], "property_types": ["house"]}, p)
    assert not sharing.wish_fits({"max_price_cents": 200_000_000}, p)     # over budget
    assert not sharing.wish_fits({"min_bedrooms": 4}, p)
    assert not sharing.wish_fits({"suburbs": ["Sea Point"]}, p)
    assert not sharing.wish_fits({"listing_type": "rent"}, p)
    assert not sharing.wish_fits({"max_price_cents": 1}, {**p, "price_on_request": True})
    assert not sharing.wish_fits({"active": False}, p)


@pytest.mark.asyncio
async def test_a_clients_search_becomes_their_wish_list(env):
    await _listing("Family home")
    await _listing("Second home", suburb="Bellville")
    ctx = {"tenant_id": "estate", "customer_phone": "083 555 1234"}
    out = await lt.run_customer_tool("search_listings", {"suburb": "Durbanville", "max_price": "R3m",
                                                         "min_bedrooms": 3}, ctx)
    assert out["results"] and "cards" not in ctx                      # one result: no carousel
    assert ctx["media_url"].startswith("https://img/")
    await lt.run_customer_tool("search_listings", {"suburb": "Bellville"}, ctx)
    wish = await sharing.get_wish("estate", "0835551234")
    assert wish["phone"] == "27835551234" and wish["max_price_cents"] == 300_000_000
    assert wish["suburbs"] == ["Durbanville", "Bellville"]             # areas add up, budget kept
    out = await lt.run_customer_tool("search_listings", {}, {"tenant_id": "estate", "customer_phone": "1"})
    assert len(out["results"]) == 2


@pytest.mark.asyncio
async def test_two_results_with_photos_become_cards(env):
    await _listing("Family home")
    await _listing("Second home")
    ctx = {"tenant_id": "estate", "customer_phone": "27835551234"}
    out = await lt.run_customer_tool("search_listings", {"suburb": "durbanville"}, ctx)
    assert len(ctx["cards"]) == 2 and "shown_as_photo_cards" in out


@pytest.mark.asyncio
async def test_matches_skip_people_already_sent_it(env):
    home = await _listing("Family home")
    await sharing.save_wish("estate", "27835551234", name="Thandi", suburbs=["Durbanville"], max_price_cents=300_000_000)
    await sharing.save_wish("estate", "27821110000", name="Ben", suburbs=["Sea Point"])
    assert [w["name"] for w in await sharing.matches_for("estate", home)] == ["Thandi"]
    await sharing.send_listings("estate", "27835551234", [home], intro="")
    assert await sharing.matches_for("estate", home) == []
    draft = await _listing("Draft", status="draft")
    assert await sharing.matches_for("estate", draft) == []           # not on the market


# ── Agent shares listings ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_share_listings_preview_then_carousel(env):
    await _listing("Family home")
    await _listing("Bellville townhouse", suburb="Bellville")
    await ls.create_enquiry("estate", {"name": "Thandi Nkosi", "whatsapp": "27835551234", "source": "whatsapp"})
    ctx = {"tenant_id": "estate", "caller_name": "Lindi"}
    args = {"to": "thandi", "listings": ["family home", "bellville"], "note": "Thought you'd like these"}
    prev = await lt.run_admin_tool("share_listings", args, ctx)
    assert prev["preview"] is True and prev["send_to"] == "Thandi Nkosi (27835551234)"
    assert env.sent["carousel"] == []                                  # nothing sent before confirm
    done = await lt.run_admin_tool("share_listings", {**args, "confirm": True}, ctx)
    assert done["verified"] and "Sent 2 listings to Thandi Nkosi" in done["reply_verbatim"]
    number, body, cards = env.sent["carousel"][0]
    assert number == "27835551234" and body.startswith("Thought you'd like these") and len(cards) == 2
    assert len(env.db.rows["vula_listing_shares"]) == 2
    again = await lt.run_admin_tool("share_listings", args, ctx)
    assert "already_sent_before" in again                              # the agent is told


@pytest.mark.asyncio
async def test_share_refused_by_whatsapp_is_not_reported_as_sent(env):
    await _listing("Family home")
    await _listing("Second home")
    env.sent["carousel_ok"] = False
    done = await lt.run_admin_tool("share_listings", {"to": "082 999 0000", "listings": ["family home", "second home"],
                                                      "confirm": True}, {"tenant_id": "estate"})
    assert "24 hours" in done["error"] and not env.db.rows.get("vula_listing_shares")


@pytest.mark.asyncio
async def test_one_listing_goes_with_a_book_a_viewing_button(env):
    await _listing("Family home")
    await lt.run_admin_tool("share_listings", {"to": "0829990000", "listings": ["family home"], "confirm": True},
                            {"tenant_id": "estate"})
    number, body, ids, image = env.sent["buttons"][0]
    assert number == "27829990000" and ids == ["lst_b_family-home", "lst_q_family-home"] and image


@pytest.mark.asyncio
async def test_listing_change_tells_the_agent_who_matches(env):
    await sharing.save_wish("estate", "27835551234", name="Thandi", suburbs=["Durbanville"])
    done = await lt.run_admin_tool("create_listing", {"title": "Garden cottage", "listing_type": "sale",
                                                      "suburb": "Durbanville", "price": "R1.2m", "confirm": True},
                                   {"tenant_id": "estate"})
    assert "1 client is looking for something like this: Thandi" in done["reply_verbatim"]
    assert env.sent["carousel"] == [] and env.sent["buttons"] == []    # told, not sent


@pytest.mark.asyncio
async def test_agent_records_a_clients_wish(env):
    await ls.create_enquiry("estate", {"name": "Thandi Nkosi", "whatsapp": "27835551234", "source": "whatsapp"})
    out = await lt.run_admin_tool("save_buyer_wish", {"client": "Thandi", "max_price": "R3m", "min_bedrooms": 3,
                                                      "areas": ["Durbanville"], "listing_type": "sale"},
                                  {"tenant_id": "estate"})
    assert out["verified"] and "3+ bed property to buy in Durbanville, up to R3 000 000" in out["reply_verbatim"]


# ── Card taps ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_card_taps(env):
    from vula.api import whatsapp as wa
    await _listing("Family home")
    assert await wa._handle_listing_tap("27835551234", "estate", "lst_v_family-home")
    assert env.sent["buttons"][0][2] == ["lst_b_family-home", "lst_q_family-home"]
    assert await wa._handle_listing_tap("27835551234", "estate", "lst_b_family-home")
    enq = env.db.rows["vula_property_enquiries"][-1]
    assert enq["wants_viewing"] and enq["source"] == "whatsapp" and enq["whatsapp"] == "27835551234"
    assert "arrange a time" in env.sent["text"][-1][1]
    assert await wa._handle_listing_tap("27835551234", "estate", "lst_v_gone")
    assert "no longer on the market" in env.sent["text"][-1][1]


# ── Other businesses ──────────────────────────────────────────────────────────
def test_sharing_tools_need_a_preview_and_the_listings_module():
    from core.skills import commerce_admin as ca
    assert "share_listings" in ca._PREVIEW_FIRST
    assert {"share_listings", "listing_matches", "save_buyer_wish"} <= lt.ADMIN_TOOL_NAMES


# ── Property-portal enquiry emails (vula/listings/portal_leads.py) ─────────────
from vula.listings import portal_leads as pl

P24_BODY = """You have received a new enquiry on Property24.

Listing: Family home
Name: Thandi Nkosi
Cell: 083 555 1234
Email: thandi@example.com
Message: Is the property still available? Can I view on Saturday?
"""


def test_portal_of_and_parse():
    assert pl.portal_of("Property24 <leads@mail.property24.com>") == "Property24"
    assert pl.portal_of("noreply@privateproperty.co.za") == "Private Property"
    assert pl.portal_of("scam@property24.com.evil.io") is None
    assert pl.portal_of("someone@gmail.com") is None
    lead = pl.parse_lead(P24_BODY)
    assert lead == {"name": "Thandi Nkosi", "whatsapp": "27835551234", "email": "thandi@example.com",
                    "message": "Is the property still available? Can I view on Saturday?"}
    assert pl.parse_lead("Hello there") == {"name": None, "whatsapp": None, "email": None, "message": None}


@pytest.mark.asyncio
async def test_portal_email_becomes_one_enquiry(env, monkeypatch):
    from vula.api import tenants
    monkeypatch.setattr(tenants, "enabled_modules", lambda tid: ["listings"] if tid == "estate" else ["products"])
    await _listing("Family home")
    em = {"from": "Property24 <leads@property24.com>", "subject": "New enquiry: Family home",
          "body": P24_BODY, "message_id": "<abc@p24>", "is_sent": False}
    assert await pl.maybe_take("estate", dict(em)) is True
    enq = env.db.rows["vula_property_enquiries"][-1]
    assert enq["source"] == "portal" and enq["whatsapp"] == "27835551234" and enq["property_id"]
    assert enq["message"].startswith("[Property24] Is the property still available")
    assert await pl.maybe_take("estate", dict(em)) is False             # same email: filed once
    assert len(env.db.rows["vula_property_enquiries"]) == 1
    assert env.sent["carousel"] == [] and env.sent["buttons"] == []     # the person isn't messaged
    # other businesses, our own sent mail, and non-portal mail are left alone
    assert await pl.maybe_take("shop", {**em, "message_id": "<x>"}) is False
    assert await pl.maybe_take("estate", {**em, "message_id": "<y>", "is_sent": True}) is False
    assert await pl.maybe_take("estate", {**em, "message_id": "<z>", "from": "a@gmail.com"}) is False


@pytest.mark.asyncio
async def test_unreadable_portal_email_is_still_filed_for_the_agent(env, monkeypatch):
    from vula.api import tenants
    monkeypatch.setattr(tenants, "enabled_modules", lambda tid: ["listings"])
    assert await pl.maybe_take("estate", {"from": "x@privateproperty.co.za", "subject": "New lead for you",
                                          "body": "", "message_id": "<html-only>", "is_sent": False})
    enq = env.db.rows["vula_property_enquiries"][-1]
    assert enq["name"] == "Private Property enquiry" and "check your inbox" in enq["message"]
    assert enq["property_id"] is None and enq["whatsapp"] is None
