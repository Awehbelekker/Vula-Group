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
    # Durable dedup (migration 071) and number routing both hit the real DB via
    # vula.commerce.service._client() — mocked here so this test can't self-poison
    # vula_wa_msg_dedup with its hardcoded msg_id against production (bit us 3x this
    # session: a prior real insert made every later run see "wamid.abc123" as a dup).
    with (
        patch("vula.api.whatsapp._handle_message", new_callable=AsyncMock) as mock_handle,
        patch("vula.commerce.service._client", return_value=MagicMock()),
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


# ── _send_invoice_document ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_invoice_document_skips_when_not_configured():
    with patch("vula.api.whatsapp.settings") as mock_settings:
        mock_settings.whatsapp_token = ""
        mock_settings.whatsapp_phone_id = ""
        from vula.api.whatsapp import _send_invoice_document
        result = await _send_invoice_document(
            "+27821234567", b"%PDF-1.4 fake", "OTH-INV-00001.pdf", "Your invoice"
        )
    assert result is False


@pytest.mark.asyncio
async def test_send_invoice_document_uploads_then_sends():
    upload_resp = MagicMock()
    upload_resp.raise_for_status = MagicMock()
    upload_resp.json.return_value = {"id": "media-789"}

    send_resp = MagicMock()
    send_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=[upload_resp, send_resp])

    with (
        patch("vula.api.whatsapp.settings") as mock_settings,
        patch("vula.api.whatsapp.httpx.AsyncClient", return_value=mock_client),
    ):
        mock_settings.whatsapp_token = "token"
        mock_settings.whatsapp_phone_id = "12345"
        from vula.api.whatsapp import _send_invoice_document
        result = await _send_invoice_document(
            "0821234567", b"%PDF-1.4 fake", "OTH-INV-00001.pdf", "Your invoice", ""
        )

    assert result is True
    # First call uploads the PDF as multipart media
    upload_call = mock_client.post.call_args_list[0]
    assert upload_call.args[0].endswith("/12345/media")
    assert upload_call.kwargs["data"]["type"] == "application/pdf"
    assert upload_call.kwargs["files"]["file"][0] == "OTH-INV-00001.pdf"
    # Second call sends a document message referencing the media id
    send_call = mock_client.post.call_args_list[1]
    payload = send_call.kwargs["json"]
    assert payload["type"] == "document"
    assert payload["to"] == "27821234567"  # 0 → 27 normalisation
    assert payload["document"]["id"] == "media-789"
    assert payload["document"]["filename"] == "OTH-INV-00001.pdf"
    assert payload["document"]["caption"] == "Your invoice"


@pytest.mark.asyncio
async def test_send_invoice_document_returns_false_without_media_id():
    upload_resp = MagicMock()
    upload_resp.raise_for_status = MagicMock()
    upload_resp.json.return_value = {}  # no media id returned

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=upload_resp)

    with (
        patch("vula.api.whatsapp.settings") as mock_settings,
        patch("vula.api.whatsapp.httpx.AsyncClient", return_value=mock_client),
    ):
        mock_settings.whatsapp_token = "token"
        mock_settings.whatsapp_phone_id = "12345"
        from vula.api.whatsapp import _send_invoice_document
        result = await _send_invoice_document(
            "27821234567", b"%PDF", "OTH-INV-00002.pdf", "", ""
        )

    assert result is False
    # Only the upload was attempted; no document message sent
    assert mock_client.post.await_count == 1


# ── _rag_reply ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rag_reply_returns_fallback_when_no_sources():
    mock_pipeline = AsyncMock()
    mock_pipeline.query = AsyncMock(return_value=[])

    # Disable the multi-agent runner so the documented RAG fallback path runs.
    with (
        patch("core.agent_runner.get_agent_runner", side_effect=Exception("agent disabled")),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        from vula.api.whatsapp import _rag_reply
        reply = await _rag_reply("tenant-abc", "What are our payment terms?")

    assert "don't have enough information" in reply


@pytest.mark.asyncio
async def test_rag_reply_returns_answer_when_sources_found():
    mock_pipeline = AsyncMock()
    mock_pipeline.query = AsyncMock(return_value=[{"text": "30 days net"}])
    mock_pipeline.answer = AsyncMock(return_value="Payment terms are 30 days net.")

    # Disable the multi-agent runner so the documented RAG fallback path runs.
    with (
        patch("core.agent_runner.get_agent_runner", side_effect=Exception("agent disabled")),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline),
    ):
        from vula.api.whatsapp import _rag_reply
        reply = await _rag_reply("tenant-abc", "What are our payment terms?")

    assert reply == "Payment terms are 30 days net."


