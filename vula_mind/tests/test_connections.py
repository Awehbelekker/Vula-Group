"""App connections: their state, the daily check, and Connect buttons that never go nowhere
(5 Nov 2026). A mailbox couldn't sign in for two weeks and a ClickUp link had been dead since
August without anyone hearing about it. Made-up business, people and addresses throughout.
"""
import asyncio
from types import SimpleNamespace

import pytest

from vula import connections as cx

T = "thandi-demo"


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.patch, self.up = db, name, [], None, None

    def select(self, *a): return self
    def order(self, *a, **k): return self
    def eq(self, k, v): self.f.append((k, v)); return self
    def update(self, patch): self.patch = patch; return self
    def upsert(self, row, on_conflict=None): self.up = (row, on_conflict.split(",")); return self

    def execute(self):
        rows = self.db.setdefault(self.name, [])
        if self.up:
            row, keys = self.up
            hit = next((r for r in rows if all(r.get(k) == row.get(k) for k in keys)), None)
            if hit:
                hit.update(row)
            else:
                rows.append(dict(row))
            return SimpleNamespace(data=[row])
        hit = [r for r in rows if all(r.get(k) == v for k, v in self.f)]
        if self.patch is not None:
            for r in hit:
                r.update(self.patch)
        return SimpleNamespace(data=[dict(r) for r in hit])


class _DB:
    def __init__(self, **t): self.t = t
    def table(self, name): return _Q(self.t, name)


@pytest.fixture()
def world(monkeypatch):
    db = _DB(
        vula_clickup_accounts=[{"tenant_id": T, "status": "needs_reconnect", "api_token": "fernet:x",
                                "last_sync_status": "error", "last_sync_error": "ClickUp refused Vula's sign-in",
                                "last_synced_at": "2026-10-03T13:51:04+00:00"}],
        vula_email_accounts=[{"tenant_id": T, "email": "office@seaview.example", "status": "error", "secret": "fernet:y",
                              "sync_fail_count": 40, "last_error": "AUTHENTICATIONFAILED",
                              "last_sync_at": "2026-10-23T09:14:53+00:00"},
                             {"tenant_id": T, "email": "sales@seaview.example", "status": "connected", "secret": "fernet:z",
                              "sync_fail_count": 0}],
        vula_whatsapp_accounts=[{"tenant_id": T, "phone_number": "+27 21 000 0000", "status": "connected",
                                 "access_token": "fernet:w", "token_expires_at": None}],
        vula_google_accounts=[], vula_microsoft_accounts=[], vula_dynamics365_accounts=[],
        vula_team_members=[{"tenant_id": T, "active": True, "role": "owner", "whatsapp": "27820000001", "name": "Sam"},
                           {"tenant_id": T, "active": True, "role": "staff", "whatsapp": "27820000002", "name": "Thandi"}],
        vula_tenant_config=[{"tenant_id": T}],
        vula_connection_health=[])
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    monkeypatch.setattr("vula.integrations.notify._client", lambda: db)
    monkeypatch.setattr(cx, "_locked", lambda v: False)
    monkeypatch.setattr("vula.api.tenants.dashboard_link", lambda t, p: f"https://seaview.example/#/go/{p}")
    monkeypatch.setattr("vula.api.tenants.is_active", lambda t: True)
    for name in ("clickup_client_id", "clickup_client_secret", "google_client_id", "google_client_secret",
                 "microsoft_client_id", "microsoft_client_secret"):
        monkeypatch.setattr(cx.settings, name, "")
    sent = []

    async def send(to, text, tenant_id=None, idem_key=None, **k):
        sent.append((to, text, idem_key))
        return True
    monkeypatch.setattr("vula.api.whatsapp._send_reply", send)
    return db, sent


def _by(items):
    return {(c["provider"], c["account"]): c for c in items}


