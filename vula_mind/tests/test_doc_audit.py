"""19 Nov 2026 (Ian, DIGG: "check DIGG's documents"): the books check also checks the documents behind the
money — each against its own file, then the books against the documents, duplicates, suppliers, prices and
who has access (vula/commerce/doc_audit.py) — and the business fixes each item in Books review. The file
check reads only the PDF's own text and never changes a figure; every fix is read back and audited."""
import asyncio

import pytest

from vula.commerce import accounting, books_audit, books_review as br, data_checks, doc_audit as da, reread, service
from tests.fake_supabase import FakeSupabase

T = "t1"


def _pdf(*lines: str) -> bytes:
    import pymupdf as fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for ln in lines:
        page.insert_text((72, y), ln)
        y += 18
    data = doc.tobytes()
    doc.close()
    return data


FILES = {
    "u-good": _pdf("TAX INVOICE", "Solid Cape (Pty) Ltd   VAT 4123456789", "Invoice No: INV-7001",
                   "Subtotal 1 000.00", "VAT 150.00", "Total R 1 150.00"),
    "u-bad": _pdf("TAX INVOICE", "Build It Bellville", "Invoice No: BB-55", "Total R 2 300.00"),
    "u-photo": b"\xff\xd8\xff\xe0 not a pdf",
}


def _doc(i, category="Invoice", url=None, h=None, bill=None, **fields):
    return {"id": i, "tenant_id": T, "category": category, "filename": f"{i}.pdf", "fields": fields,
            "status": "filed", "content_hash": h or f"h-{i}", "file_url": url, "mime": "application/pdf",
            "commerce_invoice_id": bill, "supplier_id": None, "created_at": "2026-07-10T08:00:00Z",
            "project": "Bokaap", "summary": "", "source": "whatsapp"}


def _bill(i, number, supplier, total, status="sent", **kw):
    return {"id": i, "tenant_id": T, "invoice_number": number, "supplier": supplier, "direction": "inbound",
            "doc_type": "invoice", "status": status, "total_cents": total, "vat_cents": kw.pop("vat", 0),
            "subtotal_cents": kw.pop("sub", total), "total_paid_cents": kw.pop("paid", 0),
            "issue_date": kw.pop("issue_date", "2026-07-08"), "project": "Bokaap", **kw}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "vula_filed_documents": [
            # read right, and its file says so
            _doc("good", url="u-good", bill="b-good", supplier="Solid Cape", document_number="INV-7001",
                 tax_id="4123456789", date="2026-07-08", subtotal_cents=100000, vat_cents=15000, total_cents=115000,
                 line_items=[{"description": "cement", "total_cents": 100000}]),
            # the total was read as R2,500 but the file says R2,300; the bill was booked at the misread
            _doc("bad", url="u-bad", bill="b-bad", supplier="Build It", document_number="BB-55", date="2026-07-08",
                 total_cents=250000),
            # a photo: nothing to check it against; subtotal + VAT doesn't make the total
            _doc("photo", url="u-photo", supplier="Sea Point Hire", document_number="113631", date="2026-07-08",
                 subtotal_cents=200000, vat_cents=30000, total_cents=200000),
            # on file twice, never booked
            _doc("dup1", supplier="STE Scaffolding", document_number="STE00866", date="2026-07-01", total_cents=80000),
            _doc("dup2", supplier="STE Scaffolding", document_number="STE00866", date="2026-07-01", total_cents=80000),
            # already kept as a duplicate: never counted
            {**_doc("old", supplier="STE Scaffolding", document_number="STE00866", total_cents=80000), "status": "duplicate"},
            # another business's document
            {**_doc("zz-t2-doc", supplier="Nope", total_cents=999900), "tenant_id": "t2"}],
        "commerce_invoices": [
            _bill("b-good", "INV-7001", "Solid Cape", 115000, vat=15000, sub=100000),
            _bill("b-bad", "BB-55", "Build It", 250000),
            # the same bill twice in the books, one of them paid
            _bill("b-hpc1", "HPC-9", "HPC Doors", 640000, status="paid", paid=640000),
            _bill("b-hpc2", "HPC-9", "HPC Doors", 640000, issue_date="2026-07-20"),
            _bill("b-x", "X-1", "Nope", 100, tenant_id="t2")],
        "commerce_invoice_payments": [{"id": "p1", "tenant_id": T, "invoice_id": "b-hpc1", "amount_cents": 640000}],
        "commerce_suppliers": [
            {"id": "s1", "tenant_id": T, "name": "Solid Cape (Pty) Ltd", "tax_id": "4123456789"},
            {"id": "s2", "tenant_id": T, "name": "SOLID CAPE", "tax_id": "4123456789"},
            {"id": "s3", "tenant_id": T, "name": "Gardens Handiman", "tax_id": "12345"}],
        "vula_price_observations": [
            {"id": "o1", "tenant_id": T, "doc_id": "photo", "source_kind": "invoice", "vat_basis": "unknown"},
            {"id": "o2", "tenant_id": T, "doc_id": "photo", "source_kind": "invoice", "vat_basis": "unknown"},
            {"id": "o3", "tenant_id": T, "doc_id": "good", "source_kind": "invoice", "vat_basis": "incl"}],
        "vula_team_members": [
            {"id": "m1", "tenant_id": T, "name": "Judy", "role": "owner", "access": [], "active": True},
            {"id": "m2", "tenant_id": T, "name": "Sam", "role": "staff", "access": [], "active": True},
            {"id": "m3", "tenant_id": T, "name": "Lee", "role": "staff", "access": ["books"], "active": True},
            {"id": "m4", "tenant_id": T, "name": "Ann", "role": "accountant", "access": ["books"], "active": True,
             "expires_on": "2027-03-31"}],
        "vula_tenant_users": [{"tenant_id": T, "user_id": "u1", "role": "owner"},
                              {"tenant_id": T, "user_id": "u2", "role": "accountant"}],
        "vula_merchant_audit": [], "commerce_bank_transactions": [], "journal_entries": [],
        "commerce_expenses": [], "vula_books_audits": [], "commerce_invoice_settings": []})
    for mod in (da, br, service, data_checks, books_audit, accounting):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr(accounting, "is_vat_registered", lambda tid: False)

    async def names(tid):
        return ["DIGG"], []
    monkeypatch.setattr(service, "own_business_names", names)

    async def download(url):
        return FILES[url]
    monkeypatch.setattr(reread, "_download", download)
    return d


