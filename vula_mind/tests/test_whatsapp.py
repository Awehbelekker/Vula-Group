"""Tests for the WhatsApp inbound webhook."""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from vula.api.server import app

client = TestClient(app)


# ── Webhook verification ──────────────────────────────────────────────────────

def test_webhook_verify_success():
    with patch("vula.api.whatsapp.settings") as mock_settings:
        mock_settings.whatsapp_verify_token = "my-secret-token"
        resp = client.get(
            "/v1/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "my-secret-token",
                "hub.challenge": "123456",
            },
        )
    assert resp.status_code == 200
    assert resp.json() == 123456


def test_webhook_verify_wrong_token():
    with patch("vula.api.whatsapp.settings") as mock_settings:
        mock_settings.whatsapp_verify_token = "my-secret-token"
        resp = client.get(
            "/v1/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "123456",
            },
        )
    assert resp.status_code == 403


def test_webhook_verify_wrong_mode():
    with patch("vula.api.whatsapp.settings") as mock_settings:
        mock_settings.whatsapp_verify_token = "tok"
        resp = client.get(
            "/v1/whatsapp/webhook",
            params={"hub.mode": "other", "hub.verify_token": "tok", "hub.challenge": "123"},
        )
    assert resp.status_code == 403


# ── Inbound message ───────────────────────────────────────────────────────────

def _wa_payload(phone: str, text: str) -> dict:
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "type": "text",
                        "from": phone,
                        "id": "wamid.abc123",
                        "text": {"body": text},
                    }]
                }
            }]
        }]
    }


def test_inbound_returns_ok():
    with (
        patch("vula.api.whatsapp._handle_message", new_callable=AsyncMock) as mock_handle,
    ):
        resp = client.post("/v1/whatsapp/webhook", json=_wa_payload("+27821234567", "Hello"))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    mock_handle.assert_called_once_with("+27821234567", "Hello", "wamid.abc123")


def test_inbound_ignores_non_text_messages():
    payload = {
        "entry": [{"changes": [{"value": {
            "messages": [{"type": "image", "from": "+27821234567", "id": "wamid.xyz"}]
        }}]}]
    }
    with patch("vula.api.whatsapp._handle_message", new_callable=AsyncMock) as mock_handle:
        resp = client.post("/v1/whatsapp/webhook", json=payload)
    assert resp.status_code == 200
    mock_handle.assert_not_called()


def test_inbound_empty_payload():
    resp = client.post("/v1/whatsapp/webhook", json={})
    assert resp.status_code == 200


# ── _tenant_for_phone ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tenant_for_phone_returns_none_when_not_configured():
    with patch("vula.api.whatsapp.settings") as mock_settings:
        mock_settings.supabase_url = ""
        mock_settings.supabase_service_key = ""
        from vula.api.whatsapp import _tenant_for_phone
        result = await _tenant_for_phone("+27821234567")
    assert result is None


@pytest.mark.asyncio
async def test_tenant_for_phone_finds_tenant():
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = [{"tenant_id": "digg-001"}]

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with (
        patch("vula.api.whatsapp.settings") as mock_settings,
        patch("vula.api.whatsapp.httpx.AsyncClient", return_value=mock_client),
    ):
        mock_settings.supabase_url = "https://project.supabase.co"
        mock_settings.supabase_service_key = "key"
        from vula.api.whatsapp import _tenant_for_phone
        result = await _tenant_for_phone("27821234567")

    assert result == "digg-001"


# ── _send_reply ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_reply_skips_when_not_configured():
    with patch("vula.api.whatsapp.settings") as mock_settings:
        mock_settings.whatsapp_token = ""
        mock_settings.whatsapp_phone_id = ""
        from vula.api.whatsapp import _send_reply
        result = await _send_reply("+27821234567", "Hello")
    assert result is False


@pytest.mark.asyncio
async def test_send_reply_normalises_sa_number():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)

    with (
        patch("vula.api.whatsapp.settings") as mock_settings,
        patch("vula.api.whatsapp.httpx.AsyncClient", return_value=mock_client),
    ):
        mock_settings.whatsapp_token = "token"
        mock_settings.whatsapp_phone_id = "12345"
        mock_settings.whatsapp_api_url = "https://graph.facebook.com/v19.0"
        from vula.api.whatsapp import _send_reply
        result = await _send_reply("0821234567", "Test reply")

    assert result is True
    call_kwargs = mock_client.post.call_args.kwargs
    payload = call_kwargs["json"]
    assert payload["to"] == "27821234567"  # 0 → 27 normalisation
    assert payload["text"]["body"] == "Test reply"


@pytest.mark.asyncio
async def test_send_reply_truncates_long_message():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_response)

    long_message = "A" * 5000

    with (
        patch("vula.api.whatsapp.settings") as mock_settings,
        patch("vula.api.whatsapp.httpx.AsyncClient", return_value=mock_client),
    ):
        mock_settings.whatsapp_token = "token"
        mock_settings.whatsapp_phone_id = "12345"
        mock_settings.whatsapp_api_url = "https://graph.facebook.com/v19.0"
        from vula.api.whatsapp import _send_reply
        await _send_reply("27821234567", long_message)

    payload = mock_client.post.call_args.kwargs["json"]
    assert len(payload["text"]["body"]) <= 4096


# ── _rag_reply ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rag_reply_returns_fallback_when_no_sources():
    mock_pipeline = AsyncMock()
    mock_pipeline.query = AsyncMock(return_value=[])

    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline):
        from vula.api.whatsapp import _rag_reply
        reply = await _rag_reply("tenant-abc", "What are our payment terms?")

    assert "don't have enough information" in reply


@pytest.mark.asyncio
async def test_rag_reply_returns_answer_when_sources_found():
    mock_pipeline = AsyncMock()
    mock_pipeline.query = AsyncMock(return_value=[{"text": "30 days net"}])
    mock_pipeline.answer = AsyncMock(return_value="Payment terms are 30 days net.")

    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline):
        from vula.api.whatsapp import _rag_reply
        reply = await _rag_reply("tenant-abc", "What are our payment terms?")

    assert reply == "Payment terms are 30 days net."


@pytest.mark.asyncio
async def test_rag_reply_handles_pipeline_error():
    # Pipeline import itself raises (e.g. missing Qdrant dependency)
    import vula.ingestion.pipeline as _pip
    original = _pip.VulaIngestionPipeline

    class _BrokenPipeline:
        def __init__(self, **_):
            raise Exception("Qdrant down")

    _pip.VulaIngestionPipeline = _BrokenPipeline
    try:
        from vula.api.whatsapp import _rag_reply
        reply = await _rag_reply("tenant-abc", "hello")
        assert "trouble" in reply.lower()
    finally:
        _pip.VulaIngestionPipeline = original
