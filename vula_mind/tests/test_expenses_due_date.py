"""Tests for the one-off "pending bill" path on POST /admin/expenses (ExpenseIn.due_date).
Full behavior — pending row created, shows up in GET /admin/expenses/due, and the regular
claims path (no due_date) is unaffected — was verified live against off-the-hook during
development (both paths, cleaned up after). This file locks in the branching logic with a
mocked DB, since that's the part most likely to regress silently on a future refactor."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.commerce import ExpenseIn, admin_create_expense


@pytest.mark.asyncio
async def test_due_date_set_creates_pending_row_not_a_claim():
    mock_table = MagicMock()
    mock_table.insert.return_value.execute.return_value = MagicMock(data=None)
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with (
        patch("vula.api.commerce.service._client", return_value=mock_db),
        patch("vula.commerce.expenses.create_claim", new=AsyncMock()) as mock_create_claim,
    ):
        body = ExpenseIn(description="Accountant fee", amount_cents=200000, due_date="2026-08-01")
        result = await admin_create_expense("off-the-hook", body)

    mock_create_claim.assert_not_awaited()
    mock_db.table.assert_called_with("commerce_expenses")
    inserted = mock_table.insert.call_args[0][0]
    assert inserted["status"] == "pending"
    assert inserted["due_date"] == "2026-08-01"
    assert inserted["description"] == "Accountant fee"
    assert result["expense"]["status"] == "pending"


@pytest.mark.asyncio
async def test_no_due_date_uses_the_claims_workflow_unchanged():
    with patch("vula.commerce.expenses.create_claim",
              new=AsyncMock(return_value={"id": "c1", "status": "submitted"})) as mock_create_claim:
        body = ExpenseIn(description="Stock purchase", amount_cents=15000)
        result = await admin_create_expense("off-the-hook", body)

    mock_create_claim.assert_awaited_once()
    _, kwargs = mock_create_claim.call_args
    assert "due_date" not in kwargs   # never forwarded — create_claim's signature doesn't accept it
    assert result["expense"]["status"] == "submitted"
