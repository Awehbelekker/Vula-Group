"""20 Nov 2026 (Ian, DIGG): R Engelbrecht's skip-hire payments ("FNB App Payment To Skipp Rubble Removal")
were filed as casual labour — not by any rule about Skipp Rubble, but by old one-word rules ('app' ->
casual_labour from "FNB App Payment To …"). On DIGG those rules filed 233 FNB bank charges as casual labour
too. Now only payee and reference rules are read, a bank's own fee line goes to bank charges in code, a
WhatsApp proof of payment teaches nothing, and Books review lists what the old rules filed, once per payee."""
import asyncio

import pytest

from vula.commerce import accounting, books_audit, books_review as br, ledger, ledger_events, ledger_v2 as lv, service
from tests.fake_supabase import FakeSupabase

T = "digg-demo"


def _chart():
    return [{"id": f"acc-{c}", "tenant_id": T, "code": c, "name": n, "type": t, "vat_treatment": v,
             "deductible": d, "sort": s} for (c, n, t, v, d, s) in accounting.DEFAULT_CHART]


def _line(i, desc, **kw):
    return {"id": i, "tenant_id": T, "txn_date": "2026-07-28", "description": desc, "amount_cents": 320000,
            "direction": "out", "match_status": "unmatched", "account_code": "casual_labour",
            "categorized_by": "learned", "reference": "", **kw}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "commerce_accounts": _chart(),
        "commerce_txn_rules": [
            {"id": "w1", "tenant_id": T, "signal": "app", "signal_type": "token", "account_code": "casual_labour", "hits": 7},
            {"id": "w2", "tenant_id": T, "signal": "fnb", "signal_type": "token", "account_code": "casual_labour", "hits": 6},
            {"id": "w3", "tenant_id": T, "signal": "charges", "signal_type": "token", "account_code": "casual_labour", "hits": 9},
            {"id": "m1", "tenant_id": T, "signal": "sporty ceilings", "signal_type": "merchant",
             "account_code": "subcontractors", "hits": 1},
            {"id": "x1", "tenant_id": "other", "signal": "skipp rubble", "signal_type": "merchant",
             "account_code": "owner_drawings", "hits": 5}],
        "commerce_merchant_profiles": [],
        "commerce_bank_transactions": [
            _line("s1", "FNB App Payment To Skipp Rubble Removal"),
            _line("s2", "FNB App Payment To Skipp Rubble Removal", amount_cents=85000, txn_date="2026-07-29"),
            _line("c1", "FNB bank charges", amount_cents=3500),
            _line("c2", "FNB bank charges (2)", amount_cents=1200),
            _line("ok", "FNB App Payment To Sporty Ceilings", account_code="subcontractors"),
            _line("own", "FNB App Payment To Hpc Skipp Rubble Digg", categorized_by="owner"),
            _line("in", "Magtape Hpc", direction="in", account_code="sales"),
            _line("bc", "FNB bank charges (3)", amount_cents=900, account_code="bank_charges", categorized_by="rule"),
            _line("o1", "FNB App Payment To Skipp Rubble Removal", tenant_id="other")],
        "vula_merchant_audit": [], "journal_entries": [], "journal_lines": [], "journal_post_failures": []})
    for mod in (br, service, accounting, ledger, lv, ledger_events, books_audit):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr(lv, "is_on", lambda tid: False)
    return d


def test_one_word_rules_no_longer_decide_a_line(db):
    assert accounting.lookup_learned_category(T, {"description": "FNB App Payment To Skipp Rubble Removal"}) is None
    assert accounting.lookup_learned_category(T, {"description": "FNB App Payment To Sporty Ceilings"}) == "subcontractors"
    db.rows["commerce_txn_rules"].append({"id": "r1", "tenant_id": T, "signal": "skip inv 22", "signal_type": "reference",
                                          "account_code": "cost_of_sales", "hits": 1})
    assert accounting.lookup_learned_category(T, {"description": "FNB App Payment To Someone",
                                                  "reference": "SKIP INV 22"}) == "cost_of_sales"


