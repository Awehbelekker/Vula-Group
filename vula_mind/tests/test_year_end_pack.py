"""22 Nov 2026 (Ian: the accountants have DIGG's read-only logins; next is "the year-end pack"). One
financial year as the accountant compiles the annual financial statements from it
(vula/commerce/year_end_pack.py): every amount read back to the cent, account numbers by their last 4
digits only, and a business not on the one book never shown the old cash-only journal. Made-up business
and figures (the October 2026 set from test_books_reports, plus opening balances on the bank lines)."""
import asyncio
import io
import zipfile
from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from openpyxl import load_workbook

from vula.commerce import (accounting, bank_recon, books_reports, books_rebuild, books_review, cross_check, exports,
                           finance_folders as ff, finances, ledger, ledger_events as ev, ledger_v2 as lv,
                           project_file as pf, reports, service, statement_files as sf, year_end_pack as yep)
from vula.commerce.xlsx_check import value
from tests.fake_supabase import FakeSupabase
from tests.test_books_reports import BILL, FUEL, INV, T, _bank, _chart

NUMBER = "62012345678"
AUDIT = {"id": "a1", "tenant_id": T, "created_at": "2027-03-02T08:00:00", "agreed_by": "owner@example.com",
         "agreed_at": "2027-03-03T09:00:00", "agreed_note": "Two staff claims still to settle",
         "signed_off_by": None, "signed_off_at": None}


def _doc(i, cat, d, **fields):
    return {"id": i, "tenant_id": T, "category": cat, "fields": {"date": d, **fields}, "filename": f"{i}.pdf",
            "file_url": f"https://x.supabase.co/storage/v1/object/public/documents/t1/{i}.pdf",
            "created_at": f"{d}T09:00:00", "status": "filed", "summary": "", "supplier_id": None,
            "commerce_invoice_id": None}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({"commerce_accounts": _chart(), "journal_entries": [], "journal_lines": [],
                      "journal_post_failures": [], "commerce_invoice_settings": [{"tenant_id": T, "vat_registered": True}],
                      "commerce_books_settings": [],
                      "commerce_bank_accounts": [{"id": "ba-1", "tenant_id": T, "account_number": NUMBER,
                                                  "bank": "FNB", "name": "Cheque"}],
                      "commerce_invoices": [dict(INV), dict(BILL)], "commerce_invoice_payments": [],
                      "commerce_bank_transactions": [], "commerce_expenses": [dict(FUEL)], "commerce_orders": [],
                      "commerce_suppliers": [{"id": "sup1", "tenant_id": T, "name": "Build It", "tax_id": "4123"}],
                      "vula_filed_documents": [
                          _doc("d1", "Invoice", "2026-10-02", supplier="Build It", total_cents=23000,
                               document_number="S-9"),
                          _doc("d2", "Proof of Payment", "2026-10-10"),
                          _doc("old", "Invoice", "2025-10-02", total_cents=999)]})
    for mod in (lv, accounting, ledger, ev, service, books_reports, reports, finances, cross_check, books_rebuild,
                exports, ff, pf, sf, yep, books_review):
        monkeypatch.setattr(mod, "_client", lambda: d)
    on = {"v": True}
    monkeypatch.setattr(lv, "is_on", lambda tid: on["v"] and tid == T)
    d.switch = on
    monkeypatch.setattr(ff, "_business", lambda tid: "Mandla Designs")
    monkeypatch.setattr(yep, "_today", lambda: date(2027, 3, 15))
    monkeypatch.setattr(books_review, "latest", lambda tid: dict(AUDIT) if tid == T else None)
    monkeypatch.setattr(books_review, "review", lambda tid: {
        "report": {"id": "a1"}, "open": {"staff_claims": 1, "in_transit": 0, "no_account": 0},
        "in_transit": [], "drafts": [],
        "staff_claims": [{"date": "2026-10-03", "description": "Fuel", "paid_by_name": "Judy", "amount_cents": 11500}]})
    monkeypatch.setattr(reports, "render_pdf", lambda business, data: b"%PDF-1.4 statements")
    ev.invoice_state(T, INV)
    ledger.post_invoice_payment(T, INV, {"id": "p1", "amount_cents": 46000, "paid_at": "2026-10-10"})
    ledger.post_invoice_paid(T, {**INV, "status": "paid", "total_paid_cents": 46000, "paid_at": "2026-11-02"})
    ev.invoice_state(T, BILL)
    ledger.post_expense(T, FUEL)
    # the statement: R1 000 before the year's first line, then rent out, two receipts in
    d.rows["commerce_bank_transactions"] += [
        _bank("r1", "2026-10-07", "out", 11500, match_status="unmatched", account_code="rent", vat_cents=1500,
              balance_cents=88500, source_file=f"{NUMBER}_Oct.pdf"),
        _bank("b1", "2026-10-10", "in", 46000, matched_invoice_id="inv1", balance_cents=134500,
              description=f"Deposit ref {NUMBER}"),
        _bank("b2", "2026-11-02", "in", 69000, matched_invoice_id="inv1", balance_cents=203500)]
    ev.sync_bank_lines(T, ["b1", "b2", "r1"])
    return d


