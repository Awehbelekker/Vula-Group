"""The morning brief: the Today list on WhatsApp for every business, and each business's own switch."""
import asyncio
from datetime import date, datetime

import pytest

from config import settings
from vula import owner_advisor, owner_briefs, owner_digest

NOW = datetime(2026, 11, 11, 7, 30, tzinfo=owner_digest.SAST)       # a Wednesday morning
EMPTY_MONEY = {"paid": [], "overdue": [], "upcoming_cents": 0, "upcoming_n": 0}


# ── the Today list, counted ─────────────────────────────────────────────────────────────

def test_today_items_counts_each_kind(monkeypatch):
    items = [
        {"kind": "chat"}, {"kind": "chat"},
        {"kind": "follow_up", "label": "Follow-up due"}, {"kind": "follow_up", "label": "Follow-up overdue"},
        {"kind": "email", "detail": "urgent"}, {"kind": "email", "detail": None},
        {"kind": "quote", "amount_cents": 250_000}, {"kind": "quote", "amount_cents": 125_050},
        {"kind": "document"},
        {"kind": "booking", "label": "Booking · Haircut", "at": "2026-11-11T07:00:00+00:00"},   # 09:00 SAST
    ]
    seen = {}

    def fake_today(db, tid, scope, now):
        seen.update(tid=tid, scope=scope, now=now)
        return {"items": items}

    import vula.api.command_centre as cc
    monkeypatch.setattr(cc, "compute_today", fake_today)
    monkeypatch.setattr(owner_digest, "_client", lambda: None)
    t = owner_digest.today_items("t1", NOW)
    assert seen["tid"] == "t1" and seen["scope"] == {"full": True}       # the owner's full list
    assert t["waiting"] == 2 and t["follow_ups"] == 2 and t["follow_ups_overdue"] == 1
    assert t["emails"] == 2 and t["emails_urgent"] == 1
    assert t["quotes"] == 2 and t["quotes_cents"] == 375_050             # integer cents, summed
    assert t["documents"] == 1 and t["bookings"] == ["09:00 Haircut"]


def test_render_today_only_business_gets_a_brief():
    """A service business with no invoices moving still hears about its day."""
    d = {**EMPTY_MONEY, "today": {"waiting": 1, "follow_ups": 3, "follow_ups_overdue": 1, "emails": 1,
                                  "emails_urgent": 1, "quotes": 2, "quotes_cents": 375_050, "documents": 4,
                                  "bookings": ["09:00 Haircut", "14:30 Colour"]}}
    text = owner_digest.render(d)
    for s in ("Needs you today", "1 customer is waiting for a person", "3 follow-ups due (1 overdue)",
              "1 email needs a reply (1 urgent)", "2 quotes drafted by Vula, R3,750.50",
              "4 documents to check", "2 bookings today: 09:00 Haircut, 14:30 Colour", "*Today* on your dashboard"):
        assert s in text, s
    assert "who owes me" not in text          # no money lines → no money replies offered


def test_render_combines_today_and_money():
    d = {"paid": [{"customer": "Sam", "invoice": "INV-1", "cents": 120000}], "overdue": [("Lerato", 40000)],
         "upcoming_cents": 0, "upcoming_n": 0, "today": {"documents": 1}}
    text = owner_digest.render(d)
    assert text.index("Needs you today") < text.index("Paid yesterday")
    assert "1 document to check" in text and "R1,200.00" in text and "who owes me" in text


def test_nothing_at_all_sends_nothing():
    assert owner_digest.render({**EMPTY_MONEY, "today": {}}) is None
    assert owner_digest.render({**EMPTY_MONEY, "today": {"waiting": 0, "bookings": []}}) is None


def test_long_booking_list_is_cut_short():
    d = {**EMPTY_MONEY, "today": {"bookings": [f"{9 + i}:00 Slot" for i in range(8)]}}
    assert "8 bookings today" in owner_digest.render(d) and "and 2 more" in owner_digest.render(d)


def test_build_survives_a_broken_today_list(monkeypatch):
    class _Q:
        def __getattr__(self, n): return lambda *a, **k: self
        def execute(self):
            class R: data = []
            return R()
    monkeypatch.setattr(owner_digest, "_client", lambda: type("C", (), {"table": lambda s, n: _Q()})())

    def boom(*a, **k): raise RuntimeError("supabase down")
    monkeypatch.setattr(owner_digest, "today_items", boom)
    d = owner_digest.build("t1", date(2026, 11, 11), NOW)
    assert d["today"] == {} and owner_digest.render(d) is None


# ── each business's switch ──────────────────────────────────────────────────────────────

def _config(monkeypatch, briefs):
    import vula.api.tenants as tenants
    monkeypatch.setattr(tenants, "get_config", lambda tid, fresh=False: {"owner_briefs": briefs.get(tid)})


