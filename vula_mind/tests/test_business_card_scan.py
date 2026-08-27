"""Tests for the business-card vision scan + web-search research fallback (2026-08-27), fixing
a real report: business card photos went through OCR-text extraction, which reliably read the
email (a distinct @-pattern OCRs cleanly) but consistently missed the phone number (small
print, varied formatting, icons). Mirrors _scan_financial_photo's proven fix for receipts."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from vula.api.whatsapp import _research_missing_contact_details, _scan_business_card_photo

TID = "gerflor"


def _mock_llm_response(content: str):
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    return AsyncMock(return_value=resp)


# ── _scan_business_card_photo ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_business_card_returns_all_fields(tmp_path):
    img = tmp_path / "card.jpg"
    img.write_bytes(b"fake jpeg bytes")
    raw = '{"name": "Jane Smith", "company": "ABC Developers", "title": "Director", "phone": "+27 82 555 1234", "email": "jane@abc.co.za"}'
    with (
        patch("core.llm_router.resolve_cloud_vision_route", return_value=("model", "key", "base")),
        patch("litellm.acompletion", new=_mock_llm_response(raw)),
    ):
        result = await _scan_business_card_photo(str(img))
    assert result["name"] == "Jane Smith"
    assert result["phone"] == "+27 82 555 1234"
    assert result["email"] == "jane@abc.co.za"


@pytest.mark.asyncio
async def test_scan_business_card_missing_phone_stays_null(tmp_path):
    img = tmp_path / "card.jpg"
    img.write_bytes(b"fake jpeg bytes")
    raw = '{"name": "Jane Smith", "company": null, "title": null, "phone": null, "email": "jane@abc.co.za"}'
    with (
        patch("core.llm_router.resolve_cloud_vision_route", return_value=("model", "key", "base")),
        patch("litellm.acompletion", new=_mock_llm_response(raw)),
    ):
        result = await _scan_business_card_photo(str(img))
    assert result["phone"] is None
    assert result["email"] == "jane@abc.co.za"


@pytest.mark.asyncio
async def test_scan_business_card_nonexistent_file_returns_none():
    result = await _scan_business_card_photo("/no/such/file.jpg")
    assert result is None


@pytest.mark.asyncio
async def test_scan_business_card_no_vision_route_returns_none(tmp_path):
    img = tmp_path / "card.jpg"
    img.write_bytes(b"fake jpeg bytes")
    with patch("core.llm_router.resolve_cloud_vision_route", return_value=None):
        result = await _scan_business_card_photo(str(img))
    assert result is None


@pytest.mark.asyncio
async def test_scan_business_card_malformed_json_returns_none(tmp_path):
    img = tmp_path / "card.jpg"
    img.write_bytes(b"fake jpeg bytes")
    with (
        patch("core.llm_router.resolve_cloud_vision_route", return_value=("model", "key", "base")),
        patch("litellm.acompletion", new=_mock_llm_response("not json at all")),
    ):
        result = await _scan_business_card_photo(str(img))
    assert result is None


# ── _research_missing_contact_details ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_finds_phone_from_web_results():
    with (
        patch("core.skills.web_search._ddg_search", new=AsyncMock(return_value=[
            {"url": "https://bomax.co.za/contact", "title": "Contact"}])),
        patch("core.skills.web_search._fetch_text", new=AsyncMock(return_value="Call us on 021 555 1234")),
        patch("core.llm_router.resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=_mock_llm_response('{"phone": "021 555 1234", "email": null}')),
    ):
        found = await _research_missing_contact_details("Jane Smith", "Bomax Architect")
    assert found["phone"] == "021 555 1234"


@pytest.mark.asyncio
async def test_research_no_name_or_company_returns_empty():
    found = await _research_missing_contact_details("", "")
    assert found == {}


@pytest.mark.asyncio
async def test_research_no_search_results_returns_empty():
    with patch("core.skills.web_search._ddg_search", new=AsyncMock(return_value=[])):
        found = await _research_missing_contact_details("Jane Smith", "Bomax Architect")
    assert found == {}


@pytest.mark.asyncio
async def test_research_never_raises_on_failure():
    with patch("core.skills.web_search._ddg_search", new=AsyncMock(side_effect=RuntimeError("boom"))):
        found = await _research_missing_contact_details("Jane Smith", "Bomax Architect")
    assert found == {}


@pytest.mark.asyncio
async def test_research_never_invents_when_llm_returns_nulls():
    with (
        patch("core.skills.web_search._ddg_search", new=AsyncMock(return_value=[
            {"url": "https://example.com", "title": "About"}])),
        patch("core.skills.web_search._fetch_text", new=AsyncMock(return_value="We are a design studio.")),
        patch("core.llm_router.resolve_generation_route", new=AsyncMock(return_value=("model", "key", "base"))),
        patch("litellm.acompletion", new=_mock_llm_response('{"phone": null, "email": null}')),
    ):
        found = await _research_missing_contact_details("Jane Smith", "Some Studio")
    assert found.get("phone") is None
    assert found.get("email") is None