def _book(data):
    return load_workbook(io.BytesIO(data))


def _find(ws, text):
    for row in ws.iter_rows():
        for c in row:
            if c.value == text:
                return c.row
    raise AssertionError(f"{text!r} not on {ws.title}")


def test_the_year_and_a_year_not_started(db):
    y = yep.year(T, "2026-03-01")
    assert (y["from"], y["to"], y["until"], y["ended"], y["label"]) == ("2026-03-01", "2027-02-28", "2027-02-28",
                                                                       True, "2026-27")
    running = yep.year(T, None, today=date(2026, 11, 22))
    assert running["from"] == "2026-03-01" and running["until"] == "2026-11-22" and not running["ended"]
    with pytest.raises(ValueError, match="hasn't started"):
        yep.year(T, "2027-06-01")


def test_on_the_one_book_every_sheet_comes_from_the_journal_and_reads_back(db):
    data, man = yep.build_workbook(T, "2026-03-01", viewer="accounting@example.com")
    wb = _book(data)
    assert wb.sheetnames == ["Cover", "Trial balance", "Profit and loss", "Balance sheet", "General ledger", "VAT",
                             "Aged debtors", "Aged creditors", "Bank reconciliation", "Statements on file",
                             "Open items", "Documents"]
    assert man["source"] == "journal" and man["checks"]["trial_balance_balances"] \
        and man["checks"]["balance_sheet_balances"]
    tb = wb["Trial balance"]
    r = _find(tb, "Difference (must be 0)")
    assert value(tb, f"D{r}") == 0 and value(tb, f"D{r - 1}") == value(tb, f"E{r - 1}") > 0
    pnl = wb["Profit and loss"]
    year = accounting.pnl(T, "2026-03-01", "2027-02-28", "accrual")
    r = _find(pnl, "Net profit (loss)")
    assert round(value(pnl, f"N{r}") * 100) == year["net_profit_cents"]       # 12 months + the year column
    assert pnl["B5"].value == "Mar 2026" and pnl["M5"].value == "Feb 2027"
    assert _find(pnl, "Cash view (as the money moved)")
    cover = wb["Cover"]
    texts = [c.value for row in cover.iter_rows() for c in row if c.value]
    assert "Agreed by the business" in texts and "owner@example.com on 2027-03-03" in texts
    assert "Signed off by Vula" in texts and "Staff claims not paid back" in texts
    assert "accounting@example.com" in texts


def test_ageing_is_as_at_year_end_not_today(db):
    db.rows["commerce_invoice_payments"].append({"id": "p1", "tenant_id": T, "invoice_id": "inv1",
                                                 "amount_cents": 46000, "paid_at": "2026-10-10T10:00:00"})
    inv = db.rows["commerce_invoices"][0]
    inv.update({"status": "paid", "paid_at": "2026-11-02T10:00:00", "due_date": "2026-10-15"})
    db.rows["commerce_invoices"].append({**INV, "id": "inv2", "invoice_number": "INV-2", "issue_date": "2026-11-05",
                                         "status": "sent"})
    oct_end = finances.aged_as_at(T, "2026-10-31", "outbound")
    assert oct_end["total_cents"] == 69000 and [i["number"] for i in oct_end["items"]] == ["INV-1"]
    assert oct_end["aging"]["days_1_30"] == 69000 and oct_end["items"][0]["days_over"] == 16
    later = finances.aged_as_at(T, "2026-11-30", "outbound")
    assert [i["number"] for i in later["items"]] == ["INV-2"]                  # INV-1 marked paid on 2 Nov
    owed = finances.aged_as_at(T, "2027-02-28", "inbound")
    assert owed["total_cents"] == 23000 and owed["parties"] == [{"name": "Build It", "owed_cents": 23000, "count": 1}]
    assert owed["aging"]["days_90_plus"] == 23000 and owed["ties"]
    # the old aged creditors read through it (its query named a column that doesn't exist)
    assert finances.aged_creditors(T)["suppliers"] == [{"name": "Build It", "owed_cents": 23000}]
    with pytest.raises(ValueError):
        finances.aged_as_at(T, None, "sideways")


