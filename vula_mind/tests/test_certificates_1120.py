"""20 Nov 2026 (Ian, DIGG): "Vizion Developments invoices are actually payment certificates — they should match
the invoices DIGG sent." Vizion, the principal agent on the Cape Town High Performance Centre, certifies what
the client owes DIGG; DIGG invoices that amount and the client pays it ("Magtape Credit Hpc-PC2"). The
certificates were filed as supplier invoices, DIGG's own HPC invoices were never added to Invoices, and one
was in Invoices with 15% VAT added. vula/commerce/certificates.py + doc_audit.sales_not_in_invoices."""
import pytest

from vula.commerce import (accounting, books_audit, books_review as br, certificates as cert, data_checks,
                           doc_audit as da, ledger, ledger_events, ledger_v2 as lv, service)
from tests.fake_supabase import FakeSupabase

T = "digg-demo"


def _doc(i, category, filename, summary="", project="HPC Bokaap", bill=None, **fields):
    return {"id": i, "tenant_id": T, "category": category, "filename": filename, "summary": summary,
            "fields": fields, "status": "filed", "content_hash": f"h-{i}", "file_url": None, "mime": "application/pdf",
            "commerce_invoice_id": bill, "supplier_id": None, "created_at": "2026-07-10T08:00:00Z", "project": project}


def _line(i, day, cents, desc, **kw):
    return {"id": i, "tenant_id": T, "txn_date": day, "amount_cents": cents, "direction": "in", "description": desc,
            "match_status": "unmatched", "account_code": "sales", "categorized_by": "owner", "reference": "", **kw}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "vula_filed_documents": [
            # Vizion's certificates, filed three ways
            _doc("pc2", "Invoice", "Cape Town High Performance Centre - Payment Certificate No. 2.pdf",
                 "This is Payment Certificate No. 2 for the Cape Town High Performance Centre project, issued by Vizion "
                 "Developments to DIGG (Pty) Ltd for an amount of R249,668.44 excluding VAT.",
                 supplier="Vizion Developments (Pty) Ltd", date="2026-07-01", total_cents=24966844),
            _doc("pc3", "Invoice", "Cape Town High Performance Centre - Payment Certificate No. 3.pdf",
                 "This is Payment Certificate No. 3 ... for an amount of R199,688.08 excluding VAT.",
                 vendor="Vizion Developments (Pty) Ltd", date="2026-07-08", subtotal=199688.08, invoice_no="3"),
            _doc("pc14", "Report", "Cape Town High Performance Centre - Payment Certificate No.\n 14.pdf",
                 "This document is Payment Certificate No. 14 ...", report_type="Payment Certificate",
                 date="2026-10-07", amount_due_cents=4902539, certificate_number="14"),
            _doc("pc15", "Invoice", "Cape Town High Performance Centre - Payment Certificate No.\n 15.pdf",
                 "Payment Certificate No. 15 for the Cape Town High Performance Centre project",
                 supplier="DIGG (Pty) Ltd", date="2026-11-12", total_cents=2226456),
            # DIGG's own invoices for them
            _doc("hpc02", "Invoice", "DIGG_Invoice_HPC_PC No2.pdf", supplier="AwehBelekker (Pty) Ltd t/a DIGG",
                 customer="Oro Props (Pty) Ltd", document_number="DIGG-2026-HPC02", date="2026-07-01",
                 total_cents=24966844, _not_a_bill={"reason": "Our own invoice — a sale, in Invoices"}),
            _doc("hpc03", "Invoice", "DIGG_Invoice_HPC_PC No3.pdf", supplier="AwehBelekker (Pty) Ltd t/a DIGG",
                 customer="Oro Props (Pty) Ltd", document_number="DIGG-2026-HPC03", date="2026-07-08",
                 total_cents=19968808),
            _doc("hpc14", "Invoice", "DIGG 2026 HPC14.pdf", bill="b108", supplier="AwehBelekker (Pty) Ltd t/a DIGG",
                 customer="Cape Town High Performance Centre", document_number="DIGG-2026-HPC14", date="2026-10-07",
                 total_cents=4902539),
            # a mention is not a certificate
            _doc("mail", "Correspondence", "Re: snags.pdf", "Email about the final account and payment certificate 16"),
            {**_doc("x1", "Invoice", "Payment Certificate No. 2.pdf", total_cents=24966844), "tenant_id": "other"},
            # a second copy of certificate 3 (no amount read): one certificate, not two
            _doc("pc3b", "General Document", "Cape Town High Performance Centre - Payment Certificate No. 3 (1).pdf")],
        "commerce_invoices": [
            {"id": "b108", "tenant_id": T, "invoice_number": "DIG-BILL-00108", "customer_name": "DIGG Architecture",
             "direction": "outbound", "doc_type": "invoice", "status": "draft", "total_cents": 5637919,
             "issue_date": "2026-10-07", "project": "HPC Bokaap", "total_paid_cents": 0}],
        "commerce_invoice_payments": [],
        "commerce_bank_transactions": [
            _line("r2", "2026-07-03", 24966844, "Magtape Credit Hpc-PC2 Digg"),
            _line("r3", "2026-07-10", 19968808, "Rtc Credit Hpc-PC3 8740291910"),
            _line("r5", "2026-07-24", 38729760, "Magtape Credit Hpc-PC5"),
            _line("r14", "2026-10-14", 4902540, "Int-Banking Pmt Frm Hpc-PC14"),
            _line("r9", "2026-09-02", 14210145, "Int-Banking Pmt Frm Hpc-PC09"),
            _line("rx", "2026-07-03", 24966844, "Someone else", tenant_id="other")],
        "commerce_invoice_settings": [], "vula_merchant_audit": [], "journal_entries": [], "journal_lines": [],
        "journal_post_failures": [], "commerce_bank_line_documents": [], "vula_books_audits": []})
    for mod in (da, br, service, data_checks, books_audit, accounting, cert, ledger, lv, ledger_events):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr(accounting, "is_vat_registered", lambda tid: False)
    monkeypatch.setattr(lv, "is_on", lambda tid: False)

    async def names(tid):
        return ["AwehBelekker (Pty) Ltd", "DIGG"], []
    monkeypatch.setattr(service, "own_business_names", names)
    n = iter(range(200, 300))

    async def number(tid, doc_type, direction="outbound"):
        return f"DIG-INV-{next(n):05d}"
    monkeypatch.setattr(service, "_next_invoice_number", number)
    return d


