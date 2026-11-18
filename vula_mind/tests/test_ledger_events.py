"""18 Nov 2026 (Ian: "finish Vula's own books first"): every place money moves posts to the one book
for a business on books_v2 (vula/commerce/ledger_events.py) — accrual with a cash view, the bank equal
to the statements, payments through clearing. Plus the fixes for every business: VAT fails closed, the
bank date is the paid date, the bank line is saved before anything is settled, a part-paid bill isn't
booked twice, money in can be the owner's capital, debtors include part-paid invoices."""
from unittest.mock import AsyncMock, patch

import pytest

from vula.commerce import accounting, ledger, ledger_events as ev, ledger_v2 as lv, service
from tests.fake_supabase import FakeSupabase, _Query

T = "t1"


def _chart():
    return [{"id": f"acc-{c}", "tenant_id": T, "code": c, "name": n, "type": t, "vat_treatment": v,
             "deductible": d, "sort": s} for (c, n, t, v, d, s) in accounting.DEFAULT_CHART]


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({"commerce_accounts": _chart(), "journal_entries": [], "journal_lines": [],
                      "journal_post_failures": [], "commerce_invoice_settings": [{"tenant_id": T, "vat_registered": True}],
                      "commerce_books_settings": [], "commerce_bank_accounts": [{"id": "ba-1", "tenant_id": T, "account_number": "62012345678"}],
                      "commerce_invoices": [], "commerce_bank_transactions": [], "commerce_expenses": [],
                      "commerce_suppliers": []})
    for mod in (lv, accounting, ledger, ev, service):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr(lv, "is_on", lambda tid: tid == T)
    return d


def _bal(tid, code, basis="accrual", bank_account_id=None):
    rows = [r for r in lv.balances(tid, basis=basis) if r["account_code"] == code
            and (bank_account_id is None or r.get("bank_account_id") == bank_account_id)]
    return sum(r["debit_cents"] - r["credit_cents"] for r in rows)


def _balanced():
    return all(lv.trial_balance(T, basis=v)["balanced"] for v in lv.VIEWS)


INV = {"id": "inv1", "tenant_id": T, "invoice_number": "INV-1", "customer_name": "Sam", "direction": "outbound",
       "doc_type": "invoice", "status": "sent", "total_cents": 115000, "vat_cents": 15000,
       "issue_date": "2026-10-01", "source": None}


def test_an_invoice_paid_in_two_parts_and_its_bank_lines(db):
    ev.invoice_state(T, INV)
    assert _bal(T, "sales") == -100000 and _bal(T, "accounts_receivable") == 115000
    assert _bal(T, "sales", "cash") == 0
    ledger.post_invoice_payment(T, INV, {"id": "p1", "amount_cents": 46000, "paid_at": "2026-10-10"})
    ledger.post_invoice_paid(T, {**INV, "status": "paid", "total_paid_cents": 46000, "paid_at": "2026-11-02"})
    assert _bal(T, "accounts_receivable") == 0 and _bal(T, "sales") == -100000
    assert _bal(T, "sales", "cash") == -100000 and _bal(T, "vat_output", "cash") == -15000
    assert _bal(T, "clearing") == 115000 and _bal(T, "bank_cash") == 0
    # the two bank lines arrive: they clear clearing, and the bank is exactly the statement
    db.rows["commerce_bank_transactions"] += [
        {"id": "b1", "tenant_id": T, "txn_date": "2026-10-10", "direction": "in", "amount_cents": 46000,
         "match_status": "matched", "matched_invoice_id": "inv1", "account_code": "sales", "bank_account": "62012345678"},
        {"id": "b2", "tenant_id": T, "txn_date": "2026-11-02", "direction": "in", "amount_cents": 69000,
         "match_status": "matched", "matched_invoice_id": "inv1", "account_code": "sales", "bank_account": "62012345678"}]
    ev.sync_bank_lines(T, ["b1", "b2"])
    for view in lv.VIEWS:
        assert _bal(T, "clearing", view) == 0
        assert _bal(T, "bank_cash", view, bank_account_id="ba-1") == 115000
    assert _balanced()


