"""Tests for commit_inbound_document()'s reimbursable inference (2026-08-08 fix).

Real bug this closes: this expense-insert path never set `reimbursable`/`paid_with` at all,
silently defaulting to the column's false regardless of what the document said about who paid —
confirmed live on a card-paid hardware invoice (reimbursable=false despite "paid by card" in the
scan). create_claim() (expenses.py) already infers this correctly via resolve_paid_with(); this
just wires the same inference into the path that actually handled this document.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.commerce.service import commit_inbound_document

TID = "digg-demo"


def _mock_db():
    captured = {}

    def table(name):
        t = MagicMock()
        if name == "commerce_expenses":
            def insert(row):
                captured["row"] = row
                m = MagicMock()
                m.execute.return_value = MagicMock(data=[row])
                return m
            t.insert.side_effect = insert
        else:
            t.insert.return_value.execute.return_value = MagicMock(data=[{}])
            t.upsert.return_value.execute.return_value = MagicMock(data=[{}])
            t.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])
        return t

    mock_db = MagicMock()
    mock_db.table.side_effect = table
    return mock_db, captured


@pytest.fixture(autouse=True)
def _no_kb_ingest():
    mock_pipeline = MagicMock()
    mock_pipeline.ingest_text = AsyncMock(return_value=MagicMock(chunks_stored=0))
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline):
        yield


@pytest.mark.asyncio
async def test_personal_card_payment_marked_reimbursable():
    mock_db, captured = _mock_db()
    extracted = {
        "doc_type": "receipt", "supplier": "Bauxite Extrusions", "total_cents": 852348,
        "card_last4": "5723", "payment_method": "card", "line_items": [],
    }
    with (
        patch("vula.commerce.service._client", return_value=mock_db),
        patch("vula.commerce.service.match_supplier", new=AsyncMock(return_value=None)),
        patch("vula.commerce.expenses.list_cards", return_value=[]),  # no registered card matches
    ):
        result = await commit_inbound_document(TID, extracted, auto_commit=True)

    assert result["committed"] is True
    assert captured["row"]["paid_with"] == "personal"
    assert captured["row"]["reimbursable"] is True


@pytest.mark.asyncio
async def test_company_card_payment_not_reimbursable():
    mock_db, captured = _mock_db()
    extracted = {
        "doc_type": "receipt", "supplier": "Bauxite Extrusions", "total_cents": 852348,
        "card_last4": "5723", "payment_method": "card", "line_items": [],
    }
    with (
        patch("vula.commerce.service._client", return_value=mock_db),
        patch("vula.commerce.service.match_supplier", new=AsyncMock(return_value=None)),
        patch("vula.commerce.expenses.list_cards",
              return_value=[{"last4": "5723", "active": True}]),  # registered company card
    ):
        result = await commit_inbound_document(TID, extracted, auto_commit=True)

    assert result["committed"] is True
    assert captured["row"]["paid_with"] == "company_card"
    assert captured["row"]["reimbursable"] is False


@pytest.mark.asyncio
async def test_unknown_payment_method_defaults_not_reimbursable():
    mock_db, captured = _mock_db()
    extracted = {
        "doc_type": "receipt", "supplier": "Bauxite Extrusions", "total_cents": 852348,
        "line_items": [],
    }
    with (
        patch("vula.commerce.service._client", return_value=mock_db),
        patch("vula.commerce.service.match_supplier", new=AsyncMock(return_value=None)),
        patch("vula.commerce.expenses.list_cards", return_value=[]),
    ):
        result = await commit_inbound_document(TID, extracted, auto_commit=True)

    assert result["committed"] is True
    assert captured["row"]["paid_with"] is None
    assert captured["row"]["reimbursable"] is False
