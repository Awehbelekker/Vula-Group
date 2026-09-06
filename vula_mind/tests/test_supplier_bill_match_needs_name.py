"""A supplier bill must not be settled on a matching amount alone.

2026-09-06. _match_supplier_bill (added 2026-09-03) reused _match_by_amount, which returns a
match whenever exactly ONE candidate shares the amount — name evidence was only a score boost.
That is defensible for money IN: a credit of exactly R1,152.93 against the single outstanding
invoice for R1,152.93 is specific evidence. It is not defensible for money OUT, where payments
are round and dozens of open bills compete for the same figure.

Dry-run against production before any of it was applied — off-the-hook 738 unmatched rows / 32
open bills, digg-demo 54 / 78 — produced 66 "matches", nearly all nonsense:

    The Crazy Store Tablevi   R99.90  -> OFF-INV-00069 / Pick n Pay
    Vida e Caffe Sunridge     R98.00  -> OFF-INV-00015 / Yoco Technologies
    The Car Studio Wash      R180.00  -> OFF-BILL-00004 / Atlantis Seafood
    Int On Debit Balance       R0.11  -> DIG-INV-00041 / SOLID CAPE (PTY) LTD
    FNB App Transfer To Pay  R400.00  -> DIG-INV-00010 / DO IT YOURSELF HARDWARE

Each would have marked a real supplier bill paid and posted a payables entry against it. No
damage had occurred only because no statement had been ingested since the matcher shipped.
"""
import pytest

from vula.commerce import bank_rec

BILLS = [
    {"id": "b1", "invoice_number": "OFF-INV-00069", "supplier": "Pick n Pay", "total_cents": 9990},
    {"id": "b2", "invoice_number": "OFF-BILL-00012",
     "supplier": "Atlantis Seafood Distributors(Pty) Ltd", "total_cents": 184748},
    {"id": "b3", "invoice_number": "DIG-INV-00010",
     "supplier": "DO IT YOURSELF HARDWARE", "total_cents": 40000},
]


def _txn(amount_cents, description, reference=""):
    return {"amount_cents": amount_cents, "description": description, "reference": reference}


@pytest.mark.parametrize("amount,description", [
    (9990, "The Crazy Store Tablevi Cape Town (Card 572)"),
    (184748, "Immediate External Payment: Bauernmann P"),
    (40000, "FNB App Transfer To Pay"),
    (40000, "Payshap Account Off-Us Skipp Rubble Removal"),
])
def test_amount_alone_never_settles_a_supplier_bill(amount, description):
    """Every one of these is a real production transaction that matched a real open bill purely
    on the figure, with nothing connecting the two parties."""
    assert bank_rec._match_supplier_bill(_txn(amount, description), BILLS) is None


@pytest.mark.parametrize("description", [
    "External Payment: Atlantis Seafoods IN50135117",
    "IMMEDIATE EXTERNAL PAYMENT ATLANTIS SEAFOOD",
])
def test_a_real_name_match_still_settles_the_bill(description):
    m = bank_rec._match_supplier_bill(_txn(184748, description), BILLS)
    assert m is not None and m["invoice_number"] == "OFF-BILL-00012"


def test_the_bill_number_in_the_reference_is_also_evidence():
    m = bank_rec._match_supplier_bill(
        _txn(40000, "FNB App Transfer To Pay", reference="DIG-INV-00010"), BILLS)
    assert m is not None and m["id"] == "b3"


def test_money_in_still_matches_on_amount_alone():
    """The money-IN side is deliberately unchanged — tightening it would break the working
    reconciliation path that already marks customer invoices paid from statement credits."""
    invoices = [{"id": "i1", "invoice_number": "OFF-INV-1",
                 "customer_name": "Someone Entirely Different", "total_cents": 115293}]
    m = bank_rec._match_invoice(_txn(115293, "Payment Received: ABSA BANK Bernard"), invoices)
    assert m is not None, "amount alone remains sufficient evidence for a credit"


def test_ambiguity_between_two_same_amount_bills_is_still_refused():
    bills = [
        {"id": "x", "invoice_number": "A", "supplier": "Alpha Supplies", "total_cents": 50000},
        {"id": "y", "invoice_number": "B", "supplier": "Beta Trading", "total_cents": 50000},
    ]
    assert bank_rec._match_supplier_bill(_txn(50000, "EFT payment"), bills) is None
