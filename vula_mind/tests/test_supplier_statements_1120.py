"""20 Nov 2026 (Ian, DIGG): a supplier's statements came in on WhatsApp and only their balance was read; none
of the invoices on them were in Vula and none of the payments matched a bank line, so the books said R0 owed.
A supplier's activity statement (Xero layout) is now read line by line in code, kept only when it adds up to
the cent, and compared with the books (vula/ingestion/supplier_statement.py, vula/commerce/supplier_statements.py).
The statement below is made up in the same layout; no real figures."""
import pytest

from vula.commerce import (accounting, books_audit, books_review as br, doc_audit as da, ledger, ledger_events,
                           ledger_v2 as lv, reread, service, statement_files, supplier_statements as ss)
from vula.ingestion import supplier_statement as sp
from tests.fake_supabase import FakeSupabase

T = "t1"

STATEMENT = """Registered Office: Unit 1, Somewhere Road, Cape Town
STATEMENT - Activity
Our Business
From Date
1 Jan 2026
To Date
30 Nov 2026
Glass Co
Date
Activity
Reference
Due Date
Invoice Amount
Payments
Balance ZAR
1 Jan 2026
Opening Balance
0.00
3 Mar 2026
Invoice # INV-0101
Site A
3 Mar 2026
1,000.00
1,000.00
3 Mar 2026
Payment on Invoice # INV-0101
1,000.00
0.00
10 Apr 2026 Invoice # INV-0102
Site A
10 Apr
2026
500.50
500.50
10 Apr 2026 Invoice # INV-0103
Site B ...
10 Apr
2026
250.25
750.75
12 Apr 2026
Payment on Invoice # INV-0102
500.50
250.25
12 Apr 2026
Payment on Invoice # INV-0103
250.25
0.00
20 Apr 2026
Payment on Invoice # INV-0105
300.00
(300.00)
Registered Office: Unit 1, Somewhere Road, Cape Town
25 Apr 2026
Invoice # INV-0105
Site B
25 Apr 2026
300.00
0.00
2 May 2026
Invoice # INV-0106
Site C
2 May 2026
2,000.00
2,000.00
16 May 2026 Payment on Invoice # INV-0106
PART
PAYME...
750.00
1,250.00
BALANCE DUE ZAR   1,250.00
Name. : Glass Co Pty Ltd
Bank : Some Bank
"""


def test_an_activity_statement_is_read_line_by_line_to_the_cent():
    r = sp.parse(STATEMENT)
    assert r and r["reconciled"] and r["balance_cents"] == 125000 and r["supplier"] == "Glass Co Pty Ltd"
    assert (r["from"], r["to"], r["account_name"]) == ("2026-01-01", "2026-11-30", "Our Business")
    kinds = [(x["kind"], x["number"], x["amount_cents"]) for x in r["lines"]]
    assert kinds[0] == ("invoice", "INV-0101", 100000) and kinds[-1] == ("payment", "INV-0106", 75000)
    assert sum(1 for k in kinds if k[0] == "invoice") == 5 and sum(1 for k in kinds if k[0] == "payment") == 5
    assert any(x["balance_cents"] == -30000 for x in r["lines"])          # paid ahead: a balance in brackets


def test_a_statement_that_does_not_add_up_is_not_kept():
    assert sp.parse(STATEMENT.replace("BALANCE DUE ZAR   1,250.00", "BALANCE DUE ZAR   1,350.00")) is None
    assert sp.parse(STATEMENT.replace("500.50\n500.50", "500.50\n600.50", 1)) is None
    assert sp.parse("TAX INVOICE\nTotal 100.00") is None


