"""Test for GET /admin/expenses returning a real, curated category list (2026-08-12).

Dashboard's Category column used to be read-only text with no way to correct it, even though
the backend's /assign endpoint already accepted account_code/category — it just had nothing to
pick from. Categories are sourced from the tenant's actual chart of accounts (finite, curated),
deliberately not free text like `project` (which is how junk like "P-DEMO" accumulates there).
"""
from unittest.mock import patch

import pytest

from vula.api.commerce import admin_list_expenses

TID = "digg-demo"

CHART = [
    {"code": "stock", "name": "Cost of sales / stock", "type": "expense"},
    {"code": "travel", "name": "Travel", "type": "expense"},
    {"code": "sales", "name": "Sales", "type": "income"},  # must be excluded
]


@pytest.mark.asyncio
async def test_returns_only_expense_type_accounts_as_categories():
    with (
        patch("vula.commerce.expenses.list_claims", return_value=[]),
        patch("vula.commerce.expenses.known_projects", return_value=["HPC_Bokaap"]),
        patch("vula.commerce.accounting.ensure_chart", return_value=CHART),
    ):
        result = await admin_list_expenses(TID)

    codes = {c["code"] for c in result["categories"]}
    assert codes == {"stock", "travel"}
    assert result["projects"] == ["HPC_Bokaap"]
