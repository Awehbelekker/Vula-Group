"""Tests for expenses.assign()'s purpose_category param (dashboard-side manual correction/
allocation, VulaExpenses.jsx's new Purpose column) and the /assign endpoint's validation."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from vula.commerce import expenses

TID = "gerflor"


def _fake_client(select_row=None):
    mock_client = MagicMock()
    select_q = mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value
    select_q.limit.return_value.execute.return_value = MagicMock(data=[select_row] if select_row else [])
    update_q = mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value
    update_q.execute.return_value = MagicMock(data=[{"id": "e1", "purpose_category": "clients"}])
    return mock_client, update_q


def test_assign_valid_purpose_category_patches_and_clears_detail():
    mock_client, update_q = _fake_client({"id": "e1", "supplier": "Woolworths", "description": "lunch"})
    with patch("vula.commerce.expenses._client", return_value=mock_client):
        expenses.assign(TID, "e1", purpose_category="clients")
    patch_arg = mock_client.table.return_value.update.call_args[0][0]
    assert patch_arg["purpose_category"] == "clients"
    assert patch_arg["purpose_detail"] is None


def test_assign_rejects_unknown_purpose_category():
    mock_client, _ = _fake_client({"id": "e1"})
    with patch("vula.commerce.expenses._client", return_value=mock_client):
        with pytest.raises(ValueError):
            expenses.assign(TID, "e1", purpose_category="groceries")


def test_assign_without_purpose_category_leaves_it_untouched():
    mock_client, _ = _fake_client({"id": "e1"})
    with patch("vula.commerce.expenses._client", return_value=mock_client):
        expenses.assign(TID, "e1", project="HPC Bokaap")
    patch_arg = mock_client.table.return_value.update.call_args[0][0]
    assert "purpose_category" not in patch_arg
    assert "purpose_detail" not in patch_arg


@pytest.mark.asyncio
async def test_admin_assign_expense_returns_400_on_bad_purpose_category():
    from vula.api.commerce import ExpenseAssignIn, admin_assign_expense
    with patch("vula.commerce.expenses.assign", side_effect=ValueError("purpose_category must be one of (...)")):
        with pytest.raises(HTTPException) as exc_info:
            await admin_assign_expense(TID, "e1", ExpenseAssignIn(purpose_category="groceries"))
    assert exc_info.value.status_code == 400
