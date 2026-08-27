"""Tests for vula/commerce/expenses.py's odometer tracking (migration 142) — a real KM logbook
column for petrol claims, from Ian's own original claim-sheet template."""
from unittest.mock import MagicMock, patch

import pytest

from vula.commerce import expenses

TID = "gerflor"


# ── parse_odometer_reading ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("45280", 45280),
    ("45,280", 45280),
    ("45 280", 45280),
    ("45280km", 45280),
    ("45280 km", 45280),
    ("45280kms", 45280),
    ("45280 KM.", 45280),
])
def test_parse_odometer_reading_valid(text, expected):
    assert expenses.parse_odometer_reading(text) == expected


@pytest.mark.parametrize("text", ["", None, "hello", "fuel", "0", "-5", "3000000", "45280 rand"])
def test_parse_odometer_reading_invalid(text):
    assert expenses.parse_odometer_reading(text) is None


# ── set_odometer ──────────────────────────────────────────────────────────────────

def test_set_odometer_writes_value():
    mock_client = MagicMock()
    mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"id": "e1", "odometer_km": 45280}])
    with patch("vula.commerce.expenses._client", return_value=mock_client):
        out = expenses.set_odometer(TID, "e1", 45280)
    patch_arg = mock_client.table.return_value.update.call_args[0][0]
    assert patch_arg["odometer_km"] == 45280
    assert out["odometer_km"] == 45280


# ── last_odometer_before ────────────────────────────────────────────────────────

def test_last_odometer_before_returns_most_recent_prior_reading():
    mock_client = MagicMock()
    query = mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .eq.return_value.not_.is_.return_value.lt.return_value.order.return_value.limit.return_value
    query.execute.return_value = MagicMock(data=[{"id": "e0", "odometer_km": 44900}])
    with patch("vula.commerce.expenses._client", return_value=mock_client):
        km = expenses.last_odometer_before(TID, "27821234567", "2026-08-19")
    assert km == 44900


def test_last_odometer_before_skips_excluded_id():
    mock_client = MagicMock()
    query = mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .eq.return_value.not_.is_.return_value.lt.return_value.order.return_value.limit.return_value
    query.execute.return_value = MagicMock(data=[
        {"id": "e1", "odometer_km": 45280},  # this is the current claim itself — must be skipped
        {"id": "e0", "odometer_km": 44900},
    ])
    with patch("vula.commerce.expenses._client", return_value=mock_client):
        km = expenses.last_odometer_before(TID, "27821234567", "2026-08-19", exclude_id="e1")
    assert km == 44900


def test_last_odometer_before_none_when_nothing_prior():
    mock_client = MagicMock()
    query = mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .eq.return_value.not_.is_.return_value.lt.return_value.order.return_value.limit.return_value
    query.execute.return_value = MagicMock(data=[])
    with patch("vula.commerce.expenses._client", return_value=mock_client):
        km = expenses.last_odometer_before(TID, "27821234567", "2026-08-19")
    assert km is None


def test_last_odometer_before_never_raises_on_db_error():
    mock_client = MagicMock()
    mock_client.table.side_effect = RuntimeError("boom")
    with patch("vula.commerce.expenses._client", return_value=mock_client):
        km = expenses.last_odometer_before(TID, "27821234567", "2026-08-19")
    assert km is None
