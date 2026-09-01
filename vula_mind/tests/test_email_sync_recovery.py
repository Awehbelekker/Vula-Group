"""A mailbox that errors must be able to come back on its own.

Real incident (2026-09-01): off-the-hook's mailbox (info@offthehook.capetown) stopped syncing on
2026-08-24 with "[Errno 110] Connection timed out" and sat dark for 8 days. process_all_email_sync
selected only status == "connected", so the three-failure threshold that sets status='error' was
a ONE-WAY TRAP — the account was dropped from the loop and never retried, meaning it could not
recover even once the mail server was reachable again, and _record_sync_recovery was unreachable.

The giveaway was sync_fail_count frozen at exactly 3 (the threshold): an hourly retry over 8 days
would have shown ~190. The tenant about to take live WhatsApp orders had silently stopped
ingesting supplier invoices and customer mail.
"""
from unittest.mock import AsyncMock, patch

import pytest

from vula.email_imap import sync as email_sync


class _Q:
    def __init__(self, sink, rows):
        self.sink = sink
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.sink.setdefault("eq", []).append((col, val))
        return self

    def in_(self, col, vals):
        self.sink.setdefault("in_", []).append((col, list(vals)))
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


def _db(sink, rows):
    return type("C", (), {"table": lambda self, n: _Q(sink, rows)})()


@pytest.mark.asyncio
async def test_errored_mailbox_is_still_picked_up_for_retry():
    """The core fix: an account in 'error' must remain in the sync set."""
    sink = {}
    rows = [{"id": "a1", "tenant_id": "off-the-hook", "notify_phone": None}]
    with patch.object(email_sync, "_client", lambda: _db(sink, rows)), \
         patch.object(email_sync, "process_email_sync",
                      AsyncMock(return_value={"synced": 2})) as proc, \
         patch.object(email_sync, "backfill_followup_summaries", AsyncMock()), \
         patch.object(email_sync, "send_followup_reminders", AsyncMock()):
        total = await email_sync.process_all_email_sync()

    assert total == 2
    proc.assert_awaited_once_with("off-the-hook", "a1")
    statuses = [v for c, v in sink.get("in_", []) if c == "status"]
    assert statuses, "should filter with in_(status, ...), not eq(status, 'connected')"
    assert "error" in statuses[0], "an errored mailbox must still be retried"
    assert "connected" in statuses[0]


@pytest.mark.asyncio
async def test_connected_only_filter_is_gone():
    """Guards the exact regression: eq("status", "connected") is the one-way trap."""
    sink = {}
    with patch.object(email_sync, "_client", lambda: _db(sink, [])), \
         patch.object(email_sync, "process_email_sync", AsyncMock(return_value={"synced": 0})):
        await email_sync.process_all_email_sync()
    assert ("status", "connected") not in sink.get("eq", []), \
        "filtering to connected-only makes the error state unrecoverable"


@pytest.mark.asyncio
async def test_one_broken_mailbox_does_not_stop_the_others():
    sink = {}
    rows = [{"id": "a1", "tenant_id": "off-the-hook", "notify_phone": None},
            {"id": "a2", "tenant_id": "digg-demo", "notify_phone": None}]

    async def _side_effect(tenant_id, account_id):
        if account_id == "a1":
            raise RuntimeError("still timing out")
        return {"synced": 5}

    with patch.object(email_sync, "_client", lambda: _db(sink, rows)), \
         patch.object(email_sync, "process_email_sync", AsyncMock(side_effect=_side_effect)), \
         patch.object(email_sync, "backfill_followup_summaries", AsyncMock()), \
         patch.object(email_sync, "send_followup_reminders", AsyncMock()):
        total = await email_sync.process_all_email_sync()

    assert total == 5, "the healthy mailbox must still sync"


@pytest.mark.asyncio
async def test_recovery_clears_the_error_and_announces_it():
    """Now reachable from the loop: a mailbox coming back must clear its error state."""
    updates = {}

    class _T:
        def update(self, patch_):
            updates.update(patch_)
            return self

        def eq(self, *a, **k):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    db = type("C", (), {"table": lambda self, n: _T()})()
    with patch("vula.integrations.notify.notify_team", AsyncMock()) as notify:
        await email_sync._record_sync_recovery(
            db, "off-the-hook", "a1", "info@offthehook.capetown", was_error=True)

    assert updates["status"] == "connected"
    assert updates["sync_fail_count"] == 0
    assert updates["last_error"] is None
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeated_failures_do_not_re_nag_the_team():
    """A mailbox down for a week must not notify every hour now that we keep retrying it."""
    class _T:
        def update(self, patch_):
            return self

        def eq(self, *a, **k):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    db = type("C", (), {"table": lambda self, n: _T()})()
    with patch("vula.integrations.notify.notify_team", AsyncMock()) as notify:
        # well past the threshold — the state a long-dead mailbox sits in
        await email_sync._record_sync_failure(db, "off-the-hook", "a1", "info@x.co.za", 47, "boom")
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_threshold_crossing_still_notifies_once():
    class _T:
        def update(self, patch_):
            return self

        def eq(self, *a, **k):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    db = type("C", (), {"table": lambda self, n: _T()})()
    with patch("vula.integrations.notify.notify_team", AsyncMock()) as notify:
        await email_sync._record_sync_failure(
            db, "off-the-hook", "a1", "info@x.co.za",
            email_sync._FAIL_NOTIFY_THRESHOLD - 1, "boom")
    notify.assert_awaited_once()
