"""Tests for _record_outbound_status's never-downgrade guard (vula/api/whatsapp.py) — 2026-09-08:
_WA_STATUS_RANK gave 'sent' and 'failed' the same rank (1), so the guard (`rank(new) < rank(cur)`)
never blocked a stale/out-of-order 'sent' webhook from overwriting an already-recorded 'failed'
status, silently erasing a real delivery failure the team had already been alerted to.
"""
from unittest.mock import MagicMock, patch

import pytest

import vula.api.whatsapp as wa


def _db_with(row):
    updates = []

    class _Q:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def update(self, p):
            updates.append(p)
            return self
        def execute(self):
            return MagicMock(data=[row] if row else [])
    return MagicMock(table=lambda n: _Q()), updates


ROW = {"id": "r1", "tenant_id": "off-the-hook", "to_phone": "27821112222",
       "body_preview": "x", "notified_at": "already"}  # already notified — no alert re-fires


@pytest.mark.asyncio
async def test_a_stale_sent_never_overwrites_an_already_recorded_failed():
    db, updates = _db_with(dict(ROW, status="failed"))
    with patch("vula.commerce.service._client", lambda: db):
        await wa._record_outbound_status("wamid-1", "sent")
    assert updates == [], "a 'sent' arriving after 'failed' must be treated as stale, not applied"


@pytest.mark.asyncio
async def test_a_genuine_failed_still_overwrites_sent():
    db, updates = _db_with(dict(ROW, status="sent"))
    with patch("vula.commerce.service._client", lambda: db):
        await wa._record_outbound_status("wamid-1", "failed", "boom")
    assert updates and updates[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_delivered_after_failed_is_still_blocked_by_the_normal_rank_check():
    """Not a regression from this fix — 'failed' still isn't the highest rank, so a stale
    'delivered'/'read' arriving after it stays subject to the ordinary rank comparison, not
    treated as unconditionally blocked just because the special case exists."""
    db, updates = _db_with(dict(ROW, status="failed"))
    with patch("vula.commerce.service._client", lambda: db):
        await wa._record_outbound_status("wamid-1", "delivered")
    assert updates and updates[0]["status"] == "delivered"


@pytest.mark.asyncio
async def test_normal_forward_progression_is_unaffected():
    db, updates = _db_with(dict(ROW, status="sent"))
    with patch("vula.commerce.service._client", lambda: db):
        await wa._record_outbound_status("wamid-1", "delivered")
    assert updates and updates[0]["status"] == "delivered"


@pytest.mark.asyncio
async def test_a_stale_read_still_never_downgrades_to_sent():
    db, updates = _db_with(dict(ROW, status="read"))
    with patch("vula.commerce.service._client", lambda: db):
        await wa._record_outbound_status("wamid-1", "sent")
    assert updates == []
