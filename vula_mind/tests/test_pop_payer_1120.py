"""20 Nov 2026 (Ian, DIGG): two proofs of payment the owner sent — "MRS J DOWNING" paid R Engelbrecht R3,750
and R2,500 — were staged as money IN (one booked as R2,500 of sales), because only the payee was looked at
and an unknown payee meant "in". And the rows made from proofs of payment counted in the books as if they were
lines of the bank statement. Now who paid decides the direction too, and a proof of payment never counts:
the bank statement's own line does."""
import pytest

from vula.commerce import accounting, bank_rec, books_audit, ledger_events, service
from tests.fake_supabase import FakeSupabase

T = "digg-demo"


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "vula_team_members": [{"id": "m1", "tenant_id": T, "name": "Judy Downing", "role": "owner"},
                              {"id": "m2", "tenant_id": T, "name": "Richard Downing", "role": "staff"}],
        "commerce_invoice_settings": [{"tenant_id": T, "company_name": "Aweh Be Lekker (Pty) Ltd", "trading_as": "DIGG"}],
        "commerce_suppliers": [], "commerce_invoices": [], "commerce_bank_transactions": [],
        "vula_filed_documents": [], "open_questions": []})
    for mod in (bank_rec, service, accounting, books_audit):
        monkeypatch.setattr(mod, "_client", lambda: d)
    service._OWN_CACHE.pop(T, None)
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid: {"display_name": "DIGG Architecture"})
    return d


def test_who_paid_decides_the_direction(db):
    assert bank_rec.classify_pop_direction(T, "R engelbreght", 375000, payer="MRS J DOWNING") == \
        ("out", True, "paid personally by Judy Downing")
    assert bank_rec.classify_pop_direction(T, "R Engelbrecht", 125000, payer="AWEH BE LEKKER (PTY) LTD")[:2] == ("out", True)
    assert bank_rec.classify_pop_direction(T, "DIGG", 500000, payer="Vizion Developments")[:2] == ("in", True)
    assert bank_rec.classify_pop_direction(T, "Someone", 500000, payer="A Stranger")[:2] == ("in", False)
    assert bank_rec.classify_pop_direction(T, "Someone", 500000, payer="MR J SMITH")[:2] == ("in", False)   # not on the team


def test_the_owner_s_own_payment_is_staged_as_money_out_and_named(db):
    reply = bank_rec.stage_pop_for_review(T, 250000, "2026-05-19", "judy skip", "R engelbreght", payer="MRS J DOWNING")
    row = db.rows["commerce_bank_transactions"][-1]
    assert row["direction"] == "out" and "paid personally by Judy Downing" in row["description"]
    assert "paid by *Judy Downing* personally" in reply


def test_a_proof_of_payment_never_counts_as_a_bank_line(db):
    pop = {"id": "p1", "tenant_id": T, "txn_date": "2026-05-19", "description": "WhatsApp proof of payment — R engelbreght",
           "amount_cents": 250000, "direction": "in", "match_status": "asked", "account_code": "sales",
           "source_file": "whatsapp_pop"}
    real = {**pop, "id": "b1", "description": "FNB App Payment", "direction": "out", "account_code": "casual_labour",
            "source_file": "19 May 2026.pdf"}
    db.rows["commerce_bank_transactions"] += [pop, real]
    assert not ledger_events.is_live(pop) and ledger_events.is_live(real)
    assert [r["description"] for r in accounting._txns(T, None, None)] == ["FNB App Payment"]
    assert not books_audit._live(pop) and books_audit._live(real)


def test_the_books_check_lists_them_and_names_who_paid(db):
    db.rows["commerce_bank_transactions"] += [
        {"id": "p1", "tenant_id": T, "txn_date": "2026-05-19", "description": "WhatsApp proof of payment — R engelbreght",
         "amount_cents": 250000, "direction": "in", "match_status": "asked", "account_code": "sales",
         "source_file": "whatsapp_pop"}]
    db.rows["vula_filed_documents"] += [
        {"id": "d1", "tenant_id": T, "category": "Proof of Payment", "status": "filed",
         "fields": {"payer": "MRS J DOWNING", "payee_name": "R engelbreght", "amount_cents": 250000, "date": "2026-05-19"}}]
    sec = books_audit._pop_lines(T, db.rows["commerce_bank_transactions"])
    assert sec["status"] == "problem" and len(sec["findings"]) == 1
    f = sec["findings"][0]
    assert f["payer"] == "Judy Downing" and "recorded as money IN, but Judy Downing paid it personally" in f["text"]
    assert "no longer counted" in f["text"]
    assert books_audit._pop_lines(T, [])["status"] == "ok"
