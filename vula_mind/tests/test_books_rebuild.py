"""18 Nov 2026 (Phase 1 PR 3): a business's history rebuilt into the one trusted book — a dry run that
writes nothing, a commit only for the operator after a signed-off audit, both views balanced, the bank
reconciled per account, debtors and creditors tied (vula/commerce/books_rebuild.py, bank_recon.py,
vula/books_health.py), and card-machine payments with tips (ledger_events.tap_payment)."""
import copy

import pytest

from vula.commerce import accounting, bank_recon, books_rebuild as br, ledger, ledger_events as ev, ledger_v2 as lv, service
from tests.fake_supabase import FakeSupabase

T = "t1"
ACC = "62012345678"


def _chart():
    return [{"id": f"acc-{c}", "tenant_id": T, "code": c, "name": n, "type": t, "vat_treatment": v,
             "deductible": d, "sort": s} for (c, n, t, v, d, s) in accounting.DEFAULT_CHART + lv.LEDGER_CHART]


def _bank(i, d, direction, cents, bal, **kw):
    return {"id": i, "tenant_id": T, "txn_date": d, "direction": direction, "amount_cents": cents,
            "balance_cents": bal, "match_status": "unmatched", "account_code": "rent" if direction == "out" else "sales",
            "vat_cents": 0, "categorized_by": "owner", "bank_account": ACC, "description": i, **kw}


