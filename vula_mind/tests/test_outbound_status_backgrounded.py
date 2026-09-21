"""Test for backgrounding the outbound-status-callback handler in the WhatsApp webhook —
confirmed live 2026-09-20: this was previously awaited inline, and on a 'failed' status it can
send an off-WhatsApp alert email (_alert_off_whatsapp) whose SMTP send was seen live blocking
this exact webhook handler for ~41s (digg-demo) — well past Meta's ~15-20s webhook timeout,
risking a retry storm the same way the document-ingest path was already fixed for (see
_run_bg's own docstring). _record_outbound_status must now be fired via _run_bg, never awaited
directly in the request path."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vula.api.server import app

client = TestClient(app)


def _wa_status_payload(wamid: str = "wamid.status1") -> dict:
    return {
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "123"},
            "statuses": [{"id": wamid, "status": "failed",
                         "errors": [{"title": "Re-engagement message"}]}],
        }}]}]
    }


def test_outbound_status_callback_fired_via_run_bg_not_awaited_inline():
    with (
        patch("vula.api.whatsapp._run_bg") as mock_run_bg,
        patch("vula.commerce.service._client", return_value=MagicMock()),
        patch("vula.api.commerce.record_message_status"),
    ):
        resp = client.post("/v1/whatsapp/webhook", json=_wa_status_payload())

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    mock_run_bg.assert_called_once()
    _, kwargs = mock_run_bg.call_args
    assert kwargs.get("label") == "record_outbound_status"


@pytest.mark.asyncio
async def test_webhook_returns_fast_even_if_the_backgrounded_handler_would_hang():
    """The actual regression: a slow _record_outbound_status (e.g. a hung SMTP alert send)
    must never delay the webhook's 200 response, since it's now fire-and-forget."""
    import asyncio

    async def _never_finishes(*a, **k):
        await asyncio.sleep(30)

    with (
        patch("vula.api.whatsapp._record_outbound_status", side_effect=_never_finishes),
        patch("vula.commerce.service._client", return_value=MagicMock()),
        patch("vula.api.commerce.record_message_status"),
    ):
        import time
        start = time.monotonic()
        resp = client.post("/v1/whatsapp/webhook", json=_wa_status_payload())
        elapsed = time.monotonic() - start

    assert resp.status_code == 200
    assert elapsed < 5.0  # would have been ~30s+ before backgrounding
