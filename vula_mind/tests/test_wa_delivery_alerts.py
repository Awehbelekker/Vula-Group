"""A proactive WhatsApp message that can't be delivered must not fail silently.

2026-09-07, measured on the first 12 messages tracked by migration 153: EIGHT failed, every one
with Meta's 131047 "Re-engagement message" — outside the 24-hour window in which free-form text
is allowed. Among them, twice over, an order alert:

    📦 Order to fulfil:  🆕 New WhatsApp order OTH-00099

Nobody was told. Two things were wrong beyond the window itself:

1. The failure ALERT went through notify_team, which is WhatsApp-only. An alert about a message
   that failed for being outside the window was itself a free-form message outside the window,
   so it failed identically — while notified_at was stamped as though someone had been told.
2. Nothing tried the one delivery method Meta exempts from the window: an approved template.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import vula.api.whatsapp as wa


# ── the alert must leave WhatsApp ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_failure_alert_goes_by_email_not_whatsapp():
    creds = {"smtp_host": "smtp.test", "email": "owner@test.co.za"}
    with patch("vula.email_imap.credentials.get_email_creds", lambda t, a=None: creds), \
         patch("vula.email_imap.service.send",
               AsyncMock(return_value={"sent": True})) as send:
        ok = await wa._alert_off_whatsapp("off-the-hook", "Not delivered", "an order alert")
    assert ok is True
    assert send.await_args[0][1] == "owner@test.co.za"
    assert "Not delivered" in send.await_args[0][2]


@pytest.mark.asyncio
async def test_no_email_channel_reports_false_so_the_alert_is_retried():
    """Returning False keeps notified_at unstamped, so the next delivery callback tries again
    rather than the alert being silently dropped — which is exactly what happened before."""
    with patch("vula.email_imap.credentials.get_email_creds", lambda t, a=None: None):
        assert await wa._alert_off_whatsapp("off-the-hook", "x", "y") is False


@pytest.mark.asyncio
async def test_a_broken_mailbox_does_not_raise_into_the_send_path():
    creds = {"smtp_host": "smtp.test", "email": "owner@test.co.za"}
    with patch("vula.email_imap.credentials.get_email_creds", lambda t, a=None: creds), \
         patch("vula.email_imap.service.send", AsyncMock(side_effect=RuntimeError("smtp down"))):
        assert await wa._alert_off_whatsapp("off-the-hook", "x", "y") is False


@pytest.mark.asyncio
async def test_notified_at_is_only_stamped_when_someone_was_actually_told():
    updates = []

    class _Q:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def update(self, p):
            updates.append(p)
            return self
        def execute(self):
            return MagicMock(data=[{
                "id": "r1", "tenant_id": "off-the-hook", "to_phone": "27821112222",
                "status": "accepted", "body_preview": "Order to fulfil", "notified_at": None}])

    db = MagicMock(table=lambda n: _Q())
    with patch("vula.commerce.service._client", lambda: db), \
         patch.object(wa, "_alert_off_whatsapp", AsyncMock(return_value=False)):
        await wa._record_outbound_status("wamid-1", "failed", "131047 Re-engagement message")
    assert {"notified_at": "now()"} not in updates, "nobody was told — do not claim they were"


# ── the template fallback ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_configured_template_means_nothing_is_substituted():
    with patch.object(wa.settings, "whatsapp_notify_template", ""):
        assert await wa._send_notify_template({"phone_id": "p", "token": "t"},
                                              "27821112222", "off-the-hook") is False


@pytest.mark.asyncio
async def test_the_template_carries_the_business_name_as_its_variable():
    posted = {}

    class _Resp:
        def raise_for_status(self): return None
        def json(self): return {"messages": [{"id": "wamid-tpl"}]}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            posted.update(json or {})
            return _Resp()

    with patch.object(wa.settings, "whatsapp_notify_template", "vula_notification"), \
         patch.object(wa.settings, "whatsapp_notify_template_lang", "en"), \
         patch.object(wa.httpx, "AsyncClient", lambda **k: _Client()), \
         patch("vula.api.tenants.get_config", lambda t: {"display_name": "Off the Hook"}), \
         patch.object(wa, "_record_outbound", lambda *a, **k: None):
        ok = await wa._send_notify_template({"phone_id": "p", "token": "t"},
                                            "27821112222", "off-the-hook")
    assert ok is True
    assert posted["type"] == "template"
    assert posted["template"]["name"] == "vula_notification"
    params = posted["template"]["components"][0]["parameters"]
    assert params[0]["text"] == "Off the Hook"


def test_the_fallback_only_fires_on_the_24_hour_window_error():
    """131047 is the window. 131026 (number can't receive) and 131030 (not on the allow list)
    are not fixed by a template, and retrying them would just burn a template send."""
    import inspect
    src = inspect.getsource(wa._send_reply)
    assert '"131047" in (body or "")' in src
    assert "_send_notify_template" in src


# ── the fulfilment ticket must not depend on one channel ────────────────────────

@pytest.mark.asyncio
async def test_a_failed_whatsapp_ticket_falls_through_to_email():
    """off-the-hook's OTH-00099 was dispatched twice on 2026-09-06 and BOTH sends failed with
    131047. dispatch_order discarded the return value, so an undelivered ticket looked exactly
    like a delivered one. A fulfilment ticket is the most time-critical message the platform
    sends; it must not die with one channel."""
    from vula.commerce import order_workflow as ow
    cfg = {"dispatch_channel": "whatsapp", "fulfillment_whatsapp": "27821112222",
           "fulfillment_email": "kitchen@test.co.za"}
    with patch.object(ow, "get_order_settings", lambda t: cfg), \
         patch("vula.api.whatsapp._send_reply", AsyncMock(return_value=False)), \
         patch("vula.api.email._send", AsyncMock(return_value=None)) as email:
        await ow.dispatch_order("off-the-hook", "o1", "2 x Hake", customer_name="Thabo")
    email.assert_awaited_once()
    assert "didn't reach you" in email.await_args[0][1]


@pytest.mark.asyncio
async def test_a_delivered_whatsapp_ticket_does_not_also_email():
    from vula.commerce import order_workflow as ow
    cfg = {"dispatch_channel": "whatsapp", "fulfillment_whatsapp": "27821112222",
           "fulfillment_email": "kitchen@test.co.za"}
    with patch.object(ow, "get_order_settings", lambda t: cfg), \
         patch("vula.api.whatsapp._send_reply", AsyncMock(return_value=True)), \
         patch("vula.api.email._send", AsyncMock()) as email:
        await ow.dispatch_order("off-the-hook", "o1", "2 x Hake")
    email.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_whatsapp_falls_back_to_the_owner_email_when_no_fulfilment_email(caplog):
    """WhatsApp-only config, fulfillment_email None — the ticket must still reach the owner's
    own email (vula_team_members) rather than being lost."""
    from vula.commerce import order_workflow as ow
    cfg = {"dispatch_channel": "whatsapp", "fulfillment_whatsapp": "27821112222",
           "fulfillment_email": None}

    class _Tbl:
        def select(self, *a): return self
        def eq(self, *a): return self
        def execute(self): return type("R", (), {"data": [{"email": "owner@oth.co.za", "role": "owner"}]})()

    with patch.object(ow, "get_order_settings", lambda t: cfg), \
         patch("vula.api.whatsapp._send_reply", AsyncMock(return_value=False)), \
         patch("vula.commerce.service._client", lambda: type("C", (), {"table": lambda s, n: _Tbl()})()), \
         patch("vula.api.email._send", AsyncMock(return_value=None)) as email:
        await ow.dispatch_order("off-the-hook", "OTH-00099", "2 x Hake")
    email.assert_awaited_once()
    assert email.await_args[0][0] == "owner@oth.co.za"


@pytest.mark.asyncio
async def test_no_channel_and_no_email_is_logged_loudly(caplog):
    """Truly nowhere to go — no fulfilment email, no team-member email — must be loud, not silent."""
    from vula.commerce import order_workflow as ow
    cfg = {"dispatch_channel": "whatsapp", "fulfillment_whatsapp": "27821112222",
           "fulfillment_email": None}

    class _Tbl:
        def select(self, *a): return self
        def eq(self, *a): return self
        def execute(self): return type("R", (), {"data": []})()

    with patch.object(ow, "get_order_settings", lambda t: cfg), \
         patch("vula.api.whatsapp._send_reply", AsyncMock(return_value=False)), \
         patch("vula.commerce.service._client", lambda: type("C", (), {"table": lambda s, n: _Tbl()})()), \
         caplog.at_level("ERROR"):
        await ow.dispatch_order("off-the-hook", "OTH-00099", "2 x Hake")
    assert "ORDER TICKET UNDELIVERED" in caplog.text
    assert "OTH-00099" in caplog.text


def test_the_caller_is_still_told_the_message_did_not_go():
    """The template is a nudge, not the message. A caller that checks the return value must not
    be told its actual content was delivered."""
    import inspect
    src = inspect.getsource(wa._send_reply)
    idx = src.find("_send_notify_template")
    assert "return False" in src[idx:idx + 200]
