"""Tests for the WhatsApp EXPORT trigger wiring (vula/api/whatsapp.py's _EXPORT_RE and
_handle_data_export) — go-live readiness pass, Phase 2.1. Mirrors _DELETE_RE's exact-phrase
shape deliberately: a request this consequential shouldn't fire on an unrelated message."""
from unittest.mock import AsyncMock, patch

import pytest

from vula.api.whatsapp import _EXPORT_RE, _handle_data_export

PHONE = "+27821234567"
TID = "digg-demo"


@pytest.mark.parametrize("text", ["export", "EXPORT", "send my data", "get my data", "my data export"])
def test_export_re_matches_trigger_phrases(text):
    assert _EXPORT_RE.match(text)


@pytest.mark.parametrize("text", [
    "what's my order status", "please export the invoice as pdf",
    "I exported this earlier", "my data is wrong",
])
def test_export_re_does_not_match_unrelated_messages(text):
    assert not _EXPORT_RE.match(text)


@pytest.mark.asyncio
async def test_handle_data_export_no_tenant_id_replies_with_failure_message():
    with patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply:
        await _handle_data_export(PHONE, None)
    mock_reply.assert_called_once()
    assert "hello@vula.co.za" in mock_reply.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_data_export_success_replies_with_masked_email():
    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.data_export.send_data_export", new=AsyncMock(
            return_value={"sent": True, "email": "client@example.com"})),
    ):
        await _handle_data_export(PHONE, TID)
    mock_reply.assert_called_once()
    reply_text = mock_reply.call_args[0][1]
    assert "emailed" in reply_text
    assert "client@example.com" not in reply_text  # masked, not the raw address


@pytest.mark.asyncio
async def test_handle_data_export_no_email_on_file_gives_actionable_reply():
    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.data_export.send_data_export", new=AsyncMock(
            return_value={"error": "no_email_on_file"})),
    ):
        await _handle_data_export(PHONE, TID)
    reply_text = mock_reply.call_args[0][1]
    assert "hello@vula.co.za" in reply_text


@pytest.mark.asyncio
async def test_handle_data_export_generic_failure_gives_fallback_reply():
    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.data_export.send_data_export", new=AsyncMock(
            return_value={"error": "render_failed"})),
    ):
        await _handle_data_export(PHONE, TID)
    reply_text = mock_reply.call_args[0][1]
    assert "hello@vula.co.za" in reply_text


@pytest.mark.asyncio
async def test_handle_data_export_never_raises_on_unexpected_exception():
    with (
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_reply,
        patch("vula.api.data_export.send_data_export", new=AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        await _handle_data_export(PHONE, TID)  # must not raise
    mock_reply.assert_called_once()