def _row(db, table, i):
    return next(r for r in db.rows[table] if r["id"] == i)


def _sec(rep, key):
    return next(s for s in rep["sections"] if s["key"] == key)


def test_each_document_is_checked_against_its_own_file_once(db):
    docs = da.money_docs(T)
    assert {d["id"] for d in docs} == {"good", "bad", "photo", "dup1", "dup2"}   # live, this business only
    got = da.refresh_file_checks(T, docs)
    assert got == {"checked": 3, "left": 0, "failed": 0}
    assert _row(db, "vula_filed_documents", "good")["fields"]["_file_check"]["missing"] == []
    bad = _row(db, "vula_filed_documents", "bad")["fields"]["_file_check"]
    assert bad["text"] and bad["missing"] == [{"field": "total_cents", "cents": 250000}]
    assert _row(db, "vula_filed_documents", "photo")["fields"]["_file_check"]["text"] is False
    assert _row(db, "vula_filed_documents", "bad")["fields"]["total_cents"] == 250000       # never changed
    # a file is read once — until it changes
    assert da.refresh_file_checks(T, da.money_docs(T))["checked"] == 0
    _row(db, "vula_filed_documents", "bad")["content_hash"] = "h-new"
    assert da.refresh_file_checks(T, da.money_docs(T))["checked"] == 1


def test_the_books_check_names_every_document_problem(db):
    rep = books_audit.run(T)
    fig = _sec(rep, "doc_figures")
    by = {f["rows"][0]: f for f in fig["findings"]}
    assert set(by) == {"bad", "photo"}
    assert by["bad"]["severity"] == "problem" and "R2,500.00 isn't in the document" in by["bad"]["text"]
    assert by["photo"]["problems"] == ["totals"]
    assert fig["status"] == "problem" and fig["files"]["no_text"] == 1
    books = _sec(rep, "doc_books")
    kinds = sorted(f["kind"] for f in books["findings"])
    assert kinds == ["not_booked", "not_booked", "not_booked"]           # photo + the two STE copies
    dups = _sec(rep, "doc_duplicates")
    assert sorted(f["kind"] for f in dups["findings"]) == ["bill_duplicate", "doc_duplicate"]
    bd = next(f for f in dups["findings"] if f["kind"] == "bill_duplicate")
    assert bd["rows"] == ["b-hpc1", "b-hpc2"] and bd["cents"] == 640000 and dups["status"] == "problem"
    sup = {f["kind"] for f in _sec(rep, "suppliers")["findings"]}
    assert sup == {"supplier_duplicate", "vat_number_invalid"}
    pv = _sec(rep, "price_vat")["findings"][0]
    assert pv["count"] == 2 and pv["rows"] == ["photo"]
    acc = _sec(rep, "access")
    assert [p["name"] for p in acc["people"]["people"] if p["full"]] == ["Judy", "Sam"]   # Sam: no sections chosen
    assert acc["findings"][0]["kind"] == "full_by_default"
    assert "phone" not in str(acc) and "email" not in str(acc)
    assert rep["summary"]["clean"] is False
    # nothing of another business
    assert "zz-t2-doc" not in str(rep) and "b-x" not in str(rep)


