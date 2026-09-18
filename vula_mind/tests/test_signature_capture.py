"""Tests for the WhatsApp signature-capture flow (2026-09-18, migration 165) — a staff member
sends a trigger phrase, Vula asks for a photo, and the next photo within a short window is
captured as the tenant's signature instead of going through the normal photo pipeline. Mirrors
the proven-safe _purpose_prompted_at pattern (in-memory, time-windowed, fails toward letting
messages through) rather than inventing a new pending-question interceptor.
"""
import time
from unittest.mock import AsyncMock, patch

import pytest

from vula.api.whatsapp import (
    _handle_image_or_video,
    _handle_signature_capture,
    _maybe_start_signature_capture,
    _note_signature_prompt,
    _recently_asked_about_signature,
    _signature_prompted_at,
    _SIGNATURE_PROMPT_WINDOW_S,
)

TID = "digg-demo"
STAFF_PHONE = "27827077080"
CUSTOMER_PHONE = "27645755210"


@pytest.fixture(autouse=True)
def _clear_prompt_memory():
    _signature_prompted_at.clear()
    yield
    _signature_prompted_at.clear()


# ── prompt-window bookkeeping ──────────────────────────────────────────────────────

def test_note_and_recently_asked_round_trip():
    assert _recently_asked_about_signature(STAFF_PHONE) is False
    _note_signature_prompt(STAFF_PHONE)
    assert _recently_asked_about_signature(STAFF_PHONE) is True


def test_window_expires():
    _note_signature_prompt(STAFF_PHONE)
    _signature_prompted_at[STAFF_PHONE] = time.monotonic() - (_SIGNATURE_PROMPT_WINDOW_S + 1)
    assert _recently_asked_about_signature(STAFF_PHONE) is False


def test_window_is_per_person():
    _note_signature_prompt(STAFF_PHONE)
    assert _recently_asked_about_signature(CUSTOMER_PHONE) is False


def test_a_restart_fails_toward_letting_messages_through():
    _signature_prompted_at.clear()
    assert _recently_asked_about_signature(STAFF_PHONE) is False


# ── _maybe_start_signature_capture (text trigger) ──────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "set my signature", "please update my signature", "I want to change my signature",
    "add a signature",
])
async def test_trigger_phrases_prompt_for_a_photo(text):
    with patch("vula.api.whatsapp._is_tenant_owner", return_value=True):
        reply = await _maybe_start_signature_capture(TID, STAFF_PHONE, text)
    assert reply is not None
    assert "photo" in reply.lower()
    assert _recently_asked_about_signature(STAFF_PHONE) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "what were today's sales?", "sign this invoice", "significant delay on site",
    "here's the signage for the new office",
])
async def test_unrelated_messages_are_not_triggers(text):
    with patch("vula.api.whatsapp._is_tenant_owner", return_value=True):
        reply = await _maybe_start_signature_capture(TID, STAFF_PHONE, text)
    assert reply is None
    assert _recently_asked_about_signature(STAFF_PHONE) is False


@pytest.mark.asyncio
async def test_a_customer_cannot_trigger_signature_capture():
    with (
        patch("vula.api.whatsapp._is_tenant_owner", return_value=False),
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock(return_value=False)),
    ):
        reply = await _maybe_start_signature_capture(TID, CUSTOMER_PHONE, "set my signature")
    assert reply is None
    assert _recently_asked_about_signature(CUSTOMER_PHONE) is False


@pytest.mark.asyncio
async def test_no_tenant_id_is_never_a_trigger():
    reply = await _maybe_start_signature_capture("", STAFF_PHONE, "set my signature")
    assert reply is None


# ── _handle_signature_capture (photo capture) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_capture_saves_url_and_clears_the_prompt():
    _note_signature_prompt(STAFF_PHONE)
    with (
        patch("vula.api.whatsapp._download_media_bytes", new=AsyncMock(return_value=b"fake-jpeg-bytes")),
        patch("vula.api.whatsapp._upload_to_storage", return_value="https://storage.example/sig.png") as mock_upload,
        patch("vula.commerce.service.upsert_invoice_settings", new=AsyncMock(return_value={})) as mock_upsert,
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_reply,
    ):
        result = await _handle_signature_capture(STAFF_PHONE, "media1", TID)

    assert result is True
    mock_upload.assert_called_once_with("signatures", f"{TID}/signature.png", b"fake-jpeg-bytes", "image/jpeg")
    mock_upsert.assert_awaited_once_with(TID, {"signature_url": "https://storage.example/sig.png"})
    assert "saved" in mock_reply.call_args.args[1].lower()
    assert _recently_asked_about_signature(STAFF_PHONE) is False


