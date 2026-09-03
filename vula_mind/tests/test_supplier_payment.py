"""Paying a supplier must clear the bill AND land on the right side of the ledger.

Two connected 2026-09-03 findings, from asking how a tenant proves an invoice is paid
(Ian: "mark paid, give POP, or bank statement").

1. Bank reconciliation matched outgoing payments against casual labour and card expenses, but
   never against inbound supplier INVOICES. So paying a supplier confirmed the money left the
   bank while the bill stayed 'draft' forever. That is why off-the-hook showed R80,542.72 "still
   owed by us", including bills from 2020-2023 that were certainly settled — and why a payables
   report could not be trusted.

2. Worse, and latent: update_invoice_status(..., "paid") posted ledger.post_invoice_paid
   UNCONDITIONALLY, which credits `sales`. Marking an inbound supplier bill paid would have
   booked money going OUT as revenue, and inflated VAT output at the same time. Nothing had
   marked an inbound bill paid yet — but wiring bank rec to do so was about to. The ledger had
   no payables posting at all.
"""
from unittest.mock import MagicMock, patch

import pytest

from vula.commerce import ledger, service
from vula.commerce.bank_rec import _match_supplier_bill


# ── the accounting must not invent revenue ──────────────────────────────────────

def _lines_for(fn, doc):
    """Capture the journal lines a posting would write, without touching the ledger."""
    captured = {}

    def _spy(tenant_id, **kw):      # _post takes tenant_id positionally
        captured.update(kw)

    with patch.object(ledger, "_post", _spy):
        fn("off-the-hook", doc)
    return captured


def test_paying_a_supplier_credits_the_bank_and_debits_an_expense():
    bill = {"id": "b1", "invoice_number": "OFF-BILL-00001", "supplier": "Atlantis Seafood",
            "total_cents": 520462, "vat_cents": 67886, "account_code": "stock_purchases"}
    cap = _lines_for(ledger.post_supplier_invoice_paid, bill)
    by_acc = {ln["account_code"]: ln for ln in cap["lines"]}
    assert by_acc["bank_cash"]["credit_cents"] == 520462, "money must LEAVE the bank"
    assert by_acc["bank_cash"]["debit_cents"] == 0
    assert by_acc["stock_purchases"]["debit_cents"] == 520462 - 67886
    assert "sales" not in by_acc, "paying a supplier is not revenue"


def test_supplier_vat_goes_to_input_not_output():
    """VAT on a purchase is reclaimable (input), not owed (output)."""
    bill = {"id": "b1", "total_cents": 11500, "vat_cents": 1500}
    cap = _lines_for(ledger.post_supplier_invoice_paid, bill)
    codes = {ln["account_code"] for ln in cap["lines"]}
    assert "vat_input" in codes
    assert "vat_output" not in codes


def test_a_customer_paying_us_still_credits_sales():
    """The money-in side must be untouched by this change."""
    inv = {"id": "i1", "invoice_number": "OFF-INV-00085", "total_cents": 3200000, "vat_cents": 0}
    cap = _lines_for(ledger.post_invoice_paid, inv)
    by_acc = {ln["account_code"]: ln for ln in cap["lines"]}
    assert by_acc["sales"]["credit_cents"] == 3200000
    assert by_acc["bank_cash"]["debit_cents"] == 3200000, "money must ENTER the bank"


def test_a_zero_or_negative_bill_posts_nothing():
    for total in (0, -100):
        cap = _lines_for(ledger.post_supplier_invoice_paid, {"id": "b", "total_cents": total})
        assert cap == {}