def _history():
    inv = {"id": "inv1", "tenant_id": T, "invoice_number": "INV-1", "customer_name": "Sam", "direction": "outbound",
           "doc_type": "invoice", "status": "part_paid", "total_cents": 115000, "vat_cents": 15000,
           "total_paid_cents": 46000, "issue_date": "2026-03-10", "source": None}
    credited = {**inv, "id": "inv2", "invoice_number": "INV-2", "status": "sent", "total_cents": 23000, "vat_cents": 3000,
                "total_paid_cents": 5000}
    cn = {"id": "cn1", "tenant_id": T, "invoice_number": "CN-1", "direction": "outbound", "doc_type": "credit_note",
          "status": "sent", "total_cents": 5000, "credited_invoice_id": "inv2", "issue_date": "2026-03-20", "source": None}
    bill = {"id": "bill1", "tenant_id": T, "invoice_number": "S-9", "supplier": "Build It", "direction": "inbound",
            "doc_type": "invoice", "status": "paid", "total_cents": 23000, "vat_cents": 3000, "total_paid_cents": 10000,
            "issue_date": "2026-03-05", "paid_at": "2026-03-25", "account_code": "cost_of_sales", "source": None}
    draft_bill = {**bill, "id": "bill2", "status": "draft", "total_paid_cents": 0}
    xero = {**inv, "id": "inv3", "invoice_number": "X-1", "source": "migration_open", "status": "paid",
            "total_paid_cents": 0, "paid_at": "2026-03-15"}
    order_inv = {**inv, "id": "inv4", "invoice_number": "ORD-1", "order_id": "o1", "status": "paid",
                 "total_cents": 11500, "vat_cents": 1500, "total_paid_cents": 11500}
    payments = [
        {"id": "p1", "tenant_id": T, "invoice_id": "inv1", "amount_cents": 46000, "payment_method": "eft",
         "paid_at": "2026-03-12"},
        {"id": "p2", "tenant_id": T, "invoice_id": "inv2", "amount_cents": 5000, "payment_method": "credit_note",
         "note": "Credit note CN-1", "paid_at": "2026-03-20"},
        {"id": "sp1", "tenant_id": T, "invoice_id": "bill1", "amount_cents": 10000, "payment_method": "eft",
         "paid_at": "2026-03-08"}]
    orders = [{"id": "o1", "tenant_id": T, "display_id": "OTH-1", "status": "paid", "total_cents": 11500,
               "updated_at": "2026-03-11"},
              {"id": "o2", "tenant_id": T, "display_id": "OTH-2", "status": "pending_payment", "total_cents": 9000}]
    expenses = [{"id": "e1", "tenant_id": T, "amount_cents": 11500, "vat_cents": 1500, "account_code": "fuel",
                 "paid_with": "personal", "reimbursable": True, "status": "reimbursed", "date": "2026-03-03",
                 "reimbursed_at": "2026-03-30"},
                {"id": "e2", "tenant_id": T, "amount_cents": 999, "status": "unconfirmed", "date": "2026-03-03"}]
    bank = [_bank("b1", "2026-03-12", "in", 46000, 146000, match_status="matched", matched_invoice_id="inv1"),
            _bank("b2", "2026-03-14", "out", 30000, 116000),
            _bank("b3", "2026-03-14", "out", 30000, 86000, match_status="ignored"),      # set aside: a misread copy
            _bank("b4", "2026-03-16", "out", 4000, 112000, categorized_by="default", account_code="other_expense"),
            _bank("b5", "2026-03-18", "in", 500000, 612000, account_code="owner_capital"),
            _bank("b6", "2026-03-20", "in", 9999, None, bank_account=None)]               # on no account
    old_v1 = [{"id": "je1", "tenant_id": T, "entry_date": "2026-03-12", "description": "old", "source_type": "invoice_payment",
               "source_id": "p1", "version": 1, "reverses_entry_id": None},
              {"id": "je2", "tenant_id": T, "entry_date": "2026-03-25", "description": "old",
               "source_type": "supplier_invoice_paid", "source_id": "bill1", "version": 1, "reverses_entry_id": None},
              {"id": "je3", "tenant_id": T, "entry_date": "2026-02-28", "description": "Xero", "source_type": "opening_balance",
               "source_id": "xero", "version": 1, "reverses_entry_id": None}]
    old_lines = [
        {"id": "jl1", "journal_entry_id": "je1", "account_id": "acc-bank_cash", "debit_cents": 46000, "credit_cents": 0},
        {"id": "jl2", "journal_entry_id": "je1", "account_id": "acc-sales", "debit_cents": 0, "credit_cents": 40000},
        {"id": "jl3", "journal_entry_id": "je1", "account_id": "acc-vat_output", "debit_cents": 0, "credit_cents": 6000},
        {"id": "jl4", "journal_entry_id": "je2", "account_id": "acc-bank_cash", "debit_cents": 0, "credit_cents": 23000},
        {"id": "jl5", "journal_entry_id": "je2", "account_id": "acc-cost_of_sales", "debit_cents": 23000, "credit_cents": 0},
        {"id": "jl6", "journal_entry_id": "je3", "account_id": "acc-accounts_receivable", "debit_cents": 115000, "credit_cents": 0},
        {"id": "jl7", "journal_entry_id": "je3", "account_id": "acc-opening_balance_equity", "debit_cents": 0, "credit_cents": 115000}]
    return {"commerce_accounts": _chart(), "journal_entries": old_v1, "journal_lines": old_lines,
            "journal_post_failures": [], "commerce_invoice_settings": [{"tenant_id": T, "vat_registered": True}],
            "commerce_books_settings": [], "commerce_bank_accounts": [{"id": "ba-1", "tenant_id": T, "account_number": ACC}],
            "commerce_invoices": [inv, credited, cn, bill, draft_bill, xero, order_inv],
            "commerce_invoice_payments": payments, "commerce_orders": orders, "commerce_expenses": expenses,
            "commerce_bank_transactions": bank, "commerce_suppliers": [], "vula_books_audits": [],
            "vula_tenant_config": [{"tenant_id": T, "features": {}}], "vula_merchant_audit": [],
            "kb_payments": [], "kb_ledger_lines": [], "kb_sessions": [], "commerce_bank_line_documents": []}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase(_history())
    for mod in (lv, accounting, ledger, ev, service, br):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr(lv, "is_on", lambda tid: tid == T)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d, raising=False)
    return d


def _bal(code, basis="accrual"):
    return sum(r["debit_cents"] - r["credit_cents"] for r in lv.balances(T, basis=basis) if r["account_code"] == code)