def _doc(i, **kw):
    return {"id": i, "tenant_id": T, "category": kw.pop("category", "Invoice"), "filename": f"{i}.pdf",
            "status": "filed", "content_hash": f"h-{i}", "file_url": kw.pop("url", None), "mime": "application/pdf",
            "commerce_invoice_id": kw.pop("bill", None), "supplier_id": kw.pop("supplier_id", None),
            "created_at": "2026-11-20T07:41:00Z", "project": None, "summary": "", "fields": kw}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "vula_filed_documents": [
            _doc("st1", category="Account Statement", url="u-st1", supplier="Our Business", balance_cents=125000),
            _doc("d102", supplier="Glass Co (Pty) Ltd", document_number="INV-0102", total_cents=50050, bill="b102"),
            _doc("d105", supplier="Glass Co", document_number="INV-0105", total_cents=30000),
            {**_doc("x1", category="Account Statement", url="u-st1"), "tenant_id": "other"}],
        "commerce_invoices": [
            {"id": "b102", "tenant_id": T, "invoice_number": "DIG-BILL-00102", "supplier": "Glass Co", "direction": "inbound",
             "doc_type": "invoice", "status": "paid", "total_cents": 50050, "total_paid_cents": 50050, "issue_date": "2026-04-10"},
            {"id": "b106", "tenant_id": T, "invoice_number": "DIG-BILL-00106", "supplier": "Glass Co", "direction": "inbound",
             "doc_type": "invoice", "status": "draft", "total_cents": 200000, "total_paid_cents": 0, "issue_date": "2026-05-02"}],
        "commerce_suppliers": [{"id": "s1", "tenant_id": T, "name": "Glass Co", "aliases": []},
                               {"id": "own", "tenant_id": T, "name": "Our Business (Pty) Ltd", "aliases": []}],
        "commerce_bank_transactions": [
            {"id": "l1", "tenant_id": T, "txn_date": "2026-03-04", "amount_cents": 100000, "direction": "out",
             "description": "FNB App Payment To Glass Co", "match_status": "unmatched", "account_code": "cost_of_sales",
             "categorized_by": "owner", "bank_account": "62000004607"},
            {"id": "l2", "tenant_id": T, "txn_date": "2026-04-12", "amount_cents": 75075, "direction": "out",
             "description": "Payshap Glass", "match_status": "unmatched", "account_code": "cost_of_sales",
             "categorized_by": "owner", "bank_account": "62000004607"}],
        "commerce_bank_accounts": [{"id": "a1", "tenant_id": T, "account_number": "62000004607", "bank": "FNB",
                                    "name": None, "private_to": None}],
        "vula_merchant_audit": [], "commerce_bank_line_documents": [], "journal_entries": [], "journal_lines": [],
        "journal_post_failures": [], "commerce_invoice_payments": []})
    for mod in (da, br, service, accounting, ledger, lv, ledger_events, ss, statement_files):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr("vula.commerce.bank_rec._own_names_now", lambda tid: ["Our Business (Pty) Ltd"])
    monkeypatch.setattr(lv, "is_on", lambda tid: False)

    async def download(url):
        return b"%PDF"
    monkeypatch.setattr(reread, "_download", download)
    monkeypatch.setattr(da, "pdf_text", lambda data: STATEMENT)
    return d


def test_statements_are_read_once_and_kept_on_the_document(db):
    assert ss.read_statements(T) == {"read": 1, "unread": 0, "left": 0}
    st = next(r for r in db.rows["vula_filed_documents"] if r["id"] == "st1")["fields"]["statement"]
    assert st["reconciled"] and st["balance_cents"] == 125000 and st["hash"] == "h-st1"
    assert ss.read_statements(T)["read"] == 0                                # not read twice
    other = next(r for r in db.rows["vula_filed_documents"] if r["id"] == "x1")
    assert "statement" not in other["fields"]                                # another business's never touched


def test_each_line_is_found_in_the_books_or_listed(db):
    ss.read_statements(T)
    c = ss.check(T)
    assert len(c["suppliers"]) == 1
    s = c["suppliers"][0]
    assert s["supplier_id"] == "s1"                                           # the supplier, never the business
    status = {i["number"]: i["status"] for i in s["invoices"]}
    assert status == {"INV-0101": "missing", "INV-0102": "in_books", "INV-0103": "missing", "INV-0105": "on_file",
                      "INV-0106": "draft"}
    assert s["balance_cents"] == 125000 and s["owed_in_books_cents"] == 0 and s["draft_bills_cents"] == 200000
    assert [i["number"] for i in s["open_invoices"]] == ["INV-0106"] and s["open_invoices"][0]["open_cents"] == 125000
    found = {p["date"]: p["line"]["id"] if p["line"] else None for p in s["payments"]}
    assert found["2026-03-03"] == "l1"                                       # one payment, one bank line
    assert found["2026-04-12"] == "l2"                                       # two invoices paid as one transfer
    assert found["2026-04-20"] is None and found["2026-05-16"] is None
    assert "INV-0101, INV-0103" in ss.request_text(s)
    assert s["difference_cents"] == 125000 and "say you owe R1,250.00; the books say R0.00" in s["text"]


def test_the_books_check_and_the_review_list_them(db):
    secs = {x["key"]: x for x in da.sections(T, check_files=True)}
    assert secs["supplier_statements"]["status"] == "problem"
    w = da.worklist(T)
    assert len(w["supplier_statements"]) == 1 and "INV-0101" in w["supplier_statements"][0]["request"]
