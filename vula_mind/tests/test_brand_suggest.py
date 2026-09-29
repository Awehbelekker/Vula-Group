"""A brand kit suggested from the business's own logo or website (2026-09-29).

DIGG uploaded its logo but its brand kit stayed on Vula's default green; Off the Hook picked a pale
teal (#60D0DE) that white button text can't sit on. Colours are sampled from the real image."""
import pytest
from PIL import Image, ImageDraw

from vula.commerce import brand_suggest as bs


def _logo(fg, bg=(255, 255, 255, 0), second=None):
    img = Image.new("RGBA", (200, 80), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 130, 70], fill=fg)
    if second:
        d.rectangle([140, 10, 190, 70], fill=second)
    return img


def test_a_monochrome_logo_gives_its_own_dark_brand():
    out = bs.suggest_from_image(_logo((30, 30, 30, 255)))            # DIGG's charcoal wordmark
    assert out["accent_color"] == out["ink_color"]
    assert bs.contrast(out["accent_color"], "#FFFFFF") > 10
    assert "monochrome" in out["why"]


def test_a_pale_brand_colour_is_darkened_until_buttons_read():
    out = bs.suggest_from_image(_logo((96, 208, 222, 255), second=(14, 124, 123, 255)))
    assert bs.contrast(out["accent_color"], "#FFFFFF") >= 3.0         # white text on the button
    assert out["secondary_color"] and out["palette"][0].startswith("#")


def test_navy_logo_keeps_its_navy():
    out = bs.suggest_from_image(_logo((26, 41, 94, 255)))             # Gerflor navy
    r, g, b = bs._rgb(out["accent_color"])
    assert b > r and b > g                                              # still blue, not snapped to a preset


def test_a_websites_theme_colour_and_logo_are_found():
    html = ('<html><head><meta name="theme-color" content="#0E7C7B">'
            '<link rel="icon" href="/favicon.png"></head>'
            '<body><img class="site-logo" src="/img/logo.png"></body></html>')
    urls, theme = bs._find_logo_urls(html, "https://offthehook.co.za/")
    assert theme == "#0E7C7B"
    assert urls[0] == "https://offthehook.co.za/img/logo.png" and "https://offthehook.co.za/favicon.png" in urls


@pytest.mark.asyncio
async def test_nothing_usable_is_an_empty_suggestion(monkeypatch):
    async def boom(url):
        raise RuntimeError("unreachable")
    monkeypatch.setattr("vula.commerce.reference_url.safe_fetch_bytes", boom)
    monkeypatch.setattr("vula.commerce.reference_url.safe_fetch_html", boom)
    assert await bs.suggest(logo_url="https://x/logo.png") == {}


# ── the brand reaches every surface ──────────────────────────────────────────

def test_a_businesss_invoice_email_wears_its_brand_not_vulas():
    from vula.api.email import _brand_parts
    header, footer, accent = _brand_parts({"name": "Gerflor Cape Town", "accent_color": "#1a295e",
                                            "tagline": "Flooring that lasts"}, "gerflor")
    assert accent == "#1a295e" and "background:#1a295e" in header
    assert "Gerflor Cape Town" in footer and "Flooring that lasts" in header
    assert "Vula Group" not in header and "Your AI is ready" not in header
    h2, _, a2 = _brand_parts({"name": "<script>x</script>", "accent_color": "red"}, "x")
    assert a2 == "#2C5545" and "<script>" not in h2                   # escaped, bad colour ignored


def test_excel_headers_use_the_brand_colour_when_white_text_reads():
    import io
    import openpyxl
    from vula.commerce.xlsx import render_invoices_xlsx
    rows = [{"date": "2026-09-01", "ref": "INV-1", "party": "SPM", "is_refund": False,
             "total_cents": 48500, "vat_cents": 0, "lines": []}]
    navy = openpyxl.load_workbook(io.BytesIO(render_invoices_xlsx(rows, "SPM", accent="#1a295e")))
    assert navy.worksheets[1]["A1"].fill.fgColor.rgb == "FF1A295E"
    pale = openpyxl.load_workbook(io.BytesIO(render_invoices_xlsx(rows, "SPM", accent="#60d0de")))
    assert pale.worksheets[1]["A1"].fill.fgColor.rgb == "FF2C5545"   # too pale for white text → Vula green


@pytest.mark.asyncio
async def test_the_phone_app_icon_is_a_png_even_without_a_logo():
    from PIL import Image
    import io
    from vula.commerce.brand_icon import render_icon
    png = await render_icon(None, "#1a295e", "Gerflor", 192)
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.size == (192, 192)
    assert img.getpixel((5, 5))[:3] == (0x1A, 0x29, 0x5E)             # its brand colour tile


def test_a_dashboard_address_finds_its_business(monkeypatch):
    from vula.api import tenants

    class Q:
        def select(self, *a): return self
        def limit(self, *a): return self
        def execute(self):
            class R: data = [
                {"tenant_id": "off-the-hook", "domains": [], "store_url": "https://offthehook.co.za"},
                {"tenant_id": "digg-demo", "domains": ["admin.digg-ct.co.za"], "store_url": None},
                {"tenant_id": "gerflor", "domains": [], "store_url": None}]
            return R()

    class DB:
        def table(self, name): return Q()
    monkeypatch.setattr(tenants, "_client", lambda: DB())
    assert tenants.tenant_for_host("admin.offthehook.co.za") == "off-the-hook"
    assert tenants.tenant_for_host("offthehook.co.za") is None          # the shop itself, not the admin
    assert tenants.tenant_for_host("admin.digg-ct.co.za") == "digg-demo"
    assert tenants.tenant_for_host("digg.vula-ai.com") == "digg-demo"
    assert tenants.tenant_for_host("gerflor.vula-ai.com") == "gerflor"
    assert tenants.tenant_for_host("vula-ai.com") is None
