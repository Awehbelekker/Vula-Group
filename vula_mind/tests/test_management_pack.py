"""23 Nov 2026 (Ian: "Management accounts next"; DIGG only started trading this year). The month's
management accounts (vula/commerce/management_pack.py) and the send to the owners around the 5th
(vula/management_send.py). Every figure worked out in code from one source (the bank lines, or the one
book's journal), read back from the file; account numbers by their last 4 digits; owners only, once a
month. Made-up business and figures."""
import asyncio
import io
import json
from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from openpyxl import load_workbook

from vula import features, management_send as ms, team_index
from vula.commerce import (accounting, books_reports, books_review, finance_folders as ff, finances,
                           ledger_v2 as lv, management_pack as mp, service, statement_files as sf, year_end_pack as yep)
from tests.fake_supabase import FakeSupabase

T = "t1"
NUMBER = "62012345678"
START = 50000                      # R500 in the account before the first line


def _chart():
    return [{"id": f"acc-{c}", "tenant_id": T, "code": c, "name": n, "type": t, "vat_treatment": v,
             "deductible": d, "sort": s} for (c, n, t, v, d, s) in accounting.DEFAULT_CHART]


# (date, in/out, cents, account) — three months: August to October 2026
LINES = [("2026-08-03", "in", 300000, "sales"), ("2026-08-10", "out", 100000, "casual_labour"),
         ("2026-08-20", "out", 20000, "rent"),
         ("2026-09-05", "in", 200000, "sales"), ("2026-09-12", "out", 150000, "casual_labour"),
         ("2026-09-25", "out", 50000, "cost_of_sales"),
         ("2026-10-02", "in", 250000, "sales"), ("2026-10-09", "out", 120000, "casual_labour"),
         ("2026-10-15", "out", 20000, "rent"), ("2026-10-28", "out", 10000, "fuel")]


def _bank(spec, start=START):
    rows, bal = [], start
    for i, (d, way, cents, code) in enumerate(spec):
        bal += cents if way == "in" else -cents
        rows.append({"id": f"b{i:03d}", "tenant_id": T, "txn_date": d, "direction": way, "amount_cents": cents,
                     "account_code": code, "match_status": "unmatched", "balance_cents": bal, "vat_cents": 0,
                     "bank_account": NUMBER, "source_file": f"{NUMBER}_{d[:7]}.pdf", "description": code,
                     "reference": ""})
    return rows


def _extra():
    return [
        # on no account number: counted in the totals, never put on an account
        {"id": "u1", "tenant_id": T, "txn_date": "2026-10-20", "direction": "in", "amount_cents": 5000,
         "account_code": "other_income", "match_status": "unmatched", "balance_cents": None, "bank_account": None,
         "source_file": "x.pdf", "description": "interest", "reference": "", "vat_cents": 0},
        # set aside, and a proof of payment: neither is a bank line
        {"id": "x1", "tenant_id": T, "txn_date": "2026-10-03", "direction": "in", "amount_cents": 999999,
         "account_code": "sales", "match_status": "ignored", "balance_cents": None, "bank_account": NUMBER,
         "source_file": "y.pdf", "description": "misread", "reference": "", "vat_cents": 0},
        {"id": "pop", "tenant_id": T, "txn_date": "2026-10-04", "direction": "out", "amount_cents": 777700,
         "account_code": "cost_of_sales", "match_status": "unmatched", "balance_cents": None, "bank_account": None,
         "source_file": "whatsapp_pop", "description": "pop", "reference": "", "vat_cents": 0},
        # another business's money
        {"id": "o1", "tenant_id": "other", "txn_date": "2026-10-04", "direction": "in", "amount_cents": 123400,
         "account_code": "sales", "match_status": "unmatched", "balance_cents": 1, "bank_account": NUMBER,
         "source_file": "z.pdf", "description": "theirs", "reference": "", "vat_cents": 0}]


def _invoices():
    return [{"id": "inv1", "tenant_id": T, "invoice_number": "MD-INV-0101", "customer_name": "Atlantis Foods",
             "direction": "outbound", "doc_type": "invoice", "status": "part_paid", "total_cents": 80000,
             "issue_date": "2026-09-20", "due_date": "2026-10-20", "created_at": "2026-09-20T08:00:00"},
            {"id": "bill1", "tenant_id": T, "invoice_number": "S-9", "supplier": "Build It", "supplier_id": "sup1",
             "direction": "inbound", "doc_type": "invoice", "status": "sent", "total_cents": 40000,
             "issue_date": "2026-08-01", "due_date": "2026-08-31", "created_at": "2026-08-01T08:00:00"},
            {"id": "inv-x", "tenant_id": "other", "invoice_number": "X-1", "customer_name": "Theirs",
             "direction": "outbound", "doc_type": "invoice", "status": "sent", "total_cents": 999900,
             "issue_date": "2026-09-01", "due_date": "2026-09-30", "created_at": "2026-09-01T08:00:00"}]


