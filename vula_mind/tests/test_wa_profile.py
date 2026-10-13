"""The shop's WhatsApp business profile from its brand kit (vula/commerce/wa_profile.py)."""
import httpx
import pytest

import vula.api.whatsapp as wa
from vula.commerce import service, wa_profile, wa_templates, web_basket

TID = "seedleme"
INV = {"trading_as": "SeedleMe", "tagline": "Handmade concrete basins, made in Cape Town.",
       "company_email": "seedleme@gmail.com", "registered_address": "12 Example Rd, Cape Town",
       "logo_url": "https://x/logo.png", "icon_url": "https://x/icon.png"}


@pytest.fixture
def env(monkeypatch):
    out = {"posts": [], "profile": {"about": "Hello"}}

    async def inv(tid):
        return dict(INV)

    async def resolve(tid):
        return {"phone_id": "1387582947771270", "token": "t"}

    async def handle(url, token):
        return "4::aGFuZGxl", None

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            return httpx.Response(200, json={"data": [out["profile"]]}, request=httpx.Request("GET", url))

        async def post(self, url, json=None, headers=None):
            out["posts"].append((url, json))
            out["profile"] = {k: v for k, v in json.items() if k not in ("messaging_product", "profile_picture_handle")}
            return httpx.Response(200, json={"success": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(service, "get_invoice_settings", inv)
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, fresh=False: {"business_type": "retail",
                                                                                 "description": "Concrete basins and sinks."})
    monkeypatch.setattr("vula.api.tenants.store_url", lambda tid: "https://seedleme.com")
    monkeypatch.setattr(web_basket, "shop_enabled", lambda tid, products=None: True)
    monkeypatch.setattr(web_basket, "shop_url", lambda tid, anchor="": "https://shop.seedleme.com/")
    monkeypatch.setattr(wa, "_resolve_wa", resolve)
    monkeypatch.setattr(wa_templates, "_upload_media_handle", handle)
    monkeypatch.setattr(wa_profile.httpx, "AsyncClient", Client)
    return out


@pytest.mark.asyncio
async def test_proposal_comes_from_the_brand_kit(env):
    p = await wa_profile.proposed(TID)
    assert p["about"] == "Handmade concrete basins, made in Cape Town."
    assert p["description"] == "Concrete basins and sinks."
    assert p["websites"] == ["https://seedleme.com", "https://shop.seedleme.com/"]
    assert p["vertical"] == "RETAIL" and p["picture_url"] == "https://x/icon.png"
    assert p["email"] == "seedleme@gmail.com"


def test_fields_respect_metas_limits():
    assert len(wa_profile._cut("x" * 200, 139)) == 139


@pytest.mark.asyncio
async def test_apply_sets_the_profile_and_reads_it_back(env):
    res = await wa_profile.apply(TID, await wa_profile.proposed(TID))
    url, body = env["posts"][0]
    assert url.endswith("/1387582947771270/whatsapp_business_profile")
    assert body["profile_picture_handle"] == "4::aGFuZGxl" and body["vertical"] == "RETAIL"
    assert res["applied"] and res["profile"]["about"] == "Handmade concrete basins, made in Cape Town."


@pytest.mark.asyncio
async def test_blank_fields_and_bad_websites_are_left_alone(env):
    await wa_profile.apply(TID, {"about": "Hi", "email": "", "websites": ["seedleme.com"]})
    body = env["posts"][0][1]
    assert body == {"messaging_product": "whatsapp", "about": "Hi"}
