"""Outlook calendar → the meetings diary; reminders only for people who asked."""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula import outlook_calendar as oc
from vula.when import SAST

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=SAST)
IAN = {"id": "o", "name": "Ian", "whatsapp": "27820000001", "role": "owner", "email": "ian@biz.co.za"}
THABO = {"id": "t", "name": "Thabo", "whatsapp": "27820000002", "role": "sales_rep", "email": "thabo@biz.co.za"}


def _t(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


class _Q:
    def __init__(self, store, name):
        self.store, self.name, self.f, self.op, self.payload = store, name, [], "select", None

    def select(self, *_): return self
    def limit(self, *_): return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def eq(self, k, v): self.f.append(lambda r: r.get(k) == v); return self
    def gte(self, k, v): self.f.append(lambda r: r.get(k) and _t(r[k]) >= _t(v)); return self
    def lte(self, k, v): self.f.append(lambda r: r.get(k) and _t(r[k]) <= _t(v)); return self

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if self.op == "insert":
            row = {"id": f"m{len(rows) + 1}", "reminded_at": None, **self.payload}
            rows.append(row)
            return type("R", (), {"data": [row]})()
        hit = [r for r in rows if all(f(r) for f in self.f)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        return type("R", (), {"data": hit})()


def _event(i, subject, start, location="", cancelled=False, all_day=False):
    return {"id": i, "subject": subject, "start": {"dateTime": start + ".0000000", "timeZone": "UTC"},
            "location": {"displayName": location}, "isCancelled": cancelled, "isAllDay": all_day}


@pytest.fixture
def world(monkeypatch):
    store = {"vula_team_members": [{**IAN, "tenant_id": "t1"},
                                   {**THABO, "tenant_id": "t1", "calendar_remind_minutes": 30, "calendar_brief": True}]}
    client = type("C", (), {"table": lambda s, n: _Q(store, n)})()
    monkeypatch.setattr(oc, "_client", lambda: client)
    import vula.team_index as team_index
    monkeypatch.setattr(team_index, "active_members", lambda tid: [IAN, THABO])
    monkeypatch.setattr(team_index, "member_for_phone",
                        lambda tid, p: next((m for m in (IAN, THABO) if m["whatsapp"][-9:] == "".join(c for c in p if c.isdigit())[-9:]), None))
    monkeypatch.setattr(team_index, "invalidate", lambda *a: None)
    state = {"scopes": "openid Mail.Read Calendars.Read offline_access", "email": "thabo@biz.co.za", "events": []}

    async def token(tid):
        return {"access_token": "tok", "email": state["email"], "scopes": state["scopes"]}
    import vula.microsoft.credentials as cred
    monkeypatch.setattr(cred, "get_access_token", token)

    async def fetch(tok, a, b):
        return state["events"]
    monkeypatch.setattr(oc, "fetch", fetch)
    return store, state


def _sync():
    return asyncio.run(oc.sync("t1", NOW))


def test_events_come_into_the_diary_for_the_connected_person(world):
    store, state = world
    state["events"] = [_event("e1", "Site visit — Acme", "2026-11-13T08:00:00", "12 Main Rd, Paarden Eiland"),
                       _event("e2", "Teams catch-up", "2026-11-13T12:00:00", "Microsoft Teams Meeting"),
                       _event("e3", "Leave", "2026-11-14T00:00:00", all_day=True),
                       _event("e4", "Cancelled one", "2026-11-14T08:00:00", cancelled=True)]
    out = _sync()
    rows = store["vula_meetings"]
    assert out == {"added": 2, "updated": 0, "cancelled": 0, "for": "Thabo"}
    acme = rows[0]
    assert (acme["created_by"], acme["source"], acme["external_id"]) == ("27820000002", "outlook", "outlook:e1")
    assert acme["starts_at"] == "2026-11-13T08:00:00+00:00" and acme["address"] == "12 Main Rd, Paarden Eiland"
    assert acme["remind_minutes"] == 30 and acme["with_brief"] is True        # Thabo asked for these
    assert rows[1]["address"] is None                                          # an online meeting


def test_moved_renamed_and_removed_events(world):
    store, state = world
    state["events"] = [_event("e1", "Acme", "2026-11-13T08:00:00", "12 Main Rd"),
                       _event("e2", "Bongi", "2026-11-13T12:00:00")]
    _sync()
    store["vula_meetings"][0]["reminded_at"] = "2026-11-13T07:30:00+00:00"
    state["events"] = [_event("e1", "Acme (moved)", "2026-11-13T09:00:00", "12 Main Rd")]
    out = _sync()
    acme, bongi = store["vula_meetings"]
    assert out["updated"] == 1 and out["cancelled"] == 1 and out["added"] == 0
    assert acme["with_name"] == "Acme (moved)" and acme["starts_at"] == "2026-11-13T09:00:00+00:00"
    assert acme["reminded_at"] is None and bongi["status"] == "cancelled"
    assert _sync() == {"added": 0, "updated": 0, "cancelled": 0, "for": "Thabo"}   # nothing changes twice


def test_no_reminder_unless_the_person_asked(world):
    store, state = world
    state["email"] = "ian@biz.co.za"                                          # Ian never asked
    state["events"] = [_event("e1", "Acme", "2026-11-13T08:00:00")]
    _sync()
    assert store["vula_meetings"][0]["created_by"] == "27820000001"
    assert store["vula_meetings"][0]["remind_minutes"] is None and store["vula_meetings"][0]["with_brief"] is False


def test_a_connection_without_calendar_access_is_left_alone(world):
    store, state = world
    state["scopes"] = "openid Mail.Read offline_access"
    assert "reconnect" in _sync()["skipped"] and not store.get("vula_meetings")


def test_unknown_account_email_goes_to_the_owner(world):
    store, state = world
    state["email"] = "shared-inbox@biz.co.za"
    state["events"] = [_event("e1", "Acme", "2026-11-13T08:00:00")]
    assert _sync()["for"] == "Ian"


def test_setting_reminders_applies_to_synced_meetings(world):
    store, state = world
    state["email"] = "ian@biz.co.za"
    state["events"] = [_event("e1", "Acme", "2026-11-13T08:00:00")]
    _sync()
    back = oc.set_reminders("t1", "27820000001", 60, True, now=NOW)
    assert back["calendar_remind_minutes"] == 60 and back["calendar_brief"] is True
    assert store["vula_meetings"][0]["remind_minutes"] == 60 and store["vula_meetings"][0]["with_brief"] is True
    oc.set_reminders("t1", "27820000001", None, now=NOW)
    assert store["vula_meetings"][0]["remind_minutes"] is None


def test_calendar_action_on_whatsapp(world, monkeypatch):
    store, state = world
    store["vula_microsoft_accounts"] = [{"tenant_id": "t1", "email": "ian@biz.co.za", "scopes": state["scopes"],
                                         "status": "connected"}]
    import vula.commerce.service as service
    monkeypatch.setattr(service, "_client", oc._client)
    from core.skills.commerce_admin import CommerceAdminSkill
    s = CommerceAdminSkill()
    out = s._meeting("t1", {"action": "calendar"}, {"phone": "27820000001"})
    assert "I'm reading the business's Outlook calendar (ian@biz.co.za)" in out["reply_verbatim"]
    out = s._meeting("t1", {"action": "calendar", "calendar_op": "remind"}, {"phone": "27820000001"})
    assert out["status"] == "need_info" and "How long before" in out["message"]
    out = s._meeting("t1", {"action": "calendar", "remind": "30 minutes before"}, {"phone": "27820000001"})
    assert "I'll remind you 30 minutes before each meeting in your Outlook calendar (ian@biz.co.za)" in out["reply_verbatim"]
    out = s._meeting("t1", {"action": "calendar", "remind": "no reminders"}, {"phone": "27820000001"})
    assert out["reply_verbatim"].startswith("⏸️ No reminders")
    store["vula_microsoft_accounts"][0]["scopes"] = "Mail.Read"
    out = s._meeting("t1", {"action": "calendar", "remind": "1 hour"}, {"phone": "27820000001"})
    assert "without the calendar" in out["reply_verbatim"]


def test_graph_event_parsing():
    row = oc.to_meeting({"id": "x", "subject": " Acme ", "start": {"dateTime": "2026-11-13T08:00:00.0000000"},
                         "location": {"displayName": "", "address": {"street": "5 Dock Rd", "city": "Cape Town"}}})
    assert row == {"external_id": "outlook:x", "with_name": "Acme", "address": "5 Dock Rd, Cape Town",
                   "starts_at": "2026-11-13T08:00:00+00:00", "status": "scheduled"}


# ── token refresh never breaks an older connection ────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_falls_back_to_what_the_connection_granted():
    from vula.microsoft import service as ms_service
    from vula.microsoft.credentials import _refresh
    refused = MagicMock(status_code=400, text="AADSTS65001: consent required")
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"access_token": "new", "expires_in": 3600, "scope": "Mail.Read offline_access"}
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(side_effect=[refused, ok])
    with patch("vula.microsoft.credentials.httpx.AsyncClient", return_value=client), \
            patch("vula.microsoft.credentials._client") as db:
        assert await _refresh("t1", "rt", "Mail.Read offline_access") == "new"
    scopes = [c.kwargs["data"]["scope"] for c in client.post.call_args_list]
    assert scopes == [" ".join(ms_service.SCOPES), "Mail.Read offline_access"]
    assert "Calendars.Read" in scopes[0]
    saved = db.return_value.table.return_value.update.call_args.args[0]
    assert saved["scopes"] == "Mail.Read offline_access"
