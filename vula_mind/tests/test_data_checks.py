"""What a business adds is checked before Vula relies on it (vula/commerce/data_checks.py, 10 Nov 2026).

Each case is something that went wrong on a client's project and was put right by hand."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vula.commerce import data_checks as dc
from vula.commerce import project_file as pf

FILED = date(2026, 11, 10)


@pytest.mark.parametrize("value,bad", [
    ("2022-09-26", True),      # "22/09/26" read as 2022
    ("2028-09-03", True),      # in the future
    ("2015-09-26", True),
    ("2026-10-26", False), ("2026-11-30", False), ("2025-12-01", False), (None, False), ("26/10/2026", False),
])
def test_a_date_that_cant_be_right(value, bad):
    assert (dc.date_problem(value, FILED) is not None) is bad


def test_filing_flags_a_misread_invoice_date_and_tells_the_sender():
    fields, issues = dc.at_filing("Invoice", "IN171643.PDF", {"date": "2022-09-26", "supplier": "FLUSH"}, FILED)
    assert [i["code"] for i in issues] == ["date"] and fields["_checks"] == ["date"]
    assert "26 Sep 2022" in dc.notice(issues) and dc.notice(issues).startswith("⚠️")


def test_an_old_drawing_or_specification_is_not_a_date_problem():
    for cat in ("Drawing / Plan", "Specification", "Account Statement", "Report"):
        assert dc.at_filing(cat, "x.pdf", {"date": "2021-10-06"}, FILED)[1] == []


def test_an_internal_cost_sheet_is_kept_off_client_links_but_keeps_its_category():
    fields, issues = dc.at_filing("Bill of Quantities (BOQ)", "Belladonna Flat – project bill variance 2026-10-26.xlsx",
                                  {"sections": [{"section": "Kitchen", "budget_cents": 1, "actual_cents": 2}]}, FILED)
    assert fields["_internal"] is True and [i["code"] for i in issues] == ["internal"]
    # by its columns alone, too
    assert dc.looks_internal("Bill of Quantities (BOQ)", "Bill.xlsx", {"line_items": [{"actual_cents": 5}]})
    assert not dc.looks_internal("Bill of Quantities (BOQ)", "Bill.xlsx", {"line_items": [{"total_cents": 5}]})
    assert not dc.looks_internal("Report", "cost report.pdf", {})          # a report never reaches a client anyway


def test_an_internal_sheet_never_reaches_the_client_copy():
    docs = [{"id": "boq", "category": "Bill of Quantities (BOQ)", "fields": {"_internal": True}, "filename": "v.xlsx",
             "created_at": "2026-10-26"},
            {"id": "spec", "category": "Specification", "fields": {}, "filename": "s.pdf", "created_at": "2026-10-26"}]
    rows = pf.organise(docs, pf._Suppliers([], "DIGG"))["rows"]
    assert {r["id"]: r["client"] for r in rows} == {"boq": False, "spec": True}


def test_an_invoice_with_no_supplier_is_asked_about():
    _f, issues = dc.at_filing("Invoice", "INV-259193.pdf", {"date": "2026-09-25"}, FILED)
    assert [i["code"] for i in issues] == ["no_supplier"]
    assert dc.at_filing("Invoice", "x.pdf", {"supplier": "Pudlo"}, FILED)[1] == []


def test_a_check_never_stops_a_document_being_filed():
    fields, issues = dc.at_filing("Invoice", None, None, FILED)          # no filename, no fields
    assert isinstance(fields, dict)
    with patch.object(dc, "date_problem", side_effect=RuntimeError("boom")):
        fields, issues = dc.at_filing("Invoice", "x.pdf", {"date": "2022-01-01"}, FILED)
    assert issues == [] and fields == {"date": "2022-01-01"}


@pytest.mark.parametrize("bill,txn,match", [
    ({"supplier": "FLUSH Bathrooms (Pty) Ltd", "invoice_number": "DIG-BILL-00039", "total_cents": 840650},
     {"description": "Payshap Account Off-Us Rudi Project"}, True),          # exact cents: nobody pays that by chance
    ({"supplier": "Sea Point Hire", "invoice_number": "DIG-BILL-1", "total_cents": 500000},
     {"description": "FNB App Payment To Sea Point 113367"}, True),          # the supplier's name
    ({"supplier": "Builders", "invoice_number": "INV113367", "total_cents": 300000},
     {"description": "Payment inv113367"}, True),                             # the bill's number
    ({"supplier": "Gelmar", "invoice_number": "DIG-INV-00008", "total_cents": 50000},
     {"description": "FNB App Transfer To Pay"}, False),                      # R500 to someone: not enough
])
def test_a_payment_is_only_matched_to_a_bill_with_more_than_the_amount(bill, txn, match):
    assert dc._payment_names_bill(bill, txn) is match


def test_the_health_list_finds_a_bill_on_another_project():
    docs = [{"id": "d1", "project": "Belladonna", "commerce_invoice_id": "b1"},
            {"id": "d2", "project": "Belladonna", "commerce_invoice_id": "b2"}]
    bills = [{"id": "b1", "invoice_number": "DIG-BILL-00039", "supplier": "FLUSH", "project": "Sporty TV – Phase 2"},
             {"id": "b2", "invoice_number": "DIG-BILL-00040", "supplier": "Pudlo", "project": "belladonna"}]
    with patch.object(dc, "_rows", return_value=bills):
        out = dc.bill_project_mismatches("t1", docs)
    assert [o["bill"] for o in out] == ["DIG-BILL-00039"]


def test_the_health_list_reads_only_this_business(monkeypatch):
    seen = []

    class _Q:
        def __init__(self, n): self.n = n
        def select(self, *a): return self
        def eq(self, k, v): seen.append((self.n, k, v)); return self
        def neq(self, *a): return self
        def is_(self, *a): return self
        def in_(self, *a): return self
        def gte(self, *a): return self
        def limit(self, *a): return self
        def order(self, *a, **k): return self
        def range(self, *a): return self
        def execute(self): return SimpleNamespace(data=[])

    monkeypatch.setattr(dc, "_client", lambda: SimpleNamespace(table=lambda n: _Q(n)))
    monkeypatch.setattr("vula.commerce.service.own_business_names", lambda t: _async(([], [])))
    out = dc.sweep("t1")
    assert out["issues"] == 0 and out["sections"] == []
    assert seen and all(v == "t1" for (_n, k, v) in seen if k == "tenant_id")


async def _async_value(v):
    return v


def _async(v):
    return _async_value(v)


def test_the_health_list_finds_a_bill_booked_at_the_wrong_amount():
    docs = [{"id": "d1", "commerce_invoice_id": "b1", "fields": {"total_cents": 200000}},
            {"id": "d2", "commerce_invoice_id": "b2", "fields": {"total_cents": 840650}},
            {"id": "d3", "commerce_invoice_id": "b3", "fields": {}}]
    bills = [{"id": "b1", "invoice_number": "DIG-BILL-00098", "supplier": "Sea Point Hire", "total_cents": 230000},
             {"id": "b2", "invoice_number": "DIG-BILL-00039", "supplier": "FLUSH", "total_cents": 840650},
             {"id": "b3", "invoice_number": "X", "supplier": "Y", "total_cents": 1}]
    out = dc.bill_amount_mismatches("t1", docs, bills)
    assert [o["bill"] for o in out] == ["DIG-BILL-00098"]
    assert out[0]["detail"].startswith("Booked at R2,300.00, the document says R2,000.00. 15% VAT was added")


def test_a_document_the_owner_took_off_the_link_stays_off():
    docs = [{"id": "q1", "category": "Quote / Estimate", "fields": {"_hidden_from_client": True, "supplier": "Flush"},
             "filename": "MERRQUO-329634.1.pdf", "created_at": "2026-09-22"},
            {"id": "q4", "category": "Quote / Estimate", "fields": {"supplier": "Flush"}, "filename": "MERRQUO-329634.4.pdf",
             "created_at": "2026-10-08"}]
    rows = pf.organise(docs, pf._Suppliers([], "DIGG"))["rows"]
    assert {r["id"]: r["client"] for r in rows} == {"q1": False, "q4": True}