def test_bank_reconciliation_on_the_one_book_shows_the_missing_opening_balance(db):
    data, man = yep.build_workbook(T, "2026-03-01")
    ws = _book(data)["Bank reconciliation"]
    r = _find(ws, "Cheque …5678")
    assert round(value(ws, f"E{r}") * 100) == 203500 and round(value(ws, f"F{r}") * 100) == 103500
    assert round(value(ws, f"G{r}") * 100) == -100000                          # the R1 000 opening isn't in the books
    assert not man["checks"]["bank_reconciles"]


def test_not_on_the_one_book_uses_the_bank_lines_and_never_the_old_journal(db):
    db.switch["v"] = False
    data, man = yep.build_workbook(T, "2026-03-01")
    wb = _book(data)
    assert man["source"] == "bank"
    for name in ("Trial balance", "Balance sheet"):
        assert wb[name]["A4"].value == yep.NOT_ON_BOOK
    pnl = wb["Profit and loss"]
    year = accounting.pnl(T, "2026-03-01", "2027-02-28")
    r = _find(pnl, "Net profit (loss)")
    assert round(value(pnl, f"N{r}") * 100) == year["net_profit_cents"] == 46000 + 69000 - 11500
    rec = wb["Bank reconciliation"]
    r = _find(rec, "Cheque …5678")
    assert round(value(rec, f"E{r}") * 100) == 100000 and round(value(rec, f"J{r}") * 100) == 0
    assert rec[f"K{r}"].value == 0 and man["checks"]["bank_reconciles"]


def test_a_break_in_the_statement_chain_is_counted():
    rows = [{"id": "a", "txn_date": "2026-10-01", "direction": "in", "amount_cents": 1000, "balance_cents": 11000,
             "match_status": "unmatched", "bank_account": NUMBER},
            {"id": "b", "txn_date": "2026-10-02", "direction": "out", "amount_cents": 500, "balance_cents": 9500,
             "match_status": "unmatched", "bank_account": NUMBER},           # should be 10 500: a line is missing
            {"id": "c", "txn_date": "2026-10-03", "direction": "in", "amount_cents": 700, "match_status": "ignored",
             "bank_account": NUMBER, "balance_cents": 10200},
            {"id": "d", "txn_date": "2026-10-04", "direction": "in", "amount_cents": 300, "match_status": "unmatched",
             "bank_account": "", "balance_cents": None}]
    rec = bank_recon.statement_check(rows, "2026-03-01", "2027-02-28")
    a = rec["accounts"][0]
    assert (a["opening_cents"], a["books_cents"], a["statement_cents"], a["breaks"]) == (10000, 10500, 9500, 1)
    assert a["difference_cents"] == 1000 and not a["reconciles"] and not rec["all_reconcile"]
    assert rec["unassigned"] == {"lines": 1, "in_cents": 300, "out_cents": 0}


def test_the_pack_has_everything_and_no_full_account_number(db):
    def fetch(url):
        if url.endswith("d2.pdf"):
            raise RuntimeError("gone")
        return b"%PDF " + url.encode()
    data, man = yep.build_pack(T, "2026-03-01", fetch=fetch)
    z = zipfile.ZipFile(io.BytesIO(data))
    root = "Mandla Designs - year-end pack 2026-27"
    names = set(z.namelist())
    assert {f"{root}/Year-end workbook.xlsx", f"{root}/General ledger.csv", f"{root}/Bank lines.csv",
            f"{root}/Financial statements.pdf"} <= names
    assert any(n.startswith(f"{root}/Documents/03 Supplier invoices and delivery notes/") for n in names)
    assert man["documents"] == 2 and len(man["files_failed"]) == 1
    docs = _book(z.read(f"{root}/Year-end workbook.xlsx"))["Documents"]
    assert any(c.value == "(file could not be fetched)" for row in docs.iter_rows() for c in row)
    for n in names:                                    # every text part, the workbook's XML included
        raw = z.read(n)
        parts = [raw]
        if n.endswith(".xlsx"):
            inner = zipfile.ZipFile(io.BytesIO(raw))
            parts = [inner.read(x) for x in inner.namelist()]
        assert not any(NUMBER.encode() in p for p in parts), n
    assert "…5678" in z.read(f"{root}/Bank lines.csv").decode()
    assert yep.filename(man, "zip") == "Year-end_pack_2026-27.zip"


