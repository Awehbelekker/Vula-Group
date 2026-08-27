"""Tests for the duplicate-filing race fix (migration 081), the hash-only dedup key fix
(migration 143), and the missing finance-post on manual project assignment.

Migration 143 background: auditing a real digg-demo "loop" report found that a genuine
WhatsApp redelivery of the SAME Proof of Payment (identical content_hash) filed TWICE, because
Vula's own auto-generated filename bakes in a processing-time timestamp — "Proof of Payment
20260827-1115.pdf" vs "...1116.pdf" — so even byte-identical redeliveries got a different
filename each attempt, defeating the old (tenant_id, source, filename, content_hash) key.
content_hash is now the sole dedup key (filename dropped) — verified live afterward (the same
redelivered bytes now collapse to one row)."""
from unittest.mock import MagicMock, patch

import pytest

from vula.integrations.doc_filing import file_document, _existing_filed_doc


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
    assert kwargs.get("on_conflict") == "tenant_id,source,content_hash"
    assert kwargs.get("ignore_duplicates") is True
    assert row["id"] == "row1"


@pytest.mark.asyncio
async def test_file_document_fetches_winner_row_when_it_loses_the_race():
    """ignore_duplicates=True returns no data on conflict — the loser must fetch the row the
    winner actually created, not silently return an id-less row. No data/content_hash here,
    so the lookup falls back to filename + IS NULL on content_hash."""
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
async def test_file_document_redelivery_same_content_different_filename_collapses_to_one_row():
    """The exact real-world case migration 143 fixes: a redelivery with the SAME bytes but a
    freshly time-stamped filename must still be recognised as the winner's row, not a new one."""
    mock_table = MagicMock()
    mock_table.upsert.return_value.execute.return_value = MagicMock(data=None)  # conflict — already exists
    mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value \
        .limit.return_value.execute.return_value = MagicMock(
            data=[{"id": "first-attempt-row", "filename": "Proof of Payment 20260827-1115.pdf"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.integrations.doc_filing._client", return_value=mock_db):
        row = await file_document(
            "digg-demo", filename="Proof of Payment 20260827-1116.pdf", data=b"same bytes",
            content_type="application/pdf", source="whatsapp", status="filed",
        )

    # Looked up by content_hash alone (no .eq("filename", ...) in the chain) and found the
    # original row rather than creating a second one under the new filename.
    assert row["id"] == "first-attempt-row"


def test_existing_filed_doc_matches_on_content_hash_ignoring_filename():
    """The chain when content_hash is given is exactly tenant_id/project/status/content_hash —
    4 .eq() calls, no filename filter at all — proven by the mock only being wired that deep;
    a code path that ALSO called .eq("filename", ...) would hit an unconfigured mock and never
    reach this crafted return value."""
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.eq.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{"id": "row1", "filename": "old-name.pdf"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.integrations.doc_filing._client", return_value=mock_db):
        row = _existing_filed_doc("digg-demo", "Bokaap", "new-name.pdf", content_hash="abc123")

    assert row["id"] == "row1"


def test_existing_filed_doc_falls_back_to_filename_when_no_hash():
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.eq.return_value.is_.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{"id": "row1"}])
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.integrations.doc_filing._client", return_value=mock_db):
        row = _existing_filed_doc("digg-demo", "Bokaap", "invoice.pdf", content_hash=None)

    assert row["id"] == "row1"


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
