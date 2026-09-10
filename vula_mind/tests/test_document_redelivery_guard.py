"""One inbound file → one ack → one run.

2026-08-27: a genuine redelivery of the SAME document (identical bytes, but Vula's
auto-generated filename bakes in a timestamp) was fully reprocessed.
2026-09-10 (DIGG): one "Payment Notification.pdf" produced EIGHT "Got it" acks and eight full
analyses — the old guard was a plain READ, so every near-simultaneous handler passed it before
any had filed. Now: an atomic claim on the file's content hash (vula_media_dedup, migration
157) BEFORE the ack, using Meta's webhook sha256 when present.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.whatsapp import _claim_media_once, _handle_document_ingest

TID = "digg-demo"
PHONE = "27645755210"


def _dedup_client(insert_raises=None, existing_created_at=None):
    """A Supabase-client double whose vula_media_dedup insert can raise a unique-violation and
    whose select returns an existing claim row."""
    c = MagicMock()
    tbl = c.table.return_value
    if insert_raises:
        tbl.insert.return_value.execute.side_effect = insert_raises
    else:
        tbl.insert.return_value.execute.return_value = MagicMock(data=[{}])
    row = [{"created_at": existing_created_at}] if existing_created_at else []
    tbl.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=row)
    tbl.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])
    return c


@pytest.mark.asyncio
async def test_claim_wins_on_first_insert():
    with patch("vula.commerce.service._client", return_value=_dedup_client()):
        assert await _claim_media_once(TID, "sha-abc", "document") is True


@pytest.mark.asyncio
async def test_claim_lost_for_a_recent_duplicate():
    from datetime import datetime, timezone
    recent = datetime.now(timezone.utc).isoformat()
    client = _dedup_client(insert_raises=Exception("duplicate key value violates unique constraint (23505)"),
                           existing_created_at=recent)
    with patch("vula.commerce.service._client", return_value=client):
        assert await _claim_media_once(TID, "sha-abc", "document") is False


@pytest.mark.asyncio
async def test_claim_refreshed_and_reprocesses_a_stale_resend():
    client = _dedup_client(insert_raises=Exception("23505 duplicate"),
                           existing_created_at="2020-01-01T00:00:00+00:00")
    with patch("vula.commerce.service._client", return_value=client):
        assert await _claim_media_once(TID, "sha-abc", "document") is True


@pytest.mark.asyncio
async def test_claim_fails_open_on_db_error():
    client = MagicMock()
    client.table.side_effect = RuntimeError("db down")
    with patch("vula.commerce.service._client", return_value=client):
        assert await _claim_media_once(TID, "sha-abc", "document") is True


@pytest.mark.asyncio
async def test_duplicate_delivery_sends_no_ack_and_does_no_work(tmp_path):
    """The losing handler must return before the ack — no "Got it", no download, no ingest."""
    from datetime import datetime, timezone
    client = _dedup_client(insert_raises=Exception("23505"),
                           existing_created_at=datetime.now(timezone.utc).isoformat())
    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock()) as dl,
        patch("vula.commerce.service._client", return_value=client),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as pipe,
    ):
        await _handle_document_ingest(PHONE, "media123", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID, content_sha="sha-xyz")
    reply.assert_not_called()
    dl.assert_not_called()
    pipe.assert_not_called()


@pytest.mark.asyncio
async def test_first_delivery_with_sha_acks_and_proceeds(tmp_path):
    local_file = tmp_path / "Payment Notification.pdf"
    local_file.write_bytes(b"real pdf bytes")
    client = _dedup_client()  # insert wins
    # the secondary filed-doc guard sees nothing
    (client.table.return_value.select.return_value.eq.return_value.eq.return_value
     .gte.return_value.limit.return_value.execute.return_value) = MagicMock(data=[])
    pipe = MagicMock()
    pipe.ingest_file = AsyncMock(return_value=MagicMock(status="failed", error="reached", filename="x.pdf"))
    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=client),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipe),
    ):
        await _handle_document_ingest(PHONE, "media123", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID, content_sha="sha-xyz")
    assert any("Got it" in c.args[1] for c in reply.call_args_list)
    pipe.ingest_file.assert_called_once()


@pytest.mark.asyncio
async def test_dedup_failure_never_blocks_real_processing(tmp_path):
    local_file = tmp_path / "doc.pdf"
    local_file.write_bytes(b"bytes")
    client = MagicMock()
    client.table.side_effect = RuntimeError("db down")
    pipe = MagicMock()
    pipe.ingest_file = AsyncMock(return_value=MagicMock(status="failed", error="reached", filename="doc.pdf"))
    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()),
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.commerce.service._client", return_value=client),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipe),
    ):
        await _handle_document_ingest(PHONE, "media123", "doc.pdf", "application/pdf",
                                      route_tenant_id=TID, content_sha="sha-xyz")
    pipe.ingest_file.assert_called_once()
