"""_run_with_holding_message: a slow commerce_assistant turn (MAX_TOOL_ITERATIONS=6 rounds of
local-LLM tool calls) had no signal at all to the customer while it ran — WhatsApp's own
typing indicator only lasts ~25s (Meta's limit). 2026-09-11, pre-go-live brief item #6. Real
prod /metrics checked first (p95 ~1.2s on current traffic) — this is cheap insurance for the
slow tail, not a fix for an observed problem, so it's deliberately just the holding message,
not a hard-timeout/escalation-routing system."""
import asyncio

import pytest
from unittest.mock import AsyncMock, patch

from vula.api.whatsapp import _run_with_holding_message

TID = "off-the-hook"
PHONE = "27645755210"


@pytest.mark.asyncio
async def test_fast_reply_never_gets_a_holding_message():
    async def quick():
        return "the real answer"

    with patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply:
        result = await _run_with_holding_message(PHONE, TID, quick(), delay=1.0)

    assert result == "the real answer"
    reply.assert_not_called()


@pytest.mark.asyncio
async def test_slow_reply_gets_exactly_one_holding_message_then_the_real_answer():
    async def slow():
        await asyncio.sleep(0.2)
        return "the real answer, eventually"

    with patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply:
        result = await _run_with_holding_message(PHONE, TID, slow(), delay=0.05)

    assert result == "the real answer, eventually"
    reply.assert_called_once()
    assert reply.call_args[0][0] == PHONE
    assert "checking" in reply.call_args[0][1].lower()


@pytest.mark.asyncio
async def test_real_work_runs_exactly_once_even_when_slow():
    calls = {"n": 0}

    async def slow_counting():
        calls["n"] += 1
        await asyncio.sleep(0.2)
        return "done"

    with patch("vula.api.whatsapp._send_reply", new=AsyncMock()):
        await _run_with_holding_message(PHONE, TID, slow_counting(), delay=0.05)

    assert calls["n"] == 1   # never duplicated, never restarted


@pytest.mark.asyncio
async def test_holding_message_send_failure_does_not_swallow_the_real_result():
    async def slow():
        await asyncio.sleep(0.2)
        return "the real answer"

    with patch("vula.api.whatsapp._send_reply", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await _run_with_holding_message(PHONE, TID, slow(), delay=0.05)

    assert result == "the real answer"


@pytest.mark.asyncio
async def test_real_work_exception_propagates_after_a_slow_holding_message():
    async def slow_failure():
        await asyncio.sleep(0.2)
        raise ValueError("skill blew up")

    with patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as reply:
        with pytest.raises(ValueError, match="skill blew up"):
            await _run_with_holding_message(PHONE, TID, slow_failure(), delay=0.05)

    reply.assert_called_once()   # the holding message still went out before the failure
