"""Tests for aggregated marketing analytics (vula/api/commerce.py:admin_broadcast_analytics)
— the fourth gap closed from the marketing capability audit. Per-broadcast funnels
(admin_broadcast_recipients) and per-customer engagement already existed; nothing rolled
them up across campaigns into totals, a reach trend, or a best-performing ranking."""
from unittest.mock import MagicMock, patch

import pytest

from vula.api.commerce import admin_broadcast_analytics

_LOGS = [
    {"id": "b1", "name": "Weekly specials", "template_name": "weekly_fish",
     "sent_count": 100, "delivered_count": 95, "read_count": 60, "clicked_count": 20,
     "failed_count": 5, "created_at": "2026-07-06T10:00:00Z", "sent_at": "2026-07-06T10:00:00Z"},
    {"id": "b2", "name": "Reorder reminder", "template_name": "reorder",
     "sent_count": 50, "delivered_count": 48, "read_count": 30, "clicked_count": 5,
     "failed_count": 2, "created_at": "2026-07-13T10:00:00Z", "sent_at": "2026-07-13T10:00:00Z"},
    {"id": "b3", "name": "Tiny test send", "template_name": "promo",
     "sent_count": 2, "delivered_count": 2, "read_count": 2, "clicked_count": 2,
     "failed_count": 0, "created_at": "2026-07-13T12:00:00Z", "sent_at": "2026-07-13T12:00:00Z"},
]


def _mock_db(logs, orders=None):
    logs_table = MagicMock()
    logs_table.select.return_value.eq.return_value.gte.return_value.order.return_value \
        .limit.return_value.execute.return_value = MagicMock(data=logs)

    orders_table = MagicMock()
    orders_table.select.return_value.eq.return_value.in_.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=orders or [])

    def _table(name):
        return logs_table if name == "commerce_broadcast_logs" else orders_table

    mock_db = MagicMock()
    mock_db.table.side_effect = _table
    return mock_db


@pytest.mark.asyncio
async def test_totals_sum_across_all_broadcasts_in_window():
    mock_db = _mock_db(_LOGS)
    with patch("vula.api.commerce.service._client", return_value=mock_db):
        result = await admin_broadcast_analytics("off-the-hook", 90)

    t = result["totals"]
    assert t["broadcasts"] == 3
    assert t["sent"] == 152
    assert t["delivered"] == 145
    assert t["read"] == 92
    assert t["clicked"] == 27
    assert t["failed"] == 7
    assert t["open_rate"] == round(100 * 92 / 145, 1)
    assert t["click_rate"] == round(100 * 27 / 145, 1)


@pytest.mark.asyncio
async def test_attributed_revenue_excludes_pending_cancelled_refunded():
    orders = [
        {"total_cents": 15000, "status": "paid", "attributed_broadcast_id": "b1"},
        {"total_cents": 20000, "status": "dispatched", "attributed_broadcast_id": "b2"},
        {"total_cents": 5000, "status": "pending_payment", "attributed_broadcast_id": "b1"},
        {"total_cents": 3000, "status": "cancelled", "attributed_broadcast_id": "b2"},
        {"total_cents": 1000, "status": "refunded", "attributed_broadcast_id": "b1"},
    ]
    mock_db = _mock_db(_LOGS, orders=orders)
    with patch("vula.api.commerce.service._client", return_value=mock_db):
        result = await admin_broadcast_analytics("off-the-hook", 90)

    assert result["totals"]["attributed_orders"] == 2
    assert result["totals"]["attributed_revenue_cents"] == 35000


@pytest.mark.asyncio
async def test_best_performing_excludes_low_volume_and_ranks_by_click_rate():
    mock_db = _mock_db(_LOGS)
    with patch("vula.api.commerce.service._client", return_value=mock_db):
        result = await admin_broadcast_analytics("off-the-hook", 90)

    ids = [b["id"] for b in result["best_performing"]]
    assert "b3" not in ids   # only 2 delivered — below the min-3 threshold
    assert ids[0] == "b1"    # 20/95 ~ 21% beats b2's 5/48 ~ 10.4%
    assert result["best_performing"][0]["click_rate"] > result["best_performing"][1]["click_rate"]


@pytest.mark.asyncio
async def test_weekly_trend_buckets_by_week_start():
    mock_db = _mock_db(_LOGS)
    with patch("vula.api.commerce.service._client", return_value=mock_db):
        result = await admin_broadcast_analytics("off-the-hook", 90)

    trend = result["trend"]
    # b2 and b3 share the same week (both 2026-07-13, a Monday) -> merged into one bucket.
    assert len(trend) == 2
    week2 = next(w for w in trend if w["week"] == "2026-07-13")
    assert week2["sent"] == 52   # 50 + 2
    assert week2["clicked"] == 7   # 5 + 2


@pytest.mark.asyncio
async def test_empty_state_returns_zeros_not_error():
    mock_db = _mock_db([])
    with patch("vula.api.commerce.service._client", return_value=mock_db):
        result = await admin_broadcast_analytics("off-the-hook", 90)

    assert result["totals"]["broadcasts"] == 0
    assert result["totals"]["open_rate"] == 0
    assert result["totals"]["click_rate"] == 0
    assert result["best_performing"] == []
    assert result["trend"] == []


@pytest.mark.asyncio
async def test_attribution_rollup_failure_degrades_gracefully():
    """Migration 104 not applied yet -> commerce_orders query raises; totals/trend/best
    must still come back correctly, just with zeroed attribution figures."""
    logs_table = MagicMock()
    logs_table.select.return_value.eq.return_value.gte.return_value.order.return_value \
        .limit.return_value.execute.return_value = MagicMock(data=_LOGS)
    orders_table = MagicMock()
    orders_table.select.side_effect = RuntimeError("column attributed_broadcast_id does not exist")

    def _table(name):
        return logs_table if name == "commerce_broadcast_logs" else orders_table

    mock_db = MagicMock()
    mock_db.table.side_effect = _table

    with patch("vula.api.commerce.service._client", return_value=mock_db):
        result = await admin_broadcast_analytics("off-the-hook", 90)

    assert result["totals"]["broadcasts"] == 3
    assert result["totals"]["attributed_orders"] == 0
    assert result["totals"]["attributed_revenue_cents"] == 0
