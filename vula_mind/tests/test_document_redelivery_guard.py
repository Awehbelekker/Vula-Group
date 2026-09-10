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
async def test_second_doc_in_a_burst_gets_no_ack_but_still_processes(tmp_path):
    """A batch of files (or a re-sent file) shares ONE "Got it"; every doc still downloads +
    extracts — only the content fingerprint below decides what's a real duplicate."""
    import time
    local_file = tmp_path / "Payment Notification.pdf"
    local_file.write_bytes(b"pdf")
    wa._media_claims_local[(TID, f"ack:{PHONE}")] = time.monotonic()  # ack already sent this burst
    client = _dedup_client()
    (client.table.return_value.select.return_value.eq.return_value.eq.return_value
     .gte.return_value.limit.return_value.execute.return_value) = MagicMock(data=[])
    pipe = MagicMock()
    pipe.ingest_file = AsyncMock(return_value=MagicMock(status="failed", error="reached", filename="x.pdf"))
    with (
        patch("vula.commerce.service._client", return_value=client),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=pipe),
    ):
        await _handle_document_ingest(PHONE, "media456", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID, content_sha=None)
    assert not any("Got it" in c.args[1] for c in reply.call_args_list)  # no 2nd ack
    pipe.ingest_file.assert_called_once()                                 # but it did process


def test_content_fingerprint_same_payment_collapses():
    from vula.api.whatsapp import _content_fingerprint as fp
    a = fp("Proof of Payment", {"trace_id": "W1KYP3WQ", "amount": "6579.00", "payee_name": "Sagacity"})
    b = fp("Proof of Payment", {"trace_id": "W1KYP3WQ", "amount": "R6,579.00",
                                "payee_name": "Sagacity Group", "date": "2026/09/10"})
    assert a and a == b


def test_content_fingerprint_different_payments_do_not_collapse():
    from vula.api.whatsapp import _content_fingerprint as fp
    a = fp("Proof of Payment", {"trace_id": "W1KYP3WQ", "amount": "6579.00"})
    c = fp("Proof of Payment", {"trace_id": "EBRSGYC1JXQB", "amount": "2094.40"})
    # different FNB "Payment Notification.pdf"s, different transactions
    assert a != c
    # amount/payee fallback still separates two refs
    d = fp("Proof of Payment", {"amount": "44000.00", "payee_name": "ACME", "reference": "37of2002"})
    e = fp("Proof of Payment", {"amount": "50000.00", "payee_name": "ACME", "reference": "01726"})
    assert d and d != e


def test_content_fingerprint_returns_empty_on_thin_extraction():
    from vula.api.whatsapp import _content_fingerprint as fp
    assert fp("Document", {"amount": "100"}) == ""      # one field only — not enough signal
    assert fp("Document", {}) == ""


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
