"""Tests for vula/commerce/finances.py::insights() — specifically the 2026-08-15 fix where
receivables ("owed to you") summed EVERY commerce_invoices row regardless of doc_type, so an
unaccepted quote counted as money owed, and a credit note (which should reduce what's owed)
instead made the figure go UP because nothing subtracted it.
"""
import pytest

import vula.commerce.finances as finances
import vula.commerce.service as service


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self._filters = []
        self._gte = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, key, val):
        self._filters.append((key, val))
        return self

    def gte(self, key, val):
        self._gte = (key, val)
        return self

    def _matches(self, row):
        if not all(row.get(k) == v for k, v in self._filters):
            return False
        if self._gte and str(row.get(self._gte[0]) or "") < self._gte[1]:
            return False
        return True

    def execute(self):
        return _Result([r for r in self._rows if self._matches(r)])


class _FakeClient:
    def __init__(self, invoices=None, orders=None, expenses=None):
        self._tables = {
            "commerce_invoices": invoices or [],
            "commerce_orders": orders or [],
            "commerce_expenses": expenses or [],
        }

    def table(self, name):
        return _FakeQuery(self._tables.get(name, []))


TID = "test-tenant"


def _inv(doc_type="invoice", status="sent", total_cents=10000, **kw):
    return {"tenant_id": TID, "doc_type": doc_type, "status": status, "total_cents": total_cents,
            "vat_cents": 0, "subtotal_cents": total_cents, "direction": "outbound",
            "created_at": "2026-08-01T00:00:00Z", "customer_name": "Thabo", **kw}


@pytest.mark.asyncio
async def test_receivables_excludes_quotes(monkeypatch):
    invoices = [_inv(doc_type="invoice", status="sent", total_cents=10000),
                _inv(doc_type="quote", status="sent", total_cents=99999)]
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(invoices=invoices))
    result = await finances.insights(TID)
    assert result["receivables"]["outstanding_cents"] == 10000


@pytest.mark.asyncio
async def test_receivables_nets_out_credit_notes(monkeypatch):
    invoices = [_inv(doc_type="invoice", status="sent", total_cents=10000),
                _inv(doc_type="credit_note", status="sent", total_cents=3000)]
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(invoices=invoices))
    result = await finances.insights(TID)
    assert result["receivables"]["outstanding_cents"] == 7000  # 10000 - 3000, not 13000


@pytest.mark.asyncio
async def test_receivables_overdue_excludes_quotes_and_credit_notes(monkeypatch):
    invoices = [_inv(doc_type="invoice", status="overdue", total_cents=5000),
                _inv(doc_type="quote", status="overdue", total_cents=8000),
                _inv(doc_type="credit_note", status="overdue", total_cents=1000)]
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(invoices=invoices))
    result = await finances.insights(TID)
    assert result["receivables"]["overdue_cents"] == 5000


@pytest.mark.asyncio
async def test_receivables_clean_with_only_real_invoices(monkeypatch):
    invoices = [_inv(doc_type="invoice", status="sent", total_cents=5000),
                _inv(doc_type="invoice", status="paid", total_cents=2000)]
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(invoices=invoices))
    result = await finances.insights(TID)
    assert result["receivables"]["outstanding_cents"] == 5000


# ── Aging buckets (30/60/90/90+) ─────────────────────────────────────────────────

def _days_ago(n):
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc).date() - timedelta(days=n)).isoformat()


@pytest.mark.asyncio
async def test_aging_buckets_current_when_not_yet_due(monkeypatch):
    invoices = [_inv(status="sent", total_cents=5000, due_date=_days_ago(-5))]  # due in 5 days
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(invoices=invoices))
    result = await finances.insights(TID)
    assert result["receivables"]["aging"]["current"] == 5000
    assert result["receivables"]["aging"]["days_1_30"] == 0


@pytest.mark.asyncio
async def test_aging_buckets_boundaries(monkeypatch):
    invoices = [
        _inv(status="overdue", total_cents=1000, due_date=_days_ago(30)),   # exactly 30 -> 1-30
        _inv(status="overdue", total_cents=2000, due_date=_days_ago(31)),   # exactly 31 -> 31-60
        _inv(status="overdue", total_cents=3000, due_date=_days_ago(90)),   # exactly 90 -> 61-90
        _inv(status="overdue", total_cents=4000, due_date=_days_ago(91)),   # exactly 91 -> 90+
    ]
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(invoices=invoices))
    result = await finances.insights(TID)
    aging = result["receivables"]["aging"]
    assert aging["days_1_30"] == 1000
    assert aging["days_31_60"] == 2000
    assert aging["days_61_90"] == 3000
    assert aging["days_90_plus"] == 4000


@pytest.mark.asyncio
async def test_aging_buckets_no_due_date_counts_as_current(monkeypatch):
    invoices = [_inv(status="sent", total_cents=5000, due_date=None)]
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(invoices=invoices))
    result = await finances.insights(TID)
    assert result["receivables"]["aging"]["current"] == 5000


@pytest.mark.asyncio
async def test_aging_buckets_ignore_paid_and_draft(monkeypatch):
    invoices = [_inv(status="paid", total_cents=5000, due_date=_days_ago(60)),
                _inv(status="draft", total_cents=6000, due_date=_days_ago(60))]
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(invoices=invoices))
    result = await finances.insights(TID)
    aging = result["receivables"]["aging"]
    assert sum(aging.values()) == 0


@pytest.mark.asyncio
async def test_aging_buckets_exclude_quotes_and_credit_notes(monkeypatch):
    invoices = [_inv(doc_type="quote", status="overdue", total_cents=9000, due_date=_days_ago(60)),
                _inv(doc_type="credit_note", status="overdue", total_cents=9000, due_date=_days_ago(60))]
    monkeypatch.setattr(service, "_client", lambda: _FakeClient(invoices=invoices))
    result = await finances.insights(TID)
    aging = result["receivables"]["aging"]
    assert sum(aging.values()) == 0