def test_the_dry_run_writes_nothing_and_says_what_would_change(db):
    before = copy.deepcopy(db.rows)
    rep = br.plan(T, "2026-03-01", audit=False)
    assert db.rows == before
    by = rep["actions"]["by_source"]
    assert by["changed:invoice_payment"]["count"] == 1                 # old cash entry → the one book's version
    assert by["remove:supplier_invoice_paid"]["count"] == 1            # the old type the one book doesn't use
    assert "remove:opening_balance" not in by                          # Xero's opening balances are never touched
    assert by["new:invoice_issued"]["count"] == 2 and by["new:credit_note"]["count"] == 1
    assert by["new:bill_recorded"]["count"] == 1                       # the draft bill stays out
    assert by["new:order_paid"]["count"] == 1 and "new:invoice_paid" not in by or by["new:invoice_paid"]["count"] == 1
    assert rep["trial_balance"]["before"]["accrual"]["balanced"]
    for view in lv.VIEWS:
        assert rep["trial_balance"]["after"][view]["balanced"]
    assert rep["control"]["debtors_tie"] and rep["control"]["creditors_tie"]
    assert rep["suspense"]["lines"] == 1 and rep["suspense"]["cents"] == 4000
    assert rep["bank_reconciliation"]["unassigned"]["lines"] == 1
    assert rep["plan_hash"] == br.plan(T, "2026-03-01", audit=False)["plan_hash"]


def test_the_bank_reconciles_per_account(db):
    rep = br.plan(T, "2026-03-01", audit=False)
    acct = rep["bank_reconciliation"]["accounts"][0]
    assert acct["account"] == "…5678" and acct["statement_cents"] == 612000
    assert acct["reconciles"] and acct["difference_cents"] == 0       # opening 100,000 + movements = last balance


def test_a_missing_bank_line_shows_as_a_difference(db):
    db.rows["commerce_bank_transactions"] = [r for r in db.rows["commerce_bank_transactions"] if r["id"] != "b2"]
    acct = br.plan(T, "2026-03-01", audit=False)["bank_reconciliation"]["accounts"][0]
    assert not acct["reconciles"] and acct["difference_cents"] == 30000


def test_commit_needs_the_operator_a_signed_off_audit_and_the_same_plan(db):
    h = br.plan(T, "2026-03-01", audit=False)["plan_hash"]
    assert "operator" in br.commit(T, accrual_from="2026-03-01", plan_hash_=h, person="x", master=False)["error"]
    assert "signed off" in br.commit(T, accrual_from="2026-03-01", plan_hash_=h, person="ian", master=True)["error"]
    db.rows["vula_books_audits"].append({"id": "a1", "tenant_id": T, "signed_off_by": "ian", "created_at": "2026-11-18"})
    assert "changed since" in br.commit(T, accrual_from="2026-03-01", plan_hash_="stale", person="ian", master=True)["error"]
    assert db.rows["vula_tenant_config"][0]["features"] == {}          # nothing was switched on


def test_commit_rebuilds_the_books_and_twice_changes_nothing(db):
    db.rows["vula_books_audits"].append({"id": "a1", "tenant_id": T, "signed_off_by": "ian", "created_at": "2026-11-18"})
    h = br.plan(T, "2026-03-01", audit=False)["plan_hash"]
    out = br.commit(T, accrual_from="2026-03-01", plan_hash_=h, person="ian", master=True)
    assert out["ok"], out.get("error")
    assert out["left_to_do"] == 0 and out["balanced"]
    assert db.rows["vula_tenant_config"][0]["features"]["books_v2"] is True
    assert db.rows["commerce_books_settings"][0]["accrual_from"] == "2026-03-01"
    for view in lv.VIEWS:
        assert lv.trial_balance(T, basis=view)["balanced"]
    # INV-1 and INV-2 still owed; Xero's invoice was in the opening balances and its payment cleared it
    assert _bal("accounts_receivable") == (115000 - 46000) + (23000 - 5000)
    assert _bal("accounts_payable") == 0 and _bal("staff_reimbursements") == 0
    assert _bal("owner_capital") == -500000 and _bal("suspense") == 4000
    assert lv.live_entry(T, "supplier_invoice_paid", "bill1") is None
    assert lv.live_entry(T, "opening_balance", "xero")["id"] == "je3"
    n = len(db.rows["journal_entries"])
    again = br.plan(T, "2026-03-01", audit=False)
    assert again["actions"]["new"] == again["actions"]["changed"] == again["actions"]["remove"] == 0
    out2 = br.commit(T, accrual_from="2026-03-01", plan_hash_=again["plan_hash"], person="ian", master=True)
    assert out2["ok"] and len(db.rows["journal_entries"]) == n