@pytest.mark.parametrize("desc", ["FNB bank charges", "FNB bank charges (3)", "Service Fees", "Monthly account fee"])
def test_a_bank_s_own_fee_line_is_a_bank_charge_by_its_words(desc):
    assert accounting.is_bank_charge({"description": desc, "direction": "out"})
    assert not accounting.is_bank_charge({"description": desc, "direction": "in"})      # a fee refund isn't a fee
    assert not accounting.is_bank_charge({"description": "FNB App Payment To Bank Charges Ltd", "direction": "out"})


def test_bank_charges_are_filed_before_any_rule_or_model(db, monkeypatch):
    async def _never(*a, **k):
        raise AssertionError("the model is not asked about a bank's own fee")
    monkeypatch.setattr(accounting, "_ai_categorize", _never)
    one = asyncio.run(accounting.categorize(T, {"description": "FNB bank charges (2)", "direction": "out"}, _chart()))
    assert one == {"account_code": "bank_charges", "source": "rule"}
    many = asyncio.run(accounting.categorize_batch(
        T, [{"description": "FNB bank charges", "direction": "out", "amount_cents": 3500}], _chart()))
    assert many == [{"account_code": "bank_charges", "source": "rule"}]


def test_a_proof_of_payment_teaches_no_rule(db):
    before = len(db.rows["commerce_txn_rules"])
    accounting.learn_category_rule(T, {"description": "WhatsApp proof of payment — R engelbreght",
                                       "reference": "judy skip", "source_file": "whatsapp_pop"}, "sales")
    assert len(db.rows["commerce_txn_rules"]) == before
    accounting.learn_category_rule(T, {"description": "FNB App Payment To Skipp Rubble Removal"}, "cost_of_sales")
    assert any(r["signal"] == "skipp rubble" and r["account_code"] == "cost_of_sales"
               for r in db.rows["commerce_txn_rules"] if r["tenant_id"] == T)


def test_the_review_lists_what_old_rules_filed_once_per_payee(db):
    groups = br.refile_groups([r for r in db.rows["commerce_bank_transactions"] if r["tenant_id"] == T],
                              *br._known_payees(T))
    by = {g["payee"]: g for g in groups}
    assert set(by) == {"skipp rubble", "bank charges"}     # not the payee rule, the owner's choice, money in, or bank_charges
    skip = by["skipp rubble"]
    assert skip["count"] == 2 and skip["cents"] == 405000 and sorted(skip["ids"]) == ["s1", "s2"]
    assert skip["why"] == "one_word_rule" and skip["accounts"] == [{"code": "casual_labour", "lines": 2}]
    assert skip["suggested"] is None                       # nothing is chosen for the business
    ch = by["bank charges"]
    assert ch["why"] == "bank_charge" and ch["suggested"] == "bank_charges" and sorted(ch["ids"]) == ["c1", "c2"]
    assert groups[0]["payee"] == "skipp rubble"            # largest total first


def test_once_re_filed_by_the_owner_a_group_drops_off(db):
    for r in db.rows["commerce_bank_transactions"]:
        if r["id"] in ("s1", "s2"):
            r.update(account_code="cost_of_sales", categorized_by="owner")
    accounting.learn_category_rule(T, _line("s1", "FNB App Payment To Skipp Rubble Removal"), "cost_of_sales")
    groups = br.refile_groups([r for r in db.rows["commerce_bank_transactions"] if r["tenant_id"] == T],
                              *br._known_payees(T))
    assert [g["payee"] for g in groups] == ["bank charges"]
    # and the next Skipp Rubble line is filed by the payee rule the owner's choice taught
    assert accounting.lookup_learned_category(T, {"description": "FNB App Payment To Skipp Rubble Removal"}) == "cost_of_sales"


def test_the_books_check_counts_them(db):
    lines = [r for r in db.rows["commerce_bank_transactions"] if r["tenant_id"] == T]
    known, decided = br._known_payees(T)
    flagged = [ln for ln in lines if br.refile_why(ln, known, decided)]
    assert sorted(ln["id"] for ln in flagged) == ["c1", "c2", "s1", "s2"]
