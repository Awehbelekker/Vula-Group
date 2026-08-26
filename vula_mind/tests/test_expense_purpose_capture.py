"""Tests for the purpose-category capture flow wired into whatsapp.py: _log_expense_claim's
auto-classify + note/question, and _maybe_allocate_pending_purpose's single/multi-pending
freeform-reply resolution (migrations 140/141)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.whatsapp import _log_expense_claim, _maybe_allocate_pending_purpose

TID = "gerflor"
PHONE = "27739852984"

BASE_CLAIM = {
    "id": "c1", "amount_cents": 74580, "category": "fuel", "project": None,
    "reimbursable": False, "needs_project": False,
}


def _common_patches():
    return (
        patch("vula.commerce.expenses.resolve_paid_with", return_value="company_card"),
        patch("vula.commerce.expenses.match_project", return_value=None),
        patch("vula.commerce.expenses.list_cards", return_value=[]),
        patch("vula.models.tenants.get_tenant_db", side_effect=Exception("no tenant db in test")),
        patch("vula.models.field_ops.get_field_ops_db", side_effect=Exception("no field ops in test")),
    )


# ── _log_expense_claim: auto-classify note vs. question ─────────────────────────

@pytest.mark.asyncio
async def test_confident_classification_adds_note_not_a_question():
    p = _common_patches()
    with (
        p[0], p[1], p[2], p[3], p[4],
        patch("vula.commerce.expenses.create_claim", new=AsyncMock(return_value=dict(BASE_CLAIM))),
        patch("vula.commerce.expenses.classify_purpose_category", new=AsyncMock(return_value="petrol")),
        patch("vula.commerce.expenses.set_purpose_category") as mock_set,
    ):
        msg = await _log_expense_claim(TID, PHONE, {"total_cents": 74580, "supplier": "Engen"})
    assert "Logged as *Petrol*" in msg
    assert "What was this for" not in msg
    mock_set.assert_called_once_with(TID, "c1", "petrol")


@pytest.mark.asyncio
async def test_uncertain_classification_asks_instead_of_noting():
    p = _common_patches()
    with (
        p[0], p[1], p[2], p[3], p[4],
        patch("vula.commerce.expenses.create_claim", new=AsyncMock(return_value=dict(BASE_CLAIM))),
        patch("vula.commerce.expenses.classify_purpose_category", new=AsyncMock(return_value="uncertain")),
        patch("vula.commerce.expenses.set_purpose_category") as mock_set,
    ):
        msg = await _log_expense_claim(TID, PHONE, {"total_cents": 74580, "supplier": "Woolworths"})
    assert "What was this for" in msg
    assert "Logged as *" not in msg
    mock_set.assert_not_called()


@pytest.mark.asyncio
async def test_classification_failure_never_breaks_the_claim_reply():
    p = _common_patches()
    with (
        p[0], p[1], p[2], p[3], p[4],
        patch("vula.commerce.expenses.create_claim", new=AsyncMock(return_value=dict(BASE_CLAIM))),
        patch("vula.commerce.expenses.classify_purpose_category", new=AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        msg = await _log_expense_claim(TID, PHONE, {"total_cents": 74580, "supplier": "Engen"})
    assert "Logged as an expense" in msg  # the claim confirmation itself still went out


# ── _maybe_allocate_pending_purpose: single pending ──────────────────────────────

def _fake_service_client(rows):
    mock_client = MagicMock()
    query = mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .eq.return_value.eq.return_value.is_.return_value.order.return_value
    query.execute.return_value = MagicMock(data=rows)
    return mock_client


@pytest.mark.asyncio
async def test_single_pending_matches_a_known_keyword():
    rows = [{"id": "c1", "amount_cents": 12000, "supplier": "Coffee shop"}]
    with (
        patch("vula.commerce.service._client", return_value=_fake_service_client(rows)),
        patch("vula.commerce.expenses.set_purpose_category") as mock_set,
    ):
        reply = await _maybe_allocate_pending_purpose(TID, PHONE, "coffee with a client")
    assert "Logged that" in reply and "Clients" in reply
    mock_set.assert_called_once_with(TID, "c1", "clients", detail=None)


@pytest.mark.asyncio
async def test_single_pending_falls_back_to_other_with_detail_preserved():
    rows = [{"id": "c1", "amount_cents": 5000, "supplier": "Random Shop"}]
    with (
        patch("vula.commerce.service._client", return_value=_fake_service_client(rows)),
        patch("vula.commerce.expenses.set_purpose_category") as mock_set,
    ):
        reply = await _maybe_allocate_pending_purpose(TID, PHONE, "printer ink for the office")
    assert "Other" in reply
    mock_set.assert_called_once_with(TID, "c1", "other", detail="printer ink for the office")


@pytest.mark.asyncio
async def test_no_pending_claims_returns_none():
    with patch("vula.commerce.service._client", return_value=_fake_service_client([])):
        reply = await _maybe_allocate_pending_purpose(TID, PHONE, "fuel")
    assert reply is None


# ── _maybe_allocate_pending_purpose: multiple pending (indexed reply) ────────────

@pytest.mark.asyncio
async def test_multi_pending_indexed_reply_resolves_each_by_id():
    rows = [
        {"id": "c1", "amount_cents": 8500, "supplier": "Woolworths"},
        {"id": "c2", "amount_cents": 45000, "supplier": "Engen"},
    ]
    with (
        patch("vula.commerce.service._client", return_value=_fake_service_client(rows)),
        patch("vula.commerce.expenses.set_purpose_category") as mock_set,
    ):
        reply = await _maybe_allocate_pending_purpose(TID, PHONE, "1 office supplies, 2 fuel")
    assert mock_set.call_count == 2
    mock_set.assert_any_call(TID, "c1", "other", detail="office supplies")
    mock_set.assert_any_call(TID, "c2", "petrol", detail=None)
    assert "Logged:" in reply


@pytest.mark.asyncio
async def test_multi_pending_unparseable_reply_lists_and_reasks():
    rows = [
        {"id": "c1", "amount_cents": 8500, "supplier": "Woolworths"},
        {"id": "c2", "amount_cents": 45000, "supplier": "Engen"},
    ]
    with (
        patch("vula.commerce.service._client", return_value=_fake_service_client(rows)),
        patch("vula.commerce.expenses.set_purpose_category") as mock_set,
    ):
        reply = await _maybe_allocate_pending_purpose(TID, PHONE, "fuel")
    mock_set.assert_not_called()
    assert "1)" in reply and "2)" in reply
    assert "Woolworths" in reply and "Engen" in reply


@pytest.mark.asyncio
async def test_multi_pending_partial_indexed_reply_still_reasks():
    rows = [
        {"id": "c1", "amount_cents": 8500, "supplier": "Woolworths"},
        {"id": "c2", "amount_cents": 45000, "supplier": "Engen"},
    ]
    with (
        patch("vula.commerce.service._client", return_value=_fake_service_client(rows)),
        patch("vula.commerce.expenses.set_purpose_category") as mock_set,
    ):
        reply = await _maybe_allocate_pending_purpose(TID, PHONE, "1 office supplies")
    mock_set.assert_not_called()
    assert "1)" in reply and "2)" in reply
