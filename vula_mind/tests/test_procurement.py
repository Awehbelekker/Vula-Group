"""Tests for the procurement/stock-ordering completion hook (Nolo's follow-up flow):
a ClickUp task tagged procurement/stock that reaches a done status should log a dated
line item to vula_project_finances (when a Rand amount is readable off the task) and
notify the project owner via notify_team's procurement_done event."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.integrations.procurement import (
    _extract_amount,
    handle_task_status_change,
    is_procurement_task,
)


def test_extract_amount_reads_rand_figure():
    assert _extract_amount("Order timber — R4,500 total") == 4500.0
    assert _extract_amount("no amount mentioned here") == 0.0


def test_is_procurement_task_matches_tag_case_insensitively():
    assert is_procurement_task({"tags": ["Procurement"]})
    assert is_procurement_task({"tags": ["stock"]})
    assert not is_procurement_task({"tags": ["design"]})
    assert not is_procurement_task({"tags": []})


@pytest.mark.asyncio
async def test_status_change_ignored_when_not_done():
    task = {"id": "t1", "status": "in progress", "tags": ["procurement"]}
    assert await handle_task_status_change("digg-demo", task) is None


@pytest.mark.asyncio
async def test_status_change_ignored_when_not_tagged():
    task = {"id": "t1", "status": "complete", "tags": ["design"]}
    assert await handle_task_status_change("digg-demo", task) is None


@pytest.mark.asyncio
async def test_done_and_tagged_posts_finance_row_and_notifies():
    task = {
        "id": "t1", "title": "Order tiles — R12,000", "description": "",
        "status": "complete", "tags": ["procurement"], "list_id": "L1",
    }
    mock_table = MagicMock()
    mock_table.upsert.return_value.execute.return_value = MagicMock(
        data=[{"id": "row1", "amount": 12000.0}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with (
        patch("vula.commerce.service._client", return_value=mock_db),
        patch("vula.integrations.doc_filing._clickup_candidates",
              return_value=[("L1", "Team Space / HPC_Bokaap / Phase 4")]),
        patch("vula.integrations.notify.notify_team", new=AsyncMock(return_value=1)) as mock_notify,
    ):
        posted = await handle_task_status_change("digg-demo", task)

    assert posted["id"] == "row1"
    mock_table.upsert.assert_called_once()
    row = mock_table.upsert.call_args[0][0]
    assert row["project"] == "HPC_Bokaap"
    assert row["amount"] == 12000.0
    assert row["direction"] == "out"
    assert row["category"] == "procurement"
    mock_notify.assert_awaited_once()
    args, _ = mock_notify.call_args
    assert args[0] == "digg-demo"
    assert args[1] == "procurement_done"
    assert "HPC_Bokaap" in args[2]


@pytest.mark.asyncio
async def test_done_and_tagged_without_readable_amount_still_notifies_no_finance_post():
    task = {
        "id": "t2", "title": "Order tiles from supplier", "description": "",
        "status": "closed", "tags": ["stock"], "list_id": None,
    }
    with (
        patch("vula.integrations.notify.notify_team", new=AsyncMock(return_value=1)) as mock_notify,
    ):
        posted = await handle_task_status_change("digg-demo", task)

    assert posted is None
    mock_notify.assert_awaited_once()
