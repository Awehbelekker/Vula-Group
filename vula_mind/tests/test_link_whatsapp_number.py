"""A number added straight to Vula's own WhatsApp Business Account (2026-10-13, SeedleMe).

Embedded Signup stalled, so 071 312 8891 was added in WhatsApp Manager on Vula's account (the one
Off the Hook, DIGG and Gerflor are on) — phone number ID 1387582947771270. Such a number has no
token of its own. Before this, a tenant row without a token fell back to the server's DEFAULT
phone number, so SeedleMe's customers would have been answered from Vula's own line, and the
templates button had no credentials at all."""
from types import SimpleNamespace

import pytest

from config import settings
from vula.api import whatsapp, whatsapp_connect
from vula.commerce import wa_templates

# conftest stubs the lookup for every test (no test may reach Meta) — these tests are about it.
_real_creds = whatsapp._get_tenant_wa_creds

PNID = "1387582947771270"
WABA = "1343999077678607"


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.op, self.payload, self.conflict = db, table, [], "select", None, None

    def select(self, *_a, **_k): return self
    def limit(self, *_a): return self
    def maybe_single(self): self.single = True; return self

    def eq(self, k, v):
        self.f.append((k, v))
        return self

    def upsert(self, payload, on_conflict=None):
        self.op, self.payload, self.conflict = "upsert", payload, on_conflict
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            for r in rows:
                if r.get(self.conflict) == self.payload.get(self.conflict):
                    r.update(self.payload)
                    break
            else:
                rows.append(dict(self.payload))
            return SimpleNamespace(data=[self.payload])
        hit = [r for r in rows if all(r.get(k) == v for k, v in self.f)]
        if getattr(self, "single", False):
            return SimpleNamespace(data=hit[0] if hit else None)
        return SimpleNamespace(data=hit)


class _DB:
    def __init__(self, rows):
        self.tables = {"vula_whatsapp_accounts": rows}

    def table(self, name):
        return _Q(self, name)


@pytest.fixture()
def accounts(monkeypatch):
    db = _DB([{"tenant_id": "gerflor", "waba_id": WABA, "phone_number_id": "1356665274189058",
               "access_token": "enc", "status": "connected"}])
    monkeypatch.setattr(settings, "whatsapp_token", "server-system-token")
    monkeypatch.setattr(settings, "whatsapp_phone_id", "DEFAULT-NUMBER")
    monkeypatch.setattr("supabase.create_client", lambda *a, **k: db)
    monkeypatch.setattr(whatsapp_connect, "_supabase", lambda: db)
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)
    whatsapp._wa_creds_cache.clear()
    whatsapp._ROUTE_CACHE.clear()
    return db


@pytest.mark.asyncio
async def test_a_number_without_its_own_token_replies_from_its_own_number(accounts):
    accounts.tables["vula_whatsapp_accounts"].append(
        {"tenant_id": "seedleme", "waba_id": WABA, "phone_number_id": PNID, "access_token": None,
         "status": "connected"})
    creds = await _real_creds("seedleme")
    assert creds == {"token": "server-system-token", "phone_id": PNID}     # never DEFAULT-NUMBER
    assert await wa_templates._waba_creds("seedleme") == {"waba_id": WABA, "token": "server-system-token"}


@pytest.mark.asyncio
async def test_a_business_with_no_number_still_falls_back_as_before(accounts):
    creds = await _real_creds("nobody")
    assert creds == {"token": "server-system-token", "phone_id": "DEFAULT-NUMBER"}
    assert await wa_templates._waba_creds("nobody") is None


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.is_success = 200 <= status < 300
        self.content = b"x"
        self.text = str(data)

    def json(self):
        return self._data


def _meta(monkeypatch, numbers, register_ok=True):
    calls = []

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, headers=None, params=None):
            calls.append(("GET", url, headers))
            return _Resp(200, {"data": numbers}) if url.endswith(f"{WABA}/phone_numbers") else _Resp(404, {})

        async def post(self, url, headers=None, json=None):
            calls.append(("POST", url, json))
            return _Resp(200 if register_ok else 400, {"success": True} if register_ok
                         else {"error": {"message": "PIN mismatch"}})

    monkeypatch.setattr(whatsapp_connect.httpx, "AsyncClient", _Client)
    monkeypatch.setattr("vula.api.tenants.get_config",
                        lambda tid, fresh=False: {"modules": ["products", "orders"]})
    return calls


SEEDLEME_NUMBER = {"id": PNID, "display_phone_number": "+27 71 312 8891", "verified_name": "SeedleMe",
                   "status": "PENDING", "name_status": "PENDING_REVIEW", "code_verification_status": "VERIFIED"}


@pytest.mark.asyncio
async def test_linking_checks_meta_registers_and_routes_to_the_business(accounts, monkeypatch):
    calls = _meta(monkeypatch, [SEEDLEME_NUMBER])
    whatsapp._wa_creds_cache["seedleme"] = {"token": "stale", "phone_id": "DEFAULT-NUMBER"}
    res = await whatsapp_connect.link_existing_number("seedleme", PNID, pin="123456", linked_by="ian@x")
    assert res["waba_id"] == WABA and res["phone_number"] == "+27 71 312 8891"
    assert res["registered_now"] is True and res["route_mode"] == "commerce"
    reg = next(c for c in calls if c[0] == "POST")
    assert reg[1].endswith(f"{PNID}/register") and reg[2]["pin"] == "123456"
    row = next(r for r in accounts.tables["vula_whatsapp_accounts"] if r["tenant_id"] == "seedleme")
    assert row["phone_number_id"] == PNID and row["access_token"] is None and row["status"] == "connected"
    assert "pin" not in row and "123456" not in str(row)                     # the PIN is never stored
    assert "seedleme" not in whatsapp._wa_creds_cache                         # stale creds forgotten
    assert whatsapp._resolve_number_route(PNID) == ("seedleme", "commerce")


@pytest.mark.asyncio
async def test_an_already_registered_number_is_not_registered_again(accounts, monkeypatch):
    calls = _meta(monkeypatch, [{**SEEDLEME_NUMBER, "status": "CONNECTED"}])
    res = await whatsapp_connect.link_existing_number("seedleme", PNID, pin="123456")
    assert res["registered_now"] is None and not any(c[0] == "POST" for c in calls)


@pytest.mark.asyncio
async def test_a_number_not_on_vulas_account_is_refused(accounts, monkeypatch):
    _meta(monkeypatch, [])
    with pytest.raises(whatsapp_connect.LinkNumberError, match="isn't on Vula's WhatsApp account"):
        await whatsapp_connect.link_existing_number("seedleme", PNID)
    assert not any(r["tenant_id"] == "seedleme" for r in accounts.tables["vula_whatsapp_accounts"])


@pytest.mark.asyncio
async def test_another_businesss_number_is_refused(accounts, monkeypatch):
    _meta(monkeypatch, [SEEDLEME_NUMBER])
    with pytest.raises(whatsapp_connect.LinkNumberError, match="already linked to 'gerflor'"):
        await whatsapp_connect.link_existing_number("seedleme", "1356665274189058")


@pytest.mark.asyncio
async def test_meta_refusing_the_pin_says_so_and_links_nothing(accounts, monkeypatch):
    _meta(monkeypatch, [SEEDLEME_NUMBER], register_ok=False)
    with pytest.raises(whatsapp_connect.LinkNumberError, match="PIN mismatch"):
        await whatsapp_connect.link_existing_number("seedleme", PNID, pin="000000")
    assert not any(r["tenant_id"] == "seedleme" for r in accounts.tables["vula_whatsapp_accounts"])
