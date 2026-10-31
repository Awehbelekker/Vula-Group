"""WhatsApp numbers (EZContact brief, PR 4, 24 Oct).

- A reconnected number used its old token until a restart: the credentials cache never expired
  and connect never cleared it.
- Disconnect didn't stop anything (cached credentials kept sending, the route kept routing), and
  once the cache was gone the business fell back to Vula's own default number.
- Nothing showed a number's health: quality, daily limit, display name, registered to send.
"""
from types import SimpleNamespace

import pytest

from config import settings
from vula.api import whatsapp, whatsapp_connect

_real_creds = whatsapp._get_tenant_wa_creds   # conftest stubs the lookup for every other test
PNID = "1387582947771270"


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.op, self.payload = db, table, [], "select", None

    def select(self, *_a, **_k): return self
    def limit(self, *_a): return self

    def eq(self, k, v):
        self.f.append((k, v))
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def execute(self):
        rows = [r for r in self.db.tables.setdefault(self.table, []) if all(r.get(k) == v for k, v in self.f)]
        if self.op == "update":
            for r in rows:
                r.update(self.payload)
        return SimpleNamespace(data=rows)


class _DB:
    def __init__(self, rows):
        self.tables = {"vula_whatsapp_accounts": rows}

    def table(self, name):
        return _Q(self, name)


@pytest.fixture()
def db(monkeypatch):
    d = _DB([{"tenant_id": "off-the-hook", "phone_number_id": PNID, "access_token": None,
              "status": "connected", "route_mode": "commerce"}])
    monkeypatch.setattr(settings, "whatsapp_token", "server-system-token")
    monkeypatch.setattr(settings, "whatsapp_phone_id", "DEFAULT-NUMBER")
    monkeypatch.setattr("supabase.create_client", lambda *a, **k: d)
    monkeypatch.setattr(whatsapp_connect, "_supabase", lambda: d)
    monkeypatch.setattr("vula.commerce.service._client", lambda: d)
    whatsapp._wa_creds_cache.clear()
    whatsapp._WA_CREDS_AT.clear()
    whatsapp._ROUTE_CACHE.clear()
    whatsapp_connect._HEALTH_CACHE.clear()
    return d


@pytest.mark.asyncio
async def test_cached_credentials_expire(db, monkeypatch):
    import time
    assert (await _real_creds("off-the-hook"))["phone_id"] == PNID
    db.tables["vula_whatsapp_accounts"][0]["phone_number_id"] = "NEW-NUMBER"
    assert (await _real_creds("off-the-hook"))["phone_id"] == PNID          # still cached
    whatsapp._WA_CREDS_AT["off-the-hook"] = time.time() - whatsapp._WA_CREDS_TTL - 1
    assert (await _real_creds("off-the-hook"))["phone_id"] == "NEW-NUMBER"   # re-read after the TTL


@pytest.mark.asyncio
async def test_disconnect_stops_sending_and_routing_at_once(db):
    assert (await _real_creds("off-the-hook"))["phone_id"] == PNID
    assert whatsapp._resolve_number_route(PNID) == ("off-the-hook", "commerce")
    res = await whatsapp_connect.disconnect_whatsapp("off-the-hook")
    assert res["status"] == "disconnected"
    assert "off-the-hook" not in whatsapp._wa_creds_cache and PNID not in whatsapp._ROUTE_CACHE
    assert await _real_creds("off-the-hook") is None                # never Vula's DEFAULT-NUMBER
    assert whatsapp._resolve_number_route(PNID) == (None, None)


@pytest.mark.asyncio
async def test_a_business_with_no_number_row_keeps_the_setup_fallback(db):
    assert await _real_creds("brand-new") == {"token": "server-system-token", "phone_id": "DEFAULT-NUMBER"}


def test_forget_clears_every_route_of_that_business():
    whatsapp._ROUTE_CACHE.clear()
    whatsapp._ROUTE_CACHE.update({"a": (0, ("oth", "commerce")), "b": (0, ("digg", "knowledge"))})
    whatsapp._wa_creds_cache["oth"] = {"token": "t", "phone_id": "a"}
    whatsapp._forget_wa_number("oth")
    assert "a" not in whatsapp._ROUTE_CACHE and "b" in whatsapp._ROUTE_CACHE
    assert "oth" not in whatsapp._wa_creds_cache


# ── number health ────────────────────────────────────────────────────────────

def test_health_summary_in_plain_words():
    out = whatsapp_connect.health_summary({
        "display_phone_number": "+27 71 312 8891", "verified_name": "Off the Hook",
        "quality_rating": "GREEN", "messaging_limit_tier": "TIER_1K", "name_status": "APPROVED",
        "platform_type": "CLOUD_API", "status": "CONNECTED"})
    rows = {r["label"]: (r["value"], r["tone"]) for r in out["rows"]}
    assert rows == {"Quality": ("Green — good", "ok"), "Daily limit": ("1,000 new customers a day", "ok"),
                    "Display name": ("Approved", "ok"), "Sending": ("Registered to send", "ok")}
    bad = {r["label"]: r for r in whatsapp_connect.health_summary({
        "quality_rating": "RED", "name_status": "DECLINED", "platform_type": "NOT_APPLICABLE"})["rows"]}
    assert bad["Quality"]["tone"] == "danger" and "Declined" in bad["Display name"]["value"]
    assert bad["Sending"]["value"].startswith("Not registered") and "Daily limit" not in bad
    assert whatsapp_connect.health_summary({})["rows"] == []                  # missing fields left out


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data, self.is_success = status, data, 200 <= status < 300

    def json(self):
        return self._data


