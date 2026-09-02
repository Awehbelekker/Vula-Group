"""A WhatsApp reply that never arrives must not be silent.

2026-09-02: a failed send was logged to Railway and nothing else — no telemetry, no human told.
71 call sites send replies and only 4 check the return value, so 67 paths were send-and-forget.
On the main client-facing channel that means a customer's answer can simply never arrive, and
the business only finds out if the customer chases. The likeliest real cause is Meta's 24-hour
customer-service window (#131047): outside it, only an approved template can be delivered.
"""
from unittest.mock import AsyncMock, patch

import pytest

import vula.api.whatsapp as wa

WINDOW_ERR = '{"error":{"message":"Re-engagement message","code":131047}}'


@pytest.fixture(autouse=True)
def _clear_cooldown():
    wa._send_failure_notified.clear()
    yield
    wa._send_failure_notified.clear()


@pytest.mark.asyncio
async def test_a_failed_send_is_recorded_as_telemetry():
    with patch("core.reasoning_telemetry.emit") as emit, \
         patch("vula.integrations.notify.notify_team", AsyncMock()):
        await wa._record_send_failure("27786537562", "off-the-hook", WINDOW_ERR, "400")
    assert emit.called
    kw = emit.call_args.kwargs
    assert kw["system"] == "vula-wa-send"
    assert kw["outcome"] == "failed"
    assert kw["extra"]["code"] == "131047"


@pytest.mark.asyncio
async def test_the_24h_window_failure_tells_the_team_the_customer_did_not_get_it():
    with patch("core.reasoning_telemetry.emit"), \
         patch("vula.integrations.notify.notify_team", AsyncMock()) as notify:
        await wa._record_send_failure("27786537562", "off-the-hook", WINDOW_ERR, "400")
    msg = notify.await_args[0][2]
    assert "didn't go through" in msg
    assert "24-hour reply window" in msg
    assert "haven't received it" in msg


@pytest.mark.asyncio
async def test_a_dead_number_does_not_alert_on_every_attempt():
    """A number that has gone dead would otherwise alert on every single reply."""
    with patch("core.reasoning_telemetry.emit"), \
         patch("vula.integrations.notify.notify_team", AsyncMock()) as notify:
        for _ in range(5):
            await wa._record_send_failure("27786537562", "off-the-hook", WINDOW_ERR, "400")
    assert notify.await_count == 1, "should notify once per number per cooldown"


@pytest.mark.asyncio
async def test_different_numbers_each_get_their_own_alert():
    with patch("core.reasoning_telemetry.emit"), \
         patch("vula.integrations.notify.notify_team", AsyncMock()) as notify:
        await wa._record_send_failure("27111111111", "off-the-hook", WINDOW_ERR, "400")
        await wa._record_send_failure("27222222222", "off-the-hook", WINDOW_ERR, "400")
    assert notify.await_count == 2


@pytest.mark.asyncio
async def test_an_unknown_error_is_measured_but_does_not_nag():
    """Telemetry always; a human is only told when there's something they can act on."""
    with patch("core.reasoning_telemetry.emit") as emit, \
         patch("vula.integrations.notify.notify_team", AsyncMock()) as notify:
        await wa._record_send_failure("27786537562", "off-the-hook",
                                      '{"error":{"code":999999}}', "boom")
    assert emit.called
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_tenant_still_records_telemetry_without_notifying():
    with patch("core.reasoning_telemetry.emit") as emit, \
         patch("vula.integrations.notify.notify_team", AsyncMock()) as notify:
        await wa._record_send_failure("27786537562", "", WINDOW_ERR, "400")
    assert emit.called
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failure_to_notify_never_escapes_the_send_path():
    with patch("core.reasoning_telemetry.emit"), \
         patch("vula.integrations.notify.notify_team",
               AsyncMock(side_effect=RuntimeError("notify is down"))):
        await wa._record_send_failure("27786537562", "off-the-hook", WINDOW_ERR, "400")


@pytest.mark.asyncio
async def test_telemetry_failing_does_not_break_the_send_path():
    with patch("core.reasoning_telemetry.emit", side_effect=RuntimeError("sink down")), \
         patch("vula.integrations.notify.notify_team", AsyncMock()):
        await wa._record_send_failure("27786537562", "off-the-hook", WINDOW_ERR, "400")


@pytest.mark.parametrize("code,fragment", [
    ("131047", "24-hour"),
    ("131026", "can't receive"),
    ("131030", "allowed-recipients"),
    ("131031", "restricted"),
])
def test_the_codes_a_human_can_act_on_are_explained_in_plain_language(code, fragment):
    assert fragment in wa._SEND_FAILURE_MEANING[code]
