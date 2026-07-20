"""Tests for the duplicate-filing race fix (migration 081) and the missing finance-post on
manual project assignment. Both were found investigating a real data gap for digg-demo: 68
duplicate vula_filed_documents rows across tenants (WEB_CONCURRENCY=2 racing the same
SELECT-then-INSERT dedup check), and dashboard-assigned documents never reaching
vula_project_finances. The concurrent-race fix and the finance-posting fix were both verified
live (two truly concurrent file_document() calls collapsing to one row; assign_project()
producing a real vula_project_finances row) — this file locks in the call shape with mocks."""
from unittest.mock import MagicMock, patch

import pytest

from vula.integrations.doc_filing import file_document


@pytest.mark.asyncio
async def test_file_document_upserts_with_ignore_duplicates_on_the_unique_key():
    mock_table = MagicMock()
    mock_table.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "row1"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.integrations.doc_filing._client", return_value=mock_db):
        row = await file_document(
            "digg-demo", filename="invoice.pdf", data=None, content_type="application/pdf",
            category="Invoice", source="email", status="pending_project",
        )

    mock_table.upsert.assert_called_once()
    _, kwargs = mock_table.upsert.call_args
    assert kwargs.get("on_conflict") == "tenant_id,source,filename,content_hash"
    assert kwargs.get("ignore_duplicates") is True
    assert row["id"] == "row1"


@pytest.mark.asyncio
async def test_file_document_fetches_winner_row_when_it_loses_the_race():
    """ignore_duplicates=True returns no data on conflict — the loser must fetch the row the
    winner actually created, not silently return an id-less row. No data/content_hash here,
    so the lookup falls back to IS NULL on content_hash rather than an equality match."""
    mock_table = MagicMock()
    mock_table.upsert.return_value.execute.return_value = MagicMock(data=None)
    mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.is_.return_value \
        .limit.return_value.execute.return_value = MagicMock(data=[{"id": "winner-row", "project": "Bokaap"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.integrations.doc_filing._client", return_value=mock_db):
        row = await file_document(
            "digg-demo", filename="invoice.pdf", data=None, content_type="application/pdf",
            source="email", status="pending_project",
        )

    assert row["id"] == "winner-row"
    assert row["project"] == "Bokaap"   # the winner's resolved state, not clobbered


@pytest.mark.asyncio
async def test_assign_project_posts_to_finances_and_learns():
    doc_row = {"id": "doc1", "tenant_id": "digg-demo", "filename": "invoice.pdf",
              "fields": {"amount": 1000}, "doc_id": "kb1", "summary": "s", "category": "Invoice",
              "file_url": None}
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=[doc_row])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    from vula.api.documents import assign_project, AssignIn

    with (
        patch("vula.api.documents._client", return_value=mock_db),
        patch("vula.integrations.doc_filing.learn_filing_rule", return_value=2) as mock_learn,
        patch("vula.integrations.finances.post_finance_from_doc", return_value={"id": "f1"}) as mock_post,
    ):
        result = await assign_project("doc1", AssignIn(project="Bokaap Reno"))

    mock_learn.assert_called_once_with("digg-demo", {"amount": 1000}, "Bokaap Reno")
    mock_post.assert_called_once_with(
        "digg-demo", "Bokaap Reno", {"amount": 1000}, "kb1", "invoice.pdf", "s", "Invoice")
    assert result["learned_signals"] == 2