def test_unchosen_business_follows_the_platform_default(monkeypatch):
    _config(monkeypatch, {})
    monkeypatch.setattr(settings, "owner_digest_enabled", False)
    monkeypatch.setattr(settings, "owner_advisor_enabled", True)
    assert owner_briefs.enabled("t1", "morning") is False
    assert owner_briefs.enabled("t1", "weekly") is True


def test_business_choice_beats_the_default(monkeypatch):
    _config(monkeypatch, {"on": {"morning": True}, "off": {"morning": False, "weekly": "yes"}})
    monkeypatch.setattr(settings, "owner_digest_enabled", False)
    monkeypatch.setattr(settings, "owner_advisor_enabled", True)
    assert owner_briefs.enabled("on", "morning") is True
    monkeypatch.setattr(settings, "owner_digest_enabled", True)
    assert owner_briefs.enabled("off", "morning") is False
    assert owner_briefs.enabled("off", "weekly") is True          # a non-bool is ignored → default
    assert owner_briefs.settings_for("off")["chosen"] == ["morning"]


def test_save_merges_and_none_goes_back_to_default(monkeypatch):
    store = {"t1": {"weekly": False}}
    _config(monkeypatch, store)
    writes = []

    class _T:
        def update(self, row): writes.append(row); store["t1"] = row["owner_briefs"]; return self
        def eq(self, *a): return self
        def execute(self): return None

    import vula.api.tenants as tenants
    monkeypatch.setattr(tenants, "_client", lambda: type("C", (), {"table": lambda s, n: _T()})())
    monkeypatch.setattr(tenants, "invalidate", lambda tid: None)
    monkeypatch.setattr(settings, "owner_advisor_enabled", True)
    out = owner_briefs.save("t1", {"morning": True})
    assert writes[-1] == {"owner_briefs": {"weekly": False, "morning": True}} and out["morning"] is True
    out = owner_briefs.save("t1", {"weekly": None, "morning": None, "other": True})
    assert writes[-1] == {"owner_briefs": None} and out["weekly"] is True      # back to the default


# ── sending ─────────────────────────────────────────────────────────────────────────────

def _send_env(monkeypatch, briefs, module, kind_fn):
    """Two businesses, one owner each; records who was sent what."""
    _config(monkeypatch, briefs)

    class _Rows:
        def __getattr__(self, n): return lambda *a, **k: self
        def execute(self):
            class R: data = [{"tenant_id": "a"}, {"tenant_id": "b"}]
            return R()
    monkeypatch.setattr(module, "_client", lambda: type("C", (), {"table": lambda s, n: _Rows()})())
    import vula.api.tenants as tenants
    import vula.team_index as team_index
    import vula.api.whatsapp as wa
    monkeypatch.setattr(tenants, "is_active", lambda tid: True)
    monkeypatch.setattr(team_index, "active_members", lambda tid: [
        {"role": "owner", "whatsapp": f"2782000000{tid}"}, {"role": "staff", "whatsapp": "27820000999"}])
    monkeypatch.setattr(module, "build", kind_fn)
    sent = []

    async def fake_send(phone, text, tid, idem_key=None):
        sent.append((tid, phone, idem_key))
        return True
    monkeypatch.setattr(wa, "_send_reply", fake_send)
    return sent


def test_morning_brief_goes_only_to_businesses_that_have_it_on(monkeypatch):
    monkeypatch.setattr(settings, "owner_digest_enabled", False)
    sent = _send_env(monkeypatch, {"a": {"morning": True}}, owner_digest,
                     lambda tid, today, now=None: {**EMPTY_MONEY, "today": {"documents": 1}})
    res = asyncio.run(owner_digest.send_all(NOW))
    assert res["sent"] == 1 and sent == [("a", "2782000000a", "digest:a:2026-11-11:2782000000a")]


def test_morning_brief_default_on_skips_a_business_that_said_no(monkeypatch):
    monkeypatch.setattr(settings, "owner_digest_enabled", True)
    sent = _send_env(monkeypatch, {"b": {"morning": False}}, owner_digest,
                     lambda tid, today, now=None: {**EMPTY_MONEY, "today": {"documents": 1}})
    asyncio.run(owner_digest.send_all(NOW))
    assert [s[0] for s in sent] == ["a"]                              # owners only, never staff


def test_nothing_to_say_sends_nothing(monkeypatch):
    monkeypatch.setattr(settings, "owner_digest_enabled", True)
    sent = _send_env(monkeypatch, {}, owner_digest, lambda tid, today, now=None: {**EMPTY_MONEY, "today": {}})
    assert asyncio.run(owner_digest.send_all(NOW))["sent"] == 0 and sent == []