def _make(spec=LINES):
    d = FakeSupabase({
        "commerce_accounts": _chart(), "commerce_bank_transactions": _bank(spec) + _extra(),
        "commerce_bank_accounts": [{"id": "ba-1", "tenant_id": T, "account_number": NUMBER, "bank": "FNB",
                                    "name": "Cheque", "private_to": None}],
        "commerce_invoices": _invoices(),
        "commerce_invoice_payments": [{"id": "p1", "tenant_id": T, "invoice_id": "inv1", "amount_cents": 30000,
                                       "paid_at": "2026-10-05T00:00:00"},
                                      {"id": "p2", "tenant_id": T, "invoice_id": "inv1", "amount_cents": 50000,
                                       "paid_at": "2026-11-03T00:00:00"}],          # after the month end
        "commerce_suppliers": [{"id": "sup1", "tenant_id": T, "name": "Build It"}],
        "commerce_invoice_settings": [{"tenant_id": T, "vat_registered": False}], "commerce_books_settings": [],
        "vula_filed_documents": [], "vula_statement_checks": [], "vula_merchant_audit": [],
        "journal_entries": [], "journal_lines": [], "vula_tenant_config": [{"tenant_id": T, "features": {}}]})
    d.max_rows = 1000
    return d


@pytest.fixture
def db(monkeypatch):
    d = _make()
    for mod in (accounting, books_reports, finances, service, sf, yep, books_review):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr(lv, "is_on", lambda tid: False)
    monkeypatch.setattr(ff, "_business", lambda tid: "Mandla Designs")
    monkeypatch.setattr(books_review, "latest", lambda tid: None)
    monkeypatch.setattr(mp, "_today", lambda: date(2026, 11, 23))
    return d


def _f(month="2026-10"):
    return mp.figures(T, month, today=date(2026, 11, 23))


# ── the month and what it's compared with ───────────────────────────────────

def test_the_month_and_its_comparisons(db):
    p = mp.period(T, "2026-10", today=date(2026, 11, 23))
    assert p["this"] == {"from": "2026-10-01", "to": "2026-10-31", "label": "October 2026"}
    assert p["previous"]["label"] == "September 2026" and p["last_year"] is None       # not trading a year ago
    assert p["ytd"]["from"] == "2026-08-03" and p["ytd"]["to"] == "2026-10-31"        # from the first trading day
    assert p["first_trading_day"] == "2026-08-03" and p["ended"]
    assert mp.period(T, None, today=date(2026, 11, 23))["month"] == "2026-10"           # the last full month
    assert mp.period(T, "2026-08", today=date(2026, 11, 23))["previous"] is None        # the first month


def test_a_business_trading_a_year_ago_gets_last_year(db):
    p = mp.period(T, "2027-09", today=date(2027, 10, 5))
    assert p["last_year"]["label"] == "September 2026"
    assert p["ytd"]["from"] == "2027-03-01"                                             # the financial year


@pytest.mark.parametrize("month,why", [("2026-13", "YYYY-MM"), ("Oct", "YYYY-MM"), ("2026-12", "hasn't started"),
                                       ("2026-07", "start in August 2026")])
def test_a_month_that_cant_be_shown(db, month, why):
    with pytest.raises(ValueError, match=why):
        mp.period(T, month, today=date(2026, 11, 23))


# ── the figures ──────────────────────────────────────────────────────────────

