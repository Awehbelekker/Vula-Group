"""A shop's own address, set up from Settings (2026-10-13, Ian: "can we streamline domain
setup"): type it, Vula adds it to Railway itself and shows the two DNS records to copy; links
switch over by themselves once it works."""
from unittest.mock import AsyncMock

import pytest

from vula.commerce import shop_domain as sd


def test_only_a_shop_subdomain_of_the_businesss_own_domain():
    assert sd.normalise("https://Shop.SeedleMe.com/") == "shop.seedleme.com"
    for bad, why in (("seedleme.com", "starts with shop."), ("shop seedleme", "doesn't look like"),
                     ("shop.vula-group-production.up.railway.app", "own business")):
        with pytest.raises(sd.DomainError, match=why):
            sd.normalise(bad)


def test_the_address_is_suggested_from_the_store(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, **k: {"store_url": "https://www.seedleme.com"})
    assert sd.suggestion("seedleme") == "shop.seedleme.com"
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, **k: {"store_url": "https://x.myshopify.com"})
    assert sd.suggestion("seedleme") == ""


def test_railways_answer_becomes_two_records_to_copy():
    rd = {"_target": "abc123.up.railway.app", "status": {
        "verificationToken": "railway-verify=9f3c", "certificateStatus": "PENDING",
        "dnsRecords": [{"hostlabel": "shop", "requiredValue": "abc123.up.railway.app", "status": "PENDING"}]}}
    assert sd.records("shop.seedleme.com", rd) == [
        {"type": "CNAME", "name": "shop", "value": "abc123.up.railway.app", "ok": False},
        {"type": "TXT", "name": "_railway-verify.shop", "value": "railway-verify=9f3c", "ok": None}]
    text = sd.instructions("shop.seedleme.com", sd.records("shop.seedleme.com", rd))
    assert "seedleme.com" in text and "CNAME  name: shop  value: abc123.up.railway.app" in text


@pytest.fixture()
def railway(monkeypatch):
    calls, saved = [], []
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "s")
    from config import settings
    monkeypatch.setattr(settings, "railway_api_token", "tok")

    async def gql(query, variables):
        calls.append((query.split("(")[0].split()[-1], variables))
        if "query domains" in query:
            return {"domains": {"serviceDomains": [{"domain": "abc123.up.railway.app", "targetPort": 8080}],
                                "customDomains": [{"id": "d1", "domain": d} for d in saved]}}
        return {}
    monkeypatch.setattr(sd, "_gql", gql)
    monkeypatch.setattr(sd, "_save", lambda tid, d: saved.append(d) if d else saved.clear())
    monkeypatch.setattr("vula.api.tenants.tenant_for_host", lambda h: None)
    monkeypatch.setattr(sd, "status", AsyncMock(return_value={"state": "waiting_dns"}))
    return calls, saved


@pytest.mark.asyncio
async def test_adding_the_address_puts_it_on_railway_and_the_shop(railway):
    calls, saved = railway
    assert await sd.connect("seedleme", "shop.seedleme.com") == {"state": "waiting_dns"}
    assert calls[1] == ("customDomainCreate", {"input": {"projectId": "p", "environmentId": "e", "serviceId": "s",
                                                         "domain": "shop.seedleme.com", "targetPort": 8080}})
    assert saved == ["shop.seedleme.com"]
    calls.clear()
    await sd.connect("seedleme", "shop.seedleme.com")                 # again: not added twice
    assert [c[0] for c in calls] == ["domains"]


@pytest.mark.asyncio
async def test_without_a_railway_token_the_address_is_still_saved(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "railway_api_token", "")
    saved = []
    monkeypatch.setattr(sd, "_save", lambda tid, d: saved.append(d))
    monkeypatch.setattr("vula.api.tenants.tenant_for_host", lambda h: None)
    monkeypatch.setattr(sd, "status", AsyncMock(return_value={"state": "waiting_dns", "automated": False}))
    assert (await sd.connect("seedleme", "shop.seedleme.com"))["automated"] is False
    assert saved == ["shop.seedleme.com"]


@pytest.mark.asyncio
async def test_another_businesss_address_is_refused(monkeypatch):
    monkeypatch.setattr("vula.api.tenants.tenant_for_host", lambda h: "off-the-hook")
    with pytest.raises(sd.DomainError, match="another business"):
        await sd.connect("seedleme", "shop.offthehook.co.za")


@pytest.mark.asyncio
async def test_status_reads_live_when_the_address_answers(monkeypatch):
    from vula.commerce import web_basket
    monkeypatch.setattr(web_basket, "configured_shop_host", lambda tid: "shop.seedleme.com")
    monkeypatch.setattr(web_basket, "check_shop_host", AsyncMock(return_value=True))
    monkeypatch.setattr(sd, "suggestion", lambda tid: "")
    st = await sd.status("seedleme")
    assert (st["domain"], st["state"]) == ("shop.seedleme.com", "live")


def test_only_the_owner_or_a_manager_can_change_it(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vula.api import shop_domain as api
    monkeypatch.setattr("vula.api.commerce._may_apply_stock", AsyncMock(return_value=False))
    app = FastAPI()
    app.include_router(api.router, prefix="/v1/commerce")
    r = TestClient(app).post("/v1/commerce/seedleme/admin/shop-domain", json={"domain": "shop.seedleme.com"})
    assert r.status_code == 403
