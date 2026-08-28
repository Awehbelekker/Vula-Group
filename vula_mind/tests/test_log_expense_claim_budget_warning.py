"""Tests for _log_expense_claim's budget-warning hook (2026-08-28) — a returned warning line
from expenses.budget_warning_line() is appended to the claim-confirmation reply; None leaves it
unchanged; a raise never blocks/delays the reply itself.
"""
from unittest.mock import AsyncMock, patch

import pytest

from vula.api.whatsapp import _log_expense_claim

TID = "digg-demo"
PHONE = "27827077080"
SCAN_DATA = {"total_cents": 50000, "supplier": "Bauxite Extrusions", "notes": "Hardware"}


def _base_patches():
    claim = {
        "id": "c1", "amount_cents": 50000, "category": "supplies",
        "project": "HPC_Bokaap", "reimbursable": False, "needs_project": False,
    }
    return (
        patch("vula.commerce.expenses.create_claim", new=AsyncMock(return_value=claim)),
        patch("vula.commerce.expenses.resolve_paid_with", return_value="company_card"),
        patch("vula.commerce.expenses.match_project", return_value=None),
        patch("vula.commerce.expenses.list_cards", return_value=[]),
        patch("vula.models.tenants.get_tenant_db", side_effect=Exception("no tenant db in test")),
        patch("vula.models.field_ops.get_field_ops_db", side_effect=Exception("no field ops in test")),
        patch("vula.commerce.expenses.classify_purpose_category", new=AsyncMock(return_value="uncertain")),
    )


@pytest.mark.asyncio
async def test_warning_line_is_appended_to_reply():
    p = _base_patches()
    with (
        p[0], p[1], p[2], p[3], p[4], p[5], p[6],
        patch("vula.commerce.expenses.budget_warning_line",
              return_value="\n\n⚠️ *Budget alert:* you've reached *90%*..."),
    ):
        msg = await _log_expense_claim(TID, PHONE, SCAN_DATA)
    assert "Budget alert" in msg


@pytest.mark.asyncio
async def test_no_warning_leaves_reply_unchanged():
    p = _base_patches()
    with (
        p[0], p[1], p[2], p[3], p[4], p[5], p[6],
        patch("vula.commerce.expenses.budget_warning_line", return_value=None),
    ):
        msg = await _log_expense_claim(TID, PHONE, SCAN_DATA)
    assert "Budget alert" not in msg


@pytest.mark.asyncio
async def test_budget_warning_raise_never_blocks_the_claim_reply():
    p = _base_patches()
    with (
        p[0], p[1], p[2], p[3], p[4], p[5], p[6],
        patch("vula.commerce.expenses.budget_warning_line", side_effect=RuntimeError("boom")),
    ):
        msg = await _log_expense_claim(TID, PHONE, SCAN_DATA)
    assert "Logged as an expense" in msg