def test_a_bill_booked_at_a_misread_amount_is_listed_against_its_document(db):
    da.refresh_file_checks(T, da.money_docs(T))
    _row(db, "vula_filed_documents", "bad")["fields"]["total_cents"] = 230000   # corrected by hand elsewhere
    _row(db, "vula_filed_documents", "bad")["fields"].pop("_file_check")
    found = da.books_findings(T, da.money_docs(T), da.bills(T))
    m = [f for f in found if f["kind"] == "bill_amount"]
    assert m and m[0]["bill"] == "b-bad" and "R2,500.00" in m[0]["text"] and "R2,300.00" in m[0]["text"]


def test_the_worklist_reads_things_as_they_are_now(db):
    da.refresh_file_checks(T, da.money_docs(T))
    w = da.worklist(T)
    assert [d["id"] for d in w["doc_figures"]] == ["bad", "photo"]
    assert {d["id"] for d in w["not_booked"]} == {"photo", "dup1", "dup2"}
    assert w["bill_duplicates"][0]["keep"] == "b-hpc1"
    assert w["price_vat"]["count"] == 2 and [d["id"] for d in w["price_vat"]["docs"]] == ["photo"]
    db.rows["vula_books_audits"].append({"id": "a1", "tenant_id": T, "report": {"sections": []},
                                         "created_at": "2026-11-19T09:00:00Z"})
    out = br.review(T)
    assert out["open"]["doc_figures"] == 2 and out["open"]["bill_duplicates"] == 1
    assert "price_vat" not in out["open"] and out["documents"]["access"]["logins"] == {"owner": 1, "accountant": 1}


def test_correcting_figures_keeps_the_old_values_and_flags_the_bill(db):
    da.refresh_file_checks(T, da.money_docs(T))
    got = da.correct_figures(T, "bad", {"total_cents": 230000}, "the PDF says R2,300.00", actor={"email": "o@x"})
    f = _row(db, "vula_filed_documents", "bad")["fields"]
    assert got["ok"] and f["total_cents"] == 230000 and "_file_check" not in f
    assert f["_corrected"][0]["old"] == 250000 and f["_corrected"][0]["reason"] == "the PDF says R2,300.00"
    assert got["bill_differs"]["id"] == "b-bad"                       # the bill still says R2,500 — not changed here
    assert _row(db, "commerce_invoices", "b-bad")["total_cents"] == 250000
    assert db.rows["vula_merchant_audit"][-1]["action"] == "document_figures_corrected"
    with pytest.raises(br.Refused):
        da.correct_figures(T, "bad", {"total_cents": 230000}, "")       # a reason is required
    with pytest.raises(br.Refused, match="10 digits"):
        da.correct_figures(T, "bad", {"tax_id": "123"}, "the VAT number on it")
    # then the bill is booked at its document's figures
    da.refresh_file_checks(T, da.money_docs(T))
    got = da.match_document(T, "b-bad", "booked at the misread total")
    assert got["ok"] and _row(db, "commerce_invoices", "b-bad")["total_cents"] == 230000
    assert got["was"]["total_cents"] == 250000


def test_a_paid_bill_is_never_changed_or_cancelled(db):
    with pytest.raises(br.Refused, match="paid twice"):
        da.cancel_duplicate_bill(T, "b-hpc1", "b-hpc2", "the same invoice twice")
    got = da.cancel_duplicate_bill(T, "b-hpc2", "b-hpc1", "the same invoice twice")
    assert got["ok"] and _row(db, "commerce_invoices", "b-hpc2")["status"] == "cancelled"
    assert "Duplicate of HPC-9" in _row(db, "commerce_invoices", "b-hpc2")["cancel_reason"]
    db.rows["vula_filed_documents"].append(_doc("hpcdoc", bill="b-hpc1", supplier="HPC Doors", document_number="HPC-9",
                                                total_cents=600000))
    with pytest.raises(br.Refused, match="paid"):
        da.match_document(T, "b-hpc1", "the document says less")


