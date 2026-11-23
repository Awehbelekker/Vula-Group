"""22 Nov 2026 (Ian: "one customer payment covering several invoices"). The money-in side of the supplier
bill payments: a customer pays two or three of the business's invoices in one transfer, or pays one that
reconcile couldn't tie. Books review offers the line the invoices that add up to it exactly — the line
names the customer or carries the invoice's own number — and paying records each invoice's payment once,
on the bank date, and ties the line. Made-up names and figures."""
import pytest

from vula.commerce import accounting, books_review as br, ledger, ledger_events, ledger_v2 as lv, service
from tests.fake_supabase import FakeSupabase

T = "t1"


def _chart():
    return [{"id": f"acc-{c}", "tenant_id": T, "code": c, "name": n, "type": t, "vat_treatment": v,
             "deductible": d, "sort": s} for (c, n, t, v, d, s) in accounting.DEFAULT_CHART]


def _line(i, desc, amount, day, **kw):
    return {"id": i, "tenant_id": T, "txn_date": day, "description": desc, "amount_cents": amount, "direction": "in",
            "match_status": "unmatched", "account_code": "sales", "categorized_by": "default", "reference": "", **kw}


def _inv(i, number, amount, day, customer="Atlantis Seafood Distributors", **kw):
    return {"id": i, "tenant_id": T, "invoice_number": number, "customer_name": customer, "direction": "outbound",
            "doc_type": "invoice", "status": "sent", "total_cents": amount, "total_paid_cents": 0,
            "issue_date": day, "project": "Harbour Kitchen", **kw}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "commerce_accounts": _chart(),
        "commerce_invoices": [
            _inv("i1", "MD-INV-0101", 1150000, "2026-09-01"),
            _inv("i2", "MD-INV-0102", 575000, "2026-09-15"),
            _inv("i3", "MD-INV-0103", 345000, "2026-09-20", status="draft"),
            _inv("i4", "MD-INV-0104", 230000, "2026-10-01", customer="Rudi Smit"),
            _inv("own", "MD-BILL-0005", 1725000, "2026-09-01", customer="Mandla Designs"),     # the business itself
            _inv("q1", "MD-Q-0009", 575000, "2026-09-15", doc_type="quote"),
            _inv("gone", "MD-INV-0099", 575000, "2026-09-15", status="cancelled"),
            _inv("x1", "X-1", 1725000, "2026-09-01", tenant_id="other")],
        "commerce_invoice_payments": [], "commerce_bank_line_documents": [],
        "commerce_bank_transactions": [
            _line("p1", "Magtape Credit Atlantis Seafood Distribu", 1725000, "2026-10-02"),   # i1 + i2
            _line("p2", "Payment from client ref MDINV0104", 230000, "2026-10-09"),          # by its number
            _line("p3", "Magtape Credit Atlantis Seafood Distribu", 999999, "2026-10-02"),    # adds up to nothing
            _line("p4", "Magtape Credit Someone Else", 575000, "2026-10-02"),                 # names no customer
            _line("p5", "Magtape Credit Atlantis Seafood", 345000, "2026-10-05"),             # the draft one
            _line("out", "Atlantis Seafood refund", 575000, "2026-10-02", direction="out")],
        "vula_merchant_audit": [], "commerce_txn_rules": [], "commerce_merchant_profiles": [],
        "journal_entries": [], "journal_lines": [], "journal_post_failures": [], "vula_tenant_config": []})
    for mod in (br, service, accounting, ledger, lv, ledger_events):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr("vula.commerce.bank_rec._own_names_now", lambda tid: ["Mandla Designs (Pty) Ltd"])
    monkeypatch.setattr(lv, "is_on", lambda tid: False)
    return d


def _row(db, table, i):
    return next(r for r in db.rows[table] if r["id"] == i)


def _lines(db):
    return [r for r in db.rows["commerce_bank_transactions"] if r["tenant_id"] == T]


