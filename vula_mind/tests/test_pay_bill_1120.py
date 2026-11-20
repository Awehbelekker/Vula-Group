"""20 Nov 2026 (Ian, DIGG): "R Engelbrecht and Belekkie Skips are linked; the banking details are in
R Engelbrecht's name." DIGG pays them from FNB as "Skipp Rubble Removal", so reconcile never matched a
payment to its bill: the payments were filed as casual labour while the bills sat in draft, and booking
those drafts would count every skip twice. Books review now offers each payment its open bill (payee
names the supplier, amount = what's still owed), and paying records the bill's payment once, on the
bank date, ties the line to it and remembers the payee for the supplier."""
import pytest

from vula.commerce import accounting, books_review as br, ledger, ledger_events, ledger_v2 as lv, service
from tests.fake_supabase import FakeSupabase

T = "digg-demo"
SUP = "sup-belekkie"


def _chart():
    return [{"id": f"acc-{c}", "tenant_id": T, "code": c, "name": n, "type": t, "vat_treatment": v,
             "deductible": d, "sort": s} for (c, n, t, v, d, s) in accounting.DEFAULT_CHART]


def _line(i, desc, amount, day, **kw):
    return {"id": i, "tenant_id": T, "txn_date": day, "description": desc, "amount_cents": amount, "direction": "out",
            "match_status": "unmatched", "account_code": "casual_labour", "categorized_by": "learned", "reference": "",
            **kw}


def _bill(i, number, amount, day, supplier="R Engelbrecht", **kw):
    return {"id": i, "tenant_id": T, "invoice_number": number, "supplier": supplier, "supplier_id": SUP,
            "direction": "inbound", "doc_type": "invoice", "status": "draft", "total_cents": amount,
            "total_paid_cents": 0, "issue_date": day, "project": "HPC Bokaap", **kw}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "commerce_accounts": _chart(),
        "commerce_suppliers": [
            {"id": SUP, "tenant_id": T, "name": "Belekkie Skips", "aliases": ["R Engelbrecht"],
             "bank_account_name": "R Engelbrecht"},
            {"id": "sup-x", "tenant_id": "other", "name": "Skipp Rubble Removal", "aliases": []}],
        "commerce_invoices": [
            _bill("b1", "DIG-BILL-00196", 560000, "2026-07-13"),
            _bill("b2", "DIG-BILL-00199", 280000, "2026-08-07"),
            _bill("b3", "DIG-BILL-00183", 280000, "2026-08-11"),
            _bill("paid", "DIG-BILL-00197", 280000, "2026-09-22", status="paid", total_paid_cents=280000),
            _bill("gone", "DIG-BILL-00179", 322000, "2026-08-11", status="cancelled"),
            _bill("far", "DIG-BILL-00001", 125000, "2026-01-05"),
            _bill("other-sup", "SC-1", 280000, "2026-08-07", supplier="Solid Cape", supplier_id="sup-sc"),
            _bill("x1", "X-1", 560000, "2026-07-13", tenant_id="other")],
        "commerce_invoice_payments": [],
        "commerce_bank_transactions": [
            _line("p1", "Payshap Account Off-Us Skipp Rubble Remova", 560000, "2026-07-14"),
            _line("p2", "Payshap Account Off-Us Hpc Skip And Rubble", 280000, "2026-08-07"),
            _line("p3", "FNB App Payment To Skipp Rubble Removal", 320000, "2026-07-28"),        # no bill of R3,200
            _line("p4", "FNB App Payment To Skipp Rubble Removal", 125000, "2026-11-02"),        # 2026-01 bill is too old
            _line("p5", "FNB App Payment To Prelims - Health Inv", 280000, "2026-08-27"),         # same amount, other payee
            _line("in1", "Skipp Rubble refund", 280000, "2026-08-08", direction="in", account_code="sales")],
        "vula_merchant_audit": [], "commerce_txn_rules": [], "commerce_merchant_profiles": [],
        "journal_entries": [], "journal_lines": [], "journal_post_failures": [], "vula_tenant_config": []})
    for mod in (br, service, accounting, ledger, lv, ledger_events):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr("vula.commerce.bank_rec._own_names_now", lambda tid: ["AwehBelekker (Pty) Ltd", "DIGG"])
    monkeypatch.setattr(lv, "is_on", lambda tid: False)
    return d


def _row(db, table, i):
    return next(r for r in db.rows[table] if r["id"] == i)


