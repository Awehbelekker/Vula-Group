"""Each person connects their own Outlook calendar; only they can, and no meeting comes in twice."""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from vula import oauth_state
from vula import outlook_calendar as oc
from vula.api import microsoft as api
from vula.microsoft import member_credentials as mc
from vula.when import SAST

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=SAST)
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
    def upsert(self, row, **k): self.op, self.payload = "upsert", row; return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def delete(self): self.op = "delete"; return self
    def eq(self, k, v): self.f.append(lambda r: r.get(k) == v); return self
    def gte(self, k, v): self.f.append(lambda r: r.get(k) and _t(r[k]) >= _t(v)); return self
    def lte(self, k, v): self.f.append(lambda r: r.get(k) and _t(r[k]) <= _t(v)); return self

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if self.op == "insert":
            row = {"id": f"m{len(rows) + 1}", "reminded_at": None, **self.payload}
            rows.append(row)
            return type("R", (), {"data": [row]})()
        if self.op == "upsert":
            key = ("tenant_id", "member_id", "provider")
            old = next((r for r in rows if all(r.get(k) == self.payload.get(k) for k in key)), None)
            (old.update(self.payload) if old else rows.append(dict(self.payload)))
            return type("R", (), {"data": [self.payload]})()
        hit = [r for r in rows if all(f(r) for f in self.f)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        if self.op == "delete":
            self.store[self.name] = [r for r in rows if r not in hit]
        return type("R", (), {"data": hit})()


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "secrets_key", "test-signing-secret", raising=False)


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
    events = {"business": [], "thabo": []}

    async def fetch(token, a, b):
        return events[token]
    monkeypatch.setattr(oc, "fetch", fetch)
    return store, events


def _ev(i, subject, start):
    return {"id": i, "subject": subject, "start": {"dateTime": start + ".0000000"}, "location": {"displayName": ""}}


# ── the link and the callback ─────────────────────────────────────────────────────────────

def test_the_link_opens_microsoft_asking_for_the_calendar_only():
    url = mc.link_for("t1", "t")
    token = parse_qs(urlparse(url).query)["t"][0]
    resp = asyncio.run(api.my_calendar_start(t=token))
    q = parse_qs(urlparse(resp.headers["location"]).query)
    assert resp.status_code == 302 and q["scope"] == ["offline_access User.Read Calendars.Read"]
    state = oauth_state.verify(mc.STATE_KIND, q["state"][0])
    assert state == {"tenant_id": "t1", "extra": {"m": "t"}}


def test_an_expired_or_forged_link_is_refused():
    old = oauth_state.sign(mc.LINK_KIND, "t1", {"m": "t"}, now=1_000_000)
    assert "expired" in asyncio.run(api.my_calendar_start(t=old)).body.decode()
    business = oauth_state.sign("microsoft", "t1")                  # another kind of state
    assert "expired" in asyncio.run(api.my_calendar_start(t=business)).body.decode()


def test_the_callback_stores_the_token_on_that_person(world, monkeypatch):
    store, events = world
    monkeypatch.setattr(api.service, "exchange_code", AsyncMock(return_value={
        "access_token": "thabo", "refresh_token": "rt", "expires_in": 3600, "email": "thabo@outlook.com",
        "scope": "Calendars.Read User.Read"}))
    events["thabo"] = [_ev("p1", "Acme", "2026-11-13T10:00:00")]
    state = oauth_state.sign(mc.STATE_KIND, "t1", {"m": "t"})
    page = asyncio.run(api.oauth_callback(code="c", state=state)).body.decode()
    assert "Your calendar is connected" in page
    row = store["vula_member_calendars"][0]
    assert (row["member_id"], row["email"], row["access_token"]) == ("t", "thabo@outlook.com", "enc:thabo")
    assert api.service.exchange_code.call_args.kwargs["scopes"] == mc.PERSONAL_SCOPES
    assert store["vula_meetings"][0]["created_by"] == "27820000002"      # synced straight away


def test_a_forged_callback_state_stores_nothing(world):
    store, _ = world
    page = asyncio.run(api.oauth_callback(code="c", state="forged.state")).body.decode()
    assert "expired" in page and not store.get("vula_member_calendars")


# ── sync ──────────────────────────────────────────────────────────────────────────────────

def _connect(store, member="t", email="thabo@outlook.com"):
    store.setdefault("vula_member_calendars", []).append(
        {"tenant_id": "t1", "member_id": member, "provider": "microsoft", "email": email, "status": "connected",
         "access_token": "enc:thabo", "scopes": "Calendars.Read", "token_expiry": "2099-01-01T00:00:00+00:00"})


