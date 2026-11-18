"""18 Nov 2026 (Ian, DIGG): the business works through its books audit and fixes each item
(vula/commerce/books_review.py, routes in vula/api/books_audit.py). Every fix keeps its reason, is read
back and audited; only an owner or manager can do it; and the business agrees before Vula signs off."""
import asyncio

import pytest
from fastapi import HTTPException

from vula.commerce import accounting, books_review as br, ledger, ledger_events, ledger_v2 as lv, service
from tests.fake_supabase import FakeSupabase

T = "t1"


def _chart():
    return [{"id": f"acc-{c}", "tenant_id": T, "code": c, "name": n, "type": t, "vat_treatment": v,
             "deductible": d, "sort": s} for (c, n, t, v, d, s) in accounting.DEFAULT_CHART]


def _line(i, **kw):
    return {"id": i, "tenant_id": T, "txn_date": "2026-07-18", "description": f"line {i}", "amount_cents": 10000,
            "direction": "out", "match_status": "unmatched", "account_code": "cost_of_sales",
            "categorized_by": "owner", "bank_account": "62012345678", **kw}


REPORT = {"sections": [
    {"key": "restore", "status": "problem", "headline": "2 lines set aside by mistake",
     "findings": [{"kind": "restore", "rows": ["r1", "r2"]}]},
    {"key": "duplicates", "status": "attention", "headline": "1 duplicate",
     "findings": [{"kind": "duplicate", "rows": ["d1", "d2"], "cents": 10000}]},
    {"key": "transfers", "status": "problem", "headline": "1 transfer",
     "findings": [{"kind": "transfer", "rows": ["t_out", "t_in"], "cents": 50000}]},
    {"key": "dates", "status": "attention", "headline": "1 odd date",
     "findings": [{"kind": "ledger_date", "rows": ["je1"]}]}],
    "summary": {"clean": False},
    "books": {"in_transit": {"largest": [
        {"kind": "bill", "id": "bill1", "label": "S-1", "date": "2026-07-01", "recorded_cents": -23000,
         "bank_cents": 0, "open_cents": -23000}]},
        "bank_reconciliation": {"accounts": []}}}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "commerce_accounts": _chart(),
        "commerce_bank_transactions": [
            _line("r1", match_status="ignored", excluded_reason="misread copy"), _line("r2", match_status="ignored"),
            _line("d1"), _line("d2"),
            _line("t_out", amount_cents=50000), _line("t_in", direction="in", amount_cents=50000, account_code="sales"),
            _line("u1", categorized_by="default", account_code="other_expense"),
            _line("p1", amount_cents=23000, txn_date="2026-07-20"),          # the bill's payment, never tied
            _line("x1", tenant_id="t2")],
        "commerce_invoices": [
            {"id": "bill1", "tenant_id": T, "invoice_number": "S-1", "direction": "inbound", "doc_type": "invoice",
             "status": "paid", "total_cents": 23000, "issue_date": "2026-07-01"},
            {"id": "bill2", "tenant_id": T, "invoice_number": "S-2", "direction": "inbound", "doc_type": "invoice",
             "status": "sent", "total_cents": 23000, "issue_date": "2026-07-01"},
            {"id": "inv9", "tenant_id": T, "invoice_number": "INV-9", "direction": "outbound", "doc_type": "invoice",
             "status": "draft", "total_cents": 5000, "issue_date": "2026-08-01"}],
        "commerce_expenses": [
            {"id": "e1", "tenant_id": T, "date": "2006-07-15", "amount_cents": 4000, "status": "submitted",
             "reimbursable": True, "paid_with": "personal", "description": "fuel"}],
        "journal_entries": [{"id": "je1", "tenant_id": T, "entry_date": "2006-07-15", "source_type": "expense",
                             "source_id": "e1", "description": "Expense: fuel", "version": 1}],
        "journal_lines": [], "journal_post_failures": [], "vula_merchant_audit": [],
        "vula_books_audits": [{"id": "a1", "tenant_id": T, "report": REPORT, "created_at": "2026-11-18T09:00:00Z"}]})
    for mod in (br, service, accounting, ledger, lv, ledger_events):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr(lv, "is_on", lambda tid: False)
    return d


def _row(db, i):
    return next(r for r in db.rows["commerce_bank_transactions"] if r["id"] == i)


def test_the_worklist_shows_what_is_still_open_as_it_is_now(db):
    out = br.review(T)
    assert [x["id"] for x in out["restore"]] == ["r1", "r2"]
    assert out["restore"][0]["bank_account"] == "…5678"            # account numbers masked
    assert len(out["duplicates"]) == 1 and len(out["transfers"]) == 1
    assert [x["id"] for x in out["to_categorise"]] == ["u1"]
    assert out["in_transit"][0]["id"] == "bill1"
    assert [c["id"] for c in out["in_transit"][0]["candidates"]] == ["p1"]
    assert [c["id"] for c in out["staff_claims"]] == ["e1"]
    assert [d["id"] for d in out["drafts"]] == ["inv9"]
    assert out["odd_dates"] == [{"kind": "expense", "id": "e1", "date": "2006-07-15", "what": "Expense: fuel"}]
    assert out["clean"] is False and out["open"]["restore"] == 2
    # fixed → off the list without running the check again
    br.restore(T, ["r1", "r2"], "the running balance proves these are right")
    assert br.review(T)["restore"] == []


