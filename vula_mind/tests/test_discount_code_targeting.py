"""Tests for discount code first_order_only + per_customer_limit (migration 105) — the
third gap closed from the marketing capability audit. Migration 091 deliberately scoped
codes to storewide/global-usage-limit only; this adds the two customer-aware checks without
touching the existing storewide percent/fixed/free_shipping computation."""
from unittest.mock import MagicMock, patch

import pytest

from vula.commerce.service import DiscountError, resolve_discount_code

_CODE_ROW = {
    "id": "code1", "code": "WELCOME10", "type": "percent", "value": 10,
    "active": True, "starts_at": None, "ends_at": None, "usage_limit": None,
    "usage_count": 0, "min_order_cents": None,
}


def _mock_db(code_row, orders_data=None, orders_count=None):
    codes_table = MagicMock()
    codes_table.select.return_value.eq.return_value.ilike.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=[code_row] if code_row else [])

    orders_table = MagicMock()
    # first_order_only check: select("id").eq(tenant).eq(phone).limit(1).execute()
    orders_table.select.return_value.eq.return_value.eq.return_value.limit.return_value \
        .execute.return_value = MagicMock(data=orders_data or [])
    # per_customer_limit check: select("id", count="exact").eq(tenant).eq(phone).ilike(...).execute()
    orders_table.select.return_value.eq.return_value.eq.return_value.ilike.return_value \
        .execute.return_value = MagicMock(data=orders_data or [], count=orders_count)

    def _table(name):
        return codes_table if name == "commerce_discount_codes" else orders_table

    mock_db = MagicMock()
    mock_db.table.side_effect = _table
    return mock_db


@pytest.mark.asyncio
async def test_no_phone_skips_customer_checks_entirely():
    """The public preview endpoint calls this with no phone — must not error just because
    the code has first_order_only/per_customer_limit set."""
    row = {**_CODE_ROW, "first_order_only": True, "per_customer_limit": 1}
    mock_db = _mock_db(row)
    with patch("vula.commerce.service._client", return_value=mock_db):
        result = await resolve_discount_code("off-the-hook", "WELCOME10", 10000)
    assert result["discount_cents"] == 1000


@pytest.mark.asyncio
async def test_first_order_only_allows_customer_with_no_prior_orders():
    row = {**_CODE_ROW, "first_order_only": True}
    mock_db = _mock_db(row, orders_data=[])
    with patch("vula.commerce.service._client", return_value=mock_db):
        result = await resolve_discount_code("off-the-hook", "WELCOME10", 10000, "27821234567")
    assert result["discount_cents"] == 1000


@pytest.mark.asyncio
async def test_first_order_only_rejects_repeat_customer():
    row = {**_CODE_ROW, "first_order_only": True}
    mock_db = _mock_db(row, orders_data=[{"id": "prior-order"}])
    with patch("vula.commerce.service._client", return_value=mock_db):
        with pytest.raises(DiscountError, match="first order"):
            await resolve_discount_code("off-the-hook", "WELCOME10", 10000, "27821234567")


@pytest.mark.asyncio
async def test_per_customer_limit_allows_under_the_cap():
    row = {**_CODE_ROW, "per_customer_limit": 2}
    mock_db = _mock_db(row, orders_count=1)
    with patch("vula.commerce.service._client", return_value=mock_db):
        result = await resolve_discount_code("off-the-hook", "WELCOME10", 10000, "27821234567")
    assert result["discount_cents"] == 1000


@pytest.mark.asyncio
async def test_per_customer_limit_rejects_at_the_cap():
    row = {**_CODE_ROW, "per_customer_limit": 2}
    mock_db = _mock_db(row, orders_count=2)
    with patch("vula.commerce.service._client", return_value=mock_db):
        with pytest.raises(DiscountError, match="maximum number of times"):
            await resolve_discount_code("off-the-hook", "WELCOME10", 10000, "27821234567")


@pytest.mark.asyncio
async def test_per_customer_limit_falls_back_to_data_length_when_count_none():
    """Supabase's count can come back None depending on the client version — must not crash,
    same defensive fallback already used elsewhere in this codebase (e.g. approvals.py)."""
    row = {**_CODE_ROW, "per_customer_limit": 1}
    mock_db = _mock_db(row, orders_data=[{"id": "o1"}], orders_count=None)
    with patch("vula.commerce.service._client", return_value=mock_db):
        with pytest.raises(DiscountError, match="maximum number of times"):
            await resolve_discount_code("off-the-hook", "WELCOME10", 10000, "27821234567")


@pytest.mark.asyncio
async def test_neither_flag_set_customer_checks_are_a_noop():
    mock_db = _mock_db(dict(_CODE_ROW))
    with patch("vula.commerce.service._client", return_value=mock_db):
        result = await resolve_discount_code("off-the-hook", "WELCOME10", 10000, "27821234567")
    assert result["discount_cents"] == 1000