def test_confirm_book_not_a_bill_and_duplicate(db):
    da.refresh_file_checks(T, da.money_docs(T))
    # the photo: subtotal/VAT don't add up but the owner checked the paper copy
    with pytest.raises(br.Refused):
        asyncio.run(da.book(T, "bad"))                                  # already booked
    da.confirm(T, "photo", "checked against the paper copy")
    assert da.figure_problems(_row(db, "vula_filed_documents", "photo")) == []
    booked = {}

    async def commit(tenant_id, fields, **kw):
        booked.update(kw)
        _row(db, "vula_filed_documents", kw["filed_document_id"])["commerce_invoice_id"] = "b-new"
        return {"ok": True, "committed": True}
    import vula.commerce.service as svc
    orig = svc.commit_inbound_document
    svc.commit_inbound_document = commit
    try:
        got = asyncio.run(da.book(T, "photo"))
    finally:
        svc.commit_inbound_document = orig
    assert got["ok"] and got["bill_id"] == "b-new" and booked["notify"] is False
    da.mark_duplicate(T, "dup2", "dup1")
    assert _row(db, "vula_filed_documents", "dup2")["status"] == "duplicate"
    assert [d["id"] for d in da.worklist(T)["doc_duplicates"]] == []
    da.not_a_bill(T, "dup1", "a pro-forma, the real invoice came later")
    assert {d["id"] for d in da.worklist(T)["not_booked"]} == set()


def test_saying_whether_prices_include_vat_updates_every_price_from_that_document(db):
    got = da.set_vat_basis(T, "photo", "excl")
    assert got == {"ok": True, "prices": 2, "basis": "excl"}
    assert {r["vat_basis"] for r in db.rows["vula_price_observations"] if r["doc_id"] == "photo"} == {"excl"}
    from vula.commerce import price_book
    assert price_book.document_vat_basis(_row(db, "vula_filed_documents", "photo")["fields"]) == "excl"
    with pytest.raises(br.Refused):
        da.set_vat_basis(T, "photo", "maybe")


def test_another_business_s_documents_and_bills_are_never_touched(db):
    with pytest.raises(br.Refused):
        da.confirm(T, "zz-t2-doc", "")
    with pytest.raises(br.Refused):
        da.cancel_duplicate_bill(T, "b-x", "b-hpc1", "not ours")


def test_staff_cannot_use_the_document_fixes(monkeypatch):
    from fastapi import HTTPException
    from vula.api import books_audit as api, tenant_auth

    async def scope(auth, tid):
        return {"full": False, "role": "staff", "access": ["books"]}
    monkeypatch.setattr(tenant_auth, "member_scope", scope)

    class Req:
        headers = {"authorization": "Bearer x"}
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.doc_figures(T, "bad", api.FiguresBody(changes={"total_cents": 1}, reason="x y z"), Req()))
    assert e.value.status_code == 403
    with pytest.raises(HTTPException):
        asyncio.run(api.bill_cancel_duplicate(T, "b-hpc2", api.OfBody(of_id="b-hpc1", reason="dup"), Req()))


def test_the_business_s_own_invoices_are_never_offered_as_bills(db):
    """DIGG's first run (19 Nov 2026): 8 of 43 'not booked' invoices were DIGG's own invoices to clients,
    2 more were one of them with the client read as the supplier, and some were addressed to another
    business. None of them is a cost to book."""
    db.rows["vula_filed_documents"] += [
        _doc("own1", supplier="AwehBelekker (Pty) Ltd t/a DIGG", document_number="DIGG-2026-HPC02",
             customer="Vizion Developments", date="2026-07-01", total_cents=24966844),
        _doc("twin1", supplier="Vizion Developments (Pty) Ltd", date="2026-07-01", total_cents=24966844),
        _doc("other1", supplier="Teck Flooring Pty Ltd", document_number="51934", customer="Enziglo (Pty) Ltd",
             date="2026-07-15", total_cents=27843245)]

    async def names(tid):
        return ["DIGG", "AwehBelekker (Pty) Ltd"], []
    import vula.commerce.service as svc
    orig = svc.own_business_names
    svc.own_business_names = names
    try:
        found = {f["rows"][0]: f for f in da.books_findings(T, da.money_docs(T), da.bills(T))}
        assert found["own1"]["kind"] == "our_invoice" and "own invoice to Vizion Developments" in found["own1"]["text"]
        assert found["twin1"]["kind"] == "our_invoice" and found["twin1"]["twin"]["id"] == "own1"
        assert found["other1"]["kind"] == "not_booked" and found["other1"]["doc"]["addressed_to_other"]
        assert "addressed to Enziglo" in found["other1"]["text"] and found["other1"]["severity"] == "attention"
        w = da.worklist(T)
        assert {d["id"] for d in w["our_invoices"]} == {"own1", "twin1"}
        assert "own1" not in {d["id"] for d in w["not_booked"]}
        with pytest.raises(br.Refused, match="own invoice"):
            asyncio.run(da.book(T, "own1"))
        da.not_a_bill(T, "own1", "Our own invoice to Vizion — in Invoices")
        assert "own1" not in {d["id"] for d in da.worklist(T)["our_invoices"]}
    finally:
        svc.own_business_names = orig
