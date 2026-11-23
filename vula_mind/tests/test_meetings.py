"""Meetings booked on WhatsApp: a reminder only when asked, with the address and a map link."""
import asyncio
from datetime import datetime, timedelta

import pytest

from vula import meetings as mt
from vula.when import SAST

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=SAST)          # Thursday 09:00
ME = "27820000001"


class _Q:
    """Enough of the Supabase query builder for vula_meetings."""
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.op, self.payload = db, name, [], "select", None
        self.not_ = self
        self._neg = False

    def select(self, *_): return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def order(self, *a, **k): return self
    def limit(self, *_): return self
    def ilike(self, *_): self.filters.append(lambda r: False); return self

    def _add(self, fn):
        neg, self._neg = self._neg, False
        self.filters.append((lambda r: not fn(r)) if neg else fn)
        return self

    def __getattribute__(self, name):
        if name == "not_":
            object.__setattr__(self, "_neg", True)
            return self
        return object.__getattribute__(self, name)

    def eq(self, k, v): return self._add(lambda r: r.get(k) == v)
    def is_(self, k, v): return self._add(lambda r: r.get(k) is None)
    def gte(self, k, v): return self._add(lambda r: _t(r.get(k)) >= _t(v))
    def gt(self, k, v): return self._add(lambda r: _t(r.get(k)) > _t(v))
    def lte(self, k, v): return self._add(lambda r: _t(r.get(k)) <= _t(v))
    def lt(self, k, v): return self._add(lambda r: _t(r.get(k)) < _t(v))

    def execute(self):
        rows = self.db.setdefault(self.name, [])
        if self.op == "insert":
            row = {"id": f"m{len(rows) + 1}", "status": "scheduled", "reminded_at": None, **self.payload}
            rows.append(row)
            data = [row]
        else:
            data = [r for r in rows if all(f(r) for f in self.filters)]
            if self.op == "update":
                for r in data:
                    r.update(self.payload)
        return type("R", (), {"data": data})()