def test_posting_the_same_event_again_changes_nothing(db):
    ev.invoice_state(T, INV)
    ev.invoice_state(T, INV)
    ledger.post_invoice_payment(T, INV, {"id": "p1", "amount_cents": 46000, "paid_at": "2026-10-10"})
    ledger.post_invoice_payment(T, INV, {"id": "p1", "amount_cents": 46000, "paid_at": "2026-10-10"})
    assert len(db.rows["journal_entries"]) == 2


def test_a_credit_note_against_what_is_owed_and_a_cancelled_invoice(db):
    ev.invoice_state(T, INV)
    ev.credit_note(T, INV, {"id": "cn1", "invoice_number": "CN-1", "issue_date": "2026-10-05"}, 23000)
    assert _bal(T, "accounts_receivable") == 92000 and _bal(T, "sales") == -80000
    assert _bal(T, "vat_output") == -12000 and _bal(T, "sales", "cash") == 0
    other = {**INV, "id": "inv2", "invoice_number": "INV-2"}
    ev.invoice_state(T, other)
    ev.invoice_state(T, {**other, "status": "cancelled"})
    assert lv.live_entry(T, "invoice_issued", "inv2") is None
    # credited in full (cancelled with credits in total_paid_cents) keeps its entry: the credit note took it out
    ev.invoice_state(T, {**INV, "status": "cancelled", "total_paid_cents": 115000})
    assert lv.live_entry(T, "invoice_issued", "inv1") is not None
    assert _balanced()


def test_an_invoice_before_the_accrual_start_is_a_cash_sale(db):
    db.rows["commerce_books_settings"].append({"tenant_id": T, "accrual_from": "2026-11-01"})
    assert ev.invoice_state(T, INV) is None
    ledger.post_invoice_paid(T, {**INV, "status": "paid", "paid_at": "2026-11-03"})
    for view in lv.VIEWS:
        assert _bal(T, "sales", view) == -100000 and _bal(T, "accounts_receivable", view) == 0


def test_a_supplier_bill_part_paid_then_marked_paid_books_the_remainder_only(db):
    bill = {"id": "bill1", "tenant_id": T, "invoice_number": "S-9", "supplier": "Build It", "direction": "inbound",
            "doc_type": "invoice", "status": "sent", "total_cents": 23000, "vat_cents": 3000,
            "issue_date": "2026-10-02", "account_code": "cost_of_sales", "source": None}
    ev.invoice_state(T, bill)
    assert _bal(T, "accounts_payable") == -23000 and _bal(T, "cost_of_sales") == 20000
    ledger.post_supplier_payment(T, bill, {"id": "sp1", "amount_cents": 10000, "paid_at": "2026-10-05"})
    ledger.post_supplier_invoice_paid(T, {**bill, "status": "paid", "total_paid_cents": 10000, "paid_at": "2026-10-20"})
    # the instalments' VAT adds up to the bill's VAT to the cent (10000 then 13000 of a R230 bill, R30 VAT)
    assert _bal(T, "accounts_payable") == 0 and _bal(T, "clearing") == -23000
    assert _bal(T, "cost_of_sales", "cash") == 20000 and _bal(T, "vat_input", "cash") == 3000
    assert _balanced()


def test_the_old_books_no_longer_book_a_part_paid_bill_twice(monkeypatch):
    posted = []
    monkeypatch.setattr(ledger, "_v2", lambda tid: False)
    monkeypatch.setattr(ledger, "_migrated", lambda inv: False)
    monkeypatch.setattr(ledger, "_post", lambda tid, **kw: posted.append(kw))
    ledger.post_supplier_invoice_paid(T, {"id": "b", "tenant_id": T, "total_cents": 23000, "vat_cents": 3000,
                                          "total_paid_cents": 10000, "account_code": "cost_of_sales"})
    lines = posted[0]["lines"]
    assert sum(ln["credit_cents"] for ln in lines) == 13000
    assert next(ln for ln in lines if ln["account_code"] == "vat_input")["debit_cents"] == 13000 * 3000 // 23000


