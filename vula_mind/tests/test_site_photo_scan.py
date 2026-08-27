"""Tests for the site/building-photo vision scan + web-search address fallback (2026-08-27),
fixing a real gap: a building-exterior photo had no dedicated category and fell into "General
Document" with whatever the OCR-text pass guessed — the same weakness already fixed for receipts
and business cards, since a street-sign address is exactly the small/angled/distant text an
OCR-text pipeline is unreliable on."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from vula.api.whatsapp import _research_missing_address, _scan_site_photo


def _mock_llm_response(content: str):
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    return AsyncMock(return_value=resp)


# ── _scan_site_photo ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_site_photo_returns_all_fields(tmp_path):
    img = tmp_path / "site.jpg"
    img.write_bytes(b"fake jpeg bytes")
    raw = '{"address": "12 Main Road, Sea Point", "business_name": "Solid Cape", "notes": "under construction"}'
    with (
        patch("core.llm_router.resolve_cloud_vision_route", return_value=("model", "key", "base")),
        patch("litellm.acompletion", new=_mock_llm_response(raw)),
    ):
        result = await _scan_site_photo(str(img))
    assert result["address"] == "12 Main Road, Sea Point"
    assert result["business_name"] == "Solid Cape"
    assert result["notes"] == "under construction"


@pytest.mark.asyncio
async def test_scan_site_photo_missing_address_stays_null(tmp_path):
    img = tmp_path / "site.jpg"
    img.write_bytes(b"fake jpeg bytes")
    raw = '{"address": null, "business_name": "Solid Cape", "notes": null}'
    with (
        patch("core.llm_router.resolve_cloud_vision_route", return_value=("model", "key", "base")),
        patch("litellm.acompletion", new=_mock_llm_response(raw)),
    ):
        result = await _scan_site_photo(str(img))
    assert result["address"] is None
    assert result["business_name"] == "Solid Cape"


@pytest.mark.asyncio
async def test_scan_site_photo_nonexistent_file_returns_none():
    result = await _scan_site_photo("/no/such/file.jpg")
    assert result is None


@pytest.mark.asyncio
async def test_scan_site_photo_no_vision_route_returns_none(tmp_path):
    img = tmp_path / "site.jpg"
    img.write_bytes(b"fake jpeg bytes")
    with patch("core.llm_router.resolve_cloud_vision_route", return_value=None):
        result = await _scan_site_photo(str(img))
    assert result is None


@pytest.mark.asyncio
async def test_scan_site_photo_malformed_json_returns_none(tmp_path):
    img = tmp_path / "site.jpg"
    img.write_bytes(b"fake jpeg bytes")
    with (
        patch("core.llm_router.resolve_cloud_vision_route", return_value=("model", "key", "base")),
        patch("litellm.acompletion", new=_mock_llm_response("not json at all")),
    ):
        result = await _scan_site_photo(str(img))
    assert result is None


# ── _research_missing_address ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_finds_address_from_web_results():
    with (
        patch("core.skills.web_search._ddg_search", new=AsyncMock(return_value=[
            {"url": "https://solidcape.co.za/contact", "title": "Contact"}])),
        patch("core.skills.web_search._fetch_text", new=AsyncMock(return_value="Visit us at 12 Main Road, Sea Point")),
        patch("core.llm_router.resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=_mock_llm_response('{"address": "12 Main Road, Sea Point"}')),
    ):
        found = await _research_missing_address("Solid Cape")
    assert found["address"] == "12 Main Road, Sea Point"


@pytest.mark.asyncio
async def test_research_no_business_name_returns_empty():
    found = await _research_missing_address("")
    assert found == {}


@pytest.mark.asyncio
async def test_research_no_search_results_returns_empty():
    with patch("core.skills.web_search._ddg_search", new=AsyncMock(return_value=[])):
        found = await _research_missing_address("Solid Cape")
    assert found == {}


@pytest.mark.asyncio
async def test_research_never_raises_on_failure():
    with patch("core.skills.web_search._ddg_search", new=AsyncMock(side_effect=RuntimeError("boom"))):
        found = await _research_missing_address("Solid Cape")
    assert found == {}


@pytest.mark.asyncio
async def test_research_never_invents_when_llm_returns_null():
    with (
        patch("core.skills.web_search._ddg_search", new=AsyncMock(return_value=[
            {"url": "https://example.com", "title": "About"}])),
        patch("core.skills.web_search._fetch_text", new=AsyncMock(return_value="We build things.")),
        patch("core.llm_router.resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=_mock_llm_response('{"address": null}')),
    ):
        found = await _research_missing_address("Solid Cape")
    assert found.get("address") is None
