"""WhatsApp account webhook fields (31 Oct 2026).

account_update, message_template_status_update, phone_number_quality_update and
template_category_update reached the webhook and were dropped: a rejected or paused template, a
quality downgrade or a banned account went unnoticed until sends started failing. They now go to
vula/api/whatsapp_account_events.py: a structured log line (no customer content) for every event,
and an operator alert (platform_support.alert_operator) for the bad ones. Never raises, and never
falls through to the messages path.
"""
import json
from types import SimpleNamespace

import pytest

from config import settings
from vula.api import whatsapp
from vula.api import whatsapp_account_events as events


@pytest.fixture()
def alerts(monkeypatch):
    sent = []

    async def _alert(text, log_text=True):
        sent.append(text)
        return True

    monkeypatch.setattr("vula.integrations.platform_support.alert_operator", _alert)
    monkeypatch.setattr(events, "_tenant_for_waba", lambda waba_id: "off-the-hook" if waba_id == "W1" else None)
    events._SEEN.clear()
    return sent


# ── templates ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["REJECTED", "PAUSED", "DISABLED"])
async def test_a_bad_template_status_alerts_the_operator(alerts, event):
    out = await events.handle("message_template_status_update", "W1", {
        "event": event, "message_template_id": 42, "message_template_name": "order_ready",
        "message_template_language": "en", "reason": "INCORRECT_CATEGORY"})
    assert out == "alerted"
    assert len(alerts) == 1
    assert "order_ready" in alerts[0] and event in alerts[0] and "off-the-hook" in alerts[0]
    assert "INCORRECT_CATEGORY" in alerts[0]


@pytest.mark.asyncio
async def test_an_approved_template_is_only_logged(alerts, caplog):
    caplog.set_level("INFO", logger=events.log.name)
    out = await events.handle("message_template_status_update", "W1", {
        "event": "APPROVED", "message_template_name": "order_ready", "message_template_language": "en"})
    assert out == "logged" and alerts == []
    assert any("message_template_status_update" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_template_category_change_is_logged(alerts):
    out = await events.handle("template_category_update", "W1", {
        "message_template_name": "order_ready", "previous_category": "UTILITY", "new_category": "MARKETING"})
    assert out == "logged" and alerts == []


# ── quality ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    {"display_phone_number": "27713128891", "event": "DOWNGRADE", "current_limit": "TIER_250"},
    {"display_phone_number": "27713128891", "event": "FLAGGED", "current_limit": "TIER_1K"},
    {"display_phone_number": "27713128891", "event": "UNCHANGED", "quality_rating": "RED"},
    {"display_phone_number": "27713128891", "quality_rating": "YELLOW"},
])
async def test_a_quality_drop_alerts_the_operator(alerts, value):
    assert await events.handle("phone_number_quality_update", "W1", value) == "alerted"
    assert len(alerts) == 1 and "27713128891" in alerts[0]


@pytest.mark.asyncio
async def test_a_quality_upgrade_is_only_logged(alerts):
    out = await events.handle("phone_number_quality_update", "W1", {
        "display_phone_number": "27713128891", "event": "UPGRADE", "current_limit": "TIER_10K",
        "quality_rating": "GREEN"})
    assert out == "logged" and alerts == []


# ── account ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    {"event": "DISABLED_UPDATE", "ban_info": {"waba_ban_state": "DISABLE", "waba_ban_date": "2026-10-31"}},
    {"event": "ACCOUNT_RESTRICTION", "restriction_info": [{"restriction_type": "RESTRICTED_ADD_PHONE_NUMBER_ACTION"}]},
    {"event": "BANNED"},
    {"event": "ACCOUNT_VIOLATION", "violation_info": {"violation_type": "SCAM"}},
])
async def test_a_banned_restricted_or_disabled_account_alerts(alerts, value):
    assert await events.handle("account_update", "W1", value) == "alerted"
    assert len(alerts) == 1 and value["event"] in alerts[0]


@pytest.mark.asyncio
async def test_a_verified_account_is_only_logged(alerts):
    out = await events.handle("account_update", "W1", {"event": "VERIFIED_ACCOUNT", "phone_number": "27713128891"})
    assert out == "logged" and alerts == []


@pytest.mark.asyncio
async def test_a_redelivered_event_alerts_once(alerts):
    value = {"event": "PAUSED", "message_template_id": 7, "message_template_name": "promo"}
    await events.handle("message_template_status_update", "W1", value)
    assert await events.handle("message_template_status_update", "W1", value) == "duplicate"
    assert len(alerts) == 1


# ── fail-open ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_never_raises(monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("Meta down")

    monkeypatch.setattr("vula.integrations.platform_support.alert_operator", _boom)
    monkeypatch.setattr(events, "_tenant_for_waba", lambda w: (_ for _ in ()).throw(RuntimeError("db down")))
    events._SEEN.clear()
    assert await events.handle("account_update", "W1", {"event": "BANNED"}) == "error"
    for bad in (None, [], "x", {"event": None}, {"ban_info": "?"}):
        await events.handle("account_update", "W1", bad)          # nothing raises
    await events.handle("phone_number_quality_update", None, None)


def test_the_tenant_lookup_fails_open(monkeypatch):
    def _broken():
        raise RuntimeError("no db")

    monkeypatch.setattr("vula.commerce.service._client", _broken)
    assert events._tenant_for_waba("W1") is None
    assert events._tenant_for_waba("") is None


# ── the webhook ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["account_update", "message_template_status_update",
                                   "phone_number_quality_update", "template_category_update"])
async def test_the_webhook_hands_these_fields_over_and_skips_the_messages_path(monkeypatch, field):
    monkeypatch.setattr(settings, "vula_fb_app_secret", "")
    monkeypatch.setattr(settings, "debug", True)
    started = []
    monkeypatch.setattr(whatsapp, "_run_bg", lambda coro, *, label, **_k: (started.append(label), coro.close()))

    def _messages_path(*_a, **_k):
        raise AssertionError("fell through to the messages path")

    monkeypatch.setattr(whatsapp, "_durable_dedup_claim", _messages_path)
    monkeypatch.setattr(whatsapp, "_resolve_number_route", _messages_path)
    body = {"entry": [{"id": "W1", "changes": [{"field": field, "value": {
        "event": "REJECTED", "messages": [{"from": "27820000000", "id": "wamid.X", "type": "text"}]}}]}]}

    async def _body():
        return json.dumps(body).encode()

    assert await whatsapp.receive_message(SimpleNamespace(body=_body), None) == {"status": "ok"}
    assert started == [f"account_event_{field}"]