def test_an_order_and_its_own_invoice_are_one_sale(db):
    db.rows["vula_books_audits"].append({"id": "a1", "tenant_id": T, "signed_off_by": "ian", "created_at": "2026-11-18"})
    br.commit(T, accrual_from="2026-03-01", plan_hash_=br.plan(T, "2026-03-01", audit=False)["plan_hash"],
              person="ian", master=True)
    assert lv.live_entry(T, "order_paid", "o1") is not None
    assert lv.live_entry(T, "invoice_issued", "inv4") is None and lv.live_entry(T, "invoice_paid", "inv4") is None


def test_another_business_is_never_read(db):
    db.rows["commerce_invoices"].append({"id": "z", "tenant_id": "t2", "doc_type": "invoice", "status": "sent",
                                         "total_cents": 999999, "direction": "outbound", "issue_date": "2026-03-10"})
    rep = br.plan(T, "2026-03-01", audit=False)
    assert all("z" != a.get("entry", {}).get("source_id") for a in rep["_actions"])


def test_opening_entries_follow_the_running_balance_within_a_day():
    rows = [_bank("x2", "2026-03-01", "out", 1000, 8000), _bank("x1", "2026-03-01", "out", 1000, 9000)]
    e = bank_recon.opening_entries(T, rows, {ACC: "ba-1"})[0]
    assert e["entry_date"] == "2026-02-28"
    assert next(ln for ln in e["lines"] if ln["account_code"] == "bank_cash")["debit_cents"] == 10000


def test_a_card_machine_payment_with_a_tip_and_fees(db):
    from types import SimpleNamespace as L
    db.rows["journal_entries"], db.rows["journal_lines"] = [], []
    ev.tap_payment(T, {"id": "tp1", "amount_cents": 12500, "created_at": "2026-03-04"}, [
        L(kind="bill", party="merchant", cents=8050), L(kind="bill", party="s1", cents=3450),
        L(kind="tip", party="s1", cents=1000), L(kind="provider_fee", party="merchant", cents=-300),
        L(kind="provider_fee", party="s1", cents=-50)], bill_cents=11500)
    assert _bal("sales") == -10000 and _bal("vat_output") == -1500
    assert _bal("tips_payable") == -(3450 + 1000) + 50 and _bal("wages") == 3450
    assert _bal("bank_charges") == 300 and _bal("clearing") == 12500 - 350
    for view in lv.VIEWS:
        assert lv.trial_balance(T, basis=view)["balanced"]


# ── books health ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_books_health_alerts_once_and_notes_recovery(db, monkeypatch):
    from vula import books_health as bh
    db.rows["vula_tenant_config"][0]["features"] = {"books_v2": True}
    db.rows["vula_health_events"] = []
    monkeypatch.setattr(bh, "_client", lambda: db)
    monkeypatch.setattr("core.mass_mind.health._client", lambda: db)
    monkeypatch.setattr(bh, "_state", {})
    monkeypatch.setattr(bh, "_restored", True)
    sent = []

    async def alert(text):
        sent.append(text)
    monkeypatch.setattr(bh, "_alert", alert)
    changed = await bh.tick()
    assert any(c["name"] == "complete" and not c["ok"] for c in changed) and len(sent) == 1
    assert await bh.tick() == [] and len(sent) == 1                  # still failing: no second alert
    db.rows["vula_books_audits"].append({"id": "a1", "tenant_id": T, "signed_off_by": "ian", "created_at": "2026-11-18"})
    done = br.commit(T, accrual_from="2026-03-01",
                     plan_hash_=br.plan(T, "2026-03-01", audit=False)["plan_hash"], person="ian", master=True)
    assert done["ok"], done
    changed = await bh.tick()
    assert any(c["name"] == "complete" and c["ok"] for c in changed) and "fixed" in sent[-1]
    for i, r in enumerate(db.rows["vula_health_events"]):
        r.setdefault("created_at", f"2099-01-01T00:00:{i:02d}+00:00")    # the database stamps it, in order
    st = bh.status()
    assert {c["name"] for c in st["checks"]} == set(bh.LABELS) and st["failing"] == []


