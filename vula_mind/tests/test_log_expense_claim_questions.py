"""Tests for _log_expense_claim's follow-up questions (2026-08-12 fix).

Real bug: the project-allocation prompt and the payment-method prompt used to be an if/elif —
mutually exclusive — so whenever a project question was also needed (the common case for a
project-running tenant), the payment-method question got silently dropped entirely, even with
paid_with genuinely unknown and registered company cards on file. Confirmed the underlying
answer-handler (_maybe_allocate_pending_expense) already processes both answer types
independently, so the fix is purely "ask both when both apply."
"""
from unittest.mock import AsyncMock, patch

import pytest

from vula.api.whatsapp import _log_expense_claim

TID = "digg-demo"
PHONE = "27827077080"

SCAN_DATA = {"total_cents": 50000, "supplier": "Bauxite Extrusions", "notes": "Hardware"}


def _patches(*, needs_project: bool, has_project: bool, paid_with, cards_registered: bool):
    claim = {
        "id": "c1", "amount_cents": 50000, "category": "supplies",
        "project": ("HPC_Bokaap" if has_project else None),
        "reimbursable": paid_with == "personal", "needs_project": needs_project,
    }
    return (
        patch("vula.commerce.expenses.create_claim", new=AsyncMock(return_value=claim)),
        patch("vula.commerce.expenses.resolve_paid_with", return_value=paid_with),
        patch("vula.commerce.expenses.match_project", return_value=None),
        patch("vula.commerce.expenses.list_cards", return_value=(["card1"] if cards_registered else [])),
        patch("vula.models.tenants.get_tenant_db", side_effect=Exception("no tenant db in test")),
        patch("vula.models.field_ops.get_field_ops_db", side_effect=Exception("no field ops in test")),
    )


@pytest.mark.asyncio
async def test_asks_both_questions_when_both_are_needed():
    patches = _patches(needs_project=True, has_project=False, paid_with=None, cards_registered=True)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        msg = await _log_expense_claim(TID, PHONE, SCAN_DATA)
    assert "Which project/site is this for?" in msg
    assert "company card" in msg and "own money" in msg


@pytest.mark.asyncio
async def test_asks_only_project_when_payment_method_already_known():
    patches = _patches(needs_project=True, has_project=False, paid_with="personal", cards_registered=True)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        msg = await _log_expense_claim(TID, PHONE, SCAN_DATA)
    assert "Which project/site is this for?" in msg
    assert "Reply 'company' or 'own'" not in msg


@pytest.mark.asyncio
async def test_asks_only_payment_method_when_project_already_known():
    patches = _patches(needs_project=False, has_project=True, paid_with=None, cards_registered=True)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        msg = await _log_expense_claim(TID, PHONE, SCAN_DATA)
    assert "Which project/site is this for?" not in msg
    assert "company card" in msg and "own money" in msg


@pytest.mark.asyncio
async def test_asks_neither_when_both_already_resolved():
    patches = _patches(needs_project=False, has_project=True, paid_with="company_card", cards_registered=True)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        msg = await _log_expense_claim(TID, PHONE, SCAN_DATA)
    assert "Which project/site is this for?" not in msg
    assert "Reply 'company' or 'own'" not in msg


# ── learned supplier→project rule (2026-08-12) ──────────────────────────────────

@pytest.mark.asyncio
async def test_uses_learned_project_before_asking():
    """Same learned-rules mechanism the document-filing path already uses — a previously
    confirmed supplier→project mapping should resolve the project without ever asking."""
    patches = _patches(needs_project=True, has_project=False, paid_with="personal", cards_registered=True)
    create_claim_mock = AsyncMock(return_value={
        "id": "c1", "amount_cents": 50000, "category": "supplies",
        "project": "HPC_Bokaap", "reimbursable": True, "needs_project": False,
    })
    with (
        patches[1], patches[3], patches[4], patches[5],
        patch("vula.commerce.expenses.create_claim", new=create_claim_mock),
        patch("vula.commerce.expenses.match_project") as mock_match_project,
        patch("vula.integrations.doc_filing.lookup_learned_project",
              return_value={"project": "HPC_Bokaap", "ambiguous": False}),
    ):
        msg = await _log_expense_claim(TID, PHONE, SCAN_DATA)

    assert "Which project/site is this for?" not in msg
    create_claim_mock.assert_called_once()
    assert create_claim_mock.call_args.kwargs["project"] == "HPC_Bokaap"
    mock_match_project.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_to_text_match_when_learned_rule_is_ambiguous():
    patches = _patches(needs_project=True, has_project=False, paid_with="personal", cards_registered=True)
    with (
        patches[0], patches[1], patches[3], patches[4], patches[5],
        patch("vula.commerce.expenses.match_project", return_value=None) as mock_match_project,
        patch("vula.integrations.doc_filing.lookup_learned_project",
              return_value={"project": None, "ambiguous": True, "candidates": ["HPC_Bokaap", "SPORTY.TV"]}),
    ):
        await _log_expense_claim(TID, PHONE, SCAN_DATA)

    mock_match_project.assert_called_once()
