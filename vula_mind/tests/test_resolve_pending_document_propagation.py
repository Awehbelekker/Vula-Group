"""Tests for resolve_pending_document()'s 2026-08-12 fix: propagate a resolved project back to
the real financial record, not just vula_filed_documents.

Real bug this closes: when a tenant answered "which project?" for a pending invoice/quote/BoQ,
only vula_filed_documents.project got updated — the commerce_invoices row commit_inbound_
document already committed (at filing time, project unknown) never had its `project` touched.
Confirmed live: a real R240,553.53 BoQ-derived quote sat permanently unlinked from any project
this way, invisible to project_financials() forever, even after being correctly answered.
"""
from unittest.mock import MagicMock, patch

import pytest

from vula.integrations.doc_filing import resolve_pending_document

TID = "digg-demo"
PHONE = "27645755210"


def _pending_doc(**overrides):
    doc = {
        "id": "doc1", "filename": "Judy_Bouwerk2.xlsx", "status": "pending_project",
        "filed_by": PHONE, "category": "Bill of Quantities (BOQ)",
        "commerce_invoice_id": "quote1", "fields": {"total_cents": 24055353},
        "content_hash": "abc123", "file_url": None, "created_at": "2026-08-12T00:00:00Z",
    }
    doc.update(overrides)
    return doc


def _mock_db(doc_row):
    updates = {}

    def table(name):
        t = MagicMock()
        if name == "vula_filed_documents":
            (t.select.return_value.eq.return_value.eq.return_value.gte.return_value
             .order.return_value.eq.return_value.limit.return_value.execute.return_value) = \
                MagicMock(data=[doc_row])

            def update(patch_dict):
                updates["vula_filed_documents"] = patch_dict
                m = MagicMock()
                m.eq.return_value.execute.return_value = MagicMock(data=[{}])
                return m
            t.update.side_effect = update
        elif name == "commerce_invoices":
            def update(patch_dict):
                updates["commerce_invoices"] = patch_dict
                m = MagicMock()
                m.eq.return_value.execute.return_value = MagicMock(data=[{}])
                return m
            t.update.side_effect = update
        return t

    mock_db = MagicMock()
    mock_db.table.side_effect = table
    return mock_db, updates


@pytest.mark.asyncio
async def test_resolving_a_boq_propagates_project_and_bridges_contract_value():
    mock_db, updates = _mock_db(_pending_doc())
    with (
        patch("vula.integrations.doc_filing._client", return_value=mock_db),
        patch("vula.integrations.doc_filing.match_project",
              return_value={"project": "Porterfield", "clickup_list_id": None}),
        patch("vula.integrations.doc_filing._existing_filed_doc", return_value=None),
        patch("vula.integrations.doc_filing.learn_filing_rule", return_value=1),
        patch("vula.integrations.finances.post_finance_from_doc"),
        patch("vula.commerce.service.upsert_project_boq") as mock_boq,
    ):
        result = await resolve_pending_document(TID, PHONE, "Porterfield")

    assert result["filed"] is True
    assert updates["vula_filed_documents"]["project"] == "Porterfield"
    assert updates["commerce_invoices"]["project"] == "Porterfield"
    mock_boq.assert_called_once_with(TID, "Porterfield", 24055353)


@pytest.mark.asyncio
async def test_resolving_a_non_boq_invoice_propagates_project_but_no_boq_bridge():
    mock_db, updates = _mock_db(_pending_doc(category="Invoice"))
    with (
        patch("vula.integrations.doc_filing._client", return_value=mock_db),
        patch("vula.integrations.doc_filing.match_project",
              return_value={"project": "HPC_Bokaap", "clickup_list_id": None}),
        patch("vula.integrations.doc_filing._existing_filed_doc", return_value=None),
        patch("vula.integrations.doc_filing.learn_filing_rule", return_value=1),
        patch("vula.integrations.finances.post_finance_from_doc"),
        patch("vula.commerce.service.upsert_project_boq") as mock_boq,
    ):
        result = await resolve_pending_document(TID, PHONE, "HPC_Bokaap")

    assert result["filed"] is True
    assert updates["commerce_invoices"]["project"] == "HPC_Bokaap"
    mock_boq.assert_not_called()


@pytest.mark.asyncio
async def test_no_commerce_invoice_id_skips_propagation_without_error():
    """A document category with no linked financial record (e.g. a Drawing) must not error —
    only vula_filed_documents.project gets touched."""
    mock_db, updates = _mock_db(_pending_doc(category="Drawing / Plan", commerce_invoice_id=None))
    with (
        patch("vula.integrations.doc_filing._client", return_value=mock_db),
        patch("vula.integrations.doc_filing.match_project",
              return_value={"project": "HPC_Bokaap", "clickup_list_id": None}),
        patch("vula.integrations.doc_filing._existing_filed_doc", return_value=None),
        patch("vula.integrations.doc_filing.learn_filing_rule", return_value=1),
        patch("vula.integrations.finances.post_finance_from_doc"),
    ):
        result = await resolve_pending_document(TID, PHONE, "HPC_Bokaap")

    assert result["filed"] is True
    assert "commerce_invoices" not in updates
