"""22 Nov 2026 (Ian, DIGG): a proof of payment the business sends on WhatsApp asks which bill(s) it paid —
numbered, from the supplier's bills adding up exactly (vula/commerce/pop_links.py) — and the answer links
the filed proof and records each open bill's payment once on its date. It used to offer one guess and,
on "yes", mark one bill paid with no payment recorded; "1" got "couldn't find an open bill".
Made-up names and figures."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from vula.commerce import bank_rec, bank_review, books_review as br, doc_audit, pop_links as pl, service
from tests.fake_supabase import FakeSupabase

T = "t1"


def _bill(i, number, supplier, day, total, status="draft", paid=0, supplier_id="s1", tenant_id=T):
    return {"id": i, "tenant_id": tenant_id, "invoice_number": number, "supplier": supplier, "supplier_id": supplier_id,
            "direction": "inbound", "doc_type": "invoice", "status": status, "total_cents": total,
            "total_paid_cents": paid, "issue_date": day, "project": "Bokaap"}


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({
        "vula_filed_documents": [
            {"id": "pop1", "tenant_id": T, "category": "Proof of Payment", "filename": "POP.pdf", "summary": "",
             "status": "filed", "content_hash": "h1", "file_url": "u/pop1.pdf", "mime": "application/pdf",
             "commerce_invoice_id": None, "supplier_id": None, "created_at": "2026-09-25T10:00:00Z", "project": None,
             "fields": {"amount_cents": 840000, "date": "2026-09-25", "payee_name": "R Engelbrecht",
                        "payee_account_number": "..1234561576", "payer": "Mandla Designs (Pty) Ltd"}}],
        "commerce_suppliers": [
            {"id": "s1", "tenant_id": T, "name": "Belekkie Skips", "aliases": ["R Engelbrecht"],
             "bank_account_name": "R Engelbrecht", "bank_account_number": "1234561576"}],
        "commerce_invoices": [
            _bill("b1", "DIG-BILL-00196", "Belekkie Skips", "2026-09-10", 560000),
            _bill("b2", "DIG-BILL-00197", "Belekkie Skips", "2026-09-12", 280000, status="paid", paid=280000),
            _bill("b3", "DIG-BILL-00198", "Belekkie Skips", "2026-10-01", 280000),
            _bill("bx", "X-1", "Belekkie Skips", "2026-09-10", 840000, tenant_id="other")],
        "commerce_bank_transactions": [], "commerce_invoice_payments": [], "vula_merchant_audit": [],
        "commerce_bank_line_documents": []})
    for mod in (bank_rec, bank_review, pl, br, doc_audit, service):
        monkeypatch.setattr(mod, "_client", lambda: d)
    monkeypatch.setattr("vula.api.merchant_audit._client", lambda: d)
    monkeypatch.setattr("vula.commerce.bank_rec._own_names_now", lambda tid: ["Mandla Designs (Pty) Ltd"])
    monkeypatch.setattr("vula.commerce.bank_rec._team_member_paying", lambda tid, payer: None)
    monkeypatch.setattr(bank_rec, "classify_pop_direction", lambda *a, **k: ("out", True, "payee is a supplier"))
    monkeypatch.setattr("vula.open_questions.ask", MagicMock())
    monkeypatch.setattr("vula.open_questions.close_for", MagicMock())
    monkeypatch.setattr(bank_review, "_books_sync", lambda *a, **k: None)
    paid = []

    async def record(tid, inv_id, cents, payment_method=None, note=None, paid_at=None):
        paid.append((inv_id, cents, paid_at))
        for r in d.rows["commerce_invoices"]:
            if r["id"] == inv_id:
                r["total_paid_cents"] = (r.get("total_paid_cents") or 0) + cents
                r["status"] = "paid" if r["total_paid_cents"] >= r["total_cents"] else "part_paid"
        return {"ok": True}
    monkeypatch.setattr(service, "record_invoice_payment", record)
    d.paid = paid
    return d


def _stage(db, amount=840000, payee="R Engelbrecht", account="..1234561576"):
    msg, txn_id = bank_rec.stage_pop(T, amount, "2026-09-25", "SKIPS", payee, sender_phone="27820000001",
                                     payer="Mandla Designs (Pty) Ltd", payee_account=account)
    bank_rec.attach_pop_doc(T, txn_id, "pop1")
    txn = next(r for r in db.rows["commerce_bank_transactions"] if r["id"] == txn_id)
    return msg, txn


def test_the_question_is_numbered_from_the_bills_that_add_up(db):
    msg, txn = _stage(db)
    assert "Which bill(s) did it pay?" in msg and "Belekkie Skips, found by the account paid into" in msg
    assert "1) *DIG-BILL-00196* + *DIG-BILL-00197*" in msg and "2) *DIG-BILL-00196* + *DIG-BILL-00198*" in msg
    assert txn["pop_options"] == [["b1", "b2"], ["b1", "b3"]] and txn["pop_doc_id"] == "pop1"
    assert txn["source_file"] == "whatsapp_pop" and txn["direction"] == "out"
    assert "bx" not in str(txn["pop_options"])                       # another business's bill


def test_one_option_asks_yes(db):
    msg, txn = _stage(db, amount=560000)
    assert "That looks like *DIG-BILL-00196* (2026-09-10)" in msg and "Reply *yes* to link it" in msg
    assert txn["pop_options"] == [["b1"]]


@pytest.mark.asyncio
async def test_a_number_links_the_proof_and_records_each_open_bill_once(db):
    _, txn = _stage(db)
    reply = await bank_review._handle_supplier_pop_answer(T, "1", txn)
    assert reply.startswith("✅ Linked to *DIG-BILL-00196* + *DIG-BILL-00197*")
    assert "payment of R8,400.00 recorded on 2026-09-25" in reply
    assert db.paid == [("b1", 560000, "2026-09-25")]                  # the paid bill is only tied
    doc = db.rows["vula_filed_documents"][0]
    assert doc["fields"]["pop_link"]["bill_ids"] == ["b1", "b2"] and doc["supplier_id"] == "s1"
    row = next(r for r in db.rows["commerce_bank_transactions"] if r["id"] == txn["id"])
    assert row["match_status"] == "matched" and row["matched_invoice_id"] == "b1"


@pytest.mark.asyncio
async def test_yes_a_bill_number_and_out_of_range(db):
    _, txn = _stage(db)
    assert "number from 1 to 2" in await bank_review._handle_supplier_pop_answer(T, "7", txn)
    assert "Which one?" in await bank_review._handle_supplier_pop_answer(T, "yes", txn)
    reply = await bank_review._handle_supplier_pop_answer(T, "DIG-BILL-00198", txn)
    assert reply.startswith("✅ Linked to *DIG-BILL-00196* + *DIG-BILL-00198*")
    assert sorted(db.paid) == [("b1", 560000, "2026-09-25"), ("b3", 280000, "2026-09-25")]


@pytest.mark.asyncio
async def test_a_single_option_takes_yes(db):
    _, txn = _stage(db, amount=560000)
    db.rows["vula_filed_documents"][0]["fields"]["amount_cents"] = 560000
    reply = await bank_review._handle_supplier_pop_answer(T, "yes", txn)
    assert reply.startswith("✅ Linked to *DIG-BILL-00196*") and db.paid == [("b1", 560000, "2026-09-25")]


@pytest.mark.asyncio
async def test_none_says_what_it_was(db):
    _, txn = _stage(db)
    reply = await bank_review._handle_supplier_pop_answer(T, "none - wages for the week", txn)
    assert "not a supplier's bill (wages for the week)" in reply and db.paid == []
    assert db.rows["vula_filed_documents"][0]["fields"]["pop_link"]["none"] == "wages for the week"
    assert next(r for r in db.rows["commerce_bank_transactions"] if r["id"] == txn["id"])["match_status"] == "ignored"


@pytest.mark.asyncio
async def test_an_older_row_without_its_document_records_the_payments(db):
    msg, txn_id = bank_rec.stage_pop(T, 840000, "2026-09-25", "SKIPS", "R Engelbrecht", payer="Mandla Designs",
                                     payee_account="..1234561576")
    txn = next(r for r in db.rows["commerce_bank_transactions"] if r["id"] == txn_id)
    reply = await bank_review._handle_supplier_pop_answer(T, "2", txn)
    assert reply.startswith("✅ Linked to *DIG-BILL-00196* + *DIG-BILL-00198*")
    assert sorted(db.paid) == [("b1", 560000, "2026-09-25"), ("b3", 280000, "2026-09-25")]


def test_no_supplier_falls_back_to_the_old_question(db):
    msg, txn = _stage(db, payee="Someone Unknown", account="")
    assert "Which bill(s)" not in msg and txn.get("pop_options") is None


@pytest.mark.asyncio
async def test_an_email_proof_tells_the_team_once(db, monkeypatch):
    from vula.email_imap import sync
    sent = AsyncMock(return_value=1)
    monkeypatch.setattr("vula.integrations.notify.notify_team", sent)
    await sync._pop_to_link_notice(T, db.rows["vula_filed_documents"][0])
    assert sent.await_count == 1
    event, text = sent.await_args.args[1], sent.await_args.args[2]
    assert event == "pop_to_link" and "It may pay DIG-BILL-00196 + DIG-BILL-00197 (or 1 other match)" in text
    assert sent.await_args.kwargs["idem_key"] == "pop_to_link:pop1"
