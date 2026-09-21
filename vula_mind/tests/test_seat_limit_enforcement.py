"""Tests for per-tenant seat-limit enforcement (vula/api/users.py) — go-live readiness pass,
Phase 4.2. VulaOnboarding.jsx advertises "1-2 users" (Starter), "Up to 5 users" (Growth),
"Unlimited users" (Business); check_seat_quota (plan_limits.py) already enforces this — this
wires it into the actual insert path. Only a genuinely NEW seat is gated: re-inviting or
changing an existing member's role must never be blocked by the cap."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.users import _map_tenant_user, create_user, CreateUserIn
from vula.commerce.plan_limits import PlanLimitError

TID = "digg-demo"


def _mock_client(existing_rows):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=existing_rows)
    return db


# ── _map_tenant_user ─────────────────────────────────────────────────────────────

def test_new_seat_blocked_when_over_cap():
    with (
        patch("vula.api.users._client", return_value=_mock_client([])),
        patch("vula.commerce.plan_limits.check_seat_quota", side_effect=PlanLimitError("cap reached")),
    ):
        with pytest.raises(PlanLimitError):
            _map_tenant_user("user-1", TID, "staff")


def test_new_seat_allowed_when_under_cap():
    mock_db = _mock_client([])
    with (
        patch("vula.api.users._client", return_value=mock_db),
        patch("vula.commerce.plan_limits.check_seat_quota"),  # no-op
    ):
        _map_tenant_user("user-1", TID, "staff")
    mock_db.table.return_value.insert.assert_called_once_with(
        {"user_id": "user-1", "tenant_id": TID, "role": "staff"}
    )


def test_existing_member_role_change_never_gated_by_seat_cap():
    """Re-inviting or changing role for someone already mapped must never be blocked — it
    doesn't consume an additional seat."""
    mock_db = _mock_client([{"user_id": "user-1"}])
    with (
        patch("vula.api.users._client", return_value=mock_db),
        patch("vula.commerce.plan_limits.check_seat_quota", side_effect=PlanLimitError("cap reached")) as mock_check,
    ):
        _map_tenant_user("user-1", TID, "owner")  # must not raise
    mock_check.assert_not_called()
    mock_db.table.return_value.update.assert_called_once()


# ── create_user endpoint ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_user_returns_error_on_seat_limit():
    fake_resp = MagicMock(status_code=201)
    fake_resp.json.return_value = {"id": "new-user-1"}
    with (
        patch("vula.api.users._auth_admin", new=AsyncMock(return_value=fake_resp)),
        patch("vula.api.users._map_tenant_user", side_effect=PlanLimitError(
            "You've reached the 2-user limit on your plan — upgrade for more seats.")),
    ):
        result = await create_user(
            TID, CreateUserIn(email="new@digg-demo.co.za", role="staff"),
            identity={"user_id": "owner-1"},
        )
    assert "upgrade for more seats" in result["error"]


@pytest.mark.asyncio
async def test_create_user_succeeds_when_map_tenant_user_ok():
    fake_resp = MagicMock(status_code=201)
    fake_resp.json.return_value = {"id": "new-user-1"}
    with (
        patch("vula.api.users._auth_admin", new=AsyncMock(return_value=fake_resp)),
        patch("vula.api.users._map_tenant_user"),
        patch("vula.commerce.service._client", return_value=MagicMock()),
    ):
        result = await create_user(
            TID, CreateUserIn(email="new@digg-demo.co.za", role="staff"),
            identity={"user_id": "owner-1"},
        )
    assert "error" not in result
    assert result.get("temp_password")
