"""18 Nov 2026 (Ian: "reports read the journal"): for a business on the one book every report comes from
its journal, on the accrual or the cash view, and they agree with each other (vula/commerce/books_reports.py).
A business not on it keeps today's reports exactly. October 2026 at a VAT-registered business:

  - invoice INV-1 R1 150 (R150 VAT) issued 1 Oct; R460 paid 10 Oct, the rest 2 Nov (bank lines b1, b2);
  - a supplier bill R230 (R30 VAT) recorded 2 Oct, due 15 Oct, not paid;
  - rent R115 (R15 VAT) off the bank 7 Oct;
  - fuel R115 (R15 VAT) paid by a staff member with their own money 3 Oct.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from vula.commerce import (accounting, books_reports, cross_check, finances, ledger, ledger_events as ev,
                           ledger_v2 as lv, reports, service, tax)
from tests.fake_supabase import FakeSupabase

T = "t1"
OCT = ("2026-10-01", "2026-10-31")
INV = {"id": "inv1", "tenant_id": T, "invoice_number": "INV-1", "customer_name": "Sam", "direction": "outbound",
       "doc_type": "invoice", "status": "sent", "total_cents": 115000, "vat_cents": 15000,
       "issue_date": "2026-10-01", "source": None}
BILL = {"id": "bill1", "tenant_id": T, "invoice_number": "S-9", "supplier_id": "sup1", "supplier": "Build It",
        "direction": "inbound", "doc_type": "invoice", "status": "sent", "total_cents": 23000, "vat_cents": 3000,
        "issue_date": "2026-10-02", "due_date": "2026-10-15", "account_code": "cost_of_sales", "source": None}
FUEL = {"id": "e1", "tenant_id": T, "amount_cents": 11500, "vat_cents": 1500, "account_code": "fuel",
        "paid_with": "personal", "reimbursable": True, "date": "2026-10-03"}


def _chart():
    return [{"id": f"acc-{c}", "tenant_id": T, "code": c, "name": n, "type": t, "vat_treatment": v,
             "deductible": d, "sort": s} for (c, n, t, v, d, s) in accounting.DEFAULT_CHART]


def _bank(i, date, direction, amount, **kw):
    return {"id": i, "tenant_id": T, "txn_date": date, "direction": direction, "amount_cents": amount,
            "match_status": "matched", "account_code": "sales", "bank_account": "62012345678",
            "categorized_by": "owner", "description": i.upper(), **kw}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({"commerce_accounts": _chart(), "journal_entries": [], "journal_lines": [],
                      "journal_post_failures": [], "commerce_invoice_settings": [{"tenant_id": T, "vat_registered": True}],
                      "commerce_books_settings": [],
                      "commerce_bank_accounts": [{"id": "ba-1", "tenant_id": T, "account_number": "62012345678",
                                                  "bank": "FNB", "name": "Cheque"}],
                      "commerce_invoices": [dict(INV), dict(BILL)], "commerce_invoice_payments": [],
                      "commerce_bank_transactions": [], "commerce_expenses": [dict(FUEL)], "commerce_orders": [],
                      "commerce_suppliers": [{"id": "sup1", "tenant_id": T, "name": "Build It", "tax_id": "4123"}]})
    for mod in (lv, accounting, ledger, ev, service, books_reports, reports, finances, cross_check):
        monkeypatch.setattr(mod, "_client", lambda: d)
    on = {"v": True}
    monkeypatch.setattr(lv, "is_on", lambda tid: on["v"] and tid == T)
    d.switch = on
    ev.invoice_state(T, INV)
    ledger.post_invoice_payment(T, INV, {"id": "p1", "amount_cents": 46000, "paid_at": "2026-10-10"})
    ledger.post_invoice_paid(T, {**INV, "status": "paid", "total_paid_cents": 46000, "paid_at": "2026-11-02"})
    ev.invoice_state(T, BILL)
    ledger.post_expense(T, FUEL)
    d.rows["commerce_bank_transactions"] += [
        _bank("b1", "2026-10-10", "in", 46000, matched_invoice_id="inv1"),
        _bank("b2", "2026-11-02", "in", 69000, matched_invoice_id="inv1"),
        _bank("r1", "2026-10-07", "out", 11500, match_status="unmatched", account_code="rent", vat_cents=1500)]
    ev.sync_bank_lines(T, ["b1", "b2", "r1"])
    return d


def test_the_profit_and_loss_on_each_view(db):
    acc = accounting.pnl(T, *OCT)
    assert acc["source"] == "journal" and acc["basis"] == "accrual"
    assert acc["total_income_cents"] == 100000                 # invoiced 1 Oct, though paid partly in Nov
    assert {r["code"]: r["amount_cents"] for r in acc["expenses"]} == {"cost_of_sales": 20000, "rent": 10000,
                                                                      "fuel": 10000}
    assert acc["net_profit_cents"] == 60000
    cash = accounting.pnl(T, *OCT, basis="cash")
    assert cash["basis"] == "cash" and cash["total_income_cents"] == 40000      # R460 received, ex VAT
    assert cash["total_expense_cents"] == 20000                                # the unpaid bill isn't a cost yet
    assert {"account", "code", "amount_cents"} <= set(acc["income"][0])         # the Books screen's keys


def test_vat_on_the_business_basis_and_on_payments(db):
    v = accounting.vat_return(T, *OCT)
    assert v["vat_registered"] and v["basis"] == "accrual"
    assert (v["output_vat_cents"], v["input_vat_cents"], v["net_vat_cents"]) == (15000, 6000, 9000)
    assert v["by_month"] == [{"month": "2026-10", "output_cents": 15000, "input_cents": 6000, "net_cents": 9000}]
    db.rows["commerce_books_settings"].append({"tenant_id": T, "vat_basis": "payments"})
    p = accounting.vat_return(T, *OCT)
    assert p["basis"] == "cash" and (p["output_vat_cents"], p["input_vat_cents"]) == (6000, 3000)
    assert "payments basis" in p["note"]


def test_the_trial_balance_is_one_view_never_both_added_together(db):
    for view in lv.VIEWS:
        tb = ledger.trial_balance(T, basis=view)
        assert tb["balanced"] and tb["total_debit_cents"] == tb["total_credit_cents"] and tb["basis"] == view
    sales = next(a for a in ledger.trial_balance(T)["accounts"] if a["code"] == "sales")
    assert sales["credit_cents"] - sales["debit_cents"] == 100000       # once, not in both views
    with pytest.raises(ValueError):
        ledger.trial_balance(T, basis="both")


def test_the_general_ledger_lists_every_line_with_its_source(db):
    for view in lv.VIEWS:
        rows = accounting.general_ledger(T, basis=view)
        assert sum(r["debit_cents"] for r in rows) == sum(r["credit_cents"] for r in rows)
    rows = accounting.general_ledger(T, *OCT)
    bank = [r for r in rows if r["bank_txn_id"] == "r1" and r["account_code"] == "bank_cash"]
    assert bank and bank[0]["credit_cents"] == 11500 and bank[0]["bank_account"] == "Cheque …5678"
    assert all("62012345678" not in str(r) for r in rows)              # no full account number
    # a recategorised line: the old entry, its reversal and the new one are all listed, and net out
    next(r for r in db.rows["commerce_bank_transactions"] if r["id"] == "r1").update(account_code="utilities")
    ev.sync_bank_lines(T, ["r1"])
    rows = accounting.general_ledger(T, *OCT)
    rent = [r for r in rows if r["account_code"] == "rent"]
    assert len(rent) == 2 and sum(r["debit_cents"] - r["credit_cents"] for r in rent) == 0
    assert any(r["reverses_entry_id"] for r in rent)


def test_balance_sheet_and_cash_flow_agree_with_the_book(db):
    for view in lv.VIEWS:
        assert reports.balance_sheet(T, "2026-11-30", view)["balanced"]
    c = reports.cash_flow(T, *OCT)
    assert (c["opening_cents"], c["in_cents"], c["out_cents"], c["closing_cents"]) == (0, 46000, 11500, 34500)
    assert sum(r["net_cents"] for r in c["by_source"]) == c["in_cents"] - c["out_cents"]
    assert {r["code"]: r["net_cents"] for r in c["by_source"]} == {"clearing": 46000, "rent": -10000,
                                                                  "vat_input": -1500}
    assert c["by_account"] == [{"account": "Cheque …5678", "code": "bank_cash", "opening_cents": 0,
                                "in_cents": 46000, "out_cents": 11500, "closing_cents": 34500}]
    d = reports.build_all(T, *OCT)
    assert d["basis"] == "accrual" and "accrual basis" in reports.summary_text(d)


def test_aged_creditors_tie_to_the_creditors_account(db):
    a = finances.aged_creditors(T, "2026-11-20")
    assert a["total_cents"] == 23000 and a["aging"]["days_31_60"] == 23000
    assert a["suppliers"] == [{"name": "Build It", "owed_cents": 23000}]
    assert a["control_cents"] == 23000 and a["ties"]
    db.rows["commerce_invoice_payments"].append({"id": "sp1", "tenant_id": T, "invoice_id": "bill1",
                                                 "amount_cents": 10000})
    ledger.post_supplier_payment(T, BILL, {"id": "sp1", "amount_cents": 10000, "paid_at": "2026-11-05"})
    a = finances.aged_creditors(T, "2026-11-20")
    assert a["total_cents"] == 13000 and a["control_cents"] == 13000 and a["ties"]


def test_tax_year_and_the_vat_cross_check_read_the_book(db):
    from datetime import date
    y = tax.year_figures(T, date(2026, 3, 1), date(2027, 2, 28))
    assert y["source"] == "journal" and y["income_cents"] == 100000 and y["expense_cents"] == 40000
    assert y["first"] == date(2026, 10, 1) and y["last"] == date(2026, 11, 2)
    x = cross_check.vat(T, *OCT)
    assert x["source"] == "journal" and x["output_cents"] == 15000 and x["input_claimable_cents"] == 6000
    assert x["sales_cents"] == 100000


def test_finance_insights_from_the_book(db):
    with patch("vula.commerce.payment_behavior.tenant_watch_list", AsyncMock(return_value=[])):
        r = asyncio.run(finances.insights(T, days=4000))
    assert r["source"] == "journal" and r["revenue_cents"] == 100000 and r["expenses_cents"] == 40000
    assert r["vat"]["collected_cents"] == 15000 and r["vat"]["claimable_cents"] == 6000
    assert r["payables"]["total_cents"] == 23000
    assert r["receivables"]["control_cents"] == 0


def test_with_the_switch_off_the_reports_are_exactly_as_before(db):
    db.switch["v"] = False
    p = accounting.pnl(T, *OCT)
    assert "source" not in p and "basis" not in p
    assert p["income"] == [{"account": "Sales / income", "amount_cents": 46000}]        # bank lines, as today
    assert accounting.general_ledger(T, *OCT)[0].keys() >= {"direction", "amount_cents", "source"}
    assert "basis" not in ledger.trial_balance(T)
    assert "source" not in reports.profit_and_loss(T, *OCT)
    assert "source" not in cross_check.vat(T, *OCT)


def test_routes_refuse_an_unknown_view_and_the_csv_is_one_row_per_line(db):
    from vula.api import commerce as api
    with pytest.raises(HTTPException) as e:
        api._report_basis("both")
    assert e.value.status_code == 400
    res = asyncio.run(api.admin_report_ledger_csv(T, "2026-10-01", "2026-10-31", "cash"))
    text = res.body.decode()
    head, *rows = text.strip().splitlines()
    assert head.startswith("Date,Entry,Description,Account code,Account,Debit (R),Credit (R)")
    assert len(rows) == len(accounting.general_ledger(T, *OCT, basis="cash"))
    assert 'ledger-t1-cash.csv' in res.headers["content-disposition"]


def test_another_business_journal_is_never_read(db):
    assert books_reports.general_ledger("t2") == []
    assert books_reports.trial_balance("t2")["accounts"] == []