def _lines(db):
    return [r for r in db.rows["commerce_bank_transactions"] if r["tenant_id"] == T]


@pytest.mark.parametrize("a,b,same", [("skipp", "skips", True), ("skip", "skips", True), ("rubble", "rubble", True),
                                      ("belekkie", "belekker", False), ("hpc", "hpc", True), ("prelims", "skips", False)])
def test_words_alike(a, b, same):
    assert br._alike(a, b) is same


def test_a_payment_is_offered_the_open_bill_its_payee_names(db):
    groups = br.bill_payments(T, _lines(db))
    assert len(groups) == 1 and groups[0]["supplier"] == "Belekkie Skips (R Engelbrecht)"
    by = {x["id"]: [b["id"] for b in x["bills"]] for x in groups[0]["lines"]}
    assert by == {"p1": ["b1"], "p2": ["b2", "b3"]}       # nearest date first; no Solid Cape, no paid/cancelled/old bill
    assert "p5" not in by and "in1" not in by


def test_the_business_s_own_name_never_names_a_supplier(db):
    db.rows["commerce_bank_transactions"].append(_line("own", "Transfer Belekker Savings", 280000, "2026-08-08"))
    assert "own" not in {x["id"] for g in br.bill_payments(T, _lines(db)) for x in g["lines"]}


def test_paying_records_the_bill_once_ties_the_line_and_remembers_the_payee(db):
    out = br.pay_bill(T, "p1", "b1", "cost_of_sales", actor={"email": "judy@example.com"})
    assert out["ok"] and out["bill_status"] == "paid" and out["remembered"] == "Skipp Rubble"
    bill = _row(db, "commerce_invoices", "b1")
    assert bill["status"] == "paid" and bill["total_paid_cents"] == 560000
    pays = [p for p in db.rows["commerce_invoice_payments"] if p["invoice_id"] == "b1"]
    assert len(pays) == 1 and pays[0]["amount_cents"] == 560000 and str(pays[0]["paid_at"])[:10] == "2026-07-14"
    ln = _row(db, "commerce_bank_transactions", "p1")
    assert ln["matched_invoice_id"] == "b1" and ln["match_status"] == "matched"
    assert ln["account_code"] == "cost_of_sales" and ln["categorized_by"] == "owner" and ln["project"] == "HPC Bokaap"
    assert "Skipp Rubble" in _row(db, "commerce_suppliers", SUP)["aliases"]
    assert any(a["action"] == "bank_line_paid_bill" for a in db.rows["vula_merchant_audit"])
    # it drops off the list, and a later payment named only by the new alias is still offered its bill
    assert "p1" not in {x["id"] for g in br.bill_payments(T, _lines(db)) for x in g["lines"]}


def test_pay_bill_refuses_what_would_book_money_wrong(db):
    with pytest.raises(br.Refused, match="isn't what's still owed"):
        br.pay_bill(T, "p3", "b2", "cost_of_sales")
    with pytest.raises(br.Refused, match="paid, not waiting"):
        br.pay_bill(T, "p2", "paid", "cost_of_sales")
    with pytest.raises(br.Refused, match="cancelled"):
        br.pay_bill(T, "p2", "gone", "cost_of_sales")
    with pytest.raises(br.Refused, match="isn't there"):
        br.pay_bill(T, "p1", "x1", "cost_of_sales")              # another business's bill
    with pytest.raises(br.Refused, match="Only money going out"):
        br.pay_bill(T, "in1", "b2", "cost_of_sales")
    with pytest.raises(br.Refused, match="Choose the account"):
        br.pay_bill(T, "p2", "b2", "no_such_account")
    br.pay_bill(T, "p2", "b2", "cost_of_sales")
    with pytest.raises(br.Refused, match="already tied"):
        br.pay_bill(T, "p2", "b3", "cost_of_sales")
    assert db.rows["commerce_invoice_payments"] and all(p["invoice_id"] == "b2" for p in db.rows["commerce_invoice_payments"])


def test_a_line_with_a_bill_is_not_also_offered_for_re_filing(db, monkeypatch):
    monkeypatch.setattr(br, "latest", lambda tid: {"id": "a1", "report": {"sections": []}})
    out = br.review(T)
    paying = {x["id"] for g in out["bill_payments"] for x in g["lines"]}
    refiling = {i for g in out["refile"] for i in g["ids"]}
    assert paying == {"p1", "p2"} and not (paying & refiling) and {"p3", "p4"} <= refiling
    assert out["open"]["bill_payments"] == 2
