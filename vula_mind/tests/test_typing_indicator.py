"""Read receipts + typing indicator — the dead-air fix.

2026-09-01, ahead of off-the-hook going live for real WhatsApp orders: a customer got no blue
ticks and no typing dots between sending a message and the reply landing, which real telemetry
shows can be many seconds (voice transcription alone measured 3–10s before the LLM even runs).
Request shape verified against Meta's own docs (/docs/whatsapp/cloud-api/typing-indicators):
a single POST carries both status:read and typing_indicator.
"""
from unittest.mock import AsyncMock, patch

import pytest

import vula.api.whatsapp as wa


class _Client:
    def __init__(self, sink, exc=None):
        self._sink = sink
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        if self._exc:
            raise self._exc
        self._sink.append({"url": url, "headers": dict(headers or {}), "json": json})
        return type("R", (), {"status_code": 200, "raise_for_status": lambda self: None})()


CREDS = {"token": "tok-123", "phone_id": "PHONE-1"}


@pytest.mark.asyncio
async def test_sends_read_status_and_typing_in_one_post():
    sink = []
    with patch.object(wa, "_get_tenant_wa_creds", AsyncMock(return_value=CREDS)), \
         patch.object(wa.httpx, "AsyncClient", lambda **k: _Client(sink)):
        await wa._mark_read_and_typing("wamid.ABC", "off-the-hook")

    assert len(sink) == 1
    body = sink[0]["json"]
    assert body["messaging_product"] == "whatsapp"
    assert body["status"] == "read"
    assert body["message_id"] == "wamid.ABC"
    assert body["typing_indicator"] == {"type": "text"}
    assert sink[0]["url"] == "https://graph.facebook.com/v19.0/PHONE-1/messages"
    assert sink[0]["headers"]["Authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_no_message_id_is_a_noop():
    sink = []
    with patch.object(wa, "_get_tenant_wa_creds", AsyncMock(return_value=CREDS)), \
         patch.object(wa.httpx, "AsyncClient", lambda **k: _Client(sink)):
        await wa._mark_read_and_typing("", "off-the-hook")
    assert sink == []


@pytest.mark.asyncio
async def test_no_credentials_is_a_noop_not_a_crash():
    sink = []
    with patch.object(wa, "_get_tenant_wa_creds", AsyncMock(return_value=None)), \
         patch.object(wa.settings, "whatsapp_token", ""), \
         patch.object(wa.settings, "whatsapp_phone_id", ""), \
         patch.object(wa.httpx, "AsyncClient", lambda **k: _Client(sink)):
        await wa._mark_read_and_typing("wamid.ABC", "off-the-hook")
    assert sink == []


@pytest.mark.asyncio
async def test_falls_back_to_global_env_credentials():
    sink = []
    with patch.object(wa, "_get_tenant_wa_creds", AsyncMock(return_value=None)), \
         patch.object(wa.settings, "whatsapp_token", "env-tok"), \
         patch.object(wa.settings, "whatsapp_phone_id", "ENV-PHONE"), \
         patch.object(wa.httpx, "AsyncClient", lambda **k: _Client(sink)):
        await wa._mark_read_and_typing("wamid.ABC", "off-the-hook")
    assert sink[0]["url"].endswith("/ENV-PHONE/messages")


@pytest.mark.asyncio
async def test_failure_never_propagates_and_cannot_break_an_order():
    """A cosmetic nicety must not be able to take down a real order."""
    with patch.object(wa, "_get_tenant_wa_creds", AsyncMock(return_value=CREDS)), \
         patch.object(wa.httpx, "AsyncClient",
                      lambda **k: _Client([], exc=RuntimeError("meta down"))):
        assert await wa._mark_read_and_typing("wamid.ABC", "off-the-hook") is None
