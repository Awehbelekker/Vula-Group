"""A customer's proof of payment dispatches an order only once the right person approves it
(13 Nov 2026, Ian: "Yes but with approval by correct user").

* Email now stages a proof of payment for approval, as WhatsApp already did; it never marks an
  invoice or order paid by itself. Old mail (the history catch-up) asks nobody and settles nothing.
* Only someone with payment rights can approve: the owner, a manager, a member with full access,
  or one whose access includes orders or a money section. A CRM-only rep's "yes" is not approval.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api import whatsapp
from vula.commerce import bank_rec

TID = "off-the-hook"


@pytest.mark.parametrize("member,ok", [
    ({"role": "owner"}, True),
    ({"role": "manager", "access": ["crm"]}, True),
    ({"role": "staff", "access": []}, True),                       # no list = full access
    ({"role": "staff", "access": ["orders"]}, True),
    ({"role": "staff", "access": ["crm", "invoices"]}, True),
    ({"role": "sales_rep", "access": ["crm", "leads"]}, False),
    ({"role": "owner", "active": False}, False),
    (None, False),
])
def test_who_may_confirm_a_payment(member, ok):
    assert whatsapp.may_confirm_payments(member) is ok


@pytest.mark.asyncio
async def test_a_crm_only_reps_yes_is_not_approval():
    answer = AsyncMock(return_value="✅ Order OTH-1042 marked paid")
    rep = {"role": "sales_rep", "access": ["crm"], "whatsapp": "27820001111"}
    with (patch("vula.integrations.notify.team_member_for_phone", return_value=rep),
          patch("vula.commerce.bank_review.handle_answer", new=answer),
          patch("vula.commerce.bank_review.handle_client_answer", new=answer)):
        assert await whatsapp._maybe_bank_review_answer(TID, "27820001111", "yes") is None
    answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_manager_s_yes_is_approval():
    answer = AsyncMock(return_value="✅ Order OTH-1042 marked paid")
    mgr = {"role": "manager", "whatsapp": "27820002222"}
    with (patch("vula.integrations.notify.team_member_for_phone", return_value=mgr),
          patch("vula.commerce.bank_review.handle_client_answer", new=AsyncMock(return_value=None)),
          patch("vula.commerce.bank_review.handle_answer", new=answer)):
        assert await whatsapp._maybe_bank_review_answer(TID, "27820002222", "yes") == "✅ Order OTH-1042 marked paid"


@pytest.mark.asyncio
async def test_an_emailed_proof_of_payment_is_staged_not_settled(tmp_path, monkeypatch):
    pdf = tmp_path / "pop.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(bank_rec, "extract_pdf_text", lambda p, pwd: "Proof of payment R450.00")
    monkeypatch.setattr(bank_rec, "extract_payment_confirmation", AsyncMock(return_value=[
        {"date": "2026-11-13", "amount_cents": 45000, "description": "Y Smith", "direction": "in",
         "reference": "OTH-1042"}]))
    reconcile = AsyncMock(side_effect=AssertionError("must not settle"))
    monkeypatch.setattr(bank_rec, "reconcile", reconcile)
    staged = MagicMock(return_value="📸 Looks like it matches order OTH-1042 — reply yes")
    monkeypatch.setattr(bank_rec, "stage_pop_for_review", staged)
    out = await bank_rec.ingest_payment_confirmation(TID, pdf, stage=True)
    assert out["staged"] == 1 and out["matched_orders"] == 0
    assert staged.call_args.args[:2] == (TID, 45000)


@pytest.mark.asyncio
async def test_old_mail_proofs_are_saved_without_asking_or_settling(tmp_path, monkeypatch):
    pdf = tmp_path / "pop.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(bank_rec, "extract_pdf_text", lambda p, pwd: "Proof of payment R450.00")
    monkeypatch.setattr(bank_rec, "extract_payment_confirmation", AsyncMock(return_value=[
        {"date": "2024-06-01", "amount_cents": 45000, "description": "Y", "direction": "in"}]))
    reconcile = AsyncMock(return_value={"matched_invoices": 0, "matched_orders": 0})
    monkeypatch.setattr(bank_rec, "reconcile", reconcile)
    monkeypatch.setattr(bank_rec, "stage_pop_for_review", MagicMock(side_effect=AssertionError("asked")))
    await bank_rec.ingest_payment_confirmation(TID, pdf, settle=False, stage=False)
    assert reconcile.await_args.kwargs["auto_settle"] is False


@pytest.mark.asyncio
async def test_email_stages_new_proofs_and_tells_the_team(tmp_path):
    from types import SimpleNamespace
    from vula.email_imap import sync
    pop = AsyncMock(return_value={"staged": 1, "proposals": ["Looks like order OTH-1042 — reply yes"],
                                  "matched_invoices": 0, "matched_orders": 0})
    notify = AsyncMock(return_value=True)
    with (
        patch.object(sync, "_already_filed", return_value=False),
        patch.object(sync, "_client", return_value=MagicMock()),
        patch("config.settings.upload_dir", Path(tmp_path)),
        patch("vula.ingestion.pipeline.VulaIngestionPipeline",
              return_value=SimpleNamespace(ingest_file=AsyncMock(return_value=SimpleNamespace(doc_id="d1")))),
        patch("vula.api.whatsapp._analyze_document",
              new=AsyncMock(return_value={"category": "Proof of Payment", "summary": "pop", "fields": {}})),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value={"id": "f1"})),
        patch("vula.integrations.doc_filing.match_project", return_value=None),
        patch("vula.integrations.doc_filing.lookup_learned_project", return_value=None),
        patch("vula.api.tenants.uses_projects", return_value=False),
        patch("vula.commerce.bank_rec.ingest_payment_confirmation", new=pop),
        patch("vula.integrations.notify.notify_team", new=notify),
    ):
        await sync._file_attachment(TID, {"subject": "Proof of payment", "from": "y@x.co.za"},
                                    {"name": "proof of payment.pdf", "data": b"%PDF", "mime": "application/pdf"},
                                    quiet=False)
    assert pop.await_args.kwargs["stage"] is True
    assert notify.await_args.args[1] == "payment_received" and "OTH-1042" in notify.await_args.args[2]