def test_with_the_switch_off_only_the_old_books_are_written(db, monkeypatch):
    monkeypatch.setattr(lv, "is_on", lambda tid: False)
    old = []
    monkeypatch.setattr(ledger, "_post", lambda tid, **kw: old.append(kw["source_type"]))
    ledger.post_invoice_payment(T, INV, {"id": "p1", "amount_cents": 46000, "paid_at": "2026-10-10"})
    ledger.post_expense(T, {"id": "e1", "amount_cents": 5000})
    assert old == ["invoice_payment", "expense"] and db.rows["journal_entries"] == []
    assert ev.sync_bank_lines(T, ["b1"]) == {"posted": 0, "removed": 0, "failed": 0}


def test_staff_own_money_is_owed_to_them_until_paid_back(db):
    exp = {"id": "e1", "tenant_id": T, "amount_cents": 11500, "vat_cents": 1500, "account_code": "fuel",
           "paid_with": "personal", "reimbursable": True, "date": "2026-10-03"}
    ledger.post_expense(T, exp)
    assert _bal(T, "staff_reimbursements") == -11500 and _bal(T, "clearing") == 0
    ev.expense_reimbursed(T, {**exp, "reimbursed_at": "2026-10-30"})
    assert _bal(T, "staff_reimbursements") == 0 and _bal(T, "clearing") == -11500
    # company card: through clearing; cash: cash on hand
    ledger.post_expense(T, {**exp, "id": "e2", "paid_with": "company_card", "reimbursable": False})
    ledger.post_expense(T, {**exp, "id": "e3", "paid_with": "petty_cash", "reimbursable": False})
    assert _bal(T, "clearing") == -23000 and _bal(T, "cash_on_hand") == -11500
    assert _balanced()


def test_an_expense_changed_after_posting_is_corrected_and_a_client_paid_one_leaves(db):
    exp = {"id": "e1", "tenant_id": T, "amount_cents": 5000, "account_code": "fuel", "paid_with": "company_card",
           "date": "2026-10-03", "status": "submitted"}
    db.rows["commerce_expenses"].append(dict(exp))
    ledger.post_expense(T, exp)
    from vula.commerce import expenses
    db.rows["commerce_expenses"][0]["account_code"] = "delivery"
    expenses.books_refresh(T, "e1")
    assert lv.live_entry(T, "expense", "e1")["version"] == 2
    assert _bal(T, "fuel") == 0 and _bal(T, "delivery") == 5000
    ledger.reverse_expense(T, {**exp, "account_code": "delivery"})
    assert lv.live_entry(T, "expense", "e1") is None and _bal(T, "delivery") == 0


def _line(i, **kw):
    return {"id": i, "tenant_id": T, "txn_date": "2026-10-07", "direction": "out", "amount_cents": 11500,
            "match_status": "unmatched", "account_code": "rent", "vat_cents": 1500, "categorized_by": "owner",
            "description": "RENT OCT", **kw}


