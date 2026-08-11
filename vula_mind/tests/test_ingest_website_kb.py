"""Tests for scripts/ingest_website_kb.py — the scrape-to-KB glue the ingestion pipeline
and web scraper never had (they were completely disconnected before this)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from scripts.ingest_website_kb import ingest_website


@pytest.mark.asyncio
async def test_ingest_website_happy_path():
    fake_result = SimpleNamespace(status="success", chunks_stored=4, error=None)
    with (
        patch("vula.skills.web_scraper.WebFetcher.fetch_markdown",
              new=AsyncMock(return_value=("Gerflor Home", "Some **markdown** content"))),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline.ingest_text",
              new=AsyncMock(return_value=fake_result)),
    ):
        results = await ingest_website("gerflor", ["https://www.gerflor.co.za/"])

    assert results == [{
        "url": "https://www.gerflor.co.za/", "title": "Gerflor Home",
        "status": "success", "chunks_stored": 4, "error": None,
    }]


@pytest.mark.asyncio
async def test_ingest_website_passes_title_and_markdown_into_content():
    fake_result = SimpleNamespace(status="success", chunks_stored=1, error=None)
    ingest_mock = AsyncMock(return_value=fake_result)
    with (
        patch("vula.skills.web_scraper.WebFetcher.fetch_markdown",
              new=AsyncMock(return_value=("Products", "Vinyl flooring range"))),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline.ingest_text", new=ingest_mock),
    ):
        await ingest_website("gerflor", ["https://www.gerflor.co.za/products"])

    call = ingest_mock.call_args
    content = call.args[0]
    assert "# Products" in content
    assert "Vinyl flooring range" in content
    assert call.kwargs["filename"] == "https://www.gerflor.co.za/products"
    assert call.kwargs["source_type"] == "website"


@pytest.mark.asyncio
async def test_ingest_website_skips_empty_page_without_calling_ingest():
    ingest_mock = AsyncMock()
    with (
        patch("vula.skills.web_scraper.WebFetcher.fetch_markdown",
              new=AsyncMock(return_value=("", "   "))),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline.ingest_text", new=ingest_mock),
    ):
        results = await ingest_website("gerflor", ["https://www.gerflor.co.za/blank"])

    assert results == [{"url": "https://www.gerflor.co.za/blank", "status": "empty"}]
    ingest_mock.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_website_records_fetch_failure_and_continues_other_urls():
    fake_result = SimpleNamespace(status="success", chunks_stored=2, error=None)
    with (
        patch("vula.skills.web_scraper.WebFetcher.fetch_markdown",
              new=AsyncMock(side_effect=[Exception("timeout"), ("OK", "content")])),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline.ingest_text",
              new=AsyncMock(return_value=fake_result)),
        patch("scripts.ingest_website_kb.asyncio.sleep", new=AsyncMock()),
    ):
        results = await ingest_website("gerflor", ["https://bad.example/", "https://good.example/"])

    assert results[0]["status"] == "fetch_failed"
    assert "timeout" in results[0]["error"]
    assert results[1]["status"] == "success"
