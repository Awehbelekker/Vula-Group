"""The morning brief: the Today list on WhatsApp for every business, and each business's own switch."""
import asyncio
from datetime import date, datetime

import pytest

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


# ── off unless asked, at the owner's own time ────────────────────────────────────────────

def _config(monkeypatch, briefs):
    import vula.api.tenants as tenants
    monkeypatch.setattr(tenants, "get_config", lambda tid, fresh=False: {"owner_briefs": briefs.get(tid)})


def _writable(monkeypatch, store):
    """A tenant config the briefs can write to and read back."""
    _config(monkeypatch, store)

    class _T:
        def __init__(self): self.tid = None
        def update(self, row): self.row = row; return self
        def eq(self, col, val): store[val] = self.row["owner_briefs"]; return self
        def execute(self): return None
    import vula.api.tenants as tenants
    monkeypatch.setattr(tenants, "_client", lambda: type("C", (), {"table": lambda s, n: _T()})())
    monkeypatch.setattr(tenants, "invalidate", lambda tid: None)


def test_nothing_is_on_unless_the_business_asked(monkeypatch):
    _config(monkeypatch, {})
    s = owner_briefs.settings_for("t1")
    assert s["morning"]["on"] is False and s["weekly"]["on"] is False
    assert not owner_briefs.enabled("t1", "morning") and not owner_briefs.enabled("t1", "weekly")


def test_the_owner_s_own_time_and_days(monkeypatch):
    _config(monkeypatch, {"t1": {"morning": {"on": True, "hour": 6, "minute": 30, "days": [0, 1, 2, 3, 4, 5]},
                                 "weekly": {"on": True, "day": 4, "hour": 15}}})
    s = owner_briefs.settings_for("t1")
    assert s["morning"]["when"] == "Mon–Sat at 06:30" and s["weekly"]["when"] == "Fridays at 15:00"
    sat = datetime(2026, 11, 14, 6, 35, tzinfo=owner_digest.SAST)
    assert owner_briefs.due("morning", s["morning"], sat)
    assert not owner_briefs.due("morning", s["morning"], sat.replace(hour=6, minute=0))     # too early
    assert not owner_briefs.due("morning", s["morning"], sat.replace(hour=9, minute=0))     # window over
    assert not owner_briefs.due("morning", s["morning"], datetime(2026, 11, 15, 6, 35, tzinfo=owner_digest.SAST))  # Sun
    assert owner_briefs.due("weekly", s["weekly"], datetime(2026, 11, 13, 15, 10, tzinfo=owner_digest.SAST))
    assert not owner_briefs.due("weekly", s["weekly"], datetime(2026, 11, 10, 15, 10, tzinfo=owner_digest.SAST))


def test_odd_saved_values_are_ignored(monkeypatch):
    _config(monkeypatch, {"t1": {"morning": {"on": "yes", "hour": 3, "days": [9, "mon"]}, "weekly": True}})
    s = owner_briefs.settings_for("t1")
    assert s["morning"]["on"] is False and s["morning"]["hour"] == 7 and s["morning"]["days"] == [0, 1, 2, 3, 4]
    assert s["weekly"]["on"] is True                     # an early {"weekly": true} still reads


@pytest.mark.parametrize("text,want", [
    ("weekdays at 7", {"on": True, "hour": 7, "minute": 0, "days": [0, 1, 2, 3, 4]}),
    ("Mon-Sat 6:30", {"on": True, "hour": 6, "minute": 30, "days": [0, 1, 2, 3, 4, 5]}),
    ("every day at 8am", {"on": True, "hour": 8, "minute": 0, "days": list(range(7))}),
    ("monday and thursday 7.15", {"on": True, "hour": 7, "minute": 15, "days": [0, 3]}),
    ("6pm", {"on": True, "hour": 18, "minute": 0}),
    ("yes please", {"on": True}),
    ("no thanks", {"on": False}),
    ("maybe later on", None),
    ("at 2am", None),                                       # not a sensible hour to message anyone
])
def test_parse_the_owner_s_words(text, want):
    assert owner_briefs.parse_schedule("morning", text) == want


def test_weekly_takes_one_day():
    assert owner_briefs.parse_schedule("weekly", "Friday 3pm") == {"on": True, "hour": 15, "minute": 0, "day": 4}


def test_save_merges_and_reads_back(monkeypatch):
    store = {"t1": {"weekly": {"on": True, "day": 4, "hour": 15}}}
    _writable(monkeypatch, store)
    out = owner_briefs.save("t1", {"morning": {"on": True, "hour": 6}})
    assert store["t1"]["morning"] == {"on": True, "hour": 6} and store["t1"]["weekly"]["day"] == 4
    assert out["morning"]["when"] == "weekdays at 06:00"
    owner_briefs.save("t1", {"morning": {"on": False}})
    assert store["t1"]["morning"] == {"on": False, "hour": 6}     # off keeps the chosen time for later
    owner_briefs.save("t1", {"morning": None, "weekly": None})
    assert store["t1"] is None


# ── sending ─────────────────────────────────────────────────────────────────────────────

def _send_env(monkeypatch, briefs, module, kind_fn):
    """Two businesses, one owner each; records who was sent what."""
    _config(monkeypatch, briefs)
    module._HANDLED.clear()

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
    calls = []

    def counting(*a, **k):
        calls.append(a[0])
        return kind_fn(*a, **k)
    monkeypatch.setattr(module, "build", counting)
    sent = []

    async def fake_send(phone, text, tid, idem_key=None):
        sent.append((tid, phone, idem_key))
        return True
    monkeypatch.setattr(wa, "_send_reply", fake_send)
    return sent, calls