@pytest.mark.asyncio
async def test_download_failure_replies_and_does_not_save():
    with (
        patch("vula.api.whatsapp._download_media_bytes", new=AsyncMock(return_value=None)),
        patch("vula.commerce.service.upsert_invoice_settings", new=AsyncMock()) as mock_upsert,
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_reply,
    ):
        result = await _handle_signature_capture(STAFF_PHONE, "media1", TID)

    assert result is True
    mock_upsert.assert_not_called()
    assert "couldn't download" in mock_reply.call_args.args[1].lower()


@pytest.mark.asyncio
async def test_upload_failure_replies_and_does_not_save():
    with (
        patch("vula.api.whatsapp._download_media_bytes", new=AsyncMock(return_value=b"bytes")),
        patch("vula.api.whatsapp._upload_to_storage", return_value=None),
        patch("vula.commerce.service.upsert_invoice_settings", new=AsyncMock()) as mock_upsert,
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_reply,
    ):
        result = await _handle_signature_capture(STAFF_PHONE, "media1", TID)

    assert result is True
    mock_upsert.assert_not_called()
    assert "couldn't save" in mock_reply.call_args.args[1].lower()


@pytest.mark.asyncio
async def test_unexpected_exception_replies_gracefully_not_raise():
    with (
        patch("vula.api.whatsapp._download_media_bytes", new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_reply,
    ):
        result = await _handle_signature_capture(STAFF_PHONE, "media1", TID)

    assert result is True
    mock_reply.assert_awaited_once()


# ── _handle_image_or_video wiring ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_signature_photo_is_captured_before_any_other_photo_handling():
    _note_signature_prompt(STAFF_PHONE)
    with (
        patch("vula.api.whatsapp._is_tenant_owner", return_value=True),
        patch("vula.api.whatsapp._handle_signature_capture", new=AsyncMock(return_value=True)) as mock_capture,
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock()) as mock_rep,
        patch("vula.api.whatsapp._handle_media", new=AsyncMock()) as mock_media,
        patch("vula.api.whatsapp._handle_document_ingest", new=AsyncMock()) as mock_ingest,
    ):
        await _handle_image_or_video(
            STAFF_PHONE, "image", "media1", "", "image/jpeg", "msg1",
            route_mode="commerce", route_tenant=TID, content_sha=None)

    mock_capture.assert_awaited_once_with(STAFF_PHONE, "media1", TID)
    mock_rep.assert_not_called()
    mock_media.assert_not_called()
    mock_ingest.assert_not_called()


@pytest.mark.asyncio
async def test_photo_from_a_non_staff_sender_is_never_captured_as_a_signature():
    """Even with a prompt outstanding for someone else, or a customer somehow having a live
    prompt, only a confirmed staff sender's photo is ever captured."""
    _note_signature_prompt(CUSTOMER_PHONE)
    with (
        patch("vula.api.whatsapp._is_tenant_owner", return_value=False),
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._handle_signature_capture", new=AsyncMock()) as mock_capture,
        patch("vula.api.whatsapp._handle_media", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._describe_photo_for_rep", new=AsyncMock(return_value="")),
        patch("vula.api.whatsapp._run_commerce_assistant", new=AsyncMock(return_value=True)),
    ):
        await _handle_image_or_video(
            CUSTOMER_PHONE, "image", "media1", "", "image/jpeg", "msg1",
            route_mode="commerce", route_tenant=TID, content_sha=None)

    mock_capture.assert_not_called()


@pytest.mark.asyncio
async def test_no_pending_prompt_photo_goes_through_normal_handling():
    with (
        patch("vula.api.whatsapp._is_tenant_owner", return_value=True),
        patch("vula.api.whatsapp._sender_is_sales_rep", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._handle_signature_capture", new=AsyncMock()) as mock_capture,
        patch("vula.api.whatsapp._handle_media", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._handle_document_ingest", new=AsyncMock()) as mock_ingest,
    ):
        await _handle_image_or_video(
            STAFF_PHONE, "image", "media1", "restock photo", "image/jpeg", "msg1",
            route_mode="commerce", route_tenant=TID, content_sha=None)

    mock_capture.assert_not_called()
    mock_ingest.assert_awaited_once()
