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


@pytest.mark.asyncio
async def test_a_pdf_pop_is_offered_against_the_supplier_bill(tmp_path):
    """2026-10-06: the FNB payment notification for STE Scaffolding's R2,397.17 was only filed —
    the POP matcher ran for photos, never PDFs, so the bill stayed owed."""
    local_file = tmp_path / "Payment Notification (16).pdf"
    local_file.write_bytes(b"%PDF fake")
    pop = {"category": "Proof of Payment", "summary": "Payment notification: R2,397.17 to Ste.",
           "fields": {"amount_cents": 239717, "payee_name": "Ste", "reference": "Digg",
                      "date": "2026-10-05"}}
    stage = MagicMock(return_value="📸 Got the payment confirmation — R2,397.17 to *Ste*. That looks "
                                   "like bill *DIG-BILL-00092*. Reply *yes* to mark it paid.")
    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=_mock_client_no_dup()),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch("vula.api.whatsapp._analyze_document", new=AsyncMock(return_value=pop)),
        patch("vula.api.whatsapp._file_uploaded_document",
              new=AsyncMock(return_value=("📂 Which project is this for?", {"id": "row"}))),
        patch("vula.commerce.bank_rec.stage_pop_for_review", stage),
    ):
        mock_pipeline_cls.return_value.ingest_file = AsyncMock(
            return_value=MagicMock(status="success", filename="Payment Notification (16).pdf",
                                   doc_id="d1", chunks_stored=1))
        await _handle_document_ingest(PHONE, "m1", "Payment Notification (16).pdf", "application/pdf",
                                      route_tenant_id=TID)
    stage.assert_called_once_with(TID, 239717, "2026-10-05", "Digg", "Ste", sender_phone=PHONE)
    final = mock_reply.call_args_list[-1].args[1]
    assert final.startswith("📸") and "Reply *yes* to mark it paid" in final
    assert "Which project" not in final            # one question at a time
