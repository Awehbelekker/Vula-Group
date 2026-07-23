"""Tests for customer segmentation by tags/area/language — data that was already captured
(commerce_contacts.tags/area from the OTH import, commerce_conversation_sessions.
preferred_language from migration 052) but wasn't usable to target a broadcast segment.
Covers _apply_criteria's new filter keys, _aggregate_customers pulling preferred_language
into the merged customer row, and the new admin_customer_tags endpoint that lets a tag be
set from the dashboard (previously no UI existed at all)."""
from unittest.mock import MagicMock, patch

import pytest

from vula.api.commerce import _apply_criteria, admin_customer_tags, _aggregate_customers


def test_apply_criteria_tags_matches_any_overlap():
    rows = [
        {"phone": "1", "tags": ["vip", "wholesale"], "total_spent_cents": 0, "orders": 0},
        {"phone": "2", "tags": ["retail"], "total_spent_cents": 0, "orders": 0},
        {"phone": "3", "tags": [], "total_spent_cents": 0, "orders": 0},
    ]
    out = _apply_criteria(rows, {"tags": ["vip"]})
    assert [r["phone"] for r in out] == ["1"]


def test_apply_criteria_tags_is_case_insensitive():
    rows = [{"phone": "1", "tags": ["VIP"], "total_spent_cents": 0, "orders": 0}]
    out = _apply_criteria(rows, {"tags": ["vip"]})
    assert len(out) == 1


def test_apply_criteria_area_exact_match_case_insensitive():
    rows = [
        {"phone": "1", "area": "Tableview", "total_spent_cents": 0, "orders": 0},
        {"phone": "2", "area": "Sunningdale", "total_spent_cents": 0, "orders": 0},
        {"phone": "3", "area": None, "total_spent_cents": 0, "orders": 0},
    ]
    out = _apply_criteria(rows, {"area": "tableview"})
    assert [r["phone"] for r in out] == ["1"]


def test_apply_criteria_language_exact_match():
    rows = [
        {"phone": "1", "preferred_language": "zu", "total_spent_cents": 0, "orders": 0},
        {"phone": "2", "preferred_language": "en", "total_spent_cents": 0, "orders": 0},
        {"phone": "3", "preferred_language": None, "total_spent_cents": 0, "orders": 0},
    ]
    out = _apply_criteria(rows, {"language": "zu"})
    assert [r["phone"] for r in out] == ["1"]


def test_apply_criteria_combines_new_and_existing_keys_anded():
    rows = [
        {"phone": "1", "tags": ["vip"], "area": "Tableview", "total_spent_cents": 60000, "orders": 3},
        {"phone": "2", "tags": ["vip"], "area": "Tableview", "total_spent_cents": 100, "orders": 1},
    ]
    out = _apply_criteria(rows, {"tags": ["vip"], "area": "Tableview", "min_spend": 500})
    assert [r["phone"] for r in out] == ["1"]


def test_apply_criteria_no_tags_or_area_criteria_is_unaffected():
    """Existing segments (built before this feature) must keep working unchanged."""
    rows = [{"phone": "1", "tags": [], "area": None, "total_spent_cents": 60000, "orders": 2}]
    out = _apply_criteria(rows, {"min_spend": 500})
    assert len(out) == 1


@pytest.mark.asyncio
async def test_admin_customer_tags_upserts_contact():
    mock_table = MagicMock()
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table

    with patch("vula.api.commerce.service._client", return_value=mock_db):
        result = await admin_customer_tags(
            "off-the-hook", "0821234567", {"tags": ["vip", " wholesale ", ""], "area": " Tableview "})

    assert result == {"ok": True, "tags": ["vip", "wholesale"], "area": "Tableview"}
    mock_table.upsert.assert_called_once()
    row, kwargs = mock_table.upsert.call_args[0][0], mock_table.upsert.call_args[1]
    assert row["phone"] == "27821234567"   # normalised via _norm_phone (0xx -> 27xx)
    assert row["tags"] == ["vip", "wholesale"]
    assert row["area"] == "Tableview"
    assert kwargs["on_conflict"] == "tenant_id,phone"


@pytest.mark.asyncio
async def test_admin_customer_tags_rejects_invalid_phone():
    with pytest.raises(Exception):
        await admin_customer_tags("off-the-hook", "", {"tags": ["vip"]})


@pytest.mark.asyncio
async def test_aggregate_customers_pulls_preferred_language_from_newest_session():
    mock_db = MagicMock()

    def _table(name):
        t = MagicMock()
        if name == "commerce_orders":
            t.select.return_value.eq.return_value.order.return_value.limit.return_value \
                .execute.return_value = MagicMock(data=[])
        elif name == "commerce_conversation_sessions":
            # Newest-first order — the first row for a phone should win.
            t.select.return_value.eq.return_value.order.return_value.limit.return_value \
                .execute.return_value = MagicMock(data=[
                    {"customer_phone": "27821234567", "customer_name": "Jane", "channel": "whatsapp",
                     "updated_at": "2026-07-20T00:00:00Z", "session_key": "27821234567",
                     "preferred_language": "zu"},
                    {"customer_phone": "27821234567", "customer_name": "Jane", "channel": "whatsapp",
                     "updated_at": "2026-01-01T00:00:00Z", "session_key": "27821234567",
                     "preferred_language": "en"},
                ])
        elif name == "commerce_contacts":
            t.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
                MagicMock(data=[])
        return t

    mock_db.table.side_effect = _table

    with patch("vula.api.commerce.service._client", return_value=mock_db):
        customers = await _aggregate_customers("off-the-hook", use_cache=False)

    assert customers["27821234567"]["preferred_language"] == "zu"
