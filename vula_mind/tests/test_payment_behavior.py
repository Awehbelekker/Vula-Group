"""Tests for vula/commerce/payment_behavior.py — the "who actually pays on time" score built
on real paid-invoice history (2026-08-15, follow-on to the invoicing overhaul). Pure
integer-date arithmetic, no LLM involvement, same discipline as every other money computation
in this codebase.
"""
import pytest

from vula.commerce.payment_behavior import customer_payment_behavior, tenant_watch_list, MIN_SAMPLE


def _paid(due_date, paid_at, total_cents=10000):
    return {"status": "paid", "due_date": due_date, "paid_at": paid_at, "total_cents": total_cents}


# ── customer_payment_behavior (pure) ────────────────────────────────────────────

def test_below_min_sample_returns_not_enough_history():
    invoices = [_paid("2026-07-01", "2026-07-01")]
    result = customer_payment_behavior(invoices)
    assert result["sample_size"] == 1
    assert result["label"] == "not_enough_history"
    assert result["on_time_pct"] is None


def test_always_on_time_is_reliable():
    invoices = [_paid("2026-07-01", "2026-06-30"), _paid("2026-07-15", "2026-07-15"),
                _paid("2026-08-01", "2026-07-28")]
    result = customer_payment_behavior(invoices)
    assert result["sample_size"] == 3
    assert result["on_time_pct"] == 100
    assert result["avg_days_late"] == 0.0
    assert result["label"] == "reliable"


def test_always_late_is_high_risk():
    invoices = [_paid("2026-07-01", "2026-07-15"), _paid("2026-07-15", "2026-08-01")]
    result = customer_payment_behavior(invoices)
    assert result["on_time_pct"] == 0
    assert result["avg_days_late"] > 0
    assert result["label"] == "high_risk"


def test_mixed_history_computes_correct_average_days_late():
    # On time, on time, 10 days late, 20 days late -> avg over ALL 4 (not just the late ones)
    invoices = [
        _paid("2026-07-01", "2026-07-01"), _paid("2026-07-10", "2026-07-05"),
        _paid("2026-07-15", "2026-07-25"), _paid("2026-08-01", "2026-08-21"),
    ]
    result = customer_payment_behavior(invoices)
    assert result["sample_size"] == 4
    assert result["on_time_pct"] == 50
    assert result["avg_days_late"] == 7.5  # (0 + 0 + 10 + 20) / 4
    assert result["label"] == "frequently_late"


def test_label_boundaries():
    def make(on_time, total):
        rows = [_paid("2026-07-01", "2026-07-01")] * on_time
        rows += [_paid("2026-07-01", "2026-07-10")] * (total - on_time)
        return rows

    assert customer_payment_behavior(make(9, 10))["label"] == "reliable"          # 90%
    assert customer_payment_behavior(make(7, 10))["label"] == "usually_on_time"   # 70%
    assert customer_payment_behavior(make(4, 10))["label"] == "frequently_late"   # 40%
    assert customer_payment_behavior(make(3, 10))["label"] == "high_risk"         # 30%


def test_unpaid_and_missing_dates_are_excluded_from_the_sample():
    invoices = [
        _paid("2026-07-01", "2026-07-01"),
        {"status": "sent", "due_date": "2026-07-01", "paid_at": None, "total_cents": 5000},
        {"status": "paid", "due_date": None, "paid_at": "2026-07-01", "total_cents": 5000},
    ]
    result = customer_payment_behavior(invoices)
    assert result["sample_size"] == 1
    assert result["label"] == "not_enough_history"


def test_min_sample_constant_is_two():
    assert MIN_SAMPLE == 2


# ── tenant_watch_list (async, groups by customer) ───────────────────────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        return _Result(self._rows)


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        assert name == "commerce_invoices"
        return _FakeQuery(self._rows)


@pytest.mark.asyncio
async def test_tenant_watch_list_flags_only_customers_with_enough_bad_history(monkeypatch):
    rows = [
        # Reliable customer — never appears on the watch list.
        {"customer_phone": "27821111111", "customer_name": "Reliable Rita", "status": "paid",
         "due_date": "2026-07-01", "paid_at": "2026-07-01", "total_cents": 10000},
        {"customer_phone": "27821111111", "customer_name": "Reliable Rita", "status": "paid",
         "due_date": "2026-07-15", "paid_at": "2026-07-14", "total_cents": 10000},
        # Chronically late customer — should appear.
        {"customer_phone": "27822222222", "customer_name": "Late Larry", "status": "paid",
         "due_date": "2026-07-01", "paid_at": "2026-07-20", "total_cents": 10000},
        {"customer_phone": "27822222222", "customer_name": "Late Larry", "status": "paid",
         "due_date": "2026-07-15", "paid_at": "2026-08-05", "total_cents": 10000},
        # Only ONE paid invoice — not enough history, excluded regardless of lateness.
        {"customer_phone": "27823333333", "customer_name": "New Nomsa", "status": "paid",
         "due_date": "2026-07-01", "paid_at": "2026-07-25", "total_cents": 10000},
    ]
    monkeypatch.setattr("vula.commerce.service._client", lambda: _FakeClient(rows))
    result = await tenant_watch_list("off-the-hook")
    names = [w["customer_name"] for w in result["watch_list"]]
    assert names == ["Late Larry"]
    assert result["customers_scored"] == 3
    assert result["overall"]["sample_size"] == 5


@pytest.mark.asyncio
async def test_tenant_watch_list_empty_when_no_paid_invoices(monkeypatch):
    monkeypatch.setattr("vula.commerce.service._client", lambda: _FakeClient([]))
    result = await tenant_watch_list("off-the-hook")
    assert result["watch_list"] == []
    assert result["overall"]["label"] == "not_enough_history"


@pytest.mark.asyncio
async def test_tenant_watch_list_respects_limit(monkeypatch):
    rows = []
    for n in range(15):
        phone = f"2782000{n:04d}"
        rows += [
            {"customer_phone": phone, "customer_name": f"Late-{n}", "status": "paid",
             "due_date": "2026-07-01", "paid_at": "2026-07-20", "total_cents": 10000},
            {"customer_phone": phone, "customer_name": f"Late-{n}", "status": "paid",
             "due_date": "2026-07-15", "paid_at": "2026-08-05", "total_cents": 10000},
        ]
    monkeypatch.setattr("vula.commerce.service._client", lambda: _FakeClient(rows))
    result = await tenant_watch_list("off-the-hook", limit=5)
    assert len(result["watch_list"]) == 5
    assert result["customers_scored"] == 15
