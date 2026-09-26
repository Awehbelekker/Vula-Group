"""Per-sender flood guard on the WhatsApp webhook (settings.wa_sender_rate_limit)."""
from unittest.mock import AsyncMock, patch

from vula.api import whatsapp as wa


def test_16th_message_in_a_minute_is_over_the_limit(monkeypatch):
    monkeypatch.setattr(wa.settings, "wa_sender_rate_limit", 15)
    results = [wa._sender_over_limit("t1", "27820000001", now=100.0 + i) for i in range(16)]
    assert results[:15] == [False] * 15 and results[15] is True


def test_window_slides_and_senders_are_separate(monkeypatch):
    monkeypatch.setattr(wa.settings, "wa_sender_rate_limit", 2)
    assert not wa._sender_over_limit("t1", "a", now=0.0)
    assert not wa._sender_over_limit("t1", "a", now=1.0)
    assert wa._sender_over_limit("t1", "a", now=2.0)
    assert not wa._sender_over_limit("t1", "b", now=2.0)       # another customer
    assert not wa._sender_over_limit("t2", "a", now=2.0)       # same number, another business
    assert not wa._sender_over_limit("t1", "a", now=70.0)      # a minute later


def test_zero_disables(monkeypatch):
    monkeypatch.setattr(wa.settings, "wa_sender_rate_limit", 0)
    assert not any(wa._sender_over_limit("t1", "a", now=float(i)) for i in range(50))


def test_one_warning_per_window():
    assert wa._should_warn_sender("t1", "a", now=0.0)
    assert not wa._should_warn_sender("t1", "a", now=30.0)
    assert wa._should_warn_sender("t1", "a", now=61.0)


def _payload(i, run, phone="27820000009"):
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "pn1"},
        "messages": [{"from": phone, "id": f"wamid.{run}.{i}", "type": "text", "text": {"body": f"hi {i}"}}],
    }}]}]}


def _post_many(n, *, owner=False):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import uuid
    run = uuid.uuid4().hex   # message ids must be new, or the webhook's own dedup skips them
    app = FastAPI()
    app.include_router(wa.router, prefix="/v1/whatsapp")
    handled = AsyncMock()
    sent = AsyncMock(return_value=True)
    with patch.object(wa.settings, "wa_sender_rate_limit", 3), \
         patch.object(wa.settings, "vula_fb_app_secret", ""), patch.object(wa.settings, "debug", True), \
         patch.object(wa, "_resolve_number_route", return_value=("t1", "commerce")), \
         patch("vula.api.tenants.is_active", return_value=True), \
         patch.object(wa, "_is_tenant_owner", return_value=owner), \
         patch.object(wa, "_mark_read_and_typing", new=AsyncMock()), \
         patch.object(wa, "_handle_commerce_message", new=handled), \
         patch.object(wa, "_send_reply", new=sent), \
         patch("vula.commerce.service._client", side_effect=RuntimeError("no db")):
        c = TestClient(app)
        for i in range(n):
            assert c.post("/v1/whatsapp/webhook", json=_payload(i, run)).status_code == 200
    return handled, sent


def test_webhook_stops_running_the_assistant_and_warns_once():
    handled, sent = _post_many(6)
    assert handled.call_count == 3
    assert sent.call_count == 1 and "one message" in sent.call_args[0][1]


def test_webhook_never_limits_the_team():
    handled, sent = _post_many(6, owner=True)
    assert handled.call_count == 6 and sent.call_count == 0
