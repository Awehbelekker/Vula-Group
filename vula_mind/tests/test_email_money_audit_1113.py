"""Money fixes from the 13 Nov 2026 audit: old mail and re-read statements.

* A payment can't settle an invoice or order raised after it (an old proof of payment found in a
  mailbox's history matched today's open order on amount alone and dispatched it).
* A bank line that already settled something keeps that match (a re-read statement gave the same
  R1,500 credit to a second R1,500 invoice).
* Old mail (the history catch-up) is filed, never booked, and its statements settle nothing.
* An emailed bill with a figure that isn't in its own text is staged, not booked (as WhatsApp does).
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce import bank_rec

TID = "off-the-hook"


# ── a payment can't settle what was raised after it ──────────────────────────────────────────

def test_raised_after_the_payment_is_excluded_with_a_day_of_slack():
    assert bank_rec._raised_after({"created_at": "2026-11-13T08:00:00+00:00"}, "2024-03-02")
    assert not bank_rec._raised_after({"created_at": "2026-11-13T08:00:00+00:00"}, "2026-11-12")   # a day's slack
    assert not bank_rec._raised_after({"issue_date": "2026-10-01", "created_at": "2026-11-13"}, "2026-10-05")
    assert not bank_rec._raised_after({}, "2026-10-05")                                          # unknown: fail open
    assert not bank_rec._raised_after({"created_at": "2026-11-13"}, None)


def test_an_old_proof_of_payment_does_not_match_todays_order():
    order = {"id": "o1", "display_id": "OTH-1042", "customer_name": "Y", "total_cents": 45000,
             "created_at": "2026-11-13T07:30:00+00:00"}
    old = {"date": "2024-06-01", "amount_cents": 45000, "description": "EFT", "reference": None}
    new = {**old, "date": "2026-11-13"}
    assert bank_rec._match_order(old, [order]) is None
    assert bank_rec._match_order(new, [order]) == order


# ── a line that already settled something keeps that match ──────────────────────────────────

@pytest.mark.asyncio
async def test_a_re_read_line_keeps_its_match_and_settles_nothing_else():
    txns = [{"date": "2026-10-03", "description": "ACME PAYMENT", "amount_cents": 150000,
             "direction": "in", "balance_cents": None, "reference": None}]
    tables = {}

    def table(name):
        t = tables.setdefault(name, MagicMock(name=name))
        return t
    db = MagicMock()
    db.table.side_effect = table
    inv = MagicMock()
    inv.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = [
        {"id": "INV-14", "invoice_number": "INV-14", "customer_name": "Acme", "total_cents": 150000,
         "status": "sent", "doc_type": "invoice", "issue_date": "2026-09-20"}]
    inv.select.return_value.eq.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
    tables["commerce_invoices"] = inv
    btx = MagicMock()
    btx.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"txn_date": "2026-10-03", "amount_cents": 150000, "description": "ACME PAYMENT",
         "matched_invoice_id": "INV-10", "matched_order_id": None, "matched_expense_id": None}]
    btx.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
    tables["commerce_bank_transactions"] = btx
    upd = AsyncMock()
    with (
        patch.object(bank_rec, "_client", return_value=db),
        patch.object(bank_rec, "_load_settlements", return_value=[]),
        patch("vula.commerce.service._client", return_value=db),
        patch("vula.commerce.accounting.ensure_chart", return_value=[{"code": "sales"}]),
        patch("vula.commerce.accounting.categorize_batch",
              new=AsyncMock(return_value=[{"account_code": "sales", "source": "default"}])),
        patch("vula.commerce.accounting.vat_for", return_value=0),
        patch("vula.commerce.service.get_invoice_settings", new=AsyncMock(return_value={})),
        patch("vula.commerce.service.update_invoice_status", new=upd),
        patch("vula.commerce.service.update_order_status", new=AsyncMock()),
    ):
        await bank_rec.reconcile(TID, txns)
    upd.assert_not_awaited()                                   # INV-14 is not marked paid
    row = [c.args[0] for c in btx.upsert.call_args_list if isinstance(c.args[0], dict)][0]
    assert row["matched_invoice_id"] == "INV-10" and row["match_status"] == "matched"


# ── email: old mail is filed, not booked; unverified figures are staged ───────────────────────

async def _file(tmp_path, *, quiet, fields, name="inv.pdf", subject="Invoice 51"):
    from vula.email_imap import sync
    committed = AsyncMock(return_value={"record_type": "invoice"})
    statement = AsyncMock(return_value={"matched_invoices": 0})
    pop = AsyncMock(return_value={"matched_invoices": 0, "matched_orders": 0})
    db = MagicMock()
    with (
        patch.object(sync, "_already_filed", return_value=False),
        patch.object(sync, "_client", return_value=db),
        patch("config.settings.upload_dir", Path(tmp_path)),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline",
              return_value=SimpleNamespace(ingest_file=AsyncMock(return_value=SimpleNamespace(doc_id="d1")))),
        patch("vula.api.whatsapp._analyze_document",
              new=AsyncMock(return_value={"category": "Invoice", "summary": "bill", "fields": fields})),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value={"id": "f1"})),
        patch("vula.integrations.doc_filing.match_project", return_value=None),
        patch("vula.integrations.doc_filing.lookup_learned_project", return_value=None),
        patch("vula.api.tenants.uses_projects", return_value=False),
        patch("vula.commerce.service.commit_inbound_document", new=committed),
        patch("vula.commerce.bank_rec.ingest_statement", new=statement),
        patch("vula.commerce.bank_rec.ingest_payment_confirmation", new=pop),
    ):
        await sync._file_attachment(TID, {"subject": subject, "from": "a@b.co.za"},
                                    {"name": name, "data": b"%PDF-1.4", "mime": "application/pdf"},
                                    quiet=quiet)
    return committed, statement, pop, db


@pytest.mark.asyncio
async def test_old_mail_is_filed_but_never_booked(tmp_path):
    committed, *_ = await _file(tmp_path, quiet=True, fields={"supplier": "Sea Harvest", "total_cents": 120000})
    committed.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_mail_is_booked_as_before(tmp_path):
    committed, *_ = await _file(tmp_path, quiet=False, fields={"supplier": "Sea Harvest", "total_cents": 120000})
    assert committed.await_args.kwargs["auto_commit"] is True


@pytest.mark.asyncio
async def test_an_unverified_figure_is_staged_not_booked(tmp_path):
    committed, _, _, db = await _file(tmp_path, quiet=False, fields={
        "supplier": "Skip Hire", "total_cents": 3116500,
        "_unverified_figures": [{"field": "total_cents", "cents": 3116500, "rand": 31165.0}]})
    kw = committed.await_args.kwargs
    assert kw["auto_commit"] is False
    assert "_unverified_figures" not in committed.await_args.args[1]
    db.table.assert_any_call("vula_filed_documents")
    assert {"needs_review": True} in [c.args[0] for c in db.table.return_value.update.call_args_list]


@pytest.mark.asyncio
async def test_old_statements_and_proofs_of_payment_settle_nothing(tmp_path):
    _, statement, _, _ = await _file(tmp_path, quiet=True, fields={}, name="statement.pdf",
                                     subject="Your FNB statement")
    assert statement.await_args.kwargs["settle"] is False
    _, _, pop, _ = await _file(tmp_path, quiet=True, fields={}, name="proof of payment.pdf",
                               subject="Proof of payment")
    assert pop.await_args.kwargs["settle"] is False
    _, statement, _, _ = await _file(tmp_path, quiet=False, fields={}, name="statement.pdf",
                                     subject="Your FNB statement")
    assert statement.await_args.kwargs["settle"] is True
