"""Tests for vula/commerce/plan_limits.py (go-live readiness pass, Phase 4) — enforcing the
plan limits VulaOnboarding.jsx already advertises to prospects (Starter: 25 filed documents,
1-2 users; Growth: unlimited documents, 5 users; Business: unlimited both). Mirrors commerce/
service.py's discount-code usage_limit check: fail-open on a count-query error, fail-closed
once the count is actually known and over cap."""
from unittest.mock import MagicMock, patch

import pytest

from vula.commerce.plan_limits import (
    PlanLimitError,
    check_document_quota,
    check_seat_quota,
)

TID = "digg-demo"


def _mock_count_client(count):
    """For check_document_quota's .select(...).eq(...).execute() chain."""
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(count=count, data=[])
    return db


def _mock_seat_count_client(count):
    """For check_seat_quota's .select(...).eq(...).in_(...).execute() chain."""
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.in_.return_value.execute.return_value = MagicMock(count=count, data=[])
    return db


# ── check_document_quota ────────────────────────────────────────────────────────

def test_document_quota_ok_below_cap():
    with (
        patch("vula.api.tenants.get_config", return_value={"plan": "starter"}),
        patch("vula.commerce.service._client", return_value=_mock_count_client(24)),
    ):
        check_document_quota(TID)  # must not raise


def test_document_quota_raises_at_cap():
    with (
        patch("vula.api.tenants.get_config", return_value={"plan": "starter"}),
        patch("vula.commerce.service._client", return_value=_mock_count_client(25)),
    ):
        with pytest.raises(PlanLimitError, match="25-document limit"):
            check_document_quota(TID)


def test_document_quota_growth_plan_is_unlimited():
    with (
        patch("vula.api.tenants.get_config", return_value={"plan": "growth"}),
        patch("vula.commerce.service._client") as mock_client,
    ):
        check_document_quota(TID)  # must not raise
    mock_client.assert_not_called()  # never even counts — no cap to check


def test_document_quota_business_plan_is_unlimited():
    with patch("vula.api.tenants.get_config", return_value={"plan": "business"}):
        check_document_quota(TID)  # must not raise


def test_document_quota_defaults_to_starter_when_plan_unset():
    with (
        patch("vula.api.tenants.get_config", return_value={}),
        patch("vula.commerce.service._client", return_value=_mock_count_client(25)),
    ):
        with pytest.raises(PlanLimitError):
            check_document_quota(TID)


def test_document_quota_fails_open_on_count_query_error():
    with (
        patch("vula.api.tenants.get_config", return_value={"plan": "starter"}),
        patch("vula.commerce.service._client", side_effect=RuntimeError("db down")),
    ):
        check_document_quota(TID)  # must not raise


# ── check_seat_quota ─────────────────────────────────────────────────────────────

def test_seat_quota_ok_below_cap():
    with (
        patch("vula.api.tenants.get_config", return_value={"plan": "starter"}),
        patch("vula.commerce.service._client", return_value=_mock_seat_count_client(1)),
    ):
        check_seat_quota(TID)  # must not raise


def test_seat_quota_raises_at_starter_cap_of_two():
    with (
        patch("vula.api.tenants.get_config", return_value={"plan": "starter"}),
        patch("vula.commerce.service._client", return_value=_mock_seat_count_client(2)),
    ):
        with pytest.raises(PlanLimitError, match="2-user limit"):
            check_seat_quota(TID)


def test_seat_quota_raises_at_growth_cap_of_five():
    with (
        patch("vula.api.tenants.get_config", return_value={"plan": "growth"}),
        patch("vula.commerce.service._client", return_value=_mock_seat_count_client(5)),
    ):
        with pytest.raises(PlanLimitError, match="5-user limit"):
            check_seat_quota(TID)


def test_seat_quota_business_plan_is_unlimited():
    with patch("vula.api.tenants.get_config", return_value={"plan": "business"}):
        check_seat_quota(TID)  # must not raise


def test_seat_quota_query_excludes_master_role():
    mock_db = _mock_seat_count_client(1)
    with (
        patch("vula.api.tenants.get_config", return_value={"plan": "starter"}),
        patch("vula.commerce.service._client", return_value=mock_db),
    ):
        check_seat_quota(TID)
    mock_db.table.return_value.select.return_value.eq.return_value.in_.assert_called_once_with(
        "role", ["owner", "staff"]
    )


def test_seat_quota_fails_open_on_count_query_error():
    with (
        patch("vula.api.tenants.get_config", return_value={"plan": "starter"}),
        patch("vula.commerce.service._client", side_effect=RuntimeError("db down")),
    ):
        check_seat_quota(TID)  # must not raise
