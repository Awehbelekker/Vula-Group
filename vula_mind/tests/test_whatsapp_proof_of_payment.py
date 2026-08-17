"""Tests for the "Proof of Payment" document category (vula/api/whatsapp.py, 2026-08-17):
a bank's own payment-notification/EFT-confirmation PDF (tenant paying a SUPPLIER) now gets
classified distinctly, and its payee is checked against the supplier list — added if genuinely
new, left alone if it already matches (never overwriting an existing supplier's data)."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from vula.api.whatsapp import _classify_document, _file_uploaded_document

TID = "digg-demo"


def _result(filename="Payment Notification.pdf", doc_id="doc1"):
    return SimpleNamespace(filename=filename, doc_id=doc_id, chunks_stored=1)


class _EmptyClient:
    """Fakes commerce_service._client() for the customer-linkage lookup — no order history."""
    def table(self, *a): return self
    def select(self, *a): return self
    def eq(self, *a): return self
    def limit(self, *a): return self
    def execute(self): return SimpleNamespace(data=[])


def _no_project_match():
    return {"project": None, "candidates": None, "ambiguous": False, "confidence": 0.0}


@pytest.mark.asyncio
async def test_classify_document_detects_payment_notification(tmp_path):
    path = tmp_path / "payment.txt"
    path.write_text("NOTIFICATION OF PAYMENT\nFirst National Bank hereby confirms...")
    assert _classify_document(path.name, path) == "Proof of Payment"


@pytest.mark.asyncio
async def test_proof_of_payment_adds_new_supplier(tmp_path):
    local_path = tmp_path / "Payment Notification.pdf"
    local_path.write_bytes(b"%PDF-1.7 fake")
    fields = {"payee_name": "Solucent (Pty) Ltd", "payee_bank": "FIRST NATIONAL BANK",
              "payee_branch_code": "250655", "payee_account_number": "..808178",
              "amount_cents": 1819800, "reference": "10571 -DIG"}

    with (
        patch("vula.integrations.doc_filing.lookup_learned_project", return_value=None),
        patch("vula.integrations.doc_filing.match_project", return_value=_no_project_match()),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value={"id": "f1"})),
        patch("vula.integrations.doc_filing.project_examples", return_value=[]),
        patch("vula.commerce.service._client", return_value=_EmptyClient()),
        patch("vula.commerce.service.match_supplier", new=AsyncMock(return_value=None)),
        patch("vula.commerce.service.upsert_supplier", new=AsyncMock(return_value={"name": "Solucent (Pty) Ltd"})) as mock_upsert,
    ):
        note = await _file_uploaded_document(
            TID, "27821234567", _result(), local_path, "application/pdf",
            "Proof of Payment", "Payment notification to Solucent", fields)

    assert "Added" in note and "Solucent (Pty) Ltd" in note
    mock_upsert.assert_awaited_once()
    call_data = mock_upsert.call_args.args[1]
    assert call_data["name"] == "Solucent (Pty) Ltd"
    assert call_data["account_number"] == "..808178"
    assert "FIRST NATIONAL BANK" in call_data["notes"]


@pytest.mark.asyncio
async def test_proof_of_payment_skips_existing_supplier():
    """A confident match must NOT be overwritten — upsert_supplier is never called."""
    fields = {"payee_name": "Board Store", "payee_bank": "FIRST NATIONAL BANK"}
    existing = {"supplier": {"id": "s1", "name": "The Board Store M"},
                "tier": "fuzzy_name", "confidence": 0.9, "auto_apply": True}

    with (
        patch("vula.integrations.doc_filing.lookup_learned_project", return_value=None),
        patch("vula.integrations.doc_filing.match_project", return_value=_no_project_match()),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value={"id": "f1"})),
        patch("vula.integrations.doc_filing.project_examples", return_value=[]),
        patch("vula.commerce.service._client", return_value=_EmptyClient()),
        patch("vula.commerce.service.match_supplier", new=AsyncMock(return_value=existing)),
        patch("vula.commerce.service.upsert_supplier", new=AsyncMock()) as mock_upsert,
        patch.object(Path, "read_bytes", return_value=b"%PDF-1.7 fake"),
    ):
        note = await _file_uploaded_document(
            TID, "27821234567", _result(), Path("Payment Notification.pdf"),
            "application/pdf", "Proof of Payment", "summary", fields)

    assert "already on your supplier list" in note
    mock_upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_proof_of_payment_no_payee_name_does_not_crash():
    fields = {"amount_cents": 100000}  # extraction failed to find a payee name

    with (
        patch("vula.integrations.doc_filing.lookup_learned_project", return_value=None),
        patch("vula.integrations.doc_filing.match_project", return_value=_no_project_match()),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value={"id": "f1"})),
        patch("vula.integrations.doc_filing.project_examples", return_value=[]),
        patch("vula.commerce.service._client", return_value=_EmptyClient()),
        patch("vula.commerce.service.match_supplier", new=AsyncMock()) as mock_match,
        patch("vula.commerce.service.upsert_supplier", new=AsyncMock()) as mock_upsert,
        patch.object(Path, "read_bytes", return_value=b"%PDF-1.7 fake"),
    ):
        note = await _file_uploaded_document(
            TID, "27821234567", _result(), Path("Payment Notification.pdf"),
            "application/pdf", "Proof of Payment", "summary", fields)

    assert note  # still returns the normal project-filing note, no crash
    mock_match.assert_not_awaited()
    mock_upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_supplier_invoice_adds_new_supplier():
    """2026-08-17: extended same-day to real supplier invoices/quotes/BOQs too — same check-
    existing/add-if-not behavior as Proof of Payment, keyed off the 'supplier' field
    _FINANCIAL_DOC_CATEGORIES already extracts (also feeds tax_id into match_supplier's
    tier-1 exact match, and into the new supplier row for future matching)."""
    fields = {"supplier": "Cape Brick Suppliers", "tax_id": "4123456789",
              "total_cents": 450000, "date": "2026-08-15"}

    with (
        patch("vula.integrations.doc_filing.lookup_learned_project", return_value=None),
        patch("vula.integrations.doc_filing.match_project", return_value=_no_project_match()),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value={"id": "f1"})),
        patch("vula.integrations.doc_filing.project_examples", return_value=[]),
        patch("vula.commerce.service._client", return_value=_EmptyClient()),
        patch("vula.commerce.service.commit_inbound_document", new=AsyncMock()),
        patch("vula.commerce.service.match_supplier", new=AsyncMock(return_value=None)) as mock_match,
        patch("vula.commerce.service.upsert_supplier", new=AsyncMock(return_value={"name": "Cape Brick Suppliers"})) as mock_upsert,
        patch.object(Path, "read_bytes", return_value=b"%PDF-1.7 fake"),
    ):
        note = await _file_uploaded_document(
            TID, "27821234567", _result(filename="Invoice.pdf"), Path("Invoice.pdf"),
            "application/pdf", "Invoice", "summary", fields)

    assert "Added" in note and "Cape Brick Suppliers" in note
    mock_match.assert_awaited_once_with(TID, name="Cape Brick Suppliers", tax_id="4123456789")
    call_data = mock_upsert.call_args.args[1]
    assert call_data["tax_id"] == "4123456789"


@pytest.mark.asyncio
async def test_supplier_invoice_skips_existing_supplier():
    fields = {"supplier": "Cape Brick Suppliers"}
    existing = {"supplier": {"id": "s1", "name": "Cape Brick Suppliers"},
                "tier": "exact_name", "confidence": 1.0, "auto_apply": True}

    with (
        patch("vula.integrations.doc_filing.lookup_learned_project", return_value=None),
        patch("vula.integrations.doc_filing.match_project", return_value=_no_project_match()),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value={"id": "f1"})),
        patch("vula.integrations.doc_filing.project_examples", return_value=[]),
        patch("vula.commerce.service._client", return_value=_EmptyClient()),
        patch("vula.commerce.service.commit_inbound_document", new=AsyncMock()),
        patch("vula.commerce.service.match_supplier", new=AsyncMock(return_value=existing)),
        patch("vula.commerce.service.upsert_supplier", new=AsyncMock()) as mock_upsert,
        patch.object(Path, "read_bytes", return_value=b"%PDF-1.7 fake"),
    ):
        note = await _file_uploaded_document(
            TID, "27821234567", _result(filename="Invoice.pdf"), Path("Invoice.pdf"),
            "application/pdf", "Invoice", "summary", fields)

    assert "already on your supplier list" in note
    mock_upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_unrelated_categories_never_touch_supplier_list():
    """Regression guard: a category with no supplier-shaped fields (e.g. Meeting Minutes) must
    never trigger the supplier auto-add logic, even if its fields happen to contain a
    coincidentally-named key."""
    fields = {"payee_name": "Should Not Be Added", "supplier": "Also Should Not Be Added"}

    with (
        patch("vula.integrations.doc_filing.lookup_learned_project", return_value=None),
        patch("vula.integrations.doc_filing.match_project", return_value=_no_project_match()),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value={"id": "f1"})),
        patch("vula.integrations.doc_filing.project_examples", return_value=[]),
        patch("vula.commerce.service._client", return_value=_EmptyClient()),
        patch("vula.commerce.service.match_supplier", new=AsyncMock()) as mock_match,
        patch("vula.commerce.service.upsert_supplier", new=AsyncMock()) as mock_upsert,
        patch.object(Path, "read_bytes", return_value=b"%PDF-1.7 fake"),
    ):
        await _file_uploaded_document(
            TID, "27821234567", _result(filename="Minutes.pdf"), Path("Minutes.pdf"),
            "application/pdf", "Meeting Minutes", "summary", fields)

    mock_match.assert_not_awaited()
    mock_upsert.assert_not_awaited()
