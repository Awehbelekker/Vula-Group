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


# ── long-press shortcuts and the round icon (2026-10-27) ─────────────────────

@pytest.mark.asyncio
async def test_one_app_per_business_with_separate_plain_and_round_icons():
    m = await _manifest("https://admin.offthehook.co.za")
    assert m["id"] == m["start_url"]
    purposes = {(i["sizes"], i["purpose"]): i["src"] for i in m["icons"]}
    assert set(purposes) == {("192x192", "any"), ("512x512", "any"), ("192x192", "maskable"), ("512x512", "maskable")}
    assert purposes[("512x512", "maskable")].endswith("/app-icon/maskable/512.png")
    assert not any("any maskable" == i["purpose"] for i in m["icons"])


@pytest.mark.asyncio
async def test_long_press_opens_the_scanner_the_assistant_and_the_inbox(monkeypatch):
    m = await _manifest("https://admin.offthehook.co.za")
    urls = {s["short_name"]: s["url"] for s in m["shortcuts"]}
    assert urls == {"Scan": "https://admin.offthehook.co.za/?launch=scan",
                    "Ask Vula": "https://admin.offthehook.co.za/?launch=chat",
                    "Inbox": "https://admin.offthehook.co.za/#/go/inbox"}
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid, fresh=False: {"modules": ["invoices"]})
    m = await _manifest("https://admin.offthehook.co.za")
    assert m["shortcuts"][-1]["url"] == "https://admin.offthehook.co.za/#/go/invoices"   # only when it has invoices


def _logo(w=400, h=100):
    import io
    from PIL import Image
    img = Image.new("RGBA", (w, h), (200, 20, 20, 255))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("square", [False, True])
async def test_the_round_icon_keeps_the_whole_logo_inside_androids_crop(monkeypatch, square):
    import io
    from PIL import Image
    from vula.commerce import brand_icon

    async def fetch(url):
        return _logo(300, 300) if square else _logo()
    monkeypatch.setattr("vula.commerce.reference_url.safe_fetch_bytes", fetch)
    png = await brand_icon.render_icon("https://x/logo.png", "#0077b6", "Off the Hook", 192,
                                       square=square, maskable=True)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    c, r = 96, 192 * 0.4                                     # the 80%-wide circle Android keeps
    is_red = lambda p: p[0] > 150 and p[1] < 80             # noqa: E731
    if square:                                               # shrunk onto its own edge colour, not white
        assert is_red(img.getpixel((2, 2)))
    else:
        red = [(x, y) for x in range(192) for y in range(192) if is_red(img.getpixel((x, y)))]
        assert red and all((x - c) ** 2 + (y - c) ** 2 <= r ** 2 for x, y in red)


# ── the iPhone's launch screen (2026-10-27) ───────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("dark", [False, True])
async def test_the_launch_screen_is_the_businesss_icon_and_name_on_the_apps_own_colour(monkeypatch, dark):
    import io
    from PIL import Image
    from vula.commerce import brand_icon

    async def settings(tid):
        return {"trading_as": "Off the Hook", "accent_color": "#0077b6"}
    monkeypatch.setattr(commerce.service, "get_invoice_settings", settings)
    res = await commerce.public_app_splash("off-the-hook", "1179x2556", dark=dark)
    img = Image.open(io.BytesIO(res.body)).convert("RGB")
    assert img.size == (1179, 2556) and res.media_type == "image/png"
    bg, ink = brand_icon.SPLASH_LOOK[dark]
    assert img.getpixel((5, 5)) == bg                                    # the dashboard's page colour
    assert img.getpixel((1179 // 2, int(2556 * 0.42))) == (0, 119, 182)  # the icon, in the middle
    name_row = [img.getpixel((x, y)) for y in range(1300, 1500) for x in range(300, 880)]
    assert ink in name_row or any(sum(abs(a - b) for a, b in zip(p, ink)) < 60 for p in name_row)   # the name below


@pytest.mark.asyncio
@pytest.mark.parametrize("size", ["4000x4000", "1179x2557", "big", "1179"])
async def test_only_iphone_screen_sizes_are_drawn(size):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        await commerce.public_app_splash("off-the-hook", size)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_a_long_name_is_shortened_to_fit():
    from vula.commerce.brand_icon import _fit
    from PIL import ImageFont
    try:
        ImageFont.truetype("DejaVuSans-Bold.ttf", 10)
    except OSError:
        pytest.skip("DejaVu isn't installed here (the production image has it)")
    font_for = lambda s: ImageFont.truetype("DejaVuSans-Bold.ttf", s)       # noqa: E731
    label, font = _fit("Digg Architecture and Interiors Proprietary Limited Cape Town", font_for, 600, 70)
    assert label.endswith("…") and font.getlength(label) <= 600
    assert _fit("DIGG", font_for, 600, 70)[0] == "DIGG"


def test_the_dashboard_asks_for_exactly_the_sizes_drawn():
    """vula_dashboard/src/lib/iosSplash.js links one image per iPhone; each must be drawable here."""
    import re
    from pathlib import Path
    from vula.commerce.brand_icon import SPLASH_SIZES
    js = (Path(__file__).resolve().parents[2] / "vula_dashboard/src/lib/iosSplash.js").read_text()
    phones = re.findall(r"\[(\d+), (\d+), (\d)\]", js)
    assert {(int(w) * int(r), int(h) * int(r)) for w, h, r in phones} == SPLASH_SIZES