def _row(db, table, i):
    return next(r for r in db.rows[table] if r["id"] == i)


def test_a_certificate_is_known_by_its_own_title_not_a_mention():
    assert cert.looks_like("Cape Town High Performance Centre - Payment Certificate No. 2.pdf", "")
    assert cert.looks_like("scan.pdf", "This is Payment Certificate No. 3 for the project")
    assert cert.looks_like("scan.pdf", "", {"report_type": "Payment Certificate"})
    assert not cert.looks_like("Re: snags.pdf", "Email about the final account and payment certificate 16")
    assert not cert.looks_like("DIGG_Invoice_HPC_PC No2.pdf", "Invoice for Payment Certificate No. 2")


def test_what_a_certificate_says_is_read_from_whatever_shape_it_was_filed_in(db):
    docs = {d["id"]: d for d in db.rows["vula_filed_documents"]}
    assert cert.read(docs["pc2"])["number"] == 2 and cert.read(docs["pc2"])["amount_cents"] == 24966844
    assert cert.read(docs["pc3"])["amount_cents"] == 19968808                 # an older read: rand float
    assert cert.read(docs["pc14"])["number"] == 14 and cert.read(docs["pc14"])["amount_cents"] == 4902539
    assert cert.pc_number("Int-Banking Pmt Frm Hpc-PC09") == 9 and cert.pc_number("Magtape Credit Hpc-PC2 Digg") == 2
    assert cert.pc_number("FNB App Payment To Topcon") is None


def test_each_certificate_meets_its_invoice_and_its_payment(db):
    m = cert.match(T)
    by = {r["number"]: r for r in m["certificates"]}
    assert sorted(by) == [2, 3, 14, 15]
    assert by[2]["invoice"]["number"] == "DIGG-2026-HPC02" and by[2]["received"]["id"] == "r2"
    assert by[3]["invoice"]["number"] == "DIGG-2026-HPC03" and by[3]["received"]["id"] == "r3"
    assert by[14]["received"]["cents"] == 4902540                               # a cent of bank rounding
    assert by[15]["invoice"] is None and by[15]["received"] is None
    assert [u["number"] for u in m["unmatched_receipts"]] == [5, 9]            # paid, nothing on file
    kinds = [f["kind"] for f in cert.findings(m)]
    assert kinds.count("certificate_label") == 4 and "certificate_not_invoiced" in kinds
    assert kinds.count("receipt_no_certificate") == 2


