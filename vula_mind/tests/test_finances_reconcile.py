"""Tests for reconciling a payment (POP) against a procurement "expense" line (the entry
Nolo's ClickUp task completion logs — see vula/integrations/procurement.py) instead of
only invoice<->payment pairs, and for not double-counting a reconciled pair's amount in
project totals (both sides of a matched pair stay in the ledger for provenance, but only
one side should count toward spent/received)."""
from unittest.mock import MagicMock, patch

from vula.integrations.finances import (
    _dedupe_reconciled,
    _find_match,
    finance_summary,
    post_finance_from_doc,
    project_financials,
)


def test_find_match_pairs_payment_against_expense_kind():
    expense_row = {"id": "exp1", "kind": "expense", "amount": 12000.0, "project": "HPC_Bokaap",
                   "reconciled": False, "counterparty": None, "reference": None}
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.in_.return_value.eq.return_value \
        .execute.return_value = MagicMock(data=[expense_row])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.integrations.finances._client", return_value=mock_db):
        mate = _find_match("digg-demo", ["invoice", "expense"], 12000.0, "ABC Tiles", None,
                          "", "HPC_Bokaap")

    assert mate["id"] == "exp1"


def test_post_finance_from_doc_reconciles_payment_against_procurement_expense():
    expense_row = {"id": "exp1", "kind": "expense", "amount": 12000.0, "project": "HPC_Bokaap",
                   "reconciled": False, "counterparty": None, "reference": None,
                   "category": "procurement", "description": "Order tiles"}
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.in_.return_value.eq.return_value \
        .execute.return_value = MagicMock(data=[expense_row])
    mock_table.upsert.return_value.execute.return_value = MagicMock(
        data=[{"id": "pay1", "amount": 12000.0}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with (
        patch("vula.integrations.finances._client", return_value=mock_db),
        patch("vula.integrations.finances._notify_payment"),
    ):
        row = post_finance_from_doc(
            "digg-demo", "HPC_Bokaap",
            {"amount": "12000", "payee": "ABC Tiles Supplier"},
            "doc1", "pop.pdf", summary="proof of payment", category="Payment")

    assert row["kind"] == "payment"
    assert row["reconciled"] is True
    assert row["matched_id"] == "exp1"
    mock_table.update.assert_called_once()
    upd_args, upd_kwargs = mock_table.update.call_args
    assert upd_args[0]["reconciled"] is True
    assert upd_args[0]["matched_id"] == "pay1"


def test_dedupe_reconciled_counts_a_matched_pair_once():
    rows = [
        {"id": "exp1", "matched_id": "pay1", "reconciled": True, "direction": "out", "amount": 12000.0},
        {"id": "pay1", "matched_id": "exp1", "reconciled": True, "direction": "out", "amount": 12000.0},
        {"id": "exp2", "matched_id": None, "reconciled": False, "direction": "out", "amount": 500.0},
    ]
    deduped = _dedupe_reconciled(rows)
    assert len(deduped) == 2
    assert sum(r["amount"] for r in deduped) == 12500.0


def test_finance_summary_does_not_double_count_reconciled_pair():
    rows = [
        {"id": "exp1", "matched_id": "pay1", "reconciled": True, "direction": "out",
         "amount": 12000.0, "project": "HPC_Bokaap"},
        {"id": "pay1", "matched_id": "exp1", "reconciled": True, "direction": "out",
         "amount": 12000.0, "project": "HPC_Bokaap"},
    ]
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.order.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=rows)
    mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.integrations.finances._client", return_value=mock_db):
        summary = finance_summary("digg-demo")

    assert summary["total_out"] == 12000.0   # not 24000 — the pair counts once
    assert len(summary["transactions"]) == 2  # but both rows still shown for provenance


def test_project_financials_does_not_double_count_reconciled_pair():
    rows = [
        {"id": "exp1", "matched_id": "pay1", "reconciled": True, "direction": "out",
         "amount": 12000.0, "project": "HPC_Bokaap"},
        {"id": "pay1", "matched_id": "exp1", "reconciled": True, "direction": "out",
         "amount": 12000.0, "project": "HPC_Bokaap"},
    ]
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=rows)
    mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.integrations.finances._client", return_value=mock_db):
        result = project_financials("digg-demo", "HPC_Bokaap")

    assert result["spent"] == 12000.0