def test_each_connection_says_what_state_it_is_in(world):
    items = _by(cx.state(T))
    assert items[("email", "office@seaview.example")]["state"] == "broken"
    assert items[("email", "office@seaview.example")]["problem"] == "the mail server refused the password"
    assert items[("email", "sales@seaview.example")]["state"] == "working"
    assert items[("clickup", "")]["state"] == "broken" and "refused" in items[("clickup", "")]["problem"]
    assert items[("whatsapp", "+27 21 000 0000")]["state"] == "working"
    assert items[("google", "")]["state"] == "unavailable"            # Vula's Google app isn't set up
    assert items[("clickup", "")]["one_tap"] is False


def test_a_locked_saved_sign_in_is_broken(world, monkeypatch):
    monkeypatch.setattr(cx, "_locked", lambda v: v == "fernet:z")
    items = _by(cx.state(T))
    assert items[("email", "sales@seaview.example")]["state"] == "broken"
    assert "can't be unlocked" in items[("email", "sales@seaview.example")]["problem"]


def test_the_owner_is_told_once_with_a_link(world):
    db, sent = world
    res = asyncio.run(cx.check_tenant(T))
    assert res["told"] == 1 and len(sent) == 1
    to, text, key = sent[0]
    assert to == "27820000001"                                         # the owner, not the staff member
    assert "2 of your Vula connections stopped working" in text
    assert "office@seaview.example" in text and "ClickUp" in text and "since 23 Oct" in text
    assert "https://seaview.example/#/go/settings" in text
    assert key.startswith("conn:thandi-demo:")
    asyncio.run(cx.check_tenant(T))                                    # the next day: nothing new
    assert len(sent) == 1


def test_working_again_is_said_once(world):
    db, sent = world
    asyncio.run(cx.check_tenant(T))
    db.t["vula_email_accounts"][0].update({"status": "connected", "sync_fail_count": 0})
    asyncio.run(cx.check_tenant(T))
    assert "Working again: Mailbox office@seaview.example" in sent[-1][1]
    asyncio.run(cx.check_tenant(T))
    assert len(sent) == 2


def test_a_week_later_it_reminds(world):
    db, sent = world
    asyncio.run(cx.check_tenant(T))
    for r in db.t["vula_connection_health"]:
        if r.get("alerted_at"):
            r["alerted_at"] = "2026-10-01T00:00:00+00:00"
    asyncio.run(cx.check_tenant(T))
    assert len(sent) == 2 and "stopped working" in sent[-1][1]


def test_no_owner_falls_back_to_admins(world):
    db, sent = world
    db.t["vula_team_members"][0]["role"] = "manager"
    asyncio.run(cx.check_tenant(T))
    assert sent and sent[0][0] == "27820000001"
    db.t["vula_team_members"][0]["role"] = "staff"
    db.t["vula_connection_health"].clear()
    sent.clear()
    asyncio.run(cx.check_tenant(T))
    assert sent == []                                                  # staff aren't told


def test_one_tap_needs_both_id_and_secret(world, monkeypatch):
    monkeypatch.setattr(cx.settings, "clickup_client_id", "abc")
    assert not cx.one_tap("clickup")
    monkeypatch.setattr(cx.settings, "clickup_client_secret", "def")
    assert cx.one_tap("clickup")


def test_a_connect_button_without_vulas_app_says_so_and_tells_the_operator(world, monkeypatch):
    told = []

    async def alert(text, log_text=True):
        told.append(text)
        return True
    monkeypatch.setattr("vula.integrations.platform_support.alert_operator", alert)
    cx._TOLD.clear()
    from vula.api import clickup, google, microsoft
    out = asyncio.run(clickup.authorize_url(T))
    assert out["available"] is False and "one tap" in out["error"] and "url" not in out
    asyncio.run(clickup.authorize_url(T))
    assert len(told) == 1 and "CLICKUP_CLIENT_ID" in told[0]           # once, not per click
    assert asyncio.run(google.authorize_url(T))["available"] is False
    assert asyncio.run(microsoft.authorize_url(T))["available"] is False


def test_the_check_runs_for_every_active_business(world, monkeypatch):
    res = asyncio.run(cx.check_all())
    assert res == {"tenants": 1, "broken": 2, "told": 1}
