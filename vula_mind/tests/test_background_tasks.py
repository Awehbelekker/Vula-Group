"""Tests for the generic background-task primitive (vula/commerce/background_tasks.py)."""
import asyncio

import pytest

from vula.commerce.background_tasks import run_background


async def _drain():
    # Let the fire-and-forget task created by run_background actually run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_run_background_calls_notify_builder_with_result():
    notified = {}

    async def coro():
        return {"ok": True, "value": 42}

    async def notify_builder(result):
        notified["result"] = result

    run_background("tid", "test_job", coro(), notify_builder=notify_builder)
    await _drain()

    assert notified["result"] == {"ok": True, "value": 42}


@pytest.mark.asyncio
async def test_run_background_no_notify_builder_does_not_raise():
    async def coro():
        return "done"

    run_background("tid", "test_job", coro())
    await _drain()  # should not raise even with no notify_builder


@pytest.mark.asyncio
async def test_run_background_failure_never_propagates_and_calls_failure_notify():
    captured = {}

    async def coro():
        raise ValueError("boom")

    async def notify_builder(result):
        captured["notify_builder_called"] = True

    async def notify_on_failure(exc):
        captured["exc"] = str(exc)

    run_background("tid", "test_job", coro(), notify_builder=notify_builder,
                    notify_on_failure=notify_on_failure)
    await _drain()

    assert captured.get("exc") == "boom"
    assert "notify_builder_called" not in captured


@pytest.mark.asyncio
async def test_run_background_failure_notify_error_is_swallowed():
    async def coro():
        raise ValueError("boom")

    async def notify_on_failure(exc):
        raise RuntimeError("notify itself failed")

    # Should not raise despite notify_on_failure itself raising.
    run_background("tid", "test_job", coro(), notify_on_failure=notify_on_failure)
    await _drain()


@pytest.mark.asyncio
async def test_run_background_notify_builder_error_is_swallowed():
    async def coro():
        return "result"

    async def notify_builder(result):
        raise RuntimeError("notify itself failed")

    # Should not raise despite notify_builder itself raising.
    run_background("tid", "test_job", coro(), notify_builder=notify_builder)
    await _drain()