# ── update_invoice_status must pick the side by direction ───────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("direction,expected", [
    ("inbound", "post_supplier_invoice_paid"),
    ("outbound", "post_invoice_paid"),
    (None, "post_invoice_paid"),          # legacy rows default to our own invoice
])
async def test_the_ledger_side_follows_the_document_direction(direction, expected):
    row = {"id": "x", "direction": direction, "total_cents": 1000}
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.eq.return_value \
        .execute.return_value = MagicMock(data=[row])
    with patch.object(service, "_client", lambda: db), \
         patch.object(ledger, "post_supplier_invoice_paid") as sup, \
         patch.object(ledger, "post_invoice_paid") as own:
        await service.update_invoice_status("off-the-hook", "x", "paid")
    called = "post_supplier_invoice_paid" if sup.called else "post_invoice_paid"
    assert called == expected
    assert not (sup.called and own.called), "exactly one side of the books"


# ── matching an outgoing payment to a bill ──────────────────────────────────────

BILLS = [
    {"id": "b1", "supplier": "Atlantis Seafood Distributors", "invoice_number": "OFF-BILL-1",
     "total_cents": 970417},
    {"id": "b2", "supplier": "Maintenance Solutions", "invoice_number": "OFF-BILL-2",
     "total_cents": 1045000},
]


def test_an_outgoing_payment_matches_the_right_supplier_bill():
    txn = {"amount_cents": 970417, "description": "ATLANTIS SEAFOOD DISTRIBUTORS", "reference": ""}
    assert (_match_supplier_bill(txn, BILLS) or {}).get("id") == "b1"


def test_a_payment_that_matches_nothing_is_left_alone():
    """Never guess: an unmatched debit stays unmatched for review."""
    txn = {"amount_cents": 12345, "description": "SOMETHING ELSE", "reference": ""}
    assert _match_supplier_bill(txn, BILLS) is None


def test_two_bills_at_the_same_amount_are_ambiguous_and_not_guessed():
    same = [dict(BILLS[0]), dict(BILLS[0], id="b3", supplier="Other Supplier")]
    txn = {"amount_cents": 970417, "description": "PAYMENT", "reference": ""}
    assert _match_supplier_bill(txn, same) is None, "ambiguous amounts must not auto-match"


# ── which expense account a supplier payment lands in ───────────────────────────
# commerce_invoices carries no account_code column, so without a lookup every supplier payment
# would land in "other_expense" and the trial balance would show all supplier spend in one
# undifferentiated bucket. Supplier categories are free text, so only an EXACT match against a
# real expense account is accepted — inventing a mapping would file spend under an account the
# tenant never chose, which is harder to spot than an honestly generic one.

def _chart_stub(codes):
    return [{"code": c, "type": "expense"} for c in codes] + [
        {"code": "bank_cash", "type": "asset"}, {"code": "sales", "type": "income"}]


def _supplier_stub(category):
    class _Q:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): return MagicMock(data=[{"category": category}])
    return MagicMock(table=MagicMock(return_value=_Q()))


@pytest.mark.parametrize("category,expected", [
    ("packaging", "packaging"),        # names a real expense account
    ("fuel", "fuel"),
    ("Marketing", "marketing"),        # case-insensitive
    ("food", "other_expense"),         # NOT a chart code — must not be invented into one
    ("", "other_expense"),
    (None, "other_expense"),
])
def test_the_supplier_category_is_used_only_when_it_names_a_real_account(category, expected):
    from vula.commerce import accounting, service as svc
    with patch.object(svc, "_client", lambda: _supplier_stub(category)), \
         patch.object(accounting, "ensure_chart",
                      lambda t: _chart_stub(["packaging", "fuel", "marketing", "other_expense"])):
        assert ledger._supplier_account_code("off-the-hook", "s1", "Acme") == expected


def test_a_lookup_failure_never_breaks_the_posting():
    """A ledger entry must still be written if the supplier lookup fails."""
    from vula.commerce import service as svc

    def _boom():
        raise RuntimeError("db down")

    with patch.object(svc, "_client", _boom):
        assert ledger._supplier_account_code("off-the-hook", "s1", "Acme") == "other_expense"


def test_no_supplier_information_falls_back_safely():
    assert ledger._supplier_account_code(None, None, None) == "other_expense"
    assert ledger._supplier_account_code("off-the-hook", None, None) == "other_expense"
