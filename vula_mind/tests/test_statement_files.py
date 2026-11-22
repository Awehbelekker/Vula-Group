"""19 Nov 2026 (Ian, DIGG: "is everything in the correct folder so an accountant can navigate — bank
statements labelled per account?"). Every bank statement is kept under its account
(vula/commerce/statement_files.py), and the year's money documents are shown in an accountant's folders
(vula/commerce/finance_folders.py)."""
import asyncio
import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest

from vula import private_docs
from vula.commerce import bank_accounts, finance_folders as ff, project_file as pf, statement_files as sf
from vula.integrations import doc_filing
from tests.fake_supabase import FakeSupabase
from tests.test_statement_compare import NUMBER, OFFICIAL

T = "t1"


@pytest.fixture
def db(monkeypatch, tmp_path):
    d = FakeSupabase({"vula_filed_documents": [], "commerce_bank_accounts": [], "commerce_bank_transactions": [],
                      "commerce_invoices": [], "commerce_suppliers": []})
    for mod in (sf, bank_accounts, ff, pf):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr(bank_accounts, "available", lambda: True)
    monkeypatch.setattr(private_docs, "private_accounts", lambda tid: {})
    monkeypatch.setattr(ff, "_business", lambda tid: "DIGG")

    async def fake_file(tenant_id, *, filename, data, content_type, category="", summary="", fields=None,
                        source="whatsapp", filed_by="", status="filed", **_kw):
        import hashlib
        row = {"id": f"doc{len(d.rows['vula_filed_documents']) + 1}", "tenant_id": tenant_id, "filename": filename,
               "category": category, "summary": summary, "fields": fields or {}, "source": source, "status": status,
               "file_url": f"https://x.supabase.co/storage/v1/object/public/documents/{tenant_id}/{filename}",
               "content_hash": hashlib.sha256(data).hexdigest(), "project": None, "created_at": "2026-09-30T10:00:00"}
        d.rows["vula_filed_documents"].append(row)
        return dict(row)
    monkeypatch.setattr(doc_filing, "file_document", fake_file)
    d.tmp = tmp_path
    return d


def _pdf(db, name="FNB Sep.pdf", data=b"%PDF-1.4 september"):
    p = db.tmp / name
    p.write_bytes(data)
    return p


def test_a_statement_is_kept_under_its_account_with_its_period(db):
    row = asyncio.run(sf.keep(T, _pdf(db), filename="FNB Sep.pdf", source="whatsapp", text=OFFICIAL))
    assert row["category"] == "Bank Statement" and row["project"] is None
    f = row["fields"]
    acct = db.rows["commerce_bank_accounts"][0]
    assert f["bank_account_id"] == acct["id"] and f["account"] == "…5678" and f["bank"] == "FNB"
    assert (f["period_from"], f["period_to"], f["opening_cents"], f["closing_cents"]) == \
        ("2026-09-22", "2026-09-25", 100000, 388950)
    assert row["summary"] == "FNB statement …5678, 22 Sep 2026 to 25 Sep 2026"
    assert NUMBER not in str(row["fields"]) + row["summary"]          # only the last four digits


def test_the_same_file_again_or_a_copy_filed_as_something_else_is_relabelled_not_filed_twice(db):
    import hashlib
    data = b"%PDF-1.4 september"
    db.rows["vula_filed_documents"].append({"id": "old", "tenant_id": T, "category": "Account Statement",
                                            "project": "Porterfield", "fields": {"note": "kept"},
                                            "filename": "FNB Sep.pdf", "content_hash": hashlib.sha256(data).hexdigest()})
    row = asyncio.run(sf.keep(T, _pdf(db, data=data), filename="FNB Sep.pdf", source="dashboard", text=OFFICIAL,
                              official=True))
    assert row["relabelled"] and len(db.rows["vula_filed_documents"]) == 1
    old = db.rows["vula_filed_documents"][0]
    assert old["category"] == "Bank Statement" and old["project"] is None
    assert old["fields"]["note"] == "kept" and old["fields"]["official"] is True and old["fields"]["account"] == "…5678"


def test_a_private_account_statement_is_never_the_business_file(db, monkeypatch):
    monkeypatch.setattr(private_docs, "private_accounts", lambda tid: {NUMBER: "member-1"})
    assert asyncio.run(sf.keep(T, _pdf(db), filename="x.pdf", source="whatsapp", text=OFFICIAL)) is None
    assert db.rows["vula_filed_documents"] == []


def test_keeping_never_stops_a_statement_being_read(db):
    assert asyncio.run(sf.keep(T, db.tmp / "gone.pdf", filename="gone.pdf", source="whatsapp")) is None


