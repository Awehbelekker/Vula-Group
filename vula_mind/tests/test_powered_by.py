"""The "Powered by Vula" credit on what a tenant's customers see (logo set, batch 2).

The pill at the foot of the shop page and the pay page, the inline badge at the foot of invoice
and quote PDFs. Vula is only ever this small credit: the tenant's name and logo stay on top.
"""
import base64
import re

import pytest

from vula.commerce import pay_page, powered_by


def test_the_pill_is_the_logo_sets_artwork_with_its_colours_as_classes():
    html = powered_by.pill_html()
    assert html.startswith('<footer class="vpb"><a href="https://vula-ai.com"')
    assert 'aria-label="Powered by Vula"' in html and 'aria-hidden="true"' in html
    assert re.findall(r'class="vpb-(\w+)"', html) == ["bg", "txt", "mark", "dot", "word"]
    assert "fill=" not in html and "<title>" not in html


def test_dark_mode_swaps_to_the_dark_pills_colours_only_where_asked():
    assert "prefers-color-scheme:dark" in powered_by.pill_css()
    assert "#1B1E1B" in powered_by.pill_css() and "#5FBF95" in powered_by.pill_css()
    assert "prefers-color-scheme" not in powered_by.pill_css(dark_mode=False)


def test_the_pdf_badge_is_the_inline_colour_svg():
    uri = powered_by.inline_badge_data_uri()
    assert uri.startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(uri.split(",", 1)[1]).decode()
    assert "<title>Powered by Vula</title>" in svg and "#1F5A45" in svg


def test_the_pay_page_ends_with_the_pill_below_the_tenants_card():
    inv = {"invoice_number": "INV-1", "status": "sent", "total_cents": 1000, "total_paid_cents": 0}
    html = pay_page.render({"name": "Off the Hook", "accent_color": "#0077b6"}, inv, None, None)
    assert html.index("Off the Hook") < html.index('<footer class="vpb">')
    assert html.rstrip().endswith("</footer></body></html>")
    assert "prefers-color-scheme" not in html  # the pay page is light only


@pytest.mark.asyncio
async def test_the_shop_page_ends_with_the_pill(monkeypatch):
    from vula.api import menu_page
    from vula.commerce import service, shop_stats, web_basket

    async def products(tid, category=None):
        return []

    async def inv(tid):
        return {"trading_as": "Off the Hook"}

    monkeypatch.setattr(web_basket, "shop_products", products)
    monkeypatch.setattr(service, "get_invoice_settings", inv)
    monkeypatch.setattr(web_basket, "wa_number", lambda tid: "")
    monkeypatch.setattr(shop_stats, "record", lambda *a, **k: None)
    html = (await menu_page.photo_menu("off-the-hook", None)).body.decode()
    assert "{{PB" not in html
    assert html.index('<main id="grid">') < html.index('<footer class="vpb">') < html.index('id="bar"')
    assert "prefers-color-scheme:dark" in html


def test_invoice_and_quote_pdfs_carry_the_badge_at_the_foot(monkeypatch):
    pytest.importorskip("weasyprint")
    from vula.commerce import pdf
    seen = []

    class FakeHTML:
        def __init__(self, string):
            seen.append(string)

        def write_pdf(self):
            return b"%PDF"

    monkeypatch.setattr("weasyprint.HTML", FakeHTML)
    inv = {"tenant_id": "off-the-hook", "doc_type": "invoice", "invoice_number": "INV-1",
           "total_cents": 1000, "line_items": []}
    pdf.render_invoice_pdf(inv)
    pdf.render_invoice_pdf({**inv, "doc_type": "quote"})
    for html in seen:
        foot = html[html.index('<div class="footer">'):]
        assert 'alt="Powered by Vula"' in foot and "width:110px" in foot
        assert powered_by.inline_badge_data_uri() in foot
