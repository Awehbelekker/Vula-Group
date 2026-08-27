"""Test for the WhatsApp document redelivery guard (2026-08-27, migration 143's companion
fix): confirmed live that a genuine redelivery of the SAME document (identical bytes) was
fully reprocessed — vision scan, KB ingestion, a confusing reply — because Vula's own
auto-generated filename bakes in a processing-time timestamp, so nothing recognised it as the
same file already handled moments ago. _handle_document_ingest now checks content_hash against
recently-filed documents BEFORE any expensive work starts."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.whatsapp import _handle_document_ingest

TID = "digg-demo"
PHONE = "27645755210"


@pytest.mark.asyncio
async def test_recent_duplicate_content_skips_all_processing(tmp_path):
    local_file = tmp_path / "Proof of Payment 20260827-1116.pdf"
    local_file.write_bytes(b"same real pdf bytes")

    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .gte.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{"id": "already-filed-row"}])

    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=mock_client),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
    ):
        await _handle_document_ingest(PHONE, "media123", "Proof of Payment.pdf",
                                      "application/pdf", route_tenant_id=TID)

    mock_pipeline_cls.assert_not_called()  # no KB ingestion attempted
    replies = [c.args[1] for c in mock_reply.call_args_list]
    assert any("Already processed" in r for r in replies)


@pytest.mark.asyncio
async def test_no_recent_duplicate_proceeds_to_ingest(tmp_path):
    local_file = tmp_path / "Proof of Payment 20260827-1116.pdf"
    local_file.write_bytes(b"genuinely new pdf bytes")

    mock_client = MagicMock()
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .gte.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

    mock_pipeline = MagicMock()
    mock_pipeline.ingest_file = AsyncMock(return_value=MagicMock(
        status="failed", error="stop here — proves ingest was reached", filename="x.pdf"))

    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=mock_client),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        await _handle_document_ingest(PHONE, "media123", "Proof of Payment.pdf",
                                      "application/pdf", route_tenant_id=TID)

    mock_pipeline.ingest_file.assert_called_once()
    replies = [c.args[1] for c in mock_reply.call_args_list]
    assert not any("Already processed" in r for r in replies)


@pytest.mark.asyncio
async def test_dedup_check_failure_never_blocks_real_processing(tmp_path):
    """Best-effort — a DB error on the guard check must degrade to 'proceed as normal', never
    silently drop a genuine document."""
    local_file = tmp_path / "doc.pdf"
    local_file.write_bytes(b"bytes")

    mock_client = MagicMock()
    mock_client.table.side_effect = RuntimeError("db down")

    mock_pipeline = MagicMock()
    mock_pipeline.ingest_file = AsyncMock(return_value=MagicMock(
        status="failed", error="reached", filename="doc.pdf"))

    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()),
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=mock_client),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        await _handle_document_ingest(PHONE, "media123", "doc.pdf",
                                      "application/pdf", route_tenant_id=TID)

    mock_pipeline.ingest_file.assert_called_once()
