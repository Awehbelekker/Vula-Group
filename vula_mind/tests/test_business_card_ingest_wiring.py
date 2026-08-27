"""Integration test: _handle_document_ingest upgrades a Business Card's OCR-derived fields
with a direct vision scan, and researches phone/email when genuinely missing from the card."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.whatsapp import _handle_document_ingest

TID = "digg-demo"
PHONE = "27645755210"


def _mock_client_no_dup():
    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .gte.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    return mock_client


@pytest.mark.asyncio
async def test_vision_scan_fills_in_phone_ocr_missed(tmp_path):
    local_file = tmp_path / "card.jpg"
    local_file.write_bytes(b"fake jpg bytes")

    ocr_analysis = {"category": "Business Card", "summary": "Business card for Jane Smith.",
                    "fields": {"name": "Jane Smith", "company": "ABC Developers",
                               "title": None, "phone": None, "email": "jane@abc.co.za"}}

    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=_mock_client_no_dup()),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch("vula.api.whatsapp._analyze_document", new=AsyncMock(return_value=ocr_analysis)),
        patch("vula.api.whatsapp._scan_business_card_photo",
              new=AsyncMock(return_value={"name": "Jane Smith", "company": "ABC Developers",
                                          "title": "Director", "phone": "+27825551234", "email": None})),
        patch("vula.api.whatsapp._research_missing_contact_details", new=AsyncMock(return_value={})),
        patch("vula.api.whatsapp._file_uploaded_document", new=AsyncMock(return_value=("", None))),
    ):
        mock_pipeline_cls.return_value.ingest_file = AsyncMock(
            return_value=MagicMock(status="success", filename="card.jpg", doc_id="d1"))
        await _handle_document_ingest(PHONE, "media123", "card.jpg", "image/jpeg", route_tenant_id=TID)

    replies = [c.args[1] for c in mock_reply.call_args_list]
    assert any("Title" in r or "Director" in r for r in replies)
    # Vision found a phone the OCR pass missed — the reply must reflect it, not stay blank.
    assert any("825551234" in r or "27825551234" in r for r in replies)


@pytest.mark.asyncio
async def test_research_fallback_used_when_vision_also_finds_nothing(tmp_path):
    local_file = tmp_path / "card.jpg"
    local_file.write_bytes(b"fake jpg bytes")

    ocr_analysis = {"category": "Business Card", "summary": "Business card for Jane Smith.",
                    "fields": {"name": "Jane Smith", "company": "ABC Developers",
                               "title": None, "phone": None, "email": None}}

    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=_mock_client_no_dup()),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch("vula.api.whatsapp._analyze_document", new=AsyncMock(return_value=ocr_analysis)),
        patch("vula.api.whatsapp._scan_business_card_photo", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._research_missing_contact_details",
              new=AsyncMock(return_value={"phone": "0215551234", "email": None})) as mock_research,
        patch("vula.api.whatsapp._file_uploaded_document", new=AsyncMock(return_value=("", None))),
    ):
        mock_pipeline_cls.return_value.ingest_file = AsyncMock(
            return_value=MagicMock(status="success", filename="card.jpg", doc_id="d1"))
        await _handle_document_ingest(PHONE, "media123", "card.jpg", "image/jpeg", route_tenant_id=TID)

    mock_research.assert_called_once_with("Jane Smith", "ABC Developers")
    replies = [c.args[1] for c in mock_reply.call_args_list]
    assert any("web search" in r for r in replies)


@pytest.mark.asyncio
async def test_non_business_card_never_triggers_vision_scan(tmp_path):
    local_file = tmp_path / "invoice.pdf"
    local_file.write_bytes(b"fake pdf bytes")

    ocr_analysis = {"category": "Invoice", "summary": "An invoice.",
                    "fields": {"supplier": "ACME", "total_cents": 10000}}

    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()),
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=_mock_client_no_dup()),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch("vula.api.whatsapp._analyze_document", new=AsyncMock(return_value=ocr_analysis)),
        patch("vula.api.whatsapp._scan_business_card_photo", new=AsyncMock()) as mock_scan,
        patch("vula.api.whatsapp._file_uploaded_document", new=AsyncMock(return_value=("", None))),
    ):
        mock_pipeline_cls.return_value.ingest_file = AsyncMock(
            return_value=MagicMock(status="success", filename="invoice.pdf", doc_id="d1"))
        await _handle_document_ingest(PHONE, "media123", "invoice.pdf", "application/pdf", route_tenant_id=TID)

    mock_scan.assert_not_called()
