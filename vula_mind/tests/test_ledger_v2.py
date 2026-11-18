"""17 Nov 2026 (Ian: "finish Vula's own books first"): the journal becomes the one book — accrual with
a cash view of the same entries, one live entry per source, corrections by reversal, failures kept
(vula/commerce/ledger_v2.py, migration 288)."""
import pytest

from vula.commerce import accounting
from vula.commerce import ledger_v2 as lv
from tests.fake_supabase import FakeSupabase

T = "t1"


def _db():
    chart = [{"id": f"acc-{c}", "tenant_id": T, "code": c, "name": n, "type": t, "vat_treatment": v,
              "deductible": d, "sort": s} for (c, n, t, v, d, s) in accounting.DEFAULT_CHART]
    return FakeSupabase({"commerce_accounts": chart, "journal_entries": [], "journal_lines": [],
                         "journal_post_failures": []})


@pytest.fixture
def db(monkeypatch):
    d = _db()
    monkeypatch.setattr(lv, "_client", lambda: d)
    monkeypatch.setattr(accounting, "_client", lambda: d)
    return d


def _bal(rows, code):
    r = [x for x in rows if x["account_code"] == code]
    return sum(x["debit_cents"] - x["credit_cents"] for x in r)


def test_an_invoice_is_a_sale_when_issued_on_accrual_and_when_paid_on_cash(db):
    issued = [lv.line("accounts_receivable", 115000, basis="accrual"),
              lv.line("sales", credit=100000, basis="accrual"), lv.line("vat_output", credit=15000, basis="accrual")]
    paid = [lv.line("bank_cash", 115000),
            lv.line("accounts_receivable", credit=115000, basis="accrual"),
            lv.line("sales", credit=100000, basis="cash"), lv.line("vat_output", credit=15000, basis="cash")]
    assert lv.post(T, entry_date="2026-10-01", description="INV-1 issued", source_type="invoice_issued",
                   source_id="inv1", lines=issued)
    # end of October, not yet paid: a sale on accrual, nothing on cash
    acc = lv.balances(T, until="2026-10-31", basis="accrual")
    cash = lv.balances(T, until="2026-10-31", basis="cash")
    assert _bal(acc, "sales") == -100000 and _bal(acc, "accounts_receivable") == 115000
    assert _bal(cash, "sales") == 0
    assert lv.post(T, entry_date="2026-11-05", description="INV-1 paid", source_type="invoice_payment",
                   source_id="pay1", lines=paid, bank_txn_id="b1")
    acc, cash = lv.balances(T, basis="accrual"), lv.balances(T, basis="cash")
    assert _bal(acc, "sales") == -100000 and _bal(acc, "accounts_receivable") == 0 and _bal(acc, "bank_cash") == 115000
    assert _bal(cash, "sales") == -100000 and _bal(cash, "vat_output") == -15000 and _bal(cash, "bank_cash") == 115000
    for view in lv.VIEWS:
        assert lv.trial_balance(T, basis=view)["balanced"]


def test_posting_the_same_source_twice_is_one_entry(db):
    lines = [lv.line("bank_cash", 5000), lv.line("sales", credit=5000)]
    a = lv.post(T, entry_date="2026-11-01", description="x", source_type="order_paid", source_id="o1", lines=lines)
    b = lv.post(T, entry_date="2026-11-01", description="x", source_type="order_paid", source_id="o1", lines=lines)
    assert a == b and len(db.rows["journal_entries"]) == 1


def test_a_correction_reverses_the_live_entry_and_posts_the_next_version(db):
    first = [lv.line("bank_cash", credit=30000), lv.line("casual_labour", 30000)]
    lv.post(T, entry_date="2026-07-14", description="line", source_type="bank_line", source_id="b9", lines=first)
    fixed = [lv.line("bank_cash", credit=30000), lv.line("transfer_clearing", 30000)]
    lv.ensure_ledger_chart(T)
    new = lv.repost(T, source_type="bank_line", source_id="b9", entry_date="2026-07-14",
                    description="line (transfer)", lines=fixed, reason="a transfer between own accounts")
    live = lv.live_entry(T, "bank_line", "b9")
    assert live["id"] == new and live["version"] == 2
    tb = lv.balances(T, basis="accrual")
    assert _bal(tb, "casual_labour") == 0 and _bal(tb, "transfer_clearing") == 30000
    assert sum(1 for e in db.rows["journal_entries"] if e["source_type"] == "reversal") == 1
    # the same correction again changes nothing
    assert lv.repost(T, source_type="bank_line", source_id="b9", entry_date="2026-07-14",
                     description="line (transfer)", lines=fixed) == new
    assert len(db.rows["journal_entries"]) == 3


def test_unpost_reverses_and_the_book_shows_nothing_for_the_source(db):
    lv.post(T, entry_date="2026-11-01", description="x", source_type="order_paid", source_id="o2",
            lines=[lv.line("bank_cash", 7000), lv.line("sales", credit=7000)])
    assert lv.unpost(T, "order_paid", "o2", reason="cancelled")
    assert lv.live_entry(T, "order_paid", "o2") is None
    assert _bal(lv.balances(T), "sales") == 0


def test_an_unbalanced_entry_is_refused_and_kept_as_a_failure(db):
    got = lv.post(T, entry_date="2026-11-01", description="bad", source_type="expense", source_id="e1",
                  lines=[lv.line("bank_cash", credit=1000), lv.line("fuel", 900)])
    assert got is None and db.rows["journal_entries"] == []
    fail = db.rows["journal_post_failures"]
    assert len(fail) == 1 and fail[0]["source_id"] == "e1" and "balance" in fail[0]["error"]
    assert lv.open_failures(T)[0]["source_type"] == "expense"


def test_each_view_must_balance_on_its_own():
    with pytest.raises(lv.Unbalanced):
        lv.check([lv.line("bank_cash", 1000), lv.line("sales", credit=1000, basis="cash")])   # accrual view off
    lv.check([lv.line("bank_cash", 1000), lv.line("accounts_receivable", credit=1000, basis="accrual"),
              lv.line("sales", credit=1000, basis="cash")])
    with pytest.raises(ValueError):
        lv.line("sales", 100, 100)


def test_an_unknown_account_is_a_kept_failure_not_a_crash(db):
    got = lv.post(T, entry_date="2026-11-01", description="x", source_type="bank_line", source_id="b1",
                  lines=[lv.line("bank_cash", 100), lv.line("no_such_account", credit=100)])
    assert got is None and len(db.rows["journal_post_failures"]) == 1


def test_the_trusted_books_accounts_are_added_once(db):
    lv.ensure_ledger_chart(T)
    lv.ensure_ledger_chart(T)
    codes = [a["code"] for a in db.rows["commerce_accounts"]]
    assert all(codes.count(c) == 1 for c, *_ in lv.LEDGER_CHART)
    assert lv.SYSTEM_CODES <= set(codes)


def test_another_business_cannot_be_reversed(db):
    eid = lv.post(T, entry_date="2026-11-01", description="x", source_type="order_paid", source_id="o3",
                  lines=[lv.line("bank_cash", 7000), lv.line("sales", credit=7000)])
    assert lv.reverse("t2", eid) is None
    assert lv.live_entry(T, "order_paid", "o3")["id"] == eid


def test_the_switch_is_off_unless_turned_on(monkeypatch):
    from vula import features
    monkeypatch.setattr(features, "_own", lambda t: {})
    assert lv.is_on(T) is False
    monkeypatch.setattr(features, "_own", lambda t: {"books_v2": True})
    assert lv.is_on(T) is True