def test_profit_and_loss_from_the_bank_lines(db):
    f = _f()
    t = f["pnl"]["totals"]
    assert f["source"] == "bank"
    assert t["this"] == {"income_cents": 255000, "direct_cents": 120000, "gross_cents": 135000,
                         "expense_cents": 150000, "overheads_cents": 30000, "net_cents": 105000}
    assert t["previous"]["income_cents"] == 200000 and t["previous"]["net_cents"] == 0
    assert t["previous"]["direct_cents"] == 200000                    # casual labour + cost of sales
    assert t["ytd"]["income_cents"] == 755000 and t["ytd"]["expense_cents"] == 470000
    assert "last_year" not in t
    assert t["this"]["net_cents"] == accounting.pnl(T, "2026-10-01", "2026-10-31")["net_profit_cents"]
    sales = next(r for r in f["pnl"]["income"] if r["account"] == "Sales / income")
    assert sales["amounts"] == {"this": 250000, "previous": 200000, "ytd": 750000}
    labour = next(r for r in f["pnl"]["expenses"] if r["account"] == "Casual labour")
    assert labour["direct"] and not next(r for r in f["pnl"]["expenses"] if r["account"] == "Rent")["direct"]


def test_cash_per_account_and_lines_on_no_account(db):
    c = _f()["cash"]
    assert c["accounts"] == [{"account": "Cheque …5678", "lines": 4, "opening_cents": 230000, "in_cents": 250000,
                              "out_cents": 150000, "closing_cents": 330000, "as_at": "2026-10-28", "adds_up": True}]
    assert c["unassigned"] == {"lines": 1, "in_cents": 5000, "out_cents": 0}
    assert c["closing_cents"] == 330000 and c["in_cents"] == 255000 and c["out_cents"] == 150000


def test_whats_owed_at_the_month_end(db):
    o = _f()["owed"]
    assert o["customers"]["total_cents"] == 50000                   # R800 less R300 paid by then (not the November R500)
    assert o["customers"]["top"] == [{"name": "Atlantis Foods", "owed_cents": 50000}]
    assert o["suppliers"]["total_cents"] == 40000 and o["suppliers"]["over_60_cents"] == 40000   # 61 days past due


def test_key_figures(db):
    k = _f()["kpis"]
    assert k["gross_margin_pct"] == 52.9 and k["net_margin_pct"] == 41.2
    assert k["debtor_days"] == 6 and k["debtor_days_window"] == 92        # R500 owed ÷ R7 550 sales × 92 days
    assert k["cash_runway_months"] is None                                 # cash went up: no runway to show
    assert mp.pct(5, 0) is None and mp.pct(1, 3) == 33.3


def test_runway_with_three_losing_months(monkeypatch):
    spec = [("2026-07-01", "in", 1000000, "sales"), ("2026-08-10", "out", 200000, "casual_labour"),
            ("2026-09-10", "out", 200000, "casual_labour"), ("2026-10-10", "out", 200000, "casual_labour")]
    d = _make(spec)
    d.rows["commerce_bank_transactions"] = [r for r in d.rows["commerce_bank_transactions"] if r["id"] != "u1"]
    for mod in (accounting, books_reports, finances, service, sf, yep, books_review):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr(lv, "is_on", lambda tid: False)
    monkeypatch.setattr(ff, "_business", lambda tid: "Mandla Designs")
    monkeypatch.setattr(books_review, "latest", lambda tid: None)
    k = _f()["kpis"]
    assert k["average_monthly_cash_cents"] == -200000
    assert k["cash_runway_months"] == 2.3                                  # R4 500 ÷ R2 000 a month


def test_notes_are_built_from_the_figures(db):
    db.rows["vula_statement_checks"].append({"tenant_id": T, "account_number": "62099990000", "status": "proposed",
                                             "period_from": "2026-10-01", "period_to": "2026-10-31"})
    n = _f()["notes"]
    assert "Sales R2,550.00 — up 27.5% on September." in n
    assert "Profit for October: R1,050.00." in n
    assert "Largest cost: Casual labour R1,200.00 (80% of costs)." in n
    assert "Customers owe R500.00." in n
    assert "You owe suppliers R400.00, R400.00 of it over 60 days past due." in n
    assert "No statement on file for Cheque …5678 in October — these figures may be short." in n
    assert any(x.startswith("The official October statement for …0000 has been compared but not applied") for x in n)
    assert any("on no account number" in x for x in n)
    assert any(x.startswith("No comparison with October last year") for x in n)
    assert any("the books check isn't agreed yet" in x for x in n)


def test_a_statement_on_file_drops_its_note(db):
    db.rows["vula_filed_documents"].append({"id": "s1", "tenant_id": T, "category": sf.CATEGORY, "fields": {
        "bank_account_id": "ba-1", "period_from": "2026-10-01", "period_to": "2026-10-31", "official": True}})
    assert not any(x.startswith("No statement on file") for x in _f()["notes"])


