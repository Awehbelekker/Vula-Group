"""One inbound file → one ack → one run.

2026-08-27: a genuine redelivery of the SAME document (identical bytes, but Vula's
auto-generated filename bakes in a timestamp) was fully reprocessed.
2026-09-10 (DIGG): a single "Payment Notification.pdf" arrived as SEVEN distinct WhatsApp
messages (7 wamids) in a 5-second burst and every one got a full ack + vision-scan + analysis.
msg_id dedup can't help (7 real ids); only a CONTENT claim can — a process-local gate plus an
atomic DB upsert (vula_media_dedup, migration 157).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import vula.api.whatsapp as wa
from vula.api.whatsapp import _claim_media_once, _handle_document_ingest

TID = "digg-demo"
PHONE = "27645755210"


def _dedup_client(upsert_returns_data=True, existing_created_at=None):
    """A Supabase-client double for vula_media_dedup: upsert returns the row(s) it inserted
    (empty on conflict), select returns any existing claim row."""
    c = MagicMock()
    tbl = c.table.return_value
    tbl.upsert.return_value.execute.return_value = MagicMock(data=[{}] if upsert_returns_data else [])
    row = [{"created_at": existing_created_at}] if existing_created_at else []
    tbl.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=row)
    # the filed-documents secondary net: .select().eq().eq().gte().limit().execute() → no dup
    tbl.select.return_value.eq.return_value.eq.return_value.gte.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    tbl.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])
    return c


@pytest.mark.asyncio
async def test_claim_wins_on_first_call():
    with patch("vula.commerce.service._client", return_value=_dedup_client()):
        assert await _claim_media_once(TID, "sha-win", "document") is True


@pytest.mark.asyncio
async def test_same_worker_burst_is_caught_by_the_local_gate():
    with patch("vula.commerce.service._client", return_value=_dedup_client()):
        first = await _claim_media_once(TID, "sha-burst", "document")
        second = await _claim_media_once(TID, "sha-burst", "document")
        third = await _claim_media_once(TID, "sha-burst", "document")
    assert (first, second, third) == (True, False, False)


@pytest.mark.asyncio
async def test_cross_worker_duplicate_is_caught_by_the_db_claim():
    from datetime import datetime, timezone
    # upsert returns [] (conflict — another worker already inserted), existing row is recent
    client = _dedup_client(upsert_returns_data=False,
                           existing_created_at=datetime.now(timezone.utc).isoformat())
    with patch("vula.commerce.service._client", return_value=client):
        assert await _claim_media_once(TID, "sha-xworker", "document") is False


@pytest.mark.asyncio
async def test_stale_claim_is_refreshed_and_reprocesses():
    client = _dedup_client(upsert_returns_data=False,
                           existing_created_at="2020-01-01T00:00:00+00:00")
    with patch("vula.commerce.service._client", return_value=client):
        assert await _claim_media_once(TID, "sha-stale", "document") is True


@pytest.mark.asyncio
async def test_db_error_fails_open_but_local_gate_still_applies():
    client = MagicMock()
    client.table.side_effect = RuntimeError("db down")
    with patch("vula.commerce.service._client", return_value=client):
        assert await _claim_media_once(TID, "sha-dberr", "document") is True   # first: local wins, db fails open
        assert await _claim_media_once(TID, "sha-dberr", "document") is False  # second: local gate


@pytest.mark.asyncio
async def test_short_window_lets_a_different_file_with_the_same_name_through():
    # filename-key claims use a 45s window; simulate the first being older than that
    import time
    wa._media_claims_local[(TID, "meta:Payment Notification.pdf|application/pdf|" + PHONE)] = time.monotonic() - 60
    with patch("vula.commerce.service._client", return_value=_dedup_client()):
        got = await _claim_media_once(TID, "meta:Payment Notification.pdf|application/pdf|" + PHONE,
                                     "document", PHONE, window_seconds=45)
    assert got is True


@pytest.mark.asyncio
async def test_burst_duplicate_sends_no_ack_and_does_no_work(tmp_path):
    """A 2nd copy of the same file (same name+type+sender, different bytes/sha — Meta re-encodes
    on each resend) must return before the ack: no "Got it", no download, no ingest."""
    import time
    fname_key = ("digg-demo", f"name:payment notification.pdf|application/pdf|{PHONE}")
    with patch("vula.commerce.service._client", return_value=_dedup_client()):
        wa._media_claims_local[fname_key] = time.monotonic()  # first copy already claimed it
        with (
            patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply,
            patch("vula.api.whatsapp._download_document", new=AsyncMock()) as dl,
            patch("vula.ingestion.pipeline.VulaIngestionPipeline") as pipe,
        ):
            # note: a DIFFERENT sha from the first copy — content hash can't save us here
            await _handle_document_ingest(PHONE, "media123", "Payment Notification.pdf",
                                          "application/pdf", route_tenant_id=TID, content_sha="sha-copy-2")
    reply.assert_not_called()
    dl.assert_not_called()
    pipe.assert_not_called()


@pytest.mark.asyncio
async def test_first_delivery_acks_and_proceeds(tmp_path):
    local_file = tmp_path / "Payment Notification.pdf"
    local_file.write_bytes(b"real pdf bytes")
    client = _dedup_client()
    pipe = MagicMock()
    pipe.ingest_file = AsyncMock(return_value=MagicMock(status="failed", error="reached", filename="x.pdf"))
    with (
        patch("vula.commerce.service._client", return_value=client),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipe),
    ):
        await _handle_document_ingest(PHONE, "media123", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID, content_sha="sha-first")
    assert any("Got it" in c.args[1] for c in reply.call_args_list)
    pipe.ingest_file.assert_called_once()