def test_restore_and_set_aside_keep_the_reason_read_back_and_audit(db):
    got = br.restore(T, ["r1", "r2", "d1"], "the right copy, set aside by mistake")
    assert got["done"] == ["r1", "r2"] and _row(db, "r1")["match_status"] == "unmatched"
    assert _row(db, "r1")["excluded_reason"] is None
    got = br.set_aside(T, ["d2"], "the same line read twice")
    assert got["ok"] and _row(db, "d2")["match_status"] == "ignored"
    assert _row(db, "d2")["excluded_reason"] == "the same line read twice"
    acts = [a["action"] for a in db.rows["vula_merchant_audit"]]
    assert acts == ["bank_lines_restored", "bank_lines_set_aside"]
    with pytest.raises(br.Refused):
        br.set_aside(T, ["d1"], "")                                    # a reason is required


def test_a_transfer_is_two_legs_and_adds_the_books_accounts_once(db):
    got = br.transfer(T, ["t_out", "t_in"])
    assert got["ok"] and _row(db, "t_out")["account_code"] == _row(db, "t_in")["account_code"] == "transfer_clearing"
    br.transfer(T, ["t_out", "t_in"])
    codes = [a["code"] for a in db.rows["commerce_accounts"]]
    assert codes.count("transfer_clearing") == 1
    with pytest.raises(br.Refused):
        br.transfer(T, ["d1", "d2"])                                   # both money out


def test_link_ties_a_payment_to_a_paid_bill_without_paying_it_twice(db):
    got = br.link(T, "p1", invoice_id="bill1")
    assert got["ok"] and _row(db, "p1")["matched_invoice_id"] == "bill1"
    assert [x["id"] for x in br.review(T)["in_transit"]] == []         # no longer in transit
    with pytest.raises(br.Refused, match="already tied"):
        br.link(T, "p1", invoice_id="bill1")
    with pytest.raises(br.Refused, match="isn't marked paid"):
        br.link(T, "d1", invoice_id="bill2")
    with pytest.raises(br.Refused, match="Money in"):
        br.link(T, "t_in", invoice_id="bill1")
    db.rows["commerce_bank_transactions"].append(_line("p2", amount_cents=23000))
    with pytest.raises(br.Refused, match="more than"):
        br.link(T, "p2", invoice_id="bill1")
    with pytest.raises(br.Refused):
        br.link(T, "x1", invoice_id="bill1")                           # another business's line


def test_link_a_bank_line_to_a_staff_claim(db):
    db.rows["commerce_bank_transactions"].append(_line("p3", amount_cents=4000))
    assert br.link(T, "p3", expense_id="e1")["ok"] and _row(db, "p3")["matched_expense_id"] == "e1"


def test_a_misread_date_is_corrected_with_its_reason(db):
    got = br.set_date(T, "expense", "e1", "2026-07-15", "the year was misread from the slip")
    assert got == {"ok": True, "was": "2006-07-15", "now": "2026-07-15"}
    with pytest.raises(br.Refused):
        br.set_date(T, "expense", "e1", "2009-01-01", "older than the business")
    with pytest.raises(br.Refused):
        br.set_date(T, "order", "o1", "2026-07-15", "not a kind that can be corrected here")


def test_the_business_agrees_the_latest_check_only(db):
    with pytest.raises(br.Refused):
        br.agree(T, "old", "x", "owner")
    got = br.agree(T, "a1", "Two drafts left for the accountant.", "owner")
    assert got["ok"] and db.rows["vula_books_audits"][0]["agreed_note"].startswith("Two drafts")


def test_every_route_is_for_an_owner_or_manager(monkeypatch):
    from vula.api import books_audit as api

    class Req:
        headers = {"authorization": "Bearer x"}

    async def staff(auth, tenant):
        return {"full": False, "access": ["bank"], "role": "staff"}
    monkeypatch.setattr("vula.api.tenant_auth.member_scope", staff)
    for call in (api.review("t1", Req()),
                 api.restore_lines("t1", api.LinesBody(ids=["r1"], reason="x y z"), Req()),
                 api.link_line("t1", "p1", api.LinkBody(invoice_id="bill1"), Req()),
                 api.agree_audit("t1", "a1", api.AgreeBody(note=""), Req())):
        with pytest.raises(HTTPException) as e:
            asyncio.run(call)
        assert e.value.status_code == 403


def test_the_dry_run_kept_for_a_business_has_no_full_account_numbers():
    rep = {"bank_reconciliation": {"accounts": [{"account": "…5678", "number": "62012345678", "bank_account_id": "x"}]}}
    assert br.mask_books(rep)["bank_reconciliation"]["accounts"] == [{"account": "…5678"}]


def test_vula_signs_off_only_after_the_business_has_agreed(db):
    from vula.api import master
    with pytest.raises(HTTPException) as e:
        asyncio.run(master.master_books_audit_sign_off(T, "a1", {}, identity={"email": "ian@vula"}))
    assert e.value.status_code == 409 and not db.rows["vula_books_audits"][0].get("signed_off_by")
    br.agree(T, "a1", "", "owner")
    got = asyncio.run(master.master_books_audit_sign_off(T, "a1", {"with": "Judy"}, identity={"email": "ian@vula"}))
    assert got["ok"] and got["signed_off_by"] == "ian@vula with Judy"
