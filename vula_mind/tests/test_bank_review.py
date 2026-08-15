"""Tests for vula/commerce/bank_review.py's client-matching flow (handle_client_answer /
_apply_order_match / _apply_invoice_match). The full flow — question formatting, exact order-
number reply, order marked paid, and correctly chaining to the next pending item — was verified
live against off-the-hook with synthetic test rows during development (cleaned up after,
including self-healing a real row it touched as a side effect of the intended chaining
behavior). This file locks in the pure formatting/parsing pieces with a mocked DB."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce.bank_review import _client_question


def test_client_question_format():
    txn = {"amount_cents": 22000, "txn_date": "2026-07-17", "description": "EFT reference unclear"}
    q = _client_question(txn, 1, 3)
    assert "R220.00" in q
    assert "2026-07-17" in q
    assert "order number" in q.lower()


class _FakeTable:
    """Minimal chainable Supabase-table mock: any .select/.eq/.ilike/.in_/.order/.limit call
    returns self; .execute() returns whatever rows were configured for this table name."""
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def ilike(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def update(self, *a, **k): return self

    def execute(self):
        return MagicMock(data=self._rows)


class _FakeDB:
    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return self._tables.get(name, _FakeTable([]))


@pytest.mark.asyncio
async def test_handle_client_answer_stop():
    txn = {"id": "txn1", "amount_cents": 22000, "txn_date": "2026-07-17", "description": "x"}
    db = _FakeDB({"commerce_bank_transactions": _FakeTable([txn])})
    with patch("vula.commerce.bank_review._client", return_value=db):
        from vula.commerce.bank_review import handle_client_answer
        reply = await handle_client_answer("off-the-hook", "stop")
    assert "Bank tab" in reply


@pytest.mark.asyncio
async def test_handle_client_answer_no_pending_question_returns_none():
    db = _FakeDB({"commerce_bank_transactions": _FakeTable([])})  # nothing 'asked'
    with patch("vula.commerce.bank_review._client", return_value=db):
        from vula.commerce.bank_review import handle_client_answer
        reply = await handle_client_answer("off-the-hook", "OFF-00006")
    assert reply is None


@pytest.mark.asyncio
async def test_handle_client_answer_exact_order_number_marks_paid():
    txn = {"id": "txn1", "amount_cents": 22000, "txn_date": "2026-07-17", "description": "x"}
    order = {"id": "ord1", "display_id": "OFF-00006", "customer_name": "Staci Brits",
             "customer_phone": "27821234567", "total_cents": 22000, "status": "pending_payment"}
    db = _FakeDB({
        "commerce_bank_transactions": _FakeTable([txn]),
        "commerce_orders": _FakeTable([order]),
    })
    with (
        patch("vula.commerce.bank_review._client", return_value=db),
        patch("vula.commerce.service.update_order_status", new=AsyncMock()) as mock_update,
        patch("vula.api.yoco._notify_order_paid", new=AsyncMock()) as mock_notify,
    ):
        from vula.commerce.bank_review import handle_client_answer
        reply = await handle_client_answer("off-the-hook", "OFF-00006")
    assert "marked paid" in reply
    mock_update.assert_awaited_once_with("ord1", "paid")
    mock_notify.assert_awaited_once()


# ── "yes" confirms a WhatsApp proof-of-payment's proposed candidate (migration 132) ────────

@pytest.mark.asyncio
async def test_handle_client_answer_yes_confirms_proposed_invoice():
    txn = {"id": "txn1", "amount_cents": 15000, "txn_date": "2026-08-15", "description": "x",
           "proposed_match_type": "invoice", "proposed_match_id": "inv1"}
    invoice = {"id": "inv1", "invoice_number": "OTH-0042", "customer_name": "Thabo",
               "total_cents": 15000, "status": "sent"}
    db = _FakeDB({
        "commerce_bank_transactions": _FakeTable([txn]),
        "commerce_invoices": _FakeTable([invoice]),
    })
    with (
        patch("vula.commerce.bank_review._client", return_value=db),
        patch("vula.commerce.service.update_invoice_status", new=AsyncMock()) as mock_update,
    ):
        from vula.commerce.bank_review import handle_client_answer
        reply = await handle_client_answer("off-the-hook", "yes")
    assert "OTH-0042" in reply
    assert "marked paid" in reply
    mock_update.assert_awaited_once_with("off-the-hook", "inv1", "paid")


@pytest.mark.asyncio
async def test_handle_client_answer_yes_confirms_proposed_order():
    txn = {"id": "txn1", "amount_cents": 15000, "txn_date": "2026-08-15", "description": "x",
           "proposed_match_type": "order", "proposed_match_id": "ord1"}
    order = {"id": "ord1", "display_id": "OFF-00006", "customer_name": "Staci Brits",
             "customer_phone": "27821234567", "total_cents": 15000, "status": "pending_payment"}
    db = _FakeDB({
        "commerce_bank_transactions": _FakeTable([txn]),
        "commerce_orders": _FakeTable([order]),
    })
    with (
        patch("vula.commerce.bank_review._client", return_value=db),
        patch("vula.commerce.service.update_order_status", new=AsyncMock()) as mock_update,
        patch("vula.api.yoco._notify_order_paid", new=AsyncMock()),
    ):
        from vula.commerce.bank_review import handle_client_answer
        reply = await handle_client_answer("off-the-hook", "yes")
    assert "OFF-00006" in reply
    mock_update.assert_awaited_once_with("ord1", "paid")


@pytest.mark.asyncio
async def test_handle_client_answer_yes_without_a_proposal_falls_through_to_normal_search():
    # No proposed_match_type at all (an ordinary statement-sourced unmatched credit) — "yes"
    # isn't a real order number or customer name, so it should behave like any other miss,
    # never crash trying to look up a candidate that was never proposed.
    txn = {"id": "txn1", "amount_cents": 15000, "txn_date": "2026-08-15", "description": "x"}
    db = _FakeDB({
        "commerce_bank_transactions": _FakeTable([txn]),
        "commerce_orders": _FakeTable([]),
        "commerce_invoices": _FakeTable([]),
    })
    with patch("vula.commerce.bank_review._client", return_value=db):
        from vula.commerce.bank_review import handle_client_answer
        reply = await handle_client_answer("off-the-hook", "yes")
    assert "couldn't find" in reply.lower()
