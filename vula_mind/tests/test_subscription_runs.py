"""Subscription runs are claimed before the order is created, and use today's prices."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import subscriptions as subs


def _chain(results):
    q = MagicMock()
    for m in ("select", "eq", "lte", "limit", "update"):
        getattr(q, m).return_value = q
    q.execute.side_effect = results
    db = MagicMock()
    db.table.return_value = q
    return db


@pytest.mark.asyncio
async def test_run_claimed_elsewhere_creates_no_order():
    sub = {"id": "s1", "tenant_id": "t1", "next_run": "2026-09-20", "cadence": "weekly", "items": [{}]}
    db = _chain([MagicMock(data=[sub]), MagicMock(data=[])])
    place = AsyncMock()
    with patch.object(subs, "_client", return_value=db), patch.object(subs, "_place_order", place):
        assert await subs.process_due("t1") == 0
    place.assert_not_awaited()


@pytest.mark.asyncio
async def test_items_repriced_to_current_price_except_variants():
    sub = {"tenant_id": "t1", "items": [
        {"product_id": "p1", "unit_price_cents": 1000, "quantity": 1},
        {"product_id": "p2", "variant_id": "v1", "unit_price_cents": 500, "quantity": 1}]}
    db = _chain([MagicMock(data=[{"price_cents": 1250}])])
    with patch("vula.commerce.service._client", return_value=db):
        out = await subs._repriced(sub)
    assert out["items"][0]["unit_price_cents"] == 1250
    assert out["items"][1]["unit_price_cents"] == 500


@pytest.mark.asyncio
async def test_failed_order_gives_the_claim_back():
    """The run is claimed first; if the order then can't be created, next_run goes back so the
    next hourly pass retries instead of skipping a whole cadence (2026-09-27 review)."""
    sub = {"id": "s1", "tenant_id": "t1", "next_run": "2026-09-20", "last_run": "2026-09-13T08:00:00",
           "cadence": "weekly", "items": [{}]}
    db = _chain([MagicMock(data=[sub]), MagicMock(data=[{"id": "s1"}]), MagicMock(data=[{"id": "s1"}])])
    with patch.object(subs, "_client", return_value=db), \
         patch.object(subs, "_repriced", AsyncMock(side_effect=lambda s: s)), \
         patch.object(subs, "_place_order", AsyncMock(side_effect=RuntimeError("db down"))), \
         patch.object(subs, "_notify", AsyncMock()) as notify:
        assert await subs.process_due("t1") == 0
    notify.assert_not_awaited()
    claim, release = [c.args[0] for c in db.table.return_value.update.call_args_list]
    assert claim["next_run"] > "2026-09-20"
    assert release["next_run"] == "2026-09-20" and release["last_run"] == "2026-09-13T08:00:00"
    # the release only applies if next_run is still the one this run claimed
    assert ("next_run", claim["next_run"]) in [c.args for c in db.table.return_value.eq.call_args_list]
