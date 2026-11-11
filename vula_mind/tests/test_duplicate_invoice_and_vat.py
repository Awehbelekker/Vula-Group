"""Two things DIGG's batch of skip invoices showed on 10 Nov 2026.

1. The same invoice sent again (a new PDF months later, different bytes) was booked as a second bill:
   it's now kept as a duplicate at filing and never booked again (doc_filing.earlier_copy).
2. Invoices that never mention VAT were read as "priced excluding VAT" and 15% was added — R2,800
   booked as R3,220: now only a document that mentions VAT is treated as excluding it."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vula.api.whatsapp import _document_states_vat
from vula.integrations import doc_filing as df

T = "test-tenant"


@pytest.mark.parametrize("text,stated", [
    ("2 x Skip rentals @R1400.00 each R2800.00 TOTAL R2800.00", False),      # the skip company
    ("Nelito ceilings. Day rates. TOTAL R:27,100", False),
    ("Evaluation of site", False),
    ("R41,752.47 excluding VAT", True),                                      # STE Scaffolding's quotes
    ("Subtotal R100.00 VAT 15% R15.00", True), ("Total excl. R500", True), ("Prices incl VAT", True),
])
def test_only_a_document_that_mentions_vat_is_read_as_excluding_it(text, stated):
    assert _document_states_vat(text) is stated


@pytest.mark.parametrize("a,b", [("972", "972"), ("INV-0972", "972"), ("Invoice No 972", "972"), ("inv 00972", "972")])
def test_invoice_numbers_compare_as_printed(a, b):
    assert df._doc_number(a) == df._doc_number(b)


class _Q:
    def __init__(self, rows): self.rows, self.f = rows, []
    def select(self, *a): return self
    def limit(self, *a): return self
    def eq(self, k, v): self.f.append(lambda r, k=k, v=v: str(_get(r, k)) == str(v)); return self
    def neq(self, k, v): self.f.append(lambda r, k=k, v=v: _get(r, k) != v); return self
    def in_(self, k, vs): self.f.append(lambda r, k=k, vs=vs: _get(r, k) in vs); return self
    def ilike(self, k, pat): self.f.append(lambda r, k=k, p=pat.strip("%").lower(): p in str(_get(r, k) or "").lower()); return self
    def execute(self): return SimpleNamespace(data=[r for r in self.rows if all(f(r) for f in self.f)])


def _get(r, k):
    if k.startswith("fields->>"):
        return (r.get("fields") or {}).get(k.split("->>")[1])
    return r.get(k)


ON_FILE = [{"id": "d972", "tenant_id": T, "category": "Invoice", "status": "filed", "filename": "Invoice 972 - HPC.pdf",
            "created_at": "2026-07-14T09:06:00", "fields": {"supplier": "R Engelbrecht", "document_number": "972",
                                                             "total_cents": 560000, "date": "2026-07-13"}}]


def _earlier(fields, category="Invoice", rows=ON_FILE):
    with patch.object(df, "_client", return_value=SimpleNamespace(table=lambda n: _Q(rows))):
        return df.earlier_copy(T, category, fields)


def test_the_same_supplier_and_invoice_number_is_the_earlier_copy():
    got = _earlier({"supplier": "R Engelbrecht", "document_number": "972", "total_cents": 560000, "date": "2026-07-13"})
    assert got and got["id"] == "d972"
    # even read with VAT added by mistake (R5,600 → R6,440), or with no total
    assert _earlier({"supplier": "R. Engelbrecht (Capitec)", "document_number": "0972", "total_cents": 644000})["id"] == "d972"
    assert _earlier({"supplier": "R Engelbrecht", "document_number": "972"})["id"] == "d972"


def test_the_same_number_for_a_different_amount_is_flagged_not_dropped():
    got = _earlier({"supplier": "R Engelbrecht", "document_number": "972", "total_cents": 700000})
    assert got["_revised"] is True and got["id"] == "d972"
    # a revision number of the business's own quotes is never matched
    assert _earlier({"supplier": "R Engelbrecht", "document_number": "Rev 04"}, rows=[dict(ON_FILE[0], fields=dict(
        ON_FILE[0]["fields"], document_number="Rev 04"))]) is None
    # a quote is never the duplicate of an invoice with the same number
    assert _earlier({"supplier": "R Engelbrecht", "document_number": "972", "total_cents": 560000}, category="Quote / Estimate") is None


def test_a_different_number_or_supplier_is_a_new_invoice():
    assert _earlier({"supplier": "R Engelbrecht", "document_number": "1039", "total_cents": 280000, "date": "2026-09-02"}) is None
    assert _earlier({"supplier": "Pudlo SA", "document_number": "972", "total_cents": 560000, "date": "2026-07-14"}) is None
    # a drawing or a proof of payment is never called a duplicate invoice
    assert _earlier({"supplier": "R Engelbrecht", "document_number": "972"}, category="Proof of Payment") is None
    # a copy already marked duplicate is not the original
    assert _earlier({"supplier": "R Engelbrecht", "document_number": "972"}, rows=[dict(ON_FILE[0], status="duplicate")]) is None


def test_a_duplicate_is_never_booked_again():
    from vula.commerce import service
    db = SimpleNamespace(table=lambda n: _Q([{"id": "dup1", "tenant_id": T, "status": "duplicate"}]))
    with patch.object(service, "_client", return_value=db):
        out = asyncio.run(service.commit_inbound_document(T, {"supplier": "R Engelbrecht", "total_cents": 560000,
                                                             "doc_type": "invoice"}, filed_document_id="dup1"))
    assert out == {"committed": False, "duplicate": True}


def test_the_same_file_sent_again_is_not_booked_a_second_time():
    from vula.commerce import service
    db = SimpleNamespace(table=lambda n: _Q([{"id": "d1", "tenant_id": T, "status": "filed", "commerce_invoice_id": "bill-1"}]))
    with patch.object(service, "_client", return_value=db):
        out = asyncio.run(service.commit_inbound_document(T, {"supplier": "R Engelbrecht", "total_cents": 560000,
                                                             "doc_type": "invoice"}, filed_document_id="d1"))
    assert out == {"committed": False, "already_booked": True, "invoice_id": "bill-1"}