def _t(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


@pytest.fixture
def db(monkeypatch):
    store = {}
    client = type("C", (), {"table": lambda s, n: _Q(store, n)})()
    monkeypatch.setattr(mt, "_client", lambda: client)
    import vula.commerce.service as service
    monkeypatch.setattr(service, "_client", lambda: client)
    monkeypatch.setattr(mt, "now_sast", lambda: NOW)
    import vula.when as when
    monkeypatch.setattr(when, "now_sast", lambda: NOW)
    return store


def _skill():
    from core.skills.commerce_admin import CommerceAdminSkill
    return CommerceAdminSkill()


def _say(**args):
    return _skill()._meeting("t1", args, {"phone": "+27 82 000 0001"})


# ── booking ───────────────────────────────────────────────────────────────────────────────

def test_book_with_address_gives_a_map_link_and_offers_a_reminder(db):
    out = _say(action="book", **{"with": "Acme Builders"}, when="Friday at 10am",
               address="12 Main Road, Paarden Eiland")
    text = out["reply_verbatim"]
    assert "📅 Booked: *Acme Builders* — tomorrow at 10:00" in text
    assert "https://www.google.com/maps/search/?api=1&query=12+Main+Road%2C+Paarden+Eiland" in text
    assert "Want a reminder before it?" in text
    row = db["vula_meetings"][0]
    assert row["created_by"] == ME and row["remind_minutes"] is None        # no reminder unasked
    assert row["starts_at"] == "2026-11-13T10:00:00+02:00"


def test_book_with_a_reminder(db):
    out = _say(action="book", **{"with": "Acme"}, when="Friday at 10am", remind="1 hour before")
    assert "I'll remind you 1 hour before (09:00)" in out["reply_verbatim"]
    assert "No address yet" in out["reply_verbatim"]
    assert db["vula_meetings"][0]["remind_minutes"] == 60


@pytest.mark.parametrize("args,asks", [
    ({"with": "Acme"}, "When is it?"),
    ({"with": "Acme", "when": "Monday"}, "What time on Mon 16 Nov?"),
    ({"with": "Acme", "when": "today 7am"}, "already passed"),
    ({"when": "Friday at 10"}, "Who is the meeting with?"),
    ({"with": "Acme", "when": "Friday at 10", "remind": "sometime"}, "How long before"),
])
def test_missing_parts_are_asked_never_guessed(db, args, asks):
    out = _say(action="book", **args)
    assert out["status"] == "need_info" and asks in out["message"]
    assert not db.get("vula_meetings")


def test_a_clash_is_pointed_out(db):
    _say(action="book", **{"with": "Acme"}, when="Friday at 10am")
    out = _say(action="book", **{"with": "Bongi Plumbing"}, when="Friday at 10:30")
    assert "⚠️ Close to: *Acme* — tomorrow at 10:00" in out["reply_verbatim"]


# ── changing ──────────────────────────────────────────────────────────────────────────────

def test_remind_me_before_my_next_meeting(db):
    _say(action="book", **{"with": "Acme"}, when="Friday at 10am")
    out = _say(action="update", remind="30 min")
    assert "I'll remind you 30 minutes before (09:30)" in out["reply_verbatim"]


def test_move_and_add_address_reads_back(db):
    _say(action="book", **{"with": "Acme Builders"}, when="Friday at 10am", remind="1 hour")
    db["vula_meetings"][0]["reminded_at"] = "2026-11-13T07:00:00+00:00"
    out = _say(action="update", **{"with": "acme"}, when="Friday at 11", address="5 Dock Road")
    row = db["vula_meetings"][0]
    assert row["starts_at"] == "2026-11-13T11:00:00+02:00" and row["address"] == "5 Dock Road"
    assert row["reminded_at"] is None                          # the reminder goes again for 11:00
    assert "✅ Updated: *Acme Builders* — tomorrow at 11:00" in out["reply_verbatim"]


def test_two_matches_asks_which(db):
    _say(action="book", **{"with": "Acme Paarl"}, when="Friday at 10am")
    _say(action="book", **{"with": "Acme Bellville"}, when="Monday at 10am")
    out = _say(action="cancel", **{"with": "Acme"})
    assert out["status"] == "need_info" and "Which one?" in out["message"]


def test_cancel(db):
    _say(action="book", **{"with": "Acme"}, when="Friday at 10am")
    out = _say(action="cancel", **{"with": "Acme"})
    assert out["reply_verbatim"].startswith("❌ Cancelled") and db["vula_meetings"][0]["status"] == "cancelled"
    assert "no meetings" in _say(action="list")["reply_verbatim"]


def test_someone_else_s_meeting_is_not_found(db):
    _say(action="book", **{"with": "Acme"}, when="Friday at 10am")
    out = _skill()._meeting("t1", {"action": "cancel", "with": "Acme"}, {"phone": "27829999999"})
    assert out["status"] == "need_info" and db["vula_meetings"][0]["status"] == "scheduled"


# ── the reminder itself ───────────────────────────────────────────────────────────────────

def test_reminder_goes_once_at_the_right_time_with_the_map_link(db, monkeypatch):
    _say(action="book", **{"with": "Acme"}, when="Friday at 10am", address="12 Main Rd", remind="1 hour")
    _say(action="book", **{"with": "No reminder Co"}, when="Friday at 10:15am")
    sent = []

    async def fake_send(phone, text, tid, idem_key=None):
        sent.append((phone, text, idem_key))
        return True
    import vula.api.whatsapp as wa
    monkeypatch.setattr(wa, "_send_reply", fake_send)
    friday = datetime(2026, 11, 13, 8, 55, tzinfo=SAST)
    assert asyncio.run(mt.send_due_reminders(friday)) == 0                   # 09:00 not yet
    assert asyncio.run(mt.send_due_reminders(friday + timedelta(minutes=5))) == 1
    assert asyncio.run(mt.send_due_reminders(friday + timedelta(minutes=10))) == 0   # once only
    phone, text, key = sent[0]
    assert phone == ME and "⏰ Meeting in 1 hour: *Acme* at 10:00" in text
    assert "📍 12 Main Rd" in text and "maps/search/?api=1&query=12+Main+Rd" in text


@pytest.mark.parametrize("words,minutes", [("1 hour before", 60), ("an hour", 60), ("30 min", 30),
                                           ("half an hour", 30), ("the day before", 1440), ("2 hours", 120),
                                           ("no", 0), ("1 minute", None), ("whenever", None)])
def test_parse_lead(words, minutes):
    assert mt.parse_lead(words) == minutes


def test_meetings_show_in_the_morning_brief():
    from vula import owner_digest
    d = {"paid": [], "overdue": [], "upcoming_cents": 0, "upcoming_n": 0,
         "today": {"meetings": ["10:00 Acme Builders", "14:30 Bongi Plumbing"]}}
    assert "2 meetings today: 10:00 Acme Builders, 14:30 Bongi Plumbing" in owner_digest.render(d)


def test_every_team_member_can_use_it_reps_included():
    from core.skills import commerce_admin as ca
    assert "meeting" not in ca._TOOL_SCOPE
    assert "meeting" in {t["function"]["name"] for t in ca._REP_TOOL_SPECS}
