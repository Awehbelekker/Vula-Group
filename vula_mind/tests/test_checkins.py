"""Check-ins: "arrived" + a location pin → WhatsApp's time, the place, distance from the address."""
import asyncio
from datetime import datetime, timezone

import pytest

from vula import checkins
from vula.when import SAST

NOW = datetime(2026, 11, 13, 9, 55, tzinfo=SAST)          # Friday 09:55
REP, OTHER = "27820000002", "27829999999"
ACME = (-33.9100, 18.4800)                               # where "12 Main Rd" geocodes in these tests


def _t(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


class _Q:
    def __init__(self, store, name):
        self.store, self.name, self.f, self.op, self.payload, self.span, self.desc = store, name, [], "select", None, None, False

    def select(self, *_): return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def limit(self, *_): return self
    def range(self, a, b): self.span = (a, b); return self
    def order(self, col, desc=False): self.desc = desc; self.col = col; return self
    def eq(self, k, v): self.f.append(lambda r: r.get(k) == v); return self
    def in_(self, k, vs): self.f.append(lambda r: r.get(k) in vs); return self
    def neq(self, k, v): self.f.append(lambda r: r.get(k) != v); return self
    def gte(self, k, v): self.f.append(lambda r: r.get(k) is not None and _t(r[k]) >= _t(v)); return self
    def lte(self, k, v): self.f.append(lambda r: r.get(k) is not None and _t(r[k]) <= _t(v)); return self
    def lt(self, k, v): self.f.append(lambda r: r.get(k) is not None and _t(r[k]) < _t(v)); return self

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if self.op == "insert":
            row = {"id": f"{self.name[:3]}{len(rows) + 1}", "created_at": NOW.isoformat(), **self.payload}
            rows.append(row)
            return type("R", (), {"data": [row]})()
        hit = [r for r in rows if all(f(r) for f in self.f)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        if self.desc:
            hit = sorted(hit, key=lambda r: str(r.get(self.col)), reverse=True)
        if self.span:
            hit = hit[self.span[0]:self.span[1] + 1]
        return type("R", (), {"data": hit})()


@pytest.fixture
def world(monkeypatch):
    store = {"vula_meetings": [
        {"id": "m1", "tenant_id": "t1", "created_by": REP, "with_name": "Acme Builders", "address": "12 Main Rd",
         "starts_at": "2026-11-13T10:00:00+02:00", "status": "scheduled"},
        {"id": "m2", "tenant_id": "t1", "created_by": REP, "with_name": "Late Co", "address": None,
         "starts_at": "2026-11-13T16:00:00+02:00", "status": "scheduled"}]}
    client = type("C", (), {"table": lambda s, n: _Q(store, n)})()
    import vula.commerce.service as service
    import vula.meetings as mt
    import vula.team_activity as ta
    import vula.team_index as team_index
    import vula.when as when
    from vula.commerce import geo
    for mod in (service, checkins, mt, ta):
        monkeypatch.setattr(mod, "_client", lambda: client)
    team = [{"id": "o", "name": "Ian", "whatsapp": "27820000001", "role": "owner", "access": []},
            {"id": "r", "name": "Thabo", "whatsapp": REP, "role": "sales_rep", "access": ["crm"]}]
    monkeypatch.setattr(team_index, "active_members", lambda tid: team)
    monkeypatch.setattr(team_index, "member_for_phone",
                        lambda tid, p: next((m for m in team if m["whatsapp"][-9:] == "".join(c for c in p if c.isdigit())[-9:]), None))
    for mod in (checkins, mt, ta, when):
        monkeypatch.setattr(mod, "now_sast", lambda: NOW)

    async def fake_geocode(q):
        return {"lat": ACME[0], "lng": ACME[1], "label": q} if "main rd" in q.lower() else None
    monkeypatch.setattr(geo, "geocode", fake_geocode)
    return store


def _pin(lat, lng, phone=REP, place="", sent=None):
    return asyncio.run(checkins.from_pin("t1", phone, lat, lng, place, sent, now=NOW))


# ── "arrived", then the pin ───────────────────────────────────────────────────────────────

def test_arrived_matches_the_meeting_and_asks_for_the_pin(world):
    from core.skills.commerce_admin import CommerceAdminSkill
    out = CommerceAdminSkill()._meeting("t1", {"action": "check_in"}, {"phone": "+" + REP})
    assert "Checking in for *Acme Builders* — today at 10:00" in out["reply_verbatim"]
    assert "Send your current location" in out["reply_verbatim"]
    row = world["vula_checkins"][0]
    assert (row["status"], row["meeting_id"], row["person"]) == ("awaiting_pin", "m1", REP)


def test_the_pin_checks_in_with_whatsapp_time_and_distance(world):
    checkins.start("t1", REP, "", now=NOW)
    sent = datetime(2026, 11, 13, 7, 58, tzinfo=timezone.utc)        # 09:58 SAST, WhatsApp's stamp
    reply = _pin(ACME[0] + 0.0003, ACME[1], sent=sent)               # ~33 m north
    assert "✅ Checked in for *Acme Builders* at 09:58 on Fri 13 Nov (WhatsApp's time)." in reply
    assert "33 m from 12 Main Rd" in reply and "maps/search/?api=1&query=12+Main+Rd" in reply
    row = world["vula_checkins"][0]
    assert row["status"] == "checked_in" and row["distance_m"] == 33 and row["at"] == sent.isoformat()


def test_far_away_is_said_plainly(world):
    checkins.start("t1", REP, "", now=NOW)
    reply = _pin(ACME[0] + 0.03, ACME[1])                           # ~3.3 km away
    assert "⚠️ This pin is 3.3 km from the meeting address (12 Main Rd)" in reply


def test_a_pin_near_meeting_time_checks_in_without_saying_arrived(world):
    reply = _pin(ACME[0], ACME[1])                                   # Acme starts in 5 minutes
    assert reply.startswith("✅ Checked in for *Acme Builders*") and world["vula_checkins"][0]["meeting_id"] == "m1"


def test_any_other_pin_goes_the_usual_way(world):
    world["vula_meetings"].pop(0)                                    # nothing starting about now
    assert _pin(-33.9, 18.4) is None and not world.get("vula_checkins")
    assert _pin(ACME[0], ACME[1], phone=OTHER) is None               # not on the team


def test_check_in_without_a_meeting(world):
    world["vula_meetings"].clear()
    checkins.start("t1", REP, "the Bellville site", now=NOW)
    reply = _pin(-33.89, 18.63, place="Bellville Station")
    assert "Checked in for *the Bellville site*" in reply and "📍 Bellville Station" in reply
    assert "maps/search/?api=1&query=-33.89,18.63" in reply


def test_an_address_that_cant_be_placed(world):
    world["vula_meetings"][0]["address"] = "Somewhere vague"
    reply = _pin(ACME[0], ACME[1])
    assert "couldn't place Somewhere vague on the map" in reply


# ── the webhook path ──────────────────────────────────────────────────────────────────────

def test_a_pin_that_is_not_a_check_in_is_routed_as_before(world, monkeypatch):
    import vula.api.whatsapp as wa
    routed, sent = [], []

    async def handle(phone, text, msg_id, route_tenant_id=None):
        routed.append(text)

    async def send(*a, **k):
        sent.append(a)
        return True
    monkeypatch.setattr(wa, "_handle_message", handle)
    monkeypatch.setattr(wa, "_send_reply", send)
    asyncio.run(wa._handle_location(OTHER, "📍 pin", "w1", "knowledge", "t1", -33.9, 18.4, "", "1763017080"))
    assert routed == ["📍 pin"] and sent == []
    asyncio.run(wa._handle_location(REP, "📍 pin", "w2", "knowledge", "t1", ACME[0], ACME[1], "", "1763017080"))
    assert routed == ["📍 pin"] and "Checked in" in sent[0][1]


# ── showing it ────────────────────────────────────────────────────────────────────────────

def test_check_ins_show_in_team_activity_with_photos(world):
    from vula import team_activity as ta
    world["vula_filed_documents"] = [
        {"tenant_id": "t1", "filed_by": REP, "mime": "image/jpeg", "created_at": "2026-11-13T10:05:00+02:00"},
        {"tenant_id": "t1", "filed_by": "+" + REP, "mime": "image/jpeg", "created_at": "2026-11-13T10:20:00+02:00"},
        {"tenant_id": "t1", "filed_by": REP, "mime": "application/pdf", "created_at": "2026-11-13T10:21:00+02:00"}]
    _pin(ACME[0] + 0.0003, ACME[1], sent=datetime(2026, 11, 13, 7, 58, tzinfo=timezone.utc))
    a, b, _ = ta.period("this week", NOW)
    text = ta.person_text(ta.person_week("t1", {"name": "Thabo", "whatsapp": REP}, a, b, NOW), "this week", NOW)
    assert "meeting with Acme Builders, 12 Main Rd · checked in 09:58 (33 m away), 2 photos" in text
    assert "1 check-in" in text
    assert {r["name"]: r["check_ins"] for r in ta.team_summary("t1", a, b, now=NOW)}["Thabo"] == 1


def test_meetings_sheet_has_the_check_in(world):
    from vula.commerce import sheet_builder as sb
    _pin(ACME[0] + 0.0003, ACME[1], sent=datetime(2026, 11, 13, 7, 58, tzinfo=timezone.utc))
    spec = sb.validate({"source": "meetings", "columns": ["date", "with", "checked_in", "distance_m"],
                        "filters": {"period": "this_week"}})
    rows = sb.rows_for("t1", spec, {"names": [], "query": ""}, today=NOW.date())
    assert [(r["with"], r["checked_in"], r["distance_m"]) for r in rows] == [("Acme Builders", "09:58", 33),
                                                                            ("Late Co", "", None)]


def test_routing_phrases():
    from core.skills.base import looks_like_owner_admin_question as q
    for s in ("arrived", "I have arrived", "I'm at Acme Builders", "check in", "checking in"):
        assert q(s), s
    assert not q("how do i check in to the hotel")
