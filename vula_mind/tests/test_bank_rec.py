"""Tests for vula/commerce/bank_rec.py's matching logic and the single-document payment-
confirmation extraction. The full reconcile()-against-a-real-order flow was verified live
against off-the-hook with a synthetic test order during development (see the feature's commit
message) — this file locks in the pure-logic pieces plus a regression test for a real bug found
during that live test: an unconverted DD/MM/YYYY date reaching the DB as a DATE column value."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce.bank_rec import (
    _match_invoice, _match_order, extract_payment_confirmation, reconciliation_ok,
    propose_pop_match, stage_pop_for_review,
)


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


# ── reconciliation_ok (2026-08 accuracy audit) ────────────────────────────────
# Bank statements have no single "total" the way an invoice does, so extraction_quality.py's
# line-sum-vs-stated-total check can't be reused as-is — this cross-checks each line's stated
# running balance against the previous line's balance +/- this transaction's amount instead.

def test_reconciliation_ok_when_running_balance_is_consistent():
    txns = [
        {"amount_cents": 10000, "direction": "in", "balance_cents": 20000},
        {"amount_cents": 5000, "direction": "out", "balance_cents": 15000},
        {"amount_cents": 2000, "direction": "in", "balance_cents": 17000},
    ]
    assert reconciliation_ok(txns) is True


def test_reconciliation_fails_on_a_genuine_balance_mismatch():
    txns = [
        {"amount_cents": 10000, "direction": "in", "balance_cents": 20000},
        # Model misread this amount — stated balance doesn't follow from the previous line.
        {"amount_cents": 5000, "direction": "out", "balance_cents": 99999},
    ]
    assert reconciliation_ok(txns) is False


def test_reconciliation_ok_when_no_balance_data_present():
    """Not every statement format shows a running balance — nothing to check, must not be
    treated as a failure (that would flag every such statement as suspect for no reason)."""
    txns = [
        {"amount_cents": 10000, "direction": "in", "balance_cents": None},
        {"amount_cents": 5000, "direction": "out", "balance_cents": None},
    ]
    assert reconciliation_ok(txns) is True


def test_reconciliation_tolerates_small_rounding():
    txns = [
        {"amount_cents": 10000, "direction": "in", "balance_cents": 20000},
        {"amount_cents": 5000, "direction": "out", "balance_cents": 15050},  # 50c off, within tolerance
    ]
    assert reconciliation_ok(txns) is True


@pytest.mark.asyncio
async def test_ingest_statement_flags_extraction_reconciled_in_result():
    from pathlib import Path
    from vula.commerce import bank_rec

    bad_txns = [
        {"date": "2026-08-01", "description": "Deposit", "amount_cents": 10000,
         "direction": "in", "balance_cents": 20000, "reference": None},
        {"date": "2026-08-02", "description": "Withdrawal", "amount_cents": 5000,
         "direction": "out", "balance_cents": 99999, "reference": None},
    ]
    with (
        patch.object(bank_rec, "extract_pdf_text", return_value="statement text"),
        patch.object(bank_rec, "get_statement_password", return_value=None),
        patch.object(bank_rec, "extract_transactions", new=AsyncMock(return_value=bad_txns)),
        patch.object(bank_rec, "reconcile", new=AsyncMock(return_value={"parsed": 2, "saved": 2})),
    ):
        result = await bank_rec.ingest_statement("off-the-hook", Path("fake.pdf"))

    assert result["extraction_reconciled"] is False


# ── WhatsApp proof-of-payment staging (2026-08-15, migration 132) ───────────────
# Unlike reconcile()'s confident-match auto-apply, a WhatsApp screenshot never auto-marks paid —
# it always stages into bank_review.py's existing interactive flow, even on a confident match.

class _FakeTable:
    def __init__(self, rows):
        self._rows = rows
        self.inserted = None

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def insert(self, payload):
        self.inserted = payload
        return self

    def execute(self):
        if self.inserted is not None:
            return MagicMock(data=[self.inserted])
        return MagicMock(data=self._rows)


class _FakeDB:
    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return self._tables.get(name, _FakeTable([]))


def test_propose_pop_match_finds_invoice_by_amount_and_reference():
    invoice = {"id": "inv1", "invoice_number": "OTH-0042", "customer_name": "Thabo",
               "total_cents": 15000, "status": "sent", "doc_type": "invoice"}
    db = _FakeDB({
        "commerce_invoices": _FakeTable([invoice]),
        "commerce_orders": _FakeTable([]),
    })
    with patch("vula.commerce.bank_rec._client", return_value=db):
        result = propose_pop_match("off-the-hook", 15000, reference="OTH-0042", payee="Thabo")
    assert result is not None
    match_type, cand = result
    assert match_type == "invoice"
    assert cand["id"] == "inv1"


def test_propose_pop_match_falls_back_to_order_when_no_invoice_matches():
    order = {"id": "ord1", "display_id": "OFF-00006", "customer_name": "Staci Brits",
              "customer_phone": "27821234567", "total_cents": 15000}
    db = _FakeDB({
        "commerce_invoices": _FakeTable([]),
        "commerce_orders": _FakeTable([order]),
    })
    with patch("vula.commerce.bank_rec._client", return_value=db):
        result = propose_pop_match("off-the-hook", 15000, reference="OFF-00006")
    assert result is not None
    match_type, cand = result
    assert match_type == "order"
    assert cand["id"] == "ord1"


def test_propose_pop_match_returns_none_when_nothing_matches():
    db = _FakeDB({"commerce_invoices": _FakeTable([]), "commerce_orders": _FakeTable([])})
    with patch("vula.commerce.bank_rec._client", return_value=db):
        result = propose_pop_match("off-the-hook", 99999)
    assert result is None


# ── Customer-facing: sender-phone-scoped matching (2026-08-15, C2 second phase) ────────────
# A customer texting the storefront line about their OWN order carries a much stronger signal
# (their phone number) than a bank statement line ever has. This should be tried before the
# generic tenant-wide amount+name matcher, and should resolve cases the generic matcher alone
# would find ambiguous (two different customers who happen to owe the same amount).

def test_propose_pop_match_prefers_senders_own_invoice_over_an_ambiguous_amount_tie():
    # Two invoices share the SAME amount for DIFFERENT customers — the generic amount matcher
    # alone would call this ambiguous (no name/reference boost) and return None. Knowing the
    # sender's own phone should resolve it immediately.
    mine = {"id": "inv1", "invoice_number": "OTH-0001", "customer_name": "Thabo",
            "customer_phone": "0821234567", "total_cents": 15000, "status": "sent",
            "doc_type": "invoice"}
    someone_elses = {"id": "inv2", "invoice_number": "OTH-0002", "customer_name": "Sipho",
                      "customer_phone": "0839876543", "total_cents": 15000, "status": "sent",
                      "doc_type": "invoice"}
    db = _FakeDB({
        "commerce_invoices": _FakeTable([mine, someone_elses]),
        "commerce_orders": _FakeTable([]),
    })
    with patch("vula.commerce.bank_rec._client", return_value=db):
        # sender uses the international-prefix form; the row stores the local 0-prefixed form —
        # normalization must treat these as the same number.
        result = propose_pop_match("off-the-hook", 15000, sender_phone="27821234567")
    assert result == ("invoice", mine)


def test_propose_pop_match_falls_back_to_generic_matcher_when_sender_has_no_open_items():
    order = {"id": "ord1", "display_id": "OFF-00006", "customer_name": "Staci Brits",
             "customer_phone": "27821234567", "total_cents": 15000}
    db = _FakeDB({
        "commerce_invoices": _FakeTable([]),
        "commerce_orders": _FakeTable([order]),
    })
    with patch("vula.commerce.bank_rec._client", return_value=db):
        # A different phone than the order's own — no phone match, but the generic amount
        # matcher (a single unambiguous candidate) should still resolve it.
        result = propose_pop_match("off-the-hook", 15000, sender_phone="27000000000")
    assert result == ("order", order)


def test_propose_pop_match_without_sender_phone_behaves_exactly_as_before():
    invoice = {"id": "inv1", "invoice_number": "OTH-0042", "customer_name": "Thabo",
               "customer_phone": "27821234567", "total_cents": 15000, "status": "sent",
               "doc_type": "invoice"}
    db = _FakeDB({
        "commerce_invoices": _FakeTable([invoice]),
        "commerce_orders": _FakeTable([]),
    })
    with patch("vula.commerce.bank_rec._client", return_value=db):
        result = propose_pop_match("off-the-hook", 15000, reference="OTH-0042")
    assert result == ("invoice", invoice)


def test_stage_pop_for_review_proposes_specific_invoice_and_stores_candidate():
    invoice = {"id": "inv1", "invoice_number": "OTH-0042", "customer_name": "Thabo",
               "total_cents": 15000, "status": "sent", "doc_type": "invoice"}
    txn_table = _FakeTable([])
    db = _FakeDB({
        "commerce_invoices": _FakeTable([invoice]),
        "commerce_orders": _FakeTable([]),
        "commerce_bank_transactions": txn_table,
    })
    with patch("vula.commerce.bank_rec._client", return_value=db):
        msg = stage_pop_for_review("off-the-hook", 15000, "2026-08-15", "OTH-0042", "Thabo")
    assert "OTH-0042" in msg
    assert "yes" in msg.lower()
    assert txn_table.inserted["proposed_match_type"] == "invoice"
    assert txn_table.inserted["proposed_match_id"] == "inv1"
    assert txn_table.inserted["match_status"] == "asked"
    assert txn_table.inserted["direction"] == "in"


def test_stage_pop_for_review_asks_open_question_when_no_match():
    txn_table = _FakeTable([])
    db = _FakeDB({
        "commerce_invoices": _FakeTable([]),
        "commerce_orders": _FakeTable([]),
        "commerce_bank_transactions": txn_table,
    })
    with patch("vula.commerce.bank_rec._client", return_value=db):
        msg = stage_pop_for_review("off-the-hook", 5000, None, None, None)
    assert "which order or invoice" in msg.lower()
    assert txn_table.inserted["proposed_match_type"] is None
    assert txn_table.inserted["proposed_match_id"] is None
