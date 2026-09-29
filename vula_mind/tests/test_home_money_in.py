"""Home shows a face-to-face shop's real takings (2026-09-29). Off the Hook: 1 online order ever,
742 bank lines — the orders-only Home read R0. Money in = sales-like bank credits; transfers,
loans, reversals and set-aside lines don't count."""
from datetime import date, timedelta

import pytest

from tests.test_job_costing import FakeDB
from vula.api import commerce
from vula.commerce import service

TID = "off-the-hook"


@pytest.mark.asyncio
async def test_money_in_counts_sales_credits_only(monkeypatch):
    today = date.today()
    d = lambda n: (today - timedelta(days=n)).isoformat()
    fake = FakeDB({
        "commerce_orders": [{"tenant_id": TID, "id": "o1", "total_cents": 50000, "status": "paid",
                             "created_at": d(40) + "T10:00:00Z"}],
        "commerce_bank_transactions": [
            {"tenant_id": TID, "direction": "in", "txn_date": d(1), "amount_cents": 1200000, "category": "sales", "match_status": "unmatched"},
            {"tenant_id": TID, "direction": "in", "txn_date": d(3), "amount_cents": 800000, "category": None, "match_status": "unmatched"},
            {"tenant_id": TID, "direction": "in", "txn_date": d(20), "amount_cents": 500000, "category": "sales", "match_status": "matched"},
            {"tenant_id": TID, "direction": "in", "txn_date": d(2), "amount_cents": 9900000, "category": "loan", "match_status": "unmatched"},
            {"tenant_id": TID, "direction": "in", "txn_date": d(2), "amount_cents": 700000, "category": "sales", "match_status": "ignored"},
            {"tenant_id": TID, "direction": "out", "txn_date": d(1), "amount_cents": 300000, "category": "cost_of_sales", "match_status": "unmatched"},
        ],
    })
    monkeypatch.setattr(service, "_client", lambda: fake)

    async def no_low_stock(*a, **k):
        return []
    monkeypatch.setattr(service, "get_low_stock_products", no_low_stock)
    out = await commerce.admin_stats(TID)
    assert out["bank_in_7d_cents"] == 2000000          # R12,000 + R8,000; not the loan or set-aside line
    assert out["bank_in_30d_cents"] == 2500000
    assert len(out["daily_bank_in"]) == 7 and out["bank_in_last_date"] == d(1)