def test_weekly_advisor_respects_the_business_switch(monkeypatch):
    monkeypatch.setattr(settings, "owner_advisor_enabled", False)
    sent = _send_env(monkeypatch, {"b": {"weekly": True}}, owner_advisor, lambda tid, today=None: {})
    monkeypatch.setattr(owner_advisor, "render", lambda d: "Weekly")
    asyncio.run(owner_advisor.send_all(datetime(2026, 11, 9, 9, 0, tzinfo=owner_advisor.SAST)))
    assert [s[0] for s in sent] == ["b"]


# ── the API ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preview_rejects_an_unknown_kind():
    from fastapi import HTTPException
    from vula.api.owner_briefs import preview_brief
    with pytest.raises(HTTPException) as e:
        await preview_brief("t1", kind="hourly", identity={})
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_preview_builds_the_text_without_sending(monkeypatch):
    from vula.api.owner_briefs import preview_brief
    monkeypatch.setattr(owner_digest, "build", lambda tid, today, now=None: {**EMPTY_MONEY, "today": {"documents": 2}})
    import vula.api.whatsapp as wa

    async def must_not_send(*a, **k): raise AssertionError("preview sent a message")
    monkeypatch.setattr(wa, "_send_reply", must_not_send)
    out = await preview_brief("t1", kind="morning", identity={})
    assert out["empty"] is False and "2 documents to check" in out["text"]


def test_preview_and_writes_need_the_owner_or_a_manager():
    from vula.api.owner_briefs import router
    from vula.api.tenant_auth import require_tenant_admin
    guarded = {r.path: [d.call for d in r.dependant.dependencies] for r in router.routes}
    assert require_tenant_admin in guarded["/{tenant}/briefs/preview"]
    assert any(require_tenant_admin in v for p, v in guarded.items() if p == "/{tenant}/briefs")


# ── switching by asking on WhatsApp (commerce_admin owner_briefs tool) ─────────────────────

def _skill():
    from core.skills.commerce_admin import CommerceAdminSkill
    return CommerceAdminSkill()


def test_owner_can_switch_the_morning_brief_off_by_asking(monkeypatch):
    store = {"t1": {}}
    _config(monkeypatch, store)

    class _T:
        def update(self, row): store["t1"] = row["owner_briefs"]; return self
        def eq(self, *a): return self
        def execute(self): return None
    import vula.api.tenants as tenants
    monkeypatch.setattr(tenants, "_client", lambda: type("C", (), {"table": lambda s, n: _T()})())
    monkeypatch.setattr(tenants, "invalidate", lambda tid: None)
    monkeypatch.setattr(settings, "owner_digest_enabled", True)
    out = _skill()._owner_briefs("t1", {"action": "off", "which": "morning"})
    assert store["t1"] == {"morning": False}
    assert "morning brief (weekdays around 07:00) is *off*" in out["reply_verbatim"]
    assert out["settings"]["morning"] is False


def test_a_switch_that_doesnt_stick_is_not_reported_done(monkeypatch):
    _config(monkeypatch, {})                     # the write "succeeds" but the read-back is unchanged

    class _T:
        def update(self, row): return self
        def eq(self, *a): return self
        def execute(self): return None
    import vula.api.tenants as tenants
    monkeypatch.setattr(tenants, "_client", lambda: type("C", (), {"table": lambda s, n: _T()})())
    monkeypatch.setattr(tenants, "invalidate", lambda tid: None)
    monkeypatch.setattr(settings, "owner_advisor_enabled", False)
    out = _skill()._owner_briefs("t1", {"action": "on", "which": "weekly"})
    assert "error" in out and "reply_verbatim" not in out


def test_status_lists_both_briefs(monkeypatch):
    _config(monkeypatch, {"t1": {"weekly": True}})
    monkeypatch.setattr(settings, "owner_digest_enabled", False)
    text = _skill()._owner_briefs("t1", {"action": "status"})["reply_verbatim"]
    assert "morning brief (weekdays around 07:00) is *off*" in text and "weekly advisor (Mondays) is *on*" in text


def test_preview_by_asking_sends_nothing(monkeypatch):
    monkeypatch.setattr(owner_digest, "build", lambda tid, today, now=None: {**EMPTY_MONEY, "today": {"waiting": 1}})
    out = _skill()._owner_briefs("t1", {"action": "preview"})
    assert "1 customer is waiting for a person" in out["reply_verbatim"]


def test_staff_and_reps_never_get_the_tool():
    from core.skills import commerce_admin as ca
    assert ca._TOOL_SCOPE["owner_briefs"] == "owner_only"


def test_brief_requests_reach_the_owner_assistant_on_a_knowledge_line():
    from core.skills.base import looks_like_owner_admin_question as q
    for s in ("stop my morning brief", "turn the weekly advisor back on", "switch off the daily summary",
              "show me today's morning briefing"):
        assert q(s), s
    assert not q("what's on the programme today")