@pytest.mark.asyncio
async def test_health_endpoint_reads_meta_once_and_never_returns_the_token(db, monkeypatch):
    calls = []

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, params=None, headers=None):
            calls.append((url, headers))
            return _Resp(200, {"quality_rating": "YELLOW", "messaging_limit_tier": "TIER_250",
                               "platform_type": "CLOUD_API", "display_phone_number": "+27 71 312 8891"})

    monkeypatch.setattr(whatsapp_connect.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(whatsapp, "_get_tenant_wa_creds", _real_creds)
    out = await whatsapp_connect.whatsapp_health("off-the-hook")
    again = await whatsapp_connect.whatsapp_health("off-the-hook")
    assert out is again and len(calls) == 1 and calls[0][0].endswith(PNID)
    assert out["connected"] and out["rows"][0]["label"] == "Quality" and out["rows"][0]["tone"] == "warn"
    assert "server-system-token" not in str(out)


@pytest.mark.asyncio
async def test_health_of_a_disconnected_number_reads_nothing(db, monkeypatch):
    db.tables["vula_whatsapp_accounts"][0]["status"] = "disconnected"
    monkeypatch.setattr(whatsapp, "_get_tenant_wa_creds", _real_creds)
    out = await whatsapp_connect.whatsapp_health("off-the-hook")
    assert out == {"tenant_id": "off-the-hook", "connected": False, "rows": []}


def test_health_route_is_tenant_guarded():
    from vula.api import server
    assert any(r.match("/v1/whatsapp/health/off-the-hook") for r in server._TENANT_GUARD_RES)


# ── disconnect removes Vula's webhook subscription (31 Oct) ─────────────────
# Disconnect used to null the token and keep the row, but Vula's app stayed subscribed to the
# business's WhatsApp account, so Meta kept sending its webhooks. Now a best-effort
# DELETE {graph}/{waba}/subscribed_apps with the number's own token, only when no other connected
# number is on that account. Never fails the disconnect.

class _MetaClient:
    calls: list = []
    fail: object = None

    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def delete(self, url, headers=None):
        _MetaClient.calls.append((url, headers))
        if isinstance(_MetaClient.fail, Exception):
            raise _MetaClient.fail
        return _Resp(_MetaClient.fail or 200, {"success": True})


@pytest.fixture()
def meta(monkeypatch):
    _MetaClient.calls, _MetaClient.fail = [], None
    monkeypatch.setattr(whatsapp_connect.httpx, "AsyncClient", _MetaClient)
    return _MetaClient


def _with_waba(db, **extra):
    row = db.tables["vula_whatsapp_accounts"][0]
    row.update({"waba_id": "WABA-1", "access_token": "business-token", **extra})
    return row


@pytest.mark.asyncio
async def test_disconnect_unsubscribes_vula_from_the_whatsapp_account(db, meta):
    row = _with_waba(db)
    res = await whatsapp_connect.disconnect_whatsapp("off-the-hook")
    assert res["status"] == "disconnected"
    from vula.meta_graph import graph_url
    assert meta.calls == [(graph_url("WABA-1/subscribed_apps"), {"Authorization": "Bearer business-token"})]
    assert row["access_token"] is None and row["status"] == "disconnected"     # row kept, token cleared


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [500, RuntimeError("Meta down")])
async def test_a_failed_unsubscribe_never_fails_the_disconnect(db, meta, fail):
    row = _with_waba(db)
    meta.fail = fail
    res = await whatsapp_connect.disconnect_whatsapp("off-the-hook")
    assert res["status"] == "disconnected" and row["access_token"] is None and len(meta.calls) == 1


@pytest.mark.asyncio
async def test_an_account_with_another_connected_number_stays_subscribed(db, meta):
    _with_waba(db)
    db.tables["vula_whatsapp_accounts"].append({
        "tenant_id": "off-the-hook", "phone_number_id": "OTHER", "waba_id": "WABA-1",
        "access_token": "business-token", "status": "connected", "route_mode": "commerce"})
    res = await whatsapp_connect.disconnect_whatsapp("off-the-hook", phone_number_id=PNID)
    assert res["status"] == "connected" and meta.calls == []


@pytest.mark.asyncio
async def test_no_token_or_vulas_own_account_is_never_unsubscribed(db, meta, monkeypatch):
    _with_waba(db, access_token=None)
    await whatsapp_connect.disconnect_whatsapp("off-the-hook")
    assert meta.calls == []
    row = _with_waba(db, status="connected")
    monkeypatch.setattr(settings, "platform_waba_id", "WABA-1")
    await whatsapp_connect.disconnect_whatsapp("off-the-hook")
    assert meta.calls == [] and row["status"] == "disconnected"