def test_another_business_is_never_read(db):
    s = json.dumps(mp.public(_f()))
    assert "Theirs" not in s and "123400" not in s and "999900" not in s


# ── the files ────────────────────────────────────────────────────────────────

def test_the_workbook_reads_back(db):
    f = _f()
    book = load_workbook(io.BytesIO(mp.workbook(f)))
    assert book.sheetnames == ["Summary", "Profit and loss", "Cash", "Owed", "VAT"]
    ws = book["Profit and loss"]
    heads = [c.value for c in ws[4]]
    assert heads == ["Account", "October 2026", "September 2026", "Change", "Year to date"]


def test_a_figure_that_doesnt_add_up_is_refused(db):
    f = _f()
    f["pnl"]["totals"]["this"]["income_cents"] += 1                  # the totals no longer match the rows
    with pytest.raises(RuntimeError, match="didn't read back"):
        mp.workbook(f)


def test_the_pdf_and_message_show_no_full_account_number(db):
    import fitz
    f = _f()
    data = mp.pdf(f)
    assert data[:4] == b"%PDF"
    text = "".join(p.get_text() for p in fitz.open(stream=data, filetype="pdf"))
    assert "Mandla Designs — management accounts" in text and "R3,300.00" in text
    book = mp.workbook(f)
    cells = " ".join(str(c.value) for ws in load_workbook(io.BytesIO(book)) for row in ws.iter_rows() for c in row)
    msg = mp.summary_text(f)
    for blob in (text, cells, msg, json.dumps(mp.public(f))):
        assert NUMBER not in blob
    assert msg.startswith("*Mandla Designs — management accounts, October 2026*")
    assert "Profit for the month: R1,050.00 on sales of R2,550.00." in msg
    assert "In the bank at 31 Oct 2026: R3,300.00." in msg
    assert msg.count("Customers owe") == 1


def test_vat_for_a_registered_business(db):
    db.rows["commerce_invoice_settings"][0]["vat_registered"] = True
    db.rows["commerce_bank_transactions"][6]["vat_cents"] = 32609     # VAT in the October sales
    f = _f()
    assert f["vat"]["output_cents"] == 32609 + 652           # and 15/115 of the R50 other income
    assert any(x.startswith("VAT for October:") for x in f["notes"])
    assert load_workbook(io.BytesIO(mp.workbook(f)))["VAT"]["A4"].value == "VAT on sales"


def test_position_now(db):
    p = mp.position(T, today=date(2026, 10, 31))
    assert p["bank"]["total_cents"] == 330000 and p["bank"]["accounts"][0]["account"] == "Cheque …5678"
    assert p["owed_to_you_cents"] == 50000 and p["you_owe_cents"] == 40000
    assert p["profit_month_cents"] == 105000 and p["vat_month_cents"] is None


# ── the routes ───────────────────────────────────────────────────────────────

def test_routes(db):
    from vula.api import commerce, server
    with pytest.raises(HTTPException) as e:
        asyncio.run(commerce.admin_report_management(T, "2026-13"))
    assert e.value.status_code == 400
    out = asyncio.run(commerce.admin_report_management(T, "2026-10"))
    assert out["period"]["month"] == "2026-10" and "tenant_id" not in out
    pdf = asyncio.run(commerce.admin_report_management_pdf(T, "2026-10"))
    assert pdf.body[:4] == b"%PDF" and "Management_accounts_2026-10.pdf" in pdf.headers["content-disposition"]
    xlsx = asyncio.run(commerce.admin_report_management_xlsx(T, "2026-10"))
    assert xlsx.body[:2] == b"PK"
    for path in ("/v1/commerce/t1/admin/reports/management", "/v1/commerce/t1/admin/reports/management.pdf",
                 "/v1/commerce/t1/admin/reports/management.xlsx", "/v1/commerce/t1/admin/reports/position"):
        assert any(rx.match(path) for rx in server._ACCOUNTANT_READS)           # an accountant may read them
        rule = next(r for r in server._SECTION_RULES if r[1].match(path))
        assert rule[2] >= server._MONEY_TABS                                   # staff need Money access


# ── the send to the owners ───────────────────────────────────────────────────

SAST = ms.SAST


@pytest.mark.parametrize("when,month", [(datetime(2026, 12, 4, 23, 0, tzinfo=SAST), None),
                                        (datetime(2026, 12, 5, 7, 59, tzinfo=SAST), None),
                                        (datetime(2026, 12, 5, 8, 0, tzinfo=SAST), "2026-11"),
                                        (datetime(2026, 12, 20, 10, 0, tzinfo=SAST), "2026-11"),
                                        (datetime(2027, 1, 5, 9, 0, tzinfo=SAST), "2026-12")])