def test_bank_lines_follow_what_they_say(db):
    db.rows["commerce_bank_transactions"] += [
        _line("r1"),
        _line("u1", categorized_by="default", account_code="other_expense"),
        _line("c1", direction="in", amount_cents=500000, account_code="owner_capital", vat_cents=0,
              description="TRF FROM RICHARD"),
        _line("w1", worker_id="wk1", account_code="casual_labour", vat_cents=0)]
    ev.sync_bank_lines(T, ["r1", "u1", "c1", "w1"])
    assert _bal(T, "rent") == 10000 and _bal(T, "vat_input") == 1500
    assert _bal(T, "suspense") == 11500                       # Vula wasn't sure: to review
    assert _bal(T, "owner_capital") == -500000 and _bal(T, "vat_output") == 0
    assert _bal(T, "casual_labour") == 11500
    # recategorised → corrected (version 2); set aside → out of the books
    row = next(r for r in db.rows["commerce_bank_transactions"] if r["id"] == "u1")
    row.update(account_code="fuel", vat_cents=0, categorized_by="owner")
    ev.sync_bank_lines(T, ["u1"])
    assert _bal(T, "suspense") == 0 and _bal(T, "fuel") == 11500
    assert lv.live_entry(T, "bank_line", "u1")["version"] == 2
    next(r for r in db.rows["commerce_bank_transactions"] if r["id"] == "r1")["match_status"] = "ignored"
    ev.sync_bank_lines(T, ["r1"])
    assert _bal(T, "rent") == 0 and lv.live_entry(T, "bank_line", "r1") is None
    assert _bal(T, "bank_cash") == 500000 - 2 * 11500           # r1 is out of the books
    assert ev.sync_bank_lines(T, ["u1", "c1"])["posted"] == 2      # again: nothing new
    assert _balanced()


def test_another_business_lines_are_never_posted(db):
    db.rows["commerce_bank_transactions"].append({**_line("x1"), "tenant_id": "t2"})
    ev.sync_bank_lines(T, ["x1"])
    assert db.rows["journal_entries"] == []


def test_an_order_paid_and_refunded(db):
    ledger.post_order_paid(T, {"id": "o1", "display_id": "OTH-1", "total_cents": 11500, "updated_at": "2026-10-04"})
    assert _bal(T, "sales") == -10000 and _bal(T, "vat_output") == -1500 and _bal(T, "clearing") == 11500
    ledger.post_order_refund(T, {"id": "o1", "total_cents": 11500, "updated_at": "2026-10-06"})
    assert _bal(T, "sales") == 0 and _bal(T, "clearing") == 0


# ── fixes for every business ─────────────────────────────────────────────────

def test_vat_registration_fails_closed(monkeypatch):
    class Broken:
        def table(self, *_):
            raise RuntimeError("supabase down")
    monkeypatch.setattr(accounting, "_client", lambda: Broken())
    assert accounting.is_vat_registered(T) is False


def test_money_in_can_be_the_owners_capital_and_the_books_own_accounts_are_never_offered():
    acc = {a["code"]: a for a in [{"code": c, "name": n, "type": t} for (c, n, t, *_ ) in
                                  accounting.DEFAULT_CHART + lv.LEDGER_CHART]}
    assert accounting.usable_for(acc["owner_capital"], "in") and accounting.usable_for(acc["owner_loan"], "in")
    assert accounting.usable_for(acc["clearing"], "in") and not accounting.usable_for(acc["clearing"], "out")
    assert not accounting.usable_for(acc["owner_capital"], "out")
    assert not accounting.usable_for(acc["rent"], "in") and accounting.usable_for(acc["rent"], "out")
    for code in ("suspense", "opening_balance_equity", "staff_reimbursements", "bank_cash", "vat_input"):
        assert accounting.refused_for(acc[code], "in") and accounting.refused_for(acc[code], "out")
    assert accounting.refused_for(acc["cost_of_sales"], "in") is None      # a supplier's refund
    assert accounting.refused_for(acc["owner_capital"], "out")


def test_debtors_include_part_paid_invoices_at_what_is_still_owed():
    from datetime import date
    from vula.commerce import finances
    out = finances._aging_buckets([
        {"status": "part_paid", "total_cents": 10000, "total_paid_cents": 4000, "due_date": "2026-11-01"},
        {"status": "sent", "total_cents": 5000, "due_date": "2026-11-30"}], date(2026, 11, 18))
    assert out["days_1_30"] == 6000 and out["current"] == 5000


class _UpsertFails(FakeSupabase):
    def table(self, name):
        q = _Query(self, name)
        if name == "commerce_bank_transactions":
            def boom(*a, **k):
                raise RuntimeError("bank table unavailable")
            q.upsert = boom
        return q


