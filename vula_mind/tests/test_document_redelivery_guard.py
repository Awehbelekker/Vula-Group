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


def _dedup_client(upsert_returns_data=True, existing_created_at=None, existing_claimed_by=None):
    """A Supabase-client double for vula_media_dedup: upsert returns the row(s) it inserted
    (empty on conflict), select returns any existing claim row."""
    c = MagicMock()
    tbl = c.table.return_value
    tbl.upsert.return_value.execute.return_value = MagicMock(data=[{}] if upsert_returns_data else [])
    row = ([{"created_at": existing_created_at, "claimed_by": existing_claimed_by}]
           if existing_created_at else [])
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


# --- Duplicate notice: tell the sender who/when, instead of pure silence (2026-09-11) --------
#
# The 2026-09-10 fix above stopped a redelivery burst from becoming a wall of full analyses —
# but it went all the way to silence, and a GENUINE later re-send (a stale claim from an
# earlier, unrelated send) now looks identical to "nothing happened" from the sender's side.
# These tests cover the notice _send_duplicate_notice adds back on top of the existing silent
# drop, without reintroducing the wall-of-messages problem.

@pytest.mark.asyncio
async def test_duplicate_content_sha_notifies_sender_as_self():
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    client = _dedup_client(upsert_returns_data=False, existing_created_at=now_iso,
                           existing_claimed_by=PHONE)
    with (
        patch("vula.commerce.service._client", return_value=client),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply,
    ):
        await _handle_document_ingest(PHONE, "media789", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID, content_sha="sha-dup1")
    msgs = [c.args[1] for c in reply.call_args_list]
    assert any("👀" in m and "by you" in m for m in msgs)
    assert not any("Got it" in m for m in msgs)   # the ack line was never reached


@pytest.mark.asyncio
async def test_duplicate_by_team_member_uses_their_name():
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    other_phone = "27831234567"
    client = _dedup_client(upsert_returns_data=False, existing_created_at=now_iso,
                           existing_claimed_by=other_phone)
    with (
        patch("vula.commerce.service._client", return_value=client),
        patch("vula.integrations.notify.team_member_for_phone", return_value={"name": "Thabo"}),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply,
    ):
        await _handle_document_ingest(PHONE, "media790", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID, content_sha="sha-dup2")
    msgs = [c.args[1] for c in reply.call_args_list]
    assert any("Thabo" in m for m in msgs)


@pytest.mark.asyncio
async def test_duplicate_by_unknown_phone_falls_back_to_masked_number():
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    other_phone = "27831234567"
    client = _dedup_client(upsert_returns_data=False, existing_created_at=now_iso,
                           existing_claimed_by=other_phone)
    with (
        patch("vula.commerce.service._client", return_value=client),
        patch("vula.integrations.notify.team_member_for_phone", return_value=None),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply,
    ):
        await _handle_document_ingest(PHONE, "media791", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID, content_sha="sha-dup3")
    msgs = [c.args[1] for c in reply.call_args_list]
    assert any("...4567" in m for m in msgs)
    assert not any(other_phone in m for m in msgs)


@pytest.mark.asyncio
async def test_duplicate_content_fingerprint_notice_includes_payee_and_amount(tmp_path):
    """The richest duplicate path (post-analysis content fingerprint) has real extracted
    fields on hand — the notice should use them, not just the filename."""
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    local_file = tmp_path / "Payment Notification.pdf"
    local_file.write_bytes(b"pdf bytes")
    analysis = {"category": "Proof of Payment",
               "fields": {"payee_name": "Sagacity", "amount_cents": 657900}}

    async def fake_claim(tenant_id, content_sha, kind, phone="", window_seconds=600):
        return not content_sha.startswith("content:")   # only the fingerprint claim fails

    with (
        patch("vula.commerce.service._client", return_value=_dedup_client()),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply,
        patch("vula.api.whatsapp._download_document", new=AsyncMock(return_value=local_file)),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline") as mock_pipeline_cls,
        patch("vula.api.whatsapp._claim_media_once", new=AsyncMock(side_effect=fake_claim)),
        patch("vula.api.whatsapp._dup_claim_row",
              new=AsyncMock(return_value={"created_at": now_iso, "claimed_by": PHONE})),
        patch("vula.api.whatsapp._analyze_document", new=AsyncMock(return_value=analysis)),
    ):
        mock_pipeline_cls.return_value.ingest_file = AsyncMock(
            return_value=MagicMock(status="success", filename="Payment Notification.pdf", doc_id="d1"))
        await _handle_document_ingest(PHONE, "media792", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID, content_sha=None)

    msgs = [c.args[1] for c in reply.call_args_list]
    assert any("Sagacity" in m and "R6,579.00" in m for m in msgs)


@pytest.mark.asyncio
async def test_duplicate_notice_burst_gate_suppresses_repeat_notices():
    """Two rapid duplicate hits on the same phone (a redelivery burst) must produce exactly
    ONE notice — the whole point of _DUP_NOTICE_WINDOW."""
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    client = _dedup_client(upsert_returns_data=False, existing_created_at=now_iso,
                           existing_claimed_by=PHONE)
    with (
        patch("vula.commerce.service._client", return_value=client),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply,
    ):
        await _handle_document_ingest(PHONE, "media793", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID,
                                      content_sha="sha-burst-notice")
        await _handle_document_ingest(PHONE, "media794", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID,
                                      content_sha="sha-burst-notice")
    notice_count = sum(1 for c in reply.call_args_list if "👀" in c.args[1])
    assert notice_count == 1


@pytest.mark.asyncio
async def test_dup_claim_row_lookup_db_error_still_sends_generic_notice():
    """A local-only claim (no DB round trip needed to know it's a dup) whose follow-up
    who/when lookup then hits a dead DB must still notify — with the degenerate wording,
    not silence and not a crash."""
    import time
    wa._media_claims_local[(TID, "sha-local-only")] = time.monotonic()

    class _BoomClient:
        def table(self, *a, **kw):
            raise RuntimeError("db down")

    with (
        patch("vula.commerce.service._client", return_value=_BoomClient()),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply,
    ):
        await _handle_document_ingest(PHONE, "media795", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID,
                                      content_sha="sha-local-only")
    msgs = [c.args[1] for c in reply.call_args_list]
    assert any("👀" in m for m in msgs)
    assert not any(("filed " in m) or (" by " in m) for m in msgs)   # no when/who clause


def test_dup_claimant_label_resolver_error_falls_back_to_masked_phone():
    with patch("vula.integrations.notify.team_member_for_phone", side_effect=RuntimeError("boom")):
        label = wa._dup_claimant_label(TID, "27831234567", PHONE)
    assert label == "...4567"


@pytest.mark.asyncio
async def test_dup_notice_send_reply_failure_does_not_crash():
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    client = _dedup_client(upsert_returns_data=False, existing_created_at=now_iso,
                           existing_claimed_by=PHONE)
    with (
        patch("vula.commerce.service._client", return_value=client),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        await _handle_document_ingest(PHONE, "media796", "Payment Notification.pdf",
                                      "application/pdf", route_tenant_id=TID,
                                      content_sha="sha-replyfail")
    # No assertion beyond "didn't raise" — pytest fails the test on an uncaught exception.
