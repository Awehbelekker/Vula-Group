"""A new business set up over WhatsApp from Vula's own number (5 Nov 2026): Master makes it with
the owner's name and number, Vula's number sends the welcome, the owner answers one question at a
time (each read back from the database), and until the business has a number of its own its
people work with Vula through Vula's number. Made-up business, people and details; no network.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from config import settings
from vula import onboard_wa as ob
from vula.api import whatsapp as _wa
from vula.integrations import platform_support as ps

T, OTHER = "seaview-tiles", "oakstreet-demo"
OWNER, STAFF, STRANGER = "27820000001", "27820000002", "27829999999"
VULA_PNID = "VULA-OWN-NUMBER"
_real_creds = _wa._get_tenant_wa_creds   # conftest stubs the lookup for every test


class _Q:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.f, self.ins, self.patch, self.row, self.up, self.n = [], [], None, None, None, None

    def select(self, *a, **k): return self
    def eq(self, k, v): self.f.append((k, v)); return self
    def in_(self, k, vs): self.ins.append((k, list(vs))); return self
    def order(self, *a, **k): return self
    def limit(self, n): self.n = n; return self
    def insert(self, row): self.row = row; return self
    def update(self, patch): self.patch = patch; return self
    def upsert(self, row, on_conflict=None):
        self.up = (row, (on_conflict or "tenant_id").split(",")); return self

    def _hit(self, r):
        return all(r.get(k) == v for k, v in self.f) and all(r.get(k) in vs for k, vs in self.ins)

    def execute(self):
        rows = self.db.setdefault(self.name, [])
        if self.row is not None:
            new = {"id": str(uuid.uuid4()), **self.row}
            rows.append(new)
            return SimpleNamespace(data=[dict(new)])
        if self.up:
            row, keys = self.up
            hit = next((r for r in rows if all(r.get(k) == row.get(k) for k in keys)), None)
            if hit:
                hit.update(row)
            else:
                hit = {"id": str(uuid.uuid4()), **row}
                rows.append(hit)
            return SimpleNamespace(data=[dict(hit)])
        hit = [r for r in rows if self._hit(r)]
        if self.patch is not None:
            for r in hit:
                r.update(self.patch)
        return SimpleNamespace(data=[dict(r) for r in hit][: self.n or None])


class _DB:
    def __init__(self, **t): self.t = t
    def table(self, name): return _Q(self.t, name)


@pytest.fixture()
def world(monkeypatch):
    db = _DB(vula_tenant_config=[{"tenant_id": OTHER, "display_name": "Oak Street"}],
             vula_team_members=[{"tenant_id": OTHER, "name": "Pat", "whatsapp": STAFF, "role": "owner", "active": True}],
             vula_onboarding=[], vula_whatsapp_accounts=[])
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    from vula import team_index
    team_index.invalidate()
    ob._USES_PLATFORM.clear()
    ob._LEADS.clear()
    monkeypatch.setattr(settings, "platform_wa_phone_id", VULA_PNID)
    monkeypatch.setattr(settings, "platform_wa_token", "vula-own-token")
    monkeypatch.setattr(settings, "platform_waba_id", "VULA-WABA")
    sent, alerts = [], []

    async def send_template(to, name, *params):
        sent.append(("template", to, name, params))
        return True

    async def send_text(to, text):
        sent.append(("text", to, text))
        return True

    async def alert(text, log_text=True):
        alerts.append(text)
        return True
    monkeypatch.setattr(ps, "send_template", send_template)
    monkeypatch.setattr(ps, "send_text", send_text)
    monkeypatch.setattr(ps, "alert_operator", alert)

    async def create_tenant(body, identity=None):
        db.t["vula_tenant_config"].append({"tenant_id": body.tenant_id, "display_name": body.display_name,
                                           "business_type": body.business_type})
        return {"tenant": {"tenant_id": body.tenant_id}}
    monkeypatch.setattr("vula.api.tenants.create_tenant", create_tenant)
    monkeypatch.setattr("vula.api.tenants.get_config", lambda t, fresh=False: next(
        (r for r in db.t["vula_tenant_config"] if r["tenant_id"] == t), None))
    monkeypatch.setattr("vula.api.tenants.dashboard_url_for", lambda t: f"https://{t}.vula-ai.com")

    inv, profile, orders = {}, {}, {}

    async def upsert_inv(tid, data):
        inv.setdefault(tid, {}).update(data)
        return inv[tid]

    async def get_inv(tid):
        return dict(inv.get(tid) or {})

    async def save_answers(tid, updates, by="", public_keys=None):
        profile.setdefault(tid, {}).update(updates)
        return {"saved": True}
    monkeypatch.setattr("vula.commerce.service.upsert_invoice_settings", upsert_inv)
    monkeypatch.setattr("vula.commerce.service.get_invoice_settings", get_inv)
    monkeypatch.setattr("vula.commerce.business_profile.save_answers", save_answers)
    monkeypatch.setattr("vula.commerce.business_profile.get_answers", lambda tid: dict(profile.get(tid) or {}))
    monkeypatch.setattr("vula.commerce.order_workflow.upsert_order_settings",
                        lambda tid, patch: orders.setdefault(tid, {}).update(patch) or orders[tid])
    monkeypatch.setattr("vula.turns.reply", lambda *a, **k: None)
    return SimpleNamespace(db=db, sent=sent, alerts=alerts, inv=inv, profile=profile, orders=orders)


def _make(world, **kw):
    args = dict(tenant_id=T, display_name="Seaview Tiles", business_type="trades", owner_name="Sam Dube",
                owner_whatsapp="082 000 0001", owner_email="sam@seaview.example")
    args.update(kw)
    return asyncio.run(ob.create_client({"email": "ian@vula.example"}, **args))


def _say(text, phone=OWNER):
    async def go():
        action, tid = ob.decide(phone, {"type": "text", "text": {"body": text}})
        if action == "as_tenant":
            return ("as_tenant", tid)
        await ob.answer(phone, {"type": "text", "text": {"body": text}}, action)
        return action, tid
    return asyncio.run(go())


def _last(world):
    return [s for s in world.sent if s[0] == "text"][-1][2]


def test_master_makes_the_business_puts_the_owner_on_the_team_and_sends_the_welcome(world):
    out = _make(world)
    assert out["welcome"] == "sent"
    owner = next(r for r in world.db.t["vula_team_members"] if r["tenant_id"] == T)
    assert (owner["whatsapp"], owner["role"], owner["email"]) == (OWNER, "owner", "sam@seaview.example")
    row = ob.get(T)
    assert row["status"] == "invited" and row["owner_phone"] == OWNER and row["welcome_status"] == "sent"
    assert world.sent == [("template", OWNER, ps.SETUP_TEMPLATE, ("Sam", "Seaview Tiles"))]
    assert out["onboarding"]["step_number"] == 1 and out["onboarding"]["total"] == 15   # no shop questions


def test_a_bad_number_is_refused_and_nothing_is_made(world):
    with pytest.raises(ValueError):
        _make(world, owner_whatsapp="12345")
    assert not ob.get(T) and not any(r["tenant_id"] == T for r in world.db.t["vula_tenant_config"])


def test_without_vulas_own_number_the_welcome_is_not_sent_and_says_why(world, monkeypatch):
    monkeypatch.setattr(settings, "platform_wa_token", "")
    out = _make(world)
    assert out["welcome"].startswith("not sent") and "own WhatsApp number" in out["welcome"]
    assert world.sent == [] and ob.get(T)["welcome_status"] == out["welcome"]


def test_the_owner_answers_one_question_at_a_time_and_each_answer_is_read_back(world):
    _make(world)
    assert _say("Start") == ("setup", T)
    assert "Question 1 of" in _last(world) and "Seaview Tiles" in _last(world)
    _say("Seaview Tiles & Stone")
    assert "Saved ✅ Business name: Seaview Tiles & Stone" in _last(world) and "Question 2 of" in _last(world)
    assert world.inv[T]["company_name"] == "Seaview Tiles & Stone" and ob.get(T)["step"] == "website"
    _say("no")                                           # no website
    assert "Saved ✅ Website: No website" in _last(world)
    _say("We supply and lay tiles for homes")
    _say("Seaview and Main Road")
    _say("Mon–Fri 8–5, closed weekends")
    assert world.profile[T]["hours"] == "Mon–Fri 8–5, closed weekends"
    assert world.orders[T]["business_hours_note"] == "Mon–Fri 8–5, closed weekends"
    _say("EFT, 50% deposit")
    _say("skip")                                         # pricing, for later
    assert "Skipped" in _last(world)
    _say("not an email")
    assert "doesn't look like an email" in _last(world) and ob.get(T)["step"] == "email"
    _say("accounts@seaview.example")
    assert world.inv[T]["company_email"] == "accounts@seaview.example"
    _say("4123")
    assert "10 digits" in _last(world) and ob.get(T)["step"] == "vat"
    _say("4123456789")
    assert world.inv[T]["vat_registered"] is True and world.inv[T]["vat_number"] == "4123456789"
    _say("FNB, Seaview Tiles, 62812345678")
    assert world.inv[T]["account_number"] == "62812345678" and world.inv[T]["branch_code"] == "250655"
    assert "universal branch code" in _last(world)
    assert ob.get(T)["step"] == "logo"
    _say("we don't have one")
    assert "send the logo as a photo" in _last(world).lower() and ob.get(T)["step"] == "logo"
    _say("skip")
    _say("done")                                         # documents: nothing sent
    assert ob.get(T)["step"] == "team"
    _say("Thandi, 082 000 0003, manager")
    assert "Added ✅ Thandi (27820000003, manager)" in _last(world)
    _say("done")
    assert ob.get(T)["step"] == "alerts"                 # team messages only if the owner picks them
    assert "Nothing is sent unless you choose it" in _last(world) and "customer's question" in _last(world)
    _say("7")
    assert "numbers from the list" in _last(world) and ob.get(T)["step"] == "alerts"
    _say("none")
    assert ob.get(T)["step"] == "briefs"                 # notifications only if the owner asks
    _say("sometime maybe")
    assert "weekdays at 7" in _last(world) and ob.get(T)["step"] == "briefs"
    _say("weekdays at 6:30")
    assert "Morning brief: On, weekdays at 06:30" in "\n".join(s[2] for s in world.sent if s[0] == "text")
    row = ob.get(T)
    assert row["status"] == "done" and row["completed_at"]
    final = _last(world)
    assert "That's Seaview Tiles & Stone set up ✅" in final and "https://seaview-tiles.vula-ai.com/#/go/home" in final
    assert "Sign in with WhatsApp" in final and "Pricing" in final.split("Still to fill in")[1]
    assert world.alerts and T in world.alerts[-1]


def test_stop_pauses_start_carries_on_and_while_paused_vula_works_as_usual(world):
    _make(world)
    _say("hi")
    _say("Seaview Tiles")
    _say("stop")
    assert ob.get(T)["status"] == "paused"
    assert _say("what invoices are unpaid?") == ("as_tenant", T)
    _say("start")
    assert ob.get(T)["status"] == "in_progress" and "Question 2 of" in _last(world)


def test_after_setup_the_team_works_through_vulas_number_as_their_business(world):
    _make(world)
    ob._update(T, {"status": "done", "step": None})
    assert _say("send me this month's sales") == ("as_tenant", T)
    # a member of a business that has no setup row here is not routed to it
    assert _say("hello", phone=STAFF) == ("lead", None)


def test_once_the_business_has_its_own_number_vulas_number_no_longer_speaks_for_it(world):
    _make(world)
    ob._update(T, {"status": "done", "step": None})
    world.db.t["vula_whatsapp_accounts"].append({"tenant_id": T, "phone_number_id": "SEAVIEW-OWN",
                                                 "status": "connected", "phone_number": "27215550000"})
    assert _say("hello") == ("lead", None)


def test_a_stranger_gets_one_polite_reply_a_day_and_ian_is_told(world):
    assert _say("Hi, how do I get Vula for my salon?", phone=STRANGER) == ("lead", None)
    assert _say("hello?", phone=STRANGER) == ("lead", None)
    texts = [s for s in world.sent if s[0] == "text" and s[1] == STRANGER]
    assert len(texts) == 1 and "This is Vula" in texts[0][2]
    assert len(world.alerts) == 1 and STRANGER in world.alerts[0]


def test_documents_and_slips_go_to_the_normal_filing_as_the_business(world):
    _make(world)
    ob._update(T, {"status": "in_progress", "step": "documents"})
    doc = {"type": "document", "document": {"id": "m1", "mime_type": "application/pdf", "filename": "Price list.pdf"}}
    assert ob.decide(OWNER, doc) == ("as_tenant", T)
    assert ob.get(T)["answers"]["documents"] == "Price list.pdf"


def test_the_logo_photo_is_stored_and_read_back(world, monkeypatch):
    _make(world)
    ob._update(T, {"status": "in_progress", "step": "logo"})
    photo = {"type": "image", "image": {"id": "m2", "mime_type": "image/png"}}
    assert ob.decide(OWNER, photo) == ("setup", T)

    async def media(mid):
        return b"png-bytes" if mid == "m2" else None
    monkeypatch.setattr("vula.api.whatsapp._download_media_bytes", media)
    monkeypatch.setattr("vula.api.whatsapp._upload_to_storage",
                        lambda bucket, path, data, ct: f"https://store.example/{bucket}/{path}")
    asyncio.run(ob.answer(OWNER, photo, "setup"))
    assert world.inv[T]["logo_url"].startswith("https://store.example/product-images/seaview-tiles/logo/")
    assert "Logo saved ✅" in _last(world) and ob.get(T)["step"] == "documents"


def test_one_reminder_after_a_quiet_day_never_a_second(world):
    _make(world)
    ob._update(T, {"invited_at": (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()})
    world.sent.clear()
    assert asyncio.run(ob.remind_due()) == {"reminded": 1}
    assert asyncio.run(ob.remind_due()) == {"reminded": 0}
    assert [s[2] for s in world.sent] == [ps.SETUP_TEMPLATE]


def test_bank_details_parse():
    b = ob.parse_bank("Capitec\nAccount name: Main Road Plumbing\nAccount number 1234 567 890\nBranch code 470010")
    assert b == {"bank_name": "Capitec", "account_name": "Main Road Plumbing", "account_number": "1234567890",
                 "branch_code": "470010", "universal_branch": ""}
    assert ob.parse_bank("Standard Bank, Oak Street Bakery") is None


def test_replies_for_the_business_go_from_vulas_number_while_its_message_is_handled(world, monkeypatch):
    from vula.api import whatsapp as wa
    _make(world)
    monkeypatch.setattr("supabase.create_client", lambda *a, **k: world.db)
    wa._wa_creds_cache.clear()
    tok = ps.REPLY_VIA_PLATFORM.set(T)
    try:
        assert asyncio.run(_real_creds(T)) == {"token": "vula-own-token", "phone_id": VULA_PNID}
    finally:
        ps.REPLY_VIA_PLATFORM.reset(tok)
    # and proactive sends for it (no number of its own) also come from Vula's number, never the default line
    monkeypatch.setattr(settings, "whatsapp_phone_id", "DEFAULT-NUMBER")
    monkeypatch.setattr(settings, "whatsapp_token", "server-token")
    wa._wa_creds_cache.clear()
    assert asyncio.run(_real_creds(T))["phone_id"] == VULA_PNID
    # a business that wasn't set up here keeps the old rules
    wa._wa_creds_cache.clear()
    assert asyncio.run(_real_creds(OTHER))["phone_id"] == "DEFAULT-NUMBER"


def test_the_webhook_hands_vulas_number_to_onboarding_before_any_business_routing(world, monkeypatch):
    from vula.api import whatsapp as wa
    _make(world)
    ob._update(T, {"status": "done", "step": None})
    monkeypatch.setattr(settings, "vula_fb_app_secret", "")
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(wa, "_durable_dedup_claim", lambda mid: True)
    monkeypatch.setattr(wa, "_resolve_number_route", lambda pnid: pytest.fail("business routing used"))
    monkeypatch.setattr(wa, "_sender_over_limit", lambda t, p: False)
    monkeypatch.setattr(wa, "_is_team", lambda t, p: True)
    monkeypatch.setattr("vula.api.tenants.is_active", lambda t: True)

    async def noop(*a, **k):
        return False
    monkeypatch.setattr(wa, "_mark_read_and_typing", noop)
    monkeypatch.setattr(wa, "_tap_try_text", noop)
    monkeypatch.setattr(wa, "_stamp_member_window", lambda *a: None)
    monkeypatch.setattr(wa, "_caption_waits_for_document", lambda *a: False)
    seen = []

    async def handle(phone, text, msg_id, route_tenant_id=None):
        seen.append((phone, text, route_tenant_id, ps.REPLY_VIA_PLATFORM.get()))
    monkeypatch.setattr(wa, "_handle_message", handle)
    tasks = []
    monkeypatch.setattr(wa, "_run_bg", lambda coro, **k: tasks.append(asyncio.get_running_loop().create_task(coro)))

    class Req:
        def __init__(self, body): self._b = json.dumps(body).encode()
        async def body(self): return self._b

    def payload(phone, text, mid):
        return {"entry": [{"changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": VULA_PNID},
            "messages": [{"from": phone, "id": mid, "type": "text", "text": {"body": text}}]}}]}]}

    async def go():
        await wa.receive_message(Req(payload(OWNER, "what's owed to us?", "wamid.1")), None)
        await wa.receive_message(Req(payload(STRANGER, "hi", "wamid.2")), None)
        await asyncio.gather(*tasks)
        assert ps.REPLY_VIA_PLATFORM.get() == ""
    asyncio.run(go())
    assert seen == [(OWNER, "what's owed to us?", T, T)]
    assert any(s[0] == "text" and s[1] == STRANGER for s in world.sent)


# ── 5 Nov 2026: answers drafted from the business's website ─────────────────────────────────────
# Master's New tenant › Website imports the site and drafts the profile answers
# (vula/commerce/profile_from_site.py). A question with a draft says what the website says; *yes*
# keeps it, anything else is the answer. Questions that aren't profile answers never show one.

def test_a_question_the_website_answered_offers_it_and_yes_keeps_it(world, monkeypatch):
    drafts = {"what_we_do": {"answer": "Tiles and stone for homes", "quote": "…", "url": "https://seaview.example/about"},
              "email": {"answer": "never offered", "quote": "…", "url": "u"}}
    monkeypatch.setattr("vula.commerce.business_profile.get_drafts",
                        lambda tid: {k: v for k, v in drafts.items() if k not in (world.profile.get(tid) or {})})
    _make(world)
    _say("Start")
    _say("Seaview Tiles")
    _say("no")                                                       # the website question
    assert "Question 3 of" in _last(world) and "Your website says: “Tiles and stone for homes”" in _last(world)
    _say("yes")
    assert world.profile[T]["what_we_do"] == "Tiles and stone for homes"
    assert "Saved ✅ What you do: Tiles and stone for homes" in _last(world)
    assert "Your website says" not in _last(world)                   # area: no draft
    for answer in ("Seaview", "Mon–Fri 8–5", "EFT", "skip"):
        _say(answer)
    assert "Question 8 of" in _last(world) and "never offered" not in _last(world)   # email isn't a profile answer


def test_a_different_answer_replaces_the_website_draft(world, monkeypatch):
    monkeypatch.setattr("vula.commerce.business_profile.get_drafts",
                        lambda tid: {} if "what_we_do" in (world.profile.get(tid) or {}) else
                        {"what_we_do": {"answer": "Old wording", "quote": "…", "url": "u"}})
    _make(world)
    _say("Start")
    _say("Seaview Tiles")
    _say("no")
    _say("We supply and lay tiles")
    assert world.profile[T]["what_we_do"] == "We supply and lay tiles"


# ── 5 Nov 2026: the owner gives the website on WhatsApp ────────────────────────────────────────
# The same scan as Master's Website field (website_setup.start): brand now, products, pages and
# answer drafts in the background. Skipped when Master was given the site; the logo question is
# skipped when the site gave a logo.

def _site(world, monkeypatch, *, logo=True, fail=None):
    started = []

    async def start(tid, url):
        if fail:
            raise fail
        started.append((tid, url))
        patch = {"accent_color": "#1A5E8A", **({"logo_url": "https://store.example/logo.png"} if logo else {})}
        world.inv.setdefault(tid, {}).update(patch)
        return {"url": "https://seaviewtiles.example", "brand": {"applied": True, **patch}, "import_started": True}
    monkeypatch.setattr("vula.commerce.website_setup.start", start)
    return started


def test_a_website_sent_on_whatsapp_starts_the_scan_and_the_logo_question_is_skipped(world, monkeypatch):
    started = _site(world, monkeypatch)
    _make(world)
    _say("Start")
    _say("Seaview Tiles")
    _say("It's www.seaviewtiles.example")
    assert started == [(T, "www.seaviewtiles.example")]
    msg = _last(world)
    assert "Saved ✅ Website: seaviewtiles.example" in msg and "colours and logo are set" in msg
    assert "reading your products and pages" in msg and ob.get(T)["step"] == "what_we_do"
    ob._update(T, {"step": "bank"})
    _say("FNB, Seaview Tiles, 62812345678")
    assert ob.get(T)["step"] == "documents"                           # logo came from the website
    assert ob.get(T)["answers"]["logo"] == "From the website"


def test_no_logo_on_the_site_still_asks_for_one(world, monkeypatch):
    _site(world, monkeypatch, logo=False)
    _make(world)
    _say("Start")
    _say("Seaview Tiles")
    _say("seaviewtiles.example")
    ob._update(T, {"step": "bank"})
    _say("FNB, Seaview Tiles, 62812345678")
    assert ob.get(T)["step"] == "logo"


def test_a_bad_address_is_asked_again_and_nothing_is_saved(world, monkeypatch):
    _site(world, monkeypatch, fail=ValueError("'x' doesn't look like a website address"))
    _make(world)
    _say("Start")
    _say("Seaview Tiles")
    _say("we're on seaview.example")
    assert "doesn't look like a website address" in _last(world) and ob.get(T)["step"] == "website"
    _say("not sure what you mean")
    assert "Send the website address" in _last(world) and ob.get(T)["step"] == "website"


def test_a_website_given_in_master_is_not_asked_again(world, monkeypatch):
    async def create_tenant(body, identity=None):
        world.db.t["vula_tenant_config"].append({"tenant_id": body.tenant_id, "display_name": body.display_name})
        return {"tenant": {"tenant_id": body.tenant_id}, "website": {"url": "https://seaviewtiles.example",
                                                                      "brand": {"applied": False}}}
    monkeypatch.setattr("vula.api.tenants.create_tenant", create_tenant)
    _make(world)
    _say("Start")
    _say("Seaview Tiles")
    assert ob.get(T)["step"] == "what_we_do" and ob.get(T)["answers"]["website"] == "seaviewtiles.example"


# ── 8 Nov 2026: shop questions (master brief item 9) ───────────────────────────────────────────
# A business that sells products is asked how orders reach customers, where it delivers and
# what it costs, its minimum order and who gets order alerts — saved into the order settings
# button checkout reads, and read back from them. Other businesses never see these.

@pytest.fixture()
def shop(world, monkeypatch):
    from vula.commerce import order_workflow as ow
    world.orders.clear()

    def get_settings(tid):
        return {**ow._DEFAULTS, **(world.orders.get(tid) or {})}
    monkeypatch.setattr(ow, "get_order_settings", get_settings)
    _make(world)
    next(r for r in world.db.t["vula_tenant_config"] if r["tenant_id"] == T)["modules"] = ["products", "orders"]
    _say("Start")
    _say("Seaview Fish")
    _say("no")                                                         # website
    for a in ("Fresh fish for homes", "Seaview", "Mon–Sat 8–4", "EFT or card", "price list"):
        _say(a)
    assert ob.get(T)["step"] == "fulfilment", ob.get(T)["step"]
    return world


def test_a_shop_answers_how_orders_reach_customers_and_it_lands_in_order_settings(shop):
    w = shop
    assert "Question 8 of 20" in _last(w)                             # 15 + 5 shop questions
    _say("we do both")
    assert w.orders[T]["collection_enabled"] is True and w.orders[T]["delivery_enabled"] is True
    assert "Delivery and collection" in _last(w) and ob.get(T)["step"] == "collection_point"
    _say("12 Harbour Road, Mon–Sat 8–4")
    assert w.orders[T]["collection_note"] == "12 Harbour Road, Mon–Sat 8–4"
    _say("we deliver")                                                 # no fee: asked again
    assert "Send the areas and the fee" in _last(w) and ob.get(T)["step"] == "delivery"
    _say("Seaview, Main Road and Oak Street — R60, free over R500")
    assert w.orders[T]["delivery_areas"] == ["Seaview", "Main Road", "Oak Street"]
    assert w.orders[T]["delivery_fee_cents"] == 6000 and w.orders[T]["free_delivery_over_cents"] == 50000
    assert "Seaview, Main Road, Oak Street — R60.00, free over R500.00" in _last(w)
    _say("R150")
    assert w.orders[T]["min_order_cents"] == 15000
    _say("me")
    assert w.orders[T]["fulfillment_whatsapp"] == OWNER and w.orders[T]["dispatch_channel"] == "whatsapp"
    assert ob.get(T)["step"] == "email"


def test_collection_only_skips_the_delivery_question_and_another_number_gets_alerts(shop):
    w = shop
    _say("collection only")
    assert w.orders[T]["delivery_enabled"] is False and w.orders[T]["collection_enabled"] is True
    _say("12 Harbour Road")
    assert ob.get(T)["step"] == "min_order"                            # no delivery question
    assert "Question 10 of 19" in _last(w)
    _say("no")
    assert w.orders[T]["min_order_cents"] is None and "No minimum" in _last(w)
    _say("082 000 0009")
    assert w.orders[T]["fulfillment_whatsapp"] == "27820000009"


def test_delivery_only_skips_the_collection_question(shop):
    w = shop
    _say("delivery")
    assert w.orders[T]["collection_enabled"] is False and ob.get(T)["step"] == "delivery"
    _say("free")
    assert w.orders[T]["delivery_fee_cents"] == 0 and "free" in _last(w)


def test_a_business_that_doesnt_sell_products_never_sees_the_shop_questions(world):
    _make(world)
    _say("Start")
    assert "Question 1 of 15" in _last(world)
    ob._update(T, {"step": "pricing"})
    _say("Quote per job")
    assert ob.get(T)["step"] == "email" and "Question 8 of 15" in _last(world)


def test_the_parsers():
    assert ob.parse_fulfilment("deliver and pick-up") == "both"
    assert ob.parse_fulfilment("customers collect") == "collection"
    assert ob.parse_fulfilment("?") is None
    assert ob.rand_cents("R1 500") == 150000 and ob.rand_cents("R1,500.50") == 150050
    assert ob.parse_delivery("we deliver to Seaview, R45.50") == {"areas": ["Seaview"], "fee_cents": 4550,
                                                                  "free_over_cents": None}
    assert ob.parse_delivery("anywhere nearby") is None
