"""Tests for the page-builder/storefront-header backend changes (2026-08-12): public pages
ordered by sort_order (so the list can double as a nav menu), and the public /brand endpoint
surfacing the new header layout fields."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.commerce import list_pages, public_brand


@pytest.mark.asyncio
async def test_list_pages_orders_by_sort_order_then_updated_at():
    mock_table = MagicMock()
    mock_query = mock_table.select.return_value.eq.return_value.eq.return_value.order.return_value.order.return_value
    mock_query.execute.return_value = MagicMock(data=[{"slug": "home", "title": "Home"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.api.commerce.service._client", return_value=mock_db):
        result = await list_pages("gerflor")

    assert result["pages"] == [{"slug": "home", "title": "Home"}]
    order_calls = [c.args for c in mock_table.select.return_value.eq.return_value.eq.return_value.order.call_args_list]
    assert order_calls[0] == ("sort_order",)


@pytest.mark.asyncio
async def test_public_brand_includes_header_layout_fields():
    settings = {
        "trading_as": "Gerflor Cape Town", "logo_url": "https://x/logo.png",
        "accent_color": "#123456", "ink_color": "#000000", "font_pairing": "vula",
        "logo_align": "center", "logo_size": "lg",
        "header_sticky": False, "header_nav_position": "below-logo",
        "header_cta_text": "Get a quote", "header_cta_link": "#contact",
        "company_phone": "27821234567",
    }
    with patch("vula.api.commerce.service.get_invoice_settings", new=AsyncMock(return_value=settings)):
        result = await public_brand("gerflor")

    assert result["logo_align"] == "center"
    assert result["logo_size"] == "lg"
    assert result["header_sticky"] is False
    assert result["header_nav_position"] == "below-logo"
    assert result["header_cta_text"] == "Get a quote"
    assert result["header_cta_link"] == "#contact"
    assert result["whatsapp"] == "27821234567"


@pytest.mark.asyncio
async def test_public_brand_defaults_when_settings_missing():
    with patch("vula.api.commerce.service.get_invoice_settings", new=AsyncMock(return_value=None)):
        result = await public_brand("gerflor")

    assert result["logo_align"] == "left"
    assert result["logo_size"] == "md"
    assert result["header_sticky"] is True
    assert result["header_nav_position"] == "right"
    assert result["header_cta_text"] is None
    assert result["whatsapp"] is None