def test_another_business_is_never_read(db):
    db.rows["commerce_invoices"].append({**BILL, "id": "x1", "tenant_id": "other", "total_cents": 777700})
    db.rows["commerce_bank_transactions"].append(_bank("x9", "2026-10-08", "in", 555500, tenant_id="other"))
    data, _man = yep.build_workbook(T, "2026-03-01")
    wb = _book(data)
    flat = [c.value for ws in wb for row in ws.iter_rows() for c in row]
    assert 7777.0 not in flat and 5555.0 not in flat


# ── the routes ───────────────────────────────────────────────────────────────

def _request():
    from starlette.requests import Request
    return Request({"type": "http", "headers": [(b"authorization", b"Bearer x")], "method": "GET", "path": "/"})


def test_the_routes_are_for_owners_managers_and_the_accountant(db, monkeypatch):
    from vula.api import books_audit, server, tenant_auth
    monkeypatch.setattr(tenant_auth, "member_scope", AsyncMock(return_value={
        "role": "accountant", "access": ["finances"], "full": False, "email": "accounting@example.com"}))
    res = asyncio.run(books_audit.year_end_workbook(T, _request(), year="2026-03-01"))
    assert res.headers["content-disposition"] == 'attachment; filename="Year-end_pack_2026-27.xlsx"'
    assert "accounting@example.com" in [c.value for c in _book(res.body)["Cover"]["B"]]
    with pytest.raises(HTTPException) as e:
        asyncio.run(books_audit.year_end_workbook(T, _request(), year="2027-06-01"))
    assert e.value.status_code == 400
    monkeypatch.setattr(tenant_auth, "member_scope", AsyncMock(return_value={"role": "staff", "access": ["orders"],
                                                                             "full": False}))
    with pytest.raises(HTTPException) as e:
        asyncio.run(books_audit.year_end_pack_zip(T, _request(), year="2026-03-01"))
    assert e.value.status_code == 403
    import re
    assert any(re.match(p, f"/v1/commerce/{T}/admin/finance-folders/year-end-pack.zip") for p in server._ACCOUNTANT_READS)
    assert any(re.match(p, f"/v1/commerce/{T}/admin/finance-folders/year-end-pack.xlsx") for p in server._ACCOUNTANT_READS)
    cors = next(m for m in server.app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert "X-Files-Failed" in cors.kwargs["expose_headers"]       # else the dashboard never sees the failures
    from vula import accountant_activity
    path = f"/v1/commerce/{T}/admin/finance-folders/year-end-pack.zip"
    assert accountant_activity.action_for(path) == "accountant_downloaded"
    assert accountant_activity.PAGES[accountant_activity.page_of(path)] == "Year-end pack (zip)"


def test_a_year_of_more_than_a_thousand_bank_lines_is_read_whole(db):
    """The database hands back 1 000 rows a request; DIGG's year of 1 346 lines came back as 998, so the
    year's P&L was short until the lines were read page by page (accounting._txns)."""
    db.switch["v"] = False
    db.max_rows = 1000
    months = [f"2026-{m:02d}-15" for m in range(3, 13)] + ["2027-01-15", "2027-02-15"]
    db.rows["commerce_bank_transactions"] = [_bank(f"s{i:04d}", months[i % 12], "in", 100, match_status="unmatched")
                                             for i in range(1200)]
    assert accounting.pnl(T, "2026-03-01", "2027-02-28")["total_income_cents"] == 1200 * 100
    assert len(accounting.general_ledger(T, "2026-03-01", "2027-02-28")) == 1200
    data, _m = yep.build_workbook(T, "2026-03-01")          # the months add up to the year
    cov = sf.coverage(T, date(2026, 3, 1), date(2027, 2, 28))
    assert sum(m["lines"] for a in cov["accounts"] for m in a["months"]) == 1200


def test_management_accounts_on_the_one_book_read_the_journal(db):
    """The monthly management pack (management_pack.py) reads the same journal as this pack."""
    from vula.commerce import management_pack as mp
    f = mp.figures(T, "2026-10", today=date(2027, 3, 15))
    assert f["source"] == "journal"
    assert f["pnl"]["totals"]["this"]["net_cents"] == accounting.pnl(T, "2026-10-01", "2026-10-31", "accrual")["net_profit_cents"]
    assert [a["account"] for a in f["cash"]["accounts"]] == [r["account"] for r in
                                                             books_reports.cash_by_account(T, "2026-10-01", "2026-10-31")]
    assert f["vat"] is not None                                    # VAT registered in this set
    mp.workbook(f)                                                 # reads back
    assert NUMBER not in mp.summary_text(f)
