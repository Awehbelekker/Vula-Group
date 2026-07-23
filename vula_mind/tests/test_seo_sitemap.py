"""Tests for sitemap.xml/robots.txt (vula/api/commerce.py:page_sitemap/page_robots) — part
of the SEO-depth gap closed from the marketing capability audit ("no sitemap.xml, no
robots.txt... anywhere"). Scoped to tenants with a real public domain configured
(settings.store_urls) — a hash-routed Vula URL isn't a shape search engines should index,
so tenants without one get a valid-but-empty sitemap rather than a misleading one."""
from unittest.mock import MagicMock, patch

import pytest

from vula.api.commerce import page_robots, page_sitemap


@pytest.mark.asyncio
async def test_sitemap_lists_published_pages_under_the_real_domain():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.order.return_value \
        .execute.return_value = MagicMock(data=[
            {"slug": "home", "updated_at": "2026-07-20T10:00:00Z"},
            {"slug": "specials", "updated_at": "2026-07-21T10:00:00Z"},
        ])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with (
        patch("vula.api.commerce.service._client", return_value=mock_db),
        patch("vula.api.tenants.store_url", return_value="https://offthehook.co.za"),
    ):
        resp = await page_sitemap("off-the-hook")

    body = resp.body.decode()
    assert "<loc>https://offthehook.co.za/p/home</loc>" in body
    assert "<loc>https://offthehook.co.za/p/specials</loc>" in body
    assert "<lastmod>2026-07-20</lastmod>" in body
    assert resp.media_type == "application/xml"


@pytest.mark.asyncio
async def test_sitemap_returns_valid_empty_xml_when_no_domain_configured():
    with patch("vula.api.tenants.store_url", return_value=None):
        resp = await page_sitemap("digg-demo")

    body = resp.body.decode()
    assert "<urlset" in body and "<url>" not in body


@pytest.mark.asyncio
async def test_sitemap_degrades_gracefully_on_db_error():
    mock_db = MagicMock()
    mock_db.table.side_effect = RuntimeError("column does not exist")

    with (
        patch("vula.api.commerce.service._client", return_value=mock_db),
        patch("vula.api.tenants.store_url", return_value="https://offthehook.co.za"),
    ):
        resp = await page_sitemap("off-the-hook")

    body = resp.body.decode()
    assert "<urlset" in body and "<url>" not in body


@pytest.mark.asyncio
async def test_robots_points_at_the_real_domain_sitemap():
    with patch("vula.api.tenants.store_url", return_value="https://offthehook.co.za"):
        resp = await page_robots("off-the-hook")
    body = resp.body.decode()
    assert "Allow: /" in body
    assert "Sitemap: https://offthehook.co.za/sitemap.xml" in body


@pytest.mark.asyncio
async def test_robots_omits_sitemap_line_without_a_domain():
    with patch("vula.api.tenants.store_url", return_value=None):
        resp = await page_robots("digg-demo")
    body = resp.body.decode()
    assert "Allow: /" in body
    assert "Sitemap:" not in body
