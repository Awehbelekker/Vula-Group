"""Tests for vula/commerce/bank_rec.py's matching logic and the single-document payment-
confirmation extraction. The full reconcile()-against-a-real-order flow was verified live
against off-the-hook with a synthetic test order during development (see the feature's commit
message) — this file locks in the pure-logic pieces plus a regression test for a real bug found
during that live test: an unconverted DD/MM/YYYY date reaching the DB as a DATE column value."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce.bank_rec import _match_invoice, _match_order, extract_payment_confirmation


def test_match_invoice_ambiguous_same_amount_no_name_match():
    invoices = [
        {"id": "inv1", "invoice_number": "INV-001", "customer_name": "Judy Downing", "total_cents": 50000},
        {"id": "inv2", "invoice_number": "INV-002", "customer_name": "Richard Downing", "total_cents": 50000},
    ]
    txn = {"amount_cents": 50000, "description": "EFT payment", "reference": ""}
    assert _match_invoice(txn, invoices) is None


def test_match_invoice_name_resolves_ambiguity():
    invoices = [
        {"id": "inv1", "invoice_number": "INV-001", "customer_name": "Judy Downing", "total_cents": 50000},
        {"id": "inv2", "invoice_number": "INV-002", "customer_name": "Richard Downing", "total_cents": 50000},
    ]
    txn = {"amount_cents": 50000, "description": "Judy Downing EFT", "reference": "INV-001"}
    m = _match_invoice(txn, invoices)
    assert m is not None and m["id"] == "inv1"


def test_match_order_by_amount_and_display_id():
    orders = [{"id": "ord1", "display_id": "OFF-00006", "customer_name": "Staci Brits", "total_cents": 15000}]
    txn = {"amount_cents": 15000, "description": "OFF-00006 payment", "reference": ""}
    m = _match_order(txn, orders)
    assert m is not None and m["id"] == "ord1"


def test_match_order_no_amount_match_returns_none():
    orders = [{"id": "ord1", "display_id": "OFF-00006", "customer_name": "Staci Brits", "total_cents": 15000}]
    txn = {"amount_cents": 99999, "description": "random", "reference": ""}
    assert _match_order(txn, orders) is None


@pytest.mark.asyncio
async def test_extract_payment_confirmation_converts_sa_date_format():
    """Regression test: the model returning an unconverted DD/MM/YYYY date (matching the SA
    date format the prompt itself describes) must be normalised to None, never passed through
    as-is — an unconverted date reaching commerce_bank_transactions.txn_date (a DATE column)
    silently failed the whole insert (saved=0) during live testing, though the invoice/order
    match+mark-paid still succeeded since that doesn't depend on txn_date."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=(
        '[{"date":"17/07/2026","description":"Payment Confirmation","amount_cents":15000,'
        '"direction":"in","reference":"OFF-00006"}]'
    )))]
    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=mock_response)),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("test-model", "key", "base"))),
    ):
        txns = await extract_payment_confirmation("Standard Bank Payment Confirmation ...")
    assert len(txns) == 1
    assert txns[0]["amount_cents"] == 15000
    assert txns[0]["direction"] == "in"
    assert txns[0]["date"] is None  # unconverted date rejected, not passed through raw


@pytest.mark.asyncio
async def test_extract_payment_confirmation_accepts_iso_date():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=(
        '[{"date":"2026-07-17","description":"Payment Confirmation","amount_cents":15000,'
        '"direction":"in","reference":"OFF-00006"}]'
    )))]
    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=mock_response)),
        patch("core.llm_router.resolve_generation_route",
              new=AsyncMock(return_value=("test-model", "key", "base"))),
    ):
        txns = await extract_payment_confirmation("Standard Bank Payment Confirmation ...")
    assert txns[0]["date"] == "2026-07-17"