def _reconcile_patches(db, upd):
    return (patch("vula.commerce.bank_rec._client", return_value=db),
            patch("vula.commerce.service._client", return_value=db),
            patch("vula.commerce.accounting.ensure_chart", return_value=[{"code": "sales", "type": "income"}]),
            patch("vula.commerce.accounting.categorize_batch",
                  new=AsyncMock(return_value=[{"account_code": "sales", "source": "ai"}])),
            patch("vula.commerce.service.update_invoice_status", new=upd),
            patch("vula.commerce.service.update_order_status", new=AsyncMock()))


TXN = [{"date": "2026-08-01", "description": "INV-0001 Jane", "amount_cents": 30000,
        "direction": "in", "balance_cents": None, "reference": "INV-0001"}]
OPEN = {"id": "i1", "tenant_id": "off-the-hook", "invoice_number": "INV-0001", "customer_name": "Jane",
        "total_cents": 50000, "total_paid_cents": 20000, "status": "part_paid", "doc_type": "invoice",
        "direction": "outbound"}


@pytest.mark.asyncio
async def test_reconcile_matches_a_part_paid_invoice_on_what_is_owed_and_dates_it_by_the_bank():
    from contextlib import ExitStack
    from vula.commerce import bank_rec
    db = FakeSupabase({"commerce_invoices": [dict(OPEN)]})
    upd = AsyncMock()
    with ExitStack() as st:
        for p in _reconcile_patches(db, upd):
            st.enter_context(p)
        st.enter_context(patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})))
        out = await bank_rec.reconcile("off-the-hook", TXN)
    upd.assert_awaited_once_with("off-the-hook", "i1", "paid", paid_at="2026-08-01")
    assert out["matched_invoices"] == 1
    assert db.rows["commerce_bank_transactions"][0]["matched_invoice_id"] == "i1"


@pytest.mark.asyncio
async def test_reconcile_settles_nothing_when_the_bank_line_isnt_saved():
    from contextlib import ExitStack
    from vula.commerce import bank_rec
    db = _UpsertFails({"commerce_invoices": [dict(OPEN)]})
    upd = AsyncMock()
    with ExitStack() as st:
        for p in _reconcile_patches(db, upd):
            st.enter_context(p)
        st.enter_context(patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})))
        out = await bank_rec.reconcile("off-the-hook", TXN)
    upd.assert_not_awaited()
    assert out["matched_invoices"] == 0 and out["saved"] == 0


@pytest.mark.asyncio
async def test_reconcile_treats_an_unreadable_vat_setting_as_not_registered():
    from contextlib import ExitStack
    from vula.commerce import bank_rec
    db = FakeSupabase({})
    seen = []
    with ExitStack() as st:
        for p in _reconcile_patches(db, AsyncMock()):
            st.enter_context(p)
        st.enter_context(patch("vula.commerce.service.get_invoice_settings",
                               new=AsyncMock(side_effect=RuntimeError("down"))))
        st.enter_context(patch("vula.commerce.accounting.vat_for",
                               side_effect=lambda acc, amt, reg: seen.append(reg) or 0))
        await bank_rec.reconcile("off-the-hook", [{**TXN[0], "description": "CASH DEPOSIT"}])
    assert seen == [False]


def test_a_business_not_registered_for_vat_claims_none_its_supplier_vat_is_cost(db):
    db.rows["commerce_invoice_settings"][0]["vat_registered"] = False
    ledger.post_expense(T, {"id": "e1", "amount_cents": 11500, "vat_cents": 1500, "account_code": "fuel",
                            "paid_with": "company_card", "date": "2026-10-03"})
    ev.invoice_state(T, {"id": "b1", "tenant_id": T, "invoice_number": "S-1", "direction": "inbound",
                         "doc_type": "invoice", "status": "sent", "total_cents": 23000, "vat_cents": 3000,
                         "issue_date": "2026-10-02", "account_code": "cost_of_sales", "source": None})
    assert _bal(T, "vat_input") == 0 and _bal(T, "fuel") == 11500 and _bal(T, "cost_of_sales") == 23000
