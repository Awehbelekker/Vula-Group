"""Master › Health: Vula's WhatsApp login-code template on every WhatsApp account (2026-10-25).

The login code (vula/api/wa_login.py) only reaches someone outside their 24-hour window as Meta's
authentication template. Templates are shared by name across a WhatsApp Business Account, so it's
submitted once per account — alone, never with a business's other templates — and an account that
already has it is left alone."""
from types import SimpleNamespace

import pytest

from vula.commerce import wa_templates as wt

ROWS = [{"tenant_id": "off-the-hook", "waba_id": "1111118607"}, {"tenant_id": "digg-demo", "waba_id": "1111118607"},
        {"tenant_id": "awake-sa", "waba_id": "2222220042"}, {"tenant_id": "off-the-hook-roland", "waba_id": None}]


class _Q:
    def select(self, *_a): return self
    def eq(self, *_a): return self
    def execute(self): return SimpleNamespace(data=ROWS)


@pytest.fixture
def meta(monkeypatch):
    state = {"status": {"1111118607": "missing", "2222220042": "APPROVED"}, "created": [], "refuse": None}
    monkeypatch.setattr(wt, "_client", lambda: SimpleNamespace(table=lambda name: _Q()))

    async def creds(tid):
        waba = next(r["waba_id"] for r in ROWS if r["tenant_id"] == tid)
        return {"waba_id": waba, "token": "t"} if waba else None

    async def status(c):
        return state["status"][c["waba_id"]]

    async def create(tid, c, payload):
        if state["refuse"]:
            return {"error": state["refuse"]}
        state["created"].append((tid, c["waba_id"], payload["name"], payload["category"]))
        return {"name": payload["name"], "status": "PENDING"}
    monkeypatch.setattr(wt, "_waba_creds", creds)
    monkeypatch.setattr(wt, "login_template_status", status)
    monkeypatch.setattr(wt, "_create_raw", create)
    return state


@pytest.mark.asyncio
async def test_each_account_shows_its_businesses_and_meta_status(meta):
    res = await wt.login_template_accounts()
    assert res["accounts"] == [{"account": "…0042", "tenants": ["awake-sa"], "status": "APPROVED"},
                               {"account": "…8607", "tenants": ["digg-demo", "off-the-hook"], "status": "missing"}]
    assert res["no_account"] == ["off-the-hook-roland"]


@pytest.mark.asyncio
async def test_only_the_login_template_goes_and_only_where_it_is_missing(meta):
    res = await wt.submit_login_template()
    assert meta["created"] == [("digg-demo", "1111118607", "vula_login_code", "AUTHENTICATION")]
    assert res["submitted"] == [{"account": "…8607", "status": "PENDING"}]
    assert res["already_there"] == [{"account": "…0042", "status": "APPROVED"}]
    meta["status"]["1111118607"] = "PENDING"                 # a second tap never resubmits
    meta["created"].clear()
    again = await wt.submit_login_template()
    assert meta["created"] == [] and not again["submitted"]


@pytest.mark.asyncio
async def test_meta_refusing_is_reported_not_claimed(meta):
    meta["refuse"] = "Invalid parameter"
    res = await wt.submit_login_template()
    assert res["submitted"] == [] and res["failed"] == [{"account": "…8607", "error": "Invalid parameter"}]


@pytest.mark.asyncio
async def test_an_account_meta_cant_read_is_not_submitted_blind(meta):
    meta["status"]["1111118607"] = None
    res = await wt.submit_login_template()
    assert meta["created"] == [] and res["failed"][0]["account"] == "…8607"


def test_the_master_routes_are_master_only():
    from vula.api import master
    paths = {(r.path, tuple(sorted(r.methods))) for r in master.router.routes}
    assert ("/wa-login-template", ("GET",)) in paths and ("/wa-login-template", ("POST",)) in paths
    assert any(d.dependency.__name__ == "require_master" for d in master.router.dependencies)


@pytest.mark.asyncio
@pytest.mark.parametrize("ok,data,want", [
    (True, [{"name": "vula_login_code", "status": "APPROVED"}], "APPROVED"),
    (True, [], "missing"),
    (False, [], None),
])
async def test_reading_metas_status(monkeypatch, ok, data, want):
    seen = {}

    class Client:
        def __init__(self, **_k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False

        async def get(self, url, headers=None, params=None):
            seen.update(url=url, params=params)
            return SimpleNamespace(is_success=ok, json=lambda: {"data": data})
    monkeypatch.setattr(wt.httpx, "AsyncClient", Client)
    assert await wt.login_template_status({"waba_id": "1111118607", "token": "t"}) == want
    assert seen["url"].endswith("/1111118607/message_templates") and seen["params"]["name"] == "vula_login_code"
