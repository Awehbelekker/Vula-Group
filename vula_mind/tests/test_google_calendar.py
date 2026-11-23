"""Each person's own Google Calendar: calendar-only link, their own diary, never another's."""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

from vula import google_calendar as gcal
from vula import oauth_state
from vula import outlook_calendar as oc
from vula.api import google as api
from vula.google import member_credentials as gc
from vula.microsoft import member_credentials as mc
from vula.when import SAST

from tests.test_member_calendar import IAN, THABO, _Q, _t  # noqa: F401 — the same fake DB

NOW = datetime(2026, 11, 15, 9, 0, tzinfo=SAST)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "secrets_key", "test-signing-secret", raising=False)
    monkeypatch.setattr(settings, "google_client_id", "gid.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "gsecret")


@pytest.fixture
def world(monkeypatch):
    store = {"vula_team_members": [{**IAN, "tenant_id": "t1"}, {**THABO, "tenant_id": "t1", "calendar_remind_minutes": 15}]}
    client = type("C", (), {"table": lambda s, n: _Q(store, n)})()
    for mod in (oc, mc):
        monkeypatch.setattr(mod, "_client", lambda: client)
    import vula.commerce.service as service
    monkeypatch.setattr(service, "_client", lambda: client)
    import vula.team_index as team_index
    monkeypatch.setattr(team_index, "active_members", lambda tid: [IAN, THABO])
    monkeypatch.setattr(team_index, "member_for_phone",
                        lambda tid, p: next((m for m in (IAN, THABO) if m["whatsapp"][-9:] == "".join(c for c in p if c.isdigit())[-9:]), None))
    import vula.email_imap.credentials as ec
    monkeypatch.setattr(ec, "encrypt_secret", lambda v: f"enc:{v}")
    monkeypatch.setattr(ec, "decrypt_secret", lambda v: (v or "").removeprefix("enc:"))
    events = []

    async def fetch(token, a, b):
        return events
    monkeypatch.setattr(gcal, "fetch", fetch)
    return store, events


def _g(i, summary, start, location="", status="confirmed"):
    return {"id": i, "summary": summary, "start": {"dateTime": start}, "location": location, "status": status}


def test_the_link_asks_google_for_the_calendar_only_with_a_refresh_token():
    token = parse_qs(urlparse(gc.link_for("t1", "t")).query)["t"][0]
    resp = asyncio.run(api.my_calendar_start(t=token))
    q = parse_qs(urlparse(resp.headers["location"]).query)
    assert resp.status_code == 302 and q["scope"] == ["openid email https://www.googleapis.com/auth/calendar.events.readonly"]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert oauth_state.verify(gc.STATE_KIND, q["state"][0]) == {"tenant_id": "t1", "extra": {"m": "t"}}


def test_an_expired_or_other_kind_of_link_is_refused():
    old = oauth_state.sign(gc.LINK_KIND, "t1", {"m": "t"}, now=1_000_000)
    assert "expired" in asyncio.run(api.my_calendar_start(t=old)).body.decode()
    outlook_link = oauth_state.sign(mc.LINK_KIND, "t1", {"m": "t"})
    assert "expired" in asyncio.run(api.my_calendar_start(t=outlook_link)).body.decode()


def test_callback_stores_on_that_person_and_syncs(world, monkeypatch):
    store, events = world
    monkeypatch.setattr(api.service, "exchange_code", AsyncMock(return_value={
        "access_token": "g-tok", "refresh_token": "rt", "expires_in": 3600, "email": "thabo@gmail.com",
        "scope": "openid email https://www.googleapis.com/auth/calendar.events.readonly"}))
    events.append(_g("e1", "Site visit Acme", "2026-11-16T10:00:00+02:00", "12 Main Rd"))
    page = asyncio.run(api.oauth_callback(code="c", state=oauth_state.sign(gc.STATE_KIND, "t1", {"m": "t"}))).body.decode()
    assert "Your calendar is connected" in page
    row = store["vula_member_calendars"][0]
    assert (row["provider"], row["member_id"], row["email"]) == ("google", "t", "thabo@gmail.com")
    m = store["vula_meetings"][0]
    assert (m["source"], m["external_id"], m["created_by"]) == ("google", "google:e1", "27820000002")
    assert m["starts_at"] == "2026-11-16T08:00:00+00:00" and m["address"] == "12 Main Rd" and m["remind_minutes"] == 15


def test_calendar_box_not_ticked_is_said_plainly(world, monkeypatch):
    store, _ = world
    monkeypatch.setattr(api.service, "exchange_code", AsyncMock(return_value={
        "access_token": "g-tok", "email": "x@gmail.com", "scope": "openid email"}))
    page = asyncio.run(api.oauth_callback(code="c", state=oauth_state.sign(gc.STATE_KIND, "t1", {"m": "t"}))).body.decode()
    assert "tick the calendar box" in page and not store.get("vula_member_calendars")


@pytest.mark.parametrize("event,want", [
    (_g("a", "Lunch", "2026-11-16T12:00:00Z", "https://meet.google.com/abc"), {"address": None}),
    ({"id": "b", "summary": "Leave", "start": {"date": "2026-11-16"}}, None),         # all-day
    (_g("c", "", "2026-11-16T12:00:00Z", status="cancelled"), {"with_name": "Meeting", "status": "cancelled"}),
])
def test_event_mapping(event, want):
    out = gcal.to_meeting(event)
    if want is None:
        assert out is None
    else:
        for k, v in want.items():
            assert out[k] == v


def test_google_and_outlook_never_touch_each_other(world):
    store, events = world
    store["vula_member_calendars"] = [{"tenant_id": "t1", "member_id": "t", "provider": "google", "status": "connected",
                                       "access_token": "enc:g", "scopes": "calendar.events.readonly",
                                       "token_expiry": "2099-01-01T00:00:00+00:00", "email": "thabo@gmail.com"}]
    store["vula_meetings"] = [{"id": "o1", "tenant_id": "t1", "source": "outlook", "created_by": "27820000002",
                               "external_id": "outlook:x", "starts_at": "2026-11-16T08:00:00+00:00", "status": "scheduled"}]
    asyncio.run(gcal.sync_member("t1", "t", NOW))                         # Google calendar is empty
    assert store["vula_meetings"][0]["status"] == "scheduled"
    assert oc.disconnect_member("t1", "t", NOW, provider="google") is True
    assert store["vula_meetings"][0]["status"] == "scheduled"            # Outlook meeting untouched
    assert not [r for r in store["vula_member_calendars"] if r["provider"] == "google"]


def test_whatsapp_connect_my_google_calendar(world, monkeypatch):
    from core.skills.commerce_admin import CommerceAdminSkill
    out = CommerceAdminSkill()._meeting("t1", {"action": "calendar", "calendar_op": "connect", "calendar_provider": "google"},
                                        {"phone": "27820000002"})
    assert "your Google calendar" in out["reply_verbatim"] and "/v1/google/my-calendar/start?t=" in out["reply_verbatim"]
    from config import settings
    monkeypatch.setattr(settings, "google_client_id", "")
    out = CommerceAdminSkill()._meeting("t1", {"action": "calendar", "calendar_op": "connect", "calendar_provider": "google"},
                                        {"phone": "27820000002"})
    assert "isn't switched on" in out["reply_verbatim"]


def test_sync_all_includes_google(world, monkeypatch):
    store, events = world
    store["vula_member_calendars"] = [{"tenant_id": "t1", "member_id": "t", "provider": "google", "status": "connected",
                                       "access_token": "enc:g", "scopes": "calendar.events.readonly",
                                       "token_expiry": "2099-01-01T00:00:00+00:00"}]
    events.append(_g("e9", "Acme", "2026-11-16T10:00:00+02:00"))
    out = asyncio.run(oc.sync_all())
    assert out["google:t1/t"]["added"] == 1