ON_AT_7 = {"morning": {"on": True, "hour": 7, "minute": 0, "days": [0, 1, 2, 3, 4]}}


def test_morning_brief_only_for_a_business_that_asked_at_its_time(monkeypatch):
    sent, calls = _send_env(monkeypatch, {"a": ON_AT_7, "b": {"morning": {"on": True, "hour": 9}}}, owner_digest,
                            lambda tid, today, now=None: {**EMPTY_MONEY, "today": {"documents": 1}})
    res = asyncio.run(owner_digest.send_all(NOW))                   # 07:30: a's time, not b's
    assert res["sent"] == 1 and sent == [("a", "2782000000a", "digest:a:2026-11-11:2782000000a")]
    asyncio.run(owner_digest.send_all(NOW.replace(minute=35)))      # the next loop: not built again
    assert calls == ["a"] and len(sent) == 1


def test_a_business_that_never_asked_gets_nothing(monkeypatch):
    sent, calls = _send_env(monkeypatch, {}, owner_digest,
                            lambda tid, today, now=None: {**EMPTY_MONEY, "today": {"documents": 1}})
    assert asyncio.run(owner_digest.send_all(NOW))["sent"] == 0 and sent == [] and calls == []


def test_owners_only_never_staff(monkeypatch):
    sent, _ = _send_env(monkeypatch, {"a": ON_AT_7, "b": ON_AT_7}, owner_digest,
                        lambda tid, today, now=None: {**EMPTY_MONEY, "today": {"documents": 1}})
    asyncio.run(owner_digest.send_all(NOW))
    assert sorted(s[1] for s in sent) == ["2782000000a", "2782000000b"]


def test_nothing_to_say_sends_nothing(monkeypatch):
    sent, _ = _send_env(monkeypatch, {"a": ON_AT_7}, owner_digest,
                        lambda tid, today, now=None: {**EMPTY_MONEY, "today": {}})
    assert asyncio.run(owner_digest.send_all(NOW))["sent"] == 0 and sent == []


def test_weekly_advisor_on_the_owner_s_day(monkeypatch):
    sent, _ = _send_env(monkeypatch, {"b": {"weekly": {"on": True, "day": 4, "hour": 15}}}, owner_advisor,
                        lambda tid, today=None: {})
    monkeypatch.setattr(owner_advisor, "render", lambda d: "Weekly")
    asyncio.run(owner_advisor.send_all(datetime(2026, 11, 9, 15, 30, tzinfo=owner_advisor.SAST)))   # Monday
    assert sent == []
    asyncio.run(owner_advisor.send_all(datetime(2026, 11, 13, 15, 30, tzinfo=owner_advisor.SAST)))  # Friday
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


# ── by asking on WhatsApp (commerce_admin owner_briefs tool) ────────────────────────────

def _skill():
    from core.skills.commerce_admin import CommerceAdminSkill
    return CommerceAdminSkill()


def test_switching_on_without_a_time_asks_for_one(monkeypatch):
    store = {}
    _writable(monkeypatch, store)
    out = _skill()._owner_briefs("t1", {"action": "on", "which": "morning"})
    assert out["status"] == "need_info" and "What time" in out["message"] and "weekdays at 07:00" in out["message"]
    assert store == {}                                    # nothing switched on behind the owner's back


def test_switching_on_with_the_owner_s_time(monkeypatch):
    store = {}
    _writable(monkeypatch, store)
    out = _skill()._owner_briefs("t1", {"action": "on", "which": "morning", "when": "Mon-Sat at 6:30"})
    assert store["t1"]["morning"] == {"on": True, "hour": 6, "minute": 30, "days": [0, 1, 2, 3, 4, 5]}
    assert "morning brief is *on* — Mon–Sat at 06:30" in out["reply_verbatim"]


def test_off_then_on_again_keeps_the_chosen_time(monkeypatch):
    store = {"t1": {"morning": {"on": True, "hour": 6, "minute": 30, "days": [0, 1, 2, 3, 4]}}}
    _writable(monkeypatch, store)
    out = _skill()._owner_briefs("t1", {"action": "off", "which": "morning"})
    assert "morning brief is *off*" in out["reply_verbatim"] and store["t1"]["morning"]["on"] is False
    out = _skill()._owner_briefs("t1", {"action": "on", "which": "morning"})
    assert "weekdays at 06:30" in out["reply_verbatim"]


def test_moving_the_weekly_advisor(monkeypatch):
    store = {}
    _writable(monkeypatch, store)
    out = _skill()._owner_briefs("t1", {"action": "schedule", "which": "weekly", "when": "Friday 3pm"})
    assert "weekly advisor is *on* — Fridays at 15:00" in out["reply_verbatim"]


def test_a_change_that_doesnt_stick_is_not_reported_done(monkeypatch):
    _config(monkeypatch, {})                     # the write "succeeds" but the read-back is unchanged

    class _T:
        def update(self, row): return self
        def eq(self, *a): return self
        def execute(self): return None
    import vula.api.tenants as tenants
    monkeypatch.setattr(tenants, "_client", lambda: type("C", (), {"table": lambda s, n: _T()})())
    monkeypatch.setattr(tenants, "invalidate", lambda tid: None)
    out = _skill()._owner_briefs("t1", {"action": "on", "which": "weekly", "when": "Monday at 8"})
    assert "error" in out and "reply_verbatim" not in out


def test_status_lists_both_briefs(monkeypatch):
    _config(monkeypatch, {"t1": {"weekly": {"on": True, "day": 0, "hour": 8}}})
    text = _skill()._owner_briefs("t1", {"action": "status"})["reply_verbatim"]
    assert "morning brief is *off*" in text and "weekly advisor is *on* — Mondays at 08:00" in text


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