@pytest.mark.asyncio
async def test_rag_reply_passes_phone_as_metadata_to_agent_runner():
    """2026-07-27 bookings-via-chat gap: commerce_assistant's book_appointment/
    cancel_appointment need customer_phone/session_id, but _rag_reply never passed
    metadata through to the agent runner at all."""
    from types import SimpleNamespace

    captured = {}

    class _FakeRunner:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(final_answer="ok", skill_used="commerce_assistant", confidence=0.8)

    with patch("core.agent_runner.get_agent_runner", return_value=_FakeRunner()):
        from vula.api.whatsapp import _rag_reply
        await _rag_reply("tenant-abc", "book me a slot", phone="27821234567")

    assert captured["metadata"] == {"customer_phone": "27821234567", "session_id": "27821234567"}


@pytest.mark.asyncio
async def test_rag_reply_metadata_none_when_no_phone():
    from types import SimpleNamespace

    captured = {}

    class _FakeRunner:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(final_answer="ok", skill_used="reasoning", confidence=0.8)

    with patch("core.agent_runner.get_agent_runner", return_value=_FakeRunner()):
        from vula.api.whatsapp import _rag_reply
        await _rag_reply("tenant-abc", "what standards apply?")

    assert captured["metadata"] is None


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
        # Disable the multi-agent runner so the RAG fallback path runs and raises.
        with patch("core.agent_runner.get_agent_runner", side_effect=Exception("agent disabled")):
            from vula.api.whatsapp import _rag_reply
            reply = await _rag_reply("tenant-abc", "hello")
        assert "trouble" in reply.lower()
    finally:
        _pip.VulaIngestionPipeline = original


# ── _maybe_escalate_and_learn — customer-sentiment flag to the helper ─────────

@pytest.mark.asyncio
async def test_escalation_flags_frustrated_customer_to_helper():
    from vula.api.whatsapp import _maybe_escalate_and_learn

    with (
        patch("vula.escalation.find_learned_answer", return_value=None),
        patch("vula.escalation.create_escalation",
              return_value={"id": "e1", "helper_phone": "27821112222", "helper_name": "Staci"}),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        reply = await _maybe_escalate_and_learn(
            "off-the-hook", "27820001111", "This is ridiculous, worst service ever!!!",
            "Sorry, I'm not sure about that.", confidence=0.9,
        )

    assert "check with the team" in reply.lower()
    helper_msg = mock_send.call_args[0][1]
    assert "frustrated" in helper_msg.lower()


@pytest.mark.asyncio
async def test_escalation_no_frustration_flag_for_normal_question():
    from vula.api.whatsapp import _maybe_escalate_and_learn

    with (
        patch("vula.escalation.find_learned_answer", return_value=None),
        patch("vula.escalation.create_escalation",
              return_value={"id": "e1", "helper_phone": "27821112222", "helper_name": "Staci"}),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        await _maybe_escalate_and_learn(
            "off-the-hook", "27820001111", "Do you deliver to Bellville?",
            "I don't know that one.", confidence=0.9,
        )

    helper_msg = mock_send.call_args[0][1]
    assert "frustrated" not in helper_msg.lower()


# ── _run_commerce_assistant — voice/language threading ────────────────────────

@pytest.mark.asyncio
async def test_run_commerce_assistant_persists_detected_language():
    """Whisper's detected language must reach commerce_service.set_session_language and the
    skill's metadata. Regression test: detected_lang was silently dropped between
    _handle_commerce_message and _run_commerce_assistant, and the resulting NameError inside
    _run_commerce_assistant was swallowed by a broad except — so this path never ran at all."""
    from vula.api.whatsapp import _run_commerce_assistant

    mock_session = {"id": "sess-1", "preferred_language": None}
    mock_service = MagicMock()
    mock_service.get_or_create_session = AsyncMock(return_value=mock_session)
    mock_service.get_recent_messages = AsyncMock(return_value=[])
    mock_service.format_history = MagicMock(return_value="")
    mock_service.set_session_language = AsyncMock()
    mock_service.append_message = AsyncMock()

    mock_output = MagicMock(success=True, answer="Goeie dag! Hoe kan ek help?", error=None)
    mock_skill = AsyncMock(return_value=mock_output)

    with (
        patch("vula.commerce.service", mock_service),
        patch("core.skills.loader.get_skill", return_value=mock_skill),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)),
    ):
        handled = await _run_commerce_assistant(
            "27821234567", "gee my twee hake asseblief", "off-the-hook", detected_lang="af"
        )

    assert handled is True
    mock_service.set_session_language.assert_awaited_once_with("off-the-hook", "27821234567", "af")
    _, call_kwargs = mock_skill.call_args
    sent_input = mock_skill.call_args[0][0]
    assert sent_input.metadata["preferred_language"] == "af"
