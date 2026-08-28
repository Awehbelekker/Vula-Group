"""Tests for the odometer-capture flow wired into whatsapp.py (migration 142): the
_log_expense_claim question when a claim classifies as petrol, and
_maybe_allocate_pending_odometer's single/multi-pending resolution."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.whatsapp import (
    _log_expense_claim,
    _maybe_allocate_pending_odometer,
    _maybe_allocate_pending_purpose,
)

TID = "gerflor"
PHONE = "27707490592"

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


# ── _log_expense_claim: odometer question on a confident petrol classification ───

@pytest.mark.asyncio
async def test_confident_petrol_classification_asks_for_odometer():
    p = _common_patches()
    with (
        p[0], p[1], p[2], p[3], p[4],
        patch("vula.commerce.expenses.create_claim", new=AsyncMock(return_value=dict(BASE_CLAIM))),
        patch("vula.commerce.expenses.classify_purpose_category", new=AsyncMock(return_value="petrol")),
        patch("vula.commerce.expenses.set_purpose_category"),
    ):
        msg = await _log_expense_claim(TID, PHONE, {"total_cents": 74580, "supplier": "Engen"})
    assert "odometer reading" in msg


@pytest.mark.asyncio
async def test_non_petrol_classification_does_not_ask_for_odometer():
    p = _common_patches()
    with (
        p[0], p[1], p[2], p[3], p[4],
        patch("vula.commerce.expenses.create_claim", new=AsyncMock(return_value=dict(BASE_CLAIM))),
        patch("vula.commerce.expenses.classify_purpose_category", new=AsyncMock(return_value="clients")),
        patch("vula.commerce.expenses.set_purpose_category"),
    ):
        msg = await _log_expense_claim(TID, PHONE, {"total_cents": 12000, "supplier": "Mugg & Bean"})
    assert "odometer reading" not in msg


# ── _maybe_allocate_pending_odometer: single pending ─────────────────────────────

def _fake_service_client(rows):
    mock_client = MagicMock()
    query = mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .eq.return_value.eq.return_value.eq.return_value.is_.return_value.order.return_value
    query.execute.return_value = MagicMock(data=rows)
    return mock_client


@pytest.mark.asyncio
async def test_single_pending_odometer_sets_reading():
    rows = [{"id": "c1", "amount_cents": 74580, "supplier": "Engen"}]
    with (
        patch("vula.commerce.service._client", return_value=_fake_service_client(rows)),
        patch("vula.commerce.expenses.set_odometer") as mock_set,
    ):
        reply = await _maybe_allocate_pending_odometer(TID, PHONE, "45280")
    assert "45,280 km" in reply
    mock_set.assert_called_once_with(TID, "c1", 45280)


@pytest.mark.asyncio
async def test_single_pending_odometer_non_numeric_reply_unresolved():
    rows = [{"id": "c1", "amount_cents": 74580, "supplier": "Engen"}]
    with (
        patch("vula.commerce.service._client", return_value=_fake_service_client(rows)),
        patch("vula.commerce.expenses.set_odometer") as mock_set,
    ):
        reply = await _maybe_allocate_pending_odometer(TID, PHONE, "hey what's up")
    assert reply is None
    mock_set.assert_not_called()


@pytest.mark.asyncio
async def test_no_pending_odometer_claims_returns_none():
    with patch("vula.commerce.service._client", return_value=_fake_service_client([])):
        reply = await _maybe_allocate_pending_odometer(TID, PHONE, "45280")
    assert reply is None


# ── _maybe_allocate_pending_odometer: multi-pending ───────────────────────────────

@pytest.mark.asyncio
async def test_multi_pending_indexed_reply_resolves_each():
    rows = [
        {"id": "c1", "amount_cents": 74580, "supplier": "Engen", "date": "2026-08-19"},
        {"id": "c2", "amount_cents": 65000, "supplier": "Shell", "date": "2026-08-25"},
    ]
    with (
        patch("vula.commerce.service._client", return_value=_fake_service_client(rows)),
        patch("vula.commerce.expenses.set_odometer") as mock_set,
    ):
        reply = await _maybe_allocate_pending_odometer(TID, PHONE, "1 45280, 2 46100")
    assert mock_set.call_count == 2
    mock_set.assert_any_call(TID, "c1", 45280)
    mock_set.assert_any_call(TID, "c2", 46100)
    assert "Noted" in reply


@pytest.mark.asyncio
async def test_multi_pending_bare_number_lists_and_asks():
    rows = [
        {"id": "c1", "amount_cents": 74580, "supplier": "Engen", "date": "2026-08-19"},
        {"id": "c2", "amount_cents": 65000, "supplier": "Shell", "date": "2026-08-25"},
    ]
    with (
        patch("vula.commerce.service._client", return_value=_fake_service_client(rows)),
        patch("vula.commerce.expenses.set_odometer") as mock_set,
    ):
        reply = await _maybe_allocate_pending_odometer(TID, PHONE, "45280")
    mock_set.assert_not_called()
    assert "1)" in reply and "2)" in reply


@pytest.mark.asyncio
async def test_multi_pending_unrelated_text_unresolved():
    rows = [
        {"id": "c1", "amount_cents": 74580, "supplier": "Engen", "date": "2026-08-19"},
        {"id": "c2", "amount_cents": 65000, "supplier": "Shell", "date": "2026-08-25"},
    ]
    with patch("vula.commerce.service._client", return_value=_fake_service_client(rows)):
        reply = await _maybe_allocate_pending_odometer(TID, PHONE, "thanks!")
    assert reply is None


# ── _maybe_allocate_pending_purpose: a bare number is never swallowed as a purpose ─

def _fake_purpose_client(rows):
    mock_client = MagicMock()
    query = mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .eq.return_value.eq.return_value.is_.return_value.order.return_value
    query.execute.return_value = MagicMock(data=rows)
    return mock_client


@pytest.mark.asyncio
async def test_purpose_resolver_ignores_bare_number():
    rows = [{"id": "c1", "amount_cents": 5000, "supplier": "Some Shop"}]
    with (
        patch("vula.commerce.service._client", return_value=_fake_purpose_client(rows)),
        patch("vula.commerce.expenses.set_purpose_category") as mock_set,
    ):
        reply = await _maybe_allocate_pending_purpose(TID, PHONE, "45280")
    assert reply is None
    mock_set.assert_not_called()


@pytest.mark.asyncio
async def test_purpose_resolver_petrol_answer_asks_for_odometer_too():
    # Vendor deliberately non-matching against classify_purpose_category_deterministic (2026-08-28
    # self-heal fix) — this test's whole point is resolving via the REPLY text ("fuel"), not
    # having the vendor name pre-empt it.
    rows = [{"id": "c1", "amount_cents": 74580, "supplier": "Corner Shop"}]
    with (
        patch("vula.commerce.service._client", return_value=_fake_purpose_client(rows)),
        patch("vula.commerce.expenses.set_purpose_category"),
    ):
        reply = await _maybe_allocate_pending_purpose(TID, PHONE, "fuel")
    assert "odometer reading" in reply
