"""Team activity: one person's week, the team's counts, who may see what, and team data in sheets."""
from datetime import datetime

import pytest

from vula import team_activity as ta
from vula.when import SAST

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=SAST)          # Thursday noon
OWNER, THABO, LINDI, STAFF = "27820000001", "27820000002", "27820000003", "27820000004"
TEAM = [
    {"id": "o", "name": "Ian", "whatsapp": OWNER, "role": "owner", "access": []},
    {"id": "t", "name": "Thabo Mokoena", "whatsapp": THABO, "role": "sales_rep", "access": ["crm"]},
    {"id": "l", "name": "Lindiwe Dlamini", "whatsapp": LINDI, "role": "sales_rep", "access": ["crm"]},
    {"id": "s", "name": "Thabo Nkosi", "whatsapp": STAFF, "role": "staff", "access": ["inbox"]},
]


def _t(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


class _Q:
    def __init__(self, store, name):
        self.store, self.name, self.f, self.span = store, name, [], None

    def select(self, *_): return self
    def order(self, *a, **k): return self
    def limit(self, *_): return self
    def range(self, a, b): self.span = (a, b); return self
    def eq(self, k, v): self.f.append(lambda r: r.get(k) == v); return self
    def neq(self, k, v): self.f.append(lambda r: r.get(k) != v); return self
    def in_(self, k, vs): self.f.append(lambda r: r.get(k) in vs); return self
    def gte(self, k, v): self.f.append(lambda r: r.get(k) is not None and _t(r[k]) >= _t(v)); return self
    def lt(self, k, v): self.f.append(lambda r: r.get(k) is not None and _t(r[k]) < _t(v)); return self
    def lte(self, k, v): self.f.append(lambda r: r.get(k) is not None and _t(r[k]) <= _t(v)); return self

    def execute(self):
        if self.name == "missing_table":
            raise RuntimeError("relation does not exist")
        rows = [r for r in self.store.get(self.name, []) if all(f(r) for f in self.f)]
        if self.span:
            rows = rows[self.span[0]:self.span[1] + 1]
        return type("R", (), {"data": rows})()


@pytest.fixture
def world(monkeypatch):
    store = {
        "vula_meetings": [
            {"tenant_id": "t1", "created_by": THABO, "with_name": "Acme Builders", "address": "12 Main Rd",
             "starts_at": "2026-11-10T10:00:00+02:00", "status": "scheduled"},
            {"tenant_id": "t1", "created_by": "+" + THABO, "with_name": "Bongi Plumbing", "address": None,
             "starts_at": "2026-11-13T09:00:00+02:00", "status": "scheduled"},
            {"tenant_id": "t1", "created_by": THABO, "with_name": "Cancelled Co", "starts_at": "2026-11-13T11:00:00+02:00",
             "status": "cancelled"},
            {"tenant_id": "t2", "created_by": THABO, "with_name": "Other business", "starts_at": "2026-11-13T09:00:00+02:00",
             "status": "scheduled"},                                     # another tenant: never counted
        ],
        "commerce_bookings": [
            {"tenant_id": "t1", "booked_by": "0820000003", "customer_name": "Sam", "service_name": "Site measure",
             "start_at": "2026-11-11T14:00:00+02:00", "status": "confirmed"}],
        "vula_filed_documents": [
            {"tenant_id": "t1", "filed_by": THABO, "category": "meeting_notes", "summary": "Acme wants samples",
             "fields": {"action_items": ["send samples", "quote"]}, "created_at": "2026-11-10T11:00:00+02:00"}],
        "vula_reminders": [
            {"tenant_id": "t1", "created_by": THABO, "text": "Call Acme", "status": "open",
             "due_at": "2026-11-11T09:00:00+02:00"},
            {"tenant_id": "t1", "created_by": THABO, "text": "Send brochure", "status": "done",
             "due_at": "2026-11-10T09:00:00+02:00", "completed_at": "2026-11-10T15:00:00+02:00"}],
        "commerce_contacts": [{"tenant_id": "t1", "created_by": LINDI, "name": "New Lead", "created_at": "2026-11-11T08:00:00+02:00"}],
        "vula_call_sheets": [{"tenant_id": "t1", "rep_whatsapp": THABO, "created_at": "2026-11-09T08:00:00+02:00",
                              "entries": [{"source": "manual", "text": "Phoned Acme", "created_at": "2026-11-11T10:00:00+02:00"},
                                          {"source": "log_meeting", "text": "dup of note", "created_at": "2026-11-10T11:00:00+02:00"}]}],
    }
    client = type("C", (), {"table": lambda s, n: _Q(store, n)})()
    import vula.commerce.service as service
    import vula.team_index as team_index
    import vula.when as when
    monkeypatch.setattr(service, "_client", lambda: client)
    monkeypatch.setattr(ta, "_client", lambda: client)
    monkeypatch.setattr(team_index, "active_members", lambda tid: TEAM)
    monkeypatch.setattr(team_index, "member_for_phone",
                        lambda tid, p: next((m for m in TEAM if m["whatsapp"][-9:] == "".join(c for c in p if c.isdigit())[-9:]), None))
    monkeypatch.setattr(ta, "now_sast", lambda: NOW)
    monkeypatch.setattr(when, "now_sast", lambda: NOW)
    return store


def _skill():
    from core.skills.commerce_admin import CommerceAdminSkill
    return CommerceAdminSkill()


# ── who and when ──────────────────────────────────────────────────────────────────────────

def test_match_by_name_number_and_two_thabos(world):
    assert [m["id"] for m in ta.match_member("t1", "Lindiwe")] == ["l"]
    assert [m["id"] for m in ta.match_member("t1", "lindi")] == ["l"]
    assert [m["id"] for m in ta.match_member("t1", "082 000 0002")] == ["t"]
    assert sorted(m["id"] for m in ta.match_member("t1", "Thabo's")) == ["s", "t"]   # → "which one?"
    assert [m["id"] for m in ta.match_member("t1", "thabo mokoena")] == ["t"]


@pytest.mark.parametrize("words,start,end,label", [
    ("", "2026-11-09", "2026-11-16", "this week"), ("last week", "2026-11-02", "2026-11-09", "last week"),
    ("next week", "2026-11-16", "2026-11-23", "next week"), ("today", "2026-11-12", "2026-11-13", "today"),
    ("this month", "2026-11-01", "2026-12-01", "this month"), ("last month", "2026-10-01", "2026-11-01", "October"),
])
def test_periods(words, start, end, label):
    a, b, lab = ta.period(words, NOW)
    assert (a.date().isoformat(), b.date().isoformat(), lab) == (start, end, label)


# ── counts from rows ──────────────────────────────────────────────────────────────────────

def test_team_summary_counts_each_person_from_rows(world):
    a, b, _ = ta.period("this week", NOW)
    rows = {r["name"]: r for r in ta.team_summary("t1", a, b, now=NOW)}
    t = rows["Thabo Mokoena"]
    assert (t["meetings_held"], t["meetings_coming"]) == (1, 1)   # cancelled + other tenant left out
    assert (t["notes"], t["follow_ups_done"], t["follow_ups_overdue"], t["call_sheet_entries"]) == (1, 1, 1, 1)
    assert rows["Lindiwe Dlamini"]["bookings"] == 1 and rows["Lindiwe Dlamini"]["contacts_added"] == 1  # 082… matched
    assert list(rows) == ["Ian", "Lindiwe Dlamini", "Thabo Mokoena", "Thabo Nkosi"]            # by name, not ranked


def test_role_filter(world):
    a, b, _ = ta.period("this week", NOW)
    assert [r["name"] for r in ta.team_summary("t1", a, b, role="sales_rep", now=NOW)] == ["Lindiwe Dlamini", "Thabo Mokoena"]


def test_a_missing_table_counts_nothing(world, monkeypatch):
    real = ta._client()

    class Partial:
        def table(self, n):
            return _Q({}, "missing_table") if n == "vula_meetings" else real.table(n)
    monkeypatch.setattr(ta, "_client", lambda: Partial())
    a, b, _ = ta.period("this week", NOW)
    t = {r["name"]: r for r in ta.team_summary("t1", a, b, now=NOW)}["Thabo Mokoena"]
    assert t["meetings_held"] == 0 and t["notes"] == 1


def test_person_week_text(world):
    a, b, _ = ta.period("this week", NOW)
    text = ta.person_text(ta.person_week("t1", TEAM[1], a, b, NOW), "this week", NOW)
    assert "📅 *Thabo Mokoena* — this week" in text
    assert "✓ Tue 10 Nov 10:00 — meeting with Acme Builders, 12 Main Rd" in text
    assert "Fri 13 Nov 09:00 — meeting with Bongi Plumbing" in text and "Cancelled Co" not in text
    assert "1 meeting note, 1 follow-up done, 1 call-sheet entry" in text
    assert "🔴 1 follow-up overdue" in text and "Call Acme" in text


# ── who may see what (enforced in the tool) ───────────────────────────────────────────────

def test_owner_sees_anyone(world):
    out = _skill()._team_activity("t1", {"action": "person", "who": "Lindiwe"}, {"phone": OWNER})
    assert "*Lindiwe Dlamini* — this week" in out["reply_verbatim"] and "Site measure" in out["reply_verbatim"]


def test_owner_sees_the_team(world):
    out = _skill()._team_activity("t1", {"action": "team", "period": "this week"}, {"phone": OWNER})
    assert "👥 *Team* — this week" in out["reply_verbatim"] and "*Thabo Mokoena*: 1 meeting held" in out["reply_verbatim"]


def test_a_rep_sees_only_themselves(world):
    mine = _skill()._team_activity("t1", {"action": "person", "who": "me"}, {"phone": THABO})
    assert "*Thabo Mokoena*" in mine["reply_verbatim"]
    other = _skill()._team_activity("t1", {"action": "person", "who": "Lindiwe"}, {"phone": THABO})
    assert "Only the owner or a manager" in other["reply_verbatim"] and "Site measure" not in other["reply_verbatim"]
    team = _skill()._team_activity("t1", {"action": "team"}, {"phone": THABO})
    assert "Only the owner or a manager" in team["reply_verbatim"]


def test_an_unknown_sender_sees_only_themselves(world):
    out = _skill()._team_activity("t1", {"action": "team"}, {"phone": "27829999999"})
    assert "Only the owner or a manager" in out["reply_verbatim"]


def test_two_matches_asks_which(world):
    out = _skill()._team_activity("t1", {"action": "person", "who": "Thabo"}, {"phone": OWNER})
    assert out["status"] == "need_info" and "Thabo Mokoena" in out["message"] and "Thabo Nkosi" in out["message"]


def test_routing_phrases():
    from core.skills.base import looks_like_owner_admin_question as q
    for s in ("how does Thabo's week look", "what did the team do this week", "what did Lindi do last week",
              "team activity this month", "how is my week looking"):
        assert q(s), s
    assert not q("how is your day going")


# ── team data in custom sheets ────────────────────────────────────────────────────────────

def test_sheet_meetings_by_person_with_names(world):
    from vula.commerce import sheet_builder as sb
    spec = sb.validate({"source": "meetings", "group_by": "person", "filters": {"period": "this_week", "person": "thabo"}})
    rows = sb.rows_for("t1", spec, {"names": [], "query": ""}, today=NOW.date())
    assert [(r["person"], r["with"], r["time"]) for r in rows] == [("Thabo Mokoena", "Acme Builders", "10:00"),
                                                                   ("Thabo Mokoena", "Bongi Plumbing", "09:00")]


def test_sheet_follow_ups_and_notes(world):
    from vula.commerce import sheet_builder as sb
    spec = sb.validate({"source": "follow_ups", "filters": {"period": "this_week"}})
    rows = sb.rows_for("t1", spec, {"names": [], "query": ""}, today=NOW.date())
    assert {r["text"] for r in rows} == {"Call Acme", "Send brochure"}
    spec = sb.validate({"source": "meeting_notes", "filters": {"period": "this_week"}})
    rows = sb.rows_for("t1", spec, {"names": [], "query": ""}, today=NOW.date())
    assert rows[0]["actions"] == 2 and rows[0]["person"] == "Thabo Mokoena"


def test_team_sources_need_team_access():
    from vula.commerce import sheet_builder as sb
    assert {sb.SOURCES[s].scope for s in ("meetings", "bookings", "follow_ups", "meeting_notes")} == {"team"}
    with pytest.raises(sb.SpecError):
        sb.validate({"source": "bank", "group_by": "person"})       # money sources have no person


def test_the_activity_endpoint_needs_an_owner_or_manager():
    from vula.api.team_activity import router
    from vula.api.tenant_auth import require_tenant_admin
    deps = {r.path: [d.call for d in r.dependant.dependencies] for r in router.routes}
    assert require_tenant_admin in deps["/{tenant}/activity"]
