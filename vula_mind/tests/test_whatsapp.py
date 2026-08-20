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

    # 2026-08-17: metadata is now always a (possibly-empty) dict rather than None, so
    # preferred_language can be added regardless of whether a phone was given — functionally
    # equivalent downstream (agent_runner.py already does `metadata or {}`).
    assert captured["metadata"] == {}


@pytest.mark.asyncio
async def test_rag_reply_emits_latency_telemetry():
    """2026-08-18: no production-queryable latency telemetry existed for the WhatsApp chat path
    at all — the only persistence was a local, per-instance, best-effort SQLite file. Reuses the
    existing durable core.reasoning_telemetry sink instead of new infrastructure."""
    from types import SimpleNamespace

    class _FakeRunner:
        async def run(self, **kwargs):
            return SimpleNamespace(final_answer="ok", skill_used="reasoning",
                                   confidence=0.62, latency_ms=1834)

    with (
        patch("core.agent_runner.get_agent_runner", return_value=_FakeRunner()),
        patch("core.reasoning_telemetry.emit") as mock_emit,
    ):
        from vula.api.whatsapp import _rag_reply
        await _rag_reply("digg-demo", "what standards apply?")

    mock_emit.assert_called_once()
    kwargs = mock_emit.call_args.kwargs
    assert kwargs["tenant_id"] == "digg-demo"
    assert kwargs["system"] == "vula-whatsapp-rag"
    assert kwargs["extra"]["latency_ms"] == 1834
    assert kwargs["extra"]["confidence"] == 0.62
    assert kwargs["extra"]["skill"] == "reasoning"


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


# ── _maybe_helper_escalation_answer — helper's own new question isn't an answer ────

_OPEN_ESC = {"id": "e1", "customer_phone": "27645755210", "tenant_id": "digg-demo"}


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "If a tile is 200 x 200 how many tiles are in a square?",
    "What time does the site open tomorrow?",
    "How many bags of cement do we need for this",  # no trailing '?'
    "Can you check the BOQ for me?",
])
async def test_helper_own_new_question_not_swallowed_as_answer(question):
    """2026-07-29: Judy, sitting as helper on a 2-day-old stale escalation from an unrelated
    message, asked her own genuine question — it got treated as "the answer" and relayed to
    the wrong person (the original asker), and she never got a reply to her real question."""
    from vula.api.whatsapp import _maybe_helper_escalation_answer

    with (
        patch("vula.escalation.open_escalation_for_helper", return_value=dict(_OPEN_ESC)),
        patch("vula.escalation.answer_escalation") as mock_answer,
    ):
        result = await _maybe_helper_escalation_answer("27827077080", question)

    assert result is False
    mock_answer.assert_not_called()