def test_every_statement_read_is_kept_with_where_it_came_from(db, monkeypatch):
    from vula.commerce import bank_rec
    from vula.ingestion import fnb_statement
    monkeypatch.setattr(fnb_statement, "pdf_text", lambda path, pwd=None: OFFICIAL)
    monkeypatch.setattr(bank_rec, "get_statement_password", lambda tid: None)
    monkeypatch.setattr(bank_rec, "reconcile", AsyncMock(return_value={"saved": 4}))
    monkeypatch.setattr(bank_rec, "supersede_misread", lambda tid, txns: {})
    keep = AsyncMock(return_value={"id": "doc1"})
    monkeypatch.setattr(sf, "keep", keep)
    asyncio.run(bank_rec.ingest_statement(T, _pdf(db), source_file="FNB Sep.pdf", source="whatsapp"))
    assert keep.await_args.kwargs["source"] == "whatsapp" and keep.await_args.kwargs["filename"] == "FNB Sep.pdf"


def test_coverage_shows_the_months_to_fetch_per_account(db):
    from datetime import date
    asyncio.run(sf.keep(T, _pdf(db), filename="FNB Sep.pdf", source="dashboard", text=OFFICIAL, official=True))
    db.rows["commerce_bank_transactions"] += [
        {"id": "l1", "tenant_id": T, "bank_account": NUMBER, "txn_date": "2026-08-14", "match_status": "unmatched"},
        {"id": "l2", "tenant_id": T, "bank_account": NUMBER, "txn_date": "2026-09-23", "match_status": "matched"},
        {"id": "l3", "tenant_id": T, "bank_account": None, "txn_date": "2026-09-23", "match_status": "unmatched"},
        {"id": "l4", "tenant_id": T, "bank_account": None, "txn_date": "2026-09-23", "match_status": "ignored"}]
    c = sf.coverage(T, date(2026, 3, 1), date(2026, 10, 31))
    a = c["accounts"][0]
    assert a["account"] == "FNB …5678"
    sep = next(m for m in a["months"] if m["month"] == "2026-09")
    aug = next(m for m in a["months"] if m["month"] == "2026-08")
    assert sep == {"month": "2026-09", "statement": True, "official": True, "lines": 1}
    assert aug == {"month": "2026-08", "statement": False, "official": False, "lines": 1}
    assert "2026-08" in a["missing"] and "2026-09" not in a["missing"] and len(a["months"]) == 8
    assert c["lines_on_no_account"] == 1


def _doc(i, cat, d, **kw):
    return {"id": i, "tenant_id": T, "category": cat, "fields": {"date": d, **kw.pop("fields", {})},
            "filename": f"{i}.pdf", "file_url": f"https://x.supabase.co/storage/v1/object/public/documents/t1/{i}.pdf",
            "created_at": f"{d}T09:00:00", "status": "filed", **kw}


def _folders_db(db):
    asyncio.run(sf.keep(T, _pdf(db), filename="FNB Sep.pdf", source="dashboard", text=OFFICIAL, official=True))
    db.rows["commerce_bank_accounts"][0]["name"] = "Business cheque"
    db.rows["commerce_suppliers"].append({"id": "s1", "tenant_id": T, "name": "Build It", "aliases": ["BUILDIT"]})
    db.rows["commerce_invoices"].append({"id": "ci1", "tenant_id": T, "direction": "outbound"})
    db.rows["vula_filed_documents"] += [
        _doc("b1", "Invoice", "2026-04-02", supplier_id="s1", fields={"total_cents": 23000, "document_number": "S-9"}),
        _doc("b2", "Invoice", "2026-05-02", fields={"supplier": "BUILDIT", "total_cents": 11500}),
        _doc("s1", "Invoice", "2026-06-10", commerce_invoice_id="ci1", fields={"supplier": "DIGG", "total_cents": 50000}),
        _doc("p1", "Proof of Payment", "2026-06-12"),
        _doc("old", "Invoice", "2025-12-01", fields={"total_cents": 999}),              # last year
        _doc("dup", "Invoice", "2026-04-02", status="duplicate", fields={"total_cents": 23000}),
        _doc("pg2", "Invoice", "2026-04-02", fields={"page_of": "b1"}),
        _doc("dwg", "Drawing / Plan", "2026-04-02"),                                    # not a money document
    ]


