"""vula/api/server.py — scheduler leadership task tracking.

Confirmed live on off-the-hook's real WhatsApp number 2026-09-11: the scheduler lock (see
_scheduler_leadership_loop, migration 067) flapped leadership between the two uvicorn workers
(WEB_CONCURRENCY=2) dozens of times over an afternoon with no redeploy in between. Losing
leadership only ever flipped a local bool — the tasks a prior "acquired" call had started via
asyncio.create_task were never cancelled (their handles weren't even kept), so every flap back
to "acquired" left one more permanently running, never-deduplicated copy of every periodic loop
alive, including the one that sends delivery briefings and end-of-day summaries. That produced
three real duplicate sends of each message that evening, not the two the leadership mechanism
was originally built (2026-07-16) to prevent.

These tests cover the fix: _start_scheduled_job_tasks now tracks every task it creates and
refuses to stack a second batch on top of a live one, and _stop_scheduled_job_tasks (wired into
the "leadership lost" branch of _scheduler_leadership_loop) actually cancels them.
"""
import asyncio

import pytest

import vula.api.server as srv

_LOOP_NAMES = (
    "_seed_training_on_boot", "_infra_snapshot_loop", "_recurring_invoices_loop",
    "_scheduled_campaigns_loop", "_automations_loop", "_subscriptions_loop",
    "_recurring_bills_loop", "_daily_commerce_jobs_loop", "_email_sync_loop",
    "_weekly_rates_loop", "_call_sheet_loop", "_expense_sheet_loop",
    "_daily_trial_expiry_loop", "_commerce_jobs_scheduler_loop",
    "_stale_escalation_scheduler_loop", "_stale_handoff_scheduler_loop",
    "_voice_retry_scheduler_loop",
)


async def _never_returns() -> None:
    """Stand-in for a real periodic loop: runs forever until cancelled, does no real work."""
    await asyncio.Event().wait()


@pytest.fixture(autouse=True)
def _fake_loops(monkeypatch):
    """Replace every real loop _start_scheduled_job_tasks starts with a harmless stand-in, and
    make sure no test leaks a live task into the next one."""
    for name in _LOOP_NAMES:
        monkeypatch.setattr(srv, name, _never_returns)
    yield
    srv._stop_scheduled_job_tasks()


@pytest.mark.asyncio
async def test_start_creates_one_task_per_loop():
    srv._start_scheduled_job_tasks()
    await asyncio.sleep(0)  # let the tasks actually get scheduled
    assert len(srv._scheduled_job_tasks) == len(_LOOP_NAMES)
    assert all(not t.done() for t in srv._scheduled_job_tasks)


@pytest.mark.asyncio
async def test_calling_start_again_while_live_does_not_stack_a_second_batch():
    """The exact bug: a leadership flap that re-acquires before the old batch is torn down
    must not leave two copies of every loop running."""
    srv._start_scheduled_job_tasks()
    await asyncio.sleep(0)
    first_batch = list(srv._scheduled_job_tasks)

    srv._start_scheduled_job_tasks()  # simulates re-acquiring leadership without a real stop
    await asyncio.sleep(0)

    assert srv._scheduled_job_tasks == first_batch, "must not create a duplicate set of loops"
    assert len(srv._scheduled_job_tasks) == len(_LOOP_NAMES)


@pytest.mark.asyncio
async def test_stop_cancels_every_task_and_clears_the_list():
    srv._start_scheduled_job_tasks()
    await asyncio.sleep(0)
    live = list(srv._scheduled_job_tasks)
    assert live and all(not t.done() for t in live)

    srv._stop_scheduled_job_tasks()
    await asyncio.gather(*live, return_exceptions=True)  # let cancellation fully land

    assert srv._scheduled_job_tasks == []
    assert all(t.cancelled() for t in live)


@pytest.mark.asyncio
async def test_stop_then_start_creates_a_fresh_batch():
    """A genuine stop must not permanently wedge the guard — leadership can be legitimately
    re-acquired later and must start real loops again."""
    srv._start_scheduled_job_tasks()
    await asyncio.sleep(0)
    first_batch = list(srv._scheduled_job_tasks)

    srv._stop_scheduled_job_tasks()
    await asyncio.sleep(0)

    srv._start_scheduled_job_tasks()
    await asyncio.sleep(0)

    assert len(srv._scheduled_job_tasks) == len(_LOOP_NAMES)
    assert not any(t in first_batch for t in srv._scheduled_job_tasks)
    assert all(not t.done() for t in srv._scheduled_job_tasks)


@pytest.mark.asyncio
async def test_stop_is_a_safe_noop_with_nothing_running():
    assert srv._scheduled_job_tasks == []
    srv._stop_scheduled_job_tasks()  # must not raise
    assert srv._scheduled_job_tasks == []


@pytest.mark.asyncio
async def test_leadership_loop_stops_tasks_exactly_on_losing_the_lock(monkeypatch):
    """End-to-end through _scheduler_leadership_loop itself: acquire, hold, then lose — the
    tasks started on acquisition must be gone by the time "lost" is reported, not orphaned."""
    calls = iter([True, True, False])  # acquired, held (renew ok), then a flap: lost

    async def _fake_try_acquire():
        try:
            return next(calls)
        except StopIteration:
            raise KeyboardInterrupt  # stop the infinite while True loop

    monkeypatch.setattr(srv, "_try_acquire_or_renew_scheduler_lock", _fake_try_acquire)
    monkeypatch.setattr(srv, "_SCHEDULER_RENEW_SECONDS", 0)

    with pytest.raises(KeyboardInterrupt):
        await srv._scheduler_leadership_loop()

    assert srv._scheduled_job_tasks == [], "tasks from the acquired stint must be cancelled"