def _offers(db):
    return {ln["id"]: [[i["number"] for i in o["invoices"]] for o in ln["options"]]
            for g in br.invoice_payments(T, _lines(db)) for ln in g["lines"]}


def test_a_payment_is_offered_the_invoices_that_add_up_to_it(db):
    offers = _offers(db)
    assert offers["p1"] == [["MD-INV-0101", "MD-INV-0102"]]            # two of one customer
    assert offers["p2"] == [["MD-INV-0104"]]                            # found by the invoice's own number
    assert offers["p5"] == [["MD-INV-0103"]]                            # a draft: it was sent after all
    assert "p3" not in offers and "p4" not in offers and "out" not in offers
    every = str(br.invoice_payments(T, _lines(db)))
    assert "MD-BILL-0005" not in every and "MD-Q-0009" not in every and "X-1" not in every


def test_paying_records_each_invoice_once_on_the_bank_date(db):
    out = br.pay_invoices(T, "p1", ["i1", "i2"], actor={"email": "owner@example.com"})
    assert out["ok"] and out["invoices"] == ["MD-INV-0101", "MD-INV-0102"] and out["invoice"] == "MD-INV-0101"
    pays = sorted((p["invoice_id"], p["amount_cents"], str(p["paid_at"])[:10]) for p in db.rows["commerce_invoice_payments"])
    assert pays == [("i1", 1150000, "2026-10-02"), ("i2", 575000, "2026-10-02")]
    assert _row(db, "commerce_invoices", "i1")["status"] == "paid" and _row(db, "commerce_invoices", "i2")["status"] == "paid"
    line = _row(db, "commerce_bank_transactions", "p1")
    assert line["match_status"] == "matched" and line["matched_invoice_id"] == "i1" and line["account_code"] == "sales"
    shares = sorted((s["invoice_id"], s["amount_cents"]) for s in db.rows["commerce_bank_line_documents"])
    assert shares == [("i1", 1150000), ("i2", 575000)]
    assert any(a["action"] == "bank_line_paid_invoices" for a in db.rows["vula_merchant_audit"])
    assert "p1" not in _offers(db)                                      # tied now
    with pytest.raises(br.Refused, match="already tied"):
        br.pay_invoices(T, "p1", ["i1", "i2"])


def test_a_draft_invoice_is_paid_with_it(db):
    assert br.pay_invoices(T, "p5", ["i3"])["ok"]
    assert _row(db, "commerce_invoices", "i3")["status"] == "paid"


def test_what_is_refused(db):
    with pytest.raises(br.Refused, match="isn't the invoices together"):
        br.pay_invoices(T, "p1", ["i1", "i3"])
    with pytest.raises(br.Refused, match="same customer"):
        br.pay_invoices(T, "p1", ["i1", "i4"])
    with pytest.raises(br.Refused, match="money coming in"):
        br.pay_invoices(T, "out", ["i2"])
    with pytest.raises(br.Refused, match="isn't there"):
        br.pay_invoices(T, "p1", ["x1"])                                # another business's
    with pytest.raises(br.Refused, match="isn't there"):
        br.pay_invoices(T, "p4", ["q1"])                                # a quote
    with pytest.raises(br.Refused, match="cancelled"):
        br.pay_invoices(T, "p4", ["gone"])
    with pytest.raises(br.Refused, match="not waiting|already stands"):
        br.pay_invoices(T, "p1", ["own"])                               # addressed to the business itself
    assert db.rows["commerce_invoice_payments"] == []


def test_the_review_lists_them(db, monkeypatch):
    monkeypatch.setattr(br, "latest", lambda tid: {"id": "a1", "report": {}})
    monkeypatch.setattr("vula.commerce.doc_audit.worklist", lambda tid: {})
    r = br.review(T)
    assert r["open"]["invoice_payments"] == 3
    assert {ln["id"] for g in r["invoice_payments"] for ln in g["lines"]} == {"p1", "p2", "p5"}