def test_a_failed_posting_is_tried_again(db, monkeypatch):
    from vula import books_health as bh
    monkeypatch.setattr(bh, "_client", lambda: db)
    payload = {"p_tenant_id": T, "p_entry_date": "2026-03-01", "p_description": "x", "p_source_type": "expense",
               "p_source_id": "e9", "p_lines": [lv.line("bank_cash", credit=500), lv.line("fuel", 500)], "p_meta": {}}
    db.rows["journal_post_failures"].append({"id": "f1", "tenant_id": T, "source_type": "expense", "source_id": "e9",
                                             "payload": payload, "attempts": 1, "resolved_at": None})
    assert bh.retry_failures(T) == {"retried": 1, "resolved": 1}
    assert db.rows["journal_post_failures"][0]["resolved_at"] and lv.live_entry(T, "expense", "e9")


def test_a_bill_marked_paid_with_no_payment_recorded_leaves_creditors_and_shows_in_transit(db):
    """DIGG: bills marked paid in full (total_paid_cents = total) with no payment on record and no
    bank line. The books take the payments themselves: the bill leaves creditors, and the payment
    shows as in transit until a bank line is matched to it."""
    db.rows["commerce_invoices"].append({
        "id": "bill9", "tenant_id": T, "invoice_number": "S-99", "supplier": "Solid Cape", "direction": "inbound",
        "doc_type": "invoice", "status": "paid", "total_cents": 49933, "vat_cents": 0, "total_paid_cents": 49933,
        "issue_date": "2026-06-23", "paid_at": "2026-10-02", "account_code": "cost_of_sales", "source": "whatsapp"})
    rep = br.plan(T, "2026-03-01", audit=False)
    assert rep["control"]["creditors_tie"]
    assert any(x["id"] == "bill9" and x["open_cents"] == -49933 for x in rep["in_transit"]["largest"])


def test_one_bank_payment_for_two_bills_leaves_neither_in_transit(db):
    """DIGG, 25 Sep: one R8,400 transfer paid two skips bills (R5,600 + R2,800). Tied through
    commerce_bank_line_documents, each bill is cleared by its share — not one looking overpaid and the
    other still in transit."""
    for i, cents in (("billA", 5600), ("billB", 2800)):
        db.rows["commerce_invoices"].append({
            "id": i, "tenant_id": T, "invoice_number": i, "supplier": "Belekkie Skips", "direction": "inbound",
            "doc_type": "invoice", "status": "paid", "total_cents": cents, "vat_cents": 0, "total_paid_cents": cents,
            "issue_date": "2026-09-22", "paid_at": "2026-09-25", "account_code": "cost_of_sales", "source": "whatsapp"})
    db.rows["commerce_bank_transactions"].append(_bank("pay8", "2026-09-25", "out", 8400, 0,
                                                       matched_invoice_id="billA", match_status="matched"))
    before = br.plan(T, "2026-03-01", audit=False)["in_transit"]["largest"]
    assert {x["id"] for x in before} >= {"billA", "billB"}           # one overpaid, one with no bank line
    db.rows["commerce_bank_line_documents"] += [
        {"id": "s1", "tenant_id": T, "txn_id": "pay8", "invoice_id": "billA", "amount_cents": 5600},
        {"id": "s2", "tenant_id": T, "txn_id": "pay8", "invoice_id": "billB", "amount_cents": 2800}]
    after = br.plan(T, "2026-03-01", audit=False)["in_transit"]["largest"]
    assert not {x["id"] for x in after} & {"billA", "billB"}