def test_the_folders_an_accountant_expects(db):
    _folders_db(db)
    tree = ff.organise(T, "2026-03-01")
    assert (tree["from"], tree["to"]) == ("2026-03-01", "2027-02-28")
    by = {f["key"]: f for f in tree["folders"]}
    bank = by["bank"]["groups"]
    assert [g["name"] for g in bank] == ["Business cheque …5678"] and bank[0]["items"][0]["official"]
    bills = {g["name"]: [i["amount_cents"] for i in g["items"]] for g in by["bills"]["groups"]}
    assert list(bills.values()) == [[23000, 11500]]            # one supplier, whichever spelling; no duplicate/page/old
    assert list(bills)[0].startswith("Build It")
    assert [i["amount_cents"] for g in by["sales"]["groups"] for i in g["items"]] == [50000]
    assert [g["name"] for g in by["pops"]["groups"]] == ["2026-06 June"]
    assert by["legal"]["count"] == 0
    assert all("dwg" != i["id"] for f in tree["folders"] for g in f["groups"] for i in g["items"])
    assert tree["coverage"]["accounts"][0]["account"] == "Business cheque …5678"


def test_the_year_as_a_zip_with_an_index_that_adds_up(db):
    from openpyxl import load_workbook
    _folders_db(db)
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url.endswith("p1.pdf"):
            raise RuntimeError("gone")
        return b"%PDF " + url.encode()
    data, man = ff.build_zip(T, "2026-03-01", fetch=fetch)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    root = man["root"]
    assert f"{root}/Index.xlsx" in names
    assert any(n.startswith(f"{root}/01 Bank statements/Business cheque …5678/2026-09-25 FNB statement") for n in names)
    assert any(n.startswith(f"{root}/03 Supplier invoices and delivery notes/Build It") and "2026-04-02 Build It" in n
               and "S-9" in n for n in names)
    assert man["documents"] == 5 and man["files"] == 4 and len(man["files_failed"]) == 1
    ws = load_workbook(io.BytesIO(z.read(f"{root}/Index.xlsx"))).active
    rows = [r for r in ws.iter_rows(min_row=4, values_only=True) if r[0]]
    assert len(rows) == 5 and any(r[5] == "(file could not be fetched)" for r in rows)
    one, man1 = ff.build_zip(T, "2026-03-01", folder="bank", fetch=lambda u: b"%PDF")
    assert man1["documents"] == 1
    with pytest.raises(ValueError):
        ff.build_zip(T, "2026-03-01", folder="nope", fetch=fetch)
    with pytest.raises(LookupError):
        ff.build_zip(T, "2024-03-01", fetch=fetch)


def test_backfill_keeps_the_originals_on_the_server_and_says_which_are_gone(db, monkeypatch):
    from config import settings
    root = db.tmp / "uploads"
    (root / T / "m_1").mkdir(parents=True)
    (root / T / "m_1" / "FNB Sep.pdf").write_bytes(b"%PDF-1.4 september")
    monkeypatch.setattr(settings, "upload_dir", root)
    monkeypatch.setattr(sf, "read_text", lambda tid, path, password=None: OFFICIAL)
    db.rows["commerce_bank_transactions"] += [
        {"id": "l1", "tenant_id": T, "source_file": "FNB Sep.pdf", "txn_date": "2026-09-22"},
        {"id": "l2", "tenant_id": T, "source_file": "62012345678_Oct.pdf", "txn_date": "2026-10-02"},
        {"id": "l3", "tenant_id": T, "source_file": "whatsapp_pop", "txn_date": "2026-10-02"}]
    out = asyncio.run(sf.backfill(T))
    assert out["kept"] == 1 and out["missing"] == ["…5678_Oct.pdf"] and out["statement_files"] == 2
    assert db.rows["vula_filed_documents"][0]["category"] == "Bank Statement"
    again = asyncio.run(sf.backfill(T))
    assert again["kept"] == 0 and again["relabelled"] == 1 and len(db.rows["vula_filed_documents"]) == 1


def test_another_business_documents_are_never_in_the_folders(db):
    _folders_db(db)
    assert all(f["count"] == 0 for f in ff.organise("t2", "2026-03-01")["folders"])


def test_a_backfill_asked_for_runs_once_for_that_business_only(db, monkeypatch):
    db.rows["vula_tenant_config"] = [
        {"tenant_id": T, "features": {"statement_backfill": True, "books_v2": False}},
        {"tenant_id": "t2", "features": {"trade_costing": True}}]
    ran = []

    async def fake_backfill(tid):
        ran.append(tid)
        return {"kept": 2, "relabelled": 1, "missing": ["a.pdf"], "statement_files": 3}
    monkeypatch.setattr(sf, "backfill", fake_backfill)
    done = asyncio.run(sf.run_requested())
    assert ran == [T] and done[T]["kept"] == 2
    assert db.rows["vula_tenant_config"][0]["features"] == {"statement_backfill": False, "books_v2": False}
    assert db.rows["vula_tenant_config"][1]["features"] == {"trade_costing": True}
    assert asyncio.run(sf.run_requested()) == {} and ran == [T]          # once
