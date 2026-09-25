"""Public endpoints: Twilio webhook signature, signed unsubscribe links, menu escaping, and a
per-request reply transport instead of a global _send_reply swap (2026-09-25 review)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vula.api import email_public, menu_page, twilio_whatsapp
import vula.api.whatsapp as wa


def _twilio_app():
    app = FastAPI()
    app.include_router(twilio_whatsapp.router, prefix="/v1/twilio")
    return TestClient(app)


def test_twilio_webhook_rejects_unsigned_post(monkeypatch):
    monkeypatch.setattr(twilio_whatsapp.settings, "twilio_auth_token", "tok")
    monkeypatch.setattr(twilio_whatsapp.settings, "debug", False)
    handle = AsyncMock()
    with patch.object(wa, "_handle_message", handle):
        r = _twilio_app().post("/v1/twilio/webhook", data={"From": "whatsapp:+27820000000", "Body": "hi"})
    assert r.status_code == 403
    handle.assert_not_awaited()


def test_twilio_webhook_accepts_signed_post(monkeypatch):
    monkeypatch.setattr(twilio_whatsapp.settings, "twilio_auth_token", "tok")
    params = {"From": "whatsapp:+27820000000", "Body": "hi", "MessageSid": "SM1", "NumMedia": "0"}
    sig = twilio_whatsapp.twilio_signature("tok", "http://testserver/v1/twilio/webhook", params)
    handle = AsyncMock()
    with patch.object(wa, "_handle_message", handle):
        r = _twilio_app().post("/v1/twilio/webhook", data=params, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200
    handle.assert_awaited_once()


def test_twilio_webhook_fails_closed_without_token(monkeypatch):
    monkeypatch.setattr(twilio_whatsapp.settings, "twilio_auth_token", "")
    monkeypatch.setattr(twilio_whatsapp.settings, "debug", False)
    r = _twilio_app().post("/v1/twilio/webhook", data={"From": "whatsapp:+1", "Body": "hi"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_reply_transport_is_per_request_not_global():
    """A Twilio-routed request must not re-route a concurrent Meta reply."""
    via_twilio = AsyncMock(return_value=True)
    meta_calls = []

    async def meta_path(to):
        await asyncio.sleep(0.01)
        with patch.object(wa, "_get_tenant_wa_creds", AsyncMock(return_value=None)), \
             patch.object(wa.settings, "whatsapp_token", ""):
            meta_calls.append(await wa._send_reply(to, "hello", "t1"))

    async def twilio_path():
        tok = wa._REPLY_TRANSPORT.set(via_twilio)
        try:
            await asyncio.sleep(0.02)
            await wa._send_reply("27820000001", "hi", "t1")
        finally:
            wa._REPLY_TRANSPORT.reset(tok)

    await asyncio.gather(twilio_path(), meta_path("27820000002"))
    via_twilio.assert_awaited_once()
    assert via_twilio.await_args.args[0] == "27820000001"
    assert meta_calls == [False]  # took the Meta path (unconfigured here), not Twilio


def _unsub_app():
    app = FastAPI()
    app.include_router(email_public.router)
    return TestClient(app)


def test_signed_unsubscribe_link_is_one_click_and_unsigned_needs_a_click():
    db = MagicMock()
    with patch.object(email_public, "_client", return_value=db):
        url = email_public.unsubscribe_url("", "t1", "Jo@Example.com")
        r = _unsub_app().get(url)
        assert r.status_code == 200 and "unsubscribed" in r.text
        assert db.table.return_value.upsert.call_count == 1

        r = _unsub_app().get("/email/unsubscribe?tenant=t1&email=victim@example.com")
        assert "<form" in r.text and db.table.return_value.upsert.call_count == 1

        r = _unsub_app().post("/email/unsubscribe", data={"tenant": "t1", "email": "victim@example.com"})
        assert "unsubscribed" in r.text and db.table.return_value.upsert.call_count == 2


def test_unsubscribe_page_escapes_the_address():
    with patch.object(email_public, "_client", return_value=MagicMock()):
        r = _unsub_app().get("/email/unsubscribe?tenant=t1&email=<script>x</script>@e.com")
    assert "<script>" not in r.text


def test_menu_escape_covers_attribute_quotes():
    out = menu_page._esc('x" onerror="alert(1)')
    assert '"' not in out and "&quot;" in out