def test_own_calendar_fills_their_own_diary_with_their_reminder(world):
    store, events = world
    _connect(store)
    events["thabo"] = [_ev("p1", "Acme", "2026-11-13T10:00:00")]
    out = asyncio.run(oc.sync_member("t1", "t", NOW))
    m = store["vula_meetings"][0]
    assert out["added"] == 1 and m["created_by"] == "27820000002" and m["remind_minutes"] == 15
    assert store["vula_member_calendars"][0]["last_sync_error"] is None


def test_business_calendar_skips_someone_with_their_own(world, monkeypatch):
    store, events = world
    _connect(store)
    import vula.microsoft.credentials as cred

    async def biz(tid):
        return {"access_token": "business", "email": "thabo@biz.co.za", "scopes": "Calendars.Read"}
    monkeypatch.setattr(cred, "get_access_token", biz)
    events["business"] = [_ev("b1", "Acme", "2026-11-13T10:00:00")]
    assert "reads their own calendar" in asyncio.run(oc.sync("t1", NOW))["skipped"]
    assert not store.get("vula_meetings")


def test_two_calendars_never_cancel_each_others_meetings(world):
    store, events = world
    _connect(store)
    store["vula_meetings"] = [{"id": "x", "tenant_id": "t1", "source": "outlook", "created_by": "27820000001",
                               "external_id": "outlook:ian1", "starts_at": "2026-11-13T12:00:00+00:00", "status": "scheduled"}]
    asyncio.run(oc.sync_member("t1", "t", NOW))                        # Thabo's calendar is empty
    assert store["vula_meetings"][0]["status"] == "scheduled"           # Ian's meeting untouched


def test_disconnect_cancels_future_meetings_only(world):
    store, events = world
    _connect(store)
    store["vula_meetings"] = [
        {"id": "past", "tenant_id": "t1", "source": "outlook", "created_by": "27820000002", "starts_at": "2026-11-12T08:00:00+00:00", "status": "scheduled"},
        {"id": "next", "tenant_id": "t1", "source": "outlook", "created_by": "27820000002", "starts_at": "2026-11-14T08:00:00+00:00", "status": "scheduled"},
        {"id": "mine", "tenant_id": "t1", "source": "whatsapp", "created_by": "27820000002", "starts_at": "2026-11-14T09:00:00+00:00", "status": "scheduled"}]
    assert oc.disconnect_member("t1", "t", NOW) is True
    st = {m["id"]: m["status"] for m in store["vula_meetings"]}
    assert st == {"past": "scheduled", "next": "cancelled", "mine": "scheduled"}
    assert not store["vula_member_calendars"]


# ── only the person themselves ────────────────────────────────────────────────────────────

def test_dashboard_endpoints_act_on_the_caller_only(world, monkeypatch):
    store, _ = world
    import vula.api.tenant_auth as tauth

    async def scope(auth, tid):
        return {"member": THABO, "full": False}
    monkeypatch.setattr(tauth, "member_scope", scope)
    link = asyncio.run(api.my_calendar_link("t1", authorization="Bearer x"))
    token = parse_qs(urlparse(link["url"]).query)["t"][0]
    assert oauth_state.verify(mc.LINK_KIND, token)["extra"] == {"m": "t"}
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.my_calendar("t1", authorization=""))
    assert e.value.status_code == 401


def test_whatsapp_connect_and_disconnect(world):
    store, _ = world
    from core.skills.commerce_admin import CommerceAdminSkill
    s = CommerceAdminSkill()
    out = s._meeting("t1", {"action": "calendar", "calendar_op": "connect"}, {"phone": "27820000002"})
    assert "/v1/microsoft/my-calendar/start?t=" in out["reply_verbatim"] and "not your email" in out["reply_verbatim"]
    out = s._meeting("t1", {"action": "calendar", "calendar_op": "connect"}, {"phone": "27829999999"})
    assert "error" in out                                               # not on the team
    _connect(store)
    out = s._meeting("t1", {"action": "calendar"}, {"phone": "27820000002"})
    assert "your own Outlook calendar (thabo@outlook.com)" in out["reply_verbatim"]
    out = s._meeting("t1", {"action": "calendar", "calendar_op": "disconnect"}, {"phone": "27820000002"})
    assert out["reply_verbatim"].startswith("✅ Your calendar is disconnected")


@pytest.mark.asyncio
async def test_personal_refresh_falls_back_to_what_was_granted():
    refused = MagicMock(status_code=400, text="consent required")
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"access_token": "new", "expires_in": 3600}
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(side_effect=[refused, ok])
    with patch("vula.microsoft.member_credentials.httpx.AsyncClient", return_value=client), \
            patch("vula.microsoft.member_credentials._client"), \
            patch("vula.email_imap.credentials.encrypt_secret", lambda v: v):
        assert await mc._refresh("t1", "t", "rt", "Calendars.Read") == "new"
    scopes = [c.kwargs["data"]["scope"] for c in client.post.call_args_list]
    assert scopes == ["offline_access User.Read Calendars.Read", "Calendars.Read offline_access"]
