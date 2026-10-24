"""The installed app's manifest and the phone's Share sheet (2026-10-24).

The business-branded manifest is served from the API's address, so the dashboard passes its own
(?app=). Only Vula's dashboard hosts are taken: they become the app's start page and the target
photos / PDFs shared from the phone go to (vula_dashboard/public/sw.js)."""
import json

import pytest

from vula.api import commerce


@pytest.fixture(autouse=True)
def brand(monkeypatch):
    async def fake(tid):
        return {"name": "Off the Hook", "accent_color": "#0077b6"}
    monkeypatch.setattr(commerce, "public_brand", fake)
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, fresh=False: {"domains": []})


async def _manifest(app=None):
    return json.loads((await commerce.public_manifest("off-the-hook", app)).body)


@pytest.mark.asyncio
async def test_the_businesss_dashboard_gets_a_share_target_on_its_own_address():
    m = await _manifest("https://admin.offthehook.co.za")
    assert m["start_url"] == m["scope"] == "https://admin.offthehook.co.za/"
    assert m["share_target"]["action"] == "https://admin.offthehook.co.za/share-target"
    assert m["share_target"]["method"] == "POST" and m["share_target"]["enctype"] == "multipart/form-data"
    assert "image/*" in m["share_target"]["params"]["files"][0]["accept"]


@pytest.mark.asyncio
async def test_a_vula_ai_address_is_trusted_too():
    m = await _manifest("https://seedleme.vula-ai.com")
    assert m["share_target"]["action"] == "https://seedleme.vula-ai.com/share-target"


@pytest.mark.asyncio
@pytest.mark.parametrize("app", [None, "https://evil.example.com", "http://admin.offthehook.co.za",
                                 "https://admin.offthehook.co.za/phish", "javascript:alert(1)"])
async def test_any_other_address_is_ignored(app):
    m = await _manifest(app)
    assert m["start_url"] == "/" and "share_target" not in m
    assert m["name"] == "Off the Hook"