@pytest.mark.asyncio
async def test_helper_real_answer_still_relayed():
    from vula.api.whatsapp import _maybe_helper_escalation_answer

    with (
        patch("vula.escalation.open_escalation_for_helper", return_value=dict(_OPEN_ESC)),
        patch("vula.escalation.answer_escalation",
              return_value={"customer_phone": "27645755210", "tenant_id": "digg-demo"}),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        result = await _maybe_helper_escalation_answer(
            "27827077080", "It's a business card for the new supplier contact.")

    assert result is True
    assert mock_send.call_count == 2
    assert mock_send.call_args_list[0].args[0] == "27645755210"  # relayed to the original asker


# ── _handle_message — deterministic pending state wins over the sales_rep agent ────

@pytest.mark.asyncio
async def test_pending_expense_allocation_wins_over_sales_rep_gate():
    """2026-07-29: a sales_rep's answer to "which project is this for?" (e.g. "HPC") was
    swallowed by the sales_rep gate's tool-calling agent, which — having no awareness of the
    pending question — hallucinated an unrelated tool call instead of falling through.
    Confirmed live: the same reply triggered a Google-account/email-drafting response, and a
    payment-method correction triggered fabricated "meeting notes". Deterministic in-flight
    conversational state must be checked before the general-purpose sales_rep agent."""
    from vula.api.whatsapp import _handle_message

    with (
        patch("vula.api.whatsapp._maybe_helper_escalation_answer", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._maybe_allocate_pending_expense",
              new=AsyncMock(return_value="Logged to HPC.")),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
        patch("vula.api.whatsapp._run_commerce_admin", new=AsyncMock(return_value=True)) as mock_admin,
    ):
        await _handle_message("27645755210", "HPC", "wamid.1", route_tenant_id="digg-demo")

    mock_admin.assert_not_called()
    mock_send.assert_called_once_with("27645755210", "Logged to HPC.", "digg-demo")


@pytest.mark.asyncio
async def test_sales_rep_gate_still_runs_when_nothing_pending():
    from vula.api.whatsapp import _handle_message

    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"whatsapp": "27645755210"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with (
        patch("vula.api.whatsapp._maybe_helper_escalation_answer", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._maybe_allocate_pending_expense", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._maybe_bank_review_answer", new=AsyncMock(return_value=None)),
        patch("vula.integrations.notify.handle_preference_command", return_value=None),
        patch("vula.commerce.service._client", return_value=mock_db),
        patch("vula.api.whatsapp._run_commerce_admin", new=AsyncMock(return_value=True)) as mock_admin,
    ):
        await _handle_message("27645755210", "Please see business card", "wamid.2",
                              route_tenant_id="digg-demo")

    mock_admin.assert_called_once()


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


# ── _run_commerce_assistant — product photo (2026-08-14) ───────────────────────

def _commerce_service_mock():
    mock_service = MagicMock()
    mock_service.get_or_create_session = AsyncMock(return_value={"id": "sess-1", "preferred_language": None})
    mock_service.get_recent_messages = AsyncMock(return_value=[])
    mock_service.format_history = MagicMock(return_value="")
    mock_service.set_session_language = AsyncMock()
    mock_service.append_message = AsyncMock()
    return mock_service


@pytest.mark.asyncio
async def test_sends_product_photo_when_output_has_media_url():
    from vula.api.whatsapp import _run_commerce_assistant

    mock_output = MagicMock(success=True, answer="Added Hake Fillets to your cart.", error=None,
                            media_url="https://example.com/hake.jpg")
    mock_skill = AsyncMock(return_value=mock_output)

    with (
        patch("vula.commerce.service", _commerce_service_mock()),
        patch("core.skills.loader.get_skill", return_value=mock_skill),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)),
        patch("vula.api.whatsapp._resolve_wa", new=AsyncMock(return_value={"token": "t", "phone_id": "p"})),
        patch("vula.api.whatsapp._send_wa_image", new=AsyncMock(return_value=True)) as mock_send_image,
    ):
        handled = await _run_commerce_assistant("27821234567", "add hake fillets", "off-the-hook")

    assert handled is True
    mock_send_image.assert_awaited_once()
    call_args = mock_send_image.call_args[0]
    assert call_args[2] == "https://example.com/hake.jpg"


@pytest.mark.asyncio
async def test_no_photo_send_when_output_has_no_media_url():
    from vula.api.whatsapp import _run_commerce_assistant

    mock_output = MagicMock(success=True, answer="There are 12 items in that category.",
                            error=None, media_url=None)
    mock_skill = AsyncMock(return_value=mock_output)

    with (
        patch("vula.commerce.service", _commerce_service_mock()),
        patch("core.skills.loader.get_skill", return_value=mock_skill),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)),
        patch("vula.api.whatsapp._send_wa_image", new=AsyncMock(return_value=True)) as mock_send_image,
    ):
        handled = await _run_commerce_assistant("27821234567", "what fish do you have", "off-the-hook")

    assert handled is True
    mock_send_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_photo_send_failure_does_not_block_text_reply():
    from vula.api.whatsapp import _run_commerce_assistant

    mock_output = MagicMock(success=True, answer="Added Hake Fillets to your cart.", error=None,
                            media_url="https://example.com/hake.jpg")
    mock_skill = AsyncMock(return_value=mock_output)

    with (
        patch("vula.commerce.service", _commerce_service_mock()),
        patch("core.skills.loader.get_skill", return_value=mock_skill),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send_reply,
        patch("vula.api.whatsapp._resolve_wa", new=AsyncMock(side_effect=Exception("no creds"))),
    ):
        handled = await _run_commerce_assistant("27821234567", "add hake fillets", "off-the-hook")

    assert handled is True


# ── Orchestration memory bridge: knowledge-mode (HRM) turns also land in
# commerce_conversation_messages under the same admin:{phone} session-key commerce uses ────

