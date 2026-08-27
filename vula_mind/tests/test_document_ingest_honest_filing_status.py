"""Tests for _handle_document_ingest's 2026-08-27 honesty fix: the WhatsApp reply used to say
"✅ Filed" unconditionally, regardless of whether _file_uploaded_document actually persisted a
row — confirmed live on a real gerflor billboard photo where filing failed outright (Postgres
error 42P10, migration 143's unique index was never actually created) yet the reply still
claimed success. filed_row only has a real "id" when the write actually persisted."""
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


ANALYSIS = {"category": "General Document", "summary": "A project billboard.",
           "fields": {"project_name": "Additions & Alterations"}}


@pytest.mark.asyncio
async def test_successful_filing_says_filed(tmp_path):
    local_file = tmp_path / "billboard.jpg"
    local_file.write_bytes(b"fake jpg bytes")

    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=_mock_client_no_dup()),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch("vula.api.whatsapp._analyze_document", new=AsyncMock(return_value=ANALYSIS)),
        patch("vula.api.whatsapp._file_uploaded_document",
              new=AsyncMock(return_value=("", {"id": "real-row-id"}))),
    ):
        mock_pipeline_cls.return_value.ingest_file = AsyncMock(
            return_value=MagicMock(status="success", filename="billboard.jpg", doc_id="d1",
                                   chunks_stored=1))
        await _handle_document_ingest(PHONE, "media123", "billboard.jpg", "image/jpeg", route_tenant_id=TID)

    replies = [c.args[1] for c in mock_reply.call_args_list]
    assert any(r.startswith("✅ Filed") for r in replies)
    assert not any("couldn't file" in r for r in replies)


@pytest.mark.asyncio
async def test_failed_filing_says_read_not_filed(tmp_path):
    """The exact real incident: extraction succeeds (real content, real summary) but the
    durable filing write fails — the reply must say so honestly, not claim success."""
    local_file = tmp_path / "billboard.jpg"
    local_file.write_bytes(b"fake jpg bytes")

    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=_mock_client_no_dup()),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch("vula.api.whatsapp._analyze_document", new=AsyncMock(return_value=ANALYSIS)),
        patch("vula.api.whatsapp._file_uploaded_document", new=AsyncMock(return_value=("", None))),
    ):
        mock_pipeline_cls.return_value.ingest_file = AsyncMock(
            return_value=MagicMock(status="success", filename="billboard.jpg", doc_id="d1",
                                   chunks_stored=1))
        await _handle_document_ingest(PHONE, "media123", "billboard.jpg", "image/jpeg", route_tenant_id=TID)

    replies = [c.args[1] for c in mock_reply.call_args_list]
    assert not any(r.startswith("✅ Filed") for r in replies)
    assert any(r.startswith("📖 Read") for r in replies)
    assert any("couldn't file it just now" in r for r in replies)
    # the real content that WAS extracted must still reach the rep — a filing failure
    # shouldn't also hide the summary they'd otherwise have gotten
    assert any("project billboard" in r for r in replies)


@pytest.mark.asyncio
async def test_failed_filing_row_without_id_also_treated_as_not_filed(tmp_path):
    """doc_filing.py's own comment confirms file_document() can return a row dict even on total
    failure (no real id inside) — filed_ok must check for a real id, not just truthiness."""
    local_file = tmp_path / "billboard.jpg"
    local_file.write_bytes(b"fake jpg bytes")

    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=_mock_client_no_dup()),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch("vula.api.whatsapp._analyze_document", new=AsyncMock(return_value=ANALYSIS)),
        patch("vula.api.whatsapp._file_uploaded_document",
              new=AsyncMock(return_value=("", {"filename": "billboard.jpg"}))),
    ):
        mock_pipeline_cls.return_value.ingest_file = AsyncMock(
            return_value=MagicMock(status="success", filename="billboard.jpg", doc_id="d1",
                                   chunks_stored=1))
        await _handle_document_ingest(PHONE, "media123", "billboard.jpg", "image/jpeg", route_tenant_id=TID)

    replies = [c.args[1] for c in mock_reply.call_args_list]
    assert not any(r.startswith("✅ Filed") for r in replies)
    assert any(r.startswith("📖 Read") for r in replies)
