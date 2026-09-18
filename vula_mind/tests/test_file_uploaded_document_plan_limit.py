"""Test for the plan-limit note override in _file_uploaded_document (vula/api/whatsapp.py) —
go-live readiness pass, Phase 4.1. file_document() returns no "id" plus a displayable error
when a Starter tenant is over the document cap, rather than raising; without this override the
WhatsApp reply would have falsely claimed "📂 Filed under X" even though nothing was stored."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.whatsapp import _file_uploaded_document

TID = "digg-demo"
PHONE = "27645755210"


@pytest.mark.asyncio
async def test_plan_limit_reached_note_overrides_false_success_message(tmp_path):
    local_file = tmp_path / "invoice.pdf"
    local_file.write_bytes(b"fake pdf bytes")

    plan_limit_row = {
        "tenant_id": TID, "filename": "invoice.pdf", "status": "filed",
        "plan_limit_reached": True,
        "error": "You've reached the 25-document limit on Starter — "
                 "upgrade to Growth for unlimited document intelligence.",
    }

    with (
        patch("vula.integrations.doc_filing.match_project",
              return_value={"project": "Bokaap", "confidence": 0.9, "clickup_list_id": None}),
        patch("vula.integrations.doc_filing.file_document", new=AsyncMock(return_value=plan_limit_row)),
        patch("vula.integrations.doc_filing.lookup_learned_project", return_value=None),
        patch("vula.commerce.service._client", return_value=MagicMock()),
    ):
        note, row = await _file_uploaded_document(
            TID, PHONE, MagicMock(filename="invoice.pdf", doc_id="d1"),
            local_file, "application/pdf", "Invoice", "A supplier invoice.", {},
        )

    assert row.get("plan_limit_reached") is True
    assert "upgrade to Growth" in note
    assert "Filed under" not in note