def test_a_certificate_is_never_a_money_document_or_a_bill_to_book(db):
    ids = {d["id"] for d in da.money_docs(T)}
    assert not ids & {"pc2", "pc3", "pc15"} and "hpc02" in ids
    books = da.books_findings(T, da.money_docs(T), da.bills(T))
    assert not any(set(f["rows"]) & {"pc2", "pc3", "pc15"} for f in books)


def test_labelling_files_it_as_a_certificate_and_never_a_bill(db):
    out = cert.label(T, "pc2", actor={"email": "judy@example.com"})
    row = _row(db, "vula_filed_documents", "pc2")
    assert out["ok"] and row["category"] == cert.CATEGORY
    assert row["fields"]["certificate"]["number"] == 2 and row["fields"]["certificate"]["amount_cents"] == 24966844
    assert row["fields"]["_not_a_bill"] and row["fields"]["total_cents"] == 24966844   # nothing read is lost
    with pytest.raises(br.Refused, match="doesn't say"):
        cert.label(T, "mail")


def test_own_invoices_not_in_invoices_are_listed_with_their_payment(db):
    out = {x["id"]: x for x in da.sales_not_in_invoices(T)}
    assert set(out) == {"hpc02", "hpc03", "hpc14"}
    assert [r["id"] for r in out["hpc02"]["receipts"]] == ["r2"]
    assert "15% VAT added" in out["hpc14"]["text"] and out["hpc14"]["differs"]["number"] == "DIG-BILL-00108"


def test_add_to_invoices_creates_it_once_paid_on_the_bank_date(db):
    out = da.add_to_invoices(T, "hpc02", "r2", actor={"email": "judy@example.com"})
    assert out["ok"] and out["paid_on"] == "2026-07-03"
    inv = _row(db, "commerce_invoices", out["invoice"]["id"])
    assert inv.get("direction", "outbound") == "outbound"     # the table's default (migration 009)
    assert inv["total_cents"] == 24966844 and inv["status"] == "paid"
    assert inv["invoice_number"] == "DIGG-2026-HPC02" and inv["customer_name"] == "Oro Props (Pty) Ltd"
    assert _row(db, "vula_filed_documents", "hpc02")["commerce_invoice_id"] == inv["id"]
    line = _row(db, "commerce_bank_transactions", "r2")
    assert line["matched_invoice_id"] == inv["id"] and line["match_status"] == "matched"
    pays = [p for p in db.rows["commerce_invoice_payments"] if p["invoice_id"] == inv["id"]]
    assert len(pays) == 1 and str(pays[0]["paid_at"])[:10] == "2026-07-03"
    assert "hpc02" not in {x["id"] for x in da.sales_not_in_invoices(T)}
    with pytest.raises(br.Refused, match="already in Invoices"):
        da.add_to_invoices(T, "hpc02")


def test_add_to_invoices_refuses_what_would_count_a_sale_twice(db):
    with pytest.raises(br.Refused, match="Only the business's own invoices"):
        da.add_to_invoices(T, "pc2")                       # a certificate is never added as an invoice
    with pytest.raises(br.Refused, match="isn't the invoice's total"):
        da.add_to_invoices(T, "hpc03", "r2")
    with pytest.raises(br.Refused, match="already in Invoices"):
        da.add_to_invoices(T, "hpc14")                     # tied to DIG-BILL-00108 already
    db.rows["commerce_invoices"].append({"id": "b9", "tenant_id": T, "invoice_number": "DIG-INV-00009",
                                         "customer_name": "Oro Props", "direction": "outbound", "doc_type": "invoice",
                                         "status": "sent", "total_cents": 19968808, "issue_date": "2026-07-09"})
    with pytest.raises(br.Refused, match="may already be this invoice"):
        da.add_to_invoices(T, "hpc03", "r3")
    out = da.link_invoice(T, "hpc03", "b9")
    assert out["ok"] and _row(db, "vula_filed_documents", "hpc03")["commerce_invoice_id"] == "b9"
    with pytest.raises(br.Refused, match="correct the invoice first"):
        da.link_invoice(T, "hpc02", "b108")


def test_the_books_check_has_a_certificates_section(db):
    secs = {s["key"]: s for s in da.sections(T, check_files=False)}
    s = secs["certificates"]
    assert "4 payment certificates on file (2, 3, 14, 15)" in s["headline"]
    assert any(f["kind"] == "receipt_no_certificate" for f in s["findings"])
    review = da.worklist(T)
    assert len(review["certificates"]["certificates"]) == 4 and len(review["sales_not_in_invoices"]) == 3
    assert br.certificates_open(review["certificates"]) == 4 + 1 + 2