@pytest.mark.asyncio
async def test_knowledge_mode_rag_turn_mirrors_into_commerce_history_under_admin_session_key():
    """A knowledge-mode tenant's staff/admin turn (the 'admin and staff get full RAG' branch
    of _handle_message) should also be written into commerce_conversation_messages under the
    same admin:{phone} session-key convention _run_commerce_admin already uses — so a tenant
    who later gets both modes bridged isn't starting from a context vacuum. This must never
    change what the user actually sees (the KB reply itself), only add a mirrored copy."""
    from vula.api.whatsapp import _handle_message

    mock_history_db = MagicMock()
    mock_history_db.save = MagicMock()
    mock_history_db.format_for_prompt = MagicMock(return_value="")

    # bridge_service's ._client() is left as an auto-generated MagicMock attribute — its
    # default __iter__ yields nothing, so the sales_rep gate's team-member lookup naturally
    # finds no matches and falls through to the RAG branch, no explicit stubbing needed.
    bridge_service = _commerce_service_mock()

    with (
        patch("vula.api.whatsapp._maybe_helper_escalation_answer", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._maybe_allocate_pending_expense", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._maybe_bank_review_answer", new=AsyncMock(return_value=None)),
        patch("vula.integrations.notify.handle_preference_command", return_value=None),
        patch("vula.integrations.doc_filing.resolve_pending_document", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._active_project_for_phone", return_value=None),
        patch("vula.chat.history.get_db", return_value=mock_history_db),
        patch("vula.api.whatsapp._rag_reply", new=AsyncMock(return_value="Here's the answer.")),
        patch("vula.api.whatsapp._maybe_escalate_and_learn",
              new=AsyncMock(side_effect=lambda tid, ph, txt, reply, conf: reply)),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
        patch("vula.commerce.service", bridge_service),
    ):
        await _handle_message("27645755210", "what standards apply here?", "wamid.3",
                              route_tenant_id="digg-demo")

    # The actual reply the user sees is completely unchanged.
    mock_send.assert_called_once_with("27645755210", "Here's the answer.", tenant_id="digg-demo")

    # And it was also mirrored into commerce_conversation_messages under admin:{phone}.
    bridge_service.get_or_create_session.assert_any_call(
        "digg-demo", session_key="admin:27645755210", channel="whatsapp",
        customer_phone="27645755210")
    calls = [c.args for c in bridge_service.append_message.call_args_list]
    assert ("digg-demo", "sess-1", "user", "what standards apply here?") in calls
    assert ("digg-demo", "sess-1", "assistant", "Here's the answer.") in calls


@pytest.mark.asyncio
async def test_knowledge_mode_rag_turn_survives_bridge_failure():
    """The commerce-history mirror is best-effort — if it fails, the real KB reply must still
    be sent to the user, unaffected."""
    from vula.api.whatsapp import _handle_message

    mock_history_db = MagicMock()
    mock_history_db.save = MagicMock()
    mock_history_db.format_for_prompt = MagicMock(return_value="")

    broken_service = MagicMock()
    broken_service.get_or_create_session = AsyncMock(side_effect=Exception("db unavailable"))

    with (
        patch("vula.api.whatsapp._maybe_helper_escalation_answer", new=AsyncMock(return_value=False)),
        patch("vula.api.whatsapp._maybe_allocate_pending_expense", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._maybe_bank_review_answer", new=AsyncMock(return_value=None)),
        patch("vula.integrations.notify.handle_preference_command", return_value=None),
        patch("vula.integrations.doc_filing.resolve_pending_document", new=AsyncMock(return_value=None)),
        patch("vula.api.whatsapp._active_project_for_phone", return_value=None),
        patch("vula.chat.history.get_db", return_value=mock_history_db),
        patch("vula.api.whatsapp._rag_reply", new=AsyncMock(return_value="Here's the answer.")),
        patch("vula.api.whatsapp._maybe_escalate_and_learn",
              new=AsyncMock(side_effect=lambda tid, ph, txt, reply, conf: reply)),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
        patch("vula.commerce.service", broken_service),
    ):
        await _handle_message("27645755210", "what standards apply here?", "wamid.4",
                              route_tenant_id="digg-demo")

    mock_send.assert_called_once_with("27645755210", "Here's the answer.", tenant_id="digg-demo")