def test_when_it_is_due(when, month):
    assert ms.due_month(when) == month


TEAM = [{"id": "m1", "name": "Sam Owner", "role": "owner", "whatsapp": "27820000001", "email": "sam@example.com"},
        {"id": "m2", "name": "Lee Owner", "role": "owner", "whatsapp": None, "email": "lee@example.com"},
        {"id": "m3", "name": "Max Manager", "role": "manager", "whatsapp": "27820000003", "email": "max@example.com"}]


@pytest.fixture
def send(db, monkeypatch):
    from vula.api import whatsapp
    from vula.commerce import mail_router
    calls = {"doc": AsyncMock(return_value=True), "tpl": AsyncMock(return_value=True),
             "mail": AsyncMock(return_value=True)}
    monkeypatch.setattr(whatsapp, "_send_invoice_document", calls["doc"])
    monkeypatch.setattr(whatsapp, "send_template_message", calls["tpl"])
    monkeypatch.setattr(mail_router, "send_tenant_email", calls["mail"])
    monkeypatch.setattr(team_index, "active_members", lambda tid: [dict(m) for m in TEAM] if tid == T else [])
    monkeypatch.setattr(ms, "_link", lambda tid, month, pdf: "https://signed.example/m.pdf")
    return calls


def test_owners_only_whatsapp_then_email_and_once_a_month(db, send):
    out = asyncio.run(ms.send_month(T, "2026-10"))
    assert out["month"] == "2026-10"
    assert [(s["name"], s["via"]) for s in out["sent"]] == [("Sam Owner", "WhatsApp"),
                                                            ("Lee Owner", "email (your mailbox)")]
    assert send["doc"].await_count == 1 and send["doc"].await_args.args[0] == "27820000001"     # never the manager
    files = send["mail"].await_args.kwargs["attachments"]
    assert [x["filename"] for x in files] == ["Management_accounts_2026-10.pdf", "Management_accounts_2026-10.xlsx"]
    rows = [a for a in db.rows["vula_merchant_audit"] if a["action"] == ms.ACTION]
    assert {a["detail"]["to"] for a in rows} == {"27820000001", "lee@example.com"}
    again = asyncio.run(ms.send_month(T, "2026-10"))
    assert again["sent"] == [] and again["skipped"] == "already sent" and send["doc"].await_count == 1


def test_outside_the_24_hours_the_template_then_email(db, send):
    send["doc"].return_value = False
    out = asyncio.run(ms.send_month(T, "2026-10"))
    assert out["sent"][0]["via"] == "WhatsApp (message template)"
    assert send["tpl"].await_args.kwargs["header_document"]["filename"] == "Management_accounts_2026-10.pdf"
    send["tpl"].return_value = False
    db.rows["vula_merchant_audit"].clear()
    out = asyncio.run(ms.send_month(T, "2026-10"))
    assert out["sent"][0] == {"name": "Sam Owner", "via": "email (your mailbox)"}


def test_a_month_with_no_money_is_not_sent(db, send):
    db.rows["commerce_bank_transactions"] = [r for r in db.rows["commerce_bank_transactions"]
                                             if not str(r["txn_date"]).startswith("2026-09")]
    out = asyncio.run(ms.send_month(T, "2026-09"))
    assert out["sent"] == [] and "no money" in out["skipped"] and not send["doc"].await_count


def test_the_switch_decides_who_gets_it(db, send, monkeypatch):
    from vula.api import tenants
    monkeypatch.setattr(tenants, "is_active", lambda tid: True)
    when = datetime(2026, 11, 5, 9, 0, tzinfo=SAST)
    monkeypatch.setattr(features, "on", lambda tid, name: False)
    assert asyncio.run(ms.tick(when)) == 0 and not send["doc"].await_count
    monkeypatch.setattr(features, "on", lambda tid, name: name == "management_accounts" and tid == T)
    assert asyncio.run(ms.tick(datetime(2026, 11, 4, 9, 0, tzinfo=SAST))) == 0          # not the 5th yet
    assert asyncio.run(ms.tick(when)) == 2
    assert asyncio.run(ms.tick(datetime(2026, 11, 5, 10, 0, tzinfo=SAST))) == 0        # an hour later: done
