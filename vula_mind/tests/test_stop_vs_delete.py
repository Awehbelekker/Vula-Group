"""STOP opts out (and pauses repeat orders) without erasing anything; DELETE erases; START
opts back in (2026-09-25 review — STOP used to run the full data deletion)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import vula.api.whatsapp as wa


def test_keywords_are_separate():
    for t in ("STOP", "stop", "Unsubscribe", "opt out", "opt-out", "Stop."):
        assert wa._OPTOUT_RE.match(t) and not wa._DELETE_RE.match(t), t
    for t in ("DELETE", "delete my data", "erase my data"):
        assert wa._DELETE_RE.match(t) and not wa._OPTOUT_RE.match(t), t
    for t in ("START", "opt in", "unstop"):
        assert wa._OPTIN_RE.match(t), t
    # Sentences that merely contain the word never trigger anything.
    for t in ("stop sending me the old price list", "please don't delete my order", "start my order"):
        assert not (wa._OPTOUT_RE.match(t) or wa._DELETE_RE.match(t) or wa._OPTIN_RE.match(t)), t


@pytest.mark.asyncio
async def test_stop_suppresses_and_pauses_but_erases_nothing():
    opt_out = MagicMock()
    pause = AsyncMock(return_value=1)
    send = AsyncMock(return_value=True)
    delete = AsyncMock()
    with patch("vula.api.commerce.record_opt_out", opt_out), \
         patch("vula.commerce.subscriptions.pause_for_phone", pause), \
         patch.object(wa, "_send_reply", send), \
         patch.object(wa, "_handle_data_deletion", delete):
        await wa._handle_opt_out("27820000000", "off-the-hook")
    opt_out.assert_called_once()
    pause.assert_awaited_once_with("off-the-hook", "27820000000")
    delete.assert_not_awaited()
    msg = send.await_args.args[1]
    assert "START" in msg and "DELETE" in msg and "paused" in msg


@pytest.mark.asyncio
async def test_start_opts_back_in():
    opt_in = MagicMock()
    with patch("vula.api.commerce.record_opt_in", opt_in), \
         patch.object(wa, "_send_reply", AsyncMock(return_value=True)):
        await wa._handle_opt_in("27820000000", "off-the-hook")
    opt_in.assert_called_once_with("off-the-hook", "27820000000", source="start_keyword")


@pytest.mark.asyncio
async def test_pause_for_phone_matches_stored_formats():
    from vula.commerce import subscriptions

    q = MagicMock()
    q.update.return_value = q
    q.eq.return_value = q
    q.in_.return_value = q
    q.execute.return_value = MagicMock(data=[{"id": "s1"}])
    db = MagicMock()
    db.table.return_value = q
    with patch.object(subscriptions, "_client", return_value=db):
        n = await subscriptions.pause_for_phone("t1", "+27 82 000 0000")
    assert n == 1
    variants = set(q.in_.call_args.args[1])
    assert {"27820000000", "+27820000000", "0820000000"} <= variants


@pytest.mark.asyncio
async def test_automation_never_messages_an_opted_out_customer():
    from vula.commerce import automations
    auto = {"id": "a1", "action_type": "whatsapp_customer", "action_config": {"message": "Hi {{name}}"}}
    send = AsyncMock(return_value=True)
    with patch("vula.api.commerce._suppressed_phones", return_value={"27820000000"}), \
         patch("vula.api.whatsapp._send_reply", send):
        ok = await automations._run_action("t1", auto, {"customer_phone": "082 000 0000", "name": "Jo"})
    assert ok is False
    send.assert_not_awaited()
    with patch("vula.api.commerce._suppressed_phones", return_value=set()), \
         patch("vula.api.whatsapp._send_reply", send):
        assert await automations._run_action("t1", auto, {"customer_phone": "0820000000", "name": "Jo"})
